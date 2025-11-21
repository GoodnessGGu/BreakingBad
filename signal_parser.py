# signal_parser.py
import re
import logging

logger = logging.getLogger(__name__)

# --- Core Signal Parsing Logic ---
def clean_signal_line(line: str) -> str:
    """
    Cleans up a raw signal line by fixing common format issues:
    - Converts letter 'O' or 'o' to zero '0' in time.
    - Replaces 'i' or other invalid separators with ';'.
    - Removes extra spaces.
    - Normalizes structure like 00:15;EURUSD;CALL;5
    """
    # Fix common typos and separators
    line = line.strip()
    line = line.replace("i", ";")
    line = re.sub(r"[Oo](\d)", r"0\1", line)   # O1:05 -> 01:05
    line = re.sub(r"[Oo]\s*:", "0:", line)     # O :05 -> 0:05
    line = re.sub(r"\s+", "", line)            # remove stray spaces

    # Ensure we have semicolons in right places
    if ";" not in line:
        # Try to guess separator positions (time;pair;direction;amount)
        parts = re.findall(r"(\d{1,2}:\d{2}|[A-Z]{6}|CALL|PUT|\d+)", line)
        line = ";".join(parts)

    return line


def parse_signal(line: str):
    """
    Parses a single cleaned signal line into a dictionary.
    Expected format: HH:MM;PAIR;DIRECTION;TIMEFRAME
    Example: 03:40;EURAUD;CALL;5
    """
    try:
        cleaned = clean_signal_line(line)
        parts = cleaned.split(";")

        if len(parts) < 4:
            logger.warning(f"Skipping invalid signal format: {line}")
            return None

        time_str = parts[0].strip()
        pair = parts[1].upper().replace("/", "")
        direction = parts[2].upper()
        expiry = int(re.sub(r"\D", "", parts[3]))  # Extract numeric part safely

        # Validate structure
        if not re.match(r"^\d{1,2}:\d{2}$", time_str):
            logger.warning(f"Invalid time format: {time_str}")
            return None

        if direction not in ["CALL", "PUT"]:
            logger.warning(f"Invalid direction: {direction}")
            return None

        return {
            "time": time_str,
            "pair": pair,
            "direction": direction,
            "expiry": expiry
        }
    except Exception as e:
        logger.error(f"❌ Failed to parse line '{line}': {e}")
        return None


# --- Parse Signals from Text ---
def parse_signals_from_text(text: str):
    """
    Parses multiple signals from a text string.
    The text can contain multiple signals in one line.
    """
    signals = []
    # Split the text by what looks like a time pattern, but keep the delimiter
    parts = re.split(r'(\d{1,2}:\d{2})', text)
    
    # The first part is usually empty or garbage, so skip it.
    # Then, we have pairs of [time, rest_of_signal]
    for i in range(1, len(parts), 2):
        time_str = parts[i]
        signal_body = parts[i+1]
        
        # Reconstruct the signal line
        line = time_str + signal_body
        
        # Now parse the single line
        sig = parse_signal(line)
        if sig:
            signals.append(sig)

    logger.info(f"✅ Parsed {len(signals)} signals from text input.")
    return signals


# --- Parse Signals from File ---
def parse_signals_from_file(filepath: str):
    """
    Reads a signal file (usually .txt) and parses all valid signals.
    """
    signals = []
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            lines = f.readlines()
        for line in lines:
            sig = parse_signal(line)
            if sig:
                signals.append(sig)
        logger.info(f"✅ Parsed {len(signals)} signals from {filepath}.")
    except Exception as e:
        logger.error(f"❌ Failed to parse signal file: {e}")
    return signals