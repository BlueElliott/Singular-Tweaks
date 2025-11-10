from . import __version__
# ...existing code...
import os, time, re, requests
from typing import List, Dict, Any, Optional
from urllib.parse import quote
import logging

from fastapi import FastAPI, Query, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from contextlib import asynccontextmanager
from fastapi.routing import APIRoute

# ================== 1. CONFIG & GLOBALS ==================
# 1.1 Runtime configuration model
# Values can come from environment variables or be set at runtime via HTTP.

SINGULAR_API_BASE = "https://app.singular.live/apiv2"
TFL_URL = (
    "https://api.tfl.gov.uk/Line/Mode/"
    "tube,overground,dlr,elizabeth-line,tram,cable-car/Status"
)


class AppConfig(BaseModel):
    # Singular Control App token (for /model + /control calls)
    singular_token: Optional[str] = os.getenv("SINGULAR_TOKEN") or None
    # Singular Data Stream URL (for TfL payloads)
    singular_stream_url: Optional[str] = os.getenv("SINGULAR_STREAM_URL") or None
    # TfL creds (optional extra)
    tfl_app_id: Optional[str] = os.getenv("TFL_APP_ID") or None
    tfl_app_key: Optional[str] = os.getenv("TFL_APP_KEY") or None


# Mutable global config instance – kept simple on purpose.
CONFIG = AppConfig()

# basic logger
logger = logging.getLogger("singular_tweaks")
if not logger.handlers:
    logging.basicConfig(level=logging.INFO)


# ================== 2. OPENAPI: UNIQUE OPERATION IDS ==================
def generate_unique_id(route: APIRoute) -> str:
    """
    Make operationIds unique in the OpenAPI schema (important for clients),
    even when one function handles multiple HTTP methods.
    """
    methods = sorted(
        [m for m in route.methods if m in {"GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"}]
    )
    method = methods[0].lower() if methods else "get"
    # route.name defaults to function name; combine with path to guarantee uniqueness
    safe_path = re.sub(r"[^a-z0-9]+", "-", route.path.lower()).strip("-")
    return f"{route.name}-{method}-{safe_path}"


# ================== 2.1 APP ==================
app = FastAPI(
    title="TfL + Singular Tweaks (DataStream + ControlApp + Auto-Discovery)",
    generate_unique_id_function=generate_unique_id,
)

# ================== 2.2 UTILITIES ==================
def tfl_params() -> Dict[str, str]:
    """Build query parameters for the TfL API based on current config."""
    p: Dict[str, str] = {}
    if CONFIG.tfl_app_id and CONFIG.tfl_app_key:
        p["app_id"] = CONFIG.tfl_app_id
        p["app_key"] = CONFIG.tfl_app_key
    return p


def fetch_all_line_statuses() -> Dict[str, str]:
    r = requests.get(TFL_URL, params=tfl_params(), timeout=10)
    r.raise_for_status()
    out: Dict[str, str] = {}
    for line in r.json():
        out[line["name"]] = (
            line.get("lineStatuses", [{}])[0].get("statusSeverityDescription", "Unknown")
        )
    return out


def send_to_datastream(payload: Dict[str, Any]):
    """PUT a payload to the configured Singular Data Stream.

    The URL can be set via env (SINGULAR_STREAM_URL) or at runtime via /config/singular.
    """
    if not CONFIG.singular_stream_url:
        raise HTTPException(400, "No Singular data stream URL configured")
    resp = requests.put(
        CONFIG.singular_stream_url,
        json=payload,
        headers={"Content-Type": "application/json"},
        timeout=10,
    )
    # surface HTTP errors clearly
    try:
        resp.raise_for_status()
    except Exception as e:
        logger.exception("Datastream PUT failed")
        # still return details for debugging
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
    """PATCH control items to the configured Singular Control App.

    Body MUST be an array of items with subCompositionId / payload / state.
    """
    if not CONFIG.singular_token:
        raise HTTPException(400, "No Singular control app token configured")
    ctrl_control = f"{SINGULAR_API_BASE}/controlapps/{CONFIG.singular_token}/control"
    resp = requests.patch(
        ctrl_control,
        json=items,
        headers={"Content-Type": "application/json"},
        timeout=10,
    )
    # raise or return error details consistently
    try:
        resp.raise_for_status()
    except Exception:
        logger.exception("Control PATCH failed")
    return resp


def now_ms_float() -> float:
    return float(time.time() * 1000)


def slugify(name: str) -> str:
    s = name.lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s or "item"


# Robust base URL for command generation
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
# id -> key
ID_TO_KEY: Dict[str, str] = {}


