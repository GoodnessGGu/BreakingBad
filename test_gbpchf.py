from iqclient import IQOptionAlgoAPI
import time
import logging

# Configure basic logging to see API steps
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')



def main():
    asset = input("Enter asset to test: ").upper()

    print(f"--- Testing {asset} Availability ---")
    api = IQOptionAlgoAPI()
    
    print("Connecting...")
    if api.connect():
        print("✅ Connected!")
        
        # Give a moment for initial data sync (optional but good practice)
        time.sleep(2)
        
        
        print(f"Checking availability for {asset}...")
        
        # Check standard binary/digital availability (1 minute expiry)
        option_type = api.check_asset_availability(asset, expiry=1, is_seconds=False)
        
        if option_type:
            print(f"✅ SUCCESS: {asset} is AVAILABLE for trading.")
            print(f"   Option Type: {option_type.name} (Value: {option_type.value})")
        else:
            print(f"❌ FAILURE: {asset} is currently UNAVAILABLE or CLOSED.")
            
    else:
        print("❌ Failed to connect to API.")

while True:
    if __name__ == "__main__":
        main()
