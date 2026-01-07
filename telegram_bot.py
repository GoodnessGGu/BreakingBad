
import sys
import logging
import asyncio
import time
from datetime import datetime, timezone, timedelta
from collections import defaultdict

from telegram import Update, ReplyKeyboardMarkup, KeyboardButton

from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler, filters

from iqclient import IQOptionAlgoAPI as IQOptionAPI
from settings import (
    TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, TIMEZONE_OFFSET,
    TELEGRAM_API_ID, TELEGRAM_API_HASH, SIGNAL_CHANNELS
)
import os
from telethon import TelegramClient, events
import logging
# Import refactored utilities. 
from utilities import parse_signals, run_trade
from analysis import check_technical_indicators

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
# Global Telethon Client
telethon_client = None

# User Configuration
USER_CONFIG = {
    "amount": 1.0,           # Default trade amount
    "max_gales": 2,          # Max retries per signal
    "mode": "INDIVIDUAL",    # INDIVIDUAL or COLLECTIVE
    "collective_multiplier": 1.0,  # Multiplier for next signal in Collective mode
    "ta_filter": False,            # Default: TA Filter OFF
    "active_channels": []          # Filter: If empty, ALL allowed. If populated, only these IDs allowed.
}

# Channel Names Mapping (Ideally passed via environment or learned)
CHANNEL_NAMES = {
    int(os.getenv('CHANNEL_ID_1') or 0): "Channel 1",
    int(os.getenv('CHANNEL_ID_2') or 0): "Channel 2",
    int(os.getenv('SIGNAL_CHANNEL_ID') or 0): "Signal Channel"
}

# Active Trades Tracker
ACTIVE_TRADES = [] # List of dicts: {'asset', 'direction', 'expiry', 'start_time', 'is_seconds'}
# Trade History (In-Memory for now)
TRADE_HISTORY = [] # List of finished trade dicts

