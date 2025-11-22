import os
import sys
import time
import re
import json
import logging
import traceback
from pathlib import Path
from typing import List, Dict, Any, Optional
from urllib.parse import quote
from html import escape as html_escape
from datetime import datetime

import requests
from fastapi import FastAPI, Query, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.routing import APIRoute
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from contextlib import asynccontextmanager


# ================== CRASH LOGGING ==================

def _crash_log_path() -> Path:
    """Get path to crash log file in app data directory."""
    if sys.platform == "win32":
        app_data = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    else:
        app_data = Path.home() / ".local" / "share"
    log_dir = app_data / "ElliottsSingularControls" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir / "crash_report.txt"


def log_crash(error: Exception, context: str = ""):
    """Log a crash/error to the crash report file."""
    try:
        log_path = _crash_log_path()
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(log_path, "a", encoding="utf-8") as f:
            f.write("=" * 60 + "\n")
            f.write(f"CRASH REPORT - {timestamp}\n")
            f.write(f"Version: {_runtime_version()}\n")
            if context:
                f.write(f"Context: {context}\n")
            f.write(f"Error Type: {type(error).__name__}\n")
            f.write(f"Error Message: {str(error)}\n")
            f.write("\nTraceback:\n")
            f.write(traceback.format_exc())
            f.write("\n")
    except Exception:
        pass  # Don't crash while logging crashes


def setup_crash_handler():
    """Setup global exception handler to log unhandled exceptions."""
    def exception_handler(exc_type, exc_value, exc_tb):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return
        log_crash(exc_value, "Unhandled exception")
        sys.__excepthook__(exc_type, exc_value, exc_tb)
    sys.excepthook = exception_handler


# Initialize crash handler
setup_crash_handler()

# ================== 0. PATHS & VERSION ==================

def _app_root() -> Path:
    """Folder where the app is running from (install dir or source)."""
    if getattr(sys, "frozen", False):  # PyInstaller exe
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent  # Go up one level from elliotts_singular_controls/


def _runtime_version() -> str:
    """
    Try to read version from version.txt next to the app, then package version.
    Fallback to '1.0.9' if not present.
    """
    try:
        vfile = _app_root() / "version.txt"
        if vfile.exists():
            text = vfile.read_text(encoding="utf-8").strip()
            if ":" in text:
                text = text.split(":", 1)[1].strip()
            return text
    except Exception:
        pass
    # Try to get version from package
    try:
        from elliotts_singular_controls import __version__
        return __version__
    except Exception:
        pass
    return "1.0.9"


# ================== 1. CONFIG & GLOBALS ==================

DEFAULT_PORT = int(os.getenv("SINGULAR_TWEAKS_PORT", "3113"))

SINGULAR_API_BASE = "https://app.singular.live/apiv2"
TFL_URL = (
    "https://api.tfl.gov.uk/Line/Mode/"
    "tube,overground,dlr,elizabeth-line,tram,cable-car/Status"
)

# Underground lines
TFL_UNDERGROUND = [
    "Bakerloo",
    "Central",
    "Circle",
    "District",
    "Hammersmith & City",
    "Jubilee",
    "Metropolitan",
    "Northern",
    "Piccadilly",
    "Victoria",
    "Waterloo & City",
]

# Overground/Other lines
TFL_OVERGROUND = [
    "Liberty",
    "Lioness",
    "Mildmay",
    "Suffragette",
    "Weaver",
    "Windrush",
    "DLR",
    "Elizabeth line",
    "Tram",
    "IFS Cloud Cable Car",
]

# All TFL lines combined
TFL_LINES = TFL_UNDERGROUND + TFL_OVERGROUND

# Official TFL line colours (matched to TfL brand guidelines)
TFL_LINE_COLOURS = {
    # Underground
    "Bakerloo": "#B36305",
    "Central": "#E32017",
    "Circle": "#FFD300",
    "District": "#00782A",
    "Hammersmith & City": "#F3A9BB",
    "Jubilee": "#A0A5A9",
    "Metropolitan": "#9B0056",
    "Northern": "#000000",
    "Piccadilly": "#003688",
    "Victoria": "#0098D4",
    "Waterloo & City": "#95CDBA",
    # London Overground lines (new branding)
    "Liberty": "#6bcdb2",
    "Lioness": "#fbb01c",
    "Mildmay": "#137cbd",
    "Suffragette": "#6a9a3a",
    "Weaver": "#9b4f7a",
    "Windrush": "#e05206",
    # Other rail
    "DLR": "#00afad",
    "Elizabeth line": "#6950a1",
    "Tram": "#6fc42a",
    "IFS Cloud Cable Car": "#e21836",
}

def _config_dir() -> Path:
    if getattr(sys, "frozen", False):
        base = Path(sys.executable).parent
    else:
        # When running from source, use elliotts_singular_controls directory
        base = Path(__file__).resolve().parent
    return base

CONFIG_PATH = _config_dir() / "elliotts_singular_controls_config.json"

logger = logging.getLogger("elliotts_singular_controls")
if not logger.handlers:
    logging.basicConfig(level=logging.INFO)


class AppConfig(BaseModel):
    singular_token: Optional[str] = None
    singular_stream_url: Optional[str] = None
    tfl_app_id: Optional[str] = None
    tfl_app_key: Optional[str] = None
    enable_tfl: bool = False  # Disabled by default for new installs
    tfl_auto_refresh: bool = False  # Auto-refresh TFL data every 60s
    theme: str = "dark"
    port: Optional[int] = None


def load_config() -> AppConfig:
    base: Dict[str, Any] = {
        "singular_token": os.getenv("SINGULAR_TOKEN") or None,
        "singular_stream_url": os.getenv("SINGULAR_STREAM_URL") or None,
        "tfl_app_id": os.getenv("TFL_APP_ID") or None,
        "tfl_app_key": os.getenv("TFL_APP_KEY") or None,
        "enable_tfl": False,  # Disabled by default
        "tfl_auto_refresh": False,
        "theme": "dark",
        "port": int(os.getenv("SINGULAR_TWEAKS_PORT")) if os.getenv("SINGULAR_TWEAKS_PORT") else None,
    }
    if CONFIG_PATH.exists():
        try:
            with CONFIG_PATH.open("r", encoding="utf-8") as f:
                file_data = json.load(f)
            base.update(file_data)
        except Exception as e:
            logger.warning("Failed to load config file %s: %s", CONFIG_PATH, e)
    return AppConfig(**base)


def save_config(cfg: AppConfig) -> None:
    try:
        with CONFIG_PATH.open("w", encoding="utf-8") as f:
            json.dump(cfg.model_dump(), f, indent=2)
        logger.info("Saved config to %s", CONFIG_PATH)
    except Exception as e:
        logger.error("Failed to save config file %s: %s", CONFIG_PATH, e)


CONFIG = load_config()

def effective_port() -> int:
    return CONFIG.port or DEFAULT_PORT


COMMAND_LOG: List[str] = []
MAX_LOG_ENTRIES = 200

def log_event(kind: str, detail: str) -> None:
    ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    line = f"[{ts}] {kind}: {detail}"
    COMMAND_LOG.append(line)
    if len(COMMAND_LOG) > MAX_LOG_ENTRIES:
        del COMMAND_LOG[: len(COMMAND_LOG) - MAX_LOG_ENTRIES]


# ================== 2. FASTAPI APP ==================

def generate_unique_id(route: APIRoute) -> str:
    methods = sorted([m for m in route.methods if m in {"GET","POST","PUT","PATCH","DELETE","OPTIONS","HEAD"}])
    method = methods[0].lower() if methods else "get"
    safe_path = re.sub(r"[^a-z0-9]+", "-", route.path.lower()).strip("-")
    return f"{route.name}-{method}-{safe_path}"

app = FastAPI(
    title="Elliott's Singular Controls",
    description="Helper UI and HTTP API for Singular.live + optional TfL data.",
    version=_runtime_version(),
    generate_unique_id_function=generate_unique_id,
)

# static files (for font)
STATIC_DIR = _app_root() / "static"
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR), html=False), name="static")


def tfl_params() -> Dict[str, str]:
    p: Dict[str, str] = {}
    if CONFIG.tfl_app_id and CONFIG.tfl_app_key and CONFIG.enable_tfl:
        p["app_id"] = CONFIG.tfl_app_id
        p["app_key"] = CONFIG.tfl_app_key
    return p


def fetch_all_line_statuses() -> Dict[str, str]:
    if not CONFIG.enable_tfl:
        raise HTTPException(400, "TfL integration is disabled in settings")
    try:
        r = requests.get(TFL_URL, params=tfl_params(), timeout=10)
        r.raise_for_status()
        out: Dict[str, str] = {}
        for line in r.json():
            out[line["name"]] = line.get("lineStatuses", [{}])[0].get("statusSeverityDescription", "Unknown")
        return out
    except requests.RequestException as e:
        logger.error("TfL API request failed: %s", e)
        raise HTTPException(503, f"TfL API request failed: {str(e)}")


def send_to_datastream(payload: Dict[str, Any]):
    if not CONFIG.singular_stream_url:
        raise HTTPException(400, "No Singular data stream URL configured")
    resp = None
    try:
        resp = requests.put(
            CONFIG.singular_stream_url,
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=10,
        )
        resp.raise_for_status()
        return {
            "stream_url": CONFIG.singular_stream_url,
            "status": resp.status_code,
            "response": resp.text,
        }
    except requests.RequestException as e:
        logger.exception("Datastream PUT failed")
        return {
            "stream_url": CONFIG.singular_stream_url,
            "status": resp.status_code if resp is not None else 0,
            "response": resp.text if resp is not None else "",
            "error": str(e),
        }


def ctrl_patch(items: list):
    if not CONFIG.singular_token:
        raise HTTPException(400, "No Singular control app token configured")
    ctrl_control = f"{SINGULAR_API_BASE}/controlapps/{CONFIG.singular_token}/control"
    try:
        resp = requests.patch(
            ctrl_control,
            json=items,
            headers={"Content-Type": "application/json"},
            timeout=10,
        )
        resp.raise_for_status()
        log_event("Control PATCH", f"{ctrl_control} items={len(items)}")
        return resp
    except requests.RequestException as e:
        logger.exception("Control PATCH failed")
        raise HTTPException(503, f"Control PATCH failed: {str(e)}")


def now_ms_float() -> float:
    return float(time.time() * 1000)


def slugify(name: str) -> str:
    s = name.lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s or "item"


def _base_url(request: Request) -> str:
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme
    return f"{proto}://{host}"


# ================== 3. REGISTRY (Control App model) ==================

REGISTRY: Dict[str, Dict[str, Any]] = {}
ID_TO_KEY: Dict[str, str] = {}

