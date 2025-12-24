# ELRS OSD Per-Pilot Configuration

This enhancement adds per-pilot OSD element configuration with a visual preview interface.

## Features

### 1. Web-Based Configuration Interface
- **URL**: `/elrs_osd_config`
- **Access**: Via quick button in ELRS Settings panel ("Configure OSD per Pilot")

![OSD Configuration Interface](../../docs/osd-configuration.png)

- **Features**:
  - Select pilot from dropdown
  - Visual OSD grid preview (18 rows × 50 columns)
  - Drag-and-drop element positioning
  - Manual controls for row, alignment, and custom column
  - Live preview of element positions
  - **Test buttons to send each element type to pilot's goggles**
  - Save per-pilot or reset to defaults

### Test Message Feature
The configuration interface includes test buttons for each OSD element, allowing you to:
- Send a realistic test message directly to the pilot's goggles
- Verify element positioning before a race
- Test display timing (timed elements will clear after their configured uptime)
- Confirm the pilot's backpack connection is working

Available test message types:
- Heat Name, Class Name, Event Name
- Race Stage ("ARM NOW"), Race Start ("GO!"), Race Finish, Race Stop
- Current Lap/Position
- Lap Results (time and gap display)
- Recent Laps (rolling lap times)
- Announcement
- Pilot Done ("FINISHED!")
- Results (placement and win condition)

Each test message uses the actual formatting and positioning that would be used during a real race, ensuring what you see in testing matches the live experience.

### 2. Global vs Per-Pilot Configuration

The system supports two configuration modes:

#### Global Configuration (Default)
- All pilots use the same OSD settings defined in the ELRS Backpack Settings panel
- Settings include element positions, display timings, and enabled/disabled states
- Simplest setup for events where all pilots have similar OSD preferences

#### Per-Pilot Configuration
- Individual pilots can have custom OSD layouts
- Each pilot's configuration includes a `use_global` flag:
  - `use_global: true` - Pilot uses global settings (default)
  - `use_global: false` - Pilot uses their custom per-pilot settings
- Per-pilot settings override global settings when `use_global` is false
- Pilots without custom configuration automatically use global settings

### 3. OSD Elements Configurable Per-Pilot

Each element can be configured with:
- **enabled** - Whether the element is displayed (true/false)
- **row** - Row position (0-17)
- **alignment** - Text alignment ("left", "center", "right")
- **custom_col** - Column position for fine-tuning (0-49)
- **is_timed** - Display mode (true = timed/temporary, false = static/permanent)
- **uptime** - How long the element displays in decaseconds (10 = 1 second)

#### Available Elements:
- Heat Name
- Class Name
- Event Name
- Race Stage Message ("ARM NOW")
- Race Start Message ("GO!")
- Race Finish Message ("FINISH LAP!")
- Race Stop Message ("LAND NOW!")
- Current Lap/Position
- Lap Results
- Recent Laps (Rolling lap times)
- Announcement
- Pilot Done Message ("FINISHED!")
- Results (Placement and win condition)
- Lap Times (End of race summary)

### 4. Display Modes

#### Static Mode (`is_timed: false`)
- Element remains on screen until replaced by another message
- Useful for persistent information like current lap count

#### Timed Mode (`is_timed: true`)
- Element displays for a configurable duration then clears
- Duration controlled by `uptime` setting (in decaseconds)
- Example: `uptime: 50` = 5 seconds display time

### 5. Lap Time Features

#### Minimum Lap Time Filtering
- Laps under the system's minimum lap time are filtered from ELRS display
- Prevents false triggers from showing incorrect lap data
- Holeshot (first pass through timing gate) is always displayed regardless of time

#### Lap Time Aggregation
- Short laps (under minimum lap time) are aggregated into the next valid lap
- Accumulated time is added to the next lap that meets the minimum threshold
- Ensures lap times displayed are always meaningful

#### Lap Counter
- The LAP counter only increments for valid laps
- Short laps that are filtered/aggregated do not increment the displayed lap count

#### Rolling Recent Laps
The Recent Laps feature displays a rolling list of the pilot's most recent lap times during a race:
- Shows the last N laps (configurable via `num_laps` setting, default 3)
- Updates automatically each time a new lap is recorded
- Can be configured as timed (disappears after uptime) or static (always visible)
- Displays holeshot as "HS" and subsequent laps as "L1", "L2", etc.

#### Recent Laps Display Format
- Displays lap times in seconds-only format with 2 decimal places
- Format: `HS:45.01` (holeshot), `L1:44.23` (lap 1), `L2:43.56` (lap 2)
- Compact format saves screen space compared to `MM:SS.mmm` format
- Short laps (under minimum lap time) are aggregated into the next valid lap

### 6. Configuration Storage
Per-pilot configurations are stored in the `elrs_osd_config` pilot attribute as JSON.

Example configuration structure:
```json
{
  "use_global": false,
  "heat_name": {
    "enabled": true,
    "row": 2,
    "alignment": "center",
    "custom_col": 0
  },
  "recent_laps": {
    "enabled": true,
    "row": 10,
    "alignment": "left",
    "custom_col": 0,
    "is_timed": true,
    "uptime": 50,
    "num_laps": 3
  },
  "race_start": {
    "enabled": true,
    "row": 5,
    "alignment": "center",
    "custom_col": 0,
    "is_timed": true,
    "uptime": 30
  }
}
```

## Implementation Details

### Configuration Priority

When determining settings for a pilot:

