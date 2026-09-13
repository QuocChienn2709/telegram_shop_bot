# main.py
import asyncio
import html
import json
import logging
import os
import re
import time
import requests
from datetime import datetime

from aiohttp import web
from aiohttp.web import Request, Response
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, ContextTypes,
    MessageHandler, filters,
)
from telegram.constants import ParseMode

from config import Config
from database import (
    init_db, add_product, delete_product, delete_all_products,
    set_product_requires_email, decrement_stock,
    get_product, list_products, count_products,
    list_all_products, count_all_products,
    get_available_key, create_order, get_order, update_order_status,
    set_order_email, set_order_email_status, get_awaiting_email_order,
    get_pending_orders_by_user, get_recent_orders_by_user, restore_cancelled_order,
    hide_order, get_db, register_user,
    get_user_lang, set_user_lang, is_lang_set,
    get_all_user_ids, count_users,
    get_user_balance, add_balance, subtract_balance,
    create_topup_order, mark_topup_paid,
    list_users_paginated, get_user_detail,
    list_users_with_topup, count_users_with_topup,
    get_user_topup_orders, get_total_topup_amount,
    get_setting, set_setting, delete_setting, get_all_settings,
    get_text, set_text, delete_text, get_all_texts,
    get_binance_address, set_binance_address,
    get_binance_network, set_binance_network,
    get_usdt_rate, set_usdt_rate,
)
from payos_client import create_payment_link, verify_payment_webhook, get_payment_status

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)
init_db()

EMAIL_RE = re.compile(r'^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$')

# ============================================================
# BINANCE RATE
# ============================================================
_binance_rate_cache = {"rate": None, "ts": 0, "source": "manual", "error": ""}
BINANCE_CACHE_TTL = 300
_session = requests.Session()
_session.headers.update({"User-Agent": "Mozilla/5.0"})


def _fetch_binance_p2p():
    endpoints = [
        "https://p2p.binance.com/bapi/c2c/v2/friendly/c2c/adv/search",
        "https://p2p.binance.com/bapi/c2c/v2/public/c2c/adv/search",
    ]
    payload = {"asset": "USDT", "fiat": "VND", "merchantCheck": False, "page": 1,
               "payTypes": [], "publisherType": None, "rows": 5, "tradeType": "SELL"}
    headers = {
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "vi-VN,vi;q=0.9,en;q=0.8",
        "Content-Type": "application/json",
        "Origin": "https://p2p.binance.com",
        "Referer": "https://p2p.binance.com/en/trade/sell/USDT?fiat=VND",
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"),
    }
    last_err = ""
    for url in endpoints:
        try:
            r = _session.post(url, headers=headers, json=payload, timeout=8)
            if r.status_code != 200:
                last_err = f"HTTP {r.status_code}"
                continue
            data = r.json()
            adv_list = data.get("data") or []
            prices = []
            for item in adv_list:
                adv = item.get("adv", {}) if isinstance(item, dict) else {}
                p = adv.get("price")
                if p:
                    try:
                        prices.append(float(p))
                    except (TypeError, ValueError):
                        pass
            if not prices:
                continue
            if len(prices) >= 3:
                sp = sorted(prices)
                avg = sum(sp[1:-1]) / (len(sp) - 2)
            else:
                avg = sum(prices) / len(prices)
            return round(avg, 2), ""
        except Exception as e:
            last_err = str(e)
    return None, last_err


def _fetch_fallback_rate():
    try:
        r = _session.get("https://open.er-api.com/v6/latest/USD", timeout=8)
        if r.status_code != 200:
            return None, f"er-api HTTP {r.status_code}"
        data = r.json()
        vnd = data.get("rates", {}).get("VND")
        if not vnd:
            return None, "er-api khong co VND"
        return round(float(vnd) * 1.01, 2), ""
    except Exception as e:
        return None, f"er-api: {e}"


def get_binance_rate_live():
    global _binance_rate_cache
    manual = get_usdt_rate()
    if not Config.BINANCE_AUTO_RATE:
        return manual, "Manual (auto OFF)", ""
    now = time.time()
    if _binance_rate_cache["rate"] and (now - _binance_rate_cache["ts"]) < BINANCE_CACHE_TTL:
        return (_binance_rate_cache["rate"], _binance_rate_cache["source"],
                _binance_rate_cache.get("error", ""))
    live, err1 = _fetch_binance_p2p()
    if live and live > 0:
        _binance_rate_cache = {"rate": live, "ts": now, "source": "Binance P2P", "error": ""}
        return live, "Binance P2P", ""
    fb, err2 = _fetch_fallback_rate()
    if fb and fb > 0:
        _binance_rate_cache = {"rate": fb, "ts": now, "source": "er-api (fallback)", "error": err1}
        return fb, "er-api (fallback)", err1
    _binance_rate_cache = {"rate": manual, "ts": now, "source": "Manual (API loi)",
                           "error": f"{err1} | {err2}"}
    return manual, "Manual (API loi)", f"{err1} | {err2}"


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
        "btn_back_shop": "Về cửa hàng",
        "btn_pay_again": "Thanh toán", "btn_delete_order": "Xóa đơn",
        "btn_hide_order": "Ẩn đơn", "btn_recheck_order": "Kiểm tra lại",
        "btn_refresh": "Load lại", "btn_lang": "Ngôn ngữ",
        "btn_lang_short": "Đổi ngôn ngữ",
        "btn_wallet": "Ví của tôi", "btn_topup": "Nạp ví",
        "btn_pay_wallet": "Thanh toán bằng ví",
        "btn_topup_payos": "Nạp qua PayOS", "btn_topup_binance": "Nạp qua Binance",
        "refreshed": "Đã load lại",
        "not_found": "Không tìm thấy sản phẩm.",
        "out_of_stock": "Sản phẩm đã hết hàng.",
        "out_of_stock_wait": "Sản phẩm đã hết hàng. Vui lòng chờ admin thêm hàng.",
        "stock_out_tag": "[HẾT HÀNG]",
        "invalid_data": "Dữ liệu không hợp lệ.",
        "detail_title": "Chi tiết sản phẩm", "detail_name": "Tên",
        "detail_desc": "Mô tả", "detail_price": "Giá",
        "detail_stock": "Tồn kho", "detail_sold": "Đã bán",
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
        "order_cancelled_check_hint": "Nếu bạn đã chuyển khoản, vào Đơn hàng chờ để kiểm tra lại.",
        "order_recheck_paid": "Đã phát hiện thanh toán!",
        "order_recheck_notpaid": "Chưa phát hiện thanh toán cho đơn này.",
        "pending_title": "Đơn hàng chờ thanh toán",
        "pending_empty": "Bạn không có đơn hàng nào đang chờ.",
        "pending_hint": "Nhấn 'Thanh toán' để tiếp tục hoặc 'Xóa đơn' để hủy.",
        "account_info": "Thông tin tài khoản",
        "payment_method_title": "Chọn phương thức thanh toán:",
        "btn_pay_payos": "Thanh toán VND (PayOS)",
        "btn_pay_binance": "Thanh toán USDT (Binance)",
        "binance_title": "Thanh toán Binance USDT",
        "binance_amount": "Số tiền", "binance_address": "Địa chỉ ví",
        "binance_network": "Mạng", "binance_memo": "Nội dung/Memo",
        "binance_rate": "Tỷ giá",
        "binance_note": "Chuyển đúng số tiền và mạng. Ghi đúng nội dung để admin xác nhận.",
        "binance_not_set": "Admin chưa cấu hình ví Binance.",
        "binance_sent": "Tôi đã chuyển khoản",
        "binance_waiting": "Đang chờ admin xác nhận. Vui lòng đợi.",
        "admin_received_key": "Đã nhận tiền - Giao key",
        "admin_received_topup": "Đã nhận USDT - Cộng ví",
        "admin_cancel_order": "Hủy đơn này",
        "admin_binance_req": "Yêu cầu xác nhận Binance",
        "admin_binance_topup_req": "Yêu cầu xác nhận nạp ví Binance",
        "lang_changed": "Đã đổi ngôn ngữ: Tiếng Việt",
        "lang_choose": "Chọn ngôn ngữ để tiếp tục:",
        "lang_required": "Vui lòng chọn ngôn ngữ trước khi tiếp tục:",
        "btn_lang_vi": "Tiếng Việt", "btn_lang_en": "English",
        "youtube_email_ask": "Vui lòng gửi email của bạn cho bot để admin thêm vào team.\n\nVí dụ: yourname@gmail.com",
        "youtube_email_preview": "Email của bạn: <code>{email}</code>\n\nNhấn nút bên dưới để <b>xác nhận gửi email này cho admin</b>.",
        "youtube_email_btn_confirm_send": "Xác nhận gửi cho admin",
        "youtube_email_btn_cancel": "Hủy",
        "youtube_email_sent_admin": "Đã gửi email <code>{email}</code> cho admin.\n\nVui lòng chờ admin thêm bạn vào team.",
        "youtube_email_cancelled": "Đã hủy gửi email. Bạn có thể gửi lại email khác.",
        "youtube_email_pending": "Email: <code>{email}</code>\n\nĐang chờ admin thêm vào team. Vui lòng đợi.",
        "youtube_email_done": "Hoàn tất!\n\nEmail <code>{email}</code> đã được thêm vào team.\nVui lòng kiểm tra hộp thư để nhận lời mời.",
        "youtube_email_paid_msg": "Thanh toán thành công!\n\nVui lòng gửi email của bạn cho bot để admin thêm vào team.",
        "email_confirm_required": "Bạn có email chưa xác nhận. Vui lòng xác nhận gửi email trước khi dùng chức năng khác.",
        "admin_email_request_title": "Yêu cầu thêm vào team",
        "admin_email_confirm_btn": "Đã thêm vào team",
        "admin_email_confirmed": "[ĐÃ XÁC NHẬN]",
        "wallet_title": "Ví của bạn", "wallet_balance": "Số dư",
        "wallet_topup_prompt": "Nhập số tiền muốn nạp (VND). Tối thiểu 2,000.",
        "wallet_topup_invalid": "Số tiền không hợp lệ. Tối thiểu 2,000 VND.",
        "wallet_topup_created": "Đơn nạp ví <b>#{code}</b> đã tạo.\n\nSố tiền: <b>{amount:,} VND</b>",
        "wallet_topup_success": "Nạp ví thành công!\n\nSố tiền: <b>{amount:,} VND</b>\nSố dư mới: <b>{balance:,} VND</b>",
        "wallet_not_enough": "Số dư không đủ. Vui lòng nạp thêm ví.",
        "wallet_paid_success": "Đã thanh toán bằng ví!\n\nĐã trừ: <b>{amount:,} VND</b>\nSố dư còn: <b>{balance:,} VND</b>",
        "admin_users_title": "Danh sách người dùng",
        "admin_topups_title": "Danh sách users đã nạp ví",
        "admin_user_detail": "Chi tiết người dùng",
        "admin_user_topup_history": "Lịch sử nạp ví",
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
        "btn_back_shop": "Back to shop",
        "btn_pay_again": "Pay", "btn_delete_order": "Delete",
        "btn_hide_order": "Hide", "btn_recheck_order": "Recheck",
        "btn_refresh": "Refresh", "btn_lang": "Language",
        "btn_lang_short": "Change language",
        "btn_wallet": "My wallet", "btn_topup": "Top up",
        "btn_pay_wallet": "Pay with wallet",
        "btn_topup_payos": "Topup via PayOS", "btn_topup_binance": "Topup via Binance",
        "refreshed": "Refreshed",
        "not_found": "Product not found.",
        "out_of_stock": "Out of stock.",
        "out_of_stock_wait": "Out of stock. Please wait for admin to restock.",
        "stock_out_tag": "[OUT]",
        "invalid_data": "Invalid data.",
        "detail_title": "Product details", "detail_name": "Name",
        "detail_desc": "Description", "detail_price": "Price",
        "detail_stock": "Stock", "detail_sold": "Sold",
        "detail_no_desc": "(no description)",
        "order_title": "Order", "order_product": "Product",
        "order_amount": "Amount", "order_pay": "Click to pay",
        "order_content": "Payment ref",
        "order_hint": "After payment, press 'Paid? Check now' below.",
        "order_not_found": "Order not found.",
        "order_paid": "Order already paid.",
        "order_cancelled": "Order cancelled.",
        "order_pending": "not paid yet. Please pay or cancel.",
        "order_success": "Payment successful!",
        "order_no_key": "Paid but out of keys. Contact admin.",
        "order_cannot_cancel": "Cannot cancel this order.",
        "order_cancelled_ok": "Order cancelled",
        "order_cancelled_check_hint": "If you already paid, go to Pending orders to recheck.",
        "order_recheck_paid": "Payment detected!",
        "order_recheck_notpaid": "No payment detected for this order.",
        "pending_title": "Pending orders",
        "pending_empty": "You have no pending orders.",
        "pending_hint": "Tap 'Pay' to continue or 'Delete' to cancel.",
        "account_info": "Account info",
        "payment_method_title": "Choose payment method:",
        "btn_pay_payos": "Pay VND (PayOS)",
        "btn_pay_binance": "Pay USDT (Binance)",
        "binance_title": "Binance USDT Payment",
        "binance_amount": "Amount", "binance_address": "Wallet address",
        "binance_network": "Network", "binance_memo": "Memo",
        "binance_rate": "Rate",
        "binance_note": "Send exact amount on correct network with memo.",
        "binance_not_set": "Binance wallet not configured.",
        "binance_sent": "I have sent",
        "binance_waiting": "Waiting for admin confirmation. Please wait.",
        "admin_received_key": "Received - Deliver key",
        "admin_received_topup": "Received USDT - Credit wallet",
        "admin_cancel_order": "Cancel this order",
        "admin_binance_req": "Binance confirmation request",
        "admin_binance_topup_req": "Binance topup confirmation request",
        "lang_changed": "Language changed to English",
        "lang_choose": "Choose language to continue:",
        "lang_required": "Please select a language to continue:",
        "btn_lang_vi": "Tiếng Việt", "btn_lang_en": "English",
        "youtube_email_ask": "Please send your email to the bot so admin can add you to the team.\n\nExample: yourname@gmail.com",
        "youtube_email_preview": "Your email: <code>{email}</code>\n\nPress the button below to <b>confirm sending this email to admin</b>.",
        "youtube_email_btn_confirm_send": "Confirm send to admin",
        "youtube_email_btn_cancel": "Cancel",
        "youtube_email_sent_admin": "Email <code>{email}</code> has been sent to admin.\n\nPlease wait for admin to add you to the team.",
        "youtube_email_cancelled": "Email sending cancelled. You can send another email.",
        "youtube_email_pending": "Email: <code>{email}</code>\n\nWaiting for admin confirmation. Please wait.",
        "youtube_email_done": "Done!\n\nEmail <code>{email}</code> has been added to the team.\nCheck your inbox for invitation.",
        "youtube_email_paid_msg": "Payment successful!\n\nPlease send your email to the bot so admin can add you to the team.",
        "email_confirm_required": "You have an unconfirmed email. Please confirm it before using other features.",
        "admin_email_request_title": "Team request",
        "admin_email_confirm_btn": "Added to team",
        "admin_email_confirmed": "[CONFIRMED]",
        "wallet_title": "Your wallet", "wallet_balance": "Balance",
        "wallet_topup_prompt": "Enter amount to top up (VND). Minimum 2,000.",
        "wallet_topup_invalid": "Invalid amount. Minimum 2,000 VND.",
        "wallet_topup_created": "Topup order <b>#{code}</b> created.\n\nAmount: <b>{amount:,} VND</b>",
        "wallet_topup_success": "Topup successful!\n\nAmount: <b>{amount:,} VND</b>\nNew balance: <b>{balance:,} VND</b>",
        "wallet_not_enough": "Insufficient balance. Please top up.",
        "wallet_paid_success": "Paid with wallet!\n\nDeducted: <b>{amount:,} VND</b>\nRemaining: <b>{balance:,} VND</b>",
        "admin_users_title": "Users list",
        "admin_topups_title": "Users with wallet balance",
        "admin_user_detail": "User detail",
        "admin_user_topup_history": "Topup history",
    },
}


