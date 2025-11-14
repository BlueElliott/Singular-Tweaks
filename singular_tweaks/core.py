try:
    # Normal case: installed as a package
    from singular_tweaks import __version__  # type: ignore
except Exception:
    # Fallback for frozen / odd environments
    __version__ = "dev"

import os
import sys
import time
import re
import json
import logging
from pathlib import Path
from typing import List, Dict, Any, Optional
from urllib.parse import quote

import requests
from fastapi import FastAPI, Query, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from contextlib import asynccontextmanager
from fastapi.routing import APIRoute

# ================== 1. CONFIG & GLOBALS ==================

# Default port (can be overridden by env var; user-configurable in settings)
DEFAULT_PORT = int(os.getenv("SINGULAR_TWEAKS_PORT", "3113"))

SINGULAR_API_BASE = "https://app.singular.live/apiv2"
TFL_URL = (
    "https://api.tfl.gov.uk/Line/Mode/"
    "tube,overground,dlr,elizabeth-line,tram,cable-car/Status"
)

# Where to store persistent config (next to the exe / package)
def _config_dir() -> Path:
    if getattr(sys, "frozen", False):  # PyInstaller
        base = Path(sys.executable).parent
    else:
        base = Path(__file__).resolve().parent
    return base


CONFIG_PATH = _config_dir() / "singular_tweaks_config.json"

logger = logging.getLogger("singular_tweaks")
if not logger.handlers:
    logging.basicConfig(level=logging.INFO)


class AppConfig(BaseModel):
    # Singular Control App token (for /model + /control calls)
    singular_token: Optional[str] = None
    # Singular Data Stream URL (for TfL payloads)
    singular_stream_url: Optional[str] = None
    # TfL creds (optional extra)
    tfl_app_id: Optional[str] = None
    tfl_app_key: Optional[str] = None

    # Feature toggles
    enable_tfl: bool = True
    enable_datastream: bool = True

    # Optional persistent port override
    port: Optional[int] = None