1. Check if pilot has per-pilot configuration
2. Check `use_global` flag in pilot's configuration
3. If `use_global: true` or no config exists → use global settings
4. If `use_global: false` → use per-pilot settings with fallback to global for missing values

### Files

#### Configuration Files:
1. **`templates/osd_config.html`** - Web interface with OSD preview
2. **`osd_config_routes.py`** - Flask blueprint with API endpoints
3. **`OSD_CONFIG_README.md`** - This documentation

#### Core Files:
1. **`__init__.py`** - Plugin initialization, registers pilot attribute and routes
2. **`elrs_backpack.py`** - Main OSD logic with per-pilot support

### API Endpoints

#### `GET /elrs_osd_config/`
Renders the configuration page with pilot list.

#### `GET /elrs_osd_config/api/pilot/<pilot_id>`
Returns the OSD configuration for a specific pilot.

#### `POST /elrs_osd_config/api/pilot/<pilot_id>`
Saves the OSD configuration for a specific pilot.

#### `DELETE /elrs_osd_config/api/pilot/<pilot_id>`
Deletes per-pilot configuration (resets to global defaults).

#### `GET /elrs_osd_config/api/preview/<pilot_id>/<element_id>`
Returns preview data for a specific element (position and mock content).

## Usage

### For Race Directors

1. **Access the Configuration Page**:
   - Go to Settings → ELRS Backpack General Settings
   - Click "Configure OSD per Pilot" button
   - Or navigate directly to `/elrs_osd_config`

2. **Configure Global Settings**:
   - Use the ELRS Backpack Settings panels for default positions and timings
   - These apply to all pilots using global configuration

3. **Configure Individual Pilots**:
   - Select pilot from dropdown in the OSD Config page
   - Toggle "Use Global Config" off to enable per-pilot customization
   - Drag elements on the preview grid, OR use the controls panel
   - Configure each element:
     - Enable/disable the element
     - Set row position (0-17)
     - Set alignment (left, center, right)
     - Set custom column if needed (0-49)
     - Set display mode (static or timed)
     - Set display duration for timed elements
   - Click "Save Configuration"

4. **Reset to Defaults**:
   - Click "Reset to Defaults" to clear pilot-specific settings
   - Pilot will use global settings from ELRS Backpack OSD Settings panel

### For Pilots

Each pilot can request custom OSD layout from the race director. The configuration:
- Persists across races and events
- Only affects that specific pilot
- Falls back to global settings if not configured
- Can be completely customized for individual preferences

## Element Settings Reference

### Element IDs

| Element ID | Description | Default Global Option |
|------------|-------------|----------------------|
| `heat_name` | Heat name display | `_heatname_row` |
| `class_name` | Class name display | `_classname_row` |
| `event_name` | Event name display | `_eventname_row` |
| `race_stage` | "ARM NOW" message | `_status_row` |
| `race_start` | "GO!" message | `_status_row` |
| `race_finish` | "FINISH LAP!" message | `_status_row` |
| `race_stop` | "LAND NOW!" message | `_status_row` |
| `current_lap` | Current lap/position | `_currentlap_row` |
| `lap_results` | Lap time/gap display | `_lapresults_row` |
| `recent_laps` | Rolling recent lap times | `_recentlaps_row` |
| `announcement` | Announcement text | `_announcement_row` |
| `pilot_done` | "FINISHED!" message | `_status_row` |
| `results` | Post-race results | `_results_row` |
| `lap_times` | End-of-race lap summary | `_laptimes_row` |

### Setting Keys

| Setting | Type | Description |
|---------|------|-------------|
| `enabled` | boolean | Whether element is displayed |
| `row` | integer | Row position (0-17) |
| `alignment` | string | "left", "center", or "right" |
| `custom_col` | integer | Column position (0-49) |
| `is_timed` | boolean | true = temporary display, false = static |
| `uptime` | integer | Display duration in decaseconds |
| `num_laps` | integer | Number of recent laps to show (recent_laps only) |

## Technical Notes

- OSD grid: 18 rows × 50 columns (HDZero standard)
- Configuration stored as JSON in pilot attribute `elrs_osd_config`
- Color codes (lowercase letters like `x`, `w`) occupy column space but don't display
- Column calculations use full text length including color codes
- Alignment defaults when custom_col is 0:
  - Left: column 0
  - Center: column 25 (screen center)
  - Right: column 49 (right edge)
- Per-pilot settings override global settings when `use_global: false`
- No per-pilot config = uses global settings (backward compatible)

## Testing

1. Start RotorHazard server
2. Navigate to `/elrs_osd_config`
3. Select a pilot
4. Toggle "Use Global Config" off
5. Configure element positions and settings
6. Save configuration
7. Verify configuration persists after page reload
8. Test in actual race to verify OSD displays correctly
9. Verify lap filtering and aggregation with short lap times

## Troubleshooting

### Element going off screen
- Check alignment settings - right-aligned elements should use column 49 as default
- Verify custom_col is appropriate for the text length

### Lap counter not incrementing
- This is expected when laps are under minimum lap time
- Short laps are aggregated into the next valid lap
- Check MinLapSec setting in race format

### Element not appearing
- Check if element is enabled in per-pilot config
- Verify `use_global` flag if using per-pilot settings
- Check the global enabled setting if using global config

### Display timing issues
- Check `is_timed` setting - should be true for temporary displays
- Verify `uptime` value (in decaseconds, so 50 = 5 seconds)
- Static mode (`is_timed: false`) elements remain until cleared