def singular_model_fetch() -> Any:
    if not CONFIG.singular_token:
        raise RuntimeError("No Singular control app token configured")
    ctrl_model = f"{SINGULAR_API_BASE}/controlapps/{CONFIG.singular_token}/model"
    try:
        r = requests.get(ctrl_model, timeout=10)
        r.raise_for_status()
        return r.json()
    except requests.RequestException as e:
        logger.error("Model fetch failed: %s", e)
        raise RuntimeError(f"Model fetch failed: {r.status_code if 'r' in locals() else 'unknown'}")


def _walk_nodes(node):
    items = []
    if isinstance(node, dict):
        items.append(node)
        for k in ("subcompositions", "Subcompositions"):
            if k in node and isinstance(node[k], list):
                for child in node[k]:
                    items.extend(_walk_nodes(child))
    elif isinstance(node, list):
        for el in node:
            items.extend(_walk_nodes(el))
    return items


def build_registry():
    REGISTRY.clear()
    ID_TO_KEY.clear()
    data = singular_model_fetch()
    flat = _walk_nodes(data)
    for n in flat:
        sid = n.get("id")
        name = n.get("name")
        model = n.get("model")
        if not sid or name is None or model is None:
            continue
        key = slugify(name)
        orig_key = key
        i = 2
        while key in REGISTRY and REGISTRY[key]["id"] != sid:
            key = f"{orig_key}-{i}"
            i += 1
        REGISTRY[key] = {
            "id": sid,
            "name": name,
            "fields": {(f.get("id") or ""): f for f in (model or [])},
        }
        ID_TO_KEY[sid] = key
    log_event("Registry", f"Built with {len(REGISTRY)} subcompositions")


def kfind(key_or_id: str) -> str:
    if key_or_id in REGISTRY:
        return key_or_id
    if key_or_id in ID_TO_KEY:
        return ID_TO_KEY[key_or_id]
    raise HTTPException(404, f"Subcomposition not found: {key_or_id}")


def coerce_value(field_meta: Dict[str, Any], value_str: str, as_string: bool = False):
    if as_string:
        return value_str
    ftype = (field_meta.get("type") or "").lower()
    if ftype in ("number", "range", "slider"):
        try:
            if "." in value_str:
                return float(value_str)
            return int(value_str)
        except ValueError:
            return value_str
    if ftype in ("checkbox", "toggle", "bool", "boolean"):
        return value_str.lower() in ("1", "true", "yes", "on")
    return value_str


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        if CONFIG.singular_token:
            build_registry()
    except Exception as e:
        logger.warning("[WARN] Registry build failed: %s", e)
    yield

app.router.lifespan_context = lifespan

# ================== 4. Pydantic models ==================

class SingularConfigIn(BaseModel):
    token: str

class TflConfigIn(BaseModel):
    app_id: str
    app_key: str

class StreamConfigIn(BaseModel):
    stream_url: str

class SettingsIn(BaseModel):
    port: Optional[int] = None
    enable_tfl: bool = False
    theme: Optional[str] = "dark"

class SingularItem(BaseModel):
    subCompositionId: str
    state: Optional[str] = None
    payload: Optional[dict] = None


# ================== 5. HTML helpers ==================

def _nav_html() -> str:
    parts = ['<div class="nav">']
    parts.append('<a href="/">Home</a>')
    parts.append('<a href="/commands">Commands</a>')
    parts.append('<a href="/modules">Modules</a>')
    parts.append('<a href="/settings">Settings</a>')
    parts.append('</div>')
    return "".join(parts)


def _base_style() -> str:
    theme = CONFIG.theme or "dark"
    if theme == "light":
        bg = "#f0f2f5"; fg = "#1a1a2e"; card_bg = "#ffffff"; border = "#e0e0e0"; accent = "#00bcd4"
        accent_hover = "#0097a7"; text_muted = "#666666"; input_bg = "#fafafa"
    else:
        # Modern dark theme - matched to desktop GUI colours
        bg = "#1a1a1a"; fg = "#ffffff"; card_bg = "#2d2d2d"; border = "#3d3d3d"; accent = "#00bcd4"
        accent_hover = "#0097a7"; text_muted = "#888888"; input_bg = "#252525"

    lines = []
    lines.append('<link rel="icon" type="image/x-icon" href="/static/favicon.ico">')
    lines.append('<link rel="icon" type="image/png" href="/static/esc_icon.png">')
    lines.append("<style>")
    lines.append("  @font-face {")
    lines.append("    font-family: 'ITVReem';")
    lines.append("    src: url('/static/ITV Reem-Regular.ttf') format('truetype');")
    lines.append("    font-weight: normal;")
    lines.append("    font-style: normal;")
    lines.append("  }")
    lines.append("  * { box-sizing: border-box; }")
    lines.append(
        f"  body {{ font-family: 'ITVReem', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;"
        f" max-width: 900px; margin: 0 auto; background: {bg}; color: {fg}; padding: 20px; line-height: 1.6; }}"
    )
    lines.append(f"  h1 {{ font-size: 28px; font-weight: 700; margin: 20px 0 8px 0; padding-top: 50px; color: {fg}; }}")
    lines.append(f"  h1 + p {{ color: {text_muted}; margin-bottom: 24px; }}")
    lines.append(
        f"  fieldset {{ margin-bottom: 20px; padding: 20px 24px; background: {card_bg}; "
        f"border: 1px solid {border}; border-radius: 12px; }}"
    )
    lines.append(f"  legend {{ font-weight: 600; padding: 0 12px; font-size: 14px; color: {text_muted}; }}")
    lines.append(f"  label {{ display: block; margin-top: 12px; font-size: 14px; color: {text_muted}; }}")
    lines.append(
        f"  input, select {{ width: 100%; padding: 10px 14px; margin-top: 6px; "
        f"background: {input_bg}; color: {fg}; border: 1px solid {border}; border-radius: 8px; "
        f"font-size: 14px; transition: border-color 0.2s, box-shadow 0.2s; }}"
    )
    lines.append(f"  input:focus, select:focus {{ outline: none; border-color: {accent}; box-shadow: 0 0 0 3px {accent}33; }}")
    lines.append(
        f"  button {{ display: inline-flex; align-items: center; justify-content: center; gap: 8px; "
        f"margin-top: 12px; margin-right: 8px; padding: 10px 20px; cursor: pointer; "
        f"background: {accent}; color: #fff; border: none; border-radius: 8px; "
        f"font-size: 14px; font-weight: 500; transition: all 0.2s; }}"
    )
    lines.append(f"  button:hover {{ background: {accent_hover}; transform: translateY(-1px); box-shadow: 0 4px 12px {accent}40; }}")
    lines.append(f"  button:active {{ transform: translateY(0); }}")
    lines.append(
        f"  pre {{ background: #000; color: #4ade80; padding: 16px; white-space: pre-wrap; "
        f"max-height: 250px; overflow: auto; border-radius: 8px; font-size: 13px; "
        f"font-family: 'SF Mono', Monaco, 'Cascadia Code', Consolas, monospace; border: 1px solid {border}; }}"
    )
    lines.append(
        f"  .nav {{ position: fixed; top: 16px; left: 16px; display: flex; gap: 4px; z-index: 1000; "
        f"background: {card_bg}; padding: 6px; border-radius: 10px; border: 1px solid {border}; box-shadow: 0 2px 8px rgba(0,0,0,0.3); }}"
    )
    lines.append(
        f"  .nav a {{ color: {text_muted}; text-decoration: none; padding: 8px 14px; border-radius: 6px; "
        f"font-size: 13px; font-weight: 500; transition: all 0.2s; }}"
    )
    lines.append(f"  .nav a:hover {{ background: {input_bg}; color: {accent}; }}")
    lines.append(f"  table {{ border-collapse: collapse; width: 100%; margin-top: 12px; border-radius: 8px; overflow: hidden; }}")
    lines.append(f"  th, td {{ border: 1px solid {border}; padding: 10px 14px; font-size: 13px; text-align: left; }}")
    lines.append(f"  th {{ background: {accent}; color: #fff; font-weight: 600; }}")
    lines.append(f"  tr:nth-child(even) td {{ background: {input_bg}; }}")
    lines.append(f"  tr:hover td {{ background: {border}; }}")
    lines.append(
        f"  code {{ font-family: 'SF Mono', Monaco, 'Cascadia Code', Consolas, monospace; "
        f"background: {input_bg}; padding: 3px 8px; border-radius: 6px; font-size: 12px; "
        f"border: 1px solid {border}; display: inline-block; max-width: 450px; overflow-x: auto; "
        f"white-space: nowrap; vertical-align: middle; }}"
    )
    lines.append(f"  h3 {{ margin-top: 24px; margin-bottom: 8px; font-size: 16px; color: {fg}; }}")
    lines.append(f"  h3 small {{ color: {text_muted}; font-weight: 400; }}")
    lines.append(f"  p {{ margin: 8px 0; }}")
    lines.append(f"  .status-badge {{ display: inline-flex; align-items: center; gap: 6px; padding: 4px 12px; border-radius: 20px; font-size: 13px; }}")
    lines.append(f"  .status-badge.success {{ background: #10b98120; color: #10b981; }}")
    lines.append(f"  .status-badge.error {{ background: #ef444420; color: #ef4444; }}")
    lines.append(f"  .status-badge.warning {{ background: #f59e0b20; color: #f59e0b; }}")
    lines.append(f"  .play-btn {{ display: inline-flex; align-items: center; justify-content: center; width: 32px; height: 32px; "
                 f"background: {accent}; color: #fff; border-radius: 50%; text-decoration: none; font-size: 14px; "
                 f"transition: all 0.2s; }}")
    lines.append(f"  .play-btn:hover {{ background: {accent_hover}; transform: scale(1.1); box-shadow: 0 2px 8px {accent}60; }}")
    lines.append("</style>")
    return "\n".join(lines)


# ================== 6. JSON config endpoints ==================

@app.get("/config")
def get_config():
    return {
        "singular": {
            "token_set": bool(CONFIG.singular_token),
            "token": CONFIG.singular_token,
            "stream_url": CONFIG.singular_stream_url,
        },
        "tfl": {
            "app_id_set": bool(CONFIG.tfl_app_id),
            "app_key_set": bool(CONFIG.tfl_app_key),
        },
        "settings": {
            "port": effective_port(),
            "raw_port": CONFIG.port,
            "enable_tfl": CONFIG.enable_tfl,
            "tfl_auto_refresh": CONFIG.tfl_auto_refresh,
            "theme": CONFIG.theme,
        },
    }


