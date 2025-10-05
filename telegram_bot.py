import os
import sys
import logging
import asyncio
from datetime import datetime
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
)
from iqclient import IQOptionAPI
from main import run_trade
from signal_parser import parse_signals, load_signals

# Console supports emojis
sys.stdout.reconfigure(encoding="utf-8")

# Logging setup
logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

# --- Environment Variables ---
EMAIL = os.getenv("IQ_EMAIL")
PASSWORD = os.getenv("IQ_PASSWORD")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
PORT = int(os.environ.get("PORT", 8443))
RENDER_URL = os.environ.get("RENDER_EXTERNAL_HOSTNAME")

# Check for credentials
if not all([EMAIL, PASSWORD, TELEGRAM_TOKEN]):
    raise RuntimeError("❌ Missing environment variables: IQ_EMAIL, IQ_PASSWORD, TELEGRAM_TOKEN")

# Initialize IQ Option API
api = IQOptionAPI(EMAIL, PASSWORD)
api._connect()

# --- Telegram Bot Handlers ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🚀 Trading Bot is online!\n\nCommands:\n"
        "/signals <paste signals>\n"
        "/balance — check account balance\n"
        "/logs — get latest trading log\n"
        "/status — check current trading status"
    )

async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    bal = api.get_current_account_balance()
    await update.message.reply_text(f"💰 Current Balance: ${bal:.2f}")

async def logs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send latest logs."""
    try:
        with open("bot.log", "r", encoding="utf-8") as f:
            lines = f.readlines()[-40:]  # Last 40 log lines
        await update.message.reply_text("📜 Latest logs:\n" + "".join(lines))
    except FileNotFoundError:
        await update.message.reply_text("⚠️ No logs found yet.")

async def signals(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Accept pasted signals directly via /signals command."""
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
    """Handle uploaded signals.txt file."""
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
    """Run parsed signals asynchronously."""
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


# --- BOT STARTUP ---
async def on_startup_notify(app):
    """Send a startup message to the admin (first user who used /start)."""
    admin_id = os.getenv("ADMIN_CHAT_ID")
    if admin_id:
        try:
            bal = api.get_current_account_balance()
            acc_type = api.account_manager.current_account_type.title()
            await app.bot.send_message(
                chat_id=admin_id,
                text=f"✅ *Bot Online!*\n\nAccount: *{acc_type}*\nBalance: *${bal:.2f}*",
                parse_mode="Markdown"
            )
            logger.info(f"📨 Startup message sent to admin ({admin_id}).")
        except Exception as e:
            logger.error(f"⚠️ Failed to send startup message: {e}")
    else:
        logger.warning("⚠️ ADMIN_CHAT_ID not set — no startup notification sent.")


def main():
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    # Register commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("balance", balance))
    app.add_handler(CommandHandler("logs", logs))
    app.add_handler(CommandHandler("signals", signals))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_file))

    # Send startup message asynchronously after bot launch
    async def post_startup(app):
        await on_startup_notify(app)

    # --- Automatic mode detection ---
    if RENDER_URL:
        logger.info("🌐 Running in Render environment — using webhook mode.")
        app.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            url_path=TELEGRAM_TOKEN,
            webhook_url=f"https://{RENDER_URL}/{TELEGRAM_TOKEN}",
            post_init=post_startup
        )
    else:
        logger.info("💻 Running locally — using polling mode.")
        app.run_polling(post_init=post_startup)


if __name__ == "__main__":
    main()
