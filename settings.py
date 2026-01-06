#settings.py
import os
from dotenv import load_dotenv
load_dotenv()


LOGIN_URL = 'https://api.iqoption.com/v2/login'
LOGOUT_URL = "https://auth.iqoption.com/api/v1.0/logout"
WS_URL = 'wss://ws.iqoption.com/echo/websocket'


EMAIL = os.getenv('email')
PASSWORD = os.getenv('password')
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID') # Optional, for restricting access


DEFAULT_ACCOUNT_TYPE = 'demo' # REAL/DEMO/real/demo

# Constants for account types
ACCOUNT_REAL = 1
ACCOUNT_TOURNAMENT = 2
ACCOUNT_DEMO = 4
ACCOUNT_CFD = 6

# Trading Timezone Offset (UTC-3)
TIMEZONE_OFFSET = -3
