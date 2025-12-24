# ELRS OSD Per-Pilot Configuration

This enhancement adds per-pilot OSD element configuration with a visual preview interface.

## Features

### 1. Web-Based Configuration Interface
- **URL**: `/elrs_osd_config`
- **Access**: Via quick button in ELRS Settings panel ("Configure OSD per Pilot")
- **Features**:
  - Select pilot from dropdown
  - Visual OSD grid preview (18 rows × 50 columns)
  - Drag-and-drop element positioning
  - Manual controls for row, alignment, and custom column
  - Live preview of element positions
  - Save per-pilot or reset to defaults

### 2. OSD Elements Configurable Per-Pilot
- Heat Name
- Class Name
- Event Name
- Race Stage Message ("ARM NOW")
- Race Start Message ("GO!")
- Race Finish Message ("FINISH LAP!")
- Race Stop Message ("LAND NOW!")
- Current Lap/Position
- Lap Results
- Announcement
- Leader Message
- Results

### 3. Configuration Storage
Per-pilot configurations are stored in the `elrs_osd_config` pilot attribute as JSON. The system:
- Checks for per-pilot configuration first
- Falls back to global settings if no custom config exists
- Allows complete customization per pilot while maintaining global defaults

## Implementation Details

### Files Added/Modified

#### New Files:
1. **`templates/osd_config.html`** - Web interface with OSD preview
2. **`osd_config_routes.py`** - Flask blueprint with API endpoints
3. **`OSD_CONFIG_README.md`** - This documentation

#### Modified Files:
1. **`__init__.py`**:
   - Imported `osd_config_routes`
   - Registered `elrs_osd_config` pilot attribute
   - Registered Flask blueprint
   - Added quick button to access configuration page

2. **`elrs_backpack.py`**:
   - Added `json` import
   - Added `get_pilot_osd_config(pilot_id)` - Retrieve per-pilot config
   - Added `get_osd_setting(pilot_id, element_id, setting_key, default_key, default_value)` - Get setting with fallback
   - Updated `onRaceStage()` - Uses per-pilot configuration
   - Updated `onRaceStart()` - Uses per-pilot configuration

### API Endpoints

#### `GET /elrs_osd_config/`
Renders the configuration page with pilot list.

#### `GET /elrs_osd_config/api/pilot/<pilot_id>`
Returns the OSD configuration for a specific pilot.

**Response**:
```json
{
  "success": true,
  "pilot_id": 1,
  "config": {
    "heat_name": {
      "row": 2,
      "alignment": "center",
      "custom_col": 0
    },
    "race_stage": {
      "row": 5,
      "alignment": "left",
      "custom_col": 10
    }
  }
}
```

#### `POST /elrs_osd_config/api/pilot/<pilot_id>`
Saves the OSD configuration for a specific pilot.

**Request Body**:
```json
{
  "config": {
    "heat_name": {
      "row": 2,
      "alignment": "center",
      "custom_col": 0
    }
  }
}
```

**Response**:
```json
{
  "success": true,
  "pilot_id": 1
}
```

#### `DELETE /elrs_osd_config/api/pilot/<pilot_id>`
Deletes per-pilot configuration (resets to global defaults).

## Usage

### For Race Directors

1. **Access the Configuration Page**:
   - Go to Settings → ELRS Backpack General Settings
   - Click "Configure OSD per Pilot" button
   - Or navigate directly to `/elrs_osd_config`

2. **Configure a Pilot**:
   - Select pilot from dropdown
   - Drag elements on the preview grid, OR
   - Use the controls panel to set:
     - Row (0-17)
     - Alignment (left, center, right, custom)
     - Custom column (0-49) when using custom alignment
   - Click "Save Configuration"

3. **Reset to Defaults**:
   - Click "Reset to Defaults" to clear pilot-specific settings
   - Pilot will use global settings from ELRS Backpack OSD Settings panel

### For Pilots

Each pilot can have their own OSD layout configured by the race director. The configuration:
- Persists across races and events
- Only affects that specific pilot
- Falls back to global settings if not configured

## Implementation Complete ✅

All event handlers have been updated to use per-pilot configuration:

- [x] `onRaceStage()` - Race stage message, heat/class/event name positioning
- [x] `onRaceStart()` - Race start message positioning
- [x] `onRaceFinish()` - Race finish message positioning
- [x] `onRaceStop()` - Race stop message and lap times positioning
- [x] `onRacePilotDone()` - Pilot done message and results positioning
- [x] `onRaceLapRecorded()` - Current lap/position and lap results positioning
- [x] `onSendMessage()` - Announcement positioning

### Implementation Pattern Used

Each handler now uses the `get_osd_setting()` method to retrieve per-pilot settings with automatic fallback to global settings:

**Example**:
```python
# Get per-pilot setting with global fallback
status_row = self.get_osd_setting(pilot_id, 'race_finish', 'row', '_status_row', default_value)
align = self.get_osd_setting(pilot_id, 'race_finish', 'alignment', '_racefinish_align', "center")
custom_col = self.get_osd_setting(pilot_id, 'race_finish', 'custom_col', '_racefinish_custom_col', 0)
```

### Element IDs Reference

Use these element IDs when calling `get_osd_setting()`:

| Element ID | Description |
|------------|-------------|
| `heat_name` | Heat name display |
| `class_name` | Class name display |
| `event_name` | Event name display |
| `race_stage` | "ARM NOW" message |
| `race_start` | "GO!" message |
| `race_finish` | "FINISH LAP!" message |
| `race_stop` | "LAND NOW!" message |
| `current_lap` | Current lap/position display |
| `lap_results` | Lap time/gap display |
| `announcement` | Announcement text |
| `leader` | "RACE LEADER" message |
| `results` | Post-race results |

### Setting Keys

Each element can have these settings:
- `row` - Row position (0-17)
- `alignment` - Text alignment ("left", "center", "right", "custom")
- `custom_col` - Column position when alignment is "custom" (0-49)

## Testing

1. Start RotorHazard server
2. Navigate to `/elrs_osd_config`
3. Select a pilot
4. Configure element positions
5. Save configuration
6. Verify configuration persists after page reload
7. Test in actual race to verify OSD displays correctly

## Technical Notes

- OSD grid: 18 rows × 50 columns (HDZero standard)
- Configuration stored as JSON in pilot attribute `elrs_osd_config`
- JavaScript handles drag-and-drop with grid snapping
- All API calls are asynchronous
- Per-pilot settings override global settings
- No per-pilot config = uses global settings (backward compatible)

## Future Enhancements

Possible improvements:
- Import/export configurations
- Copy configuration from one pilot to another
- Templates/presets for common layouts
- Per-race-class default configurations
- Visual indicators for which elements are enabled
- Text preview customization (show actual race data)
