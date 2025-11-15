import os
import sys
import time
import re
import json
import logging
from pathlib import Path
from typing import List, Dict, Any, Optional
from urllib.parse import quote
from html import escape as html_escape

import requests
from fastapi import FastAPI, Query, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.routing import APIRoute
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from contextlib import asynccontextmanager

# ================== 0. PATHS & VERSION ==================

def _app_root() -> Path:
    """Folder where the app is running from (install dir or source)."""
    if getattr(sys, "frozen", False):  # PyInstaller exe
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent  # Go up one level from singular_tweaks/


def _runtime_version() -> str:
    """
    Try to read version from version.txt next to the app.
    Fallback to 'dev' if not present.
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
    return "1.0.1"


# ================== 1. CONFIG & GLOBALS ==================

DEFAULT_PORT = int(os.getenv("SINGULAR_TWEAKS_PORT", "3113"))

SINGULAR_API_BASE = "https://app.singular.live/apiv2"
TFL_URL = (
    "https://api.tfl.gov.uk/Line/Mode/"
    "tube,overground,dlr,elizabeth-line,tram,cable-car/Status"
)

def _config_dir() -> Path:
    if getattr(sys, "frozen", False):
        base = Path(sys.executable).parent
    else:
        # When running from source, use singular_tweaks directory
        base = Path(__file__).resolve().parent
    return base

CONFIG_PATH = _config_dir() / "singular_tweaks_config.json"

logger = logging.getLogger("singular_tweaks")
if not logger.handlers:
    logging.basicConfig(level=logging.INFO)


class AppConfig(BaseModel):
    singular_token: Optional[str] = None
    singular_stream_url: Optional[str] = None
    tfl_app_id: Optional[str] = None
    tfl_app_key: Optional[str] = None
    enable_tfl: bool = True
    enable_datastream: bool = True
    theme: str = "dark"
    port: Optional[int] = None


def load_config() -> AppConfig:
    base: Dict[str, Any] = {
        "singular_token": os.getenv("SINGULAR_TOKEN") or None,
        "singular_stream_url": os.getenv("SINGULAR_STREAM_URL") or None,
        "tfl_app_id": os.getenv("TFL_APP_ID") or None,
        "tfl_app_key": os.getenv("TFL_APP_KEY") or None,
        "enable_tfl": True,
        "enable_datastream": True,
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
            json.dump(cfg.dict(), f, indent=2)
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
    title="TfL + Singular Tweaks",
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
    if not CONFIG.enable_datastream:
        raise HTTPException(400, "Data Stream integration is disabled in settings")
    if not CONFIG.singular_stream_url:
        raise HTTPException(400, "No Singular data stream URL configured")
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
            "status": getattr(resp, 'status_code', 0),
            "response": getattr(resp, 'text', ''),
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
    enable_tfl: bool = True
    enable_datastream: bool = True
    theme: Optional[str] = "dark"

class SingularItem(BaseModel):
    subCompositionId: str
    state: Optional[str] = None
    payload: Optional[dict] = None


# ================== 5. HTML helpers ==================

def _nav_html() -> str:
    show_integrations = CONFIG.enable_tfl or CONFIG.enable_datastream
    parts = ['<div class="nav">']
    parts.append('<a href="/">Home</a>')
    parts.append('<a href="/commands">Commands</a>')
    if show_integrations:
        parts.append('<a href="/integrations">Integrations</a>')
    parts.append('<a href="/settings">Settings</a>')
    parts.append('</div>')
    return "".join(parts)


def _base_style() -> str:
    theme = CONFIG.theme or "dark"
    if theme == "light":
        bg = "#f5f5f5"; fg = "#111"; card_bg = "#fff"; border = "#ccc"; accent = "#0af"
    else:
        bg = "#05070a"; fg = "#f5f5f5"; card_bg = "#10141c"; border = "#333"; accent = "#4da3ff"

    lines = []
    lines.append("<style>")
    lines.append("  @font-face {")
    lines.append("    font-family: 'ITVReem';")
    lines.append("    src: url('/static/ITV Reem-Regular.ttf') format('truetype');")
    lines.append("    font-weight: normal;")
    lines.append("    font-style: normal;")
    lines.append("  }")
    lines.append(
        "  body { font-family: 'ITVReem', system-ui, -apple-system, BlinkMacSystemFont, sans-serif;"
        f" max-width: 1000px; margin: 2rem auto; background: {bg}; color: {fg}; padding: 0 1rem; }}"
    )
    lines.append(
        f"  fieldset {{ margin-bottom: 1.5rem; padding: 1rem; background: {card_bg}; border: 1px solid {border}; border-radius: 4px; }}"
    )
    lines.append("  legend { font-weight: 600; padding: 0 0.5rem; }")
    lines.append("  label { display:block; margin-top:0.5rem; }")
    lines.append(
        f"  input, select {{ width:100%; padding:0.35rem 0.5rem; box-sizing:border-box;"
        f" background:#181c26; color:{fg}; border:1px solid {border}; border-radius: 3px; }}"
    )
    lines.append(
        f"  button {{ margin-top:0.75rem; padding:0.4rem 0.8rem; cursor:pointer;"
        f" background:{accent}; color:#fff; border:none; border-radius: 3px; }}"
    )
    lines.append("  button:hover { opacity: 0.9; }")
    lines.append(
        "  pre { background:#000; color:#0f0; padding:0.5rem; white-space:pre-wrap; max-height:300px; overflow:auto; border-radius: 3px; }"
    )
    lines.append(
        "  .version-badge { position:fixed; top:10px; right:10px; background:#222; color:#fff;"
        " padding:4px 10px; border-radius:999px; font-size:12px; opacity:0.9; }"
    )
    lines.append("  .nav { position:fixed; top:10px; left:10px; font-size:13px; }")
    lines.append(f"  .nav a {{ color:{accent}; text-decoration:none; margin-right:8px; }}")
    lines.append("  .nav a:hover { text-decoration: underline; }")
    lines.append("  table { border-collapse:collapse; width:100%; margin-top:0.5rem; }")
    lines.append(f"  th, td {{ border:1px solid {border}; padding:6px 8px; font-size:13px; }}")
    lines.append("  th { background:#222; color:#fff; text-align: left; }")
    lines.append(
        "  code { font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas,"
        ' "Liberation Mono", "Courier New", monospace; background: rgba(255,255,255,0.1); padding: 2px 4px; border-radius: 3px; }'
    )
    lines.append("  h1, h2, h3 { margin-top: 1rem; }")
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
            "enable_datastream": CONFIG.enable_datastream,
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
    CONFIG.singular_stream_url = cfg.stream_url
    save_config(CONFIG)
    return {"ok": True, "message": "Data Stream URL updated"}


@app.get("/settings/json")
def get_settings_json():
    return {
        "port": effective_port(),
        "raw_port": CONFIG.port,
        "enable_tfl": CONFIG.enable_tfl,
        "enable_datastream": CONFIG.enable_datastream,
        "config_path": str(CONFIG_PATH),
        "theme": CONFIG.theme,
    }


@app.post("/settings")
def update_settings(settings: SettingsIn):
    CONFIG.enable_tfl = settings.enable_tfl
    CONFIG.enable_datastream = settings.enable_datastream
    CONFIG.port = settings.port
    CONFIG.theme = (settings.theme or "dark")
    save_config(CONFIG)
    return {
        "ok": True,
        "message": "Settings updated. Restart app to apply new port.",
        "port": effective_port(),
        "enable_tfl": CONFIG.enable_tfl,
        "enable_datastream": CONFIG.enable_datastream,
        "theme": CONFIG.theme,
    }


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
    parts.append("<title>Singular Tweaks v" + _runtime_version() + "</title>")
    parts.append(_base_style())
    parts.append("</head><body>")
    parts.append(_nav_html())
    parts.append('<div class="version-badge">v' + _runtime_version() + " • port " + str(effective_port()) + "</div>")
    parts.append("<h1>Singular Tweaks</h1>")
    parts.append("<p>Mainly used to send <strong>GET</strong> and simple HTTP commands to your Singular Control App.</p>")
    saved = "Not set"
    if CONFIG.singular_token:
        saved = "..." + CONFIG.singular_token[-6:]
    parts.append('<fieldset><legend>Singular Control App</legend>')
    parts.append("<p>Enter your <strong>Control App Token</strong> (from Singular.live).</p>")
    parts.append('<p>Saved token: <code id="saved-token">' + html_escape(saved) + "</code></p>")
    parts.append('<p>Status: <span id="singular-status">Unknown</span></p>')
    parts.append('<form id="singular-form">')
    parts.append('<label>Control App Token <input name="token" autocomplete="off" /></label>')
    parts.append('<button type="submit">Save Token &amp; Refresh Commands</button>')
    parts.append('<button type="button" onclick="pingSingular()">Ping Singular</button>')
    parts.append('<button type="button" onclick="refreshRegistry()">Rebuild Command List</button>')
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
    parts.append('      saved.textContent = "..." + token.slice(-6);')
    parts.append("    } else {")
    parts.append('      saved.textContent = "Not set";')
    parts.append("    }")
    parts.append("  } catch (e) { console.error(e); }")
    parts.append("}")
    parts.append("async function pingSingular() {")
    parts.append('  const statusEl = document.getElementById("singular-status");')
    parts.append('  statusEl.textContent = "Checking...";')
    parts.append("  try {")
    parts.append('    const res = await fetch("/singular/ping");')
    parts.append("    const txt = await res.text();")
    parts.append("    try {")
    parts.append("      const data = JSON.parse(txt);")
    parts.append("      if (data.ok) {")
    parts.append('        statusEl.textContent = "Connected (" + (data.subs || 0) + " subs)";')
    parts.append("      } else { statusEl.textContent = 'Error'; }")
    parts.append("    } catch (e) { statusEl.textContent = txt; }")
    parts.append("  } catch (e) { statusEl.textContent = 'Ping failed'; }")
    parts.append("}")
    parts.append("async function refreshRegistry() {")
    parts.append('  const statusEl = document.getElementById("singular-status");')
    parts.append('  statusEl.textContent = "Refreshing registry...";')
    parts.append("  try {")
    parts.append('    const res = await fetch("/singular/refresh", { method: "POST" });')
    parts.append("    const data = await res.json();")
    parts.append('    statusEl.textContent = "Registry: " + (data.count || 0) + " subs";')
    parts.append("  } catch (e) { statusEl.textContent = 'Refresh failed'; }")
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


@app.get("/integrations", response_class=HTMLResponse)
def integrations_page():
    parts: List[str] = []
    parts.append("<html><head>")
    parts.append("<title>Integrations - Singular Tweaks</title>")
    parts.append(_base_style())
    parts.append("</head><body>")
    parts.append(_nav_html())
    parts.append('<div class="version-badge">v' + _runtime_version() + " • port " + str(effective_port()) + "</div>")
    parts.append("<h1>Integrations</h1>")
    parts.append("<p>Optional integrations for TfL and Singular Data Stream.</p>")
    # Data stream
    parts.append("<fieldset><legend>Singular Data Stream</legend>")
    cur = html_escape(CONFIG.singular_stream_url or "not set")
    parts.append("<p>Currently: <code>" + cur + "</code></p>")
    parts.append("<p>Enabled in settings: <strong>" + ("Yes" if CONFIG.enable_datastream else "No") + "</strong></p>")
    parts.append('<form id="stream-form">')
    stream_val = html_escape(CONFIG.singular_stream_url or "")
    parts.append('<label>Data Stream URL <input name="stream_url" value="' + stream_val + '" autocomplete="off" /></label>')
    parts.append('<button type="submit">Save Data Stream URL</button>')
    parts.append("</form></fieldset>")
    # TfL
    parts.append("<fieldset><legend>TfL API (optional)</legend>")
    parts.append("<p>Enabled in settings: <strong>" + ("Yes" if CONFIG.enable_tfl else "No") + "</strong></p>")
    parts.append('<form id="tfl-form">')
    tfl_id = html_escape(CONFIG.tfl_app_id or "")
    tfl_key = html_escape(CONFIG.tfl_app_key or "")
    parts.append('<label>TfL App ID <input name="app_id" value="' + tfl_id + '" autocomplete="off" /></label>')
    parts.append('<label>TfL App Key <input name="app_key" value="' + tfl_key + '" autocomplete="off" /></label>')
    parts.append('<button type="submit">Save TfL Credentials</button>')
    parts.append("</form></fieldset>")
    # JS
    parts.append("<script>")
    parts.append("async function postJSON(url, data) {")
    parts.append("  const res = await fetch(url, {")
    parts.append('    method: "POST",')
    parts.append('    headers: { "Content-Type": "application/json" },')
    parts.append("    body: JSON.stringify(data),")
    parts.append("  });")
    parts.append("  return res.text();")
    parts.append("}")
    parts.append('document.getElementById("stream-form").onsubmit = async (e) => {')
    parts.append("  e.preventDefault();")
    parts.append("  const f = e.target;")
    parts.append("  const stream_url = f.stream_url.value;")
    parts.append('  await postJSON("/config/stream", { stream_url });')
    parts.append("  alert('Data stream updated.');")
    parts.append("};")
    parts.append('document.getElementById("tfl-form").onsubmit = async (e) => {')
    parts.append("  e.preventDefault();")
    parts.append("  const f = e.target;")
    parts.append("  const app_id = f.app_id.value;")
    parts.append("  const app_key = f.app_key.value;")
    parts.append('  await postJSON("/config/tfl", { app_id, app_key });')
    parts.append("  alert('TfL config updated.');")
    parts.append("};")
    parts.append("</script>")
    parts.append("</body></html>")
    return HTMLResponse("".join(parts))


@app.get("/commands", response_class=HTMLResponse)
def commands_page(request: Request):
    base = _base_url(request)
    parts: List[str] = []
    parts.append("<html><head>")
    parts.append("<title>Commands - Singular Tweaks</title>")
    parts.append(_base_style())
    parts.append("</head><body>")
    parts.append(_nav_html())
    parts.append('<div class="version-badge">v' + _runtime_version() + " • port " + str(effective_port()) + "</div>")
    parts.append("<h1>Singular Commands</h1>")
    parts.append("<p>This view focuses on simple <strong>GET</strong> triggers you can use in automation systems.</p>")
    parts.append("<p>Base URL: <code>" + html_escape(base) + "</code></p>")
    parts.append("<fieldset><legend>Discovered Subcompositions</legend>")
    parts.append('<p><button type="button" onclick="loadCommands()">Reload Commands</button></p>')
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
    parts.append("    html += '<table><tr><th>Action</th><th>GET URL</th><th>Test</th></tr>';")
    parts.append("    html += '<tr><td>IN</td><td><code>' + item.in_url + '</code></td>' +")
    parts.append("            '<td><a href=\"' + item.in_url + '\" target=\"_blank\">Open</a></td></tr>';")
    parts.append("    html += '<tr><td>OUT</td><td><code>' + item.out_url + '</code></td>' +")
    parts.append("            '<td><a href=\"' + item.out_url + '\" target=\"_blank\">Open</a></td></tr>';")
    parts.append("    html += '</table>';")
    parts.append("    const fields = item.fields || {};")
    parts.append("    const fkeys = Object.keys(fields);")
    parts.append("    if (fkeys.length) {")
    parts.append("      html += '<p><strong>Fields:</strong></p>';")
    parts.append("      html += '<table><tr><th>Field</th><th>Example GET</th></tr>';")
    parts.append("      for (const fid of fkeys) {")
    parts.append("        const ex = fields[fid];")
    parts.append("        if (ex.set_url) {")
    parts.append("          html += '<tr><td>' + fid + '</td><td><code>' + ex.set_url + '</code></td></tr>';")
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
    parts.append("<title>Settings - Singular Tweaks</title>")
    parts.append(_base_style())
    parts.append("</head><body>")
    parts.append(_nav_html())
    parts.append('<div class="version-badge">v' + _runtime_version() + " • port " + str(effective_port()) + "</div>")
    parts.append("<h1>Settings</h1>")
    # General
    parts.append("<fieldset><legend>General</legend>")
    parts.append('<form id="settings-form">')
    parts.append('<label>Port (takes effect on next restart)')
    parts.append('<input id="port-input" name="port" type="number" value="' + str(effective_port()) + '" />')
    parts.append("</label>")
    parts.append('<label><input type="checkbox" id="enable-tfl" ' + ('checked' if CONFIG.enable_tfl else '') + ' /> Enable TfL integration</label>')
    parts.append('<label><input type="checkbox" id="enable-ds" ' + ('checked' if CONFIG.enable_datastream else '') + ' /> Enable Data Stream integration</label>')
    parts.append('<label>Theme <select id="theme-select">')
    parts.append('<option value="dark"' + (' selected' if CONFIG.theme == 'dark' else '') + ">Dark</option>")
    parts.append('<option value="light"' + (' selected' if CONFIG.theme == 'light' else '') + ">Light</option>")
    parts.append("</select></label>")
    parts.append('<button type="submit">Save Settings</button>')
    parts.append("</form>")
    parts.append("<p>Config file: <code>" + html_escape(str(CONFIG_PATH)) + "</code></p>")
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
    parts.append('document.getElementById("settings-form").onsubmit = async (e) => {')
    parts.append("  e.preventDefault();")
    parts.append('  const portVal = document.getElementById("port-input").value;')
    parts.append("  const port = portVal ? parseInt(portVal, 10) : null;")
    parts.append('  const enable_tfl = document.getElementById("enable-tfl").checked;')
    parts.append('  const enable_datastream = document.getElementById("enable-ds").checked;')
    parts.append('  const theme = document.getElementById("theme-select").value || "dark";')
    parts.append('  const data = await postJSON("/settings", { port, enable_tfl, enable_datastream, theme });')
    parts.append("  alert(data.message || 'Settings saved. Restart app to apply new port.');")
    parts.append("  location.reload();")
    parts.append("};")
    parts.append("async function checkUpdates() {")
    parts.append('  const out = document.getElementById("update-output");')
    parts.append('  out.textContent = "Checking GitHub...";')
    parts.append("  try {")
    parts.append('    const owner = "BlueElliott";')
    parts.append('    const repo = "Singular-Tweaks";')
    parts.append('    const url = "https://api.github.com/repos/" + owner + "/" + repo + "/releases/latest";')
    parts.append("    const res = await fetch(url);")
    parts.append("    if (!res.ok) {")
    parts.append("      if (res.status === 404) {")
    parts.append('        out.textContent = "Updates: this repository is private or has no releases visible to the public.";')
    parts.append("      } else {")
    parts.append('        out.textContent = "GitHub API error: " + res.status;')
    parts.append("      }")
    parts.append("      return;")
    parts.append("    }")
    parts.append("    const data = await res.json();")
    parts.append("    const latest = data.tag_name || data.name || 'unknown';")
    parts.append("    const current = '" + _runtime_version() + "';")
    parts.append("    let msg = 'Current version: ' + current + '\\nLatest release: ' + latest;")
    parts.append("    if (latest !== current && latest !== 'v' + current) {")
    parts.append("      msg += '\\n\\nA newer version may be available.';")
    parts.append("    } else {")
    parts.append("      msg += '\\n\\nYou are up to date.';")
    parts.append("    }")
    parts.append("    if (data.html_url) { msg += '\\nRelease page: ' + data.html_url; }")
    parts.append("    out.textContent = msg;")
    parts.append("  } catch (e) { out.textContent = 'Update check failed: ' + e; }")
    parts.append("}")
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
        "Starting Singular Tweaks v%s on http://localhost:%s (binding 0.0.0.0)",
        _runtime_version(),
        port
    )
    uvicorn.run(app, host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()