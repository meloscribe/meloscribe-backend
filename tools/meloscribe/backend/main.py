import os
import sys
import json
import sqlite3
import platform
import requests
import threading
import subprocess
import secrets
import collections
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles

from shared import (
    active_websockets,
    log_buffer,
    manager,
    log_error,
    SYSTEM_LOGS,
    _error_log,
    CREATION_FLAGS,
    TOOLS_DIR,
    db_path
)

from routes_public import router as public_router
from routes_admin import router as admin_router
from routes_settings import router as settings_router, sync_credentials_route_internal
from routes_workflow import router as workflow_router

# -------------------------------------------------------------------
# FastAPI App Settings & Instance
# -------------------------------------------------------------------
app = FastAPI(
    title="Meloscribe Backend",
    version="1.0.0",
    docs_url="/api/docs" if platform.system() == "Windows" else None,
    redoc_url=None
)

# CORS configuration
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"https://(.*\.)?meloscribe\.(com|dev)|http://localhost:\d+|http://127\.0\.0\.1:\d+",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Custom security middleware
PUBLIC_ROUTES = [
    "/api/public",
    "/api/checkout",
    "/api/download",
    "/api/order",
    "/api/notify/subscribe",
    "/api/notify/confirm",
    "/api/kofi/webhook",
    "/api/webhooks/stripe",
    "/callback",
    "/pinterest-callback",
    "/public",
    "/ws/logs"
]

@app.middleware("http")
async def security_middleware(request: Request, call_next):
    # Bypass auth check for local development on Windows
    if platform.system() == "Windows":
        return await call_next(request)
        
    path = request.url.path
    if any(path.startswith(p) for p in PUBLIC_ROUTES):
        return await call_next(request)
        
    # Check X-Meloscribe-Key header on server
    api_key_header = request.headers.get("X-Meloscribe-Key")
    stored_key = get_server_api_key()
    
    if not stored_key:
        return JSONResponse(content={"error": "API key system uninitialized on server."}, status_code=500)
        
    if api_key_header != stored_key:
        # Return a beautiful HTML page for browser requests, JSON for API clients
        accept = request.headers.get("accept", "")
        if "text/html" in accept:
            html = """
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Access Denied — Meloscribe</title>
  <style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;600;700&display=swap');
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: 'Inter', sans-serif;
      background: #0a0a0f;
      color: #e2e8f0;
      min-height: 100vh;
      display: flex;
      align-items: center;
      justify-content: center;
      overflow: hidden;
    }
    .bg {
      position: fixed; inset: 0; z-index: 0;
      background: radial-gradient(ellipse at 20% 50%, rgba(0,245,255,0.05) 0%, transparent 60%),
                  radial-gradient(ellipse at 80% 20%, rgba(255,45,146,0.05) 0%, transparent 60%),
                  radial-gradient(ellipse at 50% 80%, rgba(139,92,246,0.04) 0%, transparent 60%);
    }
    .card {
      position: relative; z-index: 1;
      background: rgba(15,15,25,0.85);
      border: 1px solid rgba(255,45,146,0.25);
      border-radius: 24px;
      padding: 56px 64px;
      max-width: 480px;
      width: 90%;
      text-align: center;
      box-shadow: 0 0 60px rgba(255,45,146,0.08), 0 32px 64px rgba(0,0,0,0.5);
      backdrop-filter: blur(20px);
    }
    .icon {
      width: 72px; height: 72px;
      margin: 0 auto 28px;
      background: linear-gradient(135deg, rgba(255,45,146,0.15), rgba(255,45,146,0.05));
      border: 1px solid rgba(255,45,146,0.3);
      border-radius: 20px;
      display: flex; align-items: center; justify-content: center;
      font-size: 32px;
    }
    .badge {
      display: inline-block;
      background: rgba(255,45,146,0.1);
      border: 1px solid rgba(255,45,146,0.3);
      color: #ff2d92;
      font-size: 11px;
      font-weight: 600;
      letter-spacing: 2px;
      text-transform: uppercase;
      padding: 4px 14px;
      border-radius: 100px;
      margin-bottom: 20px;
    }
    h1 {
      font-size: 28px;
      font-weight: 700;
      color: #f8fafc;
      margin-bottom: 12px;
      line-height: 1.2;
    }
    p {
      color: #64748b;
      font-size: 14px;
      line-height: 1.7;
      margin-bottom: 32px;
    }
    .divider {
      height: 1px;
      background: linear-gradient(90deg, transparent, rgba(255,45,146,0.2), rgba(0,245,255,0.2), transparent);
      margin-bottom: 28px;
    }
    .meta {
      font-size: 11px;
      color: #334155;
      letter-spacing: 0.5px;
    }
    .glow {
      position: absolute;
      top: -1px; left: 50%; transform: translateX(-50%);
      width: 60%; height: 2px;
      background: linear-gradient(90deg, transparent, #ff2d92, #00f5ff, transparent);
      border-radius: 1px;
    }
  </style>
</head>
<body>
  <div class="bg"></div>
  <div class="card">
    <div class="glow"></div>
    <div class="icon">&#128274;</div>
    <div class="badge">401 Unauthorized</div>
    <h1>Access Denied</h1>
    <p>You don&rsquo;t have permission to access this resource. This area is restricted to authorized Meloscribe administrators only.</p>
    <div class="divider"></div>
    <div class="meta">MELOSCRIBE ADMIN &bull; SECURE ZONE</div>
  </div>
</body>
</html>"""
            return HTMLResponse(content=html, status_code=401)
        return JSONResponse(content={"error": "Unauthorized Access: Invalid API Key."}, status_code=401)
        
    return await call_next(request)

