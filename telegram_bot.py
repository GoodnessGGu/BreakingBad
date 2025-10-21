# telegram_bot.py
import os
import asyncio
import logging
from telegram import Update
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    ContextTypes, filters
)
from iqclient import IQOptionAPI
from signal_parser import parse_signals_from_text, parse_signals_from_file

# --- Logging ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# --- Environment Variables ---
EMAIL = os.getenv("IQ_EMAIL")
PASSWORD = os.getenv("IQ_PASSWORD")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
ADMIN_ID = os.getenv("TELEGRAM_ADMIN_ID")

# --- Initialize IQ Option API ---
api = IQOptionAPI(email=EMAIL, password=PASSWORD)
api._connect()

# --- Ensure IQ Option connection ---
def ensure_connection():
    try:
        if not getattr(api, "_connected", False):
            logger.warning("🔌 IQ Option API disconnected — reconnecting...")
            api._connect()
            logger.info("🔁 Reconnected to IQ Option API.")
    except Exception as e:
        logger.error(f"Failed to reconnect IQ Option API: {e}")

# --- Command Handlers ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_chat.id) != str(ADMIN_ID):
        await update.message.reply_text("⛔ Unauthorized access.")
        return
    await update.message.reply_text("🤖 Bot is online and ready!")

async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_connection()
    try:
        bal = api.get_current_account_balance()
        acc_type = getattr(api, "account_mode", "unknown").capitalize()
        await update.message.reply_text(
            f"💼 *{acc_type}* Account\n💰 Balance: *${bal:.2f}*",
            parse_mode="Markdown"
        )
    except Exception as e:
        await update.message.reply_text(f"⚠️ Could not fetch balance: {e}")

async def refill(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_connection()
    try:
        api.refill_practice_balance()
        await update.message.reply_text("✅ Practice balance refilled!")
    except Exception as e:
        await update.message.reply_text(f"⚠️ Failed to refill balance: {e}")

async def signals(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text(
            "⚠️ Usage: /signals followed by text or attach a file with signals."
        )
        return

    text = " ".join(context.args)
    signals = parse_signals_from_text(text)
    if not signals:
        await update.message.reply_text("⚠️ Could not parse any valid signals.")
        return

    for sig in signals:
        logger.info(f"📊 Parsed signal: {sig}")
        asyncio.create_task(api.execute_signal(sig))

    await update.message.reply_text("📨 Signals received and executing...")

async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    document = update.message.document
    if not document:
        return

    file = await document.get_file()
    file_path = f"/tmp/{document.file_name}"
    await file.download_to_drive(file_path)

    signals = parse_signals_from_file(file_path)
    if not signals:
        await update.message.reply_text("⚠️ No valid signals found in file.")
        return

    for sig in signals:
        logger.info(f"📊 Parsed signal from file: {sig}")
        asyncio.create_task(api.execute_signal(sig))

    await update.message.reply_text("📂 File signals received and executing...")

# --- Admin Startup Notification ---
async def notify_admin_startup(app):
    """
    Sends startup message with balance and account info to admin.
    """
    try:
        if not ADMIN_ID:
            logger.warning("⚠️ TELEGRAM_ADMIN_ID not set. Skipping startup notification.")
            return

        # Reconnect if needed
        if not getattr(api, "_connected", False):
            api._connect()

        balance = api.get_current_account_balance()
        acc_type = getattr(api, "account_mode", "unknown").capitalize()

        message = (
            f"🤖 *Trading Bot Online*\n"
            f"📧 Account: `{EMAIL}`\n"
            f"💼 Account Type: *{acc_type}*\n"
            f"💰 Balance: *${balance:.2f}*\n\n"
            f"✅ Ready to receive signals!"
        )
        await app.bot.send_message(chat_id=int(ADMIN_ID), text=message, parse_mode="Markdown")
        logger.info("✅ Startup notification sent to admin.")
    except Exception as e:
        logger.error(f"❌ Failed to send startup notification: {e}")

# --- Main Entrypoint ---
def main():
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    # --- Command Handlers ---
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("balance", balance))
    app.add_handler(CommandHandler("refill", refill))
    app.add_handler(CommandHandler("signals", signals))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_file))

    logger.info("🌐 Starting bot in polling mode...")

    async def on_startup(app):
        try:
            # 🧹 Clean webhook (fixes 409 conflict)
            await app.bot.delete_webhook()
            logger.info("✅ Deleted any existing webhook before polling.")
        except Exception as e:
            logger.warning(f"⚠️ Could not delete webhook: {e}")
        await notify_admin_startup(app)

    # 🚀 Run polling safely
    async def run_bot():
        await on_startup(app)
        await app.run_polling(close_loop=False)

    asyncio.run(run_bot())

if __name__ == "__main__":
    main()
