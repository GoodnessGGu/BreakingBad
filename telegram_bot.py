
import sys
import logging
import asyncio
from datetime import datetime, timezone, timedelta
from collections import defaultdict

from telegram import Update, ReplyKeyboardMarkup, KeyboardButton

from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler, filters

from iqclient import IQOptionAPI
from settings import TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, TIMEZONE_OFFSET
# Import refactored utilities. 
from utilities import parse_signals, run_trade

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# Silence noisy loggers
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)
logging.getLogger("apscheduler").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)

# Global API instance
api = None

# User Configuration
USER_CONFIG = {
    "amount": 1.0,           # Default trade amount
    "max_gales": 2,          # Max retries per signal
    "mode": "INDIVIDUAL",    # INDIVIDUAL or COLLECTIVE
    "collective_multiplier": 1.0  # Multiplier for next signal in Collective mode
}

async def get_main_keyboard():
    """
    Return the main menu keyboard.
    """
    keyboard = [
        [KeyboardButton("💰 Check Balance"), KeyboardButton("⚙ Config")],
        [KeyboardButton("❓ Help")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

# --- Configuration Commands ---

async def config_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """View current configuration."""
    text = (
        "⚙ *Current Configuration*\n\n"
        f"💵 *Amount:* ${USER_CONFIG['amount']}\n"
        f"🔄 *Max Gales:* {USER_CONFIG['max_gales']}\n"
        f"📊 *Mode:* {USER_CONFIG['mode']}\n"
        f"❌ *Collective Mult:* x{USER_CONFIG['collective_multiplier']}\n\n"
        "*To change settings:*\n"
        "`/setamount 10`\n"
        "`/setgale 2`\n"
        "`/mode individual` or `/mode collective`"
    )
    await context.bot.send_message(
        chat_id=update.effective_chat.id, 
        text=text, 
        parse_mode='Markdown',
        reply_markup=await get_main_keyboard()
    )

async def set_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Set the trade amount."""
    try:
        amount = float(context.args[0])
        if amount < 1:
            raise ValueError("Amount must be >= 1")
        USER_CONFIG['amount'] = amount
        await context.bot.send_message(chat_id=update.effective_chat.id, text=f"✅ Amount set to ${amount}")
    except (IndexError, ValueError):
        await context.bot.send_message(chat_id=update.effective_chat.id, text="⚠️ Usage: `/setamount <value>` (e.g., /setamount 5)")

async def set_gale(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Set max martingales per signal."""
    try:
        gale = int(context.args[0])
        if gale < 0:
            raise ValueError("Gales must be >= 0")
        USER_CONFIG['max_gales'] = gale
        await context.bot.send_message(chat_id=update.effective_chat.id, text=f"✅ Max Gales set to {gale}")
    except (IndexError, ValueError):
        await context.bot.send_message(chat_id=update.effective_chat.id, text="⚠️ Usage: `/setgale <count>` (e.g., /setgale 2)")

async def set_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Set martingale mode."""
    try:
        mode = context.args[0].upper()
        if mode not in ["INDIVIDUAL", "COLLECTIVE"]:
            raise ValueError("Invalid mode")
        USER_CONFIG['mode'] = mode
        # Reset collective multiplier on mode switch
        USER_CONFIG['collective_multiplier'] = 1.0 
        await context.bot.send_message(chat_id=update.effective_chat.id, text=f"✅ Mode set to {mode}")
    except (IndexError, ValueError):
        await context.bot.send_message(chat_id=update.effective_chat.id, text="⚠️ Usage: `/mode individual` or `/mode collective`")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handle the /start command.
    """
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=(
            "👋 *Hello! I am your IQ Option Signal Bot.*\n\n"
            "I can execute your trading signals automatically.\n\n"
            "👇 *Use the buttons below to control the bot:*"
        ),
        parse_mode='Markdown',
        reply_markup=await get_main_keyboard()
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handle the /help command.
    """
    help_text = (
        "📚 *Bot Commands Help*\n\n"
        "💰 *Check Balance* - Shows your current IQ Option account balance.\n"
        "❓ *Help* - Shows this help message.\n\n"
        "📉 *Trading Signals*:\n"
        "Paste your signals directly in the chat. Supported formats:\n"
        "`06:15;EURUSD;CALL;5`\n"
        "`06:15 - EURUSD CALL M5`\n\n"
        "The bot automatically detects valid signals and schedules them."
    )
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=help_text,
        parse_mode='Markdown',
        reply_markup=await get_main_keyboard()
    )

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handle the /status command.
    """
    if not api or not api._connected:
         await context.bot.send_message(chat_id=update.effective_chat.id, text="⚠️ API not connected.")
         return

    try:
        balance = api.get_current_account_balance()
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=f"✅ *API Connected*\n💰 *Balance:* ${balance:.2f}",
            parse_mode='Markdown',
            reply_markup=await get_main_keyboard()
        )
    except Exception as e:
         await context.bot.send_message(chat_id=update.effective_chat.id, text=f"❌ Error getting status: {e}")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handle incoming text messages (looking for signals or button clicks).
    """
    text = update.message.text
    chat_id = update.effective_chat.id

    # Handle Button Clicks
    if text == "💰 Check Balance":
        await status(update, context)
        return
    elif text == "❓ Help":
        await help_command(update, context)
        return

    # Security check if restricted
    if TELEGRAM_CHAT_ID and str(chat_id) != str(TELEGRAM_CHAT_ID):
        logger.warning(f"Unauthorized access attempt from {chat_id}")
        return

    signals = parse_signals(text)
    if not signals:
        await context.bot.send_message(
            chat_id=chat_id, 
            text="ℹ️ No valid signals found. Send /help for instructions.",
            reply_markup=await get_main_keyboard()
        )
        return

    count = len(signals)
    await context.bot.send_message(
        chat_id=chat_id, 
        text=f"📥 Received {count} signal(s). Scheduling...",
        reply_markup=await get_main_keyboard()
    )

    # Process signals
    asyncio.create_task(process_signals_task(context, chat_id, signals))

# ... existing code ...

def main():
    # ... existing code ...
    
    start_handler = CommandHandler('start', start)
    help_handler = CommandHandler('help', help_command)
    status_handler = CommandHandler('status', status)
    
    # Config handlers
    config_handler = CommandHandler('config', config_command)
    amount_handler = CommandHandler('setamount', set_amount)
    gale_handler = CommandHandler('setgale', set_gale)
    mode_handler = CommandHandler('mode', set_mode)
    
    echo_handler = MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message)
    
    application.add_handler(start_handler)
    application.add_handler(help_handler)
    application.add_handler(status_handler)
    
    application.add_handler(config_handler)
    application.add_handler(amount_handler)
    application.add_handler(gale_handler)
    application.add_handler(mode_handler)
    
    application.add_handler(echo_handler)
    
    logger.info("🤖 Telegram Bot is polling...")
    application.run_polling()



async def process_signals_task(context: ContextTypes.DEFAULT_TYPE, chat_id, signals):
    """
    Schedule and execute trades based on signals.
    """
    grouped = defaultdict(list)
    for sig in signals:
        grouped[sig["time"]].append(sig)

    for sched_time in sorted(grouped.keys()):
        # Create timezone object
        tz = timezone(timedelta(hours=TIMEZONE_OFFSET))
        now = datetime.now(tz)
        
        # Calculate delay (both are aware now)
        delay = (sched_time - now).total_seconds()
        
        if delay < -60: # If signal is older than 60s
             # Skip or execute immediately? Usually skip.
             await context.bot.send_message(
                 chat_id=chat_id, 
                 text=f"⚠️ Skipping past signal at {sched_time.strftime('%H:%M')} (Time passed)"
             )
             continue

        if delay > 0:
            await context.bot.send_message(
                 chat_id=chat_id, 
                 text=f"⏳ Waiting {int(delay)}s for {len(grouped[sched_time])} signal(s) at {sched_time.strftime('%H:%M')}..."
            )
            await asyncio.sleep(delay)

        # Execute trades
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"🚀 Executing {len(grouped[sched_time])} trades now!"
        )
        
        # Determine strict sequential processing vs parallel based on mode
        # COLLECTIVE mode implies we need to know result of Trade A before AMOUNT of Trade B is managed?
        # If multiple signals are at SAME time, they will run in parallel.
        # Collective Martingale primarily affects the NEXT batch or NEXT signal.
        
        # Logic:
        # 1. Calculate Amount = Base * Multiplier
        # 2. Run trades
        # 3. If Mode == COLLETIVE:
        #    If ANY trade in this batch wins -> Reset Multiplier = 1
        #    If ALL trades fail -> Multiplier *= 2
        
        current_amount = USER_CONFIG['amount'] * USER_CONFIG['collective_multiplier']
        current_gales = USER_CONFIG['max_gales']
        
        # If using COLLECTIVE mode, the individual gales inside 'run_trade' should probably be 0? 
        # Requirement: "A collective martingale, where if a signal results in a loss, it proceeds to the next signal and double the amount"
        # This usually means NO internal gales for the signal itself if we are doing collective.
        # However, user might want MIXED (try 2 gales on signal A, if all fail, double amount for signal B).
        # We will respect `max_gales` AND `collective_strength`.
        
        results = await asyncio.gather(
            *(
                asyncio.to_thread(
                    run_trade, api, sig["asset"], sig["direction"], sig["expiry"], current_amount, current_gales
                )
                for sig in grouped[sched_time]
            )
        )

        # Report results and update Collective State
        batch_win = False
        for res in results:
             if res:
                 await context.bot.send_message(chat_id=chat_id, text=res.get("message", "Trade executed"))
                 if res.get("success"):
                     batch_win = True

        if USER_CONFIG['mode'] == 'COLLECTIVE':
            if batch_win:
                USER_CONFIG['collective_multiplier'] = 1.0
                if not USER_CONFIG['collective_multiplier'] == 1.0: # debug check (optional)
                    pass
                msg = "✅ Profit target hit (or win occurred). Collective Martingale reset to x1."
            else:
                USER_CONFIG['collective_multiplier'] *= 2.0
                msg = f"❌ Batch loss. Collective Martingale increased to x{USER_CONFIG['collective_multiplier']} for NEXT signal."
            
            await context.bot.send_message(chat_id=chat_id, text=msg)



async def on_startup(application):
    """
    Send a startup message to the admin.
    """
    if not TELEGRAM_CHAT_ID:
        logger.warning("⚠️ TELEGRAM_CHAT_ID not set. Cannot send startup message.")
        return

    msg = "🚀 *Bot Started Successfully*\n\n"
    
    if api and api._connected:
        try:
            balance = api.get_current_account_balance()
            msg += f"✅ *API Connected*\n"
            msg += f"💰 *Balance:* ${balance:.2f}\n"
        except:
             msg += "⚠️ API Connected but failed to fetch balance.\n"
    else:
        msg += "⚠️ IQ Option API NOT Connected.\n"

    msg += f"\n⚙ *Config:*\n"
    msg += f"💵 Amount: ${USER_CONFIG['amount']}\n"
    msg += f"📊 Mode: {USER_CONFIG['mode']}\n"
    msg += f"🕒 TZ Offset: UTC{TIMEZONE_OFFSET}"

    try:
        await application.bot.send_message(
            chat_id=TELEGRAM_CHAT_ID, 
            text=msg, 
            parse_mode='Markdown',
            reply_markup=await get_main_keyboard()
        )
    except Exception as e:
        logger.error(f"Failed to send startup message: {e}")


def main():
    global api
    if not TELEGRAM_TOKEN:
        logger.error("❌ TELEGRAM_TOKEN not found in settings or environment.")
        return

    # Initialize IQ Option API
    logger.info("Initializing IQ Option API...")
    api = IQOptionAPI()
    try:
        api._connect()
        logger.info("✅ IQ Option API Connected")
    except Exception as e:
        logger.error(f"❌ Failed to connect to IQ Option: {e}")
        return

    # Initialize Telegram Bot
    application = ApplicationBuilder().token(TELEGRAM_TOKEN).post_init(on_startup).build()
    
    start_handler = CommandHandler('start', start)
    help_handler = CommandHandler('help', help_command)
    status_handler = CommandHandler('status', status)
    echo_handler = MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message)
    
    application.add_handler(start_handler)
    application.add_handler(help_handler)
    application.add_handler(status_handler)
    application.add_handler(echo_handler)
    
    logger.info("🤖 Telegram Bot is polling...")
    application.run_polling()


if __name__ == '__main__':
    # Fix for Windows event loop policy
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        
    main()