def singular_model_fetch() -> Any:
    """Fetch the Singular Control App model using the current token."""
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
        # dive into known child collections
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
        # Keep only subcomposition-like nodes with id, name, and model
        if not sid or name is None or model is None:
            continue
        key = slugify(name)
        orig_key = key
        i = 2
        # ensure slug uniqueness if same name appears multiple times
        while key in REGISTRY and REGISTRY[key]["id"] != sid:
            key = f"{orig_key}-{i}"
            i += 1
        REGISTRY[key] = {
            "id": sid,
            "name": name,
            "fields": {(f.get("id") or ""): f for f in (model or [])},
        }
        ID_TO_KEY[sid] = key


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
    # text / other → raw string
    return value_str


# ================== 3. LIFESPAN (auto-build registry on startup) ==================
@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        build_registry()
    except Exception as e:
        # Don’t crash server if registry build fails; you can /singular/refresh later
        logger.warning(f"[WARN] Registry build failed: {e}")
    yield
    # (optional) add shutdown cleanup here if needed


# Attach lifespan to the app
app.router.lifespan_context = lifespan  # attach lifespan without recreating the app


# ================== 3. CONFIG HTTP API ==================
# 3.1 Pydantic models for incoming config payloads
class SingularConfigIn(BaseModel):
    token: str
    stream_url: Optional[str] = None


class TflConfigIn(BaseModel):
    app_id: str
    app_key: str


# 3.2 Config endpoints
@app.get("/config")
def get_config():
    """Return a redacted view of current runtime configuration."""
    return {
        "singular": {
            "token_set": bool(CONFIG.singular_token),
            "stream_url": CONFIG.singular_stream_url,
        },
        "tfl": {
            "app_id_set": bool(CONFIG.tfl_app_id),
            "app_key_set": bool(CONFIG.tfl_app_key),
        },
    }


@app.post("/config/singular")
def set_singular_config(cfg: SingularConfigIn):
    CONFIG.singular_token = cfg.token
    if cfg.stream_url is not None:
        CONFIG.singular_stream_url = cfg.stream_url
    # try to refresh the registry immediately so /singular/list works
    try:
        build_registry()
    except Exception as e:
        raise HTTPException(400, f"Token saved, but registry build failed: {e}")
    return {
        "ok": True,
        "message": "Singular config updated",
        "subs": len(REGISTRY),
    }


@app.post("/config/tfl")
def set_tfl_config(cfg: TflConfigIn):
    CONFIG.tfl_app_id = cfg.app_id
    CONFIG.tfl_app_key = cfg.app_key
    return {"ok": True, "message": "TfL config updated"}


@app.get("/singular/ping")
def singular_ping():
    """Quick health-check against the configured Singular Control App."""
    try:
        data = singular_model_fetch()

        # Handle both dict- and list-shaped models
        if isinstance(data, dict):
            top_keys = list(data.keys())[:5]
        elif isinstance(data, list):
            if data and isinstance(data[0], dict):
                # show keys from the first element in the list
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
        }
    except Exception as e:
        raise HTTPException(500, f"Singular ping failed: {e}")

# ================== 4. OLD (DATA STREAM) ENDPOINTS ==================
@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/status")
def status_preview():
    try:
        return fetch_all_line_statuses()
    except Exception as e:
        raise HTTPException(500, str(e))


@app.api_route("/update", methods=["GET", "POST"])
def update_status():
    """Fetch TfL + PUT to Data Stream (old behavior)."""
    try:
        data = fetch_all_line_statuses()
        result = send_to_datastream(data)
        return {"sent_to": "datastream", "payload": data, **result}
    except Exception as e:
        raise HTTPException(500, f"Update failed: {e}")


@app.api_route("/test", methods=["GET", "POST"])
def update_test():
    try:
        keys = list(fetch_all_line_statuses().keys())
        payload = {k: "TEST" for k in keys}
        result = send_to_datastream(payload)
        return {"sent_to": "datastream", "payload": payload, **result}
    except Exception as e:
        raise HTTPException(500, f"Test failed: {e}")


@app.api_route("/blank", methods=["GET", "POST"])
def update_blank():
    try:
        keys = list(fetch_all_line_statuses().keys())
        payload = {k: "" for k in keys}
        result = send_to_datastream(payload)
        return {"sent_to": "datastream", "payload": payload, **result}
    except Exception as e:
        raise HTTPException(500, f"Blank failed: {e}")


# ================== 4.1 CONTROL APP: INTROSPECTION ==================
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
    """Show discovered subcompositions + fields (from registry)."""
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


