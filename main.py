# main.py
import sys
import logging
import asyncio
import re
from datetime import datetime, date
from collections import defaultdict
from iqclient import IQOptionAPI  # using your renamed IQOptionAPI

# Configure console to support emojis
sys.stdout.reconfigure(encoding="utf-8")

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format="%(message)s"
)
logger = logging.getLogger(__name__)


def load_signals(file_path="signals.txt"):
    """
    Load raw signals text from a file.
    """
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return f.read().strip()
    except FileNotFoundError:
        logger.warning(f"⚠️ No signals file found at {file_path}")
        return ""


def parse_signals(text: str):
    """
    Parse signals from format: HH:MM;ASSET;DIRECTION;EXPIRY
    Example: 06:15;EURUSD;CALL;5
    """
    signals = []
    pattern = re.compile(r"(\d{2}:\d{2});([A-Z]+);(CALL|PUT);(\d+)", re.IGNORECASE)

    for line in text.splitlines():
        m = pattern.search(line.strip())
        if not m:
            continue
        time_str, asset, direction, expiry = m.groups()
        hh, mm = map(int, time_str.split(":"))
        scheduled_dt = datetime.combine(date.today(), datetime.min.time()).replace(
            hour=hh, minute=mm, second=0
        )
        signals.append({
            "time": scheduled_dt,
            "asset": asset,
            "direction": direction.lower(),
            "expiry": int(expiry),
            "line": line.strip(),
        })
    return sorted(signals, key=lambda x: x["time"])


def run_trade(api, asset, direction, expiry, amount, max_gales=2):
    """
    Place trade with digital only, run martingale loop.
    This runs in a separate thread when launched from asyncio.
    """
    current_amount = amount
    for gale in range(max_gales + 1):
        # Try digital trade
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
            if gale == 0:
                logger.info(
                    f"✅ Direct WIN on {asset} | Profit: ${pnl:.2f} | Balance: ${balance:.2f}"
                )
            else:
                logger.info(
                    f"🔥 WIN after Gale {gale} on {asset} | Profit: ${pnl:.2f} | Balance: ${balance:.2f}"
                )
            return
        else:
            logger.warning(
                f"⚠️ LOSS on {asset} | Attempt {gale} | PnL: {pnl} | Balance: ${balance:.2f}"
            )
            current_amount *= 2  # Martingale

    logger.error(f"💀 Lost all attempts (Direct + {max_gales} Gales) on {asset}")


async def process_signals(api, raw_text: str):
    """
    Process raw signals (from text or file) and run trades in parallel by schedule.
    """
    signals = parse_signals(raw_text)
    if not signals:
        logger.info("No valid signals found.")
        return

    # Group signals by scheduled time
    grouped = defaultdict(list)
    for sig in signals:
        grouped[sig["time"]].append(sig)

    # Process groups in chronological order
    for sched_time in sorted(grouped.keys()):
        now = datetime.now()
        delay = (sched_time - now).total_seconds()
        if delay > 0:
            logger.info(
                f"\n⏳ Waiting {int(delay)}s until {sched_time.strftime('%H:%M')} "
                f"for {len(grouped[sched_time])} signal(s)..."
            )
            await asyncio.sleep(delay)

        logger.info(
            f"\n🚀 Executing {len(grouped[sched_time])} signal(s) scheduled at {sched_time.strftime('%H:%M')}"
        )
        for sig in grouped[sched_time]:
            logger.info(f"📊 Signal: {sig['line']}")

        # Fire all trades immediately in parallel
        await asyncio.gather(
            *(
                asyncio.to_thread(
                    run_trade, api, sig["asset"], sig["direction"], sig["expiry"], 1
                )
                for sig in grouped[sched_time]
            )
        )


async def main():
    print("\n📡 Initializing API and Establishing Connection")
    api = IQOptionAPI()
    api._connect()
    logger.info("Connected ✅")
    logger.info(f"Starting balance: ${api.get_current_account_balance()}")

    # Load signals from file by default (can be replaced with Telegram input later)
    raw_signals = load_signals("signals.txt")
    await process_signals(api, raw_signals)

    logger.info("\n✅ All signals processed.")
    logger.info(f"Final balance: ${api.get_current_account_balance()}")


if __name__ == "__main__":
    asyncio.run(main())