@app.post("/config/singular")
def set_singular_config(cfg: SingularConfigIn):
    CONFIG.singular_token = cfg.token
    save_config(CONFIG)
    try:
        build_registry()
    except Exception as e:
        raise HTTPException(400, f"Token saved, but registry build failed: {e}")
    return {"ok": True, "message": "Singular token updated", "subs": len(REGISTRY)}


@app.post("/config/tfl")
def set_tfl_config(cfg: TflConfigIn):
    CONFIG.tfl_app_id = cfg.app_id
    CONFIG.tfl_app_key = cfg.app_key
    save_config(CONFIG)
    return {"ok": True, "message": "TfL config updated"}


@app.post("/config/stream")
def set_stream_config(cfg: StreamConfigIn):
    url = cfg.stream_url.strip()
    # Auto-prefix if user just enters the datastream ID
    if url and not url.startswith("http"):
        url = f"https://datastream.singular.live/datastreams/{url}"
    CONFIG.singular_stream_url = url
    save_config(CONFIG)
    return {"ok": True, "message": "Data Stream URL updated", "url": url}


class ModuleToggleIn(BaseModel):
    enabled: bool


@app.post("/config/module/tfl")
def toggle_tfl_module(cfg: ModuleToggleIn):
    CONFIG.enable_tfl = cfg.enabled
    if not cfg.enabled:
        CONFIG.tfl_auto_refresh = False  # Disable auto-refresh when module is disabled
    save_config(CONFIG)
    return {"ok": True, "enabled": CONFIG.enable_tfl}


@app.post("/config/module/tfl/auto-refresh")
def toggle_tfl_auto_refresh(cfg: ModuleToggleIn):
    CONFIG.tfl_auto_refresh = cfg.enabled
    save_config(CONFIG)
    return {"ok": True, "enabled": CONFIG.tfl_auto_refresh}


@app.get("/settings/json")
def get_settings_json():
    return {
        "port": effective_port(),
        "raw_port": CONFIG.port,
        "enable_tfl": CONFIG.enable_tfl,
        "tfl_auto_refresh": CONFIG.tfl_auto_refresh,
        "config_path": str(CONFIG_PATH),
        "theme": CONFIG.theme,
    }


@app.get("/version/check")
def check_version():
    """Check for updates against GitHub releases."""
    current = _runtime_version()
    try:
        resp = requests.get(
            "https://api.github.com/repos/BlueElliott/Singular-Tweaks/releases/latest",
            timeout=5
        )
        if resp.status_code == 404:
            return {
                "current": current,
                "latest": None,
                "up_to_date": True,
                "message": "Repository is private or has no public releases",
            }
        resp.raise_for_status()
        data = resp.json()
        latest = data.get("tag_name", "unknown")
        release_url = data.get("html_url", "")

        # Normalize versions for comparison (remove 'v' prefix if present)
        current_normalized = current.lstrip('v')
        latest_normalized = latest.lstrip('v')
        up_to_date = current_normalized == latest_normalized

        return {
            "current": current,
            "latest": latest,
            "up_to_date": up_to_date,
            "release_url": release_url,
            "message": "You are up to date" if up_to_date else "A newer version is available",
        }
    except requests.RequestException as e:
        logger.error("Version check failed: %s", e)
        return {
            "current": current,
            "latest": None,
            "up_to_date": True,
            "message": f"Version check failed: {str(e)}",
        }


@app.post("/settings")
def update_settings(settings: SettingsIn):
    CONFIG.enable_tfl = settings.enable_tfl
    # Only update port if provided (port config moved to GUI launcher)
    if settings.port is not None:
        CONFIG.port = settings.port
    CONFIG.theme = (settings.theme or "dark")
    save_config(CONFIG)
    return {
        "ok": True,
        "message": "Settings updated.",
        "port": effective_port(),
        "enable_tfl": CONFIG.enable_tfl,
        "theme": CONFIG.theme,
    }


@app.get("/config/export")
def export_config():
    """Export current configuration as JSON for backup."""
    return CONFIG.model_dump()


@app.post("/config/import")
def import_config(config_data: Dict[str, Any]):
    """Import configuration from JSON backup."""
    try:
        # Update CONFIG with imported data
        if "singular_token" in config_data:
            CONFIG.singular_token = config_data["singular_token"]
        if "singular_stream_url" in config_data:
            CONFIG.singular_stream_url = config_data["singular_stream_url"]
        if "tfl_app_id" in config_data:
            CONFIG.tfl_app_id = config_data["tfl_app_id"]
        if "tfl_app_key" in config_data:
            CONFIG.tfl_app_key = config_data["tfl_app_key"]
        if "enable_tfl" in config_data:
            CONFIG.enable_tfl = config_data["enable_tfl"]
        if "tfl_auto_refresh" in config_data:
            CONFIG.tfl_auto_refresh = config_data["tfl_auto_refresh"]
        if "theme" in config_data:
            CONFIG.theme = config_data["theme"]
        if "port" in config_data:
            CONFIG.port = config_data["port"]

        # Save to file
        save_config(CONFIG)

        return {
            "ok": True,
            "message": "Configuration imported successfully. Restart app to apply changes.",
        }
    except Exception as e:
        logger.error("Failed to import config: %s", e)
        raise HTTPException(400, f"Failed to import config: {str(e)}")


@app.get("/events")
def get_events():
    return {"events": COMMAND_LOG[-100:]}


@app.get("/singular/ping")
def singular_ping():
    try:
        data = singular_model_fetch()
        if isinstance(data, dict):
            top_keys = list(data.keys())[:5]
        elif isinstance(data, list):
            if data and isinstance(data[0], dict):
                top_keys = [f"[0].{k}" for k in data[0].keys()][:5]
            else:
                top_keys = [f"list(len={len(data)})"]
        else:
            top_keys = [type(data).__name__]
        return {
            "ok": True,
            "message": "Connected to Singular",
            "model_type": type(data).__name__,
            "top_level_keys": top_keys,
            "subs": len(REGISTRY),
        }
    except Exception as e:
        raise HTTPException(500, f"Singular ping failed: {e}")


# ================== 7. TfL / DataStream endpoints ==================

@app.get("/health")
def health():
    return {"status": "ok", "version": _runtime_version(), "port": effective_port()}


@app.get("/status")
def status_preview():
    try:
        data = fetch_all_line_statuses()
        log_event("TfL status", f"{len(data)} lines")
        return data
    except Exception as e:
        raise HTTPException(500, str(e))


@app.api_route("/update", methods=["GET", "POST"])
def update_status():
    try:
        data = fetch_all_line_statuses()
        result = send_to_datastream(data)
        log_event("DataStream update", "Sent TfL payload")
        return {"sent_to": "datastream", "payload": data, **result}
    except Exception as e:
        raise HTTPException(500, f"Update failed: {e}")


@app.api_route("/test", methods=["GET", "POST"])
def update_test():
    try:
        keys = list(fetch_all_line_statuses().keys())
        payload = {k: "TEST" for k in keys}
        result = send_to_datastream(payload)
        log_event("DataStream test", "Sent TEST payload")
        return {"sent_to": "datastream", "payload": payload, **result}
    except Exception as e:
        raise HTTPException(500, f"Test failed: {e}")


@app.api_route("/blank", methods=["GET", "POST"])
def update_blank():
    try:
        keys = list(fetch_all_line_statuses().keys())
        payload = {k: "" for k in keys}
        result = send_to_datastream(payload)
        log_event("DataStream blank", "Sent blank payload")
        return {"sent_to": "datastream", "payload": payload, **result}
    except Exception as e:
        raise HTTPException(500, f"Blank failed: {e}")


@app.get("/tfl/lines")
def get_tfl_lines():
    """Return list of all TFL lines for the manual input UI."""
    return {"lines": TFL_LINES}


@app.post("/manual")
def send_manual(payload: Dict[str, str]):
    """Send a manual payload to the datastream."""
    try:
        result = send_to_datastream(payload)
        log_event("DataStream manual", f"Sent manual payload with {len(payload)} lines")
        return {"sent_to": "datastream", "payload": payload, **result}
    except Exception as e:
        raise HTTPException(500, f"Manual send failed: {e}")


# ================== 8. Control app endpoints ==================

@app.post("/singular/control")
def singular_control(items: List[SingularItem]):
    r = ctrl_patch([i.dict(exclude_none=True) for i in items])
    return {"status": r.status_code, "response": r.text}


@app.get("/singular/list")
def singular_list():
    return {
        k: {"id": v["id"], "name": v["name"], "fields": list(v["fields"].keys())}
        for k, v in REGISTRY.items()
    }


@app.post("/singular/refresh")
def singular_refresh():
    build_registry()
    return {"ok": True, "count": len(REGISTRY)}


def _field_examples(base: str, key: str, field_id: str, field_meta: dict):
    ftype = (field_meta.get("type") or "").lower()
    examples: Dict[str, str] = {}
    set_url = f"{base}/{key}/set?field={quote(field_id)}&value=VALUE"
    examples["set_url"] = set_url
    if ftype == "timecontrol":
        start = f"{base}/{key}/timecontrol?field={quote(field_id)}&run=true&value=0"
        stop = f"{base}/{key}/timecontrol?field={quote(field_id)}&run=false&value=0"
        examples["timecontrol_start_url"] = start
        examples["timecontrol_stop_url"] = stop
        examples["start_10s_if_supported"] = (
            f"{base}/{key}/timecontrol?field={quote(field_id)}&run=true&value=0&seconds=10"
        )
    return examples


@app.get("/singular/commands")
def singular_commands(request: Request):
    base = _base_url(request)
    catalog: Dict[str, Any] = {}
    for key, meta in REGISTRY.items():
        sid = meta["id"]
        entry: Dict[str, Any] = {
            "id": sid,
            "name": meta["name"],
            "in_url": f"{base}/{key}/in",
            "out_url": f"{base}/{key}/out",
            "fields": {},
        }
        for fid, fmeta in meta["fields"].items():
            if not fid:
                continue
            entry["fields"][fid] = _field_examples(base, key, fid, fmeta)
        catalog[key] = entry
    return {
        "note": "Most control endpoints support GET for testing, but POST is recommended in automation.",
        "catalog": catalog,
    }


@app.get("/{key}/help")
def singular_commands_for_one(key: str, request: Request):
    k = kfind(key)
    base = _base_url(request)
    meta = REGISTRY[k]
    sid = meta["id"]
    entry: Dict[str, Any] = {
        "id": sid,
        "name": meta["name"],
        "in_url": f"{base}/{k}/in",
        "out_url": f"{base}/{k}/out",
        "fields": {},
    }
    for fid, fmeta in meta["fields"].items():
        if not fid:
            continue
        entry["fields"][fid] = _field_examples(base, k, fid, fmeta)
    return {"commands": entry}


