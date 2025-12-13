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
from channel_monitor import ChannelMonitor
from trade_database import db
from chart_generator import generate_pnl_chart, generate_winrate_chart, generate_asset_performance_chart, generate_summary_dashboard
from trade_exporter import export_to_csv, export_to_excel
from health_monitor import HealthMonitor
from timezone_utils import get_timezone_name, now as tz_now

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

# Telegram API credentials for channel monitoring
TELEGRAM_API_ID = os.getenv("TELEGRAM_API_ID")
TELEGRAM_API_HASH = os.getenv("TELEGRAM_API_HASH")
SIGNAL_CHANNEL_ID = os.getenv("SIGNAL_CHANNEL_ID")

# --- Start Time (for uptime reporting) ---
START_TIME = time.time()

# --- Initialize IQ Option API (without connecting) ---
api = IQOptionAPI(email=EMAIL, password=PASSWORD)

# --- Initialize Channel Monitor (global instance) ---
channel_monitor = None

# --- Initialize Health Monitor (global instance) ---
health_monitor_instance = None

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
        [KeyboardButton("📡 Channels"), KeyboardButton("⚙️ Settings")],
        [KeyboardButton("📈 Charts"), KeyboardButton("📋 History")],
        [KeyboardButton("📊 Stats"), KeyboardButton("ℹ️ Help")]
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
        "Or upload a text file with signals.\n\n"
        "📢 *Channel Monitoring:*\n"
        "`/channels` - Toggle channel signal monitoring\n\n"
        "📊 *Analytics:*\n"
        "`/stats [days]` - View trading statistics\n"
        "`/history [days]` - View trade history\n"
        "`/charts [days]` - Generate performance charts\n"
        "`/export [days]` - Export trades to Excel\n\n"
        "🔧 *System:*\n"
        "`/health` - Check bot health status\n"
        "`/timezone [tz]` - View/set timezone\n"
        "`/shutdown` - Stop the bot remotely"
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
    elif text == "📡 Channels":
        await channels_command(update, context)
    elif text == "⚙️ Settings":
        await settings_info(update, context)
    elif text == "📈 Charts":
        await charts_command(update, context)
    elif text == "📋 History":
        await history_command(update, context)
    elif text == "📊 Stats":
        await stats_command(update, context)
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
            f"🕒 Uptime: {uptime_str}\n"
            f"🌍 Timezone: {get_timezone_name()}\n\n"
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

