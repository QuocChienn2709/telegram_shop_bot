# main.py
import asyncio, html, json, logging, os, re
from datetime import datetime
from aiohttp import web
from aiohttp.web import Request, Response
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (Application, CommandHandler, CallbackQueryHandler,
                          ContextTypes, MessageHandler, filters)
from telegram.constants import ParseMode

from config import Config
from database import (
    init_db, add_product, delete_product, delete_all_products,
    get_product, list_products, count_products, list_all_products, count_all_products,
    get_available_key, create_order, get_order, update_order_status,
    get_pending_orders_by_user, get_db, register_user,
    get_user_lang, set_user_lang, is_lang_set, get_all_user_ids, count_users,
    get_setting, set_setting, delete_setting, get_all_settings,
    get_text, get_text_raw, set_text, delete_text, get_all_texts,
    get_binance_address, set_binance_address, get_binance_network,
    set_binance_network, get_usdt_rate, set_usdt_rate
)
from payos_client import create_payment_link, verify_payment_webhook, get_payment_status

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)
init_db()


# ============================================================
# I18N
# ============================================================
DEFAULT_TEXTS = {
    "vi": {
        "shop_empty": "Cửa hàng hiện chưa có sản phẩm.",
        "shop_title": "Cửa hàng tài khoản Pro",
        "shop_prompt": "Chọn sản phẩm bên dưới:",
        "no_more": "Không còn sản phẩm nào.",
        "list_title": "Danh sách sản phẩm (trang {page})",
        "btn_prev": "Trước", "btn_next": "Sau", "btn_orders": "Đơn hàng chờ",
        "btn_buy": "Mua ngay", "btn_back": "Quay lại",
        "btn_check": "Đã thanh toán? Kiểm tra", "btn_cancel": "Hủy đơn",
        "btn_back_pay": "Quay lại thanh toán", "btn_back_menu": "Quay lại menu",
        "not_found": "Không tìm thấy sản phẩm.",
        "out_of_stock": "Sản phẩm đã hết hàng.",
        "invalid_data": "Dữ liệu không hợp lệ.",
        "detail_title": "Chi tiết sản phẩm", "detail_name": "Tên", "detail_desc": "Mô tả",
        "detail_price": "Giá", "detail_stock": "Tồn kho", "detail_sold": "Đã bán",
        "detail_no_desc": "(không có mô tả)",
        "order_title": "Đơn hàng", "order_product": "Sản phẩm",
        "order_amount": "Số tiền", "order_pay": "Nhấn để thanh toán",
        "order_content": "Nội dung CK",
        "order_hint": "Sau khi thanh toán, nhấn 'Đã thanh toán? Kiểm tra' bên dưới.",
        "order_not_found": "Không tìm thấy đơn hàng.",
        "order_paid": "Đơn hàng đã thanh toán.",
        "order_cancelled": "Đơn hàng đã bị hủy.",
        "order_pending": "chưa được thanh toán. Vui lòng thanh toán hoặc hủy.",
        "order_success": "Thanh toán thành công!",
        "order_no_key": "Đã thanh toán nhưng hết key. Liên hệ admin.",
        "order_cannot_cancel": "Không thể hủy đơn hàng này.",
        "order_cancelled_ok": "Đã hủy đơn hàng",
        "pending_title": "Đơn hàng chờ thanh toán",
        "pending_empty": "Bạn không có đơn hàng nào đang chờ.",
        "account_info": "Thông tin tài khoản",
        "account_user": "Tài khoản", "account_pass": "Mật khẩu",
        "payment_method_title": "Chọn phương thức thanh toán:",
        "btn_pay_payos": "Thanh toán VND (PayOS)",
        "btn_pay_binance": "Thanh toán USDT (Binance)",
        "binance_title": "Thanh toán Binance USDT",
        "binance_amount": "Số tiền", "binance_address": "Địa chỉ ví",
        "binance_network": "Mạng", "binance_memo": "Nội dung/Memo",
        "binance_note": "Chuyển đúng số tiền và mạng.",
        "binance_not_set": "Admin chưa cấu hình ví Binance.",
        "binance_sent": "Tôi đã chuyển khoản",
        "binance_waiting": "Đang chờ admin xác nhận.",
        "lang_changed": "Đã đổi ngôn ngữ: Tiếng Việt",
        "lang_choose": "Chọn ngôn ngữ:",
        "lang_required": "Vui lòng chọn ngôn ngữ trước:",
        "btn_lang_vi": "Tiếng Việt", "btn_lang_en": "English",
    },
    "en": {
        "shop_empty": "No products available yet.",
        "shop_title": "Pro Account Shop",
        "shop_prompt": "Choose a product below:",
        "no_more": "No more products.",
        "list_title": "Products (page {page})",
        "btn_prev": "Prev", "btn_next": "Next", "btn_orders": "Pending orders",
        "btn_buy": "Buy now", "btn_back": "Back",
        "btn_check": "Paid? Check now", "btn_cancel": "Cancel order",
        "btn_back_pay": "Back to payment", "btn_back_menu": "Back to menu",
        "not_found": "Product not found.",
        "out_of_stock": "Out of stock.",
        "invalid_data": "Invalid data.",
        "detail_title": "Product details", "detail_name": "Name", "detail_desc": "Description",
        "detail_price": "Price", "detail_stock": "Stock", "detail_sold": "Sold",
        "detail_no_desc": "(no description)",
        "order_title": "Order", "order_product": "Product",
        "order_amount": "Amount", "order_pay": "Click to pay",
        "order_content": "Payment ref",
        "order_hint": "After payment, press 'Paid? Check now' below.",
        "order_not_found": "Order not found.",
        "order_paid": "Order already paid.",
        "order_cancelled": "Order cancelled.",
        "order_pending": "not paid yet.",
        "order_success": "Payment successful!",
        "order_no_key": "Paid but out of keys. Contact admin.",
        "order_cannot_cancel": "Cannot cancel this order.",
        "order_cancelled_ok": "Order cancelled",
        "pending_title": "Pending orders",
        "pending_empty": "You have no pending orders.",
        "account_info": "Account info",
        "account_user": "Username", "account_pass": "Password",
        "payment_method_title": "Choose payment method:",
        "btn_pay_payos": "Pay VND (PayOS)",
        "btn_pay_binance": "Pay USDT (Binance)",
        "binance_title": "Binance USDT Payment",
        "binance_amount": "Amount", "binance_address": "Wallet address",
        "binance_network": "Network", "binance_memo": "Memo",
        "binance_note": "Send exact amount on correct network.",
        "binance_not_set": "Binance wallet not configured.",
        "binance_sent": "I have sent",
        "binance_waiting": "Waiting for admin confirmation.",
        "lang_changed": "Language changed to English",
        "lang_choose": "Choose language:",
        "lang_required": "Please select a language:",
        "btn_lang_vi": "Tiếng Việt", "btn_lang_en": "English",
    }
}


