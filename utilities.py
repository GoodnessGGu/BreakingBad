import logging
import time
from collections import defaultdict
import re
import asyncio
from datetime import datetime, timedelta, date, timezone
from typing import Optional

from settings import TIMEZONE_OFFSET
from models import OptionType, Direction, OptionsTradeParams
# import iqclient # Removed to avoid circular import (listener -> utilities -> iqclient -> trade -> utilities)

logger = logging.getLogger(__name__)


def get_timestamps(start_str: str = None, end_str: str = None) -> tuple:
    """Creates a time range by converting datetime strings to Unix timestamps."""
    try:
        if end_str is None:
            end_dt = datetime.now()
        else:
            end_dt = datetime.strptime(end_str, "%Y-%m-%d %H:%M:%S")

        if start_str is None:
            start_dt = end_dt - timedelta(hours=24)
        else:
            start_dt = datetime.strptime(start_str, "%Y-%m-%d %H:%M:%S")

        return int(start_dt.timestamp()), int(end_dt.timestamp())
    except Exception as e:
        logger.error(str(e))
        return None, None


def get_expiry_timestamp(timestamp:int, expiry:int=1):
    """Calculate expiration timestamp based on a given timestamp and expiry duration."""
    min_time_needed = 31
    timestamp = timestamp / 1000
    now_date = datetime.fromtimestamp(timestamp)
    now_date_hm = now_date.replace(second=0, microsecond=0)

    if expiry == 1:
        if (now_date_hm + timedelta(minutes=1)).timestamp() - timestamp >= min_time_needed:
            expiration = now_date_hm + timedelta(minutes=1)
        else:
            expiration = now_date_hm + timedelta(minutes=2)
    else:
        time_until_expiry = (now_date_hm + timedelta(minutes=1)).timestamp() - timestamp
        expiration = now_date_hm + timedelta(minutes=expiry)
        if time_until_expiry < min_time_needed:
            expiration = now_date_hm + timedelta(minutes=expiry+1)

    return expiration.timestamp()

def get_expiry_timestamp_seconds(timestamp: int, expiry_seconds: int) -> int:
    """
    Calculate precise expiration timestamp for Blitz options (seconds-based).
    Blitz options usually expire exactly X seconds from purchase time.
    """
    # Simply add seconds to current server time and return integer timestamp
    # Note: timestamp input is usually in milliseconds, output expected in seconds
    current_ts_sec = timestamp / 1000
    expiration = current_ts_sec + expiry_seconds
    return int(expiration)


def get_remaining_secs(timestamp, duration):
    """Calculate the remaining seconds until expiration."""
    expiry_ts = get_expiry_timestamp(timestamp, duration)
    return expiry_ts - int(timestamp/1000)


def generate_request_id(request_id: Optional[str] = None) -> str:
    """Generate a unique request ID for API calls."""
    if request_id is not None:
        return request_id
    microsecond_part = str(time.time()).split('.')[1]
    return microsecond_part


