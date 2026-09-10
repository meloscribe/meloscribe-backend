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
    subtitle: str = ""
    subtitle_normal: str = ""
    subtitle_slow: str = ""
    subtitle_hook: str = ""
    subtitle_easy_normal: str = ""
    subtitle_easy_slow: str = ""
    theme: str = "warm"
    price: str = "4.00"
    format: str = "viral_part"
    shutdown: bool = False
    doR2: bool = True
    doKofi: bool = False
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
    enableVisualizerWideNormal: bool = True
    enableVisualizerWideTutorial: bool = True
    enableVisualizerHook: bool = True
    enableMetronome: bool = True
    enablePortraitAddon: bool = True
    manual_crop: bool = False
    timesig: str = "auto"
    metro_offset: float = 0.0
    scheduleDate: str = ""
    scheduleTime: str = "16:00"
    phase: int = 1
    resumeFromStep: int = 0
    paddle_product_id: str = ""
    hook_start: float = 0.0
    hook_end: float = 60.0

process_lock = threading.Lock()
captured_youtube_urls: dict[str, str] = {}
is_batch_processing = False

# -------------------------------------------------------------------
# Process Runner (streams stdout -> WebSocket)
# -------------------------------------------------------------------
workflow_global_progress = 0.0

async def run_tool(cmd: list[str], label: str = "", base_progress: float = 0.0, slice_weight: float = 0.1):
    global workflow_global_progress
    if active_workflow_task.get("stop_requested"):
        print(f"[Workflow] Stop requested — skipping execution of {label}")
        return -1
    loop = asyncio.get_event_loop()

    def _run():
        global workflow_global_progress
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

        proc = active_workflow_task.get("current_process")
        if proc and proc.stdout:
            import time
            while True:
                line = proc.stdout.readline()
                if not line:
                    if proc.poll() is not None:
                        break
                    time.sleep(0.05)
                    continue

                if active_workflow_task.get("stop_requested"):
                    break
                
                # Write to persistent log file
                try:
                    log_file = Path(__file__).resolve().parent / "backend_logs.txt"
                    with open(log_file, "a", encoding="utf-8") as lf:
                        lf.write(f"[{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [{label}] {line}")
                except Exception:
                    pass

                asyncio.run_coroutine_threadsafe(
                    manager.broadcast({"type": "log", "message": line.rstrip()}),
                    loop,
                )
                if line.startswith("PROGRESS:") or line.startswith("VIS_PROGRESS:"):
                    try:
                        pct = int(line.split(":")[1].replace("%", "").strip().split("(")[0])
                        calculated = base_progress + ((pct / 100.0) * slice_weight)
                        workflow_global_progress = max(workflow_global_progress, min(0.99, calculated))
                        asyncio.run_coroutine_threadsafe(
                            manager.broadcast({"type": "progress", "value": workflow_global_progress}),
                            loop,
                        )
                    except Exception:
                        pass
                elif "[R2 Upload] Progress:" in line:
                    try:
                        pct_str = line.split("Progress:")[1].split("%")[0].strip()
                        pct = float(pct_str)
                        calculated = base_progress + ((pct / 100.0) * slice_weight)
                        workflow_global_progress = max(workflow_global_progress, min(0.99, calculated))
                        asyncio.run_coroutine_threadsafe(
                            manager.broadcast({"type": "progress", "value": workflow_global_progress}),
                            loop,
                        )
                    except Exception:
                        pass
                
                if "SUCCESS! Video uploaded at https://youtu.be/" in line:
                    yt_url = line.split("at ")[-1].strip()
                    if song_arg:
                        captured_youtube_urls[song_arg] = yt_url

        rc = 0
        if proc:
            rc_wait = proc.wait()
            rc = rc_wait if rc_wait is not None else (proc.returncode if proc.returncode is not None else 0)
        active_workflow_task["current_process"] = None
        return rc

    rc = await loop.run_in_executor(None, _run)
    return rc

