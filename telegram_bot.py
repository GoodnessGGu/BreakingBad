# telegram_bot.py
import logging
import asyncio
import os
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters
from iqclient import IQOptionAPI
from main import parse_signals, run_trade, process_signals

# === LOGIN (hardcoded for now; later we secure with env vars) ===
EMAIL = "your_email_here"
PASSWORD = "your_password_here"
TELEGRAM_TOKEN = "your_telegram_token_here"

# === INIT API ===
api = IQOptionAPI()
api._connect(email=EMAIL, password=PASSWORD)

# === LOGGING ===
logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)


# === COMMAND HANDLERS ===

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🚀 Trading Bot is online!\nUse /balance or /signals to get started.")


async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    bal = api.get_current_account_balance()
    await update.message.reply_text(f"💰 Current Balance: ${bal:.2f}")


async def refill(update: Update, context: ContextTypes.DEFAULT_TYPE):
    api.refill_demo_account(10000)
    await update.message.reply_text("🔄 Demo account refilled with $10,000.")


async def signals(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Accept signals pasted directly after the command.
    """
    if not update.message.text or "\n" not in update.message.text:
        await update.message.reply_text("⚠️ Paste signals in format HH:MM;ASSET;CALL|PUT;EXP after /signals")
        return

    raw_signals = update.message.text.replace("/signals", "").strip()
    signals = parse_signals(raw_signals)

    if not signals:
        await update.message.reply_text("❌ No valid signals found.")
        return

    await update.message.reply_text(f"📊 Loaded {len(signals)} signals. Scheduling trades...")

    # Process asynchronously in background
    asyncio.create_task(process_signals(api, raw_signals))
    await update.message.reply_text("✅ Signals running in background.")


async def upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Accept a signals.txt file upload.
    """
    if not update.message.document:
        await update.message.reply_text("⚠️ Please upload a signals.txt file.")
        return

    file = await update.message.document.get_file()
    file_path = "signals.txt"
    await file.download_to_drive(file_path)

    with open(file_path, "r", encoding="utf-8") as f:
        raw_signals = f.read()

    signals = parse_signals(raw_signals)
    if not signals:
        await update.message.reply_text("❌ No valid signals found in file.")
        return

    await update.message.reply_text(f"📊 Loaded {len(signals)} signals from file. Scheduling trades...")
    asyncio.create_task(process_signals(api, raw_signals))
    await update.message.reply_text("✅ Signals from file are running in background.")


# === MAIN APP ===

def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("balance", balance))
    app.add_handler(CommandHandler("refill", refill))
    app.add_handler(CommandHandler("signals", signals))
    app.add_handler(MessageHandler(filters.Document.ALL, upload))

    logger.info("🤖 Telegram Bot running...")
    app.run_polling()


if __name__ == "__main__":
    main()
