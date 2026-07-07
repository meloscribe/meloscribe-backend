"""
Meloscribe Backend — FastAPI Server
Bridges the existing Antigravity Music Python tools with the React frontend.
Streams logs via WebSocket and manages workflow orchestration.
"""
import os
import sys
import json
import asyncio
import subprocess
import threading
import sqlite3
import datetime
from pathlib import Path
from typing import Optional

CREATION_FLAGS = 0x08000000 if os.name == 'nt' else 0

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, BackgroundTasks
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import requests
import stripe

# -------------------------------------------------------------------
# Paths
# -------------------------------------------------------------------
TOOLS_DIR = Path(__file__).resolve().parent.parent.parent  # D:\Antigravity Music\tools
SETTINGS_PATH = TOOLS_DIR / "meloscribe" / "backend" / "settings.json"

sys.path.append(str(Path(__file__).resolve().parent))
try:
    from settings import load_settings
    settings = load_settings()
except Exception as e:
    print(f"Error loading settings in main.py: {e}")
    settings = {}

# Startup database backup check
try:
    backup_marker = Path(__file__).resolve().parent / ".db_backup_done"
    if not backup_marker.exists():
        db_file = Path(__file__).resolve().parent / "analytics.db"
        if db_file.exists():
            import shutil
            import datetime
            backup_dir = Path(__file__).resolve().parent / "backups"
            backup_dir.mkdir(exist_ok=True)
            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_file = backup_dir / f"analytics_backup_{timestamp}.db"
            shutil.copy2(db_file, backup_file)
            print(f"[Backup] Automatically backed up database to {backup_file}")
            with open(backup_marker, "w", encoding="utf-8") as f:
                f.write(f"backup done at {timestamp}")
except Exception as backup_err:
    print(f"[Backup] Failed to create auto-backup on update: {backup_err}")