# -------------------------------------------------------------------
# One-Click Rendering Pipeline background thread
# -------------------------------------------------------------------
async def _run_workflow(req: WorkflowRequest):
    active_workflow_task["stop_requested"] = False  # Reset lock!
    active_workflow_task["pause_requested"] = False # Reset pause!
    active_workflow_task["waiting_for_crop"] = False # Reset crop wait!
    active_workflow_task["waiting_for_hook"] = False # Reset hook wait!
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
    versions = [("", song)]
    if has_easy:
        versions.append((" Easy", f"{song} Easy"))

    # --- Phase 1: Ingest & Render Keysight ---
    for suffix, folder_name in versions:
        v_song = f"{song}{suffix}"
        cmd = [python, "-u", "musescore_launcher.py", "--song", v_song, "--author", author, "--no_wait"]
        steps.append((cmd, f"Launch MuseScore Layout ({v_song})"))

    # --- Step 1: Render Keysight & Compress ---
    cmd = [python, "-u", "keysight_bot.py", "--song", song, "--theme", req.theme]
    steps.append((cmd, "Render Keysight (Original Version)"))
    
    cmd_hb = [python, "-u", "handbrake_bot.py", "--input", str(Path(settings.get("keysight_dir", r"C:\Dev\meloscribe\Keysight export")) / f"{song}.mp4")]
    steps.append((cmd_hb, "Compress Original Video (Normal Speed)"))
    
    cmd_hb_slow = [python, "-u", "handbrake_bot.py", "--input", str(Path(settings.get("keysight_dir", r"C:\Dev\meloscribe\Keysight export")) / f"{song} slow.mp4")]
    steps.append((cmd_hb_slow, "Compress Original Video (Slow Speed)"))

    if has_easy:
        cmd = [python, "-u", "keysight_bot.py", "--song", f"{song} Easy", "--theme", req.theme]
        steps.append((cmd, "Render Keysight (Easy Version)"))
        
        cmd_hb_easy = [python, "-u", "handbrake_bot.py", "--input", str(Path(settings.get("keysight_dir", r"C:\Dev\meloscribe\Keysight export")) / f"{song} Easy.mp4")]
        steps.append((cmd_hb_easy, "Compress Easy Video (Normal Speed)"))
        
        cmd_hb_easy_slow = [python, "-u", "handbrake_bot.py", "--input", str(Path(settings.get("keysight_dir", r"C:\Dev\meloscribe\Keysight export")) / f"{song} Easy slow.mp4")]
        steps.append((cmd_hb_easy_slow, "Compress Easy Video (Slow Speed)"))

    # --- Step 2: Wait for MuseScore PDF Sheets ---
    steps.append(("WAIT_FOR_PDF", "Wait for MuseScore PDF Sheets"))

    # --- Step 3: Wait for Crop Confirmation (Zoom & Shift) ---
    steps.append(("WAIT_FOR_CROP_CONFIRMATION", f"Confirm Crop Safe Zone ({song})"))

    # --- Phase 2: Portrait Video & Uploads ---
    zoom_val = str(req.zoom)
    shift_val = str(req.shift)
    
    keysight_dir = Path(settings.get("keysight_dir", r"C:\Dev\meloscribe\Keysight export"))

    for suffix, folder_name in versions:
        v_song = f"{song}{suffix}"
        is_easy = ("easy" in suffix.lower())
        for vtype, prefix in [("normal", ""), ("tutorial", " slow")]:
            vid_in = str(keysight_dir / f"{v_song}{prefix}.mp4")
            midi_path = f"C:\\Cakewalk Projects\\{folder_name}\\{v_song}{prefix}.mid"
            
            cmd_portrait = [
                python, "-u", "video_generator.py",
                "--video", vid_in, "--title", v_song, "--author", author,
                "--type", vtype, "--zoom", zoom_val, "--shift", shift_val,
                "--midipath", midi_path, "--theme", req.theme
            ]
            
            # Select specific subtitle for each version
            if not is_easy:
                sub_text = req.subtitle_normal if vtype == "normal" else req.subtitle_slow
            else:
                sub_text = req.subtitle_easy_normal if vtype == "normal" else req.subtitle_easy_slow
                
            if not sub_text and req.subtitle:
                sub_text = req.subtitle
                
            if sub_text:
                sub_text = sub_text.strip(" -")
                
            if sub_text:
                cmd_portrait.extend(["--subtitle", sub_text])
            if vtype == "tutorial":
                cmd_portrait.append("--metronome")
                if req.metro_offset:
                    cmd_portrait.extend(["--metro_offset", str(req.metro_offset)])
            if req.enablePortraitAddon:
                cmd_portrait.append("--use_portrait_addon")
            if has_easy:
                cmd_portrait.append("--has_easy")
            if (vtype == "normal" and req.enableVisualizerNormal) or (vtype == "tutorial" and req.enableVisualizerTutorial):
                cmd_portrait.append("--visualizer")
            steps.append((cmd_portrait, f"Generate Portrait Video ({v_song}{prefix})"))

            if req.format == "full_arrangement":
                cmd_wide = [
                    python, "-u", "video_generator.py",
                    "--video", vid_in, "--title", v_song, "--author", author,
                    "--type", vtype, "--zoom", zoom_val, "--shift", shift_val,
                    "--midipath", midi_path, "--theme", req.theme, "--wide"
                ]
                if sub_text:
                    cmd_wide.extend(["--subtitle", sub_text])
                if vtype == "tutorial":
                    cmd_wide.append("--metronome")
                    if req.metro_offset:
                        cmd_wide.extend(["--metro_offset", str(req.metro_offset)])
                if (vtype == "normal" and req.enableVisualizerWideNormal) or (vtype == "tutorial" and req.enableVisualizerWideTutorial):
                    cmd_wide.append("--visualizer")
                steps.append((cmd_wide, f"Generate Widescreen Video ({v_song}{prefix})"))

    for suffix, folder_name in versions:
        v_song = f"{song}{suffix}"
        cmd = [python, "-u", "cover_generator.py", "--song", v_song, "--author", author, "--theme", req.theme]
        steps.append((cmd, f"Generate Cover Art ({v_song})"))

    # --- Step 4: Wait for Hook Segment Confirmation ---
    steps.append(("WAIT_FOR_HOOK_CONFIRMATION", f"Confirm Hook Segment ({song})"))

    if req.doR2:
        for suffix, folder_name in versions:
            v_song = f"{song}{suffix}"
            cmd = [
                python, "-u", "upload_bot.py",
                "--song", v_song,
                "--author", author,
                "--mode", "r2",
                "--format", req.format,
                "--hook_start", str(req.hook_start),
                "--hook_end", str(req.hook_end)
            ]
            if req.subtitle_hook is not None:
                cmd.extend(["--subtitle_hook", req.subtitle_hook.strip()])
            if req.metro_offset:
                cmd.extend(["--metro_offset", str(req.metro_offset)])
            steps.append((cmd, f"Cloudflare R2 Upload ({v_song})"))

    if req.localUpload:
        # Local website catalog sync
        for suffix, folder_name in versions:
            v_song = f"{song}{suffix}"
            cmd = [python, "-u", "upload_bot.py", "--song", v_song, "--price", req.price, "--kofi_id", req.paddle_product_id or "prod_dummy123", "--mode", "website", "--author", author]
            steps.append((cmd, f"Local Catalog Sync ({v_song})"))

        socials = ["youtube", "instagram", "facebook", "tiktok", "threads", "pinterest"]

        from datetime import datetime as dt, timedelta
        interval_days = int(settings.get("schedule_interval_days", 3))
        
        start_date_str = req.scheduleDate
        start_time_str = req.scheduleTime or "16:00"
        
        try:
            current_date = dt.fromisoformat(start_date_str)
        except Exception:
            current_date = dt.now()
        
        # Build social videos queue based on 5-video-split-strategy
        social_videos = []
        if req.format == "full_arrangement":
            # V1: Teaser (Hook)
            social_videos.append((song, "hook", "Teaser"))
            # V2: Normal Speed Original
            social_videos.append((song, "normal", "Normal Speed Original"))
            # V3: Slow Speed Original (Tutorial)
            social_videos.append((song, "tutorial", "Slow Speed Original"))
            if has_easy:
                # V4: Normal Speed Easy
                social_videos.append((f"{song} Easy", "normal", "Normal Speed Easy"))
                # V5: Slow Speed Easy (Tutorial)
                social_videos.append((f"{song} Easy", "tutorial", "Slow Speed Easy"))
        else: # viral_part (Teaser is not needed as video is already short)
            # V1: Normal Speed Original
            social_videos.append((song, "normal", "Normal Speed Original"))
            # V2: Slow Speed Original (Tutorial)
            social_videos.append((song, "tutorial", "Slow Speed Original"))
            if has_easy:
                # V3: Normal Speed Easy
                social_videos.append((f"{song} Easy", "normal", "Normal Speed Easy"))
                # V4: Slow Speed Easy (Tutorial)
                social_videos.append((f"{song} Easy", "tutorial", "Slow Speed Easy"))
        
        # Add steps for each video version, with each step scheduled with the proper date spacing
        for v_idx, (v_song, profile, label) in enumerate(social_videos):
            plat_date = current_date + timedelta(days=v_idx * interval_days)
            plat_date_str = plat_date.date().isoformat()
            
            for platform in socials:
                cmd = [
                    python, "-u", "upload_bot.py",
                    "--song", v_song,
                    "--author", author,
                    "--mode", platform,
                    "--profile", profile,
                    "--format", req.format
                ]
                if req.scheduleDate:
                    cmd.extend(["--schedule_date", plat_date_str, "--schedule_time", start_time_str])
                steps.append((cmd, f"Social Upload ({platform} - {label})"))

        # Original Ko-Fi Upload step
        cmd = [
            python, "-u", "upload_bot.py",
            "--song", song,
            "--mode", "kofi",
            "--price", req.price,
            "--format", req.format
        ]
        steps.append((cmd, f"Ko-Fi Upload ({song})"))
        
        # Easy Ko-Fi Upload step
        if has_easy:
            cmd = [
                python, "-u", "upload_bot.py",
                "--song", f"{song} Easy",
                "--mode", "kofi",
                "--price", req.price,
                "--format", req.format
            ]
            steps.append((cmd, f"Ko-Fi Upload ({song} Easy)"))
    else:
        # Server-side upload (localUpload is False)
        # Always run local catalog sync so local files are updated
        for suffix, folder_name in versions:
            v_song = f"{song}{suffix}"
            cmd = [python, "-u", "upload_bot.py", "--song", v_song, "--price", req.price, "--kofi_id", req.paddle_product_id or "prod_dummy123", "--mode", "website", "--author", author]
            steps.append((cmd, f"Local Catalog Sync ({v_song})"))

        server_platforms = ["youtube", "instagram", "facebook", "tiktok", "threads", "pinterest"]
        start_time_str = req.scheduleTime or "16:00"
        cmd = [
            python, "-u", "stage_to_server.py",
            "--song", song,
            "--author", author,
            "--price", req.price,
            "--schedule_date", req.scheduleDate or "",
            "--schedule_time", start_time_str,
            "--platforms", ",".join(server_platforms),
            "--format", req.format
        ]
        if has_easy:
            cmd.append("--has_easy")
        steps.append((cmd, f"Stage to Oracle VM Server ({song})"))

    total = len(steps)
    if total == 0:
        await manager.broadcast({"type": "done", "message": "No tasks selected."})
        return

    # Calculate step weights (in estimated seconds) for monotonic weighted progress
    def estimate_weight(c, l):
        lbl = str(l).lower()
        if "keysight" in lbl or "handbrake" in lbl:
            return 180.0  # 3 mins per Keysight render
        elif "portrait video" in lbl:
            return 45.0
        elif "widescreen video" in lbl:
            return 35.0
        elif "musescore" in lbl:
            return 15.0
        elif "r2 upload" in lbl:
            return 20.0
        elif "cover art" in lbl:
            return 5.0
        elif "stage to oracle" in lbl or "social upload" in lbl:
            return 15.0
        elif c in ["WAIT_FOR_PDF", "WAIT_FOR_CROP_CONFIRMATION", "WAIT_FOR_HOOK_CONFIRMATION"]:
            return 3.0
        return 10.0

    step_weights = [estimate_weight(c, l) for c, l in steps]
    total_weight = sum(step_weights) if step_weights else 1.0

    cumulative_starts = []
    curr_w = 0.0
    for w in step_weights:
        cumulative_starts.append(curr_w / total_weight)
        curr_w += w

    global workflow_global_progress
    workflow_global_progress = 0.0

    start_idx = max(0, req.resumeFromStep)
    for i in range(start_idx, total):
        while active_workflow_task.get("pause_requested", False) and not active_workflow_task["stop_requested"]:
            await asyncio.sleep(0.5)

        if active_workflow_task["stop_requested"]:
            await manager.broadcast({"type": "done", "message": "⏹️ Workflow stopped by user."})
            return

        cmd, label = steps[i]
        base_progress = cumulative_starts[i]
        slice_weight = step_weights[i] / total_weight
        workflow_global_progress = max(workflow_global_progress, base_progress)
        await manager.broadcast({"type": "progress", "value": workflow_global_progress})

        # Ticker state for smooth progress updates during long steps
        ticker_stop = False
        est_sec = step_weights[i]

        async def smooth_ticker(b_prog, s_w, e_sec):
            elapsed = 0.0
            global workflow_global_progress
            while not ticker_stop and not active_workflow_task["stop_requested"]:
                await asyncio.sleep(1.0)
                elapsed += 1.0
                ratio = min(0.95, elapsed / e_sec)
                val = b_prog + (ratio * s_w)
                workflow_global_progress = max(workflow_global_progress, val)
                await manager.broadcast({"type": "progress", "value": workflow_global_progress})

        ticker_task = asyncio.create_task(smooth_ticker(base_progress, slice_weight, est_sec))

        if cmd == "WAIT_FOR_PDF":
            musescore_dir = Path(settings.get("musescore_dir", r"C:\Dev\meloscribe\Scores"))
            expected_files = [musescore_dir / f"{song}.pdf"]
            if has_easy and ((musescore_dir / f"{song} Easy.pdf").exists() or (musescore_dir / f"{song} Easy.mscz").exists() or (Path(cakewalk_dir) / f"{song} Easy").exists()):
                expected_files.append(musescore_dir / f"{song} Easy.pdf")

            await manager.broadcast({"type": "status", "message": f"Waiting for MuseScore PDF exports in Scores/ directory..."})
            
            while not active_workflow_task["stop_requested"]:
                missing = [f.name for f in expected_files if not f.exists()]
                if not missing:
                    await manager.broadcast({"type": "log", "message": "✅ Found all expected PDF files! Continuing workflow..."})
                    break
                
                await manager.broadcast({"type": "status", "message": f"⏳ Waiting for: {', '.join(missing)}..."})
                await asyncio.sleep(2)
            
            ticker_stop = True
            ticker_task.cancel()
            workflow_global_progress = max(workflow_global_progress, base_progress + slice_weight)
            await manager.broadcast({"type": "progress", "value": workflow_global_progress})

            if active_workflow_task["stop_requested"]:
                return
            continue
            
        if cmd == "WAIT_FOR_CROP_CONFIRMATION":
            # Auto-skip Crop Confirmation if all TikTok portrait videos already exist on disk (>1MB)
            tiktoks_dir = Path(settings.get("tiktok_dir", r"C:\Dev\meloscribe\TikToks"))
            expected_vids = [tiktoks_dir / f"{song}.mp4", tiktoks_dir / f"{song} slow.mp4"]
            if has_easy:
                expected_vids.extend([tiktoks_dir / f"{song} Easy.mp4", tiktoks_dir / f"{song} Easy slow.mp4"])
            
            all_exist = all(f.exists() and f.stat().st_size > 1024 * 1024 for f in expected_vids)
            if all_exist:
                await manager.broadcast({
                    "type": "log",
                    "message": f"⚡ [Auto-Skip] All TikTok portrait videos for '{song}' already exist. Auto-skipping Crop Confirmation!"
                })
                ticker_stop = True
                ticker_task.cancel()
                workflow_global_progress = max(workflow_global_progress, base_progress + slice_weight)
                await manager.broadcast({"type": "progress", "value": workflow_global_progress})
                continue

            # Auto-calculate optimal zoom & shift from MIDI
            midi_path = f"C:\\Cakewalk Projects\\{song}\\{song}.mid"
            auto_zoom, auto_shift = 1.50, 0
            try:
                sys.path.append(str(TOOLS_DIR))
                from auto_crop import calculate_auto_crop
                auto_zoom, auto_shift = calculate_auto_crop(midi_path, left_tolerance_keys=1, right_tolerance_keys=4)
                active_workflow_task["zoom"] = auto_zoom
                active_workflow_task["shift"] = auto_shift
            except Exception as e:
                print(f"[AutoCrop] Calculation error: {e}")
                
            if not req.manual_crop:
                await manager.broadcast({
                    "type": "log",
                    "message": f"🎯 Smart Auto-Crop: Zoom {auto_zoom:.2f}x, Shift {auto_shift:+d}px (1 key left, +4 keys right tolerance). Continuing automatically..."
                })
                ticker_stop = True
                ticker_task.cancel()
                workflow_global_progress = max(workflow_global_progress, base_progress + slice_weight)
                await manager.broadcast({"type": "progress", "value": workflow_global_progress})
                continue

            active_workflow_task["waiting_for_crop"] = True
            active_workflow_task["pause_requested"] = True
            await manager.broadcast({
                "type": "crop_confirmation_required",
                "message": f"⏸️ Keysight video rendered! Please inspect and confirm crop safe zone (Zoom & Shift) for '{song}'.",
                "song": song,
                "zoom": auto_zoom,
                "shift": auto_shift
            })
            await manager.broadcast({"type": "status", "message": f"⏸️ Waiting for Crop Confirmation (Zoom & Shift)..."})
            
            while active_workflow_task.get("waiting_for_crop", False) and not active_workflow_task["stop_requested"]:
                await asyncio.sleep(0.5)
            
            ticker_stop = True
            ticker_task.cancel()
            workflow_global_progress = max(workflow_global_progress, base_progress + slice_weight)
            await manager.broadcast({"type": "progress", "value": workflow_global_progress})

            if active_workflow_task["stop_requested"]:
                return
            
            active_workflow_task["pause_requested"] = False
            await manager.broadcast({
                "type": "log",
                "message": f"✅ Crop confirmed! Zoom: {active_workflow_task.get('zoom', auto_zoom)}x, Shift: {active_workflow_task.get('shift', auto_shift)}px. Resuming video rendering..."
            })
            continue

        if cmd == "WAIT_FOR_HOOK_CONFIRMATION":
            active_workflow_task["waiting_for_hook"] = True
            active_workflow_task["pause_requested"] = True
            await manager.broadcast({
                "type": "hook_confirmation_required",
                "message": f"⏸️ TikTok video rendered! Please select hook clip segment (Start & End) for '{song}'.",
                "song": song,
                "hook_start": active_workflow_task.get("hook_start", req.hook_start),
                "hook_end": active_workflow_task.get("hook_end", req.hook_end)
            })
            await manager.broadcast({"type": "status", "message": f"⏸️ Waiting for Hook Confirmation (Start & End)..."})
            
            while active_workflow_task.get("waiting_for_hook", False) and not active_workflow_task["stop_requested"]:
                await asyncio.sleep(0.5)
            
            ticker_stop = True
            ticker_task.cancel()
            workflow_global_progress = max(workflow_global_progress, base_progress + slice_weight)
            await manager.broadcast({"type": "progress", "value": workflow_global_progress})

            if active_workflow_task["stop_requested"]:
                return
            
            active_workflow_task["pause_requested"] = False
            await manager.broadcast({
                "type": "log",
                "message": f"✅ Hook confirmed! Start: {active_workflow_task.get('hook_start', 0.0)}s, End: {active_workflow_task.get('hook_end', 60.0)}s. Resuming workflow..."
            })
            continue

        # Dynamically override --hook_start and --hook_end for upload commands if hook was confirmed
        if isinstance(cmd, list) and any(arg in ["--hook_start", "--hook_end"] for arg in cmd):
            current_hstart = str(active_workflow_task.get("hook_start", req.hook_start))
            current_hend = str(active_workflow_task.get("hook_end", req.hook_end))
            new_cmd = []
            skip_next = False
            for c_val in cmd:
                if skip_next:
                    skip_next = False
                    continue
                if c_val == "--hook_start":
                    new_cmd.extend(["--hook_start", current_hstart])
                    skip_next = True
                elif c_val == "--hook_end":
                    new_cmd.extend(["--hook_end", current_hend])
                    skip_next = True
                else:
                    new_cmd.append(c_val)
            cmd = new_cmd

        # Dynamically override --zoom and --shift for video_generator commands if crop was confirmed
        if isinstance(cmd, list) and any("video_generator.py" in str(arg) for arg in cmd):
            current_zoom = str(active_workflow_task.get("zoom", 1.50))
            current_shift = str(active_workflow_task.get("shift", 0))
            new_cmd = []
            skip_next = False
            for c_val in cmd:
                if skip_next:
                    skip_next = False
                    continue
                if c_val == "--zoom":
                    new_cmd.extend(["--zoom", current_zoom])
                    skip_next = True
                elif c_val == "--shift":
                    new_cmd.extend(["--shift", current_shift])
                    skip_next = True
                else:
                    new_cmd.append(c_val)
            cmd = new_cmd

        # Dynamically check live settings toggles RIGHT BEFORE executing upload steps
        fresh_settings = load_settings()

        if label.startswith("Cloudflare R2 Upload"):
            if not fresh_settings.get("doR2", True):
                await manager.broadcast({"type": "log", "message": f"⏭️ [Upload] Cloudflare R2 toggle is OFF in live settings — skipping {label}."})
                ticker_stop = True
                ticker_task.cancel()
                workflow_global_progress = max(workflow_global_progress, base_progress + slice_weight)
                await manager.broadcast({"type": "progress", "value": workflow_global_progress})
                continue

        if label.startswith("Social Upload"):
            social_plat = None
            try:
                social_plat = label.split("Social Upload (")[1].split(" - ")[0].split(")")[0].strip().lower()
            except Exception:
                pass
            if social_plat:
                plat_key = f"do{social_plat.capitalize()}"
                if not fresh_settings.get(plat_key, True):
                    await manager.broadcast({"type": "log", "message": f"⏭️ [Upload] {social_plat.capitalize()} toggle ('{plat_key}') is OFF in live settings — skipping {label}."})
                    ticker_stop = True
                    ticker_task.cancel()
                    workflow_global_progress = max(workflow_global_progress, base_progress + slice_weight)
                    await manager.broadcast({"type": "progress", "value": workflow_global_progress})
                    continue

        if label.startswith("Stage to Oracle VM Server"):
            live_platforms = []
            for plat_name, setting_key in [("youtube", "doYoutube"), ("instagram", "doInstagram"), ("facebook", "doFacebook"), ("tiktok", "doTiktok"), ("threads", "doThreads"), ("pinterest", "doPinterest")]:
                if fresh_settings.get(setting_key, True):
                    live_platforms.append(plat_name)
            
            if not live_platforms:
                await manager.broadcast({"type": "log", "message": f"⏭️ [Upload] All social platform toggles are OFF in live settings — skipping Stage to Oracle VM Server."})
                ticker_stop = True
                ticker_task.cancel()
                workflow_global_progress = max(workflow_global_progress, base_progress + slice_weight)
                await manager.broadcast({"type": "progress", "value": workflow_global_progress})
                continue
            
            if isinstance(cmd, list) and "--platforms" in cmd:
                p_idx = cmd.index("--platforms")
                cmd[p_idx + 1] = ",".join(live_platforms)
                await manager.broadcast({"type": "log", "message": f"ℹ️ [Upload Staging] Live target platforms checked right before execution: {', '.join(live_platforms)}"})

        if label.startswith("Ko-Fi Upload"):
            if not fresh_settings.get("doKofi", True):
                await manager.broadcast({"type": "log", "message": f"⏭️ [Upload] Ko-Fi Upload toggle is OFF in live settings — skipping {label}."})
                ticker_stop = True
                ticker_task.cancel()
                workflow_global_progress = max(workflow_global_progress, base_progress + slice_weight)
                await manager.broadcast({"type": "progress", "value": workflow_global_progress})
                continue

        platform = None
        if "Social Upload (" in label:
            try:
                platform = label.split("Social Upload (")[1].split(" - ")[0].split(")")[0].strip()
            except Exception:
                pass

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
        rc = await run_tool(cmd, label, base_progress, slice_weight)
        
        ticker_stop = True
        ticker_task.cancel()
        workflow_global_progress = max(workflow_global_progress, base_progress + slice_weight)
        await manager.broadcast({"type": "progress", "value": workflow_global_progress})

        if rc != 0 and not active_workflow_task["stop_requested"]:
            await manager.broadcast({"type": "done", "message": f"❌ {label} failed (exit code {rc}). Resume from step {i}."})
            return

    workflow_global_progress = 1.0
    await manager.broadcast({"type": "progress", "value": 1.0})
    done_msg = "🎉 Automation Workflow completed successfully! All files are rendered, packaged, uploaded, and synced!"
    await manager.broadcast({"type": "done", "message": done_msg})

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
                versions = [("", song_name)]
                if has_easy:
                    versions.append((" Easy", f"{song_name} Easy"))
                    
                # MuseScore launcher first
                for suffix, folder_name in versions:
                    v_song = f"{song_name}{suffix}"
                    steps.append([python, "-u", str(TOOLS_DIR / "musescore_launcher.py"), "--song", v_song, "--author", author])

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
                    

                    
                zoom_val = "1.50"
                shift_val = "0"
                
                keysight_dir = Path(settings.get("keysight_dir", r"C:\Dev\meloscribe\Keysight export"))
                for suffix, folder_name in versions:
                    v_song = f"{song_name}{suffix}"
                    is_easy = (suffix == " easy")
                    for vtype, prefix in [("normal", ""), ("tutorial", " slow")]:
                        vid_in = str(keysight_dir / f"{v_song}{prefix}.mp4")
                        midi_path = f"C:\\Cakewalk Projects\\{folder_name}\\{v_song}{prefix}.mid"
                        
                        cmd_portrait = [
                            python, "-u", str(TOOLS_DIR / "video_generator.py"),
                            "--video", vid_in, "--title", v_song, "--author", author,
                            "--type", vtype, "--zoom", zoom_val, "--shift", shift_val,
                            "--midipath", midi_path, "--theme", theme, "--use_portrait_addon",
                            "--force"
                        ]
                        
                        # Subtitle selection
                        if not is_easy:
                            sub_text = settings.get("subtitle_normal", "") if vtype == "normal" else settings.get("subtitle_slow", "")
                        else:
                            sub_text = settings.get("subtitle_easy_normal", "") if vtype == "normal" else settings.get("subtitle_easy_slow", "")
                        if not sub_text and settings.get("subtitle", ""):
                            sub_text = settings.get("subtitle", "")
                        if sub_text:
                            cmd_portrait.extend(["--subtitle", sub_text.strip()])
                            
                        if vtype == "tutorial":
                            cmd_portrait.append("--metronome")
                        if has_easy:
                            cmd_portrait.append("--has_easy")
                        steps.append(cmd_portrait)

                        
                        if fmt == "full_arrangement":
                            cmd_widescreen = [
                                python, "-u", str(TOOLS_DIR / "video_generator.py"),
                                "--video", vid_in, "--title", v_song, "--author", author,
                                "--type", vtype, "--zoom", zoom_val, "--shift", shift_val,
                                "--midipath", midi_path, "--theme", theme, "--wide",
                                "--force"
                            ]
                            if vtype == "tutorial":
                                cmd_widescreen.append("--metronome")
                            steps.append(cmd_widescreen)
                            
                    # Cover Generator
                    steps.append([python, "-u", str(TOOLS_DIR / "cover_generator.py"), "--song", v_song, "--author", author, "--theme", theme])
                    # R2 Upload
                    cmd_r2 = [python, "-u", str(TOOLS_DIR / "upload_bot.py"), "--song", v_song, "--author", author, "--mode", "r2", "--format", fmt]
                    if settings.get("subtitle_hook"):
                        cmd_r2.extend(["--subtitle_hook", settings.get("subtitle_hook").strip()])
                    steps.append(cmd_r2)
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
                        creation_flags = subprocess.CREATE_NO_WINDOW
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
                                    pct = int(line.split(":")[1].replace("%", "").strip().split("(")[0].strip())
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
                    # Trigger git push & VM sync automatically to publish the updated catalog/covers
                    threading.Thread(target=run_git_push, daemon=True).start()
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
async def _run_workflow_safe(req: WorkflowRequest):
    try:
        await _run_workflow(req)
    except Exception as err:
        import traceback
        tb = traceback.format_exc()
        print(f"[Workflow Error] {tb}")
        try:
            await manager.broadcast({"type": "status", "message": f"❌ Error: {str(err)}"})
            await manager.broadcast({"type": "log", "message": f"CRITICAL WORKFLOW EXCEPTION:\n{tb}"})
            await manager.broadcast({"type": "done", "message": f"❌ Workflow failed: {str(err)}"})
        except Exception:
            pass