# -------------------------------------------------------------------
# Security helper functions
# -------------------------------------------------------------------
def initialize_server_api_key():
    try:
        key_path = Path(__file__).resolve().parent / "api_key.txt"
        if not key_path.exists():
            new_key = secrets.token_hex(16)
            key_path.write_text(new_key, encoding="utf-8")
            print(f"[Security] Generated new server_api_key file: {new_key}")
        else:
            print(f"[Security] Loaded server_api_key")
    except Exception as e:
        print(f"[Security] Failed to initialize server_api_key file: {e}")

def get_server_api_key():
    try:
        key_path = Path(__file__).resolve().parent / "api_key.txt"
        if key_path.exists():
            return key_path.read_text(encoding="utf-8").strip()
    except Exception:
        pass
    return None

# -------------------------------------------------------------------
# Periodic background sync scheduler thread
# -------------------------------------------------------------------
def periodic_background_sync():
    import time
    time.sleep(60) # Settle for 60s
    while True:
        log_error("[Background Sync] Periodic background sync cycle starting...")
        
        # 1. YouTube Sync
        try:
            sync_path = str(TOOLS_DIR / "meloscribe" / "backend" / "yt_sync.py")
            if os.path.exists(sync_path):
                import importlib.util
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
            if os.path.exists(sync_path):
                import importlib.util
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
            if os.path.exists(sync_path):
                import importlib.util
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
            if os.path.exists(sync_path):
                import importlib.util
                spec = importlib.util.spec_from_file_location("demographics_sync", sync_path)
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                mod.sync_all_demographics()
                log_error("[Background Sync] Demographics sync: SUCCESS")
        except Exception as e:
            log_error(f"[Background Sync] Demographics sync error: {e}")

        # 5. Competitor Sync
        try:
            from routes_admin import sync_competitors
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
            conn = sqlite3.connect(str(db_path))
            cursor = conn.cursor()
            count = sync_utils.evaluate_action_triggers(cursor)
            conn.commit()
            conn.close()
            log_error(f"[Background Sync] Evaluated action triggers. Created {count} to-dos.")
        except Exception as e:
            log_error(f"[Background Sync] Action triggers evaluation error: {e}")

        log_error("[Background Sync] Periodic background sync cycle completed. Sleeping for 15 minutes...")
        time.sleep(900)

