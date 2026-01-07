import logging
from iqclient import IQOptionAlgoAPI

# Setup simple logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def main():
    print("--- IQ Option Account Diagnostic ---")
    api = IQOptionAlgoAPI()
    try:
        if api.connect():
            print("Connected!")
            
            print("\nFetching Balances...")
            balances = api.account_manager.get_account_balances()
            
            print(f"\nFound {len(balances)} accounts:")
            for acc in balances:
                print(f" - ID: {acc.get('id')} | Type: {acc.get('type')} | Balance: {acc.get('amount')} {acc.get('currency')}")
                
            print("\nCurrent Local State:")
            print(f"Current ID: {api.account_manager.current_account_id}")
            print(f"Current Type: {api.account_manager.current_account_type}")
            
        else:
            print("Failed to connect.")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    main()
