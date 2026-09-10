# main.py (toàn bộ file, thay thế hoàn toàn)
import asyncio
import logging
import json
import time
import requests
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes
from telegram.constants import ParseMode
from aiohttp import web
from aiohttp.web import Request, Response

from config import Config
from database import (
    init_db, add_product, get_product, list_products,
    get_available_key, create_order, get_order, update_order_status,
    get_pending_orders_by_user, update_stock
)
from payos_client import create_payment_link, verify_payment_webhook

# --- Logging ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# --- Khởi tạo DB ---
init_db()

# --- Hàm hỗ trợ ---
def product_buttons(products, page=0, per_page=5):
    keyboard = []
    for p in products:
        keyboard.append([InlineKeyboardButton(
            f"🛒 {p['name']} - {p['price']:,} VND (còn {p['stock']})",
            callback_data=f"buy_{p['id']}"
        )])
    # Nếu có đủ 5 sản phẩm và còn sản phẩm tiếp theo (kiểm tra bằng cách lấy thêm 1)
    if len(products) == per_page:
        keyboard.append([InlineKeyboardButton("⏭ Xem thêm", callback_data=f"page_{page+1}")])
    keyboard.append([InlineKeyboardButton("📦 Đơn hàng chờ", callback_data="my_orders")])
    return InlineKeyboardMarkup(keyboard)

def order_buttons(order_id):
    keyboard = [
        [InlineKeyboardButton("✅ Đã thanh toán? Kiểm tra", callback_data=f"check_{order_id}")],
        [InlineKeyboardButton("❌ Hủy đơn", callback_data=f"cancel_{order_id}")]
    ]
    return InlineKeyboardMarkup(keyboard)

# --- Lệnh /start ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    from database import get_db
    with get_db() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO users (user_id, username, first_name, last_name) VALUES (?, ?, ?, ?)",
            (user.id, user.username, user.first_name, user.last_name)
        )
    # Lấy 5 sản phẩm đầu tiên
    prods = list_products(limit=5, offset=0)
    await update.message.reply_text(
        "🏪 *Cửa hàng tài khoản Pro*\n\nChọn sản phẩm bên dưới:",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=product_buttons(prods, page=0)
    )

# --- Danh sách sản phẩm (callback) ---
async def list_products_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    page = 0
    if data.startswith("page_"):
        page = int(data.split("_")[1])
    products = list_products(limit=5, offset=page*5)
    if not products:
        await query.edit_message_text("Không còn sản phẩm nào.", reply_markup=None)
        return
    await query.edit_message_text(
        "📋 *Danh sách sản phẩm:*",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=product_buttons(products, page)
    )

