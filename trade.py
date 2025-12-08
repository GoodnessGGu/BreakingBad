import asyncio
import time
import logging
from datetime import datetime, timezone
from options_assests import UNDERLYING_ASSESTS
from utilities import get_expiration, get_remaining_secs

logger = logging.getLogger(__name__)


# Custom exceptions for better error categorization
class TradeExecutionError(Exception):
    """Base exception for trade execution errors"""
    pass


class InvalidTradeParametersError(TradeExecutionError):
    """Raised when trade parameters are invalid"""
    pass


class TradeManager:
    """
    Manages IQOption trading operations
    
    Handles trade parameter validation, order execution, confirmation waiting,
    and trade outcome tracking.
    """
    def __init__(self, websocket_manager, message_handler, account_manager):
        self.ws_manager = websocket_manager
        self.message_handler = message_handler
        self.account_manager = account_manager

    def get_asset_id(self, asset_name: str) -> int:
        if asset_name in UNDERLYING_ASSESTS:
            return UNDERLYING_ASSESTS[asset_name]
        raise KeyError(f'{asset_name} not found!')

    # ========== DIGITAL OPTIONS ==========
    async def _execute_digital_option_trade(self, asset:str, amount:float, direction:str, expiry:int=1):
        try:
            direction = direction.lower()
            self._validate_options_trading_parameters(asset, amount, direction, expiry)

            direction_map = {'put': 'P', 'call': 'C'}        
            direction_code = direction_map[direction]

            from random import randint
            request_id = str(randint(0, 100000))

            msg = self._build_options_body(asset, amount, expiry, direction_code)
            self.ws_manager.send_message("sendMessage", msg, request_id)

            return await self.wait_for_order_confirmation(request_id, expiry)
            
        except (InvalidTradeParametersError, TradeExecutionError, KeyError) as e:
            logger.error(f"Trade execution failed: {e}")
            return False, str(e)
        except Exception as e:
            logger.error(f"Unexpected error during trade execution: {e}", exc_info=True)
            return False, f"Unexpected error: {str(e)}"

    async def wait_for_order_confirmation(self, request_id:int, expiry:int, timeout:int=10):
        start_time = time.time()
        while time.time() - start_time < timeout:
            result = self.message_handler.open_positions['digital_options'].get(request_id)
            if result is not None:
                if isinstance(result, int):
                    expires_in = get_remaining_secs(self.message_handler.server_time, expiry)
                    logger.info(f'Order Executed Successfully, Order ID: {result}, Expires in: {expires_in} Seconds')
                    return True, result
                else:
                    logger.error(f'Order Execution Failed, Reason: !!! {result} !!!')
                    return False, result
            await asyncio.sleep(0.1)
                
        logger.error(f"Order Confirmation timed out after {timeout} seconds")
        return False, "Order confirmation timed out"

    def _build_options_body(self, asset: str, amount: float, expiry: int, direction: str) -> str:
        active_id = str(self.get_asset_id(asset))
        expiration = get_expiration(self.message_handler.server_time, expiry)
        date_formatted = datetime.fromtimestamp(expiration, timezone.utc).strftime("%Y%m%d%H%M")

        instrument_id = f"do{active_id}A{date_formatted[:8]}D{date_formatted[8:]}00T{expiry}M{direction}SPT"

        return {
            "name": "digital-options.place-digital-option",
            "version": "3.0",
            "body": {
                "user_balance_id": int(self.account_manager.current_account_id),
                "instrument_id": str(instrument_id),
                "amount": str(amount),
                "asset_id": int(active_id),
                "instrument_index": 0,
            }
        }
    
    # ========== PARAM VALIDATION ==========
    def _validate_options_trading_parameters(self, asset: str, amount: float, direction: str, expiry: int) -> None:
        if not isinstance(asset, str) or not asset.strip():
            raise InvalidTradeParametersError("Asset name cannot be empty")
        if not isinstance(amount, (int, float)) or amount < 1:
            raise InvalidTradeParametersError(f"Minimum Bet Amount is $1, got: {amount}")
        direction = direction.lower().strip()
        if direction not in ['put', 'call']:
            raise InvalidTradeParametersError(f"Direction must be 'put' or 'call', got: {direction}")
        if not isinstance(expiry, int) or expiry < 1:
            raise InvalidTradeParametersError(f"Expiry must be positive integer, got: {expiry}")
        if not self.account_manager.current_account_id:
            raise TradeExecutionError("No active account available")
            
    # ========== TRADE OUTCOME ==========
    async def get_trade_outcome(self, order_id: int, expiry:int=1):
        start_time = time.time()
        timeout = get_remaining_secs(self.message_handler.server_time, expiry)

        while time.time() - start_time < timeout + 3:
            order_data = self.message_handler.position_info.get(order_id, {})
            if order_data and order_data.get("status") == "closed":
                pnl = order_data.get('pnl', 0)
                result_type = "WIN" if pnl > 0 else "LOSS"
                logger.info(f"Trade closed - Order ID: {order_id}, Result: {result_type}, PnL: ${pnl:.2f}")
                return True, pnl
            await asyncio.sleep(.5)

        return False, None
