# Singular Tweaks

> **Python tools and tweaks for controlling Singular.live with optional TfL integration**

[![Build Status](https://github.com/BlueElliott/Singular-Tweaks/actions/workflows/build.yml/badge.svg)](https://github.com/BlueElliott/Singular-Tweaks/actions)
[![PyPI version](https://badge.fury.io/py/singular-tweaks.svg)](https://pypi.org/project/singular-tweaks/)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)

A helper UI and HTTP API that makes it easy to control Singular.live compositions via simple HTTP GET requests. Perfect for integration with automation systems, OBS, vMix, Companion, and other broadcast tools.

## ✨ Features

- 🎨 **Web-based Control Panel** - Configure and test your Singular compositions
- 🔗 **Simple HTTP API** - Trigger compositions with GET requests
- 🚇 **TfL Integration** - Fetch and display London transport statuses
- 📊 **Data Stream Support** - Send data to Singular's Data Stream
- 🎯 **Easy Setup** - No coding required for basic usage
- 🔄 **Auto-discovery** - Automatically finds all your Singular subcompositions

## 📦 Installation

### Windows (Recommended)

**Option 1: Installer (Easiest)**
1. Download `SingularTweaks-Setup-vX.X.X.exe` from [Releases](https://github.com/BlueElliott/Singular-Tweaks/releases)
2. Run the installer (no admin rights needed)
3. Launch from Start Menu or Desktop shortcut

**Option 2: Standalone Executable**
1. Download `SingularTweaks.exe` from [Releases](https://github.com/BlueElliott/Singular-Tweaks/releases)
2. Double-click to run
3. Open browser to `http://localhost:3113`

### Python (All Platforms)

```bash
# Install via pip
pip install singular-tweaks

# Run
singular-tweaks

# Or run as module
python -m singular_tweaks.core
```

### From Source

```bash
git clone https://github.com/BlueElliott/Singular-Tweaks.git
cd Singular-Tweaks
pip install -r requirements.txt
python singular_tweaks/core.py
```

## 🚀 Quick Start

1. **Start the application**
   - Windows: Run `SingularTweaks.exe`
   - Python: Run `singular-tweaks`

2. **Open the web interface**
   - Navigate to `http://localhost:3113`

3. **Configure Singular**
   - Enter your Singular Control App Token
   - Click "Save Token & Refresh Commands"

4. **Control your compositions**
   - Visit the "Commands" page to see all available controls
   - Use the provided URLs in your automation system

## 📖 Usage Examples

### Trigger a Composition In/Out

```bash
# Bring composition IN
GET http://localhost:3113/lower-third/in

# Take composition OUT
GET http://localhost:3113/lower-third/out
```

### Update Field Values

```bash
# Set a text field
GET http://localhost:3113/lower-third/set?field=Name&value=John%20Smith

# Set a number field
GET http://localhost:3113/scoreboard/set?field=Score&value=42
```

### Use with Stream Deck / Companion

Simply use the "System: Open URL" action with any of the control URLs shown in the Commands page.

### Use with OBS Browser Source

Create a browser source that opens the control URL when you need to trigger a graphic.

## ⚙️ Configuration

### Singular Control App
1. Log in to [Singular.live](https://app.singular.live)
2. Go to your Control App
3. Copy the Control App Token
4. Paste it into Singular Tweaks

### TfL Integration (Optional)
1. Register for a [TfL API account](https://api.tfl.gov.uk/)
2. Get your App ID and App Key
3. Enter them in the Integrations page

### Data Stream (Optional)
1. Get your Singular Data Stream URL
2. Enter it in the Integrations page
3. Use `/update` endpoint to send data

## 🔧 API Reference

### Core Endpoints

- `GET /` - Web interface home
- `GET /commands` - List all available commands
- `GET /settings` - Application settings
- `GET /health` - Health check

### Control Endpoints

- `GET /{key}/in` - Animate composition IN
- `GET /{key}/out` - Animate composition OUT
- `GET /{key}/set?field=X&value=Y` - Set field value
- `GET /{key}/timecontrol?field=X&run=true` - Control timers

### Integration Endpoints

- `GET /status` - Get TfL line statuses
- `POST /update` - Send TfL data to Data Stream
- `POST /test` - Send test data to Data Stream

## 🛠️ Development

### Setup Development Environment

```bash
git clone https://github.com/BlueElliott/Singular-Tweaks.git
cd Singular-Tweaks
python -m venv .venv
.venv\Scripts\activate  # Windows
source .venv/bin/activate  # Mac/Linux
pip install -r requirements.txt
pip install -r requirements-dev.txt
```

### Run Tests

```bash
pytest
pytest --cov=singular_tweaks  # With coverage
```

### Code Quality

```bash
black singular_tweaks/
ruff check singular_tweaks/
bandit -r singular_tweaks/
```

### Build Executable

```bash
pyinstaller SingularTweaks.spec
```

## 📝 Configuration File

Settings are saved to `singular_tweaks_config.json` in the application directory:

```json
{
  "singular_token": "your-token",
  "singular_stream_url": "https://...",
  "tfl_app_id": "your-app-id",
  "tfl_app_key": "your-app-key",
  "enable_tfl": true,
  "enable_datastream": true,
  "theme": "dark",
  "port": 3113
}
```

## 🐛 Troubleshooting

**Port already in use?**
- Change the port in Settings page
- Or set environment variable: `SINGULAR_TWEAKS_PORT=3114`

**Can't connect to Singular?**
- Verify your Control App Token is correct
- Check your internet connection
- Try refreshing the command list

**Fonts not displaying?**
- Make sure the `static/` folder is in the same directory as the executable

## 📄 License

MIT License - see [LICENSE](LICENSE) file for details

## 🤝 Contributing

Contributions welcome! Please feel free to submit a Pull Request.

1. Fork the repository
2. Create your feature branch (`git checkout -b feature/AmazingFeature`)
3. Commit your changes (`git commit -m 'Add some AmazingFeature'`)
4. Push to the branch (`git push origin feature/AmazingFeature`)
5. Open a Pull Request

## 🙏 Acknowledgments

- [Singular.live](https://singular.live) - Amazing broadcast graphics platform
- [TfL](https://tfl.gov.uk) - Transport for London API
- [FastAPI](https://fastapi.tiangolo.com/) - Modern web framework
- [PyInstaller](https://pyinstaller.org/) - Executable packaging

## 📧 Support

- 🐛 **Issues**: [GitHub Issues](https://github.com/BlueElliott/Singular-Tweaks/issues)
- 💬 **Discussions**: [GitHub Discussions](https://github.com/BlueElliott/Singular-Tweaks/discussions)

---

**Made with ❤️ by BlueElliott**