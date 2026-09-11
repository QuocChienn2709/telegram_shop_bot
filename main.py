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
    get_product, list_products, count_products, list_all_products, count_all_products,
    get_available_key, create_order, get_order, update_order_status,
    get_pending_orders_by_user, get_db, register_user,
    get_user_lang, set_user_lang, get_all_user_ids, count_users,
    get_setting, set_setting, delete_setting, get_all_settings,
    get_text, set_text, delete_text, get_all_texts,
    get_binance_address, set_binance_address,
    get_binance_network, set_binance_network,
    get_usdt_rate, set_usdt_rate
)
from payos_client import (
    create_payment_link, verify_payment_webhook, get_payment_status
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)
init_db()


# ============================================================
# I18N - ĐA NGÔN NGỮ
# ============================================================
DEFAULT_TEXTS = {
    "vi": {
        "shop_empty":   "Cửa hàng hiện chưa có sản phẩm.",
        "shop_title":   "Cửa hàng tài khoản Pro",
        "shop_prompt":  "Chọn sản phẩm bên dưới:",
        "no_more":      "Không còn sản phẩm nào.",
        "list_title":   "Danh sách sản phẩm (trang {page})",
        "btn_prev":     "Trước",
        "btn_next":     "Sau",
        "btn_orders":   "Đơn hàng chờ",
        "btn_buy":      "Mua ngay",
        "btn_back":     "Quay lại",
        "btn_check":    "Đã thanh toán? Kiểm tra",
        "btn_cancel":   "Hủy đơn",
        "not_found":    "Không tìm thấy sản phẩm.",
        "out_of_stock": "Sản phẩm đã hết hàng.",
        "invalid_data": "Dữ liệu không hợp lệ.",
        "detail_title": "Chi tiết sản phẩm",
        "detail_name":  "Tên",
        "detail_desc":  "Mô tả",
        "detail_price": "Giá",
        "detail_stock": "Tồn kho",
        "detail_sold":  "Đã bán",
        "detail_no_desc":"(không có mô tả)",
        "order_title":  "Đơn hàng",
        "order_product":"Sản phẩm",
        "order_amount": "Số tiền",
        "order_pay":    "Nhấn để thanh toán",
        "order_hint":   "Sau khi thanh toán, nhấn 'Đã thanh toán? Kiểm tra' bên dưới.",
        "order_not_found":"Không tìm thấy đơn hàng.",
        "order_paid":   "Đơn hàng đã thanh toán.",
        "order_cancelled":"Đơn hàng đã bị hủy.",
        "order_pending":"chưa được thanh toán. Vui lòng thanh toán qua link hoặc hủy.",
        "order_success":"Thanh toán thành công!",
        "order_no_key":"Đã thanh toán nhưng hết key. Liên hệ admin.",
        "order_cannot_cancel":"Không thể hủy đơn hàng này.",
        "order_cancelled_ok":"Đã hủy đơn hàng",
        "pending_title": "Đơn hàng chờ thanh toán",
        "pending_empty": "Bạn không có đơn hàng nào đang chờ.",
        "account_info":  "Thông tin tài khoản",
        "account_user":  "Tài khoản",
        "account_pass":  "Mật khẩu",
        "payment_method_title":"Chọn phương thức thanh toán:",
        "btn_pay_payos": "Thanh toán VND (PayOS)",
        "btn_pay_binance":"Thanh toán USDT (Binance)",
        "binance_title": "Thanh toán Binance USDT",
        "binance_amount":"Số tiền",
        "binance_address":"Địa chỉ ví",
        "binance_network":"Mạng",
        "binance_note":  "Chuyển đúng số tiền và mạng. Nội dung: mã đơn hàng.",
        "binance_not_set":"Admin chưa cấu hình ví Binance.",
        "binance_sent":  "Tôi đã chuyển khoản",
        "binance_waiting":"Đang chờ admin xác nhận. Vui lòng đợi.",
        "lang_changed":  "Đã đổi ngôn ngữ: Tiếng Việt",
        "lang_choose":   "Chọn ngôn ngữ:",
        "btn_lang_vi":   "Tiếng Việt",
        "btn_lang_en":   "English",
    },
    "en": {
        "shop_empty":   "No products available yet.",
        "shop_title":   "Pro Account Shop",
        "shop_prompt":  "Choose a product below:",
        "no_more":      "No more products.",
        "list_title":   "Products (page {page})",
        "btn_prev":     "Prev",
        "btn_next":     "Next",
        "btn_orders":   "Pending orders",
        "btn_buy":      "Buy now",
        "btn_back":     "Back",
        "btn_check":    "Paid? Check now",
        "btn_cancel":   "Cancel order",
        "not_found":    "Product not found.",
        "out_of_stock": "Out of stock.",
        "invalid_data": "Invalid data.",
        "detail_title": "Product details",
        "detail_name":  "Name",
        "detail_desc":  "Description",
        "detail_price": "Price",
        "detail_stock": "Stock",
        "detail_sold":  "Sold",
        "detail_no_desc":"(no description)",
        "order_title":  "Order",
        "order_product":"Product",
        "order_amount": "Amount",
        "order_pay":    "Click to pay",
        "order_hint":   "After payment, press 'Paid? Check now' below.",
        "order_not_found":"Order not found.",
        "order_paid":   "Order already paid.",
        "order_cancelled":"Order cancelled.",
        "order_pending":"not paid yet. Please pay via link or cancel.",
        "order_success":"Payment successful!",
        "order_no_key":"Paid but out of keys. Contact admin.",
        "order_cannot_cancel":"Cannot cancel this order.",
        "order_cancelled_ok":"Order cancelled",
        "pending_title": "Pending orders",
        "pending_empty": "You have no pending orders.",
        "account_info":  "Account info",
        "account_user":  "Username",
        "account_pass":  "Password",
        "payment_method_title":"Choose payment method:",
        "btn_pay_payos": "Pay VND (PayOS)",
        "btn_pay_binance":"Pay USDT (Binance)",
        "binance_title": "Binance USDT Payment",
        "binance_amount":"Amount",
        "binance_address":"Wallet address",
        "binance_network":"Network",
        "binance_note":  "Send exact amount on correct network. Note: order code.",
        "binance_not_set":"Binance wallet not configured by admin.",
        "binance_sent":  "I have sent",
        "binance_waiting":"Waiting for admin confirmation. Please wait.",
        "lang_changed":  "Language changed to English",
        "lang_choose":   "Choose language:",
        "btn_lang_vi":   "Tiếng Việt",
        "btn_lang_en":   "English",
    }
}


