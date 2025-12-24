import hashlib
import json
import logging

import gevent
import gevent.lock
import gevent.socket as socket
import util.RH_GPIO as RH_GPIO
from gevent.queue import Queue
from RHRace import RaceStatus, WinCondition
from VRxControl import VRxController

from .connections import BackpackConnection, ConnectionTypeEnum
from .msp import MSPPacket, MSPPacketType, MSPTypes

logger = logging.getLogger(__name__)


class CancelError(BaseException): ...


class ELRSBackpack(VRxController):
    _connection: BackpackConnection | None = None

    def __init__(self, name, label, rhapi):
        super().__init__(name, label)
        self._rhapi = rhapi
        self._send_queue = Queue()
        self._recieve_queue = Queue(maxsize=100)
        self._queue_lock = gevent.lock.RLock()

    @property
    def _backpack_connected(self) -> bool:
        if self._connection is None:
            return False

        return self._connection.connected

    def get_pilot_osd_config(self, pilot_id: int) -> dict:
        """
        Get per-pilot OSD configuration. Returns empty dict if no custom config exists.

        :param pilot_id: The pilot ID
        :return: Dictionary of OSD configuration for the pilot
        """
        try:
            config_json = self._rhapi.db.pilot_attribute_value(pilot_id, 'elrs_osd_config')
            if config_json:
                return json.loads(config_json)
        except Exception as e:
            logger.warning(f"Error loading pilot {pilot_id} OSD config: {e}")
        return {}

    def get_osd_setting(self, pilot_id: int, element_id: str, setting_key: str, default_key: str, default_value=None):
        """
        Get an OSD setting for a pilot, checking per-pilot config first, then falling back to global settings.

        :param pilot_id: The pilot ID
        :param element_id: The element identifier (e.g., 'heat_name', 'race_stage')
        :param setting_key: The setting key within the element config ('row', 'alignment', 'custom_col')
        :param default_key: The global setting key to use as fallback
        :param default_value: Default value if neither per-pilot nor global setting exists
        :return: The setting value
        """
        # Check per-pilot config first
        pilot_config = self.get_pilot_osd_config(pilot_id)
        if element_id in pilot_config and setting_key in pilot_config[element_id]:
            return pilot_config[element_id][setting_key]

        # Fall back to global setting
        if default_key:
            return self._rhapi.db.option(default_key, default_value)

        return default_value

    def get_pilot_behavior_setting(self, pilot_id: int, behavior_key: str, default_value="0"):
        """
        Get a behavior setting for a pilot, checking per-pilot config first, then falling back to global settings.

        :param pilot_id: The pilot ID
        :param behavior_key: The behavior setting key (e.g., '_round_num', '_gap_mode', '_results_mode')
        :param default_value: Default value if neither per-pilot nor global setting exists
        :return: The setting value as a string ("0" or "1")
        """
        # Check per-pilot config first
        pilot_config = self.get_pilot_osd_config(pilot_id)
        if behavior_key in pilot_config:
            return "1" if pilot_config[behavior_key] else "0"

        # Fall back to global setting
        return self._rhapi.db.option(behavior_key, default_value)

    def get_pilot_string_setting(self, pilot_id: int, setting_key: str, default_value=None):
        """
        Get a string-type setting for a pilot, checking per-pilot config first, then falling back to global settings.

        :param pilot_id: The pilot ID
        :param setting_key: The setting key (e.g., '_racestage_message', '_racestart_message')
        :param default_value: Default value if neither per-pilot nor global setting exists
        :return: The setting value as a string
        """
        # Check per-pilot config first
        pilot_config = self.get_pilot_osd_config(pilot_id)
        if setting_key in pilot_config:
            return pilot_config[setting_key]

        # Fall back to global setting
        return self._rhapi.db.option(setting_key, default_value)

    def register_handlers(self, args) -> None:
        """
        Registers handlers in the RotorHazard system
        """
        args["register_fn"](self)

    def start_race(self):
        """
        Start the race
        """
        if self._rhapi.db.option("_race_start") == "1":
            start_race_args = {"start_time_s": 10}
            if self._rhapi.race.status == RaceStatus.READY:
                self._rhapi.race.stage(start_race_args)

    def stop_race(self):
        """
        Stop the race
        """
        if self._rhapi.db.option("_race_stop") == "1":
            status = self._rhapi.race.status
            if status in (RaceStatus.STAGING, RaceStatus.RACING):
                if self._rhapi.db.option("_autosave_on_stop") == "1":
                    self._rhapi.race.save()
                else:
                    self._rhapi.race.stop()

    #
    # Connection handling
    #

    def start_recieve_loop(self, *_):
        """
        Start the msp packet processing loop
        """
        gevent.spawn(self.recieve_loop)
        logger.info("Backpack recieve greenlet started.")

    def start_connection(self, *_) -> None:
        """
        Starts the connection loop
        """
        if self._backpack_connected:
            message = "Backpack already connected"
            self._rhapi.ui.message_notify(self._rhapi.language.__(message))
            return

        id_ = self._rhapi.db.option("_conn_opt", None, as_int=True)
        for con in ConnectionTypeEnum:
            if id_ == con.id_:
                break
        else:
            message = "Connection type not provided"
            self._rhapi.ui.message_notify(self._rhapi.language.__(message))
            return

        if con == ConnectionTypeEnum.USB:
            self._establish_connection(con.type_)

        elif con == ConnectionTypeEnum.ONBOARD:
            if RH_GPIO.is_real_hw_GPIO():
                logger.info("Turning on GPIO pins for NuclearHazard boards")
                RH_GPIO.setmode(RH_GPIO.BCM)
                RH_GPIO.setup(16, RH_GPIO.OUT, initial=RH_GPIO.HIGH)
                gevent.sleep(0.5)
                RH_GPIO.setup(11, RH_GPIO.OUT, initial=RH_GPIO.HIGH)
                gevent.sleep(0.5)
                RH_GPIO.output(11, RH_GPIO.LOW)
                gevent.sleep()
                RH_GPIO.output(11, RH_GPIO.HIGH)

                self._establish_connection(con.type_)

            else:
                message = "Instance not running on Raspberry Pi"
                self._rhapi.ui.message_notify(self._rhapi.language.__(message))

        elif con == ConnectionTypeEnum.SOCKET:
            addr = self._rhapi.db.option("_socket_ip", None)
            if addr is not None:
                try:
                    ip_addr = socket.gethostbyname(addr)
                except socket.gaierror:
                    message = "Failed to connect to device's socket"
                    self._rhapi.ui.message_notify(self._rhapi.language.__(message))
                else:
                    self._establish_connection(con.type_, ip_addr=ip_addr)
            else:
                message = "IP Address for socket not provided"
                self._rhapi.ui.message_notify(self._rhapi.language.__(message))

    def _establish_connection(
        self, connection_type: type[BackpackConnection], **kwargs
    ):
        """
        Setup the backpack connection

        :param connection_type: The type of connection to use
        """
        # Clear data in send queue
        while not self._send_queue.empty():
            self._send_queue.get()

        self._connection = connection_type(self._send_queue, self._recieve_queue)
        if not self._connection.connect(**kwargs):
            message = "Attempt to establish backpack connection failed"
            self._rhapi.ui.message_notify(self._rhapi.language.__(message))
            return

        message = "Backpack sucessfully connected"
        self._rhapi.ui.message_notify(self._rhapi.language.__(message))

        self.version_request()

    def recieve_loop(self) -> None:
        """
        Handles recieving data from the backpack
        """
        try:
            while True:
                packet: MSPPacket = self._recieve_queue.get()

                function_ = packet.function

                if packet.type_ == MSPPacketType.RESPONSE:
                    if function_ == MSPTypes.MSP_ELRS_GET_BACKPACK_VERSION:
                        version = bytes(i for i in packet.payload if i != 0).decode(
                            "utf-8"
                        )
                        message = f"Backpack device firmware version: {version}"
                        logger.info(message)
                        self._rhapi.ui.message_notify(self._rhapi.language.__(message))

                if packet.type_ == MSPPacketType.COMMAND:
                    if function_ == MSPTypes.MSP_ELRS_BACKPACK_SET_RECORDING_STATE:
                        itr = packet.iterate_payload()
                        if (val := next(itr)) == 0x00:
                            self.stop_race()
                        elif val == 0x01:
                            self.start_race()

        except KeyboardInterrupt:
            logger.error("Stopping blackpack connector greenlet")

    def disconnect(self, *_) -> None:
        """
        Disconnect the connection loop
        """
        if not self._backpack_connected:
            message = "Backpack not connected"
            self._rhapi.ui.message_notify(self._rhapi.language.__(message))
            return

        assert self._connection is not None
        self._connection.disconnect()

        message = "Backpack disconnected"
        self._rhapi.ui.message_notify(self._rhapi.language.__(message))

    #
    # Packet creation
    #

    def hash_phrase(self, bindphrase: str) -> bytes:
        """
        Hashes a string into a UID

        :param bindphrase: The string to hash
        :return: The hashed phrase
        """

        hash_ = bytearray(
            x
            for x in hashlib.md5(
                (f'-DMY_BINDING_PHRASE="{bindphrase}"').encode()
            ).digest()[0:6]
        )
        if (hash_[0] % 2) == 1:
            hash_[0] -= 0x01

        return hash_

    def get_pilot_uid(self, pilot_id: int) -> bytes:
        """
        Get the uid for a pilot. If a bindphrase is not
        saved as an attribute, the pilot callsign is used
        to generate the uid.

        :param pilot_id: The pilot id
        :return: The pilot uid
        """
        assert pilot_id > 0, "Can not generate backpack uid for invalid pilot"
        bindphrase = self._rhapi.db.pilot_attribute_value(pilot_id, "comm_elrs")
        if bindphrase:
            uid = self.hash_phrase(bindphrase)
        else:
            pilot = self._rhapi.db.pilot_by_id(pilot_id)
            assert pilot is not None, "Pilot not in database"
            uid = self.hash_phrase(pilot.callsign)

        return uid

    def get_visible_text_length(self, text: str) -> int:
        """
        Calculate the visible length of text excluding OSD color codes.

        In HDZero OSD, lowercase letters are color codes that don't display.
        All visible text uses UPPERCASE letters, numbers, spaces, and punctuation.
        Common color codes: w (white), x (reset/transparent), r (red), etc.

        :param text: The text string with potential color codes
        :return: The visible length of the text
        """
        # Remove all lowercase letters (color codes) - only uppercase, numbers, spaces, and punctuation display
        visible_text = ''.join(c for c in text if not c.islower())
        return len(visible_text)

    def calculate_osd_column(self, text: str, alignment: str, custom_col: int = 0) -> int:
        """
        Calculate the starting column for OSD text based on alignment preference.

        HDZero OSD has 50 columns (0-49).

        The custom_col parameter meaning varies by alignment type:
        - left: custom_col is the LEFT edge position
        - right: custom_col is the RIGHT edge position
        - center: custom_col is the CENTER position

        This function always returns the LEFT edge (starting column) where text should begin.

        :param text: The text string to display (may include color codes)
        :param alignment: Alignment type - "left", "center", or "right"
        :param custom_col: Column position (meaning depends on alignment type)
        :return: Starting column position (0-49)
        """
        # Use full text length (color codes still occupy column space)
        text_length = len(text)

        logger.info(f"calculate_osd_column: text='{text}', alignment='{alignment}', custom_col={custom_col}, text_length={text_length}")

        if alignment == "left":
            # custom_col is already the LEFT edge position
            result = max(0, min(custom_col, 49))
            logger.info(f"  LEFT alignment: result={result}")
            return result
        elif alignment == "right":
            # custom_col is the RIGHT edge position
            # If custom_col is 0 (default), use 49 (rightmost column)
            right_edge = custom_col if custom_col > 0 else 49
            # Calculate starting position: right_edge - text_length + 1
            col = right_edge - text_length + 1
            result = max(col, 0)
            logger.info(f"  RIGHT alignment: right_edge={right_edge}, col={col}, result={result}")
            return result
        elif alignment == "center":
            # custom_col is the CENTER position
            # If custom_col is 0 (default), use 25 (center of screen)
            center = custom_col if custom_col > 0 else 25
            # Calculate starting position: center - (text_length / 2)
            offset = text_length // 2
            col = center - offset
            result = max(col, 0)
            logger.info(f"  CENTER alignment: center={center}, offset={offset}, col={col}, result={result}")
            return result
        else:
            # Fallback for any other alignment type (backward compatibility)
            # Treat as left-aligned
            result = max(0, min(custom_col, 49))
            logger.info(f"  FALLBACK alignment: result={result}")
            return result

    def send_msp(self, msp: MSPPacket) -> None:
        """
        Sends a MSP packet to the backpack connection
        if it is active

        :param msp: _description_
        """
        if self._backpack_connected:
            self._send_queue.put(msp)

    def set_send_uid(self, address: bytes) -> None:
        """
        Sends the packet to set the address for the
        recipient of future packets

        :param address: Address to set
        """
        packet = MSPPacket()
        packet.set_function(MSPTypes.MSP_ELRS_SET_SEND_UID)
        payload = bytearray()
        payload.append(0x01)
        payload += address
        packet.set_payload(payload)
        self.send_msp(packet)

    def reset_send_uid(self) -> None:
        """
        Sends the packet to reset the packet recipient
        to the system default
        """
        packet = MSPPacket()
        packet.set_function(MSPTypes.MSP_ELRS_SET_SEND_UID)
        payload = bytearray()
        payload.append(0x00)
        packet.set_payload(payload)
        self.send_msp(packet)

    def send_osd_message_batch(self, uid: bytes, row: int, col: int, text: str) -> None:
        """
        Atomically sends a batch of packets to display an OSD message.

        :param uid: Pilot UID to send to
        :param row: OSD row
        :param col: OSD column
        :param text: Message text
        """
        with self._queue_lock:
            self.set_send_uid(uid)
            self.send_clear_osd()
            self.send_osd_text(row, col, text)
            self.send_display_osd()
            self.reset_send_uid()

    def send_osd_clear_batch(self, uid: bytes, row: int) -> None:
        """
        Atomically sends a batch of packets to clear an OSD row.

        :param uid: Pilot UID to send to
        :param row: OSD row to clear
        """
        with self._queue_lock:
            self.set_send_uid(uid)
            self.send_clear_osd_row(row)
            self.send_display_osd()
            self.reset_send_uid()

    def send_clear_osd(self) -> None:
        """
        Sends the packet to clear the goggle's osd
        """
        packet = MSPPacket()
        packet.set_function(MSPTypes.MSP_ELRS_SET_OSD)
        payload = bytearray()
        payload.append(0x02)
        packet.set_payload(payload)
        self.send_msp(packet)

    def send_osd_text(self, row: int, col: int, text: str) -> None:
        """
        Sends a packet that provides text data to the
        recipient. This does not display the text to the
        recipient until `send_display_osd` is called

        :param row: The row to display the text on
        :param col: The column to place the start of the
        :param message: _description_
        """
        logger.info(f"send_osd_text: row={row}, col={col}, text='{text}', len={len(text)}")
        payload = bytearray((0x03, row, col, 0))
        for index, char in enumerate(text):
            if index >= 50:
                break

            payload.append(ord(char))

        packet = MSPPacket()
        packet.set_function(MSPTypes.MSP_ELRS_SET_OSD)
        packet.set_payload(payload)
        self.send_msp(packet)

    def send_display_osd(self) -> None:
        """
        Sends a packet that informs the recipient
        to display any provided text
        """
        packet = MSPPacket()
        packet.set_function(MSPTypes.MSP_ELRS_SET_OSD)
        payload = bytearray((0x04,))
        packet.set_payload(payload)
        self.send_msp(packet)

    def send_clear_osd_row(self, row: int) -> None:
        """
        Sends a packet that clears the text data
        in a specific row. This does not remove
        the text until `send_display_osd` is called.

        :param row: The row to remove text from
        """
        payload = bytearray((0x03, row, 0, 0))
        for _ in range(50):
            payload.append(0)

        packet = MSPPacket()
        packet.set_function(MSPTypes.MSP_ELRS_SET_OSD)
        packet.set_payload(payload)
        self.send_msp(packet)

    def version_request(self):
        """
        Sends the packet requesting the version of the
        backpack hardware
        """
        packet = MSPPacket()
        packet.set_function(MSPTypes.MSP_ELRS_GET_BACKPACK_VERSION)
        self.send_msp(packet)

    def activate_bind(self, *_) -> None:
        """
        Sends a packet to put the connected device in
        bind mode
        """
        message = "Activating backpack's bind mode..."
        self._rhapi.ui.message_notify(self._rhapi.language.__(message))

        packet = MSPPacket()
        packet.set_function(MSPTypes.MSP_ELRS_BACKPACK_SET_MODE)
        payload = bytearray((ord("B"),))
        packet.set_payload(payload)
        self.send_msp(packet)

    def activate_wifi(self, *_) -> None:
        """
        Sends a packet to put the connected device in
        bind mode
        """
        message = "Turning on backpack's wifi..."
        self._rhapi.ui.message_notify(self._rhapi.language.__(message))

        packet = MSPPacket()
        packet.set_function(MSPTypes.MSP_ELRS_BACKPACK_SET_MODE)
        payload = bytearray((ord("W"),))
        packet.set_payload(payload)
        self.send_msp(packet)

    #
    # Field Tests
    #

    def test_bind_osd(self, *_):
        """
        A test for checking the connection of the pilot
        bound to the timer backpack
        """

        def test():
            self._queue_lock.acquire()
            text = "ROTORHAZARD"
            for row in range(18):
                self.send_clear_osd()
                start_col = self.calculate_osd_column(text, "center", 0)
                self.send_osd_text(row, start_col, text)
                self.send_display_osd()

                gevent.sleep(0.5)

                self.send_clear_osd_row(row)
                self.send_display_osd()

            gevent.sleep(1)
            self.send_clear_osd()
            self.send_display_osd()
            self._queue_lock.release()

        gevent.spawn(test)

    def test_element(self, pilot_id: int, element_id: str) -> dict:
        """
        Generate a realistic test message for an OSD element using actual formatting logic.
        Returns the formatted message(s), row(s), and col(s) that would be sent.

        :param pilot_id: The pilot ID
        :param element_id: The element identifier (e.g., 'heat_name', 'race_stage')
        :return: Dictionary with message details
        """
        messages = []

        try:
            if element_id == 'heat_name':
                # Get settings
                row = int(self.get_osd_setting(pilot_id, 'heat_name', 'row', '_heatname_row', 2))
                alignment = self.get_osd_setting(pilot_id, 'heat_name', 'alignment', '_heatname_align', 'center')
                custom_col = int(self.get_osd_setting(pilot_id, 'heat_name', 'custom_col', '_heatname_custom_col', 0))
                use_round_num = self.get_pilot_behavior_setting(pilot_id, '_round_num', "0") == "1"

                # Generate mock message
                if use_round_num:
                    message = "x HEAT 1 | ROUND 2 w"
                else:
                    message = "x HEAT 1 w"

                col = self.calculate_osd_column(message, alignment, custom_col)
                messages.append({'row': row, 'col': col, 'message': message})

            elif element_id == 'class_name':
                row = int(self.get_osd_setting(pilot_id, 'class_name', 'row', '_classname_row', 1))
                alignment = self.get_osd_setting(pilot_id, 'class_name', 'alignment', '_classname_align', 'center')
                custom_col = int(self.get_osd_setting(pilot_id, 'class_name', 'custom_col', '_classname_custom_col', 0))

                message = "x OPEN CLASS w"
                col = self.calculate_osd_column(message, alignment, custom_col)
                messages.append({'row': row, 'col': col, 'message': message})

            elif element_id == 'event_name':
                row = int(self.get_osd_setting(pilot_id, 'event_name', 'row', '_eventname_row', 0))
                alignment = self.get_osd_setting(pilot_id, 'event_name', 'alignment', '_eventname_align', 'center')
                custom_col = int(self.get_osd_setting(pilot_id, 'event_name', 'custom_col', '_eventname_custom_col', 0))

                message = "x MULTIGP RACE 2025 w"
                col = self.calculate_osd_column(message, alignment, custom_col)
                messages.append({'row': row, 'col': col, 'message': message})

            elif element_id == 'race_stage':
                row = int(self.get_osd_setting(pilot_id, 'race_stage', 'row', '_status_row', 5))
                alignment = self.get_osd_setting(pilot_id, 'race_stage', 'alignment', '_racestage_align', 'center')
                custom_col = int(self.get_osd_setting(pilot_id, 'race_stage', 'custom_col', '_racestage_custom_col', 0))
                message_text = self.get_pilot_string_setting(pilot_id, '_racestage_message', "w ARM NOW x")

                col = self.calculate_osd_column(message_text, alignment, custom_col)
                messages.append({'row': row, 'col': col, 'message': message_text})

            elif element_id == 'race_start':
                row = int(self.get_osd_setting(pilot_id, 'race_start', 'row', '_status_row', 5))
                alignment = self.get_osd_setting(pilot_id, 'race_start', 'alignment', '_racestart_align', 'center')
                custom_col = int(self.get_osd_setting(pilot_id, 'race_start', 'custom_col', '_racestart_custom_col', 0))
                message_text = self.get_pilot_string_setting(pilot_id, '_racestart_message', "w   GO!   x")

                col = self.calculate_osd_column(message_text, alignment, custom_col)
                messages.append({'row': row, 'col': col, 'message': message_text})

            elif element_id == 'race_finish':
                row = int(self.get_osd_setting(pilot_id, 'race_finish', 'row', '_status_row', 5))
                alignment = self.get_osd_setting(pilot_id, 'race_finish', 'alignment', '_racefinish_align', 'center')
                custom_col = int(self.get_osd_setting(pilot_id, 'race_finish', 'custom_col', '_racefinish_custom_col', 0))
                message_text = self.get_pilot_string_setting(pilot_id, '_racefinish_message', "w FINISH LAP! x")

                col = self.calculate_osd_column(message_text, alignment, custom_col)
                messages.append({'row': row, 'col': col, 'message': message_text})

            elif element_id == 'race_stop':
                row = int(self.get_osd_setting(pilot_id, 'race_stop', 'row', '_status_row', 5))
                alignment = self.get_osd_setting(pilot_id, 'race_stop', 'alignment', '_racestop_align', 'center')
                custom_col = int(self.get_osd_setting(pilot_id, 'race_stop', 'custom_col', '_racestop_custom_col', 0))
                message_text = self.get_pilot_string_setting(pilot_id, '_racestop_message', "w  LAND NOW!  x")

                col = self.calculate_osd_column(message_text, alignment, custom_col)
                messages.append({'row': row, 'col': col, 'message': message_text})

            elif element_id == 'current_lap':
                row = int(self.get_osd_setting(pilot_id, 'current_lap', 'row', '_currentlap_row', 0))
                alignment = self.get_osd_setting(pilot_id, 'current_lap', 'alignment', '_currentlap_align', 'left')
                custom_col = int(self.get_osd_setting(pilot_id, 'current_lap', 'custom_col', '_currentlap_custom_col', 0))

                if self.get_pilot_behavior_setting(pilot_id, '_position_mode', "0") == "1":
                    message = "POSN: 2ND | LAP: 4"
                else:
                    message = "LAP: 4"

                col = self.calculate_osd_column(message, alignment, custom_col)
                messages.append({'row': row, 'col': col, 'message': message})

            elif element_id == 'lap_results':
                row = int(self.get_osd_setting(pilot_id, 'lap_results', 'row', '_lapresults_row', 15))
                alignment = self.get_osd_setting(pilot_id, 'lap_results', 'alignment', '_lapresults_align', 'center')
                custom_col = int(self.get_osd_setting(pilot_id, 'lap_results', 'custom_col', '_lapresults_custom_col', 0))

                # Generate mock lap result message
                message = "x 0:42.5 | 3:45.2 w"

                col = self.calculate_osd_column(message, alignment, custom_col)
                messages.append({'row': row, 'col': col, 'message': message})

            elif element_id == 'announcement':
                row = int(self.get_osd_setting(pilot_id, 'announcement', 'row', '_announcement_row', 3))
                alignment = self.get_osd_setting(pilot_id, 'announcement', 'alignment', '_announcement_align', 'center')
                custom_col = int(self.get_osd_setting(pilot_id, 'announcement', 'custom_col', '_announcement_custom_col', 0))

                message = "x NEXT RACE IN 5 MINUTES w"

                col = self.calculate_osd_column(message, alignment, custom_col)
                messages.append({'row': row, 'col': col, 'message': message})

            elif element_id == 'leader':
                row = int(self.get_osd_setting(pilot_id, 'leader', 'row', '_status_row', 5))
                alignment = self.get_osd_setting(pilot_id, 'leader', 'alignment', '_leader_align', 'center')
                custom_col = int(self.get_osd_setting(pilot_id, 'leader', 'custom_col', '_leader_custom_col', 0))
                leader_message = self.get_pilot_string_setting(pilot_id, '_leader_message', 'RACE LEADER')

                message = f"x {leader_message} | 0:42.1 w"

                col = self.calculate_osd_column(message, alignment, custom_col)
                messages.append({'row': row, 'col': col, 'message': message})

            elif element_id == 'results':
                base_row = int(self.get_osd_setting(pilot_id, 'results', 'row', '_results_row', 13))
                alignment = self.get_osd_setting(pilot_id, 'results', 'alignment', '_placement_align', 'center')
                custom_col = int(self.get_osd_setting(pilot_id, 'results', 'custom_col', '_placement_custom_col', 0))

                # Generate two-line results
                message1 = "PLACEMENT: 2"
                col1 = self.calculate_osd_column(message1, alignment, custom_col)
                messages.append({'row': base_row, 'col': col1, 'message': message1})

                message2 = "TOTAL TIME: 3:45.2"
                col2 = self.calculate_osd_column(message2, alignment, custom_col)
                messages.append({'row': base_row + 1, 'col': col2, 'message': message2})

            elif element_id == 'lap_times':
                base_row = int(self.get_osd_setting(pilot_id, 'lap_times', 'row', '_laptimes_row', 14))
                alignment = self.get_osd_setting(pilot_id, 'lap_times', 'alignment', '_laptimes_align', 'center')
                custom_col = int(self.get_osd_setting(pilot_id, 'lap_times', 'custom_col', '_laptimes_custom_col', 0))

                # Generate multiple lap time lines
                mock_lap_times = ['HS: 0:45.2', 'L1: 0:46.1', 'L2: 0:44.5']
                for i, lap_time in enumerate(mock_lap_times):
                    col = self.calculate_osd_column(lap_time, alignment, custom_col)
                    messages.append({'row': base_row + i, 'col': col, 'message': lap_time})

            elif element_id == 'recent_laps':
                base_row = int(self.get_osd_setting(pilot_id, 'recent_laps', 'row', '_recentlaps_row', 11) or 11)
                alignment = self.get_osd_setting(pilot_id, 'recent_laps', 'alignment', '_recentlaps_align', 'left') or 'left'
                custom_col = int(self.get_osd_setting(pilot_id, 'recent_laps', 'custom_col', '_recentlaps_custom_col', 0) or 0)
                num_laps = int(self.get_osd_setting(pilot_id, 'recent_laps', 'num_laps', '_recentlaps_count', 3) or 3)

                # Generate mock recent lap times (seconds only format)
                mock_recent = [f'HS:45.01' if i == 0 else f'L{i}:4{5-i}.{i}2' for i in range(num_laps)]
                for i, lap_time in enumerate(mock_recent):
                    col = self.calculate_osd_column(lap_time, alignment, custom_col)
                    messages.append({'row': base_row + i, 'col': col, 'message': lap_time})

            else:
                return {'success': False, 'error': f'Unknown element ID: {element_id}'}

            return {'success': True, 'messages': messages, 'element_id': element_id}

        except Exception as e:
            logger.error(f"Error generating test for element {element_id}: {e}")
            return {'success': False, 'error': str(e)}

    #
    # VRxC Event Triggers
    #

    def pilot_alter(self, args: dict) -> None:
        """
        Logs the uid change of the pilot

        :param args: _description_
        """
        pilot_id = args["pilot_id"]
        uid = self.get_pilot_uid(pilot_id)
        uid_formated = ".".join([str(byte) for byte in uid])
        logger.info("Pilot %s's UID set to %s", pilot_id, uid_formated)

    def onRaceStage(self, args) -> None:
        """
        _summary_

        :param args: _description_
        """
        if not self._backpack_connected:
            return

        use_heat_name = self._rhapi.db.option("_heat_name") == "1"
        use_round_num = self._rhapi.db.option("_round_num") == "1"
        use_class_name = self._rhapi.db.option("_class_name") == "1"
        use_event_name = self._rhapi.db.option("_event_name") == "1"

        # Pull heat name and rounds
        heat_data = self._rhapi.db.heat_by_id(args["heat_id"])
        if heat_data:
            class_id = heat_data.class_id
            heat_name = heat_data.display_name
            round_num = self._rhapi.db.heat_max_round(args["heat_id"]) + 1
        else:
            class_id = None
            heat_name = None
            round_num = None

        # Check class name
        if class_id:
            raceclass = self._rhapi.db.raceclass_by_id(class_id)
            class_name = raceclass.display_name
        else:
            raceclass = None
            class_name = None

        # Generate heat message
        heat_name_row = int(self._rhapi.db.option("_heatname_row") or 2)
        heat_align = self._rhapi.db.option("_heatname_align") or "center"
        heat_custom_col = int(self._rhapi.db.option("_heatname_custom_col") or 0)
        if all([use_heat_name, use_round_num, heat_name, round_num]):
            round_trans = self._rhapi.__("Round")
            heat_message = (
                f"x {heat_name.upper()} | {round_trans.upper()} {round_num} w"
            )
            heat_start_col = self.calculate_osd_column(heat_message, heat_align, heat_custom_col)
            heat_message_parms = (heat_name_row, heat_start_col, heat_message)
        elif use_heat_name and heat_name:
            heat_message = f"x {heat_name.upper()} w"
            heat_start_col = self.calculate_osd_column(heat_message, heat_align, heat_custom_col)
            heat_message_parms = (heat_name_row, heat_start_col, heat_message)
        else:
            heat_message_parms = None

        # Generate class message
        class_name_row = int(self._rhapi.db.option("_classname_row") or 1)
        class_align = self._rhapi.db.option("_classname_align") or "center"
        class_custom_col = int(self._rhapi.db.option("_classname_custom_col") or 0)
        if use_class_name and class_name:
            class_message = f"x {class_name.upper()} w"
            class_start_col = self.calculate_osd_column(class_message, class_align, class_custom_col)
            class_message_parms = (class_name_row, class_start_col, class_message)

        # Generate event message
        event_name_row = int(self._rhapi.db.option("_eventname_row") or 0)
        event_name = self._rhapi.db.option("eventName")
        event_align = self._rhapi.db.option("_eventname_align") or "center"
        event_custom_col = int(self._rhapi.db.option("_eventname_custom_col") or 0)
        if use_event_name and event_name:
            event_name = self._rhapi.db.option("eventName")
            event_message = f"x {event_name.upper()} w"
            event_start_col = self.calculate_osd_column(event_message, event_align, event_custom_col)
            event_message_parms = (event_name_row, event_start_col, event_message)

        stage_message_text = self._rhapi.db.option("_racestage_message") or "w ARM NOW x"
        stage_align = self._rhapi.db.option("_racestage_align") or "center"
        stage_custom_col = int(self._rhapi.db.option("_racestage_custom_col") or 0)
        start_col = self.calculate_osd_column(stage_message_text, stage_align, stage_custom_col)
        stage_mesage = (
            self._rhapi.db.option("_status_row"),
            start_col,
            stage_message_text,
        )

        # Send stage message to all pilots
        def arm(pilot_id):
            uid = self.get_pilot_uid(pilot_id)

            # Get per-pilot settings for this pilot
            # Heat name
            pilot_heat_row = int(self.get_osd_setting(pilot_id, 'heat_name', 'row', '_heatname_row', heat_name_row) or heat_name_row)
            pilot_heat_align = self.get_osd_setting(pilot_id, 'heat_name', 'alignment', '_heatname_align', heat_align) or heat_align
            pilot_heat_col = int(self.get_osd_setting(pilot_id, 'heat_name', 'custom_col', '_heatname_custom_col', heat_custom_col) or 0)

            # Class name
            pilot_class_row = int(self.get_osd_setting(pilot_id, 'class_name', 'row', '_classname_row', class_name_row) or class_name_row)
            pilot_class_align = self.get_osd_setting(pilot_id, 'class_name', 'alignment', '_classname_align', class_align) or class_align
            pilot_class_col = int(self.get_osd_setting(pilot_id, 'class_name', 'custom_col', '_classname_custom_col', class_custom_col) or 0)

            # Event name
            pilot_event_row = int(self.get_osd_setting(pilot_id, 'event_name', 'row', '_eventname_row', event_name_row) or event_name_row)
            pilot_event_align = self.get_osd_setting(pilot_id, 'event_name', 'alignment', '_eventname_align', event_align) or event_align
            pilot_event_col = int(self.get_osd_setting(pilot_id, 'event_name', 'custom_col', '_eventname_custom_col', event_custom_col) or 0)

            # Stage message
            pilot_status_row = int(self.get_osd_setting(pilot_id, 'race_stage', 'row', '_status_row', 5) or 5)
            pilot_stage_align = self.get_osd_setting(pilot_id, 'race_stage', 'alignment', '_racestage_align', stage_align) or stage_align
            pilot_stage_col = int(self.get_osd_setting(pilot_id, 'race_stage', 'custom_col', '_racestage_custom_col', stage_custom_col) or 0)

            # Recalculate positions with pilot-specific settings
            pilot_stage_start_col = self.calculate_osd_column(stage_message_text, pilot_stage_align, pilot_stage_col)
            pilot_stage_message = (pilot_status_row, pilot_stage_start_col, stage_message_text)

            # Check per-pilot enabled settings (fall back to global if not set)
            pilot_heat_enabled = self.get_osd_setting(pilot_id, 'heat_name', 'enabled', None, None)
            if pilot_heat_enabled is None:
                pilot_heat_enabled = use_heat_name
            else:
                pilot_heat_enabled = bool(pilot_heat_enabled)

            pilot_class_enabled = self.get_osd_setting(pilot_id, 'class_name', 'enabled', None, None)
            if pilot_class_enabled is None:
                pilot_class_enabled = use_class_name
            else:
                pilot_class_enabled = bool(pilot_class_enabled)

            pilot_event_enabled = self.get_osd_setting(pilot_id, 'event_name', 'enabled', None, None)
            if pilot_event_enabled is None:
                pilot_event_enabled = use_event_name
            else:
                pilot_event_enabled = bool(pilot_event_enabled)

            pilot_heat_message_parms = None
            if pilot_heat_enabled and heat_name:
                if all([use_round_num, heat_name, round_num]):
                    round_trans = self._rhapi.__("Round")
                    heat_message = f"x {heat_name.upper()} | {round_trans.upper()} {round_num} w"
                else:
                    heat_message = f"x {heat_name.upper()} w"
                pilot_heat_start_col = self.calculate_osd_column(heat_message, pilot_heat_align, pilot_heat_col)
                pilot_heat_message_parms = (pilot_heat_row, pilot_heat_start_col, heat_message)

            pilot_class_message_parms = None
            if pilot_class_enabled and class_name:
                class_message = f"x {class_name.upper()} w"
                pilot_class_start_col = self.calculate_osd_column(class_message, pilot_class_align, pilot_class_col)
                pilot_class_message_parms = (pilot_class_row, pilot_class_start_col, class_message)

            pilot_event_message_parms = None
            if pilot_event_enabled and event_name:
                event_message = f"x {event_name.upper()} w"
                pilot_event_start_col = self.calculate_osd_column(event_message, pilot_event_align, pilot_event_col)
                pilot_event_message_parms = (pilot_event_row, pilot_event_start_col, event_message)

            with self._queue_lock:
                self.set_send_uid(uid)
                self.send_clear_osd()

                # Send messages to backpack with per-pilot positions
                self.send_osd_text(*pilot_stage_message)
                if pilot_heat_message_parms:
                    self.send_osd_text(*pilot_heat_message_parms)
                if pilot_class_message_parms:
                    self.send_osd_text(*pilot_class_message_parms)
                if pilot_event_message_parms:
                    self.send_osd_text(*pilot_event_message_parms)

                self.send_display_osd()
                self.reset_send_uid()

        seat_pilots = self._rhapi.race.pilots
        for seat in seat_pilots:
            if (
                seat_pilots[seat]
                and self._rhapi.db.pilot_attribute_value(
                    seat_pilots[seat], "elrs_active"
                )
                == "1"
            ):
                gevent.spawn(arm, seat_pilots[seat])

    def onRaceStart(self, *_) -> None:
        if not self._backpack_connected:
            return

        def start(pilot_id):
            uid = self.get_pilot_uid(pilot_id)
            pilot_config = self.get_pilot_osd_config(pilot_id)
            use_global = pilot_config.get('use_global', True)

            # Check per-pilot enabled setting
            if use_global:
                # Global doesn't have an enabled setting for race_start, always enabled
                pass
            else:
                race_start_config = pilot_config.get('race_start', {})
                pilot_start_enabled = race_start_config.get('enabled', None)
                if pilot_start_enabled is not None and not pilot_start_enabled:
                    return  # Skip if explicitly disabled for this pilot

            # Get per-pilot settings
            if use_global:
                status_row = int(self._rhapi.db.option('_status_row') or 5)
                start_align = self._rhapi.db.option('_racestart_align') or "center"
                start_custom_col = int(self._rhapi.db.option('_racestart_custom_col') or 0)
                racestart_uptime = int(self._rhapi.db.option('_racestart_uptime') or 5)
                is_timed = racestart_uptime > 0
            else:
                race_start_config = pilot_config.get('race_start', {})
                status_row = int(race_start_config.get('row', None) or self._rhapi.db.option('_status_row') or 5)
                start_align = race_start_config.get('alignment', None) or self._rhapi.db.option('_racestart_align') or "center"
                start_custom_col = int(race_start_config.get('custom_col', None) or self._rhapi.db.option('_racestart_custom_col') or 0)

                # Check is_timed setting
                is_timed = race_start_config.get('is_timed', None)
                if is_timed is None:
                    global_uptime = int(self._rhapi.db.option('_racestart_uptime') or 5)
                    is_timed = global_uptime > 0

                # Get uptime
                racestart_uptime = race_start_config.get('uptime', None)
                if racestart_uptime is None:
                    racestart_uptime = int(self._rhapi.db.option('_racestart_uptime') or 5)
                else:
                    racestart_uptime = int(racestart_uptime)

            start_message_text = self._rhapi.db.option("_racestart_message") or "w   GO!   x"
            start_col = self.calculate_osd_column(start_message_text, start_align, start_custom_col)

            # Send GO message atomically (lock released immediately after)
            self.send_osd_message_batch(
                uid,
                status_row,
                start_col,
                start_message_text
            )

            # Spawn separate greenlet for delayed clearing (only if timed mode)
            if is_timed and racestart_uptime > 0:
                def delayed_clear():
                    gevent.sleep(racestart_uptime * 1e-1)
                    gevent.sleep(0.1)  # Transmission buffer
                    self.send_osd_clear_batch(uid, status_row)

                gevent.spawn(delayed_clear)

        seat_pilots = self._rhapi.race.pilots
        for seat in seat_pilots:
            if (
                seat_pilots[seat]
                and self._rhapi.db.pilot_attribute_value(
                    seat_pilots[seat], "elrs_active"
                )
                == "1"
            ):
                gevent.spawn(start, seat_pilots[seat])

    def onRaceFinish(self, *_) -> None:
        if not self._backpack_connected:
            return

        def finish(pilot_id):
            uid = self.get_pilot_uid(pilot_id)
            pilot_config = self.get_pilot_osd_config(pilot_id)
            use_global = pilot_config.get('use_global', True)

            # Check per-pilot enabled setting
            if not use_global:
                race_finish_config = pilot_config.get('race_finish', {})
                pilot_finish_enabled = race_finish_config.get('enabled', None)
                if pilot_finish_enabled is not None and not pilot_finish_enabled:
                    return  # Skip if explicitly disabled for this pilot

            # Get per-pilot settings
            if use_global:
                status_row = int(self._rhapi.db.option('_status_row') or 5)
                finish_align = self._rhapi.db.option('_racefinish_align') or "center"
                finish_custom_col = int(self._rhapi.db.option('_racefinish_custom_col') or 0)
                finish_uptime = int(self._rhapi.db.option('_finish_uptime') or 20)
                is_timed = finish_uptime > 0
            else:
                race_finish_config = pilot_config.get('race_finish', {})
                status_row = int(race_finish_config.get('row', None) or self._rhapi.db.option('_status_row') or 5)
                finish_align = race_finish_config.get('alignment', None) or self._rhapi.db.option('_racefinish_align') or "center"
                finish_custom_col = int(race_finish_config.get('custom_col', None) or self._rhapi.db.option('_racefinish_custom_col') or 0)

                # Check is_timed setting
                is_timed = race_finish_config.get('is_timed', None)
                if is_timed is None:
                    global_uptime = int(self._rhapi.db.option('_finish_uptime') or 20)
                    is_timed = global_uptime > 0

                # Get uptime
                finish_uptime = race_finish_config.get('uptime', None)
                if finish_uptime is None:
                    finish_uptime = int(self._rhapi.db.option('_finish_uptime') or 20)
                else:
                    finish_uptime = int(finish_uptime)

            finish_message_text = self._rhapi.db.option("_racefinish_message") or "w FINISH LAP! x"
            start_col = self.calculate_osd_column(finish_message_text, finish_align, finish_custom_col)

            # Send FINISH message atomically
            with self._queue_lock:
                self.set_send_uid(uid)
                self.send_clear_osd_row(status_row)
                self.send_osd_text(
                    status_row,
                    start_col,
                    finish_message_text
                )
                self.send_display_osd()
                self.reset_send_uid()

            # Delayed clear in separate greenlet (only if timed mode)
            if is_timed and finish_uptime > 0:
                def delayed_clear():
                    gevent.sleep(finish_uptime * 1e-1)
                    gevent.sleep(0.1)  # Transmission buffer
                    self.send_osd_clear_batch(uid, status_row)

                gevent.spawn(delayed_clear)

        seat_pilots = self._rhapi.race.pilots
        seats_finished = self._rhapi.race.seats_finished

        for seat in seat_pilots:
            if (
                seat_pilots[seat]
                and self._rhapi.db.pilot_attribute_value(
                    seat_pilots[seat], "elrs_active"
                )
                == "1"
            ):
                if not seats_finished[seat]:
                    gevent.spawn(finish, seat_pilots[seat])

    def onRaceStop(self, *_) -> None:
        if not self._backpack_connected:
            return

        def show_lap_times(pilot_id, seat):
            # Check per-pilot enabled setting
            pilot_laptimes_enabled = self.get_osd_setting(pilot_id, 'lap_times', 'enabled', '_show_laptimes', True)
            if not pilot_laptimes_enabled:
                return  # Skip if disabled for this pilot

            uid = self.get_pilot_uid(pilot_id)

            # Get lap data for this pilot using laps_raw API
            all_laps = self._rhapi.race.laps_raw
            raw_laps = all_laps[seat] if seat < len(all_laps) else []

            # Get minimum lap time in milliseconds
            min_lap_sec = int(self._rhapi.db.option("MinLapSec") or 0)
            min_lap_ms = min_lap_sec * 1000

            # Aggregate laps: combine short laps with the next valid lap
            # Holeshot (index 0) is always included as-is
            laps = []  # List of (display_index, aggregated_lap_time)
            pending_time = 0
            display_idx = 0

            for i, lap in enumerate(raw_laps):
                lap_time = lap.get("lap_time", 0)
                if lap_time <= 0:
                    continue

                if i == 0:
                    # Holeshot is always included as-is
                    laps.append((display_idx, lap_time))
                    display_idx += 1
                else:
                    # Aggregate time
                    pending_time += lap_time
                    # Check if aggregated time meets minimum
                    if pending_time >= min_lap_ms:
                        laps.append((display_idx, pending_time))
                        display_idx += 1
                        pending_time = 0

            # Prepare lap time messages (up to 10 laps to fit on screen)
            lap_messages = []
            max_laps = min(len(laps), 10)  # Limit to 10 laps to avoid overflow

            for idx in range(max_laps):
                display_idx, lap_time_ms = laps[idx]
                lap_time = self._rhapi.utils.format_split_time_to_str(
                    lap_time_ms, "{m}:{s}.{d}"
                )
                # Display index 0 = HS (holeshot), index 1 = L1, index 2 = L2, etc.
                if display_idx == 0:
                    lap_messages.append((idx, f"HS: {lap_time}"))
                else:
                    lap_messages.append((idx, f"L{display_idx}: {lap_time}"))

            # If no laps, show a message
            if not lap_messages:
                lap_messages.append((0, "NO LAPS RECORDED"))

            # Get per-pilot settings for lap times display
            laptimes_row = int(self.get_osd_setting(pilot_id, 'lap_times', 'row', '_laptimes_row', 14) or 14)
            laptimes_align = self.get_osd_setting(pilot_id, 'lap_times', 'alignment', '_laptimes_align', "center") or "center"
            laptimes_custom_col = int(self.get_osd_setting(pilot_id, 'lap_times', 'custom_col', '_laptimes_custom_col', 0) or 0)

            with self._queue_lock:
                self.set_send_uid(uid)
                self.send_clear_osd()

                # Display each lap on its own row starting from laptimes_row
                for idx, message in lap_messages:
                    start_col = self.calculate_osd_column(message, laptimes_align, laptimes_custom_col)
                    self.send_osd_text(laptimes_row + idx, start_col, message)

                self.send_display_osd()
                self.reset_send_uid()

            # Delayed clear in separate greenlet (10 seconds + 100ms buffer)
            def delayed_clear():
                gevent.sleep(15.0)  # 15 seconds display time (10 + 5 extra delay)
                gevent.sleep(0.1)   # Transmission buffer

                # Clear all rows that were used
                with self._queue_lock:
                    self.set_send_uid(uid)
                    self.send_clear_osd()
                    self.send_display_osd()
                    self.reset_send_uid()

            gevent.spawn(delayed_clear)

        seat_pilots = self._rhapi.race.pilots
        seats_finished = self._rhapi.race.seats_finished

        for seat in seat_pilots:
            if (
                seat_pilots[seat]
                and self._rhapi.db.pilot_attribute_value(
                    seat_pilots[seat], "elrs_active"
                )
                == "1"
            ):
                # Show lap times for all pilots when race stops
                gevent.spawn(show_lap_times, seat_pilots[seat], seat)

    def onRaceLapRecorded(self, args: dict) -> None:
        if not self._backpack_connected:
            return

        def update_pos(result):
            pilot_id = result["pilot_id"]

            # Check per-pilot enabled setting
            pilot_currentlap_enabled = self.get_osd_setting(pilot_id, 'current_lap', 'enabled', None, None)
            if pilot_currentlap_enabled is not None and not pilot_currentlap_enabled:
                return  # Skip if explicitly disabled for this pilot

            # Find seat for this pilot
            seat = None
            for slot, pid in self._rhapi.race.pilots.items():
                if pid == pilot_id:
                    seat = slot
                    break

            if seat is None:
                return

            # Get lap data and calculate aggregated lap count
            all_laps = self._rhapi.race.laps_raw
            all_pilot_laps = all_laps[seat] if seat < len(all_laps) else []

            # Get minimum lap time in milliseconds
            min_lap_sec = int(self._rhapi.db.option("MinLapSec") or 0)
            min_lap_ms = min_lap_sec * 1000

            # Calculate aggregated lap count (same logic as show_recent_laps)
            aggregated_lap_count = 0
            pending_time = 0

            for i, lap in enumerate(all_pilot_laps):
                lap_time = lap.get("lap_time", 0)
                if lap_time <= 0:
                    continue

                if i == 0:
                    # Holeshot counts as first lap
                    aggregated_lap_count += 1
                else:
                    # Aggregate time until we reach min_lap_ms
                    pending_time += lap_time
                    if pending_time >= min_lap_ms:
                        aggregated_lap_count += 1
                        pending_time = 0

            if self._rhapi.db.option("_position_mode") != "1":
                message = f"LAP: {aggregated_lap_count}"
            else:
                message = f"POSN: {str(result['position']).upper()} | LAP: {aggregated_lap_count}"

            # Get per-pilot settings
            currentlap_row = int(self.get_osd_setting(pilot_id, 'current_lap', 'row', '_currentlap_row', 0) or 0)
            currentlap_align = self.get_osd_setting(pilot_id, 'current_lap', 'alignment', '_currentlap_align', "center") or "center"
            currentlap_custom_col = int(self.get_osd_setting(pilot_id, 'current_lap', 'custom_col', '_currentlap_custom_col', 0) or 0)
            start_col = self.calculate_osd_column(message, currentlap_align, currentlap_custom_col)

            uid = self.get_pilot_uid(pilot_id)
            self._queue_lock.acquire()
            self.set_send_uid(uid)
            self.send_clear_osd_row(currentlap_row)

            self.send_osd_text(currentlap_row, start_col, message)
            self.send_display_osd()
            self.reset_send_uid()
            self._queue_lock.release()

        def lap_results(result, gap_info):
            pilot_id = result["pilot_id"]
            pilot_config = self.get_pilot_osd_config(pilot_id)
            use_global = pilot_config.get('use_global', True)

            # Check per-pilot enabled setting
            if not use_global:
                lap_results_config = pilot_config.get('lap_results', {})
                pilot_lapresults_enabled = lap_results_config.get('enabled', None)
                if pilot_lapresults_enabled is not None and not pilot_lapresults_enabled:
                    return  # Skip if explicitly disabled for this pilot

            # Get per-pilot settings
            if use_global:
                lapresults_row = int(self._rhapi.db.option('_lapresults_row') or 15)
                lapresults_align = self._rhapi.db.option('_lapresults_align') or "center"
                lapresults_custom_col = int(self._rhapi.db.option('_lapresults_custom_col') or 0)
                results_uptime = int(self._rhapi.db.option('_results_uptime') or 40)
                is_timed = results_uptime > 0
            else:
                lap_results_config = pilot_config.get('lap_results', {})
                lapresults_row = int(lap_results_config.get('row', None) or self._rhapi.db.option('_lapresults_row') or 15)
                lapresults_align = lap_results_config.get('alignment', None) or self._rhapi.db.option('_lapresults_align') or "center"
                lapresults_custom_col = int(lap_results_config.get('custom_col', None) or self._rhapi.db.option('_lapresults_custom_col') or 0)

                # Check is_timed setting
                is_timed = lap_results_config.get('is_timed', None)
                if is_timed is None:
                    global_uptime = int(self._rhapi.db.option('_results_uptime') or 40)
                    is_timed = global_uptime > 0

                # Get uptime
                results_uptime = lap_results_config.get('uptime', None)
                if results_uptime is None:
                    results_uptime = int(self._rhapi.db.option('_results_uptime') or 40)
                else:
                    results_uptime = int(results_uptime)

            message = ""
            if self.get_pilot_behavior_setting(pilot_id, "_gap_mode") != "1":
                if gap_info.race.win_condition == WinCondition.FASTEST_CONSECUTIVE:
                    formatted_time1 = self._rhapi.utils.format_split_time_to_str(
                        gap_info.current.last_lap_time, "{m}:{s}.{d}"
                    )
                    formatted_time2 = self._rhapi.utils.format_split_time_to_str(
                        gap_info.current.consecutives, "{m}:{s}.{d}"
                    )
                    message = f"x {formatted_time1} | {gap_info.current.consecutives_base}/{formatted_time2} w"
                elif (
                    gap_info.race.win_condition == WinCondition.FASTEST_LAP
                    and gap_info.current.is_best
                ):
                    formatted_time = self._rhapi.utils.format_split_time_to_str(
                        gap_info.current.last_lap_time, "{m}:{s}.{d}"
                    )
                    message = f"x BEST LAP | {formatted_time} w"
                else:
                    formatted_time1 = self._rhapi.utils.format_split_time_to_str(
                        gap_info.current.last_lap_time, "{m}:{s}.{d}"
                    )
                    formatted_time2 = self._rhapi.utils.format_split_time_to_str(
                        gap_info.current.total_time_laps, "{m}:{s}.{d}"
                    )
                    message = f"x {formatted_time1} | {formatted_time2} w"

            elif gap_info.race.win_condition == WinCondition.FASTEST_CONSECUTIVE:
                formatted_time1 = self._rhapi.utils.format_split_time_to_str(
                    gap_info.current.last_lap_time, "{m}:{s}.{d}"
                )
                formatted_time2 = self._rhapi.utils.format_split_time_to_str(
                    gap_info.current.consecutives, "{m}:{s}.{d}"
                )
                message = f"x {formatted_time1} | {gap_info.current.consecutives_base}/{formatted_time2} w"

            elif gap_info.race.win_condition == WinCondition.FASTEST_LAP:
                if gap_info.next_rank.diff_time:
                    formatted_time = self._rhapi.utils.format_split_time_to_str(
                        gap_info.next_rank.diff_time, "{m}:{s}.{d}"
                    )
                    formatted_callsign = str.upper(gap_info.next_rank.callsign)
                    message = f"x {formatted_callsign} | +{formatted_time} w"

                elif gap_info.current.is_best_lap and gap_info.current.lap_number:
                    formatted_time = self._rhapi.utils.format_split_time_to_str(
                        gap_info.current.last_lap_time, "{m}:{s}.{d}"
                    )
                    message = f"x {self._rhapi.db.option('_leader_message')} | {formatted_time} w"

                elif gap_info.current.lap_number:
                    formatted_time = self._rhapi.utils.format_split_time_to_str(
                        gap_info.first_rank.diff_time, "{m}:{s}.{d}"
                    )
                    formatted_callsign = str.upper(gap_info.first_rank.callsign)
                    message = f"x {formatted_callsign} | +{formatted_time} w"

            else:
                if gap_info.next_rank.diff_time:
                    formatted_time = self._rhapi.utils.format_split_time_to_str(
                        gap_info.next_rank.diff_time, "{m}:{s}.{d}"
                    )
                    formatted_callsign = str.upper(gap_info.next_rank.callsign)
                    message = f"x {formatted_callsign} | +{formatted_time} w"

                elif gap_info.current.lap_number:
                    formatted_time = self._rhapi.utils.format_split_time_to_str(
                        gap_info.current.last_lap_time, "{m}:{s}.{d}"
                    )
                    message = f"x {self._rhapi.db.option('_leader_message')} | {formatted_time} w"

            start_col = self.calculate_osd_column(message, lapresults_align, lapresults_custom_col)

            uid = self.get_pilot_uid(pilot_id)

            # Send lap results atomically
            with self._queue_lock:
                self.set_send_uid(uid)
                self.send_osd_text(lapresults_row, start_col, message)
                self.send_display_osd()
                self.reset_send_uid()

            # Delayed clear in separate greenlet (only if timed mode)
            if is_timed and results_uptime > 0:
                def delayed_clear():
                    gevent.sleep(results_uptime * 1e-1)
                    gevent.sleep(0.1)  # Transmission buffer
                    self.send_osd_clear_batch(uid, lapresults_row)

                gevent.spawn(delayed_clear)

        def show_recent_laps(pilot_id, seat):
            """Display the last N lap times for a pilot"""
            # Check per-pilot enabled setting first
            pilot_config = self.get_pilot_osd_config(pilot_id)

            # Check if pilot is using global config
            use_global = pilot_config.get('use_global', True)

            if use_global:
                # Use global setting
                if self._rhapi.db.option('_show_recentlaps') != "1":
                    return
            else:
                # Use per-pilot setting
                recent_laps_config = pilot_config.get('recent_laps', {})
                pilot_recentlaps_enabled = recent_laps_config.get('enabled', None)

                if pilot_recentlaps_enabled is None:
                    # No per-pilot setting, fall back to global
                    if self._rhapi.db.option('_show_recentlaps') != "1":
                        return
                elif not pilot_recentlaps_enabled:
                    return  # Explicitly disabled for this pilot

            # Get per-pilot settings for recent laps display
            recentlaps_row = int(self.get_osd_setting(pilot_id, 'recent_laps', 'row', '_recentlaps_row', 11) or 11)
            recentlaps_align = self.get_osd_setting(pilot_id, 'recent_laps', 'alignment', '_recentlaps_align', 'left') or 'left'
            recentlaps_custom_col = int(self.get_osd_setting(pilot_id, 'recent_laps', 'custom_col', '_recentlaps_custom_col', 0) or 0)
            num_laps = int(self.get_osd_setting(pilot_id, 'recent_laps', 'num_laps', '_recentlaps_count', 3) or 3)

            # Get lap data for this pilot
            all_laps = self._rhapi.race.laps_raw
            all_pilot_laps = all_laps[seat] if seat < len(all_laps) else []

            if not all_pilot_laps:
                return

            # Get minimum lap time in milliseconds
            min_lap_sec = int(self._rhapi.db.option("MinLapSec") or 0)
            min_lap_ms = min_lap_sec * 1000

            # Aggregate laps: combine short laps with the next valid lap
            # Holeshot (index 0) is always included as-is
            # For subsequent laps, aggregate times until we reach min_lap_ms
            laps = []  # List of (display_index, aggregated_lap_time)
            pending_time = 0
            display_idx = 0

            for i, lap in enumerate(all_pilot_laps):
                lap_time = lap.get("lap_time", 0)
                if lap_time <= 0:
                    continue

                if i == 0:
                    # Holeshot is always included as-is
                    laps.append((display_idx, lap_time))
                    display_idx += 1
                else:
                    # Aggregate time
                    pending_time += lap_time
                    # Check if aggregated time meets minimum
                    if pending_time >= min_lap_ms:
                        laps.append((display_idx, pending_time))
                        display_idx += 1
                        pending_time = 0

            if not laps:
                return

            # Get the last N laps with valid times
            recent = laps[-num_laps:] if len(laps) >= num_laps else laps

            uid = self.get_pilot_uid(pilot_id)

            # Build and send recent lap messages
            # Get display mode and uptime settings (check per-pilot config first, then global)
            if use_global:
                recentlaps_uptime = int(self._rhapi.db.option('_recentlaps_uptime') or 5)
                # For global, check if uptime > 0 means timed mode
                is_timed = recentlaps_uptime > 0
            else:
                recent_laps_config = pilot_config.get('recent_laps', {})

                # Check is_timed setting
                is_timed = recent_laps_config.get('is_timed', None)
                if is_timed is None:
                    # Fall back to global - if uptime > 0, it's timed
                    global_uptime = int(self._rhapi.db.option('_recentlaps_uptime') or 5)
                    is_timed = global_uptime > 0

                # Get uptime
                recentlaps_uptime = recent_laps_config.get('uptime', None)
                if recentlaps_uptime is None:
                    recentlaps_uptime = int(self._rhapi.db.option('_recentlaps_uptime') or 5)
                else:
                    recentlaps_uptime = int(recentlaps_uptime)

            with self._queue_lock:
                self.set_send_uid(uid)

                # Clear the rows we'll use
                for i in range(num_laps):
                    self.send_clear_osd_row(recentlaps_row + i)

                # Display each recent lap
                # Display index 0 = HS (holeshot), index 1 = L1, index 2 = L2, etc.
                for i, (display_idx, lap_time_ms) in enumerate(recent):
                    # Convert lap_time (milliseconds) to total seconds with 2 decimal places
                    total_seconds = lap_time_ms / 1000.0
                    lap_time = f"{total_seconds:.2f}"
                    if display_idx == 0:
                        message = f"HS:{lap_time}"
                    else:
                        message = f"L{display_idx}:{lap_time}"
                    start_col = self.calculate_osd_column(message, recentlaps_align, recentlaps_custom_col)
                    self.send_osd_text(recentlaps_row + i, start_col, message)

                self.send_display_osd()
                self.reset_send_uid()

            # Delayed clear in separate greenlet (only if timed mode is enabled)
            if is_timed and recentlaps_uptime > 0:
                def delayed_clear():
                    gevent.sleep(recentlaps_uptime * 1e-1)
                    gevent.sleep(0.1)  # Transmission buffer
                    with self._queue_lock:
                        self.set_send_uid(uid)
                        for i in range(num_laps):
                            self.send_clear_osd_row(recentlaps_row + i)
                        self.send_display_osd()
                        self.reset_send_uid()

                gevent.spawn(delayed_clear)

        seats_finished = self._rhapi.race.seats_finished
        pilots_completion = {}
        for slot, pilot_id in self._rhapi.race.pilots.items():
            if pilot_id:
                pilots_completion[pilot_id] = seats_finished[slot]

        results = args["results"]["by_race_time"]
        for result in results:
            if (
                self._rhapi.db.pilot_attribute_value(result["pilot_id"], "elrs_active")
                == "1"
            ):
                if not pilots_completion[result["pilot_id"]]:
                    gevent.spawn(update_pos, result)

                    if result["pilot_id"] == args["pilot_id"]:
                        # Show lap results only after first full lap (laps > 0)
                        if result["laps"] > 0:
                            gevent.spawn(lap_results, result, args["gap_info"])
                        # Show recent laps for holeshot and all laps (laps >= 0)
                        seat = args.get("node_index", 0)
                        gevent.spawn(show_recent_laps, result["pilot_id"], seat)

    def onLapDelete(self, *_) -> None:
        """
        Update a pilot's OSD when a they have finished
        """
        if not self._backpack_connected:
            return

        def delete(pilot_id):
            uid = self.get_pilot_uid(pilot_id)
            self._queue_lock.acquire()
            self.set_send_uid(uid)
            self.send_clear_osd()
            self.send_display_osd()
            self.reset_send_uid()
            self._queue_lock.release()

        if self._rhapi.db.option("_results_mode") == "1":
            seat_pilots = self._rhapi.race.pilots
            for seat in seat_pilots:
                if (
                    seat_pilots[seat]
                    and self._rhapi.db.pilot_attribute_value(
                        seat_pilots[seat], "elrs_active"
                    )
                    == "1"
                ):
                    gevent.spawn(delete, seat_pilots[seat])

    def onRacePilotDone(self, args: dict) -> None:
        """
        Update a pilot's OSD when a they have finished
        """
        if not self._backpack_connected:
            return

        def done(result, win_condition):
            pilot_id = result["pilot_id"]
            pilot_config = self.get_pilot_osd_config(pilot_id)
            use_global = pilot_config.get('use_global', True)

            # Check per-pilot enabled setting for pilot_done
            if not use_global:
                pilot_done_config = pilot_config.get('pilot_done', {})
                pilot_done_enabled = pilot_done_config.get('enabled', None)
                if pilot_done_enabled is not None and not pilot_done_enabled:
                    return  # Skip if explicitly disabled for this pilot

            # Get per-pilot settings for pilot done message
            pilotdone_message_text = self._rhapi.db.option("_pilotdone_message") or "w FINISHED! x"

            if use_global:
                status_row = int(self._rhapi.db.option('_status_row') or 5)
                pilotdone_align = self._rhapi.db.option('_pilotdone_align') or "center"
                pilotdone_custom_col = int(self._rhapi.db.option('_pilotdone_custom_col') or 0)
                finish_uptime = int(self._rhapi.db.option('_finish_uptime') or 20)
                is_timed = finish_uptime > 0
            else:
                pilot_done_config = pilot_config.get('pilot_done', {})
                status_row = int(pilot_done_config.get('row', None) or self._rhapi.db.option('_status_row') or 5)
                pilotdone_align = pilot_done_config.get('alignment', None) or self._rhapi.db.option('_pilotdone_align') or "center"
                pilotdone_custom_col = int(pilot_done_config.get('custom_col', None) or self._rhapi.db.option('_pilotdone_custom_col') or 0)

                # Check is_timed setting
                is_timed = pilot_done_config.get('is_timed', None)
                if is_timed is None:
                    global_uptime = int(self._rhapi.db.option('_finish_uptime') or 20)
                    is_timed = global_uptime > 0

                # Get uptime
                finish_uptime = pilot_done_config.get('uptime', None)
                if finish_uptime is None:
                    finish_uptime = int(self._rhapi.db.option('_finish_uptime') or 20)
                else:
                    finish_uptime = int(finish_uptime)

            start_col = self.calculate_osd_column(pilotdone_message_text, pilotdone_align, pilotdone_custom_col)

            # Get per-pilot settings for results display
            results_row1 = int(self.get_osd_setting(pilot_id, 'results', 'row', '_results_row', 13) or 13)
            results_row2 = results_row1 + 1

            # Get per-pilot setting for current lap row (to clear it)
            currentlap_row = int(self.get_osd_setting(pilot_id, 'current_lap', 'row', '_currentlap_row', 0) or 0)

            # Check per-pilot enabled settings for results and lap_times
            pilot_results_enabled = self.get_osd_setting(pilot_id, 'results', 'enabled', None, None)
            if pilot_results_enabled is None:
                pilot_results_enabled = self.get_pilot_behavior_setting(pilot_id, "_results_mode") == "1"
            else:
                pilot_results_enabled = bool(pilot_results_enabled)

            pilot_laptimes_enabled = self.get_osd_setting(pilot_id, 'lap_times', 'enabled', '_show_laptimes', True)

            uid = self.get_pilot_uid(pilot_id)

            # Send pilot done message atomically
            with self._queue_lock:
                self.set_send_uid(uid)
                self.send_clear_osd_row(currentlap_row)
                self.send_clear_osd_row(status_row)
                self.send_osd_text(
                    status_row,
                    start_col,
                    pilotdone_message_text
                )

                if pilot_results_enabled:
                    # Get per-pilot settings for placement and win message
                    placement_align = self.get_osd_setting(pilot_id, 'results', 'alignment', '_placement_align', "center") or "center"
                    placement_custom_col = int(self.get_osd_setting(pilot_id, 'results', 'custom_col', '_placement_custom_col', 0) or 0)
                    placement_message = f"PLACEMENT: {result['position']}"
                    place_col = self.calculate_osd_column(placement_message, placement_align, placement_custom_col)
                    self.send_osd_text(results_row1, place_col, placement_message)

                    winmessage_align = self.get_osd_setting(pilot_id, 'results', 'alignment', '_winmessage_align', "center") or "center"
                    winmessage_custom_col = int(self.get_osd_setting(pilot_id, 'results', 'custom_col', '_winmessage_custom_col', 0) or 0)

                    if win_condition == WinCondition.FASTEST_CONSECUTIVE:
                        win_message = f"FASTEST {result['consecutives_base']} CONSEC: {result['consecutives']}"
                    elif win_condition == WinCondition.FASTEST_LAP:
                        win_message = f"FASTEST LAP: {result['fastest_lap']}"
                    elif win_condition == WinCondition.FIRST_TO_LAP_X:
                        win_message = f"TOTAL TIME: {result['total_time']}"
                    else:
                        win_message = f"LAPS COMPLETED: {result['laps']}"

                    win_col = self.calculate_osd_column(win_message, winmessage_align, winmessage_custom_col)
                    self.send_osd_text(results_row2, win_col, win_message)

                # Display lap times if enabled (use per-pilot setting already checked above)
                if pilot_laptimes_enabled:
                    # Get lap data for this pilot using results
                    raw_laps = result.get('laps_list', [])

                    # Get minimum lap time in milliseconds
                    min_lap_sec = int(self._rhapi.db.option("MinLapSec") or 0)
                    min_lap_ms = min_lap_sec * 1000

                    # Aggregate laps: combine short laps with the next valid lap
                    # Holeshot (index 0) is always included as-is
                    all_laps = []  # List of (display_index, aggregated_lap_time)
                    pending_time = 0
                    display_idx = 0

                    for i, lap in enumerate(raw_laps):
                        lap_time = lap.get("lap_time", 0)
                        if lap_time <= 0:
                            continue

                        if i == 0:
                            # Holeshot is always included as-is
                            all_laps.append((display_idx, lap_time))
                            display_idx += 1
                        else:
                            # Aggregate time
                            pending_time += lap_time
                            # Check if aggregated time meets minimum
                            if pending_time >= min_lap_ms:
                                all_laps.append((display_idx, pending_time))
                                display_idx += 1
                                pending_time = 0

                    # Get per-pilot settings for lap times
                    laptimes_row = int(self.get_osd_setting(pilot_id, 'lap_times', 'row', '_laptimes_row', 14) or 14)
                    laptimes_align = self.get_osd_setting(pilot_id, 'lap_times', 'alignment', '_laptimes_align', "center") or "center"
                    laptimes_custom_col = int(self.get_osd_setting(pilot_id, 'lap_times', 'custom_col', '_laptimes_custom_col', 0) or 0)

                    # Limit to first 5 laps to fit on screen
                    max_laps = min(len(all_laps), 5)

                    for idx in range(max_laps):
                        display_idx, lap_time_ms = all_laps[idx]
                        lap_time = self._rhapi.utils.format_split_time_to_str(
                            lap_time_ms, "{m}:{s}.{d}"
                        )
                        # Display index 0 = HS (holeshot), index 1 = L1, index 2 = L2, etc.
                        if display_idx == 0:
                            message = f"HS: {lap_time}"
                        else:
                            message = f"L{display_idx}: {lap_time}"
                        start_col = self.calculate_osd_column(message, laptimes_align, laptimes_custom_col)
                        self.send_osd_text(laptimes_row + idx, start_col, message)

                self.send_display_osd()
                self.reset_send_uid()

            # Delayed clear in separate greenlet (only if timed mode)
            if is_timed and finish_uptime > 0:
                def delayed_clear():
                    gevent.sleep(finish_uptime * 1e-1)
                    gevent.sleep(0.1)  # Transmission buffer
                    self.send_osd_clear_batch(uid, status_row)

                gevent.spawn(delayed_clear)

        results = args["results"]
        leaderboard = results[results["meta"]["primary_leaderboard"]]
        for result in leaderboard:
            if (
                self._rhapi.db.pilot_attribute_value(args["pilot_id"], "elrs_active")
                == "1"
            ) and (result["pilot_id"] == args["pilot_id"]):
                gevent.spawn(done, result, results["meta"]["win_condition"])
                break

    def onLapsClear(self, *_) -> None:
        """
        Removes data from pilot's OSD when laps are removed from the system
        """
        if not self._backpack_connected:
            return

        def clear(pilot_id):
            uid = self.get_pilot_uid(pilot_id)
            self._queue_lock.acquire()
            self.set_send_uid(uid)
            self.send_clear_osd()
            self.send_display_osd()
            self.reset_send_uid()
            self._queue_lock.release()

        seat_pilots = self._rhapi.race.pilots
        for seat in seat_pilots:
            if (
                seat_pilots[seat]
                and self._rhapi.db.pilot_attribute_value(
                    seat_pilots[seat], "elrs_active"
                )
                == "1"
            ):
                gevent.spawn(clear, seat_pilots[seat])

    def onSendMessage(self, args: dict | None = None) -> None:
        """
        Sends custom text to pilots of the active heat
        """
        if not self._backpack_connected:
            return

        if args is None:
            return

        def notify(pilot):
            uid = self.get_pilot_uid(pilot)
            pilot_config = self.get_pilot_osd_config(pilot)
            use_global = pilot_config.get('use_global', True)

            # Check per-pilot enabled setting
            if not use_global:
                announcement_config = pilot_config.get('announcement', {})
                pilot_announcement_enabled = announcement_config.get('enabled', None)
                if pilot_announcement_enabled is not None and not pilot_announcement_enabled:
                    return  # Skip if explicitly disabled for this pilot

            # Get per-pilot settings for announcement
            if use_global:
                announcement_row = int(self._rhapi.db.option('_announcement_row') or 3)
                announcement_align = self._rhapi.db.option('_announcement_align') or "center"
                announcement_custom_col = int(self._rhapi.db.option('_announcement_custom_col') or 0)
                announcement_uptime = int(self._rhapi.db.option('_announcement_uptime') or 50)
                is_timed = announcement_uptime > 0
            else:
                announcement_config = pilot_config.get('announcement', {})
                announcement_row = int(announcement_config.get('row', None) or self._rhapi.db.option('_announcement_row') or 3)
                announcement_align = announcement_config.get('alignment', None) or self._rhapi.db.option('_announcement_align') or "center"
                announcement_custom_col = int(announcement_config.get('custom_col', None) or self._rhapi.db.option('_announcement_custom_col') or 0)

                # Check is_timed setting
                is_timed = announcement_config.get('is_timed', None)
                if is_timed is None:
                    global_uptime = int(self._rhapi.db.option('_announcement_uptime') or 50)
                    is_timed = global_uptime > 0

                # Get uptime
                announcement_uptime = announcement_config.get('uptime', None)
                if announcement_uptime is None:
                    announcement_uptime = int(self._rhapi.db.option('_announcement_uptime') or 50)
                else:
                    announcement_uptime = int(announcement_uptime)

            decorated_message = f"x {str.upper(args['message'])} w"
            start_col = self.calculate_osd_column(decorated_message, announcement_align, announcement_custom_col)

            # Send announcement atomically
            with self._queue_lock:
                self.set_send_uid(uid)
                self.send_osd_text(
                    announcement_row,
                    start_col,
                    decorated_message
                )
                self.send_display_osd()
                self.reset_send_uid()

            # Delayed clear in separate greenlet (only if timed mode)
            if is_timed and announcement_uptime > 0:
                def delayed_clear():
                    gevent.sleep(announcement_uptime * 1e-1)
                    gevent.sleep(0.1)  # Transmission buffer
                    self.send_osd_clear_batch(uid, announcement_row)

                gevent.spawn(delayed_clear)

        seat_pilots = self._rhapi.race.pilots
        for seat in seat_pilots:
            if (
                seat_pilots[seat]
                and self._rhapi.db.pilot_attribute_value(
                    seat_pilots[seat], "elrs_active"
                )
                == "1"
            ):
                gevent.spawn(notify, seat_pilots[seat])
