import os
import sys
import json
import sqlite3
import platform
import requests
import boto3
import shutil
import subprocess
import threading
import uuid
import re
import tempfile
import asyncio
from pathlib import Path
from pydantic import BaseModel
from fastapi import APIRouter, Request, HTTPException, Form, UploadFile, File
from fastapi.responses import JSONResponse, FileResponse, StreamingResponse

from shared import (
    settings,
    db_path,
    active_workflow_task,
    manager,
    log_error,
    verify_admin,
    load_settings,
    CREATION_FLAGS,
    TOOLS_DIR
)

router = APIRouter()

# -------------------------------------------------------------------
# Pydantic Request Models & Locks
# -------------------------------------------------------------------
class WorkflowRequest(BaseModel):
    song: str = ""
    author: str = ""
    theme: str = "warm"
    price: str = "4.00"
    format: str = "viral_part"
    shutdown: bool = False
    doR2: bool = True
    doKofi: bool = True
    doYoutube: bool = True
    doInstagram: bool = True
    doFacebook: bool = True
    doTiktok: bool = True
    doThreads: bool = True
    doPinterest: bool = True
    localUpload: bool = False
    zoom: float = 1.5
    shift: int = 0
    enableVisualizerNormal: bool = True
    enableVisualizerTutorial: bool = True
    enableVisualizerHook: bool = True
    enableMetronome: bool = True
    enablePortraitAddon: bool = True
    timesig: str = "auto"
    scheduleDate: str = ""
    scheduleTime: str = "16:00"
    phase: int = 1
    resumeFromStep: int = 0
    paddle_product_id: str = ""

process_lock = threading.Lock()
captured_youtube_urls: dict[str, str] = {}
is_batch_processing = False

# -------------------------------------------------------------------
# Process Runner (streams stdout -> WebSocket)
# -------------------------------------------------------------------
async def run_tool(cmd: list[str], label: str = ""):
    active_workflow_task["stop_requested"] = False
    loop = asyncio.get_event_loop()

    def _run():
        with process_lock:
            active_workflow_task["current_process"] = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                cwd=str(TOOLS_DIR),
                creationflags=CREATION_FLAGS
            )

        song_arg = None
        if "--song" in cmd:
            try:
                idx = cmd.index("--song")
                if idx + 1 < len(cmd):
                    song_arg = cmd[idx + 1]
            except Exception:
                pass

        for line in iter(active_workflow_task["current_process"].stdout.readline, ""):
            if active_workflow_task["stop_requested"]:
                break
            asyncio.run_coroutine_threadsafe(
                manager.broadcast({"type": "log", "message": line.rstrip()}),
                loop,
            )
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
            
            if "SUCCESS! Video uploaded at https://youtu.be/" in line:
                yt_url = line.split("at ")[-1].strip()
                if song_arg:
                    captured_youtube_urls[song_arg] = yt_url

        active_workflow_task["current_process"].wait()
        rc = active_workflow_task["current_process"].returncode
        active_workflow_task["current_process"] = None
        return rc

    rc = await loop.run_in_executor(None, _run)
    return rc