def t(chat_or_user, key, **kwargs) -> str:
    """
    Lấy text theo ngôn ngữ user. Override bằng DB `texts` nếu có.
    """
    if hasattr(chat_or_user, "id"):
        uid = chat_or_user.id
    else:
        uid = chat_or_user
    lang = get_user_lang(uid)
    if lang not in DEFAULT_TEXTS:
        lang = "vi"
    # DB override
    override = get_text(f"{lang}_{key}")
    if override:
        s = override
    else:
        s = DEFAULT_TEXTS[lang].get(key) or DEFAULT_TEXTS["vi"].get(key) or key
    if kwargs:
        try:
            return s.format(**kwargs)
        except Exception:
            return s
    return s


# ============================================================
# SAFE HTML SENDER
# ============================================================
_TG_EMOJI_RE = re.compile(r'<tg-emoji[^>]*>(.*?)</tg-emoji>', re.DOTALL)


def _strip_tg_emoji(text: str) -> str:
    return _TG_EMOJI_RE.sub(r'\1', text)


def _is_entity_error(exc: Exception) -> bool:
    s = str(exc).lower()
    return "entity_text_invalid" in s or "can't parse entities" in s or "entity" in s


async def safe_reply(message, text, **kwargs):
    try:
        return await message.reply_text(text, parse_mode=ParseMode.HTML, **kwargs)
    except Exception as e:
        if _is_entity_error(e):
            logger.warning(f"safe_reply fallback: {e}")
            return await message.reply_text(_strip_tg_emoji(text), parse_mode=ParseMode.HTML, **kwargs)
        raise


async def safe_edit(query, text, **kwargs):
    try:
        return await query.edit_message_text(text, parse_mode=ParseMode.HTML, **kwargs)
    except Exception as e:
        if _is_entity_error(e):
            logger.warning(f"safe_edit fallback: {e}")
            return await query.edit_message_text(_strip_tg_emoji(text), parse_mode=ParseMode.HTML, **kwargs)
        raise


async def safe_send(bot, chat_id, text, **kwargs):
    try:
        return await bot.send_message(chat_id=chat_id, text=text, parse_mode=ParseMode.HTML, **kwargs)
    except Exception as e:
        if _is_entity_error(e):
            return await bot.send_message(chat_id=chat_id, text=_strip_tg_emoji(text), parse_mode=ParseMode.HTML, **kwargs)
        raise


# ============================================================
# KEY FORMATTER
# ============================================================
def format_key_display(key: str, lang: str = "vi") -> str:
    if not key:
        return ""
    user_label = "Tài khoản" if lang == "vi" else "Username"
    pass_label = "Mật khẩu" if lang == "vi" else "Password"
    if "|" in key:
        acc, _, pwd = key.partition("|")
        return f"{user_label}: <code>{html.escape(acc.strip())}</code>\n{pass_label}: <code>{html.escape(pwd.strip())}</code>"
    if ":" in key:
        acc, _, pwd = key.partition(":")
        return f"{user_label}: <code>{html.escape(acc.strip())}</code>\n{pass_label}: <code>{html.escape(pwd.strip())}</code>"
    return f"<code>{html.escape(key)}</code>"


# ============================================================
# UI EMOJI
# ============================================================
UI_KEYS = {
    "shop": "Tiêu đề cửa hàng / Shop title",
    "cart": "Nút mua / Buy button",
    "orders": "Nút đơn hàng / Orders button",
    "back": "Nút quay lại / Back",
    "next": "Trang sau / Next page",
    "prev": "Trang trước / Prev page",
    "check": "Kiểm tra thanh toán / Check payment",
    "cancel": "Hủy đơn / Cancel order",
    "pay": "Thanh toán / Pay",
    "cart": "Nút mua / Buy",
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


async def validate_custom_emoji(bot, chat_id, emoji_id) -> bool:
    """Test xem bot có gửi được custom emoji này không."""
    try:
        msg = await bot.send_message(
            chat_id=chat_id,
            text=f'<tg-emoji emoji-id="{emoji_id}">🎁</tg-emoji>',
            parse_mode=ParseMode.HTML
        )
        await msg.delete()
        return True
    except Exception as e:
        logger.info(f"Emoji validation failed: {e}")
        return False


# ============================================================
# UI BUILDERS
# ============================================================
def product_buttons(products, page=0, per_page=5, uid=None):
    keyboard = []
    for p in products:
        text = f"{p['name']} - {p['price']:,}đ - còn {p['stock']}"
        kwargs = {"text": text, "callback_data": f"buy_{p['id']}"}
        if p.get("emoji_id"):
            kwargs["icon_custom_emoji_id"] = p["emoji_id"]
        keyboard.append([InlineKeyboardButton(**kwargs)])

    nav = []
    if page > 0:
        nav.append(button(t(uid, "btn_prev"), callback_data=f"page_{page-1}", ui_key="prev"))
    if len(products) == per_page:
        nav.append(button(t(uid, "btn_next"), callback_data=f"page_{page+1}", ui_key="next"))
    if nav:
        keyboard.append(nav)
    keyboard.append([button(t(uid, "btn_orders"), callback_data="my_orders", ui_key="orders")])
    return InlineKeyboardMarkup(keyboard)


def order_buttons(order_id, uid=None):
    return InlineKeyboardMarkup([
        [button(t(uid, "btn_check"), callback_data=f"check_{order_id}", ui_key="check")],
        [button(t(uid, "btn_cancel"), callback_data=f"cancel_{order_id}", ui_key="cancel")]
    ])


def detail_buttons(product_id, uid=None):
    return InlineKeyboardMarkup([
        [button(t(uid, "btn_buy"), callback_data=f"buy_{product_id}", ui_key="cart")],
        [button(t(uid, "btn_back"), callback_data="back_list", ui_key="back")]
    ])


def payment_buttons(order_id, uid=None):
    return InlineKeyboardMarkup([
        [button(t(uid, "btn_pay_payos"), callback_data=f"pay_payos_{order_id}", ui_key="pay")],
        [button(t(uid, "btn_pay_binance"), callback_data=f"pay_binance_{order_id}", ui_key="pay")],
        [button(t(uid, "btn_cancel"), callback_data=f"cancel_{order_id}", ui_key="cancel")]
    ])


# ============================================================
# USER HANDLERS
# ============================================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    register_user(user.id, user.username, user.first_name, user.last_name)
    prods = list_products(limit=5, offset=0)
    if not prods:
        await safe_reply(update.message, t(user.id, "shop_empty"))
        return
    header = ui_emoji_html("shop")
    title = t(user.id, "shop_title")
    title_html = f"{header} <b>{html.escape(title)}</b>" if header else f"<b>{html.escape(title)}</b>"
    prompt = t(user.id, "shop_prompt")
    await safe_reply(
        update.message,
        f"{title_html}\n\n{html.escape(prompt)}",
        reply_markup=product_buttons(prods, page=0, uid=user.id)
    )


async def lang_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(t(uid, "btn_lang_vi"), callback_data="setlang_vi")],
        [InlineKeyboardButton(t(uid, "btn_lang_en"), callback_data="setlang_en")]
    ])
    await safe_reply(update.message, t(uid, "lang_choose"), reply_markup=kb)


