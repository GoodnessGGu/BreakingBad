import json
import logging

logger = logging.getLogger(__name__)

class MessageHandler:
    def __init__(self):
        self.server_time = None
        self.profile_msg = None
        self.balance_data = None
        self.candles = None
        self.underlying_list = None
        self.initialization_data = None
        self._underlying_assests = None
        self.hisory_positions = None
        self.open_positions = {
            'digital_options': {},
            'binary_options': {}
        }
        self.position_info = {}

    def handle_message(self, message):
        message_name = message.get('name')
        handlers = {
            'profile': self._handle_profile,
            'candles': self._handle_candles,
            'balances': self._handle_balances,
            'timeSync': self._handle_server_time,
            'underlying-list': self._handle_underlying_list,
            'initialization-data': self._handle_initialization_data,
            'training-balance-reset': self._handle_training_balance_reset,
            "history-positions": self._handle_position_history,
            "digital-option-placed": self._handle_digital_option_placed,
            "position-changed": self._handle_position_changed,
            "option-opened": self._handle_binary_option_opened,
            "option-closed": self._handle_binary_option_closed,
        }
        handler = handlers.get(message_name)
        if handler:
            handler(message)

    def _handle_server_time(self, message):
        self.server_time = message['msg']

    def _handle_profile(self, message):
        self.profile_msg = message
        balances = message['msg']['balances']
        for balance in balances:
            if balance['type'] == 4:
                self.active_balance_id = balance['id']
                break

    def _handle_balances(self, message):
        self.balance_data = message['msg']

    def _handle_training_balance_reset(self, message):
        if message['status'] == 2000:
            logger.info('Demo Account Balance Reset Successfully')
        elif message['status'] == 4001:
            logger.warning(message['msg']['message'])
        else:
            logger.info(message)

    def _handle_initialization_data(self, message):
        self._underlying_assests = message['msg']

    def _handle_candles(self, message):
        self.candles = message['msg']['candles']

    def _handle_underlying_list(self, message):
        if message['msg'].get('type', None) == 'digital-option':
            self._underlying_assests = message['msg']['underlying']
        else:
            self._underlying_assests = message['msg']['items']

    def _handle_position_history(self, message):
        self.hisory_positions = message['msg']['positions']

    def _handle_digital_option_placed(self, message):
        if message["msg"].get("id") is not None:
            self.open_positions['digital_options'][message["request_id"]] = message["msg"].get("id")
        else:
            self.open_positions['digital_options'][message["request_id"]] = message["msg"].get("message")

    def _handle_position_changed(self, message):
        self.position_info[int(message["msg"]["raw_event"]["order_ids"][0])] = message['msg']
        self._save_data(message['msg'], 'positions')

    # ================= BINARY HANDLERS =================
    def _handle_binary_option_opened(self, message):
        option_id = message["msg"].get("id")
        if option_id:
            self.open_positions['binary_options'][message["request_id"]] = option_id
        else:
            self.open_positions['binary_options'][message["request_id"]] = message["msg"].get("message")

    def _handle_binary_option_closed(self, message):
        option_id = int(message["msg"]["id"])
        self.position_info[option_id] = message["msg"]
        self._save_data(message['msg'], 'binary_positions')

    # Utility
    def _save_data(self, message, filename):
        with open(f'{filename}.json', 'w') as file:
            json.dump(message, file, indent=4)