# ================== 4.2 COMMAND CATALOG ENDPOINTS ==================
def _field_examples(base: str, key: str, field_id: str, field_meta: dict):
    ftype = (field_meta.get("type") or "").lower()
    examples: Dict[str, str] = {}

    # SET example (text/number/etc.)
    set_url = f"{base}/{key}/set?field={quote(field_id)}&value=VALUE"
    examples["set_url"] = set_url
    examples["set_curl"] = f'curl -X POST "{set_url}"'

    # TIME CONTROL examples
    if ftype == "timecontrol":
        start = f"{base}/{key}/timecontrol?field={quote(field_id)}&run=true&value=0"
        stop = f"{base}/{key}/timecontrol?field={quote(field_id)}&run=false&value=0"
        examples["timecontrol_start_url"] = start
        examples["timecontrol_stop_url"] = stop
        examples["timecontrol_start_curl"] = f'curl -X POST "{start}"'
        examples["timecontrol_stop_curl"] = f'curl -X POST "{stop}"'
        # common convenience: include a 10s example
        examples["start_10s_if_supported"] = (
            f'{base}/{key}/timecontrol?field={quote(field_id)}&run=true&value=0&seconds=10'
        )
    return examples


@app.get("/singular/commands")
def singular_commands(request: Request):
    """
    Returns a command catalog (URLs + curl) for all discovered subcompositions.
    """
    base = _base_url(request)
    catalog: Dict[str, Any] = {}

    for key, meta in REGISTRY.items():
        sid = meta["id"]
        entry: Dict[str, Any] = {
            "id": sid,
            "name": meta["name"],
            "in_url": f"{base}/{key}/in",
            "out_url": f"{base}/{key}/out",
            "in_curl": f'curl -X POST "{base}/{key}/in"',
            "out_curl": f'curl -X POST "{base}/{key}/out"',
            "fields": {},
        }
        for fid, fmeta in meta["fields"].items():
            if not fid:
                continue
            entry["fields"][fid] = _field_examples(base, key, fid, fmeta)
        catalog[key] = entry

    return {
        "docs": f"{base}/docs",
        "list": f"{base}/singular/list",
        "note": "Control endpoints accept GET and POST for convenience. Prefer POST in automation.",
        "catalog": catalog,
    }


@app.get("/{key}/help")
def singular_commands_for_one(key: str, request: Request):
    """
    Commands for a single subcomposition (by slug or id).
    """
    k = kfind(key)
    base = _base_url(request)
    meta = REGISTRY[k]
    sid = meta["id"]

    entry: Dict[str, Any] = {
        "id": sid,
        "name": meta["name"],
        "in_url": f"{base}/{k}/in",
        "out_url": f"{base}/{k}/out",
        "in_curl": f'curl -X POST "{base}/{k}/in"',
        "out_curl": f'curl -X POST "{base}/{k}/out"',
        "fields": {},
    }
    for fid, fmeta in meta["fields"].items():
        if not fid:
            continue
        entry["fields"][fid] = _field_examples(base, k, fid, fmeta)

    return {
        "docs": f"{base}/docs",
        "list": f"{base}/singular/list",
        "commands": entry,
    }


# ================== 4.3 CONTROL APP: DYNAMIC ROUTES ==================
@app.api_route("/{key}/in", methods=["GET", "POST"])
def sub_in(key: str):
    k = kfind(key)
    sid = REGISTRY[k]["id"]
    r = ctrl_patch([{"subCompositionId": sid, "state": "In"}])
    return {"status": r.status_code, "id": sid, "response": r.text}


@app.api_route("/{key}/out", methods=["GET", "POST"])
def sub_out(key: str):
    k = kfind(key)
    sid = REGISTRY[k]["id"]
    r = ctrl_patch([{"subCompositionId": sid, "state": "Out"}])
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
        # many clock comps expect "Countdown Seconds" as a string
        payload["Countdown Seconds"] = str(seconds)

    payload[field] = {
        "UTC": float(utc if utc is not None else now_ms_float()),
        "isRunning": bool(run),
        "value": int(value),
    }
    r = ctrl_patch([{"subCompositionId": sid, "payload": payload}])
    return {"status": r.status_code, "id": sid, "sent": payload, "response": r.text}


