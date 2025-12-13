# channel_monitor.py
import os
import asyncio
import logging
from datetime import datetime
from telethon import TelegramClient, events
from channel_signal_parser import parse_channel_signal, is_signal_message
from iqclient import run_trade
from settings import config

logger = logging.getLogger(__name__)


class ChannelMonitor:
    """
    Monitors a Telegram channel for trading signals using Telethon.
    """
    
    def __init__(self, api_id: str, api_hash: str, channel_id: str, iq_api, notification_callback=None):
        """
        Initialize the channel monitor.
        
        Args:
            api_id: Telegram API ID
            api_hash: Telegram API Hash
            channel_id: Channel ID to monitor
            iq_api: IQ Option API instance
            notification_callback: Async function to send notifications to admin
        """
        self.api_id = api_id
        self.api_hash = api_hash
        self.channel_id = int(channel_id) if channel_id else None
        self.iq_api = iq_api
        self.notification_callback = notification_callback
        self.client = None
        self.is_running = False
        self._monitoring_task = None
        
    async def start_monitoring(self):
        """Start monitoring the channel for signals."""
        if self.is_running:
            logger.warning("⚠️ Channel monitoring is already running")
            return
        
        if not self.channel_id:
            logger.error("❌ No channel ID configured")
            return
        
        try:
            # Initialize Telethon client
            self.client = TelegramClient(
                'bot_session',
                self.api_id,
                self.api_hash
            )
            
            await self.client.start()
            logger.info("✅ Telethon client started")
            
            # Register event handler for new messages
            @self.client.on(events.NewMessage(chats=self.channel_id))
            async def handle_new_message(event):
                await self._process_message(event)
            
            self.is_running = True
            logger.info(f"📡 Started monitoring channel ID: {self.channel_id}")
            
            if self.notification_callback:
                await self.notification_callback(
                    f"📡 *Channel Monitoring Started*\n"
                    f"Monitoring channel ID: `{self.channel_id}`\n"
                    f"Waiting for signals..."
                )
            
            # Keep the client running
            await self.client.run_until_disconnected()
            
        except Exception as e:
            logger.error(f"❌ Failed to start channel monitoring: {e}")
            self.is_running = False
            if self.notification_callback:
                await self.notification_callback(f"❌ Failed to start monitoring: {e}")
    
    async def stop_monitoring(self):
        """Stop monitoring the channel."""
        if not self.is_running:
            logger.warning("⚠️ Channel monitoring is not running")
            return
        
        try:
            self.is_running = False
            
            if self.client:
                await self.client.disconnect()
                logger.info("✅ Telethon client disconnected")
            
            logger.info("📡 Stopped monitoring channel")
            
            if self.notification_callback:
                await self.notification_callback("📡 *Channel Monitoring Stopped*")
                
        except Exception as e:
            logger.error(f"❌ Failed to stop channel monitoring: {e}")
    
    def is_monitoring(self) -> bool:
        """Check if monitoring is currently active."""
        return self.is_running
    
    async def _process_message(self, event):
        """Process a new message from the channel."""
        try:
            message_text = event.message.message
            
            # Check if this is a signal message
            if not is_signal_message(message_text):
                logger.debug("Message is not a signal, ignoring")
                return
            
            logger.info("🔔 Signal detected in channel!")
            
            # Parse the signal
            signal = parse_channel_signal(message_text)
            
            if not signal:
                logger.warning("⚠️ Failed to parse signal from message")
                if self.notification_callback:
                    await self.notification_callback(
                        "⚠️ *Signal Detected but Failed to Parse*\n"
                        f"Message: {message_text[:200]}..."
                    )
                return
            
            # Notify admin about detected signal
            if self.notification_callback:
                entry_time_str = signal['time'].strftime('%I:%M %p')
                await self.notification_callback(
                    f"🔔 *New Signal Detected!*\n\n"
                    f"📊 Pair: `{signal['pair']}`\n"
                    f"📈 Direction: *{signal['direction']}*\n"
                    f"⏰ Entry Time: {entry_time_str}\n"
                    f"⏳ Expiry: {signal['expiry']} minutes\n\n"
                    f"Trade will be executed automatically..."
                )
            
            # Schedule and execute the trade
            await self._execute_signal(signal)
            
        except Exception as e:
            logger.error(f"❌ Error processing channel message: {e}")
            if self.notification_callback:
                await self.notification_callback(f"❌ Error processing signal: {e}")
    
    async def _execute_signal(self, signal):
        """Execute a trade based on the parsed signal."""
        try:
            from timezone_utils import now
            
            # Check if bot is paused
            if config.paused:
                logger.info("⏸️ Bot is paused, skipping trade execution")
                if self.notification_callback:
                    await self.notification_callback("⏸️ Trade skipped - Bot is paused")
                return
            
            # Calculate delay until entry time
            current_time = now()
            delay = (signal['time'] - current_time).total_seconds()
            
            if delay > 0:
                logger.info(f"⏳ Waiting {int(delay)}s until {signal['time'].strftime('%H:%M')} to execute trade")
                if self.notification_callback:
                    await self.notification_callback(
                        f"⏳ Waiting {int(delay)}s until {signal['time'].strftime('%I:%M %p')} to enter trade..."
                    )
                await asyncio.sleep(delay)
            
            # Execute the trade
            logger.info(f"🚀 Executing trade: {signal['pair']} {signal['direction']}")
            
            # Create notification callback for trade execution
            async def trade_notification(msg):
                if self.notification_callback:
                    await self.notification_callback(msg)
            
            # Run the trade with martingale
            result = await run_trade(
                self.iq_api,
                signal['pair'],
                signal['direction'],
                signal['expiry'],
                config.trade_amount,
                notification_callback=trade_notification
            )
            
            # Notify about trade entry
            if self.notification_callback:
                entry_msg = (
                    f"✅ *Trade Entered!*\n\n"
                    f"📊 Asset: `{signal['pair']}`\n"
                    f"📈 Direction: *{signal['direction']}*\n"
                    f"💰 Amount: ${config.trade_amount}\n"
                    f"⏳ Expiry: {signal['expiry']}m\n"
                    f"🕒 Entry Time: {datetime.now().strftime('%I:%M:%S %p')}"
                )
                await self.notification_callback(entry_msg)
            
        except Exception as e:
            logger.error(f"❌ Failed to execute signal: {e}")
            if self.notification_callback:
                await self.notification_callback(f"❌ Failed to execute trade: {e}")
