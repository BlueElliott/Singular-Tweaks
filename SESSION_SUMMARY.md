# Elliott's Singular Controls - Session Summary

## Project Overview
**Name:** Elliott's Singular Controls (formerly Singular Tweaks)
**Version:** 1.0.15
**Repository:** https://github.com/BlueElliott/Elliotts-Singular-Controls

A premium desktop application for controlling Singular.live graphics with TfL integration.

---

## What Was Done (v1.0.15)

### Bug Fix: TFL Manual Input Background Color
- **Issue:** On the modules page (`/modules`), when typing a non-"Good Service" value in the TFL manual input fields, the background should turn red - but it wasn't working.
- **Root Cause:** The CSS for `.tfl-input` had `background: #0c6473 !important;` which prevented JavaScript from overriding the background color dynamically.
- **Fix:** Removed `!important` from the background property in the CSS (line 1147 of `core.py`).
- **File Changed:** `elliotts_singular_controls/core.py`

The standalone TFL control page (`/tfl/control`) was already working correctly because its `.line-input` class didn't use `!important`.

---

## Repository Structure

```
Elliotts-Singular-Controls/
├── .github/workflows/      # GitHub Actions (build.yml)
├── docs/                   # Developer documentation
│   ├── STYLING_GUIDE.md
│   └── requirements-dev.txt
├── scripts/                # Build scripts (installer.nsi only now)
│   └── installer.nsi
├── elliotts_singular_controls/  # Main Python package
│   ├── __init__.py         # Version defined here
│   ├── __main__.py         # Entry point
│   ├── core.py             # FastAPI app, all HTML/CSS/JS embedded
│   └── gui_launcher.py     # System tray GUI launcher
├── static/                 # Static assets (fonts, icons)
│   ├── esc_icon.ico
│   ├── esc_icon.png
│   ├── favicon.ico
│   └── ITV Reem-Regular.ttf
├── ElliottsSingularControls.spec  # PyInstaller spec (MUST be in root!)
├── .gitignore
├── MANIFEST.in
├── pyproject.toml
├── README.md
├── requirements.txt
└── SESSION_SUMMARY.md      # This file
```

---

## Critical Technical Details

### PyInstaller Spec File Location
**IMPORTANT:** The `ElliottsSingularControls.spec` file MUST be in the repository root directory, NOT in `scripts/`.

PyInstaller resolves relative paths from the spec file's location. When the spec file was in `scripts/`, it looked for `scripts/elliotts_singular_controls/__main__.py` instead of `elliotts_singular_controls/__main__.py`.

The GitHub Actions workflow runs:
```yaml
pyinstaller ElliottsSingularControls.spec
```

### CSS Specificity for TFL Inputs
The modules page uses `.tfl-input` class and the standalone page uses `.line-input` class. Both have similar styling but:

- **modules page (core.py ~line 1147):** `input.tfl-input { ... background: #0c6473; ... }` - NO `!important` on background
- **standalone page (core.py ~line 1529):** `.line-input { ... background: #0c6473; ... }` - NO `!important`

The JavaScript functions `updateStatusColour()` (modules) and `updateColour()` (standalone) change the background to `#db422d` (red) for non-"Good Service" values.

### Version Bumping
Version is defined in `elliotts_singular_controls/__init__.py`:
```python
__version__ = "1.0.15"
```

Also update the fallback version in `ElliottsSingularControls.spec` if needed.

### Server Restart Required
When making changes to `core.py`, the FastAPI server must be restarted for changes to appear. The development server doesn't auto-reload embedded HTML/CSS.

---

## Key Files and Their Purposes

### `elliotts_singular_controls/core.py`
- **Lines 1-150:** Imports, constants, TFL line definitions and colours
- **Lines 500-600:** Base CSS styles (`_base_style()` function)
- **Lines 780-850:** TFL/DataStream API endpoints (`/status`, `/update`, `/manual`, etc.)
- **Lines 1000-1110:** Home page (`/`)
- **Lines 1112-1500:** Modules page (`/modules`) - includes TFL manual input section
- **Lines 1508-1632:** Standalone TFL control page (`/tfl/control`)
- **Lines 1635-1770:** Commands page (`/commands`)
- **Lines 1771-1890:** Settings page (`/settings`)

### `elliotts_singular_controls/gui_launcher.py`
- System tray application using pystray
- Launches uvicorn server
- Provides "Open Web GUI", "Settings", "Quit" menu options