def t(user_id, key, **kwargs):
    lang = get_user_lang(user_id)
    if lang not in DEFAULT_TEXTS:
        lang = "vi"
    ov = get_text(f"{lang}_{key}")
    s = ov if ov else (DEFAULT_TEXTS[lang].get(key) or DEFAULT_TEXTS["vi"].get(key) or key)
    if kwargs:
        try:
            return s.format(**kwargs)
        except Exception:
            return s
    return s


TEXT_EMOJI_KEYS = {
    "shop_empty": "Thông báo shop trống", "shop_title": "Tiêu đề shop",
    "shop_prompt": "Dòng 'Chọn sản phẩm...'", "pending_empty": "Khi user không có đơn chờ",
    "pending_title": "Tiêu đề danh sách đơn chờ", "pending_hint": "Hướng dẫn trong menu đơn chờ",
    "order_hint": "Hướng dẫn sau thanh toán", "order_success": "Thông báo thành công",
    "order_paid": "Đơn đã thanh toán", "order_cancelled": "Đơn đã hủy",
    "order_pending": "Đơn chờ thanh toán", "order_no_key": "Hết key",
    "binance_note": "Lưu ý Binance", "binance_title": "Tiêu đề Binance",
    "binance_waiting": "Chờ admin xác nhận", "binance_not_set": "Chưa cấu hình ví",
    "lang_required": "Yêu cầu chọn ngôn ngữ", "lang_choose": "Yêu cầu chọn ngôn ngữ",
    "lang_changed": "Đã đổi ngôn ngữ", "not_found": "Không tìm thấy",
    "out_of_stock": "Hết hàng", "out_of_stock_wait": "Thông báo hết hàng chờ admin",
    "invalid_data": "Dữ liệu không hợp lệ", "no_more": "Hết sản phẩm",
    "order_cannot_cancel": "Không thể hủy", "order_cancelled_ok": "Đã hủy",
    "detail_no_desc": "Không có mô tả", "payment_method_title": "Tiêu đề chọn phương thức TT",
    "youtube_email_ask": "Yêu cầu gửi email", "youtube_email_preview": "Xem trước email",
    "youtube_email_pending": "Chờ admin xác nhận email", "youtube_email_done": "Hoàn tất email",
    "youtube_email_paid_msg": "Thông báo thanh toán email flow", "wallet_title": "Tiêu đề ví",
    "wallet_balance": "Số dư ví", "wallet_topup_success": "Nạp ví thành công",
    "wallet_not_enough": "Số dư không đủ", "email_confirm_required": "Yêu cầu xác nhận email",
    "admin_users_title": "DS người dùng", "admin_topups_title": "Users đã nạp ví",
    "admin_user_detail": "Chi tiết user", "admin_user_topup_history": "Lịch sử nạp ví",
    "admin_binance_req": "Tiêu đề yêu cầu xác nhận Binance",
    "admin_binance_topup_req": "Tiêu đề yêu cầu xác nhận nạp Binance",
    "binance_sent": "Thông báo user đã chuyển khoản",
}


def text_emoji_html(key, fallback=""):
    eid = get_setting(f"text_{key}")
    if eid:
        return f'<tg-emoji emoji-id="{eid}">{fallback or "•"}</tg-emoji> '
    return ""


def t_html(user_id, key, fallback_char="•", **kwargs):
    base = t(user_id, key, **kwargs)
    prefix = text_emoji_html(key, fallback=fallback_char)
    return f"{prefix}{html.escape(base)}"


# ============================================================
# SAFE HTML
# ============================================================
_TG_EMOJI_RE = re.compile(r'<tg-emoji[^>]*>(.*?)</tg-emoji>', re.DOTALL)


def _strip_tg_emoji(text):
    return _TG_EMOJI_RE.sub(r'\1', text)


def _is_entity_error(exc):
    s = str(exc).lower()
    return "entity_text_invalid" in s or "can't parse entities" in s or "entity" in s


async def safe_reply(message, text, **kw):
    try:
        return await message.reply_text(text, parse_mode=ParseMode.HTML, **kw)
    except Exception as e:
        if _is_entity_error(e):
            return await message.reply_text(_strip_tg_emoji(text), parse_mode=ParseMode.HTML, **kw)
        raise


async def safe_edit(query, text, **kw):
    try:
        return await query.edit_message_text(text, parse_mode=ParseMode.HTML, **kw)
    except Exception as e:
        if _is_entity_error(e):
            return await query.edit_message_text(_strip_tg_emoji(text), parse_mode=ParseMode.HTML, **kw)
        raise


async def safe_send(bot, chat_id, text, **kw):
    try:
        return await bot.send_message(chat_id=chat_id, text=text, parse_mode=ParseMode.HTML, **kw)
    except Exception as e:
        if _is_entity_error(e):
            return await bot.send_message(chat_id=chat_id, text=_strip_tg_emoji(text), parse_mode=ParseMode.HTML, **kw)
        raise


def format_key_display(key, lang="vi"):
    if not key:
        return ""
    ul = "Tài khoản" if lang == "vi" else "Username"
    pl = "Mật khẩu" if lang == "vi" else "Password"
    for sep in ("|", ":"):
        if sep in key:
            a, _, p = key.partition(sep)
            return (f"{ul}: <code>{html.escape(a.strip())}</code>\n"
                    f"{pl}: <code>{html.escape(p.strip())}</code>")
    return f"<code>{html.escape(key)}</code>"


# ============================================================
# UI KEYS
# ============================================================
UI_KEYS = {
    "shop": "Tiêu đề shop", "cart": "Nút mua", "orders": "Nút đơn hàng chờ",
    "back": "Nút quay lại", "next": "Nút trang sau", "prev": "Nút trang trước",
    "refresh": "Nút load lại", "check": "Nút kiểm tra thanh toán",
    "cancel": "Nút hủy đơn", "pay": "Nút thanh toán chung",
    "pay_payos": "Nút PayOS", "pay_binance": "Nút Binance",
    "binance": "Biểu tượng ví Binance", "order": "Biểu tượng đơn hàng",
    "order_code": "Nhãn mã đơn hàng", "money": "Nhãn tiền/giá",
    "product": "Nhãn sản phẩm", "detail": "Nhãn chi tiết",
    "back_pay": "Nút quay lại thanh toán", "back_menu": "Nút quay lại menu",
    "lang": "Biểu tượng ngôn ngữ", "account": "Biểu tượng tài khoản",
    "key_icon": "Biểu tượng key", "email": "Biểu tượng email",
    "send": "Biểu tượng gửi", "confirm": "Biểu tượng xác nhận",
    "delete": "Biểu tượng xóa", "recheck": "Biểu tượng kiểm tra lại",
    "wallet": "Biểu tượng ví", "topup": "Nút nạp ví",
    "hide": "Biểu tượng ẩn", "oos": "Biểu tượng hết hàng",
    "binance_sent_btn": "Nút 'Tôi đã chuyển khoản'",
    "admin_recv_key": "Nút admin nhận tiền giao key",
    "admin_recv_topup": "Nút admin nhận USDT cộng ví",
    "admin_cancel": "Nút admin hủy đơn",
}


def ui_emoji_html(key, fallback=""):
    eid = get_setting(f"ui_{key}")
    if eid:
        return f'<tg-emoji emoji-id="{eid}">{fallback or "•"}</tg-emoji>'
    return fallback


def ui_emoji_id(key):
    return get_setting(f"ui_{key}")


def button(text, callback_data=None, url=None, ui_key=None):
    kw = {"text": text}
    if callback_data:
        kw["callback_data"] = callback_data
    if url:
        kw["url"] = url
    if ui_key:
        eid = ui_emoji_id(ui_key)
        if eid:
            kw["icon_custom_emoji_id"] = eid
    return InlineKeyboardButton(**kw)


def extract_custom_emoji_from_message(message):
    text = message.text or message.caption or ""
    ents = message.entities or message.caption_entities or []
    ces = [e for e in ents if e.type == "custom_emoji"]
    if not ces:
        return text, None
    eid = ces[0].custom_emoji_id
    if not eid or not eid.isdigit():
        return text, None
    enc = text.encode("utf-16-le")
    for e in sorted(ces, key=lambda x: x.offset, reverse=True):
        s, en = e.offset * 2, (e.offset + e.length) * 2
        enc = enc[:s] + enc[en:]
    return enc.decode("utf-16-le"), eid


def product_name_html(name, emoji_id=None):
    safe = html.escape(name)
    if emoji_id:
        return f'<tg-emoji emoji-id="{emoji_id}">•</tg-emoji> {safe}'
    return safe


async def validate_custom_emoji(bot, chat_id, emoji_id):
    try:
        m = await bot.send_message(chat_id=chat_id,
                                   text=f'<tg-emoji emoji-id="{emoji_id}">.</tg-emoji>',
                                   parse_mode=ParseMode.HTML)
        has_entity = any(e.type == "custom_emoji"
                         and getattr(e, "custom_emoji_id", None) == emoji_id
                         for e in (m.entities or []))
        await m.delete()
        return has_entity
    except Exception as e:
        logger.info(f"emoji validate fail: {e}")
        return False


# ============================================================
# BUTTON BUILDERS
# ============================================================
def product_buttons(products, page=0, per_page=5, uid=None):
    kb = []
    for p in products:
        stock = int(p.get("stock", 0))
        if stock > 0:
            text = f"{p['name']} - {p['price']:,}đ - còn {stock}"
        else:
            text = f"{t(uid, 'stock_out_tag')} {p['name']} - {p['price']:,}đ"
        kw = {"text": text, "callback_data": f"detail_{p['id']}"}
        if p.get("emoji_id"):
            kw["icon_custom_emoji_id"] = p["emoji_id"]
        kb.append([InlineKeyboardButton(**kw)])
    nav = []
    if page > 0:
        nav.append(button(t(uid, "btn_prev"), callback_data=f"page_{page-1}", ui_key="prev"))
    nav.append(button(t(uid, "btn_refresh"), callback_data=f"refresh_{page}", ui_key="refresh"))
    if len(products) == per_page:
        nav.append(button(t(uid, "btn_next"), callback_data=f"page_{page+1}", ui_key="next"))
    if nav:
        kb.append(nav)
    kb.append([
        button(t(uid, "btn_orders"), callback_data="my_orders", ui_key="orders"),
        button(t(uid, "btn_wallet"), callback_data="wallet", ui_key="wallet"),
    ])
    kb.append([button(t(uid, "btn_lang"), callback_data="menu_lang", ui_key="lang")])
    return InlineKeyboardMarkup(kb)


def order_buttons(order_id, uid=None):
    return InlineKeyboardMarkup([
        [button(t(uid, "btn_check"), callback_data=f"check_{order_id}", ui_key="check")],
        [button(t(uid, "btn_back_pay"), callback_data=f"backpay_{order_id}", ui_key="back_pay")],
        [button(t(uid, "btn_cancel"), callback_data=f"cancel_{order_id}", ui_key="cancel")],
    ])


def detail_buttons(product_id, uid=None):
    return InlineKeyboardMarkup([
        [button(t(uid, "btn_buy"), callback_data=f"buy_{product_id}", ui_key="cart")],
        [button(t(uid, "btn_back"), callback_data="back_list", ui_key="back")],
    ])


def payment_buttons(order_id, uid=None):
    return InlineKeyboardMarkup([
        [button(t(uid, "btn_pay_wallet"), callback_data=f"pay_wallet_{order_id}", ui_key="wallet")],
        [button(t(uid, "btn_pay_payos"), callback_data=f"pay_payos_{order_id}", ui_key="pay_payos")],
        [button(t(uid, "btn_pay_binance"), callback_data=f"pay_binance_{order_id}", ui_key="pay_binance")],
        [button(t(uid, "btn_cancel"), callback_data=f"cancel_{order_id}", ui_key="cancel")],
    ])


def lang_buttons(uid=None):
    return InlineKeyboardMarkup([
        [button(t(uid, "btn_lang_vi"), callback_data="setlang_vi", ui_key="lang")],
        [button(t(uid, "btn_lang_en"), callback_data="setlang_en", ui_key="lang")],
    ])


def email_confirm_buttons(order_code, uid=None):
    return InlineKeyboardMarkup([
        [button(t(uid, "youtube_email_btn_confirm_send"),
                callback_data=f"cfmsend_{order_code}", ui_key="send")],
        [button(t(uid, "youtube_email_btn_cancel"),
                callback_data=f"cfmcancel_{order_code}", ui_key="cancel")],
    ])


