# signal_parser.py
import re
import logging
from datetime import datetime, date

# Set up logger
logger = logging.getLogger(__name__)

def clean_line(line: str) -> str:
    """
    Clean up messy signal formats by:
    - Replacing 'i' or ',' with ';'
    - Fixing O or o to 0 in time
    - Removing extra spaces
    - Ensuring 4 components (time;asset;direction;expiry)
    """
    line = line.strip()
    if not line:
        return ""

    # Replace common bad separators and lowercase letters
    line = re.sub(r"[iI,|]", ";", line)
    line = line.replace(" ", "")
    line = line.replace("O", "0").replace("o", "0")

    # Fix malformed time like "f:" or "Of:" (replace f or F with 0)
    line = re.sub(r"^[fF]", "0", line)
    line = re.sub(r"([^\d])f:", r"\10:", line)

    # Make sure it has semicolons as separators
    parts = line.split(";")
    if len(parts) < 4:
        logger.debug(f"Skipping malformed line: {line}")
        return ""

    # Normalize fields
    time_str = parts[0].strip()
    asset = parts[1].upper().strip()
    direction = parts[2].upper().strip()
    expiry = parts[3].strip()

    return f"{time_str};{asset};{direction};{expiry}"


def _parse_signals(text: str):
    """
    Core parser for signals like:
    HH:MM;ASSET;CALL;5
    (accepts messy versions with mixed separators)
    """
    signals = []
    pattern = re.compile(r"(\d{2}:\d{2});([A-Z]+);(CALL|PUT);(\d+)", re.IGNORECASE)

    for raw_line in text.splitlines():
        line = clean_line(raw_line)
        if not line:
            continue

        match = pattern.match(line)
        if not match:
            logger.debug(f"Skipping invalid line after cleanup: {raw_line}")
            continue

        time_str, asset, direction, expiry = match.groups()
        try:
            hh, mm = map(int, time_str.split(":"))
        except ValueError:
            logger.debug(f"Invalid time in line: {line}")
            continue

        scheduled_dt = datetime.combine(date.today(), datetime.min.time()).replace(
            hour=hh, minute=mm, second=0
        )

        signal = {
            "time": scheduled_dt,
            "asset": asset,
            "direction": direction.lower(),
            "expiry": int(expiry),
            "line": line
        }
        signals.append(signal)

    logger.info(f"✅ Parsed {len(signals)} valid signals.")
    return signals


def parse_signals_from_text(text: str):
    """
    Parse signals from raw text input (e.g., from Telegram command).
    """
    if not text.strip():
        logger.warning("⚠️ Empty signal text received.")
        return []
    return _parse_signals(text)


def parse_signals_from_file(file_path: str):
    """
    Parse signals from a file uploaded via Telegram or local source.
    """
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read().strip()
            if not content:
                logger.warning(f"⚠️ File {file_path} is empty.")
                return []
            return _parse_signals(content)
    except FileNotFoundError:
        logger.error(f"❌ Signal file not found: {file_path}")
    except Exception as e:
        logger.error(f"❌ Error reading {file_path}: {e}")
    return []