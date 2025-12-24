"""
Flask blueprint for ELRS OSD configuration interface
"""
import json
import logging

import gevent
from flask import Blueprint, render_template, request, jsonify

logger = logging.getLogger(__name__)

# Create blueprint
osd_config_bp = Blueprint(
    'elrs_osd_config',
    __name__,
    template_folder='templates',
    url_prefix='/elrs_osd_config'
)


def initialize_routes(rhapi, controller):
    """
    Initialize the routes with access to RHAPI and the ELRS controller

    :param rhapi: RotorHazard API instance
    :param controller: ELRSBackpack controller instance
    """

    @osd_config_bp.route('/')
    def osd_config_page():
        """Render the OSD configuration page"""
        pilots = rhapi.db.pilots

        # Get global database settings for defaults
        global_db_settings = {
            'heat_name': {
                'row': rhapi.db.option('_heatname_row', 2),
                'alignment': rhapi.db.option('_heatname_align', 'center'),
                'custom_col': rhapi.db.option('_heatname_custom_col', 0),
                'enabled': rhapi.db.option('_heat_name', True)
            },
            'class_name': {
                'row': rhapi.db.option('_classname_row', 1),
                'alignment': rhapi.db.option('_classname_align', 'center'),
                'custom_col': rhapi.db.option('_classname_custom_col', 0),
                'enabled': rhapi.db.option('_class_name', True)
            },
            'event_name': {
                'row': rhapi.db.option('_eventname_row', 0),
                'alignment': rhapi.db.option('_eventname_align', 'center'),
                'custom_col': rhapi.db.option('_eventname_custom_col', 0),
                'enabled': rhapi.db.option('_event_name', True)
            },
            'race_stage': {
                'row': rhapi.db.option('_status_row', 5),
                'alignment': rhapi.db.option('_racestage_align', 'center'),
                'custom_col': rhapi.db.option('_racestage_custom_col', 0),
                'enabled': True
            },
            'race_start': {
                'row': rhapi.db.option('_status_row', 5),
                'alignment': rhapi.db.option('_racestart_align', 'center'),
                'custom_col': rhapi.db.option('_racestart_custom_col', 0),
                'enabled': True,
                'uptime': rhapi.db.option('_racestart_uptime', 5)
            },
            'race_finish': {
                'row': rhapi.db.option('_status_row', 5),
                'alignment': rhapi.db.option('_racefinish_align', 'center'),
                'custom_col': rhapi.db.option('_racefinish_custom_col', 0),
                'enabled': True,
                'uptime': rhapi.db.option('_finish_uptime', 20)
            },
            'race_stop': {
                'row': rhapi.db.option('_status_row', 5),
                'alignment': rhapi.db.option('_racestop_align', 'center'),
                'custom_col': rhapi.db.option('_racestop_custom_col', 0),
                'enabled': True
            },
            'current_lap': {
                'row': rhapi.db.option('_currentlap_row', 0),
                'alignment': rhapi.db.option('_currentlap_align', 'left'),
                'custom_col': rhapi.db.option('_currentlap_custom_col', 0),
                'enabled': rhapi.db.option('_position_mode', True)
            },
            'lap_results': {
                'row': rhapi.db.option('_lapresults_row', 15),
                'alignment': rhapi.db.option('_lapresults_align', 'center'),
                'custom_col': rhapi.db.option('_lapresults_custom_col', 0),
                'enabled': True,
                'uptime': rhapi.db.option('_results_uptime', 40)
            },
            'announcement': {
                'row': rhapi.db.option('_announcement_row', 3),
                'alignment': rhapi.db.option('_announcement_align', 'center'),
                'custom_col': rhapi.db.option('_announcement_custom_col', 0),
                'enabled': True,
                'uptime': rhapi.db.option('_announcement_uptime', 50)
            },
            'leader': {
                'row': rhapi.db.option('_status_row', 5),
                'alignment': rhapi.db.option('_leader_align', 'center'),
                'custom_col': rhapi.db.option('_leader_custom_col', 0),
                'enabled': True
            },
            'results': {
                'row': rhapi.db.option('_results_row', 13),
                'alignment': rhapi.db.option('_placement_align', 'center'),
                'custom_col': rhapi.db.option('_placement_custom_col', 0),
                'enabled': rhapi.db.option('_post_results', True)
            },
            'lap_times': {
                'row': rhapi.db.option('_laptimes_row', 14),
                'alignment': rhapi.db.option('_laptimes_align', 'center'),
                'custom_col': rhapi.db.option('_laptimes_custom_col', 0),
                'enabled': rhapi.db.option('_show_laptimes', True),
                'uptime': rhapi.db.option('_laptimes_uptime', 100)
            },
            'behavior': {
                '_round_num': rhapi.db.option('_round_num', False),
                '_gap_mode': rhapi.db.option('_gap_mode', False),
                '_results_mode': rhapi.db.option('_results_mode', False)
            },
            'messages': {
                '_racestage_message': rhapi.db.option('_racestage_message', 'w ARM NOW x'),
                '_racestart_message': rhapi.db.option('_racestart_message', 'w   GO!   x'),
                '_racefinish_message': rhapi.db.option('_racefinish_message', 'w FINISH LAP! x'),
                '_racestop_message': rhapi.db.option('_racestop_message', 'w  LAND NOW!  x'),
                '_leader_message': rhapi.db.option('_leader_message', 'RACE LEADER')
            }
        }

        return render_template('osd_config.html', pilots=pilots, global_db_settings=json.dumps(global_db_settings))

    @osd_config_bp.route('/api/pilot/<int:pilot_id>', methods=['GET'])
    def get_pilot_config(pilot_id):
        """Get OSD configuration for a specific pilot"""
        try:
            # Get pilot attribute for OSD config
            config_json = rhapi.db.pilot_attribute_value(pilot_id, 'elrs_osd_config')

            if config_json:
                config = json.loads(config_json)
            else:
                # Return empty config if none exists
                config = {}

            return jsonify({
                'success': True,
                'pilot_id': pilot_id,
                'config': config
            })
        except Exception as e:
            logger.error(f"Error getting pilot config: {e}")
            return jsonify({
                'success': False,
                'error': str(e)
            }), 500

    @osd_config_bp.route('/api/pilot/<int:pilot_id>', methods=['POST'])
    def save_pilot_config(pilot_id):
        """Save OSD configuration for a specific pilot"""
        try:
            data = request.get_json()
            config = data.get('config', {})

            # Serialize config to JSON
            config_json = json.dumps(config)

            # Save as pilot attribute
            rhapi.db.pilot_alter(pilot_id, attributes={
                'elrs_osd_config': config_json
            })

            logger.info(f"Saved OSD config for pilot {pilot_id}")

            return jsonify({
                'success': True,
                'pilot_id': pilot_id
            })
        except Exception as e:
            logger.error(f"Error saving pilot config: {e}")
            return jsonify({
                'success': False,
                'error': str(e)
            }), 500

    @osd_config_bp.route('/api/pilot/<int:pilot_id>', methods=['DELETE'])
    def delete_pilot_config(pilot_id):
        """Delete OSD configuration for a specific pilot (reset to defaults)"""
        try:
            # Clear the pilot attribute
            rhapi.db.pilot_alter(pilot_id, attributes={
                'elrs_osd_config': None
            })

            logger.info(f"Deleted OSD config for pilot {pilot_id}")

            return jsonify({
                'success': True,
                'pilot_id': pilot_id
            })
        except Exception as e:
            logger.error(f"Error deleting pilot config: {e}")
            return jsonify({
                'success': False,
                'error': str(e)
            }), 500

    @osd_config_bp.route('/api/global', methods=['GET'])
    def get_global_config():
        """Get global OSD configuration"""
        try:
            # Global config is stored as an option
            config_json = rhapi.db.option('elrs_global_osd_config')

            if config_json:
                config = json.loads(config_json)
            else:
                # Return empty config if none exists
                config = {}

            return jsonify({
                'success': True,
                'config': config
            })
        except Exception as e:
            logger.error(f"Error getting global config: {e}")
            return jsonify({
                'success': False,
                'error': str(e)
            }), 500

    @osd_config_bp.route('/api/global', methods=['POST'])
    def save_global_config():
        """Save global OSD configuration"""
        try:
            data = request.get_json()
            config = data.get('config', {})

            # Serialize config to JSON
            config_json = json.dumps(config)

            # Save as database option
            rhapi.db.option_set('elrs_global_osd_config', config_json)

            # Also update the individual database options used by main ELRS Backpack OSD Settings
            # Map element IDs to their corresponding database option keys
            element_db_mapping = {
                'heat_name': {
                    'row': '_heatname_row',
                    'alignment': '_heatname_align',
                    'custom_col': '_heatname_custom_col',
                    'enabled': '_heat_name'
                },
                'class_name': {
                    'row': '_classname_row',
                    'alignment': '_classname_align',
                    'custom_col': '_classname_custom_col',
                    'enabled': '_class_name'
                },
                'event_name': {
                    'row': '_eventname_row',
                    'alignment': '_eventname_align',
                    'custom_col': '_eventname_custom_col',
                    'enabled': '_event_name'
                },
                'race_stage': {
                    'row': '_status_row',
                    'alignment': '_racestage_align',
                    'custom_col': '_racestage_custom_col'
                },
                'race_start': {
                    'row': '_status_row',
                    'alignment': '_racestart_align',
                    'custom_col': '_racestart_custom_col'
                },
                'race_finish': {
                    'row': '_status_row',
                    'alignment': '_racefinish_align',
                    'custom_col': '_racefinish_custom_col'
                },
                'race_stop': {
                    'row': '_status_row',
                    'alignment': '_racestop_align',
                    'custom_col': '_racestop_custom_col'
                },
                'current_lap': {
                    'row': '_currentlap_row',
                    'alignment': '_currentlap_align',
                    'custom_col': '_currentlap_custom_col',
                    'enabled': '_position_mode'
                },
                'lap_results': {
                    'row': '_lapresults_row',
                    'alignment': '_lapresults_align',
                    'custom_col': '_lapresults_custom_col'
                },
                'announcement': {
                    'row': '_announcement_row',
                    'alignment': '_announcement_align',
                    'custom_col': '_announcement_custom_col'
                },
                'leader': {
                    'row': '_status_row',
                    'alignment': '_leader_align',
                    'custom_col': '_leader_custom_col'
                },
                'results': {
                    'row': '_results_row',
                    'alignment': '_placement_align',
                    'custom_col': '_placement_custom_col',
                    'enabled': '_post_results'
                },
                'lap_times': {
                    'row': '_laptimes_row',
                    'alignment': '_laptimes_align',
                    'custom_col': '_laptimes_custom_col',
                    'enabled': '_show_laptimes'
                }
            }

            # Update each element's settings in the database
            for element_id, element_config in config.items():
                if element_id in element_db_mapping:
                    mapping = element_db_mapping[element_id]

                    # Update row if present
                    if 'row' in element_config and 'row' in mapping:
                        rhapi.db.option_set(mapping['row'], element_config['row'])

                    # Update alignment if present
                    if 'alignment' in element_config and 'alignment' in mapping:
                        rhapi.db.option_set(mapping['alignment'], element_config['alignment'])

                    # Update custom_col if present
                    if 'custom_col' in element_config and 'custom_col' in mapping:
                        rhapi.db.option_set(mapping['custom_col'], element_config['custom_col'])

                    # Update enabled if present
                    if 'enabled' in element_config and 'enabled' in mapping:
                        rhapi.db.option_set(mapping['enabled'], element_config['enabled'])

            # Handle global behavior options
            if '_round_num' in config:
                rhapi.db.option_set('_round_num', config['_round_num'])
            if '_gap_mode' in config:
                rhapi.db.option_set('_gap_mode', config['_gap_mode'])
            if '_results_mode' in config:
                rhapi.db.option_set('_results_mode', config['_results_mode'])

            # Handle message content fields
            message_keys = [
                '_racestage_message',
                '_racestart_message',
                '_racefinish_message',
                '_racestop_message',
                '_leader_message'
            ]
            for message_key in message_keys:
                if message_key in config:
                    rhapi.db.option_set(message_key, config[message_key])

            logger.info("Saved global OSD config and updated main settings")

            return jsonify({
                'success': True
            })
        except Exception as e:
            logger.error(f"Error saving global config: {e}")
            return jsonify({
                'success': False,
                'error': str(e)
            }), 500

    @osd_config_bp.route('/api/pilot/<int:pilot_id>/clear', methods=['POST'])
    def clear_pilot_screen(pilot_id):
        """Clear/blank a pilot's OSD screen"""
        try:
            # Get pilot's UID using controller method (handles both text bind phrases and numeric UIDs)
            uid = controller.get_pilot_uid(pilot_id)

            # Send clear screen command
            with controller._queue_lock:
                controller.set_send_uid(uid)
                controller.send_clear_osd()
                controller.send_display_osd()
                controller.reset_send_uid()

            logger.info(f"Cleared screen for pilot {pilot_id}")

            return jsonify({
                'success': True,
                'pilot_id': pilot_id
            })
        except Exception as e:
            logger.error(f"Error clearing screen for pilot {pilot_id}: {e}")
            return jsonify({
                'success': False,
                'error': str(e)
            }), 500

    @osd_config_bp.route('/api/pilot/<int:pilot_id>/test_message', methods=['POST'])
    def test_message(pilot_id):
        """Send a test message to a pilot's OSD"""
        try:
            data = request.get_json()
            message = data.get('message', '')
            element_id = data.get('element_id')

            if not message:
                return jsonify({
                    'success': False,
                    'error': 'Message text is required'
                }), 400

            if not element_id:
                return jsonify({
                    'success': False,
                    'error': 'Element ID is required'
                }), 400

            # Load pilot's OSD config
            config_json = rhapi.db.pilot_attribute_value(pilot_id, 'elrs_osd_config')
            if config_json:
                pilot_config = json.loads(config_json)
            else:
                pilot_config = {}

            # Load global config as fallback
            global_config_json = rhapi.db.option('elrs_global_osd_config')
            if global_config_json:
                global_config = json.loads(global_config_json)
            else:
                global_config = {}

            # Get element config (pilot config takes precedence)
            element_config = pilot_config.get(element_id) if pilot_config else None
            if not element_config:
                element_config = global_config.get(element_id, {})

            # Extract row, alignment, and custom_col
            row = element_config.get('row', 9)
            alignment = element_config.get('alignment', 'center')
            custom_col = element_config.get('custom_col', 0)

            # Calculate actual column position based on alignment
            if alignment == 'custom':
                col = custom_col
            else:
                # Use controller's calculate_osd_column method
                col = controller.calculate_osd_column(message, alignment, custom_col)

            # Get pilot's UID using controller method (handles both text bind phrases and numeric UIDs)
            uid = controller.get_pilot_uid(pilot_id)

            # Send test message
            controller.send_osd_message_batch(uid, row, col, message)

            logger.info(f"Sent test message to pilot {pilot_id} at row {row}, col {col}: {message}")

            return jsonify({
                'success': True,
                'pilot_id': pilot_id,
                'message': message,
                'row': row,
                'col': col
            })
        except Exception as e:
            logger.error(f"Error sending test message to pilot {pilot_id}: {e}")
            return jsonify({
                'success': False,
                'error': str(e)
            }), 500

    @osd_config_bp.route('/api/pilot/<int:pilot_id>/test_element/<element_id>', methods=['POST'])
    def test_element(pilot_id, element_id):
        """Send a realistic test message for an OSD element using actual formatting"""
        try:
            # Generate test message using controller's test_element method
            result = controller.test_element(pilot_id, element_id)

            if not result.get('success'):
                return jsonify(result), 400

            # Get pilot's UID
            uid = controller.get_pilot_uid(pilot_id)

            # Send all messages to the pilot's OSD
            messages = result.get('messages', [])
            with controller._queue_lock:
                controller.set_send_uid(uid)
                controller.send_clear_osd()

                for msg in messages:
                    controller.send_osd_text(msg['row'], msg['col'], msg['message'])

                controller.send_display_osd()
                controller.reset_send_uid()

            # Load pilot's OSD config
            config_json = rhapi.db.pilot_attribute_value(pilot_id, 'elrs_osd_config')
            pilot_config = json.loads(config_json) if config_json else {}

            # Load global config
            global_config_json = rhapi.db.option('elrs_global_osd_config')
            global_config = json.loads(global_config_json) if global_config_json else {}

            # Get element config: pilot config first, then global config
            if pilot_config and element_id in pilot_config:
                element_config = pilot_config.get(element_id, {})
            else:
                element_config = global_config.get(element_id, {})

            # Get display mode and duration from config
            is_timed = element_config.get('is_timed', False)
            uptime_deciseconds = int(element_config.get('uptime', 0))

            # Spawn delayed clear if timed mode and uptime > 0
            if is_timed and uptime_deciseconds > 0:
                def delayed_clear():
                    # Wait for configured uptime
                    gevent.sleep(uptime_deciseconds * 1e-1)
                    # Add transmission buffer (100ms) to ensure display packets fully transmit
                    gevent.sleep(0.1)
                    # Clear the message atomically
                    with controller._queue_lock:
                        controller.set_send_uid(uid)
                        controller.send_clear_osd()
                        controller.send_display_osd()
                        controller.reset_send_uid()

                gevent.spawn(delayed_clear)
                logger.info(f"Sent test for element '{element_id}' to pilot {pilot_id} (clearing after {uptime_deciseconds * 0.1}s): {messages}")
            else:
                logger.info(f"Sent test for element '{element_id}' to pilot {pilot_id} (static): {messages}")

            return jsonify({
                'success': True,
                'pilot_id': pilot_id,
                'element_id': element_id,
                'messages': messages
            })
        except Exception as e:
            logger.error(f"Error sending test element to pilot {pilot_id}: {e}")
            return jsonify({
                'success': False,
                'error': str(e)
            }), 500

    @osd_config_bp.route('/api/pilot/<int:pilot_id>/uid', methods=['GET'])
    def get_pilot_uid(pilot_id):
        """Get pilot's ELRS BP Bind Phrase (UID)"""
        try:
            # Get pilot attribute for comm_elrs
            uid = rhapi.db.pilot_attribute_value(pilot_id, 'comm_elrs') or ''

            return jsonify({
                'success': True,
                'pilot_id': pilot_id,
                'uid': uid
            })
        except Exception as e:
            logger.error(f"Error getting pilot UID: {e}")
            return jsonify({
                'success': False,
                'error': str(e)
            }), 500

    @osd_config_bp.route('/api/pilot/<int:pilot_id>/uid', methods=['POST'])
    def set_pilot_uid(pilot_id):
        """Set pilot's ELRS BP Bind Phrase"""
        try:
            data = request.get_json()
            bind_phrase = data.get('uid', '').strip()

            # Update pilot's comm_elrs attribute (can be any text string)
            rhapi.db.pilot_alter(pilot_id, attributes={
                'comm_elrs': bind_phrase
            })

            logger.info(f"Updated bind phrase for pilot {pilot_id}: {bind_phrase}")

            return jsonify({
                'success': True,
                'pilot_id': pilot_id,
                'uid': bind_phrase
            })
        except Exception as e:
            logger.error(f"Error setting pilot UID: {e}")
            return jsonify({
                'success': False,
                'error': str(e)
            }), 500

    @osd_config_bp.route('/api/calculate_column', methods=['POST'])
    def calculate_column():
        """
        Calculate OSD column position using the same logic as the backend.
        This ensures the preview matches actual OSD display.

        The 'column' parameter meaning varies by alignment:
        - left: column = left edge position
        - right: column = right edge position
        - center: column = center position

        Request body:
        {
            "text": "RACE LEADER | 0:42.1",
            "alignment": "right",
            "row": 5,
            "column": 49
        }

        Returns:
        {
            "success": true,
            "left_column": 28,
            "row": 5,
            "text_length": 22
        }
        """
        try:
            data = request.get_json()
            text = data.get('text', '')
            alignment = data.get('alignment', 'left')
            row = int(data.get('row', 0))
            column = int(data.get('column', 0))

            # Validate and clamp column based on alignment type
            # HDZero OSD has 50 columns (0-49)
            if alignment == 'right':
                # For right alignment, the column is the right edge
                # Ensure it doesn't exceed the last column (49)
                column = min(column, 49)
            elif alignment == 'left':
                # For left alignment, the column is the left edge
                # Ensure it doesn't exceed the last column (49)
                column = min(column, 49)
            elif alignment == 'center':
                # For center alignment, the column is the center position
                # Ensure it doesn't exceed the last column (49)
                column = min(column, 49)

            # Ensure column is not negative
            column = max(0, column)

            # Use the controller's calculate_osd_column method
            # This returns the LEFT column position (where text starts)
            left_column = controller.calculate_osd_column(text, alignment, column)
            text_length = controller.get_visible_text_length(text)

            return jsonify({
                'success': True,
                'left_column': left_column,
                'row': row,
                'text_length': text_length
            })
        except Exception as e:
            logger.error(f"Error calculating column: {e}")
            return jsonify({
                'success': False,
                'error': str(e)
            }), 500

    return osd_config_bp