# -------------------------------------------------------------------
# Startup sequence
# -------------------------------------------------------------------
def run_automatic_backup():
    try:
        from zipfile import ZipFile
        from datetime import date
        
        backups_dir = Path("C:/Dev/meloscribe-app/backups")
        backups_dir.mkdir(parents=True, exist_ok=True)
        
        today = date.today()
        current_month_prefix = f"meloscribe_backup_{today.year}_{today.month:02d}"
        
        # Check if any backup for the current month already exists
        existing_backups = list(backups_dir.glob(f"{current_month_prefix}*.zip"))
        if existing_backups:
            print(f"[Backup] Backup for {today.year}-{today.month:02d} already exists. Skipping.")
            return
            
        backup_filename = f"{current_month_prefix}_{today.day:02d}.zip"
        backup_zip_path = backups_dir / backup_filename
        
        print(f"[Backup] Starting automated monthly backup: {backup_filename}")
        
        files_to_backup = [
            (Path("C:/Dev/meloscribe-app/tools/meloscribe/backend/analytics.db"), "analytics.db"),
            (Path("C:/Dev/meloscribe-app/tools/meloscribe/backend/settings.json"), "settings.json"),
            (Path("C:/Dev/meloscribe-app/tools/meloscribe/backend/pinterest_tokens.json"), "pinterest_tokens.json"),
            (Path("C:/Dev/meloscribe-app/tools/meloscribe/backend/tiktok_tokens.json"), "tiktok_tokens.json"),
            (Path("C:/Dev/meloscribe-app/tools/meloscribe/backend/ig_tokens.json"), "ig_tokens.json"),
        ]
        
        with ZipFile(backup_zip_path, 'w') as zipf:
            for filepath, arcname in files_to_backup:
                if filepath.exists():
                    zipf.write(filepath, arcname)
                    print(f"[Backup] Added to archive: {arcname}")
                    
        print(f"[Backup] Automated monthly backup completed successfully: {backup_zip_path}")
    except Exception as e:
        print(f"[Backup] Warning: Automated backup failed: {e}")

_sync_errors = []

