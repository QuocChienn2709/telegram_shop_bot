# main.py
import asyncio
import html
import json
import logging
import os
import re
from datetime import datetime

from aiohttp import web
from aiohttp.web import Request, Response
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, ContextTypes,
    MessageHandler, filters
)
from telegram.constants import ParseMode

from config import Config
from database import (
    init_db, add_product, delete_product, delete_all_products,
    get_product, list_products, list_all_products,
    get_available_key, create_order, get_order, update_order_status,
    get_pending_orders_by_user, get_db, register_user,
    get_setting, set_setting, delete_setting, get_all_settings
)
from payos_client import (
    create_payment_link, verify_payment_webhook, get_payment_status
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)
init_db()


# ============================================================
# SAFE HTML SENDER - tự động fallback khi tg-emoji lỗi
# ============================================================
_TG_EMOJI_RE = re.compile(r'<tg-emoji[^>]*>(.*?)</tg-emoji>', re.DOTALL)


def _strip_tg_emoji(text: str) -> str:
    """Xoá tag <tg-emoji> giữ nội dung."""
    return _TG_EMOJI_RE.sub(r'\1', text)


def _is_entity_error(exc: Exception) -> bool:
    s = str(exc).lower()
    return "entity_text_invalid" in s or "can't parse entities" in s or "entity" in s


async def safe_reply(message, text, **kwargs):
    """Reply HTML. Fallback strip tg-emoji nếu lỗi entity."""
    try:
        return await message.reply_text(text, parse_mode=ParseMode.HTML, **kwargs)
    except Exception as e:
        if _is_entity_error(e):
            logger.warning(f"safe_reply fallback: {e}")
            clean = _strip_tg_emoji(text)
            return await message.reply_text(clean, parse_mode=ParseMode.HTML, **kwargs)
        raise


async def safe_edit(query, text, **kwargs):
    """Edit HTML. Fallback strip tg-emoji nếu lỗi entity."""
    try:
        return await query.edit_message_text(text, parse_mode=ParseMode.HTML, **kwargs)
    except Exception as e:
        if _is_entity_error(e):
            logger.warning(f"safe_edit fallback: {e}")
            clean = _strip_tg_emoji(text)
            return await query.edit_message_text(clean, parse_mode=ParseMode.HTML, **kwargs)
        raise


# ============================================================
# UI EMOJI KEYS
# ============================================================
UI_KEYS = {
    "shop":     "Tiêu đề cửa hàng",
    "cart":     "Nút mua / giỏ hàng",
    "orders":   "Nút đơn hàng",
    "back":     "Nút quay lại",
    "next":     "Nút trang sau",
    "prev":     "Nút trang trước",
    "check":    "Nút kiểm tra thanh toán",
    "cancel":   "Nút hủy đơn",
    "key":      "Nhãn key sản phẩm",
    "success":  "Thông báo thành công",
    "error":    "Thông báo lỗi",
    "warning":  "Cảnh báo",
    "info":     "Thông tin / chi tiết",
    "help":     "Trợ giúp",
    "admin":    "Quản trị",
    "money":    "Tiền / giá",
    "box":      "Hộp / danh sách",
    "detail":   "Chi tiết",
    "desc":     "Mô tả",
    "list":     "Danh sách",
    "add":      "Thêm",
    "delete":   "Xóa",
    "edit":     "Sửa",
    "pay":      "Thanh toán",
    "loading":  "Đang xử lý",
}


def ui_emoji_html(key, fallback="") -> str:
    eid = get_setting(f"ui_{key}")
    if eid:
        return f'<tg-emoji emoji-id="{eid}">{fallback or "•"}</tg-emoji>'
    return fallback


def ui_emoji_id(key):
    return get_setting(f"ui_{key}")


def button(text, callback_data=None, url=None, ui_key=None):
    kwargs = {"text": text}
    if callback_data:
        kwargs["callback_data"] = callback_data
    if url:
        kwargs["url"] = url
    if ui_key:
        eid = ui_emoji_id(ui_key)
        if eid:
            kwargs["icon_custom_emoji_id"] = eid
    return InlineKeyboardButton(**kwargs)