@app.api_route("/{key}/in", methods=["GET", "POST"])
def sub_in(key: str):
    k = kfind(key)
    sid = REGISTRY[k]["id"]
    r = ctrl_patch([{"subCompositionId": sid, "state": "In"}])
    log_event("IN", f"{k} ({sid})")
    return {"status": r.status_code, "id": sid, "response": r.text}


@app.api_route("/{key}/out", methods=["GET", "POST"])
def sub_out(key: str):
    k = kfind(key)
    sid = REGISTRY[k]["id"]
    r = ctrl_patch([{"subCompositionId": sid, "state": "Out"}])
    log_event("OUT", f"{k} ({sid})")
    return {"status": r.status_code, "id": sid, "response": r.text}


@app.api_route("/{key}/set", methods=["GET", "POST"])
def sub_set(
    key: str,
    field: str = Query(..., description="Field id as shown in /singular/list"),
    value: str = Query(..., description="Value to set"),
    asString: int = Query(0, description="Send value strictly as string if 1"),
):
    k = kfind(key)
    meta = REGISTRY[k]
    sid = meta["id"]
    fields = meta["fields"]
    if field not in fields:
        raise HTTPException(404, f"Field not found on {k}: {field}")
    v = coerce_value(fields[field], value, as_string=bool(asString))
    patch = [{"subCompositionId": sid, "payload": {field: v}}]
    r = ctrl_patch(patch)
    log_event("SET", f"{k} ({sid}) field={field} value={value}")
    return {"status": r.status_code, "id": sid, "sent": patch, "response": r.text}


@app.api_route("/{key}/timecontrol", methods=["GET", "POST"])
def sub_timecontrol(
    key: str,
    field: str = Query(..., description="timecontrol field id"),
    run: bool = Query(True, description="True=start, False=stop"),
    value: int = Query(0, description="usually 0"),
    utc: Optional[float] = Query(None, description="override UTC ms; default now()"),
    seconds: Optional[int] = Query(None, description="optional duration for countdowns"),
):
    k = kfind(key)
    meta = REGISTRY[k]
    sid = meta["id"]
    fields = meta["fields"]
    if field not in fields:
        raise HTTPException(404, f"Field not found on {k}: {field}")
    if (fields[field].get("type") or "").lower() != "timecontrol":
        raise HTTPException(400, f"Field '{field}' is not a timecontrol")
    payload: Dict[str, Any] = {}
    if seconds is not None:
        payload["Countdown Seconds"] = str(seconds)
    payload[field] = {
        "UTC": float(utc if utc is not None else now_ms_float()),
        "isRunning": bool(run),
        "value": int(value),
    }
    r = ctrl_patch([{"subCompositionId": sid, "payload": payload}])
    log_event("TIMECONTROL", f"{k} ({sid}) field={field} run={run} seconds={seconds}")
    return {"status": r.status_code, "id": sid, "sent": payload, "response": r.text}


# ================== 9. HTML Pages ==================

@app.get("/", response_class=HTMLResponse)
def index():
    parts: List[str] = []
    parts.append("<html><head>")
    parts.append("<title>Elliott's Singular Controls v" + _runtime_version() + "</title>")
    parts.append(_base_style())
    parts.append("</head><body>")
    parts.append(_nav_html())
    parts.append("<h1>Elliott's Singular Controls</h1>")
    parts.append("<p>Mainly used to send <strong>GET</strong> and simple HTTP commands to your Singular Control App.</p>")
    # Show full token (not sensitive)
    saved = "Not set"
    if CONFIG.singular_token:
        saved = CONFIG.singular_token
    parts.append('<fieldset><legend>Singular Control App Token</legend>')
    parts.append('<div style="display:flex;align-items:center;gap:12px;margin-bottom:12px;">')
    parts.append('<span style="font-size:13px;color:#8b949e;">Current:</span>')
    parts.append('<code id="saved-token" style="flex:1;max-width:none;">' + html_escape(saved) + "</code>")
    parts.append('<span id="singular-status" class="status-badge warning">Unknown</span>')
    parts.append('<button type="button" onclick="pingSingular()" style="margin:0;">Ping</button>')
    parts.append("</div>")
    parts.append('<form id="singular-form" style="display:flex;gap:8px;align-items:flex-end;">')
    parts.append('<label style="flex:1;margin:0;">New Token <input name="token" autocomplete="off" placeholder="Paste your Control App Token here" /></label>')
    parts.append('<button type="submit" style="margin:0;">Update Token</button>')
    parts.append("</form></fieldset>")
    parts.append('<fieldset><legend>Event Log</legend>')
    parts.append("<p>Shows recent HTTP commands and updates triggered by this tool.</p>")
    parts.append('<button type="button" onclick="loadEvents()">Refresh Log</button>')
    parts.append('<pre id="log">No events yet.</pre>')
    parts.append("</fieldset>")
    # JS
    parts.append("<script>")
    parts.append("async function postJSON(url, data) {")
    parts.append("  const res = await fetch(url, {")
    parts.append('    method: "POST",')
    parts.append('    headers: { "Content-Type": "application/json" },')
    parts.append("    body: JSON.stringify(data),")
    parts.append("  });")
    parts.append("  const text = await res.text();")
    parts.append("  return text;")
    parts.append("}")
    parts.append("async function loadConfig() {")
    parts.append("  try {")
    parts.append('    const res = await fetch("/config");')
    parts.append("    if (!res.ok) return;")
    parts.append("    const cfg = await res.json();")
    parts.append("    const tokenSet = cfg.singular.token_set;")
    parts.append("    const token = cfg.singular.token;")
    parts.append('    const saved = document.getElementById("saved-token");')
    parts.append("    if (tokenSet && token) {")
    parts.append('      saved.textContent = token;')
    parts.append("    } else {")
    parts.append('      saved.textContent = "Not set";')
    parts.append("    }")
    parts.append("  } catch (e) { console.error(e); }")
    parts.append("}")
    parts.append("async function pingSingular() {")
    parts.append('  const statusEl = document.getElementById("singular-status");')
    parts.append('  statusEl.textContent = "Checking...";')
    parts.append('  statusEl.className = "status-badge warning";')
    parts.append("  try {")
    parts.append('    const res = await fetch("/singular/ping");')
    parts.append("    const txt = await res.text();")
    parts.append("    try {")
    parts.append("      const data = JSON.parse(txt);")
    parts.append("      if (data.ok) {")
    parts.append('        statusEl.textContent = "Connected (" + (data.subs || 0) + " subs)";')
    parts.append('        statusEl.className = "status-badge success";')
    parts.append('      } else { statusEl.textContent = "Error"; statusEl.className = "status-badge error"; }')
    parts.append('    } catch (e) { statusEl.textContent = txt; statusEl.className = "status-badge error"; }')
    parts.append('  } catch (e) { statusEl.textContent = "Ping failed"; statusEl.className = "status-badge error"; }')
    parts.append("}")
    parts.append("async function refreshRegistry() {")
    parts.append('  const statusEl = document.getElementById("singular-status");')
    parts.append('  statusEl.textContent = "Refreshing...";')
    parts.append('  statusEl.className = "status-badge warning";')
    parts.append("  try {")
    parts.append('    const res = await fetch("/singular/refresh", { method: "POST" });')
    parts.append("    const data = await res.json();")
    parts.append('    statusEl.textContent = "Registry: " + (data.count || 0) + " subs";')
    parts.append('    statusEl.className = "status-badge success";')
    parts.append('  } catch (e) { statusEl.textContent = "Refresh failed"; statusEl.className = "status-badge error"; }')
    parts.append("}")
    parts.append("async function loadEvents() {")
    parts.append("  try {")
    parts.append('    const res = await fetch("/events");')
    parts.append("    const data = await res.json();")
    parts.append('    document.getElementById("log").innerText = (data.events || []).join("\\n") || "No events yet.";')
    parts.append("  } catch (e) {")
    parts.append('    document.getElementById("log").innerText = "Failed to load events: " + e;')
    parts.append("  }")
    parts.append("}")
    parts.append('document.getElementById("singular-form").onsubmit = async (e) => {')
    parts.append("  e.preventDefault();")
    parts.append("  const f = e.target;")
    parts.append("  const token = f.token.value;")
    parts.append("  if (!token) { alert('Please enter a token.'); return; }")
    parts.append('  await postJSON("/config/singular", { token });')
    parts.append("  await loadConfig();")
    parts.append("  await pingSingular();")
    parts.append("  alert('Token saved. Registry refreshed.');")
    parts.append("};")
    parts.append("loadConfig();")
    parts.append("pingSingular();")
    parts.append("loadEvents();")
    parts.append("</script>")
    parts.append("</body></html>")
    return HTMLResponse("".join(parts))