# -------------------------------------------------------------------
# One-Click Rendering Pipeline background thread
# -------------------------------------------------------------------
async def _run_workflow(req: WorkflowRequest):
    python = sys.executable
    song = req.song
    author = req.author

    await manager.broadcast({"type": "status", "message": f"Starting workflow for '{song}'..."})
    await manager.broadcast({"type": "progress", "value": 0})

    for folder_key in ["tiktok_dir", "covers_dir", "packages_dir"]:
        f_val = settings.get(folder_key)
        if f_val:
            try:
                os.makedirs(f_val, exist_ok=True)
            except Exception:
                pass

    cakewalk_dir = settings.get("cakewalk_dir", r"C:\Cakewalk Projects")
    
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
        if req.enableVisualizerNormal:
            cmd = [python, "-u", "keysight_bot.py", "--song", song, "--theme", req.theme]
            steps.append((cmd, "Render Keysight (Original Version)"))
            
            cmd_hb = [python, "-u", "handbrake_bot.py", "--input", str(Path(settings.get("keysight_dir", r"C:\Dev\meloscribe\Keysight export")) / f"{song}.mp4")]
            steps.append((cmd_hb, "Compress Original Video (Normal Speed)"))
            
            cmd_hb_slow = [python, "-u", "handbrake_bot.py", "--input", str(Path(settings.get("keysight_dir", r"C:\Dev\meloscribe\Keysight export")) / f"{song} slow.mp4")]
            steps.append((cmd_hb_slow, "Compress Original Video (Slow Speed)"))

        if has_easy and req.enableVisualizerTutorial:
            cmd = [python, "-u", "keysight_bot.py", "--song", f"{song} Easy", "--theme", req.theme]
            steps.append((cmd, "Render Keysight (Easy Version)"))
            
            cmd_hb_easy = [python, "-u", "handbrake_bot.py", "--input", str(Path(settings.get("keysight_dir", r"C:\Dev\meloscribe\Keysight export")) / f"{song} Easy.mp4")]
            steps.append((cmd_hb_easy, "Compress Easy Video (Normal Speed)"))
            
            cmd_hb_easy_slow = [python, "-u", "handbrake_bot.py", "--input", str(Path(settings.get("keysight_dir", r"C:\Dev\meloscribe\Keysight export")) / f"{song} Easy slow.mp4")]
            steps.append((cmd_hb_easy_slow, "Compress Easy Video (Slow Speed)"))
            
    elif req.phase == 2:
        zoom_val = str(req.zoom)
        shift_val = str(req.shift)
        
        versions = [("", song)]
        if has_easy:
            versions.append((" Easy", f"{song} Easy"))

        keysight_dir = Path(settings.get("keysight_dir", r"C:\Dev\meloscribe\Keysight export"))
        for suffix, folder_name in versions:
            v_song = f"{song}{suffix}"
            for vtype, prefix in [("normal", ""), ("tutorial", " slow")]:
                vid_in = str(keysight_dir / f"{v_song}{prefix}.mp4")
                midi_path = f"C:\\Cakewalk Projects\\{folder_name}\\{v_song}{prefix}.mid"
                
                cmd_portrait = [
                    python, "-u", "video_generator.py",
                    "--video", vid_in, "--title", v_song, "--author", author,
                    "--type", vtype, "--zoom", zoom_val, "--shift", shift_val,
                    "--midipath", midi_path, "--theme", req.theme
                ]
                if req.enablePortraitAddon:
                    cmd_portrait.append("--use_portrait_addon")
                steps.append((cmd_portrait, f"Generate Portrait Video ({v_song}{prefix})"))

                if req.format == "full_arrangement":
                    cmd_wide = [
                        python, "-u", "video_generator.py",
                        "--video", vid_in, "--title", v_song, "--author", author,
                        "--type", vtype, "--zoom", zoom_val, "--shift", shift_val,
                        "--midipath", midi_path, "--theme", req.theme, "--wide"
                    ]
                    steps.append((cmd_wide, f"Generate Widescreen Video ({v_song}{prefix})"))

        for suffix, folder_name in versions:
            v_song = f"{song}{suffix}"
            cmd = [python, "-u", "cover_generator.py", "--song", v_song, "--author", author, "--theme", req.theme]
            steps.append((cmd, f"Generate Cover Art ({v_song})"))

        for suffix, folder_name in versions:
            v_song = f"{song}{suffix}"
            cmd = [python, "-u", "musescore_launcher.py", "--song", v_song, "--author", author]
            steps.append((cmd, f"Launch MuseScore Layout ({v_song})"))

        if req.doR2:
            for suffix, folder_name in versions:
                v_song = f"{song}{suffix}"
                cmd = [python, "-u", "upload_bot.py", "--song", v_song, "--author", author, "--mode", "r2", "--format", req.format]
                steps.append((cmd, f"Cloudflare R2 Upload ({v_song})"))

        if req.localUpload:
            for suffix, folder_name in versions:
                v_song = f"{song}{suffix}"
                cmd = [python, "-u", "upload_bot.py", "--song", v_song, "--price", req.price, "--kofi_id", req.paddle_product_id or "prod_dummy123", "--mode", "website", "--author", author]
                steps.append((cmd, f"Local Catalog Sync ({v_song})"))

        socials = []
        if req.doYoutube: socials.append("youtube")
        if req.doInstagram: socials.append("instagram")
        if req.doFacebook: socials.append("facebook")
        if req.doTiktok: socials.append("tiktok")
        if req.doThreads: socials.append("threads")
        if req.doPinterest: socials.append("pinterest")

        for platform in socials:
            cmd = [python, "-u", "upload_bot.py", "--song", song, "--author", author, "--mode", platform, "--format", req.format]
            if req.scheduleDate:
                cmd.extend(["--schedule_date", req.scheduleDate, "--schedule_time", req.scheduleTime])
            steps.append((cmd, f"Social Upload ({platform})"))

    total = len(steps)
    if total == 0:
        await manager.broadcast({"type": "done", "message": "No tasks selected."})
        return

    start_idx = max(0, req.resumeFromStep)
    for i in range(start_idx, total):
        if active_workflow_task["stop_requested"]:
            await manager.broadcast({"type": "done", "message": "⏹️ Workflow stopped by user."})
            return

        cmd, label = steps[i]
        if label.startswith("Social Upload (pinterest)"):
            try:
                tokens_path = Path(__file__).resolve().parent / "pinterest_tokens.json"
                if not tokens_path.exists():
                    tokens_path = TOOLS_DIR / "pinterest_tokens.json"
                if tokens_path.exists():
                    with open(tokens_path, "r", encoding="utf-8") as f:
                        pin_config = json.load(f)
                    is_easy = song.lower().endswith("easy")
                    board_id = pin_config.get("pinterest_board_easy") if is_easy else pin_config.get("pinterest_board_intermediate")
                    if board_id:
                        cmd.extend(["--pinterest_board_id", board_id])
            except Exception as e:
                print(f"[Workflow] Error injecting Pinterest board ID: {e}")

        if label.startswith("Social Upload (tiktok)"):
            try:
                from tiktok_auth import get_valid_token
                token = get_valid_token()
                if token:
                    cmd.extend(["--tiktok_token", token])
            except Exception as e:
                print(f"[Workflow] Error injecting TikTok token: {e}")

        if label.startswith("Social Upload (youtube)") or label.startswith("Social Upload (instagram)") or label.startswith("Social Upload (facebook)") or label.startswith("Social Upload (threads)"):
            try:
                from settings import load_settings
                s_dict = load_settings()
                if platform == "youtube" and s_dict.get("yt_category"):
                    cmd.extend(["--yt_category", s_dict.get("yt_category")])
            except Exception as e:
                print(f"[Workflow] Error injecting social settings: {e}")

        if label.startswith("Ko-Fi Upload") and captured_youtube_urls:
            try:
                # Extract clean song key case-insensitively
                for kofi_song in captured_youtube_urls:
                    if kofi_song.lower().strip() in label.lower():
                        if kofi_song in captured_youtube_urls:
                            cmd.extend(["--youtube_url", captured_youtube_urls[kofi_song]])
            except Exception as e:
                print(f"[Workflow] Error injecting YouTube URL: {e}")
            
        await manager.broadcast({"type": "status", "message": f"[{i+1}/{total}] {label}..."})
        await manager.broadcast({"type": "progress", "value": i / total})
        rc = await run_tool(cmd, label)
        if rc != 0 and not active_workflow_task["stop_requested"]:
            await manager.broadcast({"type": "done", "message": f"❌ {label} failed (exit code {rc}). Resume from step {i}."})
            return

    await manager.broadcast({"type": "progress", "value": 1.0})
    await manager.broadcast({"type": "done", "message": "🎉 Workflow completed!"})

