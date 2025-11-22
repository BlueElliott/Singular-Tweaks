# Singular Tweaks - Development Session Summary

## Session Overview
**Date:** 2025-11-22
**Version:** 1.0.13
**Focus:** TFL Manual Input with Line Colours, Disconnect Warning, and UI Theme Consistency

---

## What We Accomplished This Session

### 1. TFL Manual Input Section with Line Colours
**Feature:** Added a manual input section to the Modules page where users can override individual TfL line statuses.

**Implementation:**
- Split lines into two columns: **Underground** (11 lines) and **Overground & Other** (10 lines)
- Each line has a coloured label matching official TfL branding
- Input boxes change colour based on status:
  - **Teal (#0c6473)** = "Good Service" or empty
  - **Red (#db422d)** = Any other status (delays, closures, etc.)
- Colour changes are LOCAL only (not sent to Singular) - the actual status is determined by what you type

**New TfL Lines Added:**
```python
TFL_UNDERGROUND = [
    "Bakerloo", "Central", "Circle", "District",
    "Hammersmith & City", "Jubilee", "Metropolitan",
    "Northern", "Piccadilly", "Victoria", "Waterloo & City",
]

TFL_OVERGROUND = [
    "Liberty", "Lioness", "Mildmay", "Suffragette",
    "Weaver", "Windrush", "DLR", "Elizabeth line",
    "Tram", "IFS Cloud Cable Car",
]
```

**TfL Brand Colours Added:**
```python
TFL_LINE_COLOURS = {
    # Underground
    "Bakerloo": "#B36305", "Central": "#E32017", "Circle": "#FFD300",
    "District": "#00782A", "Hammersmith & City": "#F3A9BB",
    "Jubilee": "#A0A5A9", "Metropolitan": "#9B0056", "Northern": "#000000",
    "Piccadilly": "#003688", "Victoria": "#0098D4", "Waterloo & City": "#95CDBA",
    # London Overground (new branding)
    "Liberty": "#6bcdb2", "Lioness": "#fbb01c", "Mildmay": "#137cbd",
    "Suffragette": "#6a9a3a", "Weaver": "#9b4f7a", "Windrush": "#e05206",
    # Other rail
    "DLR": "#00afad", "Elizabeth line": "#6950a1",
    "Tram": "#6fc42a", "IFS Cloud Cable Car": "#e21836",
}
```

---

### 2. Collapsible TFL Module Section
**Feature:** The manual input section is now part of the TFL module and collapses when the module is disabled.

**Implementation:**
- Wrapped all TFL content in a `<div id="tfl-content">` container
- Toggle switch controls visibility via `toggleModule()` JavaScript function
- When disabled: content hides, auto-refresh stops
- When enabled: content shows

---

### 3. Disconnect Warning Overlay
**Feature:** When the server is closed or restarted, the web GUI shows a flashing warning overlay.

**Implementation:**
- Added fullscreen overlay with warning modal
- Pulsing animation (opacity 1 → 0.6) when connection is lost
- Shows reconnection attempt counter
- Auto-reloads page when connection is restored
- Connection checked every 3 seconds via `/health` endpoint

**CSS Animation:**
```css
@keyframes pulse-warning {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.6; }
}
.disconnect-overlay.active {
    animation: pulse-warning 1.5s ease-in-out infinite;
}
```

**JavaScript Monitoring:**
```javascript
async function monitorConnection() {
    const connected = await checkConnection();
    if (!connected) {
        connectionLost = true;
        reconnectAttempts++;
        overlay.style.display = "flex";
        overlay.classList.add("active");
        status.textContent = "Reconnect attempt " + reconnectAttempts + "...";
    }
}
setInterval(monitorConnection, 3000);
```

---

### 4. Web GUI Theme Matched to Desktop GUI
**Problem:** Web GUI colours didn't match desktop GUI - inconsistent experience.

**Solution:** Updated web GUI colours to match desktop GUI exactly:

| Element | Old Colour | New Colour |
|---------|------------|------------|
| Background | #0d1117 | #1a1a1a |
| Card BG | #161b22 | #2d2d2d |
| Border | #30363d | #3d3d3d |
| Input BG | #21262d | #252525 |
| Muted Text | #8b949e | #888888 |
| Success | #10b981 | #4caf50 |
| Error | #ef4444 | #ff5252 |

**Desktop GUI Reference (gui_launcher.py):**
```python
self.bg_dark = "#1a1a1a"
self.bg_medium = "#252525"
self.bg_card = "#2d2d2d"
self.accent_teal = "#00bcd4"
self.text_gray = "#888888"
self.button_red = "#ff5252"
self.button_green = "#4caf50"
```

---

### 5. Static Assets Added
New logo and favicon files added to `/static/`:
- `esc_icon.ico` - Windows icon format
- `esc_icon.png` - PNG icon
- `esc_logo.png` - Full logo PNG
- `esc_logo.svg` - Vector logo
- `favicon.ico` - Browser favicon

---

## File Changes Summary

### `singular_tweaks/core.py`
**Major additions:**
- `TFL_UNDERGROUND` and `TFL_OVERGROUND` lists (lines 113-139)
- `TFL_LINE_COLOURS` dictionary with all official colours (lines 145-170)
- Manual TFL input section HTML generation (lines 1178-1231)
- `updateStatusColour(input)` JavaScript function (lines 1358-1365)
- Disconnect overlay HTML and CSS (lines 1133-1151)
- Connection monitoring JavaScript (lines 1421-1466)
- Updated `_base_style()` colours to match desktop (lines 484-487)

### `singular_tweaks/gui_launcher.py`
- Minor updates from previous session (console logging, UI)

### `static/` directory
- Added 5 new logo/icon files

---

## Architecture: How TFL Manual Input Works

### Flow:
1. User types status in input box (e.g., "Delays")
2. `oninput="updateStatusColour(this)"` triggers colour change
3. Input background changes: teal → red (or vice versa)
4. User clicks "Send Manual" button
5. `sendManual()` collects all input values
6. Empty inputs default to "Good Service"
7. POST to `/manual` endpoint
8. Server forwards to Singular datastream

### Key JavaScript Functions:
```javascript
// Update input box colour based on status
function updateStatusColour(input) {
    var value = input.value.trim().toLowerCase();
    if (value === "" || value === "good service") {
        input.style.background = "#0c6473";  // Teal
    } else {
        input.style.background = "#db422d";  // Red
    }
}

// Collect all manual inputs and send
function getManualPayload() {
    const payload = {};
    TFL_LINES.forEach(line => {
        const safeId = line.replace(/ /g, "-").replace(/&/g, "and");
        const input = document.getElementById("manual-" + safeId);
        payload[line] = input.value.trim() || "Good Service";
    });
    return payload;
}

// Reset all inputs to empty (teal background)
function resetManual() {
    TFL_LINES.forEach(line => {
        const safeId = line.replace(/ /g, "-").replace(/&/g, "and");
        const input = document.getElementById("manual-" + safeId);
        if (input) {
            input.value = "";
            input.style.background = "#0c6473";
        }
    });
}
```

---

## Git History This Session

```
475104d Add TFL manual input with line colours and disconnect warning
525f246 Redesign GUI with modern rounded buttons and fix console logging
ccf71fc Improve console window with initial status and output capture
e86b589 Fix console window and settings page bugs
107cfac Bump version to 1.0.13
```

---

## Testing Checklist

### ✅ Verified Working
- [x] TFL line colours display correctly
- [x] Underground/Overground columns align properly
- [x] Input box colour changes when typing (teal ↔ red)
- [x] "Send Manual" sends data to Singular
- [x] "Reset All" clears inputs and resets colours
- [x] Module toggle shows/hides TFL content
- [x] Disconnect overlay appears when server stops
- [x] Overlay flashes with pulse animation
- [x] Page reloads when connection restored
- [x] Web GUI colours match desktop GUI

---

## Known Issues & Limitations

### 1. Long Line Names
- "IFS Cloud Cable Car" is quite long for the 130px label width
- Currently handled with `text-overflow: ellipsis` if needed

### 2. Disconnect Detection Timing
- 3-second polling interval means up to 3 seconds before disconnect is detected
- Trade-off between responsiveness and server load

### 3. Manual vs Auto Status
- Manual input overrides TFL API data when "Send Manual" is clicked
- Auto-refresh will overwrite manual data if enabled

---

## Quick Reference

### Run the Application
```bash
# GUI Launcher
python -m singular_tweaks.gui_launcher

# Server only (no GUI)
python -m singular_tweaks.core
```

### API Endpoints
```
GET  /modules          - Web GUI modules page
GET  /health           - Health check (used for disconnect detection)
POST /manual           - Send manual TFL data to datastream
POST /config/module/tfl - Toggle TFL module on/off
```

### Colour Reference
```
Good Service (teal): #0c6473
Issues (red):        #db422d
Desktop BG:          #1a1a1a
Card BG:             #2d2d2d
Border:              #3d3d3d
Muted Text:          #888888
Accent:              #00bcd4
```

---

## Next Steps (For Laptop Session)

1. **Potential Enhancements:**
   - Add more TfL modes (National Rail, River Bus, etc.)
   - Add preset status buttons (e.g., "Set All Good Service")
   - Add undo/history for manual changes

2. **Bug Fixes if Found:**
   - Check alignment on different screen sizes
   - Test disconnect overlay on slow connections

3. **Future Features:**
   - Export/import manual configurations
   - Scheduled status updates
   - Multiple datastream targets

---

## Repository Info

**Author:** BlueElliott (elliott.ramdass10@gmail.com)
**Repository:** https://github.com/BlueElliott/Singular-Tweaks
**License:** MIT
**Current Version:** 1.0.13

---

*Last Updated: 2025-11-22 - Session focused on TFL manual input, disconnect warning, and UI theme consistency*