async def setlang_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    lang = query.data.split("_")[1]
    if lang not in ("vi", "en"):
        return
    set_user_lang(query.from_user.id, lang)
    await safe_edit(query, t(query.from_user.id, "lang_changed"), reply_markup=None)


async def list_products_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    data = query.data
    page = 0
    if data.startswith("page_"):
        try:
            page = max(0, int(data.split("_")[1]))
        except ValueError:
            page = 0
    products = list_products(limit=5, offset=page * 5)
    if not products:
        await safe_edit(query, t(uid, "no_more"), reply_markup=None)
        return
    await safe_edit(
        query,
        f"<b>{html.escape(t(uid, 'list_title', page=page + 1))}</b>",
        reply_markup=product_buttons(products, page, uid=uid)
    )


async def show_product_detail(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    try:
        product_id = int(query.data.split("_")[1])
    except (ValueError, IndexError):
        return
    product = get_product(product_id)
    if not product:
        await safe_edit(query, t(uid, "not_found"))
        return
    name_html = product_name_html(product["name"], product.get("emoji_id"))
    desc_html = html.escape(product.get("description") or t(uid, "detail_no_desc"))
    text = (
        f"<b>{html.escape(t(uid, 'detail_title'))} #{product['id']}</b>\n\n"
        f"<b>{html.escape(t(uid, 'detail_name'))}:</b> {name_html}\n"
        f"<b>{html.escape(t(uid, 'detail_price'))}:</b> {product['price']:,} VND\n"
        f"<b>{html.escape(t(uid, 'detail_stock'))}:</b> {product['stock']}\n"
        f"<b>{html.escape(t(uid, 'detail_sold'))}:</b> {product['sold']}\n\n"
        f"<b>{html.escape(t(uid, 'detail_desc'))}:</b>\n{desc_html}"
    )
    await safe_edit(query, text, reply_markup=detail_buttons(product_id, uid=uid))


async def buy_product(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """User nhấn mua → tạo đơn → chọn phương thức thanh toán."""
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    try:
        product_id = int(query.data.split("_")[1])
    except (ValueError, IndexError):
        await safe_edit(query, t(uid, "invalid_data"), reply_markup=None)
        return
    product = get_product(product_id)
    if not product or product["stock"] <= 0:
        await safe_edit(query, t(uid, "out_of_stock"), reply_markup=None)
        return

    order_code = int(f"{int(datetime.now().timestamp())}{product_id:03d}{uid % 1000:03d}")
    create_order(order_code, uid, product_id, 1, product["price"])

    # Lưu temp để dùng khi chọn phương thức
    context.bot_data[f"order_{order_code}"] = {"product_id": product_id, "user_id": uid}

    name_html = product_name_html(product["name"], product.get("emoji_id"))
    text = (
        f"<b>{html.escape(t(uid, 'order_title'))} #{order_code}</b>\n\n"
        f"<b>{html.escape(t(uid, 'order_product'))}:</b> {name_html}\n"
        f"<b>{html.escape(t(uid, 'order_amount'))}:</b> {product['price']:,} VND\n\n"
        f"{html.escape(t(uid, 'payment_method_title'))}"
    )
    await safe_edit(query, text, reply_markup=payment_buttons(order_code, uid=uid))


async def pay_payos_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    try:
        order_code = int(query.data.split("_")[2])
    except (ValueError, IndexError):
        return
    order = get_order(order_code)
    if not order:
        await safe_edit(query, t(uid, "order_not_found"), reply_markup=None)
        return
    product = get_product(order["product_id"])

    # Description = mã đơn hàng để dễ tra soát
    desc_payos = f"DH{order_code}"
    payment_url, error = create_payment_link(
        order_code=order_code, amount=order["amount"],
        description=desc_payos, buyer_name=query.from_user.full_name
    )
    if not payment_url:
        await safe_edit(query, f"Lỗi PayOS: {html.escape(str(error))}", reply_markup=None)
        return

    name_html = product_name_html(product["name"], product.get("emoji_id")) if product else str(order["product_id"])
    text = (
        f"<b>{html.escape(t(uid, 'order_title'))} #{order_code}</b>\n\n"
        f"<b>{html.escape(t(uid, 'order_product'))}:</b> {name_html}\n"
        f"<b>{html.escape(t(uid, 'order_amount'))}:</b> {order['amount']:,} VND\n"
        f"<b>Nội dung CK:</b> <code>DH{order_code}</code>\n\n"
        f'<a href="{payment_url}">{html.escape(t(uid, "order_pay"))}</a>\n\n'
        f"{html.escape(t(uid, 'order_hint'))}"
    )
    await safe_edit(query, text,
                    reply_markup=order_buttons(order_code, uid=uid),
                    disable_web_page_preview=True)


async def pay_binance_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    try:
        order_code = int(query.data.split("_")[2])
    except (ValueError, IndexError):
        return
    order = get_order(order_code)
    if not order:
        await safe_edit(query, t(uid, "order_not_found"), reply_markup=None)
        return
    addr = get_binance_address()
    if not addr:
        await safe_edit(query, t(uid, "binance_not_set"), reply_markup=None)
        return
    network = get_binance_network()
    rate = get_usdt_rate()
    usdt_amount = round(order["amount"] / rate, 2)

    text = (
        f"<b>{html.escape(t(uid, 'binance_title'))}</b>\n"
        f"Order #{order_code}\n\n"
        f"<b>{html.escape(t(uid, 'binance_amount'))}:</b> <code>{usdt_amount} USDT</code>\n"
        f"<b>{html.escape(t(uid, 'binance_address'))}:</b>\n<code>{html.escape(addr)}</code>\n"
        f"<b>{html.escape(t(uid, 'binance_network'))}:</b> <b>{html.escape(network)}</b>\n"
        f"<b>Nội dung/Memo:</b> <code>DH{order_code}</code>\n\n"
        f"{html.escape(t(uid, 'binance_note'))}"
    )
    kb = InlineKeyboardMarkup([
        [button(t(uid, "binance_sent"), callback_data=f"binance_sent_{order_code}", ui_key="check")],
        [button(t(uid, "btn_cancel"), callback_data=f"cancel_{order_code}", ui_key="cancel")]
    ])
    await safe_edit(query, text, reply_markup=kb, disable_web_page_preview=True)


async def binance_sent_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    try:
        order_code = int(query.data.split("_")[2])
    except (ValueError, IndexError):
        return
    await safe_edit(query, t(uid, "binance_waiting"), reply_markup=None)

    # Thông báo cho admin
    order = get_order(order_code)
    if order:
        product = get_product(order["product_id"])
        rate = get_usdt_rate()
        usdt = round(order["amount"] / rate, 2)
        admin_text = (
            f"<b>Yêu cầu xác nhận Binance</b>\n"
            f"Order: <code>{order_code}</code>\n"
            f"User: <code>{uid}</code>\n"
            f"Sản phẩm: {html.escape(product['name']) if product else '?'}\n"
            f"Số tiền: {order['amount']:,} VND ≈ {usdt} USDT\n\n"
            f"Xác nhận: <code>/confirm {order_code}</code>"
        )
        for aid in Config.ADMIN_IDS:
            try:
                await safe_send(context.bot, aid, admin_text)
            except Exception as e:
                logger.error(f"Notify admin {aid} failed: {e}")


async def check_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Chỉ dùng cho PayOS. Binance do admin confirm thủ công."""
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    try:
        order_code = int(query.data.split("_")[1])
    except (ValueError, IndexError):
        await safe_edit(query, t(uid, "invalid_data"))
        return
    order = get_order(order_code)
    if not order:
        await safe_edit(query, t(uid, "order_not_found"), reply_markup=None)
        return
    if order["status"] == "paid":
        await safe_edit(
            query,
            f"{html.escape(t(uid, 'order_paid'))}\n\n"
            f"<b>{html.escape(t(uid, 'account_info'))}:</b>\n"
            f"{format_key_display(order['key_assigned'] or '', get_user_lang(uid))}"
        )
        return
    if order["status"] == "cancelled":
        await safe_edit(query, t(uid, "order_cancelled"), reply_markup=None)
        return

    data = get_payment_status(order_code)
    paid = bool(data and data.get("code") == "00" and data.get("data", {}).get("status") == "PAID")
    if paid:
        key = get_available_key(order["product_id"])
        if key:
            update_order_status(order_code, "paid", key)
            await safe_edit(
                query,
                f"{html.escape(t(uid, 'order_success'))}\n\n"
                f"<b>{html.escape(t(uid, 'account_info'))}:</b>\n"
                f"{format_key_display(key, get_user_lang(uid))}"
            )
        else:
            await safe_edit(query, t(uid, "order_no_key"), reply_markup=None)
    else:
        await safe_edit(
            query,
            f"#{order_code} {html.escape(t(uid, 'order_pending'))}",
            reply_markup=order_buttons(order_code, uid=uid)
        )


async def cancel_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    try:
        order_code = int(query.data.split("_")[1])
    except (ValueError, IndexError):
        return
    order = get_order(order_code)
    if not order or order["status"] != "pending":
        await safe_edit(query, t(uid, "order_cannot_cancel"))
        return
    update_order_status(order_code, "cancelled")
    await safe_edit(query, f"{html.escape(t(uid, 'order_cancelled_ok'))} #{order_code}.")


async def my_orders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    orders = get_pending_orders_by_user(uid)
    if not orders:
        await safe_edit(query, t(uid, "pending_empty"))
        return
    text = f"<b>{html.escape(t(uid, 'pending_title'))}:</b>\n\n"
    for o in orders[:10]:
        prod = get_product(o["product_id"])
        name = product_name_html(prod["name"], prod.get("emoji_id")) if prod else "?"
        text += f"#{o['id']} - {name} - {o['amount']:,} VND\n"
    await safe_edit(query, text)


# ============================================================
# ADMIN - PRODUCTS
# ============================================================
async def admin_add_product(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in Config.ADMIN_IDS:
        await safe_reply(update.message, "⛔")
        return
    try:
        clean_text, emoji_id = extract_custom_emoji_from_message(update.message)
        parts = clean_text.split(maxsplit=4)
        if len(parts) < 4:
            await safe_reply(
                update.message,
                "<b>Cú pháp:</b>\n"
                "<code>/add &lt;tên&gt; &lt;giá&gt; &lt;số_lượng&gt; [keys]</code>\n"
                "<code>/add &lt;tên&gt;|&lt;mô tả&gt; &lt;giá&gt; &lt;số_lượng&gt; [keys]</code>\n\n"
                "<b>Key tài khoản:</b>\n"
                "<code>/add Netflix 100000 2 user1@gmail.com|pass1,user2@gmail.com|pass2</code>"
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

        # Validate emoji trước khi lưu
        emoji_note = ""
        if emoji_id:
            ok = await validate_custom_emoji(context.bot, update.effective_user.id, emoji_id)
            if not ok:
                emoji_note = "\n⚠️ Emoji không hiển thị được (bot không sở hữu). Đã bỏ qua."
                emoji_id = None

        pid = add_product(name, description, price, stock, keys, emoji_id=emoji_id)
        preview = ""
        if keys:
            preview = f"\n\n<b>Key mẫu:</b>\n{format_key_display(keys[0])}"
        await safe_reply(
            update.message,
            f"Đã thêm sản phẩm ID <code>{pid}</code>\n"
            f"Tên: {html.escape(name)}\n"
            f"Giá: {price:,} VND\n"
            f"Số lượng: {stock}\n"
            f"Keys: {len(keys)}{emoji_note}{preview}"
        )
    except Exception as e:
        logger.error(f"admin_add_product error: {e}", exc_info=True)
        await safe_reply(update.message, f"Lỗi: {html.escape(str(e))}")


async def admin_import_products(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in Config.ADMIN_IDS:
        return
    doc = update.message.document
    if not doc or not (doc.file_name or "").lower().endswith(".txt"):
        return
    try:
        tg_file = await doc.get_file()
        raw = await tg_file.download_as_bytearray()
        try:
            content = raw.decode("utf-8")
        except UnicodeDecodeError:
            content = raw.decode("utf-8-sig", errors="ignore")
    except Exception as e:
        await safe_reply(update.message, f"Lỗi đọc file: {html.escape(str(e))}")
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
                failed.append((line_no, f"giá lỗi: {price_str}")); continue
            keys = [k.strip() for k in keys_str.split(",") if k.strip()] if keys_str and keys_str != "-" else []
            pid = add_product(name, description, price, len(keys), keys, emoji_id=None)
            success.append((pid, name, price, len(keys)))
        except Exception as e:
            failed.append((line_no, str(e)))

    report = f"<b>Import {html.escape(doc.file_name or '')}</b>\n"
    report += f"✅ {len(success)} | ❌ {len(failed)}\n\n"
    if success:
        for pid, name, price, stock in success[:20]:
            report += f"<code>{pid}</code> {html.escape(name)} - {price:,}đ - {stock} key\n"
    if failed:
        report += "\n<b>Lỗi:</b>\n"
        for line_no, err in failed[:10]:
            report += f"Dòng {line_no}: {html.escape(err)}\n"
    await safe_reply(update.message, report)


async def admin_add_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in Config.ADMIN_IDS:
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
            f"Đã thêm {len(new_keys)} key vào <code>{product_id}</code>\nTồn kho mới: {product['stock'] + len(new_keys)}"
        )
    except Exception as e:
        await safe_reply(update.message, f"Lỗi: {html.escape(str(e))}")


async def admin_set_product_emoji(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in Config.ADMIN_IDS:
        return
    try:
        clean_text, emoji_id = extract_custom_emoji_from_message(update.message)
        parts = clean_text.split()
        if len(parts) < 2:
            await safe_reply(update.message, "Cú pháp: <code>/setemoji &lt;id&gt; [dán emoji Premium]</code>")
            return
        product_id = int(parts[1])
        if not emoji_id:
            await safe_reply(update.message, "Không tìm thấy custom emoji.")
            return
        product = get_product(product_id)
        if not product:
            await safe_reply(update.message, f"Không tìm thấy SP <code>{product_id}</code>.")
            return
        ok = await validate_custom_emoji(context.bot, update.effective_user.id, emoji_id)
        if not ok:
            await safe_reply(update.message, "⚠️ Bot không có quyền dùng emoji này. Không lưu.")
            return
        db = get_db()
        db.products.update_one({"id": product_id}, {"$set": {"emoji_id": emoji_id}})
        await safe_reply(update.message, f"Đã đặt emoji cho SP <code>{product_id}</code>.")
    except Exception as e:
        await safe_reply(update.message, f"Lỗi: {html.escape(str(e))}")


async def admin_setdesc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in Config.ADMIN_IDS:
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
        await safe_reply(update.message, f"Đã cập nhật mô tả SP <code>{product_id}</code>.")
    except Exception as e:
        await safe_reply(update.message, f"Lỗi: {html.escape(str(e))}")


async def admin_detail(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in Config.ADMIN_IDS:
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
        for i, k in enumerate(keys[:10], 1):
            text += f"  {i}. {format_key_display(k)}\n"
        if len(keys) > 10:
            text += f"  <i>... và {len(keys) - 10} key khác</i>\n"
        await safe_reply(update.message, text)
    except Exception as e:
        await safe_reply(update.message, f"Lỗi: {html.escape(str(e))}")


async def admin_list_products(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """FIX: /list phân trang để tránh vượt 4096 ký tự."""
    if update.effective_user.id not in Config.ADMIN_IDS:
        return
    total = count_all_products()
    if total == 0:
        await safe_reply(update.message, "Chưa có sản phẩm nào.")
        return
    products = list_all_products(limit=20, offset=0)
    text = f"<b>Danh sách SP ({len(products)}/{total}):</b>\n\n"
    for p in products:
        name = html.escape(p["name"])[:30]
        text += f"<code>{p['id']}</code> {name} - {p['price']:,}đ - kho:{p['stock']} - bán:{p['sold']}\n"
    if total > 20:
        text += f"\n<i>... và {total - 20} SP khác. Xem tiếp:</i> <code>/list2</code>"
    text += "\n\n<code>/detail &lt;id&gt;</code> - <code>/del &lt;id&gt;</code>"
    await safe_reply(update.message, text)


async def admin_list2(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in Config.ADMIN_IDS:
        return
    total = count_all_products()
    products = list_all_products(limit=20, offset=20)
    if not products:
        await safe_reply(update.message, "Hết danh sách.")
        return
    text = f"<b>Trang 2/{(total - 1) // 20 + 1}:</b>\n\n"
    for p in products:
        name = html.escape(p["name"])[:30]
        text += f"<code>{p['id']}</code> {name} - {p['price']:,}đ - kho:{p['stock']} - bán:{p['sold']}\n"
    await safe_reply(update.message, text)


async def admin_delete_product(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in Config.ADMIN_IDS:
        return
    try:
        parts = update.message.text.split()
        if len(parts) < 2:
            await safe_reply(update.message, "Cú pháp: <code>/del &lt;id&gt;</code>")
            return
        product_id = int(parts[1])
        product = get_product(product_id)
        if not product:
            await safe_reply(update.message, f"Không tìm thấy SP <code>{product_id}</code>.")
            return
        if delete_product(product_id):
            await safe_reply(update.message, f"Đã xóa SP <code>{product_id}</code> - {html.escape(product['name'])}")
    except Exception as e:
        await safe_reply(update.message, f"Lỗi: {html.escape(str(e))}")


async def admin_delete_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in Config.ADMIN_IDS:
        return
    parts = update.message.text.split()
    if len(parts) < 2 or parts[1].lower() != "confirm":
        await safe_reply(update.message, "Xóa TẤT CẢ SP. Xác nhận: <code>/delall confirm</code>")
        return
    count = delete_all_products()
    await safe_reply(update.message, f"Đã xóa {count} sản phẩm.")


# ============================================================
# ADMIN - ORDER CONFIRM (Binance)
# ============================================================
async def admin_confirm_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Xác nhận đơn Binance đã thanh toán → giao key."""
    if update.effective_user.id not in Config.ADMIN_IDS:
        return
    try:
        parts = update.message.text.split()
        if len(parts) < 2:
            await safe_reply(update.message, "Cú pháp: <code>/confirm &lt;order_code&gt;</code>")
            return
        order_code = int(parts[1])
        order = get_order(order_code)
        if not order:
            await safe_reply(update.message, "Không tìm thấy đơn.")
            return
        if order["status"] != "pending":
            await safe_reply(update.message, f"Đơn ở trạng thái <b>{order['status']}</b>, không cần confirm.")
            return
        key = get_available_key(order["product_id"])
        if not key:
            await safe_reply(update.message, "Hết key. Nạp thêm trước.")
            return
        update_order_status(order_code, "paid", key)
        await safe_reply(update.message, f"Đã xác nhận đơn <code>{order_code}</code>.")
        # Thông báo cho user
        lang = get_user_lang(order["user_id"])
        try:
            await safe_send(
                context.bot, order["user_id"],
                f"<b>{html.escape(t(lang, 'order_success'))}</b>\n\n"
                f"<b>{html.escape(t(lang, 'account_info'))}:</b>\n"
                f"{format_key_display(key, lang)}"
            )
        except Exception as e:
            logger.error(f"Notify user failed: {e}")
    except Exception as e:
        await safe_reply(update.message, f"Lỗi: {html.escape(str(e))}")


# ============================================================
# ADMIN - BROADCAST
# ============================================================
async def admin_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in Config.ADMIN_IDS:
        return
    parts = update.message.text.split(maxsplit=1)
    if len(parts) < 2:
        await safe_reply(
            update.message,
            "Cú pháp: <code>/broadcast &lt;nội dung&gt;</code>\n"
            "Hỗ trợ HTML: <code>&lt;b&gt;đậm&lt;/b&gt;</code>, <code>&lt;i&gt;nghiêng&lt;/i&gt;</code>, <code>&lt;a href=...&gt;link&lt;/a&gt;</code>"
        )
        return
    msg = parts[1]
    users = get_all_user_ids()
    sent, failed = 0, 0
    for uid in users:
        try:
            await safe_send(context.bot, uid, msg, disable_web_page_preview=True)
            sent += 1
            await asyncio.sleep(0.05)  # tránh rate-limit
        except Exception as e:
            failed += 1
            logger.warning(f"Broadcast fail {uid}: {e}")
    await safe_reply(update.message, f"Broadcast xong.\n✅ {sent} | ❌ {failed}")


# ============================================================
# ADMIN - UI EMOJI
# ============================================================
async def admin_setui(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in Config.ADMIN_IDS:
        return
    try:
        clean_text, emoji_id = extract_custom_emoji_from_message(update.message)
        parts = clean_text.split()
        if len(parts) < 2:
            await safe_reply(
                update.message,
                "<b>Cú pháp:</b> <code>/setui &lt;key&gt; [emoji]</code>\n\n" +
                "\n".join(f"- <code>{k}</code> - {v}" for k, v in UI_KEYS.items())
            )
            return
        key = parts[1].lower()
        if key not in UI_KEYS:
            await safe_reply(update.message, f"Key không hợp lệ: <code>{html.escape(key)}</code>")
            return
        if not emoji_id:
            await safe_reply(update.message, "Không tìm thấy custom emoji.")
            return
        # VALIDATE emoji
        ok = await validate_custom_emoji(context.bot, update.effective_user.id, emoji_id)
        if not ok:
            await safe_reply(
                update.message,
                "⚠️ <b>Bot không có quyền dùng emoji này.</b>\n"
                "Emoji sẽ hiển thị ở dạng fallback hoặc lỗi.\n"
                "Vẫn lưu? Dùng <code>/setui_force &lt;key&gt; [emoji]</code> để ép."
            )
            return
        set_setting(f"ui_{key}", emoji_id)
        await safe_reply(update.message, f"✅ Đã đặt emoji cho <code>{key}</code>.")
    except Exception as e:
        await safe_reply(update.message, f"Lỗi: {html.escape(str(e))}")


async def admin_setui_force(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in Config.ADMIN_IDS:
        return
    try:
        clean_text, emoji_id = extract_custom_emoji_from_message(update.message)
        parts = clean_text.split()
        if len(parts) < 2 or not emoji_id:
            await safe_reply(update.message, "Cú pháp: <code>/setui_force &lt;key&gt; [emoji]</code>")
            return
        key = parts[1].lower()
        if key not in UI_KEYS:
            await safe_reply(update.message, "Key không hợp lệ.")
            return
        set_setting(f"ui_{key}", emoji_id)
        await safe_reply(update.message, f"⚠️ Đã ép lưu emoji cho <code>{key}</code> (có thể không hiển thị).")
    except Exception as e:
        await safe_reply(update.message, f"Lỗi: {html.escape(str(e))}")


async def admin_viewui(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in Config.ADMIN_IDS:
        return
    settings = {s["key"]: s["emoji_id"] for s in get_all_settings()}
    text = "<b>UI Emoji:</b>\n\n"
    for k, desc in UI_KEYS.items():
        eid = settings.get(f"ui_{k}")
        if eid:
            text += f"• <code>{k}</code> - {desc} - <code>{eid}</code>\n"
        else:
            text += f"• <code>{k}</code> - {desc} - <i>chưa</i>\n"
    await safe_reply(update.message, text)


async def admin_delui(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in Config.ADMIN_IDS:
        return
    parts = update.message.text.split()
    if len(parts) < 2:
        await safe_reply(update.message, "Cú pháp: <code>/delui &lt;key&gt;</code>")
        return
    key = parts[1].lower()
    if key not in UI_KEYS:
        await safe_reply(update.message, "Key không hợp lệ.")
        return
    delete_setting(f"ui_{key}")
    await safe_reply(update.message, f"Đã xóa emoji cho <code>{key}</code>.")


# ============================================================
# ADMIN - EDITABLE TEXTS
# ============================================================
TEXT_KEYS_INFO = {
    "shop_empty": "Thông báo khi cửa hàng trống",
    "shop_title": "Tiêu đề cửa hàng",
    "shop_prompt": "Dòng 'Chọn sản phẩm bên dưới'",
    "pending_empty": "Khi user không có đơn chờ",
    "order_hint": "Hướng dẫn sau thanh toán",
    "order_success": "Thông báo thanh toán thành công",
    "binance_note": "Lưu ý khi thanh toán Binance",
}


async def admin_settext(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Cú pháp: /settext <lang> <key> <value>
    Ví dụ: /settext vi shop_title 🏪 Cửa hàng của tôi
    """
    if update.effective_user.id not in Config.ADMIN_IDS:
        return
    try:
        parts = update.message.text.split(maxsplit=3)
        if len(parts) < 4:
            lines = ["Cú pháp: <code>/settext &lt;lang&gt; &lt;key&gt; &lt;value&gt;</code>"]
            lines.append("lang: <code>vi</code> hoặc <code>en</code>")
            lines.append("Key có thể dùng:\n" + "\n".join(f"• <code>{k}</code> - {v}" for k, v in TEXT_KEYS_INFO.items()))
            await safe_reply(update.message, "\n".join(lines))
            return
        lang, key, value = parts[1].lower(), parts[2].lower(), parts[3]
        if lang not in ("vi", "en"):
            await safe_reply(update.message, "Lang phải là <code>vi</code> hoặc <code>en</code>.")
            return
        set_text(f"{lang}_{key}", value)
        await safe_reply(update.message, f"✅ Đã đổi <code>{lang}_{key}</code>:\n\n{html.escape(value)}")
    except Exception as e:
        await safe_reply(update.message, f"Lỗi: {html.escape(str(e))}")


async def admin_viewtext(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in Config.ADMIN_IDS:
        return
    overrides = {t["key"]: t["value"] for t in get_all_texts()}
    text = "<b>Texts đang override:</b>\n\n"
    if not overrides:
        text += "<i>Chưa có override nào. Dùng giá trị mặc định.</i>\n\n"
    else:
        for k, v in overrides.items():
            text += f"• <code>{k}</code>\n  <i>{html.escape(v[:100])}</i>\n"
    text += "\n<b>Key có thể chỉnh:</b>\n"
    for k, desc in TEXT_KEYS_INFO.items():
        text += f"• <code>{k}</code> - {desc}\n"
    text += "\nĐặt: <code>/settext vi shop_title Nội dung mới</code>\nXóa override: <code>/deltext vi shop_title</code>"
    await safe_reply(update.message, text)


async def admin_deltext(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in Config.ADMIN_IDS:
        return
    parts = update.message.text.split()
    if len(parts) < 3:
        await safe_reply(update.message, "Cú pháp: <code>/deltext &lt;lang&gt; &lt;key&gt;</code>")
        return
    lang, key = parts[1].lower(), parts[2].lower()
    delete_text(f"{lang}_{key}")
    await safe_reply(update.message, f"Đã xóa override <code>{lang}_{key}</code>.")


# ============================================================
# ADMIN - BINANCE SETTINGS
# ============================================================
async def admin_setbinance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Cú pháp: /setbinance <address> [network]
    Ví dụ: /setbinance TXXX... TRC20
    """
    if update.effective_user.id not in Config.ADMIN_IDS:
        return
    parts = update.message.text.split()
    if len(parts) < 2:
        await safe_reply(
            update.message,
            "Cú pháp: <code>/setbinance &lt;địa_chỉ_ví&gt; [network]</code>\n"
            "Network: <code>TRC20</code> (mặc định), <code>BEP20</code>, <code>ERC20</code>"
        )
        return
    addr = parts[1]
    network = parts[2].upper() if len(parts) >= 3 else "TRC20"
    if network not in ("TRC20", "BEP20", "ERC20", "POLYGON"):
        await safe_reply(update.message, "Network phải là TRC20 / BEP20 / ERC20 / POLYGON.")
        return
    set_binance_address(addr)
    set_binance_network(network)
    await safe_reply(
        update.message,
        f"✅ Đã đặt ví Binance:\n"
        f"• Address: <code>{html.escape(addr)}</code>\n"
        f"• Network: <b>{network}</b>"
    )


async def admin_setrate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cú pháp: /setrate <VND_per_USDT>"""
    if update.effective_user.id not in Config.ADMIN_IDS:
        return
    parts = update.message.text.split()
    if len(parts) < 2:
        await safe_reply(update.message, f"Tỷ giá hiện tại: <code>{get_usdt_rate():,}</code> VND/USDT\nĐặt: <code>/setrate 25000</code>")
        return
    try:
        rate = int(parts[1].replace(".", "").replace(",", ""))
        if rate <= 0: raise ValueError()
        set_usdt_rate(rate)
        await safe_reply(update.message, f"✅ Đã đặt tỷ giá: <code>{rate:,}</code> VND/USDT")
    except ValueError:
        await safe_reply(update.message, "Tỷ giá không hợp lệ.")


async def admin_viewbinance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in Config.ADMIN_IDS:
        return
    addr = get_binance_address()
    net = get_binance_network()
    rate = get_usdt_rate()
    text = (
        f"<b>Binance settings</b>\n\n"
        f"• Address: <code>{html.escape(addr) if addr else '(chưa đặt)'}</code>\n"
        f"• Network: <b>{net}</b>\n"
        f"• Rate: <code>{rate:,}</code> VND/USDT\n"
        f"• Users: <code>{count_users()}</code>"
    )
    await safe_reply(update.message, text)


# ============================================================
# ADMIN HELP
# ============================================================
async def admin_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "<b>Lệnh admin:</b>\n\n"
        "<b>Sản phẩm:</b>\n"
        "<code>/add Tên Giá SL [Keys]</code>\n"
        "<code>/add Tên|Mô_tả Giá SL [Keys]</code>\n"
        "Gửi file <b>.txt</b> để import\n"
        "<code>/list</code> - xem 20 SP đầu\n"
        "<code>/list2</code> - xem tiếp 20 SP\n"
        "<code>/detail &lt;id&gt;</code>\n"
        "<code>/del &lt;id&gt;</code>\n"
        "<code>/delall confirm</code>\n\n"
        "<b>Keys:</b>\n"
        "<code>/addkey &lt;id&gt; K1,K2</code>\n"
        "<code>/setdesc &lt;id&gt; Mô tả</code>\n"
        "<code>/setemoji &lt;id&gt; [emoji]</code>\n\n"
        "<b>Binance:</b>\n"
        "<code>/setbinance &lt;address&gt; [network]</code>\n"
        "<code>/setrate &lt;VND_per_USDT&gt;</code>\n"
        "<code>/viewbinance</code>\n"
        "<code>/confirm &lt;order_code&gt;</code>\n\n"
        "<b>UI Emoji:</b>\n"
        "<code>/setui &lt;key&gt; [emoji]</code>\n"
        "<code>/setui_force &lt;key&gt; [emoji]</code>\n"
        "<code>/viewui</code> | <code>/delui &lt;key&gt;</code>\n\n"
        "<b>Texts:</b>\n"
        "<code>/settext &lt;vi|en&gt; &lt;key&gt; &lt;value&gt;</code>\n"
        "<code>/viewtext</code> | <code>/deltext &lt;lang&gt; &lt;key&gt;</code>\n\n"
        "<b>Khác:</b>\n"
        "<code>/broadcast &lt;msg&gt;</code> - gửi all users\n"
        "<code>/stats</code> - thống kê"
    )
    await safe_reply(update.message, text)


async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in Config.ADMIN_IDS:
        return
    text = (
        f"<b>Thống kê</b>\n\n"
        f"• Users: <code>{count_users()}</code>\n"
        f"• Sản phẩm: <code>{count_all_products()}</code>\n"
        f"• SP còn hàng: <code>{count_products()}</code>\n"
        f"• Ví Binance: <code>{html.escape(get_binance_address()) or 'chưa đặt'}</code>\n"
        f"• Network: <b>{get_binance_network()}</b>\n"
        f"• Tỷ giá: <code>{get_usdt_rate():,}</code> VND/USDT"
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
                    lang = get_user_lang(order["user_id"])
                    try:
                        await safe_send(
                            app.bot, order["user_id"],
                            f"<b>{html.escape(t(lang, 'order_success'))}</b>\n\n"
                            f"<b>{html.escape(t(lang, 'account_info'))}:</b>\n"
                            f"{format_key_display(key, lang)}"
                        )
                    except Exception as e:
                        logger.error(f"Notify failed: {e}")
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
    app.add_handler(CommandHandler("lang", lang_cmd))

    # Admin products
    app.add_handler(CommandHandler("add", admin_add_product))
    app.add_handler(CommandHandler("addkey", admin_add_key))
    app.add_handler(CommandHandler("setemoji", admin_set_product_emoji))
    app.add_handler(CommandHandler("setdesc", admin_setdesc))
    app.add_handler(CommandHandler("detail", admin_detail))
    app.add_handler(CommandHandler("list", admin_list_products))
    app.add_handler(CommandHandler("list2", admin_list2))
    app.add_handler(CommandHandler("del", admin_delete_product))
    app.add_handler(CommandHandler("delall", admin_delete_all))

    # Binance
    app.add_handler(CommandHandler("setbinance", admin_setbinance))
    app.add_handler(CommandHandler("setrate", admin_setrate))
    app.add_handler(CommandHandler("viewbinance", admin_viewbinance))
    app.add_handler(CommandHandler("confirm", admin_confirm_order))

    # Broadcast + stats
    app.add_handler(CommandHandler("broadcast", admin_broadcast))
    app.add_handler(CommandHandler("stats", admin_stats))

    # UI emoji
    app.add_handler(CommandHandler("setui", admin_setui))
    app.add_handler(CommandHandler("setui_force", admin_setui_force))
    app.add_handler(CommandHandler("viewui", admin_viewui))
    app.add_handler(CommandHandler("delui", admin_delui))

    # Texts
    app.add_handler(CommandHandler("settext", admin_settext))
    app.add_handler(CommandHandler("viewtext", admin_viewtext))
    app.add_handler(CommandHandler("deltext", admin_deltext))

    app.add_handler(CommandHandler("help", admin_help))

    # Import .txt
    app.add_handler(MessageHandler(
        filters.Document.FileExtension("txt") & filters.User(Config.ADMIN_IDS),
        admin_import_products))

    # Callbacks
    app.add_handler(CallbackQueryHandler(setlang_callback, pattern=r"^setlang_"))
    app.add_handler(CallbackQueryHandler(list_products_callback, pattern=r"^page_"))
    app.add_handler(CallbackQueryHandler(show_product_detail, pattern=r"^detail_\d+$"))
    app.add_handler(CallbackQueryHandler(buy_product, pattern=r"^buy_"))
    app.add_handler(CallbackQueryHandler(pay_payos_callback, pattern=r"^pay_payos_"))
    app.add_handler(CallbackQueryHandler(pay_binance_callback, pattern=r"^pay_binance_"))
    app.add_handler(CallbackQueryHandler(binance_sent_callback, pattern=r"^binance_sent_"))
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
