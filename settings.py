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
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID') or os.getenv('ADMIN_ID') # Optional, for restricting access

# Userbot Settings
TELEGRAM_API_ID = os.getenv('TELEGRAM_API_ID')
TELEGRAM_API_HASH = os.getenv('TELEGRAM_API_HASH')
# Channels to listen to (comma separated or single)
SIGNAL_CHANNELS = [
    int(x) for x in [
        os.getenv('CHANNEL_ID_1'), 
        os.getenv('CHANNEL_ID_2'), 
        os.getenv('SIGNAL_CHANNEL_ID')
    ] if x
]


DEFAULT_ACCOUNT_TYPE = 'demo' # REAL/DEMO/real/demo

# Constants for account types
ACCOUNT_REAL = 1
ACCOUNT_TOURNAMENT = 2
ACCOUNT_DEMO = 4
ACCOUNT_CFD = 6

# Trading Timezone Offset (Default UTC-3, configurable via .env)
TIMEZONE_OFFSET = int(os.getenv('TIMEZONE_OFFSET', -3))