# ============================================================
# HELPERS
# ============================================================
async def _notify_admin_email_request(bot, order, email, tg_user=None):
    try:
        product = get_product(order["product_id"])
        prod_name = html.escape(product["name"]) if product else "?"
        if tg_user is not None:
            if getattr(tg_user, "username", None):
                user_info = f"@{tg_user.username}"
            else:
                user_info = getattr(tg_user, "full_name", None) or "?"
        else:
            try:
                chat = await bot.get_chat(order["user_id"])
                if chat.username:
                    user_info = f"@{chat.username}"
                else:
                    user_info = chat.full_name or "?"
            except Exception:
                user_info = "?"
        prefix_email = ui_emoji_html("email")
        admin_text = (f"{prefix_email} <b>{html.escape(t(0, 'admin_email_request_title'))}</b>\n\n"
                      f"Order: <code>{order['order_code']}</code>\n"
                      f"User: {html.escape(user_info)} (<code>{order['user_id']}</code>)\n"
                      f"SP: {prod_name}\nEmail: <code>{html.escape(email)}</code>")
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton(t(0, "admin_email_confirm_btn"),
                                  callback_data=f"cfemail_{order['order_code']}")]
        ])
        sent = 0
        for aid in Config.ADMIN_IDS:
            try:
                await safe_send(bot, aid, admin_text, reply_markup=kb)
                sent += 1
            except Exception as e:
                logger.error(f"Notify admin {aid} failed: {e}")
        return sent
    except Exception as e:
        logger.error(f"_notify_admin_email_request error: {e}", exc_info=True)
        return 0


async def check_email_block(query, uid):
    order = get_awaiting_email_order(uid)
    if order and order.get("email_status") in ("awaiting", "awaiting_user_confirm"):
        try:
            await query.answer(t(uid, "email_confirm_required"), show_alert=True)
        except Exception:
            pass
        return True
    return False


# ============================================================
# USER HANDLERS
# ============================================================
async def start(update, context):
    user = update.effective_user
    register_user(user.id, user.username, user.first_name, user.last_name)
    if not is_lang_set(user.id):
        await safe_reply(update.message, t_html(user.id, "lang_required"),
                         reply_markup=lang_buttons(uid=user.id))
        return
    prods = list_products(limit=5, offset=0)
    if not prods:
        await safe_reply(update.message, t_html(user.id, "shop_empty"))
        return
    ui_icon = ui_emoji_html("shop")
    title_body = t_html(user.id, "shop_title")
    title_html = f"{ui_icon} <b>{title_body}</b>" if ui_icon else f"<b>{title_body}</b>"
    await safe_reply(update.message,
                     f"{title_html}\n\n{t_html(user.id, 'shop_prompt')}",
                     reply_markup=product_buttons(prods, page=0, uid=user.id))


async def lang_cmd(update, context):
    uid = update.effective_user.id
    await safe_reply(update.message, t_html(uid, "lang_choose"),
                     reply_markup=lang_buttons(uid=uid))


async def setlang_callback(update, context):
    query = update.callback_query
    await query.answer()
    lang = query.data.split("_")[1]
    if lang not in ("vi", "en"):
        return
    uid = query.from_user.id
    set_user_lang(uid, lang)
    prods = list_products(limit=5, offset=0)
    if not prods:
        await safe_edit(query, t_html(uid, "shop_empty"))
        return
    ui_icon = ui_emoji_html("shop")
    title_body = t_html(uid, "shop_title")
    title_html = f"{ui_icon} <b>{title_body}</b>" if ui_icon else f"<b>{title_body}</b>"
    await safe_edit(query, f"{title_html}\n\n{t_html(uid, 'shop_prompt')}",
                    reply_markup=product_buttons(prods, page=0, uid=uid))


async def menu_lang_callback(update, context):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    kb = InlineKeyboardMarkup([
        [button(t(uid, "btn_lang_vi"), callback_data="setlang_vi", ui_key="lang")],
        [button(t(uid, "btn_lang_en"), callback_data="setlang_en", ui_key="lang")],
        [button(t(uid, "btn_back"), callback_data="back_list", ui_key="back")],
    ])
    await safe_edit(query, t_html(uid, "lang_choose"), reply_markup=kb)


async def refresh_products_callback(update, context):
    query = update.callback_query
    uid = query.from_user.id
    if await check_email_block(query, uid):
        return
    try:
        await query.answer(t(uid, "refreshed"), show_alert=False)
    except Exception:
        pass
    try:
        page = int(query.data.split("_")[1])
    except (ValueError, IndexError):
        page = 0
    prods = list_products(limit=5, offset=page * 5)
    if not prods:
        await safe_edit(query, t_html(uid, "no_more"), reply_markup=None)
        return
    await safe_edit(query, f"<b>{html.escape(t(uid, 'list_title', page=page + 1))}</b>",
                    reply_markup=product_buttons(prods, page, uid=uid))


async def list_products_callback(update, context):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    if await check_email_block(query, uid):
        return
    data = query.data
    page = 0
    if data.startswith("page_"):
        try:
            page = max(0, int(data.split("_")[1]))
        except ValueError:
            page = 0
    prods = list_products(limit=5, offset=page * 5)
    if not prods:
        await safe_edit(query, t_html(uid, "no_more"), reply_markup=None)
        return
    await safe_edit(query, f"<b>{html.escape(t(uid, 'list_title', page=page + 1))}</b>",
                    reply_markup=product_buttons(prods, page, uid=uid))


async def show_product_detail(update, context):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    if await check_email_block(query, uid):
        return
    try:
        pid = int(query.data.split("_")[1])
    except (ValueError, IndexError):
        return
    p = get_product(pid)
    if not p:
        await safe_edit(query, t_html(uid, "not_found"))
        return
    stock = int(p.get("stock", 0))
    name_html = product_name_html(p["name"], p.get("emoji_id"))
    desc_raw = p.get("description") or ""
    desc_html = html.escape(desc_raw) if desc_raw else f"<i>{html.escape(t(uid, 'detail_no_desc'))}</i>"
    money_icon = ui_emoji_html("money")
    stock_html = str(stock) if stock > 0 else f"<b>{html.escape(t(uid, 'stock_out_tag'))}</b>"
    text = (f"<b>{html.escape(t(uid, 'detail_title'))} #{p['id']}</b>\n\n"
            f"<b>{html.escape(t(uid, 'detail_name'))}:</b> {name_html}\n"
            f"{money_icon} <b>{html.escape(t(uid, 'detail_price'))}:</b> {p['price']:,} VND\n"
            f"<b>{html.escape(t(uid, 'detail_stock'))}:</b> {stock_html}\n"
            f"<b>{html.escape(t(uid, 'detail_sold'))}:</b> {p['sold']}\n\n"
            f"<b>{html.escape(t(uid, 'detail_desc'))}:</b>\n{desc_html}")
    if stock > 0:
        kb = detail_buttons(pid, uid=uid)
    else:
        kb = InlineKeyboardMarkup([[button(t(uid, "btn_back"), callback_data="back_list", ui_key="back")]])
        text += f"\n\n⚠️ <b>{html.escape(t(uid, 'out_of_stock_wait'))}</b>"
    await safe_edit(query, text, reply_markup=kb)


async def buy_product(update, context):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    if await check_email_block(query, uid):
        return
    try:
        pid = int(query.data.split("_")[1])
    except (ValueError, IndexError):
        await query.answer(t(uid, "invalid_data"), show_alert=True)
        return
    p = get_product(pid)
    if not p:
        await query.answer(t(uid, "not_found"), show_alert=True)
        return
    if int(p.get("stock", 0)) <= 0:
        await query.answer(t(uid, "out_of_stock_wait"), show_alert=True)
        return
    order_code = int(f"{int(datetime.now().timestamp())}{pid:03d}{uid % 1000:03d}")
    create_order(order_code, uid, pid, 1, p["price"])
    name_html = product_name_html(p["name"], p.get("emoji_id"))
    text = (f"{ui_emoji_html('order')} <b>{html.escape(t(uid, 'order_title'))} #{order_code}</b>\n\n"
            f"{ui_emoji_html('product')} <b>{html.escape(t(uid, 'order_product'))}:</b> {name_html}\n"
            f"{ui_emoji_html('money')} <b>{html.escape(t(uid, 'order_amount'))}:</b> {p['price']:,} VND\n\n"
            f"{t_html(uid, 'payment_method_title')}")
    await safe_edit(query, text, reply_markup=payment_buttons(order_code, uid=uid))


async def pay_payos_callback(update, context):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    if await check_email_block(query, uid):
        return
    try:
        order_code = int(query.data.split("_")[2])
    except (ValueError, IndexError):
        return
    order = get_order(order_code)
    if not order:
        await safe_edit(query, t_html(uid, "order_not_found"), reply_markup=None)
        return
    p = get_product(order["product_id"])
    url, err = create_payment_link(order_code=order_code, amount=order["amount"],
                                   description=f"DH{order_code}", buyer_name=query.from_user.full_name)
    if not url:
        await safe_edit(query, f"PayOS error: {html.escape(str(err))}", reply_markup=None)
        return
    name_html = product_name_html(p["name"], p.get("emoji_id")) if p else str(order["product_id"])
    text = (f"{ui_emoji_html('order')} <b>{html.escape(t(uid, 'order_title'))} #{order_code}</b>\n\n"
            f"{ui_emoji_html('product')} <b>{html.escape(t(uid, 'order_product'))}:</b> {name_html}\n"
            f"{ui_emoji_html('money')} <b>{html.escape(t(uid, 'order_amount'))}:</b> {order['amount']:,} VND\n"
            f"{ui_emoji_html('order_code')} <b>{html.escape(t(uid, 'order_content'))}:</b> <code>DH{order_code}</code>\n\n"
            f'{ui_emoji_html("pay_payos")} <a href="{url}">{html.escape(t(uid, "order_pay"))}</a>\n\n'
            f"{t_html(uid, 'order_hint')}")
    await safe_edit(query, text, reply_markup=order_buttons(order_code, uid=uid),
                    disable_web_page_preview=True)


async def pay_binance_callback(update, context):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    if await check_email_block(query, uid):
        return
    try:
        order_code = int(query.data.split("_")[2])
    except (ValueError, IndexError):
        return
    order = get_order(order_code)
    if not order:
        await safe_edit(query, t_html(uid, "order_not_found"), reply_markup=None)
        return
    addr = get_binance_address()
    if not addr:
        await safe_edit(query, t_html(uid, "binance_not_set"), reply_markup=None)
        return
    rate, rate_source, _err = get_binance_rate_live()
    usdt = round(order["amount"] / rate, 2)
    net = get_binance_network()
    text = (f"{ui_emoji_html('binance')} <b>{html.escape(t(uid, 'binance_title'))}</b>\n"
            f"{ui_emoji_html('order')} #{order_code}\n\n"
            f"{ui_emoji_html('money')} <b>{html.escape(t(uid, 'binance_amount'))}:</b> <code>{usdt} USDT</code>\n"
            f"<b>{html.escape(t(uid, 'binance_rate'))}:</b> <code>{rate:,.0f}</code> VND/USDT <i>({html.escape(rate_source)})</i>\n"
            f"<b>{html.escape(t(uid, 'binance_address'))}:</b>\n<code>{html.escape(addr)}</code>\n"
            f"<b>{html.escape(t(uid, 'binance_network'))}:</b> <b>{html.escape(net)}</b>\n"
            f"{ui_emoji_html('order_code')} <b>{html.escape(t(uid, 'binance_memo'))}:</b> <code>DH{order_code}</code>\n\n"
            f"{t_html(uid, 'binance_note')}")
    kb = InlineKeyboardMarkup([
        [button(t(uid, "binance_sent"), callback_data=f"binance_sent_{order_code}", ui_key="binance_sent_btn")],
        [button(t(uid, "btn_back"), callback_data=f"backpay_{order_code}", ui_key="back")],
        [button(t(uid, "btn_cancel"), callback_data=f"cancel_{order_code}", ui_key="cancel")],
    ])
    await safe_edit(query, text, reply_markup=kb, disable_web_page_preview=True)


async def binance_sent_callback(update, context):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    try:
        order_code = int(query.data.split("_")[2])
    except (ValueError, IndexError):
        return
    order = get_order(order_code)
    if not order:
        await safe_edit(query, t_html(uid, "order_not_found"), reply_markup=None)
        return
    if order["status"] != "pending":
        await safe_edit(query, t_html(uid, "order_paid"), reply_markup=None)
        return
    update_order_status(order_code, "pending")
    await safe_edit(query, t_html(uid, "binance_waiting"), reply_markup=None)
    p = get_product(order["product_id"])
    rate, rate_source, _ = get_binance_rate_live()
    usdt = round(order["amount"] / rate, 2)
    user_info = f"@{query.from_user.username}" if query.from_user.username else (query.from_user.full_name or "?")
    req_prefix = text_emoji_html("admin_binance_req")
    admin_text = (f"{req_prefix}<b>{html.escape(t(0, 'admin_binance_req'))}</b>\n\n"
                  f"• Order: <code>{order_code}</code>\n"
                  f"• User: {html.escape(user_info)} (<code>{uid}</code>)\n"
                  f"• SP: {html.escape(p['name']) if p else '?'}\n"
                  f"• Số tiền: <b>{order['amount']:,} VND</b> ≈ <b>{usdt} USDT</b>\n"
                  f"• Rate: <code>{rate:,.0f}</code> ({html.escape(rate_source)})\n"
                  f"• Memo: <code>DH{order_code}</code>\n\n"
                  f"Kiểm tra Binance → nếu đã nhận USDT → bấm nút dưới.")
    kb = InlineKeyboardMarkup([
        [button(t(0, "admin_received_key"), callback_data=f"cfbinance_{order_code}", ui_key="admin_recv_key")],
        [button(t(0, "admin_cancel_order"), callback_data=f"cancel_{order_code}", ui_key="admin_cancel")],
    ])
    for aid in Config.ADMIN_IDS:
        try:
            await safe_send(context.bot, aid, admin_text, reply_markup=kb)
        except Exception as e:
            logger.error(f"Notify admin {aid}: {e}")


async def back_pay_callback(update, context):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    if await check_email_block(query, uid):
        return
    try:
        order_code = int(query.data.split("_")[1])
    except (ValueError, IndexError):
        return
    order = get_order(order_code)
    if not order:
        await safe_edit(query, t_html(uid, "order_not_found"), reply_markup=None)
        return
    p = get_product(order["product_id"])
    name_html = product_name_html(p["name"], p.get("emoji_id")) if p else "?"
    text = (f"{ui_emoji_html('order')} <b>{html.escape(t(uid, 'order_title'))} #{order_code}</b>\n\n"
            f"{ui_emoji_html('product')} <b>{html.escape(t(uid, 'order_product'))}:</b> {name_html}\n"
            f"{ui_emoji_html('money')} <b>{html.escape(t(uid, 'order_amount'))}:</b> {order['amount']:,} VND\n\n"
            f"{t_html(uid, 'payment_method_title')}")
    await safe_edit(query, text, reply_markup=payment_buttons(order_code, uid=uid))


