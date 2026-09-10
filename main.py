# main.py
import asyncio
import json
import logging
import os
from datetime import datetime

from aiohttp import web
from aiohttp.web import Request, Response
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, ContextTypes
)
from telegram.constants import ParseMode

from config import Config
from database import (
    init_db, add_product, get_product, list_products,
    get_available_key, create_order, get_order, update_order_status,
    get_pending_orders_by_user
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
# UI HELPERS
# ============================================================
def product_buttons(products, page=0, per_page=5):
    keyboard = []
    for p in products:
        keyboard.append([
            InlineKeyboardButton(
                f"🛒 {p['name']} - {p['price']:,} VND (còn {p['stock']})",
                callback_data=f"buy_{p['id']}"
            )
        ])
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


# ============================================================
# BOT HANDLERS
# ============================================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    from database import get_db
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
        "🏪 *Cửa hàng tài khoản Pro*\n\nChọn sản phẩm bên dưới:",
        parse_mode=ParseMode.MARKDOWN,
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
        f"📋 *Danh sách sản phẩm (trang {page + 1}):*",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=product_buttons(products, page)
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
        await query.edit_message_text(
            f"❌ Lỗi tạo link thanh toán: {error}",
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
            f"✅ Đơn hàng #{order_code} đã thanh toán.\n🔑 Key: `{order['key_assigned']}`",
            parse_mode=ParseMode.MARKDOWN
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
                f"✅ Thanh toán thành công!\n🔑 Key: `{key}`",
                parse_mode=ParseMode.MARKDOWN
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
    text = "📦 *Đơn hàng chờ thanh toán:*\n\n"
    for o in orders[:10]:
        prod = get_product(o["product_id"])
        name = prod["name"] if prod else "Không xác định"
        text += f"#{o['id']} - {name} - {o['amount']:,} VND\n"
    text += "\nDùng nút 'Kiểm tra' ở từng đơn để cập nhật."
    await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN)


async def admin_add_product(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in Config.ADMIN_IDS:
        await update.message.reply_text("⛔ Bạn không có quyền.")
        return
    try:
        parts = update.message.text.split(maxsplit=4)
        if len(parts) < 5:
            await update.message.reply_text(
                "Cú pháp: /add <tên> <giá> <số_lượng> <key1,key2,...>"
            )
            return
        name = parts[1]
        price = int(parts[2])
        stock = int(parts[3])
        keys = [k.strip() for k in parts[4].split(",") if k.strip()]
        if not keys:
            await update.message.reply_text("Cần ít nhất 1 key.")
            return
        pid = add_product(name, "", price, stock, keys)
        await update.message.reply_text(
            f"✅ Đã thêm sản phẩm ID {pid} với {len(keys)} key."
        )
    except Exception as e:
        await update.message.reply_text(f"❌ Lỗi: {e}")


# ============================================================
# HTTP HANDLERS
# ============================================================
async def root_handler(request: Request):
    if request.method.upper() == "HEAD":
        return Response(status=200)
    return Response(text="Bot is running", status=200)


async def health_check(request: Request):
    if request.method.upper() == "HEAD":
        return Response(status=200)
    return Response(text="OK", status=200)


async def telegram_webhook(request: Request):
    """HEAD/GET verify, POST update."""
    method = request.method.upper()
    if method == "HEAD":
        return Response(status=200)
    if method == "GET":
        return Response(text="Telegram webhook OK", status=200)

    try:
        raw = await request.read()
        if not raw:
            logger.warning("Telegram POST body rỗng")
            return Response(status=400, text="Empty body")
        data = json.loads(raw.decode("utf-8"))
        update = Update.de_json(data, request.app["bot_app"].bot)
        await request.app["bot_app"].process_update(update)
        return Response(text="OK", status=200)
    except Exception as e:
        logger.error(f"Telegram webhook error: {e}", exc_info=True)
        return Response(status=500, text="Error")


async def payos_webhook(request: Request):
    """HEAD verify, GET test, POST xử lý webhook."""
    method = request.method.upper()
    if method == "HEAD":
        return Response(status=200)
    if method == "GET":
        return Response(text="PayOS webhook OK", status=200)

    try:
        raw = await request.read()
        if not raw:
            logger.warning("PayOS POST body rỗng")
            return Response(status=400, text="Empty body")

        try:
            body = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as e:
            logger.error(f"PayOS body không phải JSON: {e}")
            return Response(status=400, text="Invalid JSON")

        logger.info(f"PayOS webhook body: {json.dumps(body)[:500]}")

        sig_header = request.headers.get("x-payos-signature", "")
        if not verify_payment_webhook(body, sig_header):
            logger.warning("PayOS signature invalid")
            return Response(status=403, text="Invalid signature")

        data = body.get("data", {})
        order_code = data.get("orderCode")
        payos_code = data.get("code")

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
                            text=f"✅ Thanh toán thành công!\n🔑 Key của bạn: `{key}`",
                            parse_mode=ParseMode.MARKDOWN
                        )
                    except Exception as e:
                        logger.error(f"Gửi tin nhắn thất bại: {e}")
                else:
                    logger.warning(f"Hết key cho product {order['product_id']}")
        return Response(text="OK", status=200)
    except Exception as e:
        logger.error(f"PayOS webhook error: {e}", exc_info=True)
        return Response(status=500, text="Error")


# ============================================================
# MAIN
# ============================================================
async def main():
    app = Application.builder().token(Config.TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("add", admin_add_product))
    app.add_handler(CallbackQueryHandler(list_products_callback, pattern=r"^page_"))
    app.add_handler(CallbackQueryHandler(buy_product, pattern=r"^buy_"))
    app.add_handler(CallbackQueryHandler(check_order, pattern=r"^check_"))
    app.add_handler(CallbackQueryHandler(cancel_order, pattern=r"^cancel_"))
    app.add_handler(CallbackQueryHandler(my_orders, pattern=r"^my_orders$"))

    await app.initialize()
    await app.start()

    if Config.WEBHOOK_URL:
        webhook_url = f"{Config.WEBHOOK_URL}/telegram"
        await app.bot.set_webhook(webhook_url)
        logger.info(f"Telegram webhook set to: {webhook_url}")
    else:
        logger.warning("WEBHOOK_URL chưa cấu hình — Telegram sẽ không nhận update!")

    # --- aiohttp Web Server ---
    web_app = web.Application()
    web_app["bot_app"] = app

    web_app.router.add_get("/", root_handler)
    web_app.router.add_head("/", root_handler)

    web_app.router.add_get("/health", health_check)
    web_app.router.add_head("/health", health_check)

    web_app.router.add_get("/telegram", telegram_webhook)
    web_app.router.add_post("/telegram", telegram_webhook)
    web_app.router.add_head("/telegram", telegram_webhook)

    web_app.router.add_get("/payos", payos_webhook)
    web_app.router.add_post("/payos", payos_webhook)
    web_app.router.add_head("/payos", payos_webhook)

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
        logger.info("Bot shutdown complete")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Stopped by user")