# ================== 5. SIMPLE HTML UI ==================
# A very small, dependency-free page to configure tokens and keys.
# ================== 5. SIMPLE HTML UI ==================
# A very small, dependency-free page to configure tokens and keys.
@app.get("/", response_class=HTMLResponse)
def index():
    return f"""
    <html>
      <head>
        <title>Singular Tweaks v{__version__}</title>
        <style>
          body {{
            font-family: system-ui, -apple-system, BlinkMacSystemFont, sans-serif;
            max-width: 900px;
            margin: 2rem auto;
          }}
          fieldset {{ margin-bottom: 1.5rem; padding: 1rem; }}
          legend {{ font-weight: 600; }}
          label {{ display:block; margin-top:0.5rem; }}
          input {{
            width:100%; padding:0.35rem 0.5rem; box-sizing:border-box;
          }}
          button {{
            margin-top:0.75rem; padding:0.4rem 0.8rem; cursor:pointer;
          }}
          pre {{
            background:#111; color:#0f0; padding:0.5rem;
            white-space:pre-wrap; max-height: 300px; overflow:auto;
          }}
          .version-badge {{
            position: fixed;
            top: 10px;
            right: 10px;
            background: #222;
            color: #fff;
            padding: 4px 10px;
            border-radius: 999px;
            font-size: 12px;
            opacity: 0.8;
          }}
        </style>
      </head>
      <body>
        <div class="version-badge">v{__version__}</div>

        <h1>Singular Tweaks</h1>
        <p>Configure your <strong>Singular Control App</strong>, optional <strong>Data Stream</strong>, and <strong>TfL</strong> API keys.</p>

        <fieldset>
          <legend>Singular Config</legend>
          <form id="singular-form">
            <label>Control App Token
              <input name="token" autocomplete="off" />
            </label>
            <label>Data Stream URL (optional)
              <input name="stream_url" autocomplete="off" />
            </label>
            <button type="submit">Save &amp; Refresh</button>
            <button type="button" onclick="pingSingular()">Ping Singular</button>
          </form>
        </fieldset>

        <fieldset>
          <legend>TfL Config (Optional Extra)</legend>
          <form id="tfl-form">
            <label>App ID
              <input name="app_id" autocomplete="off" />
            </label>
            <label>App Key
              <input name="app_key" autocomplete="off" />
            </label>
            <button type="submit">Save TfL</button>
          </form>
        </fieldset>

        <fieldset>
          <legend>Output</legend>
          <pre id="output">Ready.</pre>
        </fieldset>

        <script>
          async function postJSON(url, data) {{
            const res = await fetch(url, {{
              method: "POST",
              headers: {{ "Content-Type": "application/json" }},
              body: JSON.stringify(data),
            }});
            const text = await res.text();
            document.getElementById("output").innerText = text;
          }}

          document.getElementById("singular-form").onsubmit = (e) => {{
            e.preventDefault();
            const f = e.target;
            postJSON("/config/singular", {{
              token: f.token.value,
              stream_url: f.stream_url.value || null,
            }});
          }};

          document.getElementById("tfl-form").onsubmit = (e) => {{
            e.preventDefault();
            const f = e.target;
            postJSON("/config/tfl", {{
              app_id: f.app_id.value,
              app_key: f.app_key.value,
            }});
          }};

          async function pingSingular() {{
            const res = await fetch("/singular/ping");
            const text = await res.text();
            document.getElementById("output").innerText = text;
          }}
        </script>
      </body>
    </html>
    """


# ================== 5.1 HELP (JSON) ==================
@app.get("/help")
def help_index():
    return {
        "docs": "/docs",
        "datastream_endpoints": {
            "GET /status": "Preview TfL payload (no push).",
            "GET|POST /update": "Fetch TfL + PUT to Data Stream.",
            "GET|POST /test": "PUT 'TEST' payload.",
            "GET|POST /blank": "PUT empty payload.",
        },
        "config_endpoints": {
            "GET /config": "Show whether tokens/keys are set.",
            "POST /config/singular": "Set Singular token + optional Data Stream URL.",
            "POST /config/tfl": "Set TfL APP_ID/APP_KEY.",
            "GET /singular/ping": "Check connection to Singular Control App.",
        },
        "controlapp_endpoints": {
            "GET /singular/list": "Discovered subs + fields (auto).",
            "GET /singular/commands": "Ready-made control URLs for all assets.",
            "GET /{key}/help": "Commands for a single asset (by slug or id).",
            "POST /singular/refresh": "Rebuild registry from Control App model.",
            "GET|POST /{key}/in": "Animate a subcomposition in (key = slug or id).",
            "GET|POST /{key}/out": "Animate a subcomposition out.",
            "GET|POST /{key}/set?field=Top%20Line&value=Hello": "Set any field; add &asString=1 if needed.",
            "GET|POST /{key}/timecontrol?field=Countdown%20Start...": "Start/stop timecontrol; add &seconds=10 if your comp supports it.",
        },
    }


# ================== 6. MAIN ==================
if __name__ == "__main__":
    import uvicorn

    logger.info("Starting FastAPI server on http://0.0.0.0:8000 ...")
    uvicorn.run(app, host="0.0.0.0", port=8000)
# ...existing code...