import logging
import time
from iqclient import IQOptionAlgoAPI
from models import OptionsTradeParams, Direction, OptionType

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def test_migration():
    logger.info("🚀 Starting Migration Test...")
    
    # 1. Initialize API
    try:
        api = IQOptionAlgoAPI()
        logger.info("✅ API Initialized")
    except Exception as e:
        logger.error(f"❌ Failed to init API: {e}")
        return

    # 2. Connect
    try:
        api._connect()
        logger.info(f"✅ Connected. Session ID: {api.get_session_id()}")
    except Exception as e:
        logger.error(f"❌ Failed to connect: {e}")
        return

    # 3. Check Balance
    try:
        balance = api.get_current_account_balance()
        logger.info(f"💰 Current Balance: ${balance}")
    except Exception as e:
        logger.error(f"❌ Failed to get balance: {e}")

    # 4. Switch to Practice
    api.switch_account("practice")
    logger.info("🔄 Switched to Practice account")

    # 5. Place a Dummy Trade (Optional, verifies trade logic)
    logger.info("🧪 Attempting a test trade on EURUSD-OTC (Digital)...")
    try:
        params = OptionsTradeParams(
            asset="EURUSD-OTC", # Ensure this asset is open or change to EURUSD
            amount=1,
            direction=Direction.CALL,
            expiry=1,
            option_type=OptionType.DIGITAL_OPTION
        )
        success, result = api.execute_options_trade(params)
        
        if success:
             logger.info(f"✅ Trade Placed! Order ID: {result}")
             # Wait for outcome
             logger.info("⏳ Waiting for outcome...")
             outcome, data = api.get_trade_outcome(result, expiry=1)
             if outcome:
                 logger.info(f"✅ Trade Closed. PnL: {data.get('pl_amount')}")
             else:
                 logger.warning("⚠️ Trade timed out waiting for outcome")
        else:
             logger.error(f"❌ Trade Placement Failed: {result}")

    except Exception as e:
        logger.error(f"❌ Trade Test Failed: {e}")

if __name__ == "__main__":
    test_migration()
