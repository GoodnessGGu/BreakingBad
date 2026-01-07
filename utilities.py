import logging
import time
from collections import defaultdict
import re
import asyncio
from datetime import datetime, timedelta, date, timezone
from typing import Optional

from settings import TIMEZONE_OFFSET
from models import OptionType, Direction, OptionsTradeParams
import iqclient # For type hinting if needed

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


def parse_signals(text: str):
    """
    Parse signals from format: HH:MM;ASSET;DIRECTION;EXPIRY
    Example: 
      06:15;EURUSD;CALL;5  (Default minutes)
      06:15;EURUSD;CALL;S5 (Seconds/Blitz)
      06:15 - EURUSD CALL M5
      06:15 - EURUSD CALL S5
    """
    signals = []
    # Pattern 1: Semicolon separated
    # Supports 5, M5, S5
    pattern1 = re.compile(r"(\d{2}:\d{2});([A-Z0-9\-/]+);(CALL|PUT);([MS]?\d+)", re.IGNORECASE)
    
    # Pattern 2: Dash/Space separated
    pattern2 = re.compile(r"(\d{1,2}:\d{2})\s*-\s*([A-Z0-9\-/]+)\s+(CALL|PUT)\s+([MS]\d+)", re.IGNORECASE)

    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
            
        m = pattern1.search(line)
        if not m:
            m = pattern2.search(line)
            
        if not m:
            continue
            
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
            # Default to minutes if just number
            is_seconds = False
            expiry_val = int(expiry_str)

        tz = timezone(timedelta(hours=TIMEZONE_OFFSET))
        now_in_tz = datetime.now(tz)
        target_date = now_in_tz.date()
        
        scheduled_dt = datetime.combine(target_date, datetime.min.time()).replace(
            hour=hh, minute=mm, second=0, tzinfo=tz
        )
        
        signals.append({
            "time": scheduled_dt,
            "asset": asset,
            "direction": direction.lower(),
            "expiry": expiry_val,
            "is_seconds": is_seconds, # New flag
            "line": line,
        })
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