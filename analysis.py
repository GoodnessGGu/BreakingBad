import logging
import statistics
from typing import List, Dict

logger = logging.getLogger(__name__)

def calculate_rsi(prices: List[float], period: int = 14) -> float:
    """
    Calculate Rate of Strength Index (RSI) using pure Python.
    """
    if len(prices) < period + 1:
        return 50.0 # Neutral if not enough data

    gains = []
    losses = []

    for i in range(1, len(prices)):
        change = prices[i] - prices[i-1]
        gains.append(max(change, 0))
        losses.append(max(-change, 0))

    # Initial SMA
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    # Smoothed averages
    for i in range(period, len(prices)-1):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss == 0:
        return 100.0
    
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return rsi

def check_technical_indicators(api, asset: str, direction: str) -> bool:
    """
    Validate a trade signal using Technical Analysis.
    
    Logic:
    - CALL: Block if RSI > 70 (Overbought)
    - PUT: Block if RSI < 30 (Oversold)
    
    Returns:
        bool: True if safe to trade, False if should be skipped.
    """
    try:
        # Get last 20 candles (enough for RSI 14)
        candles = api.get_candle_history(asset, count=30, timeframe=60)
        
        if not candles:
            logger.warning(f"⚠️ TA: No candles found for {asset}. Skipping TA check.")
            return True # Default to allow if no data
            
        # Extract closing prices
        prices = [c['close'] for c in candles]
        
        # Calculate RSI
        rsi = calculate_rsi(prices)
        current_price = prices[-1]
        
        logger.info(f"🧠 TA Check {asset}: RSI={rsi:.2f}")

        if direction.upper() == 'CALL':
            if rsi > 70:
                logger.warning(f"🚫 TA Filter: Blocked CALL on {asset} (RSI {rsi:.2f} > 70 Overbought)")
                return False
        elif direction.upper() == 'PUT':
            if rsi < 30:
                logger.warning(f"🚫 TA Filter: Blocked PUT on {asset} (RSI {rsi:.2f} < 30 Oversold)")
                return False
                
        return True

    except Exception as e:
        logger.error(f"Error in TA check: {e}")
        return True # Fail open to avoid blocking valid trades on error