# -------------------------------------------------------------------
# Batch queue worker logic
# -------------------------------------------------------------------
def batch_processor_worker():
    global is_batch_processing
    is_batch_processing = True
    
    python = sys.executable
    log_error("[Batch Worker] Background processor loop started.")
    
    try:
        while True:
            conn = sqlite3.connect(str(db_path), timeout=30.0)
            c = conn.cursor()
            c.execute("SELECT song_name, author, theme, price, format, difficulty FROM batch_ingest_queue WHERE status = 'initialized' ORDER BY id ASC")
            rows = c.fetchall()
            conn.close()
            
            target_item = None
            for row in rows:
                song_name = row[0]
                audio_path = Path(settings.get("cakewalk_dir", r"C:\Cakewalk Projects")) / song_name / "Audio Export" / f"{song_name}.wav"
                if audio_path.exists():
                    target_item = row
                    break
                    
            if not target_item:
                log_error("[Batch Worker] No more initialized queue items with ready audio files. Stopping loop.")
                break
                
            song_name, author, theme, price, fmt, difficulty = target_item
            
            conn = sqlite3.connect(str(db_path), timeout=30.0)
            c = conn.cursor()
            c.execute("UPDATE batch_ingest_queue SET status = 'processing', error_message = NULL WHERE song_name = ?", (song_name,))
            conn.commit()
            conn.close()
            
            log_error(f"[Batch Worker] Started processing '{song_name}'")
            
            should_abort_queue = False
            try:
                has_easy = (difficulty == "both")
                steps = []
                
                # Original Render
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
                    # MuseScore launcher
                    steps.append([python, "-u", str(TOOLS_DIR / "musescore_launcher.py"), "--song", v_song, "--author", author])
                    # R2 Upload
                    steps.append([python, "-u", str(TOOLS_DIR / "upload_bot.py"), "--song", v_song, "--author", author, "--mode", "r2", "--format", fmt])
                    # Catalog sync
                    steps.append([python, "-u", str(TOOLS_DIR / "upload_bot.py"), "--song", v_song, "--price", price, "--kofi_id", "prod_dummy123", "--mode", "website", "--author", author])
                
                success = True
                err_msg = ""
                total_steps = len(steps)
                for step_idx, cmd in enumerate(steps):
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
                            stdout_lines.append(line)
                            if line.startswith("PROGRESS:"):
                                try:
                                    pct = int(line.split(":")[1].replace("%", "").strip())
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
                    conn = sqlite3.connect(str(db_path), timeout=30.0)
                    c = conn.cursor()
                    c.execute("UPDATE batch_ingest_queue SET status = 'active', processed_at = CURRENT_TIMESTAMP WHERE song_name = ?", (song_name,))
                    conn.commit()
                    conn.close()
                    log_error(f"[Batch Worker] Successfully processed '{song_name}'")
                else:
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

