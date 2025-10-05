import os
import logging
import asyncio
from datetime import datetime
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes

from iqclient import IQOptionAPI
from utils.signal_parser import parse_signals, load_signals
from main import run_trade, PAUSED

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(message)s",
    handlers=[
        logging.FileHandler("bot.log", encoding="utf-8"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Environment variables (Render)
EMAIL = os.getenv("IQ_EMAIL")
PASSWORD = os.getenv("IQ_PASSWORD")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")

if not EMAIL or not PASSWORD or not TELEGRAM_TOKEN:
    raise RuntimeError("❌ Missing environment variables (IQ_EMAIL, IQ_PASSWORD, TELEGRAM_TOKEN)")

# Connect to IQ Option
api = IQOptionAPI()
api._connect()

# State variables
paused = False
active_signals = []


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 IQ Option Bot is live!\n\n"
        "Commands:\n"
        "/balance - Show balance\n"
        "/signals <paste> - Load signals\n"
        "/pause - Pause trading\n"
        "/resume - Resume trading\n"
        "/status - Show trading status\n"
        "/logs - View last logs"
    )


async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    bal = api.get_current_account_balance()
    await update.message.reply_text(f"💰 Balance: ${bal:.2f}")


async def pause(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global paused
    paused = True
    await update.message.reply_text("⏸️ Trading paused.")


async def resume(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global paused
    paused = False
    await update.message.reply_text("▶️ Trading resumed.")


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    bal = api.get_current_account_balance()
    await update.message.reply_text(
        f"📊 STATUS\nPaused: {paused}\nActive signals: {len(active_signals)}\nBalance: ${bal:.2f}"
    )


async def logs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        with open("bot.log", "r", encoding="utf-8") as f:
            lines = f.readlines()[-50:]
        await update.message.reply_text("🧾 Recent Logs:\n" + "".join(lines[-50:]))
    except FileNotFoundError:
        await update.message.reply_text("No log file found yet.")


async def signals(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("⚠️ Format: `/signals HH:MM;EURUSD;CALL;5` or upload signals.txt file.")
        return

    signals_text = " ".join(context.args).replace(",", "\n")
    signals = parse_signals(signals_text)
    if not signals:
        await update.message.reply_text("❌ Invalid signals.")
        return

    active_signals.extend(signals)
    await update.message.reply_text(f"📡 Loaded {len(signals)} signals. Scheduling...")
    asyncio.create_task(run_signals(signals))


async def file_signals(update: Update, context: ContextTypes.DEFAULT_TYPE):
    file = await update.message.document.get_file()
    content = await file.download_as_bytearray()
    signals_text = content.decode("utf-8").strip()
    signals = parse_signals(signals_text)
    if not signals:
        await update.message.reply_text("❌ No valid signals in file.")
        return

    active_signals.extend(signals)
    await update.message.reply_text(f"📄 Loaded {len(signals)} signals from file.")
    asyncio.create_task(run_signals(signals))


async def run_signals(signals):
    grouped = {}
    for sig in signals:
        grouped.setdefault(sig["time"], []).append(sig)

    for sched_time in sorted(grouped.keys()):
        now = datetime.now()
        delay = (sched_time - now).total_seconds()
        if delay > 0:
            logger.info(f"⏳ Waiting {int(delay)}s until {sched_time.strftime('%H:%M')}")
            await asyncio.sleep(delay)

        logger.info(f"🚀 Executing {len(grouped[sched_time])} signal(s)")
        await asyncio.gather(*[
            asyncio.to_thread(run_trade, api, s["asset"], s["direction"], s["expiry"], 1)
            for s in grouped[sched_time]
        ])


def main():
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("balance", balance))
    app.add_handler(CommandHandler("pause", pause))
    app.add_handler(CommandHandler("resume", resume))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("logs", logs))
    app.add_handler(CommandHandler("signals", signals))
    app.add_handler(MessageHandler(filters.Document.ALL, file_signals))

    logger.info("🤖 Telegram bot running...")
    app.run_polling()


if __name__ == "__main__":
    main()