# --- Mua hàng ---
async def buy_product(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    product_id = int(query.data.split("_")[1])
    product = get_product(product_id)
    if not product or product["stock"] <= 0:
        await query.edit_message_text("❌ Sản phẩm này đã hết hàng.", reply_markup=None)
        return

    # Tạo mã đơn hàng (số nguyên, duy nhất)
    order_code = int(f"{int(datetime.now().timestamp())}{product_id}{query.from_user.id % 1000}")

    # Lưu order vào DB
    create_order(order_code, query.from_user.id, product_id, 1, product["price"])

    # Tạo link thanh toán PayOS
    desc = f"TK {product['name'][:15]}"
    payment_url, error = create_payment_link(
        order_code=order_code,
        amount=product["price"],
        description=desc,
        buyer_name=query.from_user.full_name
    )

    if payment_url:
        context.bot_data[f"order_{order_code}"] = {
            "product_id": product_id,
            "user_id": query.from_user.id
        }
        msg = (
            f"🧾 *Đơn hàng #{order_code}*\n"
            f"Sản phẩm: {product['name']}\n"
            f"Số tiền: {product['price']:,} VND\n\n"
            f"🔗 [Nhấn vào đây để thanh toán]({payment_url})\n\n"
            f"Sau khi thanh toán, nhấn nút '✅ Đã thanh toán? Kiểm tra' bên dưới."
        )
        await query.edit_message_text(
            msg,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=order_buttons(order_code),
            disable_web_page_preview=True
        )
    else:
        await query.edit_message_text(f"❌ Lỗi tạo link thanh toán: {error}", reply_markup=None)

# --- Kiểm tra thanh toán ---
async def check_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    order_code = int(query.data.split("_")[1])
    order = get_order(order_code)
    if not order:
        await query.edit_message_text("⚠️ Không tìm thấy đơn hàng.", reply_markup=None)
        return

    if order["status"] == "paid":
        await query.edit_message_text(
            f"✅ Đơn hàng #{order_code} đã thanh toán.\n🔑 Key: `{order['key_assigned']}`",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    elif order["status"] == "cancelled":
        await query.edit_message_text(f"❌ Đơn hàng #{order_code} đã bị hủy.", reply_markup=None)
        return

    # Gọi API PayOS kiểm tra trạng thái
    headers = {
        "x-client-id": Config.PAYOS_CLIENT_ID,
        "x-api-key": Config.PAYOS_API_KEY
    }
    try:
        resp = requests.get(
            f"https://api-merchant.payos.vn/v2/payment-requests/{order_code}",
            headers=headers,
            timeout=10
        )
        data = resp.json()
        if data.get("code") == "00" and data["data"]["status"] == "PAID":
            key = get_available_key(order["product_id"])
            if key:
                update_order_status(order_code, "paid", key)
                await query.edit_message_text(
                    f"✅ Thanh toán thành công!\n🔑 Key: `{key}`",
                    parse_mode=ParseMode.MARKDOWN
                )
            else:
                await query.edit_message_text("❌ Hết key, vui lòng liên hệ admin.", reply_markup=None)
        else:
            await query.edit_message_text(
                f"⏳ Đơn hàng #{order_code} chưa được thanh toán.\nVui lòng thanh toán qua link hoặc hủy.",
                reply_markup=order_buttons(order_code)
            )
    except Exception as e:
        logger.error(f"Check order error: {e}")
        await query.edit_message_text("⚠️ Lỗi kiểm tra, thử lại sau.", reply_markup=None)

# --- Hủy đơn ---
async def cancel_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    order_code = int(query.data.split("_")[1])
    order = get_order(order_code)
    if not order:
        await query.edit_message_text("Không tìm thấy đơn hàng.")
        return
    if order["status"] != "pending":
        await query.edit_message_text(f"Đơn hàng đã ở trạng thái {order['status']}, không thể hủy.")
        return
    update_order_status(order_code, "cancelled")
    await query.edit_message_text(f"❌ Đã hủy đơn hàng #{order_code}.")

# --- Xem đơn hàng chờ ---
async def my_orders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    orders = get_pending_orders_by_user(user_id)
    if not orders:
        await query.edit_message_text("Bạn không có đơn hàng nào đang chờ.")
        return
    text = "📦 *Đơn hàng chờ thanh toán:*\n\n"
    for o in orders[:10]:
        prod = get_product(o["product_id"])
        name = prod["name"] if prod else "Không xác định"
        text += f"#{o['id']} - {name} - {o['amount']:,} VND\n"
    text += "\nDùng nút 'Kiểm tra' ở từng đơn để cập nhật."
    await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN)

# --- Admin: Thêm sản phẩm ---
async def admin_add_product(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in Config.ADMIN_IDS:
        await update.message.reply_text("⛔ Bạn không có quyền.")
        return
    try:
        parts = update.message.text.split(maxsplit=4)
        if len(parts) < 5:
            await update.message.reply_text("Sai cú pháp: /add <tên> <giá> <số lượng> <key1,key2,...>")
            return
        name = parts[1]
        price = int(parts[2])
        stock = int(parts[3])
        keys = parts[4].split(",")
        if not keys:
            await update.message.reply_text("Cần ít nhất 1 key.")
            return
        pid = add_product(name, "", price, stock, keys)
        await update.message.reply_text(f"✅ Đã thêm sản phẩm ID {pid} với {len(keys)} key.")
    except Exception as e:
        await update.message.reply_text(f"❌ Lỗi: {e}")

# --- Webhook server (aiohttp) ---
async def webhook_handler(request: Request):
    try:
        body = await request.json()
        signature = request.headers.get("x-payos-signature", "")
        if not verify_payment_webhook(body, signature):
            return Response(status=403, text="Invalid signature")

        order_code = body.get("orderCode")
        status = body.get("status")
        if status == "PAID" and order_code:
            order = get_order(order_code)
            if order and order["status"] == "pending":
                key = get_available_key(order["product_id"])
                if key:
                    update_order_status(order_code, "paid", key)
                    # Gửi thông báo cho user (lấy từ app trong request)
                    app = request.app["bot_app"]
                    try:
                        await app.bot.send_message(
                            chat_id=order["user_id"],
                            text=f"✅ Thanh toán thành công!\n🔑 Key của bạn: `{key}`",
                            parse_mode=ParseMode.MARKDOWN
                        )
                    except Exception as e:
                        logger.error(f"Gửi tin nhắn thất bại: {e}")
                else:
                    logger.warning(f"Hết key cho product {order['product_id']}")
        return Response(text="OK")
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        return Response(status=500, text="Error")

async def start_webhook_server(app_bot):
    """Khởi động webhook server trên cổng 8080"""
    web_app = web.Application()
    web_app["bot_app"] = app_bot
    web_app.router.add_post("/webhook", webhook_handler)
    runner = web.AppRunner(web_app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", 8080)
    await site.start()
    logger.info("Webhook server started on port 8080")
    return runner, site  # giữ tham chiếu để shutdown

# --- Hàm chính (FIXED) ---
async def main():
    # 1. Khởi tạo bot
    app = Application.builder().token(Config.TELEGRAM_TOKEN).build()

    # 2. Đăng ký handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("add", admin_add_product))
    app.add_handler(CallbackQueryHandler(list_products_callback, pattern="^page_"))
    app.add_handler(CallbackQueryHandler(buy_product, pattern="^buy_"))
    app.add_handler(CallbackQueryHandler(check_order, pattern="^check_"))
    app.add_handler(CallbackQueryHandler(cancel_order, pattern="^cancel_"))
    app.add_handler(CallbackQueryHandler(my_orders, pattern="^my_orders$"))

    # 3. Khởi chạy webhook server (bất đồng bộ)
    web_runner, web_site = await start_webhook_server(app)

    # 4. Chạy bot polling trong thread riêng (blocking, nhưng không làm treo event loop)
    loop = asyncio.get_running_loop()

    def run_polling():
        try:
            app.run_polling()
        except Exception as e:
            logger.error(f"Polling stopped: {e}")

    # Chạy polling trong executor (thread pool)
    await loop.run_in_executor(None, run_polling)

    # 5. Khi polling kết thúc (thường là khi bị dừng), dọn dẹp webhook
    await web_runner.cleanup()
    logger.info("Bot shutdown complete")

if __name__ == "__main__":
    asyncio.run(main())