@app.get("/modules", response_class=HTMLResponse)
def modules_page():
    parts: List[str] = []
    parts.append("<html><head>")
    parts.append("<title>Modules - Elliott's Singular Controls</title>")
    parts.append(_base_style())
    parts.append("<style>")
    parts.append("  .module-card { margin-bottom: 20px; }")
    parts.append("  .module-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; }")
    parts.append("  .module-title { font-size: 18px; font-weight: 600; margin: 0; }")
    parts.append("  .toggle-switch { position: relative; width: 50px; height: 26px; }")
    parts.append("  .toggle-switch input { opacity: 0; width: 0; height: 0; }")
    parts.append("  .toggle-slider { position: absolute; cursor: pointer; top: 0; left: 0; right: 0; bottom: 0; background: #3d3d3d; border-radius: 26px; transition: 0.3s; }")
    parts.append("  .toggle-slider:before { position: absolute; content: ''; height: 20px; width: 20px; left: 3px; bottom: 3px; background: white; border-radius: 50%; transition: 0.3s; }")
    parts.append("  .toggle-switch input:checked + .toggle-slider { background: #00bcd4; }")
    parts.append("  .toggle-switch input:checked + .toggle-slider:before { transform: translateX(24px); }")
    parts.append("  .module-actions { display: flex; gap: 8px; margin-top: 16px; }")
    parts.append("  .status-text { font-size: 13px; padding: 6px 12px; border-radius: 6px; }")
    parts.append("  .status-text.success { background: #4caf5030; color: #4caf50; }")
    parts.append("  .status-text.error { background: #ff525230; color: #ff5252; }")
    parts.append("  .status-text.idle { background: #88888830; color: #888888; }")
    parts.append("  @keyframes pulse-warning { 0%, 100% { opacity: 1; } 50% { opacity: 0.6; } }")
    parts.append("  .disconnect-overlay { position: fixed; top: 0; left: 0; right: 0; bottom: 0; background: rgba(0,0,0,0.9); display: none; justify-content: center; align-items: center; z-index: 9999; }")
    parts.append("  .disconnect-overlay.active { animation: pulse-warning 1.5s ease-in-out infinite; }")
    parts.append("  .disconnect-modal { background: #2d2d2d; border: 3px solid #ff5252; border-radius: 16px; padding: 40px; text-align: center; max-width: 400px; box-shadow: 0 0 40px rgba(255,82,82,0.3); }")
    parts.append("  .disconnect-icon { font-size: 48px; margin-bottom: 16px; color: #ff5252; }")
    parts.append("  .disconnect-title { font-size: 24px; font-weight: 700; color: #ff5252; margin-bottom: 12px; }")
    parts.append("  .disconnect-message { font-size: 14px; color: #888888; margin-bottom: 20px; }")
    parts.append("  .disconnect-status { font-size: 12px; color: #666666; }")
    # TFL Manual Input styles - matching standalone page (using !important to override base styles)
    parts.append("  .tfl-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 40px; }")
    parts.append("  .tfl-column h4 { margin: 0 0 16px 0; font-size: 11px; text-transform: uppercase; letter-spacing: 1.5px; color: #888; }")
    parts.append("  .tfl-row { display: flex; align-items: stretch; margin-bottom: 6px; border-radius: 6px; overflow: hidden; background: #252525; }")
    parts.append("  .tfl-label { width: 140px; flex-shrink: 0; display: flex; align-items: center; justify-content: center; padding: 12px 8px; }")
    parts.append("  .tfl-label span { font-size: 11px; font-weight: 600; text-align: center; line-height: 1.2; }")
    parts.append("  input.tfl-input { flex: 1 !important; padding: 12px 14px !important; font-size: 12px !important; background: #0c6473 !important; color: #fff !important; border: none !important; font-weight: 500 !important; outline: none !important; font-family: inherit !important; width: auto !important; margin: 0 !important; border-radius: 0 !important; }")
    parts.append("  input.tfl-input::placeholder { color: rgba(255,255,255,0.5) !important; }")
    parts.append("</style>")
    parts.append("</head><body>")
    # Disconnect overlay
    parts.append('<div id="disconnect-overlay" class="disconnect-overlay">')
    parts.append('<div class="disconnect-modal">')
    parts.append('<div class="disconnect-icon">&#9888;</div>')
    parts.append('<div class="disconnect-title">Connection Lost</div>')
    parts.append('<div class="disconnect-message">The server has been closed or restarted.<br>Please restart the application to reconnect.</div>')
    parts.append('<div class="disconnect-status" id="disconnect-status">Attempting to reconnect...</div>')
    parts.append('</div>')
    parts.append('</div>')
    parts.append(_nav_html())
    parts.append("<h1>Modules</h1>")
    parts.append("<p>Enable and configure optional modules to extend functionality.</p>")

    # TfL Status Module
    tfl_enabled = "checked" if CONFIG.enable_tfl else ""
    auto_refresh = "checked" if CONFIG.tfl_auto_refresh else ""
    stream_url = html_escape(CONFIG.singular_stream_url or "")

    parts.append('<fieldset class="module-card"><legend>TfL Line Status</legend>')
    parts.append('<div class="module-header">')
    parts.append('<p class="module-title">Transport for London - Line Status</p>')
    parts.append('<label class="toggle-switch"><input type="checkbox" id="tfl-enabled" ' + tfl_enabled + ' onchange="toggleModule()" /><span class="toggle-slider"></span></label>')
    parts.append('</div>')
    parts.append('<p style="color: #8b949e; margin: 0;">Fetches current TfL line status and pushes to Singular Data Stream.</p>')

    # TFL Content container (collapsible based on module toggle)
    tfl_display = "block" if CONFIG.enable_tfl else "none"
    parts.append(f'<div id="tfl-content" style="display: {tfl_display};">')

    # Data Stream URL input
    parts.append('<form id="stream-form" style="margin-top: 16px;">')
    parts.append('<label>Data Stream URL (where to push TfL data)')
    parts.append('<input name="stream_url" value="' + stream_url + '" placeholder="https://datastream.singular.live/datastreams/..." autocomplete="off" /></label>')
    parts.append('</form>')

    # Auto-refresh toggle (as toggle switch)
    parts.append('<div style="margin-top: 16px; display: flex; align-items: center; gap: 12px;">')
    parts.append('<span style="font-size: 14px;">Auto-refresh every 60 seconds</span>')
    parts.append('<label class="toggle-switch"><input type="checkbox" id="auto-refresh" ' + auto_refresh + ' onchange="toggleAutoRefresh()" /><span class="toggle-slider"></span></label>')
    parts.append('</div>')

    # Action buttons and status (inline)
    parts.append('<div class="module-actions" style="flex-wrap: wrap; align-items: center;">')
    parts.append('<button type="button" onclick="saveAndRefresh()">Save & Update</button>')
    parts.append('<button type="button" onclick="refreshTfl()" style="background: #30363d;">Update Now</button>')
    parts.append('<button type="button" onclick="previewTfl()" style="background: #30363d;">Preview</button>')
    parts.append('<button type="button" onclick="testTfl()" style="background: #f59e0b;">Send TEST</button>')
    parts.append('<button type="button" onclick="blankTfl()" style="background: #ef4444;">Send Blank</button>')
    parts.append('<span id="tfl-status" class="status-text idle" style="margin-left: 12px;">Not updated yet</span>')
    parts.append('</div>')
    parts.append('<pre id="tfl-preview" style="display: none; max-height: 200px; overflow: auto; margin-top: 12px;"></pre>')

    # Manual TFL Input Section - Using CSS classes to match standalone page
    parts.append('<div style="margin-top: 20px; padding-top: 20px; border-top: 1px solid #3d3d3d;">')
    parts.append('<div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px;">')
    parts.append('<h3 style="margin: 0; font-size: 16px; font-weight: 600;">Manual Line Status</h3>')
    parts.append('<a href="/tfl/control" target="_blank" style="display: inline-flex; align-items: center; gap: 6px; padding: 8px 16px; background: #3d3d3d; color: #fff; text-decoration: none; border-radius: 6px; font-size: 12px; font-weight: 500; transition: background 0.2s;">Open Standalone <span style="font-size: 10px;">↗</span></a>')
    parts.append('</div>')
    parts.append('<p style="color: #888; margin: 0 0 20px 0; font-size: 13px;">Override individual line statuses. Empty fields default to "Good Service".</p>')
    parts.append('<div class="tfl-grid">')

    # Underground column
    parts.append('<div class="tfl-column">')
    parts.append('<h4>Underground</h4>')
    for line in TFL_UNDERGROUND:
        safe_id = line.replace(" ", "-").replace("&", "and")
        line_colour = TFL_LINE_COLOURS.get(line, "#3d3d3d")
        needs_dark_text = line in ["Circle", "Hammersmith & City", "Waterloo & City"]
        text_colour = "#000" if needs_dark_text else "#fff"
        parts.append(f'<div class="tfl-row">')
        parts.append(f'<div class="tfl-label" style="background: {line_colour};"><span style="color: {text_colour};">{html_escape(line)}</span></div>')
        parts.append(f'<input type="text" class="tfl-input" id="manual-{safe_id}" placeholder="Good Service" oninput="updateStatusColour(this)" />')
        parts.append('</div>')
    parts.append('</div>')

    # Overground column
    parts.append('<div class="tfl-column">')
    parts.append('<h4>Overground & Other</h4>')
    for line in TFL_OVERGROUND:
        safe_id = line.replace(" ", "-").replace("&", "and")
        line_colour = TFL_LINE_COLOURS.get(line, "#3d3d3d")
        parts.append(f'<div class="tfl-row">')
        parts.append(f'<div class="tfl-label" style="background: {line_colour};"><span style="color: #fff;">{html_escape(line)}</span></div>')
        parts.append(f'<input type="text" class="tfl-input" id="manual-{safe_id}" placeholder="Good Service" oninput="updateStatusColour(this)" />')
        parts.append('</div>')
    parts.append('</div>')

    parts.append('</div>')  # Close tfl-grid
    parts.append('<div class="module-actions" style="margin-top: 20px;">')
    parts.append('<button type="button" onclick="sendManual()">Send Manual</button>')
    parts.append('<button type="button" onclick="resetManual()" style="background: #3d3d3d;">Reset All</button>')
    parts.append('<span id="manual-status" class="status-text idle" style="margin-left: 8px;">Not sent yet</span>')
    parts.append('</div>')
    parts.append('</div>')  # Close manual section
    parts.append('</div>')  # Close tfl-content
    parts.append('</fieldset>')  # Close TfL fieldset

    # Future modules placeholder
    parts.append('<fieldset style="opacity: 0.6;"><legend>More Modules Coming Soon</legend>')
    parts.append('<p style="color: #8b949e;">Additional modules will be available in future updates.</p>')
    parts.append('</fieldset>')

    # JavaScript - use a list and join with newlines
    js_lines = [
        "<script>",
        "let autoRefreshInterval = null;",
        "",
        "async function postJSON(url, data) {",
        "  const res = await fetch(url, {",
        '    method: "POST",',
        '    headers: { "Content-Type": "application/json" },',
        "    body: JSON.stringify(data),",
        "  });",
        "  return res.json();",
        "}",
        "",
        "async function toggleModule() {",
        '  const enabled = document.getElementById("tfl-enabled").checked;',
        '  const content = document.getElementById("tfl-content");',
        '  await postJSON("/config/module/tfl", { enabled });',
        "  if (enabled) {",
        '    content.style.display = "block";',
        "  } else {",
        '    content.style.display = "none";',
        "    stopAutoRefresh();",
        "  }",
        "}",
        "",
        "async function toggleAutoRefresh() {",
        '  const enabled = document.getElementById("auto-refresh").checked;',
        '  await postJSON("/config/module/tfl/auto-refresh", { enabled });',
        "  if (enabled) { startAutoRefresh(); } else { stopAutoRefresh(); }",
        "}",
        "",
        "function startAutoRefresh() {",
        "  if (autoRefreshInterval) return;",
        "  autoRefreshInterval = setInterval(refreshTfl, 60000);",
        '  console.log("Auto-refresh started");',
        "}",
        "",
        "function stopAutoRefresh() {",
        "  if (autoRefreshInterval) { clearInterval(autoRefreshInterval); autoRefreshInterval = null; }",
        '  console.log("Auto-refresh stopped");',
        "}",
        "",
        "async function saveAndRefresh() {",
        '  const streamUrl = document.querySelector("[name=stream_url]").value;',
        '  await postJSON("/config/stream", { stream_url: streamUrl });',
        "  await refreshTfl();",
        "}",
        "",
        "async function refreshTfl() {",
        '  const status = document.getElementById("tfl-status");',
        '  status.textContent = "Refreshing...";',
        '  status.className = "status-text idle";',
        "  try {",
        '    const res = await fetch("/update");',
        '    const autoOn = document.getElementById("auto-refresh").checked;',
        '    const autoText = autoOn ? " (auto-refresh on)" : "";',
        "    if (res.ok) {",
        '      status.textContent = "Updated " + new Date().toLocaleTimeString() + autoText;',
        '      status.className = "status-text success";',
        "    } else {",
        "      const err = await res.json();",
        '      status.textContent = err.detail || "Error";',
        '      status.className = "status-text error";',
        "    }",
        "  } catch (e) {",
        '    status.textContent = "Failed: " + e.message;',
        '    status.className = "status-text error";',
        "  }",
        "}",
        "",
        "async function previewTfl() {",
        '  const preview = document.getElementById("tfl-preview");',
        '  const status = document.getElementById("tfl-status");',
        '  status.textContent = "Fetching preview...";',
        '  status.className = "status-text idle";',
        "  try {",
        '    const res = await fetch("/status");',
        "    const data = await res.json();",
        '    preview.textContent = JSON.stringify(data, null, 2);',
        '    preview.style.display = "block";',
        '    status.textContent = "Preview loaded (not sent)";',
        '    status.className = "status-text idle";',
        "  } catch (e) {",
        '    status.textContent = "Preview failed: " + e.message;',
        '    status.className = "status-text error";',
        "  }",
        "}",
        "",
        "async function testTfl() {",
        '  const status = document.getElementById("tfl-status");',
        '  status.textContent = "Sending TEST...";',
        '  status.className = "status-text idle";',
        "  try {",
        '    const res = await fetch("/test");',
        "    if (res.ok) {",
        '      status.textContent = "TEST sent " + new Date().toLocaleTimeString();',
        '      status.className = "status-text success";',
        "    } else {",
        "      const err = await res.json();",
        '      status.textContent = err.detail || "Error";',
        '      status.className = "status-text error";',
        "    }",
        "  } catch (e) {",
        '    status.textContent = "Failed: " + e.message;',
        '    status.className = "status-text error";',
        "  }",
        "}",
        "",
        "async function blankTfl() {",
        '  const status = document.getElementById("tfl-status");',
        '  status.textContent = "Sending blank...";',
        '  status.className = "status-text idle";',
        "  try {",
        '    const res = await fetch("/blank");',
        "    if (res.ok) {",
        '      status.textContent = "Blank sent " + new Date().toLocaleTimeString();',
        '      status.className = "status-text success";',
        "    } else {",
        "      const err = await res.json();",
        '      status.textContent = err.detail || "Error";',
        '      status.className = "status-text error";',
        "    }",
        "  } catch (e) {",
        '    status.textContent = "Failed: " + e.message;',
        '    status.className = "status-text error";',
        "  }",
        "}",
        "",
        "const TFL_LINES = " + json.dumps(TFL_LINES) + ";",
        "",
        "function updateStatusColour(input) {",
        "  var value = input.value.trim().toLowerCase();",
        '  if (value === "" || value === "good service") {',
        '    input.style.background = "#0c6473";',  # Teal for Good Service
        "  } else {",
        '    input.style.background = "#db422d";',  # Red for anything else
        "  }",
        "}",
        "",
        "function getManualPayload() {",
        "  const payload = {};",
        "  TFL_LINES.forEach(line => {",
        '    const safeId = line.replace(/ /g, "-").replace(/&/g, "and");',
        '    const input = document.getElementById("manual-" + safeId);',
        '    const value = input ? input.value.trim() : "";',
        '    payload[line] = value || "Good Service";',
        "  });",
        "  return payload;",
        "}",
        "",
        "async function sendManual() {",
        '  var status = document.getElementById("manual-status");',
        '  status.textContent = "Sending...";',
        '  status.className = "status-text idle";',
        "  try {",
        "    var payload = getManualPayload();",
        '    var res = await fetch("/manual", {',
        '      method: "POST",',
        '      headers: { "Content-Type": "application/json" },',
        "      body: JSON.stringify(payload)",
        "    });",
        "    if (res.ok) {",
        '      status.textContent = "Updated " + new Date().toLocaleTimeString();',
        '      status.className = "status-text success";',
        "    } else {",
        "      var err = await res.json();",
        '      status.textContent = err.detail || "Error";',
        '      status.className = "status-text error";',
        "    }",
        "  } catch (e) {",
        '    status.textContent = "Failed: " + e.message;',
        '    status.className = "status-text error";',
        "  }",
        "}",
        "",
        "function resetManual() {",
        "  TFL_LINES.forEach(line => {",
        '    const safeId = line.replace(/ /g, "-").replace(/&/g, "and");',
        '    const input = document.getElementById("manual-" + safeId);',
        "    if (input) {",
        '      input.value = "";',
        '      input.style.background = "#0c6473";',  # Reset to teal background
        "    }",
        "  });",
        '  document.getElementById("manual-status").textContent = "Reset";',
        '  document.getElementById("manual-status").className = "status-text idle";',
        "}",
    ]
    parts.append("\n".join(js_lines))

    # Auto-refresh init code - also join with newlines
    init_js = [
        "",
        "// Connection monitoring",
        "let connectionLost = false;",
        "let reconnectAttempts = 0;",
        "",
        "async function checkConnection() {",
        "  try {",
        '    const res = await fetch("/health", { method: "GET", cache: "no-store" });',
        "    if (res.ok) {",
        "      if (connectionLost) {",
        "        // Reconnected - reload page to refresh state",
        "        location.reload();",
        "      }",
        "      reconnectAttempts = 0;",
        "      return true;",
        "    }",
        "  } catch (e) {",
        "    // Connection failed",
        "  }",
        "  return false;",
        "}",
        "",
        "async function monitorConnection() {",
        "  const connected = await checkConnection();",
        "  if (!connected) {",
        "    connectionLost = true;",
        "    reconnectAttempts++;",
        '    const overlay = document.getElementById("disconnect-overlay");',
        '    const status = document.getElementById("disconnect-status");',
        '    overlay.style.display = "flex";',
        '    overlay.classList.add("active");',
        '    status.textContent = "Reconnect attempt " + reconnectAttempts + "...";',
        "  }",
        "}",
        "",
        "// Check connection every 3 seconds",
        "setInterval(monitorConnection, 3000);",
        "",
        "// Start auto-refresh if enabled on page load",
        'const autoRefreshChecked = document.getElementById("auto-refresh").checked;',
        'const tflEnabledChecked = document.getElementById("tfl-enabled").checked;',
        'console.log("Auto-refresh checkbox:", autoRefreshChecked, "TFL enabled:", tflEnabledChecked);',
        'if (autoRefreshChecked && tflEnabledChecked) {',
        '  console.log("Starting auto-refresh on page load");',
        "  startAutoRefresh();",
        "} else {",
        '  console.log("Auto-refresh NOT started - conditions not met");',
        "}",
        "</script>",
    ]
    parts.append("\n".join(init_js))
    parts.append("</body></html>")
    return HTMLResponse("".join(parts))