@router.post("/api/workflow/start")
async def start_workflow(req: WorkflowRequest):
    active_workflow_task["stop_requested"] = False  # Reset lock!
    asyncio.create_task(_run_workflow_safe(req))
    return {"status": "started"}

@router.post("/api/workflow/stop")
def stop_workflow():
    active_workflow_task["stop_requested"] = True
    proc = active_workflow_task.get("current_process")
    if proc:
        try:
            import psutil
            try:
                p = psutil.Process(proc.pid)
                p.resume()
                for child in p.children(recursive=True):
                    try:
                        child.resume()
                        child.kill()
                    except:
                        pass
                p.kill()
            except:
                pass
            subprocess.Popen(f"taskkill /F /T /PID {proc.pid}", shell=True,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             creationflags=CREATION_FLAGS)
        except Exception:
            pass
    # Forcefully terminate any orphaned scp.exe, ffmpeg.exe or background python subprocesses
    try:
        subprocess.Popen("taskkill /F /IM scp.exe /IM ffmpeg.exe", shell=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         creationflags=CREATION_FLAGS)
    except:
        pass
    return {"status": "stop requested"}

@router.post("/api/workflow/pause")
def pause_workflow():
    active_workflow_task["pause_requested"] = True
    proc = active_workflow_task["current_process"]
    if proc:
        try:
            import psutil
            p = psutil.Process(proc.pid)
            p.suspend()
            for child in p.children(recursive=True):
                child.suspend()
        except Exception as e:
            print(f"[Workflow] Error suspending process: {e}")
    return {"status": "paused"}

