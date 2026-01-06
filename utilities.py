# utilities.py
import logging
from collections import defaultdict
import re
import asyncio
from datetime import datetime, timedelta, date, timezone
from settings import TIMEZONE_OFFSET

logger = logging.getLogger(__name__)


def get_timestamps(start_str: str = None, end_str: str = None) -> tuple:
    """   
    This function creates a time range by converting datetime strings to Unix timestamps.
    If no parameters are provided, it defaults to a 24-hour range ending at the current time.
    
    Returns:
        tuple: A tuple containing (start_timestamp, end_timestamp) as integers,
               or (None, None) if an error occurs during parsing.
    
    Example:
        >>> get_timestamps("2024-01-01 00:00:00", "2024-01-01 12:00:00")
        (1704067200, 1704110400)
        
        >>> get_timestamps()  # Returns last 24 hours from now
        (1693756800, 1693843200)
    """
        
    try:
        # If no end date provided, use current time
        if end_str is None:
            end_dt = datetime.now()
        else:
            end_dt = datetime.strptime(end_str, "%Y-%m-%d %H:%M:%S")

        # If no start date provided, default to 24 hours before end time
        if start_str is None:
            start_dt = end_dt - timedelta(hours=24)
        else:
            start_dt = datetime.strptime(start_str, "%Y-%m-%d %H:%M:%S")

        # Convert datetime objects to Unix timestamps (seconds since epoch)
        return int(start_dt.timestamp()), int(end_dt.timestamp())
    except Exception as e:
        logger.error(str(e))
        logger.error('Plase make sure date is within valid range')
        return None, None


def get_expiration(timestamp:int, expiry:int=1):
    """
    Calculate expiration timestamp based on a given timestamp and expiry duration.
    
    Args:
        timestamp (int): Input timestamp in milliseconds since epoch.
        expiry (int, optional): Expiry duration in minutes. Defaults to 1.
    
    Returns:
        float: Expiration timestamp in seconds since epoch.
    
    Note:
        - The function ensures a minimum of 31 seconds between the current time
          and expiration to prevent immediate expiry.
        - Input timestamp is expected in milliseconds but output is in seconds.
    
    Example:
        >>> get_expiration(1693843200000, 5)  # 5-minute expiry
        1693843500.0
    """

    # Minimum time needed before expiration (in seconds)
    min_time_needed = 31

    # Convert timestamp from milliseconds to seconds
    timestamp = timestamp / 1000

    # Create datetime object from timestamp
    now_date = datetime.fromtimestamp(timestamp)

    # Round down to nearest minute (remove seconds and microseconds)
    # This ensures consistent expiration times on minute boundaries
    now_date_hm = now_date.replace(second=0, microsecond=0)

    # Calculate expiration based on conditions
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

    # Return expiration time as timestamp in seconds
    return expiration.timestamp()


def get_remaining_secs(timestamp, duration):
    """
    Calculate the remaining seconds until expiration for a given duration.
    
    Args:
        timestamp (int): Current timestamp in milliseconds since epoch.
        duration (int): Duration in minutes until expiration.
    
    Example:
        >>> get_remaining_secs(1693843200000, 5) # 5 minutes
        300.0 or 304.0 as seconds
    
    Note:
        This function relies on get_expiration() to calculate the actual
        expiration timestamp, which includes logic for minimum time requirements.
    """

    # Get the expiration timestamp
    expiry_ts = get_expiration(timestamp, duration)

    # Calculate remaining seconds by subtracting current time from expiration time
    return expiry_ts - int(timestamp/1000)


def parse_signals(text: str):
    """
    Parse signals from format: HH:MM;ASSET;DIRECTION;EXPIRY
    Example: 06:15;EURUSD;CALL;5
    """
    signals = []
    # Pattern for HH:MM;ASSET;DIRECTION;EXPIRY
    pattern1 = re.compile(r"(\d{2}:\d{2});([A-Z0-9\-/]+);(CALL|PUT);(\d+)", re.IGNORECASE)
    # Pattern for HH:MM - ASSET DIRECTION MX (from first_main.py style)
    pattern2 = re.compile(r"(\d{1,2}:\d{2})\s*-\s*([A-Z0-9\-/]+)\s+(CALL|PUT)\s+M(\d+)", re.IGNORECASE)

    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
            
        # Try first pattern
        m = pattern1.search(line)
        if not m:
            # Try second pattern
            m = pattern2.search(line)
            
        if not m:
            continue
            
        time_str, asset, direction, expiry = m.groups()
        hh, mm = map(int, time_str.split(":"))
        
        # Create timezone object
        tz = timezone(timedelta(hours=TIMEZONE_OFFSET))
        
        # Get today's date in target timezone
        now_in_tz = datetime.now(tz)
        target_date = now_in_tz.date()
        
        # Create timezone-aware datetime
        scheduled_dt = datetime.combine(target_date, datetime.min.time()).replace(
            hour=hh, minute=mm, second=0, tzinfo=tz
        )
        
        # Handle day rollover if needed?
        # If scheduled time is significantly in the past relative to now_in_tz, maybe it's for tomorrow?
        # But usually signals are for "today". We'll stick to target_date.
        
        signals.append({
            "time": scheduled_dt,
            "asset": asset,
             "direction": direction.lower(),
            "expiry": int(expiry),
            "line": line,
        })
    return sorted(signals, key=lambda x: x["time"])



def run_trade(api, asset, direction, expiry, amount, max_gales=2):
    """
    Place trade with digital only, run martingale loop.
    This runs in a separate thread when launched from asyncio.
    """
    current_amount = amount
    for gale in range(max_gales + 1):
        success, order_id = api.execute_digital_option_trade(
            asset, current_amount, direction, expiry=expiry
        )

        if not success:
            logger.error(f"❌ Failed to place trade on {asset} (Digital only)")
            return

        logger.info(
            f"🎯 Placed trade: {asset} {direction.upper()} ${current_amount} (Expiry {expiry}m)"
        )
        pnl_ok, pnl = api.get_trade_outcome(order_id, expiry=expiry)
        balance = api.get_current_account_balance()

        if pnl_ok and pnl > 0:
            msg = ""
            if gale == 0:
                msg = f"✅ Direct WIN on {asset} | Profit: ${pnl:.2f} | Balance: ${balance:.2f}"
                logger.info(msg)
            else:
                msg = f"🔥 WIN after Gale {gale} on {asset} | Profit: ${pnl:.2f} | Balance: ${balance:.2f}"
                logger.info(msg)
            return {"success": True, "pnl": pnl, "gale": gale, "message": msg}
        else:
            logger.warning(
                f"⚠️ LOSS on {asset} | Attempt {gale} | PnL: {pnl} | Balance: ${balance:.2f}"
            )
            current_amount *= 2  # Martingale

    msg = f"💀 Lost all attempts (Direct + {max_gales} Gales) on {asset}"
    logger.error(msg)
    return {"success": False, "pnl": -amount, "gale": max_gales, "message": msg}