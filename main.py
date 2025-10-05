import asyncio
import logging
import sys
from iqclient import IQOptionAPI
from signal_parser import load_signals, parse_signals
from datetime import datetime
from collections import defaultdict

# Configure console for emojis
sys.stdout.reconfigure(encoding="utf-8")

# Logging configuration
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(message)s",
    handlers=[
        logging.FileHandler("bot.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

PAUSED = False  # Global trading control flag


def run_trade(api, asset, direction, expiry, amount, max_gales=2):
    """
    Executes a trade (digital only) and handles up to 2 martingale attempts.
    """
    if PAUSED:
        logger.info(f"🚫 Trade skipped (bot paused): {asset} {direction.upper()}")
        return

    current_amount = amount
    for gale in range(max_gales + 1):
        success, order_id = api.execute_digital_option_trade(asset, current_amount, direction, expiry=expiry)
        if not success:
            logger.error(f"❌ Failed to place trade on {asset}")
            return

        logger.info(f"🎯 Placed trade: {asset} {direction.upper()} ${current_amount} ({expiry}m expiry)")
        pnl_ok, pnl = api.get_trade_outcome(order_id, expiry=expiry)
        balance = api.get_current_account_balance()

        if pnl_ok and pnl > 0:
            logger.info(f"✅ WIN on {asset} | Profit: ${pnl:.2f} | Balance: ${balance:.2f}")
            return
        else:
            logger.warning(f"⚠️ LOSS on {asset} (Gale {gale}) | PnL: {pnl}")
            current_amount *= 2

    logger.error(f"💀 Lost all attempts (2 gales) on {asset}")


async def process_signals(api, raw_text: str):
    signals = parse_signals(raw_text)
    if not signals:
        logger.info("⚠️ No valid signals found.")
        return

    grouped = defaultdict(list)
    for sig in signals:
        grouped[sig["time"]].append(sig)

    for sched_time in sorted(grouped.keys()):
        now = datetime.now()
        delay = (sched_time - now).total_seconds()
        if delay > 0:
            logger.info(f"⏳ Waiting {int(delay)}s until {sched_time.strftime('%H:%M')} for {len(grouped[sched_time])} signal(s)...")
            await asyncio.sleep(delay)

        logger.info(f"🚀 Executing {len(grouped[sched_time])} signal(s) at {sched_time.strftime('%H:%M')}")
        await asyncio.gather(*[
            asyncio.to_thread(run_trade, api, s["asset"], s["direction"], s["expiry"], 1)
            for s in grouped[sched_time]
        ])


async def main():
    logger.info("📡 Connecting to IQ Option API...")
    api = IQOptionAPI()
    api._connect()
    logger.info(f"✅ Connected | Balance: ${api.get_current_account_balance():.2f}")

    raw_signals = load_signals("signals.txt")
    await process_signals(api, raw_signals)


if __name__ == "__main__":
    asyncio.run(main())