@router.post("/api/workflow/resume")
def resume_workflow():
    active_workflow_task["pause_requested"] = False
    proc = active_workflow_task["current_process"]
    if proc:
        try:
            import psutil
            p = psutil.Process(proc.pid)
            p.resume()
            for child in p.children(recursive=True):
                child.resume()
        except Exception as e:
            print(f"[Workflow] Error resuming process: {e}")
    return {"status": "resumed"}

class ConfirmCropRequest(BaseModel):
    zoom: float = 1.50
    shift: int = 0

@router.post("/api/workflow/confirm-crop")
def confirm_crop(req: ConfirmCropRequest):
    active_workflow_task["zoom"] = req.zoom
    active_workflow_task["shift"] = req.shift
    active_workflow_task["waiting_for_crop"] = False
    active_workflow_task["pause_requested"] = False
    proc = active_workflow_task.get("current_process")
    if proc:
        try:
            import psutil
            p = psutil.Process(proc.pid)
            p.resume()
            for child in p.children(recursive=True):
                child.resume()
        except Exception as e:
            print(f"[Workflow] Error resuming process on crop confirm: {e}")
    return {"status": "confirmed", "zoom": req.zoom, "shift": req.shift}

class ConfirmHookRequest(BaseModel):
    hook_start: float = 0.0
    hook_end: float = 60.0