def t(user_id, key, **kwargs):
    lang = get_user_lang(user_id)
    if lang not in DEFAULT_TEXTS: lang = "vi"
    ov = get_text(f"{lang}_{key}")
    if ov:
        s = ov
    else:
        s = DEFAULT_TEXTS[lang].get(key) or DEFAULT_TEXTS["vi"].get(key) or key
    if kwargs:
        try: return s.format(**kwargs)
        except Exception: return s
    return s


# ============================================================
# SAFE HTML
# ============================================================
_TG_EMOJI_RE = re.compile(r'<tg-emoji[^>]*>(.*?)</tg-emoji>', re.DOTALL)
def _strip_tg_emoji(text): return _TG_EMOJI_RE.sub(r'\1', text)
def _is_entity_error(exc):
    s = str(exc).lower()
    return "entity_text_invalid" in s or "can't parse entities" in s or "entity" in s

async def safe_reply(message, text, **kw):
    try:
        return await message.reply_text(text, parse_mode=ParseMode.HTML, **kw)
    except Exception as e:
        if _is_entity_error(e):
            logger.warning(f"safe_reply fallback: {e}")
            return await message.reply_text(_strip_tg_emoji(text), parse_mode=ParseMode.HTML, **kw)
        raise

async def safe_edit(query, text, **kw):
    try:
        return await query.edit_message_text(text, parse_mode=ParseMode.HTML, **kw)
    except Exception as e:
        if _is_entity_error(e):
            logger.warning(f"safe_edit fallback: {e}")
            return await query.edit_message_text(_strip_tg_emoji(text), parse_mode=ParseMode.HTML, **kw)
        raise

async def safe_send(bot, chat_id, text, **kw):
    try:
        return await bot.send_message(chat_id=chat_id, text=text, parse_mode=ParseMode.HTML, **kw)
    except Exception as e:
        if _is_entity_error(e):
            return await bot.send_message(chat_id=chat_id, text=_strip_tg_emoji(text), parse_mode=ParseMode.HTML, **kw)
        raise


# ============================================================
# KEY FORMAT
# ============================================================
def format_key_display(key, lang="vi"):
    if not key: return ""
    ul = "Tài khoản" if lang == "vi" else "Username"
    pl = "Mật khẩu" if lang == "vi" else "Password"
    for sep in ("|", ":"):
        if sep in key:
            a, _, p = key.partition(sep)
            return f"{ul}: <code>{html.escape(a.strip())}</code>\n{pl}: <code>{html.escape(p.strip())}</code>"
    return f"<code>{html.escape(key)}</code>"


# ============================================================
# UI EMOJI
# ============================================================
UI_KEYS = {
    "shop": "Tiêu đề shop",
    "cart": "Nút mua",
    "orders": "Nút đơn hàng chờ",
    "back": "Nút quay lại",
    "next": "Nút trang sau",
    "prev": "Nút trang trước",
    "check": "Nút kiểm tra TT",
    "cancel": "Nút hủy đơn",
    "pay": "Nút thanh toán",
    "pay_payos": "Nút PayOS",
    "pay_binance": "Nút Binance",
    "binance": "Biểu tượng ví",
    "order": "Biểu tượng đơn hàng",
    "order_code": "Nhãn mã đơn",
    "money": "Nhãn tiền/giá",
    "product": "Nhãn sản phẩm",
    "detail": "Nhãn chi tiết",
    "back_pay": "Nút quay lại TT",
    "back_menu": "Nút quay lại menu",
    "lang": "Biểu tượng ngôn ngữ",
    "account": "Biểu tượng tài khoản",
    "key_icon": "Biểu tượng key",
}

def ui_emoji_html(key, fallback=""):
    eid = get_setting(f"ui_{key}")
    if eid: return f'<tg-emoji emoji-id="{eid}">{fallback or "•"}</tg-emoji>'
    return fallback

def ui_emoji_id(key): return get_setting(f"ui_{key}")

def button(text, callback_data=None, url=None, ui_key=None):
    kw = {"text": text}
    if callback_data: kw["callback_data"] = callback_data
    if url: kw["url"] = url
    if ui_key:
        eid = ui_emoji_id(ui_key)
        if eid: kw["icon_custom_emoji_id"] = eid
    return InlineKeyboardButton(**kw)


def extract_custom_emoji(message):
    """
    Trích custom emoji đầu tiên.
    Trả về (clean_text, emoji_id, emoji_char) - emoji_char là placeholder trong text gốc.
    Nếu không có emoji: (text, None, None)
    """
    text = message.text or message.caption or ""
    ents = message.entities or message.caption_entities or []
    ces = [e for e in ents if e.type == "custom_emoji"]
    if not ces:
        return text, None, None

    e = ces[0]
    eid = e.custom_emoji_id
    if not eid or not eid.isdigit():
        return text, None, None

    # Trích emoji char (placeholder) từ entity offset trong UTF-16
    enc = text.encode("utf-16-le")
    s, en = e.offset * 2, (e.offset + e.length) * 2
    emoji_char = enc[s:en].decode("utf-16-le")

    # Xóa tất cả custom emoji khỏi text (từ cuối lên đầu để giữ offset)
    for e2 in sorted(ces, key=lambda x: x.offset, reverse=True):
        s2, en2 = e2.offset * 2, (e2.offset + e2.length) * 2
        enc = enc[:s2] + enc[en2:]
    clean = enc.decode("utf-16-le")
    return clean, eid, emoji_char


def product_name_html(name, emoji_id=None):
    safe = html.escape(name)
    if emoji_id: return f'<tg-emoji emoji-id="{emoji_id}">•</tg-emoji> {safe}'
    return safe

async def validate_custom_emoji(bot, chat_id, emoji_id):
    try:
        m = await bot.send_message(chat_id=chat_id,
            text=f'<tg-emoji emoji-id="{emoji_id}">🎁</tg-emoji>',
            parse_mode=ParseMode.HTML)
        await m.delete()
        return True
    except Exception as e:
        logger.info(f"emoji validate fail: {e}")
        return False


# ============================================================
# BUTTON BUILDERS
# ============================================================
def product_buttons(products, page=0, per_page=5, uid=None):
    kb = []
    for p in products:
        text = f"{p['name']} - {p['price']:,}đ - còn {p['stock']}"
        kw = {"text": text, "callback_data": f"buy_{p['id']}"}
        if p.get("emoji_id"): kw["icon_custom_emoji_id"] = p["emoji_id"]
        kb.append([InlineKeyboardButton(**kw)])
    nav = []
    if page > 0:
        nav.append(button(t(uid, "btn_prev"), callback_data=f"page_{page-1}", ui_key="prev"))
    if len(products) == per_page:
        nav.append(button(t(uid, "btn_next"), callback_data=f"page_{page+1}", ui_key="next"))
    if nav: kb.append(nav)
    kb.append([button(t(uid, "btn_orders"), callback_data="my_orders", ui_key="orders")])
    return InlineKeyboardMarkup(kb)

def order_buttons(order_id, uid=None):
    return InlineKeyboardMarkup([
        [button(t(uid, "btn_check"), callback_data=f"check_{order_id}", ui_key="check")],
        [button(t(uid, "btn_back_pay"), callback_data=f"backpay_{order_id}", ui_key="back_pay")],
        [button(t(uid, "btn_cancel"), callback_data=f"cancel_{order_id}", ui_key="cancel")],
    ])

