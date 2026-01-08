from utilities import parse_standard_signal
from settings import TIMEZONE_OFFSET
from datetime import datetime
import logging

# Setup basic logging
logging.basicConfig(level=logging.INFO)

def test_parsing():
    # Example Standard Signal (UTC-3 implied)
    # 12:00 PM UTC-3 should be 4:00 PM (16:00) UTC+1
    raw_signal = "12:00;EURUSD;CALL;5"
    
    print(f"--- Testing Standard Signal Parsing ---")
    print(f"Input: '{raw_signal}'")
    print(f"System Config: TIMEZONE_OFFSET={TIMEZONE_OFFSET} (UTC+{TIMEZONE_OFFSET})")
    
    result = parse_standard_signal(raw_signal)
    
    if result:
        print("\n✅ Parsing Successful!")
        print(f"Asset: {result['asset']}")
        print(f"Direction: {result['direction']}")
        print(f"Expiry: {result['expiry']}")
        print(f"Parsed Time (System Local): {result['time']}")
        print(f"Original Time String: 12:00")
        
        # Verify Shift
        hour = result['time'].hour
        expected_hour = 12 + 4 # 16
        
        if hour == expected_hour:
             print(f"✅ Timezone Conversion Correct (+4 hours): 12:00 -> {hour}:00")
        else:
             print(f"❌ Timezone Conversion Mismatch: Expected 16:00, Got {hour}:00")
    else:
        print("❌ Parsing Failed")

if __name__ == "__main__":
    test_parsing()