async def check_order(update, context):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    if await check_email_block(query, uid):
        return
    try:
        order_code = int(query.data.split("_")[1])
    except (ValueError, IndexError):
        await safe_edit(query, t_html(uid, "invalid_data"))
        return
    order = get_order(order_code)
    if not order:
        await safe_edit(query, t_html(uid, "order_not_found"), reply_markup=None)
        return
    product = get_product(order["product_id"])
    email_flow = bool(product and product.get("requires_email"))
    if order["status"] == "cancelled":
        await safe_edit(query, t_html(uid, "order_cancelled"), reply_markup=None)
        return
    if order["status"] == "paid":
        if email_flow:
            es = order.get("email_status")
            if es == "awaiting":
                await safe_edit(query, t_html(uid, "youtube_email_ask"))
                return
            if es == "awaiting_user_confirm":
                email = order.get("customer_email") or ""
                await safe_edit(query, t_html(uid, "youtube_email_preview", email=html.escape(email)),
                                reply_markup=email_confirm_buttons(order_code, uid=uid))
                return
            if es == "pending_admin":
                await safe_edit(query, t_html(uid, "youtube_email_pending",
                                              email=html.escape(order.get("customer_email") or "")))
                return
            if es == "confirmed":
                await safe_edit(query, t_html(uid, "youtube_email_done",
                                              email=html.escape(order.get("customer_email") or "")))
                return
        kb = InlineKeyboardMarkup([[button(t(uid, "btn_back_shop"), callback_data="back_list", ui_key="back")]])
        await safe_edit(query,
            f"{t_html(uid, 'order_paid')}\n\n"
            f"<b>{html.escape(t(uid, 'account_info'))}:</b>\n"
            f"{format_key_display(order['key_assigned'] or '', get_user_lang(uid))}",
            reply_markup=kb)
        return
    data = get_payment_status(order_code)
    paid = bool(data and data.get("code") == "00" and data.get("data", {}).get("status") == "PAID")
    if paid:
        if email_flow:
            update_order_status(order_code, "paid", None)
            set_order_email_status(order_code, "awaiting")
            decrement_stock(order["product_id"])
            await safe_edit(query, t_html(uid, "youtube_email_paid_msg"))
        else:
            key = get_available_key(order["product_id"])
            if key:
                update_order_status(order_code, "paid", key)
                kb = InlineKeyboardMarkup([[button(t(uid, "btn_back_shop"), callback_data="back_list", ui_key="back")]])
                await safe_edit(query,
                    f"{t_html(uid, 'order_success')}\n\n"
                    f"<b>{html.escape(t(uid, 'account_info'))}:</b>\n"
                    f"{format_key_display(key, get_user_lang(uid))}",
                    reply_markup=kb)
            else:
                await safe_edit(query, t_html(uid, "order_no_key"), reply_markup=None)
    else:
        await safe_edit(query, f"#{order_code} {t_html(uid, 'order_pending')}",
                        reply_markup=order_buttons(order_code, uid=uid))


async def cancel_order(update, context):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    try:
        order_code = int(query.data.split("_")[1])
    except (ValueError, IndexError):
        return
    order = get_order(order_code)
    if not order or order["status"] != "pending":
        await safe_edit(query, t_html(uid, "order_cannot_cancel"))
        return
    product = get_product(order["product_id"])
    email_flow = bool(product and product.get("requires_email"))
    data = get_payment_status(order_code)
    paid = bool(data and data.get("code") == "00" and data.get("data", {}).get("status") == "PAID")
    if paid:
        if email_flow:
            update_order_status(order_code, "paid", None)
            set_order_email_status(order_code, "awaiting")
            decrement_stock(order["product_id"])
            await safe_edit(query, t_html(uid, "youtube_email_paid_msg"))
        else:
            key = get_available_key(order["product_id"])
            if key:
                update_order_status(order_code, "paid", key)
                kb = InlineKeyboardMarkup([[button(t(uid, "btn_back_shop"), callback_data="back_list", ui_key="back")]])
                await safe_edit(query,
                    f"{t_html(uid, 'order_success')}\n\n"
                    f"<b>{html.escape(t(uid, 'account_info'))}:</b>\n"
                    f"{format_key_display(key, get_user_lang(uid))}",
                    reply_markup=kb)
            else:
                await safe_edit(query, t_html(uid, "order_no_key"), reply_markup=None)
        return
    update_order_status(order_code, "cancelled")
    kb = InlineKeyboardMarkup([
        [button(t(uid, "btn_orders"), callback_data="my_orders", ui_key="orders")],
        [button(t(uid, "btn_back_shop"), callback_data="back_list", ui_key="back")],
    ])
    await safe_edit(query,
        f"{t_html(uid, 'order_cancelled_ok')} #{order_code}.\n\n"
        f"<i>{html.escape(t(uid, 'order_cancelled_check_hint'))}</i>",
        reply_markup=kb)


# ============================================================
# ĐƠN HÀNG CHỜ
# ============================================================
async def my_orders(update, context):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    if await check_email_block(query, uid):
        return
    orders = get_recent_orders_by_user(uid, hours=24)
    order_icon = ui_emoji_html("order")
    if not orders:
        kb = InlineKeyboardMarkup([[button(t(uid, "btn_back_shop"), callback_data="back_list", ui_key="back")]])
        await safe_edit(query, t_html(uid, "pending_empty"), reply_markup=kb)
        return
    product_ids = list({o["product_id"] for o in orders})
    products_map = {}
    for doc in get_db().products.find({"id": {"$in": product_ids}},
                                      {"id": 1, "name": 1, "emoji_id": 1}):
        products_map[doc["id"]] = doc
    text = f"{order_icon} <b>{html.escape(t(uid, 'pending_title'))}:</b>\n\n"
    kb_rows = []
    for i, o in enumerate(orders, 1):
        p = products_map.get(o["product_id"])
        name = product_name_html(p["name"], p.get("emoji_id")) if p else "?"
        short_code = str(o["id"])[-6:]
        status_icon = "" if o["status"] == "pending" else " [Đã hủy]"
        text += f"{i}. <code>#{o['id']}</code> - {name} - {o['amount']:,} VND{status_icon}\n"
        if o["status"] == "pending":
            kb_rows.append([InlineKeyboardButton(
                f"{t(uid, 'btn_pay_again')} #{short_code}",
                callback_data=f"backpay_{o['id']}")])
            kb_rows.append([InlineKeyboardButton(
                f"{t(uid, 'btn_delete_order')} #{short_code}",
                callback_data=f"del_order_{o['id']}")])
        else:
            kb_rows.append([InlineKeyboardButton(
                f"{t(uid, 'btn_recheck_order')} #{short_code}",
                callback_data=f"recheck_{o['id']}")])
            kb_rows.append([InlineKeyboardButton(
                f"{t(uid, 'btn_hide_order')} #{short_code}",
                callback_data=f"hide_order_{o['id']}")])
    text += f"\n<i>{html.escape(t(uid, 'pending_hint'))}</i>"
    kb_rows.append([button(t(uid, "btn_back_shop"), callback_data="back_list", ui_key="back")])
    await safe_edit(query, text, reply_markup=InlineKeyboardMarkup(kb_rows))


async def delete_pending_order_callback(update, context):
    query = update.callback_query
    uid = query.from_user.id
    try:
        oc = int(query.data.split("_")[2])
    except (ValueError, IndexError):
        await query.answer("Loi du lieu", show_alert=True)
        return
    order = get_order(oc)
    if not order or order["user_id"] != uid:
        await query.answer("Khong tim thay don", show_alert=True)
        return
    if order["status"] != "pending":
        await query.answer("Don da xu ly roi", show_alert=True)
        return
    data = get_payment_status(oc)
    paid = bool(data and data.get("code") == "00" and data.get("data", {}).get("status") == "PAID")
    if paid:
        product = get_product(order["product_id"])
        email_flow = bool(product and product.get("requires_email"))
        if email_flow:
            update_order_status(oc, "paid", None)
            set_order_email_status(oc, "awaiting")
            decrement_stock(order["product_id"])
            await query.answer(t(uid, "order_recheck_paid"), show_alert=True)
            await safe_edit(query, t_html(uid, "youtube_email_paid_msg"))
        else:
            key = get_available_key(order["product_id"])
            if key:
                update_order_status(oc, "paid", key)
                await query.answer(t(uid, "order_recheck_paid"), show_alert=True)
                kb = InlineKeyboardMarkup([[button(t(uid, "btn_back_shop"), callback_data="back_list", ui_key="back")]])
                await safe_edit(query,
                    f"{t_html(uid, 'order_success')}\n\n"
                    f"<b>{html.escape(t(uid, 'account_info'))}:</b>\n"
                    f"{format_key_display(key, get_user_lang(uid))}",
                    reply_markup=kb)
            else:
                await query.answer(t(uid, "order_no_key"), show_alert=True)
        return
    update_order_status(oc, "cancelled")
    await query.answer(t(uid, "order_cancelled_ok"), show_alert=False)
    await my_orders(update, context)


async def recheck_cancelled_order_callback(update, context):
    query = update.callback_query
    uid = query.from_user.id
    try:
        order_code = int(query.data.split("_")[1])
    except (ValueError, IndexError):
        await query.answer("Loi du lieu", show_alert=True)
        return
    order = get_order(order_code)
    if not order or order["user_id"] != uid:
        await query.answer("Khong tim thay don", show_alert=True)
        return
    if order["status"] == "paid":
        await query.answer("Don da thanh toan", show_alert=True)
        await my_orders(update, context)
        return
    data = get_payment_status(order_code)
    paid = bool(data and data.get("code") == "00" and data.get("data", {}).get("status") == "PAID")
    if not paid:
        await query.answer(t(uid, "order_recheck_notpaid"), show_alert=True)
        return
    product = get_product(order["product_id"])
    email_flow = bool(product and product.get("requires_email"))
    if email_flow:
        restore_cancelled_order(order_code)
        set_order_email_status(order_code, "awaiting")
        decrement_stock(order["product_id"])
        await query.answer(t(uid, "order_recheck_paid"), show_alert=True)
        await safe_edit(query, t_html(uid, "youtube_email_paid_msg"))
    else:
        key = get_available_key(order["product_id"])
        if not key:
            await query.answer(t(uid, "order_no_key"), show_alert=True)
            return
        restore_cancelled_order(order_code, key_assigned=key)
        await query.answer(t(uid, "order_recheck_paid"), show_alert=True)
        kb = InlineKeyboardMarkup([[button(t(uid, "btn_back_shop"), callback_data="back_list", ui_key="back")]])
        await safe_edit(query,
            f"{t_html(uid, 'order_success')}\n\n"
            f"<b>{html.escape(t(uid, 'account_info'))}:</b>\n"
            f"{format_key_display(key, get_user_lang(uid))}",
            reply_markup=kb)


async def hide_order_callback(update, context):
    query = update.callback_query
    uid = query.from_user.id
    try:
        oc = int(query.data.split("_")[2])
    except (ValueError, IndexError):
        await query.answer("Loi du lieu", show_alert=True)
        return
    order = get_order(oc)
    if not order or order["user_id"] != uid:
        await query.answer("Khong tim thay don", show_alert=True)
        return
    if order["status"] != "cancelled":
        await query.answer("Chi an duoc don da huy", show_alert=True)
        return
    hide_order(oc, uid)
    await query.answer("Da an don", show_alert=False)
    await my_orders(update, context)


# ============================================================
# WALLET
# ============================================================
async def wallet_callback(update, context):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    if await check_email_block(query, uid):
        return
    bal = get_user_balance(uid)
    text = (f"{ui_emoji_html('wallet')} <b>{html.escape(t(uid, 'wallet_title'))}</b>\n\n"
            f"<b>{html.escape(t(uid, 'wallet_balance'))}:</b> {bal:,} VND")
    kb = InlineKeyboardMarkup([
        [button(t(uid, "btn_topup"), callback_data="topup", ui_key="topup")],
        [button(t(uid, "btn_back_shop"), callback_data="back_list", ui_key="back")],
    ])
    await safe_edit(query, text, reply_markup=kb)


async def topup_callback(update, context):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    if await check_email_block(query, uid):
        return
    context.user_data["topup_state"] = True
    kb = InlineKeyboardMarkup([[button(t(uid, "btn_back"), callback_data="wallet", ui_key="back")]])
    await safe_reply(query.message,
                     f"<b>{html.escape(t(uid, 'wallet_topup_prompt'))}</b>\n\n<i>Vi du: 50000</i>",
                     reply_markup=kb)


async def handle_topup_amount(update, context):
    uid = update.effective_user.id
    if not context.user_data.get("topup_state"):
        return False
    context.user_data["topup_state"] = False
    text = (update.message.text or "").strip().replace(".", "").replace(",", "")
    if not text.isdigit():
        await safe_reply(update.message, t_html(uid, "wallet_topup_invalid"))
        return True
    amount = int(text)
    if amount < 2000:
        await safe_reply(update.message, t_html(uid, "wallet_topup_invalid"))
        return True
    order_code = int(f"{int(datetime.now().timestamp())}{uid % 100000:05d}")
    create_topup_order(order_code, uid, amount)
    kb = InlineKeyboardMarkup([
        [button(t(uid, "btn_topup_payos"), callback_data=f"topup_payos_{order_code}", ui_key="pay_payos")],
        [button(t(uid, "btn_topup_binance"), callback_data=f"topup_binance_{order_code}", ui_key="pay_binance")],
        [button(t(uid, "btn_cancel"), callback_data="wallet", ui_key="cancel")],
    ])
    await safe_reply(update.message,
                     t_html(uid, "wallet_topup_created", code=order_code, amount=amount),
                     reply_markup=kb)
    return True