def detail_buttons(pid, uid=None):
    return InlineKeyboardMarkup([
        [button(t(uid, "btn_buy"), callback_data=f"buy_{pid}", ui_key="cart")],
        [button(t(uid, "btn_back"), callback_data="back_list", ui_key="back")],
    ])

def payment_buttons(oid, uid=None):
    return InlineKeyboardMarkup([
        [button(t(uid, "btn_pay_payos"), callback_data=f"pay_payos_{oid}", ui_key="pay_payos")],
        [button(t(uid, "btn_pay_binance"), callback_data=f"pay_binance_{oid}", ui_key="pay_binance")],
        [button(t(uid, "btn_cancel"), callback_data=f"cancel_{oid}", ui_key="cancel")],
    ])

def lang_buttons(uid=None):
    return InlineKeyboardMarkup([
        [button(t(uid, "btn_lang_vi"), callback_data="setlang_vi", ui_key="lang")],
        [button(t(uid, "btn_lang_en"), callback_data="setlang_en", ui_key="lang")],
    ])


# ============================================================
# USER HANDLERS
# ============================================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    register_user(user.id, user.username, user.first_name, user.last_name)
    if not is_lang_set(user.id):
        await safe_reply(update.message, t(user.id, "lang_required"), reply_markup=lang_buttons(uid=user.id))
        return
    prods = list_products(limit=5, offset=0)
    if not prods:
        await safe_reply(update.message, t(user.id, "shop_empty")); return
    header = ui_emoji_html("shop")
    title = t(user.id, "shop_title")
    title_html = f"{header} <b>{title}</b>" if header else f"<b>{title}</b>"
    await safe_reply(update.message, f"{title_html}\n\n{t(user.id, 'shop_prompt')}",
                     reply_markup=product_buttons(prods, page=0, uid=user.id))


async def lang_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    await safe_reply(update.message, t(uid, "lang_choose"), reply_markup=lang_buttons(uid=uid))


async def setlang_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    lang = query.data.split("_")[1]
    if lang not in ("vi", "en"): return
    uid = query.from_user.id
    set_user_lang(uid, lang)
    prods = list_products(limit=5, offset=0)
    if not prods:
        await safe_edit(query, t(uid, "shop_empty")); return
    header = ui_emoji_html("shop")
    title = t(uid, "shop_title")
    title_html = f"{header} <b>{title}</b>" if header else f"<b>{title}</b>"
    await safe_edit(query, f"{title_html}\n\n{t(uid, 'shop_prompt')}",
                    reply_markup=product_buttons(prods, page=0, uid=uid))


async def list_products_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    data = query.data
    page = 0
    if data.startswith("page_"):
        try: page = max(0, int(data.split("_")[1]))
        except ValueError: page = 0
    prods = list_products(limit=5, offset=page*5)
    if not prods:
        await safe_edit(query, t(uid, "no_more"), reply_markup=None); return
    await safe_edit(query, f"<b>{t(uid, 'list_title', page=page+1)}</b>",
                    reply_markup=product_buttons(prods, page, uid=uid))


