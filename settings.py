#settings.py
import os
from dotenv import load_dotenv
load_dotenv()


LOGIN_URL = 'https://api.iqoption.com/v2/login'
LOGOUT_URL = "https://auth.iqoption.com/api/v1.0/logout"
WS_URL = 'wss://ws.iqoption.com/echo/websocket'


EMAIL = os.getenv('email')
PASSWORD = os.getenv('password')


DEFAULT_ACCOUNT_TYPE = 'demo' # REAL/DEMO/real/demo

# Constants for account types
ACCOUNT_REAL = 1
ACCOUNT_TOURNAMENT = 2
ACCOUNT_DEMO = 4
ACCOUNT_CFD = 6



# Trade settings
DEFAULT_TRADE_AMOUNT = 1
MAX_MARTINGALE_GALES = 2
MARTINGALE_MULTIPLIER = 2

# Signal Suppression
SUPPRESS_OVERLAPPING_SIGNALS = True
PAUSED = False

class TradingConfig:
    def __init__(self):
        self.trade_amount = DEFAULT_TRADE_AMOUNT
        self.max_martingale_gales = MAX_MARTINGALE_GALES
        self.martingale_multiplier = MARTINGALE_MULTIPLIER
        self.suppress_overlapping_signals = SUPPRESS_OVERLAPPING_SIGNALS
        self.paused = PAUSED
        self.account_type = DEFAULT_ACCOUNT_TYPE

    def __str__(self):
        return (f"TradingConfig(amount={self.trade_amount}, "
                f"gales={self.max_martingale_gales}, "
                f"multiplier={self.martingale_multiplier}, "
                f"paused={self.paused}, "
                f"suppress={self.suppress_overlapping_signals}, "
                f"account={self.account_type})")

config = TradingConfig()