async def topup_payos_callback(update, context):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    try:
        oc = int(query.data.split("_")[2])
    except (ValueError, IndexError):
        return
    order = get_order(oc)
    if not order or order.get("type") != "topup":
        await safe_edit(query, "Khong tim thay don.", reply_markup=None)
        return
    url, err = create_payment_link(order_code=oc, amount=order["amount"],
                                   description=f"NAP{oc}", buyer_name=query.from_user.full_name)
    if not url:
        await safe_edit(query, f"PayOS error: {html.escape(str(err))}", reply_markup=None)
        return
    kb = InlineKeyboardMarkup([
        [button(t(uid, "btn_check"), callback_data=f"topup_check_{oc}", ui_key="check")],
        [button(t(uid, "btn_back"), callback_data="wallet", ui_key="back")],
    ])
    text = (f"<b>Nạp ví #{oc}</b>\n\n"
            f"<b>Số tiền:</b> {order['amount']:,} VND\n"
            f"<b>Nội dung CK:</b> <code>NAP{oc}</code>\n\n"
            f'<a href="{url}">Nhấn để thanh toán</a>\n\n'
            f"Sau khi TT, nhấn 'Đã thanh toán? Kiểm tra'.")
    await safe_edit(query, text, reply_markup=kb, disable_web_page_preview=True)


# ===== FIX BINANCE TOPUP =====
async def topup_binance_callback(update, context):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    try:
        oc = int(query.data.split("_")[2])
    except (ValueError, IndexError):
        return
    order = get_order(oc)
    if not order or order.get("type") != "topup":
        await safe_edit(query, "Khong tim thay don.", reply_markup=None)
        return
    addr = get_binance_address()
    if not addr:
        await safe_edit(query, t_html(uid, "binance_not_set"), reply_markup=None)
        return
    rate, src, _ = get_binance_rate_live()
    usdt = round(order["amount"] / rate, 2)
    net = get_binance_network()
    text = (f"<b>Nạp ví #{oc}</b>\n\n"
            f"<b>Số tiền:</b> {order['amount']:,} VND ≈ <code>{usdt} USDT</code>\n"
            f"<b>Rate:</b> <code>{rate:,.0f}</code> ({html.escape(src)})\n"
            f"<b>Địa chỉ:</b>\n<code>{html.escape(addr)}</code>\n"
            f"<b>Mạng:</b> <b>{html.escape(net)}</b>\n"
            f"<b>Memo:</b> <code>NAP{oc}</code>\n\n"
            f"Sau khi chuyển khoản, nhấn nút bên dưới để admin xác nhận.")
    kb = InlineKeyboardMarkup([
        [button(t(uid, "binance_sent"), callback_data=f"topup_binance_sent_{oc}", ui_key="binance_sent_btn")],
        [button(t(uid, "btn_back"), callback_data="wallet", ui_key="back")],
    ])
    await safe_edit(query, text, reply_markup=kb, disable_web_page_preview=True)


async def topup_binance_sent_callback(update, context):
    """User báo đã chuyển USDT → gửi admin kèm nút xác nhận."""
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    try:
        oc = int(query.data.split("_")[3])
    except (ValueError, IndexError):
        return
    order = get_order(oc)
    if not order or order.get("type") != "topup":
        await safe_edit(query, "Khong tim thay don.", reply_markup=None)
        return
    if order["status"] != "pending":
        bal = get_user_balance(uid)
        await safe_edit(query, t_html(uid, "wallet_topup_success",
                                      amount=order["amount"], balance=bal))
        return
    await safe_edit(query, t_html(uid, "binance_waiting"), reply_markup=None)
    rate, src, _ = get_binance_rate_live()
    usdt = round(order["amount"] / rate, 2)
    user_info = f"@{query.from_user.username}" if query.from_user.username else (query.from_user.full_name or "?")
    req_prefix = text_emoji_html("admin_binance_topup_req")
    admin_text = (f"{req_prefix}<b>{html.escape(t(0, 'admin_binance_topup_req'))}</b>\n\n"
                  f"• Order: <code>{oc}</code>\n"
                  f"• User: {html.escape(user_info)} (<code>{uid}</code>)\n"
                  f"• Số tiền: <b>{order['amount']:,} VND</b> ≈ <b>{usdt} USDT</b>\n"
                  f"• Rate: <code>{rate:,.0f}</code> ({html.escape(src)})\n"
                  f"• Memo: <code>NAP{oc}</code>\n\n"
                  f"Kiểm tra Binance → nếu đã nhận USDT → bấm nút dưới.")
    kb = InlineKeyboardMarkup([
        [button(t(0, "admin_received_topup"), callback_data=f"cfbinancetopup_{oc}", ui_key="admin_recv_topup")],
        [button(t(0, "admin_cancel_order"), callback_data=f"cancel_{oc}", ui_key="admin_cancel")],
    ])
    for aid in Config.ADMIN_IDS:
        try:
            await safe_send(context.bot, aid, admin_text, reply_markup=kb)
        except Exception as e:
            logger.error(f"Notify admin {aid}: {e}")


async def confirm_binance_topup_callback(update, context):
    """Admin bấm 'Đã nhận USDT' → cộng tiền vào ví user."""
    query = update.callback_query
    admin_uid = query.from_user.id
    if admin_uid not in Config.ADMIN_IDS:
        await query.answer("Khong co quyen", show_alert=True)
        return
    try:
        oc = int(query.data.split("_")[1])
    except (ValueError, IndexError):
        await query.answer("Loi du lieu", show_alert=True)
        return
    order = get_order(oc)
    if not order or order.get("type") != "topup":
        await query.answer("Khong tim thay don", show_alert=True)
        return
    if order["status"] != "pending":
        await query.answer(f"Don o trang thai: {order['status']}", show_alert=True)
        return
    ok = mark_topup_paid(oc)
    if not ok:
        await query.answer("Loi cong vi", show_alert=True)
        return
    new_bal = get_user_balance(order["user_id"])
    await query.answer("Da cong vi")
    try:
        await query.edit_message_text(
            (query.message.text or "") + f"\n\n<b>[DA CONG VI - So du moi: {new_bal:,} VND]</b>",
            parse_mode=ParseMode.HTML, reply_markup=None)
    except Exception:
        pass
    try:
        await safe_send(context.bot, order["user_id"],
                        t_html(order["user_id"], "wallet_topup_success",
                               amount=order["amount"], balance=new_bal))
    except Exception as e:
        logger.error(f"notify user: {e}")


# ===== END FIX BINANCE TOPUP =====


async def topup_check_callback(update, context):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    try:
        oc = int(query.data.split("_")[2])
    except (ValueError, IndexError):
        return
    order = get_order(oc)
    if not order or order.get("type") != "topup":
        await safe_edit(query, "Khong tim thay don.", reply_markup=None)
        return
    if order["status"] == "paid":
        bal = get_user_balance(uid)
        await query.answer("Đã nạp trước đó", show_alert=True)
        await safe_edit(query, t_html(uid, "wallet_topup_success",
                                      amount=order["amount"], balance=bal))
        return
    data = get_payment_status(oc)
    paid = bool(data and data.get("code") == "00" and data.get("data", {}).get("status") == "PAID")
    if paid:
        mark_topup_paid(oc)
        bal = get_user_balance(uid)
        await query.answer("Nạp thành công", show_alert=True)
        await safe_edit(query, t_html(uid, "wallet_topup_success",
                                      amount=order["amount"], balance=bal))
    else:
        await query.answer("Chưa nhận được thanh toán", show_alert=True)


async def pay_wallet_callback(update, context):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    if await check_email_block(query, uid):
        return
    try:
        order_code = int(query.data.split("_")[2])
    except (ValueError, IndexError):
        return
    order = get_order(order_code)
    if not order or order["status"] != "pending":
        await safe_edit(query, "Đơn không hợp lệ.", reply_markup=None)
        return
    bal = get_user_balance(uid)
    if bal < order["amount"]:
        kb = InlineKeyboardMarkup([
            [button(t(uid, "btn_topup"), callback_data="topup", ui_key="topup")],
            [button(t(uid, "btn_back"), callback_data=f"backpay_{order_code}", ui_key="back")],
        ])
        await safe_edit(query,
            f"<b>{html.escape(t(uid, 'wallet_not_enough'))}</b>\n\n"
            f"Số dư: <code>{bal:,}</code> VND\n"
            f"Cần: <code>{order['amount']:,}</code> VND",
            reply_markup=kb)
        return
    if not subtract_balance(uid, order["amount"]):
        await query.answer(t(uid, "wallet_not_enough"), show_alert=True)
        return
    product = get_product(order["product_id"])
    email_flow = bool(product and product.get("requires_email"))
    if email_flow:
        update_order_status(order_code, "paid", None)
        set_order_email_status(order_code, "awaiting")
        decrement_stock(order["product_id"])
        new_bal = get_user_balance(uid)
        await safe_edit(query, t_html(uid, "wallet_paid_success",
                                      amount=order["amount"], balance=new_bal))
        await safe_send(context.bot, query.message.chat_id, t_html(uid, "youtube_email_paid_msg"))
    else:
        key = get_available_key(order["product_id"])
        if not key:
            add_balance(uid, order["amount"])
            await query.answer(t(uid, "order_no_key"), show_alert=True)
            return
        update_order_status(order_code, "paid", key)
        new_bal = get_user_balance(uid)
        kb = InlineKeyboardMarkup([[button(t(uid, "btn_back_shop"), callback_data="back_list", ui_key="back")]])
        await safe_edit(query,
            f"{t_html(uid, 'wallet_paid_success', amount=order['amount'], balance=new_bal)}\n\n"
            f"<b>{html.escape(t(uid, 'account_info'))}:</b>\n"
            f"{format_key_display(key, get_user_lang(uid))}",
            reply_markup=kb)


# ============================================================
# TEXT HANDLER
# ============================================================
async def handle_user_text(update, context):
    user = update.effective_user
    if user.id in Config.ADMIN_IDS:
        return
    text_in = (update.message.text or "").strip()
    logger.info(f"handle_user_text: uid={user.id} text='{text_in[:30]}' topup_state={context.user_data.get('topup_state')}")
    if await handle_topup_amount(update, context):
        return
    if not EMAIL_RE.match(text_in):
        return
    order = get_awaiting_email_order(user.id)
    if not order:
        return
    set_order_email(order["order_code"], text_in)
    await safe_reply(update.message,
                     t_html(user.id, "youtube_email_preview", email=html.escape(text_in)),
                     reply_markup=email_confirm_buttons(order["order_code"], uid=user.id))


async def confirm_send_email_callback(update, context):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    try:
        order_code = int(query.data.split("_")[1])
    except (ValueError, IndexError):
        await query.answer("Loi du lieu", show_alert=True)
        return
    order = get_order(order_code)
    if not order:
        await query.answer("Khong tim thay don", show_alert=True)
        return
    if order.get("email_status") != "awaiting_user_confirm":
        await query.answer("Don da xu ly roi", show_alert=True)
        return
    email = order.get("customer_email") or ""
    set_order_email_status(order_code, "pending_admin")
    await safe_edit(query, t_html(uid, "youtube_email_sent_admin", email=html.escape(email)),
                    reply_markup=None)
    await _notify_admin_email_request(context.bot, order, email, tg_user=query.from_user)


async def cancel_send_email_callback(update, context):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    try:
        order_code = int(query.data.split("_")[1])
    except (ValueError, IndexError):
        return
    order = get_order(order_code)
    if not order or order.get("email_status") != "awaiting_user_confirm":
        await safe_edit(query, t_html(uid, "order_not_found"), reply_markup=None)
        return
    set_order_email_status(order_code, "awaiting")
    await safe_edit(query, t_html(uid, "youtube_email_cancelled"), reply_markup=None)


async def confirm_email_callback(update, context):
    query = update.callback_query
    admin_uid = query.from_user.id
    if admin_uid not in Config.ADMIN_IDS:
        await query.answer("Khong co quyen", show_alert=True)
        return
    try:
        oc = int(query.data.split("_")[1])
    except (ValueError, IndexError):
        await query.answer("Loi du lieu", show_alert=True)
        return
    order = get_order(oc)
    if not order:
        await query.answer("Khong tim thay don", show_alert=True)
        return
    if order.get("email_status") == "confirmed":
        await query.answer("Da xac nhan truoc do", show_alert=True)
        return
    set_order_email_status(oc, "confirmed")
    await query.answer("Da xac nhan")
    try:
        await query.edit_message_text((query.message.text or "") + f"\n\n{t(0, 'admin_email_confirmed')}",
                                      parse_mode=ParseMode.HTML)
    except Exception:
        pass
    user_uid = order["user_id"]
    email = order.get("customer_email") or ""
    try:
        await safe_send(context.bot, user_uid,
                        t_html(user_uid, "youtube_email_done", email=html.escape(email)))
    except Exception as e:
        logger.error(f"Notify user {user_uid}: {e}")


async def confirm_binance_callback(update, context):
    """Admin bấm xác nhận đơn Binance sản phẩm → giao key."""
    query = update.callback_query
    admin_uid = query.from_user.id
    if admin_uid not in Config.ADMIN_IDS:
        await query.answer("Khong co quyen", show_alert=True)
        return
    try:
        oc = int(query.data.split("_")[1])
    except (ValueError, IndexError):
        await query.answer("Loi du lieu", show_alert=True)
        return
    order = get_order(oc)
    if not order:
        await query.answer("Khong tim thay don", show_alert=True)
        return
    if order["status"] != "pending":
        await query.answer(f"Don o trang thai: {order['status']}", show_alert=True)
        return
    product = get_product(order["product_id"])
    email_flow = bool(product and product.get("requires_email"))
    if email_flow:
        update_order_status(oc, "paid", None)
        set_order_email_status(oc, "awaiting")
        decrement_stock(order["product_id"])
        await query.answer("Da xac nhan (email flow)")
        try:
            await query.edit_message_text((query.message.text or "") + "\n\n<b>[DA XAC NHAN - EMAIL FLOW]</b>",
                                          parse_mode=ParseMode.HTML, reply_markup=None)
        except Exception:
            pass
        try:
            await safe_send(context.bot, order["user_id"],
                            t_html(order["user_id"], "youtube_email_paid_msg"))
        except Exception as e:
            logger.error(f"notify: {e}")
    else:
        key = get_available_key(order["product_id"])
        if not key:
            await query.answer("Het key! Nap them truoc.", show_alert=True)
            return
        update_order_status(oc, "paid", key)
        await query.answer("Da xac nhan va giao key")
        try:
            await query.edit_message_text(
                (query.message.text or "") + f"\n\n<b>[DA XAC NHAN - KEY: {html.escape(key)}]</b>",
                parse_mode=ParseMode.HTML, reply_markup=None)
        except Exception:
            pass
        lang = get_user_lang(order["user_id"])
        try:
            await safe_send(context.bot, order["user_id"],
                f"{t_html(order['user_id'], 'order_success')}\n\n"
                f"<b>{html.escape(t(order['user_id'], 'account_info'))}:</b>\n"
                f"{format_key_display(key, lang)}")
        except Exception as e:
            logger.error(f"notify: {e}")


