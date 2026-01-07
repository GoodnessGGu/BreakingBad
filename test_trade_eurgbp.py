from iqclient import IQOptionAlgoAPI
from utilities import run_trade
from models import OptionType
import logging
import time

# Configure logging to see our Dynamic ID debug prints
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def test_trade():
    asset = "EURGBP-OTC"
    expiry = 2 # Minutes (Should force Binary/Turbo)
    amount = 1
    action = "call"

    print(f"\n--- Starting Test Trade for {asset} ({expiry} min) ---")
    
    # 1. Initialize & Connect
    api = IQOptionAlgoAPI()
    print("Connecting to IQ Option...")
    if not api.connect():
        print("Connection Failed")
        return

    print("Connected!")
    
    # 2. Check Availability (Mirroring Bot Logic)
    print(f"\nChecking availability for {asset}...")
    
    # Debug: Inspect Digital List content
    try:
        digitals = api.market_manager.get_underlying_assests('digital-option')
        print(f"Found {len(digitals)} digital assets.")
        for d in digitals[:3]: # Print first 3
             print(f"Digital Item Keys: {list(d.keys())}")
             if d.get('name') == asset:
                 print(f"FOUND {asset} in Digital! Content: {d}")
                 break
    except Exception as e:
        print(f"Error inspecting digitals: {e}")

    option_type = api.check_asset_availability(asset, expiry=expiry, is_seconds=False)
    
    if not option_type:
        print(f"{asset} is CLOSED/UNAVAILABLE")
        return

    print(f"{asset} is OPEN. Option Type: {option_type}")

    # 3. Dynamic ID Verification (Manual check before trade)
    print("\nVerifying Market Manager Data...")
    try:
        # Force a lookup to see what ID we get
        d_id = api.trade_manager.get_asset_id(asset)
        print(f"Resolved ID for {asset}: {d_id}")
    except Exception as e:
        print(f"Manual ID Lookup Failed: {e}")

    # 4. Execute Trade using run_trade (exact function used by bot)
    print(f"\nAttempting to place trade: {action.upper()} ${amount}...")
    
    # Note: run_trade handles retry/gale, but we just want 1 attempt usually
    result = run_trade(
        api=api,
        asset=asset,
        direction=action,
        expiry=expiry,
        amount=amount,
        max_gales=0, # No martingale for test
        option_type=option_type
    )

    print("\nExecution Result:")
    print(result)

    if result.get('success'):
        print("TEST PASSED: Trade executed successfully.")
    else:
        print("TEST FAILED: Trade execution failed.")

if __name__ == "__main__":
    test_trade()
