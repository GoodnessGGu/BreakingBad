# channel_signal_parser.py
import re
import logging
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


def parse_channel_signal(message_text: str):
    """
    Parses a signal from the Telegram channel format.
    
    Expected format:
    🔔 NEW SIGNAL!
    🎫 Trade: 🇦🇺 AUD/JPY 🇯🇵 (OTC)
    ⏳ Timer: 5 minutes
    ➡️ Entry: 12:36 PM
    📈 Direction: BUY 🟩
    
    Returns a dictionary with:
    - time: datetime object for scheduled execution
    - pair: currency pair (e.g., "AUDJPY")
    - direction: "CALL" or "PUT"
    - expiry: expiration time in minutes
    """
    try:
        # Extract trade pair
        trade_match = re.search(r'Trade:\s*.*?([A-Z]{3})/([A-Z]{3})', message_text)
        if not trade_match:
            logger.warning(f"Could not extract trade pair from message")
            return None
        
        pair = trade_match.group(1) + trade_match.group(2)  # e.g., "AUDJPY"
        
        # Extract timer (expiry)
        timer_match = re.search(r'Timer:\s*(\d+)\s*minute', message_text, re.IGNORECASE)
        if not timer_match:
            logger.warning(f"Could not extract timer from message")
            return None
        
        expiry = int(timer_match.group(1))
        
        # Extract entry time
        entry_match = re.search(r'Entry:\s*(\d{1,2}):(\d{2})\s*(AM|PM)', message_text, re.IGNORECASE)
        if not entry_match:
            logger.warning(f"Could not extract entry time from message")
            return None
        
        hour = int(entry_match.group(1))
        minute = int(entry_match.group(2))
        am_pm = entry_match.group(3).upper()
        
        # Convert to 24-hour format
        if am_pm == "PM" and hour != 12:
            hour += 12
        elif am_pm == "AM" and hour == 12:
            hour = 0
        
        # Create datetime object for entry time
        now = datetime.now()
        entry_time = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        
        # If the entry time is in the past, assume it's for tomorrow
        if entry_time < now:
            entry_time += timedelta(days=1)
        
        # Extract direction
        direction_match = re.search(r'Direction:\s*(BUY|SELL)', message_text, re.IGNORECASE)
        if not direction_match:
            logger.warning(f"Could not extract direction from message")
            return None
        
        direction_raw = direction_match.group(1).upper()
        
        # Convert BUY/SELL to CALL/PUT
        direction = "CALL" if direction_raw == "BUY" else "PUT"
        
        signal = {
            "time": entry_time,
            "pair": pair,
            "direction": direction,
            "expiry": expiry
        }
        
        logger.info(f"✅ Parsed channel signal: {pair} {direction} @ {entry_time.strftime('%H:%M')} ({expiry}m)")
        return signal
        
    except Exception as e:
        logger.error(f"❌ Failed to parse channel signal: {e}")
        logger.debug(f"Message text: {message_text}")
        return None


def is_signal_message(message_text: str) -> bool:
    """
    Checks if a message contains a trading signal.
    Returns True if the message appears to be a signal.
    """
    if not message_text:
        return False
    
    # Check for key indicators of a signal message
    indicators = [
        "NEW SIGNAL",
        "Trade:",
        "Timer:",
        "Entry:",
        "Direction:"
    ]
    
    return all(indicator in message_text for indicator in indicators)