# -------------------------------------------------------------------
# REST Endpoints
# -------------------------------------------------------------------
@router.post("/api/workflow/start")
async def start_workflow(req: WorkflowRequest):
    asyncio.create_task(_run_workflow(req))
    return {"status": "started"}

@router.post("/api/workflow/stop")
def stop_workflow():
    active_workflow_task["stop_requested"] = True
    proc = active_workflow_task["current_process"]
    if proc:
        try:
            subprocess.Popen(f"taskkill /F /T /PID {proc.pid}", shell=True,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             creationflags=CREATION_FLAGS)
        except Exception:
            pass
    return {"status": "stop requested"}

@router.post("/api/module/{module}")
def run_individual_module(module: str, req: dict):
    python = sys.executable
    song = req.get("song")
    author = req.get("author", "Traditional")
    theme = req.get("theme", "warm")
    price = req.get("price", "6.00")
    fmt = req.get("format", "full_arrangement")
    
    if not song:
        raise HTTPException(status_code=400, detail="Song name required")
        
    cmd = []
    if module == "keysight":
        cmd = [python, "-u", "keysight_bot.py", "--song", song, "--theme", theme]
    elif module == "handbrake":
        keysight_dir = Path(settings.get("keysight_dir", r"C:\Dev\meloscribe\Keysight export"))
        vid_in = str(keysight_dir / f"{song}.mp4")
        cmd = [python, "-u", "handbrake_bot.py", "--input", vid_in]
    elif module == "video_generator":
        keysight_dir = Path(settings.get("keysight_dir", r"C:\Dev\meloscribe\Keysight export"))
        vid_in = str(keysight_dir / f"{song}.mp4")
        midi_path = f"C:\\Cakewalk Projects\\{song}\\{song}.mid"
        cmd = [python, "-u", "video_generator.py", "--video", vid_in, "--title", song, "--author", author, "--type", "normal", "--zoom", "1.5", "--midipath", midi_path, "--theme", theme, "--use_portrait_addon"]
    elif module == "cover_generator":
        cmd = [python, "-u", "cover_generator.py", "--song", song, "--author", author, "--theme", theme]
    elif module == "musescore":
        cmd = [python, "-u", "musescore_launcher.py", "--song", song, "--author", author]
    elif module == "upload_r2":
        cmd = [python, "-u", "upload_bot.py", "--song", song, "--author", author, "--mode", "r2", "--format", fmt]
    elif module == "upload_website":
        cmd = [python, "-u", "upload_bot.py", "--song", song, "--price", price, "--kofi_id", "prod_dummy123", "--mode", "website", "--author", author]
    else:
        raise HTTPException(status_code=400, detail=f"Unknown module '{module}'")
        
    threading.Thread(target=lambda: asyncio.run(run_tool(cmd, f"Module: {module}")), daemon=True).start()
    return {"status": "started"}