app = FastAPI(title="Meloscribe Backend", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Rate limiting database-backed helper for multi-worker shared state
def is_rate_limited(ip: str, endpoint: str, max_requests: int, window_seconds: int) -> bool:
    import time
    import sqlite3
    now = time.time()
    cutoff = now - window_seconds
    db_path = Path(__file__).resolve().parent / "analytics.db"
    try:
        conn = sqlite3.connect(str(db_path), timeout=30.0)
        c = conn.cursor()
        
        # 1. Clean up old rate limits
        c.execute("DELETE FROM rate_limits WHERE timestamp < ?", (cutoff,))
        
        # 2. Count requests from this IP in the window
        c.execute("SELECT COUNT(*) FROM rate_limits WHERE ip = ? AND endpoint = ?", (ip, endpoint))
        count = c.fetchone()[0]
        
        if count >= max_requests:
            conn.close()
            return True
            
        # 3. Record this request
        c.execute("INSERT INTO rate_limits (ip, endpoint, timestamp) VALUES (?, ?, ?)", (ip, endpoint, now))
        conn.commit()
        conn.close()
        return False
    except Exception as e:
        print(f"[Rate Limit] Database error: {e}")
        return False

# Publicly allowed paths (anyone can request)
FORBIDDEN_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>403 — Access Restricted | Meloscribe</title>
  <link rel="preconnect" href="https://fonts.googleapis.com" />
  <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;600;700;800&display=swap" rel="stylesheet" />
  <style>
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: 'Outfit', sans-serif;
      background: #07070e;
      min-height: 100vh;
      display: flex;
      align-items: center;
      justify-content: center;
      overflow: hidden;
      color: #fff;
    }
    .orb {
      position: fixed;
      border-radius: 50%;
      filter: blur(120px);
      pointer-events: none;
      z-index: 0;
    }
    .orb-cyan  { width: 600px; height: 600px; background: rgba(0,245,255,0.12); top: -100px; left: -150px; }
    .orb-pink  { width: 500px; height: 500px; background: rgba(255,45,146,0.12); bottom: -80px; right: -120px; }
    .card {
      position: relative;
      z-index: 1;
      background: rgba(255,255,255,0.04);
      border: 1px solid rgba(0,245,255,0.18);
      border-radius: 24px;
      padding: 56px 64px;
      max-width: 480px;
      width: 90%;
      text-align: center;
      backdrop-filter: blur(24px) saturate(180%);
      box-shadow: 0 0 80px rgba(0,245,255,0.06), 0 32px 80px rgba(0,0,0,0.6);
      animation: fadeUp 0.7s cubic-bezier(0.16,1,0.3,1) both;
    }
    @keyframes fadeUp {
      from { opacity: 0; transform: translateY(28px); }
      to   { opacity: 1; transform: translateY(0); }
    }
    .icon-wrap {
      width: 72px; height: 72px;
      margin: 0 auto 28px;
      background: rgba(0,245,255,0.08);
      border: 1px solid rgba(0,245,255,0.22);
      border-radius: 20px;
      display: flex; align-items: center; justify-content: center;
      box-shadow: 0 0 32px rgba(0,245,255,0.15);
    }
    .icon-wrap svg { width: 36px; height: 36px; }
    .code {
      font-size: 80px;
      font-weight: 800;
      line-height: 1;
      background: linear-gradient(135deg, #00f5ff 0%, #ff2d92 100%);
      -webkit-background-clip: text;
      -webkit-text-fill-color: transparent;
      background-clip: text;
      margin-bottom: 12px;
      letter-spacing: -2px;
    }
    .title {
      font-size: 22px;
      font-weight: 600;
      color: #f1f5f9;
      margin-bottom: 12px;
    }
    .desc {
      font-size: 14px;
      color: rgba(255,255,255,0.45);
      line-height: 1.7;
      margin-bottom: 36px;
    }
    .badge {
      display: inline-flex;
      align-items: center;
      gap: 7px;
      background: rgba(255,45,146,0.1);
      border: 1px solid rgba(255,45,146,0.25);
      border-radius: 999px;
      padding: 6px 16px;
      font-size: 12px;
      color: #ff2d92;
      font-weight: 500;
      letter-spacing: 0.04em;
    }
    .footer {
      margin-top: 40px;
      font-size: 11px;
      color: rgba(255,255,255,0.18);
      letter-spacing: 0.06em;
      text-transform: uppercase;
    }
    .piano-keys { display: flex; gap: 3px; justify-content: center; margin-top: 32px; }
    .key-w {
      width: 18px; height: 52px;
      background: rgba(255,255,255,0.07);
      border: 1px solid rgba(0,245,255,0.12);
      border-radius: 0 0 5px 5px;
    }
    .key-b {
      width: 12px; height: 34px;
      background: rgba(0,245,255,0.18);
      border: 1px solid rgba(0,245,255,0.3);
      border-radius: 0 0 4px 4px;
      margin: 0 -6px;
      z-index: 1;
      box-shadow: 0 0 10px rgba(0,245,255,0.2);
    }
  </style>
</head>
<body>
  <div class="orb orb-cyan"></div>
  <div class="orb orb-pink"></div>
  <div class="card">
    <div class="icon-wrap">
      <svg viewBox="0 0 24 24" fill="none" stroke="#00f5ff" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
        <rect x="3" y="11" width="18" height="11" rx="2"/>
        <path d="M7 11V7a5 5 0 0 1 10 0v4"/>
      </svg>
    </div>
    <div class="code">403</div>
    <div class="title">Access Restricted</div>
    <p class="desc">This endpoint is protected and requires a valid<br/><code style="color:#00f5ff;font-size:12px;">X-Meloscribe-Key</code> header. Public API routes remain accessible.</p>
    <div class="badge">
      <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
        <circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/>
      </svg>
      Unauthorized Request
    </div>
    <div class="piano-keys">
      <div class="key-w"></div><div class="key-b"></div>
      <div class="key-w"></div><div class="key-b"></div>
      <div class="key-w"></div>
      <div class="key-w"></div><div class="key-b"></div>
      <div class="key-w"></div><div class="key-b"></div>
      <div class="key-w"></div><div class="key-b"></div>
      <div class="key-w"></div>
    </div>
    <div class="footer">api.meloscribe.dev</div>
  </div>
</body>
</html>
"""

PUBLIC_ROUTES = [
    ("/api/public/songs", "GET"),
    ("/api/public/stats", "GET"),
    ("/api/public/suggestions", "GET"),
    ("/api/public/suggestions", "POST"),  # Submit suggestions
    ("/api/public/download", "GET"),       # Public direct free downloads
    ("/api/order/hash-by-checkout", "GET"),
    ("/api/order/details", "GET"),
    ("/api/download/request", "GET"),
    ("/api/download/verify", "GET"),
    ("/api/notify/subscribe", "POST"),
    ("/api/notify/confirm", "GET"),
    ("/api/notify/unsubscribe", "GET"),
    ("/callback", "GET"),
    ("/api/webhooks/stripe", "POST"),
    ("/api/checkout/create-session", "POST"),
]

@app.middleware("http")
async def security_middleware(request: Request, call_next):
    path = request.url.path
    method = request.method
    
    # Always allow CORS preflight OPTIONS requests
    if method == "OPTIONS":
        return await call_next(request)
        
    # Bypass for static preview files & video/audio streams
    if path.startswith("/public") or path.startswith("/api/public/video-stream") or path.startswith("/api/public/audio-stream"):
        return await call_next(request)
        
    is_public = False
    for p_route, p_method in PUBLIC_ROUTES:
        if path == p_route and method == p_method:
            is_public = True
            break
            
    # Support suggestions vote/unvote paths variables
    if path.startswith("/api/public/suggestions/") and (path.endswith("/vote") or path.endswith("/unvote")):
        if method == "POST":
            is_public = True
            
    if is_public:
        # Rate limit suggestions creation and voting
        client_ip = request.headers.get("x-real-ip") or request.client.host
        if path == "/api/public/suggestions" and method == "POST":
            if is_rate_limited(client_ip, "suggestion", max_requests=5, window_seconds=3600):
                return JSONResponse(status_code=429, content={"error": "Rate limit exceeded. Max 5 suggestions per hour."})
        elif path.startswith("/api/public/suggestions/") and (path.endswith("/vote") or path.endswith("/unvote")):
            if is_rate_limited(client_ip, "vote", max_requests=20, window_seconds=600):
                return JSONResponse(status_code=429, content={"error": "Rate limit exceeded. Max 20 votes per 10 minutes."})
                
        return await call_next(request)
        
    # Check if request is local (host localhost/127.0.0.1 and no proxy headers)
    host_header = request.headers.get("host", "")
    is_local = False
    if "localhost" in host_header or "127.0.0.1" in host_header:
        if not request.headers.get("x-real-ip") and not request.headers.get("x-forwarded-for"):
            is_local = True
            
    if is_local:
        return await call_next(request)
        
    # Remote request: validate X-Meloscribe-Key
    client_key = request.headers.get("x-meloscribe-key")
    server_key = get_server_api_key()
    
    if not server_key or client_key != server_key:
        accept = request.headers.get("accept", "")
        if "application/json" in accept and "text/html" not in accept:
            return JSONResponse(status_code=403, content={"error": "Access Denied: Invalid or missing API key."})
        return HTMLResponse(content=FORBIDDEN_HTML, status_code=403)
        
    return await call_next(request)

# -------------------------------------------------------------------
# Local Windows Proxy logic (redirects to the VM server database)
# -------------------------------------------------------------------
import platform
if platform.system() == "Windows":
    import requests
    VM_API_BASE = "https://api.meloscribe.dev"

    def get_proxy_headers():
        headers = {}
        api_key = get_server_api_key()
        if api_key:
            headers["X-Meloscribe-Key"] = api_key
        return headers

    @app.get("/api/analytics")
    def get_local_analytics(range: str = "30d"):
        try:
            r = requests.get(f"{VM_API_BASE}/api/analytics?range={range}", headers=get_proxy_headers(), timeout=5.0)
            return JSONResponse(content=r.json(), status_code=r.status_code)
        except Exception as e:
            return JSONResponse(content={"error": f"Proxy error: {e}"}, status_code=500)

    @app.get("/api/logs")
    def get_local_logs():
        remote_logs = []
        try:
            r = requests.get(f"{VM_API_BASE}/api/logs", headers=get_proxy_headers(), timeout=3.0)
            if r.status_code == 200:
                remote_logs = r.json()
        except Exception as e:
            print(f"[Proxy] Failed to fetch remote logs: {e}")
            
        # Enrich local logs to differentiate them
        local_logs = []
        for entry in SYSTEM_LOGS:
            local_logs.append({
                "time": entry.get("time", ""),
                "msg": f"[LOCAL] {entry.get('msg', '')}"
            })
            
        combined = local_logs + remote_logs
        return JSONResponse(content=combined)

    @app.post("/api/logs/clear")
    def clear_local_logs():
        try:
            r = requests.post(f"{VM_API_BASE}/api/logs/clear", headers=get_proxy_headers(), timeout=5.0)
            return JSONResponse(content=r.json(), status_code=r.status_code)
        except Exception as e:
            return JSONResponse(content={"error": f"Proxy error: {e}"}, status_code=500)

    @app.get("/api/notify/subscribers")
    def get_local_subscribers():
        try:
            r = requests.get(f"{VM_API_BASE}/api/notify/subscribers", headers=get_proxy_headers(), timeout=5.0)
            return JSONResponse(content=r.json(), status_code=r.status_code)
        except Exception as e:
            return JSONResponse(content={"error": f"Proxy error: {e}"}, status_code=500)

    @app.get("/api/public/suggestions")
    def get_local_suggestions():
        try:
            r = requests.get(f"{VM_API_BASE}/api/public/suggestions", headers=get_proxy_headers(), timeout=5.0)
            return JSONResponse(content=r.json(), status_code=r.status_code)
        except Exception as e:
            return JSONResponse(content={"error": f"Proxy error: {e}"}, status_code=500)

    @app.post("/api/public/suggestions")
    def create_local_suggestion(sug: dict):
        try:
            r = requests.post(f"{VM_API_BASE}/api/public/suggestions", json=sug, headers=get_proxy_headers(), timeout=5.0)
            return JSONResponse(content=r.json(), status_code=r.status_code)
        except Exception as e:
            return JSONResponse(content={"error": f"Proxy error: {e}"}, status_code=500)

    @app.post("/api/public/suggestions/{sug_id}/vote")
    def vote_local_suggestion(sug_id: str):
        try:
            r = requests.post(f"{VM_API_BASE}/api/public/suggestions/{sug_id}/vote", headers=get_proxy_headers(), timeout=5.0)
            return JSONResponse(content=r.json(), status_code=r.status_code)
        except Exception as e:
            return JSONResponse(content={"error": f"Proxy error: {e}"}, status_code=500)

    @app.post("/api/public/suggestions/{sug_id}/unvote")
    def unvote_local_suggestion(sug_id: str):
        try:
            r = requests.post(f"{VM_API_BASE}/api/public/suggestions/{sug_id}/unvote", headers=get_proxy_headers(), timeout=5.0)
            return JSONResponse(content=r.json(), status_code=r.status_code)
        except Exception as e:
            return JSONResponse(content={"error": f"Proxy error: {e}"}, status_code=500)

    @app.get("/api/public/video-stream")
    def local_video_stream(song_name: str, request: Request):
        try:
            import requests
            from fastapi.responses import StreamingResponse
            req_headers = get_proxy_headers()
            range_header = request.headers.get("range")
            if range_header:
                req_headers["range"] = range_header
            r = requests.get(f"{VM_API_BASE}/api/public/video-stream?song_name={song_name}", headers=req_headers, stream=True, timeout=15)
            def chunk_generator():
                try:
                    for chunk in r.iter_content(chunk_size=65536):
                        if chunk:
                            yield chunk
                finally:
                    r.close()
            resp_headers = {}
            for h in ("content-type", "content-length", "content-range", "accept-ranges", "etag"):
                if h in r.headers:
                    resp_headers[h] = r.headers[h]
            return StreamingResponse(chunk_generator(), status_code=r.status_code, headers=resp_headers)
        except Exception as e:
            return JSONResponse(content={"error": f"Proxy error: {e}"}, status_code=500)

    @app.delete("/api/public/suggestions/{sug_id}")
    def delete_local_suggestion(sug_id: str):
        try:
            r = requests.delete(f"{VM_API_BASE}/api/public/suggestions/{sug_id}", headers=get_proxy_headers(), timeout=5.0)
            return JSONResponse(content=r.json(), status_code=r.status_code)
        except Exception as e:
            return JSONResponse(content={"error": f"Proxy error: {e}"}, status_code=500)

    @app.get("/api/stripe/sales")
    def get_local_stripe_sales():
        try:
            r = requests.get(f"{VM_API_BASE}/api/stripe/sales", headers=get_proxy_headers(), timeout=5.0)
            return JSONResponse(content=r.json(), status_code=r.status_code)
        except Exception as e:
            return JSONResponse(content={"error": f"Proxy error: {e}"}, status_code=500)

    @app.api_route("/api/admin/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
    async def proxy_admin_routes(path: str, request: Request):
        try:
            from fastapi import Response
            method = request.method
            headers = get_proxy_headers()
            
            # Forward admin passcode from client
            if "x-admin-passcode" in request.headers:
                headers["x-admin-passcode"] = request.headers["x-admin-passcode"]
                
            # Forward content-type if present
            if "content-type" in request.headers:
                headers["content-type"] = request.headers["content-type"]

            url = f"{VM_API_BASE}/api/admin/{path}"
            
            # Handle query parameters
            params = dict(request.query_params)
            
            # Handle body
            body = await request.body()
            
            r = requests.request(
                method=method,
                url=url,
                headers=headers,
                params=params,
                data=body,
                timeout=10.0
            )
            
            # Return json if possible, otherwise raw content
            try:
                content = r.json()
                return JSONResponse(content=content, status_code=r.status_code)
            except Exception:
                return Response(content=r.content, status_code=r.status_code, media_type=r.headers.get("content-type"))
        except Exception as e:
            return JSONResponse(content={"error": f"Proxy error: {e}"}, status_code=500)

from fastapi.staticfiles import StaticFiles
public_dir = r"c:\Dev\meloscribe-frontend\website\public"
if os.path.exists(public_dir):
    app.mount("/public", StaticFiles(directory=public_dir), name="public")
    print(f"[FastAPI] Mounted {public_dir} under /public")

import collections
from datetime import datetime

import secrets

def initialize_server_api_key():
    try:
        settings_path = Path(__file__).resolve().parent / "settings.json"
        s = {}
        if settings_path.exists():
            with open(settings_path, "r", encoding="utf-8") as f:
                s = json.load(f)
        if not s.get("server_api_key"):
            s["server_api_key"] = secrets.token_hex(16)
            with open(settings_path, "w", encoding="utf-8") as f:
                json.dump(s, f, indent=4)
            print(f"[Security] Generated new server_api_key: {s['server_api_key']}")
        else:
            print(f"[Security] Loaded server_api_key")
    except Exception as e:
        print(f"[Security] Failed to initialize server_api_key: {e}")

def get_server_api_key():
    try:
        settings_path = Path(__file__).resolve().parent / "settings.json"
        if settings_path.exists():
            with open(settings_path, "r", encoding="utf-8") as f:
                s = json.load(f)
                return s.get("server_api_key")
    except Exception:
        pass
    return None

_DB_PATH = Path(__file__).resolve().parent / "analytics.db"

# Ring buffer for system logs
SYSTEM_LOGS = collections.deque(maxlen=100)

def log_error(msg: str):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    SYSTEM_LOGS.appendleft({"time": timestamp, "msg": msg})
    print(f"[SYSTEM LOG] {msg}")

def periodic_background_sync():
    import time
    import sqlite3
    import importlib.util
    # Wait 60 seconds after startup to settle
    time.sleep(60)
    while True:
        log_error("[Background Sync] Periodic background sync cycle starting...")
        
        # 1. YouTube Sync
        try:
            sync_path = str(TOOLS_DIR / "meloscribe" / "backend" / "yt_sync.py")
            spec = importlib.util.spec_from_file_location("yt_sync", sync_path)
            mod  = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            mod.sync_youtube()
            log_error("[Background Sync] YouTube metrics sync: SUCCESS")
        except Exception as e:
            log_error(f"[Background Sync] YouTube sync error: {e}")
            
        # 2. Instagram Sync
        try:
            sync_path = str(TOOLS_DIR / "meloscribe" / "backend" / "ig_sync.py")
            spec = importlib.util.spec_from_file_location("ig_sync", sync_path)
            mod  = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            mod.sync_instagram()
            log_error("[Background Sync] Instagram metrics sync: SUCCESS")
        except Exception as e:
            log_error(f"[Background Sync] Instagram sync error: {e}")

        # 3. TikTok Sync
        try:
            sync_path = str(TOOLS_DIR / "meloscribe" / "backend" / "tiktok_sync.py")
            spec = importlib.util.spec_from_file_location("tiktok_sync", sync_path)
            mod  = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            mod.sync_tiktok()
            log_error("[Background Sync] TikTok metrics sync: SUCCESS")
        except Exception as e:
            log_error(f"[Background Sync] TikTok sync error: {e}")

        # 4. Demographics Sync
        try:
            sync_path = str(TOOLS_DIR / "meloscribe" / "backend" / "demographics_sync.py")
            spec = importlib.util.spec_from_file_location("demographics_sync", sync_path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            mod.sync_all_demographics()
            log_error("[Background Sync] Demographics sync: SUCCESS")
        except Exception as e:
            log_error(f"[Background Sync] Demographics sync error: {e}")

        # 5. Competitor Sync
        try:
            import asyncio
            try:
                asyncio.run(sync_competitors())
                log_error("[Background Sync] Competitor channels sync: SUCCESS")
            except RuntimeError:
                coro = sync_competitors()
                asyncio.run_coroutine_threadsafe(coro, asyncio.get_event_loop())
                log_error("[Background Sync] Competitor channels sync: SUCCESS (threadsafe)")
        except Exception as e:
            log_error(f"[Background Sync] Competitor sync error: {e}")

        # 6. Action Triggers
        try:
            import sync_utils
            conn = sqlite3.connect(_DB_PATH)
            cursor = conn.cursor()
            count = sync_utils.evaluate_action_triggers(cursor)
            conn.commit()
            conn.close()
            log_error(f"[Background Sync] Evaluated action triggers. Created {count} to-dos.")
        except Exception as e:
            log_error(f"[Background Sync] Action triggers evaluation error: {e}")

        log_error("[Background Sync] Periodic background sync cycle completed. Sleeping for 15 minutes...")
        time.sleep(900)

_sync_errors = []  # Collect errors from startup syncs (deferred until log_error is available)

@app.on_event("startup")
def startup_event():
    # --- Initialize Server API Key ---
    initialize_server_api_key()
    
    # --- Database Initialization / Migration ---
    try:
        from db_setup import init_db
        init_db()
        print("[Startup] Database initialized/migrated.")
    except Exception as e:
        print(f"[Startup] Database initialization failed: {e}")

    # --- Reset stuck processing items in batch queue ---
    try:
        import sqlite3
        conn = sqlite3.connect(str(_DB_PATH))
        c = conn.cursor()
        c.execute("UPDATE batch_ingest_queue SET status = 'initialized' WHERE status = 'processing'")
        conn.commit()
        conn.close()
        print("[Startup] Reset stuck processing items in batch queue to initialized.")
    except Exception as db_err:
        print(f"[Startup] Failed to reset stuck batch items: {db_err}")

    # --- Desktop Shortcut Auto-Creation (DISABLED) ---
    # Shortcut already exists. No need to recreate on every launch.

    # --- ngrok Auto-Start (background) ---
    def run_ngrok():
        import shutil
        ngrok_domain = "wooing-encrust-ladle.ngrok-free.dev"
        # Look for ngrok binary: first in tools/ngrok/, then on PATH
        ngrok_bin = str(TOOLS_DIR / "ngrok" / "ngrok.exe")
        if not Path(ngrok_bin).exists():
            ngrok_bin = shutil.which("ngrok")
        if not ngrok_bin:
            print("[ngrok] WARNING: ngrok binary not found. Webhook will not be reachable.")
            return
        try:
            subprocess.Popen(
                [ngrok_bin, "http", f"--domain={ngrok_domain}", "127.0.0.1:8787"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=CREATION_FLAGS
            )
            print(f"[ngrok] Started tunnel -> https://{ngrok_domain}")
        except Exception as e:
            print(f"[ngrok] Failed to start: {e}")
    threading.Thread(target=run_ngrok, daemon=True).start()

    # --- TikTok Sync (background, auto-refreshes token) ---
    def run_tiktok_sync():
        try:
            import importlib.util, sys as _sys
            sync_path = str(TOOLS_DIR / "meloscribe" / "backend" / "tiktok_sync.py")
            spec = importlib.util.spec_from_file_location("tiktok_sync", sync_path)
            mod  = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            mod.sync_tiktok()
        except Exception as e:
            print(f"[TikTok Sync] Failed on startup: {e}")
            _sync_errors.append(("TikTok Sync", str(e)))
    threading.Thread(target=run_tiktok_sync, daemon=True).start()

    # --- Instagram Sync (background) ---
    def run_instagram_sync():
        try:
            import importlib.util, sys as _sys
            sync_path = str(TOOLS_DIR / "meloscribe" / "backend" / "ig_sync.py")
            spec = importlib.util.spec_from_file_location("ig_sync", sync_path)
            mod  = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            mod.sync_instagram()
        except Exception as e:
            print(f"[Instagram Sync] Failed on startup: {e}")
            _sync_errors.append(("Instagram Sync", str(e)))
    threading.Thread(target=run_instagram_sync, daemon=True).start()

    # --- YouTube Sync (background) ---
    def run_youtube_sync():
        try:
            import importlib.util, sys as _sys
            sync_path = str(TOOLS_DIR / "meloscribe" / "backend" / "yt_sync.py")
            spec = importlib.util.spec_from_file_location("yt_sync", sync_path)
            mod  = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            mod.sync_youtube()
        except Exception as e:
            print(f"[YouTube Sync] Failed on startup: {e}")
            _sync_errors.append(("YouTube Sync", str(e)))
    threading.Thread(target=run_youtube_sync, daemon=True).start()

    # --- Demographics Sync (background) ---
    def run_demographics_sync():
        try:
            import importlib.util
            sync_path = str(TOOLS_DIR / "meloscribe" / "backend" / "demographics_sync.py")
            spec = importlib.util.spec_from_file_location("demographics_sync", sync_path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            mod.sync_all_demographics()
        except Exception as e:
            print(f"[Demographics Sync] Failed on startup: {e}")
            _sync_errors.append(("Demographics", str(e)))
    threading.Thread(target=run_demographics_sync, daemon=True).start()


    # --- Threads Token Refresh (background, proactively renews 60-day long-lived token) ---
    def run_threads_refresh():
        try:
            tokens_path = TOOLS_DIR / "meloscribe" / "backend" / "threads_tokens.json"
            if not tokens_path.exists():
                print("[Threads] No tokens found. Add threads_tokens.json to enable.")
                return
            import importlib.util
            poster_path = str(TOOLS_DIR / "meloscribe" / "backend" / "threads_poster.py")
            spec = importlib.util.spec_from_file_location("threads_poster", poster_path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            mod.refresh_token()
        except Exception as e:
            print(f"[Threads] Token refresh failed on startup: {e}")
            _sync_errors.append(("Threads Refresh", str(e)))
    threading.Thread(target=run_threads_refresh, daemon=True).start()

    # --- Auto Credentials Sync to VM (background) ---
    def run_creds_sync():
        import time
        time.sleep(5)  # Wait for uvicorn server startup to settle
        print("[Startup] Auto-syncing credentials to VM...")
        try:
            sync_credentials_route()
        except Exception as err:
            print(f"[Startup] Auto-sync credentials failed: {err}")
    threading.Thread(target=run_creds_sync, daemon=True).start()

    # --- Start 15-minute Periodic Background Sync ---
    threading.Thread(target=periodic_background_sync, daemon=True).start()

# -------------------------------------------------------------------
# Error Log System (in-memory ring buffer for UI display)
# -------------------------------------------------------------------
import collections
_error_log = collections.deque(maxlen=100)

def log_error(source: str, message: str = None, level: str = "error"):
    """Log an API or system error."""
    if message is None:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        SYSTEM_LOGS.appendleft({"time": timestamp, "msg": source})
        print(f"[SYSTEM] {source}")
        return
        
    entry = {
        "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "source": source,
        "message": str(message)[:500],
        "level": level
    }
    _error_log.appendleft(entry)
    print(f"[LOG/{level.upper()}] [{source}] {message}")

@app.get("/api/logs")
async def get_error_logs():
    # Flush any deferred startup errors into the log
    while _sync_errors:
        src, msg = _sync_errors.pop(0)
        log_error(src, msg)
    return JSONResponse(content=list(_error_log))





# WebSocket Manager
# -------------------------------------------------------------------
class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: dict):
        dead = []
        for conn in self.active_connections:
            try:
                await conn.send_json(message)
            except Exception:
                dead.append(conn)
        for d in dead:
            self.disconnect(d)

manager = ConnectionManager()

# -------------------------------------------------------------------
# State
# -------------------------------------------------------------------
current_process: Optional[subprocess.Popen] = None
stop_requested = False
process_lock = threading.Lock()
captured_youtube_urls: dict[str, str] = {}

# -------------------------------------------------------------------
# Settings
# -------------------------------------------------------------------
# Obsolete settings model and endpoints removed. Dynamic handlers defined below.

# -------------------------------------------------------------------
# Process Runner (streams stdout → WebSocket)
# -------------------------------------------------------------------
async def run_tool(cmd: list[str], label: str = ""):
    global current_process, stop_requested
    stop_requested = False

    loop = asyncio.get_event_loop()

    def _run():
        global current_process
        with process_lock:
            current_process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                cwd=str(TOOLS_DIR),
                creationflags=CREATION_FLAGS
            )

        # Extract song name from cmd arguments if present
        song_arg = None
        if "--song" in cmd:
            try:
                idx = cmd.index("--song")
                if idx + 1 < len(cmd):
                    song_arg = cmd[idx + 1]
            except Exception:
                pass

        for line in iter(current_process.stdout.readline, ""):
            if stop_requested:
                break
            # Broadcast log line
            asyncio.run_coroutine_threadsafe(
                manager.broadcast({"type": "log", "message": line.rstrip()}),
                loop,
            )
            # Parse progress
            if line.startswith("PROGRESS:") or line.startswith("VIS_PROGRESS:"):
                try:
                    pct = int(line.split(":")[1].replace("%", "").strip().split("(")[0])
                    asyncio.run_coroutine_threadsafe(
                        manager.broadcast({"type": "progress", "value": pct / 100}),
                        loop,
                    )
                except Exception:
                    pass
            elif "[R2 Upload] Progress:" in line:
                try:
                    pct_str = line.split("Progress:")[1].split("%")[0].strip()
                    pct = float(pct_str)
                    asyncio.run_coroutine_threadsafe(
                        manager.broadcast({"type": "progress", "value": pct / 100.0}),
                        loop,
                    )
                except Exception:
                    pass
            
            # Capture YouTube URL for Ko-Fi
            if "SUCCESS! Video uploaded at https://youtu.be/" in line:
                yt_url = line.split("at ")[-1].strip()
                if song_arg:
                    global captured_youtube_urls
                    captured_youtube_urls[song_arg] = yt_url

        current_process.wait()
        rc = current_process.returncode
        current_process = None
        return rc

    rc = await loop.run_in_executor(None, _run)
    return rc

# -------------------------------------------------------------------
# Workflow Endpoints
# -------------------------------------------------------------------
class WorkflowRequest(BaseModel):
    song: str = ""
    author: str = ""
    theme: str = "warm"
    price: str = "3.00"
    format: str = "viral_part"
    shutdown: bool = False
    doR2: bool = True
    doKofi: bool = True
    doYoutube: bool = True
    doInstagram: bool = True
    doFacebook: bool = True
    doTiktok: bool = True
    doThreads: bool = True
    localUpload: bool = False
    zoom: float = 1.5
    shift: int = 0
    enableVisualizer: bool = True
    enableMetronome: bool = True
    enablePortraitAddon: bool = True
    timesig: str = "auto"
    scheduleDate: str = ""
    scheduleTime: str = "16:00"
    phase: int = 1
    resumeFromStep: int = 0  # 0 = start from beginning
    paddle_product_id: str = ""

@app.post("/api/workflow/start")
async def start_workflow(req: WorkflowRequest):
    """Start the full automation workflow in background."""
    asyncio.create_task(_run_workflow(req))
    return {"status": "started"}

async def _run_workflow(req: WorkflowRequest):
    global stop_requested
    python = sys.executable
    song = req.song
    author = req.author

    await manager.broadcast({"type": "status", "message": f"Starting workflow for '{song}'..."})
    await manager.broadcast({"type": "progress", "value": 0})

    # Ordner-Absicherung: Erstelle Covers, TikToks, Packages falls nicht da
    for folder_key in ["tiktok_dir", "covers_dir", "packages_dir"]:
        f_val = settings.get(folder_key)
        if f_val:
            try:
                os.makedirs(f_val, exist_ok=True)
            except Exception:
                pass

    cakewalk_dir = settings.get("cakewalk_dir", r"C:\Cakewalk Projects")
    keysight_dir = settings.get("keysight_dir", r"C:\Dev\meloscribe\Keysight export")
    
    # Case-insensitive check for Easy directory
    has_easy = False
    easy_folder_name = f"{song} Easy"
    is_easy_enabled = any(settings.get(f"{p}_upload_easy", True) for p in ["yt", "ig", "fb", "tt", "threads"])
    
    if is_easy_enabled and os.path.exists(cakewalk_dir):
        try:
            for item in os.listdir(cakewalk_dir):
                if item.lower() == f"{song} easy".lower():
                    easy_folder_name = item
                    has_easy = True
                    break
        except Exception:
            pass
            
    easy_dir = Path(cakewalk_dir) / easy_folder_name

    steps = []
    if req.phase == 1:
        # Original Render
        steps.append((
            "Keysight Render (Original)", 
            [python, "-u", str(TOOLS_DIR / "keysight_bot.py"), "--song", song, "--theme", req.theme]
        ))
        
        # Handbrake In-Place Compression (Original)
        normal_vid = Path(keysight_dir) / f"{song}.mp4"
        steps.append((
            "Handbrake Compression (Original Normal)", 
            [python, "-u", str(TOOLS_DIR / "handbrake_bot.py"), "--input", str(normal_vid)]
        ))
        
        slow_vid = Path(keysight_dir) / f"{song} slow.mp4"
        steps.append((
            "Handbrake Compression (Original Slow)", 
            [python, "-u", str(TOOLS_DIR / "handbrake_bot.py"), "--input", str(slow_vid)]
        ))

        # Easy Render (if folder exists)
        if has_easy:
            steps.append((
                "Keysight Render (Easy)", 
                [python, "-u", str(TOOLS_DIR / "keysight_bot.py"), "--song", f"{song} Easy", "--theme", req.theme]
            ))
            
            easy_normal_vid = Path(keysight_dir) / f"{song} Easy.mp4"
            steps.append((
                "Handbrake Compression (Easy Normal)", 
                [python, "-u", str(TOOLS_DIR / "handbrake_bot.py"), "--input", str(easy_normal_vid)]
            ))
            
            easy_slow_vid = Path(keysight_dir) / f"{song} Easy slow.mp4"
            steps.append((
                "Handbrake Compression (Easy Slow)", 
                [python, "-u", str(TOOLS_DIR / "handbrake_bot.py"), "--input", str(easy_slow_vid)]
            ))
    else:
        # Phase 2
        zoom_val = f"{req.zoom:.2f}"
        shift_val = str(int(req.shift))

        versions = [("", song)]
        if has_easy:
            versions.append((" Easy", easy_folder_name))

        for suffix, folder_name in versions:
            v_song = f"{song}{suffix}"
            
            for vtype, prefix in [("normal", ""), ("tutorial", " slow")]:
                vid_in = str(TOOLS_DIR.parent / "Keysight export" / f"{v_song}{prefix}.mp4")
                midi_path = f"C:\\Cakewalk Projects\\{folder_name}\\{v_song}{prefix}.mid"
                
                # Generate Portrait version
                cmd_portrait = [
                    python, "-u", str(TOOLS_DIR / "video_generator.py"),
                    "--video", vid_in, "--title", v_song, "--author", author,
                    "--type", vtype, "--zoom", zoom_val, "--shift", shift_val,
                    "--midipath", midi_path, "--theme", req.theme,
                ]
                if req.enableVisualizer: cmd_portrait.append("--visualizer")
                if req.enableMetronome: cmd_portrait.append("--metronome")
                if req.enablePortraitAddon: cmd_portrait.append("--use_portrait_addon")
                if req.timesig: cmd_portrait.extend(["--timesig", req.timesig])
                steps.append((f"Portrait Video ({v_song} {vtype})", cmd_portrait))

                # Generate Widescreen version if not condensed (widescreen publishing target active)
                if req.format == "full_arrangement":
                    cmd_widescreen = [
                        python, "-u", str(TOOLS_DIR / "video_generator.py"),
                        "--video", vid_in, "--title", v_song, "--author", author,
                        "--type", vtype, "--zoom", zoom_val, "--shift", shift_val,
                        "--midipath", midi_path, "--theme", req.theme,
                        "--wide"
                    ]
                    if req.enableVisualizer: cmd_widescreen.append("--visualizer")
                    if req.enableMetronome: cmd_widescreen.append("--metronome")
                    if req.timesig: cmd_widescreen.extend(["--timesig", req.timesig])
                    steps.append((f"Widescreen Video ({v_song} {vtype})", cmd_widescreen))

            # Cover Generator
            steps.append((f"Cover Generator ({v_song})", [
                python, "-u", str(TOOLS_DIR / "cover_generator.py"),
                "--song", v_song, "--author", author, "--theme", req.theme,
            ]))

            # Ko-Fi Packager
            steps.append((f"Ko-Fi Packager ({v_song})", [
                python, "-u", str(TOOLS_DIR / "legacy" / "kofi_zipper.py"),
                "--song", v_song, "--author", author,
            ]))

            # R2 Upload
            if req.doR2:
                steps.append((f"R2 Upload ({v_song})", [
                    python, "-u", str(TOOLS_DIR / "upload_bot.py"),
                    "--song", v_song, "--author", author,
                    "--mode", "r2",
                ]))
                
                # Website Catalog Sync
                steps.append((f"Website Catalog Sync ({v_song})", [
                    python, "-u", str(TOOLS_DIR / "upload_bot.py"),
                    "--song", v_song, "--price", req.price,
                    "--kofi_id", getattr(req, "paddle_product_id", "") or "prod_dummy123",
                    "--mode", "website",
                ]))

        # Collect enabled platforms
        enabled_platforms = []
        if req.doYoutube: enabled_platforms.append("youtube")
        if req.doInstagram: enabled_platforms.append("instagram")
        if req.doFacebook: enabled_platforms.append("facebook")
        if req.doTiktok: enabled_platforms.append("tiktok")
        if req.doThreads: enabled_platforms.append("threads")
        if req.doKofi: enabled_platforms.append("kofi")

        if enabled_platforms:
            if req.localUpload:
                # Direct local upload using upload_bot.py commands with native scheduling
                # Calculate timeline locally (exally like stage_to_server.py)
                tiktok_dir = settings.get("tiktok_dir", r"C:\Dev\meloscribe\TikToks")
                teaser_exists = False
                try:
                    if os.path.exists(tiktok_dir):
                        for f in os.listdir(tiktok_dir):
                            if f.lower() == f"{song.lower()} teaser.mp4":
                                teaser_exists = True
                                break
                except Exception:
                    pass

                if req.format == "viral_part":
                    teaser_exists = False

                import datetime
                interval_days = int(settings.get("schedule_interval_days", 3))
                try:
                    base_dt = datetime.datetime.strptime(f"{req.scheduleDate} {req.scheduleTime}", "%Y-%m-%d %H:%M")
                except Exception:
                    base_dt = datetime.datetime.now() + datetime.timedelta(days=1)

                platform_map = {
                    "youtube": "yt",
                    "instagram": "ig",
                    "facebook": "fb",
                    "tiktok": "tt",
                    "threads": "threads"
                }

                def is_combination_active(is_easy, profile):
                    if is_easy and not has_easy:
                        return False
                    if "kofi" in enabled_platforms and profile == "normal":
                        return True
                    for p in enabled_platforms:
                        if p == "kofi":
                            continue
                        p_pref = platform_map.get(p)
                        if not p_pref:
                            continue
                        easy_on = settings.get(f"{p_pref}_upload_easy", True) if is_easy else True
                        profile_on = settings.get(f"{p_pref}_upload_normal", True) if profile == "normal" else settings.get(f"{p_pref}_upload_tutorial", True)
                        if easy_on and profile_on:
                            return True
                    return False

                dates = {}
                current_dt = base_dt

                if teaser_exists:
                    dates["teaser"] = current_dt.strftime("%Y-%m-%d %H:%M")
                    current_dt += datetime.timedelta(days=interval_days)

                # 1. Original Normal
                if is_combination_active(is_easy=False, profile="normal"):
                    dates["original_normal"] = current_dt.strftime("%Y-%m-%d %H:%M")
                    dates["original_kofi"] = (current_dt + datetime.timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M")
                    current_dt += datetime.timedelta(days=interval_days)

                # 2. Original Tutorial
                if is_combination_active(is_easy=False, profile="tutorial"):
                    dates["original_tutorial"] = current_dt.strftime("%Y-%m-%d %H:%M")
                    current_dt += datetime.timedelta(days=interval_days)

                # 3. Easy Normal
                if is_combination_active(is_easy=True, profile="normal"):
                    dates["easy_normal"] = current_dt.strftime("%Y-%m-%d %H:%M")
                    dates["easy_kofi"] = (current_dt + datetime.timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M")
                    current_dt += datetime.timedelta(days=interval_days)

                # 4. Easy Tutorial
                if is_combination_active(is_easy=True, profile="tutorial"):
                    dates["easy_tutorial"] = current_dt.strftime("%Y-%m-%d %H:%M")
                    current_dt += datetime.timedelta(days=interval_days)

                # Build task steps for each enabled social platform
                for platform in ["youtube", "instagram", "facebook", "tiktok", "threads"]:
                    if platform not in enabled_platforms:
                        continue
                    p_pref = platform_map.get(platform)
                    if not p_pref:
                        continue
                    
                    upload_easy = settings.get(f"{p_pref}_upload_easy", True)
                    upload_normal = settings.get(f"{p_pref}_upload_normal", True)
                    upload_tutorial = settings.get(f"{p_pref}_upload_tutorial", True)

                    # 1. Teaser (if exists)
                    if teaser_exists and "teaser" in dates:
                        cmd_teaser = [
                            python, "-u", str(TOOLS_DIR / "upload_bot.py"),
                            "--song", f"{song} Teaser", "--author", author,
                            "--mode", platform, "--datetime", dates["teaser"]
                        ]
                        steps.append((f"{platform.capitalize()} Upload (Teaser)", cmd_teaser))

                    # 2. Original Normal
                    if "original_normal" in dates and upload_normal:
                        cmd_orig_norm = [
                            python, "-u", str(TOOLS_DIR / "upload_bot.py"),
                            "--song", song, "--author", author,
                            "--mode", platform, "--datetime", dates["original_normal"],
                            "--profile", "normal"
                        ]
                        steps.append((f"{platform.capitalize()} Upload (Original Normal)", cmd_orig_norm))

                    # 3. Original Tutorial
                    if "original_tutorial" in dates and upload_tutorial:
                        cmd_orig_tut = [
                            python, "-u", str(TOOLS_DIR / "upload_bot.py"),
                            "--song", song, "--author", author,
                            "--mode", platform, "--datetime", dates["original_tutorial"],
                            "--profile", "tutorial"
                        ]
                        steps.append((f"{platform.capitalize()} Upload (Original Tutorial)", cmd_orig_tut))

                    # 4. Easy Normal
                    if has_easy and "easy_normal" in dates and upload_easy and upload_normal:
                        cmd_easy_norm = [
                            python, "-u", str(TOOLS_DIR / "upload_bot.py"),
                            "--song", f"{song} Easy", "--author", author,
                            "--mode", platform, "--datetime", dates["easy_normal"],
                            "--profile", "normal"
                        ]
                        steps.append((f"{platform.capitalize()} Upload (Easy Normal)", cmd_easy_norm))

                    # 5. Easy Tutorial
                    if has_easy and "easy_tutorial" in dates and upload_easy and upload_tutorial:
                        cmd_easy_tut = [
                            python, "-u", str(TOOLS_DIR / "upload_bot.py"),
                            "--song", f"{song} Easy", "--author", author,
                            "--mode", platform, "--datetime", dates["easy_tutorial"],
                            "--profile", "tutorial"
                        ]
                        steps.append((f"{platform.capitalize()} Upload (Easy Tutorial)", cmd_easy_tut))

                # Build task steps for Ko-Fi Shop
                if "kofi" in enabled_platforms:
                    # 1. Original Ko-Fi
                    if "original_kofi" in dates:
                        cmd_kofi_orig = [
                            python, "-u", str(TOOLS_DIR / "upload_bot.py"),
                            "--song", song, "--author", author, "--price", req.price,
                            "--mode", "kofi", "--datetime", dates["original_kofi"]
                        ]
                        cmd_kofi_orig.extend(["--format", req.format])
                        steps.append((f"Ko-Fi Upload ({song})", cmd_kofi_orig))

                    # 2. Easy Ko-Fi
                    if has_easy and "easy_kofi" in dates:
                        cmd_kofi_easy = [
                            python, "-u", str(TOOLS_DIR / "upload_bot.py"),
                            "--song", f"{song} Easy", "--author", author, "--price", req.price,
                            "--mode", "kofi", "--datetime", dates["easy_kofi"]
                        ]
                        cmd_kofi_easy.extend(["--format", req.format])
                        steps.append((f"Ko-Fi Upload ({song} Easy)", cmd_kofi_easy))
            else:
                # Stage to Oracle Server (classic behavior)
                stage_cmd = [
                    python, "-u", str(TOOLS_DIR / "stage_to_server.py"),
                    "--song", song,
                    "--author", author,
                    "--price", req.price,
                    "--schedule_date", req.scheduleDate,
                    "--schedule_time", req.scheduleTime,
                    "--platforms", ",".join(enabled_platforms)
                ]
                stage_cmd.extend(["--format", req.format])
                if has_easy:
                    stage_cmd.append("--has_easy")
                steps.append(("Stage to Oracle Server", stage_cmd))

    total = len(steps)
    start_from = req.resumeFromStep if hasattr(req, 'resumeFromStep') else 0
    
    for i, (label, cmd) in enumerate(steps):
        if i < start_from:
            continue  # Skip already-completed steps (pause/resume)
            
        if stop_requested:
            # Save progress for resume
            await manager.broadcast({"type": "done", "message": f"⏸ Paused after step {i}/{total}. Resume from step {i}."})
            return
            
        # Dynamically inject the captured YouTube URL into the Ko-Fi command
        if label.startswith("Ko-Fi Upload") and captured_youtube_urls:
            try:
                if "--song" in cmd:
                    song_idx = cmd.index("--song")
                    if song_idx + 1 < len(cmd):
                        kofi_song = cmd[song_idx + 1]
                        if kofi_song in captured_youtube_urls:
                            cmd.extend(["--youtube_url", captured_youtube_urls[kofi_song]])
            except Exception as e:
                print(f"[Workflow] Error injecting YouTube URL: {e}")
            
        await manager.broadcast({"type": "status", "message": f"[{i+1}/{total}] {label}..."})
        await manager.broadcast({"type": "progress", "value": i / total})
        rc = await run_tool(cmd, label)
        if rc != 0 and not stop_requested:
            await manager.broadcast({"type": "done", "message": f"❌ {label} failed (exit code {rc}). Resume from step {i}."})
            return

    await manager.broadcast({"type": "progress", "value": 1.0})
    await manager.broadcast({"type": "done", "message": "🎉 Workflow completed!"})

@app.post("/api/workflow/stop")
def stop_workflow():
    global stop_requested, current_process
    stop_requested = True
    if current_process:
        try:
            subprocess.Popen(f"taskkill /F /T /PID {current_process.pid}", shell=True,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             creationflags=CREATION_FLAGS)
        except Exception:
            pass
    return {"status": "stopped"}

# -------------------------------------------------------------------
# Individual Module Endpoints
# -------------------------------------------------------------------
class ModuleRequest(BaseModel):
    song: str = ""
    author: str = ""
    theme: str = "warm"
    price: str = "3.00"
    format: str = "viral_part"
    zoom: float = 1.5
    shift: int = 0
    enableVisualizer: bool = True
    enableMetronome: bool = True
    enablePortraitAddon: bool = True
    timesig: str = "auto"
    scheduleDate: str = ""
    scheduleTime: str = "16:00"
    kofi_id: str = ""
    paddle_product_id: str = ""

@app.post("/api/module/{module}")
async def run_module(module: str, req: ModuleRequest):
    python = sys.executable
    cmd_map = {
        "keysight": [python, "-u", str(TOOLS_DIR / "keysight_bot.py"), "--song", req.song, "--theme", req.theme],
        "handbrake": [python, "-u", str(TOOLS_DIR.parent / "TikToks" / f"{req.song}.mp4")],
        "cover": [python, "-u", str(TOOLS_DIR / "cover_generator.py"), "--song", req.song, "--author", req.author, "--theme", req.theme],
        "video": [python, "-u", str(TOOLS_DIR / "video_generator.py"),
                  "--video", str(TOOLS_DIR.parent / "Keysight export" / f"{req.song}.mp4"),
                  "--title", req.song, "--author", req.author, "--type", "normal",
                  "--zoom", f"{req.zoom:.2f}", "--shift", str(int(req.shift)), "--theme", req.theme],
        "kofi_zip": [python, "-u", str(TOOLS_DIR / "legacy" / "kofi_zipper.py"), "--song", req.song, "--author", req.author],
        "kofi_upload": [python, "-u", str(TOOLS_DIR / "upload_bot.py"), "--song", req.song, "--price", req.price, "--mode", "kofi", "--format", req.format],
        "r2_upload": [python, "-u", str(TOOLS_DIR / "legacy" / "r2_uploader.py"), "--song", req.song, "--upload_only"],
        "r2_full": [python, "-u", str(TOOLS_DIR / "legacy" / "r2_uploader.py"), "--song", req.song, "--author", req.author, "--price", req.price, "--paddle_product_id", getattr(req, "paddle_product_id", "") or req.kofi_id or "", "--website_sync"],
        "package_zip": [python, "-u", str(TOOLS_DIR / "legacy" / "r2_uploader.py"), "--song", req.song, "--author", req.author, "--price", req.price, "--editor_only"],
        "youtube": [python, "-u", str(TOOLS_DIR / "upload_bot.py"), "--song", req.song, "--author", req.author,
                    "--mode", "youtube", "--datetime", f"{req.scheduleDate} {req.scheduleTime}"],
        "instagram": [python, "-u", str(TOOLS_DIR / "upload_bot.py"), "--song", req.song, "--author", req.author,
                      "--mode", "instagram", "--datetime", f"{req.scheduleDate} {req.scheduleTime}"],
        "facebook": [python, "-u", str(TOOLS_DIR / "upload_bot.py"), "--song", req.song, "--author", req.author,
                     "--mode", "facebook", "--datetime", f"{req.scheduleDate} {req.scheduleTime}"],
        "tiktok": [python, "-u", str(TOOLS_DIR / "upload_bot.py"), "--song", req.song, "--author", req.author,
                   "--mode", "tiktok", "--profile", "normal"],
        "website_add": [python, "-u", str(TOOLS_DIR / "upload_bot.py"), "--song", req.song, "--price", req.price,
                        "--kofi_id", req.kofi_id, "--mode", "website", "--author", req.author],
    }
    cmd = cmd_map.get(module)
    if not cmd:
        return {"error": f"Unknown module: {module}"}
    asyncio.create_task(_run_module_task(module, cmd))
    return {"status": "started"}

async def _run_module_task(module: str, cmd: list[str]):
    await manager.broadcast({"type": "status", "message": f"Running {module}..."})
    rc = await run_tool(cmd, module)
    if rc == 0:
        await manager.broadcast({"type": "done", "message": f"✅ {module} completed!"})
    else:
        await manager.broadcast({"type": "done", "message": f"❌ {module} failed (exit {rc})"})
@app.get("/callback")
def oauth_callback(code: str, state: str = None):
    """
    Unified OAuth callback proxy and direct processor.
    - If state == 'threads', directly handles Threads token exchange and saves it.
    - Otherwise, forwards to localhost:8080 (e.g. for TikTok/other local auth).
    """
    if state == "threads":
        try:
            import json
            settings_path = TOOLS_DIR / "meloscribe" / "backend" / "settings.json"
            tokens_path = TOOLS_DIR / "meloscribe" / "backend" / "threads_tokens.json"
            
            with open(settings_path, "r", encoding="utf-8") as f:
                settings = json.load(f)
            
            app_id = settings.get("threads_app_id", "2376057852870646")
            app_secret = settings.get("threads_app_secret", "")
            redirect_uri = "https://wooing-encrust-ladle.ngrok-free.dev/callback"

            # Exchange code for short-lived token
            resp = requests.post("https://graph.threads.net/oauth/access_token", data={
                "client_id": app_id,
                "client_secret": app_secret,
                "grant_type": "authorization_code",
                "redirect_uri": redirect_uri,
                "code": code
            })
            
            if resp.status_code != 200:
                return HTMLResponse(
                    content=f"<h1>Threads short-lived token exchange failed:</h1><pre>{resp.status_code} - {resp.text}</pre>",
                    status_code=400
                )
            
            short_data = resp.json()
            short_token = short_data.get("access_token")
            user_id = short_data.get("user_id")

            # Exchange short-lived token for long-lived token
            long_resp = requests.get("https://graph.threads.net/access_token", params={
                "grant_type": "th_exchange_token",
                "client_secret": app_secret,
                "access_token": short_token
            })
            
            if long_resp.status_code != 200:
                return HTMLResponse(
                    content=f"<h1>Threads long-lived token exchange failed:</h1><pre>{long_resp.status_code} - {long_resp.text}</pre>",
                    status_code=400
                )
            
            long_data = long_resp.json()
            long_token = long_data.get("access_token")

            # Fetch profile to get username
            me_resp = requests.get("https://graph.threads.net/v1.0/me", params={
                "fields": "id,username",
                "access_token": long_token
            })
            username = "unknown"
            if me_resp.status_code == 200:
                username = me_resp.json().get("username", "unknown")

            # Save credentials
            save_data = {
                "access_token": long_token,
                "threads_user_id": str(user_id),
                "username": username
            }
            with open(tokens_path, "w", encoding="utf-8") as f:
                json.dump(save_data, f, indent=4)

            print(f"[Threads Auth] Successfully authorized as {username}. Token saved.")
            return HTMLResponse(
                content=f"<h1>Threads Autorisierung erfolgreich!</h1><p>Du bist nun als <b>@{username}</b> angemeldet. Du kannst diesen Tab schliessen.</p>",
                status_code=200
            )

        except Exception as e:
            return HTMLResponse(content=f"<h1>Internal Error during Threads exchange: {e}</h1>", status_code=500)

    try:
        url = f"http://localhost:8080/?code={code}"
        if state:
            url += f"&state={state}"
        resp = requests.get(url)
        return HTMLResponse(content=resp.text, status_code=resp.status_code)
    except Exception as e:
        return HTMLResponse(content=f"<h1>Forwarding failed: {e}</h1>", status_code=500)


# -------------------------------------------------------------------
# TikTok Auth Endpoints
# -------------------------------------------------------------------
@app.get("/api/tiktok/status")
def tiktok_status():
    """Returns whether TikTok is authorized by actively checking the API."""
    tokens_path = TOOLS_DIR / "meloscribe" / "backend" / "tiktok_tokens.json"
    if not tokens_path.exists():
        return {"authorized": False, "message": "Not connected. Use /api/tiktok/authorize to connect."}
    try:
        import sys
        sys.path.insert(0, str(TOOLS_DIR / "meloscribe" / "backend"))
        from tiktok_auth import get_valid_token
        token = get_valid_token()
        if not token:
            return {"authorized": False, "message": "Access token expired and failed to refresh."}
        
        # Test token validity via basic user info endpoint
        url = "https://open.tiktokapis.com/v2/user/info/?fields=open_id,union_id,avatar_url"
        headers = {"Authorization": f"Bearer {token}"}
        resp = requests.get(url, headers=headers, timeout=5)
        
        if resp.status_code == 200:
            data = resp.json().get("data", {})
            user_data = data.get("user", {})
            return {
                "authorized": True,
                "open_id": user_data.get("open_id", "unknown"),
                "avatar_url": user_data.get("avatar_url", "")
            }
        else:
            return {"authorized": False, "message": f"TikTok API rejected token: {resp.text}"}
    except Exception as e:
        return {"authorized": False, "message": f"Validation error: {e}"}

@app.post("/api/tiktok/authorize")
def tiktok_authorize():
    """Trigger the first-time OAuth flow (opens browser)."""
    import importlib.util
    auth_path = str(TOOLS_DIR / "meloscribe" / "backend" / "tiktok_auth.py")
    spec = importlib.util.spec_from_file_location("tiktok_auth", auth_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    
    threading.Thread(target=mod.run_initial_auth, args=(False,), daemon=True).start()
    
    import time
    time.sleep(0.5)
    
    auth_url = getattr(mod, "LAST_AUTH_URL", None)
    print(f"[TikTok Auth] Generated URL: {auth_url}")
    return {
        "status": "opening browser for TikTok authorization...",
        "url": auth_url
    }

@app.post("/api/tiktok/sync")
async def tiktok_sync_now():
    """Manually trigger a TikTok sync."""
    def _run():
        import importlib.util
        sync_path = str(TOOLS_DIR / "meloscribe" / "backend" / "tiktok_sync.py")
        spec = importlib.util.spec_from_file_location("tiktok_sync", sync_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod.sync_tiktok()
    threading.Thread(target=_run, daemon=True).start()
    return {"status": "sync started"}

# -------------------------------------------------------------------
# Instagram Auth Endpoints
# -------------------------------------------------------------------
@app.get("/api/instagram/status")
def instagram_status():
    """Returns whether Instagram/Facebook is authorized by actively checking Graph API."""
    tokens_path = TOOLS_DIR / "meloscribe" / "backend" / "ig_tokens.json"
    if not tokens_path.exists():
        return {"authorized": False, "message": "Not connected."}
    try:
        tokens = json.loads(tokens_path.read_text())
        access_token = tokens.get("fb_access_token") or tokens.get("access_token")
        if not access_token:
            return {"authorized": False, "message": "No access token found in ig_tokens.json."}
            
        # Test Graph API token validity
        url = f"https://graph.facebook.com/v18.0/me?access_token={access_token}"
        resp = requests.get(url, timeout=5)
        if resp.status_code == 200:
            return {
                "authorized": True,
                "page_name": tokens.get("fb_page_name", "unknown")
            }
        else:
            err_msg = resp.json().get("error", {}).get("message", "Invalid Token")
            return {"authorized": False, "message": f"Graph API rejected token: {err_msg}"}
    except Exception as e:
        return {"authorized": False, "message": f"Validation error: {e}"}

@app.post("/api/instagram/sync")
async def instagram_sync_now():
    """Manually trigger an Instagram sync."""
    def _run():
        import importlib.util
        sync_path = str(TOOLS_DIR / "meloscribe" / "backend" / "ig_sync.py")
        spec = importlib.util.spec_from_file_location("ig_sync", sync_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod.sync_instagram()
    threading.Thread(target=_run, daemon=True).start()
    return {"status": "sync started"}

class InstagramAuthRequest(BaseModel):
    short_lived_token: Optional[str] = None

@app.post("/api/instagram/authorize")
def instagram_authorize(req: Optional[InstagramAuthRequest] = None):
    """Exchange short-lived Facebook token for permanent Page access token, or trigger browser OAuth."""
    if req and req.short_lived_token:
        import sys
        sys.path.insert(0, str(TOOLS_DIR / "meloscribe" / "backend"))
        try:
            from ig_setup import setup_instagram_account
            success = setup_instagram_account(req.short_lived_token)
            if success:
                return {"status": "success", "message": "Instagram and Facebook successfully authorized."}
            else:
                return {"status": "error", "message": "Failed to exchange token. Check backend console logs."}
        except Exception as e:
            return {"status": "error", "message": str(e)}
    else:
        def _run():
            import importlib.util
            auth_path = str(TOOLS_DIR / "meloscribe" / "backend" / "ig_auth.py")
            spec = importlib.util.spec_from_file_location("ig_auth", auth_path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            mod.run_instagram_auth()
        threading.Thread(target=_run, daemon=True).start()
        return {"status": "opening browser for Instagram authorization..."}


# -------------------------------------------------------------------
# YouTube Endpoints
# -------------------------------------------------------------------
@app.get("/api/youtube/status")
def youtube_status():
    """Returns whether YouTube is authorized by actively checking the API."""
    tokens_path = TOOLS_DIR / "meloscribe" / "backend" / "yt_tokens.json"
    if not tokens_path.exists():
        return {"authorized": False, "message": "Not connected."}
    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        
        with open(tokens_path, "r") as f:
            creds_data = json.load(f)
            
        scopes = [
            "https://www.googleapis.com/auth/youtube.upload",
            "https://www.googleapis.com/auth/youtube.readonly",
            "https://www.googleapis.com/auth/yt-analytics.readonly"
        ]
        creds = Credentials.from_authorized_user_info(creds_data, scopes)
        
        if not creds.valid:
            if creds.expired and creds.refresh_token:
                creds.refresh(Request())
                with open(tokens_path, "w") as f:
                    f.write(creds.to_json())
            else:
                return {"authorized": False, "message": "Token expired and refresh token is invalid."}
                
        # Send a validation request
        url = "https://www.googleapis.com/youtube/v3/channels?part=id&mine=true"
        headers = {"Authorization": f"Bearer {creds.token}"}
        resp = requests.get(url, headers=headers, timeout=5)
        if resp.status_code == 200:
            return {"authorized": True}
        else:
            return {"authorized": False, "message": f"YouTube API rejected token: {resp.text}"}
    except Exception as e:
        return {"authorized": False, "message": f"Validation error: {e}"}

@app.post("/api/youtube/sync")
async def youtube_sync_now():
    """Manually trigger a YouTube sync."""
    def _run():
        import importlib.util
        sync_path = str(TOOLS_DIR / "meloscribe" / "backend" / "yt_sync.py")
        spec = importlib.util.spec_from_file_location("yt_sync", sync_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod.sync_youtube()
    threading.Thread(target=_run, daemon=True).start()
    return {"status": "sync started"}

@app.post("/api/youtube/authorize")
def youtube_authorize():
    """Trigger the OAuth flow for YouTube (opens browser)."""
    def _run():
        import importlib.util
        auth_path = str(TOOLS_DIR / "meloscribe" / "backend" / "yt_auth.py")
        spec = importlib.util.spec_from_file_location("yt_auth", auth_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod.get_authenticated_service()
    threading.Thread(target=_run, daemon=True).start()
    return {"status": "opening browser for YouTube authorization..."}


# -------------------------------------------------------------------
# Threads Endpoints
# -------------------------------------------------------------------
@app.get("/api/threads/status")
def threads_status():
    """Returns whether Threads is authorized by actively checking the API."""
    tokens_path = TOOLS_DIR / "meloscribe" / "backend" / "threads_tokens.json"
    if not tokens_path.exists():
        return {"authorized": False, "message": "Not connected."}
    try:
        tokens = json.loads(tokens_path.read_text())
        access_token = tokens.get("access_token")
        if not access_token:
            return {"authorized": False, "message": "No access token."}
            
        # Test Threads API token validity
        url = f"https://graph.threads.net/v1.0/me?fields=id,username&access_token={access_token}"
        resp = requests.get(url, timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            return {
                "authorized": True,
                "username": data.get("username", tokens.get("username", "unknown"))
            }
        else:
            return {"authorized": False, "message": f"Threads API rejected token: {resp.text}"}
    except Exception as e:
        return {"authorized": False, "message": f"Validation error: {e}"}

class ThreadsAuthRequest(BaseModel):
    access_token: Optional[str] = None

@app.post("/api/threads/authorize")
def threads_authorize(req: Optional[ThreadsAuthRequest] = None):
    """Validate Threads access token and save, or trigger browser OAuth."""
    if req and req.access_token:
        try:
            url = f"https://graph.threads.net/v1.0/me?fields=id,username&access_token={req.access_token}"
            resp = requests.get(url, timeout=5)
            if resp.status_code == 200:
                data = resp.json()
                threads_user_id = data.get("id")
                
                tokens_path = TOOLS_DIR / "meloscribe" / "backend" / "threads_tokens.json"
                save_data = {
                    "access_token": req.access_token,
                    "threads_user_id": threads_user_id,
                    "username": data.get("username", "unknown")
                }
                with open(tokens_path, "w") as f:
                    json.dump(save_data, f, indent=4)
                    
                return {
                    "status": "success", 
                    "message": f"Threads successfully connected as '{data.get('username')}'."
                }
            else:
                err_msg = resp.json().get("error", {}).get("message", "Invalid Token")
                return {"status": "error", "message": f"API rejected token: {err_msg}"}
        except Exception as e:
            return {"status": "error", "message": str(e)}
    else:
        def _run():
            import importlib.util
            auth_path = str(TOOLS_DIR / "meloscribe" / "backend" / "threads_auth.py")
            spec = importlib.util.spec_from_file_location("threads_auth", auth_path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            mod.run_threads_auth()
        threading.Thread(target=_run, daemon=True).start()
        return {"status": "opening browser for Threads authorization..."}


@app.get("/api/workflow/suggest-date")
def suggest_workflow_date():
    from datetime import datetime, timedelta
    key_path = r"C:\Dev\ssh-key-2026-05-07.key"
    server_ip = "152.70.23.171"
    
    interval_days = int(load_settings().get("schedule_interval_days", 3))
    default_date = datetime.now() + timedelta(days=1)
    default_date_str = default_date.strftime("%Y-%m-%d")
    
    if not os.path.exists(key_path):
        return {"suggested_date": default_date_str}
        
    cmd = [
        "ssh", "-i", key_path, 
        "-o", "StrictHostKeyChecking=accept-new", 
        "-o", "ConnectTimeout=5", 
        "-o", "IdentitiesOnly=yes", 
        f"ubuntu@{server_ip}", 
        "sqlite3 /home/ubuntu/meloscribe/queue.db \"SELECT max(schedule_time) FROM upload_queue WHERE status = 'pending';\""
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=12, creationflags=CREATION_FLAGS)
        if res.returncode == 0 and res.stdout.strip() and res.stdout.strip() != "NULL" and res.stdout.strip() != "":
            max_pending = res.stdout.strip()
            max_dt = datetime.strptime(max_pending, "%Y-%m-%d %H:%M")
            suggested_dt = max_dt + timedelta(days=interval_days)
            return {"suggested_date": suggested_dt.strftime("%Y-%m-%d")}
    except Exception:
        pass
        
    return {"suggested_date": default_date_str}

@app.get("/api/logs")
def get_system_logs():
    return list(SYSTEM_LOGS)

@app.post("/api/logs/clear")
def clear_system_logs():
    SYSTEM_LOGS.clear()
    _error_log.clear()
    return {"status": "success"}

# -------------------------------------------------------------------
# Settings Endpoints
# -------------------------------------------------------------------
from settings import load_settings, save_settings

@app.get("/api/settings")
def get_settings():
    return load_settings()

@app.post("/api/settings")
async def update_settings(request: Request):
    data = await request.json()
    save_settings(data)
    # Automatically sync local credentials to VM in the background
    threading.Thread(target=sync_credentials_route, daemon=True).start()
    return {"status": "success"}

# -------------------------------------------------------------------
# Website Songs Catalog Endpoints
# -------------------------------------------------------------------
@app.get("/api/website/songs")
def get_website_songs():
    songs_path = r"c:\Dev\meloscribe-frontend\website\src\data\songs.json"
    if not os.path.exists(songs_path):
        return []
    try:
        with open(songs_path, "r", encoding="utf-8") as f:
            songs_list = json.load(f)
        return songs_list
    except Exception as e:
        log_error("Website Songs", f"Failed to load songs: {e}")
        return []

def run_git_push():
    try:
        import subprocess
        frontend_dir = r"C:\Dev\meloscribe-frontend"
        if os.path.exists(frontend_dir):
            subprocess.run(["git", "add", "website/src/data/songs.json"], cwd=frontend_dir, check=True, creationflags=CREATION_FLAGS)
            subprocess.run(["git", "commit", "-m", "Auto-sync songs.json from app"], cwd=frontend_dir, check=True, creationflags=CREATION_FLAGS)
            subprocess.run(["git", "push"], cwd=frontend_dir, check=True, creationflags=CREATION_FLAGS)
            print("[Git Sync] Automatically pushed songs.json update to GitHub.")
    except Exception as git_err:
        print(f"[Git Sync] Failed to auto-push: {git_err}")

def parse_price_to_cents(price_str):
    if not price_str:
        return None
    cleaned = "".join(c for c in str(price_str) if c.isdigit() or c in (".", ","))
    cleaned = cleaned.replace(",", ".")
    try:
        val = float(cleaned)
        return int(round(val * 100))
    except ValueError:
        return None

def sync_song_price_to_paddle(song, new_price_str, api_key, is_sandbox=True):
    import math
    import requests

    eur_cents = parse_price_to_cents(new_price_str)
    if eur_cents is None:
        print(f"[Paddle Pricing] Price '{new_price_str}' could not be parsed for '{song.get('title')}'")
        return None

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Paddle-Version": "1"
    }

    api_url = "https://sandbox-api.paddle.com" if is_sandbox else "https://api.paddle.com"

    price_id = song.get("paddleId")
    product_id = None
    is_new_product = False

    if not price_id or not price_id.startswith("pri_"):
        # Create a new product first!
        song_title = song.get("title", "Untitled Song")
        print(f"[Paddle Pricing] Song '{song_title}' has no valid Price ID. Creating new product in Paddle...")
        is_new_product = True
        try:
            prod_payload = {
                "name": song_title,
                "tax_category": "standard",
                "description": f"Learning package for {song_title}"
            }
            prod_res = requests.post(f"{api_url}/products", json=prod_payload, headers=headers, timeout=10)
            if prod_res.status_code not in (200, 201):
                print(f"[Paddle Pricing] Failed to create product in Paddle: {prod_res.text}")
                return None
            product_data = prod_res.json().get("data", {})
            product_id = product_data.get("id")
            if not product_id:
                print(f"[Paddle Pricing] No product ID returned after creation.")
                return None
            print(f"[Paddle Pricing] Created new product: {product_id} for '{song_title}'")
        except Exception as e:
            print(f"[Paddle Pricing] Error creating product: {e}")
            return None
    else:
        # Fetch existing price to retrieve product_id
        try:
            res = requests.get(f"{api_url}/prices/{price_id}", headers=headers, timeout=10)
            if res.status_code != 200:
                print(f"[Paddle Pricing] Failed to fetch price {price_id} from {api_url}: {res.text}")
                return None
            price_data = res.json().get("data", {})
            product_id = price_data.get("product_id")
            if not product_id:
                print(f"[Paddle Pricing] No product_id in price {price_id}")
                return None
        except Exception as e:
            print(f"[Paddle Pricing] Error fetching price from Paddle: {e}")
            return None

    usd_cents = eur_cents  # 1:1 parity
    gbp_cents = eur_cents  # 1:1 parity

    # Format check helper
    def get_song_format(s):
        if s.get("format") == "viral_part":
            return "viral_part"
        if s.get("format") == "full_arrangement":
            return "full_arrangement"
        p = s.get("price") or ""
        if "3" in p:
            return "viral_part"
        return "full_arrangement"

    song_title = song.get("title", "")
    difficulty = song.get("difficulty", "Original")
    song_format = get_song_format(song)
    price_name = f"{song_title} ({difficulty}) - {song_format.replace('_', ' ').title()}"
    description = f"Price for {song_title} ({difficulty}) {song_format}"

    payload = {
        "product_id": product_id,
        "name": price_name,
        "description": description,
        "tax_mode": "internal",
        "unit_price": {
            "amount": str(eur_cents),
            "currency_code": "EUR"
        },
        "unit_price_overrides": [
            {
                "country_codes": ["US"],
                "unit_price": {
                    "amount": str(usd_cents),
                    "currency_code": "USD"
                }
            },
            {
                "country_codes": ["GB"],
                "unit_price": {
                    "amount": str(gbp_cents),
                    "currency_code": "GBP"
                }
            }
        ]
    }

    try:
        create_res = requests.post(f"{api_url}/prices", json=payload, headers=headers, timeout=10)
        if create_res.status_code not in (200, 201):
            print(f"[Paddle Pricing] Failed to create price in Paddle: {create_res.text}")
            return None
        new_price_data = create_res.json().get("data", {})
        new_price_id = new_price_data.get("id")
        if not new_price_id:
            print(f"[Paddle Pricing] No price ID returned.")
            return None
            
        print(f"[Paddle Pricing] Created price {new_price_id} for '{song_title}' (EUR {eur_val}, USD {usd_cents/100.0}, GBP {gbp_val})")

        # Archive old price only if it existed in Paddle (i.e. not a new product setup)
        if not is_new_product and price_id:
            try:
                archive_res = requests.patch(f"{api_url}/prices/{price_id}", json={"status": "archived"}, headers=headers, timeout=10)
                if archive_res.status_code == 200:
                    print(f"[Paddle Pricing] Archived old price {price_id}")
            except Exception as archive_err:
                print(f"[Paddle Pricing] Failed to archive price {price_id}: {archive_err}")

        return new_price_id
    except Exception as e:
        print(f"[Paddle Pricing] Error creating price: {e}")
        return None

def delete_song_assets(song_name: str):
    # 1. Local Cakewalk directories
    try:
        settings = load_settings()
        cakewalk_dir = settings.get("cakewalk_dir", r"C:\Cakewalk Projects")
        for suffix in ["", " Easy"]:
            path = Path(cakewalk_dir) / f"{song_name}{suffix}"
            if path.exists() and path.is_dir():
                import shutil
                shutil.rmtree(str(path))
                print(f"[Delete] Removed local directory: {path}")
    except Exception as e:
        print(f"[Delete] Local Cakewalk folders cleanup skipped or failed: {e}")

    # 2. Local packages and Keysight exports (including RAW files)
    try:
        settings = load_settings()
        
        # Delete packages
        packages_dir = settings.get("packages_dir", r"C:\Dev\meloscribe\packages")
        zip_path = Path(packages_dir) / f"{song_name} Full Package.zip"
        if zip_path.exists():
            os.remove(str(zip_path))
            print(f"[Delete] Removed local package: {zip_path}")
            
        # Delete rendered videos from Keysight export
        keysight_dir = settings.get("keysight_dir", r"C:\Dev\meloscribe\Keysight export")
        for suffix in ["", " slow", "_preview", " Easy", " Easy slow", " Easy_preview"]:
            vid_path = Path(keysight_dir) / f"{song_name}{suffix}.mp4"
            if vid_path.exists():
                os.remove(str(vid_path))
                print(f"[Delete] Removed rendered video: {vid_path}")
                
        # Delete raw intermediate videos
        raw_dir = Path(keysight_dir) / "RAW"
        if raw_dir.exists() and raw_dir.is_dir():
            for filename in os.listdir(raw_dir):
                if filename.lower().startswith(song_name.lower()) and filename.lower().endswith(".mp4"):
                    file_path = raw_dir / filename
                    if file_path.exists():
                        os.remove(str(file_path))
                        print(f"[Delete] Removed local raw video: {file_path}")
                        
    except Exception as e:
        print(f"[Delete] Local packages and Keysight videos cleanup skipped or failed: {e}")

    # 3. Cloudflare R2 assets
    try:
        settings = load_settings()
        r2_account_id = settings.get("r2_account_id") or os.environ.get("R2_ACCOUNT_ID")
        r2_access_key = settings.get("r2_access_key_id") or os.environ.get("R2_ACCESS_KEY_ID")
        r2_secret_key = settings.get("r2_secret_access_key") or os.environ.get("R2_SECRET_ACCESS_KEY")
        r2_bucket = settings.get("r2_bucket_name", "meloscribe-assets") or os.environ.get("R2_BUCKET_NAME", "meloscribe-assets")

        if r2_account_id and r2_access_key and r2_secret_key:
            import boto3
            s3 = boto3.client(
                's3',
                endpoint_url=f'https://{r2_account_id}.r2.cloudflarestorage.com',
                aws_access_key_id=r2_access_key,
                aws_secret_access_key=r2_secret_key
            )
            # Delete package ZIP
            try:
                s3.delete_object(Bucket=r2_bucket, Key=f"{song_name} Full Package.zip")
                print(f"[Delete] Removed R2 package ZIP: {song_name} Full Package.zip")
            except Exception:
                pass

            # Delete folder contents
            prefix = f"{song_name}/"
            paginator = s3.get_paginator('list_objects_v2')
            for page in paginator.paginate(Bucket=r2_bucket, Prefix=prefix):
                if 'Contents' in page:
                    for obj in page['Contents']:
                        s3.delete_object(Bucket=r2_bucket, Key=obj['Key'])
                        print(f"[Delete] Removed R2 object: {obj['Key']}")
    except Exception as e:
        print(f"[Delete] Cloudflare R2 assets cleanup failed: {e}")

@app.delete("/api/website/songs/{song_id}")
async def delete_website_song(song_id: str, delete_assets: bool = False, background_tasks: BackgroundTasks = None):
    try:
        songs_path = r"c:\Dev\meloscribe-frontend\website\src\data\songs.json"
        if not os.path.exists(songs_path):
            raise HTTPException(status_code=404, detail="songs.json file not found")
            
        with open(songs_path, "r", encoding="utf-8") as f:
            songs_list = json.load(f)
            
        # Find song details before deleting
        target_song = None
        for s in songs_list:
            if s.get("id") == song_id:
                target_song = s
                break
                
        if not target_song:
            raise HTTPException(status_code=404, detail="Song not found in catalog")
            
        # Filter list
        updated_list = [s for s in songs_list if s.get("id") != song_id]
        
        with open(songs_path, "w", encoding="utf-8") as f:
            json.dump(updated_list, f, indent=2, ensure_ascii=False)
            
        song_title = target_song.get("title", "")
        
        if delete_assets and song_title:
            delete_song_assets(song_title)
            
        settings = load_settings()
        env = settings.get("environment", "sandbox")
        block_push = settings.get("block_sandbox_git_push", True)
        if env == "live" or not block_push:
            if background_tasks:
                background_tasks.add_task(run_git_push)
                
        return {"status": "success", "message": f"Deleted song '{song_title}'"}
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)

@app.post("/api/website/songs")
async def update_website_songs(request: Request, background_tasks: BackgroundTasks):
    try:
        songs_list = await request.json()
        songs_path = r"c:\Dev\meloscribe-frontend\website\src\data\songs.json"
        
        # Load environment and API key from settings.json
        settings = load_settings()
        env = settings.get("environment", "sandbox")
        is_sandbox = (env != "live")
        
        api_key = None
        if not is_sandbox:
            api_key = settings.get("paddle_live_api_key")
        else:
            api_key = settings.get("paddle_sandbox_api_key")

        # Fallback to credentials backup file if not in settings
        if not api_key:
            try:
                backup_path = r"C:\Dev\credentials.json"
                if not os.path.exists(backup_path):
                    backup_path = r"C:\Dev\meloscribe_credentials_backup.json"
                with open(backup_path, "r", encoding="utf-8") as cred_f:
                    creds = json.load(cred_f)
                    api_key = creds.get("paddle", {}).get("api_key")
            except Exception as cred_err:
                print(f"[Paddle Pricing] Warning: Failed to load api_key from backup: {cred_err}")
            
        # Parse old list for comparison
        old_songs_map = {}
        if os.path.exists(songs_path):
            try:
                with open(songs_path, "r", encoding="utf-8") as f:
                    old_list = json.load(f)
                    for s in old_list:
                        if isinstance(s, dict) and "id" in s:
                            old_songs_map[s["id"]] = s
            except Exception as read_err:
                print(f"[Paddle Pricing] Failed to parse old songs list: {read_err}")
                
        # Update songs catalog
        updated_songs = []
        for song in songs_list:
            if not isinstance(song, dict):
                updated_songs.append(song)
                continue
            song_id = song.get("id")
            if song_id == "global_settings":
                updated_songs.append(song)
                continue
            updated_songs.append(song)

        with open(songs_path, "w", encoding="utf-8") as f:
            json.dump(updated_songs, f, indent=2, ensure_ascii=False)
            
        # Push to GitHub in the background only if NOT in sandbox mode OR if sandbox git push is explicitly unblocked
        block_push = settings.get("block_sandbox_git_push", True)
        if not is_sandbox or not block_push:
            background_tasks.add_task(run_git_push)
        else:
            print("[Paddle Pricing] Sandbox mode active & block_sandbox_git_push is True. Automatic songs.json sync to GitHub/Vercel blocked.")
            
        return {"status": "success"}
    except Exception as e:
        log_error("Website Songs", f"Failed to save songs: {e}")
        return JSONResponse(content={"error": str(e)}, status_code=500)

# -------------------------------------------------------------------
# Ko-Fi Endpoints
# -------------------------------------------------------------------
@app.post("/api/kofi/sync")
async def kofi_sync_now():
    """Manually trigger Ko-Fi CSV sync."""
    def _run():
        import importlib.util
        sync_path = str(TOOLS_DIR / "meloscribe" / "backend" / "kofi_csv_sync.py")
        spec = importlib.util.spec_from_file_location("kofi_csv_sync", sync_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod.sync_kofi_csv()
    threading.Thread(target=_run, daemon=True).start()
    return {"status": "sync started"}
@app.get("/api/kofi/status")
def kofi_status():
    """Returns whether the Ko-Fi webhook is active and cookies are valid."""
    import urllib.request
    ngrok_active = False
    try:
        with urllib.request.urlopen("http://localhost:4040/api/tunnels", timeout=1) as resp:
            tunnels_data = json.loads(resp.read())
            tunnels = tunnels_data.get("tunnels", [])
            ngrok_active = len(tunnels) > 0
    except Exception:
        ngrok_active = False
        
    db_path = TOOLS_DIR / "meloscribe" / "backend" / "analytics.db"
    revenue_table_exists = False
    if db_path.exists():
        try:
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='revenue'")
            revenue_table_exists = cursor.fetchone() is not None
            conn.close()
        except Exception:
            pass
            
    # Validate Ko-Fi cookies actively
    cookie_file = TOOLS_DIR / "meloscribe" / "backend" / "kofi_cookie.txt"
    cookies_valid = False
    cookie_err = ""
    if cookie_file.exists():
        try:
            raw_cookie = cookie_file.read_text().strip()
            if raw_cookie:
                cookie_dict = {}
                for pair in raw_cookie.split(';'):
                    if '=' in pair:
                        name, value = pair.strip().split('=', 1)
                        cookie_dict[name] = value
                
                headers = {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                }
                resp = requests.get(
                    "https://ko-fi.com/manage/supportreceived",
                    cookies=cookie_dict,
                    headers=headers,
                    allow_redirects=False,
                    timeout=5
                )
                if resp.status_code == 200:
                    cookies_valid = True
                else:
                    cookie_err = f"Session expired (HTTP {resp.status_code} redirect)"
            else:
                cookie_err = "kofi_cookie.txt is empty"
        except Exception as e:
            cookie_err = f"Error validating: {e}"
    else:
        cookie_err = "kofi_cookie.txt not found"
        
    return {
        "webhook_active": ngrok_active,
        "revenue_table": revenue_table_exists,
        "ngrok_domain": "wooing-encrust-ladle.ngrok-free.dev",
        "cookies_valid": cookies_valid,
        "cookies_message": "Session is active" if cookies_valid else cookie_err
    }

from fastapi import Request
@app.post("/api/kofi/webhook")
async def kofi_webhook(request: Request):
    """
    Receives Ko-Fi webhook events (Donations, Shop Orders, Subscriptions, Messages).
    """
    try:
        # Ko-Fi sends data as x-www-form-urlencoded with a 'data' field containing JSON
        form_data = await request.form()
        data_str = form_data.get("data")
        if not data_str:
            return {"status": "ignored", "message": "No data field"}
            
        import json
        payload = json.loads(data_str)
        
        # Verify Token if configured
        expected_token = load_settings().get("kofi_verification_token")
        if expected_token:
            payload_token = payload.get("verification_token")
            if payload_token != expected_token:
                print(f"[Ko-Fi Webhook] WARNING: Invalid verification token received: {payload_token}")
                return JSONResponse(content={"error": "Invalid verification token"}, status_code=401)
        
        amount = float(payload.get("amount", 0))
        event_type = payload.get("type", "Unknown") # Donation, Subscription, Shop Order
        message = payload.get("message", "")
        buyer = payload.get("from_name", "Anonymous")
        currency = payload.get("currency", "EUR")
        kofi_id = payload.get("kofi_transaction_id", "")
        
        # Try to extract song name if it's a Shop Order
        song_name = None
        if event_type == "Shop Order":
            items = payload.get("shop_items", [])
            if items and len(items) > 0:
                item_name = items[0].get("direct_link_code", "") or items[0].get("variation_name", "") or message
                import re
                m = re.split(r'\s*[-—|]\s*|\s+\(', item_name)
                if m:
                    song_name = m[0].strip()
        
        db_path = TOOLS_DIR / "meloscribe" / "backend" / "analytics.db"
        if db_path.exists():
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            
            import datetime
            now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            
            # Log revenue (only if amount > 0)
            if amount > 0:
                cursor.execute('''
                    INSERT INTO revenue (date, event_type, amount, currency, buyer, message, song_name) 
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', (now_str, event_type, amount, currency, buyer, message, song_name))
                print(f"[Ko-Fi Webhook] Logged {event_type}: {amount} {currency} from {buyer}")
            
            # Always log message if present
            if message and message.strip():
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS kofi_messages (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        date TEXT, buyer TEXT, message TEXT, amount REAL, 
                        event_type TEXT, is_read INTEGER DEFAULT 0
                    )
                ''')
                cursor.execute('''
                    INSERT INTO kofi_messages (date, buyer, message, amount, event_type) 
                    VALUES (?, ?, ?, ?, ?)
                ''', (now_str, buyer, message.strip(), amount, event_type))
                print(f"[Ko-Fi Webhook] Saved message from {buyer}: {message[:50]}")
            
            conn.commit()
            conn.close()
            
        return {"status": "success"}
    except Exception as e:
        print(f"[Ko-Fi Webhook] Error processing event: {e}")
        return {"status": "error", "message": str(e)}

@app.post("/api/kofi/manual-sale")
async def manual_kofi_sale(req: Request):
    """Manually log a Ko-Fi sale that was missed (e.g. PC was off when webhook fired)."""
    data = await req.json()
    amount = float(data.get("amount", 0))
    buyer = data.get("buyer", "Manual Entry")
    song_name = data.get("song_name", None)
    message = data.get("message", "")
    
    if amount <= 0:
        return JSONResponse(content={"error": "Amount must be > 0"}, status_code=400)
    
    db_path = TOOLS_DIR / "meloscribe" / "backend" / "analytics.db"
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    import datetime
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    c.execute('''
        INSERT INTO revenue (date, event_type, amount, currency, buyer, message, song_name) 
        VALUES (?, ?, ?, ?, ?, ?, ?)
    ''', (now_str, "Shop Order", amount, "EUR", buyer, message, song_name))
    conn.commit()
    conn.close()
    return JSONResponse(content={"success": True})

@app.get("/api/kofi/messages")
async def get_kofi_messages():
    """Get all Ko-Fi messages."""
    db_path = TOOLS_DIR / "meloscribe" / "backend" / "analytics.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    try:
        c.execute("CREATE TABLE IF NOT EXISTS kofi_messages (id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT, buyer TEXT, message TEXT, amount REAL, event_type TEXT, is_read INTEGER DEFAULT 0)")
        messages = [dict(r) for r in c.execute("SELECT * FROM kofi_messages ORDER BY date DESC LIMIT 50").fetchall()]
    except Exception:
        messages = []
    conn.close()
    return JSONResponse(content=messages)

@app.post("/api/kofi/messages/{msg_id}/read")
async def mark_message_read(msg_id: int):
    db_path = TOOLS_DIR / "meloscribe" / "backend" / "analytics.db"
    conn = sqlite3.connect(db_path)
    conn.cursor().execute("UPDATE kofi_messages SET is_read=1 WHERE id=?", (msg_id,))
    conn.commit()
    conn.close()
    return JSONResponse(content={"success": True})



@app.get("/api/analytics")
def get_analytics(range: str = "30d"):
    db_path = TOOLS_DIR / "meloscribe" / "backend" / "analytics.db"
    if not db_path.exists():
        return {"error": "Analytics database not found."}
    
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        # 1. Total KPI
        cursor.execute("SELECT SUM(views) as v, SUM(likes) as l, SUM(comments) as c, SUM(shares) as sh, SUM(saves) as sa, COUNT(id) as cnt FROM videos")
        totals = cursor.fetchone()
        
        # 2. Platform Breakdown
        cursor.execute("SELECT platform, SUM(views) as views, SUM(likes) as likes, SUM(comments) as comments, SUM(shares) as shares, SUM(saves) as saves FROM videos GROUP BY platform")
        platforms = [dict(r) for r in cursor.fetchall()]
        
        has_threads = any(p["platform"].lower() == "threads" for p in platforms)
        if not has_threads:
            cursor.execute("SELECT views FROM snapshots WHERE platform = 'threads' ORDER BY snapshot_date DESC LIMIT 1")
            threads_views_row = cursor.fetchone()
            threads_views = threads_views_row[0] if (threads_views_row and threads_views_row[0] is not None) else 0
            platforms.append({
                "platform": "threads",
                "views": threads_views,
                "likes": 0,
                "comments": 0,
                "shares": 0,
                "saves": 0
            })
        
        # 3. Song Performance (grouped by song + format)
        cursor.execute('''
            SELECT v.song_name as song, v.author, v.language, v.format,
                   SUM(v.views) as totalViews, SUM(v.likes) as totalLikes, SUM(v.saves) as totalSaves,
                   MAX(v.publish_date) as latest_publish,
                   t.bpm, t.theme, st.difficulty
            FROM videos v
            LEFT JOIN tracks t ON v.song_name = t.song_name
            LEFT JOIN song_tags st ON v.song_name = st.song_name
            GROUP BY v.song_name, v.format
        ''')
        songs_raw = cursor.fetchall()
        
        # Merge songs with same name but different formats into a single matrix row for the main chart
        matrix_dict = {}
        for r in songs_raw:
            s_name = r["song"]
            if s_name not in matrix_dict:
                matrix_dict[s_name] = {
                    "song": s_name, "author": r["author"], "language": r["language"],
                    "bpm": r["bpm"], "theme": r["theme"], "difficulty": r["difficulty"],
                    "totalViews": 0, "totalLikes": 0, "totalSaves": 0,
                    "latest_publish": r["latest_publish"]
                }
            matrix_dict[s_name]["totalViews"] += r["totalViews"] or 0
            matrix_dict[s_name]["totalLikes"] += r["totalLikes"] or 0
            matrix_dict[s_name]["totalSaves"] += r["totalSaves"] or 0
            # Keep the absolute newest publish date
            if r["latest_publish"] and (not matrix_dict[s_name]["latest_publish"] or r["latest_publish"] > matrix_dict[s_name]["latest_publish"]):
                matrix_dict[s_name]["latest_publish"] = r["latest_publish"]
            
            # Format breakdown
            matrix_dict[s_name][f"{r['format']} Views"] = r["totalViews"] or 0

        # Add platform views
        cursor.execute("SELECT song_name, platform, SUM(views) as views FROM videos GROUP BY song_name, platform")
        for row in cursor.fetchall():
            if row["song_name"] in matrix_dict:
                matrix_dict[row["song_name"]][f"{row['platform'].capitalize()} Views"] = row["views"]
                
        songs = list(matrix_dict.values())
        # Sort by latest publish date
        songs.sort(key=lambda x: x["latest_publish"] or "", reverse=True)
        
        # 4. Correlations
        formats_data = {r["format"].strip(): dict(r) for r in cursor.execute("SELECT format, AVG(views) as avgViews, COUNT(id) as count FROM videos GROUP BY format").fetchall() if r["format"]}
        all_possible_formats = ["Standard", "Tutorial", "Easy", "Easy Tutorial", "Hook/Teaser"]
        byFormat = []
        for fmt in all_possible_formats:
            found = False
            for db_fmt, db_data in formats_data.items():
                if db_fmt.lower() == fmt.lower() or (fmt == "Hook/Teaser" and db_fmt.lower() in ["hook", "teaser"]):
                    byFormat.append({
                        "format": fmt,
                        "avgViews": db_data["avgViews"] or 0,
                        "count": db_data["count"] or 0
                    })
                    found = True
                    break
            if not found:
                byFormat.append({
                    "format": fmt,
                    "avgViews": 0.0,
                    "count": 0
                })

        correlations = {
            "byLanguage": [dict(r) for r in cursor.execute("SELECT language, AVG(views) as avgViews, COUNT(id) as count FROM videos GROUP BY language").fetchall()],
            "byAuthor": [dict(r) for r in cursor.execute("SELECT author, AVG(views) as avgViews, SUM(views) as totalViews FROM videos GROUP BY author").fetchall()],
            "byBpm": [dict(r) for r in cursor.execute("SELECT t.bpm, AVG(v.views) as avgViews FROM videos v JOIN tracks t ON v.song_name = t.song_name GROUP BY t.bpm").fetchall()],
            "byFormat": byFormat,
            "byVideoType": [dict(r) for r in cursor.execute("SELECT CASE WHEN duration_sec < 61 AND duration_sec > 0 THEN 'Short (<60s)' ELSE 'Long-form' END as videoType, AVG(views) as avgViews, COUNT(id) as count FROM videos WHERE duration_sec > 0 GROUP BY videoType").fetchall()]
        }
        
        # 5. Trending / Growth Data
        cursor.execute("SELECT snapshot_date as date, platform, SUM(views) as views FROM snapshots GROUP BY snapshot_date, platform ORDER BY snapshot_date ASC")
        snapshot_rows = cursor.fetchall()
        
        platforms_to_track = ["youtube", "instagram", "tiktok", "facebook", "threads"]
        growth_dict = {}
        last_known = {p: 0 for p in platforms_to_track}
        
        dates_sorted = sorted(list(set(r["date"] for r in snapshot_rows)))
        
        rows_by_date = {}
        for r in snapshot_rows:
            dt = r["date"]
            if dt not in rows_by_date:
                rows_by_date[dt] = {}
            rows_by_date[dt][r["platform"].lower()] = r["views"]
            
        for dt in dates_sorted:
            growth_dict[dt] = {"date": dt}
            for p in platforms_to_track:
                if p in rows_by_date[dt]:
                    last_known[p] = rows_by_date[dt][p]
                growth_dict[dt][p] = last_known[p]
                
        growthData = list(growth_dict.values())
        
        # Trending Momentum: Compare recent snapshots
        trending = []
        if len(growthData) >= 2:
            latest = growthData[-1]["date"]
            cursor.execute('''
                SELECT song_name, SUM(views) as views_now 
                FROM snapshots WHERE snapshot_date = ? GROUP BY song_name
            ''', (latest,))
            now_views = {r["song_name"]: r["views_now"] for r in cursor.fetchall()}
            
            # Skip the very first snapshot day (often incomplete from initial import)
            # Use the second-oldest day as baseline, or 7 days ago, whichever is more recent
            baseline_idx = max(1, len(growthData) - 7)  # Start from index 1 to skip first day
            target_date = growthData[baseline_idx]["date"]
            cursor.execute('''
                SELECT song_name, SUM(views) as views_past 
                FROM snapshots WHERE snapshot_date = ? GROUP BY song_name
            ''', (target_date,))
            past_views = {r["song_name"]: r["views_past"] for r in cursor.fetchall()}
            
            days_diff = len(growthData) - baseline_idx
            for s in now_views:
                diff = now_views[s] - past_views.get(s, 0)
                if diff > 0:
                    trending.append({"song": s, "growth": diff, "days": days_diff})
            trending.sort(key=lambda x: x["growth"], reverse=True)

        # 6. Revenue & Top Selling
        cursor.execute("SELECT SUM(amount) as total FROM revenue")
        rev_total = cursor.fetchone()["total"] or 0
        
        cursor.execute("SELECT strftime('%Y-%m', date) as month, SUM(amount) as amount FROM revenue GROUP BY month ORDER BY month ASC")
        rev_by_month = [dict(r) for r in cursor.fetchall()]
        
        cursor.execute("SELECT song_name, SUM(amount) as revenue FROM revenue WHERE song_name IS NOT NULL AND song_name != '' GROUP BY song_name ORDER BY revenue DESC LIMIT 10")
        top_selling = [dict(r) for r in cursor.fetchall()]

        # 7. Channel Insights
        cursor.execute("SELECT platform, followers, profile_views, website_clicks FROM channel_insights WHERE date = (SELECT MAX(date) FROM channel_insights)")
        channel = [dict(r) for r in cursor.fetchall()]
        
        # 8. Best Posting Time Heatmap (day-of-week × hour → avg views)
        bestPostingTime = []
        try:
            cursor.execute('''
                SELECT 
                    CAST(strftime('%w', publish_date) AS INTEGER) as dow,
                    CAST(strftime('%H', publish_date) AS INTEGER) as hour,
                    AVG(views) as avgViews,
                    COUNT(*) as count
                FROM videos 
                WHERE publish_date IS NOT NULL AND publish_date != ''
                GROUP BY dow, hour
            ''')
            bestPostingTime = [dict(r) for r in cursor.fetchall()]
        except Exception:
            pass

        # 9. Engagement Rate per song
        for s in songs:
            v = s.get("totalViews", 0) or 0
            l = s.get("totalLikes", 0) or 0
            sv = s.get("totalSaves", 0) or 0
            if v > 0:
                s["engagementRate"] = round((l + sv) / v * 100, 2)
            else:
                s["engagementRate"] = 0

        # 10. Competitor Data
        competitors = []
        try:
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='competitors'")
            if cursor.fetchone():
                cursor.execute('''
                    SELECT c.channel_id, c.channel_name, 
                           cv.title, cv.views, cv.likes, cv.published_at, cv.video_id,
                           cv.snapshot_date
                    FROM competitors c
                    LEFT JOIN competitor_videos cv ON c.channel_id = cv.channel_id
                    ORDER BY c.channel_name, cv.published_at DESC
                ''')
                comp_dict = {}
                for r in cursor.fetchall():
                    cid = r["channel_id"]
                    if cid not in comp_dict:
                        comp_dict[cid] = {"channelId": cid, "channelName": r["channel_name"], "videos": []}
                    if r["title"]:
                        comp_dict[cid]["videos"].append({
                            "title": r["title"], "views": r["views"], "likes": r["likes"],
                            "publishedAt": r["published_at"], "videoId": r["video_id"]
                        })
                competitors = list(comp_dict.values())
        except Exception:
            pass

        # 11. Audience Demographics
        demographics = {}
        try:
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='audience_demographics'")
            if cursor.fetchone():
                cursor.execute('''
                    SELECT platform, metric_type, metric_key, metric_value 
                    FROM audience_demographics 
                    WHERE snapshot_date = (SELECT MAX(snapshot_date) FROM audience_demographics)
                    ORDER BY metric_value DESC
                ''')
                for r in cursor.fetchall():
                    plat = r["platform"]
                    mtype = r["metric_type"]
                    if plat not in demographics:
                        demographics[plat] = {}
                    if mtype not in demographics[plat]:
                        demographics[plat][mtype] = []
                    demographics[plat][mtype].append({"key": r["metric_key"], "value": r["metric_value"]})
        except Exception:
            pass

        # Calculate sum of followers to push to Oracle VM
        try:
            cursor.execute("""
                SELECT SUM(followers) 
                FROM (
                    SELECT followers FROM channel_insights 
                    WHERE (platform, date) IN (
                        SELECT platform, MAX(date) FROM channel_insights GROUP BY platform
                    )
                )
            """)
            total_f_row = cursor.fetchone()
            total_f = total_f_row[0] if (total_f_row and total_f_row[0] is not None) else 0
            if total_f and total_f > 0:
                import platform as pf
                if pf.system() == "Windows":
                    import threading
                    def push_stats_to_oracle(f_count):
                        try:
                            import requests
                            requests.post("https://api.meloscribe.dev/api/public/stats", json={"followers": f_count}, timeout=5.0)
                            print(f"[Stats Sync] Successfully pushed {f_count} followers to Oracle VM.")
                        except Exception as e:
                            print(f"[Stats Sync] Failed to push to Oracle: {e}")
                    threading.Thread(target=push_stats_to_oracle, args=(total_f,)).start()
        except Exception as stats_err:
            print(f"[Stats Sync] Error calculating total followers for sync: {stats_err}")

        conn.close()
        
        return {
            "kpi": {
                "totalViews": totals["v"] or 0,
                "totalLikes": totals["l"] or 0,
                "totalComments": totals["c"] or 0,
                "totalShares": totals["sh"] or 0,
                "totalSaves": totals["sa"] or 0,
                "totalVideos": totals["cnt"] or 0
            },
            "platformBreakdown": platforms,
            "songPerformance": songs,
            "growthData": growthData,
            "correlations": correlations,
            "trending": trending[:5],
            "revenue": {
                "total": rev_total,
                "byMonth": rev_by_month,
                "topSelling": top_selling
            },
            "channelInsights": channel,
            "bestPostingTime": bestPostingTime,
            "competitors": competitors,
            "demographics": demographics
        }
        
    except Exception as e:
        return {"error": f"Database read error: {e}"}


@app.post("/api/demographics/sync")
def sync_demographics():
    import subprocess
    import platform as pf
    
    python_exe = str(TOOLS_DIR / "meloscribe" / "backend" / ".venv" / "Scripts" / "python.exe")
    if pf.system() != "Windows":
        python_exe = str(TOOLS_DIR / "meloscribe" / "backend" / ".venv" / "bin" / "python")
        
    script_path = str(TOOLS_DIR / "scrape_demographics.py")
    
    try:
        print(f"Running demographics sync script: {script_path}")
        res = subprocess.run([python_exe, script_path], capture_output=True, text=True, timeout=180)
        
        try:
            db_path = TOOLS_DIR / "meloscribe" / "backend" / "analytics.db"
            if db_path.exists():
                import sqlite3
                conn = sqlite3.connect(db_path)
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute("SELECT platform, metric_type, metric_key, metric_value, snapshot_date FROM audience_demographics WHERE snapshot_date = (SELECT MAX(snapshot_date) FROM audience_demographics)")
                rows = [dict(r) for r in cursor.fetchall()]
                conn.close()
                
                if rows:
                    import requests
                    requests.post("https://api.meloscribe.dev/api/demographics/sync-raw", json={"demographics": rows}, timeout=10.0)
                    print("[Demographics Sync] Successfully pushed demographic data to Oracle VM.")
        except Exception as push_err:
            print(f"[Demographics Sync] Warning: Failed to push demographics to Oracle: {push_err}")
            
        if res.returncode == 0:
            return {"status": "ok", "output": res.stdout}
        else:
            return {"status": "error", "message": res.stderr or res.stdout}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.post("/api/demographics/sync-raw")
def sync_raw_demographics(payload: dict):
    rows = payload.get("demographics", [])
    if not rows:
        return {"status": "error", "message": "No demographic data in payload."}
        
    db_path = TOOLS_DIR / "meloscribe" / "backend" / "analytics.db"
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS audience_demographics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                platform TEXT,
                metric_type TEXT,
                metric_key TEXT,
                metric_value REAL,
                snapshot_date TEXT
            )
        ''')
        
        for r in rows:
            cursor.execute('''
                DELETE FROM audience_demographics 
                WHERE platform = ? AND metric_type = ? AND metric_key = ? AND snapshot_date = ?
            ''', (r["platform"], r["metric_type"], r["metric_key"], r["snapshot_date"]))
            
            cursor.execute('''
                INSERT INTO audience_demographics (platform, metric_type, metric_key, metric_value, snapshot_date)
                VALUES (?, ?, ?, ?, ?)
            ''', (r["platform"], r["metric_type"], r["metric_key"], r["metric_value"], r["snapshot_date"]))
            
        conn.commit()
        conn.close()
        return {"status": "ok", "message": f"Successfully imported {len(rows)} demographic records."}
    except Exception as e:
        return {"status": "error", "message": str(e)}

# -------------------------------------------------------------------
# WebSocket endpoint
# -------------------------------------------------------------------
@app.websocket("/ws/logs")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()  # Keep alive
    except WebSocketDisconnect:
        manager.disconnect(websocket)

# -------------------------------------------------------------------
# AI Advisor & Todo Endpoints
# -------------------------------------------------------------------
_DB_PATH = Path(__file__).parent / "analytics.db"
import datetime as _dt

# -------------------------------------------------------------------
# Competitor Tracker Endpoints
# -------------------------------------------------------------------
@app.post("/api/competitors")
async def add_competitor(req: Request):
    data = await req.json()
    channel_input = data.get("channel", "").strip()
    if not channel_input:
        return JSONResponse(content={"error": "No channel provided"}, status_code=400)
    
    try:
        from yt_auth import get_authenticated_service
        from googleapiclient.discovery import build
        creds = get_authenticated_service()
        youtube = build("youtube", "v3", credentials=creds)
        
        # Resolve channel: could be ID, handle (@name), or URL
        channel_id = channel_input
        channel_name = channel_input
        
        # If it's a URL, extract the part after the last /
        if "/" in channel_input:
            channel_input = channel_input.rstrip("/").split("/")[-1]
        
        if channel_input.startswith("@"):
            # Search by handle
            resp = youtube.search().list(part="snippet", q=channel_input, type="channel", maxResults=1).execute()
            if resp.get("items"):
                channel_id = resp["items"][0]["snippet"]["channelId"]
                channel_name = resp["items"][0]["snippet"]["channelTitle"]
        elif channel_input.startswith("UC"):
            channel_id = channel_input
            resp = youtube.channels().list(part="snippet", id=channel_id).execute()
            if resp.get("items"):
                channel_name = resp["items"][0]["snippet"]["title"]
        else:
            resp = youtube.search().list(part="snippet", q=channel_input, type="channel", maxResults=1).execute()
            if resp.get("items"):
                channel_id = resp["items"][0]["snippet"]["channelId"]
                channel_name = resp["items"][0]["snippet"]["channelTitle"]
        
        conn = sqlite3.connect(_DB_PATH)
        c = conn.cursor()
        c.execute("CREATE TABLE IF NOT EXISTS competitors (channel_id TEXT PRIMARY KEY, channel_name TEXT, added_date TEXT)")
        c.execute("INSERT OR IGNORE INTO competitors (channel_id, channel_name, added_date) VALUES (?, ?, ?)",
                  (channel_id, channel_name, _dt.datetime.now().isoformat()))
        conn.commit()
        conn.close()
        
        return JSONResponse(content={"success": True, "channelId": channel_id, "channelName": channel_name})
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)

@app.delete("/api/competitors/{channel_id}")
async def delete_competitor(channel_id: str):
    conn = sqlite3.connect(_DB_PATH)
    c = conn.cursor()
    c.execute("DELETE FROM competitors WHERE channel_id=?", (channel_id,))
    c.execute("DELETE FROM competitor_videos WHERE channel_id=?", (channel_id,))
    conn.commit()
    conn.close()
    return JSONResponse(content={"success": True})

@app.post("/api/competitors/sync")
async def sync_competitors():
    try:
        from yt_auth import get_authenticated_service
        from googleapiclient.discovery import build
        creds = get_authenticated_service()
        youtube = build("youtube", "v3", credentials=creds)
        
        conn = sqlite3.connect(_DB_PATH)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        
        c.execute("SELECT channel_id, channel_name FROM competitors")
        comps = c.fetchall()
        today = _dt.date.today().isoformat()
        total_synced = 0
        
        for comp in comps:
            cid = comp["channel_id"]
            try:
                # Get latest 10 videos from this channel
                search_resp = youtube.search().list(
                    part="snippet", channelId=cid, order="date",
                    type="video", maxResults=10
                ).execute()
                
                video_ids = [item["id"]["videoId"] for item in search_resp.get("items", [])]
                if not video_ids:
                    continue
                    
                stats_resp = youtube.videos().list(
                    part="statistics,snippet", id=",".join(video_ids)
                ).execute()
                
                for item in stats_resp.get("items", []):
                    vid_id = item["id"]
                    title = item["snippet"]["title"]
                    published = item["snippet"]["publishedAt"][:10]
                    views = int(item["statistics"].get("viewCount", 0))
                    likes = int(item["statistics"].get("likeCount", 0))
                    
                    c.execute("""INSERT OR REPLACE INTO competitor_videos 
                               (channel_id, video_id, title, views, likes, published_at, snapshot_date)
                               VALUES (?, ?, ?, ?, ?, ?, ?)""",
                             (cid, vid_id, title, views, likes, published, today))
                    total_synced += 1
            except Exception as e:
                print(f"[Competitor Sync] Failed for {comp['channel_name']}: {e}")
        
        conn.commit()
        conn.close()
        return JSONResponse(content={"success": True, "synced": total_synced})
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)

@app.get("/api/todos")
async def get_todos():
    # Load published songs from songs.json to auto-complete matching todos
    published_titles = set()
    songs_path = r"c:\Dev\meloscribe-frontend\website\src\data\songs.json"
    if os.path.exists(songs_path):
        try:
            with open(songs_path, "r", encoding="utf-8") as f:
                songs_list = json.load(f)
                for s in songs_list:
                    if isinstance(s, dict) and "title" in s:
                        published_titles.add(s["title"])
        except Exception as e:
            print(f"[Todo Auto-Complete] Error reading songs.json: {e}")

    def clean_name(todo_name):
        name = todo_name
        for prefix in ["[PRIORITY] ", "[FORMAT-SHIFT] ", "[RE-PURPOSE] "]:
            if name.startswith(prefix):
                name = name[len(prefix):]
        return "".join(c for c in name.lower() if c.isalnum())

    published_cleaned = {clean_name(t) for t in published_titles}

    conn = sqlite3.connect(_DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    
    # Get all todos
    todos_raw_db = [dict(r) for r in c.execute("SELECT * FROM todos WHERE status='pending'").fetchall()]
    
    completed_ids = []
    todos_raw = []
    
    for t in todos_raw_db:
        t_cleaned = clean_name(t["song_name"])
        is_completed = False
        for p_clean in published_cleaned:
            if p_clean and (p_clean == t_cleaned or p_clean in t_cleaned or t_cleaned in p_clean):
                is_completed = True
                break
        
        if is_completed:
            completed_ids.append(t["id"])
        else:
            todos_raw.append(t)
            
    if completed_ids:
        c.executemany("UPDATE todos SET status='completed' WHERE id=?", [(tid,) for tid in completed_ids])
        conn.commit()
        print(f"[Todo Auto-Complete] Auto-completed {len(completed_ids)} todos: {completed_ids}")
    
    # Smart sort: prioritize songs that match high-performing patterns
    for t in todos_raw:
        song = t["song_name"].replace("[PRIORITY] ", "").replace("[FORMAT-SHIFT] ", "").replace("[RE-PURPOSE] ", "")
        row = c.execute("SELECT AVG(views) as avg_v FROM videos WHERE song_name LIKE ?", (f"%{song.split(' - ')[0].strip()}%",)).fetchone()
        t["_score"] = row["avg_v"] if row and row["avg_v"] else 0
        
        # Boost PRIORITY tagged songs
        if "[PRIORITY]" in t["song_name"]:
            t["_score"] = (t["_score"] or 0) + 999999
    
    # Sort: highest predicted performance first
    todos_raw.sort(key=lambda x: x.get("_score", 0), reverse=True)
    
    # Remove internal score before sending
    for t in todos_raw:
        t.pop("_score", None)
    
    conn.close()
    return JSONResponse(content=todos_raw)

@app.post("/api/todos")
async def add_todo(req: Request):
    data = await req.json()
    song_name = data.get("song_name")
    if not song_name:
        return JSONResponse(content={"error": "No song_name provided"}, status_code=400)
    
    conn = sqlite3.connect(_DB_PATH)
    c = conn.cursor()
    c.execute("INSERT INTO todos (song_name, added_date) VALUES (?, ?)", (song_name, _dt.datetime.now().isoformat()))
    conn.commit()
    new_id = c.lastrowid
    conn.close()
    return JSONResponse(content={"success": True, "id": new_id, "song_name": song_name, "status": "pending"})

@app.delete("/api/todos/{todo_id}")
async def delete_todo(todo_id: int):
    conn = sqlite3.connect(_DB_PATH)
    c = conn.cursor()
    c.execute("DELETE FROM todos WHERE id=?", (todo_id,))
    conn.commit()
    conn.close()
    return JSONResponse(content={"success": True})

# -------------------------------------------------------------------
# Dismissed Suggestions (persistent across sessions)
# -------------------------------------------------------------------
@app.get("/api/dismissed-suggestions")
async def get_dismissed():
    conn = sqlite3.connect(_DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("CREATE TABLE IF NOT EXISTS dismissed_suggestions (id INTEGER PRIMARY KEY AUTOINCREMENT, song_name TEXT UNIQUE, dismissed_date TEXT)")
    dismissed = [r["song_name"] for r in c.execute("SELECT song_name FROM dismissed_suggestions").fetchall()]
    conn.close()
    return JSONResponse(content=dismissed)

@app.post("/api/dismissed-suggestions")
async def dismiss_suggestion(req: Request):
    data = await req.json()
    song_name = data.get("song_name", "").strip()
    if not song_name:
        return JSONResponse(content={"error": "No song_name"}, status_code=400)
    conn = sqlite3.connect(_DB_PATH)
    c = conn.cursor()
    c.execute("CREATE TABLE IF NOT EXISTS dismissed_suggestions (id INTEGER PRIMARY KEY AUTOINCREMENT, song_name TEXT UNIQUE, dismissed_date TEXT)")
    c.execute("INSERT OR IGNORE INTO dismissed_suggestions (song_name, dismissed_date) VALUES (?, ?)", (song_name, _dt.datetime.now().isoformat()))
    conn.commit()
    conn.close()
    return JSONResponse(content={"success": True})

@app.get("/api/ai/briefing")
async def get_ai_briefing():
    try:
        from ai_agent import get_latest_briefing
        briefing = get_latest_briefing()
        if not briefing:
            return JSONResponse(content={"error": "Failed to generate briefing"}, status_code=500)
        return JSONResponse(content=briefing)
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)

@app.post("/api/ai/briefing/force")
async def force_ai_briefing():
    try:
        from ai_agent import generate_daily_briefing, get_latest_briefing
        try:
            briefing = generate_daily_briefing()
            if not briefing:
                raise Exception("generate_daily_briefing returned None.")
        except Exception as e:
            print(f"[API] Force briefing failed (Rate Limit?), falling back to cache. Error: {e}")
            briefing = get_latest_briefing()
            
        return JSONResponse(content=briefing)
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)

@app.post("/api/ai/chat")
async def chat_with_ai(req: Request):
    data = await req.json()
    message = data.get("message")
    history = data.get("history", [])
    if not message:
        return JSONResponse(content={"error": "No message provided"}, status_code=400)
        
    try:
        from ai_agent import chat_with_agent
        reply = chat_with_agent(message, history)
        return JSONResponse(content={"reply": reply})
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)

@app.post("/api/actions/run")
async def run_action_engine():
    """Run the Action Engine to evaluate data-driven triggers and populate To-Dos."""
    try:
        import sync_utils
        import sqlite3
        from pathlib import Path
        db_path = Path(__file__).resolve().parent / "analytics.db"
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        count = sync_utils.evaluate_action_triggers(cursor)
        conn.commit()
        conn.close()
        return JSONResponse(content={"success": True, "actions_created": count})
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)

@app.get("/api/server/sniper-status")
def get_sniper_status():
    key_path = r"C:\Dev\ssh-key-2026-05-07.key"
    server_ip = "152.70.23.171"
    if not os.path.exists(key_path):
        return {"status": "error", "message": f"SSH Key not found at {key_path}"}
    
    cmd = [
        "ssh", "-i", key_path, 
        "-o", "StrictHostKeyChecking=accept-new", 
        "-o", "ConnectTimeout=5", 
        "-o", "IdentitiesOnly=yes", 
        f"ubuntu@{server_ip}", 
        "systemctl is-active oci-sniper && echo --- LOGS --- && tail -n 100 /home/ubuntu/oci-sniper/sniper.log 2>/dev/null"
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=12, creationflags=CREATION_FLAGS)
        if res.returncode == 0 or "inactive" in res.stdout or "active" in res.stdout:
            stdout_str = res.stdout.strip()
            status_line = "inactive"
            logs_content = "No logs available."
            if "--- LOGS ---" in stdout_str:
                parts = stdout_str.split("--- LOGS ---", 1)
                status_line = parts[0].strip()
                logs_content = parts[1].strip()
            else:
                status_line = stdout_str
            
            is_active = status_line == "active"
            return {
                "status": "success" if is_active else "warning",
                "output": f"Service is-active: {status_line}\n\n--- RECENT LOGS ---\n{logs_content}"
            }
        else:
            status_line = res.stdout.strip() if res.stdout else "unknown"
            return {
                "status": "warning" if status_line == "inactive" else "error",
                "output": f"Service status: {status_line}\nErrors: {res.stderr}"
            }
    except subprocess.TimeoutExpired:
        return {"status": "error", "message": "Connection timed out"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

class ServerActionRequest(BaseModel):
    action: str

@app.post("/api/server/sniper-action")
def run_sniper_action(req: ServerActionRequest):
    key_path = r"C:\Dev\ssh-key-2026-05-07.key"
    server_ip = "152.70.23.171"
    if not os.path.exists(key_path):
        return {"status": "error", "message": f"SSH Key not found at {key_path}"}
    
    if req.action not in ("start", "stop", "restart"):
        return {"status": "error", "message": "Invalid action"}
        
    ssh_cmd = f"sudo systemctl {req.action} oci-sniper"
    
    cmd = [
        "ssh", "-i", key_path, 
        "-o", "StrictHostKeyChecking=accept-new", 
        "-o", "ConnectTimeout=5", 
        "-o", "IdentitiesOnly=yes", 
        f"ubuntu@{server_ip}", 
        ssh_cmd
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=12, creationflags=CREATION_FLAGS)
        if res.returncode == 0:
            return {"status": "success", "message": f"Service successfully {req.action}ed."}
        else:
            return {"status": "error", "message": f"Action failed: {res.stderr or res.stdout}"}
    except subprocess.TimeoutExpired:
        return {"status": "error", "message": "Connection timed out"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.get("/api/server/uploader-status")
def get_uploader_status():
    key_path = r"C:\Dev\ssh-key-2026-05-07.key"
    server_ip = "152.70.23.171"
    if not os.path.exists(key_path):
        return {"status": "error", "message": f"SSH Key not found at {key_path}"}
    
    cmd = [
        "ssh", "-i", key_path, 
        "-o", "StrictHostKeyChecking=accept-new", 
        "-o", "ConnectTimeout=5", 
        "-o", "IdentitiesOnly=yes", 
        f"ubuntu@{server_ip}", 
        "systemctl is-active oci-uploader && echo --- LOGS --- && tail -n 100 /home/ubuntu/meloscribe/uploader.log 2>/dev/null"
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=12, creationflags=CREATION_FLAGS)
        if res.returncode == 0 or "inactive" in res.stdout or "active" in res.stdout:
            stdout_str = res.stdout.strip()
            status_line = "inactive"
            logs_content = "No logs available."
            if "--- LOGS ---" in stdout_str:
                parts = stdout_str.split("--- LOGS ---", 1)
                status_line = parts[0].strip()
                logs_content = parts[1].strip()
            else:
                status_line = stdout_str
            
            is_active = status_line == "active"
            return {
                "status": "success" if is_active else "warning",
                "output": f"Service is-active: {status_line}\n\n--- RECENT LOGS ---\n{logs_content}"
            }
        else:
            status_line = res.stdout.strip() if res.stdout else "unknown"
            return {
                "status": "warning" if status_line == "inactive" else "error",
                "output": f"Service status: {status_line}\nErrors: {res.stderr}"
            }
    except subprocess.TimeoutExpired:
        return {"status": "error", "message": "Connection timed out"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.post("/api/server/uploader-action")
def run_uploader_action(req: ServerActionRequest):
    key_path = r"C:\Dev\ssh-key-2026-05-07.key"
    server_ip = "152.70.23.171"
    if not os.path.exists(key_path):
        return {"status": "error", "message": f"SSH Key not found at {key_path}"}
    
    if req.action not in ("start", "stop", "restart"):
        return {"status": "error", "message": "Invalid action"}
        
    ssh_cmd = f"sudo systemctl {req.action} oci-uploader"
    
    cmd = [
        "ssh", "-i", key_path, 
        "-o", "StrictHostKeyChecking=accept-new", 
        "-o", "ConnectTimeout=5", 
        "-o", "IdentitiesOnly=yes", 
        f"ubuntu@{server_ip}", 
        ssh_cmd
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=12, creationflags=CREATION_FLAGS)
        if res.returncode == 0:
            return {"status": "success", "message": f"Service successfully {req.action}ed."}
        else:
            return {"status": "error", "message": f"Action failed: {res.stderr or res.stdout}"}
    except subprocess.TimeoutExpired:
        return {"status": "error", "message": "Connection timed out"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.get("/api/server/queue")
def get_server_queue():
    key_path = r"C:\Dev\ssh-key-2026-05-07.key"
    server_ip = "152.70.23.171"
    if not os.path.exists(key_path):
        return {"status": "error", "message": f"SSH Key not found at {key_path}"}
        
    # Execute a safe python query script on the server to print the queue in JSON format,
    # then append the list of all staged files in the staging folder.
    py_query = "import sqlite3, json; conn=sqlite3.connect('/home/ubuntu/meloscribe/queue.db'); conn.row_factory=sqlite3.Row; cursor=conn.cursor(); cursor.execute('SELECT * FROM upload_queue ORDER BY datetime(schedule_time) DESC LIMIT 100'); print(json.dumps([dict(r) for r in cursor.fetchall()]))"
    
    cmd_str = f"python3 -c \"{py_query}\" && echo \"---FILES---\" && find /home/ubuntu/meloscribe/staging -type f 2>/dev/null"
    
    cmd = [
        "ssh", "-i", key_path, 
        "-o", "StrictHostKeyChecking=accept-new", 
        "-o", "ConnectTimeout=5", 
        "-o", "IdentitiesOnly=yes", 
        f"ubuntu@{server_ip}", 
        cmd_str
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=12, creationflags=CREATION_FLAGS)
        if res.returncode == 0:
            stdout_str = res.stdout.strip()
            
            # Split the queue JSON output and file list output
            parts = stdout_str.split("---FILES---")
            queue_data = []
            file_paths = []
            
            if len(parts) > 0:
                try:
                    queue_data = json.loads(parts[0].strip())
                except Exception as parse_err:
                    print(f"Error parsing queue json: {parse_err}")
            
            if len(parts) > 1:
                file_paths = [line.strip() for line in parts[1].strip().split("\n") if line.strip()]
            
            # Parse staged files structure: song_name -> { "tiktoks": [], "packages": [], "covers": [] }
            staged_files = {}
            for path in file_paths:
                if "/staging/" in path:
                    rel = path.split("/staging/", 1)[1]
                    path_parts = rel.split("/")
                    if len(path_parts) >= 3:
                        song_name = path_parts[0]
                        category = path_parts[1].lower()  # tiktoks, packages, covers
                        filename = path_parts[2]
                        
                        if song_name not in staged_files:
                            staged_files[song_name] = {"tiktoks": [], "packages": [], "covers": []}
                        
                        if category in staged_files[song_name]:
                            staged_files[song_name][category].append(filename)
            
            # Enrich queue items with their respective files
            for item in queue_data:
                song = item.get("song")
                mode = item.get("mode")
                profile = item.get("profile")
                item_files = []
                
                if song in staged_files:
                    song_data = staged_files[song]
                    if mode == "kofi":
                        item_files = song_data.get("packages", [])
                    else:
                        videos = song_data.get("tiktoks", [])
                        if profile == "tutorial":
                            item_files = [f for f in videos if "slow" in f.lower()]
                        else:
                            item_files = [f for f in videos if "slow" not in f.lower()]
                
                item["files"] = item_files
                
            return JSONResponse(content=queue_data)
        else:
            return JSONResponse(content=[])
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)

class RescheduleRequest(BaseModel):
    schedule_time: str

@app.post("/api/server/queue/{task_id}/reschedule")
def reschedule_server_task(task_id: int, req: RescheduleRequest):
    key_path = r"C:\Dev\ssh-key-2026-05-07.key"
    server_ip = "152.70.23.171"
    if not os.path.exists(key_path):
        return {"status": "error", "message": f"SSH Key not found at {key_path}"}
        
    try:
        datetime.strptime(req.schedule_time, "%Y-%m-%d %H:%M")
    except ValueError:
        return {"status": "error", "message": "Invalid schedule_time format. Must be 'YYYY-MM-DD HH:MM'."}
        
    ssh_cmd = f"sqlite3 /home/ubuntu/meloscribe/queue.db \"UPDATE upload_queue SET schedule_time = '{req.schedule_time}' WHERE id = {task_id};\""
    cmd = [
        "ssh", "-i", key_path, 
        "-o", "StrictHostKeyChecking=accept-new", 
        "-o", "ConnectTimeout=5", 
        "-o", "IdentitiesOnly=yes", 
        f"ubuntu@{server_ip}", 
        ssh_cmd
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=12, creationflags=CREATION_FLAGS)
        if res.returncode == 0:
            return {"status": "success", "message": f"Task {task_id} successfully rescheduled to {req.schedule_time}."}
        else:
            return {"status": "error", "message": f"Update failed: {res.stderr or res.stdout}"}
    except subprocess.TimeoutExpired:
        return {"status": "error", "message": "Connection timed out"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.delete("/api/server/queue/{task_id}")
def delete_server_task(task_id: int):
    key_path = r"C:\Dev\ssh-key-2026-05-07.key"
    server_ip = "152.70.23.171"
    if not os.path.exists(key_path):
        return {"status": "error", "message": f"SSH Key not found at {key_path}"}
        
    ssh_cmd = f"sqlite3 /home/ubuntu/meloscribe/queue.db \"DELETE FROM upload_queue WHERE id = {task_id};\""
    cmd = [
        "ssh", "-i", key_path, 
        "-o", "StrictHostKeyChecking=accept-new", 
        "-o", "ConnectTimeout=5", 
        "-o", "IdentitiesOnly=yes", 
        f"ubuntu@{server_ip}", 
        ssh_cmd
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=12, creationflags=CREATION_FLAGS)
        if res.returncode == 0:
            return {"status": "success", "message": f"Task {task_id} successfully deleted."}
        else:
            return {"status": "error", "message": f"Deletion failed: {res.stderr or res.stdout}"}
    except subprocess.TimeoutExpired:
        return {"status": "error", "message": "Connection timed out"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.get("/api/server/disk")
def get_server_disk():
    key_path = r"C:\Dev\ssh-key-2026-05-07.key"
    server_ip = "152.70.23.171"
    if not os.path.exists(key_path):
        return {"status": "error", "message": f"SSH Key not found at {key_path}"}
        
    cmd = [
        "ssh", "-i", key_path, 
        "-o", "StrictHostKeyChecking=accept-new", 
        "-o", "ConnectTimeout=5", 
        "-o", "IdentitiesOnly=yes", 
        f"ubuntu@{server_ip}", 
        "df -h /home/ubuntu"
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=12, creationflags=CREATION_FLAGS)
        return {"status": "success", "output": res.stdout}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.post("/api/server/sync-credentials")
def sync_credentials_route():
    key_path = r"C:\Dev\ssh-key-2026-05-07.key"
    server_ip = "152.70.23.171"
    if not os.path.exists(key_path):
        return {"status": "error", "message": f"SSH Key not found at {key_path}"}
        
    backend_dir = Path(__file__).resolve().parent
    files_to_sync = ["settings.json", "ig_tokens.json", "threads_tokens.json", "tiktok_tokens.json", "yt_tokens.json"]
    
    synced_files = []
    errors = []
    
    for fname in files_to_sync:
        local_path = backend_dir / fname
        if local_path.exists():
            cmd = [
                "scp", "-i", key_path,
                "-o", "StrictHostKeyChecking=accept-new",
                "-o", "ConnectTimeout=5",
                "-o", "IdentitiesOnly=yes",
                str(local_path),
                f"ubuntu@{server_ip}:/home/ubuntu/meloscribe/tools/meloscribe/backend/{fname}"
            ]
            try:
                res = subprocess.run(cmd, capture_output=True, text=True, timeout=10, creationflags=CREATION_FLAGS)
                if res.returncode == 0:
                    synced_files.append(fname)
                else:
                    errors.append(f"Failed {fname}: {res.stderr.strip()}")
            except Exception as e:
                errors.append(f"Error {fname}: {str(e)}")
                
    if errors:
        return {"status": "error", "message": f"Sync partially failed. Synced: {synced_files}. Errors: {errors}"}
    return {"status": "success", "message": f"Successfully synchronized credentials files to OCI: {synced_files}"}

def run_credentials_watcher():
    """Background thread that monitors credentials files and syncs them to the VM on change."""
    import time
    files_to_sync = ["settings.json", "ig_tokens.json", "threads_tokens.json", "tiktok_tokens.json", "yt_tokens.json"]
    backend_dir = Path(__file__).resolve().parent
    last_mtimes = {}

    # Initialize mtimes
    for fname in files_to_sync:
        fpath = backend_dir / fname
        if fpath.exists():
            last_mtimes[fname] = os.path.getmtime(fpath)
        else:
            last_mtimes[fname] = 0.0

    print("[Watcher] Started credentials auto-sync file watcher.")
    while True:
        time.sleep(3)
        changed = False
        for fname in files_to_sync:
            fpath = backend_dir / fname
            if fpath.exists():
                mtime = os.path.getmtime(fpath)
                if mtime != last_mtimes.get(fname, 0.0):
                    last_mtimes[fname] = mtime
                    changed = True
            elif fname in last_mtimes and last_mtimes[fname] != 0.0:
                last_mtimes[fname] = 0.0
                changed = True
        
        if changed:
            print("[Watcher] Credentials changed. Auto-syncing to VM...")
            try:
                sync_credentials_route()
            except Exception as e:
                print(f"[Watcher] Auto-sync failed: {e}")

threading.Thread(target=run_credentials_watcher, daemon=True).start()

# -------------------------------------------------------------------
# Stripe Webhook & R2 Secure Download System
# -------------------------------------------------------------------
def get_stripe_api_key():
    settings = load_settings()
    is_sandbox = settings.get("environment", "sandbox") == "sandbox"
    if is_sandbox:
        return settings.get("stripe_sandbox_secret_key") or os.environ.get("STRIPE_SECRET_KEY")
    else:
        return settings.get("stripe_live_secret_key") or os.environ.get("STRIPE_SECRET_KEY")

class CheckoutRequest(BaseModel):
    songId: str
    format: str = "full_arrangement"
    difficulty: str = "Original"
    language: str = "en"

@app.post("/api/checkout/create-session")
async def create_checkout_session(req: CheckoutRequest, request: Request):
    try:
        # Load songs list to find the song and price
        songs_path = r"c:\Dev\meloscribe-frontend\website\src\data\songs.json"
        if not os.path.exists(songs_path):
            songs_path = Path(__file__).resolve().parent / "songs.json"
            
        with open(songs_path, "r", encoding="utf-8") as f:
            songs_list = json.load(f)
            
        song = next((s for s in songs_list if s.get("id") == req.songId), None)
        if not song:
            raise HTTPException(status_code=404, detail="Song not found")
            
        if song.get("paymentsDisabled") or song.get("hidden"):
            raise HTTPException(status_code=403, detail="Product is no longer available")
            
        # Parse price and currency
        price_str = song.get("price", "6 €")
        currency = "eur"
        if "$" in price_str:
            currency = "usd"
        elif "£" in price_str:
            currency = "gbp"
            
        try:
            import re
            digits = re.findall(r"\d+", price_str)
            if digits:
                amount_cents = int(digits[0]) * 100
            else:
                amount_cents = 600
        except Exception:
            amount_cents = 600
            
        # Generate secure download hash
        import uuid
        download_hash = uuid.uuid4().hex
        
        # Determine origin for success/cancel URLs
        origin = request.headers.get("origin") or "https://meloscribe.dev"
        
        stripe.api_key = get_stripe_api_key()
        if not stripe.api_key:
            raise HTTPException(status_code=500, detail="Stripe API key is not configured")
            
        product_name = f"{song.get('title')} ({req.format.replace('_', ' ').title()} - {req.difficulty})"
        product_desc = "Includes PDF Sheet Music, MIDI Files, and Practice Video Tutorials"
        
        cover_image_path = song.get("coverImage", "")
        product_image = None
        if cover_image_path:
            import urllib.parse
            # URL encode path segments to handle spaces and special characters safely
            quoted_path = urllib.parse.quote(cover_image_path)
            product_image = f"https://meloscribe.dev{quoted_path}"
            
        session = stripe.checkout.Session.create(
            mode="payment",
            line_items=[{
                "price_data": {
                    "currency": currency,
                    "product_data": {
                        "name": product_name,
                        "description": product_desc,
                        "images": [product_image] if product_image else [],
                    },
                    "unit_amount": amount_cents,
                },
                "quantity": 1,
            }],
            invoice_creation={"enabled": True},
            billing_address_collection="required",
            success_url=f"{origin}/success?checkout_id={{CHECKOUT_SESSION_ID}}",
            cancel_url=f"{origin}/",
            metadata={
                "song_title": song.get("title"),
                "download_hash": download_hash,
                "locale": req.language
            }
        )
        return {"url": session.url}
    except Exception as e:
        print(f"[Stripe Checkout] Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/webhooks/stripe")
async def stripe_webhook(request: Request):
    payload = await request.body()
    sig_header = request.headers.get("Stripe-Signature")
    
    settings = load_settings()
    is_sandbox = settings.get("environment", "sandbox") == "sandbox"
    if is_sandbox:
        webhook_secret = settings.get("stripe_sandbox_webhook_secret") or settings.get("stripe_webhook_secret") or os.environ.get("STRIPE_WEBHOOK_SECRET")
    else:
        webhook_secret = settings.get("stripe_live_webhook_secret") or settings.get("stripe_webhook_secret") or os.environ.get("STRIPE_WEBHOOK_SECRET")
    
    stripe.api_key = get_stripe_api_key()
    
    try:
        if webhook_secret:
            event = stripe.Webhook.construct_event(
                payload, sig_header, webhook_secret
            )
        else:
            print("[Stripe Webhook] WARNING: stripe_webhook_secret not set. Proceeding without signature verification.")
            event = stripe.Event.construct_from(json.loads(payload.decode('utf-8')), stripe.api_key)
    except Exception as e:
        print(f"[Stripe Webhook] Signature verification failed: {e}")
        return JSONResponse(status_code=400, content={"error": str(e)})

    event_type = event.type
    data_object_raw = event.data.object
    data_object = data_object_raw.to_dict() if hasattr(data_object_raw, "to_dict") else data_object_raw

    try:
        db_path = Path(__file__).resolve().parent / "analytics.db"
        
        if event_type == "checkout.session.completed":
            session_id = data_object.get("id")
            payment_status = data_object.get("payment_status")
            
            if payment_status == "paid":
                metadata = data_object.get("metadata", {})
                song_title = metadata.get("song_title") or "Unknown Song"
                download_hash = metadata.get("download_hash")
                locale = metadata.get("locale") or "en"
                
                # Check availability in songs.json
                try:
                    songs_json_path = r"c:\Dev\meloscribe-frontend\website\src\data\songs.json"
                    if not os.path.exists(songs_json_path):
                        songs_json_path = Path(__file__).resolve().parent / "songs.json"
                    if os.path.exists(songs_json_path):
                        with open(songs_json_path, "r", encoding="utf-8") as f:
                            songs_db = json.load(f)
                        matched_song = next((s for s in songs_db if s.get("title") == song_title), None)
                        if matched_song:
                            if matched_song.get("paymentsDisabled") or matched_song.get("hidden"):
                                print(f"[Stripe Webhook] REJECTED purchase for '{song_title}' (paymentsDisabled or hidden).")
                                return JSONResponse(content={"error": "Product is no longer available"}, status_code=403)
                except Exception as check_err:
                    print(f"[Stripe Webhook] Error checking song availability: {check_err}")
                
                if not download_hash:
                    import uuid
                    download_hash = uuid.uuid4().hex

                customer_details = data_object.get("customer_details") or {}
                email = customer_details.get("email") or "customer@example.com"
                buyer_name = customer_details.get("name") or ""
                
                amount_total = float(data_object.get("amount_total", 0)) / 100.0
                currency = (data_object.get("currency") or "eur").upper()

                conn = sqlite3.connect(str(db_path))
                c = conn.cursor()
                c.execute(
                    "INSERT OR IGNORE INTO purchases (transaction_id, email, song_name, amount, currency, status, download_hash, locale, buyer_name) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (session_id, email, song_title, amount_total, currency, "🟢 Active", download_hash, locale, buyer_name)
                )
                is_new = c.rowcount > 0
                
                c.execute(
                    "UPDATE purchases SET locale = ?, buyer_name = ? WHERE transaction_id = ?",
                    (locale, buyer_name, session_id)
                )
                
                c.execute(
                    "INSERT INTO revenue (amount, currency, source, event_type, buyer, message, song_name) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (amount_total, currency, "stripe", event_type, email, f"Stripe txn {session_id}", song_title)
                )
                conn.commit()
                conn.close()
                print(f"[Stripe Webhook] Recorded purchase for '{song_title}' by {email} with hash {download_hash} (new: {is_new})")
                
                if is_new:
                    send_purchase_delivery_email(email, song_title, download_hash, locale)
                    
        elif event_type == "charge.refunded":
            charge_id = data_object.get("id")
            payment_intent_id = data_object.get("payment_intent")
            
            conn = sqlite3.connect(str(db_path))
            c = conn.cursor()
            c.execute("SELECT transaction_id FROM purchases WHERE transaction_id = ? OR transaction_id = ?", (payment_intent_id, charge_id))
            row = c.fetchone()
            
            if not row and payment_intent_id:
                try:
                    sessions = stripe.checkout.Session.list(payment_intent=payment_intent_id, limit=1)
                    if sessions and len(sessions.data) > 0:
                        stripe_session_id = sessions.data[0].id
                        c.execute("SELECT transaction_id FROM purchases WHERE transaction_id = ?", (stripe_session_id,))
                        row = c.fetchone()
                except Exception as search_err:
                    print(f"[Stripe Webhook] Error listing sessions for refund: {search_err}")
            
            if row:
                txn_id = row[0]
                c.execute("UPDATE purchases SET status = '🔴 Refunded' WHERE transaction_id = ?", (txn_id,))
                conn.commit()
                print(f"[Stripe Webhook] Refund recorded for transaction {txn_id}.")
            else:
                print(f"[Stripe Webhook] Warning: Could not find purchase for refund of payment intent {payment_intent_id} / charge {charge_id}.")
            conn.close()
            
    except Exception as e:
        print(f"[Stripe Webhook] Error processing webhook: {e}")
        return JSONResponse(status_code=500, content={"error": str(e)})

    return {"status": "success"}

def send_purchase_delivery_email(email: str, song_name: str, download_hash: str, locale: str = "en"):
    """Send purchase delivery email via Resend containing the download link."""
    api_key = load_settings().get("resend_api_key", "")
    if not api_key:
        print("[Notify] WARNING: resend_api_key not set in settings.json. Skipping purchase email.")
        return False
        
    download_url = f"https://meloscribe.dev/order/{download_hash}"
    
    html_body = f"""
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="font-family: 'Helvetica Neue', Arial, sans-serif; background: #0a0a0f; color: #e0e0e0; max-width: 520px; margin: 0 auto; padding: 32px 16px;">
  <div style="text-align: center; margin-bottom: 32px; background: #12121c; border: 1px solid #2a2a3e; border-radius: 16px; padding: 24px 16px;">
    <span style="font-size: 32px; font-weight: 900; color: #ffffff; letter-spacing: 3px; text-transform: lowercase;">melo<span style="color: #ff2d92;">scribe</span></span>
    <div style="height: 2px; width: 60px; margin: 8px auto 0 auto; background: #00f5ff; border-radius: 2px;"></div>
    <p style="color: #888899; font-size: 11px; margin: 8px 0 0 0; text-transform: uppercase; letter-spacing: 1.5px;">piano &amp; sheet music</p>
  </div>
  <div style="background: #12121c; border: 1px solid #2a2a3e; border-radius: 16px; padding: 32px;">
    <h2 style="color: #ffffff; font-size: 20px; margin-top: 0; margin-bottom: 16px; font-weight: 700; text-align: center;">🎹 Your Sheets Are Ready!</h2>
    <p style="color: #b0b0c0; line-height: 1.8; font-size: 15px;">Hey!</p>
    <p style="color: #b0b0c0; line-height: 1.8; font-size: 15px;">
      Thank you so much for your purchase and supporting my arrangements! Your learning package for <strong>{song_name}</strong> is ready.
    </p>
    <p style="color: #b0b0c0; line-height: 1.6; font-size: 15px;">Click the button below to download your sheet music (PDF), MIDI files, and practice video tutorials:</p>
    
    <div style="text-align: center; margin: 28px 0;">
      <a href="{download_url}" style="display: inline-block; background-color: #12121c; border: 2px solid #00f5d4; color: #00f5d4; font-family: 'Helvetica Neue', Arial, sans-serif; font-weight: 700; font-size: 15px; padding: 14px 32px; border-radius: 10px; text-decoration: none; text-shadow: 0 0 8px rgba(0,245,212,0.35);">Download Learning Package</a>
    </div>
    
    <p style="color: #888; font-size: 13px; text-align: center;">
      This download link is permanent. You can access it anytime to download updates or get your files.
    </p>
    
    <p style="color: #b0b0c0; line-height: 1.6; font-size: 15px; margin-top: 24px;">Happy practicing,<br>meloscribe</p>
  </div>
  <p style="text-align: center; font-size: 11px; color: #555; margin-top: 24px;">
    Need help? Reply directly to this email or visit <a href="https://meloscribe.dev" style="color: #00f5d4;">meloscribe.dev</a>
  </p>
</body>
</html>
"""

    try:
        resp = requests.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "from": "meloscribe <info@meloscribe.dev>",
                "to": [email],
                "subject": f"🎹 Your learning package for {song_name} is ready!",
                "html": html_body
            },
            timeout=10.0
        )
        if resp.status_code in (200, 201):
            print(f"[Notify] Purchase email sent successfully to {email}")
            return True
        else:
            print(f"[Notify] Failed to send purchase email: {resp.status_code} - {resp.text}")
            return False
    except Exception as err:
        print(f"[Notify] Resend exception: {err}")
        return False

@app.get("/api/order/hash-by-checkout")
def get_hash_by_checkout(checkout_id: str):
    db_path = Path(__file__).resolve().parent / "analytics.db"
    conn = sqlite3.connect(str(db_path), timeout=30.0)
    c = conn.cursor()
    c.execute("SELECT download_hash FROM purchases WHERE transaction_id = ?", (checkout_id,))
    row = c.fetchone()
    conn.close()
    
    if row:
        return {"download_hash": row[0]}
        
    if checkout_id.startswith("demo_"):
        return {"download_hash": f"demo_hash_{checkout_id}"}
        
    if checkout_id.startswith("cs_"):
        try:
            stripe.api_key = get_stripe_api_key()
            if stripe.api_key:
                session_raw = stripe.checkout.Session.retrieve(checkout_id)
                session = session_raw.to_dict() if hasattr(session_raw, "to_dict") else session_raw
                if session.get("payment_status") == "paid":
                    metadata = session.get("metadata") or {}
                    song_title = metadata.get("song_title") or "Unknown Song"
                    download_hash = metadata.get("download_hash")
                    locale = metadata.get("locale") or "en"
                    
                    if not download_hash:
                        import uuid
                        download_hash = uuid.uuid4().hex
                    
                    customer_details = session.get("customer_details") or {}
                    email = customer_details.get("email") or "customer@example.com"
                    buyer_name = customer_details.get("name") or ""
                    
                    amount_total = float(session.get("amount_total") or 0) / 100.0
                    currency = (session.get("currency") or "eur").upper()
                    
                    conn = sqlite3.connect(str(db_path), timeout=30.0)
                    c = conn.cursor()
                    c.execute(
                        "INSERT OR IGNORE INTO purchases (transaction_id, email, song_name, amount, currency, status, download_hash, locale, buyer_name) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (checkout_id, email, song_title, amount_total, currency, "🟢 Active", download_hash, locale, buyer_name)
                    )
                    is_new = c.rowcount > 0
                    
                    c.execute(
                        "UPDATE purchases SET locale = ?, buyer_name = ? WHERE transaction_id = ?",
                        (locale, buyer_name, checkout_id)
                    )
                    
                    c.execute(
                        "INSERT INTO revenue (amount, currency, source, event_type, buyer, message, song_name) VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (amount_total, currency, "stripe", "checkout.session.completed", email, f"Stripe txn {checkout_id} (API Fallback)", song_title)
                    )
                    conn.commit()
                    conn.close()
                    print(f"[Stripe API Fallback] Recorded purchase for '{song_title}' by {email} with hash {download_hash} (new: {is_new})")
                    
                    if is_new:
                        send_purchase_delivery_email(email, song_title, download_hash, locale)
                    
                    return {"download_hash": download_hash}
        except Exception as api_err:
            print(f"[Stripe API Fallback] Error verifying transaction: {api_err}")
            
    elif checkout_id.startswith("txn_"):
        try:
            s_settings = load_settings()
            is_sandbox = s_settings.get("environment", "sandbox") == "sandbox"
            api_key = s_settings.get("paddle_sandbox_api_key" if is_sandbox else "paddle_live_api_key")
            url_prefix = "https://sandbox-api.paddle.com" if is_sandbox else "https://api.paddle.com"
            
            if api_key:
                headers = {"Authorization": f"Bearer {api_key}"}
                tx_resp = requests.get(f"{url_prefix}/transactions/{checkout_id}", headers=headers, timeout=10.0)
                if tx_resp.status_code == 200:
                    tx_data = tx_resp.json().get("data", {})
                    status = tx_data.get("status")
                    if status == "completed":
                        customer_id = tx_data.get("customer_id")
                        email = "customer@example.com"
                        buyer_name = ""
                        if customer_id:
                            cust_resp = requests.get(f"{url_prefix}/customers/{customer_id}", headers=headers, timeout=10.0)
                            if cust_resp.status_code == 200:
                                cust_info = cust_resp.json().get("data") or {}
                                email = cust_info.get("email", email)
                                buyer_name = cust_info.get("name", "")
                        
                        if not buyer_name:
                            buyer_name = (tx_data.get("billing_details") or {}).get("name") or ""
                            
                        locale = tx_data.get("locale") or "en"
                        
                        custom_data = tx_data.get("custom_data") or {}
                        song_title = custom_data.get("song_title") or "Unknown Song"
                        download_hash = custom_data.get("download_hash")
                        if not download_hash:
                            import uuid
                            download_hash = uuid.uuid4().hex
                        
                        totals = (tx_data.get("details") or {}).get("totals") or {}
                        grand_total = float(totals.get("grand_total", 0)) / 100.0
                        currency = totals.get("currency_code", "EUR")
                        
                        conn = sqlite3.connect(str(db_path), timeout=30.0)
                        c = conn.cursor()
                        c.execute(
                            "INSERT OR IGNORE INTO purchases (transaction_id, email, song_name, amount, currency, status, download_hash, locale, buyer_name) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                            (checkout_id, email, song_title, grand_total, currency, status, download_hash, locale, buyer_name)
                        )
                        is_new = c.rowcount > 0
                        
                        c.execute(
                            "UPDATE purchases SET locale = ?, buyer_name = ? WHERE transaction_id = ?",
                            (locale, buyer_name, checkout_id)
                        )
                        
                        c.execute(
                            "INSERT INTO revenue (amount, currency, source, event_type, buyer, message, song_name) VALUES (?, ?, ?, ?, ?, ?, ?)",
                            (grand_total, currency, "paddle", "transaction.completed", email, f"Paddle txn {checkout_id} (API Fallback)", song_title)
                        )
                        conn.commit()
                        conn.close()
                        print(f"[Paddle API Fallback] Recorded purchase for '{song_title}' by {email} with hash {download_hash} (new: {is_new})")
                        
                        if is_new:
                            send_purchase_delivery_email(email, song_title, download_hash, locale)
                        
                        return {"download_hash": download_hash}
        except Exception as api_err:
            print(f"[Paddle API Fallback] Error verifying transaction: {api_err}")
            
    return JSONResponse(content={"error": "Transaction not found"}, status_code=404)

@app.get("/api/order/details")
def get_order_details(hash: str):
    db_path = Path(__file__).resolve().parent / "analytics.db"
    conn = sqlite3.connect(str(db_path), timeout=30.0)
    c = conn.cursor()
    c.execute("SELECT song_name, email, download_count, created_at, status FROM purchases WHERE download_hash = ?", (hash,))
    row = c.fetchone()
    conn.close()
    
    if not row and hash.startswith("demo_hash_"):
        return {
            "song_name": "Sweetest Rain",
            "email": "demo_customer@example.com",
            "download_count": 0,
            "created_at": "2026-07-01T12:00:00Z",
            "status": "completed"
        }
        
    if not row:
        return JSONResponse(content={"error": "Order not found"}, status_code=404)
        
    status = row[4] or ""
    if status in ("inactive", "refunded", "deactivated"):
        return JSONResponse(content={"error": "This order has been deactivated / refunded"}, status_code=403)
        
    return {
        "song_name": row[0],
        "email": row[1],
        "download_count": row[2],
        "created_at": row[3]
    }

@app.get("/api/download/request")
def request_download(hash: str, type: str):
    if type not in ("pdf", "zip", "midi", "midi_slow", "video", "video_slow"):
        return JSONResponse(content={"error": "Invalid download type"}, status_code=400)
        
    db_path = Path(__file__).resolve().parent / "analytics.db"
    conn = sqlite3.connect(str(db_path), timeout=30.0)
    c = conn.cursor()
    c.execute("SELECT song_name, download_count FROM purchases WHERE download_hash = ?", (hash,))
    row = c.fetchone()
    
    song_name = None
    download_count = 0
    
    if row:
        song_name = row[0]
        download_count = row[1]
    elif hash.startswith("demo_hash_"):
        song_name = "Sweetest Rain"
        download_count = 0
        print(f"[Download Request] Sandbox hash '{hash}' resolved to '{song_name}'")
        
    if not song_name:
        conn.close()
        return JSONResponse(content={"error": "Order not found"}, status_code=404)
        
    if download_count >= 20:
        conn.close()
        return JSONResponse(content={"error": "Download limit reached (maximum 20 downloads allowed)"}, status_code=403)
        
    if row:
        new_count = download_count + 1
        c.execute("UPDATE purchases SET download_count = ? WHERE download_hash = ?", (new_count, hash))
        conn.commit()
    conn.close()
    
    r2_account_id = settings.get("r2_account_id") or os.environ.get("R2_ACCOUNT_ID")
    r2_access_key = settings.get("r2_access_key_id") or os.environ.get("R2_ACCESS_KEY_ID")
    r2_secret_key = settings.get("r2_secret_access_key") or os.environ.get("R2_SECRET_ACCESS_KEY")
    r2_bucket = settings.get("r2_bucket_name", "meloscribe-sheets") or os.environ.get("R2_BUCKET_NAME", "meloscribe-sheets")
    
    if not r2_account_id or not r2_access_key or not r2_secret_key:
        print("[Download Request] R2 credentials missing, using demo redirect fallback.")
        if type == "pdf":
            suffix = f"/{song_name}.pdf"
        elif type == "midi":
            suffix = f"/{song_name}.mid"
        elif type == "midi_slow":
            suffix = f"/{song_name} slow.mid"
        elif type == "video":
            suffix = f"/{song_name}.mp4"
        elif type == "video_slow":
            suffix = f"/{song_name} slow.mp4"
        else:
            suffix = " Full Package.zip"
        return {
            "download_url": f"https://example.com/demo-packages/{song_name}{suffix}",
            "message": "Demo mode: R2 credentials are not configured in settings.json"
        }
        
    try:
        import boto3
        from botocore.config import Config
        
        if type == "pdf":
            file_key = f"{song_name}/{song_name}.pdf"
        elif type == "midi":
            file_key = f"{song_name}/{song_name}.mid"
        elif type == "midi_slow":
            file_key = f"{song_name}/{song_name} slow.mid"
        elif type == "video":
            file_key = f"{song_name}/{song_name}.mp4"
        elif type == "video_slow":
            file_key = f"{song_name}/{song_name} slow.mp4"
        else:
            file_key = f"{song_name} Full Package.zip"
        
        s3 = boto3.client(
            's3',
            endpoint_url=f'https://{r2_account_id}.r2.cloudflarestorage.com',
            aws_access_key_id=r2_access_key,
            aws_secret_access_key=r2_secret_key,
            region_name='auto',
            config=Config(signature_version='s3v4')
        )
        
        filename = file_key.split('/')[-1]
        presigned_url = s3.generate_presigned_url(
            ClientMethod='get_object',
            Params={
                'Bucket': r2_bucket,
                'Key': file_key,
                'ResponseContentDisposition': f'attachment; filename="{filename}"'
            },
            ExpiresIn=900
        )
        
        return {"download_url": presigned_url}
    except Exception as e:
        print(f"Failed to generate presigned R2 URL: {e}")
        return JSONResponse(content={"error": f"Failed to generate download URL: {str(e)}"}, status_code=500)

@app.get("/api/download/verify")
def verify_download(checkout_id: str):
    db_path = Path(__file__).resolve().parent / "analytics.db"
    conn = sqlite3.connect(str(db_path))
    c = conn.cursor()
    c.execute("SELECT song_name FROM purchases WHERE transaction_id = ? AND status = 'completed'", (checkout_id,))
    row = c.fetchone()
    conn.close()
    
    song_name = None
    if row:
        song_name = row[0]
    elif checkout_id.startswith("demo_"):
        song_name = "Sweetest Rain"
        print(f"[Download Verify] Sandbox checkout '{checkout_id}' resolved to '{song_name}'")
        
    if not song_name:
        return JSONResponse(content={"error": "Purchase not found or not completed"}, status_code=403)
        
    r2_account_id = settings.get("r2_account_id") or os.environ.get("R2_ACCOUNT_ID")
    r2_access_key = settings.get("r2_access_key_id") or os.environ.get("R2_ACCESS_KEY_ID")
    r2_secret_key = settings.get("r2_secret_access_key") or os.environ.get("R2_SECRET_ACCESS_KEY")
    r2_bucket = settings.get("r2_bucket_name", "meloscribe-assets") or os.environ.get("R2_BUCKET_NAME", "meloscribe-assets")
    
    if not r2_account_id or not r2_access_key or not r2_secret_key:
        print("[Download Verify] R2 credentials missing, using demo redirect fallback.")
        return {
            "files": [],
            "message": "Demo mode: R2 credentials are not configured in settings.json"
        }
        
    try:
        import boto3
        from botocore.config import Config
        
        s3 = boto3.client(
            's3',
            endpoint_url=f'https://{r2_account_id}.r2.cloudflarestorage.com',
            aws_access_key_id=r2_access_key,
            aws_secret_access_key=r2_secret_key,
            region_name='auto',
            config=Config(signature_version='s3v4')
        )

        # Individual files in the song folder on R2:
        #   {song_name}/{song_name}.pdf
        #   {song_name}/{song_name}.mid
        #   {song_name}/{song_name} slow.mid
        #   {song_name}/{song_name}.mp4
        #   {song_name}/{song_name} slow.mp4
        file_specs = [
            {"key": f"{song_name}/{song_name}.pdf",       "label": "Sheet Music (PDF)",          "type": "pdf"},
            {"key": f"{song_name}/{song_name}.mid",       "label": "MIDI – Normal Speed",         "type": "midi"},
            {"key": f"{song_name}/{song_name} slow.mid",  "label": "MIDI – Slow Practice",        "type": "midi"},
            {"key": f"{song_name}/{song_name}.mp4",       "label": "Practice Video – Normal Speed", "type": "video"},
            {"key": f"{song_name}/{song_name} slow.mp4",  "label": "Practice Video – Slow",       "type": "video"},
        ]

        files = []
        for spec in file_specs:
            try:
                # Verify the object exists before generating a URL
                s3.head_object(Bucket=r2_bucket, Key=spec["key"])
                url = s3.generate_presigned_url(
                    ClientMethod='get_object',
                    Params={'Bucket': r2_bucket, 'Key': spec["key"]},
                    ExpiresIn=900  # 15 minutes
                )
                files.append({"label": spec["label"], "url": url, "type": spec["type"]})
            except Exception:
                # File doesn't exist in R2 yet — skip gracefully
                pass
        
        if not files:
            return JSONResponse(content={"error": "No download files found for this purchase. Please contact support."}, status_code=404)
        
        return {"files": files, "song_name": song_name}
    except Exception as e:
        print(f"Failed to generate presigned R2 URLs: {e}")
        return JSONResponse(content={"error": f"Failed to generate download URLs: {str(e)}"}, status_code=500)


# -------------------------------------------------------------------
# Notify-Me System — E-Mail Opt-In for new sheet music alerts
# -------------------------------------------------------------------

class NotifySubscribeRequest(BaseModel):
    email: str

def _send_confirmation_email(email: str, token: str):
    """Send double opt-in confirmation email via Resend."""
    api_key = settings.get("resend_api_key", "")
    if not api_key:
        print("[Notify] WARNING: resend_api_key not set in settings.json. Skipping email.")
        return False
    
    confirm_url = f"https://api.meloscribe.dev/api/notify/confirm?token={token}"
    unsubscribe_url = f"https://api.meloscribe.dev/api/notify/unsubscribe?token={token}"
    
    html_body = f"""
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="font-family: 'Helvetica Neue', Arial, sans-serif; background: #0a0a0f; color: #e0e0e0; max-width: 520px; margin: 0 auto; padding: 32px 16px;">
  <div style="text-align: center; margin-bottom: 32px; background: #12121c; border: 1px solid #2a2a3e; border-radius: 16px; padding: 24px 16px;">
    <span style="font-size: 32px; font-weight: 900; color: #ffffff; letter-spacing: 3px; text-transform: lowercase;">melo<span style="color: #ff2d92;">scribe</span></span>
    <div style="height: 2px; width: 60px; margin: 8px auto 0 auto; background: #00f5ff; border-radius: 2px;"></div>
    <p style="color: #888899; font-size: 11px; margin: 8px 0 0 0; text-transform: uppercase; letter-spacing: 1.5px;">piano &amp; sheet music</p>
  </div>
  <div style="background: #12121c; border: 1px solid #2a2a3e; border-radius: 16px; padding: 32px;">
    <p style="color: #b0b0c0; line-height: 1.8; font-size: 15px;">Hey!</p>
    <p style="color: #b0b0c0; line-height: 1.8; font-size: 15px;">
      Thanks for your interest! Please confirm that you want to receive email notifications
      whenever new sheet music or practice assets are dropped on meloscribe.dev.
    </p>
    <p style="color: #b0b0c0; line-height: 1.6; font-size: 15px;">Click the link below to confirm your email:</p>
    <div style="text-align: center; margin: 28px 0;">
      <a href="{confirm_url}" style="display: inline-block; background-color: #12121c; border: 2px solid #00f5d4; color: #00f5d4; font-family: 'Helvetica Neue', Arial, sans-serif; font-weight: 700; font-size: 15px; padding: 14px 32px; border-radius: 10px; text-decoration: none; text-shadow: 0 0 8px rgba(0,245,212,0.35);">Confirm Subscription</a>
    </div>
    <p style="color: #888; font-size: 13px; text-align: center;">
      If you didn&apos;t request this, you can safely ignore this email. You won&apos;t be subscribed unless you click the link above.
    </p>
    <p style="color: #b0b0c0; line-height: 1.6; font-size: 15px; margin-top: 24px;">Best,<br>meloscribe</p>
  </div>
  <p style="text-align: center; font-size: 11px; color: #555; margin-top: 24px;">
    Unsubscribe anytime: <a href="{unsubscribe_url}" style="color: #555;">click here</a>
  </p>
</body>
</html>
"""

    try:
        resp = requests.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "from": "meloscribe <info@meloscribe.dev>",
                "to": [email],
                "subject": "Confirm your sheet music notifications — meloscribe",
                "html": html_body,
            },
            timeout=10
        )
        if resp.status_code in (200, 201):
            print(f"[Notify] Confirmation email sent to {email}")
            return True
        else:
            print(f"[Notify] Resend API error {resp.status_code}: {resp.text}")
            return False
    except Exception as e:
        print(f"[Notify] Email send failed: {e}")
        return False


@app.post("/api/notify/subscribe")
async def notify_subscribe(req: NotifySubscribeRequest):
    """Register email for sheet music notifications. Sends a double opt-in confirmation."""
    import uuid as _uuid
    
    email = req.email.strip().lower()
    if not email or "@" not in email or "." not in email.split("@")[-1]:
        return JSONResponse(content={"error": "Invalid email address."}, status_code=400)
    
    token = _uuid.uuid4().hex
    db_path = Path(__file__).resolve().parent / "analytics.db"
    
    try:
        conn = sqlite3.connect(str(db_path), timeout=30.0)
        conn.execute("PRAGMA journal_mode=WAL")
        c = conn.cursor()
        
        # Check if already active
        c.execute("SELECT status FROM notify_subscribers WHERE email = ?", (email,))
        row = c.fetchone()
        if row:
            if row[0] == "active":
                return {"status": "already_active", "message": "This email is already subscribed."}
            else:
                # Re-send confirmation (update token)
                c.execute("UPDATE notify_subscribers SET token = ?, status = 'pending' WHERE email = ?", (token, email))
        else:
            c.execute(
                "INSERT INTO notify_subscribers (email, token, status) VALUES (?, ?, 'pending')",
                (email, token)
            )
        conn.commit()
        conn.close()
    except Exception as e:
        return JSONResponse(content={"error": f"Database error: {str(e)}"}, status_code=500)
    
    # Send confirmation email in background
    def _send():
        _send_confirmation_email(email, token)
    threading.Thread(target=_send, daemon=True).start()
    
    return {"status": "pending", "message": "Confirmation email sent. Please check your inbox."}


@app.get("/api/notify/confirm")
def notify_confirm(token: str):
    """Activate a subscriber after clicking the confirmation link."""
    db_path = Path(__file__).resolve().parent / "analytics.db"
    try:
        conn = sqlite3.connect(str(db_path), timeout=30.0)
        c = conn.cursor()
        c.execute(
            "UPDATE notify_subscribers SET status = 'active', confirmed_at = CURRENT_TIMESTAMP WHERE token = ?",
            (token,)
        )
        if c.rowcount == 0:
            conn.close()
            return HTMLResponse(content="""
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>meloscribe</title>
  <style>
    body {
      background: linear-gradient(135deg, #0a0a14 0%, #050508 100%);
      color: #ffffff;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      margin: 0; padding: 0;
      display: flex; justify-content: center; align-items: center;
      min-height: 100vh; overflow: hidden;
    }
    .glow-orb {
      position: absolute; width: 400px; height: 400px; border-radius: 50%;
      filter: blur(150px); z-index: 1; opacity: 0.15;
    }
    .orb-1 { background: #ff4d8d; top: -150px; left: -150px; }
    .orb-2 { background: #00f5d4; bottom: -150px; right: -150px; }
    .container {
      background: rgba(18, 18, 28, 0.45);
      backdrop-filter: blur(16px); -webkit-backdrop-filter: blur(16px);
      border: 1px solid rgba(255, 255, 255, 0.08); border-radius: 24px;
      padding: 48px; text-align: center; max-width: 420px; width: 90%; z-index: 10;
      box-shadow: 0 20px 40px rgba(0, 0, 0, 0.5);
      animation: fadeIn 0.6s ease-out;
    }
    @keyframes fadeIn {
      from { opacity: 0; transform: scale(0.95); }
      to { opacity: 1; transform: scale(1); }
    }
    .title {
      font-size: 28px; font-weight: 700; letter-spacing: 1px; margin-bottom: 24px;
      background: linear-gradient(135deg, #ffffff 40%, #a0a0b0 100%);
      -webkit-background-clip: text; -webkit-text-fill-color: transparent;
    }
    .badge {
      display: inline-block; padding: 6px 16px; border-radius: 9999px;
      font-size: 13px; font-weight: 600; text-transform: uppercase; letter-spacing: 1px;
      background: rgba(255, 77, 141, 0.1); border: 1px solid rgba(255, 77, 141, 0.25);
      color: #ff4d8d; text-shadow: 0 0 10px rgba(255, 77, 141, 0.3); margin-bottom: 20px;
    }
    .desc { color: #b0b0c0; font-size: 15px; line-height: 1.6; margin-bottom: 36px; }
    .btn {
      display: inline-block; width: 100%; padding: 14px 0; border-radius: 12px;
      background: rgba(255, 255, 255, 0.08); border: 1px solid rgba(255, 255, 255, 0.15);
      color: #ffffff; font-weight: 700; font-size: 15px; text-decoration: none;
      transition: all 0.3s ease;
    }
    .btn:hover { background: rgba(255, 255, 255, 0.15); transform: translateY(-2px); }
  </style>
</head>
<body>
  <div class="glow-orb orb-1"></div>
  <div class="glow-orb orb-2"></div>
  <div class="container">
    <div class="badge">Error</div>
    <div class="title">Expired Link</div>
    <div class="desc">Invalid or expired confirmation link. Please request a new subscription.</div>
    <a href="https://meloscribe.dev" class="btn">Go to meloscribe.dev</a>
  </div>
</body>
</html>""", status_code=404)
        conn.commit()
        conn.close()
        return HTMLResponse(content="""
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>meloscribe</title>
  <style>
    body {
      background: linear-gradient(135deg, #0a0a14 0%, #050508 100%);
      color: #ffffff;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      margin: 0; padding: 0;
      display: flex; justify-content: center; align-items: center;
      min-height: 100vh; overflow: hidden;
    }
    .glow-orb {
      position: absolute; width: 400px; height: 400px; border-radius: 50%;
      filter: blur(150px); z-index: 1; opacity: 0.15;
    }
    .orb-1 { background: #00f5d4; top: -150px; left: -150px; }
    .orb-2 { background: #ff4d8d; bottom: -150px; right: -150px; }
    .container {
      background: rgba(18, 18, 28, 0.45);
      backdrop-filter: blur(16px); -webkit-backdrop-filter: blur(16px);
      border: 1px solid rgba(255, 255, 255, 0.08); border-radius: 24px;
      padding: 48px; text-align: center; max-width: 420px; width: 90%; z-index: 10;
      box-shadow: 0 20px 40px rgba(0, 0, 0, 0.5);
      animation: fadeIn 0.6s ease-out;
    }
    @keyframes fadeIn {
      from { opacity: 0; transform: scale(0.95); }
      to { opacity: 1; transform: scale(1); }
    }
    .icon-wrap {
      margin-bottom: 24px; display: flex; justify-content: center;
    }
    .check-icon {
      width: 64px; height: 64px; color: #00f5d4;
      filter: drop-shadow(0 0 12px rgba(0, 245, 212, 0.5));
      animation: scaleUp 0.5s cubic-bezier(0.175, 0.885, 0.32, 1.275);
    }
    @keyframes scaleUp {
      0% { transform: scale(0); opacity: 0; }
      100% { transform: scale(1); opacity: 1; }
    }
    .title {
      font-size: 28px; font-weight: 700; letter-spacing: 1px; margin-bottom: 24px;
      background: linear-gradient(135deg, #ffffff 40%, #a0a0b0 100%);
      -webkit-background-clip: text; -webkit-text-fill-color: transparent;
    }
    .badge {
      display: inline-block; padding: 6px 16px; border-radius: 9999px;
      font-size: 13px; font-weight: 600; text-transform: uppercase; letter-spacing: 1px;
      background: rgba(0, 245, 212, 0.1); border: 1px solid rgba(0, 245, 212, 0.25);
      color: #00f5d4; text-shadow: 0 0 10px rgba(0, 245, 212, 0.3); margin-bottom: 20px;
    }
    .desc { color: #b0b0c0; font-size: 15px; line-height: 1.6; margin-bottom: 36px; }
    .btn {
      display: inline-block; width: 100%; padding: 14px 0; border-radius: 12px;
      background: rgba(18, 18, 28, 0.45); border: 1px solid rgba(0, 245, 212, 0.45);
      color: #00f5d4; font-weight: 700; font-size: 15px; text-decoration: none;
      transition: all 0.3s ease;
      box-shadow: 0 4px 20px rgba(0, 245, 212, 0.05);
    }
    .btn:hover {
      background: rgba(0, 245, 212, 0.1);
      border-color: #00f5d4;
      box-shadow: 0 0 15px rgba(0, 245, 212, 0.25);
      transform: translateY(-2px);
    }
  </style>
</head>
<body>
  <div class="glow-orb orb-1"></div>
  <div class="glow-orb orb-2"></div>
  <div class="container">
    <div class="icon-wrap">
      <svg class="check-icon" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor">
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
      </svg>
    </div>
    <div class="badge">Success</div>
    <div class="title">You're in!</div>
    <div class="desc">You'll be notified when new sheet music and practice assets drop on meloscribe.dev.</div>
    <a href="https://meloscribe.dev" class="btn">Go to meloscribe.dev</a>
  </div>
</body>
</html>""")
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)


@app.get("/api/notify/unsubscribe")
def notify_unsubscribe(token: str):
    """Remove a subscriber immediately by token. No login required."""
    db_path = Path(__file__).resolve().parent / "analytics.db"
    try:
        conn = sqlite3.connect(str(db_path), timeout=30.0)
        c = conn.cursor()
        c.execute("DELETE FROM notify_subscribers WHERE token = ?", (token,))
        found = c.rowcount > 0
        conn.commit()
        conn.close()
        
        badge_text = "Unsubscribed" if found else "Not Found"
        title_text = "Unsubscribed" if found else "Link Expired"
        desc_text = "You will no longer receive sheet music drops or email alerts." if found else "This unsubscribe link is invalid or has already been used."
        badge_color = "#ff4d8d" if found else "#b0b0c0"
        badge_bg = "rgba(255, 77, 141, 0.1)" if found else "rgba(255, 255, 255, 0.05)"
        badge_border = "rgba(255, 77, 141, 0.25)" if found else "rgba(255, 255, 255, 0.15)"
        
        return HTMLResponse(content=f"""
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>meloscribe</title>
  <style>
    body {{
      background: linear-gradient(135deg, #0a0a14 0%, #050508 100%);
      color: #ffffff;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      margin: 0; padding: 0;
      display: flex; justify-content: center; align-items: center;
      min-height: 100vh; overflow: hidden;
    }}
    .glow-orb {{
      position: absolute; width: 400px; height: 400px; border-radius: 50%;
      filter: blur(150px); z-index: 1; opacity: 0.15;
    }}
    .orb-1 {{ background: #ff4d8d; top: -150px; left: -150px; }}
    .orb-2 {{ background: #00f5d4; bottom: -150px; right: -150px; }}
    .container {{
      background: rgba(18, 18, 28, 0.45);
      backdrop-filter: blur(16px); -webkit-backdrop-filter: blur(16px);
      border: 1px solid rgba(255, 255, 255, 0.08); border-radius: 24px;
      padding: 48px; text-align: center; max-width: 420px; width: 90%; z-index: 10;
      box-shadow: 0 20px 40px rgba(0, 0, 0, 0.5);
      animation: fadeIn 0.6s ease-out;
    }}
    @keyframes fadeIn {{
      from {{ opacity: 0; transform: scale(0.95); }}
      to {{ opacity: 1; transform: scale(1); }}
    }}
    .title {{
      font-size: 28px; font-weight: 700; letter-spacing: 1px; margin-bottom: 24px;
      background: linear-gradient(135deg, #ffffff 40%, #a0a0b0 100%);
      -webkit-background-clip: text; -webkit-text-fill-color: transparent;
    }}
    .badge {{
      display: inline-block; padding: 6px 16px; border-radius: 9999px;
      font-size: 13px; font-weight: 600; text-transform: uppercase; letter-spacing: 1px;
      background: {badge_bg}; border: 1px solid {badge_border};
      color: {badge_color}; text-shadow: 0 0 10px rgba(255, 77, 141, 0.2); margin-bottom: 20px;
    }}
    .desc {{ color: #b0b0c0; font-size: 15px; line-height: 1.6; margin-bottom: 36px; }}
    .btn {{
      display: inline-block; width: 100%; padding: 14px 0; border-radius: 12px;
      background: rgba(255, 255, 255, 0.08); border: 1px solid rgba(255, 255, 255, 0.15);
      color: #ffffff; font-weight: 700; font-size: 15px; text-decoration: none;
      transition: all 0.3s ease;
    }}
    .btn:hover {{ background: rgba(255, 255, 255, 0.15); transform: translateY(-2px); }}
  </style>
</head>
<body>
  <div class="glow-orb orb-1"></div>
  <div class="glow-orb orb-2"></div>
  <div class="container">
    <div class="badge">{badge_text}</div>
    <div class="title">{title_text}</div>
    <div class="desc">{desc_text}</div>
    <a href="https://meloscribe.dev" class="btn">Go to meloscribe.dev</a>
  </div>
</body>
</html>""")
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)


@app.get("/api/notify/subscribers")
def notify_list_subscribers():
    """Admin endpoint: list all active subscribers."""
    import platform
    if platform.system() == "Windows":
        try:
            import requests
            response = requests.get("https://api.meloscribe.dev/api/notify/subscribers", timeout=3.5)
            if response.status_code == 200:
                return response.json()
        except Exception as proxy_err:
            print(f"[Subscribers Proxy] Proxy failed, falling back to local DB: {proxy_err}")

    db_path = Path(__file__).resolve().parent / "analytics.db"
    try:
        conn = sqlite3.connect(str(db_path), timeout=30.0)
        c = conn.cursor()
        c.execute("SELECT email, status, created_at, confirmed_at FROM notify_subscribers ORDER BY created_at DESC")
        rows = [{"email": r[0], "status": r[1], "created_at": r[2], "confirmed_at": r[3]} for r in c.fetchall()]
        conn.close()
        return {"subscribers": rows, "total": len(rows), "active": sum(1 for r in rows if r["status"] == "active")}
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)

# ── SUGGESTIONS ENDPOINTS ───────────────────────────────────────────────────

class NewSuggestion(BaseModel):
    title: str
    artist: str

@app.get("/api/public/suggestions")
def get_suggestions():
    db_path = Path(__file__).resolve().parent / "analytics.db"
    try:
        conn = sqlite3.connect(str(db_path), timeout=30.0)
        c = conn.cursor()
        c.execute("SELECT id, title, artist, votes, created_at FROM suggestions ORDER BY votes DESC, created_at DESC")
        rows = [{"id": r[0], "title": r[1], "artist": r[2], "votes": r[3], "created_at": r[4]} for r in c.fetchall()]
        conn.close()
        return rows
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)

@app.post("/api/public/suggestions")
def create_suggestion(sug: NewSuggestion):
    db_path = Path(__file__).resolve().parent / "analytics.db"
    import uuid
    from datetime import datetime
    sug_id = str(uuid.uuid4())
    created_at = datetime.now().isoformat()
    try:
        conn = sqlite3.connect(str(db_path), timeout=30.0)
        c = conn.cursor()
        # Check if a duplicate exists using direct matching (for backend safety)
        c.execute("SELECT id, title, artist, votes FROM suggestions WHERE LOWER(title) = ? AND LOWER(artist) = ?", (sug.title.strip().lower(), sug.artist.strip().lower()))
        existing = c.fetchone()
        if existing:
            # Increment votes of existing
            new_votes = existing[3] + 1
            c.execute("UPDATE suggestions SET votes = ? WHERE id = ?", (new_votes, existing[0]))
            conn.commit()
            conn.close()
            return {"id": existing[0], "title": existing[1], "artist": existing[2], "votes": new_votes, "created_at": created_at}
            
        c.execute("INSERT INTO suggestions (id, title, artist, votes, created_at) VALUES (?, ?, ?, ?, ?)",
                  (sug_id, sug.title.strip(), sug.artist.strip(), 1, created_at))
        conn.commit()
        conn.close()
        return {"id": sug_id, "title": sug.title.strip(), "artist": sug.artist.strip(), "votes": 1, "created_at": created_at}
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)

@app.post("/api/public/suggestions/{sug_id}/vote")
def upvote_suggestion(sug_id: str):
    db_path = Path(__file__).resolve().parent / "analytics.db"
    try:
        conn = sqlite3.connect(str(db_path), timeout=30.0)
        c = conn.cursor()
        c.execute("SELECT votes FROM suggestions WHERE id = ?", (sug_id,))
        row = c.fetchone()
        if not row:
            conn.close()
            return JSONResponse(content={"error": "Suggestion not found"}, status_code=404)
        new_votes = row[0] + 1
        c.execute("UPDATE suggestions SET votes = ? WHERE id = ?", (new_votes, sug_id))
        conn.commit()
        conn.close()
        return {"id": sug_id, "votes": new_votes}
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)

@app.post("/api/public/suggestions/{sug_id}/unvote")
def downvote_suggestion(sug_id: str):
    db_path = Path(__file__).resolve().parent / "analytics.db"
    try:
        conn = sqlite3.connect(str(db_path), timeout=30.0)
        c = conn.cursor()
        c.execute("SELECT votes FROM suggestions WHERE id = ?", (sug_id,))
        row = c.fetchone()
        if not row:
            conn.close()
            return JSONResponse(content={"error": "Suggestion not found"}, status_code=404)
        new_votes = max(0, row[0] - 1)
        c.execute("UPDATE suggestions SET votes = ? WHERE id = ?", (new_votes, sug_id))
        conn.commit()
        conn.close()
        return {"id": sug_id, "votes": new_votes}
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)

@app.get("/api/public/stats")
def get_public_stats():
    # Helper to return dynamic stats count
    db_path = Path(__file__).resolve().parent / "analytics.db"
    
    # Increment website page views for today
    try:
        conn = sqlite3.connect(str(db_path), timeout=30.0)
        c = conn.cursor()
        from datetime import date
        today_str = date.today().isoformat()
        
        # Check if entry exists for 'website' today
        c.execute("SELECT profile_views FROM channel_insights WHERE platform = ? AND date = ?", ("website", today_str))
        row = c.fetchone()
        if row:
            c.execute("UPDATE channel_insights SET profile_views = profile_views + 1 WHERE platform = ? AND date = ?", ("website", today_str))
        else:
            c.execute("INSERT INTO channel_insights (platform, date, followers, profile_views, website_clicks) VALUES (?, ?, 0, 1, 0)", ("website", today_str))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[Stats Track] Error logging website visitor views: {e}")
        
    try:
        conn = sqlite3.connect(str(db_path))
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM purchases")
        customers = c.fetchone()[0]
        
        # Get latest followers count sum across all platforms
        c.execute("""
            SELECT SUM(followers) 
            FROM (
                SELECT followers FROM channel_insights 
                WHERE (platform, date) IN (
                    SELECT platform, MAX(date) FROM channel_insights GROUP BY platform
                )
            )
        """)
        row = c.fetchone()
        db_followers = row[0] if (row and row[0] is not None) else 0
        
        # Count total downloads (Ko-Fi legacy + Paddle purchases)
        downloads = 0
        try:
            c.execute("SELECT COUNT(*) FROM revenue")
            downloads = c.fetchone()[0]
        except Exception:
            pass
            
        conn.close()
        
        return {
            "customers": max(14, customers),
            "followers": max(75, db_followers),
            "downloads": max(14, downloads)
        }
    except Exception:
        return {"customers": 14, "followers": 75, "downloads": 14}

@app.delete("/api/public/suggestions/{sug_id}")
def delete_suggestion(sug_id: str):
    db_path = Path(__file__).resolve().parent / "analytics.db"
    try:
        conn = sqlite3.connect(str(db_path), timeout=30.0)
        c = conn.cursor()
        c.execute("DELETE FROM suggestions WHERE id = ?", (sug_id,))
        conn.commit()
        conn.close()
        return {"status": "success"}
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)

@app.get("/api/paddle/sales")
def get_paddle_sales():
    db_path = Path(__file__).resolve().parent / "analytics.db"
    try:
        conn = sqlite3.connect(str(db_path), timeout=30.0)
        c = conn.cursor()
        c.execute("SELECT id, song_name, amount, currency, email, created_at FROM purchases ORDER BY created_at DESC LIMIT 50")
        rows = [{"id": r[0], "song_name": r[1], "amount": r[2], "currency": r[3], "email": r[4], "created_at": r[5]} for r in c.fetchall()]
        conn.close()
        return rows
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)

class StatsUpload(BaseModel):
    followers: int

@app.post("/api/public/stats")
def update_public_stats(stats: StatsUpload):
    db_path = Path(__file__).resolve().parent / "analytics.db"
    try:
        conn = sqlite3.connect(str(db_path), timeout=30.0)
        c = conn.cursor()
        from datetime import date
        today_str = date.today().isoformat()
        # Clean existing entries for 'all' platform for today
        c.execute("DELETE FROM channel_insights WHERE platform = ? AND date = ?", ("all", today_str))
        # Insert new sum
        c.execute("INSERT INTO channel_insights (platform, date, followers) VALUES (?, ?, ?)",
                  ("all", today_str, stats.followers))
        conn.commit()
        conn.close()
        print(f"[Stats Upload] Saved live followers count: {stats.followers}")
        return {"status": "success"}
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)

@app.get("/api/public/preview-video")
def get_preview_video(song_name: str):
    """
    Generate a temporary presigned URL for a song's preview video.
    Always points to the 'Original' version video, stripping 'Easy' suffixes.
    """
    clean_name = song_name
    for suffix in (" (Easy Version)", " (Easy)", "(Easy Version)", "(Easy)"):
        if clean_name.endswith(suffix):
            clean_name = clean_name[:-len(suffix)].strip()
            
    r2_account_id = settings.get("r2_account_id") or os.environ.get("R2_ACCOUNT_ID")
    r2_access_key = settings.get("r2_access_key_id") or os.environ.get("R2_ACCESS_KEY_ID")
    r2_secret_key = settings.get("r2_secret_access_key") or os.environ.get("R2_SECRET_ACCESS_KEY")
    r2_bucket = settings.get("r2_bucket_name", "meloscribe-sheets") or os.environ.get("R2_BUCKET_NAME", "meloscribe-sheets")

    if not r2_account_id or not r2_access_key or not r2_secret_key:
        print("[Preview Request] R2 credentials missing, using demo redirect fallback.")
        return {
            "download_url": f"https://example.com/demo-packages/{clean_name}/{clean_name}.mp4",
            "message": "Demo mode: R2 credentials are not configured"
        }

    try:
        import boto3
        from botocore.config import Config

        s3 = boto3.client(
            's3',
            endpoint_url=f'https://{r2_account_id}.r2.cloudflarestorage.com',
            aws_access_key_id=r2_access_key,
            aws_secret_access_key=r2_secret_key,
            region_name='auto',
            config=Config(signature_version='s3v4')
        )

        file_key = f"{clean_name}/{clean_name}_preview.mp4"
        try:
            s3.head_object(Bucket=r2_bucket, Key=file_key)
        except Exception:
            # Fallback to the full video if preview does not exist
            file_key = f"{clean_name}/{clean_name}.mp4"
            try:
                s3.head_object(Bucket=r2_bucket, Key=file_key)
            except Exception as head_err:
                print(f"[Preview Video] Video key '{file_key}' not found in R2 bucket '{r2_bucket}'.")
                return JSONResponse(content={"error": f"Preview video '{file_key}' not found in R2"}, status_code=404)

        presigned_url = s3.generate_presigned_url(
            ClientMethod='get_object',
            Params={'Bucket': r2_bucket, 'Key': file_key},
            ExpiresIn=900
        )

        return {"download_url": presigned_url}
    except Exception as e:
        print(f"Failed to generate presigned R2 preview URL: {e}")
        return JSONResponse(content={"error": f"Failed to generate preview URL: {str(e)}"}, status_code=500)

@app.get("/api/public/video-stream")
def stream_preview_video(song_name: str, request: Request):
    from fastapi.responses import StreamingResponse, FileResponse
    import requests

    def get_local_video(name):
        """Try to serve the exact requested song's local video file (no fallback to other songs)."""
        if os.name == 'nt':
            shop_videos_dir = settings.get("shop_videos_dir", r"C:\Dev\meloscribe\ShopVideos")
            local_path = os.path.join(shop_videos_dir, f"{name}.mp4")
        else:
            local_path = f"/home/ubuntu/meloscribe/Scores/{name}_preview.mp4"
        if os.path.exists(local_path):
            print(f"[Preview Video] Serving local file: {local_path}")
            return FileResponse(local_path, media_type="video/mp4")
        return None

    res = get_preview_video(song_name)
    if isinstance(res, JSONResponse):
        fb = get_local_video(song_name)
        if fb:
            return fb
        return JSONResponse(content={"error": f"Preview video not available for '{song_name}'"}, status_code=404)
    if not isinstance(res, dict):
        fb = get_local_video(song_name)
        if fb:
            return fb
        return JSONResponse(content={"error": "Invalid preview video response"}, status_code=500)
    download_url = res.get("download_url")
    if not download_url or "example.com" in download_url:
        fb = get_local_video(song_name)
        if fb:
            return fb
        return JSONResponse(content={"error": f"Preview video not available for '{song_name}'"}, status_code=404)

    req_headers = {}
    range_header = request.headers.get("range")
    if range_header:
        req_headers["range"] = range_header

    try:
        r2_resp = requests.get(download_url, headers=req_headers, stream=True, timeout=15)
        if r2_resp.status_code >= 400:
            print(f"[Preview Video] R2 returned {r2_resp.status_code} for {download_url}. Trying local video.")
            fb = get_local_video(song_name)
            if fb:
                return fb
            return JSONResponse(content={"error": f"Preview video not available for '{song_name}'"}, status_code=404)

        def chunk_generator():
            try:
                for chunk in r2_resp.iter_content(chunk_size=65536):
                    if chunk:
                        yield chunk
            finally:
                r2_resp.close()

        resp_headers = {}
        for h in ("content-type", "content-length", "content-range", "accept-ranges", "etag"):
            if h in r2_resp.headers:
                resp_headers[h] = r2_resp.headers[h]

        if "content-type" not in resp_headers:
            resp_headers["content-type"] = "video/mp4"

        return StreamingResponse(
            chunk_generator(),
            status_code=r2_resp.status_code,
            headers=resp_headers
        )
    except Exception as e:
        print(f"[Preview Video] Failed streaming from R2: {e}. Trying local video.")
        fb = get_local_video(song_name)
        if fb:
            return fb
        return JSONResponse(content={"error": f"Failed to stream video: {str(e)}"}, status_code=500)


@app.get("/api/public/audio-stream")
def stream_preview_audio(song_name: str, request: Request):
    from fastapi.responses import StreamingResponse, FileResponse
    import requests

    def get_local_fallback():
        dest_mp3 = Path(r"C:\Dev\meloscribe-frontend\website\public\audio-previews") / f"{song_name}.mp3"
        if dest_mp3.exists():
            print(f"[Preview Audio] Serving local fallback: {dest_mp3}")
            return FileResponse(dest_mp3, media_type="audio/mpeg")
        return None

    # Resolve R2 preview audio
    r2_account_id = settings.get("r2_account_id") or os.environ.get("R2_ACCOUNT_ID")
    r2_access_key = settings.get("r2_access_key_id") or os.environ.get("R2_ACCESS_KEY_ID")
    r2_secret_key = settings.get("r2_secret_access_key") or os.environ.get("R2_SECRET_ACCESS_KEY")
    r2_bucket = settings.get("r2_bucket_name", "meloscribe-sheets") or os.environ.get("R2_BUCKET_NAME", "meloscribe-sheets")

    if not r2_account_id or not r2_access_key or not r2_secret_key:
        fb = get_local_fallback()
        if fb:
            return fb
        return JSONResponse(content={"error": "R2 credentials missing"}, status_code=500)

    try:
        import boto3
        from botocore.config import Config
        s3 = boto3.client(
            's3',
            endpoint_url=f'https://{r2_account_id}.r2.cloudflarestorage.com',
            aws_access_key_id=r2_access_key,
            aws_secret_access_key=r2_secret_key,
            region_name='auto',
            config=Config(signature_version='s3v4')
        )
        # Try to check {clean_name}/{clean_name}.mp3
        clean_name = song_name
        for suffix in (" (Easy Version)", " (Easy)", "(Easy Version)", "(Easy)"):
            if clean_name.endswith(suffix):
                clean_name = clean_name[:-len(suffix)].strip()
        file_key = f"{clean_name}/{clean_name}.mp3"
        
        try:
            s3.head_object(Bucket=r2_bucket, Key=file_key)
        except Exception:
            fb = get_local_fallback()
            if fb:
                return fb
            return JSONResponse(content={"error": "Audio preview not found in R2"}, status_code=404)

        download_url = s3.generate_presigned_url(
            ClientMethod='get_object',
            Params={'Bucket': r2_bucket, 'Key': file_key},
            ExpiresIn=900
        )
    except Exception as e:
        print(f"Failed to generate presigned R2 audio preview URL: {e}")
        fb = get_local_fallback()
        if fb:
            return fb
        return JSONResponse(content={"error": str(e)}, status_code=500)

    req_headers = {}
    range_header = request.headers.get("range")
    if range_header:
        req_headers["range"] = range_header

    try:
        r2_resp = requests.get(download_url, headers=req_headers, stream=True, timeout=15)
        if r2_resp.status_code >= 400:
            fb = get_local_fallback()
            if fb:
                return fb
            return JSONResponse(content={"error": "R2 stream failed"}, status_code=r2_resp.status_code)

        def chunk_generator():
            try:
                for chunk in r2_resp.iter_content(chunk_size=65536):
                    if chunk:
                        yield chunk
            finally:
                r2_resp.close()

        resp_headers = {}
        for h in ("content-type", "content-length", "content-range", "accept-ranges", "etag"):
            if h in r2_resp.headers:
                resp_headers[h] = r2_resp.headers[h]

        if "content-type" not in resp_headers:
            resp_headers["content-type"] = "audio/mpeg"

        return StreamingResponse(chunk_generator(), status_code=r2_resp.status_code, headers=resp_headers)
    except Exception as e:
        fb = get_local_fallback()
        if fb:
            return fb
        return JSONResponse(content={"error": str(e)}, status_code=500)


# -------------------------------------------------------------------
# Broadcast Newsletter for new product Drops
# -------------------------------------------------------------------

class BroadcastRequest(BaseModel):
    title: str
    artist: str
    difficulty: str
    format: str
    price: str

def _send_new_song_notification(email: str, token: str, song_title: str, artist: str, difficulty: str, format: str, price: str):
    """Send a newsletter email about a new sheet music drop via Resend."""
    api_key = settings.get("resend_api_key", "")
    if not api_key:
        print("[Notify] WARNING: resend_api_key not set in settings.json. Skipping email.")
        return False
    
    unsubscribe_url = f"https://api.meloscribe.dev/api/notify/unsubscribe?token={token}"
    sheets_url = "https://meloscribe.dev/sheets"
    
    format_text = "Viral Part" if format == "viral_part" else "Full Arrangement"
    
    html_body = f"""
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="font-family: 'Helvetica Neue', Arial, sans-serif; background: #0a0a0f; color: #e0e0e0; max-width: 520px; margin: 0 auto; padding: 32px 16px;">
  <div style="text-align: center; margin-bottom: 32px; background: #12121c; border: 1px solid #2a2a3e; border-radius: 16px; padding: 24px 16px;">
    <span style="font-size: 32px; font-weight: 900; color: #ffffff; letter-spacing: 3px; text-transform: lowercase;">melo<span style="color: #ff2d92;">scribe</span></span>
    <div style="height: 2px; width: 60px; margin: 8px auto 0 auto; background: #00f5ff; border-radius: 2px;"></div>
    <p style="color: #888899; font-size: 11px; margin: 8px 0 0 0; text-transform: uppercase; letter-spacing: 1.5px;">piano &amp; sheet music</p>
  </div>
  <div style="background: #12121c; border: 1px solid #2a2a3e; border-radius: 16px; padding: 32px;">
    <h2 style="color: #ffffff; font-size: 20px; margin-top: 0; margin-bottom: 16px; text-align: center; font-weight: 700;">🎵 New Sheet Music Released!</h2>
    <p style="color: #b0b0c0; line-height: 1.8; font-size: 15px;">
      Hey! A new piano arrangement has just been dropped on meloscribe.dev:
    </p>
    <div style="background: #0a0a0f; border-left: 4px solid #ff2d92; padding: 16px; border-radius: 4px; margin: 24px 0;">
      <h3 style="color: #ffffff; margin: 0 0 8px 0; font-size: 18px;">{song_title}</h3>
      <p style="color: #888; margin: 0 0 12px 0; font-size: 14px;">by {artist}</p>
      <div style="margin-top: 12px;">
        <span style="display: inline-block; background: rgba(0, 245, 212, 0.1); border: 1px solid rgba(0, 245, 212, 0.4); color: #00f5d4; font-size: 12px; font-weight: 600; padding: 4px 10px; border-radius: 12px; margin-right: 8px;">{difficulty}</span>
        <span style="display: inline-block; background: rgba(255, 45, 146, 0.1); border: 1px solid rgba(255, 45, 146, 0.4); color: #ff2d92; font-size: 12px; font-weight: 600; padding: 4px 10px; border-radius: 12px; margin-right: 8px;">{format_text}</span>
        <span style="display: inline-block; background: rgba(255, 255, 255, 0.1); border: 1px solid rgba(255, 255, 255, 0.2); color: #ffffff; font-size: 12px; font-weight: 600; padding: 4px 10px; border-radius: 12px;">{price}</span>
      </div>
    </div>
    <p style="color: #b0b0c0; line-height: 1.6; font-size: 15px; text-align: center;">Get your PDF sheet music, MIDI, and offline practice videos now:</p>
    <div style="text-align: center; margin: 28px 0;">
      <a href="{sheets_url}" style="display: inline-block; background-color: #12121c; border: 2px solid #00f5d4; color: #00f5d4; font-family: 'Helvetica Neue', Arial, sans-serif; font-weight: 700; font-size: 15px; padding: 14px 32px; border-radius: 10px; text-decoration: none; text-shadow: 0 0 8px rgba(0,245,212,0.35);">Get Sheet Music</a>
    </div>
    <p style="color: #b0b0c0; line-height: 1.6; font-size: 15px; margin-top: 24px;">Happy practicing,<br>meloscribe</p>
  </div>
  <p style="text-align: center; font-size: 11px; color: #555; margin-top: 24px;">
    Want to stop receiving these alerts? <a href="{unsubscribe_url}" style="color: #555;">Unsubscribe here</a>
  </p>
</body>
</html>
"""

    try:
        resp = requests.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "from": "meloscribe <info@meloscribe.dev>",
                "to": [email],
                "subject": f"New sheet music: {song_title} — meloscribe",
                "html": html_body,
            },
            timeout=10
        )
        if resp.status_code in (200, 201):
            print(f"[Notify] Newsletter email sent to {email}")
            return True
        else:
            print(f"[Notify] Resend API error {resp.status_code}: {resp.text}")
            return False
    except Exception as e:
        print(f"[Notify] Email send failed: {e}")
        return False

    sent_count = 0
    for email, token in subscribers:
        success = _send_new_song_notification(email, token, req.title, req.artist, req.difficulty, req.format, req.price)
        if success:
            sent_count += 1
            
    return {"status": "success", "sent_count": sent_count, "total_subscribers": len(subscribers)}


# -------------------------------------------------------------------
# Public Direct Free Downloads
# -------------------------------------------------------------------
@app.get("/api/public/download")
def public_free_download(song_id: str, type: str, request: Request):
    if type not in ("pdf", "zip", "midi", "midi_slow", "video", "video_slow"):
        return JSONResponse(content={"error": "Invalid download type"}, status_code=400)

    # Load songs list from frontend or local fallback
    songs_path = r"c:\Dev\meloscribe-frontend\website\src\data\songs.json"
    if not os.path.exists(songs_path):
        songs_path = Path(__file__).resolve().parent / "songs.json"
    
    songs_list = []
    if os.path.exists(songs_path):
        with open(songs_path, "r", encoding="utf-8") as f:
            songs_list = json.load(f)

    target_song = None
    for song in songs_list:
        if str(song.get("id")) == str(song_id):
            target_song = song
            break

    if not target_song:
        return JSONResponse(content={"error": "Song not found"}, status_code=404)

    # Check if free
    price_str = str(target_song.get("price", "")).strip().lower()
    is_free = False
    if not price_str or "free" in price_str or price_str.startswith("0") or price_str == "0" or "0 €" in price_str or "0$" in price_str:
        is_free = True

    if not is_free:
        return JSONResponse(content={"error": "This song is not free"}, status_code=403)

    song_name = target_song.get("title")

    # Generate public Cloudflare R2 download URL
    r2_account_id = settings.get("r2_account_id") or os.environ.get("R2_ACCOUNT_ID")
    r2_access_key = settings.get("r2_access_key_id") or os.environ.get("R2_ACCESS_KEY_ID")
    r2_secret_key = settings.get("r2_secret_access_key") or os.environ.get("R2_SECRET_ACCESS_KEY")
    r2_bucket = settings.get("r2_bucket_name", "meloscribe-assets") or os.environ.get("R2_BUCKET_NAME", "meloscribe-assets")

    if not r2_account_id or not r2_access_key or not r2_secret_key:
        if type == "pdf":
            suffix = f"/{song_name}.pdf"
        elif type == "midi":
            suffix = f"/{song_name}.mid"
        elif type == "midi_slow":
            suffix = f"/{song_name} slow.mid"
        elif type == "video":
            suffix = f"/{song_name}.mp4"
        elif type == "video_slow":
            suffix = f"/{song_name} slow.mp4"
        else:
            suffix = " Full Package.zip"
        return {"download_url": f"https://example.com/demo-packages/{song_name}{suffix}"}

    try:
        import boto3
        from botocore.config import Config

        if type == "pdf":
            file_key = f"{song_name}/{song_name}.pdf"
        elif type == "midi":
            file_key = f"{song_name}/{song_name}.mid"
        elif type == "midi_slow":
            file_key = f"{song_name}/{song_name} slow.mid"
        elif type == "video":
            file_key = f"{song_name}/{song_name}.mp4"
        elif type == "video_slow":
            file_key = f"{song_name}/{song_name} slow.mp4"
        else:
            file_key = f"{song_name} Full Package.zip"

        s3 = boto3.client(
            's3',
            endpoint_url=f'https://{r2_account_id}.r2.cloudflarestorage.com',
            aws_access_key_id=r2_access_key,
            aws_secret_access_key=r2_secret_key,
            region_name='auto',
            config=Config(signature_version='s3v4')
        )

        filename = file_key.split('/')[-1]
        presigned_url = s3.generate_presigned_url(
            ClientMethod='get_object',
            Params={
                'Bucket': r2_bucket,
                'Key': file_key,
                'ResponseContentDisposition': f'attachment; filename="{filename}"'
            },
            ExpiresIn=3600
        )
        return {"download_url": presigned_url}
    except Exception as e:
        print(f"Failed to generate free presigned url: {e}")
        return JSONResponse(content={"error": f"Failed to generate download URL: {str(e)}"}, status_code=500)


# -------------------------------------------------------------------
# Smart Batch Ingest Queue APIs
# -------------------------------------------------------------------
from fastapi import UploadFile, File, Form, HTTPException
import shutil

is_batch_processing = False

def batch_processor_worker():
    global is_batch_processing
    is_batch_processing = True
    
    db_path = Path(__file__).resolve().parent / "analytics.db"
    cakewalk_dir = settings.get("cakewalk_dir", r"C:\Cakewalk Projects")
    python = sys.executable
    
    log_error("[Batch Worker] Background processor loop started.")
    
    try:
        while True:
            # Fetch all initialized items to see which ones are ready with audio
            conn = sqlite3.connect(str(db_path), timeout=30.0)
            c = conn.cursor()
            c.execute("SELECT song_name, author, theme, price, format, difficulty FROM batch_ingest_queue WHERE status = 'initialized' ORDER BY id ASC")
            rows = c.fetchall()
            conn.close()
            
            target_item = None
            for row in rows:
                song_name = row[0]
                audio_path = Path(cakewalk_dir) / song_name / "Audio Export" / f"{song_name}.wav"
                if audio_path.exists():
                    target_item = row
                    break
                    
            if not target_item:
                log_error("[Batch Worker] No more initialized queue items with ready audio files. Stopping loop.")
                break # No items are ready with audio files
                
            song_name, author, theme, price, fmt, difficulty = target_item
            
            # Change status to 'processing'
            conn = sqlite3.connect(str(db_path), timeout=30.0)
            c = conn.cursor()
            c.execute("UPDATE batch_ingest_queue SET status = 'processing', error_message = NULL WHERE song_name = ?", (song_name,))
            conn.commit()
            conn.close()
            
            log_error(f"[Batch Worker] Started processing '{song_name}'")
            
            should_abort_queue = False
            try:
                has_easy = (difficulty == "both")
                
                # Construct sequential step commands
                steps = []
                
                # Keysight Render (Original)
                steps.append([python, "-u", str(TOOLS_DIR / "keysight_bot.py"), "--song", song_name, "--theme", theme])
                # Compression (Original Normal)
                normal_vid = Path(settings.get("keysight_dir", r"C:\Dev\meloscribe\Keysight export")) / f"{song_name}.mp4"
                steps.append([python, "-u", str(TOOLS_DIR / "handbrake_bot.py"), "--input", str(normal_vid)])
                # Compression (Original Slow)
                slow_vid = Path(settings.get("keysight_dir", r"C:\Dev\meloscribe\Keysight export")) / f"{song_name} slow.mp4"
                steps.append([python, "-u", str(TOOLS_DIR / "handbrake_bot.py"), "--input", str(slow_vid)])
                
                if has_easy:
                    # Keysight Render (Easy)
                    steps.append([python, "-u", str(TOOLS_DIR / "keysight_bot.py"), "--song", f"{song_name} Easy", "--theme", theme])
                    # Compression (Easy Normal)
                    easy_normal_vid = Path(settings.get("keysight_dir", r"C:\Dev\meloscribe\Keysight export")) / f"{song_name} Easy.mp4"
                    steps.append([python, "-u", str(TOOLS_DIR / "handbrake_bot.py"), "--input", str(easy_normal_vid)])
                    # Compression (Easy Slow)
                    easy_slow_vid = Path(settings.get("keysight_dir", r"C:\Dev\meloscribe\Keysight export")) / f"{song_name} Easy slow.mp4"
                    steps.append([python, "-u", str(TOOLS_DIR / "handbrake_bot.py"), "--input", str(easy_slow_vid)])
                    
                # Portrait / Widescreen versions
                versions = [("", song_name)]
                if has_easy:
                    versions.append((" Easy", f"{song_name} Easy"))
                    
                zoom_val = "1.50"
                shift_val = "0"
                
                keysight_dir = Path(settings.get("keysight_dir", r"C:\Dev\meloscribe\Keysight export"))
                for suffix, folder_name in versions:
                    v_song = f"{song_name}{suffix}"
                    for vtype, prefix in [("normal", ""), ("tutorial", " slow")]:
                        vid_in = str(keysight_dir / f"{v_song}{prefix}.mp4")
                        midi_path = f"C:\\Cakewalk Projects\\{folder_name}\\{v_song}{prefix}.mid"
                        
                        cmd_portrait = [
                            python, "-u", str(TOOLS_DIR / "video_generator.py"),
                            "--video", vid_in, "--title", v_song, "--author", author,
                            "--type", vtype, "--zoom", zoom_val, "--shift", shift_val,
                            "--midipath", midi_path, "--theme", theme, "--use_portrait_addon"
                        ]
                        steps.append(cmd_portrait)
                        
                        if fmt == "full_arrangement":
                            cmd_widescreen = [
                                python, "-u", str(TOOLS_DIR / "video_generator.py"),
                                "--video", vid_in, "--title", v_song, "--author", author,
                                "--type", vtype, "--zoom", zoom_val, "--shift", shift_val,
                                "--midipath", midi_path, "--theme", theme, "--wide"
                            ]
                            steps.append(cmd_widescreen)
                            
                    # Cover Generator
                    steps.append([python, "-u", str(TOOLS_DIR / "cover_generator.py"), "--song", v_song, "--author", author, "--theme", theme])
                    # MuseScore: open MIDI + meloscribe template, wait for PDF export
                    steps.append([python, "-u", str(TOOLS_DIR / "musescore_launcher.py"), "--song", v_song, "--author", author])
                    # R2 Upload (individual assets: PDF, MIDI, videos, preview+MP3 generated inline)
                    steps.append([python, "-u", str(TOOLS_DIR / "upload_bot.py"), "--song", v_song, "--author", author, "--mode", "r2", "--format", fmt])
                    steps.append([python, "-u", str(TOOLS_DIR / "upload_bot.py"), "--song", v_song, "--price", price, "--kofi_id", "prod_dummy123", "--mode", "website", "--author", author])
                
                # Execute all steps sequentially
                success = True
                err_msg = ""
                total_steps = len(steps)
                for step_idx, cmd in enumerate(steps):
                    # Set base progress for this step
                    base_progress = int((step_idx / total_steps) * 100)
                    
                    conn = sqlite3.connect(str(db_path), timeout=30.0)
                    c = conn.cursor()
                    c.execute("UPDATE batch_ingest_queue SET progress = ? WHERE song_name = ?", (base_progress, song_name))
                    conn.commit()
                    conn.close()

                    log_error(f"[Batch Worker] Running sub-task: {' '.join(cmd[:4])}...")
                    cmd_str = " ".join(cmd)
                    is_interactive = "keysight_bot.py" in cmd_str or "musescore_launcher.py" in cmd_str
                    if sys.platform == "win32":
                        creation_flags = subprocess.CREATE_NEW_CONSOLE if is_interactive else subprocess.CREATE_NO_WINDOW
                    else:
                        creation_flags = 0
                        
                    if is_interactive:
                        res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", creationflags=creation_flags)
                        rc = res.returncode
                        stdout_str = res.stdout or ""
                        stderr_str = res.stderr or ""
                    else:
                        p = subprocess.Popen(
                            cmd,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT,
                            text=True,
                            encoding="utf-8",
                            errors="replace",
                            creationflags=creation_flags
                        )
                        
                        stdout_lines = []
                        for line in iter(p.stdout.readline, ""):
                            # Forward output to logs
                            stdout_lines.append(line)
                            
                            # Parse PROGRESS from subprocess (e.g. video_generator.py outputting 'PROGRESS:25%')
                            if line.startswith("PROGRESS:"):
                                try:
                                    pct = int(line.split(":")[1].replace("%", "").strip())
                                    # Interpolate current step progress
                                    sub_progress = min(base_progress + int(pct / total_steps), 100)
                                    conn = sqlite3.connect(str(db_path), timeout=30.0)
                                    c = conn.cursor()
                                    c.execute("UPDATE batch_ingest_queue SET progress = ? WHERE song_name = ?", (sub_progress, song_name))
                                    conn.commit()
                                    conn.close()
                                except Exception:
                                    pass
                        p.wait()
                        rc = p.returncode
                        stdout_str = "".join(stdout_lines)
                        stderr_str = ""
                        
                    if rc != 0:
                        success = False
                        sub_err = (stderr_str or stdout_str or f"Process exited with code {rc}").strip()
                        err_msg = f"Step failed: {' '.join(cmd[:4])}... Details: {sub_err}"
                        log_error(f"[Batch Worker] Error during step: {err_msg}")
                        break
                        
                if success:
                    # Update status to 'active'
                    conn = sqlite3.connect(str(db_path), timeout=30.0)
                    c = conn.cursor()
                    c.execute("UPDATE batch_ingest_queue SET status = 'active', processed_at = CURRENT_TIMESTAMP WHERE song_name = ?", (song_name,))
                    conn.commit()
                    conn.close()
                    log_error(f"[Batch Worker] Successfully processed '{song_name}'")
                else:
                    # Update status to 'failed'
                    conn = sqlite3.connect(str(db_path), timeout=30.0)
                    c = conn.cursor()
                    c.execute("UPDATE batch_ingest_queue SET status = 'failed', error_message = ? WHERE song_name = ?", (err_msg, song_name))
                    conn.commit()
                    conn.close()
                    should_abort_queue = True
            except Exception as ex:
                conn = sqlite3.connect(str(db_path), timeout=30.0)
                c = conn.cursor()
                c.execute("UPDATE batch_ingest_queue SET status = 'failed', error_message = ? WHERE song_name = ?", (str(ex), song_name))
                conn.commit()
                conn.close()
                log_error(f"[Batch Worker] Exception processing '{song_name}': {ex}")
                should_abort_queue = True
                
            if should_abort_queue:
                log_error("[Batch Worker] Queue processing aborted due to error to prevent cascaded failures. Fix the error and restart the queue.")
                break
                
    finally:
        is_batch_processing = False
        log_error("[Batch Worker] Background processor loop stopped.")

@app.post("/api/batch/initialize")
async def batch_initialize(
    metadata: str = Form(...),
    files: list[UploadFile] = File(...)
):
    try:
        items = json.loads(metadata)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid metadata JSON: {str(e)}")

    db_path = Path(__file__).resolve().parent / "analytics.db"
    conn = sqlite3.connect(str(db_path), timeout=30.0)
    c = conn.cursor()

    # Map metadata by filename
    meta_map = {item["fileName"]: item for item in items}
    cakewalk_dir = settings.get("cakewalk_dir", r"C:\Cakewalk Projects")

    initialized_songs = []

    try:
        for file in files:
            meta = meta_map.get(file.filename)
            if not meta:
                continue

            song_name = meta.get("title", "").strip()
            author = meta.get("artist", "").strip()
            theme = meta.get("theme", "warm").strip()
            price = meta.get("price", "6.00").strip()
            fmt = meta.get("format", "full_arrangement").strip()
            include_easy = meta.get("includeEasy", False)
            difficulty = "both" if include_easy else "original"

            if not song_name or not author:
                continue

            # Create primary Cakewalk project folder
            song_dir = Path(cakewalk_dir) / song_name
            os.makedirs(str(song_dir), exist_ok=True)

            # Save normal MIDI
            midi_path = song_dir / f"{song_name}.mid"
            with open(midi_path, "wb") as f:
                shutil.copyfileobj(file.file, f)

            # Save easy version if included
            if include_easy:
                easy_dir = Path(cakewalk_dir) / f"{song_name} Easy"
                os.makedirs(str(easy_dir), exist_ok=True)
                # Reset stream pointer and write copy to easy directory
                file.file.seek(0)
                easy_midi_path = easy_dir / f"{song_name} Easy.mid"
                with open(easy_midi_path, "wb") as f:
                    shutil.copyfileobj(file.file, f)

            # Insert/Replace queue entry
            c.execute(
                """
                INSERT OR REPLACE INTO batch_ingest_queue 
                (song_name, author, theme, price, format, difficulty, status, error_message, processed_at)
                VALUES (?, ?, ?, ?, ?, ?, 'initialized', NULL, NULL)
                """,
                (song_name, author, theme, price, fmt, difficulty)
            )
            initialized_songs.append(song_name)

        conn.commit()
    except Exception as e:
        conn.rollback()
        conn.close()
        raise HTTPException(status_code=500, detail=f"Database or filesystem error: {str(e)}")

    conn.close()
    return {"status": "success", "initialized": initialized_songs}

@app.get("/api/batch/queue")
def get_batch_queue():
    db_path = Path(__file__).resolve().parent / "analytics.db"
    try:
        conn = sqlite3.connect(str(db_path), timeout=30.0)
        c = conn.cursor()
        c.execute("SELECT song_name, author, theme, price, format, difficulty, status, error_message, created_at, processed_at, hook_start, hook_end, progress FROM batch_ingest_queue ORDER BY id DESC")
        rows = c.fetchall()
        conn.close()
    except Exception as e:
        return JSONResponse(content={"error": f"Database error: {str(e)}"}, status_code=500)

    queue = []
    for r in rows:
        queue.append({
            "songName": r[0],
            "author": r[1],
            "theme": r[2],
            "price": r[3],
            "format": r[4],
            "difficulty": r[5],
            "status": r[6],
            "errorMessage": r[7],
            "createdAt": r[8],
            "processedAt": r[9],
            "hookStart": r[10],
            "hookEnd": r[11],
            "progress": r[12] or 0,
        })
    return queue

@app.post("/api/batch/retry")
async def retry_batch_item(req: dict):
    song_name = req.get("song_name")
    if not song_name:
        raise HTTPException(status_code=400, detail="song_name is required")
    db_path = Path(__file__).resolve().parent / "analytics.db"
    try:
        conn = sqlite3.connect(str(db_path), timeout=30.0)
        c = conn.cursor()
        c.execute("UPDATE batch_ingest_queue SET status = 'initialized', error_message = NULL, progress = 0 WHERE song_name = ?", (song_name,))
        conn.commit()
        conn.close()

        # Automatically kick off batch worker if not already running
        global is_batch_processing
        if not is_batch_processing:
            threading.Thread(target=batch_processor_worker, daemon=True).start()

        return {"status": "success", "message": f"Reset status of '{song_name}' to initialized and started queue worker"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/batch/delete")
async def delete_batch_item(req: dict):
    song_name = req.get("song_name")
    if not song_name:
        raise HTTPException(status_code=400, detail="song_name is required")
    db_path = Path(__file__).resolve().parent / "analytics.db"
    try:
        conn = sqlite3.connect(str(db_path), timeout=30.0)
        c = conn.cursor()
        c.execute("DELETE FROM batch_ingest_queue WHERE song_name = ?", (song_name,))
        conn.commit()
        conn.close()
        return {"status": "success", "message": f"Removed '{song_name}' from the queue"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/batch/process")
def trigger_batch_process():
    global is_batch_processing
    if is_batch_processing:
        return {"status": "already_running"}
        
    threading.Thread(target=batch_processor_worker, daemon=True).start()
    return {"status": "started"}

@app.post("/api/batch/set-hook")
async def set_hook(req: dict):
    """Save hook_start / hook_end timestamps for a queued song."""
    song_name = req.get("song_name")
    hook_start = req.get("hook_start")
    hook_end = req.get("hook_end")
    if not song_name:
        raise HTTPException(status_code=400, detail="song_name is required")
    db_path = Path(__file__).resolve().parent / "analytics.db"
    try:
        conn = sqlite3.connect(str(db_path), timeout=30.0)
        conn.execute(
            "UPDATE batch_ingest_queue SET hook_start=?, hook_end=? WHERE song_name=?",
            (hook_start, hook_end, song_name)
        )
        conn.commit()
        conn.close()
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/batch/stream-keysight")
def stream_keysight_video(song_name: str, request: Request):
    """Stream the local Keysight RAW or compressed MP4 for the hook editor preview player."""
    from fastapi.responses import FileResponse, StreamingResponse
    keysight_dir = Path(settings.get("keysight_dir", r"C:\Dev\meloscribe\Keysight export"))
    raw_path = keysight_dir / "RAW" / f"{song_name}_RAW.mp4"
    compressed_path = keysight_dir / f"{song_name}.mp4"
    video_path = raw_path if raw_path.exists() else compressed_path
    if not video_path.exists():
        raise HTTPException(status_code=404, detail=f"No Keysight video found for '{song_name}'")
    
    file_size = video_path.stat().st_size
    range_header = request.headers.get("range")
    
    if range_header:
        # Handle range requests for HTML5 video seeking
        range_val = range_header.replace("bytes=", "").split("-")
        start = int(range_val[0])
        end = int(range_val[1]) if range_val[1] else file_size - 1
        chunk_size = end - start + 1
        
        def iter_file():
            with open(video_path, "rb") as f:
                f.seek(start)
                remaining = chunk_size
                while remaining > 0:
                    data = f.read(min(65536, remaining))
                    if not data:
                        break
                    remaining -= len(data)
                    yield data
        
        headers = {
            "Content-Range": f"bytes {start}-{end}/{file_size}",
            "Accept-Ranges": "bytes",
            "Content-Length": str(chunk_size),
            "Content-Type": "video/mp4",
        }
        return StreamingResponse(iter_file(), status_code=206, headers=headers)
    
    return FileResponse(str(video_path), media_type="video/mp4", headers={"Accept-Ranges": "bytes"})

@app.post("/api/batch/regenerate-preview")
async def regenerate_preview(req: dict):
    """
    Re-cut the _preview.mp4 from local Keysight file using saved hook timestamps,
    then re-upload to R2, overwriting the existing preview.
    """
    song_name = req.get("song_name")
    hook_start = req.get("hook_start")
    hook_end = req.get("hook_end")
    if not song_name:
        raise HTTPException(status_code=400, detail="song_name is required")
    
    db_path = Path(__file__).resolve().parent / "analytics.db"
    
    # Persist timestamps
    try:
        conn = sqlite3.connect(str(db_path), timeout=30.0)
        conn.execute(
            "UPDATE batch_ingest_queue SET hook_start=?, hook_end=? WHERE song_name=?",
            (hook_start, hook_end, song_name)
        )
        conn.commit()
        # Also fetch format and author for this song
        row = conn.execute(
            "SELECT format, author FROM batch_ingest_queue WHERE song_name=?", (song_name,)
        ).fetchone()
        conn.close()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"DB error: {e}")
    
    format_mode = row[0] if row else "full_arrangement"
    author = row[1] if row and len(row) > 1 and row[1] else "Traditional"
    
    keysight_dir = Path(settings.get("keysight_dir", r"C:\Dev\meloscribe\Keysight export"))
    raw_path = keysight_dir / "RAW" / f"{song_name}_RAW.mp4"
    compressed_path = keysight_dir / f"{song_name}.mp4"
    source = raw_path if raw_path.exists() else compressed_path
        
    if not source.exists():
        raise HTTPException(status_code=404, detail=f"Source video not found for '{song_name}'")
    
    dest_preview = keysight_dir / f"{song_name}_preview.mp4"
    
    # Escape paths for FFmpeg
    def escape_path_for_ffmpeg(p: str) -> str:
        p = p.replace('\\', '/')
        p = p.replace(':', '\\:')
        return p

    def get_video_dimensions(video_path):
        import subprocess as _sp
        import re
        try:
            cmd = ["ffmpeg", "-i", str(video_path)]
            creation_flags = 0x08000000 if sys.platform == "win32" else 0
            res = _sp.run(cmd, stdout=_sp.PIPE, stderr=_sp.PIPE, text=True, creationflags=creation_flags)
            match = re.search(r'Video:.*?\b(\d{3,4})x(\d{3,4})\b', res.stderr)
            if match:
                return int(match.group(1)), int(match.group(2))
        except Exception:
            pass
        return 2560, 1440

    width, height = get_video_dimensions(source)
    title_size = int(width * 0.05)
    artist_size = int(width * 0.024)
    
    tools_dir = Path(__file__).resolve().parent.parent.parent
    font_title = tools_dir / "fonts" / "arno_pro.ttf"
    font_artist = tools_dir / "fonts" / "montserrat.ttf"
    
    font_title_esc = escape_path_for_ffmpeg(str(font_title))
    font_artist_esc = escape_path_for_ffmpeg(str(font_artist))
    
    # Write temp text files to avoid ffmpeg escaping issues
    import tempfile
    import uuid
    uid = uuid.uuid4().hex[:8]
    temp_dir = tempfile.gettempdir()
    
    title_txt = os.path.join(temp_dir, f"_title_{uid}.txt")
    artist_txt = os.path.join(temp_dir, f"_artist_{uid}.txt")
    
    with open(title_txt, "w", encoding="utf-8") as f:
        f.write(song_name)
    with open(artist_txt, "w", encoding="utf-8") as f:
        f.write(author)
        
    title_txt_esc = escape_path_for_ffmpeg(title_txt)
    artist_txt_esc = escape_path_for_ffmpeg(artist_txt)
    
    # Drawtext filter complex
    filter_complex = (
        f"[0:v]drawtext=fontfile='{font_title_esc}':textfile='{title_txt_esc}':fontcolor=white:fontsize={title_size}"
        f":x=(w-text_w)/2:y=(h/2)-{int(height*0.06)}:shadowcolor=black@0.6:shadowx=4:shadowy=4"
        f":alpha='if(lt(t,1),t,if(lt(t,3.5),1,if(lt(t,4.5),4.5-t,0)))'[v1]; "
        
        f"[v1]drawtext=fontfile='{font_artist_esc}':textfile='{artist_txt_esc}':fontcolor=white:fontsize={artist_size}"
        f":x=(w-text_w)/2:y=(h/2)+{int(height*0.05)}:shadowcolor=black@0.6:shadowx=3:shadowy=3"
        f":alpha='if(lt(t,1),t,if(lt(t,3.5),1,if(lt(t,4.5),4.5-t,0)))'"
    )
    
    # Build ffmpeg command (always full length, no slicing)
    import subprocess as _sp
    cmd = [
        "ffmpeg", "-y",
        "-i", str(source),
        "-filter_complex", filter_complex,
        "-c:v", "libx264", "-preset", "fast", "-crf", "28",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        str(dest_preview)
    ]
    creation_flags = 0x08000000 if sys.platform == "win32" else 0
    rc = _sp.run(cmd, stdout=_sp.PIPE, stderr=_sp.PIPE, creationflags=creation_flags).returncode
    
    # Clean up files
    try:
        os.remove(title_txt)
        os.remove(artist_txt)
    except Exception:
        pass
    
    if rc != 0:
        raise HTTPException(status_code=500, detail="FFmpeg failed to generate preview clip")
    
    # Regenerate audio hover preview MP3 from WAV if WAV exists
    wav_path = None
    paths_to_try = [
        f"C:\\Cakewalk Projects\\{song_name}\\Audio Export\\{song_name}.wav",
        f"C:\\Cakewalk Projects\\{song_name}\\Audio Export\\.Audacity\\{song_name}.wav",
        f"C:\\Cakewalk Projects\\.Audacity\\{song_name}.wav",
    ]
    for p in paths_to_try:
        p_path = Path(p)
        if p_path.exists():
            wav_path = p_path
            break
            
    if not wav_path:
        # Case-insensitive scan fallback
        cakewalk_base = Path(r"C:\Cakewalk Projects")
        if cakewalk_base.exists():
            for folder in os.listdir(cakewalk_base):
                if folder.lower() == song_name.lower():
                    export_dir = cakewalk_base / folder / "Audio Export"
                    if export_dir.exists():
                        for f_name in os.listdir(export_dir):
                            if f_name.lower() == f"{song_name}.wav".lower():
                                wav_path = export_dir / f_name
                                break
                    if wav_path:
                        break

    dest_mp3 = Path(r"C:\Dev\meloscribe-frontend\website\public\audio-previews") / f"{song_name}.mp3"
    mp3_generated = False
    if wav_path:
        try:
            dest_mp3.parent.mkdir(parents=True, exist_ok=True)
            # Always full length, no slicing
            cmd_mp3 = [
                "ffmpeg", "-y",
                "-i", str(wav_path),
                "-c:a", "libmp3lame", "-b:a", "128k",
                str(dest_mp3)
            ]
            rc_mp3 = _sp.run(cmd_mp3, stdout=_sp.PIPE, stderr=_sp.PIPE, creationflags=creation_flags).returncode
            if rc_mp3 == 0:
                log_error(f"[Preview Regen] Success: Audio hover MP3 cropped to hook.")
                mp3_generated = True
            else:
                log_error(f"[Preview Regen] Error: FFmpeg failed to crop audio hover.")
        except Exception as e:
            log_error(f"[Preview Regen] Audio crop error: {e}")
            
    # Re-upload to R2 if credentials are configured
    r2_account_id = settings.get("r2_account_id")
    r2_access_key = settings.get("r2_access_key_id")
    r2_secret_key = settings.get("r2_secret_access_key")
    r2_bucket = settings.get("r2_bucket_name", "meloscribe-assets")
    uploaded = False
    if r2_account_id and r2_access_key and r2_secret_key:
        try:
            import boto3
            from botocore.config import Config as _Cfg
            s3 = boto3.client(
                's3',
                endpoint_url=f'https://{r2_account_id}.r2.cloudflarestorage.com',
                aws_access_key_id=r2_access_key,
                aws_secret_access_key=r2_secret_key,
                config=_Cfg(signature_version='s3v4')
            )
            
            # Upload Video Preview
            vid_key = f"{song_name}/{song_name}_preview.mp4"
            s3.upload_file(
                str(dest_preview), r2_bucket, vid_key,
                ExtraArgs={"ContentType": "video/mp4"}
            )
            log_error(f"[Preview Regen] Uploaded {vid_key} to R2 bucket '{r2_bucket}'.")
            
            # Upload Audio Preview
            if mp3_generated:
                mp3_key = f"{song_name}/{song_name}.mp3"
                s3.upload_file(
                    str(dest_mp3), r2_bucket, mp3_key,
                    ExtraArgs={"ContentType": "audio/mpeg"}
                )
                log_error(f"[Preview Regen] Uploaded {mp3_key} to R2 bucket '{r2_bucket}'.")
                
            uploaded = True
        except Exception as e:
            log_error(f"[Preview Regen] R2 upload failed: {e}")
    
    return {
        "status": "success",
        "local_path": str(dest_preview),
        "uploaded_to_r2": uploaded,
        "hook_start": hook_start,
        "hook_end": hook_end
    }


# -------------------------------------------------------------------
# Main
# -------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8787)
