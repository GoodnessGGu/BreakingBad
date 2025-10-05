# telegram_bot.py
import os
import logging
import asyncio
from datetime import datetime
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)
from iqclient import IQOptionAPI
from main import parse_signals, run_trade
import time

# ========== LOGGING SETUP ==========
logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("telegram_bot")

# ========== ENVIRONMENT VARIABLES ==========
EMAIL = os.getenv("IQ_EMAIL")
PASSWORD = os.getenv("IQ_PASSWORD")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")

# Print masked info for Render debugging
if EMAIL:
    masked = EMAIL[:2] + "****" + EMAIL[-2:]
    logger.info(f"✅ Loaded IQ_EMAIL: {masked}")
else:
    logger.error("❌ IQ_EMAIL not found in environment variables!")

# ========== IQ OPTION CONNECTION (with retry) ==========
api = IQOptionAPI()

def connect_with_retry(max_attempts=5, delay=5):
    for attempt in range(1, max_attempts + 1):
        try:
            logger.info(f"🔌 Connecting to IQ Option (attempt {attempt}/{max_attempts})...")
            api._connect()
            logger.info("✅ Connected to IQ Option successfully!")
            return True
        except Exception as e:
            logger.error(f"⚠️ Connection failed: {e}")
            if attempt < max_attempts:
                logger.info(f"⏳ Retrying in {delay} seconds...")
                time.sleep(delay)
            else:
                logger.critical("💀 Failed to connect after multiple attempts.")
                return False

connected = connect_with_retry()
if not connected:
    logger.error("❌ Bot startup aborted due to connection failure.")
    exit(1)

# ========== TELEGRAM COMMAND HANDLERS ==========
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🤖 Bot is online!\nUse /signals or upload a signals.txt file.")

async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        bal = api.get_current_account_balance()
        await update.message.reply_text(f"💰 Current Balance: ${bal:.2f}")
    except Exception as e:
        await update.message.reply_text(f"⚠️ Could not retrieve balance: {e}")

async def refill(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        success = api.refill_practice_account()
        if success:
            await update.message.reply_text("🔄 Practice account refilled successfully.")
        else:
            await update.message.reply_text("⚠️ Failed to refill demo account.")
    except Exception as e:
        await update.message.reply_text(f"⚠️ Error during refill: {e}")

async def signals(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Accept pasted signals via /signals command.
    """
    if not context.args:
        await update.message.reply_text(
            "⚠️ Paste signals in this format:\n`06:15;EURUSD;CALL;5`", parse_mode="Markdown"
        )
        return

    signals_text = " ".join(context.args).replace(",", "\n")
    signals = parse_signals(signals_text)

    if not signals:
        await update.message.reply_text("❌ No valid signals found.")
        return

    await update.message.reply_text(f"📊 {len(signals)} signals loaded. Scheduling trades...")
    asyncio.create_task(run_signals(signals))

async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Accept uploaded signals.txt files.
    """
    try:
        file = await update.message.document.get_file()
        content = await file.download_as_bytearray()
        signals_text = content.decode("utf-8").strip()
        signals = parse_signals(signals_text)

        if not signals:
            await update.message.reply_text("❌ No valid signals found in file.")
            return

        await update.message.reply_text(f"📂 {len(signals)} signals loaded from file. Scheduling trades...")
        asyncio.create_task(run_signals(signals))
    except Exception as e:
        await update.message.reply_text(f"⚠️ Error reading file: {e}")

# ========== SIGNAL EXECUTION ==========
async def run_signals(signals):
    grouped = {}
    for sig in signals:
        grouped.setdefault(sig["time"], []).append(sig)

    for sched_time in sorted(grouped.keys()):
        now = datetime.now()
        delay = (sched_time - now).total_seconds()
        if delay > 0:
            logger.info(f"⏳ Waiting {int(delay)}s for {sched_time.strftime('%H:%M')}")
            await asyncio.sleep(delay)

        logger.info(f"🚀 Executing {len(grouped[sched_time])} signal(s) at {sched_time.strftime('%H:%M')}")
        tasks = [
            asyncio.to_thread(run_trade, api, sig["asset"], sig["direction"], sig["expiry"], 1)
            for sig in grouped[sched_time]
        ]
        await asyncio.gather(*tasks)

# ========== MAIN BOT LOOP ==========
def main():
    if not TELEGRAM_TOKEN:
        logger.critical("❌ TELEGRAM_TOKEN not found! Check Render environment variables.")
        exit(1)

    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("balance", balance))
    app.add_handler(CommandHandler("refill", refill))
    app.add_handler(CommandHandler("signals", signals))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_file))

    logger.info("🤖 Telegram bot is now running and ready on Render!")
    app.run_polling(stop_signals=None)  # Keeps alive

if __name__ == "__main__":
    main()
