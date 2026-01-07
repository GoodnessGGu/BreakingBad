import logging
import asyncio
import os
from telethon import TelegramClient, events
from dotenv import load_dotenv

# Configure Logging FIRST
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

# Configuration
API_ID = os.getenv('TELEGRAM_API_ID')
API_HASH = os.getenv('TELEGRAM_API_HASH')

# Channels to listen to
SIGNAL_CHANNELS = []
for key in ['CHANNEL_ID_1', 'CHANNEL_ID_2', 'SIGNAL_CHANNEL_ID']:
    val = os.getenv(key)
    if val:
        try:
            SIGNAL_CHANNELS.append(int(val))
        except ValueError:
            logger.warning(f"Invalid channel ID in .env for {key}: {val}")

# Target Bot to forward signals to (e.g., @MyAlgoBot)
TARGET_BOT = os.getenv('BOT_USERNAME') or os.getenv('CHANNEL_USERNAME')

if not TARGET_BOT:
    # Auto-detect using the Token (Userbot needs to know where to send)
    token = os.getenv('TELEGRAM_TOKEN')
    if token:
        import requests
        try:
             logger.info("🕵️ Auto-detecting Bot Username from Token...")
             res = requests.get(f"https://api.telegram.org/bot{token}/getMe", timeout=10).json()
             if res.get('ok'):
                 TARGET_BOT = '@' + res['result']['username']
                 logger.info(f"✅ Target Bot Identified: {TARGET_BOT}")
        except Exception as e:
             logger.warning(f"⚠️ Auto-detection failed: {e}")

if not TARGET_BOT:
    logger.error("❌ Could not determine Target Bot. Please set BOT_USERNAME in .env")
    exit(1)

if not API_ID or not API_HASH:
    logger.error("❌ API_ID or API_HASH missing in .env")
    exit(1)

client = TelegramClient('anon', API_ID, API_HASH)

@client.on(events.NewMessage(chats=SIGNAL_CHANNELS))
async def main_handler(event):
    """
    Listens for new messages in configured channels and forwards them to the bot.
    """
    sender = await event.get_sender()
    chat = await event.get_chat()
    
    chat_title = getattr(chat, 'title', 'Unknown')
    logger.info(f"📥 New Message from {chat_title} ({chat.id})")
    
    text = event.message.text
    if not text:
        return

from utilities import parse_signals

# ... imports ...

@client.on(events.NewMessage(chats=SIGNAL_CHANNELS))
async def main_handler(event):
    """
    Listens for news, parses signals, and forwards CLEAN commands to the bot.
    """
    sender = await event.get_sender()
    chat = await event.get_chat()
    chat_title = getattr(chat, 'title', 'Unknown')
    
    text = event.message.text
    if not text:
        return

    # 1. Parse the raw text using our shared utilities
    signals = parse_signals(text)
    
    if not signals:
        # No valid signals found, ignore (don't spam the bot with chatter)
        return

    logger.info(f"📥 Found {len(signals)} valid signals in message from {chat_title}")

    # 2. Format signals into Standard Command Format
    # Format: HH:MM;ASSET;DIRECTION;EXPIRY
    clean_messages = []
    for sig in signals:
        # Extract HH:MM from the datetime object (which is already in User TZ)
        time_str = sig['time'].strftime("%H:%M")
        asset = sig['asset']
        direction = sig['direction'].upper()
        
        # Handle Expiry (check if seconds or minutes)
        if sig.get('is_seconds'):
            expiry_str = f"S{sig['expiry']}"
        else:
            expiry_str = str(sig['expiry'])
            
        formatted_cmd = f"SOURCE:{chat.id};{time_str};{asset};{direction};{expiry_str}"
        clean_messages.append(formatted_cmd)

    if not clean_messages:
        return

    # 3. Join and Send
    final_payload = "\n".join(clean_messages)
    
    logger.info(f"🚀 Forwarding parsed payload to {TARGET_BOT}:\n{final_payload}")
    
    try:
        await client.send_message(TARGET_BOT, final_payload)
        logger.info("✅ Payload sent successfully.")
    except Exception as e:
        logger.error(f"❌ Failed to forward: {e}")

async def main():
    logger.info("🎧 Listener started...")
    logger.info(f"📡 Monitoring {len(SIGNAL_CHANNELS)} channels.")
    logger.info(f"🎯 Forwarding targets to: {TARGET_BOT}")
    
    await client.start()
    await client.run_until_disconnected()

if __name__ == '__main__':
    client.loop.run_until_complete(main())