def parse_standard_signal(line: str):
    """
    Parse standard format: 06:15;EURUSD;CALL;5
    Uses TIMEZONE_OFFSET from settings (default UTC-3 for this format).
    """
    # Pattern 1: Semicolon separated
    pattern1 = re.compile(r"(\d{2}:\d{2});([A-Z0-9\-/]+);(CALL|PUT);([MS]?\d+)", re.IGNORECASE)
    # Pattern 2: Dash/Space separated
    pattern2 = re.compile(r"(\d{1,2}:\d{2})\s*-\s*([A-Z0-9\-/]+)\s+(CALL|PUT)\s+([MS]\d+)", re.IGNORECASE)

    m = pattern1.search(line) or pattern2.search(line)
    if not m:
        return None

    time_str, asset, direction, expiry_str = m.groups()
    hh, mm = map(int, time_str.split(":"))

    # Determine strict minutes vs seconds
    is_seconds = False
    expiry_val = 0
    
    expiry_str = expiry_str.upper()
    if expiry_str.startswith('S'):
        is_seconds = True
        expiry_val = int(expiry_str[1:])
    elif expiry_str.startswith('M'):
        is_seconds = False
        expiry_val = int(expiry_str[1:])
    else:
        is_seconds = False
        expiry_val = int(expiry_str)

    # Determine Source Timezone (Standard Signals usually UTC-3)
    # We default this to -3 for standard string parsing as per user requirement
    STANDARD_SIGNAL_OFFSET = -3 
    source_tz = timezone(timedelta(hours=STANDARD_SIGNAL_OFFSET))
    
    # Target System Timezone (Where the bot runs, e.g. UTC+1 Lagos)
    system_tz = timezone(timedelta(hours=TIMEZONE_OFFSET))
    
    now_source = datetime.now(source_tz)
    target_date = now_source.date()
    
    # Create datetime in Source TZ
    scheduled_dt_source = datetime.combine(target_date, datetime.min.time()).replace(
        hour=hh, minute=mm, second=0, tzinfo=source_tz
    )
    
    # Convert to System TZ (for execution checks)
    scheduled_dt = scheduled_dt_source.astimezone(system_tz)
    
    # Handle overnight signals (if signal time < now - 1h, assume next day? or just today)
    # For simplicity, assume today. 

    return {
        "time": scheduled_dt,
        "asset": asset,
        "direction": direction.lower(),
        "expiry": expiry_val,
        "is_seconds": is_seconds,
        "line": line
    }

def parse_nigerian_signal(text_block: str):
    """
    Parse Nigerian format:
    Trade: 🇪🇺 EUR/CAD 🇨🇦 (OTC)
    Timer: 5 minutes
    Entry: 6:19 PM
    Direction: BUY 🟩
    """
    # Needs multi-line matching
    try:
        # Extract Asset
        # Look for "Trade:" then capture content, especially XXX/XXX
        asset_match = re.search(r"Trade:.*([A-Z]{3}/[A-Z]{3}).*", text_block, re.IGNORECASE)
        if not asset_match:
            return None
        
        raw_asset = asset_match.group(1).replace("/", "") # EUR/CAD -> EURCAD
        
        # Check for OTC in the line
        if "(OTC)" in text_block or "OTC" in asset_match.group(0):
            if not raw_asset.endswith("-OTC"):
                raw_asset += "-OTC"

        # Extract Time (12h format)
        time_match = re.search(r"Entry:\s*(\d{1,2}:\d{2}\s*[AP]M)", text_block, re.IGNORECASE)
        if not time_match:
            return None
        time_str = time_match.group(1)
        
        # Extract Expiry
        expiry_match = re.search(r"Timer:\s*(\d+)", text_block, re.IGNORECASE)
        if not expiry_match:
            return None
        expiry_val = int(expiry_match.group(1))

        # Extract Direction
        dir_match = re.search(r"Direction:\s*(BUY|SELL)", text_block, re.IGNORECASE)
        if not dir_match:
            return None
        raw_dir = dir_match.group(1).upper()
        direction = "call" if raw_dir == "BUY" else "put"

        # Timezone Logic
        # Input is Nigerian Time (UTC+1)
        # We need to convert it to the User's Configured Timezone (TIMEZONE_OFFSET)
        
        # Parse 12h time
        dt_struct = datetime.strptime(time_str, "%I:%M %p")
        
        # Nigerian Timezone (UTC+1)
        nigeria_tz = timezone(timedelta(hours=1))
        
        # User Configured Timezone
        user_tz = timezone(timedelta(hours=TIMEZONE_OFFSET))
        
        now_ng = datetime.now(nigeria_tz)
        
        # Combine with TODAY's date in Nigeria
        scheduled_dt_ng = datetime.combine(now_ng.date(), dt_struct.time()).replace(tzinfo=nigeria_tz)
        
        # Convert to User Timezone to match the rest of the system
        scheduled_dt_user = scheduled_dt_ng.astimezone(user_tz)
        
        return {
            "time": scheduled_dt_user,
            "asset": raw_asset, # EURUSD-OTC
            "direction": direction,
            "expiry": expiry_val,
            "is_seconds": False, # Usually minutes for this channel
            "line": "Nigerian Signal"
        }
        
    except Exception as e:
        logger.error(f"Error parsing Nigerian signal: {e}")
        return None