def extract_custom_emoji_from_message(message):
    text = message.text or message.caption or ""
    entities = message.entities or message.caption_entities or []
    custom_emojis = [e for e in entities if e.type == "custom_emoji"]
    if not custom_emojis:
        return text, None
    emoji_id = custom_emojis[0].custom_emoji_id
    # Validate chỉ nhận chuỗi số
    if not emoji_id or not emoji_id.isdigit():
        return text, None
    encoded = text.encode("utf-16-le")
    for e in sorted(custom_emojis, key=lambda x: x.offset, reverse=True):
        s, en = e.offset * 2, (e.offset + e.length) * 2
        encoded = encoded[:s] + encoded[en:]
    return encoded.decode("utf-16-le"), emoji_id


def product_name_html(name, emoji_id=None):
    safe = html.escape(name)
    if emoji_id:
        return f'<tg-emoji emoji-id="{emoji_id}">•</tg-emoji> {safe}'
    return safe


# ============================================================
# UI BUILDERS
# ============================================================
def product_buttons(products, page=0, per_page=5):
    keyboard = []
    for p in products:
        text = f"{p['name']} - {p['price']:,}đ - còn {p['stock']}"
        kwargs = {"text": text, "callback_data": f"buy_{p['id']}"}
        if p.get("emoji_id"):
            kwargs["icon_custom_emoji_id"] = p["emoji_id"]
        keyboard.append([InlineKeyboardButton(**kwargs)])

    nav = []
    if page > 0:
        nav.append(button("Trước", callback_data=f"page_{page-1}", ui_key="prev"))
    if len(products) == per_page:
        nav.append(button("Sau", callback_data=f"page_{page+1}", ui_key="next"))
    if nav:
        keyboard.append(nav)
    keyboard.append([button("Đơn hàng chờ", callback_data="my_orders", ui_key="orders")])
    return InlineKeyboardMarkup(keyboard)


def order_buttons(order_id):
    return InlineKeyboardMarkup([
        [button("Đã thanh toán? Kiểm tra", callback_data=f"check_{order_id}", ui_key="check")],
        [button("Hủy đơn", callback_data=f"cancel_{order_id}", ui_key="cancel")]
    ])


def detail_buttons(product_id):
    return InlineKeyboardMarkup([
        [button("Mua ngay", callback_data=f"buy_{product_id}", ui_key="cart")],
        [button("Quay lại", callback_data="back_list", ui_key="back")]
    ])


# ============================================================
# USER HANDLERS
# ============================================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    register_user(user.id, user.username, user.first_name, user.last_name)
    prods = list_products(limit=5, offset=0)
    if not prods:
        await safe_reply(update.message, "Cửa hàng hiện chưa có sản phẩm.")
        return
    header = ui_emoji_html("shop")
    title = f"{header} <b>Cửa hàng tài khoản Pro</b>" if header else "<b>Cửa hàng tài khoản Pro</b>"
    await safe_reply(
        update.message,
        f"{title}\n\nChọn sản phẩm bên dưới:",
        reply_markup=product_buttons(prods, page=0)
    )


async def list_products_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    page = 0
    if data.startswith("page_"):
        try:
            page = max(0, int(data.split("_")[1]))
        except ValueError:
            page = 0
    products = list_products(limit=5, offset=page * 5)
    if not products:
        await safe_edit(query, "Không còn sản phẩm nào.", reply_markup=None)
        return
    await safe_edit(
        query,
        f"<b>Danh sách sản phẩm (trang {page + 1})</b>",
        reply_markup=product_buttons(products, page)
    )