@router.post("/api/workflow/confirm-hook")
def confirm_hook(req: ConfirmHookRequest):
    active_workflow_task["hook_start"] = req.hook_start
    active_workflow_task["hook_end"] = req.hook_end
    active_workflow_task["waiting_for_hook"] = False
    active_workflow_task["pause_requested"] = False
    proc = active_workflow_task.get("current_process")
    if proc:
        try:
            import psutil
            p = psutil.Process(proc.pid)
            p.resume()
            for child in p.children(recursive=True):
                child.resume()
        except Exception as e:
            print(f"[Workflow] Error resuming process on hook confirm: {e}")
    return {"status": "confirmed", "hook_start": req.hook_start, "hook_end": req.hook_end}

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
        subtitle = settings.get("subtitle", "")
        cmd = [python, "-u", "video_generator.py", "--video", vid_in, "--title", song, "--author", author, "--type", "normal", "--zoom", "1.5", "--midipath", midi_path, "--theme", theme, "--use_portrait_addon"]
        if subtitle:
            cmd.extend(["--subtitle", subtitle])
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

@router.get("/api/workflow/auto-crop")
def get_auto_crop_endpoint(song: str = ""):
    if not song:
        return {"zoom": 1.50, "shift": 0}
    midi_path = f"C:\\Cakewalk Projects\\{song}\\{song}.mid"
    try:
        sys.path.append(str(TOOLS_DIR))
        from auto_crop import calculate_auto_crop
        zoom, shift = calculate_auto_crop(midi_path, left_tolerance_keys=1, right_tolerance_keys=4)
        return {"zoom": zoom, "shift": shift}
    except Exception as e:
        return {"zoom": 1.50, "shift": 0, "error": str(e)}

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
                songs.pop(existing_idx)
                songs.insert(0, payload)
                print(f"[Catalog Update] Updated existing song '{payload.get('title')}' and moved to top")
            else:
                songs.insert(0, payload)
                print(f"[Catalog Add] Added new song '{payload.get('title')}' to top")
                
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
            
            # Synchronize songs.json to production VM via SCP so pricing matches checkouts instantly
            ssh_key = r"C:\Dev\ssh-key-2026-05-07.key"
            local_songs = r"c:\Dev\meloscribe-frontend\website\src\data\songs.json"
            if os.path.exists(ssh_key) and os.path.exists(local_songs):
                print("[VM Sync] Copying songs.json to production VM backend...")
                subprocess.run([
                    "scp", "-i", ssh_key, "-o", "StrictHostKeyChecking=accept-new",
                    local_songs,
                    "ubuntu@152.70.23.171:/home/ubuntu/meloscribe/tools/meloscribe/backend/songs.json"
                ], check=True, creationflags=CREATION_FLAGS)
                print("[VM Sync] Successfully uploaded songs.json to production VM!")
    except Exception as e:
        print(f"[Git push / VM Sync] Error: {e}")