def load_config() -> AppConfig:
    """
    Load configuration from env + JSON file (if present).
    Env vars provide defaults; file can override.
    """
    base: Dict[str, Any] = {
        "singular_token": os.getenv("SINGULAR_TOKEN") or None,
        "singular_stream_url": os.getenv("SINGULAR_STREAM_URL") or None,
        "tfl_app_id": os.getenv("TFL_APP_ID") or None,
        "tfl_app_key": os.getenv("TFL_APP_KEY") or None,
        "enable_tfl": True,
        "enable_datastream": True,
        "port": int(os.getenv("SINGULAR_TWEAKS_PORT"))
        if os.getenv("SINGULAR_TWEAKS_PORT")
        else None,
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


# Simple in-memory event log of HTTP-triggered commands
COMMAND_LOG: List[str] = []
MAX_LOG_ENTRIES = 200


def log_event(kind: str, detail: str) -> None:
    ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    line = f"[{ts}] {kind}: {detail}"
    COMMAND_LOG.append(line)
    if len(COMMAND_LOG) > MAX_LOG_ENTRIES:
        del COMMAND_LOG[: len(COMMAND_LOG) - MAX_LOG_ENTRIES]


# ================== 2. OPENAPI & APP ==================


def generate_unique_id(route: APIRoute) -> str:
    methods = sorted(
        [
            m
            for m in route.methods
            if m in {"GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"}
        ]
    )
    method = methods[0].lower() if methods else "get"
    safe_path = re.sub(r"[^a-z0-9]+", "-", route.path.lower()).strip("-")
    return f"{route.name}-{method}-{safe_path}"


app = FastAPI(
    title="TfL + Singular Tweaks",
    description="Helper UI and HTTP API for controlling Singular.live + optional TfL data.",
    generate_unique_id_function=generate_unique_id,
)


def tfl_params() -> Dict[str, str]:
    p: Dict[str, str] = {}
    if CONFIG.tfl_app_id and CONFIG.tfl_app_key and CONFIG.enable_tfl:
        p["app_id"] = CONFIG.tfl_app_id
        p["app_key"] = CONFIG.tfl_app_key
    return p


def fetch_all_line_statuses() -> Dict[str, str]:
    if not CONFIG.enable_tfl:
        raise HTTPException(400, "TfL integration is disabled in settings")
    r = requests.get(TFL_URL, params=tfl_params(), timeout=10)
    r.raise_for_status()
    out: Dict[str, str] = {}
    for line in r.json():
        out[line["name"]] = (
            line.get("lineStatuses", [{}])[0].get("statusSeverityDescription", "Unknown")
        )
    return out


def send_to_datastream(payload: Dict[str, Any]):
    if not CONFIG.enable_datastream:
        raise HTTPException(400, "Data Stream integration is disabled in settings")
    if not CONFIG.singular_stream_url:
        raise HTTPException(400, "No Singular data stream URL configured")
    resp = requests.put(
        CONFIG.singular_stream_url,
        json=payload,
        headers={"Content-Type": "application/json"},
        timeout=10,
    )
    try:
        resp.raise_for_status()
    except Exception as e:
        logger.exception("Datastream PUT failed")
        return {
            "stream_url": CONFIG.singular_stream_url,
            "status": resp.status_code,
            "response": resp.text,
            "error": str(e),
        }
    return {
        "stream_url": CONFIG.singular_stream_url,
        "status": resp.status_code,
        "response": resp.text,
    }


def ctrl_patch(items: list):
    if not CONFIG.singular_token:
        raise HTTPException(400, "No Singular control app token configured")
    ctrl_control = f"{SINGULAR_API_BASE}/controlapps/{CONFIG.singular_token}/control"
    resp = requests.patch(
        ctrl_control,
        json=items,
        headers={"Content-Type": "application/json"},
        timeout=10,
    )
    try:
        resp.raise_for_status()
    except Exception:
        logger.exception("Control PATCH failed")
    log_event("Control PATCH", f"{ctrl_control} items={len(items)}")
    return resp


def now_ms_float() -> float:
    return float(time.time() * 1000)


def slugify(name: str) -> str:
    s = name.lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s or "item"


def _base_url(request: Request) -> str:
    host = (
        request.headers.get("x-forwarded-host")
        or request.headers.get("host")
        or request.url.netloc
    )
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme
    return f"{proto}://{host}"


# ================== 2.3 AUTO-DISCOVERY REGISTRY ==================

REGISTRY: Dict[str, Dict[str, Any]] = {}
ID_TO_KEY: Dict[str, str] = {}


def singular_model_fetch() -> Any:
    if not CONFIG.singular_token:
        raise RuntimeError("No Singular control app token configured")
    ctrl_model = f"{SINGULAR_API_BASE}/controlapps/{CONFIG.singular_token}/model"
    r = requests.get(ctrl_model, timeout=10)
    if not r.ok:
        raise RuntimeError(f"Model fetch failed: {r.status_code} {r.text}")
    return r.json()


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
    raise HTTPException(status_code=404, detail=f"Subcomposition not found: {key_or_id}")


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

# ================== 3. CONFIG HTTP API ==================


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


def _nav_html() -> str:
    show_integrations = CONFIG.enable_tfl or CONFIG.enable_datastream
    return (
        '<div class="nav">'
        '<a href="/">Home</a>'
        '<a href="/commands">Commands</a>'
        f'{"<a href=\"/integrations\">Integrations</a>" if show_integrations else ""}'
        '<a href="/settings">Settings</a>'
        '<a href="/docs">API docs</a>'
        "</div>"
    )


def _base_style() -> str:
    return """
      <style>
        body {
          font-family: system-ui, -apple-system, BlinkMacSystemFont, sans-serif;
          max-width: 1000px;
          margin: 2rem auto;
        }
        fieldset { margin-bottom: 1.5rem; padding: 1rem; }
        legend { font-weight: 600; }
        label { display:block; margin-top:0.5rem; }
        input, select {
          width:100%; padding:0.35rem 0.5rem; box-sizing:border-box;
        }
        button {
          margin-top:0.75rem; padding:0.4rem 0.8rem; cursor:pointer;
        }
        pre {
          background:#111; color:#0f0; padding:0.5rem;
          white-space:pre-wrap; max-height: 300px; overflow:auto;
        }
        .version-badge {
          position: fixed;
          top: 10px;
          right: 10px;
          background: #222;
          color: #fff;
          padding: 4px 10px;
          border-radius: 999px;
          font-size: 12px;
          opacity: 0.9;
        }
        .nav {
          position: fixed;
          top: 10px;
          left: 10px;
          font-size: 13px;
        }
        .nav a {
          color: #06f;
          text-decoration: none;
          margin-right: 8px;
        }
        table {
          border-collapse: collapse;
          width: 100%;
          margin-top: 0.5rem;
        }
        th, td {
          border: 1px solid #444;
          padding: 4px 6px;
          font-size: 13px;
        }
        th { background:#222; }
        code { font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace; }
      </style>
    """


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
    }