async def show_product_detail(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    try:
        product_id = int(query.data.split("_")[1])
    except (ValueError, IndexError):
        return
    product = get_product(product_id)
    if not product:
        await safe_edit(query, "Không tìm thấy sản phẩm.")
        return
    name_html = product_name_html(product["name"], product.get("emoji_id"))
    desc_html = html.escape(product.get("description") or "(không có mô tả)")
    text = (
        f"<b>Chi tiết sản phẩm #{product['id']}</b>\n\n"
        f"<b>Tên:</b> {name_html}\n"
        f"<b>Giá:</b> {product['price']:,} VND\n"
        f"<b>Tồn kho:</b> {product['stock']}\n"
        f"<b>Đã bán:</b> {product['sold']}\n\n"
        f"<b>Mô tả:</b>\n{desc_html}"
    )
    await safe_edit(query, text, reply_markup=detail_buttons(product_id))


async def buy_product(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    try:
        product_id = int(query.data.split("_")[1])
    except (ValueError, IndexError):
        await safe_edit(query, "Dữ liệu không hợp lệ.", reply_markup=None)
        return
    product = get_product(product_id)
    if not product or product["stock"] <= 0:
        await safe_edit(query, "Sản phẩm đã hết hàng.", reply_markup=None)
        return

    order_code = int(f"{int(datetime.now().timestamp())}{product_id:03d}{query.from_user.id % 1000:03d}")
    create_order(order_code, query.from_user.id, product_id, 1, product["price"])

    desc_payos = f"TK {product['name'][:15]}"
    payment_url, error = create_payment_link(
        order_code=order_code, amount=product["price"],
        description=desc_payos, buyer_name=query.from_user.full_name
    )

    if payment_url:
        context.bot_data[f"order_{order_code}"] = {"product_id": product_id, "user_id": query.from_user.id}
        name_html = product_name_html(product["name"], product.get("emoji_id"))
        msg = (
            f"<b>Đơn hàng #{order_code}</b>\n\n"
            f"<b>Sản phẩm:</b> {name_html}\n"
            f"<b>Số tiền:</b> {product['price']:,} VND\n\n"
            f'<a href="{payment_url}">Nhấn để thanh toán</a>\n\n'
            f"Sau khi thanh toán, nhấn 'Đã thanh toán? Kiểm tra' bên dưới."
        )
        await safe_edit(query, msg,
                        reply_markup=order_buttons(order_code),
                        disable_web_page_preview=True)
    else:
        await safe_edit(query, f"Lỗi tạo link thanh toán: {html.escape(str(error))}",
                        reply_markup=None)


async def check_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    try:
        order_code = int(query.data.split("_")[1])
    except (ValueError, IndexError):
        await safe_edit(query, "Dữ liệu không hợp lệ.")
        return
    order = get_order(order_code)
    if not order:
        await safe_edit(query, "Không tìm thấy đơn hàng.", reply_markup=None)
        return
    if order["status"] == "paid":
        await safe_edit(
            query,
            f"Đơn hàng #{order_code} đã thanh toán.\nKey: <code>{html.escape(order['key_assigned'] or '')}</code>"
        )
        return
    if order["status"] == "cancelled":
        await safe_edit(query, f"Đơn hàng #{order_code} đã bị hủy.", reply_markup=None)
        return

    data = get_payment_status(order_code)
    paid = bool(data and data.get("code") == "00" and data.get("data", {}).get("status") == "PAID")
    if paid:
        key = get_available_key(order["product_id"])
        if key:
            update_order_status(order_code, "paid", key)
            await safe_edit(
                query,
                f"Thanh toán thành công!\nKey: <code>{html.escape(key)}</code>"
            )
        else:
            await safe_edit(query, "Đã thanh toán nhưng hết key. Liên hệ admin.", reply_markup=None)
    else:
        await safe_edit(
            query,
            f"Đơn hàng #{order_code} chưa được thanh toán.\nVui lòng thanh toán qua link hoặc hủy.",
            reply_markup=order_buttons(order_code)
        )


async def cancel_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    try:
        order_code = int(query.data.split("_")[1])
    except (ValueError, IndexError):
        return
    order = get_order(order_code)
    if not order or order["status"] != "pending":
        await safe_edit(query, "Không thể hủy đơn hàng này.")
        return
    update_order_status(order_code, "cancelled")
    await safe_edit(query, f"Đã hủy đơn hàng #{order_code}.")


async def my_orders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    orders = get_pending_orders_by_user(query.from_user.id)
    if not orders:
        await safe_edit(query, "Bạn không có đơn hàng nào đang chờ.")
        return
    text = "<b>Đơn hàng chờ thanh toán:</b>\n\n"
    for o in orders[:10]:
        prod = get_product(o["product_id"])
        name = product_name_html(prod["name"], prod.get("emoji_id")) if prod else "Không xác định"
        text += f"#{o['id']} - {name} - {o['amount']:,} VND\n"
    await safe_edit(query, text)


# ============================================================
# ADMIN - PRODUCTS
# ============================================================
async def admin_add_product(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in Config.ADMIN_IDS:
        await safe_reply(update.message, "Bạn không có quyền.")
        return
    try:
        clean_text, emoji_id = extract_custom_emoji_from_message(update.message)
        parts = clean_text.split()
        if len(parts) < 4:
            await safe_reply(
                update.message,
                "<b>Cú pháp:</b>\n"
                "<code>/add &lt;tên&gt; &lt;giá&gt; &lt;số_lượng&gt; [keys]</code>\n"
                "<code>/add &lt;tên&gt;|&lt;mô tả&gt; &lt;giá&gt; &lt;số_lượng&gt; [keys]</code>\n\n"
                "<b>Ví dụ có key:</b>\n"
                "<code>/add CapCut 50000 3 CC001,CC002,CC003</code>\n\n"
                "<b>Chưa có key:</b>\n"
                "<code>/add CapCut 50000 10</code>"
            )
            return
        name_part = parts[1]
        if "|" in name_part:
            name, description = name_part.split("|", 1)
            name, description = name.strip(), description.strip()
        else:
            name, description = name_part.strip(), ""
        price_str, stock_str = parts[2], parts[3]
        keys_str = parts[4] if len(parts) >= 5 else "-"

        try:
            price = int(price_str.replace(".", "").replace(",", "").strip())
            if price <= 0: raise ValueError()
        except ValueError:
            await safe_reply(update.message, f"Giá không hợp lệ: <code>{html.escape(price_str)}</code>")
            return

        keys_str = keys_str.strip()
        is_number = False
        try:
            int(keys_str); is_number = True
        except ValueError:
            pass
        keys = [] if (keys_str in ("-", "") or is_number) else [k.strip() for k in keys_str.split(",") if k.strip()]

        if keys:
            stock = len(keys)
        else:
            try:
                stock = int(stock_str.strip())
                if stock < 0: raise ValueError()
            except ValueError:
                await safe_reply(update.message, "Số lượng không hợp lệ.")
                return
            stock = 0

        pid = add_product(name, description, price, stock, keys, emoji_id=emoji_id)
        safe_name = html.escape(name)
        desc_info = f"\nMô tả: {html.escape(description)}" if description else ""
        emoji_info = (
            f"\nEmoji ID: <code>{emoji_id}</code> (chỉ hiển thị được nếu bot có quyền)"
            if emoji_id else ""
        )
        await safe_reply(
            update.message,
            f"Đã thêm sản phẩm ID <code>{pid}</code>\n"
            f"Tên: {safe_name}{desc_info}\n"
            f"Giá: {price:,} VND\n"
            f"Số lượng: {stock}\n"
            f"Keys: {len(keys)}{emoji_info}\n\n"
            f"Nạp key: <code>/addkey {pid} &lt;key1,key2,...&gt;</code>"
        )
    except Exception as e:
        logger.error(f"admin_add_product error: {e}", exc_info=True)
        await safe_reply(update.message, f"Lỗi: {html.escape(str(e))}")


async def admin_import_products(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in Config.ADMIN_IDS:
        await safe_reply(update.message, "Bạn không có quyền.")
        return
    doc = update.message.document
    if not doc or not (doc.file_name or "").lower().endswith(".txt"):
        await safe_reply(update.message, "Chỉ chấp nhận file .txt")
        return
    try:
        tg_file = await doc.get_file()
        raw = await tg_file.download_as_bytearray()
        try:
            content = raw.decode("utf-8")
        except UnicodeDecodeError:
            content = raw.decode("utf-8-sig", errors="ignore")
    except Exception as e:
        await safe_reply(update.message, f"Không đọc được file: {html.escape(str(e))}")
        return

    success, failed = [], []
    for line_no, line in enumerate(content.splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            parts = [p.strip() for p in line.split("|")]
            if len(parts) < 3:
                failed.append((line_no, "cần ≥ 3 phần")); continue
            if len(parts) == 3:
                name, description, price_str, keys_str = parts[0], "", parts[1], parts[2]
            else:
                name, description, price_str = parts[0], parts[1], parts[2]
                keys_str = parts[3] if len(parts) >= 4 else ""
            if not name:
                failed.append((line_no, "thiếu tên")); continue
            try:
                price = int(price_str.replace(".", "").replace(",", "").strip())
                if price <= 0: raise ValueError()
            except ValueError:
                failed.append((line_no, f"giá không hợp lệ: {price_str}")); continue
            keys = [k.strip() for k in keys_str.split(",") if k.strip()] if keys_str and keys_str != "-" else []
            pid = add_product(name, description, price, len(keys), keys, emoji_id=None)
            success.append((pid, name, price, len(keys)))
        except Exception as e:
            failed.append((line_no, str(e)))

    report = f"<b>Import file:</b> <code>{html.escape(doc.file_name)}</code>\n\n"
    report += f"Thành công: <b>{len(success)}</b>\n"
    report += f"Thất bại: <b>{len(failed)}</b>\n\n"
    if success:
        report += "<b>Đã thêm:</b>\n"
        for pid, name, price, stock in success[:20]:
            report += f"<code>{pid}</code> {html.escape(name)} - {price:,}đ - {stock} key\n"
        if len(success) > 20:
            report += f"<i>... và {len(success) - 20} SP khác</i>\n"
    if failed:
        report += "\n<b>Lỗi:</b>\n"
        for line_no, err in failed[:10]:
            report += f"Dòng {line_no}: {html.escape(err)}\n"
    await safe_reply(update.message, report)


async def admin_add_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in Config.ADMIN_IDS:
        await safe_reply(update.message, "Bạn không có quyền.")
        return
    try:
        parts = update.message.text.split(maxsplit=2)
        if len(parts) < 3:
            await safe_reply(update.message, "Cú pháp: <code>/addkey &lt;id&gt; &lt;k1,k2,...&gt;</code>")
            return
        product_id = int(parts[1])
        new_keys = [k.strip() for k in parts[2].split(",") if k.strip()]
        if not new_keys:
            await safe_reply(update.message, "Cần ít nhất 1 key.")
            return
        product = get_product(product_id)
        if not product:
            await safe_reply(update.message, f"Không tìm thấy SP <code>{product_id}</code>.")
            return
        db = get_db()
        db.products.update_one(
            {"id": product_id},
            {"$push": {"keys": {"$each": new_keys}}, "$inc": {"stock": len(new_keys)}}
        )
        await safe_reply(
            update.message,
            f"Đã thêm <b>{len(new_keys)}</b> key vào <code>{product_id}</code>\n"
            f"Tồn kho mới: {product['stock'] + len(new_keys)}"
        )
    except Exception as e:
        await safe_reply(update.message, f"Lỗi: {html.escape(str(e))}")


async def admin_set_product_emoji(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in Config.ADMIN_IDS:
        await safe_reply(update.message, "Bạn không có quyền.")
        return
    try:
        clean_text, emoji_id = extract_custom_emoji_from_message(update.message)
        parts = clean_text.split()
        if len(parts) < 2:
            await safe_reply(update.message, "Cú pháp: <code>/setemoji &lt;id&gt; [dán emoji Premium]</code>")
            return
        product_id = int(parts[1])
        if not emoji_id:
            await safe_reply(update.message, "Không tìm thấy custom emoji Premium.")
            return
        product = get_product(product_id)
        if not product:
            await safe_reply(update.message, f"Không tìm thấy SP <code>{product_id}</code>.")
            return
        db = get_db()
        db.products.update_one({"id": product_id}, {"$set": {"emoji_id": emoji_id}})
        await safe_reply(
            update.message,
            f"Đã đặt emoji cho SP <code>{product_id}</code>.\n"
            f"Emoji ID: <code>{emoji_id}</code>"
        )
    except Exception as e:
        await safe_reply(update.message, f"Lỗi: {html.escape(str(e))}")


async def admin_setdesc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in Config.ADMIN_IDS:
        await safe_reply(update.message, "Bạn không có quyền.")
        return
    try:
        parts = update.message.text.split(maxsplit=2)
        if len(parts) < 3:
            await safe_reply(update.message, "Cú pháp: <code>/setdesc &lt;id&gt; &lt;mô tả&gt;</code>")
            return
        product_id = int(parts[1])
        new_desc = parts[2].strip()
        product = get_product(product_id)
        if not product:
            await safe_reply(update.message, f"Không tìm thấy SP <code>{product_id}</code>.")
            return
        db = get_db()
        db.products.update_one({"id": product_id}, {"$set": {"description": new_desc}})
        await safe_reply(
            update.message,
            f"Đã cập nhật mô tả SP <code>{product_id}</code>:\n\n{html.escape(new_desc)}"
        )
    except Exception as e:
        await safe_reply(update.message, f"Lỗi: {html.escape(str(e))}")


async def admin_detail(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in Config.ADMIN_IDS:
        await safe_reply(update.message, "Bạn không có quyền.")
        return
    try:
        parts = update.message.text.split()
        if len(parts) < 2:
            await safe_reply(update.message, "Cú pháp: <code>/detail &lt;id&gt;</code>")
            return
        product_id = int(parts[1])
        product = get_product(product_id)
        if not product:
            await safe_reply(update.message, f"Không tìm thấy SP <code>{product_id}</code>.")
            return
        name_html = product_name_html(product["name"], product.get("emoji_id"))
        desc = html.escape(product.get("description") or "(không có mô tả)")
        keys = json.loads(product["keys"] or "[]")
        text = (
            f"<b>Chi tiết sản phẩm #{product['id']}</b>\n\n"
            f"Tên: {name_html}\n"
            f"Mô tả: {desc}\n"
            f"Giá: {product['price']:,} VND\n"
            f"Tồn kho: {product['stock']}\n"
            f"Đã bán: {product['sold']}\n"
            f"Emoji ID: <code>{product.get('emoji_id') or 'chưa đặt'}</code>\n\n"
            f"<b>Keys còn lại ({len(keys)}):</b>\n"
        )
        for k in keys[:10]:
            text += f"  <code>{html.escape(k)}</code>\n"
        if len(keys) > 10:
            text += f"  <i>... và {len(keys) - 10} key khác</i>\n"
        await safe_reply(update.message, text)
    except Exception as e:
        await safe_reply(update.message, f"Lỗi: {html.escape(str(e))}")


async def admin_list_products(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in Config.ADMIN_IDS:
        await safe_reply(update.message, "Bạn không có quyền.")
        return
    products = list_all_products()
    if not products:
        await safe_reply(update.message, "Chưa có sản phẩm nào.")
        return
    text = "<b>Toàn bộ sản phẩm:</b>\n\n"
    for p in products:
        name_html = product_name_html(p["name"], p.get("emoji_id"))
        text += f"<code>{p['id']}</code> - {name_html} - {p['price']:,}đ - kho: {p['stock']} - đã bán: {p['sold']}\n"
    text += "\nChi tiết: <code>/detail &lt;id&gt;</code> - Xoá: <code>/del &lt;id&gt;</code>"
    await safe_reply(update.message, text)


async def admin_delete_product(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in Config.ADMIN_IDS:
        await safe_reply(update.message, "Bạn không có quyền.")
        return
    try:
        parts = update.message.text.split()
        if len(parts) < 2:
            await safe_reply(
                update.message,
                "Cú pháp: <code>/del &lt;id&gt;</code>\nXóa tất cả: <code>/delall confirm</code>"
            )
            return
        product_id = int(parts[1])
        product = get_product(product_id)
        if not product:
            await safe_reply(update.message, f"Không tìm thấy SP <code>{product_id}</code>.")
            return
        if delete_product(product_id):
            await safe_reply(
                update.message,
                f"Đã xóa SP <code>{product_id}</code> - {html.escape(product['name'])}"
            )
    except Exception as e:
        await safe_reply(update.message, f"Lỗi: {html.escape(str(e))}")


async def admin_delete_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in Config.ADMIN_IDS:
        await safe_reply(update.message, "Bạn không có quyền.")
        return
    parts = update.message.text.split()
    if len(parts) < 2 or parts[1].lower() != "confirm":
        await safe_reply(update.message, "Xóa TẤT CẢ sản phẩm. Xác nhận: <code>/delall confirm</code>")
        return
    count = delete_all_products()
    await safe_reply(update.message, f"Đã xóa <b>{count}</b> sản phẩm.")


# ============================================================
# ADMIN - CUSTOM UI
# ============================================================
async def admin_setui(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in Config.ADMIN_IDS:
        await safe_reply(update.message, "Bạn không có quyền.")
        return
    try:
        clean_text, emoji_id = extract_custom_emoji_from_message(update.message)
        parts = clean_text.split()
        if len(parts) < 2:
            await safe_reply(
                update.message,
                "<b>Cú pháp:</b> <code>/setui &lt;key&gt; [dán emoji Premium]</code>\n\n"
                "<b>Danh sách key:</b>\n" +
                "\n".join(f"- <code>{k}</code> - {v}" for k, v in UI_KEYS.items()) +
                "\n\nXem bảng: <code>/viewui</code>"
            )
            return
        key = parts[1].lower()
        if key not in UI_KEYS:
            await safe_reply(
                update.message,
                f"Key không hợp lệ: <code>{html.escape(key)}</code>\nDùng <code>/viewui</code> để xem danh sách."
            )
            return
        if not emoji_id:
            await safe_reply(update.message, "Không tìm thấy custom emoji Premium trong tin nhắn.")
            return
        set_setting(f"ui_{key}", emoji_id)
        await safe_reply(
            update.message,
            f"Đã đặt emoji cho <code>{key}</code>\nEmoji ID: <code>{emoji_id}</code>\n\n"
            f"Lưu ý: emoji chỉ hiển thị nếu bot có quyền dùng."
        )
    except Exception as e:
        await safe_reply(update.message, f"Lỗi: {html.escape(str(e))}")


async def admin_viewui(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in Config.ADMIN_IDS:
        await safe_reply(update.message, "Bạn không có quyền.")
        return
    settings = {s["key"]: s["emoji_id"] for s in get_all_settings()}
    text = "<b>Bảng UI emoji hiện tại:</b>\n\n"
    for k, desc in UI_KEYS.items():
        eid = settings.get(f"ui_{k}")
        if eid:
            text += f"- <code>{k}</code> - {desc} - <code>{eid}</code>\n"
        else:
            text += f"- <code>{k}</code> - {desc} - <i>chưa đặt</i>\n"
    text += "\nĐặt: <code>/setui &lt;key&gt; [dán emoji]</code>\nXóa: <code>/delui &lt;key&gt;</code>"
    await safe_reply(update.message, text)


async def admin_delui(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in Config.ADMIN_IDS:
        await safe_reply(update.message, "Bạn không có quyền.")
        return
    parts = update.message.text.split()
    if len(parts) < 2:
        await safe_reply(update.message, "Cú pháp: <code>/delui &lt;key&gt;</code>")
        return
    key = parts[1].lower()
    if key not in UI_KEYS:
        await safe_reply(update.message, f"Key không hợp lệ: <code>{html.escape(key)}</code>")
        return
    delete_setting(f"ui_{key}")
    await safe_reply(update.message, f"Đã xóa emoji cho <code>{key}</code>.")


async def admin_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "<b>Lệnh admin:</b>\n\n"
        "<b>Sản phẩm:</b>\n"
        "<code>/add Tên Giá SL Keys</code>\n"
        "<code>/add Tên|Mô tả Giá SL Keys</code>\n"
        "Gửi file <b>.txt</b> để import\n"
        "<code>/list</code> - Xem tất cả\n"
        "<code>/detail &lt;id&gt;</code> - Chi tiết\n"
        "<code>/del &lt;id&gt;</code> - Xóa 1\n"
        "<code>/delall confirm</code> - Xóa hết\n\n"
        "<b>Cập nhật:</b>\n"
        "<code>/addkey &lt;id&gt; K1,K2</code>\n"
        "<code>/setdesc &lt;id&gt; Mô tả</code>\n"
        "<code>/setemoji &lt;id&gt; [emoji Premium]</code>\n\n"
        "<b>Custom UI:</b>\n"
        "<code>/setui &lt;key&gt; [emoji Premium]</code>\n"
        "<code>/viewui</code> - Xem bảng\n"
        "<code>/delui &lt;key&gt;</code> - Xóa\n\n"
        "<b>File .txt format:</b>\n"
        "<code>Tên|Mô tả|Giá|Key1,Key2</code>\n"
        "<code>Tên|Giá|Key1,Key2</code>"
    )
    await safe_reply(update.message, text)


# ============================================================
# HTTP HANDLERS
# ============================================================
async def root_handler(request: Request):
    if request.method == "HEAD":
        return Response(status=200, content_type="text/plain")
    return Response(text="Bot is running", status=200)


async def health_check(request: Request):
    if request.method == "HEAD":
        return Response(status=200, content_type="text/plain")
    return Response(text="OK", status=200)


async def telegram_webhook(request: Request):
    if request.method == "HEAD":
        return Response(status=200, content_type="text/plain")
    if request.method == "GET":
        return Response(text="Telegram webhook OK", status=200)
    try:
        raw = await request.read()
        if not raw:
            return Response(status=400, text="Empty body")
        data = json.loads(raw.decode("utf-8"))
        update = Update.de_json(data, request.app["bot_app"].bot)
        await request.app["bot_app"].process_update(update)
        return Response(text="OK", status=200)
    except Exception as e:
        logger.error(f"Telegram webhook error: {e}", exc_info=True)
        return Response(status=500, text="Error")


async def payos_webhook(request: Request):
    if request.method == "HEAD":
        return Response(status=200, content_type="text/plain")
    if request.method == "GET":
        return Response(text="PayOS webhook OK", status=200)
    try:
        raw = await request.read()
        if not raw:
            return Response(status=200, text="OK")
        try:
            body = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            return Response(status=200, text="OK")
        data = body.get("data", {})
        order_code = data.get("orderCode")
        payos_code = data.get("code")
        description = data.get("description", "")
        if order_code == 123 or description == "VQRIO123":
            return Response(text="OK", status=200)
        sig_header = request.headers.get("x-payos-signature", "")
        if not verify_payment_webhook(body, sig_header):
            return Response(status=200, text="OK")
        if payos_code == "00" and order_code:
            order = get_order(order_code)
            if order and order["status"] == "pending":
                key = get_available_key(order["product_id"])
                if key:
                    update_order_status(order_code, "paid", key)
                    app = request.app["bot_app"]
                    try:
                        # Tin nhắn đơn giản không tg-emoji, an toàn
                        await app.bot.send_message(
                            chat_id=order["user_id"],
                            text=f"Thanh toán thành công!\nKey: <code>{html.escape(key)}</code>",
                            parse_mode=ParseMode.HTML
                        )
                    except Exception as e:
                        logger.error(f"Gửi tin nhắn thất bại: {e}")
        return Response(text="OK", status=200)
    except Exception as e:
        logger.error(f"PayOS webhook error: {e}", exc_info=True)
        return Response(status=200, text="OK")


# ============================================================
# MAIN
# ============================================================
async def main():
    app = Application.builder().token(Config.TELEGRAM_TOKEN).build()

    # User
    app.add_handler(CommandHandler("start", start))

    # Admin
    app.add_handler(CommandHandler("add", admin_add_product))
    app.add_handler(CommandHandler("addkey", admin_add_key))
    app.add_handler(CommandHandler("setemoji", admin_set_product_emoji))
    app.add_handler(CommandHandler("setdesc", admin_setdesc))
    app.add_handler(CommandHandler("detail", admin_detail))
    app.add_handler(CommandHandler("list", admin_list_products))
    app.add_handler(CommandHandler("del", admin_delete_product))
    app.add_handler(CommandHandler("delall", admin_delete_all))

    # UI custom emoji
    app.add_handler(CommandHandler("setui", admin_setui))
    app.add_handler(CommandHandler("viewui", admin_viewui))
    app.add_handler(CommandHandler("delui", admin_delui))

    app.add_handler(CommandHandler("help", admin_help))

    # Import file .txt
    app.add_handler(MessageHandler(
        filters.Document.FileExtension("txt") & filters.User(Config.ADMIN_IDS),
        admin_import_products))

    # Callbacks
    app.add_handler(CallbackQueryHandler(list_products_callback, pattern=r"^page_"))
    app.add_handler(CallbackQueryHandler(show_product_detail, pattern=r"^detail_\d+$"))
    app.add_handler(CallbackQueryHandler(buy_product, pattern=r"^buy_"))
    app.add_handler(CallbackQueryHandler(check_order, pattern=r"^check_"))
    app.add_handler(CallbackQueryHandler(cancel_order, pattern=r"^cancel_"))
    app.add_handler(CallbackQueryHandler(my_orders, pattern=r"^my_orders$"))
    app.add_handler(CallbackQueryHandler(list_products_callback, pattern=r"^back_list$"))

    await app.initialize()
    await app.start()

    if Config.WEBHOOK_URL:
        webhook_url = f"{Config.WEBHOOK_URL}/telegram"
        await app.bot.set_webhook(webhook_url)
        logger.info(f"Telegram webhook set to: {webhook_url}")
    else:
        logger.warning("WEBHOOK_URL chưa cấu hình!")

    web_app = web.Application()
    web_app["bot_app"] = app
    web_app.router.add_get("/", root_handler)
    web_app.router.add_get("/health", health_check)
    web_app.router.add_get("/telegram", telegram_webhook)
    web_app.router.add_post("/telegram", telegram_webhook)
    web_app.router.add_get("/payos", payos_webhook)
    web_app.router.add_post("/payos", payos_webhook)

    runner = web.AppRunner(web_app)
    await runner.setup()
    port = int(os.environ.get("PORT", 8080))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info(f"Webhook server started on port {port}")

    try:
        await asyncio.Event().wait()
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        await runner.cleanup()
        await app.stop()
        await app.shutdown()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Stopped by user")
