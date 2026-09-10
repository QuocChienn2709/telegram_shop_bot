# main.py
import asyncio
import html
import json
import logging
import os
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
    init_db, add_product, update_product, delete_product, delete_all_products,
    get_product, list_products, list_all_products,
    get_available_key, create_order, get_order, update_order_status,
    get_pending_orders_by_user, get_db
)
from payos_client import (
    create_payment_link, verify_payment_webhook, get_payment_status
)

# --- Logging ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

init_db()


# ============================================================
# HELPERS
# ============================================================
def extract_custom_emoji_from_message(message):
    """Trích custom_emoji đầu tiên, trả về (clean_text, emoji_id)."""
    text = message.text or message.caption or ""
    entities = message.entities or message.caption_entities or []

    custom_emojis = [e for e in entities if e.type == "custom_emoji"]
    if not custom_emojis:
        return text, None

    emoji_id = custom_emojis[0].custom_emoji_id

    # Xóa emoji khỏi text (từ cuối lên đầu để giữ offset UTF-16)
    encoded = text.encode("utf-16-le")
    for e in sorted(custom_emojis, key=lambda x: x.offset, reverse=True):
        s = e.offset * 2
        en = (e.offset + e.length) * 2
        encoded = encoded[:s] + encoded[en:]
    clean_text = encoded.decode("utf-16-le")

    return clean_text, emoji_id


def render_name_html(name, emoji_id=None):
    """
    Render tên sản phẩm với Telegram Premium emoji.
    Tag CHUẨN: <tg-emoji emoji-id="...">...</tg-emoji>
    """
    safe_name = html.escape(name)
    if emoji_id:
        return f'<tg-emoji emoji-id="{emoji_id}">🎁</tg-emoji> {safe_name}'
    return safe_name


def render_description_html(desc):
    if not desc:
        return "<i>(không có mô tả)</i>"
    return html.escape(desc)


def make_product_button(product, prefix="🛒"):
    text = f"{prefix} {product['name']} - {product['price']:,} VND (còn {product['stock']})"
    kwargs = {"text": text, "callback_data": f"buy_{product['id']}"}
    if product.get("emoji_id"):
        kwargs["icon_custom_emoji_id"] = product["emoji_id"]
    return InlineKeyboardButton(**kwargs)


def product_buttons(products, page=0, per_page=5):
    keyboard = []
    for p in products:
        keyboard.append([make_product_button(p)])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⏮ Trước", callback_data=f"page_{page - 1}"))
    if len(products) == per_page:
        nav.append(InlineKeyboardButton("⏭ Sau", callback_data=f"page_{page + 1}"))
    if nav:
        keyboard.append(nav)
    keyboard.append([InlineKeyboardButton("📦 Đơn hàng chờ", callback_data="my_orders")])
    return InlineKeyboardMarkup(keyboard)


def order_buttons(order_id):
    keyboard = [
        [InlineKeyboardButton("✅ Đã thanh toán? Kiểm tra", callback_data=f"check_{order_id}")],
        [InlineKeyboardButton("❌ Hủy đơn", callback_data=f"cancel_{order_id}")]
    ]
    return InlineKeyboardMarkup(keyboard)


def detail_buttons(product_id):
    keyboard = [
        [InlineKeyboardButton("🛒 Mua ngay", callback_data=f"buy_{product_id}")],
        [InlineKeyboardButton("🔙 Quay lại", callback_data="back_list")]
    ]
    return InlineKeyboardMarkup(keyboard)