@app.post("/settings")
def update_settings(settings: SettingsIn):
    CONFIG.enable_tfl = settings.enable_tfl
    CONFIG.enable_datastream = settings.enable_datastream
    CONFIG.port = settings.port
    save_config(CONFIG)
    return {
        "ok": True,
        "message": "Settings updated. Restart app to apply new port.",
        "port": effective_port(),
        "enable_tfl": CONFIG.enable_tfl,
        "enable_datastream": CONFIG.enable_datastream,
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


# ================== 4. OLD DATA STREAM / TFL ENDPOINTS ==================


@app.get("/health")
def health():
    return {"status": "ok", "version": __version__, "port": effective_port()}


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


# ================== 4.1 CONTROL APP: INTROSPECTION & COMMANDS ==================


class SingularItem(BaseModel):
    subCompositionId: str
    state: Optional[str] = None
    payload: Optional[dict] = None


@app.post("/singular/control")
def singular_control(items: List[SingularItem]):
    r = ctrl_patch([i.dict(exclude_none=True) for i in items])
    return {"status": r.status_code, "response": r.text}


@app.get("/singular/list")
def singular_list():
    return {
        k: {
            "id": v["id"],
            "name": v["name"],
            "fields": list(v["fields"].keys()),
        }
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
    examples["set_curl"] = f'curl -X POST "{set_url}"'

    if ftype == "timecontrol":
        start = f"{base}/{key}/timecontrol?field={quote(field_id)}&run=true&value=0"
        stop = f"{base}/{key}/timecontrol?field={quote(field_id)}&run=false&value=0"
        examples["timecontrol_start_url"] = start
        examples["timecontrol_stop_url"] = stop
        examples["timecontrol_start_curl"] = f'curl -X POST "{start}"'
        examples["timecontrol_stop_curl"] = f'curl -X POST "{stop}"'
        examples["start_10s_if_supported"] = (
            f'{base}/{key}/timecontrol?field={quote(field_id)}&run=true&value=0&seconds=10'
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
    return {
        "docs": f"{base}/docs",
        "commands": entry,
    }


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
    seconds: Optional[int] = Query(
        None, description="optional duration for countdowns"
    ),
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


# ================== 5. HTML PAGES ==================


@app.get("/", response_class=HTMLResponse)
def index():
    saved = "Not set"
    if CONFIG.singular_token:
        tail = CONFIG.singular_token[-6:]
        saved = f"...{tail}"
    status_badge = "Unknown"
    return f"""
    <html>
      <head>
        <title>Singular Tweaks v{__version__}</title>
        {_base_style()}
      </head>
      <body>
        {_nav_html()}
        <div class="version-badge">v{__version__} • port {effective_port()}</div>

        <h1>Singular Tweaks</h1>
        <p>Mainly used to send <strong>GET</strong> and simple HTTP commands to your Singular Control App.</p>

        <fieldset>
          <legend>Singular Control App</legend>
          <p>Enter your <strong>Control App Token</strong> (from Singular.live).</p>
          <p>Saved token: <code id="saved-token">{saved}</code></p>
          <p>Status: <span id="singular-status">{status_badge}</span></p>
          <form id="singular-form">
            <label>Control App Token
              <input name="token" autocomplete="off" />
            </label>
            <button type="submit">Save Token &amp; Refresh Commands</button>
            <button type="button" onclick="pingSingular()">Ping Singular</button>
            <button type="button" onclick="refreshRegistry()">Rebuild Command List</button>
          </form>
        </fieldset>

        <fieldset>
          <legend>Event Log</legend>
          <p>Shows recent HTTP commands and updates triggered by this tool.</p>
          <button type="button" onclick="loadEvents()">Refresh Log</button>
          <pre id="log">No events yet.</pre>
        </fieldset>

        <script>
          async function postJSON(url, data) {{
            const res = await fetch(url, {{
              method: "POST",
              headers: {{ "Content-Type": "application/json" }},
              body: JSON.stringify(data),
            }});
            const text = await res.text();
            return text;
          }}

          async function loadConfig() {{
            try {{
              const res = await fetch("/config");
              if (!res.ok) return;
              const cfg = await res.json();
              const tokenSet = cfg.singular.token_set;
              const token = cfg.singular.token;
              const saved = document.getElementById("saved-token");
              if (tokenSet && token) {{
                saved.textContent = "..." + token.slice(-6);
              }} else {{
                saved.textContent = "Not set";
              }}
            }} catch (e) {{
              console.error(e);
            }}
          }}

          async function pingSingular() {{
            const statusEl = document.getElementById("singular-status");
            statusEl.textContent = "Checking...";
            try {{
              const res = await fetch("/singular/ping");
              const txt = await res.text();
              try {{
                const data = JSON.parse(txt);
                if (data.ok) {{
                  statusEl.textContent = "Connected (" + (data.subs || 0) + " subs)";
                }} else {{
                  statusEl.textContent = "Error";
                }}
              }} catch (e) {{
                statusEl.textContent = txt;
              }}
            }} catch (e) {{
              statusEl.textContent = "Ping failed";
            }}
          }}

          async function refreshRegistry() {{
            const statusEl = document.getElementById("singular-status");
            statusEl.textContent = "Refreshing registry...";
            try {{
              const res = await fetch("/singular/refresh", {{ method: "POST" }});
              const data = await res.json();
              statusEl.textContent = "Registry: " + (data.count || 0) + " subs";
            }} catch (e) {{
              statusEl.textContent = "Refresh failed";
            }}
          }}

          async function loadEvents() {{
            try {{
              const res = await fetch("/events");
              const data = await res.json();
              document.getElementById("log").innerText = (data.events || []).join("\\n") || "No events yet.";
            }} catch (e) {{
              document.getElementById("log").innerText = "Failed to load events: " + e;
            }}
          }}

          document.getElementById("singular-form").onsubmit = async (e) => {{
            e.preventDefault();
            const f = e.target;
            const token = f.token.value;
            if (!token) {{
              alert("Please enter a token.");
              return;
            }}
            const txt = await postJSON("/config/singular", {{ token }});
            await loadConfig();
            await pingSingular();
            alert("Token saved. Registry refreshed.");
          }};

          // Initial load
          loadConfig();
          pingSingular();
          loadEvents();
        </script>
      </body>
    </html>
    """


@app.get("/integrations", response_class=HTMLResponse)
def integrations_page():
    return f"""
    <html>
      <head>
        <title>Integrations - Singular Tweaks</title>
        {_base_style()}
      </head>
      <body>
        {_nav_html()}
        <div class="version-badge">v{__version__} • port {effective_port()}</div>

        <h1>Integrations</h1>
        <p>Optional integrations for TfL and Singular Data Stream.</p>

        <fieldset>
          <legend>Singular Data Stream</legend>
          <p>Currently: <code>{CONFIG.singular_stream_url or "not set"}</code></p>
          <p>Enabled in settings: <strong>{"Yes" if CONFIG.enable_datastream else "No"}</strong></p>
          <form id="stream-form">
            <label>Data Stream URL
              <input name="stream_url" value="{CONFIG.singular_stream_url or ""}" autocomplete="off" />
            </label>
            <button type="submit">Save Data Stream URL</button>
          </form>
        </fieldset>

        <fieldset>
          <legend>TfL API (optional)</legend>
          <p>Enabled in settings: <strong>{"Yes" if CONFIG.enable_tfl else "No"}</strong></p>
          <form id="tfl-form">
            <label>TfL App ID
              <input name="app_id" value="{CONFIG.tfl_app_id or ""}" autocomplete="off" />
            </label>
            <label>TfL App Key
              <input name="app_key" value="{CONFIG.tfl_app_key or ""}" autocomplete="off" />
            </label>
            <button type="submit">Save TfL Credentials</button>
          </form>
        </fieldset>

        <script>
          async function postJSON(url, data) {{
            const res = await fetch(url, {{
              method: "POST",
              headers: {{ "Content-Type": "application/json" }},
              body: JSON.stringify(data),
            }});
            return res.text();
          }}

          document.getElementById("stream-form").onsubmit = async (e) => {{
            e.preventDefault();
            const f = e.target;
            const stream_url = f.stream_url.value;
            const txt = await postJSON("/config/stream", {{ stream_url }});
            alert("Data stream updated.");
          }};

          document.getElementById("tfl-form").onsubmit = async (e) => {{
            e.preventDefault();
            const f = e.target;
            const app_id = f.app_id.value;
            const app_key = f.app_key.value;
            const txt = await postJSON("/config/tfl", {{ app_id, app_key }});
            alert("TfL config updated.");
          }};
        </script>
      </body>
    </html>
    """


@app.get("/commands", response_class=HTMLResponse)
def commands_page(request: Request):
    base = _base_url(request)
    return f"""
    <html>
      <head>
        <title>Commands - Singular Tweaks</title>
        {_base_style()}
      </head>
      <body>
        {_nav_html()}
        <div class="version-badge">v{__version__} • port {effective_port()}</div>

        <h1>Singular Commands</h1>
        <p>This view focuses on simple <strong>GET</strong> triggers you can use in automation systems.</p>
        <p>Base URL: <code>{base}</code></p>

        <fieldset>
          <legend>Discovered Subcompositions</legend>
          <p><button type="button" onclick="loadCommands()">Reload Commands</button></p>
          <div id="commands">Loading...</div>
        </fieldset>

        <script>
          async function loadCommands() {{
            const container = document.getElementById("commands");
            container.textContent = "Loading...";
            try {{
              const res = await fetch("/singular/commands");
              if (!res.ok) {{
                container.textContent = "Failed to load commands: " + res.status;
                return;
              }}
              const data = await res.json();
              const catalog = data.catalog || {{}};
              let html = "";
              const keys = Object.keys(catalog);
              if (!keys.length) {{
                container.textContent = "No subcompositions discovered. Set token on Home and refresh registry.";
                return;
              }}
              for (const key of keys) {{
                const item = catalog[key];
                html += "<h3>" + item.name + " <small>(" + key + ")</small></h3>";
                html += "<table><tr><th>Action</th><th>GET URL</th><th>Test</th></tr>";
                html += "<tr><td>IN</td><td><code>" + item.in_url + "</code></td>";
                html += "<td><a href='" + item.in_url + "' target='_blank'>Open</a></td></tr>";
                html += "<tr><td>OUT</td><td><code>" + item.out_url + "</code></td>";
                html += "<td><a href='" + item.out_url + "' target='_blank'>Open</a></td></tr>";
                html += "</table>";

                const fields = item.fields || {{}};
                const fkeys = Object.keys(fields);
                if (fkeys.length) {{
                  html += "<p><strong>Fields:</strong></p>";
                  html += "<table><tr><th>Field</th><th>Example GET</th></tr>";
                  for (const fid of fkeys) {{
                    const ex = fields[fid];
                    if (ex.set_url) {{
                      html += "<tr><td>" + fid + "</td><td><code>" + ex.set_url + "</code></td></tr>";
                    }}
                  }}
                  html += "</table>";
                }}
              }}
              container.innerHTML = html;
            }} catch (e) {{
              container.textContent = "Error: " + e;
            }}
          }}

          loadCommands();
        </script>
      </body>
    </html>
    """


@app.get("/settings", response_class=HTMLResponse)
def settings_page():
    return f"""
    <html>
      <head>
        <title>Settings - Singular Tweaks</title>
        {_base_style()}
      </head>
      <body>
        {_nav_html()}
        <div class="version-badge">v{__version__} • port {effective_port()}</div>

        <h1>Settings</h1>

        <fieldset>
          <legend>General</legend>
          <form id="settings-form">
            <label>Port (takes effect on next restart)
              <input id="port-input" name="port" type="number" value="{effective_port()}" />
            </label>
            <label>
              <input type="checkbox" id="enable-tfl" {"checked" if CONFIG.enable_tfl else ""} />
              Enable TfL integration
            </label>
            <label>
              <input type="checkbox" id="enable-ds" {"checked" if CONFIG.enable_datastream else ""} />
              Enable Data Stream integration
            </label>
            <button type="submit">Save Settings</button>
          </form>
          <p>Config file: <code>{CONFIG_PATH}</code></p>
        </fieldset>

        <fieldset>
          <legend>Updates</legend>
          <p>Current version: <code>{__version__}</code></p>
          <button type="button" onclick="checkUpdates()">Check GitHub for latest release</button>
          <pre id="update-output">Not checked yet.</pre>
        </fieldset>

        <script>
          async function postJSON(url, data) {{
            const res = await fetch(url, {{
              method: "POST",
              headers: {{ "Content-Type": "application/json" }},
              body: JSON.stringify(data),
            }});
            return res.json();
          }}

          document.getElementById("settings-form").onsubmit = async (e) => {{
            e.preventDefault();
            const portVal = document.getElementById("port-input").value;
            const port = portVal ? parseInt(portVal, 10) : null;
            const enable_tfl = document.getElementById("enable-tfl").checked;
            const enable_datastream = document.getElementById("enable-ds").checked;
            const data = await postJSON("/settings", {{ port, enable_tfl, enable_datastream }});
            alert(data.message || "Settings saved. Restart app to apply new port.");
            location.reload();
          }};

          async function checkUpdates() {{
            const out = document.getElementById("update-output");
            out.textContent = "Checking GitHub...";
            try {{
              const owner = "BlueElliott";
              const repo = "Singular-Tweaks";
              const url = "https://api.github.com/repos/" + owner + "/" + repo + "/releases/latest";
              const res = await fetch(url);
              if (!res.ok) {{
                out.textContent = "GitHub API error: " + res.status;
                return;
              }}
              const data = await res.json();
              const latest = data.tag_name || data.name || "unknown";
              let msg = "Current version: {__version__}\\nLatest release: " + latest;
              if (latest !== "v{__version__}" && latest !== "{__version__}") {{
                msg += "\\n\\nA newer version may be available.";
              }} else {{
                msg += "\\n\\nYou are up to date.";
              }}
              if (data.html_url) {{
                msg += "\\nRelease page: " + data.html_url;
              }}
              out.textContent = msg;
            }} catch (e) {{
              out.textContent = "Update check failed: " + e;
            }}
          }}
        </script>
      </body>
    </html>
    """


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


# ================== 6. MAIN ==================

if __name__ == "__main__":
    import uvicorn

    port = effective_port()
    logging.getLogger(__name__).info(
        "Starting FastAPI server on http://localhost:%s (binding 0.0.0.0) ...", port
    )
    uvicorn.run(app, host="0.0.0.0", port=port)