@app.on_event("startup")
def startup_event():
    initialize_server_api_key()
    
    # Run local automatic backup (Windows local environment only)
    if platform.system() == "Windows":
        run_automatic_backup()
    
    # DB setup/migration
    try:
        from db_setup import init_db
        init_db()
        print("[Startup] Database initialized/migrated.")
    except Exception as e:
        print(f"[Startup] Database initialization failed: {e}")

    # Reset stuck processing items in batch queue
    try:
        conn = sqlite3.connect(str(db_path))
        c = conn.cursor()
        c.execute("UPDATE batch_ingest_queue SET status = 'initialized' WHERE status = 'processing'")
        conn.commit()
        conn.close()
        print("[Startup] Reset stuck processing items in batch queue to initialized.")
    except Exception as db_err:
        print(f"[Startup] Failed to reset stuck batch items: {db_err}")

    # ngrok Tunnel (local Windows only)
    if platform.system() == "Windows":
        def run_ngrok():
            import shutil
            ngrok_domain = "wooing-encrust-ladle.ngrok-free.dev"
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

    # Startup metrics sync (background)
    def run_tiktok_sync():
        try:
            sync_path = str(TOOLS_DIR / "meloscribe" / "backend" / "tiktok_sync.py")
            if os.path.exists(sync_path):
                import importlib.util
                spec = importlib.util.spec_from_file_location("tiktok_sync", sync_path)
                mod  = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                mod.sync_tiktok()
        except Exception as e:
            print(f"[TikTok Sync] Failed on startup: {e}")
            _sync_errors.append(("TikTok Sync", str(e)))
    threading.Thread(target=run_tiktok_sync, daemon=True).start()

    def run_instagram_sync():
        try:
            sync_path = str(TOOLS_DIR / "meloscribe" / "backend" / "ig_sync.py")
            if os.path.exists(sync_path):
                import importlib.util
                spec = importlib.util.spec_from_file_location("ig_sync", sync_path)
                mod  = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                mod.sync_instagram()
        except Exception as e:
            print(f"[Instagram Sync] Failed on startup: {e}")
            _sync_errors.append(("Instagram Sync", str(e)))
    threading.Thread(target=run_instagram_sync, daemon=True).start()

    def run_youtube_sync():
        try:
            sync_path = str(TOOLS_DIR / "meloscribe" / "backend" / "yt_sync.py")
            if os.path.exists(sync_path):
                import importlib.util
                spec = importlib.util.spec_from_file_location("yt_sync", sync_path)
                mod  = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                mod.sync_youtube()
        except Exception as e:
            print(f"[YouTube Sync] Failed on startup: {e}")
            _sync_errors.append(("YouTube Sync", str(e)))
    threading.Thread(target=run_youtube_sync, daemon=True).start()

    def run_demographics_sync():
        try:
            sync_path = str(TOOLS_DIR / "meloscribe" / "backend" / "demographics_sync.py")
            if os.path.exists(sync_path):
                import importlib.util
                spec = importlib.util.spec_from_file_location("demographics_sync", sync_path)
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                mod.sync_all_demographics()
        except Exception as e:
            print(f"[Demographics Sync] Failed on startup: {e}")
            _sync_errors.append(("Demographics", str(e)))
    threading.Thread(target=run_demographics_sync, daemon=True).start()

    def run_threads_refresh():
        try:
            tokens_path = TOOLS_DIR / "meloscribe" / "backend" / "threads_tokens.json"
            if not tokens_path.exists():
                return
            poster_path = str(TOOLS_DIR / "meloscribe" / "backend" / "threads_poster.py")
            if os.path.exists(poster_path):
                import importlib.util
                spec = importlib.util.spec_from_file_location("threads_poster", poster_path)
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                mod.refresh_token()
        except Exception as e:
            print(f"[Threads] Token refresh failed on startup: {e}")
            _sync_errors.append(("Threads Refresh", str(e)))
    threading.Thread(target=run_threads_refresh, daemon=True).start()

    if platform.system() == "Windows":
        def run_creds_sync():
            import time
            time.sleep(5)
            print("[Startup] Auto-syncing credentials to VM...")
            try:
                sync_credentials_route_internal()
            except Exception as err:
                print(f"[Startup] Auto-sync credentials failed: {err}")
        threading.Thread(target=run_creds_sync, daemon=True).start()

    # Start 15-minute Periodic Background Sync Scheduler
    threading.Thread(target=periodic_background_sync, daemon=True).start()

# -------------------------------------------------------------------
# Logs & WebSockets Endpoints
# -------------------------------------------------------------------
@app.get("/api/logs")
async def get_error_logs():
    while _sync_errors:
        src, msg = _sync_errors.pop(0)
        log_error(src, msg)
    return JSONResponse(content=list(_error_log))

@app.post("/api/logs/clear")
def clear_error_logs():
    _error_log.clear()
    SYSTEM_LOGS.clear()
    return {"status": "success"}

@app.websocket("/ws/logs")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)

# -------------------------------------------------------------------
# Include Routers
# -------------------------------------------------------------------
app.include_router(public_router)
app.include_router(admin_router)
app.include_router(settings_router)
app.include_router(workflow_router)

# Mount public directory for static asset servers
public_dir = r"c:\Dev\meloscribe-frontend\website\public"
if os.path.exists(public_dir):
    app.mount("/public", StaticFiles(directory=public_dir), name="public")
    print(f"[FastAPI] Mounted {public_dir} under /public")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8787,
        # Allow large file uploads (videos can be 500MB+)
        h11_max_incomplete_event_size=0,
    )
