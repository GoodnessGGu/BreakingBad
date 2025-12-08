# telegram_bot.py
import os
import asyncio
import logging
import time
import tempfile
from datetime import datetime, date, timedelta
from collections import defaultdict
from telegram import Update
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    ContextTypes, filters
)
from telegram import ReplyKeyboardMarkup, KeyboardButton
from iqclient import IQOptionAPI, run_trade
from signal_parser import parse_signals_from_text, parse_signals_from_file
from settings import config
from keep_alive import keep_alive

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
ADMIN_ID = os.getenv("ADMIN_ID")

# --- Start Time (for uptime reporting) ---
START_TIME = time.time()

# --- Initialize IQ Option API (without connecting) ---
api = IQOptionAPI(email=EMAIL, password=PASSWORD)

# --- Ensure IQ Option connection ---
async def ensure_connection():
    """Ensures the API is connected before executing a command."""
    if getattr(api, "_connected", False):
        return

    logger.warning("🔌 IQ Option API disconnected — attempting to reconnect...")
    
    max_retries = 3
    for attempt in range(1, max_retries + 1):
        try:
            await api._connect()
            if getattr(api, "_connected", False):
                logger.info("🔁 Reconnected to IQ Option API.")
                return
        except Exception as e:
            logger.warning(f"⚠️ Connection attempt {attempt}/{max_retries} failed: {e}")
            if attempt < max_retries:
                await asyncio.sleep(2)  # Wait before retrying
    
    # If we get here, all retries failed
    raise ConnectionError("Failed to connect to IQ Option after multiple attempts. Check credentials.")

# --- Command Handlers ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_chat.id) != str(ADMIN_ID):
        await update.message.reply_text(f"⛔ Unauthorized access. Your ID is: `{update.effective_chat.id}`", parse_mode="Markdown")
        logger.warning(f"Unauthorized access attempt from ID: {update.effective_chat.id}")
        return
    
    keyboard = [
        [KeyboardButton("📊 Status"), KeyboardButton("💰 Balance")],
        [KeyboardButton("⏸ Pause"), KeyboardButton("▶ Resume")],
        [KeyboardButton("⚙️ Settings"), KeyboardButton("ℹ️ Help")]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    await update.message.reply_text("🤖 Bot is online and ready!", reply_markup=reply_markup)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "ℹ️ *Bot Commands*\n\n"
        "🖱 *Quick Actions:*\n"
        "Use the keyboard buttons for common tasks.\n\n"
        "🛠 *Configuration:*\n"
        "`/set_amount <n>` - Set trade amount\n"
        "`/set_account <type>` - REAL, DEMO, TOURNAMENT\n"
        "`/set_martingale <n>` - Max martingale steps\n"
        "`/suppress <on/off>` - Toggle signal suppression\n"
        "`/pause` / `/resume` - Control trading\n\n"
        "📡 *Signals:*\n"
        "`/signals <text>` - Parse text signals\n"
        "Or upload a text file with signals."
    )
    await update.message.reply_text(msg, parse_mode="Markdown")

async def settings_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        f"⚙️ *Current Settings*\n"
        f"💵 Amount: ${config.trade_amount}\n"
        f"🔄 Max Gales: {config.max_martingale_gales}\n"
        f"✖️ Martingale Multiplier: {config.martingale_multiplier}x\n"
        f"💼 Account: {config.account_type}\n"
        f"🚫 Suppression: {'ON' if config.suppress_overlapping_signals else 'OFF'}\n"
        f"⏸️ Paused: {'YES' if config.paused else 'NO'}\n\n"
        "To change these, use the /set commands (see ℹ️ Help)."
    )
    await update.message.reply_text(msg, parse_mode="Markdown")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    
    if text == "📊 Status":
        await status(update, context)
    elif text == "💰 Balance":
        await balance(update, context)
    elif text == "⏸ Pause":
        await pause_bot(update, context)
    elif text == "▶ Resume":
        await resume_bot(update, context)
    elif text == "⚙️ Settings":
        await settings_info(update, context)
    elif text == "ℹ️ Help":
        await help_command(update, context)
    else:
        # Ignore other text or treat as signal input if you prefer
        pass

async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        await ensure_connection()
        bal = api.get_current_account_balance()
        acc_type = getattr(api, "account_mode", "unknown").capitalize()
        await update.message.reply_text(
            f"💼 *{acc_type}* Account\n💰 Balance: *${bal:.2f}*",
            parse_mode="Markdown"
        )
    except Exception as e:
        await update.message.reply_text(f"⚠️ Could not fetch balance: {e}")