### `.github/workflows/build.yml`
- Triggered on tag push (`v*.*.*`)
- Builds Windows executable with PyInstaller
- Creates GitHub Release with the exe
- Attempts PyPI publish (requires `PYPI_API_TOKEN` secret)

---

## How to Build Locally

```bash
# Install dependencies
pip install -r requirements.txt

# Run in development mode
python -m elliotts_singular_controls.gui_launcher

# Build standalone executable
pyinstaller ElliottsSingularControls.spec
# Output: dist/ElliottsSingularControls-1.0.15.exe
```

---

## How to Release

1. **Make changes and test locally**
2. **Bump version** in `elliotts_singular_controls/__init__.py`
3. **Commit changes:**
   ```bash
   git add -A
   git commit -m "Description of changes"
   ```
4. **Push to main:**
   ```bash
   git push origin main
   ```
5. **Create and push tag:**
   ```bash
   git tag v1.0.X
   git push origin v1.0.X
   ```
6. **Monitor GitHub Actions:** https://github.com/BlueElliott/Elliotts-Singular-Controls/actions

If the build fails and you need to retry:
```bash
git tag -d v1.0.X                    # Delete local tag
git push origin :refs/tags/v1.0.X    # Delete remote tag
# Make fixes, commit, push
git tag v1.0.X                       # Recreate tag
git push origin v1.0.X               # Push tag
```

---

## Web Interface URLs

When running locally on port 3113:
- **Home:** http://localhost:3113/
- **Modules (TFL):** http://localhost:3113/modules
- **Standalone TFL Control:** http://localhost:3113/tfl/control
- **Commands:** http://localhost:3113/commands
- **Settings:** http://localhost:3113/settings

---

## TFL Line Status Feature

### How It Works
1. User can fetch live TfL data or enter manual statuses
2. Data is sent to a Singular.live Data Stream URL
3. Singular overlays display the line statuses

### Manual Input Behavior
- Empty field or "Good Service" → Teal background (`#0c6473`)
- Any other text → Red background (`#db422d`)
- This visual feedback happens on `oninput` event via JavaScript

### TFL Lines Defined
- **Underground:** Bakerloo, Central, Circle, District, Hammersmith & City, Jubilee, Metropolitan, Northern, Piccadilly, Victoria, Waterloo & City
- **Overground & Other:** Liberty, Lioness, Mildmay, Suffragette, Weaver, Windrush, DLR, Elizabeth line, Tram, IFS Cloud Cable Car

---

## Common Issues and Solutions

### Issue: TFL input background not changing color
**Solution:** Check that the CSS for `.tfl-input` doesn't have `!important` on the `background` property.

### Issue: PyInstaller can't find `__main__.py`
**Solution:** Make sure `ElliottsSingularControls.spec` is in the repository ROOT, not in `scripts/`.

### Issue: GitHub Actions build fails
**Solution:** Check the error message in the Actions log. Common issues:
- Spec file path wrong
- Missing dependencies in requirements.txt
- Version import failing (check fallback version in spec)

### Issue: Connection lost overlay not appearing
**Solution:** The disconnect overlay uses `/health` endpoint polling every 3 seconds. Make sure the health endpoint is working and the overlay CSS/JS is present in `core.py`.

---

## Disconnect Overlay Feature

The app has a "Connection Lost" overlay that appears when the server stops:
- CSS classes: `.disconnect-overlay`, `.disconnect-modal`
- JavaScript: `checkConnection()`, `monitorConnection()` functions
- Polls `/health` endpoint every 3 seconds
- Shows reconnection attempts counter

---

## Package Naming History

- **Original:** `singular_tweaks` / "Singular Tweaks"
- **Renamed to:** `elliotts_singular_controls` / "Elliott's Singular Controls"
- **PyPI package:** `elliotts-singular-controls`
- **Executable:** `ElliottsSingularControls-{VERSION}.exe`

---

## Files Modified in v1.0.15

1. `elliotts_singular_controls/core.py` - Removed `!important` from `.tfl-input` background
2. `elliotts_singular_controls/__init__.py` - Version bump to 1.0.15
3. `ElliottsSingularControls.spec` - Moved back to root from `scripts/`
4. `.github/workflows/build.yml` - Updated spec file path

---

## Next Steps (Potential)

- Add more TfL data integrations
- Implement additional Singular.live control features
- Create macOS/Linux builds
- Add automated testing
- Consider auto-reload for development