# Keep old route for backwards compatibility
@app.get("/integrations", response_class=HTMLResponse)
def integrations_redirect():
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/modules")


@app.get("/tfl/control", response_class=HTMLResponse)
def tfl_manual_standalone():
    """Standalone TFL manual control page for external operators."""
    parts: List[str] = []
    parts.append("<html><head>")
    parts.append("<title>TfL Line Status Control</title>")
    parts.append('<link rel="icon" type="image/x-icon" href="/static/favicon.ico">')
    parts.append('<link rel="icon" type="image/png" href="/static/esc_icon.png">')
    parts.append("<style>")
    parts.append("  @font-face { font-family: 'ITVReem'; src: url('/static/ITV Reem-Regular.ttf') format('truetype'); }")
    parts.append("  * { box-sizing: border-box; margin: 0; padding: 0; }")
    parts.append("  body { font-family: 'ITVReem', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #1a1a1a; color: #fff; min-height: 100vh; padding: 30px; }")
    parts.append("  .container { max-width: 900px; margin: 0 auto; }")
    parts.append("  .header { text-align: center; margin-bottom: 30px; padding-bottom: 20px; border-bottom: 1px solid #3d3d3d; }")
    parts.append("  .header h1 { font-size: 24px; font-weight: 600; margin-bottom: 8px; }")
    parts.append("  .header p { color: #888; font-size: 14px; }")
    parts.append("  .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 40px; margin-bottom: 24px; }")
    parts.append("  .column h2 { font-size: 11px; text-transform: uppercase; letter-spacing: 1.5px; color: #888; margin-bottom: 16px; }")
    parts.append("  .line-row { display: flex; align-items: stretch; margin-bottom: 6px; border-radius: 6px; overflow: hidden; background: #252525; }")
    parts.append("  .line-label { width: 140px; flex-shrink: 0; display: flex; align-items: center; justify-content: center; padding: 12px 8px; }")
    parts.append("  .line-label span { font-size: 11px; font-weight: 600; text-align: center; line-height: 1.2; }")
    parts.append("  .line-input { flex: 1; padding: 12px 14px; font-size: 12px; background: #0c6473; color: #fff; border: none; font-weight: 500; outline: none; font-family: inherit; }")
    parts.append("  .line-input::placeholder { color: rgba(255,255,255,0.5); }")
    parts.append("  .actions { display: flex; justify-content: center; gap: 12px; padding-top: 20px; border-top: 1px solid #3d3d3d; }")
    parts.append("  button { padding: 14px 32px; font-size: 14px; font-weight: 600; border: none; border-radius: 8px; cursor: pointer; transition: all 0.2s; font-family: inherit; }")
    parts.append("  .btn-primary { background: #00bcd4; color: #fff; }")
    parts.append("  .btn-primary:hover { background: #0097a7; }")
    parts.append("  .btn-secondary { background: #3d3d3d; color: #fff; }")
    parts.append("  .btn-secondary:hover { background: #4d4d4d; }")
    parts.append("  .status { text-align: center; margin-top: 16px; font-size: 13px; color: #888; }")
    parts.append("  .status.success { color: #4caf50; }")
    parts.append("  .status.error { color: #ff5252; }")
    parts.append("</style>")
    parts.append("</head><body>")
    parts.append('<div class="container">')
    parts.append('<div class="header">')
    parts.append("<h1>TfL Line Status Control</h1>")
    parts.append("<p>Update line statuses manually. Empty fields default to \"Good Service\".</p>")
    parts.append("</div>")
    parts.append('<div class="grid">')

    # Underground column
    parts.append('<div class="column">')
    parts.append("<h2>Underground</h2>")
    for line in TFL_UNDERGROUND:
        safe_id = line.replace(" ", "-").replace("&", "and")
        line_colour = TFL_LINE_COLOURS.get(line, "#3d3d3d")
        needs_dark_text = line in ["Circle", "Hammersmith & City", "Waterloo & City"]
        text_colour = "#000" if needs_dark_text else "#fff"
        parts.append(f'<div class="line-row">')
        parts.append(f'<div class="line-label" style="background: {line_colour};"><span style="color: {text_colour};">{html_escape(line)}</span></div>')
        parts.append(f'<input type="text" class="line-input" id="manual-{safe_id}" placeholder="Good Service" oninput="updateColour(this)" />')
        parts.append('</div>')
    parts.append("</div>")

    # Overground column
    parts.append('<div class="column">')
    parts.append("<h2>Overground & Other</h2>")
    for line in TFL_OVERGROUND:
        safe_id = line.replace(" ", "-").replace("&", "and")
        line_colour = TFL_LINE_COLOURS.get(line, "#3d3d3d")
        parts.append(f'<div class="line-row">')
        parts.append(f'<div class="line-label" style="background: {line_colour};"><span style="color: #fff;">{html_escape(line)}</span></div>')
        parts.append(f'<input type="text" class="line-input" id="manual-{safe_id}" placeholder="Good Service" oninput="updateColour(this)" />')
        parts.append('</div>')
    parts.append("</div>")

    parts.append("</div>")  # Close grid
    parts.append('<div class="actions">')
    parts.append('<button class="btn-primary" onclick="sendUpdate()">Send Update</button>')
    parts.append('<button class="btn-secondary" onclick="resetAll()">Reset All</button>')
    parts.append("</div>")
    parts.append('<div class="status" id="status"></div>')
    parts.append("</div>")  # Close container

    # JavaScript
    tfl_lines_js = json.dumps(TFL_UNDERGROUND + TFL_OVERGROUND)
    parts.append("<script>")
    parts.append(f"const TFL_LINES = {tfl_lines_js};")
    parts.append("function updateColour(input) {")
    parts.append("  const val = input.value.trim().toLowerCase();")
    parts.append("  input.style.background = (val === '' || val === 'good service') ? '#0c6473' : '#db422d';")
    parts.append("}")
    parts.append("function getPayload() {")
    parts.append("  const payload = {};")
    parts.append("  TFL_LINES.forEach(line => {")
    parts.append("    const safeId = line.replace(/ /g, '-').replace(/&/g, 'and');")
    parts.append("    const input = document.getElementById('manual-' + safeId);")
    parts.append("    if (input) payload[line] = input.value.trim() || 'Good Service';")
    parts.append("  });")
    parts.append("  return payload;")
    parts.append("}")
    parts.append("async function sendUpdate() {")
    parts.append("  const status = document.getElementById('status');")
    parts.append("  status.textContent = 'Sending...';")
    parts.append("  status.className = 'status';")
    parts.append("  try {")
    parts.append("    const res = await fetch('/manual', {")
    parts.append("      method: 'POST',")
    parts.append("      headers: { 'Content-Type': 'application/json' },")
    parts.append("      body: JSON.stringify(getPayload())")
    parts.append("    });")
    parts.append("    if (res.ok) {")
    parts.append("      status.textContent = 'Update sent successfully';")
    parts.append("      status.className = 'status success';")
    parts.append("    } else {")
    parts.append("      status.textContent = 'Failed to send update';")
    parts.append("      status.className = 'status error';")
    parts.append("    }")
    parts.append("  } catch (e) {")
    parts.append("    status.textContent = 'Error: ' + e.message;")
    parts.append("    status.className = 'status error';")
    parts.append("  }")
    parts.append("}")
    parts.append("function resetAll() {")
    parts.append("  TFL_LINES.forEach(line => {")
    parts.append("    const safeId = line.replace(/ /g, '-').replace(/&/g, 'and');")
    parts.append("    const input = document.getElementById('manual-' + safeId);")
    parts.append("    if (input) { input.value = ''; input.style.background = '#0c6473'; }")
    parts.append("  });")
    parts.append("  document.getElementById('status').textContent = '';")
    parts.append("}")
    parts.append("</script>")
    parts.append("</body></html>")
    return HTMLResponse("".join(parts))