async def channels_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Toggle channel monitoring on/off."""
    global channel_monitor
    
    # Check if credentials are configured
    if not TELEGRAM_API_ID or not TELEGRAM_API_HASH:
        await update.message.reply_text(
            "❌ *Channel Monitoring Not Configured*\n\n"
            "Please add the following to your .env file:\n"
            "`TELEGRAM_API_ID`\n"
            "`TELEGRAM_API_HASH`\n"
            "`SIGNAL_CHANNEL_ID`\n\n"
            "Get API credentials from: https://my.telegram.org/auth",
            parse_mode="Markdown"
        )
        return
    
    if not SIGNAL_CHANNEL_ID:
        await update.message.reply_text(
            "❌ *No Channel Configured*\n\n"
            "Please add `SIGNAL_CHANNEL_ID` to your .env file.",
            parse_mode="Markdown"
        )
        return
    
    # Create notification callback
    async def notify(msg):
        try:
            await update.message.reply_text(msg, parse_mode="Markdown")
        except Exception as e:
            logger.error(f"Failed to send notification: {e}")
    
    # Toggle monitoring
    if channel_monitor is None or not channel_monitor.is_monitoring():
        # Start monitoring
        try:
            await ensure_connection()
            
            channel_monitor = ChannelMonitor(
                api_id=TELEGRAM_API_ID,
                api_hash=TELEGRAM_API_HASH,
                channel_id=SIGNAL_CHANNEL_ID,
                iq_api=api,
                notification_callback=notify
            )
            
            await update.message.reply_text(
                "📡 *Starting Channel Monitoring...*\n\n"
                f"Channel ID: `{SIGNAL_CHANNEL_ID}`\n"
                "Please wait...",
                parse_mode="Markdown"
            )
            
            # Start monitoring in background
            asyncio.create_task(channel_monitor.start_monitoring())
            
        except Exception as e:
            logger.error(f"Failed to start channel monitoring: {e}")
            await update.message.reply_text(f"❌ Failed to start monitoring: {e}")
    else:
        # Stop monitoring
        try:
            await channel_monitor.stop_monitoring()
            channel_monitor = None
            await update.message.reply_text(
                "📡 *Channel Monitoring Stopped*",
                parse_mode="Markdown"
            )
        except Exception as e:
            logger.error(f"Failed to stop channel monitoring: {e}")
            await update.message.reply_text(f"❌ Failed to stop monitoring: {e}")

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Display trading statistics."""
    try:
        days = 7
        if context.args and context.args[0].isdigit():
            days = int(context.args[0])
        
        stats = db.get_statistics(days=days)
        best_pairs = db.get_best_pairs(days=days, limit=3)
        
        if stats['total_trades'] == 0:
            await update.message.reply_text(f"📊 No trades found in the last {days} days.")
            return
        
        msg = f"📊 *Trading Statistics* ({days} days)\n\n"
        msg += f"📈 Total Trades: {stats['total_trades']}\n"
        msg += f"✅ Wins: {stats['wins']} | ❌ Losses: {stats['losses']}\n"
        msg += f"🎯 Win Rate: {stats['win_rate']:.1f}%\n"
        msg += f"💰 Total Profit: ${stats['total_profit']:.2f}\n"
        msg += f"📊 Avg Profit/Trade: ${stats['avg_profit']:.2f}\n\n"
        
        if best_pairs:
            msg += "*🏆 Top Performing Assets:*\n"
            for pair in best_pairs:
                msg += f"• {pair['asset']}: ${pair['total_profit']:.2f} ({pair['win_rate']:.0f}% WR)\n"
        
        await update.message.reply_text(msg, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Failed to get statistics: {e}")
        await update.message.reply_text(f"❌ Error getting statistics: {e}")

async def history_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Display trade history."""
    try:
        days = 7
        if context.args and context.args[0].isdigit():
            days = int(context.args[0])
        
        trades = db.get_trades(days=days)
        
        if not trades:
            await update.message.reply_text(f"📋 No trades found in the last {days} days.")
            return
        
        msg = f"📋 *Trade History* (Last {min(10, len(trades))} of {len(trades)} trades)\n\n"
        
        for trade in trades[:10]:
            icon = "✅" if trade['result'] == 'WIN' else "❌" if trade['result'] == 'LOSS' else "⚠️"
            timestamp = trade['timestamp'][:16].replace('T', ' ')
            msg += f"{icon} {timestamp}\n"
            msg += f"   {trade['asset']} {trade['direction']} | ${trade['profit']:.2f} (G{trade['gale_level']})\n\n"
        
        if len(trades) > 10:
            msg += f"_...and {len(trades) - 10} more trades_\n\n"
        
        msg += f"Use `/export {days}` to download full history"
        
        await update.message.reply_text(msg, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Failed to get history: {e}")
        await update.message.reply_text(f"❌ Error getting history: {e}")

async def charts_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Generate and send performance charts."""
    try:
        days = 7
        if context.args and context.args[0].isdigit():
            days = int(context.args[0])
        
        await update.message.reply_text(f"📈 Generating charts for last {days} days...")
        
        trades = db.get_trades(days=days)
        stats = db.get_statistics(days=days)
        best_pairs = db.get_best_pairs(days=days, limit=5)
        
        if not trades:
            await update.message.reply_text(f"❌ No trades found in the last {days} days.")
            return
        
        chart_path = generate_summary_dashboard(trades, stats, best_pairs)
        
        if chart_path and os.path.exists(chart_path):
            with open(chart_path, 'rb') as photo:
                await update.message.reply_photo(
                    photo=photo,
                    caption=f"📊 Performance Dashboard ({days} days)"
                )
            os.remove(chart_path)
        else:
            await update.message.reply_text("❌ Failed to generate chart")
    except Exception as e:
        logger.error(f"Failed to generate charts: {e}")
        await update.message.reply_text(f"❌ Error generating charts: {e}")

async def export_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Export trade history to Excel."""
    try:
        days = 30
        if context.args and context.args[0].isdigit():
            days = int(context.args[0])
        
        await update.message.reply_text(f"📊 Exporting trades from last {days} days...")
        
        trades = db.get_trades(days=days)
        stats = db.get_statistics(days=days)
        best_pairs = db.get_best_pairs(days=days, limit=10)
        
        if not trades:
            await update.message.reply_text(f"❌ No trades found in the last {days} days.")
            return
        
        filepath = export_to_excel(trades, stats, best_pairs)
        
        if filepath and os.path.exists(filepath):
            with open(filepath, 'rb') as doc:
                await update.message.reply_document(
                    document=doc,
                    filename=os.path.basename(filepath),
                    caption=f"📊 Trade Export ({len(trades)} trades, {days} days)"
                )
            os.remove(filepath)
        else:
            await update.message.reply_text("❌ Failed to export trades")
    except Exception as e:
        logger.error(f"Failed to export trades: {e}")
        await update.message.reply_text(f"❌ Error exporting trades: {e}")

async def health_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Check bot health status."""
    global health_monitor_instance
    
    try:
        if not health_monitor_instance:
            await update.message.reply_text("⚠️ Health monitoring not initialized")
            return
        
        health_status = await health_monitor_instance.check_health()
        
        msg = "🏥 *Health Check Report*\n\n"
        
        for check_name, check_data in health_status['checks'].items():
            icon = "✅" if check_data['healthy'] else "❌"
            name = check_name.replace('_', ' ').title()
            msg += f"{icon} {name}: {check_data['message']}\n"
        
        overall_icon = "✅" if health_status['overall_healthy'] else "❌"
        msg += f"\n{overall_icon} *Overall Status:* {'Healthy' if health_status['overall_healthy'] else 'Unhealthy'}"
        
        await update.message.reply_text(msg, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Failed to check health: {e}")
        await update.message.reply_text(f"❌ Error checking health: {e}")

async def shutdown_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Shutdown the bot remotely."""
    # Verify admin
    if str(update.effective_chat.id) != str(ADMIN_ID):
        await update.message.reply_text("⛔ Unauthorized. Only admin can shutdown the bot.")
        logger.warning(f"Unauthorized shutdown attempt from ID: {update.effective_chat.id}")
        return
    
    try:
        await update.message.reply_text(
            "🛑 *Shutting down bot...*\n\n"
            "The bot will stop in 3 seconds.\n"
            "To restart, run the bot script again.",
            parse_mode="Markdown"
        )
        
        logger.info("🛑 Shutdown command received from admin")
        
        # Give time for message to send
        await asyncio.sleep(3)
        
        # Stop health monitor if running
        global health_monitor_instance
        if health_monitor_instance:
            health_monitor_instance.stop()
        
        # Stop channel monitor if running
        global channel_monitor
        if channel_monitor and channel_monitor.is_monitoring():
            await channel_monitor.stop_monitoring()
        
        # Stop the application
        logger.info("👋 Bot shutting down gracefully")
        import os
        os._exit(0)
        
    except Exception as e:
        logger.error(f"Error during shutdown: {e}")
        await update.message.reply_text(f"❌ Error during shutdown: {e}")

async def timezone_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """View or set the bot's timezone."""
    import pytz
    
    if not context.args:
        # Show current timezone
        current_tz = get_timezone_name()
        current_time = tz_now()
        
        msg = (
            f"🌍 *Current Timezone*\n\n"
            f"Timezone: `{current_tz}`\n"
            f"Current Time: {current_time.strftime('%Y-%m-%d %H:%M:%S %Z')}\n\n"
            f"*Common Timezones:*\n"
            f"• `UTC` - Coordinated Universal Time\n"
            f"• `America/New_York` - US Eastern\n"
            f"• `America/Sao_Paulo` - Brazil (UTC-3)\n"
            f"• `Europe/London` - UK\n"
            f"• `Asia/Tokyo` - Japan\n\n"
            f"To change: `/timezone <timezone_name>`\n"
            f"Note: Requires bot restart to take effect"
        )
        await update.message.reply_text(msg, parse_mode="Markdown")
    else:
        # Validate timezone
        tz_name = context.args[0]
        try:
            pytz.timezone(tz_name)
            
            msg = (
                f"✅ *Timezone Update*\n\n"
                f"To set timezone to `{tz_name}`, update your `.env` file:\n\n"
                f"`TIMEZONE={tz_name}`\n\n"
                f"Then restart the bot with `/shutdown` and start again."
            )
            await update.message.reply_text(msg, parse_mode="Markdown")
        except pytz.exceptions.UnknownTimeZoneError:
            await update.message.reply_text(
                f"❌ Unknown timezone: `{tz_name}`\n\n"
                f"Use a valid pytz timezone name.\n"
                f"See: https://en.wikipedia.org/wiki/List_of_tz_database_time_zones",
                parse_mode="Markdown"
            )

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
    app.add_handler(CommandHandler("channels", channels_command))
    
    # Analytics Commands
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(CommandHandler("history", history_command))
    app.add_handler(CommandHandler("charts", charts_command))
    app.add_handler(CommandHandler("export", export_command))
    app.add_handler(CommandHandler("health", health_command))
    app.add_handler(CommandHandler("shutdown", shutdown_command))
    app.add_handler(CommandHandler("timezone", timezone_command))

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

            # Initialize health monitor
            global health_monitor_instance
            health_monitor_instance = HealthMonitor(api, app)
            asyncio.create_task(health_monitor_instance.monitor_loop())
            logger.info("✅ Health monitoring started")

            # Notify admin that the bot is online
            await notify_admin_startup(app)

        except Exception as e:
            logger.error(f"❌ An error occurred during startup: {e}")

    app.post_init = post_init
    app.run_polling(close_loop=False)

if __name__ == "__main__":
    #keep_alive()
    main()