def parse_signals(text: str):
    """
    Master parser to handle multiple formats.
    """
    signals = []
    
    # Strategy 1: Nigerian Format (Multi-line block)
    # Check if text looks like a block signal
    if "Trade:" in text and "Entry:" in text:
        # Split by "NEW SIGNAL" or similar if multiple in one paste?
        # Assuming single block per paste for now, or multiple separated by newlines
        # Let's try to parse the whole text as one block first
        sig = parse_nigerian_signal(text)
        if sig:
            signals.append(sig) 
            return signals # Return immediately if block matched
            
        # If text contains keywords but failed, maybe multiple blocks?
        # Future improvement: split by "🔔"
        blocks = text.split("🔔")
        for b in blocks:
            sig = parse_nigerian_signal(b)
            if sig:
                signals.append(sig)
        
        if signals:
            return sorted(signals, key=lambda x: x["time"])

    # Strategy 2: Standard Line-by-Line
    for line in text.splitlines():
        line = line.strip()
        if not line: continue
        
        sig = parse_standard_signal(line)
        if sig:
            signals.append(sig)

    return sorted(signals, key=lambda x: x["time"])


def run_trade(api, asset, direction, expiry, amount, max_gales=2, option_type: OptionType = OptionType.DIGITAL_OPTION):
    """
    Place trade using the new V2 engine mechanism.
    Supports Martingale via max_gales loop.
    """
    current_amount = amount 

    for gale in range(max_gales + 1):
        try:
            dir_enum = Direction.CALL if direction.lower() == 'call' else Direction.PUT
            
            params = OptionsTradeParams(
                asset=asset,
                amount=float(current_amount),
                direction=dir_enum,
                expiry=expiry,
                option_type=option_type
            )
            
            success, order_result = api.execute_options_trade(params)

            if not success:
                logger.error(f"❌ Failed to place trade on {asset}: {order_result}")
                return {"success": False, "message": f"❌ Failed to place trade: {order_result}"}

            order_id = order_result
            
            # Fetch details for logging
            currency = api.get_currency()
            logger.info(f"🎯 Placed trade: {asset} {direction.upper()} {currency}{current_amount} (Expiry {expiry}m)")
            
            success_outcome, trade_data = api.get_trade_outcome(order_id, expiry=expiry, option_type=option_type)
            
            balance = api.get_current_account_balance()

            if success_outcome and trade_data:
                pnl = float(trade_data.get('pl_amount', 0))
                
                if pnl > 0:
                    msg = ""
                    if gale == 0:
                        msg = (
                            f"✅ *WIN* on {asset} ({direction.upper()})\n"
                            f"💵 *Profit:* +{currency}{pnl:.2f}\n"
                            f"💰 *Balance:* {currency}{balance:.2f}"
                        )
                    else:
                        msg = (
                            f"🔥 *WIN* after Gale {gale} on {asset}\n"
                            f"💵 *Profit:* +{currency}{pnl:.2f}\n"
                            f"💰 *Balance:* {currency}{balance:.2f}"
                        )
                    logger.info(msg.replace('*', '').replace('\n', ' | '))
                    return {"success": True, "pnl": pnl, "gale": gale, "message": msg}
                else:
                    logger.warning(
                        f"⚠️ LOSS on {asset} | Attempt {gale} | PnL: {pnl:.2f} | Balance: {currency}{balance:.2f}"
                    )
                    current_amount *= 2  # Martingale
            else:
                 logger.warning(f"⚠️ Trade outcome unknown or timed out on {asset}")
                 current_amount *= 2 # Threat as loss?

        except Exception as e:
            logger.error(f"Error in run_trade loop: {e}")
            return {"success": False, "message": f"❌ Error: {e}"}

    msg = (
        f"💀 *LOSS* on {asset}\n"
        f"❌ Lost all attempts ({max_gales} gales)\n"
        f"💸 *Total Loss:* -${amount:.2f}"
    )
    logger.error(msg.replace('*', '').replace('\n', ' | '))
    return {"success": False, "pnl": -amount, "gale": max_gales, "message": msg}