async def get_main_keyboard():
    """
    Return the main menu keyboard.
    """
    mode_icon = "👥" if USER_CONFIG['mode'] == "COLLECTIVE" else "👤"
    ta_icon = "🟢" if USER_CONFIG.get('ta_filter', False) else "🔴"
    
    keyboard = [
        [KeyboardButton("✅ Start"), KeyboardButton("🛑 Stop")],
        [KeyboardButton("ℹ️ Status"), KeyboardButton("💰 Check Balance")],
        [KeyboardButton("📜 History"), KeyboardButton(f"🧠 TA Filter: {ta_icon}")],
        [KeyboardButton("📡 Channels"), KeyboardButton("⚙ Config")],
        [KeyboardButton("❓ Help")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

async def channels_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    UI to toggle active channels.
    """
    chat_id = update.effective_chat.id
    
    # Refresh names map
    keys = {}
    
    # Determine allowed list
    active_ids = USER_CONFIG.get('active_channels', [])
    if not active_ids:
        # Default All Active
        pass

    # Build Keyboard for Channels
    keyboard = []
    
    # We use CHANNEL_NAMES loaded from ENV
    for cid, cname in CHANNEL_NAMES.items():
        if not cid: continue
        
        # Check if active
        # Logic: If active_channels is EMPTY => ALL Active.
        # If populated => Only those in list are Active.
        
        is_active = (not active_ids) or (cid in active_ids)
        
        status_icon = "✅" if is_active else "❌"
        btn_text = f"{cname} {status_icon}"
        # Using CallbackQuery would be better but keeping simple ReplyButton for this architecture
        # Command style: "CH:12345"
        keyboard.append([KeyboardButton(f"📡 Toggle {cname}")])
        keys[f"📡 Toggle {cname}"] = cid # Store mapping

    keyboard.append([KeyboardButton("⬅️ Back")])
    
    status_text = "📡 *Channel Configuration*\n\n"
    if not active_ids:
        status_text += "🟢 *Mode:* ALL Channels Active\n"
    else:
        status_text += "🟡 *Mode:* Filtering Active\n"

    for cid, cname in CHANNEL_NAMES.items():
        if not cid: continue
        is_active = (not active_ids) or (cid in active_ids)
        status_text += f"• {cname}: {'✅' if is_active else '❌'}\n"

    await context.bot.send_message(
        chat_id=chat_id,
        text=status_text,
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True),
        parse_mode='Markdown'
    )
    return

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
    elif text.startswith("🧠 TA Filter"):
        # Toggle TA Filter
        USER_CONFIG['ta_filter'] = not USER_CONFIG['ta_filter']
        new_state = "ON" if USER_CONFIG['ta_filter'] else "OFF"
        
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"🧠 *TA Filter Switched {new_state}*",
            reply_markup=await get_main_keyboard(),
            parse_mode='Markdown'
        )
        return

    elif text == "📜 History":
        await history(update, context)
        return
    elif text == "📡 Channels":
        await channels_command(update, context)
        return
    elif text == "⬅️ Back":
        await context.bot.send_message(
             chat_id=chat_id,
             text="🔙 Main Menu",
             reply_markup=await get_main_keyboard()
        )
        return
    elif text.startswith("📡 Toggle"):
        # Handle Channel Toggling
        cname = text.replace("📡 Toggle ", "")
        target_cid = 0
        for cid, name in CHANNEL_NAMES.items():
            if name == cname:
                target_cid = cid
                break
        
        if target_cid:
            active_ids = USER_CONFIG.get('active_channels', [])
            
            # If list is empty (All Active), we need to populate it with all others except target?
            # Or simpler logic: If empty, populate with [ALL].
            
            if not active_ids:
                # Switching from ALL -> Explicit
                active_ids = [k for k in CHANNEL_NAMES.keys() if k]
            
            if target_cid in active_ids:
                active_ids.remove(target_cid)
            else:
                active_ids.append(target_cid)
            
            # Update Config
            USER_CONFIG['active_channels'] = active_ids
            # If empty again (user deselected the last one) -> Reset to ALL mode or keep empty (block all)?
            # Let's say if empty -> ALL BLOCKED. User must select at least one.
            # Wait, default is ALL ALLOWED.
            # If user wants to block all, Stop the bot.
            
            # For UI feedback:
            await channels_command(update, context)
        return

    # Security check if restricted
    if TELEGRAM_CHAT_ID and str(chat_id) != str(TELEGRAM_CHAT_ID):
        logger.warning(f"Unauthorized access attempt from {chat_id}")
        return

    # Check for SOURCE Header from Listener
    source_id = 0
    clean_text = text
    
    # Listener sends: "SOURCE:123456\n06:15;..."
    # Or multiple lines each starting with SOURCE?
    # Our listener.py implementation does: f"SOURCE:{chat.id};{time_str}..."
    # So each line has it.
    
    # We need to filter signals based on Source ID
    
    raw_signals = []
    
    # Strategy: Parse "SOURCE:ID;" prefix line by line
    lines = text.splitlines()
    allowed_lines = []
    
    active_ids = USER_CONFIG.get('active_channels', [])
    
    for line in lines:
        if line.startswith("SOURCE:"):
            try:
                # Format: SOURCE:12345;HH:MM...
                parts = line.split(";", 2) # Limit split to find ID
                if len(parts) >= 2:
                    src_tag = parts[0] # SOURCE:12345
                    sid = int(src_tag.split(":")[1])
                    
                    # Filtering Logic
                    if active_ids and sid not in active_ids:
                        logger.info(f"🚫 Ignored signal from Channel {sid} (Filtered Out)")
                        continue
                    
                    # Reconstruct line without SOURCE prefix for parser
                    # parts[1] starts with HH:MM
                    # We need to join back the rest
                    clean_line = line.split(";", 1)[1] # Remove SOURCE:ID;
                    allowed_lines.append(clean_line)
            except Exception as e:
                logger.warning(f"Error parsing source header: {e}")
                # Treat as normal line?
                allowed_lines.append(line)
        else:
            # Direct user input (no source header), always allow
            allowed_lines.append(line)
            
    if not allowed_lines:
        # Everything filtered out
        return

    final_text = "\n".join(allowed_lines)
    signals = parse_signals(final_text)
    
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
    # Pass context.bot
    asyncio.create_task(process_signals_task(context.bot, chat_id, signals))



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
        result = await asyncio.to_thread(
            run_trade,
            api,
            sig['asset'],
            sig['direction'],
            sig['expiry'],
            amount,
            max_gales,
            sig['option_type']
        )
        
        # Log to History
        if result:
            history_entry = {
                'time': datetime.now().strftime('%H:%M:%S'),
                'asset': sig['asset'],
                'direction': sig['direction'],
                'pnl': result.get('pnl', 0),
                'status': 'WIN' if result.get('success') else 'LOSS'
            }
            TRADE_HISTORY.append(history_entry)
            # Keep only last 50
            if len(TRADE_HISTORY) > 50:
                TRADE_HISTORY.pop(0)

        return result
    finally:
        if trade_entry in ACTIVE_TRADES:
            ACTIVE_TRADES.remove(trade_entry)

async def process_signals_task(bot, chat_id, signals):
    """
    Schedule and execute trades based on signals with JIT verification.
    """
    if not signals:
        return

    # Sort signals by time
    signals.sort(key=lambda x: x['time'])

    # 1. IMMEDIATE VERIFICATION REMOVED (User Request)
    
    # 2. Send Detailed Summary Head
    msg = "📥 *Signals Received & Scheduled*\n"
    msg += f"━━━━━━━━━━━━━━━━━━━━━\n"
    for s in signals:
        time_str = s['time'].strftime('%H:%M')
        # format expiry
        exp = f"{s['expiry']}s" if s.get('is_seconds') else f"{s['expiry']}m"
        msg += f"🕒 `{time_str}` {s['asset']} {s['direction'].upper()} {exp}\n"
    
    msg += f"━━━━━━━━━━━━━━━━━━━━━\n"
    msg += f"Total: {len(signals)} signals"
    
    await bot.send_message(chat_id=chat_id, text=msg, parse_mode='Markdown')

    # Pre-Filter Stale Signals (older than 60s)
    current_time = datetime.now(timezone(timedelta(hours=TIMEZONE_OFFSET)))
    valid_signals = []
    for s in signals:
         # Calculate delay relative to NOW
         # s['time'] is already timezone-aware (system tz)
         delay = (s['time'] - current_time).total_seconds()
         if delay < -60: # If signal is more than 60s old
             logger.warning(f"⚠️ Dropping stale signal: {s['asset']} at {s['time'].strftime('%H:%M')} (Expired {int(abs(delay))}s ago)")
             continue
         valid_signals.append(s)
    
    if len(valid_signals) < len(signals):
        await bot.send_message(chat_id=chat_id, text=f"🗑️ Dropped {len(signals) - len(valid_signals)} stale signals.")

    # Group by time for efficient waiting
    grouped = defaultdict(list)
    for sig in valid_signals:
        grouped[sig["time"]].append(sig)

    for sched_time in sorted(grouped.keys()):
        # Create timezone object
        tz = timezone(timedelta(hours=TIMEZONE_OFFSET))
        now = datetime.now(tz)
        
        # Calculate time until 10 seconds BEFORE execution for JIT check
        target_verification_time = sched_time - timedelta(seconds=10)
        delay = (target_verification_time - now).total_seconds()
        
        if delay > 0:
            msg_text = f"⏳ Waiting {int(delay)}s for {len(grouped[sched_time])} signals at {sched_time.strftime('%H:%M')}..."
            logger.info(msg_text)
            # Notify user only if very long wait? User said ONE detailed message head, so maybe suppress these intermediate waits?
            # Keeping logs, suppressing frequent chat spam unless very long.
            if delay > 300: 
                await bot.send_message(chat_id=chat_id, text=msg_text)
            
            await asyncio.sleep(delay)
        elif delay < -300: # 5 Minutes Stale Threshold
             msg = f"⏳ Signal for {sched_time.strftime('%H:%M')} is STALE (>{int(abs(delay))}s ago). Skipping."
             logger.warning(msg)
             await bot.send_message(chat_id=chat_id, text=msg)
             continue
        
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
                 # Update signal with determind option type
                 sig['option_type'] = option_type
                 
                 # --- TA FILTER CHECK ---
                 if USER_CONFIG.get('ta_filter', False):
                     is_safe = await asyncio.to_thread(
                         check_technical_indicators, 
                         api, 
                         sig['asset'], 
                         sig['direction']
                     )
                     if not is_safe:
                         msg = f"🧠 *TA Filter SKIP:* {sig['asset']} ({sig['direction']}) rejected by analysis."
                         logger.info(msg)
                         await bot.send_message(chat_id=chat_id, text=msg, parse_mode='Markdown')
                         continue
                 # -----------------------

                 verified_signals.append(sig)
                 logger.info(f"✅ {sig['asset']} Verified -> {option_type.value}")
             else:
                 msg = f"🚫 *SKIP:* {sig['asset']} unavailable/closed at {sched_time.strftime('%H:%M')}"
                 logger.warning(msg)
                 await bot.send_message(chat_id=chat_id, text=msg, parse_mode='Markdown')

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
        
        await bot.send_message(
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
                 await bot.send_message(chat_id=chat_id, text=res.get("message", "Trade executed"))
                 if res.get("success"):
                     batch_win = True
        
        # Send Batch Summary
        summary_msg = generate_batch_summary(results)
        await bot.send_message(chat_id=chat_id, text=summary_msg, parse_mode='Markdown')

        if USER_CONFIG['mode'] == 'COLLECTIVE':
            if batch_win:
                USER_CONFIG['collective_multiplier'] = 1.0
                msg = "✅ Profit target hit. Collective Martingale reset."
            else:
                USER_CONFIG['collective_multiplier'] *= 2.0
                msg = f"❌ Batch loss. Collective Martingale INCREASED to x{USER_CONFIG['collective_multiplier']}."
            
            await bot.send_message(chat_id=chat_id, text=msg)


def generate_batch_summary(results):
    total = len(results)
    wins = sum(1 for r in results if r and r.get('success'))
    losses = total - wins
    total_pnl = sum(r.get('pnl', 0) for r in results if r)
    
    emoji = "🟢" if total_pnl >= 0 else "🔴"
    
    return (
        f"📊 *Batch Summary*\n"
        f"━━━━━━━━━━━━━━\n"
        f"Total Trades: {total}\n"
        f"✅ Wins: {wins}\n"
        f"❌ Losses: {losses}\n"
        f"💰 Net PnL: {emoji} ${total_pnl:.2f}"
    )

async def history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show last 10 trades."""
    if not TRADE_HISTORY:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="📜 History is empty.")
        return

    msg = "📜 *Recent Trade History*\n━━━━━━━━━━━━━━\n"
    for t in reversed(TRADE_HISTORY[-10:]):
        icon = "✅" if t['status'] == 'WIN' else "❌"
        msg += f"{icon} `{t['time']}` {t['asset']} ({t['direction'].upper()}) | ${t['pnl']:.2f}\n"
    
    await context.bot.send_message(chat_id=update.effective_chat.id, text=msg, parse_mode='Markdown')


async def on_startup(application):
    """
    Send a startup message to the admin.
    """
    global telethon_client
    
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

    # --- Start Telethon Listener ---
    if not TELEGRAM_API_ID or not TELEGRAM_API_HASH:
        logger.warning("⚠️ Telethon API ID/HASH missing. Listener disabled.")
        return

    logger.info("🎧 Starting Telethon Listener...")
    try:
        telethon_client = TelegramClient('anon', int(TELEGRAM_API_ID), TELEGRAM_API_HASH)
        
        # Signal Batching/Buffer Variables
        BATCH_BUFFER = []
        BATCH_TIMER = None

        async def flush_batch():
            """
            Process all buffered signals at once.
            """
            nonlocal BATCH_BUFFER
            if not BATCH_BUFFER: return
            
            # Copy and clear buffer
            signals_to_process = BATCH_BUFFER.copy()
            BATCH_BUFFER.clear()
            
            logger.info(f"📤 Flushing batch: {len(signals_to_process)} accumulated signals")
            
            # Trigger Bot Task with ALL signals
            asyncio.create_task(
                 process_signals_task(application.bot, TELEGRAM_CHAT_ID, signals_to_process)
            )

        # Register Handler with closure to capture 'application.bot'
        @telethon_client.on(events.NewMessage(chats=SIGNAL_CHANNELS))
        async def bridge_handler(event):
             chat = await event.get_chat()
             chat_id = chat.id
             text = event.message.text
             
             # Filter
             active_ids = USER_CONFIG.get('active_channels', [])
             if active_ids and chat_id not in active_ids:
                 return
             
             signals = parse_signals(text)
             # Use the global parse_signals which uses 'parse_standard_signal' etc.
             
             if signals:
                 logger.info(f"📥 Listener: {len(signals)} signals from {getattr(chat, 'title', chat_id)}")
                 
                 # Add to Buffer
                 BATCH_BUFFER.extend(signals)
                 logger.info(f"Buffersize: {len(BATCH_BUFFER)}") # Check if buffer grows
                 
                 # Debounce: Cancel pending flush, start new one
                 nonlocal BATCH_TIMER
                 if BATCH_TIMER and not BATCH_TIMER.done():
                     BATCH_TIMER.cancel()
                 
                 try: 
                    BATCH_TIMER = asyncio.create_task(wait_and_flush())
                    logger.info("Started/Restarted Batch Timer") 
                 except Exception as e:
                    logger.error(f"Failed to start batch timer: {e}")

        async def wait_and_flush():
             # Wait 5 seconds for more signals
             try:
                 await asyncio.sleep(5)
                 await flush_batch()
             except asyncio.CancelledError:
                 pass
             except Exception as e:
                 logger.error(f"❌ Error in wait_and_flush: {e}")

        await telethon_client.start()
        logger.info("✅ Telethon Client Started & Authenticated")
        
        # Run in background
        asyncio.create_task(telethon_client.run_until_disconnected())
        
    except Exception as e:
        logger.error(f"❌ Failed to start Telethon: {e}")



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