# ============================================================
# BOT HANDLERS - USER
# ============================================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    with get_db() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO users (user_id, username, first_name, last_name) VALUES (?, ?, ?, ?)",
            (user.id, user.username, user.first_name, user.last_name)
        )
    prods = list_products(limit=5, offset=0)
    if not prods:
        await update.message.reply_text("🏪 Cửa hàng hiện chưa có sản phẩm.")
        return
    await update.message.reply_text(
        "🏪 <b>Cửa hàng tài khoản Pro</b>\n\n"
        "Chọn sản phẩm bên dưới:",
        parse_mode=ParseMode.HTML,
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
        await query.edit_message_text("Không còn sản phẩm nào.", reply_markup=None)
        return
    await query.edit_message_text(
        f"📋 <b>Danh sách sản phẩm (trang {page + 1}):</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=product_buttons(products, page)
    )


async def show_product_detail(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Callback xem chi tiết sản phẩm: detail_<id>"""
    query = update.callback_query
    await query.answer()
    try:
        product_id = int(query.data.split("_")[1])
    except (ValueError, IndexError):
        return

    product = get_product(product_id)
    if not product:
        await query.edit_message_text("❌ Không tìm thấy sản phẩm.")
        return

    name_html = render_name_html(product["name"], product.get("emoji_id"))
    desc_html = render_description_html(product.get("description"))

    text = (
        f"📦 <b>Chi tiết sản phẩm #{product['id']}</b>\n\n"
        f"<b>Tên:</b> {name_html}\n"
        f"<b>Giá:</b> {product['price']:,} VND\n"
        f"<b>Tồn kho:</b> {product['stock']}\n"
        f"<b>Đã bán:</b> {product['sold']}\n\n"
        f"<b>Mô tả:</b>\n{desc_html}"
    )
    await query.edit_message_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=detail_buttons(product_id)
    )


async def buy_product(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    try:
        product_id = int(query.data.split("_")[1])
    except (ValueError, IndexError):
        await query.edit_message_text("❌ Dữ liệu không hợp lệ.", reply_markup=None)
        return

    product = get_product(product_id)
    if not product or product["stock"] <= 0:
        await query.edit_message_text("❌ Sản phẩm này đã hết hàng.", reply_markup=None)
        return

    order_code = int(
        f"{int(datetime.now().timestamp())}{product_id:03d}{query.from_user.id % 1000:03d}"
    )
    create_order(order_code, query.from_user.id, product_id, 1, product["price"])

    desc_payos = f"TK {product['name'][:15]}"
    payment_url, error = create_payment_link(
        order_code=order_code,
        amount=product["price"],
        description=desc_payos,
        buyer_name=query.from_user.full_name
    )

    if payment_url:
        context.bot_data[f"order_{order_code}"] = {
            "product_id": product_id,
            "user_id": query.from_user.id
        }
        name_html = render_name_html(product["name"], product.get("emoji_id"))
        msg = (
            f"🧾 <b>Đơn hàng #{order_code}</b>\n\n"
            f"<b>Sản phẩm:</b> {name_html}\n"
            f"<b>Số tiền:</b> {product['price']:,} VND\n\n"
            f'🔗 <a href="{payment_url}">Nhấn vào đây để thanh toán</a>\n\n'
            f"Sau khi thanh toán, nhấn '✅ Đã thanh toán? Kiểm tra' bên dưới."
        )
        await query.edit_message_text(
            msg,
            parse_mode=ParseMode.HTML,
            reply_markup=order_buttons(order_code),
            disable_web_page_preview=True
        )
    else:
        await query.edit_message_text(
            f"❌ Lỗi tạo link thanh toán: {html.escape(str(error))}",
            reply_markup=None
        )


async def check_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    try:
        order_code = int(query.data.split("_")[1])
    except (ValueError, IndexError):
        await query.edit_message_text("❌ Dữ liệu không hợp lệ.")
        return

    order = get_order(order_code)
    if not order:
        await query.edit_message_text("⚠️ Không tìm thấy đơn hàng.", reply_markup=None)
        return

    if order["status"] == "paid":
        await query.edit_message_text(
            f"✅ Đơn hàng #{order_code} đã thanh toán.\n🔑 Key: <code>{html.escape(order['key_assigned'] or '')}</code>",
            parse_mode=ParseMode.HTML
        )
        return
    if order["status"] == "cancelled":
        await query.edit_message_text(
            f"❌ Đơn hàng #{order_code} đã bị hủy.", reply_markup=None
        )
        return

    data = get_payment_status(order_code)
    paid = bool(
        data
        and data.get("code") == "00"
        and data.get("data", {}).get("status") == "PAID"
    )

    if paid:
        key = get_available_key(order["product_id"])
        if key:
            update_order_status(order_code, "paid", key)
            await query.edit_message_text(
                f"✅ Thanh toán thành công!\n🔑 Key: <code>{html.escape(key)}</code>",
                parse_mode=ParseMode.HTML
            )
        else:
            await query.edit_message_text(
                "⚠️ Đã thanh toán nhưng hết key. Liên hệ admin để được cấp.",
                reply_markup=None
            )
    else:
        await query.edit_message_text(
            f"⏳ Đơn hàng #{order_code} chưa được thanh toán.\n"
            f"Vui lòng thanh toán qua link hoặc hủy.",
            reply_markup=order_buttons(order_code)
        )


async def cancel_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    try:
        order_code = int(query.data.split("_")[1])
    except (ValueError, IndexError):
        await query.edit_message_text("❌ Dữ liệu không hợp lệ.")
        return
    order = get_order(order_code)
    if not order:
        await query.edit_message_text("Không tìm thấy đơn hàng.")
        return
    if order["status"] != "pending":
        await query.edit_message_text(
            f"Đơn hàng đã ở trạng thái {order['status']}, không thể hủy."
        )
        return
    update_order_status(order_code, "cancelled")
    await query.edit_message_text(f"❌ Đã hủy đơn hàng #{order_code}.")


async def my_orders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    orders = get_pending_orders_by_user(query.from_user.id)
    if not orders:
        await query.edit_message_text("Bạn không có đơn hàng nào đang chờ.")
        return
    text = "📦 <b>Đơn hàng chờ thanh toán:</b>\n\n"
    for o in orders[:10]:
        prod = get_product(o["product_id"])
        name = render_name_html(prod["name"], prod.get("emoji_id")) if prod else "Không xác định"
        text += f"#{o['id']} - {name} - {o['amount']:,} VND\n"
    text += "\nDùng nút 'Kiểm tra' ở từng đơn để cập nhật."
    await query.edit_message_text(text, parse_mode=ParseMode.HTML)


# ============================================================
# ADMIN HANDLERS
# ============================================================
async def admin_add_product(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Cú pháp:
      /add <tên> <giá> <số_lượng> [keys]
      /add <tên>|<mô tả> <giá> <số_lượng> [keys]

    Quy tắc parse:
      - Nếu phần 5 là danh sách key (chứa dấu `,` hoặc chuỗi chữ) → dùng làm keys.
      - Nếu phần 5 là số hoặc `-` hoặc rỗng → KHÔNG có key, tồn kho = 0.
    """
    if update.effective_user.id not in Config.ADMIN_IDS:
        await update.message.reply_text("⛔ Bạn không có quyền.")
        return

    try:
        clean_text, emoji_id = extract_custom_emoji_from_message(update.message)
        parts = clean_text.split()

        if len(parts) < 4:
            await update.message.reply_text(
                "❌ <b>Thiếu tham số.</b>\n\n"
                "Cú pháp:\n"
                "<code>/add &lt;tên&gt; &lt;giá&gt; &lt;số_lượng&gt; [keys]</code>\n"
                "<code>/add &lt;tên&gt;|&lt;mô tả&gt; &lt;giá&gt; &lt;số_lượng&gt; [keys]</code>\n\n"
                "<b>Ví dụ có key:</b>\n"
                "<code>/add CapCut 50000 3 CC001,CC002,CC003</code>\n\n"
                "<b>Ví dụ chưa có key (nạp sau):</b>\n"
                "<code>/add CapCut 50000 10</code>\n"
                "<code>/add CapCut 50000 10 -</code>",
                parse_mode=ParseMode.HTML
            )
            return

        # Tên + mô tả
        name_part = parts[1]
        if "|" in name_part:
            name, description = name_part.split("|", 1)
            name, description = name.strip(), description.strip()
        else:
            name, description = name_part.strip(), ""

        price_str = parts[2]
        stock_str = parts[3]
        keys_str = parts[4] if len(parts) >= 5 else "-"

        # Parse giá
        try:
            price = int(price_str.replace(".", "").replace(",", "").strip())
            if price <= 0:
                raise ValueError()
        except ValueError:
            await update.message.reply_text(
                f"❌ Giá không hợp lệ: <code>{html.escape(price_str)}</code>",
                parse_mode=ParseMode.HTML
            )
            return

        # --- Xác định keys ---
        keys_str = keys_str.strip()

        # Nếu keys_str là số hoặc "-" hoặc rỗng → không có key
        is_number = False
        try:
            int(keys_str)
            is_number = True
        except ValueError:
            pass

        if keys_str in ("-", "") or is_number:
            keys = []
        else:
            keys = [k.strip() for k in keys_str.split(",") if k.strip()]

        # --- Xác định stock ---
        if keys:
            stock = len(keys)
        else:
            try:
                stock = int(stock_str.strip())
                if stock < 0:
                    raise ValueError()
            except ValueError:
                await update.message.reply_text(
                    f"❌ Số lượng không hợp lệ: <code>{html.escape(stock_str)}</code>",
                    parse_mode=ParseMode.HTML
                )
                return
            # Không có key → tồn kho = 0 (chờ nạp key)
            stock = 0

        pid = add_product(name, description, price, stock, keys, emoji_id=emoji_id)

        name_html = render_name_html(name, emoji_id)
        desc_info = f"\n• Mô tả: {html.escape(description)}" if description else ""
        emoji_info = f"\n• Emoji ID: <code>{emoji_id}</code>" if emoji_id else ""

        await update.message.reply_text(
            f"✅ <b>Đã thêm sản phẩm ID</b> <code>{pid}</code>\n"
            f"• Tên: {name_html}{desc_info}\n"
            f"• Giá: {price:,} VND\n"
            f"• Số lượng: {stock}\n"
            f"• Keys: {len(keys)}{emoji_info}\n\n"
            f"Nạp key bằng: <code>/addkey {pid} &lt;key1,key2,...&gt;</code>\n"
            f"Xem chi tiết: <code>/detail {pid}</code>",
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        logger.error(f"admin_add_product error: {e}", exc_info=True)
        await update.message.reply_text(f"❌ Lỗi: {html.escape(str(e))}")


async def admin_import_products(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Import sản phẩm từ file .txt đính kèm.
    Format:
      Tên|Mô tả|Giá|Key1,Key2,Key3
      Tên|Giá|Key1,Key2,Key3        (không mô tả)
      Tên|Mô tả|Giá|                 (không key)
    Dòng bắt đầu bằng # là comment.
    """
    if update.effective_user.id not in Config.ADMIN_IDS:
        await update.message.reply_text("⛔ Bạn không có quyền.")
        return

    doc = update.message.document
    if not doc:
        return

    filename = doc.file_name or "unknown.txt"
    if not filename.lower().endswith(".txt"):
        await update.message.reply_text("❌ Chỉ chấp nhận file .txt")
        return

    try:
        tg_file = await doc.get_file()
        raw = await tg_file.download_as_bytearray()
        try:
            content = raw.decode("utf-8")
        except UnicodeDecodeError:
            try:
                content = raw.decode("utf-8-sig")
            except Exception:
                content = raw.decode("latin-1")
    except Exception as e:
        await update.message.reply_text(f"❌ Không đọc được file: {html.escape(str(e))}")
        return

    success, failed = [], []

    for line_no, line in enumerate(content.splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue

        try:
            parts = [p.strip() for p in line.split("|")]
            if len(parts) < 3:
                failed.append((line_no, line, "cần ≥ 3 phần"))
                continue

            if len(parts) == 3:
                name, description, price_str, keys_str = parts[0], "", parts[1], parts[2]
            else:
                name, description, price_str = parts[0], parts[1], parts[2]
                keys_str = parts[3] if len(parts) >= 4 else ""

            if not name:
                failed.append((line_no, line, "thiếu tên"))
                continue

            try:
                price = int(price_str.replace(".", "").replace(",", "").strip())
                if price <= 0:
                    raise ValueError()
            except ValueError:
                failed.append((line_no, line, f"giá không hợp lệ: {price_str}"))
                continue

            keys = []
            if keys_str and keys_str != "-":
                keys = [k.strip() for k in keys_str.split(",") if k.strip()]

            stock = len(keys)
            pid = add_product(name, description, price, stock, keys, emoji_id=None)
            success.append((pid, name, price, stock))

        except Exception as e:
            failed.append((line_no, line, str(e)))

    report = f"📥 <b>Import file:</b> <code>{html.escape(filename)}</code>\n\n"
    report += f"✅ Thành công: <b>{len(success)}</b>\n"
    report += f"❌ Thất bại: <b>{len(failed)}</b>\n\n"

    if success:
        report += "<b>Đã thêm:</b>\n"
        for pid, name, price, stock in success[:20]:
            report += f"• <code>{pid}</code> {html.escape(name)} - {price:,}đ - {stock} key\n"
        if len(success) > 20:
            report += f"<i>... và {len(success) - 20} sản phẩm khác</i>\n"

    if failed:
        report += "\n<b>Lỗi:</b>\n"
        for line_no, line, err in failed[:10]:
            report += f"• Dòng {line_no}: {html.escape(err)}\n"
        if len(failed) > 10:
            report += f"<i>... và {len(failed) - 10} lỗi khác</i>\n"

    await update.message.reply_text(report, parse_mode=ParseMode.HTML)


async def admin_add_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in Config.ADMIN_IDS:
        await update.message.reply_text("⛔ Bạn không có quyền.")
        return
    try:
        parts = update.message.text.split(maxsplit=2)
        if len(parts) < 3:
            await update.message.reply_text(
                "Cú pháp: <code>/addkey &lt;product_id&gt; &lt;key1,key2,...&gt;</code>",
                parse_mode=ParseMode.HTML
            )
            return
        product_id = int(parts[1])
        new_keys = [k.strip() for k in parts[2].split(",") if k.strip()]
        if not new_keys:
            await update.message.reply_text("❌ Cần ít nhất 1 key.")
            return
        product = get_product(product_id)
        if not product:
            await update.message.reply_text(f"❌ Không tìm thấy SP <code>{product_id}</code>.")
            return
        existing = json.loads(product["keys"] or "[]")
        existing.extend(new_keys)
        with get_db() as conn:
            conn.execute(
                "UPDATE products SET keys = ?, stock = stock + ? WHERE id = ?",
                (json.dumps(existing), len(new_keys), product_id)
            )
        await update.message.reply_text(
            f"✅ Đã thêm <b>{len(new_keys)}</b> key vào <code>{product_id}</code>\n"
            f"• Tồn kho mới: {product['stock'] + len(new_keys)}",
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        await update.message.reply_text(f"❌ Lỗi: {html.escape(str(e))}")


async def admin_set_emoji(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in Config.ADMIN_IDS:
        await update.message.reply_text("⛔ Bạn không có quyền.")
        return
    try:
        clean_text, emoji_id = extract_custom_emoji_from_message(update.message)
        parts = clean_text.split()
        if len(parts) < 2:
            await update.message.reply_text(
                "Cú pháp: <code>/setemoji &lt;product_id&gt; [dán emoji Premium]</code>",
                parse_mode=ParseMode.HTML
            )
            return
        product_id = int(parts[1])
        if not emoji_id:
            await update.message.reply_text("❌ Không tìm thấy custom emoji Premium.")
            return
        product = get_product(product_id)
        if not product:
            await update.message.reply_text(f"❌ Không tìm thấy SP <code>{product_id}</code>.")
            return
        with get_db() as conn:
            conn.execute("UPDATE products SET emoji_id = ? WHERE id = ?", (emoji_id, product_id))
        name_html = render_name_html(product["name"], emoji_id)
        await update.message.reply_text(
            f"✅ Đã đặt emoji cho <code>{product_id}</code>\n"
            f"• {name_html}",
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        await update.message.reply_text(f"❌ Lỗi: {html.escape(str(e))}")


async def admin_edit_description(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cú pháp: /setdesc <product_id> <mô tả mới>"""
    if update.effective_user.id not in Config.ADMIN_IDS:
        await update.message.reply_text("⛔ Bạn không có quyền.")
        return
    try:
        parts = update.message.text.split(maxsplit=2)
        if len(parts) < 3:
            await update.message.reply_text(
                "Cú pháp: <code>/setdesc &lt;product_id&gt; &lt;mô tả&gt;</code>",
                parse_mode=ParseMode.HTML
            )
            return
        product_id = int(parts[1])
        new_desc = parts[2].strip()
        product = get_product(product_id)
        if not product:
            await update.message.reply_text(f"❌ Không tìm thấy SP <code>{product_id}</code>.")
            return
        with get_db() as conn:
            conn.execute("UPDATE products SET description = ? WHERE id = ?", (new_desc, product_id))
        await update.message.reply_text(
            f"✅ Đã cập nhật mô tả SP <code>{product_id}</code>:\n\n{html.escape(new_desc)}",
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        await update.message.reply_text(f"❌ Lỗi: {html.escape(str(e))}")


async def admin_detail(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cú pháp: /detail <product_id>"""
    if update.effective_user.id not in Config.ADMIN_IDS:
        await update.message.reply_text("⛔ Bạn không có quyền.")
        return
    try:
        parts = update.message.text.split()
        if len(parts) < 2:
            await update.message.reply_text(
                "Cú pháp: <code>/detail &lt;product_id&gt;</code>",
                parse_mode=ParseMode.HTML
            )
            return
        product_id = int(parts[1])
        product = get_product(product_id)
        if not product:
            await update.message.reply_text(f"❌ Không tìm thấy SP <code>{product_id}</code>.")
            return
        name_html = render_name_html(product["name"], product.get("emoji_id"))
        desc_html = html.escape(product.get("description") or "(không có mô tả)")
        keys = json.loads(product["keys"] or "[]")

        text = (
            f"📦 <b>Chi tiết sản phẩm #{product['id']}</b>\n\n"
            f"• <b>Tên:</b> {name_html}\n"
            f"• <b>Mô tả:</b> {desc_html}\n"
            f"• <b>Giá:</b> {product['price']:,} VND\n"
            f"• <b>Tồn kho:</b> {product['stock']}\n"
            f"• <b>Đã bán:</b> {product['sold']}\n"
            f"• <b>Emoji ID:</b> <code>{product.get('emoji_id') or 'không có'}</code>\n\n"
            f"<b>Keys còn lại ({len(keys)}):</b>\n"
        )
        for k in keys[:10]:
            text += f"  <code>{html.escape(k)}</code>\n"
        if len(keys) > 10:
            text += f"  <i>... và {len(keys) - 10} key khác</i>\n"

        await update.message.reply_text(text, parse_mode=ParseMode.HTML)
    except Exception as e:
        await update.message.reply_text(f"❌ Lỗi: {html.escape(str(e))}")


async def admin_list_products(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in Config.ADMIN_IDS:
        await update.message.reply_text("⛔ Bạn không có quyền.")
        return
    products = list_all_products()
    if not products:
        await update.message.reply_text("Chưa có sản phẩm nào.")
        return
    text = "📋 <b>Toàn bộ sản phẩm:</b>\n\n"
    for p in products:
        name_html = render_name_html(p["name"], p.get("emoji_id"))
        text += (
            f"<code>{p['id']}</code> • {name_html} • {p['price']:,}đ • "
            f"kho: {p['stock']} • đã bán: {p['sold']}\n"
        )
    text += "\nChi tiết: <code>/detail &lt;id&gt;</code> • Xoá: <code>/del &lt;id&gt;</code>"
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


async def admin_delete_product(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cú pháp: /del <product_id>"""
    if update.effective_user.id not in Config.ADMIN_IDS:
        await update.message.reply_text("⛔ Bạn không có quyền.")
        return
    try:
        parts = update.message.text.split()
        if len(parts) < 2:
            await update.message.reply_text(
                "Cú pháp: <code>/del &lt;product_id&gt;</code>\n"
                "Xóa tất cả: <code>/delall confirm</code>",
                parse_mode=ParseMode.HTML
            )
            return
        product_id = int(parts[1])
        product = get_product(product_id)
        if not product:
            await update.message.reply_text(f"❌ Không tìm thấy SP <code>{product_id}</code>.")
            return
        ok = delete_product(product_id)
        if ok:
            name_html = render_name_html(product["name"], product.get("emoji_id"))
            await update.message.reply_text(
                f"🗑️ Đã xóa SP <code>{product_id}</code> - {name_html}",
                parse_mode=ParseMode.HTML
            )
        else:
            await update.message.reply_text("❌ Xóa thất bại.")
    except Exception as e:
        await update.message.reply_text(f"❌ Lỗi: {html.escape(str(e))}")


async def admin_delete_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cú pháp: /delall confirm"""
    if update.effective_user.id not in Config.ADMIN_IDS:
        await update.message.reply_text("⛔ Bạn không có quyền.")
        return
    parts = update.message.text.split()
    if len(parts) < 2 or parts[1].lower() != "confirm":
        await update.message.reply_text(
            "⚠️ <b>Cảnh báo:</b> Xóa TẤT CẢ sản phẩm.\n"
            "Xác nhận: <code>/delall confirm</code>",
            parse_mode=ParseMode.HTML
        )
        return
    count = delete_all_products()
    await update.message.reply_text(f"🗑️ Đã xóa <b>{count}</b> sản phẩm.", parse_mode=ParseMode.HTML)


async def admin_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "🛠️ <b>Lệnh admin:</b>\n\n"
        "<b>Thêm sản phẩm:</b>\n"
        "• <code>/add Tên Giá SL Keys</code>\n"
        "• <code>/add Tên|Mô tả Giá SL Keys</code>\n"
        "• <code>/add Tên Giá 10</code> (chưa có key, nạp sau)\n"
        "• Gửi file <b>.txt</b> để import hàng loạt\n\n"
        "<b>Quản lý:</b>\n"
        "• <code>/list</code> - Xem tất cả\n"
        "• <code>/detail &lt;id&gt;</code> - Xem chi tiết\n"
        "• <code>/del &lt;id&gt;</code> - Xóa 1\n"
        "• <code>/delall confirm</code> - Xóa hết\n\n"
        "<b>Cập nhật:</b>\n"
        "• <code>/addkey &lt;id&gt; K1,K2</code> - Thêm key\n"
        "• <code>/setdesc &lt;id&gt; Mô tả</code> - Sửa mô tả\n"
        "• <code>/setemoji &lt;id&gt; [dán emoji Premium]</code>\n\n"
        "<b>Format file .txt:</b>\n"
        "<code>Tên|Mô tả|Giá|Key1,Key2,Key3</code>\n"
        "<code>Tên|Giá|Key1,Key2</code>\n"
        "Dòng bắt đầu bằng <code>#</code> là comment."
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


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

        logger.info(f"PayOS webhook body: {json.dumps(body, ensure_ascii=False)[:800]}")

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
                        await app.bot.send_message(
                            chat_id=order["user_id"],
                            text=f"✅ Thanh toán thành công!\n🔑 Key: <code>{html.escape(key)}</code>",
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

    # Admin commands
    app.add_handler(CommandHandler("add", admin_add_product))
    app.add_handler(CommandHandler("addkey", admin_add_key))
    app.add_handler(CommandHandler("setemoji", admin_set_emoji))
    app.add_handler(CommandHandler("setdesc", admin_edit_description))
    app.add_handler(CommandHandler("detail", admin_detail))
    app.add_handler(CommandHandler("list", admin_list_products))
    app.add_handler(CommandHandler("del", admin_delete_product))
    app.add_handler(CommandHandler("delall", admin_delete_all))
    app.add_handler(CommandHandler("help", admin_help))

    # Import file .txt (chỉ admin)
    app.add_handler(MessageHandler(
        filters.Document.FileExtension("txt") & filters.User(Config.ADMIN_IDS),
        admin_import_products
    ))

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
