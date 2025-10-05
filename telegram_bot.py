import os
import sys
import logging
import asyncio
import threading
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

from iqclient import IQOptionAPI
from signal_parser import parse_signals, load_signals

# Load environment variables
load_dotenv()

EMAIL = os.getenv("IQ_EMAIL")
PASSWORD = os.getenv("IQ_PASSWORD")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID")

# Setup logging
sys.stdout.reconfigure(encoding="utf-8")
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# Global bot state
api = None
trading_paused = False


# --- IQOption Connection Management ---
def ensure_connection():
    """Ensure IQOptionAPI is connected, reconnect if necessary."""
    global api
    if api is None:
        api = IQOptionAPI()
    if not api.check_connect():
        try:
            api._connect()
            logger.info("🔁 Reconnected to IQ Option API.")
        except Exception as e:
            logger.error(f"❌ Reconnection failed: {e}")


# --- Command Handlers ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🤖 Bot is online and ready to trade!")


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_connection()
    balance = api.get_current_account_balance()
    acc_type = api.get_account_type()
    msg = f"📊 Account Type: {acc_type}\n💰 Balance: ${balance:.2f}\nTrading Paused: {trading_paused}"
    await update.message.reply_text(msg)


async def pause(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global trading_paused
    trading_paused = True
    await update.message.reply_text("⏸️ Trading paused.")


async def resume(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global trading_paused
    trading_paused = False
    await update.message.reply_text("▶️ Trading resumed.")


async def logs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send last few log lines."""
    try:
        with open("bot.log", "r", encoding="utf-8") as f:
            lines = f.readlines()[-20:]
        await update.message.reply_text("🧾 Recent Logs:\n" + "".join(lines))
    except FileNotFoundError:
        await update.message.reply_text("⚠️ No log file found.")


async def signals(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show signals loaded from file."""
    text = load_signals("signals.txt")
    if not text:
        await update.message.reply_text("⚠️ No signals available.")
        return
    parsed = parse_signals(text)
    msg = "\n".join([s["line"] for s in parsed[:10]])  # Show top 10 signals
    await update.message.reply_text(f"📡 Loaded Signals:\n{msg}")


async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle uploaded signals.txt file."""
    doc = update.message.document
    if not doc:
        return
    if not doc.file_name.endswith(".txt"):
        await update.message.reply_text("❌ Please upload a .txt file only.")
        return

    file = await doc.get_file()
    await file.download_to_drive("signals.txt")
    await update.message.reply_text("✅ Signals file updated successfully!")


# --- Startup Notification ---
async def notify_admin_startup():
    """Notify admin when bot starts."""
    ensure_connection()
    try:
        balance = api.get_current_account_balance()
        acc_type = api.get_account_type()
        msg = f"✅ Bot Online!\nAccount: {acc_type}\nBalance: ${balance:.2f}"
    except Exception:
        msg = "⚠️ Bot Online, but IQ Option connection failed."
    try:
        await app.bot.send_message(chat_id=ADMIN_CHAT_ID, text=msg)
    except Exception as e:
        logger.error(f"Failed to send startup message: {e}")


# --- Main ---
def main():
    global app

    ensure_connection()

    logger.info("🌐 Running in Render environment — using webhook mode.")
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    # Register command handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("pause", pause))
    app.add_handler(CommandHandler("resume", resume))
    app.add_handler(CommandHandler("logs", logs))
    app.add_handler(CommandHandler("signals", signals))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_file))

    # Run webhook (Render)
    app.run_webhook(
        listen="0.0.0.0",
        port=int(os.getenv("PORT", 8443)),
        url_path=TELEGRAM_TOKEN,
        webhook_url=f"https://{os.getenv('RENDER_EXTERNAL_HOSTNAME')}/{TELEGRAM_TOKEN}",
    )

    # Notify admin in background
    threading.Thread(target=lambda: asyncio.run(notify_admin_startup())).start()


if __name__ == "__main__":
    main()