@app.get("/commands", response_class=HTMLResponse)
def commands_page(request: Request):
    base = _base_url(request)
    parts: List[str] = []
    parts.append("<html><head>")
    parts.append("<title>Commands - Elliott's Singular Controls</title>")
    parts.append(_base_style())
    parts.append("<style>")
    parts.append("  .copyable { cursor: pointer; transition: all 0.2s; padding: 4px 8px; border-radius: 4px; }")
    parts.append("  .copyable:hover { background: #00bcd4; color: #fff; }")
    parts.append("  .copyable.copied { background: #4caf50; color: #fff; }")
    parts.append("</style>")
    parts.append("</head><body>")
    parts.append(_nav_html())
    parts.append("<h1>Singular Commands</h1>")
    parts.append("<p>This view focuses on simple <strong>GET</strong> triggers you can use in automation systems.</p>")
    parts.append("<p>Base URL: <code>" + html_escape(base) + "</code></p>")
    parts.append("<fieldset><legend>Discovered Subcompositions</legend>")
    parts.append('<p><button type="button" onclick="loadCommands()">Reload Commands</button>')
    parts.append('<button type="button" onclick="rebuildRegistry()">Rebuild from Singular</button></p>')
    parts.append('<div style="margin-bottom:0.5rem;">')
    parts.append('<label>Filter <input id="cmd-filter" placeholder="Filter by name or key" /></label>')
    parts.append('<label>Sort <select id="cmd-sort">')
    parts.append('<option value="name">Name (A–Z)</option>')
    parts.append('<option value="key">Key (A–Z)</option>')
    parts.append("</select></label></div>")
    parts.append('<div id="commands">Loading...</div>')
    parts.append("</fieldset>")
    # JS
    parts.append("<script>")
    parts.append("let COMMANDS_CACHE = null;")
    parts.append("function renderCommands() {")
    parts.append('  const container = document.getElementById("commands");')
    parts.append("  if (!COMMANDS_CACHE) { container.textContent = 'No commands loaded.'; return; }")
    parts.append('  const filterText = document.getElementById("cmd-filter").value.toLowerCase();')
    parts.append('  const sortMode = document.getElementById("cmd-sort").value;')
    parts.append("  let entries = Object.entries(COMMANDS_CACHE);")
    parts.append("  if (filterText) {")
    parts.append("    entries = entries.filter(([key, item]) => {")
    parts.append("      return key.toLowerCase().includes(filterText) || (item.name || '').toLowerCase().includes(filterText);")
    parts.append("    });")
    parts.append("  }")
    parts.append("  entries.sort(([ka, a], [kb, b]) => {")
    parts.append("    if (sortMode === 'key') { return ka.localeCompare(kb); }")
    parts.append("    return (a.name || '').localeCompare(b.name || '');")
    parts.append("  });")
    parts.append("  if (!entries.length) { container.textContent = 'No matches.'; return; }")
    parts.append("  let html = '';")
    parts.append("  for (const [key, item] of entries) {")
    parts.append("    html += '<h3>' + item.name + ' <small>(' + key + ')</small></h3>';")
    parts.append("    html += '<table><tr><th>Action</th><th>GET URL</th><th style=\"width:60px;text-align:center;\">Test</th></tr>';")
    parts.append("    html += '<tr><td>IN</td><td><code class=\"copyable\" onclick=\"copyToClipboard(this)\" title=\"Click to copy\">' + item.in_url + '</code></td>' +")
    parts.append("            '<td style=\"text-align:center;\"><a href=\"' + item.in_url + '\" target=\"_blank\" class=\"play-btn\" title=\"Test IN\">▶</a></td></tr>';")
    parts.append("    html += '<tr><td>OUT</td><td><code class=\"copyable\" onclick=\"copyToClipboard(this)\" title=\"Click to copy\">' + item.out_url + '</code></td>' +")
    parts.append("            '<td style=\"text-align:center;\"><a href=\"' + item.out_url + '\" target=\"_blank\" class=\"play-btn\" title=\"Test OUT\">▶</a></td></tr>';")
    parts.append("    html += '</table>';")
    parts.append("    const fields = item.fields || {};")
    parts.append("    const fkeys = Object.keys(fields);")
    parts.append("    if (fkeys.length) {")
    parts.append("      html += '<p><strong>Fields:</strong></p>';")
    parts.append("      html += '<table><tr><th>Field</th><th>Type</th><th>Command URL</th><th style=\"width:60px;text-align:center;\">Test</th></tr>';")
    parts.append("      for (const fid of fkeys) {")
    parts.append("        const ex = fields[fid];")
    parts.append("        if (ex.timecontrol_start_url) {")
    parts.append("          html += '<tr><td rowspan=\"3\">' + fid + '</td><td rowspan=\"3\">⏱ Timer</td>';")
    parts.append("          html += '<td><code class=\"copyable\" onclick=\"copyToClipboard(this)\" title=\"Click to copy\">' + ex.timecontrol_start_url + '</code></td>';")
    parts.append("          html += '<td style=\"text-align:center;\"><a href=\"' + ex.timecontrol_start_url + '\" target=\"_blank\" class=\"play-btn\" title=\"Start Timer\">▶</a></td></tr>';")
    parts.append("          html += '<tr><td><code class=\"copyable\" onclick=\"copyToClipboard(this)\" title=\"Click to copy\">' + ex.timecontrol_stop_url + '</code></td>';")
    parts.append("          html += '<td style=\"text-align:center;\"><a href=\"' + ex.timecontrol_stop_url + '\" target=\"_blank\" class=\"play-btn\" title=\"Stop Timer\">▶</a></td></tr>';")
    parts.append("          if (ex.start_10s_if_supported) {")
    parts.append("            html += '<tr><td><code class=\"copyable\" onclick=\"copyToClipboard(this)\" title=\"Click to copy\">' + ex.start_10s_if_supported + '</code></td>';")
    parts.append("            html += '<td style=\"text-align:center;\"><a href=\"' + ex.start_10s_if_supported + '\" target=\"_blank\" class=\"play-btn\" title=\"Start 10s\">▶</a></td></tr>';")
    parts.append("          } else {")
    parts.append("            html += '<tr><td colspan=\"2\" style=\"color:#666;\">Duration param not supported</td></tr>';")
    parts.append("          }")
    parts.append("        } else if (ex.set_url) {")
    parts.append("          html += '<tr><td>' + fid + '</td><td>Value</td>';")
    parts.append("          html += '<td><code class=\"copyable\" onclick=\"copyToClipboard(this)\" title=\"Click to copy\">' + ex.set_url + '</code></td>';")
    parts.append("          html += '<td style=\"text-align:center;\"><a href=\"' + ex.set_url + '\" target=\"_blank\" class=\"play-btn\" title=\"Set Value\">▶</a></td></tr>';")
    parts.append("        }")
    parts.append("      }")
    parts.append("      html += '</table>';")
    parts.append("    }")
    parts.append("  }")
    parts.append("  container.innerHTML = html;")
    parts.append("}")
    parts.append("async function loadCommands() {")
    parts.append('  const container = document.getElementById("commands");')
    parts.append("  container.textContent = 'Loading...';")
    parts.append("  try {")
    parts.append('    const res = await fetch("/singular/commands");')
    parts.append("    if (!res.ok) { container.textContent = 'Failed to load commands: ' + res.status; return; }")
    parts.append("    const data = await res.json();")
    parts.append("    COMMANDS_CACHE = data.catalog || {};")
    parts.append("    if (!Object.keys(COMMANDS_CACHE).length) {")
    parts.append("      container.textContent = 'No subcompositions discovered. Set token on Home and refresh registry.';")
    parts.append("      return;")
    parts.append("    }")
    parts.append("    renderCommands();")
    parts.append("  } catch (e) { container.textContent = 'Error: ' + e; }")
    parts.append("}")
    parts.append("async function rebuildRegistry() {")
    parts.append('  const container = document.getElementById("commands");')
    parts.append("  container.textContent = 'Rebuilding from Singular...';")
    parts.append("  try {")
    parts.append('    const res = await fetch("/singular/refresh", { method: "POST" });')
    parts.append("    const data = await res.json();")
    parts.append("    if (data.count !== undefined) {")
    parts.append("      container.textContent = 'Rebuilt: ' + data.count + ' subcompositions found. Reloading...';")
    parts.append("      setTimeout(loadCommands, 500);")
    parts.append("    } else { container.textContent = 'Rebuild failed'; }")
    parts.append("  } catch (e) { container.textContent = 'Error: ' + e; }")
    parts.append("}")
    parts.append("function copyToClipboard(el) {")
    parts.append("  const text = el.textContent || el.innerText;")
    parts.append("  navigator.clipboard.writeText(text).then(() => {")
    parts.append("    el.classList.add('copied');")
    parts.append("    const original = el.textContent;")
    parts.append("    el.setAttribute('data-original', original);")
    parts.append("    el.textContent = 'Copied!';")
    parts.append("    setTimeout(() => {")
    parts.append("      el.textContent = el.getAttribute('data-original');")
    parts.append("      el.classList.remove('copied');")
    parts.append("    }, 1500);")
    parts.append("  });")
    parts.append("}")
    parts.append("document.addEventListener('DOMContentLoaded', () => {")
    parts.append('  document.getElementById("cmd-filter").addEventListener("input", renderCommands);')
    parts.append('  document.getElementById("cmd-sort").addEventListener("change", renderCommands);')
    parts.append("});")
    parts.append("loadCommands();")
    parts.append("</script>")
    parts.append("</body></html>")
    return HTMLResponse("".join(parts))


