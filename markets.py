import time
# import pandas as pd
# import mplfinance as mpf
from enum import Enum
import logging
from typing import List, Dict
from options_assests import UNDERLYING_ASSESTS

logger = logging.getLogger(__name__)


class InstrumentType(Enum):
    """Trading instrument types supported by IQOption API."""
    FOREX = 'forex'
    CFD = 'cfd'
    CRYPTO = 'crypto'
    DIGITAL_OPTION = 'digital-option'
    BINARY_OPTION = 'binary-option'

class MarketManager:
    """
    Manages IQOption market data operations including candle history, asset management etc.
    
    Handles historical/real-time candle data, live chart plotting with threading,
    asset ID lookups, and WebSocket message processing.
    """
    def __init__(self, websocket_manager, message_handler):
        self.ws_manager = websocket_manager
        self.message_handler = message_handler
    
    def get_asset_id(self, asset_name: str) -> int:
        """
        Get numeric asset ID for trading asset name.
        """
        if asset_name in UNDERLYING_ASSESTS:
            return UNDERLYING_ASSESTS[asset_name]
        raise KeyError(f'{asset_name} not found!')
    
    def get_candle_history(self, asset_name: str, count: int = 50, timeframe: int = 60):
        """
        Get historical candle data for an asset
        """

        # Reset state and prepare request
        self.message_handler.candles = None
        
        name = "sendMessage"
        msg = {
            "name": "get-candles",
            "version": "2.0",
            "body": {
                "active_id": self.get_asset_id(asset_name),
                "size": timeframe,
                "count": count,
                "to": self.message_handler.server_time,
                "only_closed": False,
                "split_normalization": True
            }
        }
        
        self.ws_manager.send_message(name, msg)
        
        # Wait for response
        while self.message_handler.candles is None:
            time.sleep(0.1)

        return self.message_handler.candles
    
    # def plot_candles(self, candles_data=None):
    #     """
    #     Display candlestick chart using mplfinance.
    #     """
    #     pass # Dependency removed
    
    # def save_candles_to_csv(self, candles_data=None, filename: str = 'candles'):
    #     """
    #     Export candle data to CSV file.
    #     """
    #     pass # Dependency removed

    def _build_msg_body(self, instrument_type:str):
        """
        Construct WebSocket message body for different instrument types.
        """
        if instrument_type == 'digital-option':
            msg = {
                "name": "digital-option-instruments.get-underlying-list",
                "version": "3.0",
                "body": {
                    "filter_suspended": True
                }
            }
        elif instrument_type == 'binary-option':
            msg= {
                'body':{},
                'name':'get-initialization-data',
                'version':'4.0'
            }
        elif instrument_type in ['forex', 'cfd', 'crypto']:
            msg = {
                'body':{},
                'version':'1.0',
                'name':f'marginal-{instrument_type}-instruments.get-underlying-list'
            }

        return msg
    
    def get_underlying_assests(self, instrument_type:str):
        """
        Retrieve list of available underlying assets for a specific instrument type.
        """

        # Validate instrument type against enum values
        valid_types = {instrument.value for instrument in InstrumentType}
        if instrument_type not in valid_types:
            raise ValueError(f"Unsupported instrument type: {instrument_type}. "
                           f"Must be one of: {', '.join(valid_types)}")

        # Reset state to ensure fresh data
        self.message_handler._underlying_assests = None

        self.ws_manager.send_message('sendMessage', self._build_msg_body(instrument_type))

        # Wait for response (blocking operation)
        while self.message_handler._underlying_assests == None:
            time.sleep(.1)

        return self.message_handler._underlying_assests


    def save_underlying_assests_to_file(self):
        """        
        Retrieves assets from multiple instrument types, filters out
        suspended instruments, and generates two separate Python files containing
        asset dictionaries for easy import and use.
        """

        # Initialize storage dictionaries
        options_underlying_assets = {}
        marginal_underlying_assets = {}

        # Get underlying assets for marginal trading instruments (forex, CFD, crypto)
        for instrument in ['forex', 'cfd', 'crypto']:
            underlying_list = self.get_underlying_assests(instrument)
            for item in underlying_list:
                if item['is_suspended'] == False:
                    marginal_underlying_assets[item['name']] = item['active_id']

        # Get underlying assets for digital options
        digital_underlying = self.get_underlying_assests('digital-option')

        # Get underlying assets for binary options
        initialization_data = self.get_underlying_assests('binary-option')

        # Filters out suspended asset and add to options_underlying_assets
        for assest in digital_underlying:
            if assest['is_suspended'] == False:
                options_underlying_assets[assest['name']] = assest['active_id']

        instruments = ['binary', 'blitz', 'turbo']
        for instrument in instruments:
            if instrument in initialization_data:
                for _, value  in initialization_data[instrument]['actives'].items():
                    if value['is_suspended'] == False:
                        options_underlying_assets[value['ticker']] = value['id']

        # Export to separate files for different trading types
        self._export_assets_to_fiel(options_underlying_assets, 'options_assests.py')
        self._export_assets_to_fiel(marginal_underlying_assets, 'marginal_assests.py')

    def _export_assets_to_fiel(self, data:dict, file:str) -> None:
        """        
        Creates a Python file containing a dictionary of assets
        sorted by ID, with proper formatting for easy import and readability.
        """

        # Sort assets by ID for consistent file output
        data = dict(sorted(data.items(), key=lambda item:item[-1]))

        # Write formatted Python file
        with open(f'{file}', 'w') as file:
            file.write('#Auto-Generated Underlying List\n')
            file.write('UNDERLYING_ASSESTS = {\n')
            for key,value in data.items():
                file.write(f"   '{key}':{value},\n")
            file.write('}\n')

    def subscribe_candles(self, asset_name: str, timeframe: int = 60, plot_timeout: int = None):
        """
        Subscribe to real-time candle data with live plotting capability.
        """
        
        # Subscribe to real-time candle data via WebSocket
        self.ws_manager.send_message('subscribeMessage', {
            'name': 'candle-generated',
            'params': {
                'routingFilters': {
                    'active_id': self.get_asset_id(asset_name),
                    'size': timeframe
                }
            }
        })

    def _convert_to_dataframe(self, candles_data: List[Dict]):
        """Convert candle data to pandas DataFrame."""
        return [] # Dependency removed