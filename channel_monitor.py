
import asyncio
import logging
import pytz
from datetime import datetime, timedelta
from telethon import TelegramClient, events
from settings import config, TIMEZONE_AUTO
from iqclient import run_trade
from signal_parser import parse_signals_from_text

logger = logging.getLogger(__name__)

class ChannelMonitor:
    def __init__(self, api_id, api_hash, api_instance):
        self.api_id = api_id
        self.api_hash = api_hash
        self.api_instance = api_instance
        self.client = None # Lazy load
        self.is_running = False

    async def start(self, channel_identifier):
        """Starts the Telegram client and begins monitoring."""
        if self.is_running:
            return
        
        # Initialize client here, inside the loop
        if not self.client:
             self.client = TelegramClient('bot_session', self.api_id, self.api_hash)
        
        # specific handling for numeric IDs passed as strings
        
        # specific handling for numeric IDs passed as strings
        try:
            if channel_identifier.lstrip('-').isdigit():
                channel_identifier = int(channel_identifier)
        except ValueError:
            pass # Keep as string if not int

        try:
            await self.client.start()
            logger.info(f"📡 Monitor connected to Telegram (Listening: {channel_identifier})")
            
            @self.client.on(events.NewMessage(chats=channel_identifier))
            async def handler(event):
                await self._handle_new_message(event)

            self.is_running = True
            logger.info(f"✅ Auto-Monitor active for {channel_identifier} (TZ: {TIMEZONE_AUTO})")
            await self.client.run_until_disconnected()
        except Exception as e:
            logger.error(f"❌ Monitor Error: {e}")
            self.is_running = False

    async def _handle_new_message(self, event):
        text = event.message.text
        if not text:
            return

        logger.info(f"📨 Auto-Signal Received: {text[:30]}...")
        signals = parse_signals_from_text(text)
        
        if not signals:
            return

        tz = pytz.timezone(TIMEZONE_AUTO)
        now_tz = datetime.now(tz)

        for sig in signals:
            try:
                hh, mm = map(int, sig["time"].split(":"))
                sched_time = now_tz.replace(hour=hh, minute=mm, second=0, microsecond=0)
                
                # If time passed, assume next day
                if sched_time < now_tz:
                    sched_time += timedelta(days=1)

                delay = (sched_time - now_tz).total_seconds()
                
                logger.info(f"⏳ Scheduled Auto-Trade: {sig['pair']} {sig['direction']} in {int(delay)}s")
                
                # Execute after delay
                asyncio.create_task(self._delayed_trade(sig, delay))
            except Exception as e:
                logger.error(f"Error scheduling auto-trade: {e}")

    async def _delayed_trade(self, sig, delay):
        if delay > 0:
            await asyncio.sleep(delay)
        
        logger.info(f"🚀 Executing Auto-Trade: {sig['pair']}")
        await run_trade(
            self.api_instance, 
            sig['pair'], 
            sig['direction'], 
            sig['expiry'], 
            config.trade_amount
        )

    def stop(self):
        self.is_running = False
        if self.client:
            asyncio.create_task(self.client.disconnect())