def run_deep_asset_cleanup(song_name: str):
    log_error("Deep Cleanup", f"Starting deep cleanup for '{song_name}' assets...")
    # NOTE: Local directory and package deletions have been disabled to preserve user's local projects.
            
    # 3. Cloudflare R2 Assets
    r2_account_id = settings.get("r2_account_id") or os.environ.get("R2_ACCOUNT_ID")
    r2_access_key = settings.get("r2_access_key") or settings.get("r2_access_key_id") or os.environ.get("R2_ACCESS_KEY_ID")
    r2_secret_key = settings.get("r2_secret_key") or settings.get("r2_secret_access_key") or os.environ.get("R2_SECRET_ACCESS_KEY")
    r2_bucket = settings.get("r2_bucket") or settings.get("r2_bucket_name", "meloscribe-sheets") or os.environ.get("R2_BUCKET_NAME", "meloscribe-sheets")
    
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
            
    # 4. Ingest Queue DB Cleanup
    try:
        conn = sqlite3.connect(str(db_path), timeout=30.0)
        c = conn.cursor()
        c.execute("DELETE FROM batch_ingest_queue WHERE song_name = ?", (song_name,))
        c.execute("DELETE FROM batch_ingest_queue WHERE song_name = ?", (f"{song_name} Easy",))
        conn.commit()
        conn.close()
        log_error("Deep Cleanup", f"Deleted '{song_name}' and variant from batch_ingest_queue.")
    except Exception as e:
        log_error("Deep Cleanup", f"Failed to delete queue entries: {e}")
        
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
class InitializeExistingRequest(BaseModel):
    songName: str
    author: str = "Traditional"
    theme: str = "warm"
    price: str = "6.00"
    format: str = "full_arrangement"
    difficulty: str = "Original"

