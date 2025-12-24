import logging
import json

import RHAPI
from eventmanager import Evt
from RHUI import UIField, UIFieldSelectOption, UIFieldType

from .connections import ConnectionTypeEnum
from .elrs_backpack import ELRSBackpack
from .osd_config_routes import initialize_routes

logger = logging.getLogger(__name__)


def initialize(rhapi: RHAPI.RHAPI):

    controller = ELRSBackpack("elrs", "ELRS", rhapi)

    # Define alignment options for SELECT dropdowns
    alignment_opts = [
        UIFieldSelectOption(value="left", label="Left"),
        UIFieldSelectOption(value="center", label="Center"),
        UIFieldSelectOption(value="right", label="Right")
    ]

    # Event handler to sync main settings changes back to global config
    def sync_main_settings_to_global(args):
        """Sync changes from main ELRS Backpack OSD Settings to global config"""
        option_name = args.get('option')

        # List of OSD-related option names we care about
        osd_options = [
            '_heatname_row', '_heatname_align', '_heatname_custom_col', '_heat_name',
            '_classname_row', '_classname_align', '_classname_custom_col', '_class_name',
            '_eventname_row', '_eventname_align', '_eventname_custom_col', '_event_name',
            '_status_row', '_racestage_align', '_racestage_custom_col',
            '_racestart_align', '_racestart_custom_col',
            '_racefinish_align', '_racefinish_custom_col',
            '_racestop_align', '_racestop_custom_col',
            '_currentlap_row', '_currentlap_align', '_currentlap_custom_col', '_position_mode',
            '_lapresults_row', '_lapresults_align', '_lapresults_custom_col',
            '_announcement_row', '_announcement_align', '_announcement_custom_col',
            '_leader_align', '_leader_custom_col',
            '_results_row', '_placement_align', '_placement_custom_col', '_post_results',
            '_laptimes_row', '_laptimes_align', '_laptimes_custom_col', '_show_laptimes',
            '_recentlaps_row', '_recentlaps_align', '_recentlaps_custom_col', '_show_recentlaps', '_recentlaps_count'
        ]

        # Only sync if an OSD-related option was changed
        if option_name in osd_options:
            try:
                # Get or create global config
                config_json = rhapi.db.option('elrs_global_osd_config')
                if config_json:
                    global_config = json.loads(config_json)
                else:
                    global_config = {}

                # Map option names to element IDs and setting keys
                option_to_element = {
                    '_heatname_row': ('heat_name', 'row'),
                    '_heatname_align': ('heat_name', 'alignment'),
                    '_heatname_custom_col': ('heat_name', 'custom_col'),
                    '_heat_name': ('heat_name', 'enabled'),
                    '_classname_row': ('class_name', 'row'),
                    '_classname_align': ('class_name', 'alignment'),
                    '_classname_custom_col': ('class_name', 'custom_col'),
                    '_class_name': ('class_name', 'enabled'),
                    '_eventname_row': ('event_name', 'row'),
                    '_eventname_align': ('event_name', 'alignment'),
                    '_eventname_custom_col': ('event_name', 'custom_col'),
                    '_event_name': ('event_name', 'enabled'),
                    '_status_row': ('race_stage', 'row'),  # Also affects race_start, race_finish, race_stop, leader
                    '_racestage_align': ('race_stage', 'alignment'),
                    '_racestage_custom_col': ('race_stage', 'custom_col'),
                    '_racestart_align': ('race_start', 'alignment'),
                    '_racestart_custom_col': ('race_start', 'custom_col'),
                    '_racefinish_align': ('race_finish', 'alignment'),
                    '_racefinish_custom_col': ('race_finish', 'custom_col'),
                    '_racestop_align': ('race_stop', 'alignment'),
                    '_racestop_custom_col': ('race_stop', 'custom_col'),
                    '_currentlap_row': ('current_lap', 'row'),
                    '_currentlap_align': ('current_lap', 'alignment'),
                    '_currentlap_custom_col': ('current_lap', 'custom_col'),
                    '_position_mode': ('current_lap', 'enabled'),
                    '_lapresults_row': ('lap_results', 'row'),
                    '_lapresults_align': ('lap_results', 'alignment'),
                    '_lapresults_custom_col': ('lap_results', 'custom_col'),
                    '_announcement_row': ('announcement', 'row'),
                    '_announcement_align': ('announcement', 'alignment'),
                    '_announcement_custom_col': ('announcement', 'custom_col'),
                    '_leader_align': ('leader', 'alignment'),
                    '_leader_custom_col': ('leader', 'custom_col'),
                    '_results_row': ('results', 'row'),
                    '_placement_align': ('results', 'alignment'),
                    '_placement_custom_col': ('results', 'custom_col'),
                    '_post_results': ('results', 'enabled'),
                    '_laptimes_row': ('lap_times', 'row'),
                    '_laptimes_align': ('lap_times', 'alignment'),
                    '_laptimes_custom_col': ('lap_times', 'custom_col'),
                    '_show_laptimes': ('lap_times', 'enabled'),
                    '_recentlaps_row': ('recent_laps', 'row'),
                    '_recentlaps_align': ('recent_laps', 'alignment'),
                    '_recentlaps_custom_col': ('recent_laps', 'custom_col'),
                    '_show_recentlaps': ('recent_laps', 'enabled'),
                    '_recentlaps_count': ('recent_laps', 'num_laps')
                }

                if option_name in option_to_element:
                    element_id, setting_key = option_to_element[option_name]
                    value = args.get('value')

                    # Ensure element exists in global config
                    if element_id not in global_config:
                        global_config[element_id] = {}

                    # Update the setting
                    global_config[element_id][setting_key] = value

                    # Special handling for _status_row - update all status elements
                    if option_name == '_status_row':
                        for status_element in ['race_stage', 'race_start', 'race_finish', 'race_stop', 'leader']:
                            if status_element not in global_config:
                                global_config[status_element] = {}
                            global_config[status_element]['row'] = value

                    # Save updated global config
                    rhapi.db.option_set('elrs_global_osd_config', json.dumps(global_config))
                    logger.info(f"Synced {option_name} to global OSD config")
            except Exception as e:
                logger.error(f"Error syncing option {option_name} to global config: {e}")

    rhapi.events.on(Evt.VRX_INITIALIZE, controller.register_handlers)
    rhapi.events.on(Evt.PILOT_ALTER, controller.pilot_alter)
    rhapi.events.on(
        Evt.STARTUP, controller.start_recieve_loop, name="start_recieve_loop"
    )
    rhapi.events.on(Evt.OPTION_SET, sync_main_settings_to_global)
    rhapi.events.on(Evt.STARTUP, controller.start_connection, name="start_connection")

    #
    # Setup UI
    #

    elrs_bindphrase = UIField(
        name="comm_elrs", label="ELRS BP Bind Phrase", field_type=UIFieldType.TEXT
    )
    rhapi.fields.register_pilot_attribute(elrs_bindphrase)

    active = UIField("elrs_active", "Enable ELRS OSD", field_type=UIFieldType.CHECKBOX)
    rhapi.fields.register_pilot_attribute(active)

    rhapi.ui.register_panel(
        "elrs_settings", "ELRS Backpack General Settings", "settings", order=0
    )

    rhapi.ui.register_panel(
        "elrs_osd_config_link", "ELRS Backpack OSD Settings", "settings", order=0
    )

    rhapi.ui.register_markdown(
        "elrs_osd_config_link",
        "osd_config_link_button",
        '<p>Configure OSD element positions globally and individually for each pilot.</p><a href="/elrs_osd_config" target="_blank" class="btn btn-primary">Open OSD Configuration Page</a>'
    )

    #
    # Check Boxes
    #

    _race_start = UIField(
        "_race_start",
        "Start Race from Transmitter",
        desc="Allows the race director to remotely start races",
        field_type=UIFieldType.CHECKBOX,
    )
    rhapi.fields.register_option(_race_start, "elrs_settings")

    _race_stop = UIField(
        "_race_stop",
        "Stop Race from Transmitter",
        desc="Allows the race director to remotely stop races",
        field_type=UIFieldType.CHECKBOX,
    )
    rhapi.fields.register_option(_race_stop, "elrs_settings")

    _autosave_on_stop = UIField(
        "_autosave_on_stop",
        "Autosave on stop",
        desc="Automatically save the race when stopping from the transmitter",
        field_type=UIFieldType.CHECKBOX,
        value="0",
    )
    rhapi.fields.register_option(_autosave_on_stop, "elrs_settings")

    _socket_ip = UIField(
        "_socket_ip",
        "ELRS Netpack Address",
        desc="Hostanme or IP Address of the ELRS Netpack",
        value="elrs-netpack.local",
        field_type=UIFieldType.TEXT,
    )
    rhapi.fields.register_option(_socket_ip, "elrs_settings")

    conn_opts = [UIFieldSelectOption(value=None, label="")]
    for type_ in ConnectionTypeEnum:
        race_selection = UIFieldSelectOption(value=type_.id_, label=type_.name)
        conn_opts.append(race_selection)

    _conn_opt = UIField(
        "_conn_opt",
        "Backback Connection Type",
        desc="Select the type of connection to use for the backpack",
        field_type=UIFieldType.SELECT,
        options=conn_opts,
    )
    rhapi.fields.register_option(_conn_opt, "elrs_settings")

    # Note: OSD configuration options (including Recent Laps settings) are now managed
    # through the OSD Configuration Page at /elrs_osd_config

    #
    # Quick Buttons
    #

    rhapi.ui.register_quickbutton(
        "elrs_settings",
        "bp_connect",
        "Backpack Connect",
        controller.start_connection,
    )
    rhapi.ui.register_quickbutton(
        "elrs_settings",
        "bp_disconnect",
        "Backpack Disconnect",
        controller.disconnect,
    )
    rhapi.ui.register_quickbutton(
        "elrs_settings", "enable_bind", "Start Backpack Bind", controller.activate_bind
    )

    rhapi.ui.register_quickbutton(
        "elrs_settings",
        "test_osd",
        "Test Bound Backpack's OSD",
        controller.test_bind_osd,
    )
    rhapi.ui.register_quickbutton(
        "elrs_settings", "enable_wifi", "Start Backpack WiFi", controller.activate_wifi
    )

    #
    # Register OSD Configuration Storage
    #
    osd_config_field = UIField(
        "elrs_osd_config",
        "OSD Configuration (JSON)",
        field_type=UIFieldType.TEXT,
        private=True
    )
    rhapi.fields.register_pilot_attribute(osd_config_field)

    #
    # Register Flask Blueprint for OSD Configuration Page
    #
    try:
        blueprint = initialize_routes(rhapi, controller)
        rhapi.ui.blueprint_add(blueprint)
        logger.info("ELRS OSD Configuration page registered at /elrs_osd_config")
    except Exception as e:
        logger.error(f"Failed to register OSD configuration page: {e}")