async def show_product_detail(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    try: pid = int(query.data.split("_")[1])
    except (ValueError, IndexError): return
    p = get_product(pid)
    if not p:
        await safe_edit(query, t(uid, "not_found")); return
    name_html = product_name_html(p["name"], p.get("emoji_id"))
    desc_html = html.escape(p.get("description") or t(uid, "detail_no_desc"))
    text = (
        f"<b>{t(uid, 'detail_title')} #{p['id']}</b>\n\n"
        f"<b>{t(uid, 'detail_name')}:</b> {name_html}\n"
        f"<b>{t(uid, 'detail_price')}:</b> {p['price']:,} VND\n"
        f"<b>{t(uid, 'detail_stock')}:</b> {p['stock']}\n"
        f"<b>{t(uid, 'detail_sold')}:</b> {p['sold']}\n\n"
        f"<b>{t(uid, 'detail_desc')}:</b>\n{desc_html}"
    )
    await safe_edit(query, text, reply_markup=detail_buttons(pid, uid=uid))


async def buy_product(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    try: pid = int(query.data.split("_")[1])
    except (ValueError, IndexError):
        await query.answer(t(uid, "invalid_data"), show_alert=True); return
    p = get_product(pid)
    if not p or p["stock"] <= 0:
        await query.answer(t(uid, "out_of_stock"), show_alert=True); return

    order_code = int(f"{int(datetime.now().timestamp())}{pid:03d}{uid % 1000:03d}")
    create_order(order_code, uid, pid, 1, p["price"])
    context.bot_data[f"order_{order_code}"] = {"product_id": pid, "user_id": uid}

    name_html = product_name_html(p["name"], p.get("emoji_id"))
    order_icon = ui_emoji_html("order")
    prod_icon = ui_emoji_html("product")
    money_icon = ui_emoji_html("money")
    text = (
        f"{order_icon} <b>{t(uid, 'order_title')} #{order_code}</b>\n\n"
        f"{prod_icon} <b>{t(uid, 'order_product')}:</b> {name_html}\n"
        f"{money_icon} <b>{t(uid, 'order_amount')}:</b> {p['price']:,} VND\n\n"
        f"{t(uid, 'payment_method_title')}"
    )
    await safe_reply(query.message, text, reply_markup=payment_buttons(order_code, uid=uid))


async def pay_payos_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    try: oc = int(query.data.split("_")[2])
    except (ValueError, IndexError): return
    order = get_order(oc)
    if not order:
        await safe_edit(query, t(uid, "order_not_found"), reply_markup=None); return
    p = get_product(order["product_id"])
    desc = f"DH{oc}"
    url, err = create_payment_link(order_code=oc, amount=order["amount"],
                                    description=desc, buyer_name=query.from_user.full_name)
    if not url:
        await safe_edit(query, f"Lỗi PayOS: {html.escape(str(err))}", reply_markup=None); return
    name_html = product_name_html(p["name"], p.get("emoji_id")) if p else str(order["product_id"])
    text = (
        f"{ui_emoji_html('order')} <b>{t(uid, 'order_title')} #{oc}</b>\n\n"
        f"{ui_emoji_html('product')} <b>{t(uid, 'order_product')}:</b> {name_html}\n"
        f"{ui_emoji_html('money')} <b>{t(uid, 'order_amount')}:</b> {order['amount']:,} VND\n"
        f"{ui_emoji_html('order_code')} <b>{t(uid, 'order_content')}:</b> <code>DH{oc}</code>\n\n"
        f'{ui_emoji_html("pay_payos")} <a href="{url}">{t(uid, "order_pay")}</a>\n\n'
        f"{t(uid, 'order_hint')}"
    )
    await safe_edit(query, text, reply_markup=order_buttons(oc, uid=uid), disable_web_page_preview=True)


async def pay_binance_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    try: oc = int(query.data.split("_")[2])
    except (ValueError, IndexError): return
    order = get_order(oc)
    if not order:
        await safe_edit(query, t(uid, "order_not_found"), reply_markup=None); return
    addr = get_binance_address()
    if not addr:
        await safe_edit(query, t(uid, "binance_not_set"), reply_markup=None); return
    net = get_binance_network(); rate = get_usdt_rate()
    usdt = round(order["amount"] / rate, 2)
    text = (
        f"{ui_emoji_html('binance')} <b>{t(uid, 'binance_title')}</b>\n"
        f"{ui_emoji_html('order')} #{oc}\n\n"
        f"{ui_emoji_html('money')} <b>{t(uid, 'binance_amount')}:</b> <code>{usdt} USDT</code>\n"
        f"<b>{t(uid, 'binance_address')}:</b>\n<code>{html.escape(addr)}</code>\n"
        f"<b>{t(uid, 'binance_network')}:</b> <b>{html.escape(net)}</b>\n"
        f"{ui_emoji_html('order_code')} <b>{t(uid, 'binance_memo')}:</b> <code>DH{oc}</code>\n\n"
        f"{t(uid, 'binance_note')}"
    )
    kb = InlineKeyboardMarkup([
        [button(t(uid, "binance_sent"), callback_data=f"binance_sent_{oc}", ui_key="check")],
        [button(t(uid, "btn_back"), callback_data=f"backpay_{oc}", ui_key="back")],
        [button(t(uid, "btn_cancel"), callback_data=f"cancel_{oc}", ui_key="cancel")],
    ])
    await safe_edit(query, text, reply_markup=kb, disable_web_page_preview=True)


async def binance_sent_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    try: oc = int(query.data.split("_")[2])
    except (ValueError, IndexError): return
    await safe_edit(query, t(uid, "binance_waiting"), reply_markup=None)
    order = get_order(oc)
    if order:
        p = get_product(order["product_id"])
        usdt = round(order["amount"] / get_usdt_rate(), 2)
        admin_text = (
            f"<b>Yêu cầu xác nhận Binance</b>\n"
            f"Order: <code>{oc}</code>\nUser: <code>{uid}</code>\n"
            f"SP: {html.escape(p['name']) if p else '?'}\n"
            f"Amount: {order['amount']:,} VND ≈ {usdt} USDT\n\n"
            f"<code>/confirm {oc}</code>"
        )
        for aid in Config.ADMIN_IDS:
            try: await safe_send(context.bot, aid, admin_text)
            except Exception as e: logger.error(f"notify admin {aid}: {e}")


async def back_pay_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    try: oc = int(query.data.split("_")[1])
    except (ValueError, IndexError): return
    order = get_order(oc)
    if not order:
        await safe_edit(query, t(uid, "order_not_found"), reply_markup=None); return
    p = get_product(order["product_id"])
    name_html = product_name_html(p["name"], p.get("emoji_id")) if p else "?"
    text = (
        f"{ui_emoji_html('order')} <b>{t(uid, 'order_title')} #{oc}</b>\n\n"
        f"{ui_emoji_html('product')} <b>{t(uid, 'order_product')}:</b> {name_html}\n"
        f"{ui_emoji_html('money')} <b>{t(uid, 'order_amount')}:</b> {order['amount']:,} VND\n\n"
        f"{t(uid, 'payment_method_title')}"
    )
    await safe_edit(query, text, reply_markup=payment_buttons(oc, uid=uid))


async def check_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    try: oc = int(query.data.split("_")[1])
    except (ValueError, IndexError):
        await safe_edit(query, t(uid, "invalid_data")); return
    order = get_order(oc)
    if not order:
        await safe_edit(query, t(uid, "order_not_found"), reply_markup=None); return
    if order["status"] == "paid":
        await safe_edit(query,
            f"{t(uid, 'order_paid')}\n\n<b>{t(uid, 'account_info')}:</b>\n"
            f"{format_key_display(order['key_assigned'] or '', get_user_lang(uid))}"); return
    if order["status"] == "cancelled":
        await safe_edit(query, t(uid, "order_cancelled"), reply_markup=None); return

    data = get_payment_status(oc)
    paid = bool(data and data.get("code") == "00" and data.get("data", {}).get("status") == "PAID")
    if paid:
        key = get_available_key(order["product_id"])
        if key:
            update_order_status(oc, "paid", key)
            await safe_edit(query,
                f"{t(uid, 'order_success')}\n\n<b>{t(uid, 'account_info')}:</b>\n"
                f"{format_key_display(key, get_user_lang(uid))}")
        else:
            await safe_edit(query, t(uid, "order_no_key"), reply_markup=None)
    else:
        await safe_edit(query, f"#{oc} {t(uid, 'order_pending')}",
                        reply_markup=order_buttons(oc, uid=uid))


async def cancel_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    try: oc = int(query.data.split("_")[1])
    except (ValueError, IndexError): return
    order = get_order(oc)
    if not order or order["status"] != "pending":
        await safe_edit(query, t(uid, "order_cannot_cancel")); return
    update_order_status(oc, "cancelled")
    await safe_edit(query, f"{t(uid, 'order_cancelled_ok')} #{oc}.")


async def my_orders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    orders = get_pending_orders_by_user(uid)
    if not orders:
        await safe_edit(query, t(uid, "pending_empty")); return
    oi = ui_emoji_html("order")
    text = f"{oi} <b>{t(uid, 'pending_title')}:</b>\n\n"
    for o in orders[:10]:
        p = get_product(o["product_id"])
        name = product_name_html(p["name"], p.get("emoji_id")) if p else "?"
        text += f"{oi} #{o['id']} - {name} - {o['amount']:,} VND\n"
    await safe_edit(query, text)


# ============================================================
# ADMIN - PRODUCTS
# ============================================================
async def admin_add_product(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in Config.ADMIN_IDS:
        await safe_reply(update.message, "⛔"); return
    try:
        clean, emoji_id, emoji_char = extract_custom_emoji(update.message)
        parts = clean.split(maxsplit=4)
        if len(parts) < 4:
            await safe_reply(update.message,
                "<b>Cú pháp:</b>\n"
                "<code>/add &lt;tên&gt; &lt;giá&gt; &lt;số_lượng&gt; [keys]</code>\n"
                "<code>/add &lt;tên&gt;|&lt;mô tả&gt; &lt;giá&gt; &lt;số_lượng&gt; [keys]</code>"); return
        np = parts[1]
        if "|" in np:
            name, description = np.split("|", 1); name, description = name.strip(), description.strip()
        else: name, description = np.strip(), ""
        price_str, stock_str = parts[2], parts[3]
        keys_str = parts[4] if len(parts) >= 5 else "-"
        try:
            price = int(price_str.replace(".", "").replace(",", "").strip())
            if price <= 0: raise ValueError()
        except ValueError:
            await safe_reply(update.message, f"Giá lỗi: <code>{html.escape(price_str)}</code>"); return
        keys_str = keys_str.strip(); is_num = False
        try: int(keys_str); is_num = True
        except ValueError: pass
        keys = [] if (keys_str in ("-", "") or is_num) else [k.strip() for k in keys_str.split(",") if k.strip()]
        if keys: stock = len(keys)
        else:
            try:
                stock = int(stock_str.strip())
                if stock < 0: raise ValueError()
            except ValueError:
                await safe_reply(update.message, "SL lỗi."); return
            stock = 0
        emoji_note = ""
        if emoji_id:
            ok = await validate_custom_emoji(context.bot, update.effective_user.id, emoji_id)
            if not ok:
                emoji_note = "\n⚠️ Emoji bỏ qua (bot không sở hữu, cần mua username Fragment)"
                emoji_id = None
        pid = add_product(name, description, price, stock, keys, emoji_id=emoji_id)
        preview = f"\n\n<b>Key mẫu:</b>\n{format_key_display(keys[0])}" if keys else ""
        await safe_reply(update.message,
            f"Đã thêm SP <code>{pid}</code>\nTên: {html.escape(name)}\n"
            f"Giá: {price:,} VND\nSL: {stock}\nKeys: {len(keys)}{emoji_note}{preview}")
    except Exception as e:
        logger.error(f"add: {e}", exc_info=True)
        await safe_reply(update.message, f"Lỗi: {html.escape(str(e))}")


async def admin_import_products(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in Config.ADMIN_IDS: return
    doc = update.message.document
    if not doc or not (doc.file_name or "").lower().endswith(".txt"): return
    try:
        tg_file = await doc.get_file()
        raw = await tg_file.download_as_bytearray()
        try: content = raw.decode("utf-8")
        except UnicodeDecodeError: content = raw.decode("utf-8-sig", errors="ignore")
    except Exception as e:
        await safe_reply(update.message, f"Lỗi đọc: {html.escape(str(e))}"); return
    ok, fail = [], []
    for ln, line in enumerate(content.splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"): continue
        try:
            parts = [p.strip() for p in line.split("|")]
            if len(parts) < 3: fail.append((ln, "cần ≥ 3 phần")); continue
            if len(parts) == 3:
                name, description, price_str, keys_str = parts[0], "", parts[1], parts[2]
            else:
                name, description, price_str = parts[0], parts[1], parts[2]
                keys_str = parts[3] if len(parts) >= 4 else ""
            try:
                price = int(price_str.replace(".", "").replace(",", "").strip())
                if price <= 0: raise ValueError()
            except ValueError:
                fail.append((ln, f"giá lỗi: {price_str}")); continue
            keys = [k.strip() for k in keys_str.split(",") if k.strip()] if keys_str and keys_str != "-" else []
            pid = add_product(name, description, price, len(keys), keys, emoji_id=None)
            ok.append((pid, name, price, len(keys)))
        except Exception as e:
            fail.append((ln, str(e)))
    rpt = f"<b>Import {html.escape(doc.file_name or '')}</b>\n✅ {len(ok)} | ❌ {len(fail)}\n\n"
    for pid, name, price, stock in ok[:20]:
        rpt += f"<code>{pid}</code> {html.escape(name)} - {price:,}đ - {stock}\n"
    if fail:
        rpt += "\n<b>Lỗi:</b>\n"
        for ln, err in fail[:10]: rpt += f"Dòng {ln}: {html.escape(err)}\n"
    await safe_reply(update.message, rpt)


async def admin_add_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in Config.ADMIN_IDS: return
    try:
        parts = update.message.text.split(maxsplit=2)
        if len(parts) < 3:
            await safe_reply(update.message, "Cú pháp: <code>/addkey &lt;id&gt; &lt;k1,k2,...&gt;</code>"); return
        pid = int(parts[1])
        new_keys = [k.strip() for k in parts[2].split(",") if k.strip()]
        if not new_keys:
            await safe_reply(update.message, "Cần ≥ 1 key."); return
        p = get_product(pid)
        if not p:
            await safe_reply(update.message, f"Không tìm thấy SP <code>{pid}</code>."); return
        get_db().products.update_one({"id": pid},
            {"$push": {"keys": {"$each": new_keys}}, "$inc": {"stock": len(new_keys)}})
        await safe_reply(update.message, f"Đã thêm {len(new_keys)} key. Tồn mới: {p['stock'] + len(new_keys)}")
    except Exception as e:
        await safe_reply(update.message, f"Lỗi: {html.escape(str(e))}")


async def admin_set_product_emoji(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in Config.ADMIN_IDS: return
    try:
        clean, emoji_id, _ = extract_custom_emoji(update.message)
        parts = clean.split()
        if len(parts) < 2:
            await safe_reply(update.message, "Cú pháp: <code>/setemoji &lt;id&gt; [emoji]</code>"); return
        pid = int(parts[1])
        if not emoji_id:
            await safe_reply(update.message, "Không tìm thấy custom emoji."); return
        p = get_product(pid)
        if not p:
            await safe_reply(update.message, f"Không tìm thấy SP <code>{pid}</code>."); return
        ok = await validate_custom_emoji(context.bot, update.effective_user.id, emoji_id)
        if not ok:
            await safe_reply(update.message, "⚠️ Bot không có quyền dùng emoji này."); return
        get_db().products.update_one({"id": pid}, {"$set": {"emoji_id": emoji_id}})
        await safe_reply(update.message, f"Đã đặt emoji cho SP <code>{pid}</code>.")
    except Exception as e:
        await safe_reply(update.message, f"Lỗi: {html.escape(str(e))}")


async def admin_setdesc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in Config.ADMIN_IDS: return
    try:
        parts = update.message.text.split(maxsplit=2)
        if len(parts) < 3:
            await safe_reply(update.message, "Cú pháp: <code>/setdesc &lt;id&gt; &lt;mô tả&gt;</code>"); return
        pid = int(parts[1]); desc = parts[2].strip()
        p = get_product(pid)
        if not p:
            await safe_reply(update.message, f"Không tìm thấy SP <code>{pid}</code>."); return
        get_db().products.update_one({"id": pid}, {"$set": {"description": desc}})
        await safe_reply(update.message, f"Đã đổi mô tả SP <code>{pid}</code>.")
    except Exception as e:
        await safe_reply(update.message, f"Lỗi: {html.escape(str(e))}")


async def admin_detail(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in Config.ADMIN_IDS: return
    try:
        parts = update.message.text.split()
        if len(parts) < 2:
            await safe_reply(update.message, "Cú pháp: <code>/detail &lt;id&gt;</code>"); return
        pid = int(parts[1])
        p = get_product(pid)
        if not p:
            await safe_reply(update.message, f"Không tìm thấy SP <code>{pid}</code>."); return
        name_html = product_name_html(p["name"], p.get("emoji_id"))
        desc = html.escape(p.get("description") or "(không có mô tả)")
        keys = json.loads(p["keys"] or "[]")
        text = (f"<b>SP #{p['id']}</b>\nTên: {name_html}\nMô tả: {desc}\n"
                f"Giá: {p['price']:,} VND\nKho: {p['stock']}\nBán: {p['sold']}\n"
                f"Emoji ID: <code>{p.get('emoji_id') or 'chưa đặt'}</code>\n\n"
                f"<b>Keys ({len(keys)}):</b>\n")
        for i, k in enumerate(keys[:10], 1):
            text += f"  {i}. {format_key_display(k)}\n"
        if len(keys) > 10: text += f"  <i>... và {len(keys) - 10} key khác</i>\n"
        await safe_reply(update.message, text)
    except Exception as e:
        await safe_reply(update.message, f"Lỗi: {html.escape(str(e))}")


async def admin_list_products(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in Config.ADMIN_IDS: return
    total = count_all_products()
    if total == 0:
        await safe_reply(update.message, "Chưa có SP."); return
    products = list_all_products(limit=20, offset=0)
    text = f"<b>SP ({len(products)}/{total}):</b>\n\n"
    for p in products:
        text += f"<code>{p['id']}</code> {html.escape(p['name'])[:30]} - {p['price']:,}đ - kho:{p['stock']} - bán:{p['sold']}\n"
    if total > 20:
        text += f"\n<i>... và {total - 20} SP khác.</i> <code>/list2</code>"
    text += "\n\n<code>/detail &lt;id&gt;</code> - <code>/del &lt;id&gt;</code>"
    await safe_reply(update.message, text)


async def admin_list2(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in Config.ADMIN_IDS: return
    total = count_all_products()
    products = list_all_products(limit=20, offset=20)
    if not products:
        await safe_reply(update.message, "Hết."); return
    text = f"<b>Trang 2/{(total - 1) // 20 + 1}:</b>\n\n"
    for p in products:
        text += f"<code>{p['id']}</code> {html.escape(p['name'])[:30]} - {p['price']:,}đ\n"
    await safe_reply(update.message, text)


async def admin_delete_product(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in Config.ADMIN_IDS: return
    try:
        parts = update.message.text.split()
        if len(parts) < 2:
            await safe_reply(update.message, "Cú pháp: <code>/del &lt;id&gt;</code>"); return
        pid = int(parts[1])
        p = get_product(pid)
        if not p:
            await safe_reply(update.message, f"Không tìm thấy SP <code>{pid}</code>."); return
        if delete_product(pid):
            await safe_reply(update.message, f"Đã xóa SP <code>{pid}</code> - {html.escape(p['name'])}")
    except Exception as e:
        await safe_reply(update.message, f"Lỗi: {html.escape(str(e))}")


async def admin_delete_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in Config.ADMIN_IDS: return
    parts = update.message.text.split()
    if len(parts) < 2 or parts[1].lower() != "confirm":
        await safe_reply(update.message, "Xóa TẤT CẢ. Xác nhận: <code>/delall confirm</code>"); return
    c = delete_all_products()
    await safe_reply(update.message, f"Đã xóa {c} SP.")


async def admin_confirm_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in Config.ADMIN_IDS: return
    try:
        parts = update.message.text.split()
        if len(parts) < 2:
            await safe_reply(update.message, "Cú pháp: <code>/confirm &lt;oc&gt;</code>"); return
        oc = int(parts[1])
        order = get_order(oc)
        if not order:
            await safe_reply(update.message, "Không tìm thấy đơn."); return
        if order["status"] != "pending":
            await safe_reply(update.message, f"Trạng thái <b>{order['status']}</b>."); return
        key = get_available_key(order["product_id"])
        if not key:
            await safe_reply(update.message, "Hết key."); return
        update_order_status(oc, "paid", key)
        await safe_reply(update.message, f"Đã xác nhận đơn <code>{oc}</code>.")
        lang = get_user_lang(order["user_id"])
        try:
            await safe_send(context.bot, order["user_id"],
                f"<b>{t(lang, 'order_success')}</b>\n\n"
                f"<b>{t(lang, 'account_info')}:</b>\n{format_key_display(key, lang)}")
        except Exception as e:
            logger.error(f"notify: {e}")
    except Exception as e:
        await safe_reply(update.message, f"Lỗi: {html.escape(str(e))}")


async def admin_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in Config.ADMIN_IDS: return
    parts = update.message.text.split(maxsplit=1)
    if len(parts) < 2:
        await safe_reply(update.message, "Cú pháp: <code>/broadcast &lt;msg&gt;</code>"); return
    msg = parts[1]
    users = get_all_user_ids(); sent, fail = 0, 0
    for uid in users:
        try:
            await safe_send(context.bot, uid, msg, disable_web_page_preview=True)
            sent += 1; await asyncio.sleep(0.05)
        except Exception as e:
            fail += 1; logger.warning(f"bc {uid}: {e}")
    await safe_reply(update.message, f"Broadcast. ✅ {sent} | ❌ {fail}")


# ============================================================
# ADMIN - UI EMOJI
# ============================================================
async def admin_setui(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in Config.ADMIN_IDS: return
    try:
        clean, emoji_id, _ = extract_custom_emoji(update.message)
        parts = clean.split()
        if len(parts) < 2:
            txt = "<b>Cú pháp:</b> <code>/setui &lt;key&gt; [emoji]</code>\n\n<b>UI keys:</b>\n"
            txt += "\n".join(f"• <code>{k}</code> - {v}" for k, v in UI_KEYS.items())
            await safe_reply(update.message, txt); return
        key = parts[1].lower()
        if key not in UI_KEYS:
            await safe_reply(update.message, f"Key lỗi: <code>{html.escape(key)}</code>"); return
        if not emoji_id:
            await safe_reply(update.message, "Không tìm thấy emoji."); return
        ok = await validate_custom_emoji(context.bot, update.effective_user.id, emoji_id)
        if not ok:
            await safe_reply(update.message,
                "⚠️ Bot không sở hữu emoji này (cần Fragment).\n"
                f"Ép lưu: <code>/setui_force {key} [emoji]</code>"); return
        set_setting(f"ui_{key}", emoji_id)
        await safe_reply(update.message, f"✅ Đã đặt emoji <code>{key}</code>.")
    except Exception as e:
        await safe_reply(update.message, f"Lỗi: {html.escape(str(e))}")


async def admin_setui_force(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in Config.ADMIN_IDS: return
    try:
        clean, emoji_id, _ = extract_custom_emoji(update.message)
        parts = clean.split()
        if len(parts) < 2 or not emoji_id:
            await safe_reply(update.message, "Cú pháp: <code>/setui_force &lt;key&gt; [emoji]</code>"); return
        key = parts[1].lower()
        if key not in UI_KEYS:
            await safe_reply(update.message, "Key lỗi."); return
        set_setting(f"ui_{key}", emoji_id)
        await safe_reply(update.message, f"⚠️ Ép lưu emoji <code>{key}</code>.")
    except Exception as e:
        await safe_reply(update.message, f"Lỗi: {html.escape(str(e))}")


async def admin_viewui(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in Config.ADMIN_IDS: return
    st = {s["key"]: s["emoji_id"] for s in get_all_settings()}
    text = "<b>UI Emoji:</b>\n\n"
    for k, desc in UI_KEYS.items():
        eid = st.get(f"ui_{k}")
        text += f"• <code>{k}</code> - {desc} - " + (f"<code>{eid}</code>\n" if eid else "<i>chưa</i>\n")
    await safe_reply(update.message, text)


async def admin_delui(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in Config.ADMIN_IDS: return
    parts = update.message.text.split()
    if len(parts) < 2:
        await safe_reply(update.message, "Cú pháp: <code>/delui &lt;key&gt;</code>"); return
    key = parts[1].lower()
    if key not in UI_KEYS:
        await safe_reply(update.message, "Key lỗi."); return
    delete_setting(f"ui_{key}")
    await safe_reply(update.message, f"Đã xóa emoji <code>{key}</code>.")


# ============================================================
# ADMIN - TEXTS (EMOJI SUPPORT)
# ============================================================
TEXT_KEYS_INFO = {
    "shop_empty": "Shop trống", "shop_title": "Tiêu đề shop",
    "shop_prompt": "Chọn sản phẩm...", "pending_empty": "Đơn chờ trống",
    "pending_title": "Tiêu đề đơn chờ", "order_hint": "Hướng dẫn TT",
    "order_success": "TT thành công", "order_paid": "Đơn đã TT",
    "binance_note": "Lưu ý Binance", "binance_title": "Tiêu đề Binance",
    "lang_required": "Yêu cầu lang", "lang_choose": "Chọn lang",
    "lang_changed": "Đã đổi lang", "payment_method_title": "Chọn PTTT",
    "account_info": "Thông tin TK",
}


async def admin_settext(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /settext <vi|en> <key> [emoji] <value>
    - Có emoji → validate, nếu bot không sở hữu → vẫn lưu + cảnh báo
    - emoji_char giữ nguyên làm fallback (hiển thị đúng emoji user muốn)
    """
    if update.effective_user.id not in Config.ADMIN_IDS: return
    try:
        clean, emoji_id, emoji_char = extract_custom_emoji(update.message)
        parts = clean.split(maxsplit=3)
        if len(parts) < 4:
            txt = ("Cú pháp: <code>/settext &lt;vi|en&gt; &lt;key&gt; [emoji] &lt;value&gt;</code>\n\n"
                   "<b>Ví dụ:</b>\n"
                   "<code>/settext vi shop_title 🏪 Cửa hàng QC VN</code>\n"
                   "<code>/settext vi shop_empty Không có SP</code>\n\n"
                   "<b>Key:</b>\n" + "\n".join(f"• <code>{k}</code> - {v}" for k, v in TEXT_KEYS_INFO.items()))
            await safe_reply(update.message, txt); return
        lang, key, value = parts[1].lower(), parts[2].lower(), parts[3]
        if lang not in ("vi", "en"):
            await safe_reply(update.message, "Lang phải vi/en."); return
        if key not in TEXT_KEYS_INFO:
            await safe_reply(update.message, f"Key không hợp lệ: <code>{html.escape(key)}</code>"); return

        note = ""
        if emoji_id:
            ok = await validate_custom_emoji(context.bot, update.effective_user.id, emoji_id)
            if not ok:
                note = ("\n\n⚠️ <b>Bot không sở hữu emoji này.</b>\n"
                        "Khi hiển thị, emoji sẽ ở dạng tĩnh (không animation).\n"
                        "Muốn animation → mua username Fragment cho bot.")
            set_text(f"{lang}_{key}", value, emoji_id=emoji_id, emoji_char=emoji_char)
        else:
            set_text(f"{lang}_{key}", value, keep_emoji=True)

        # Preview từ raw
        raw = get_text_raw(f"{lang}_{key}")
        preview_eid = raw.get("emoji_id") if raw else None
        preview_ec = raw.get("emoji_char") if raw else None
        preview = ""
        if preview_eid:
            preview = f'<tg-emoji emoji-id="{preview_eid}">{preview_ec or "🎁"}</tg-emoji> '
        elif preview_ec:
            preview = f'{preview_ec} '

        await safe_reply(update.message,
            f"✅ Đã đổi <code>{lang}_{key}</code>:\n\n{preview}{html.escape(value)}{note}")
    except Exception as e:
        logger.error(f"settext: {e}", exc_info=True)
        await safe_reply(update.message, f"Lỗi: {html.escape(str(e))}")


async def admin_deltext_emoji(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in Config.ADMIN_IDS: return
    parts = update.message.text.split()
    if len(parts) < 3:
        await safe_reply(update.message, "Cú pháp: <code>/deltextemoji &lt;vi|en&gt; &lt;key&gt;</code>"); return
    lang, key = parts[1].lower(), parts[2].lower()
    raw = get_text_raw(f"{lang}_{key}")
    if raw is None:
        await safe_reply(update.message, "Text chưa set."); return
    set_text(f"{lang}_{key}", raw.get("value") or "", keep_emoji=False)
    await safe_reply(update.message, f"Đã xóa emoji của <code>{lang}_{key}</code>.")


async def admin_viewtext(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in Config.ADMIN_IDS: return
    ov = get_all_texts()
    text = "<b>Texts override:</b>\n\n"
    if not ov:
        text += "<i>Chưa có override nào.</i>\n\n"
    else:
        for it in ov:
            k = it["key"]; v = it.get("value") or ""
            eid = it.get("emoji_id"); ec = it.get("emoji_char") or "🎁"
            ep = f'<tg-emoji emoji-id="{eid}">{ec}</tg-emoji> ' if eid else (f'{ec} ' if it.get("emoji_char") else "")
            text += f"• <code>{k}</code>\n  {ep}<i>{html.escape(v[:80])}</i>\n"
    text += "\n<b>Key:</b>\n" + "\n".join(f"• <code>{k}</code> - {d}" for k, d in TEXT_KEYS_INFO.items())
    text += "\n\n<b>Cú pháp:</b>\n"
    text += "<code>/settext vi shop_title [emoji] Value</code>\n"
    text += "<code>/deltextemoji vi shop_title</code>\n"
    text += "<code>/deltext vi shop_title</code>"
    await safe_reply(update.message, text)


async def admin_deltext(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in Config.ADMIN_IDS: return
    parts = update.message.text.split()
    if len(parts) < 3:
        await safe_reply(update.message, "Cú pháp: <code>/deltext &lt;lang&gt; &lt;key&gt;</code>"); return
    lang, key = parts[1].lower(), parts[2].lower()
    delete_text(f"{lang}_{key}")
    await safe_reply(update.message, f"Đã xóa override <code>{lang}_{key}</code>.")


# ============================================================
# ADMIN - BINANCE
# ============================================================
async def admin_setbinance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in Config.ADMIN_IDS: return
    parts = update.message.text.split()
    if len(parts) < 2:
        await safe_reply(update.message,
            "Cú pháp: <code>/setbinance &lt;address&gt; [TRC20|BEP20|ERC20|POLYGON]</code>"); return
    addr = parts[1]; net = parts[2].upper() if len(parts) >= 3 else "TRC20"
    if net not in ("TRC20", "BEP20", "ERC20", "POLYGON"):
        await safe_reply(update.message, "Network không hợp lệ."); return
    set_binance_address(addr); set_binance_network(net)
    await safe_reply(update.message, f"✅ Ví: <code>{html.escape(addr)}</code>\nNetwork: <b>{net}</b>")


async def admin_setrate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in Config.ADMIN_IDS: return
    parts = update.message.text.split()
    if len(parts) < 2:
        await safe_reply(update.message, f"Rate hiện tại: <code>{get_usdt_rate():,}</code>. Đặt: <code>/setrate 25000</code>"); return
    try:
        r = int(parts[1].replace(".", "").replace(",", ""))
        if r <= 0: raise ValueError()
        set_usdt_rate(r)
        await safe_reply(update.message, f"✅ Rate: <code>{r:,}</code> VND/USDT")
    except ValueError:
        await safe_reply(update.message, "Rate lỗi.")


async def admin_viewbinance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in Config.ADMIN_IDS: return
    a = get_binance_address(); n = get_binance_network(); r = get_usdt_rate()
    await safe_reply(update.message,
        f"<b>Binance</b>\nAddress: <code>{html.escape(a) if a else '(chưa)'}</code>\n"
        f"Network: <b>{n}</b>\nRate: <code>{r:,}</code> VND/USDT")


async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in Config.ADMIN_IDS: return
    await safe_reply(update.message,
        f"<b>Stats</b>\nUsers: <code>{count_users()}</code>\n"
        f"SP: <code>{count_all_products()}</code>\nCòn: <code>{count_products()}</code>\n"
        f"Binance: <code>{html.escape(get_binance_address()) or 'chưa'}</code>\n"
        f"Network: <b>{get_binance_network()}</b>\nRate: <code>{get_usdt_rate():,}</code>")


async def admin_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in Config.ADMIN_IDS: return
    txt = (
        "<b>Admin commands:</b>\n\n"
        "<b>Sản phẩm:</b>\n"
        "<code>/add Tên Giá SL [Keys]</code>\n"
        "<code>/add Tên|Mô_tả Giá SL [Keys]</code>\n"
        "File .txt để import\n<code>/list</code> / <code>/list2</code>\n"
        "<code>/detail &lt;id&gt;</code> / <code>/del &lt;id&gt;</code>\n"
        "<code>/delall confirm</code>\n\n"
        "<b>Keys:</b>\n<code>/addkey &lt;id&gt; K1,K2</code>\n"
        "<code>/setdesc &lt;id&gt; Mô tả</code>\n<code>/setemoji &lt;id&gt; [emoji]</code>\n\n"
        "<b>Binance:</b>\n<code>/setbinance &lt;addr&gt; [net]</code>\n"
        "<code>/setrate &lt;vnd&gt;</code>\n<code>/viewbinance</code> / <code>/confirm &lt;oc&gt;</code>\n\n"
        "<b>UI Emoji:</b>\n<code>/setui &lt;key&gt; [emoji]</code>\n"
        "<code>/setui_force</code> / <code>/viewui</code> / <code>/delui</code>\n\n"
        "<b>Texts (có emoji):</b>\n"
        "<code>/settext &lt;vi|en&gt; &lt;key&gt; [emoji] &lt;value&gt;</code>\n"
        "<code>/viewtext</code>\n<code>/deltextemoji &lt;lang&gt; &lt;key&gt;</code>\n"
        "<code>/deltext &lt;lang&gt; &lt;key&gt;</code>\n\n"
        "<b>Khác:</b>\n<code>/broadcast &lt;msg&gt;</code>\n<code>/stats</code>"
    )
    await safe_reply(update.message, txt)


# ============================================================
# HTTP
# ============================================================
async def root_handler(request: Request):
    if request.method == "HEAD": return Response(status=200, content_type="text/plain")
    return Response(text="Bot is running", status=200)

async def health_check(request: Request):
    if request.method == "HEAD": return Response(status=200, content_type="text/plain")
    return Response(text="OK", status=200)

async def telegram_webhook(request: Request):
    if request.method == "HEAD": return Response(status=200, content_type="text/plain")
    if request.method == "GET": return Response(text="Telegram webhook OK", status=200)
    try:
        raw = await request.read()
        if not raw: return Response(status=400, text="Empty")
        data = json.loads(raw.decode("utf-8"))
        update = Update.de_json(data, request.app["bot_app"].bot)
        await request.app["bot_app"].process_update(update)
        return Response(text="OK", status=200)
    except Exception as e:
        logger.error(f"tg wh: {e}", exc_info=True)
        return Response(status=500, text="Error")

async def payos_webhook(request: Request):
    if request.method == "HEAD": return Response(status=200, content_type="text/plain")
    if request.method == "GET": return Response(text="PayOS webhook OK", status=200)
    try:
        raw = await request.read()
        if not raw: return Response(status=200, text="OK")
        try: body = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError: return Response(status=200, text="OK")
        data = body.get("data", {})
        oc = data.get("orderCode"); pc = data.get("code"); desc = data.get("description", "")
        if oc == 123 or desc == "VQRIO123": return Response(text="OK", status=200)
        if not verify_payment_webhook(body, request.headers.get("x-payos-signature", "")):
            return Response(status=200, text="OK")
        if pc == "00" and oc:
            order = get_order(oc)
            if order and order["status"] == "pending":
                key = get_available_key(order["product_id"])
                if key:
                    update_order_status(oc, "paid", key)
                    app = request.app["bot_app"]
                    lang = get_user_lang(order["user_id"])
                    try:
                        await safe_send(app.bot, order["user_id"],
                            f"<b>{t(lang, 'order_success')}</b>\n\n"
                            f"<b>{t(lang, 'account_info')}:</b>\n"
                            f"{format_key_display(key, lang)}")
                    except Exception as e:
                        logger.error(f"notify: {e}")
        return Response(text="OK", status=200)
    except Exception as e:
        logger.error(f"payos wh: {e}", exc_info=True)
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

    # UI Emoji
    app.add_handler(CommandHandler("setui", admin_setui))
    app.add_handler(CommandHandler("setui_force", admin_setui_force))
    app.add_handler(CommandHandler("viewui", admin_viewui))
    app.add_handler(CommandHandler("delui", admin_delui))

    # Texts
    app.add_handler(CommandHandler("settext", admin_settext))
    app.add_handler(CommandHandler("viewtext", admin_viewtext))
    app.add_handler(CommandHandler("deltext", admin_deltext))
    app.add_handler(CommandHandler("deltextemoji", admin_deltext_emoji))

    app.add_handler(CommandHandler("help", admin_help))

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
    app.add_handler(CallbackQueryHandler(back_pay_callback, pattern=r"^backpay_"))
    app.add_handler(CallbackQueryHandler(check_order, pattern=r"^check_"))
    app.add_handler(CallbackQueryHandler(cancel_order, pattern=r"^cancel_"))
    app.add_handler(CallbackQueryHandler(my_orders, pattern=r"^my_orders$"))
    app.add_handler(CallbackQueryHandler(list_products_callback, pattern=r"^back_list$"))

    await app.initialize()
    await app.start()

    if Config.WEBHOOK_URL:
        wh_url = f"{Config.WEBHOOK_URL}/telegram"
        await app.bot.set_webhook(wh_url)
        logger.info(f"Webhook set: {wh_url}")

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
    logger.info(f"Server started port {port}")

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
        logger.info("Stopped")