@router.post("/api/batch/initialize-existing")
def initialize_existing_song(req: InitializeExistingRequest):
    song_name = req.songName.strip()
    author = req.author.strip()
    theme = req.theme.strip()
    price = req.price.strip()
    fmt = req.format.strip()
    difficulty = req.difficulty.strip().lower()

    cakewalk_dir = settings.get("cakewalk_dir", r"C:\Cakewalk Projects")
    song_dir = Path(cakewalk_dir) / song_name

    # Verify the MIDI file exists in project directory
    midi_path = song_dir / f"{song_name}.mid"
    if not midi_path.exists():
        mid_files = list(song_dir.glob("*.mid"))
        if mid_files:
            midi_path = mid_files[0]
        else:
            raise HTTPException(status_code=400, detail=f"No MIDI file found in Cakewalk project directory: {song_dir}")

    try:
        conn = sqlite3.connect(str(db_path), timeout=30.0)
        c = conn.cursor()
        c.execute(
            """
            INSERT OR REPLACE INTO batch_ingest_queue 
            (song_name, author, theme, price, format, difficulty, status, error_message, processed_at, progress)
            VALUES (?, ?, ?, ?, ?, ?, 'initialized', NULL, NULL, 0)
            """,
            (song_name, author, theme, price, fmt, difficulty)
        )
        conn.commit()
        conn.close()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

    return {"status": "success", "songName": song_name}

