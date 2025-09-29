import os
import logging
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
from iqclient import IQOptionAPI
from main import parse_signals
import asyncio

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

# Load credentials from environment variables (Render dashboard)
EMAIL = os.getenv("IQ_EMAIL")
PASSWORD = os.getenv("IQ_PASSWORD")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")

# Ensure credentials exist
if not EMAIL or not PASSWORD or not TELEGRAM_TOKEN:
    raise RuntimeError("❌ Missing environment variables: IQ_EMAIL, IQ_PASSWORD, TELEGRAM_TOKEN")

# Initialize IQOption API
api = IQOptionAPI()
api._connect()

# Handlers
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🚀 Trading Bot is online!\nUse /balance or /signals to get started.")

async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    bal = api.get_current_account_balance()
    await update.message.reply_text(f"💰 Current Balance: ${bal:.2f}")

async def refill(update: Update, context: ContextTypes.DEFAULT_TYPE):
    success = api.refill_practice_account()
    if success:
        await update.message.reply_text("🔄 Demo account refilled successfully!")
    else:
        await update.message.reply_text("⚠️ Failed to refill demo account.")

async def signals(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Accept pasted signals in semicolon format via /signals command.
    """
    if not context.args:
        await update.message.reply_text("⚠️ Please paste signals in the format:\nHH:MM;ASSET;CALL|PUT;MINS")
        return

    signals_text = " ".join(context.args).replace(",", "\n")
    signals = parse_signals(signals_text)

    if not signals:
        await update.message.reply_text("❌ No valid signals found.")
        return

    await update.message.reply_text(f"📊 Loaded {len(signals)} signals. Scheduling trades...")
    asyncio.create_task(run_signals(signals))

async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Accept a signals.txt file upload.
    """
    file = await update.message.document.get_file()
    content = await file.download_as_bytearray()
    signals_text = content.decode("utf-8").strip()

    signals = parse_signals(signals_text)
    if not signals:
        await update.message.reply_text("❌ No valid signals found in file.")
        return

    await update.message.reply_text(f"📊 Loaded {len(signals)} signals from file. Scheduling trades...")
    asyncio.create_task(run_signals(signals))

async def run_signals(signals):
    """
    Run signals asynchronously in background.
    """
    from main import run_trade  # import here to avoid circular import

    grouped = {}
    for sig in signals:
        grouped.setdefault(sig["time"], []).append(sig)

    for sched_time in sorted(grouped.keys()):
        now = datetime.now()
        delay = (sched_time - now).total_seconds()
        if delay > 0:
            logger.info(f"⏳ Waiting {int(delay)}s until {sched_time.strftime('%H:%M')}")
            await asyncio.sleep(delay)

        logger.info(f"🚀 Executing {len(grouped[sched_time])} signal(s) at {sched_time.strftime('%H:%M')}")
        tasks = [
            asyncio.to_thread(run_trade, api, sig["asset"], sig["direction"], sig["expiry"], 1)
            for sig in grouped[sched_time]
        ]
        await asyncio.gather(*tasks)

# Bot setup
def main():
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("balance", balance))
    app.add_handler(CommandHandler("refill", refill))
    app.add_handler(CommandHandler("signals", signals))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_file))

    logger.info("🤖 Telegram bot is running...")
    app.run_polling()

if __name__ == "__main__":
    main()