@app.get("/settings", response_class=HTMLResponse)
def settings_page():
    parts: List[str] = []
    parts.append("<html><head>")
    parts.append("<title>Settings - Elliott's Singular Controls</title>")
    parts.append(_base_style())
    parts.append("</head><body>")
    parts.append(_nav_html())
    parts.append("<h1>Settings</h1>")
    # Theme toggle styles
    parts.append("<style>")
    parts.append("  .theme-toggle { display: flex; align-items: center; gap: 12px; margin: 16px 0; }")
    parts.append("  .theme-toggle-label { font-size: 14px; min-width: 50px; }")
    parts.append("  .toggle-switch { position: relative; width: 50px; height: 26px; }")
    parts.append("  .toggle-switch input { opacity: 0; width: 0; height: 0; }")
    parts.append("  .toggle-slider { position: absolute; cursor: pointer; top: 0; left: 0; right: 0; bottom: 0; background: #30363d; border-radius: 26px; transition: 0.3s; }")
    parts.append("  .toggle-slider:before { position: absolute; content: ''; height: 20px; width: 20px; left: 3px; bottom: 3px; background: white; border-radius: 50%; transition: 0.3s; }")
    parts.append("  .toggle-switch input:checked + .toggle-slider { background: #00bcd4; }")
    parts.append("  .toggle-switch input:checked + .toggle-slider:before { transform: translateX(24px); }")
    parts.append("</style>")
    # General
    parts.append("<fieldset><legend>General</legend>")
    is_light = CONFIG.theme == 'light'
    parts.append('<div class="theme-toggle">')
    parts.append('<span class="theme-toggle-label">Dark</span>')
    parts.append('<label class="toggle-switch"><input type="checkbox" id="theme-toggle" ' + ('checked' if is_light else '') + ' onchange="toggleTheme()" /><span class="toggle-slider"></span></label>')
    parts.append('<span class="theme-toggle-label">Light</span>')
    parts.append('</div>')
    parts.append("<p><strong>Server Port:</strong> <code>" + str(effective_port()) + "</code> (change via GUI launcher)</p>")
    parts.append("<p><strong>Version:</strong> <code>" + _runtime_version() + "</code></p>")
    parts.append("<p><strong>Config file:</strong> <code>" + html_escape(str(CONFIG_PATH)) + "</code></p>")
    parts.append("</fieldset>")
    # Config Import/Export
    parts.append("<fieldset><legend>Config Backup</legend>")
    parts.append("<p>Export your current configuration or import a previously saved config.</p>")
    parts.append('<button type="button" onclick="exportConfig()">Export Config</button>')
    parts.append('<input type="file" id="import-file" accept=".json" style="display:none;" onchange="importConfig()" />')
    parts.append('<button type="button" onclick="document.getElementById(\'import-file\').click()">Import Config</button>')
    parts.append('<pre id="import-output"></pre>')
    parts.append("</fieldset>")
    # Updates
    parts.append("<fieldset><legend>Updates</legend>")
    parts.append("<p>Current version: <code>" + _runtime_version() + "</code></p>")
    parts.append('<button type="button" onclick="checkUpdates()">Check GitHub for latest release</button>')
    parts.append('<pre id="update-output">Not checked yet.</pre>')
    parts.append("</fieldset>")
    # JS
    parts.append("<script>")
    parts.append("async function postJSON(url, data) {")
    parts.append("  const res = await fetch(url, {")
    parts.append('    method: "POST",')
    parts.append('    headers: { "Content-Type": "application/json" },')
    parts.append("    body: JSON.stringify(data),")
    parts.append("  });")
    parts.append("  return res.json();")
    parts.append("}")
    parts.append("async function toggleTheme() {")
    parts.append('  const isLight = document.getElementById("theme-toggle").checked;')
    parts.append('  const theme = isLight ? "light" : "dark";')
    parts.append('  await postJSON("/settings", { theme });')
    parts.append("  location.reload();")
    parts.append("}")
    parts.append("async function checkUpdates() {")
    parts.append('  const out = document.getElementById("update-output");')
    parts.append('  out.textContent = "Checking for updates...";')
    parts.append("  try {")
    parts.append('    const res = await fetch("/version/check");')
    parts.append("    const data = await res.json();")
    parts.append("    let msg = 'Current version: ' + data.current;")
    parts.append("    if (data.latest) {")
    parts.append("      msg += '\\nLatest release: ' + data.latest;")
    parts.append("    }")
    parts.append("    msg += '\\n\\n' + data.message;")
    parts.append("    if (data.release_url && !data.up_to_date) {")
    parts.append("      msg += '\\n\\nDownload: ' + data.release_url;")
    parts.append("    }")
    parts.append("    out.textContent = msg;")
    parts.append("  } catch (e) {")
    parts.append("    out.textContent = 'Version check failed: ' + e;")
    parts.append("  }")
    parts.append("}")
    parts.append("async function exportConfig() {")
    parts.append("  try {")
    parts.append('    const res = await fetch("/config/export");')
    parts.append("    const config = await res.json();")
    parts.append("    const blob = new Blob([JSON.stringify(config, null, 2)], { type: 'application/json' });")
    parts.append("    const url = URL.createObjectURL(blob);")
    parts.append("    const a = document.createElement('a');")
    parts.append("    a.href = url;")
    parts.append("    a.download = 'esc_config.json';")
    parts.append("    a.click();")
    parts.append("    URL.revokeObjectURL(url);")
    parts.append('    document.getElementById("import-output").textContent = "Config exported successfully!";')
    parts.append("  } catch (e) {")
    parts.append('    document.getElementById("import-output").textContent = "Export failed: " + e;')
    parts.append("  }")
    parts.append("}")
    parts.append("async function importConfig() {")
    parts.append('  const fileInput = document.getElementById("import-file");')
    parts.append("  const file = fileInput.files[0];")
    parts.append("  if (!file) return;")
    parts.append("  try {")
    parts.append("    const text = await file.text();")
    parts.append("    const config = JSON.parse(text);")
    parts.append('    const res = await fetch("/config/import", {')
    parts.append('      method: "POST",')
    parts.append('      headers: { "Content-Type": "application/json" },')
    parts.append("      body: JSON.stringify(config),")
    parts.append("    });")
    parts.append("    const data = await res.json();")
    parts.append('    document.getElementById("import-output").textContent = data.message || "Config imported!";')
    parts.append("    setTimeout(() => location.reload(), 2000);")
    parts.append("  } catch (e) {")
    parts.append('    document.getElementById("import-output").textContent = "Import failed: " + e;')
    parts.append("  }")
    parts.append("}")
    parts.append("checkUpdates();")
    parts.append("</script>")
    parts.append("</body></html>")
    return HTMLResponse("".join(parts))


@app.get("/help")
def help_index():
    return {
        "docs": "/docs",
        "note": "Most control endpoints support GET for quick triggering but POST is recommended for automation.",
        "examples": {
            "list_subs": "/singular/list",
            "all_commands_json": "/singular/commands",
            "commands_for_one": "/<key>/help",
            "trigger_in": "/<key>/in",
            "trigger_out": "/<key>/out",
            "set_field": "/<key>/set?field=Top%20Line&value=Hello",
            "timecontrol": "/<key>/timecontrol?field=Countdown%20Start&run=true&value=0&seconds=10",
        },
    }


# ================== 10. MAIN ENTRY POINT ==================

def main():
    """Main entry point for the application."""
    import uvicorn
    port = effective_port()
    logger.info(
        "Starting Elliott's Singular Controls v%s on http://localhost:%s (binding 0.0.0.0)",
        _runtime_version(),
        port
    )
    uvicorn.run(app, host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()