@router.get("/api/workflow/suggest-date")
def get_suggested_date():
    from datetime import datetime as dt, timedelta
    db_path = Path(__file__).resolve().parent / "analytics.db"
    try:
        conn = sqlite3.connect(str(db_path))
        c = conn.cursor()
        c.execute("""
            CREATE TABLE IF NOT EXISTS social_posts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                song_name TEXT,
                platform TEXT,
                scheduled_time TEXT,
                published_at TEXT
            )
        """)
        conn.commit()
        c.execute("SELECT scheduled_time FROM social_posts WHERE scheduled_time IS NOT NULL ORDER BY scheduled_time DESC LIMIT 1")
        row = c.fetchone()
        conn.close()
    except Exception:
        row = None
        
    if row:
        try:
            last_date = dt.fromisoformat(row[0])
            suggested = last_date + timedelta(days=2)
            if suggested < dt.now():
                suggested = dt.now() + timedelta(days=1)
            return {"suggested": suggested.date().isoformat()}
        except Exception:
            pass
            
    suggested = dt.now() + timedelta(days=1)
    return {"suggested": suggested.date().isoformat()}

# -------------------------------------------------------------------
# Website catalog modification
# -------------------------------------------------------------------
def get_songs_path():
    if platform.system() == "Windows":
        return r"c:\Dev\meloscribe-frontend\website\src\data\songs.json"
    else:
        # Resolve path relative to script directory on production VM
        return str(Path(__file__).resolve().parent / "songs.json")