async def refill(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        await ensure_connection()
        api.refill_practice_balance()
        await update.message.reply_text("✅ Practice balance refilled!")
    except Exception as e:
        await update.message.reply_text(f"⚠️ Failed to refill balance: {e}")

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        await ensure_connection()
        bal = api.get_current_account_balance()
        acc_type = getattr(api, "account_mode", "unknown").capitalize()
        connected = getattr(api, "_connected", False)
        uptime_sec = int(time.time() - START_TIME)
        uptime_str = f"{uptime_sec//3600}h {(uptime_sec%3600)//60}m"

        # Fetch open positions
        open_trades = []
        try:
            positions = await api.get_open_positions()
            if positions:
                for p in positions:
                    direction = p.get('direction', 'N/A').upper()
                    asset = p.get('asset', 'N/A')
                    amount = p.get('amount', 0)
                    open_trades.append(f"{asset} ({direction}) @ ${amount}")
        except Exception as e:
            logger.warning(f"⚠️ Failed to get open positions: {e}")

        trades_info = "\n".join(open_trades) if open_trades else "No open trades."

        msg = (
            f"🔌 Connection: {'✅ Connected' if connected else '❌ Disconnected'}\n"
            f"💼 Account Type: *{acc_type}*\n"
            f"💰 Balance: *${bal:.2f}*\n"
            f"🕒 Uptime: {uptime_str}\n\n"
            f"⚙️ *Settings:*\n"
            f"💵 Amount: ${config.trade_amount} | 🔄 Gales: {config.max_martingale_gales}\n"
            f"⏸️ Paused: {config.paused} | 🚫 Suppress: {config.suppress_overlapping_signals}\n\n"
            f"📈 *Open Trades:*{trades_info}"
        )
        await update.message.reply_text(msg, parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"⚠️ Failed to fetch status: {e}")

async def process_and_schedule_signals(update: Update, parsed_signals: list):
    """Schedules and executes trades based on parsed signals."""
    if not parsed_signals:
        await update.message.reply_text("⚠️ No valid signals found to process.")
        return

    # Convert time strings to datetime objects
    for sig in parsed_signals:
        hh, mm = map(int, sig["time"].split(":"))
        now = datetime.now()
        sched_time = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if sched_time < now:
            sched_time += timedelta(days=1)
        sig["time"] = sched_time

    # Group signals by scheduled time
    grouped = defaultdict(list)
    for sig in parsed_signals:
        grouped[sig["time"]].append(sig)

    await update.message.reply_text(f"✅ Found {len(parsed_signals)} signals. Scheduling trades...")

    all_trade_tasks = []
    for sched_time in sorted(grouped.keys()):
        now = datetime.now()
        delay = (sched_time - now).total_seconds()

        if delay > 0:
            msg = f"⏳ Waiting {int(delay)}s until {sched_time.strftime('%H:%M')} for {len(grouped[sched_time])} signal(s)..."
            logger.info(msg)
            await update.message.reply_text(msg)
            await asyncio.sleep(delay)

        exec_msg = f"🚀 Executing {len(grouped[sched_time])} signal(s) at {sched_time.strftime('%H:%M')}"
        logger.info(exec_msg)
        await update.message.reply_text(exec_msg)

        async def notify(msg):
            try:
                await update.message.reply_text(msg)
            except Exception as e:
                logger.error(f"Failed to send notification: {e}")

        for s in grouped[sched_time]:
            task = asyncio.create_task(run_trade(api, s["pair"], s["direction"], s["expiry"], config.trade_amount, notification_callback=notify))
            all_trade_tasks.append(task)

    # Wait for all trades to complete and generate report
    if all_trade_tasks:
        results = await asyncio.gather(*all_trade_tasks)
        
        report_lines = ["📊 *Trade Session Report*"]
        total_profit = 0.0
        wins = 0
        losses = 0

        for res in results:
            if not res: continue # Handle potential None returns if any
            
            icon = "✅" if res['result'] == "WIN" else "❌" if res['result'] == "LOSS" else "⚠️"
            
            result_text = res['result']
            if res['result'] == "ERROR" and 'error_message' in res:
                result_text = f"ERROR: {res['error_message']}"
                
            line = f"{icon} {res['asset']} {res['direction']} | {result_text} (Gale {res['gales']})"
            report_lines.append(line)
            
            if res['result'] == "WIN":
                wins += 1
                total_profit += res['profit']
            elif res['result'] == "LOSS":
                losses += 1
                total_profit += res['profit'] # profit is negative or 0 on loss

        report_lines.append(f"\n🏆 Wins: {wins} | 💀 Losses: {losses}")
        report_lines.append(f"💰 Total Profit: ${total_profit:.2f}")
        
        await update.message.reply_text("\n".join(report_lines), parse_mode="Markdown")

async def signals(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text(
            "⚠️ Usage: /signals followed by text or attach a file with signals."
        )
        return

    text = " ".join(context.args)
    parsed_signals = parse_signals_from_text(text)
    
    # Schedule and process signals
    asyncio.create_task(process_and_schedule_signals(update, parsed_signals))

async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    document = update.message.document
    if not document:
        return

    file = await document.get_file()
    # Use a temporary file path that is safe
    file_path = os.path.join(tempfile.gettempdir(), document.file_name)
    await file.download_to_drive(file_path)

    parsed_signals = parse_signals_from_file(file_path)
    
    # Schedule and process signals
    asyncio.create_task(process_and_schedule_signals(update, parsed_signals))

# --- Settings Commands ---
async def set_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("⚠️ Usage: /set_amount <amount>")
        return
    try:
        amount = float(context.args[0])
        if amount < 1:
            await update.message.reply_text("⚠️ Amount must be at least 1.")
            return
        config.trade_amount = amount
        await update.message.reply_text(f"✅ Trade amount set to ${config.trade_amount}")
    except ValueError:
        await update.message.reply_text("⚠️ Invalid amount.")

async def set_account(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("⚠️ Usage: /set_account <real/demo>")
        return
    target_type = context.args[0].upper()
    valid_types = ['REAL', 'DEMO', 'TOURNAMENT']
    
    # Map common terms
    if target_type == 'PRACTICE': target_type = 'DEMO'

    if target_type not in valid_types:
        await update.message.reply_text(f"⚠️ Invalid account type. Use: {', '.join(valid_types)}")
        return

    try:
        await ensure_connection()
        api.switch_account(target_type)
        config.account_type = target_type # Update config to reflect change
        await update.message.reply_text(f"✅ Switched to {target_type} account.")
    except Exception as e:
        await update.message.reply_text(f"❌ Failed to switch account: {e}")

async def set_martingale(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("⚠️ Usage: /set_martingale <count>")
        return
    try:
        count = int(context.args[0])
        if count < 0:
            await update.message.reply_text("⚠️ Count must be non-negative.")
            return
        config.max_martingale_gales = count
        await update.message.reply_text(f"✅ Max martingale gales set to {config.max_martingale_gales}")
    except ValueError:
        await update.message.reply_text("⚠️ Invalid number.")

async def pause_bot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    config.paused = True
    await update.message.reply_text("⏸️ Bot PAUSED. No new trades will be taken.")

async def resume_bot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    config.paused = False
    await update.message.reply_text("▶️ Bot RESUMED. Trading enabled.")

async def toggle_suppression(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        status = "ON" if config.suppress_overlapping_signals else "OFF"
        await update.message.reply_text(f"ℹ️ Signal suppression is currently {status}.\nUsage: /suppress <on/off>")
        return
    
    mode = context.args[0].lower()
    if mode in ['on', 'true', '1', 'yes']:
        config.suppress_overlapping_signals = True
        await update.message.reply_text("✅ Signal suppression enabled.")
    elif mode in ['off', 'false', '0', 'no']:
        config.suppress_overlapping_signals = False
        await update.message.reply_text("✅ Signal suppression disabled.")
    else:
        await update.message.reply_text("⚠️ Invalid option. Use 'on' or 'off'.")

# --- Startup Notification ---
async def notify_admin_startup(app):
    """
    Notify admin on startup with account balance and info.
    """
    try:
        if not ADMIN_ID:
            logger.warning("⚠️ TELEGRAM_ADMIN_ID not set. Skipping startup notification.")
            return

        # Connection is now handled in post_init before this is called.
        bal = api.get_current_account_balance()
        acc_type = getattr(api, "account_mode", "unknown").capitalize()

        message = (
            f"🤖 *Trading Bot Online*\n"
            f"📧 Account: `{EMAIL}`\n"
            f"💼 Account Type: *{acc_type}*\n"
            f"💰 Balance: *${bal:.2f}*\n\n"
            f"✅ Ready to receive signals!"
        )
        await app.bot.send_message(chat_id=int(ADMIN_ID), text=message, parse_mode="Markdown")
        logger.info("✅ Startup notification sent to admin.")
    except Exception as e:
        logger.error(f"❌ Failed to send startup notification: {e}")

# --- Main Entrypoint ---
def main():
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    # Commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("balance", balance))
    app.add_handler(CommandHandler("refill", refill))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("signals", signals))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_file))
    
    # Text Handler for Keyboard
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Settings Commands
    app.add_handler(CommandHandler("set_amount", set_amount))
    app.add_handler(CommandHandler("set_account", set_account))
    app.add_handler(CommandHandler("set_martingale", set_martingale))
    app.add_handler(CommandHandler("pause", pause_bot))
    app.add_handler(CommandHandler("resume", resume_bot))
    app.add_handler(CommandHandler("suppress", toggle_suppression))

    logger.info("🌐 Initializing bot...")

    async def post_init(app):
        """Function to run after initialization and before polling starts."""
        try:
            # Initialize the bot and connect to IQ Option
            await app.bot.initialize()
            await app.bot.delete_webhook()
            logger.info("✅ Deleted old webhook before polling.")

            logger.info("📡 Connecting to IQ Option API...")
            await api._connect()
            logger.info("✅ Connected to IQ Option API.")

            # Notify admin that the bot is online
            await notify_admin_startup(app)

        except Exception as e:
            logger.error(f"❌ An error occurred during startup: {e}")

    app.post_init = post_init
    app.run_polling(close_loop=False)

if __name__ == "__main__":
    #keep_alive()
    main()
