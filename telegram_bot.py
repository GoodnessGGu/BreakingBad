
import sys
import logging
import asyncio
import time
from datetime import datetime, timezone, timedelta
from collections import defaultdict

from telegram import Update, ReplyKeyboardMarkup, KeyboardButton

from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler, filters

from iqclient import IQOptionAlgoAPI as IQOptionAPI
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

# Active Trades Tracker
ACTIVE_TRADES = [] # List of dicts: {'asset', 'direction', 'expiry', 'start_time', 'is_seconds'}

async def get_main_keyboard():
    """
    Return the main menu keyboard.
    """
    keyboard = [
        [KeyboardButton("💰 Check Balance"), KeyboardButton("ℹ️ Status")],
        [KeyboardButton("⚙ Config"), KeyboardButton("🔄 Gale Mode")],
        [KeyboardButton("❓ Help")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

# --- Configuration Commands ---

async def config_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """View current configuration."""
    currency = api.get_currency() if api else "$"
    text = (
        "⚙ *Current Configuration*\n\n"
        f"💵 *Amount:* {currency}{USER_CONFIG['amount']}\n"
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
        
        # Determine minimum based on currency
        currency = api.get_currency() if api else "$"
        min_amount = 1500 if currency in ['₦', 'NGN'] else 1
        
        if amount < min_amount:
            raise ValueError(f"Amount must be >= {currency}{min_amount}")
            
        USER_CONFIG['amount'] = amount
        await context.bot.send_message(chat_id=update.effective_chat.id, text=f"✅ Amount set to {currency}{amount}")
    except (IndexError, ValueError) as e:
        # Show specific error if it was a ValueError we raised
        if "Amount must be" in str(e):
             await context.bot.send_message(chat_id=update.effective_chat.id, text=f"⚠️ {e}")
        else:
             await context.bot.send_message(chat_id=update.effective_chat.id, text="⚠️ Usage: `/setamount <value>`")

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

async def account_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Switch between REAL and DEMO accounts."""
    logger.info("received /account command")
    
    if not api or not api.is_connected():
         await context.bot.send_message(chat_id=update.effective_chat.id, text="⚠️ API not connected.")
         return
         
    try:
        target_arg = context.args[0].upper() if context.args else None
        current = api.account_manager.current_account_type.upper()
        
        logger.info(f"Switch Request. Current: {current}, Arg: {target_arg}")
        
        # Toggle if no arg
        priority_target = target_arg
        if not priority_target:
            priority_target = "REAL" if current == "PRACTICE" or current == "DEMO" else "PRACTICE"
            
        # Map DEMO -> PRACTICE for API if needed, simpler to just accept REAL/PRACTICE
        if priority_target == "DEMO": priority_target = "PRACTICE"
        
        # NOTE: Tournament is passed as "TOURNAMENT" (API expects lowercase actually? No, switch_account converts to lower)
        
        logger.info(f"Attempting switch to {priority_target}...")
        await context.bot.send_message(chat_id=update.effective_chat.id, text=f"🔄 Attempting switch to {priority_target}...")
        
        # Run switch in thread
        success = await asyncio.to_thread(api.switch_account, priority_target)
        
        if not success:
             await context.bot.send_message(chat_id=update.effective_chat.id, text=f"❌ Failed to switch to {priority_target}. Already active or account not found.")
             return

        # Give it a moment to update balance
        await asyncio.sleep(1)
        
        new_type = api.account_manager.current_account_type.upper()
        balance = api.get_current_account_balance()
        currency = api.get_currency()
        
        msg = f"✅ *Switched to {new_type}*\n💰 *Balance:* {currency}{balance:.2f}"
        await context.bot.send_message(chat_id=update.effective_chat.id, text=msg, parse_mode='Markdown')
        
    except (IndexError, ValueError):
        await context.bot.send_message(chat_id=update.effective_chat.id, text="⚠️ Usage: `/account REAL` or `/account DEMO`")
    except Exception as e:
        await context.bot.send_message(chat_id=update.effective_chat.id, text=f"❌ Switch failed: {e}")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handle the /start command.
    """
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=(
            "👋 *Welcome to IQ Option Algo Bot*\n"
            "━━━━━━━━━━━━━━━━━━━━━\n"
            "🚀 *Features Active:*\n"
            "✅ *Blitz Trading (5s)*\n"
            "✅ *Smart JIT Verification*\n"
            "✅ *Martingale Strategies*\n\n"
            f"🏦 *Account:* `{api.account_manager.current_account_type.upper() if api else 'UNK'}`\n"
            "👇 *Control Panel:*"
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
        "`06:15;EURUSD;CALL;S5` (for Blitz/5s)\n"
        "`06:15 - EURUSD CALL M5`\n\n"
        "⚙ *Configuration Commands*:\n"
        "▫️ `/config` - View settings\n"
        "▫️ `/config` - View settings\n"
        "▫️ `/config` - View settings\n"
        "▫️ `/account <type>` - Switch `REAL`, `DEMO` or `TOURNAMENT`\n"
        "▫️ `/mode <type>` - `individual` or `collective`\n"
        "▫️ `/mode <type>` - `individual` or `collective`\n"
        "▫️ `/setamount <val>` - Set base amount\n"
        "▫️ `/setgale <val>` - Set default gales\n\n"
        "🛑 *Admin Commands*:\n"
        "▫️ `/shutdown` - Stop the bot"
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
        currency = api.get_currency()
        account_type = api.account_manager.current_account_type.upper()
        
        msg = (f"✅ *API Connected*\n"
               f"🏦 *Account:* `{account_type}`\n"
               f"💰 *Balance:* {currency}{balance:.2f}")
        
        if ACTIVE_TRADES:
             msg += "\n\n📉 *Active Trades:*"
             now = datetime.now()
             for t in ACTIVE_TRADES:
                 # Calculate remaining time
                 elapsed = (now - t['start_time']).total_seconds()
                 duration = t['expiry'] if t['is_seconds'] else t['expiry'] * 60
                 remaining = max(0, duration - elapsed)
                 
                 unit = "s" if t['is_seconds'] else "m"
                 msg += f"\n• {t['asset']} {t['direction']} ({t['expiry']}{unit}) | ⏳ {int(remaining)}s left"
        else:
             msg += "\n\n💤 No active trades."
             
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=msg,
            parse_mode='Markdown',
            reply_markup=await get_main_keyboard()
        )
    except Exception as e:
         await context.bot.send_message(chat_id=update.effective_chat.id, text=f"❌ Error getting status: {e}")

async def shutdown_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Gracefully shutdown the bot."""
    if str(update.effective_chat.id) != str(TELEGRAM_CHAT_ID):
        return

    await context.bot.send_message(chat_id=update.effective_chat.id, text="🛑 *Shutting down...* User request.", parse_mode='Markdown')
    logger.info("🛑 Shutdown requested by user.")
    
    # Give time for message to send
    await asyncio.sleep(1)
    
    # Stop application
    # os._exit is safer to force kill threads
    import os
    os._exit(0)

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
    elif text == "ℹ️ Status":
        await status(update, context)
        return
    elif text == "🔄 Gale Mode":
        # Toggle Mode
        current = USER_CONFIG['mode']
        new_mode = "COLLECTIVE" if current == "INDIVIDUAL" else "INDIVIDUAL"
        
        USER_CONFIG['mode'] = new_mode
        USER_CONFIG['collective_multiplier'] = 1.0 # Reset multiplier
        
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"🔄 *Mode Switched to:* {new_mode}",
            parse_mode='Markdown'
        )
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
    account_handler = CommandHandler('account', account_command)
    
    echo_handler = MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message)
    
    application.add_handler(start_handler)
    application.add_handler(help_handler)
    application.add_handler(status_handler)
    application.add_handler(shutdown_handler)
    
    application.add_handler(config_handler)
    application.add_handler(amount_handler)
    application.add_handler(gale_handler)
    application.add_handler(mode_handler)
    application.add_handler(account_handler)
    
    application.add_handler(echo_handler)
    
    logger.info("🤖 Telegram Bot is polling...")
    application.run_polling()



async def execute_trade_wrapper(api, sig, amount, max_gales):
    """
    Wrapper to track active trades in global list.
    """
    trade_entry = {
        'asset': sig['asset'],
        'direction': sig['direction'],
        'expiry': sig['expiry'],
        'start_time': datetime.now(),
        'is_seconds': sig.get('is_seconds', False)
    }
    ACTIVE_TRADES.append(trade_entry)
    try:
        return await asyncio.to_thread(
            run_trade,
            api,
            sig['asset'],
            sig['direction'],
            sig['expiry'],
            amount,
            max_gales,
            sig['option_type']
        )
    finally:
        if trade_entry in ACTIVE_TRADES:
            ACTIVE_TRADES.remove(trade_entry)

async def process_signals_task(context: ContextTypes.DEFAULT_TYPE, chat_id, signals):
    """
    Schedule and execute trades based on signals with JIT verification.
    """
    if not signals:
        return

    # Sort signals by time
    signals.sort(key=lambda x: x['time'])

    # 1. IMMEDIATE VERIFICATION (First Signal)
    first_sig = signals[0]
    logger.info(f"🔎 Verifying first signal: {first_sig['asset']}...")
    try:
        # Check availability immediately
        # Run in thread to avoid blocking loop
        option_type = await asyncio.to_thread(
            api.check_asset_availability, 
            first_sig['asset'], 
            first_sig['expiry'],
            first_sig.get('is_seconds', False)
        )
        
        if not option_type:
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"⚠️ Warning: First signal asset {first_sig['asset']} is currently UNAVAILABLE or CLOSED.\n"
                     f"This might be a bad signal or market is closed. Scheduling anyway..."
            )
        else:
             await context.bot.send_message(
                chat_id=chat_id,
                text=f"✅ First signal verified: {first_sig['asset']} is OPEN ({option_type.value}).\n"
                     f"All {len(signals)} signals scheduled."
            )
    except Exception as e:
        logger.error(f"Failed to verify first signal: {e}")

    # Group signals by time
    grouped = defaultdict(list)
    for sig in signals:
        grouped[sig["time"]].append(sig)

    for sched_time in sorted(grouped.keys()):
        # Create timezone object
        tz = timezone(timedelta(hours=TIMEZONE_OFFSET))
        now = datetime.now(tz)
        
        # Calculate time until 10 seconds BEFORE execution for JIT check
        target_verification_time = sched_time - timedelta(seconds=10)
        delay = (target_verification_time - now).total_seconds()
        
        if delay > 0:
            msg_text = f"⏳ Waiting {int(delay)}s until verification window for {len(grouped[sched_time])} signals at {sched_time.strftime('%H:%M')}..."
            logger.info(msg_text)
            # Optional: Notify user of long waits
            if delay > 60:
                await context.bot.send_message(chat_id=chat_id, text=msg_text)
            
            await asyncio.sleep(delay)
        
        # --- JIT VERIFICATION PHASE (T-10s) ---
        logger.info(f"🔎 JIT Verifying signals for {sched_time.strftime('%H:%M')}...")
        batch_signals = grouped[sched_time]
        verified_signals = []

        for sig in batch_signals:
             # Check asset availability (Run in thread to be safe)
             option_type = await asyncio.to_thread(
                 api.check_asset_availability, 
                 sig['asset'], 
                 sig['expiry'],
                 sig.get('is_seconds', False)
             )

             if option_type:
                 # Update signal with determined option type
                 sig['option_type'] = option_type
                 verified_signals.append(sig)
                 logger.info(f"✅ {sig['asset']} Verified -> {option_type.value}")
             else:
                 msg = f"🚫 *SKIP:* {sig['asset']} unavailable/closed at {sched_time.strftime('%H:%M')}"
                 logger.warning(msg)
                 await context.bot.send_message(chat_id=chat_id, text=msg, parse_mode='Markdown')

        if not verified_signals:
            logger.warning(f"No valid signals for {sched_time}. Skipping batch.")
            continue
            
        # Calculate remaining time to exact execution
        now_after_check = datetime.now(tz)
        final_delay = (sched_time - now_after_check).total_seconds()
        
        if final_delay > 0:
             logger.info(f"Verified {len(verified_signals)} signals. Waiting {final_delay:.1f}s for precise execution...")
             await asyncio.sleep(final_delay)
        
        # Determine trade parameters
        current_amount = USER_CONFIG['amount'] * USER_CONFIG.get('collective_multiplier', 1)
        current_gales = USER_CONFIG['max_gales']
        
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"🚀 Executing {len(verified_signals)} verified trades now!"
        )

        # Execute trades
        # Note: run_trade signature: (api, asset, direction, expiry, amount, max_gales, option_type)
        # We need to pass option_type to run_trade
        results = await asyncio.gather(
            *(
                execute_trade_wrapper(
                    api, 
                    sig, 
                    current_amount, 
                    current_gales,
                )
                for sig in verified_signals
            )
        )

        # Handle Collective Martingale Multiplier
        if USER_CONFIG["mode"] == "COLLECTIVE":
            # If ANY trade wins -> Reset
            if any(res > 0 for res in results):
                USER_CONFIG["collective_multiplier"] = 1
                logger.info("Collective Win/Reset -> Multiplier 1x")
            else:
                 USER_CONFIG["collective_multiplier"] *= 2
                 logger.info(f"Collective Loss -> Multiplier {USER_CONFIG['collective_multiplier']}x")

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
            currency = api.get_currency()
            msg += f"✅ *API Connected*\n"
            msg += f"💰 *Balance:* {currency}{balance:.2f}\n"
        except:
             msg += "⚠️ API Connected but failed to fetch balance.\n"
    else:
        msg += "⚠️ IQ Option API NOT Connected.\n"

    currency = api.get_currency() if api else "$"
    msg += f"\n⚙ *Config:*\n"
    msg += f"💵 Amount: {currency}{USER_CONFIG['amount']}\n"
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


async def monitor_connection(context: ContextTypes.DEFAULT_TYPE):
    """
    Periodic health check for IQ Option connection.
    Reconnects if disconnected.
    """
    global api
    if not api or not api.is_connected():
        logger.warning("⚠️ IQ Option API disconnected. Attempting reconnect...")
        try:
             # Run blocking connect in thread
             await asyncio.to_thread(api.connect)
             
             if api.is_connected():
                 logger.info("✅ IQ Option API Reconnected!")
                 if TELEGRAM_CHAT_ID:
                     await context.bot.send_message(chat_id=TELEGRAM_CHAT_ID, text="✅ IQ Option API Reconnected!")
        except Exception as e:
             logger.error(f"Reconnect failed: {e}")

def main():
    global api
    if not TELEGRAM_TOKEN:
        logger.error("❌ TELEGRAM_TOKEN not found in settings or environment.")
        return

    # Initialize IQ Option API
    logger.info("Initializing IQ Option API...")
    api = IQOptionAPI()
    try:
        api.connect() # Use new public method
        logger.info("✅ IQ Option API Connected")
    except Exception as e:
        logger.error(f"❌ Failed to connect to IQ Option: {e}")
        # Build app anyway to allow retry loop to handle it later
        pass

    # Initialize Telegram Bot
    application = ApplicationBuilder().token(TELEGRAM_TOKEN).post_init(on_startup).build()
    
    # Add connection monitor job (runs every 30s)
    if application.job_queue:
        application.job_queue.run_repeating(monitor_connection, interval=30, first=5)
    
    start_handler = CommandHandler('start', start)
    help_handler = CommandHandler('help', help_command)
    status_handler = CommandHandler('status', status)
    shutdown_handler = CommandHandler('shutdown', shutdown_command)
    account_handler = CommandHandler('account', account_command)
    echo_handler = MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message)
    
    application.add_handler(start_handler)
    application.add_handler(help_handler)
    application.add_handler(status_handler)
    application.add_handler(shutdown_handler)
    application.add_handler(account_handler)
    application.add_handler(echo_handler)
    
    logger.info("🤖 Telegram Bot is polling...")
    logger.info("🤖 Telegram Bot is polling...")
    
    # Robust Polling Loop with Retry
    while True:
        try:
             application.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True, close_loop=False)
        except Exception as e:
            logger.error(f"⚠️ Polling Error: {e}")
            logger.info("🔄 Retrying connection in 5 seconds...")
            time.sleep(5)
        else:
             # If run_polling returns cleanly (e.g. stop signal), break loop
             break


if __name__ == '__main__':
    # Fix for Windows event loop policy
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        
    main()