@router.get("/api/website/songs")
def get_website_songs():
    songs_path = get_songs_path()
    if not os.path.exists(songs_path):
        return []
    try:
        with open(songs_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        log_error("Website Songs", f"Failed to load songs: {e}")
        return []

@router.delete("/api/website/songs/{song_id}")
def delete_website_song(song_id: str, delete_assets: bool = False):
    songs_path = get_songs_path()
    if not os.path.exists(songs_path):
        raise HTTPException(status_code=404, detail="songs.json not found")
        
    try:
        with open(songs_path, "r", encoding="utf-8") as f:
            songs = json.load(f)
            
        song_to_delete = next((s for s in songs if s.get("id") == song_id), None)
        if not song_to_delete:
            raise HTTPException(status_code=404, detail=f"Song with ID {song_id} not found in catalog")
            
        song_name = song_to_delete.get("title", "")
        updated_songs = [s for s in songs if s.get("id") != song_id]
        
        with open(songs_path, "w", encoding="utf-8") as f:
            json.dump(updated_songs, f, indent=2, ensure_ascii=False)
            
        print(f"[Catalog DELETE] Removed '{song_name}' (ID: {song_id}) from catalog.")
        
        # Git Commit & Push
        threading.Thread(target=run_git_push, daemon=True).start()
        
        if delete_assets and song_name:
            threading.Thread(target=lambda: run_deep_asset_cleanup(song_name), daemon=True).start()
            return {"status": "success", "message": f"Successfully deleted '{song_name}' from catalog. Deep asset cleanup scheduled in background."}
            
        return {"status": "success", "message": f"Successfully deleted '{song_name}' from catalog."}
    except Exception as e:
        log_error("Website Songs", f"Failed to delete song: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/api/website/songs")
async def add_website_song(request: Request):
    payload = await request.json()
    songs_path = get_songs_path()
    if not os.path.exists(songs_path):
        raise HTTPException(status_code=404, detail="songs.json not found")
        
    try:
        if isinstance(payload, list):
            # Frontend sent the entire songs array
            with open(songs_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2, ensure_ascii=False)
            print(f"[Catalog Update] Overwrote entire catalog list ({len(payload)} songs)")
        else:
            # Frontend sent a single song object
            with open(songs_path, "r", encoding="utf-8") as f:
                songs = json.load(f)
                
            existing_idx = next((i for i, s in enumerate(songs) if s.get("id") == payload.get("id")), -1)
            if existing_idx != -1:
                songs[existing_idx] = payload
                print(f"[Catalog Update] Updated existing song '{payload.get('title')}'")
            else:
                songs.append(payload)
                print(f"[Catalog Add] Added new song '{payload.get('title')}'")
                
            with open(songs_path, "w", encoding="utf-8") as f:
                json.dump(songs, f, indent=2, ensure_ascii=False)
            
        threading.Thread(target=run_git_push, daemon=True).start()
        return {"status": "success"}
    except Exception as e:
        log_error("Website Songs", f"Failed to save song: {e}")
        raise HTTPException(status_code=500, detail=str(e))

def run_git_push():
    try:
        frontend_dir = r"C:\Dev\meloscribe-frontend"
        if os.path.exists(frontend_dir):
            subprocess.run(["git", "add", "website/src/data/songs.json"], cwd=frontend_dir, check=True, creationflags=CREATION_FLAGS)
            subprocess.run(["git", "commit", "-m", "Auto-sync songs.json from app"], cwd=frontend_dir, check=True, creationflags=CREATION_FLAGS)
            subprocess.run(["git", "push"], cwd=frontend_dir, check=True, creationflags=CREATION_FLAGS)
            print("[Git push] Catalog songs.json successfully pushed to remote repository.")
    except Exception as e:
        print(f"[Git push] Error: {e}")

def run_deep_asset_cleanup(song_name: str):
    log_error("Deep Cleanup", f"Starting deep cleanup for '{song_name}' assets...")
    cakewalk_dir = settings.get("cakewalk_dir", r"C:\Cakewalk Projects")
    packages_dir = settings.get("packages_dir", r"C:\Dev\meloscribe\packages")
    
    # 1. Local Cakewalk Directories
    folders_to_delete = [
        Path(cakewalk_dir) / song_name,
        Path(cakewalk_dir) / f"{song_name} Easy"
    ]
    for folder in folders_to_delete:
        if folder.exists():
            try:
                shutil.rmtree(folder)
                log_error("Deep Cleanup", f"Deleted local directory: {folder}")
            except Exception as e:
                log_error("Deep Cleanup", f"Failed to delete {folder}: {e}")
                
    # 2. Local Customer Package ZIP
    local_zip = Path(packages_dir) / f"{song_name} Full Package.zip"
    if local_zip.exists():
        try:
            os.remove(local_zip)
            log_error("Deep Cleanup", f"Deleted local package: {local_zip}")
        except Exception as e:
            log_error("Deep Cleanup", f"Failed to delete package {local_zip}: {e}")
            
    # 3. Cloudflare R2 Assets
    r2_account_id = settings.get("r2_account_id") or os.environ.get("R2_ACCOUNT_ID")
    r2_access_key = settings.get("r2_access_key_id") or os.environ.get("R2_ACCESS_KEY_ID")
    r2_secret_key = settings.get("r2_secret_access_key") or os.environ.get("R2_SECRET_ACCESS_KEY")
    r2_bucket = settings.get("r2_bucket_name", "meloscribe-sheets") or os.environ.get("R2_BUCKET_NAME", "meloscribe-sheets")
    
    if r2_account_id and r2_access_key and r2_secret_key:
        try:
            s3 = boto3.client(
                's3',
                endpoint_url=f'https://{r2_account_id}.r2.cloudflarestorage.com',
                aws_access_key_id=r2_access_key,
                aws_secret_access_key=r2_secret_key
            )
            
            # List all objects with song_name prefix (i.e. 'Silent Night/')
            prefix = f"{song_name}/"
            res = s3.list_objects_v2(Bucket=r2_bucket, Prefix=prefix)
            if 'Contents' in res:
                for obj in res['Contents']:
                    key = obj['Key']
                    s3.delete_object(Bucket=r2_bucket, Key=key)
                    log_error("Deep Cleanup", f"Deleted R2 object: {key}")
                    
            # Delete ZIP file if present
            zip_key = f"{song_name} Full Package.zip"
            try:
                s3.delete_object(Bucket=r2_bucket, Key=zip_key)
                log_error("Deep Cleanup", f"Deleted R2 object: {zip_key}")
            except Exception:
                pass
                
        except Exception as e:
            log_error("Deep Cleanup", f"Cloudflare R2 API deletion failed: {e}")
            
    log_error("Deep Cleanup", f"Deep cleanup for '{song_name}' completed successfully.")

# -------------------------------------------------------------------
# Smart Batch Queue endpoints
# -------------------------------------------------------------------
@router.post("/api/batch/initialize")
async def batch_initialize(
    metadata: str = Form(...),
    files: list[UploadFile] = File(...)
):
    try:
        items = json.loads(metadata)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid metadata JSON: {str(e)}")

    conn = sqlite3.connect(str(db_path), timeout=30.0)
    c = conn.cursor()

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

            song_dir = Path(cakewalk_dir) / song_name
            os.makedirs(str(song_dir), exist_ok=True)

            midi_path = song_dir / f"{song_name}.mid"
            with open(midi_path, "wb") as f:
                shutil.copyfileobj(file.file, f)

            if include_easy:
                easy_dir = Path(cakewalk_dir) / f"{song_name} Easy"
                os.makedirs(str(easy_dir), exist_ok=True)
                file.file.seek(0)
                easy_midi_path = easy_dir / f"{song_name} Easy.mid"
                with open(easy_midi_path, "wb") as f:
                    shutil.copyfileobj(file.file, f)

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

@router.get("/api/batch/queue")
def get_batch_queue():
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

@router.post("/api/batch/retry")
async def retry_batch_item(req: dict):
    song_name = req.get("song_name")
    if not song_name:
        raise HTTPException(status_code=400, detail="song_name is required")
    try:
        conn = sqlite3.connect(str(db_path), timeout=30.0)
        c = conn.cursor()
        c.execute("UPDATE batch_ingest_queue SET status = 'initialized', error_message = NULL, progress = 0 WHERE song_name = ?", (song_name,))
        conn.commit()
        conn.close()

        global is_batch_processing
        if not is_batch_processing:
            threading.Thread(target=batch_processor_worker, daemon=True).start()

        return {"status": "success", "message": f"Reset status of '{song_name}' to initialized and started queue worker"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/api/batch/delete")
async def delete_batch_item(req: dict):
    song_name = req.get("song_name")
    if not song_name:
        raise HTTPException(status_code=400, detail="song_name is required")
    try:
        conn = sqlite3.connect(str(db_path), timeout=30.0)
        c = conn.cursor()
        c.execute("DELETE FROM batch_ingest_queue WHERE song_name = ?", (song_name,))
        conn.commit()
        conn.close()
        return {"status": "success", "message": f"Removed '{song_name}' from the queue"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/api/batch/process")
def trigger_batch_process():
    global is_batch_processing
    if is_batch_processing:
        return {"status": "already_running"}
        
    threading.Thread(target=batch_processor_worker, daemon=True).start()
    return {"status": "started"}

@router.post("/api/batch/set-hook")
async def set_hook(req: dict):
    song_name = req.get("song_name")
    hook_start = req.get("hook_start")
    hook_end = req.get("hook_end")
    if not song_name:
        raise HTTPException(status_code=400, detail="song_name is required")
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

@router.get("/api/batch/stream-keysight")
def stream_keysight_video(song_name: str, request: Request):
    keysight_dir = Path(settings.get("keysight_dir", r"C:\Dev\meloscribe\Keysight export"))
    raw_path = keysight_dir / "RAW" / f"{song_name}_RAW.mp4"
    compressed_path = keysight_dir / f"{song_name}.mp4"
    video_path = raw_path if raw_path.exists() else compressed_path
    if not video_path.exists():
        raise HTTPException(status_code=404, detail=f"No Keysight video found for '{song_name}'")
    
    file_size = video_path.stat().st_size
    range_header = request.headers.get("range")
    
    if range_header:
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

@router.post("/api/batch/regenerate-preview")
async def regenerate_preview(req: dict):
    song_name = req.get("song_name")
    hook_start = req.get("hook_start")
    hook_end = req.get("hook_end")
    if not song_name:
        raise HTTPException(status_code=400, detail="song_name is required")
    
    try:
        conn = sqlite3.connect(str(db_path), timeout=30.0)
        conn.execute(
            "UPDATE batch_ingest_queue SET hook_start=?, hook_end=? WHERE song_name=?",
            (hook_start, hook_end, song_name)
        )
        conn.commit()
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
    
    font_title = TOOLS_DIR / "fonts" / "arno_pro.ttf"
    font_artist = TOOLS_DIR / "fonts" / "montserrat.ttf"
    
    font_title_esc = escape_path_for_ffmpeg(str(font_title))
    font_artist_esc = escape_path_for_ffmpeg(str(font_artist))
    
    import uuid as _uid
    uid = _uid.uuid4().hex[:8]
    temp_dir = tempfile.gettempdir()
    
    title_txt = os.path.join(temp_dir, f"_title_{uid}.txt")
    artist_txt = os.path.join(temp_dir, f"_artist_{uid}.txt")
    
    with open(title_txt, "w", encoding="utf-8") as f:
        f.write(song_name)
    with open(artist_txt, "w", encoding="utf-8") as f:
        f.write(author)
        
    title_txt_esc = escape_path_for_ffmpeg(title_txt)
    artist_txt_esc = escape_path_for_ffmpeg(artist_txt)
    
    filter_complex = (
        f"[0:v]drawtext=fontfile='{font_title_esc}':textfile='{title_txt_esc}':fontcolor=white:fontsize={title_size}"
        f":x=(w-text_w)/2:y=(h/2)-{int(height*0.06)}:shadowcolor=black@0.6:shadowx=4:shadowy=4"
        f":alpha='if(lt(t,1),t,if(lt(t,3.5),1,if(lt(t,4.5),4.5-t,0)))'[v1]; "
        
        f"[v1]drawtext=fontfile='{font_artist_esc}':textfile='{artist_txt_esc}':fontcolor=white:fontsize={artist_size}"
        f":x=(w-text_w)/2:y=(h/2)+{int(height*0.05)}:shadowcolor=black@0.6:shadowx=3:shadowy=3"
        f":alpha='if(lt(t,1),t,if(lt(t,3.5),1,if(lt(t,4.5),4.5-t,0)))'"
    )
    
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
    rc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=creation_flags).returncode
    
    try:
        os.remove(title_txt)
        os.remove(artist_txt)
    except Exception:
        pass
    
    if rc != 0:
        raise HTTPException(status_code=500, detail="FFmpeg failed to generate preview clip")
    
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
            cmd_mp3 = [
                "ffmpeg", "-y",
                "-i", str(wav_path),
                "-c:a", "libmp3lame", "-b:a", "128k",
                str(dest_mp3)
            ]
            rc_mp3 = subprocess.run(cmd_mp3, stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=creation_flags).returncode
            if rc_mp3 == 0:
                log_error(f"[Preview Regen] Success: Audio hover MP3 cropped to hook.")
                mp3_generated = True
            else:
                log_error(f"[Preview Regen] Error: FFmpeg failed to crop audio hover.")
        except Exception as e:
            log_error(f"[Preview Regen] Audio crop error: {e}")
            
    r2_account_id = settings.get("r2_account_id")
    r2_access_key = settings.get("r2_access_key") or settings.get("r2_access_key_id")
    r2_secret_key = settings.get("r2_secret_key") or settings.get("r2_secret_access_key")
    r2_bucket = settings.get("r2_bucket") or settings.get("r2_bucket_name", "meloscribe-assets")
    uploaded = False
    if r2_account_id and r2_access_key and r2_secret_key:
        try:
            s3 = boto3.client(
                's3',
                endpoint_url=f'https://{r2_account_id}.r2.cloudflarestorage.com',
                aws_access_key_id=r2_access_key,
                aws_secret_access_key=r2_secret_key
            )
            
            vid_key = f"{song_name}/{song_name}_preview.mp4"
            s3.upload_file(
                str(dest_preview), r2_bucket, vid_key,
                ExtraArgs={"ContentType": "video/mp4"}
            )
            log_error(f"[Preview Regen] Uploaded {vid_key} to R2 bucket '{r2_bucket}'.")
            
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
# Ko-Fi sync (Backward compatible endpoint)
# -------------------------------------------------------------------
@router.post("/api/kofi/sync")
async def kofi_sync():
    """Manually trigger legacy Ko-Fi sales CSV sync."""
    def _run():
        import importlib.util
        sync_path = str(TOOLS_DIR / "meloscribe" / "backend" / "kofi_csv_sync.py")
        if os.path.exists(sync_path):
            spec = importlib.util.spec_from_file_location("kofi_csv_sync", sync_path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            mod.sync_kofi_sales()
    threading.Thread(target=_run, daemon=True).start()
    return {"status": "sync started"}

@router.get("/api/kofi/status")
def kofi_status():
    """Check status of Ko-Fi connection (mocked)."""
    return {"authorized": True, "message": "Legacy Ko-Fi connector active."}

@router.post("/api/kofi/webhook")
def kofi_webhook(payload: dict):
    return {"status": "success", "message": "Legacy Ko-Fi webhook captured."}

@router.post("/api/kofi/manual-sale")
def kofi_manual_sale(payload: dict):
    return {"status": "success", "message": "Legacy manual sale ignored."}

@router.get("/api/kofi/messages")
def kofi_messages():
    return []

@router.post("/api/kofi/messages/{msg_id}/read")
def kofi_messages_read(msg_id: str):
    return {"status": "success"}