class UpdateMetadataRequest(BaseModel):
    songName: str
    author: str
    theme: str
    price: str
    format: str
    difficulty: str

@router.post("/api/batch/update-metadata")
def update_batch_metadata(req: UpdateMetadataRequest):
    song_name = req.songName.strip()
    author = req.author.strip()
    theme = req.theme.strip()
    price = req.price.strip()
    fmt = req.format.strip()
    difficulty = req.difficulty.strip().lower()

    try:
        conn = sqlite3.connect(str(db_path), timeout=30.0)
        c = conn.cursor()
        c.execute(
            """
            UPDATE batch_ingest_queue 
            SET author = ?, theme = ?, price = ?, format = ?, difficulty = ?
            WHERE song_name = ?
            """,
            (author, theme, price, fmt, difficulty, song_name)
        )
        conn.commit()
        conn.close()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

    return {"status": "success", "songName": song_name}

@router.get("/api/batch/scan-cakewalk")
def scan_cakewalk_songs():
    """
    Scan the Cakewalk Projects directory and return all detected song names.
    A valid song folder must contain at least one .mid file matching the folder name.
    """
    cakewalk_dir = Path(settings.get("cakewalk_dir", r"C:\Cakewalk Projects"))
    if not cakewalk_dir.exists():
        raise HTTPException(status_code=404, detail=f"Cakewalk directory not found: {cakewalk_dir}")

    IGNORED_FOLDERS = {"_archive", "_backup", "_legacy", "test", "template"}
    songs = []

    try:
        for folder in sorted(cakewalk_dir.iterdir()):
            if not folder.is_dir():
                continue
            name = folder.name
            if name.lower().startswith("_") or name.lower() in IGNORED_FOLDERS:
                continue
            # Check if folder contains a .mid file (any name)
            mid_files = list(folder.glob("*.mid"))
            if not mid_files:
                continue
            # Check queue status
            try:
                conn = sqlite3.connect(str(db_path), timeout=10.0)
                c = conn.cursor()
                c.execute("SELECT status FROM batch_ingest_queue WHERE song_name = ?", (name,))
                row = c.fetchone()
                conn.close()
                queue_status = row[0] if row else None
            except Exception:
                queue_status = None

            # Check if Keysight exports exist
            keysight_dir = Path(settings.get("keysight_dir", r"C:\Dev\meloscribe\Keysight export"))
            has_normal_video = (keysight_dir / f"{name}.mp4").exists()
            has_slow_video   = (keysight_dir / f"{name} slow.mp4").exists()

            songs.append({
                "name": name,
                "queueStatus": queue_status,
                "hasNormalVideo": has_normal_video,
                "hasSlowVideo": has_slow_video,
                "midiFiles": [m.name for m in mid_files],
            })
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error scanning Cakewalk directory: {str(e)}")

    return {"songs": songs, "total": len(songs), "cakewalkDir": str(cakewalk_dir)}

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
    author = row[1] if row and len(row) > 1 and row[1] else (settings.get("author") or "Abilene")
    
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

    def get_video_duration(video_path):
        import subprocess as _sp
        import sys
        try:
            cmd = ['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
                   '-of', 'default=noprint_wrappers=1:nokey=1', str(video_path)]
            creation_flags = 0x08000000 if sys.platform == "win32" else 0
            res = _sp.run(cmd, stdout=_sp.PIPE, stderr=_sp.PIPE, text=True, encoding='utf-8', errors='replace', creationflags=creation_flags)
            return float(res.stdout.strip())
        except Exception:
            return 60.0

    duration = get_video_duration(source)
    half_duration = max(10.0, duration / 2.0)
    fade_start = half_duration - 3.0
    text_fade_start = half_duration - 2.0

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
    endscreen_txt = os.path.join(temp_dir, f"_endscreen_{uid}.txt")
    
    with open(title_txt, "w", encoding="utf-8") as f:
        f.write(song_name)
    with open(artist_txt, "w", encoding="utf-8") as f:
        f.write(author)
    with open(endscreen_txt, "w", encoding="utf-8") as f:
        f.write("Full video coming soon")
        
    title_txt_esc = escape_path_for_ffmpeg(title_txt)
    artist_txt_esc = escape_path_for_ffmpeg(artist_txt)
    endscreen_txt_esc = escape_path_for_ffmpeg(endscreen_txt)
    
    filter_complex = (
        f"[0:v]fade=type=out:start_time={fade_start}:duration=1.0:color=black[v_fade]; "
        f"[v_fade]drawtext=fontfile='{font_title_esc}':textfile='{title_txt_esc}':fontcolor=white:fontsize={title_size}"
        f":x=(w-text_w)/2:y=(h/2)-{int(height*0.06)}:shadowcolor=black@0.6:shadowx=4:shadowy=4"
        f":alpha='if(lt(t,1),t,if(lt(t,3.5),1,if(lt(t,4.5),4.5-t,0)))'[v1]; "
        
        f"[v1]drawtext=fontfile='{font_artist_esc}':textfile='{artist_txt_esc}':fontcolor=white:fontsize={artist_size}"
        f":x=(w-text_w)/2:y=(h/2)+{int(height*0.05)}:shadowcolor=black@0.6:shadowx=3:shadowy=3"
        f":alpha='if(lt(t,1),t,if(lt(t,3.5),1,if(lt(t,4.5),4.5-t,0)))'[v2]; "
        
        f"[v2]drawtext=fontfile='{font_title_esc}':textfile='{endscreen_txt_esc}':fontcolor=white:fontsize={int(width*0.038)}"
        f":x=(w-text_w)/2:y=(h-text_h)/2:shadowcolor=black@0.6:shadowx=3:shadowy=3"
        f":alpha='if(lt(t,{text_fade_start}),0,min(1,(t-{text_fade_start})/0.5))'"
    )
    
    cmd = [
        "ffmpeg", "-y",
        "-to", f"{half_duration:.2f}",
        "-i", str(source),
        "-filter_complex", filter_complex,
        "-af", f"afade=type=out:start_time={fade_start}:duration=3.0",
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
        os.remove(endscreen_txt)
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
