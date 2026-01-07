import logging
import time
from iqclient import IQOptionAlgoAPI

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def main():
    print("--- Testing Account Switch ---")
    api = IQOptionAlgoAPI()
    try:
        if api.connect():
            print("Connected!")
            
            # 1. Get Initial State
            initial_type = api.account_manager.current_account_type
            initial_id = api.account_manager.current_account_id
            print(f"Initial: {initial_type} (ID: {initial_id})")
            
            # 2. Try Switch
            target = "real" if initial_type == "demo" else "demo"
            print(f"Attempting switch to: {target}")
            
            success = api.switch_account(target)
            print(f"Switch Result: {success}")
            
            # 3. Verify New State
            new_type = api.account_manager.current_account_type
            new_id = api.account_manager.current_account_id
            print(f"New State: {new_type} (ID: {new_id})")
            
            if new_type == target:
                print("✅ LOCAL State Updated Correctly")
            else:
                print("❌ LOCAL State DID NOT Update")
                
            # 4. Check Balance of new state
            balance = api.get_current_account_balance()
            print(f"New Balance: {balance}")
            
        else:
            print("Failed to connect.")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    main()