# ============================================================
# ADMIN - PRODUCTS
# ============================================================
async def admin_add_product(update, context):
    if update.effective_user.id not in Config.ADMIN_IDS:
        await safe_reply(update.message, "Khong co quyen.")
        return
    try:
        clean, emoji_id = extract_custom_emoji_from_message(update.message)
        clean = re.sub(r'\s+\|', '|', clean).strip()
        body = clean[4:].strip() if clean.lower().startswith("/add") else clean
        if not body:
            await safe_reply(update.message, "Thieu tham so.")
            return
        if "|" in body:
            name_part, rest = body.split("|", 1)
            name = name_part.strip()
            has_desc = True
        else:
            tokens0 = body.split(maxsplit=1)
            if len(tokens0) < 2:
                await safe_reply(update.message, "Thieu gia hoac so luong.")
                return
            name = tokens0[0].strip()
            rest = tokens0[1]
            has_desc = False
        if not name:
            await safe_reply(update.message, "Thieu ten san pham.")
            return
        tokens = rest.strip().split()
        if not tokens:
            await safe_reply(update.message, "Thieu tham so.")
            return
        if "," in tokens[-1]:
            keys_str = tokens[-1]
            tokens = tokens[:-1]
        else:
            keys_str = "-"
        if len(tokens) < 2:
            await safe_reply(update.message, "Thieu gia hoac so luong.")
            return
        stock_str = tokens[-1]
        price_str = tokens[-2]
        description = " ".join(tokens[:-2]).strip() if has_desc else ""
        try:
            price = int(price_str.replace(".", "").replace(",", "").strip())
            if price <= 0:
                raise ValueError()
        except ValueError:
            await safe_reply(update.message, f"Gia loi: <code>{html.escape(price_str)}</code>")
            return
        keys = []
        if keys_str.strip() and keys_str.strip() != "-":
            keys = [k.strip() for k in keys_str.split(",") if k.strip()]
        if keys:
            stock = len(keys)
        else:
            try:
                stock = int(stock_str.strip())
                if stock < 0:
                    raise ValueError()
            except ValueError:
                await safe_reply(update.message, f"So luong loi: <code>{html.escape(stock_str)}</code>")
                return
        emoji_note = ""
        if emoji_id:
            ok = await validate_custom_emoji(context.bot, update.effective_user.id, emoji_id)
            if not ok:
                emoji_note = "\nLuu y: Emoji khong hien thi duoc, van luu."
        pid = add_product(name, description, price, stock, keys, emoji_id=emoji_id)
        desc_info = f"\nMo ta: {html.escape(description)}" if description else ""
        emoji_info = f"\nEmoji ID: <code>{emoji_id}</code>" if emoji_id else ""
        await safe_reply(update.message,
            f"Da them SP ID <code>{pid}</code>\n"
            f"Ten: {html.escape(name)}{desc_info}\n"
            f"Gia: {price:,} VND\n"
            f"Ton kho: {stock}\n"
            f"Keys: {len(keys)}{emoji_info}{emoji_note}\n\n"
            f"<i>Bat email flow: <code>/setflow {pid} email</code></i>")
    except Exception as e:
        logger.error(f"add: {e}", exc_info=True)
        await safe_reply(update.message, f"Loi: {html.escape(str(e))}")


async def admin_setflow(update, context):
    if update.effective_user.id not in Config.ADMIN_IDS:
        return
    parts = update.message.text.split()
    if len(parts) < 3:
        await safe_reply(update.message, "Cu phap: <code>/setflow &lt;id&gt; &lt;email|key&gt;</code>")
        return
    try:
        pid = int(parts[1])
    except ValueError:
        await safe_reply(update.message, "ID khong hop le.")
        return
    flow = parts[2].lower()
    if flow not in ("email", "key"):
        await safe_reply(update.message, "Flow phai la email hoac key.")
        return
    p = get_product(pid)
    if not p:
        await safe_reply(update.message, f"Khong tim thay SP <code>{pid}</code>.")
        return
    set_product_requires_email(pid, flow == "email")
    await safe_reply(update.message, f"SP <code>{pid}</code> ({html.escape(p['name'])}): flow = <b>{flow}</b>")


