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

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, BackgroundTasks, Form, File, UploadFile, HTTPException
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import requests

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

app = FastAPI(title="Meloscribe Backend", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# -------------------------------------------------------------------
# Local Windows Proxy logic (redirects to the VM server database)
# -------------------------------------------------------------------
import platform
if platform.system() == "Windows":
    import requests
    VM_API_BASE = "https://api.meloscribe.dev"

    @app.get("/api/analytics")
    def get_local_analytics(range: str = "30d"):
        try:
            r = requests.get(f"{VM_API_BASE}/api/analytics?range={range}", timeout=5.0)
            return JSONResponse(content=r.json(), status_code=r.status_code)
        except Exception as e:
            return JSONResponse(content={"error": f"Proxy error: {e}"}, status_code=500)

    @app.get("/api/logs")
    def get_local_logs():
        try:
            r = requests.get(f"{VM_API_BASE}/api/logs", timeout=5.0)
            return JSONResponse(content=r.json(), status_code=r.status_code)
        except Exception as e:
            return JSONResponse(content={"error": f"Proxy error: {e}"}, status_code=500)

    @app.get("/api/notify/subscribers")
    def get_local_subscribers():
        try:
            r = requests.get(f"{VM_API_BASE}/api/notify/subscribers", timeout=5.0)
            return JSONResponse(content=r.json(), status_code=r.status_code)
        except Exception as e:
            return JSONResponse(content={"error": f"Proxy error: {e}"}, status_code=500)

    @app.get("/api/public/suggestions")
    def get_local_suggestions():
        try:
            r = requests.get(f"{VM_API_BASE}/api/public/suggestions", timeout=5.0)
            return JSONResponse(content=r.json(), status_code=r.status_code)
        except Exception as e:
            return JSONResponse(content={"error": f"Proxy error: {e}"}, status_code=500)

    @app.post("/api/public/suggestions")
    def create_local_suggestion(sug: dict):
        try:
            r = requests.post(f"{VM_API_BASE}/api/public/suggestions", json=sug, timeout=5.0)
            return JSONResponse(content=r.json(), status_code=r.status_code)
        except Exception as e:
            return JSONResponse(content={"error": f"Proxy error: {e}"}, status_code=500)

    @app.post("/api/public/suggestions/{sug_id}/vote")
    def vote_local_suggestion(sug_id: str):
        try:
            r = requests.post(f"{VM_API_BASE}/api/public/suggestions/{sug_id}/vote", timeout=5.0)
            return JSONResponse(content=r.json(), status_code=r.status_code)
        except Exception as e:
            return JSONResponse(content={"error": f"Proxy error: {e}"}, status_code=500)

    @app.post("/api/public/suggestions/{sug_id}/unvote")
    def unvote_local_suggestion(sug_id: str):
        try:
            r = requests.post(f"{VM_API_BASE}/api/public/suggestions/{sug_id}/unvote", timeout=5.0)
            return JSONResponse(content=r.json(), status_code=r.status_code)
        except Exception as e:
            return JSONResponse(content={"error": f"Proxy error: {e}"}, status_code=500)

    @app.get("/api/public/video-stream")
    def local_video_stream(song_name: str, request: Request):
        try:
            import requests
            from fastapi.responses import StreamingResponse
            req_headers = {}
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
            r = requests.delete(f"{VM_API_BASE}/api/public/suggestions/{sug_id}", timeout=5.0)
            return JSONResponse(content=r.json(), status_code=r.status_code)
        except Exception as e:
            return JSONResponse(content={"error": f"Proxy error: {e}"}, status_code=500)

    @app.get("/api/paddle/sales")
    def get_local_paddle_sales():
        try:
            r = requests.get(f"{VM_API_BASE}/api/paddle/sales", timeout=5.0)
            return JSONResponse(content=r.json(), status_code=r.status_code)
        except Exception as e:
            return JSONResponse(content={"error": f"Proxy error: {e}"}, status_code=500)

from fastapi.staticfiles import StaticFiles
public_dir = r"c:\Dev\meloscribe-frontend\website\public"
if os.path.exists(public_dir):
    app.mount("/public", StaticFiles(directory=public_dir), name="public")
    print(f"[FastAPI] Mounted {public_dir} under /public")

import collections
from datetime import datetime

# Ring buffer for system logs
SYSTEM_LOGS = collections.deque(maxlen=100)

def log_error(msg: str):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    SYSTEM_LOGS.appendleft({"time": timestamp, "msg": msg})
    print(f"[SYSTEM LOG] {msg}")

_sync_errors = []  # Collect errors from startup syncs (deferred until log_error is available)

@app.on_event("startup")
def startup_event():
    # --- Database Initialization / Migration ---
    try:
        from db_setup import init_db
        init_db()
        print("[Startup] Database initialized/migrated.")
    except Exception as e:
        print(f"[Startup] Database initialization failed: {e}")

    # --- Desktop Shortcut Auto-Creation ---
    try:
        ps_script = TOOLS_DIR / "meloscribe" / "create_shortcut.ps1"
        if ps_script.exists():
            subprocess.Popen(
                ["powershell.exe", "-ExecutionPolicy", "Bypass", "-File", str(ps_script)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=CREATION_FLAGS
            )
            print("[Startup] Verified/Updated Meloscribe Desktop Shortcut.")
    except Exception as e:
        print(f"[Startup] Failed to verify shortcut: {e}")

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

# -------------------------------------------------------------------
# Error Log System (in-memory ring buffer for UI display)
# -------------------------------------------------------------------
import collections
_error_log = collections.deque(maxlen=100)

def log_error(source: str, message: str, level: str = "error"):
    """Log an API error for display in the UI."""
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
                python, "-u", str(TOOLS_DIR / "kofi_zipper.py"),
                "--song", v_song, "--author", author,
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
        "kofi_zip": [python, "-u", str(TOOLS_DIR / "kofi_zipper.py"), "--song", req.song, "--author", req.author],
        "kofi_upload": [python, "-u", str(TOOLS_DIR / "upload_bot.py"), "--song", req.song, "--price", req.price, "--mode", "kofi", "--format", req.format],
        "youtube": [python, "-u", str(TOOLS_DIR / "upload_bot.py"), "--song", req.song, "--author", req.author,
                    "--mode", "youtube", "--datetime", f"{req.scheduleDate} {req.scheduleTime}"],
        "instagram": [python, "-u", str(TOOLS_DIR / "upload_bot.py"), "--song", req.song, "--author", req.author,
                      "--mode", "instagram", "--datetime", f"{req.scheduleDate} {req.scheduleTime}"],
        "facebook": [python, "-u", str(TOOLS_DIR / "upload_bot.py"), "--song", req.song, "--author", req.author,
                     "--mode", "facebook", "--datetime", f"{req.scheduleDate} {req.scheduleTime}"],
        "tiktok": [python, "-u", str(TOOLS_DIR / "upload_bot.py"), "--song", req.song, "--author", req.author,
                   "--mode", "tiktok", "--profile", "normal"],
        "website_add": [python, "-u", str(TOOLS_DIR / "upload_bot.py"), "--song", req.song, "--price", req.price,
                        "--kofi_id", req.kofi_id, "--mode", "website"],
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
    def _run():
        import importlib.util
        auth_path = str(TOOLS_DIR / "meloscribe" / "backend" / "tiktok_auth.py")
        spec = importlib.util.spec_from_file_location("tiktok_auth", auth_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod.run_initial_auth()
    threading.Thread(target=_run, daemon=True).start()
    return {"status": "opening browser for TikTok authorization..."}

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

    price_id = song.get("kofiId")
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
                with open(r"C:\Dev\meloscribe_credentials_backup.json", "r", encoding="utf-8") as cred_f:
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
                
        # Compare prices and sync if changed
        updated_songs = []
        for song in songs_list:
            if not isinstance(song, dict):
                updated_songs.append(song)
                continue
            song_id = song.get("id")
            if song_id == "global_settings":
                updated_songs.append(song)
                continue
                
            old_song = old_songs_map.get(song_id)
            old_price = old_song.get("price", "") if old_song else ""
            new_price = song.get("price", "")
            
            # Sync to Paddle if price changed, or if it doesn't have a valid Paddle Price ID yet
            has_valid_paddle_id = song.get("kofiId", "").startswith("pri_")
            if (old_price != new_price or not has_valid_paddle_id) and api_key:
                new_price_id = sync_song_price_to_paddle(song, new_price, api_key, is_sandbox=is_sandbox)
                if new_price_id:
                    song["kofiId"] = new_price_id
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
        correlations = {
            "byLanguage": [dict(r) for r in cursor.execute("SELECT language, AVG(views) as avgViews, COUNT(id) as count FROM videos GROUP BY language").fetchall()],
            "byAuthor": [dict(r) for r in cursor.execute("SELECT author, AVG(views) as avgViews, SUM(views) as totalViews FROM videos GROUP BY author").fetchall()],
            "byBpm": [dict(r) for r in cursor.execute("SELECT t.bpm, AVG(v.views) as avgViews FROM videos v JOIN tracks t ON v.song_name = t.song_name GROUP BY t.bpm").fetchall()],
            "byFormat": [dict(r) for r in cursor.execute("SELECT format, AVG(views) as avgViews, COUNT(id) as count FROM videos GROUP BY format").fetchall()],
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
    songs_path = Path(__file__).resolve().parent / "songs.json"
    if songs_path.exists():
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
# Paddle Webhook & R2 Secure Download System
# -------------------------------------------------------------------
def verify_paddle_signature(request_body: str, signature_header: str, secret: str) -> bool:
    import hmac
    import hashlib
    if not signature_header or not secret:
        return False
    try:
        parts = dict(item.split('=') for item in signature_header.split(';'))
        ts = parts.get('ts')
        h1 = parts.get('h1')
        if not ts or not h1:
            return False
        payload = f"{ts}:{request_body}"
        computed_hash = hmac.new(
            secret.encode('utf-8'),
            payload.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(computed_hash, h1)
    except Exception:
        return False

@app.post("/api/paddle/webhook")
async def paddle_webhook(request: Request):
    signature = request.headers.get("Paddle-Signature")
    raw_body = await request.body()
    body_str = raw_body.decode("utf-8")
    
    # Signature Verification
    webhook_secret = settings.get("paddle_webhook_secret") or os.environ.get("PADDLE_WEBHOOK_SECRET")
    if webhook_secret:
        if not verify_paddle_signature(body_str, signature, webhook_secret):
            return JSONResponse(content={"error": "Invalid signature"}, status_code=400)
    
    try:
        payload = json.loads(body_str)
        event_type = payload.get("event_type")
        data = payload.get("data", {})
        
        if event_type == "transaction.completed":
            txn_id = data.get("id")
            status = data.get("status")
            custom_data = data.get("custom_data", {})
            song_title = custom_data.get("song_title") or "Unknown Song"
            
            # Verify if the song is currently disabled or hidden on the website
            try:
                songs_json_path = r"c:\Dev\meloscribe-frontend\website\src\data\songs.json"
                if os.path.exists(songs_json_path):
                    with open(songs_json_path, "r", encoding="utf-8") as f:
                        songs_db = json.load(f)
                    matched_song = next((s for s in songs_db if s.get("title") == song_title), None)
                    if matched_song:
                        if matched_song.get("paymentsDisabled") or matched_song.get("hidden"):
                            print(f"[Paddle Webhook] REJECTED purchase for '{song_title}' (paymentsDisabled or hidden).")
                            return JSONResponse(content={"error": "Product is no longer available"}, status_code=403)
            except Exception as check_err:
                print(f"[Paddle Webhook] Error checking song availability: {check_err}")
            
            import uuid
            download_hash = custom_data.get("download_hash")
            if not download_hash:
                download_hash = uuid.uuid4().hex
            
            totals = (data.get("details") or {}).get("totals") or {}
            grand_total = float(totals.get("grand_total", 0)) / 100.0
            currency = totals.get("currency_code", "EUR")
            
            # Extract locale and buyer name
            locale = data.get("locale") or (data.get("checkout") or {}).get("locale") or "en"
            buyer_name = ((data.get("customer") or {}).get("name") or 
                          (data.get("billing_details") or {}).get("name") or 
                          "")
            
            email = (data.get("billing_details") or {}).get("email_address") or (data.get("customer") or {}).get("email") or "customer@example.com"
            
            db_path = Path(__file__).resolve().parent / "analytics.db"
            conn = sqlite3.connect(str(db_path))
            c = conn.cursor()
            c.execute(
                "INSERT OR IGNORE INTO purchases (transaction_id, email, song_name, amount, currency, status, download_hash, locale, buyer_name) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (txn_id, email, song_title, grand_total, currency, status, download_hash, locale, buyer_name)
            )
            is_new = c.rowcount > 0
            
            # Ensure we update locale and buyer_name if the record was inserted before by the fallback API
            c.execute(
                "UPDATE purchases SET locale = ?, buyer_name = ? WHERE transaction_id = ?",
                (locale, buyer_name, txn_id)
            )
            
            c.execute(
                "INSERT INTO revenue (amount, currency, source, event_type, buyer, message, song_name) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (grand_total, currency, "paddle", event_type, email, f"Paddle txn {txn_id}", song_title)
            )
            conn.commit()
            conn.close()
            print(f"[Paddle Webhook] Recorded purchase for '{song_title}' by {email} with hash {download_hash} (new: {is_new})")
            
            if is_new:
                send_purchase_delivery_email(email, song_title, download_hash, locale)
        
        elif event_type in ("transaction.refunded", "transaction.updated", "adjustment.created", "adjustment.updated"):
            txn_id = data.get("transaction_id") or data.get("id")
            status = data.get("status")
            action = data.get("action")
            
            is_refund = (
                event_type == "transaction.refunded" or
                status in ("refunded", "cancelled") or
                action == "refund"
            )
            
            if is_refund and txn_id:
                db_path = Path(__file__).resolve().parent / "analytics.db"
                conn = sqlite3.connect(str(db_path))
                c = conn.cursor()
                c.execute("UPDATE purchases SET status = 'refunded' WHERE transaction_id = ?", (txn_id,))
                conn.commit()
                conn.close()
                print(f"[Paddle Webhook] Event {event_type} (Txn: {txn_id}, Action: {action}, Status: {status}) matches refund. Set status to refunded.")
            
    except Exception as e:
        print(f"Paddle Webhook processing error: {e}")
        return JSONResponse(content={"error": str(e)}, status_code=500)
        
    return {"status": "ok"}

def generate_watermark_page(text: str):
    import io
    from reportlab.pdfgen import canvas
    from reportlab.lib.pagesizes import A4
    packet = io.BytesIO()
    can = canvas.Canvas(packet, pagesize=A4)
    can.setFont("Helvetica", 8)
    can.setFillColorRGB(0.4, 0.4, 0.4)  # dark gray
    can.drawRightString(560, 20, text)
    can.save()
    packet.seek(0)
    return packet

def watermark_pdf(pdf_bytes: bytes, buyer_name: str, email: str, transaction_id: str) -> bytes:
    import io
    from pypdf import PdfReader, PdfWriter
    try:
        if buyer_name and buyer_name.strip():
            text = f"Licensed to: {buyer_name.strip()} ({email}) | Order #{transaction_id}"
        else:
            text = f"Licensed to: {email} | Order #{transaction_id}"
        
        watermark_pdf_stream = generate_watermark_page(text)
        watermark_reader = PdfReader(watermark_pdf_stream)
        watermark_page = watermark_reader.pages[0]
        
        reader = PdfReader(io.BytesIO(pdf_bytes))
        writer = PdfWriter()
        
        for page in reader.pages:
            page.merge_page(watermark_page)
            writer.add_page(page)
            
        output_stream = io.BytesIO()
        writer.write(output_stream)
        return output_stream.getvalue()
    except Exception as e:
        print(f"[Watermark] Error watermarking PDF: {e}")
        return pdf_bytes

def send_purchase_delivery_email(email: str, song_name: str, download_hash: str, locale: str = "en"):
    """Send purchase delivery email via Resend containing the download link."""
    api_key = load_settings().get("resend_api_key", "")
    if not api_key:
        print("[Notify] WARNING: resend_api_key not set in settings.json. Skipping purchase email.")
        return False
        
    download_url = f"https://meloscribe.dev/order/{download_hash}"
    
    is_de = locale.lower().startswith("de")
    if is_de:
        subject = f"Dein Lernpaket für {song_name} ist bereit! 🎹"
        header_title = "🎹 Dein Lernpaket ist bereit!"
        greeting = "Hallo!"
        thank_you = f"vielen Dank für deinen Kauf und die Unterstützung meiner Arrangements! Dein Lernpaket für <strong>{song_name}</strong> ist bereit."
        instruction = "Klicke auf den Button unten, um deine Noten (PDF), MIDI-Dateien und Video-Tutorials herunterzuladen:"
        button_text = "Lernpaket herunterladen"
        permanent_note = "Dieser Download-Link ist dauerhaft. Du kannst jederzeit darauf zugreifen, um Updates herunterzuladen oder deine Dateien abzurufen."
        signoff = "Viel Spaß beim Üben,<br>Tobias | meloscribe"
        help_text = f'Brauchst du Hilfe? Antworte direkt auf diese E-Mail oder besuche <a href="https://meloscribe.dev" style="color: #00f5d4; text-decoration: none;">meloscribe.dev</a>'
    else:
        subject = f"Your learning package for {song_name} is ready! 🎹"
        header_title = "🎹 Your Sheets Are Ready!"
        greeting = "Hey!"
        thank_you = f"Thank you so much for your purchase and supporting my arrangements! Your learning package for <strong>{song_name}</strong> is ready."
        instruction = "Click the button below to download your sheet music (PDF), MIDI files, and practice video tutorials:"
        button_text = "Download Learning Package"
        permanent_note = "This download link is permanent. You can access it anytime to download updates or get your files."
        signoff = "Happy practicing,<br>Tobias | meloscribe"
        help_text = f'Need help? Reply directly to this email or visit <a href="https://meloscribe.dev" style="color: #00f5d4; text-decoration: none;">meloscribe.dev</a>'
        
    html_body = f"""
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="font-family: 'Helvetica Neue', Arial, sans-serif; background: #0a0a0f; color: #e0e0e0; max-width: 520px; margin: 0 auto; padding: 32px 16px;">
  <div style="text-align: center; margin-bottom: 32px;">
    <h1 style="font-size: 26px; font-weight: 800; letter-spacing: 2px; margin: 0; font-family: 'Helvetica Neue', Arial, sans-serif; text-align: center;"><span style="color: #ff2d92;">m</span><span style="color: #eb3ca2;">e</span><span style="color: #d64bb2;">l</span><span style="color: #c25ac2;">o</span><span style="color: #ad69d2;">s</span><span style="color: #9978e2;">c</span><span style="color: #8487f2;">r</span><span style="color: #7096ff;">i</span><span style="color: #3caaff;">b</span><span style="color: #00f5ff;">e</span></h1>
    <p style="color: #888; font-size: 12px; margin-top: 4px;">Arranged by ear. Played by you.</p>
  </div>
  <div style="background: #12121c; border: 1px solid #2a2a3e; border-radius: 16px; padding: 32px;">
    <h2 style="color: #ffffff; font-size: 20px; margin-top: 0; margin-bottom: 16px; font-weight: 700; text-align: center;">{header_title}</h2>
    <p style="color: #b0b0c0; line-height: 1.8; font-size: 15px;">{greeting}</p>
    <p style="color: #b0b0c0; line-height: 1.8; font-size: 15px;">
      {thank_you}
    </p>
    <p style="color: #b0b0c0; line-height: 1.6; font-size: 15px;">{instruction}</p>
    
    <div style="text-align: center; margin: 28px 0;">
      <a href="{download_url}" style="display: inline-block; background-color: #12121c; border: 2px solid #00f5d4; color: #00f5d4; font-family: 'Helvetica Neue', Arial, sans-serif; font-weight: 700; font-size: 15px; padding: 14px 32px; border-radius: 10px; text-decoration: none; text-shadow: 0 0 8px rgba(0,245,212,0.35);">{button_text}</a>
    </div>
    
    <p style="color: #888; font-size: 13px; text-align: center;">
      {permanent_note}
    </p>
    
    <p style="color: #b0b0c0; line-height: 1.6; font-size: 15px; margin-top: 24px;">{signoff}</p>
  </div>
  <p style="text-align: center; font-size: 11px; color: #555; margin-top: 24px;">
    {help_text}
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
                "subject": subject,
                "html": html_body
            },
            timeout=10.0
        )
        if resp.status_code in (200, 201):
            print(f"[Notify] Purchase email sent successfully to {email} (locale: {locale})")
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
    
    if not row and checkout_id.startswith("demo_"):
        return {"download_hash": f"demo_hash_{checkout_id}"}
        
    if not row and checkout_id.startswith("txn_"):
        # FALLBACK: Webhook failed or was delayed. Query Paddle API directly!
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
                        
                        # Save purchase in local database
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
                        
                        # Send purchase delivery email using Resend!
                        if is_new:
                            send_purchase_delivery_email(email, song_title, download_hash, locale)
                        
                        return {"download_hash": download_hash}
        except Exception as api_err:
            print(f"[Paddle API Fallback] Error verifying transaction: {api_err}")
        
    if not row:
        return JSONResponse(content={"error": "Transaction not found"}, status_code=404)
        
    return {"download_hash": row[0]}

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
            "created_at": "25.10.2025, 14:32 Uhr",
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

# -------------------------------------------------------------------
# Admin / Package Management APIs
# -------------------------------------------------------------------
def verify_admin(request: Request):
    passcode = request.headers.get("x-admin-passcode")
    expected = load_settings().get("admin_passcode", "579110")
    if passcode != expected:
        raise HTTPException(status_code=401, detail="Unauthorized admin access")

# -------------------------------------------------------------------
# Dynamic Songs Catalog APIs
# -------------------------------------------------------------------
SONGS_JSON_PATH = Path(__file__).resolve().parent / "songs.json"

def load_songs_list():
    if not SONGS_JSON_PATH.exists():
        return []
    try:
        with open(SONGS_JSON_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"Error loading songs.json: {e}")
        return []

def save_songs_list(songs_list):
    try:
        with open(SONGS_JSON_PATH, "w", encoding="utf-8") as f:
            json.dump(songs_list, f, indent=2, ensure_ascii=False)
        return True
    except Exception as e:
        print(f"Error saving songs.json: {e}")
        return False

@app.get("/api/public/songs")
def get_public_songs():
    return load_songs_list()

@app.post("/api/admin/songs/add")
def admin_add_song(request: Request, payload: dict):
    verify_admin(request)
    songs_list = load_songs_list()
    
    new_song = payload.get("song")
    if not new_song or not new_song.get("title") or not new_song.get("artist"):
        raise HTTPException(status_code=400, detail="Song title and artist are required")
        
    ids = []
    for s in songs_list:
        if s.get("id") and s["id"] != "global_settings":
            try:
                ids.append(int(s["id"]))
            except ValueError:
                pass
    new_id = str(max(ids) + 1) if ids else "1"
    
    new_song["id"] = new_id
    new_song["hidden"] = new_song.get("hidden", False)
    new_song["difficulty"] = new_song.get("difficulty", "Easy")
    new_song["format"] = new_song.get("format", "full_arrangement")
    new_song["price"] = new_song.get("price", "6 €")
    new_song["kofiId"] = new_song.get("kofiId", "")
    new_song["youtubeUrl"] = new_song.get("youtubeUrl", "")
    new_song["tags"] = new_song.get("tags", [])
    
    songs_list.append(new_song)
    if save_songs_list(songs_list):
        return {"success": True, "song": new_song}
    else:
        raise HTTPException(status_code=500, detail="Failed to write songs catalog")

@app.post("/api/admin/songs/edit")
def admin_edit_song(request: Request, payload: dict):
    verify_admin(request)
    songs_list = load_songs_list()
    
    updated_song = payload.get("song")
    if not updated_song or not updated_song.get("id"):
        raise HTTPException(status_code=400, detail="Song data with ID is required")
        
    found = False
    for i, s in enumerate(songs_list):
        if s.get("id") == updated_song["id"]:
            songs_list[i] = {**s, **updated_song}
            found = True
            break
            
    if not found:
        raise HTTPException(status_code=404, detail="Song not found")
        
    if save_songs_list(songs_list):
        return {"success": True}
    else:
        raise HTTPException(status_code=500, detail="Failed to write songs catalog")

@app.post("/api/admin/songs/delete")
def admin_delete_song(request: Request, payload: dict):
    verify_admin(request)
    song_id = payload.get("id")
    if not song_id:
        raise HTTPException(status_code=400, detail="Song ID is required")
        
    songs_list = load_songs_list()
    initial_len = len(songs_list)
    songs_list = [s for s in songs_list if s.get("id") != song_id]
    
    if len(songs_list) == initial_len:
        raise HTTPException(status_code=404, detail="Song not found")
        
    if save_songs_list(songs_list):
        return {"success": True}
    else:
        raise HTTPException(status_code=500, detail="Failed to write songs catalog")

@app.get("/api/admin/packages")
def admin_list_packages(request: Request):
    verify_admin(request)
    
    r2_account_id = settings.get("r2_account_id") or os.environ.get("R2_ACCOUNT_ID")
    r2_access_key = settings.get("r2_access_key_id") or os.environ.get("R2_ACCESS_KEY_ID")
    r2_secret_key = settings.get("r2_secret_access_key") or os.environ.get("R2_SECRET_ACCESS_KEY")
    r2_bucket = settings.get("r2_bucket_name", "meloscribe-sheets") or os.environ.get("R2_BUCKET_NAME", "meloscribe-sheets")
    
    if not r2_account_id or not r2_access_key or not r2_secret_key:
        raise HTTPException(status_code=500, detail="Cloudflare R2 credentials are not configured in settings.json")
        
    import boto3
    from botocore.config import Config
    s3 = boto3.client(
        's3',
        endpoint_url=f'https://{r2_account_id}.r2.cloudflarestorage.com',
        aws_access_key_id=r2_access_key,
        aws_secret_access_key=r2_secret_key,
        config=Config(signature_version='s3v4')
    )
    
    try:
        res = s3.list_objects_v2(Bucket=r2_bucket)
        files = []
        if 'Contents' in res:
            for obj in res['Contents']:
                files.append({
                    "key": obj['Key'],
                    "size": obj['Size'],
                    "last_modified": obj['LastModified'].isoformat()
                })
        return {"files": files}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to list Cloudflare R2 files: {str(e)}")

@app.post("/api/admin/upload")
async def admin_upload_file(
    request: Request,
    song_name: str = Form(...),
    type: str = Form(...),
    file: UploadFile = File(...)
):
    verify_admin(request)
    
    filename = f"{song_name}.pdf" if type == "pdf" else \
               f"{song_name}.mid" if type == "midi" else \
               f"{song_name} slow.mid" if type == "midi_slow" else \
               f"{song_name}.mp4" if type == "video" else \
               f"{song_name} slow.mp4" if type == "video_slow" else \
               f"{song_name} Full Package.zip" if type == "zip" else file.filename
               
    if type == "zip":
        r2_key = filename
    else:
        r2_key = f"{song_name}/{filename}"
        
    r2_account_id = settings.get("r2_account_id") or os.environ.get("R2_ACCOUNT_ID")
    r2_access_key = settings.get("r2_access_key_id") or os.environ.get("R2_ACCESS_KEY_ID")
    r2_secret_key = settings.get("r2_secret_access_key") or os.environ.get("R2_SECRET_ACCESS_KEY")
    r2_bucket = settings.get("r2_bucket_name", "meloscribe-sheets") or os.environ.get("R2_BUCKET_NAME", "meloscribe-sheets")
    
    if not r2_account_id or not r2_access_key or not r2_secret_key:
        raise HTTPException(status_code=500, detail="Cloudflare R2 credentials are not configured in settings.json")
        
    import boto3
    from botocore.config import Config
    s3 = boto3.client(
        's3',
        endpoint_url=f'https://{r2_account_id}.r2.cloudflarestorage.com',
        aws_access_key_id=r2_access_key,
        aws_secret_access_key=r2_secret_key,
        config=Config(signature_version='s3v4')
    )
    
    try:
        content = await file.read()
        content_type = "application/pdf" if type == "pdf" else \
                       "audio/midi" if "midi" in type else \
                       "video/mp4" if "video" in type else \
                       "application/zip" if type == "zip" else "application/octet-stream"
                       
        s3.put_object(
            Bucket=r2_bucket,
            Key=r2_key,
            Body=content,
            ContentType=content_type
        )
        print(f"[Admin Upload] Successfully uploaded {r2_key} to R2")
        return {"success": True, "key": r2_key}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to upload file to Cloudflare R2: {str(e)}")

@app.post("/api/admin/delete")
def admin_delete_file(request: Request, payload: dict):
    verify_admin(request)
    r2_key = payload.get("key")
    if not r2_key:
        raise HTTPException(status_code=400, detail="R2 Key is required")
        
    r2_account_id = settings.get("r2_account_id") or os.environ.get("R2_ACCOUNT_ID")
    r2_access_key = settings.get("r2_access_key_id") or os.environ.get("R2_ACCESS_KEY_ID")
    r2_secret_key = settings.get("r2_secret_access_key") or os.environ.get("R2_SECRET_ACCESS_KEY")
    r2_bucket = settings.get("r2_bucket_name", "meloscribe-sheets") or os.environ.get("R2_BUCKET_NAME", "meloscribe-sheets")
    
    if not r2_account_id or not r2_access_key or not r2_secret_key:
        raise HTTPException(status_code=500, detail="Cloudflare R2 credentials are not configured in settings.json")
        
    import boto3
    from botocore.config import Config
    s3 = boto3.client(
        's3',
        endpoint_url=f'https://{r2_account_id}.r2.cloudflarestorage.com',
        aws_access_key_id=r2_access_key,
        aws_secret_access_key=r2_secret_key,
        config=Config(signature_version='s3v4')
    )
    
    try:
        s3.delete_object(Bucket=r2_bucket, Key=r2_key)
        print(f"[Admin Delete] Deleted {r2_key} from R2")
        return {"success": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete file from Cloudflare R2: {str(e)}")

@app.get("/api/admin/orders")
def admin_list_orders(request: Request):
    verify_admin(request)
    
    db_path = Path(__file__).resolve().parent / "analytics.db"
    conn = sqlite3.connect(str(db_path))
    c = conn.cursor()
    c.execute("SELECT transaction_id, email, song_name, amount, currency, status, download_hash, locale, buyer_name, download_count, created_at FROM purchases ORDER BY created_at DESC")
    rows = c.fetchall()
    conn.close()
    
    orders = []
    for row in rows:
        orders.append({
            "transaction_id": row[0],
            "email": row[1],
            "song_name": row[2],
            "amount": row[3],
            "currency": row[4],
            "status": row[5],
            "download_hash": row[6],
            "locale": row[7],
            "buyer_name": row[8],
            "download_count": row[9],
            "created_at": row[10]
        })
    return {"orders": orders}

@app.post("/api/admin/orders/reset")
def admin_reset_order_downloads(request: Request, payload: dict):
    verify_admin(request)
    transaction_id = payload.get("transaction_id")
    if not transaction_id:
        raise HTTPException(status_code=400, detail="Transaction ID required")
        
    db_path = Path(__file__).resolve().parent / "analytics.db"
    conn = sqlite3.connect(str(db_path))
    c = conn.cursor()
    c.execute("UPDATE purchases SET download_count = 0, downloaded_types = '' WHERE transaction_id = ?", (transaction_id,))
    conn.commit()
    conn.close()
    return {"success": True}

@app.post("/api/admin/orders/toggle-status")
def admin_toggle_order_status(request: Request, payload: dict):
    verify_admin(request)
    transaction_id = payload.get("transaction_id")
    new_status = payload.get("status")
    if not transaction_id or not new_status:
        raise HTTPException(status_code=400, detail="Transaction ID and status required")
        
    db_path = Path(__file__).resolve().parent / "analytics.db"
    conn = sqlite3.connect(str(db_path))
    c = conn.cursor()
    c.execute("UPDATE purchases SET status = ? WHERE transaction_id = ?", (new_status, transaction_id))
    conn.commit()
    conn.close()
    return {"success": True, "status": new_status}

@app.get("/api/download/request")
def request_download(hash: str, type: str, request: Request):
    if type not in ("pdf", "zip", "midi", "midi_slow", "video", "video_slow"):
        return JSONResponse(content={"error": "Invalid download type"}, status_code=400)
        
    db_path = Path(__file__).resolve().parent / "analytics.db"
    conn = sqlite3.connect(str(db_path), timeout=30.0)
    c = conn.cursor()
    c.execute("SELECT song_name, download_count, downloaded_types, status FROM purchases WHERE download_hash = ?", (hash,))
    row = c.fetchone()
    
    song_name = None
    download_count = 0
    downloaded_types = ""
    status = ""
    
    if row:
        song_name = row[0]
        download_count = row[1]
        downloaded_types = row[2] or ""
        status = row[3] or ""
    elif hash.startswith("demo_hash_"):
        song_name = "Sweetest Rain"
        download_count = 0
        status = "completed"
        print(f"[Download Request] Sandbox hash '{hash}' resolved to '{song_name}'")
        
    if not song_name:
        conn.close()
        return JSONResponse(content={"error": "Order not found"}, status_code=404)
        
    if status in ("inactive", "refunded", "deactivated"):
        conn.close()
        return JSONResponse(content={"error": "This order has been deactivated / refunded"}, status_code=403)
        
    # IP limits removed as requested
    if download_count >= 50:
        conn.close()
        return JSONResponse(content={"error": "Download limit reached (maximum 50 downloads allowed)"}, status_code=403)
        
    if row:
        types_list = [t.strip() for t in downloaded_types.split(",") if t.strip()]
        if type not in types_list:
            types_list.append(type)
        new_types_str = ",".join(types_list)
        download_count = download_count + 1
        c.execute("UPDATE purchases SET download_count = ?, downloaded_types = ? WHERE download_hash = ?", (download_count, new_types_str, hash))
        conn.commit()
    conn.close()
    
    # Return absolute URL pointing to our dynamic file downloader/redirector
    download_file_url = f"{request.base_url}api/download/file?hash={hash}&type={type}"
    return {"download_url": download_file_url, "download_count": download_count}

@app.get("/api/download/file")
def download_file(hash: str, type: str, request: Request):
    if type not in ("pdf", "zip", "midi", "midi_slow", "video", "video_slow"):
        return JSONResponse(content={"error": "Invalid download type"}, status_code=400)
        
    db_path = Path(__file__).resolve().parent / "analytics.db"
    conn = sqlite3.connect(str(db_path), timeout=30.0)
    c = conn.cursor()
    c.execute("SELECT song_name, email, transaction_id, buyer_name, status FROM purchases WHERE download_hash = ?", (hash,))
    row = c.fetchone()
    
    song_name = None
    email = None
    txn_id = None
    buyer_name = ""
    status = ""
    
    if row:
        song_name = row[0]
        email = row[1]
        txn_id = row[2]
        buyer_name = row[3] or ""
        status = row[4] or ""
    elif hash.startswith("demo_hash_"):
        song_name = "Sweetest Rain"
        email = "demo_customer@example.com"
        txn_id = "demo_12345"
        buyer_name = "Jane Doe"
        status = "completed"
        
    if not song_name:
        conn.close()
        return JSONResponse(content={"error": "Order not found"}, status_code=404)
        
    if status in ("inactive", "refunded", "deactivated"):
        conn.close()
        return JSONResponse(content={"error": "This order has been deactivated / refunded"}, status_code=403)
        
    # IP limits removed as requested
    conn.close()
    
    r2_account_id = settings.get("r2_account_id") or os.environ.get("R2_ACCOUNT_ID")
    r2_access_key = settings.get("r2_access_key_id") or os.environ.get("R2_ACCESS_KEY_ID")
    r2_secret_key = settings.get("r2_secret_access_key") or os.environ.get("R2_SECRET_ACCESS_KEY")
    r2_bucket = settings.get("r2_bucket_name", "meloscribe-sheets") or os.environ.get("R2_BUCKET_NAME", "meloscribe-sheets")
    
    if not r2_account_id or not r2_access_key or not r2_secret_key:
        print("[Download File] R2 credentials missing, using demo redirect fallback.")
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
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url=f"https://example.com/demo-packages/{song_name}{suffix}")
        
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
            config=Config(signature_version='s3v4')
        )
        
        if type == "pdf":
            print(f"[Download File] Fetching '{file_key}' from R2 for watermarking...")
            pdf_obj = s3.get_object(Bucket=r2_bucket, Key=file_key)
            original_pdf_bytes = pdf_obj['Body'].read()
            
            # Apply watermark dynamically
            watermarked_bytes = watermark_pdf(original_pdf_bytes, buyer_name, email, txn_id)
            
            from fastapi.responses import Response
            headers = {
                "Content-Disposition": f'attachment; filename="{song_name}.pdf"'
            }
            return Response(content=watermarked_bytes, media_type="application/pdf", headers=headers)
        else:
            # Presigned R2 redirect for ZIP, MIDI, and Video files
            presigned_url = s3.generate_presigned_url(
                ClientMethod='get_object',
                Params={'Bucket': r2_bucket, 'Key': file_key},
                ExpiresIn=900
            )
            from fastapi.responses import RedirectResponse
            return RedirectResponse(url=presigned_url)
            
    except Exception as e:
        print(f"[Download File] Error serving file: {e}")
        return JSONResponse(content={"error": f"Failed to serve file: {str(e)}"}, status_code=500)

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
  <div style="text-align: center; margin-bottom: 32px;">
    <h1 style="font-size: 24px; letter-spacing: 2px; margin: 0; font-family: 'Helvetica Neue', Arial, sans-serif;"><span style="color: #ff2d92;">m</span><span style="color: #eb3ca2;">e</span><span style="color: #d64bb2;">l</span><span style="color: #c25ac2;">o</span><span style="color: #ad69d2;">s</span><span style="color: #9978e2;">c</span><span style="color: #8487f2;">r</span><span style="color: #7096ff;">i</span><span style="color: #3caaff;">b</span><span style="color: #00f5ff;">e</span></h1>
    <p style="color: #888; font-size: 12px; margin-top: 4px;">piano &amp; sheet music</p>
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
    <p style="color: #b0b0c0; line-height: 1.6; font-size: 15px; margin-top: 24px;">Best,<br>The meloscribe team</p>
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

        file_key = f"{clean_name}/{clean_name}.mp4"

        s3 = boto3.client(
            's3',
            endpoint_url=f'https://{r2_account_id}.r2.cloudflarestorage.com',
            aws_access_key_id=r2_access_key,
            aws_secret_access_key=r2_secret_key,
            config=Config(signature_version='s3v4')
        )

        try:
            s3.head_object(Bucket=r2_bucket, Key=file_key)
        except Exception as head_err:
            print(f"[Preview Video] Video key '{file_key}' not found in R2 bucket '{r2_bucket}'. Falling back to default 'Mary On A Cross/Mary On A Cross.mp4'.")
            file_key = "Mary On A Cross/Mary On A Cross.mp4"

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

    def get_local_fallback():
        # Determine local path depending on environment
        if os.name == 'nt':
            local_path = r"C:\Dev\meloscribe-app\ShopVideos\Mary On A Cross.mp4"
        else:
            local_path = "/home/ubuntu/meloscribe/Scores/fallback.mp4"
        if os.path.exists(local_path):
            print(f"[Preview Video] Serving local fallback: {local_path}")
            return FileResponse(local_path, media_type="video/mp4")
        return None

    res = get_preview_video(song_name)
    if isinstance(res, JSONResponse):
        fb = get_local_fallback()
        if fb:
            return fb
        return res
    if not isinstance(res, dict):
        fb = get_local_fallback()
        if fb:
            return fb
        return JSONResponse(content={"error": "Invalid preview video response"}, status_code=500)
    download_url = res.get("download_url")
    if not download_url or "example.com" in download_url:
        fb = get_local_fallback()
        if fb:
            return fb
        return JSONResponse(content={"error": "Video URL not found"}, status_code=404)

    req_headers = {}
    range_header = request.headers.get("range")
    if range_header:
        req_headers["range"] = range_header

    try:
        r2_resp = requests.get(download_url, headers=req_headers, stream=True, timeout=15)
        if r2_resp.status_code >= 400:
            print(f"[Preview Video] R2 returned {r2_resp.status_code} for {download_url}. Trying local fallback.")
            fb = get_local_fallback()
            if fb:
                return fb
            return JSONResponse(content={"error": "Video not found in R2 and no local fallback available"}, status_code=404)

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
        print(f"[Preview Video] Failed streaming from R2: {e}. Trying local fallback.")
        fb = get_local_fallback()
        if fb:
            return fb
        return JSONResponse(content={"error": f"Failed to stream video: {str(e)}"}, status_code=500)


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
  <div style="text-align: center; margin-bottom: 32px;">
    <h1 style="font-size: 28px; font-weight: 800; letter-spacing: 2px; margin: 0; font-family: 'Helvetica Neue', Arial, sans-serif; text-align: center;"><span style="color: #ff2d92;">m</span><span style="color: #eb3ca2;">e</span><span style="color: #d64bb2;">l</span><span style="color: #c25ac2;">o</span><span style="color: #ad69d2;">s</span><span style="color: #9978e2;">c</span><span style="color: #8487f2;">r</span><span style="color: #7096ff;">i</span><span style="color: #3caaff;">b</span><span style="color: #00f5ff;">e</span></h1>
    <p style="color: #888; font-size: 12px; margin-top: 4px;">piano &amp; sheet music</p>
  </div>
  <div style="background: #12121c; border: 1px solid #2a2a3e; border-radius: 16px; padding: 32px;">
    <h2 style="color: #ffffff; font-size: 20px; margin-top: 0; margin-bottom: 16px; text-align: center; font-weight: 700;">🎵 New Sheet Music Released!</h2>
    <p style="color: #b0b0c0; line-height: 1.8; font-size: 15px;">
      Hey! A new piano arrangement has just been dropped on meloscribe.dev:
    </p>
    <div style="background: #0a0a0f; border-left: 4px solid #ff007f; padding: 16px; border-radius: 4px; margin: 24px 0;">
      <h3 style="color: #ffffff; margin: 0 0 8px 0; font-size: 18px;">{song_title}</h3>
      <p style="color: #888; margin: 0 0 12px 0; font-size: 14px;">by {artist}</p>
      <div style="margin-top: 12px;">
        <span style="display: inline-block; background: rgba(0, 245, 212, 0.1); border: 1px solid rgba(0, 245, 212, 0.4); color: #00f5d4; font-size: 12px; font-weight: 600; padding: 4px 10px; border-radius: 12px; margin-right: 8px;">{difficulty}</span>
        <span style="display: inline-block; background: rgba(255, 0, 127, 0.1); border: 1px solid rgba(255, 0, 127, 0.4); color: #ff007f; font-size: 12px; font-weight: 600; padding: 4px 10px; border-radius: 12px; margin-right: 8px;">{format_text}</span>
        <span style="display: inline-block; background: rgba(255, 255, 255, 0.1); border: 1px solid rgba(255, 255, 255, 0.2); color: #ffffff; font-size: 12px; font-weight: 600; padding: 4px 10px; border-radius: 12px;">{price}</span>
      </div>
    </div>
    <p style="color: #b0b0c0; line-height: 1.6; font-size: 15px; text-align: center;">Get your PDF sheet music, MIDI, and offline practice videos now:</p>
    <div style="text-align: center; margin: 28px 0;">
      <a href="{sheets_url}" style="display: inline-block; background-color: #12121c; border: 2px solid #00f5d4; color: #00f5d4; font-family: 'Helvetica Neue', Arial, sans-serif; font-weight: 700; font-size: 15px; padding: 14px 32px; border-radius: 10px; text-decoration: none; text-shadow: 0 0 8px rgba(0,245,212,0.35);">Get Sheet Music</a>
    </div>
    <p style="color: #b0b0c0; line-height: 1.6; font-size: 15px; margin-top: 24px;">Happy practicing,<br>The meloscribe team</p>
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

@app.post("/api/notify/broadcast")
async def notify_broadcast(req: BroadcastRequest):
    """Send new song notification to all active subscribers."""
    db_path = Path(__file__).resolve().parent / "analytics.db"
    try:
        conn = sqlite3.connect(str(db_path), timeout=30.0)
        c = conn.cursor()
        c.execute("SELECT email, token FROM notify_subscribers WHERE status = 'active'")
        subscribers = c.fetchall()
        conn.close()
    except Exception as e:
        return JSONResponse(content={"error": f"Database error: {str(e)}"}, status_code=500)
    
    if not subscribers:
        return {"status": "success", "sent_count": 0, "message": "No active subscribers found."}
        
    sent_count = 0
    for email, token in subscribers:
        success = _send_new_song_notification(email, token, req.title, req.artist, req.difficulty, req.format, req.price)
        if success:
            sent_count += 1
            
    return {"status": "success", "sent_count": sent_count, "total_subscribers": len(subscribers)}


# -------------------------------------------------------------------
# Main
# -------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8787)