async def admin_import_products(update, context):
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
        await safe_reply(update.message, f"Loi doc: {html.escape(str(e))}")
        return
    ok, fail = [], []
    for ln, line in enumerate(content.splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            parts = [p.strip() for p in line.split("|")]
            if len(parts) < 3:
                fail.append((ln, "can >= 3 phan"))
                continue
            if len(parts) == 3:
                name, description, price_str, keys_str = parts[0], "", parts[1], parts[2]
            else:
                name, description, price_str = parts[0], parts[1], parts[2]
                keys_str = parts[3] if len(parts) >= 4 else ""
            price = int(price_str.replace(".", "").replace(",", "").strip())
            if price <= 0:
                raise ValueError()
            keys = [k.strip() for k in keys_str.split(",") if k.strip()] if keys_str and keys_str != "-" else []
            pid = add_product(name, description, price, len(keys), keys, emoji_id=None)
            ok.append((pid, name, price, len(keys)))
        except Exception as e:
            fail.append((ln, str(e)))
    rpt = f"<b>Import {html.escape(doc.file_name or '')}</b>\nOK: {len(ok)} | FAIL: {len(fail)}\n\n"
    for pid, name, price, stock in ok[:20]:
        rpt += f"<code>{pid}</code> {html.escape(name)} - {price:,}d - {stock}\n"
    if fail:
        rpt += "\n<b>Loi:</b>\n"
        for ln, err in fail[:10]:
            rpt += f"Dong {ln}: {html.escape(err)}\n"
    await safe_reply(update.message, rpt)


async def admin_add_key(update, context):
    if update.effective_user.id not in Config.ADMIN_IDS:
        return
    try:
        parts = update.message.text.split(maxsplit=2)
        if len(parts) < 3:
            await safe_reply(update.message, "Cu phap: <code>/addkey &lt;id&gt; &lt;k1,k2,...&gt;</code>")
            return
        pid = int(parts[1])
        new_keys = [k.strip() for k in parts[2].split(",") if k.strip()]
        if not new_keys:
            await safe_reply(update.message, "Can >= 1 key.")
            return
        p = get_product(pid)
        if not p:
            await safe_reply(update.message, f"Khong tim thay SP <code>{pid}</code>.")
            return
        get_db().products.update_one({"id": pid},
            {"$push": {"keys": {"$each": new_keys}}, "$inc": {"stock": len(new_keys)}})
        await safe_reply(update.message, f"Da them {len(new_keys)} key. Ton moi: {p['stock'] + len(new_keys)}")
    except Exception as e:
        await safe_reply(update.message, f"Loi: {html.escape(str(e))}")


async def admin_set_product_emoji(update, context):
    if update.effective_user.id not in Config.ADMIN_IDS:
        return
    try:
        clean, emoji_id = extract_custom_emoji_from_message(update.message)
        parts = clean.split()
        if len(parts) < 2:
            await safe_reply(update.message, "Cu phap: <code>/setemoji &lt;id&gt; [emoji]</code>")
            return
        pid = int(parts[1])
        if not emoji_id:
            await safe_reply(update.message, "Khong tim thay custom emoji.")
            return
        p = get_product(pid)
        if not p:
            await safe_reply(update.message, f"Khong tim thay SP <code>{pid}</code>.")
            return
        ok = await validate_custom_emoji(context.bot, update.effective_user.id, emoji_id)
        get_db().products.update_one({"id": pid}, {"$set": {"emoji_id": emoji_id}})
        await safe_reply(update.message,
                         f"Da dat emoji cho SP <code>{pid}</code>." if ok
                         else f"Da luu emoji cho SP <code>{pid}</code>.")
    except Exception as e:
        await safe_reply(update.message, f"Loi: {html.escape(str(e))}")


async def admin_setdesc(update, context):
    if update.effective_user.id not in Config.ADMIN_IDS:
        return
    try:
        parts = update.message.text.split(maxsplit=2)
        if len(parts) < 3:
            await safe_reply(update.message, "Cu phap: <code>/setdesc &lt;id&gt; &lt;mo ta&gt;</code>")
            return
        pid = int(parts[1])
        desc = parts[2].strip()
        p = get_product(pid)
        if not p:
            await safe_reply(update.message, f"Khong tim thay SP <code>{pid}</code>.")
            return
        get_db().products.update_one({"id": pid}, {"$set": {"description": desc}})
        await safe_reply(update.message, f"Da doi mo ta SP <code>{pid}</code>.")
    except Exception as e:
        await safe_reply(update.message, f"Loi: {html.escape(str(e))}")


async def admin_detail(update, context):
    if update.effective_user.id not in Config.ADMIN_IDS:
        return
    try:
        parts = update.message.text.split()
        if len(parts) < 2:
            await safe_reply(update.message, "Cu phap: <code>/detail &lt;id&gt;</code>")
            return
        pid = int(parts[1])
        p = get_product(pid)
        if not p:
            await safe_reply(update.message, f"Khong tim thay SP <code>{pid}</code>.")
            return
        name_html = product_name_html(p["name"], p.get("emoji_id"))
        desc = html.escape(p.get("description") or "(khong co mo ta)")
        keys = json.loads(p["keys"] or "[]")
        flow = "email" if p.get("requires_email") else "key"
        text = (f"<b>SP #{p['id']}</b>\nTen: {name_html}\nMo ta: {desc}\n"
                f"Gia: {p['price']:,} VND\nKho: {p['stock']}\nBan: {p['sold']}\n"
                f"Flow: <b>{flow}</b>\n"
                f"Emoji ID: <code>{p.get('emoji_id') or 'chua dat'}</code>\n\n"
                f"<b>Keys ({len(keys)}):</b>\n")
        for i, k in enumerate(keys[:10], 1):
            text += f"  {i}. {format_key_display(k)}\n"
        if len(keys) > 10:
            text += f"  <i>... va {len(keys) - 10} key khac</i>\n"
        await safe_reply(update.message, text)
    except Exception as e:
        await safe_reply(update.message, f"Loi: {html.escape(str(e))}")


async def admin_list_products(update, context):
    if update.effective_user.id not in Config.ADMIN_IDS:
        return
    total = count_all_products()
    if total == 0:
        await safe_reply(update.message, "Chua co SP.")
        return
    products = list_all_products(limit=20, offset=0)
    text = f"<b>SP ({len(products)}/{total}):</b>\n\n"
    for p in products:
        flow = "email" if p.get("requires_email") else "key"
        text += f"<code>{p['id']}</code> [{flow}] {html.escape(p['name'])[:28]} - {p['price']:,}d - kho:{p['stock']}\n"
    if total > 20:
        text += f"\n<i>... {total - 20} SP khac</i> <code>/list2</code>"
    await safe_reply(update.message, text)


async def admin_list2(update, context):
    if update.effective_user.id not in Config.ADMIN_IDS:
        return
    total = count_all_products()
    products = list_all_products(limit=20, offset=20)
    if not products:
        await safe_reply(update.message, "Het.")
        return
    text = f"<b>Trang 2/{(total - 1) // 20 + 1}:</b>\n\n"
    for p in products:
        flow = "email" if p.get("requires_email") else "key"
        text += f"<code>{p['id']}</code> [{flow}] {html.escape(p['name'])[:28]} - {p['price']:,}d\n"
    await safe_reply(update.message, text)


async def admin_delete_product(update, context):
    if update.effective_user.id not in Config.ADMIN_IDS:
        return
    try:
        parts = update.message.text.split()
        if len(parts) < 2:
            await safe_reply(update.message, "Cu phap: <code>/del &lt;id&gt;</code>")
            return
        pid = int(parts[1])
        p = get_product(pid)
        if not p:
            await safe_reply(update.message, f"Khong tim thay SP <code>{pid}</code>.")
            return
        if delete_product(pid):
            await safe_reply(update.message, f"Da xoa SP <code>{pid}</code> - {html.escape(p['name'])}")
    except Exception as e:
        await safe_reply(update.message, f"Loi: {html.escape(str(e))}")


async def admin_delete_all(update, context):
    if update.effective_user.id not in Config.ADMIN_IDS:
        return
    parts = update.message.text.split()
    if len(parts) < 2 or parts[1].lower() != "confirm":
        await safe_reply(update.message, "Xoa TAT CA. Xac nhan: <code>/delall confirm</code>")
        return
    c = delete_all_products()
    await safe_reply(update.message, f"Da xoa {c} SP.")


async def admin_confirm_order(update, context):
    if update.effective_user.id not in Config.ADMIN_IDS:
        return
    try:
        parts = update.message.text.split()
        if len(parts) < 2:
            await safe_reply(update.message, "Cu phap: <code>/confirm &lt;order_code&gt;</code>")
            return
        oc = int(parts[1])
        order = get_order(oc)
        if not order:
            await safe_reply(update.message, "Khong tim thay don.")
            return
        if order["status"] != "pending":
            await safe_reply(update.message, f"Don o trang thai <b>{order['status']}</b>.")
            return
        product = get_product(order["product_id"])
        email_flow = bool(product and product.get("requires_email"))
        if email_flow:
            update_order_status(oc, "paid", None)
            set_order_email_status(oc, "awaiting")
            decrement_stock(order["product_id"])
            await safe_reply(update.message, f"Da xac nhan don <code>{oc}</code> (email flow).")
            try:
                await safe_send(context.bot, order["user_id"],
                                t_html(order["user_id"], "youtube_email_paid_msg"))
            except Exception as e:
                logger.error(f"notify: {e}")
        else:
            key = get_available_key(order["product_id"])
            if not key:
                await safe_reply(update.message, "Het key. Nap truoc.")
                return
            update_order_status(oc, "paid", key)
            await safe_reply(update.message, f"Da xac nhan don <code>{oc}</code>.")
            lang = get_user_lang(order["user_id"])
            try:
                await safe_send(context.bot, order["user_id"],
                    f"{t_html(order['user_id'], 'order_success')}\n\n"
                    f"<b>{html.escape(t(order['user_id'], 'account_info'))}:</b>\n"
                    f"{format_key_display(key, lang)}")
            except Exception as e:
                logger.error(f"notify: {e}")
    except Exception as e:
        await safe_reply(update.message, f"Loi: {html.escape(str(e))}")


async def admin_broadcast(update, context):
    if update.effective_user.id not in Config.ADMIN_IDS:
        return
    parts = update.message.text.split(maxsplit=1)
    if len(parts) < 2:
        await safe_reply(update.message, "Cu phap: <code>/broadcast &lt;noi dung&gt;</code>")
        return
    msg = parts[1]
    users = get_all_user_ids()
    sent, fail = 0, 0
    for uid in users:
        try:
            await safe_send(context.bot, uid, msg, disable_web_page_preview=True)
            sent += 1
            await asyncio.sleep(0.05)
        except Exception as e:
            fail += 1
    await safe_reply(update.message, f"Broadcast xong. OK {sent} | FAIL {fail}")


# ============================================================
# ADMIN - USERS QUERIES
# ============================================================
async def admin_users(update, context):
    if update.effective_user.id not in Config.ADMIN_IDS:
        return
    parts = update.message.text.split()
    page = 1
    if len(parts) >= 2:
        try:
            page = max(1, int(parts[1]))
        except ValueError:
            page = 1
    per_page = 20
    offset = (page - 1) * per_page
    total = count_users()
    if total == 0:
        await safe_reply(update.message, "Chua co user nao.")
        return
    users = list_users_paginated(limit=per_page, offset=offset)
    if not users:
        await safe_reply(update.message, f"Trang {page} khong co du lieu.")
        return
    text = f"<b>{html.escape(t(0, 'admin_users_title'))}</b>\n"
    text += f"Trang {page}/{(total - 1) // per_page + 1} - Tong: <b>{total}</b>\n\n"
    for i, u in enumerate(users, 1):
        uid = u.get("user_id", "?")
        username = u.get("username")
        first = u.get("first_name") or ""
        last = u.get("last_name") or ""
        full_name = f"{first} {last}".strip() or "?"
        bal = int(u.get("balance", 0))
        lang = u.get("lang", "vi")
        line = f"{offset + i}. <code>{uid}</code>"
        if username:
            line += f" @{html.escape(username)}"
        line += f" - {html.escape(full_name)} - Vi: <b>{bal:,}d</b> [{lang}]"
        text += line + "\n"
    nav = []
    if page > 1:
        nav.append(f"<code>/users {page - 1}</code>")
    if offset + per_page < total:
        nav.append(f"<code>/users {page + 1}</code>")
    if nav:
        text += "\n<i>Dieu huong: " + " | ".join(nav) + "</i>"
    text += f"\n\nXem chi tiet: <code>/user &lt;id&gt;</code>"
    await safe_reply(update.message, text)


async def admin_user_detail_cmd(update, context):
    if update.effective_user.id not in Config.ADMIN_IDS:
        return
    parts = update.message.text.split()
    if len(parts) < 2:
        await safe_reply(update.message, "Cu phap: <code>/user &lt;user_id&gt;</code>")
        return
    try:
        target_id = int(parts[1])
    except ValueError:
        await safe_reply(update.message, "user_id khong hop le.")
        return
    u = get_user_detail(target_id)
    if not u:
        await safe_reply(update.message, f"Khong tim thay user <code>{target_id}</code>.")
        return
    username = u.get("username")
    first = u.get("first_name") or ""
    last = u.get("last_name") or ""
    full_name = f"{first} {last}".strip() or "?"
    bal = int(u.get("balance", 0))
    reg = u.get("registered_at")
    reg_str = reg.strftime("%Y-%m-%d %H:%M") if reg else "?"
    total_topup = get_total_topup_amount(target_id)
    topup_history = get_user_topup_orders(target_id, limit=10)
    text = (f"<b>{html.escape(t(0, 'admin_user_detail'))}</b>\n\n"
            f"- ID: <code>{target_id}</code>\n"
            f"- Username: {('@' + html.escape(username)) if username else '—'}\n"
            f"- Ten: {html.escape(full_name)}\n"
            f"- Ngon ngu: <b>{u.get('lang', 'vi')}</b>\n"
            f"- Ngay DK: {reg_str}\n"
            f"- So du vi: <b>{bal:,} VND</b>\n"
            f"- Tong da nap: <b>{total_topup:,} VND</b>\n\n")
    if topup_history:
        text += f"<b>{html.escape(t(0, 'admin_user_topup_history'))}:</b>\n"
        for h in topup_history:
            oc = h.get("order_code", "?")
            amt = h.get("amount", 0)
            status = h.get("status", "?")
            created = h.get("created_at")
            dt = created.strftime("%d/%m %H:%M") if created else "?"
            status_icon = "OK" if status == "paid" else "..."
            text += f"- [{status_icon}] <code>{oc}</code> - {amt:,}d - {dt}\n"
    else:
        text += "<i>Chua co don nap nao.</i>"
    await safe_reply(update.message, text)


async def admin_topups(update, context):
    if update.effective_user.id not in Config.ADMIN_IDS:
        return
    parts = update.message.text.split()
    page = 1
    if len(parts) >= 2:
        try:
            page = max(1, int(parts[1]))
        except ValueError:
            page = 1
    per_page = 20
    offset = (page - 1) * per_page
    total = count_users_with_topup()
    if total == 0:
        await safe_reply(update.message, "Chua co user nao nap vi.")
        return
    users = list_users_with_topup(limit=per_page, offset=offset)
    if not users:
        await safe_reply(update.message, f"Trang {page} khong co du lieu.")
        return
    text = f"<b>{html.escape(t(0, 'admin_topups_title'))}</b>\n"
    text += f"Trang {page}/{(total - 1) // per_page + 1} - Tong: <b>{total}</b>\n\n"
    total_balance = 0
    for i, u in enumerate(users, 1):
        uid = u.get("user_id", "?")
        username = u.get("username")
        first = u.get("first_name") or ""
        last = u.get("last_name") or ""
        full_name = f"{first} {last}".strip() or "?"
        bal = int(u.get("balance", 0))
        total_balance += bal
        line = f"{offset + i}. <code>{uid}</code>"
        if username:
            line += f" @{html.escape(username)}"
        line += f" - {html.escape(full_name)} - <b>{bal:,}d</b>"
        text += line + "\n"
    text += f"\n<b>Tong so du trang nay: {total_balance:,} VND</b>"
    nav = []
    if page > 1:
        nav.append(f"<code>/topups {page - 1}</code>")
    if offset + per_page < total:
        nav.append(f"<code>/topups {page + 1}</code>")
    if nav:
        text += "\n<i>Dieu huong: " + " | ".join(nav) + "</i>"
    await safe_reply(update.message, text)


# ============================================================
# ADMIN - SETUI
# ============================================================
def _resolve_setui_key(key):
    if key in UI_KEYS:
        return f"ui_{key}", "ui"
    if key in TEXT_EMOJI_KEYS:
        return f"text_{key}", "text"
    return None, None


async def admin_setui(update, context):
    if update.effective_user.id not in Config.ADMIN_IDS:
        return
    try:
        clean, emoji_id = extract_custom_emoji_from_message(update.message)
        parts = clean.split()
        if len(parts) < 2:
            txt = "<b>Cu phap:</b> <code>/setui &lt;key&gt; [emoji]</code>\n\n"
            txt += "<b>UI keys:</b>\n" + "\n".join(f"- <code>{k}</code> - {v}" for k, v in UI_KEYS.items())
            txt += "\n\n<b>Text keys:</b>\n" + "\n".join(f"- <code>{k}</code> - {v}" for k, v in TEXT_EMOJI_KEYS.items())
            await safe_reply(update.message, txt)
            return
        key = parts[1].lower()
        setting_key, kind = _resolve_setui_key(key)
        if not setting_key:
            await safe_reply(update.message, f"Key loi: <code>{html.escape(key)}</code>")
            return
        if not emoji_id:
            await safe_reply(update.message, "Khong tim thay custom emoji.")
            return
        ok = await validate_custom_emoji(context.bot, update.effective_user.id, emoji_id)
        set_setting(setting_key, emoji_id)
        label = "UI" if kind == "ui" else "text"
        await safe_reply(update.message,
            f"Da dat emoji {label} cho <code>{key}</code>." if ok
            else f"Da luu emoji {label} cho <code>{key}</code>.")
    except Exception as e:
        await safe_reply(update.message, f"Loi: {html.escape(str(e))}")


async def admin_setui_force(update, context):
    if update.effective_user.id not in Config.ADMIN_IDS:
        return
    try:
        clean, emoji_id = extract_custom_emoji_from_message(update.message)
        parts = clean.split()
        if len(parts) < 2 or not emoji_id:
            await safe_reply(update.message, "Cu phap: <code>/setui_force &lt;key&gt; [emoji]</code>")
            return
        key = parts[1].lower()
        setting_key, _ = _resolve_setui_key(key)
        if not setting_key:
            await safe_reply(update.message, "Key loi.")
            return
        set_setting(setting_key, emoji_id)
        await safe_reply(update.message, f"Ep luu emoji cho <code>{key}</code>.")
    except Exception as e:
        await safe_reply(update.message, f"Loi: {html.escape(str(e))}")


async def admin_viewui(update, context):
    if update.effective_user.id not in Config.ADMIN_IDS:
        return
    st = {s["key"]: s["emoji_id"] for s in get_all_settings()}
    text = "<b>UI Emoji:</b>\n"
    for k, desc in UI_KEYS.items():
        eid = st.get(f"ui_{k}")
        text += f"- <code>{k}</code> - {desc} - " + (f"<code>{eid}</code>\n" if eid else "<i>chua</i>\n")
    text += "\n<b>Text Emoji:</b>\n"
    for k, desc in TEXT_EMOJI_KEYS.items():
        eid = st.get(f"text_{k}")
        text += f"- <code>{k}</code> - {desc} - " + (f"<code>{eid}</code>\n" if eid else "<i>chua</i>\n")
    await safe_reply(update.message, text)


async def admin_delui(update, context):
    if update.effective_user.id not in Config.ADMIN_IDS:
        return
    parts = update.message.text.split()
    if len(parts) < 2:
        await safe_reply(update.message, "Cu phap: <code>/delui &lt;key&gt;</code>")
        return
    key = parts[1].lower()
    setting_key, _ = _resolve_setui_key(key)
    if not setting_key:
        await safe_reply(update.message, "Key loi.")
        return
    delete_setting(setting_key)
    await safe_reply(update.message, f"Da xoa emoji <code>{key}</code>.")


async def admin_testui(update, context):
    if update.effective_user.id not in Config.ADMIN_IDS:
        return
    parts = update.message.text.split()
    if len(parts) < 2:
        await safe_reply(update.message, "Cu phap: <code>/testui &lt;key&gt;</code>")
        return
    key = parts[1].lower()
    if key in TEXT_EMOJI_KEYS:
        eid = get_setting(f"text_{key}")
        rendered = t_html(update.effective_user.id, key)
        await safe_reply(update.message,
            f"<b>Key:</b> <code>{key}</code>\n"
            f"<b>Setting ID:</b> <code>{eid or 'chua dat'}</code>\n"
            f"<b>Render:</b>\n{rendered}")
    elif key in UI_KEYS:
        eid = get_setting(f"ui_{key}")
        kb = InlineKeyboardMarkup([[button("Test", callback_data="test_noop", ui_key=key)]])
        await safe_reply(update.message,
            f"<b>Key:</b> <code>{key}</code> (UI)\n"
            f"<b>Setting ID:</b> <code>{eid or 'chua dat'}</code>", reply_markup=kb)
    else:
        await safe_reply(update.message, f"Key khong hop le: <code>{key}</code>")


async def noop_callback(update, context):
    await update.callback_query.answer("OK", show_alert=False)


# ============================================================
# ADMIN - TEXTS
# ============================================================
TEXT_KEYS_INFO = {
    "shop_empty": "Thong bao shop trong", "shop_title": "Tieu de shop",
    "shop_prompt": "Dong 'Chon san pham...'", "pending_empty": "Khi user khong co don cho",
    "pending_hint": "Huong dan trong menu don cho", "order_hint": "Huong dan sau thanh toan",
    "order_success": "Thong bao thanh cong", "binance_note": "Luu y Binance",
    "lang_required": "Yeu cau chon ngon ngu", "youtube_email_ask": "Yeu cau gui email",
    "youtube_email_preview": "Xem truoc email", "youtube_email_pending": "Cho admin xac nhan email",
    "youtube_email_done": "Hoan tat email", "out_of_stock_wait": "Thong bao het hang cho admin",
    "admin_binance_req": "Tieu de yeu cau xac nhan Binance",
    "admin_binance_topup_req": "Tieu de yeu cau xac nhan nap Binance",
}


async def admin_settext(update, context):
    if update.effective_user.id not in Config.ADMIN_IDS:
        return
    try:
        parts = update.message.text.split(maxsplit=3)
        if len(parts) < 4:
            txt = "Cu phap: <code>/settext &lt;vi|en&gt; &lt;key&gt; &lt;value&gt;</code>\n\n"
            txt += "\n".join(f"- <code>{k}</code> - {v}" for k, v in TEXT_KEYS_INFO.items())
            await safe_reply(update.message, txt)
            return
        lang, key, value = parts[1].lower(), parts[2].lower(), parts[3]
        if lang not in ("vi", "en"):
            await safe_reply(update.message, "Lang phai la vi/en.")
            return
        set_text(f"{lang}_{key}", value)
        await safe_reply(update.message, f"Da doi <code>{lang}_{key}</code>.")
    except Exception as e:
        await safe_reply(update.message, f"Loi: {html.escape(str(e))}")


async def admin_viewtext(update, context):
    if update.effective_user.id not in Config.ADMIN_IDS:
        return
    ov = {t_["key"]: t_["value"] for t_ in get_all_texts()}
    text = "<b>Texts override:</b>\n\n"
    if not ov:
        text += "<i>Chua co override.</i>"
    else:
        for k, v in ov.items():
            text += f"- <code>{k}</code>\n  <i>{html.escape(v[:100])}</i>\n"
    await safe_reply(update.message, text)


async def admin_deltext(update, context):
    if update.effective_user.id not in Config.ADMIN_IDS:
        return
    parts = update.message.text.split()
    if len(parts) < 3:
        await safe_reply(update.message, "Cu phap: <code>/deltext &lt;lang&gt; &lt;key&gt;</code>")
        return
    lang, key = parts[1].lower(), parts[2].lower()
    delete_text(f"{lang}_{key}")
    await safe_reply(update.message, f"Da xoa override <code>{lang}_{key}</code>.")


# ============================================================
# ADMIN - BINANCE
# ============================================================
async def admin_setbinance(update, context):
    if update.effective_user.id not in Config.ADMIN_IDS:
        return
    parts = update.message.text.split()
    if len(parts) < 2:
        await safe_reply(update.message, "Cu phap: <code>/setbinance &lt;address&gt; [network]</code>")
        return
    addr = parts[1]
    net = parts[2].upper() if len(parts) >= 3 else "TRC20"
    if net not in ("TRC20", "BEP20", "ERC20", "POLYGON"):
        await safe_reply(update.message, "Network khong hop le.")
        return
    set_binance_address(addr)
    set_binance_network(net)
    await safe_reply(update.message, f"Vi: <code>{html.escape(addr)}</code>\nNetwork: <b>{net}</b>")


async def admin_setrate(update, context):
    if update.effective_user.id not in Config.ADMIN_IDS:
        return
    parts = update.message.text.split()
    if len(parts) < 2:
        r_live, src, _ = get_binance_rate_live()
        await safe_reply(update.message,
            f"Rate: <code>{r_live:,.0f}</code> ({html.escape(src)})\nDat: <code>/setrate 25000</code>")
        return
    try:
        r = int(parts[1].replace(".", "").replace(",", ""))
        if r <= 0:
            raise ValueError()
        set_usdt_rate(r)
        global _binance_rate_cache
        _binance_rate_cache = {"rate": None, "ts": 0, "source": "manual", "error": ""}
        await safe_reply(update.message, f"Rate: <code>{r:,}</code> VND/USDT")
    except ValueError:
        await safe_reply(update.message, "Rate loi.")


async def admin_viewbinance(update, context):
    if update.effective_user.id not in Config.ADMIN_IDS:
        return
    a = get_binance_address()
    n = get_binance_network()
    manual = get_usdt_rate()
    live, src, err = get_binance_rate_live()
    txt = (f"<b>Binance</b>\n"
           f"- Address: <code>{html.escape(a) if a else '(chua)'}</code>\n"
           f"- Network: <b>{n}</b>\n"
           f"- Auto: <b>{'ON' if Config.BINANCE_AUTO_RATE else 'OFF'}</b>\n"
           f"- Rate live: <code>{live:,.2f}</code> ({html.escape(src)})\n"
           f"- Rate manual: <code>{manual:,}</code>")
    if err:
        txt += f"\n\n<b>Loi API:</b>\n<code>{html.escape(err[:250])}</code>"
    await safe_reply(update.message, txt)


async def admin_refreshrate(update, context):
    if update.effective_user.id not in Config.ADMIN_IDS:
        return
    global _binance_rate_cache
    _binance_rate_cache = {"rate": None, "ts": 0, "source": "manual", "error": ""}
    rate, src, err = get_binance_rate_live()
    msg = f"Rate: <code>{rate:,.2f}</code> VND/USDT\nNguon: <b>{html.escape(src)}</b>"
    if err:
        msg += f"\n\n<b>Loi:</b>\n<code>{html.escape(err[:300])}</code>"
    await safe_reply(update.message, msg)


async def admin_stats(update, context):
    if update.effective_user.id not in Config.ADMIN_IDS:
        return
    live, src, _ = get_binance_rate_live()
    await safe_reply(update.message,
        f"<b>Stats</b>\nUsers: <code>{count_users()}</code>\n"
        f"Users co vi: <code>{count_users_with_topup()}</code>\n"
        f"SP: <code>{count_all_products()}</code>\nCon: <code>{count_products()}</code>\n"
        f"Rate: <code>{live:,.2f}</code> ({html.escape(src)})")


async def admin_help(update, context):
    if update.effective_user.id not in Config.ADMIN_IDS:
        return
    txt = ("<b>Admin commands:</b>\n\n"
           "<code>/add Ten Gia SL [Keys]</code>\n"
           "<code>/add Ten|Mo_ta Gia SL [Keys]</code>\n"
           "<code>/setflow &lt;id&gt; email|key</code>\n"
           "<code>/addkey &lt;id&gt; K1,K2</code>\n"
           "<code>/setdesc &lt;id&gt; Mo ta</code>\n"
           "<code>/setemoji &lt;id&gt; [emoji]</code>\n"
           "<code>/list</code> / <code>/list2</code> / <code>/detail &lt;id&gt;</code>\n"
           "<code>/del &lt;id&gt;</code> / <code>/delall confirm</code>\n\n"
           "<b>Users:</b>\n"
           "<code>/users [page]</code> - DS users\n"
           "<code>/user &lt;id&gt;</code> - Chi tiet user\n"
           "<code>/topups [page]</code> - Users da nap vi\n\n"
           "<b>Binance:</b>\n"
           "<code>/setbinance &lt;address&gt; [network]</code>\n"
           "<code>/setrate &lt;VND_per_USDT&gt;</code>\n"
           "<code>/viewbinance</code> / <code>/refreshrate</code>\n"
           "<code>/confirm &lt;order&gt;</code>\n\n"
           "<b>UI + Text Emoji:</b>\n"
           "<code>/setui &lt;key&gt; [emoji]</code>\n"
           "<code>/viewui</code> / <code>/delui &lt;key&gt;</code>\n"
           "<code>/testui &lt;key&gt;</code>\n\n"
           "<b>Texts:</b>\n"
           "<code>/settext &lt;vi|en&gt; &lt;key&gt; &lt;value&gt;</code>\n"
           "<code>/viewtext</code> / <code>/deltext</code>\n\n"
           "<b>Khac:</b>\n"
           "<code>/broadcast &lt;msg&gt;</code> / <code>/stats</code>")
    await safe_reply(update.message, txt)


# ============================================================
# HTTP
# ============================================================
async def root_handler(request):
    if request.method == "HEAD":
        return Response(status=200, content_type="text/plain")
    return Response(text="Bot is running", status=200)


async def health_check(request):
    if request.method == "HEAD":
        return Response(status=200, content_type="text/plain")
    return Response(text="OK", status=200)


async def telegram_webhook(request):
    if request.method == "HEAD":
        return Response(status=200, content_type="text/plain")
    if request.method == "GET":
        return Response(text="Telegram webhook OK", status=200)
    try:
        raw = await request.read()
        if not raw:
            return Response(status=400, text="Empty")
        data = json.loads(raw.decode("utf-8"))
        update = Update.de_json(data, request.app["bot_app"].bot)
        await request.app["bot_app"].process_update(update)
        return Response(text="OK", status=200)
    except Exception as e:
        logger.error(f"tg webhook: {e}", exc_info=True)
        return Response(status=500, text="Error")


async def payos_webhook(request):
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
        oc = data.get("orderCode")
        pc = data.get("code")
        desc = data.get("description", "")
        if oc == 123 or desc == "VQRIO123":
            return Response(text="OK", status=200)
        if not verify_payment_webhook(body, request.headers.get("x-payos-signature", "")):
            return Response(status=200, text="OK")
        if pc == "00" and oc:
            app = request.app["bot_app"]
            order = get_order(oc)
            if not order:
                return Response(text="OK", status=200)
            if order.get("type") == "topup":
                if mark_topup_paid(oc):
                    bal = get_user_balance(order["user_id"])
                    try:
                        await safe_send(app.bot, order["user_id"],
                            t_html(order["user_id"], "wallet_topup_success",
                                   amount=order["amount"], balance=bal))
                    except Exception as e:
                        logger.error(f"notify topup: {e}")
                return Response(text="OK", status=200)
            if order["status"] == "pending":
                product = get_product(order["product_id"])
                email_flow = bool(product and product.get("requires_email"))
                if email_flow:
                    update_order_status(oc, "paid", None)
                    set_order_email_status(oc, "awaiting")
                    decrement_stock(order["product_id"])
                    try:
                        await safe_send(app.bot, order["user_id"],
                            t_html(order["user_id"], "youtube_email_paid_msg"))
                    except Exception as e:
                        logger.error(f"notify: {e}")
                else:
                    key = get_available_key(order["product_id"])
                    if key:
                        update_order_status(oc, "paid", key)
                        lang = get_user_lang(order["user_id"])
                        try:
                            await safe_send(app.bot, order["user_id"],
                                f"{t_html(order['user_id'], 'order_success')}\n\n"
                                f"<b>{html.escape(t(order['user_id'], 'account_info'))}:</b>\n"
                                f"{format_key_display(key, lang)}")
                        except Exception as e:
                            logger.error(f"notify: {e}")
            elif order["status"] == "cancelled":
                logger.info(f"Late webhook for cancelled order {oc} - restoring")
                product = get_product(order["product_id"])
                email_flow = bool(product and product.get("requires_email"))
                if email_flow:
                    restore_cancelled_order(oc)
                    set_order_email_status(oc, "awaiting")
                    decrement_stock(order["product_id"])
                    try:
                        await safe_send(app.bot, order["user_id"],
                            t_html(order["user_id"], "youtube_email_paid_msg"))
                    except Exception as e:
                        logger.error(f"notify: {e}")
                else:
                    key = get_available_key(order["product_id"])
                    if key:
                        restore_cancelled_order(oc, key_assigned=key)
                        lang = get_user_lang(order["user_id"])
                        try:
                            await safe_send(app.bot, order["user_id"],
                                f"{t_html(order['user_id'], 'order_success')}\n\n"
                                f"<b>{html.escape(t(order['user_id'], 'account_info'))}:</b>\n"
                                f"{format_key_display(key, lang)}")
                        except Exception as e:
                            logger.error(f"notify: {e}")
        return Response(text="OK", status=200)
    except Exception as e:
        logger.error(f"payos webhook: {e}", exc_info=True)
        return Response(status=200, text="OK")


# ============================================================
# MAIN
# ============================================================
async def main():
    logger.info(f"ADMIN_IDS loaded: {Config.ADMIN_IDS}")
    logger.info(f"BINANCE_AUTO_RATE: {Config.BINANCE_AUTO_RATE}")
    app = Application.builder().token(Config.TELEGRAM_TOKEN).build()

    # User
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("lang", lang_cmd))

    # Admin products
    app.add_handler(CommandHandler("add", admin_add_product))
    app.add_handler(CommandHandler("addkey", admin_add_key))
    app.add_handler(CommandHandler("setemoji", admin_set_product_emoji))
    app.add_handler(CommandHandler("setdesc", admin_setdesc))
    app.add_handler(CommandHandler("setflow", admin_setflow))
    app.add_handler(CommandHandler("detail", admin_detail))
    app.add_handler(CommandHandler("list", admin_list_products))
    app.add_handler(CommandHandler("list2", admin_list2))
    app.add_handler(CommandHandler("del", admin_delete_product))
    app.add_handler(CommandHandler("delall", admin_delete_all))

    # Admin binance
    app.add_handler(CommandHandler("setbinance", admin_setbinance))
    app.add_handler(CommandHandler("setrate", admin_setrate))
    app.add_handler(CommandHandler("viewbinance", admin_viewbinance))
    app.add_handler(CommandHandler("refreshrate", admin_refreshrate))
    app.add_handler(CommandHandler("confirm", admin_confirm_order))

    # Admin misc
    app.add_handler(CommandHandler("broadcast", admin_broadcast))
    app.add_handler(CommandHandler("stats", admin_stats))

    # Admin users
    app.add_handler(CommandHandler("users", admin_users))
    app.add_handler(CommandHandler("user", admin_user_detail_cmd))
    app.add_handler(CommandHandler("topups", admin_topups))

    # Admin UI emoji
    app.add_handler(CommandHandler("setui", admin_setui))
    app.add_handler(CommandHandler("setui_force", admin_setui_force))
    app.add_handler(CommandHandler("viewui", admin_viewui))
    app.add_handler(CommandHandler("delui", admin_delui))
    app.add_handler(CommandHandler("testui", admin_testui))

    # Admin texts
    app.add_handler(CommandHandler("settext", admin_settext))
    app.add_handler(CommandHandler("viewtext", admin_viewtext))
    app.add_handler(CommandHandler("deltext", admin_deltext))

    app.add_handler(CommandHandler("help", admin_help))

    # Import .txt
    app.add_handler(MessageHandler(
        filters.Document.FileExtension("txt") & filters.User(Config.ADMIN_IDS),
        admin_import_products))

    # Text handler (topup + email)
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND & ~filters.User(Config.ADMIN_IDS),
        handle_user_text))

    # Callbacks
    app.add_handler(CallbackQueryHandler(noop_callback, pattern=r"^test_noop$"))
    app.add_handler(CallbackQueryHandler(confirm_send_email_callback, pattern=r"^cfmsend_\d+$"))
    app.add_handler(CallbackQueryHandler(cancel_send_email_callback, pattern=r"^cfmcancel_\d+$"))
    app.add_handler(CallbackQueryHandler(confirm_email_callback, pattern=r"^cfemail_\d+$"))
    app.add_handler(CallbackQueryHandler(confirm_binance_callback, pattern=r"^cfbinance_\d+$"))
    app.add_handler(CallbackQueryHandler(confirm_binance_topup_callback, pattern=r"^cfbinancetopup_\d+$"))
    app.add_handler(CallbackQueryHandler(setlang_callback, pattern=r"^setlang_"))
    app.add_handler(CallbackQueryHandler(refresh_products_callback, pattern=r"^refresh_\d+$"))
    app.add_handler(CallbackQueryHandler(menu_lang_callback, pattern=r"^menu_lang$"))
    app.add_handler(CallbackQueryHandler(delete_pending_order_callback, pattern=r"^del_order_\d+$"))
    app.add_handler(CallbackQueryHandler(recheck_cancelled_order_callback, pattern=r"^recheck_\d+$"))
    app.add_handler(CallbackQueryHandler(hide_order_callback, pattern=r"^hide_order_\d+$"))
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
    app.add_handler(CallbackQueryHandler(wallet_callback, pattern=r"^wallet$"))
    app.add_handler(CallbackQueryHandler(topup_callback, pattern=r"^topup$"))
    app.add_handler(CallbackQueryHandler(topup_payos_callback, pattern=r"^topup_payos_\d+$"))
    app.add_handler(CallbackQueryHandler(topup_binance_callback, pattern=r"^topup_binance_\d+$"))
    app.add_handler(CallbackQueryHandler(topup_binance_sent_callback, pattern=r"^topup_binance_sent_\d+$"))
    app.add_handler(CallbackQueryHandler(topup_check_callback, pattern=r"^topup_check_\d+$"))
    app.add_handler(CallbackQueryHandler(pay_wallet_callback, pattern=r"^pay_wallet_\d+$"))

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
