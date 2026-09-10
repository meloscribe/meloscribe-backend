import os
import sys
# Force stdout to UTF-8 to prevent Windows CMD from triggering UnicodeEncodeErrors on emojis
sys.stdout.reconfigure(encoding='utf-8')
import argparse
import time
from playwright.sync_api import sync_playwright

from pathlib import Path
import ctypes
import re
import urllib.parse

tools_dir = os.path.dirname(os.path.abspath(__file__))
local_ffmpeg_bin = os.path.join(tools_dir, "ffmpeg", "bin")
if os.path.exists(local_ffmpeg_bin) and local_ffmpeg_bin not in os.environ.get("PATH", ""):
    os.environ["PATH"] = local_ffmpeg_bin + os.pathsep + os.environ.get("PATH", "")

def get_settings():
    sys.path.append(os.path.join(os.path.dirname(__file__), "meloscribe", "backend"))
    try:
        from settings import load_settings
        res = load_settings()
    except Exception:
        res = {}
        
    import platform
    if platform.system() == "Linux":
        song_name = None
        for i, arg in enumerate(sys.argv):
            if arg == "--song" and i + 1 < len(sys.argv):
                song_name = sys.argv[i + 1]
                break
        if song_name:
            base_staging_name = song_name
            if base_staging_name.lower().endswith(" easy"):
                base_staging_name = base_staging_name[:-5].strip()
            elif base_staging_name.lower().endswith(" teaser"):
                base_staging_name = base_staging_name[:-7].strip()
                
            staging_base = "/home/ubuntu/meloscribe/staging"
            song_stage_dir = resolve_case_insensitive_path(os.path.join(staging_base, base_staging_name))
            res["tiktok_dir"] = resolve_case_insensitive_path(os.path.join(song_stage_dir, "TikToks"))
            res["covers_dir"] = resolve_case_insensitive_path(os.path.join(song_stage_dir, "Covers"))
            res["musescore_dir"] = resolve_case_insensitive_path(os.path.join(song_stage_dir, "Scores"))
            res["cakewalk_dir"] = resolve_case_insensitive_path(os.path.join(song_stage_dir, "Cakewalk"))
    return res

def resolve_case_insensitive_path(path):
    if not path:
        return path
    if os.path.exists(path):
        return path
    parent = os.path.dirname(path)
    if not os.path.exists(parent):
        return path
    base = os.path.basename(path).lower()
    for f in os.listdir(parent):
        if f.lower() == base:
            return os.path.join(parent, f)
    return path

def escape_path_for_ffmpeg(p: str) -> str:
    p = p.replace('\\', '/')
    p = p.replace(':', '\\:')
    return p

def get_video_dimensions(video_path, ffmpeg_exe="ffmpeg"):
    import subprocess
    import re
    try:
        cmd = [ffmpeg_exe, "-i", str(video_path)]
        creation_flags = 0x08000000 if os.name == 'nt' else 0
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, creationflags=creation_flags)
        match = re.search(r'Video:.*?\b(\d{3,4})x(\d{3,4})\b', res.stderr)
        if match:
            return int(match.group(1)), int(match.group(2))
    except Exception:
        pass
    return 2560, 1440

def wait_for_user(message):
    print(f"\n[PAUSED] {message}")
    print("Waiting for User to click OK in the popup...")
    # 0x40000 = MB_TOPMOST (Always on Top) | 0x40 = MB_ICONINFORMATION
    ctypes.windll.user32.MessageBoxW(0, message + "\n\nPress OK when you are done.", "Bot Paused & Waiting", 0x40000 | 0x40)
    print("Continuing...\n")

def get_duration_seconds(filepath):
    import subprocess
    if not os.path.exists(filepath):
        return 0.0
    try:
        cmd = ['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
               '-of', 'default=noprint_wrappers=1:nokey=1', filepath]
        result = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace')
        return float(result.stdout.strip())
    except Exception:
        return 0.0

def get_audio_delay(video_path):
    import subprocess
    import tempfile
    import uuid
    import numpy as np
    from scipy.io import wavfile
    temp_wav = os.path.join(tempfile.gettempdir(), f"temp_offset_{uuid.uuid4().hex[:8]}.wav")
    creation_flags = 0x08000000 if os.name == 'nt' else 0
    subprocess.run(["ffmpeg", '-y', '-i', str(video_path), '-t', '10', '-vn', '-acodec', 'pcm_s16le', '-ar', '44100', '-ac', '1', temp_wav], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=creation_flags)
    try:
        sample_rate, audio_data = wavfile.read(temp_wav)
        if len(audio_data.shape) > 1: audio_data = audio_data[:,0]
        audio_data = audio_data.astype(np.float32) / 32768.0
        above = np.where(np.abs(audio_data) > 0.01)[0]
        offset = above[0] / sample_rate if len(above) > 0 else 0.0
    except Exception as e:
        print(f"Warning: Audio delay detection failed: {e}")
        offset = 0.0
    try: os.remove(temp_wav)
    except: pass
    return offset

def generate_metronome_track(midi_path: str, video_duration: float, audio_delay: float, metro_offset: float = 0.0) -> str:
    import mido
    import numpy as np
    import tempfile
    import uuid
    from scipy.io import wavfile
    mid = mido.MidiFile(midi_path)
    ticks_per_beat = mid.ticks_per_beat
    
    # 1. Detect time signature
    denominator = 4
    for track in mid.tracks:
        for msg in track:
            if msg.type == 'time_signature':
                denominator = msg.denominator
                break
    
    # 2. Parse beat times (tempo-aware)
    beat_step = ticks_per_beat
    beat_times = []
    current_tick = 0
    current_tempo = 500000  # Default 120 BPM
    tiempo_sec = 0.0
    
    next_beat_tick = 0
    if metro_offset:
        next_beat_tick = int(ticks_per_beat * metro_offset)
    elif midi_path and "god rest ye merry" in midi_path.lower():
        next_beat_tick = int(ticks_per_beat * 0.5)
    
    for msg in mid.merged_track:
        delta_sec = mido.tick2second(msg.time, ticks_per_beat, current_tempo)
        while next_beat_tick <= current_tick + msg.time:
            if next_beat_tick == 0:
                beat_times.append(tiempo_sec)
            else:
                beat_sec = tiempo_sec + mido.tick2second(next_beat_tick - current_tick, ticks_per_beat, current_tempo)
                beat_times.append(beat_sec)
            next_beat_tick += beat_step
        tiempo_sec += delta_sec
        current_tick += msg.time
        if msg.type == 'set_tempo':
            current_tempo = msg.tempo
            
    beat_times = [t + audio_delay for t in beat_times]
    
    # 3. Synthesize click track
    sr = 44100
    total_dur = beat_times[-1] + 2.0 if beat_times else video_duration
    audio = np.zeros(int(total_dur * sr), dtype=np.float32)
    
    dur_c = 0.06
    t_c = np.linspace(0, dur_c, int(dur_c * sr), endpoint=False)
    click = (np.sin(2 * np.pi * 1100 * t_c) * 0.7 +
             np.sin(2 * np.pi * 2200 * t_c) * 0.3)
    click *= np.exp(-180 * t_c)
    click += np.random.normal(0, 0.15, len(t_c)) * np.exp(-350 * t_c)
    click *= 1.2
    
    for t in beat_times:
        i = int(t * sr)
        end = i + len(click)
        if end <= len(audio):
            audio[i:end] += click
            
    tmp = os.path.join(tempfile.gettempdir(), f"_metro_track_{uuid.uuid4().hex[:8]}.wav")
    wavfile.write(tmp, sr, audio)
    return tmp

def send_ntfy_notification(title, body, image_path=None):
    settings = get_settings()
    topic = settings.get("ntfy_topic", "")
    if not topic:
        print("[ntfy] No topic configured. Skipping notification.")
        return False
    
    import requests
    import base64
    url = f"https://ntfy.sh/{topic}"
    
    # Base64 encode headers for safe UTF-8 transmission (RFC 2047)
    title_b64 = "=?utf-8?B?" + base64.b64encode(title.encode("utf-8")).decode("ascii") + "?="
    body_b64 = "=?utf-8?B?" + base64.b64encode(body.encode("utf-8")).decode("ascii") + "?="
    
    # If image_path exists, send a single combined notification containing both text body and image attachment
    if image_path and os.path.exists(image_path):
        try:
            print(f"[ntfy] Sending single combined notification with cover attachment: {image_path}")
            headers = {
                "Title": title_b64,
                "X-Message": body_b64,
                "Priority": "default",
                "Tags": "musical_note,picture",
                "X-Filename": os.path.basename(image_path)
            }
            with open(image_path, "rb") as f:
                res = requests.put(url, data=f, headers=headers, timeout=15)
            if res.status_code == 200:
                print("[ntfy] Single attachment + text notification sent successfully!")
                return True
            else:
                print(f"[ntfy] Single notification failed ({res.status_code}): {res.text}. Trying text fallback.")
        except Exception as e:
            print(f"[ntfy] Error sending single notification: {e}")

    # Fallback or text-only notification
    try:
        print("[ntfy] Sending text notification...")
        res_text = requests.post(
            url, 
            data=body.encode("utf-8"), 
            headers={"Title": title_b64, "Priority": "default", "Tags": "musical_note,clapper"}, 
            timeout=10
        )
        if res_text.status_code == 200:
            print("[ntfy] Text notification sent successfully!")
            return True
        else:
            print(f"[ntfy] Text notification failed: {res_text.status_code} {res_text.text}")
            return False
    except Exception as e:
        print(f"[ntfy] Error sending text notification: {e}")
        return False

def generate_previews(song_name, format_mode="full_arrangement", **kwargs):
    """
    Generate audio hover preview MP3 and website preview video segment.
    For full_arrangement, pass hook_start and hook_end (seconds) to control clip window.
    """
    import subprocess
    settings = get_settings()
    print(f"\n[Previews] Generating previews for '{song_name}' (Format: {format_mode})...")
    
    # Resolve hook start and end times
    hook_start = kwargs.get("hook_start")
    hook_end = kwargs.get("hook_end")
    if hook_start is None or hook_end is None:
        try:
            import sqlite3
            db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "meloscribe", "backend", "analytics.db")
            if os.path.exists(db_path):
                conn = sqlite3.connect(db_path)
                row = conn.execute("SELECT hook_start, hook_end FROM batch_ingest_queue WHERE song_name=?", (song_name,)).fetchone()
                if not row:
                    row = conn.execute("SELECT hook_start, hook_end FROM tracks WHERE song_name=?", (song_name,)).fetchone()
                conn.close()
                if row:
                    if hook_start is None: hook_start = row[0]
                    if hook_end is None: hook_end = row[1]
        except Exception:
            pass
            
    if (hook_start is None or float(hook_start) == 0.0) and settings.get("hook_start"):
        try: hook_start = float(settings.get("hook_start"))
        except Exception: pass
    if (hook_end is None or float(hook_end) == 60.0 or float(hook_end) <= float(hook_start or 0.0)) and settings.get("hook_end"):
        try: hook_end = float(settings.get("hook_end"))
        except Exception: pass

    if hook_start is None: hook_start = 0.0
    if hook_end is None: hook_end = 30.0

    # 1. Generate audio hover preview MP3 from normalized WAV
    wav_path = None
    paths_to_try = [
        f"C:\\Cakewalk Projects\\{song_name}\\Audio Export\\{song_name}.wav",
        f"C:\\Cakewalk Projects\\{song_name}\\Audio Export\\.Audacity\\{song_name}.wav",
        f"C:\\Cakewalk Projects\\.Audacity\\{song_name}.wav",
    ]
    for p in paths_to_try:
        if os.path.exists(p):
            wav_path = p
            break
            
    if not wav_path:
        # Case-insensitive directory scan fallback
        cakewalk_base = r"C:\Cakewalk Projects"
        if os.path.exists(cakewalk_base):
            for folder in os.listdir(cakewalk_base):
                if folder.lower() == song_name.lower():
                    export_dir = os.path.join(cakewalk_base, folder, "Audio Export")
                    if os.path.exists(export_dir):
                        for f in os.listdir(export_dir):
                            if f.lower() == f"{song_name}.wav".lower():
                                wav_path = os.path.join(export_dir, f)
                                break
                    if wav_path:
                        break
                        
    if wav_path:
        frontend_dir = r"c:\Dev\meloscribe-frontend\website"
        dest_mp3 = os.path.join(frontend_dir, "public", "audio-previews", f"{song_name}.mp3")
        os.makedirs(os.path.dirname(dest_mp3), exist_ok=True)
        
        print(f"[Previews] Encoding audio hover MP3: {dest_mp3}")
        creation_flags = 0x08000000 if os.name == 'nt' else 0
        
        # Always convert full audio from beginning to end
        cmd = [
            'ffmpeg', '-y',
            '-i', wav_path,
            '-c:a', 'libmp3lame', '-b:a', '128k',
            dest_mp3
        ]
            
        rc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=creation_flags).returncode
        if rc == 0:
            print(f"[Previews] Success: Audio hover MP3 compressed.")
        else:
            print(f"[Previews] Error: FFmpeg failed to compress audio hover.")
    else:
        print(f"[Previews] Warning: Could not find normalized WAV file for '{song_name}'")

    # 2. Generate video preview clip (runs until half of the video duration, with fadeout and endscreen)
    keysight_dir = settings.get("keysight_dir", r"C:\Dev\meloscribe\Keysight export")
    normal_vid = os.path.join(keysight_dir, f"{song_name}.mp4")
    raw_vid = os.path.join(keysight_dir, "RAW", f"{song_name}_RAW.mp4")
    input_source = raw_vid if os.path.exists(raw_vid) else normal_vid
    
    dest_preview = os.path.join(keysight_dir, f"{song_name}_preview.mp4")
    
    if os.path.exists(input_source):
        def get_video_duration(video_path, ffmpeg_exe="ffmpeg"):
            import subprocess
            cmd = ['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
                   '-of', 'default=noprint_wrappers=1:nokey=1', video_path]
            creation_flags = 0x08000000 if os.name == 'nt' else 0
            res = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace', creationflags=creation_flags)
            try:
                return float(res.stdout.strip())
            except:
                return 60.0

        total_dur = get_video_duration(input_source)
        width, height = get_video_dimensions(input_source, ffmpeg_exe="ffmpeg")
        title_size = int(width * 0.05)
        artist_size = int(width * 0.024)
        
        tools_dir = Path(__file__).resolve().parent
        font_title = tools_dir / "fonts" / "arno_pro.ttf"
        font_artist = tools_dir / "fonts" / "montserrat.ttf"
        
        font_title_esc = escape_path_for_ffmpeg(str(font_title))
        font_artist_esc = escape_path_for_ffmpeg(str(font_artist))
        
        author = kwargs.get("author")
        if not author:
            try:
                import sqlite3
                db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "meloscribe", "backend", "analytics.db")
                conn = sqlite3.connect(db_path)
                row = conn.execute("SELECT author FROM batch_ingest_queue WHERE song_name=?", (song_name,)).fetchone()
                conn.close()
                if row: author = row[0]
            except:
                pass
        if not author: author = settings.get("author") or "Abilene"

        # -------------------------------------------------------------
        # 2a. Generate Website Preview Video (_preview.mp4)
        # Duration: 50% of total video duration starting from 0.0s
        # Endscreen text: "Unlock sheets below"
        # -------------------------------------------------------------
        web_start = 0.0
        web_clip_dur = max(10.0, total_dur / 2.0)
        web_end = web_start + web_clip_dur
        web_fade_start = max(0.0, web_clip_dur - 2.0)
        
        import tempfile
        import uuid
        uid1 = uuid.uuid4().hex[:8]
        temp_dir = tempfile.gettempdir()
        
        title_txt = os.path.join(temp_dir, f"_title_{uid1}.txt")
        artist_txt = os.path.join(temp_dir, f"_artist_{uid1}.txt")
        endscreen_web_txt = os.path.join(temp_dir, f"_endscreen_web_{uid1}.txt")
        
        with open(title_txt, "w", encoding="utf-8") as f:
            f.write(song_name)
        with open(artist_txt, "w", encoding="utf-8") as f:
            f.write(author)
        with open(endscreen_web_txt, "w", encoding="utf-8") as f:
            f.write("Unlock sheets below")
            
        title_txt_esc = escape_path_for_ffmpeg(title_txt)
        artist_txt_esc = escape_path_for_ffmpeg(artist_txt)
        endscreen_web_esc = escape_path_for_ffmpeg(endscreen_web_txt)
        
        filter_web = (
            f"[0:v]fade=type=out:start_time={web_fade_start:.2f}:duration=1.5:color=black[v_fade]; "
            f"[v_fade]drawtext=fontfile='{font_title_esc}':textfile='{title_txt_esc}':fontcolor=white:fontsize={title_size}"
            f":x=(w-text_w)/2:y=(h/2)-{int(height*0.06)}:shadowcolor=black@0.6:shadowx=4:shadowy=4"
            f":alpha='if(lt(t,1),t,if(lt(t,3.5),1,if(lt(t,4.5),4.5-t,0)))'[v1]; "
            
            f"[v1]drawtext=fontfile='{font_artist_esc}':textfile='{artist_txt_esc}':fontcolor=white:fontsize={artist_size}"
            f":x=(w-text_w)/2:y=(h/2)+{int(height*0.05)}:shadowcolor=black@0.6:shadowx=3:shadowy=3"
            f":alpha='if(lt(t,1),t,if(lt(t,3.5),1,if(lt(t,4.5),4.5-t,0)))'[v2]; "
            
            f"[v2]drawtext=fontfile='{font_title_esc}':textfile='{endscreen_web_esc}':fontcolor=white:fontsize={int(width*0.038)}"
            f":x=(w-text_w)/2:y=(h-text_h)/2:shadowcolor=black@0.6:shadowx=3:shadowy=3"
            f":alpha='if(lt(t,{web_fade_start:.2f}),0,min(1,(t-{web_fade_start:.2f})/0.5))'"
        )
        
        print(f"[Previews] Generating Website Preview clip (50% duration: 0.00s to {web_end:.2f}s, Endscreen: 'Unlock sheets below')...")
        cmd_web = [
            "ffmpeg", "-y",
            "-ss", f"{web_start:.2f}",
            "-to", f"{web_end:.2f}",
            "-i", input_source,
            "-filter_complex", filter_web,
            "-af", f"afade=type=out:start_time={web_fade_start:.2f}:duration=2.0",
            "-c:v", "libx264", "-preset", "fast", "-crf", "28",
            "-c:a", "aac", "-b:a", "128k",
            "-movflags", "+faststart",
            dest_preview
        ]
        creation_flags = 0x08000000 if os.name == 'nt' else 0
        rc_web = subprocess.run(cmd_web, stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=creation_flags).returncode
        try:
            os.remove(endscreen_web_txt)
        except:
            pass

        if rc_web == 0:
            print(f"[Previews] Success: Website Preview Video generated -> {dest_preview}")
        else:
            print(f"[Previews] Error: FFmpeg failed to generate website preview video.")

        # -------------------------------------------------------------
        # 2b. Generate Social Media Teaser Video (TikToks/{song_name} Teaser.mp4)
        # Input: TikToks/{song_name}.mp4 (9:16 vertical portrait video)
        # Duration: hook_start to hook_end (exact frame decoding with -ss after -i)
        # Title subtitle: "Short Preview"
        # Endscreen text: "Full video coming soon"
        # -------------------------------------------------------------
        is_easy_version = "easy" in song_name.lower()
        if format_mode == "full_arrangement" and hook_start is not None and hook_end is not None and float(hook_end) > float(hook_start):
            teaser_start = float(hook_start)
            teaser_end = float(hook_end)
            teaser_dur = teaser_end - teaser_start
            
            mp3_preview_dir = settings.get("mp3_preview_dir", r"C:\Dev\meloscribe\mp3 preview")
            os.makedirs(mp3_preview_dir, exist_ok=True)
            mp3_teaser_dest = os.path.join(mp3_preview_dir, f"{song_name} Teaser.mp3")

            if is_easy_version:
                # ArrangeMe requires an audio preview MP3 for Easy versions, but NO video in TikToks
                print(f"[Previews] Easy version detected: generating audio teaser only for ArrangeMe -> {mp3_teaser_dest}")
                tiktoks_dir = settings.get("tiktok_dir", r"C:\Dev\meloscribe\TikToks")
                normal_tiktok = os.path.join(tiktoks_dir, f"{song_name}.mp4")
                if os.path.exists(normal_tiktok):
                    cmd_easy_mp3 = [
                        "ffmpeg", "-y",
                        "-ss", str(teaser_start),
                        "-to", str(teaser_end),
                        "-i", normal_tiktok,
                        "-vn",
                        "-c:a", "libmp3lame",
                        "-b:a", "192k",
                        mp3_teaser_dest
                    ]
                else:
                    cmd_easy_mp3 = [
                        "ffmpeg", "-y",
                        "-ss", str(teaser_start + 2.5),
                        "-to", str(teaser_end + 2.5),
                        "-i", input_source,
                        "-vn",
                        "-c:a", "libmp3lame",
                        "-b:a", "192k",
                        mp3_teaser_dest
                    ]
                rc_easy_mp3 = subprocess.run(cmd_easy_mp3, stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=creation_flags).returncode
                if rc_easy_mp3 == 0:
                    print(f"[Previews] Success: Isolated Easy Hook MP3 for ArrangeMe saved -> {mp3_teaser_dest}")
                else:
                    print(f"[Previews] Warning: FFmpeg failed to extract isolated Easy Hook MP3.")
            else:
                # Normal version: Generate BOTH 9:16 Social Media Teaser video AND isolated Hook MP3
                tiktoks_dir = settings.get("tiktok_dir", r"C:\Dev\meloscribe\TikToks")
                teaser_dest = os.path.join(tiktoks_dir, f"{song_name} Teaser.mp4")
                
                hook_sub = kwargs.get("subtitle_hook")
                if hook_sub is None:
                    hook_sub = settings.get("subtitle_hook", "Short Preview")
                if hook_sub is None:
                    hook_sub = ""
                    
                zoom_val = kwargs.get("zoom")
                shift_val = kwargs.get("shift")

                clean_base = song_name
                if clean_base.lower().endswith(" teaser"): clean_base = clean_base[:-7].strip()
                if clean_base.lower().endswith(" easy"): clean_base = clean_base[:-5].strip()
                
                midi_p = settings.get("midi_path", "")
                if not midi_p:
                    midi_candidates = [
                        f"C:\\Cakewalk Projects\\{clean_base}\\{clean_base}.mid",
                        f"C:\\Cakewalk Projects\\{song_name}\\{song_name}.mid"
                    ]
                    for mc in midi_candidates:
                        if os.path.exists(mc):
                            midi_p = mc
                            break

                if (zoom_val is None or shift_val is None) and midi_p and os.path.exists(midi_p):
                    try:
                        from auto_crop import calculate_auto_crop
                        az, ash = calculate_auto_crop(midi_p, left_tolerance_keys=1, right_tolerance_keys=4)
                        if zoom_val is None: zoom_val = az
                        if shift_val is None: shift_val = ash
                    except Exception as ex_crop:
                        print(f"[Previews] Note calculating auto_crop for teaser: {ex_crop}")

                if zoom_val is None: zoom_val = settings.get("zoom", 1.5)
                if shift_val is None: shift_val = settings.get("shift", 0)

                zoom_val = str(zoom_val)
                shift_val = str(shift_val)
                theme_val = settings.get("theme", "warm")

                cmd_teaser = [
                    sys.executable, os.path.join(tools_dir, "video_generator.py"),
                    "--video", input_source,
                    "--title", f"{song_name} Teaser",
                    "--author", author,
                    "--subtitle", hook_sub.strip(),
                    "--start_time", str(teaser_start),
                    "--end_time", str(teaser_end),
                    "--zoom", zoom_val,
                    "--shift", shift_val,
                    "--theme", theme_val,
                    "--use_portrait_addon",
                    "--force"
                ]
                if midi_p:
                    cmd_teaser.extend(["--midipath", midi_p])
                if settings.get("enable_visualizer_normal", True) and not kwargs.get("no_visualizer_hook", False):
                    cmd_teaser.append("--visualizer")
                rc_teaser = subprocess.run(cmd_teaser, stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=creation_flags).returncode
                    
                if rc_teaser == 0 and os.path.exists(teaser_dest):
                    print(f"[Previews] Success: Created 9:16 Social Media Teaser video in TikToks -> {teaser_dest}")
                    
                    # --- Export Isolated MP3 of the Hook Video to C:\Dev\meloscribe\mp3 preview ---
                    try:
                        print(f"[Previews] Exporting isolated Hook MP3 -> {mp3_teaser_dest}...")
                        cmd_mp3 = [
                            "ffmpeg", "-y",
                            "-i", teaser_dest,
                            "-vn",
                            "-c:a", "libmp3lame",
                            "-b:a", "192k",
                            mp3_teaser_dest
                        ]
                        rc_mp3 = subprocess.run(cmd_mp3, stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=creation_flags).returncode
                        if rc_mp3 == 0:
                            print(f"[Previews] Success: Isolated Hook MP3 saved -> {mp3_teaser_dest}")
                        else:
                            print(f"[Previews] Warning: FFmpeg failed to extract isolated Hook MP3.")
                    except Exception as ex_mp3:
                        print(f"[Previews] Warning: Exception exporting isolated Hook MP3: {ex_mp3}")
                else:
                    print(f"[Previews] Error: FFmpeg failed to generate 9:16 teaser video.")

        try:
            os.remove(title_txt)
            os.remove(artist_txt)
        except:
            pass
    else:
        print(f"[Previews] Warning: Could not find normal video for preview generation: {input_source}")

def get_stripe_api_key():
    settings = get_settings()
    is_sandbox = settings.get("environment", "sandbox") == "sandbox"
    if is_sandbox:
        return settings.get("stripe_sandbox_secret_key")
    else:
        return settings.get("stripe_live_secret_key")

def get_or_create_stripe_price(clean_name, difficulty, price_str):
    try:
        import stripe
    except ImportError:
        print("[Stripe Auto-Price] WARNING: 'stripe' library is not installed in the current Python environment.")
        return None
        
    api_key = get_stripe_api_key()
    if not api_key:
        print("[Stripe Auto-Price] WARNING: No Stripe secret key found in settings.json.")
        return None
        
    stripe.api_key = api_key
    product_name = f"{clean_name} ({difficulty}) - Piano Sheet Music"
    
    try:
        # Clean price string (e.g. "6.00" -> 600, "4 €" -> 400, "3,00" -> 300)
        clean_price = price_str.replace("€", "").replace("$", "").replace(",", ".").strip()
        amount_cents = int(float(clean_price) * 100)
    except Exception as e:
        print(f"[Stripe Auto-Price] Failed to parse price '{price_str}': {e}")
        return None
        
    try:
        # Search for existing active product with matching name
        escaped_name = product_name.replace("'", "\\'")
        query = f"name:'{escaped_name}' and active:'true'"
        products = stripe.Product.search(query=query)
        
        product_id = None
        if products.data:
            product_id = products.data[0].id
            print(f"[Stripe Auto-Price] Found existing Stripe product: {product_id} for '{product_name}'")
        else:
            # Create a new product
            product = stripe.Product.create(
                name=product_name,
                description=f"Sheet music package (PDF, MIDI, practice videos) for {clean_name} ({difficulty} version)",
            )
            product_id = product.id
            print(f"[Stripe Auto-Price] Created new Stripe product: {product_id} for '{product_name}'")
            
        # Check if a price with this product_id and amount_cents already exists
        prices = stripe.Price.list(product=product_id, active=True, limit=50)
        for p in prices.data:
            if p.unit_amount == amount_cents and p.currency == "eur":
                print(f"[Stripe Auto-Price] Reusing existing active Stripe price: {p.id}")
                return p.id
                
        # If no matching price exists, create a new price
        price = stripe.Price.create(
            product=product_id,
            unit_amount=amount_cents,
            currency="eur",
        )
        print(f"[Stripe Auto-Price] Created new Stripe price: {price.id} ({clean_price} EUR)")
        return price.id
        
    except Exception as e:
        print(f"[Stripe Auto-Price] Error interacting with Stripe API: {e}")
        return None

def add_song_to_website(song_name, price, kofi_id, format_mode=None, author="Dave Kerr", **kwargs):
    # Auto-generate previews first!
    try:
        generate_previews(song_name, format_mode or "viral_part", **kwargs)
    except Exception as e:
        print(f"[Website Sync] Error generating preview assets: {e}")

    website_json_path = r"c:\Dev\meloscribe-frontend\website\src\data\songs.json"
    if not os.path.exists(website_json_path):
        print(f"[Website Sync] Path not found: {website_json_path}")
        return False
        
    try:
        import json
        with open(website_json_path, "r", encoding="utf-8") as f:
            songs_list = json.load(f)
            
        # Check if global_settings object exists
        global_settings = None
        for s in songs_list:
            if s.get("id") == "global_settings":
                global_settings = s
                break
                
        # Generate next ID
        numeric_ids = []
        for s in songs_list:
            try:
                if s.get("id") != "global_settings":
                    numeric_ids.append(int(s.get("id")))
            except:
                pass
        next_id = str(max(numeric_ids) + 1) if numeric_ids else "1"
        
        is_easy = "easy" in song_name.lower()
        if song_name.lower().endswith(" easy"):
            clean_name = song_name[:-5].strip()
        elif song_name.lower().endswith("easy"):
            clean_name = song_name[:-4].strip()
        else:
            clean_name = song_name
            
        difficulty = "Easy" if is_easy else "Original"
        
        # Clean price output
        clean_price = str(price).strip()
        try:
            pval = float(clean_price.replace("€", "").replace(",", ".").strip())
            if pval == 0:
                clean_price = "Free"
            elif pval.is_integer():
                clean_price = f"{int(pval)} €"
            else:
                clean_price = f"{pval:.2f} €"
        except ValueError:
            if not (clean_price.endswith("€") or clean_price.endswith("$") or clean_price.lower() == "free"):
                clean_price += " €"
            
        # Determine theme from database if available
        theme = None
        db_path = r"C:\Dev\meloscribe-app\tools\meloscribe\backend\analytics.db"
        if os.path.exists(db_path):
            import sqlite3
            try:
                conn = sqlite3.connect(db_path)
                row = conn.execute("SELECT theme FROM batch_ingest_queue WHERE song_name = ?", (song_name,)).fetchone()
                if row:
                    theme = row[0]
                conn.close()
            except Exception as e:
                print(f"[Website Sync] DB theme error: {e}")
                
        if not theme:
            theme = "warm" # default fallback
            
        # Select gradient based on theme
        if theme == "cold":
            gradient = "from-neon-cyan/20 via-dark-700 to-neon-cyan/5"
        elif theme == "green":
            gradient = "from-emerald-500/20 via-dark-700 to-teal-500/5"
        elif theme == "violet":
            gradient = "from-violet-500/20 via-dark-700 to-purple-500/5"
        elif theme == "platinum":
            gradient = "from-slate-400/20 via-dark-700 to-zinc-500/5"
        else: # warm
            gradient = "from-orange-500/20 via-dark-700 to-orange-500/5"
            
        # Auto-generate Stripe Price ID if kofi_id is a dummy and it is not a free song
        final_price_id = kofi_id
        if kofi_id.startswith("prod_dummy") and "free" not in clean_price.lower():
            generated_id = get_or_create_stripe_price(clean_name, difficulty, clean_price)
            if generated_id:
                final_price_id = generated_id
        
        # Auto-detect hook / preview start for audio hover playback on website
        preview_start = kwargs.get("hook_start")
        if preview_start is None:
            try:
                import sqlite3
                db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "meloscribe", "backend", "analytics.db")
                conn = sqlite3.connect(db_path)
                row = conn.execute("SELECT hook_start FROM batch_ingest_queue WHERE song_name=?", (song_name,)).fetchone()
                if not row or row[0] is None:
                    row = conn.execute("SELECT hook_start FROM tracks WHERE song_name=?", (song_name,)).fetchone()
                conn.close()
                if row and row[0] is not None:
                    preview_start = row[0]
            except:
                pass

        new_song = {
            "id": next_id,
            "title": clean_name,
            "artist": author,
            "difficulty": difficulty,
            "price": clean_price,
            "kofiId": final_price_id,
            "stripePriceId": final_price_id,
            "coverImage": f"/covers/{clean_name}_clean.jpg",
            "audioPreviewUrl": f"/audio-previews/{clean_name}.mp3",
            "gradient": gradient,
            "theme": theme
        }
        if preview_start is not None and float(preview_start) > 0:
            new_song["previewStart"] = round(float(preview_start), 1)
        
        # Check for existing song entry by clean_name
        existing_idx = None
        for idx, s in enumerate(songs_list):
            s_title = s.get("title", "").strip().lower()
            if s_title == clean_name.strip().lower():
                existing_idx = idx
                break
        
        if existing_idx is not None:
            existing = songs_list[existing_idx]
            if is_easy:
                existing["hasEasy"] = True
                if existing.get("difficulty") == "Original" or existing.get("hasOriginal"):
                    existing["difficulty"] = "Original / Easy"
                    existing["hasOriginal"] = True
                else:
                    existing["difficulty"] = "Easy"
                existing["easyPrice"] = clean_price
                if not final_price_id.startswith("prod_dummy"):
                    existing["easyStripePriceId"] = final_price_id
                    existing["easyKofiId"] = final_price_id
                elif not existing.get("easyStripePriceId"):
                    existing["easyStripePriceId"] = final_price_id
                    existing["easyKofiId"] = final_price_id
                if not existing.get("easyId"):
                    existing["easyId"] = next_id
            else:
                existing["hasOriginal"] = True
                if existing.get("difficulty") == "Easy" or existing.get("hasEasy"):
                    existing["difficulty"] = "Original / Easy"
                    existing["hasEasy"] = True
                else:
                    existing["difficulty"] = "Original"
                existing["price"] = clean_price
                if not final_price_id.startswith("prod_dummy"):
                    existing["stripePriceId"] = final_price_id
                    existing["kofiId"] = final_price_id
                elif not existing.get("stripePriceId"):
                    existing["stripePriceId"] = final_price_id
                    existing["kofiId"] = final_price_id
            
            if preview_start is not None and float(preview_start) > 0 and not existing.get("previewStart"):
                existing["previewStart"] = round(float(preview_start), 1)

            # Move updated song entry to top of songs.json list (index 0) so it appears first on website
            updated_song = songs_list.pop(existing_idx)
            songs_list.insert(0, updated_song)
            print(f"[Website Sync] Merged {difficulty} into existing entry for '{clean_name}' (ID: {updated_song.get('id')}) as '{updated_song.get('difficulty')}' and moved to top of catalog!")
        else:
            if is_easy:
                new_song["hasEasy"] = True
                new_song["hasOriginal"] = False
                new_song["easyPrice"] = clean_price
                new_song["easyStripePriceId"] = final_price_id
                new_song["easyKofiId"] = final_price_id
            else:
                new_song["hasOriginal"] = True
                new_song["hasEasy"] = False
            songs_list.insert(0, new_song)
            print(f"[Website Sync] Added new entry for '{clean_name}' ({difficulty}, ID: {next_id}) to top of catalog (index 0)!")
        
        # Write to frontend songs.json
        with open(website_json_path, "w", encoding="utf-8") as f:
            json.dump(songs_list, f, indent=2, ensure_ascii=False)
            
        # Write to local backend songs.json
        local_json_path = r"c:\Dev\meloscribe-app\tools\meloscribe\backend\songs.json"
        if os.path.exists(local_json_path):
            try:
                with open(local_json_path, "w", encoding="utf-8") as f:
                    json.dump(songs_list, f, indent=2, ensure_ascii=False)
            except Exception as e:
                print(f"[Website Sync] Warning: could not write local songs.json: {e}")
            
        # Copy clean cover to website directory so it's committed to Git
        settings = get_settings()
        covers_dir = settings.get("covers_dir", r"C:\Dev\meloscribe\Covers")
        local_cover_path = os.path.join(covers_dir, f"{clean_name}_clean.jpg")
        website_cover_path = os.path.join(r"c:\Dev\meloscribe-frontend\website\public\covers", f"{clean_name}_clean.jpg")
        if os.path.exists(local_cover_path):
            try:
                import shutil
                os.makedirs(os.path.dirname(website_cover_path), exist_ok=True)
                shutil.copy2(local_cover_path, website_cover_path)
                print(f"[Website Sync] Copied clean cover to website assets: {website_cover_path}")
            except Exception as copy_err:
                print(f"[Website Sync] Warning: failed to copy cover to website: {copy_err}")
                
        print(f"[Website Sync] Automatically added song '{clean_name}' (ID: {next_id}, Price: {clean_price}, Theme: {theme}, Ko-fi: {kofi_id}) to website songs.json!")

        # Automatically commit and push catalog changes + preview assets to GitHub and VM
        try:
            import subprocess
            frontend_dir = r"c:\Dev\meloscribe-frontend"
            if os.path.exists(frontend_dir):
                creation_flags = 0x08000000 if os.name == 'nt' else 0
                subprocess.run(["git", "add", "website/src/data/songs.json", "website/public/audio-previews/", "website/public/covers/"], cwd=frontend_dir, check=False, creationflags=creation_flags)
                res_commit = subprocess.run(["git", "commit", "-m", f"Auto-sync {clean_name} to website catalog"], cwd=frontend_dir, capture_output=True, text=True, creationflags=creation_flags)
                if "nothing to commit" not in res_commit.stdout.lower() and "nothing to commit" not in res_commit.stderr.lower():
                    subprocess.run(["git", "push", "origin", "main"], cwd=frontend_dir, check=False, creationflags=creation_flags)
                    print("[Website Sync] Successfully committed & pushed catalog updates to GitHub for live website deployment!")
                else:
                    print("[Website Sync] Catalog already up to date on GitHub.")

                ssh_key = r"C:\Dev\ssh-key-2026-05-07.key"
                if not os.path.exists(ssh_key):
                    ssh_key = r"C:\Dev\meloscribe\ssh-key-2026-05-07.key"
                local_songs = r"c:\Dev\meloscribe-frontend\website\src\data\songs.json"
                if os.path.exists(ssh_key) and os.path.exists(local_songs):
                    subprocess.run([
                        "scp", "-i", ssh_key, "-o", "StrictHostKeyChecking=accept-new",
                        local_songs,
                        "ubuntu@152.70.23.171:/home/ubuntu/meloscribe/tools/meloscribe/backend/songs.json"
                    ], check=False, creationflags=creation_flags)
                    print("[Website Sync] Successfully synced songs.json to production VM!")
        except Exception as push_err:
            print(f"[Website Sync] Warning: Git push / VM sync error: {push_err}")
        return True
    except Exception as e:
        print(f"[Website Sync] Error syncing song to website: {e}")
        return False

def format_description_template(tpl, song_arg, author_arg, label_arg, medium_arg=None):
    is_easy = song_arg.lower().endswith(" easy")
    
    base_song = song_arg
    if base_song.lower().endswith(" easy"):
        base_song = base_song[:-5].strip()
    if base_song.lower().endswith(" teaser"):
        base_song = base_song[:-7].strip()
        
    slug = base_song.lower()
    slug = re.sub(r'[^a-z0-9]+', '-', slug)
    slug = slug.strip('-')
    
    hashtag_name = base_song.lower()
    hashtag_name = re.sub(r'[^a-z0-9]', '', hashtag_name)
    
    version_param = "easy" if is_easy else "original"
    song_link = f"https://meloscribe.dev/sheets?song={slug}&version={version_param}"
    
    res = tpl
    res = re.sub(r'#\s*\{song\}', f"#{hashtag_name}", res)
    res = res.replace("{song_hashtag}", f"#{hashtag_name}")
    res = res.replace("{song_link}", song_link)
    res = res.replace("{song}", base_song)
    res = res.replace("{label}", label_arg)
    res = res.replace("{author}", author_arg)
    if medium_arg:
        res = res.replace("{medium}", medium_arg)
        
    # Strip any http/https links to ensure no description contains links
    res = re.sub(r'https?://\S+', '', res).strip()
    # Normalize multiple newlines/spaces at the end
    res = re.sub(r'\n{3,}', '\n\n', res)
    return res

def refresh_pinterest_token():
    tokens_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "meloscribe", "backend", "pinterest_tokens.json")
    if not os.path.exists(tokens_path):
        return False
    try:
        import json
        import base64
        import requests
        with open(tokens_path, "r", encoding="utf-8") as f:
            tokens = json.load(f)
        app_id = tokens.get("pinterest_app_id")
        app_secret = tokens.get("pinterest_app_secret")
        refresh_token = tokens.get("pinterest_refresh_token")
        if not app_id or not app_secret or not refresh_token:
            return False
            
        auth_str = f"{app_id}:{app_secret}"
        b64_auth = base64.b64encode(auth_str.encode("utf-8")).decode("utf-8")
        resp = requests.post(
            "https://api.pinterest.com/v5/oauth/token",
            headers={
                "Authorization": f"Basic {b64_auth}",
                "Content-Type": "application/x-www-form-urlencoded"
            },
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token
            },
            timeout=15
        )
        if resp.status_code == 200:
            res_data = resp.json()
            new_access = res_data.get("access_token")
            if new_access:
                tokens["pinterest_access_token"] = new_access
                if "refresh_token" in res_data:
                    tokens["pinterest_refresh_token"] = res_data["refresh_token"]
                with open(tokens_path, "w", encoding="utf-8") as f:
                    json.dump(tokens, f, indent=4)
                print("[Pinterest Bot] Token refreshed successfully.")
                return True
        print(f"[Pinterest Bot] Token refresh failed: {resp.status_code} - {resp.text}")
        return False
    except Exception as e:
        print(f"[Pinterest Bot] Exception during token refresh: {e}")
        return False

def run_pinterest(song_name, profile="normal", author="Dave Kerr", board_id=None):
    # Auto-refresh token before posting
    refresh_pinterest_token()
    
    print(f"[Pinterest Bot] Creating Pin for '{song_name}' (profile: {profile}, author: {author})...")
    tokens_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "meloscribe", "backend", "pinterest_tokens.json")
    if not os.path.exists(tokens_path):
        print("[Pinterest Bot] Error: 'pinterest_tokens.json' not found. Skipping.")
        return False
        
    try:
        import json
        import urllib.parse
        import requests
        
        with open(tokens_path, "r", encoding="utf-8") as f:
            tokens = json.load(f)
            
        access_token = tokens.get("pinterest_access_token")
        if not access_token:
            print("[Pinterest Bot] Error: No access token found in tokens file.")
            return False
            
        # Determine Board ID BEFORE stripping " Easy"
        if not board_id:
            is_easy = song_name.lower().endswith(" easy")
            board_id = tokens.get("pinterest_board_easy") if is_easy else tokens.get("pinterest_board_intermediate")
            if not board_id:
                # Fallback to old key structures
                board_id = tokens.get("pinterest_board_normal") or tokens.get("pinterest_board_slow") or tokens.get("pinterest_board_id")
            
        if not board_id:
            print("[Pinterest Bot] Error: No Pinterest Board ID configured.")
            return False
            
        # Clean song name for assets and hashtags
        base_song = song_name
        if base_song.lower().endswith(" easy"):
            base_song = base_song[:-5].strip()
            
        # Build image URL
        encoded_name = urllib.parse.quote(base_song)
        cover_url = f"https://meloscribe.dev/covers/{encoded_name}_clean.jpg"
        
        # Build Title
        is_tut = "tutorial" in profile.lower() or "slow" in profile.lower()
        label_part = "Easy" if is_easy else "Original"
        if is_tut:
            label_part += " Tutorial"
            
        # Try format {song} - {author} {label}
        title = f"🎹 {base_song} - {author} {label_part}"
        if len(title) > 100:
            title = f"🎹 {base_song} - {label_part}"
        if len(title) > 100:
            title = title[:97] + "..."
            
        # Build Description
        settings = get_settings()
        default_tpl = (
            "Enjoy this piano arrangement of {song} by {author}! Whether you're here to listen or want to learn this piece yourself - I've got you covered.\n\n"
            "👉 Click the Pin to get the Sheet Music (PDF), MIDI & practice videos!\n\n"
            "Follow for more aesthetic piano covers and tutorials.\n\n"
            "#piano #pianocover #pianotutorial #sheetmusic #{song}"
        )
        desc_template = settings.get("desc_template_pinterest") or tokens.get("desc_template_pinterest") or default_tpl
        if is_tut:
            desc_template = desc_template.replace("Enjoy this piano arrangement", "Enjoy this piano tutorial")
        desc = format_description_template(desc_template, song_name, author, label_part)
        if len(desc) > 500:
            desc = desc[:497] + "..."
            
        # Make link
        slug = base_song.lower()
        slug = re.sub(r'[^a-z0-9]+', '-', slug)
        slug = slug.strip('-')
        version_param = "easy" if is_easy else "original"
        song_link = f"https://meloscribe.dev/sheets?song={slug}&version={version_param}"
        
        # Pin data structure
        pin_data = {
            "link": song_link,
            "title": title,
            "description": desc,
            "board_id": board_id,
            "media_source": {
                "source_type": "image_url",
                "url": cover_url
            }
        }
        
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json"
        }
        
        url = "https://api.pinterest.com/v5/pins"
        resp = requests.post(url, json=pin_data, headers=headers, timeout=15)
        if resp.status_code == 403 and "use API Sandbox" in resp.text:
            print("[Pinterest Bot] App is in Trial mode. Retrying with Pinterest Sandbox API...")
            url = "https://api-sandbox.pinterest.com/v5/pins"
            resp = requests.post(url, json=pin_data, headers=headers, timeout=15)

        if resp.status_code in [200, 201]:
            pin_info = resp.json()
            print(f"[Pinterest Bot] Success: Created Pin ID {pin_info.get('id')} on Board ID {board_id}!")
            return True
        else:
            print(f"[Pinterest Bot] Error (status {resp.status_code}): {resp.text}")
            return False
            
    except Exception as e:
        print(f"[Pinterest Bot] Exception failed: {e}")
        return False

def run_kofi(song_name, price, is_full=True, yt_shorts_url=None):
    tools_dir = os.path.dirname(os.path.abspath(__file__))
    
    # Since you requested to use your main profile, we MUST kill any active Brave instances
    # first, otherwise Chromium will throw a SQLite lock error and crash the bot.
    print("Closing active Brave instances to unlock main profile...")
    os.system("taskkill /F /IM brave.exe /T 2>NUL")
    time.sleep(2) # Give Windows 2 seconds to flush the lock files
    
    settings = get_settings()
    user_data_dir = settings.get("browser_user_data", os.path.expanduser(r"~\AppData\Local\BraveSoftware\Brave-Browser\User Data"))
    
    executable_path = settings.get("browser_exec", "")
    if not executable_path or not os.path.exists(executable_path):
        executable_paths = [
            os.path.expanduser(r"~\AppData\Local\BraveSoftware\Brave-Browser\Application\brave.exe"),
            r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe"
        ]
        executable_path = next((p for p in executable_paths if os.path.exists(p)), None)
    
    if not executable_path:
        print("Error: Could not find Brave/Chrome executable.")
        return

    print("Submitting Ko-Fi Package...")
    
    with sync_playwright() as p:
        browser = p.chromium.launch_persistent_context(
            user_data_dir=user_data_dir,
            executable_path=executable_path,
            headless=False,  # Cloudflare blocks headless browsers
            ignore_default_args=["--enable-automation"],
            args=["--profile-directory=Default", "--window-size=1200,900"],
            no_viewport=True
        )
        
        page = browser.pages[0]
        
        # --- USING PROVIDED YOUTUBE URL ---
        if yt_shorts_url:
            print(f"\n--- Using Provided YouTube URL: {yt_shorts_url} ---")
        else:
            print("\n--- WARNING: No YouTube URL provided for Ko-Fi ---")
        
        # 1. Navigate to Shop Settings
        dashboard_url = "https://ko-fi.com/shop/settings?productType=0"
        print(f"Navigating to {dashboard_url}")
        page.goto(dashboard_url, timeout=60000)
        page.wait_for_load_state("domcontentloaded")
        time.sleep(3)
        
        # Dismiss cookie consent banner if present (blocks all buttons!)
        try:
            cookie_btn = page.locator("button.cky-btn-accept, button:has-text('Accept All')").first
            if cookie_btn.count() > 0 and cookie_btn.is_visible(timeout=2000):
                cookie_btn.click()
                print("Cookie consent dismissed (Accept All)")
                time.sleep(1)
        except:
            # Force remove via JS if click fails
            page.evaluate("document.querySelector('.cky-consent-container')?.remove()")
            print("Cookie banner removed via JS")
        
        # --- RETROACTIVE: Edit the latest product to add YouTube link ---
        if yt_shorts_url:
            print(f"\n--- Updating previous product with YouTube link ---")
            try:
                # Click the EDIT button on the first (latest) product
                # Try multiple selectors since Ko-Fi's button markup varies
                edit_btn = None
                for edit_sel in [
                    "a[title='Edit this product']",
                    "a[title='Edit this shop item']",
                    "a[title='Edit']", 
                    "a.edit-product",
                    "a[href*='/ManageShop/Edit']",
                    "a[href*='/edit']",
                    ".shop-item-actions a:first-child",
                    ".kfds-c-shop-item-actions a:first-child"
                ]:
                    try:
                        btn = page.locator(edit_sel).first
                        if btn.count() > 0:
                            edit_btn = btn
                            print(f"Edit button found via: {edit_sel}")
                            break
                    except:
                        continue
                
                if not edit_btn:
                    raise Exception("Could not find edit button on Ko-Fi shop page")
                
                edit_btn.click(force=True)
                
                page.wait_for_url("**/edit**", timeout=15000)
                page.wait_for_load_state("domcontentloaded")
                time.sleep(3)
                
                # Dismiss cookie banner on edit page
                page.evaluate("document.querySelector('.cky-consent-container')?.remove()")

                # Look for the media/video link input field
                # Ko-Fi has a field for linking external content (video preview / embed)
                link_filled = False
                
                # Scroll down to make sure all form fields are rendered
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                time.sleep(1)
                
                # Try common field patterns for the media/video link
                for selector in [
                    "input#VideoUrl", "input#MediaUrl", "input#PreviewUrl",
                    "input#EmbedUrl", "input#ExternalUrl", "input#MediaLink",
                    "input[name='VideoUrl']", "input[name='MediaUrl']", "input[name='EmbedUrl']",
                    "input[placeholder*='youtube']", "input[placeholder*='video']",
                    "input[placeholder*='YouTube']", "input[placeholder*='Video']",
                    "input[placeholder*='embed']", "input[placeholder*='Embed']",
                    "input[placeholder*='link']", "input[placeholder*='URL']",
                    "input[placeholder*='url']", "input[placeholder*='http']",
                    "textarea[placeholder*='youtube']", "textarea[placeholder*='embed']"
                ]:
                    try:
                        field = page.locator(selector).first
                        if field.count() > 0 and field.is_visible():
                            field.fill(yt_shorts_url)
                            print(f"YouTube link pasted into field: {selector}")
                            link_filled = True
                            break
                    except:
                        continue
                
                if not link_filled:
                    print("WARNING: Could not find video/embed link field on edit page.")
                
                # Save the edited product
                if link_filled:
                    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    time.sleep(2)
                    # Ko-Fi uses <input type='button' value='Save changes'>, NOT <button>!
                    # The text is in the value attribute, not textContent
                    save_input = page.locator("input[value*='Save changes'], input[value*='Save Changes'], input#saveAndPublishButton").first
                    try:
                        save_input.wait_for(state="visible", timeout=5000)
                        save_input.click()
                        print(f"Clicked save: '{save_input.get_attribute('value')}'")
                    except:
                        # JS fallback
                        result = page.evaluate("""
                            () => {
                                const inp = document.querySelector('input[value*="Save"]');
                                if (inp) { inp.click(); return inp.value; }
                                return null;
                            }
                        """)
                        if result:
                            print(f"Clicked save via JS: '{result}'")
                        else:
                            print("WARNING: No save input found!")
                    time.sleep(5)
                    print("Previous product updated with YouTube link!")
                
                # Navigate back to shop settings for cloning
                page.goto(dashboard_url, timeout=60000)
                page.wait_for_load_state("domcontentloaded")
                time.sleep(3)
                
            except Exception as e:
                print(f"Retroactive link update failed (non-critical): {e}")
                # Navigate back to shop settings in case we got stuck
                page.goto(dashboard_url, timeout=60000)
                page.wait_for_load_state("domcontentloaded")
                time.sleep(3)
        
        # 2. Clone Context Sequence
        print("Cloning latest template...")
        clone_anchor = page.locator("a[title='Clone this product']").first
        if clone_anchor.count() > 0:
            clone_anchor.click(force=True)
        else:
            print("WARNING: Clone button not found, attempting JS fallback")
            page.evaluate("document.querySelector('a[title=\"Clone this product\"]')?.click()")
            
        time.sleep(1) # wait for SweetAlert2 modal animation
        confirm_btn = page.locator("button.swal2-confirm").first
        if confirm_btn.count() > 0:
            confirm_btn.click(force=True)
        else:
            page.evaluate("document.querySelector('button.swal2-confirm')?.click()")
        
        # 3. Wait for Edit screen
        page.wait_for_url("**/edit**", timeout=30000)
        page.wait_for_load_state("domcontentloaded")
        time.sleep(3)
        
        # Dismiss cookie banner on clone edit page
        page.evaluate("document.querySelector('.cky-consent-container')?.remove()")
        
        print("Populating product listing...")
        
        # Clear the embed media field (carried over from the cloned product)
        # We don't want the previous song's YouTube link in the new product
        for selector in [
            "input#VideoUrl", "input#MediaUrl", "input#PreviewUrl",
            "input[name='VideoUrl']", "input[name='MediaUrl']",
            "input[placeholder*='youtube']", "input[placeholder*='video']",
            "input[placeholder*='YouTube']", "input[placeholder*='Video']",
            "input[placeholder*='link']", "input[placeholder*='URL']"
        ]:
            try:
                field = page.locator(selector).first
                if field.count() > 0 and field.is_visible():
                    field.fill("")
                    print(f"Cleared embed media field: {selector}")
                    break
            except:
                continue
        
        # 4. Fill Title
        title_input = page.locator("input#Name")
        title_input.wait_for(state="visible", timeout=10000)
        title_input.fill(f"{song_name} - [MIDI + Sheet + Videos]")
        print(f"Title set: '{song_name} - [MIDI + Sheet + Videos]'")
        
        # 5. Fill Description
        desc_input = page.locator("textarea#Description")
        
        is_easy = "easy" in song_name.lower()
        is_condensed = False
        
        # Clean song name for template formatting
        clean_song_name = song_name
        if clean_song_name.lower().endswith(" easy"):
            clean_song_name = clean_song_name[:-5].strip()
        elif clean_song_name.lower().endswith("easy"):
            clean_song_name = clean_song_name[:-4].strip()
            
        # Determine template key
        if is_easy:
            if is_condensed:
                tpl_key = "desc_template_kofi_easy_viral_part"
                fallback_tpl = (
                    "Get the condensed easy learning package for my '{song}' tutorial! This download contains the viral/short part of the song in an easy/simplified arrangement and includes:\n\n"
                    "PDF easy sheet music, a high-quality simplified MIDI file, a bonus slow-speed simplified MIDI file for easy practice, "
                    "and both 2K-quality easy tutorial videos (Easy Version, Slow Easy Version) for offline learning.\n\n"
                    "This simplified sheet music/MIDI contains all unique musical sections as shown in the video."
                )
            else:
                tpl_key = "desc_template_kofi_easy_full_arrangement"
                fallback_tpl = (
                    "Get the easy learning package for my '{song}' tutorial! This download includes:\n\n"
                    "PDF easy sheet music, a high-quality simplified MIDI file, a bonus slow-speed simplified MIDI file for easy practice, "
                    "and both 2K-quality easy tutorial videos (Easy Version, Slow Easy Version) for offline learning.\n\n"
                    "This simplified sheet music/MIDI contains all unique musical sections (Intro, Verse, Chorus, Bridge) as shown in the video. "
                    "Repetitions may be omitted for brevity, but all distinct parts are included."
                )
        else:
            if is_condensed:
                tpl_key = "desc_template_kofi_original_viral_part"
                fallback_tpl = (
                    "Get the condensed learning package for my '{song}' tutorial! This download contains the viral/short part of the song and includes:\n\n"
                    "PDF sheet music, a high-quality MIDI file in original speed, a bonus slow-speed MIDI file for easy practice, "
                    "and both 2K-quality tutorial videos (Original Version, Slow Version) for offline learning.\n\n"
                    "This sheet music/MIDI contains all unique musical sections as shown in the video."
                )
            else:
                tpl_key = "desc_template_kofi_original_full_arrangement"
                fallback_tpl = (
                    "Get the learning package for my '{song}' tutorial! This download includes:\n\n"
                    "PDF sheet music, a high-quality MIDI file in original speed, a bonus slow-speed MIDI file for easy practice, "
                    "and both 2K-quality tutorial videos (Original Version, Slow Version) for offline learning.\n\n"
                    "This sheet music/MIDI contains all unique musical sections (Intro, Verse, Chorus, Bridge) as shown in the video. "
                    "Repetitions may be omitted for brevity, but all distinct parts are included."
                )
                
        kofi_tpl = settings.get(tpl_key) or settings.get("desc_template_kofi") or fallback_tpl
        desc_text = kofi_tpl.replace("{song}", clean_song_name)
        desc_input.fill(desc_text)
        print(f"Description set for mode: {'Easy' if is_easy else 'Original'} {'Viral Part' if is_condensed else 'Full Arrangement'}.")
        
        # 6. Fill Price
        price_input = page.locator("input#price")
        price_input.fill(str(price).replace(",", "."))
        print(f"Price set: {price}")
        
        # 7. Upload Files (Preview Image + Digital Assets)
        print("\n--- Step 7: Uploading Files ---")
        root_dir = os.path.dirname(tools_dir)
        
        # 7a. Preview Image via Dropzone hidden input
        img_path = os.path.join(root_dir, "Covers", f"{song_name} slow.jpg")
        print(f"[7a] Looking for preview cover: {img_path}")
        if os.path.exists(img_path):
            dz_inputs = page.locator("input.dz-hidden-input")
            dz_count = dz_inputs.count()
            print(f"[7a] Found {dz_count} dropzone input(s)")
            if dz_count >= 1:
                print(f"[7a] Uploading cover to dropzone[0]...")
                dz_inputs.nth(0).set_input_files(img_path)
                time.sleep(3)
                print(f"[7a] Cover uploaded!")
            else:
                print(f"[7a] No dropzone inputs found for cover upload")
        else:
            print(f"[7a] Cover file not found. Skipping.")
        
        # 7b. Digital Assets via file chooser dialog (click "Upload a file" area)
        pkg_dir = os.path.join(root_dir, "packages", song_name)
        print(f"\n[7b] Looking for package files in: {pkg_dir}")
        if os.path.isdir(pkg_dir):
            asset_files = [os.path.join(pkg_dir, f) for f in os.listdir(pkg_dir) if os.path.isfile(os.path.join(pkg_dir, f))]
            print(f"[7b] Found {len(asset_files)} file(s):")
            for af in asset_files:
                print(f"  - {os.path.basename(af)} ({os.path.getsize(af) / 1024 / 1024:.1f} MB)")
            
            if asset_files:
                # Scroll down to find the "Upload a file" area
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                time.sleep(1)
                
                # Find clickable upload trigger
                upload_trigger = None
                for trigger_sel in [
                    "text=Upload a file",
                    "text=upload a file",
                    ".upload-file-area",
                    "[class*='upload'] [class*='trigger']",
                    ".file-upload-area",
                    "text=Drag and drop",
                    "text=drag and drop"
                ]:
                    try:
                        el = page.locator(trigger_sel).first
                        if el.count() > 0 and el.is_visible(timeout=2000):
                            upload_trigger = el
                            print(f"[7b] Upload trigger found via: {trigger_sel}")
                            break
                    except:
                        continue
                
                if not upload_trigger:
                    # Also try file input elements that aren't dropzone
                    all_file_inputs = page.locator("input[type='file']")
                    file_input_count = all_file_inputs.count()
                    print(f"[7b] No upload trigger text found. Found {file_input_count} file input(s) total")
                    
                    # Try using the last file input (not the dropzone one)
                    if file_input_count >= 2:
                        print(f"[7b] Using last file input for assets...")
                        all_file_inputs.nth(file_input_count - 1).set_input_files(asset_files)
                        time.sleep(3)
                        print(f"[7b] Assets attached via file input!")
                    else:
                        print(f"[7b] ERROR: No suitable file upload mechanism found.")
                else:
                    # Use file chooser pattern: click the trigger, intercept the dialog
                    print(f"[7b] Clicking upload trigger and intercepting file dialog...")
                    try:
                        with page.expect_file_chooser(timeout=10000) as fc_info:
                            upload_trigger.click()
                        file_chooser = fc_info.value
                        file_chooser.set_files(asset_files)
                        print(f"[7b] {len(asset_files)} asset file(s) attached via file chooser!")
                        time.sleep(3)
                    except Exception as fc_err:
                        print(f"[7b] File chooser failed: {fc_err}")
                        # Fallback: try setting via any available file input
                        all_file_inputs = page.locator("input[type='file']")
                        if all_file_inputs.count() >= 2:
                            print(f"[7b] Fallback: using file input #{all_file_inputs.count()-1}")
                            all_file_inputs.nth(all_file_inputs.count() - 1).set_input_files(asset_files)
                            time.sleep(3)
        else:
            print(f"[7b] Package folder not found: {pkg_dir}")
            
        # Success! Yield back to User
        print("\n" + "="*50)
        print(f"Ko-Fi Automation Completed for: {song_name}")
        print("="*50)
        
        # Scroll down first so upload text and Save button are visible
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        time.sleep(1)
        
        # Wait for upload to start
        print("Waiting 10s for upload process to start...")
        time.sleep(10)
        
        # Poll for upload completion - re-scroll each time to keep text visible
        print("Polling for upload completion...")
        upload_elapsed = 0
        while True:
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            time.sleep(0.5)
            try:
                waiting_count = page.evaluate("""
                    () => {
                        const bodyText = document.body.innerText.toLowerCase();
                        if (bodyText.includes('waiting for upload') || bodyText.includes('uploading')) return 1;
                        return 0;
                    }
                """)
                if waiting_count == 0:
                    print(f"Upload complete after {upload_elapsed + 10}s total!")
                    break
                else:
                    if upload_elapsed == 0:
                        print(f"  Upload in progress...")
            except:
                pass
            time.sleep(5)
            upload_elapsed += 5
            if upload_elapsed % 30 == 0:
                print(f"  ... still uploading ({upload_elapsed + 10}s total)")
        
        time.sleep(2)
        
        # Dismiss cookie banner again just in case
        page.evaluate("document.querySelector('.cky-consent-container')?.remove()")
        
        # Ko-Fi uses <input type='button' id='saveAndPublishButton' value='Save and publish'>
        print("Looking for Save and publish button...")
        publish_input = page.locator("input#saveAndPublishButton, input[value*='Save and publish'], input[value*='Save and Publish']").first
        try:
            publish_input.scroll_into_view_if_needed()
            publish_input.wait_for(state="visible", timeout=10000)
            publish_input.click()
            print(f"Clicked: '{publish_input.get_attribute('value')}'")
        except:
            # JS fallback using known ID
            result = page.evaluate("""
                () => {
                    const btn = document.getElementById('saveAndPublishButton') || document.querySelector('input[value*="Save"]');
                    if (btn) { btn.click(); return btn.value; }
                    return null;
                }
            """)
            if result:
                print(f"Clicked via JS: '{result}'")
            else:
                print("WARNING: No save/publish input found!")
        time.sleep(8)
        
        # Attempt to capture the Ko-fi product link/id
        kofi_id = None
        current_url = page.url
        print(f"Current page URL after publish: {current_url}")
        
        if "/s/" in current_url:
            kofi_id = current_url.split("/s/")[-1].split("?")[0]
        else:
            # Look for success modal inputs or links containing ko-fi.com/s/
            try:
                # Direct link in text inputs/anchors
                link_el = page.locator("a[href*='/s/'], input[value*='/s/']").first
                if link_el.count() > 0:
                    val = link_el.get_attribute("href") or link_el.get_attribute("value")
                    if val and "/s/" in val:
                        kofi_id = val.split("/s/")[-1].split("?")[0]
            except Exception as e:
                print(f"Failed to find Ko-fi ID from DOM: {e}")
                
        if kofi_id:
            print(f"Successfully extracted published Ko-fi ID: {kofi_id}")
            add_song_to_website(song_name, price, kofi_id, format_mode="full_arrangement" if is_full else "viral_part")
        else:
            print("WARNING: Could not auto-detect Ko-fi product ID from published page. You may need to add it manually to songs.ts.")
            
        print("Done!")
        browser.close()

# --- run_metricool() removed ---
# The old Metricool browser automation (~1000 lines) was removed during the cleanup.
# All uploads now use native platform APIs: yt_poster.py, ig_poster.py, fb_poster.py, tiktok_poster.py.

def run_tiktok(song_name, author_name, schedule_dt=None, profile="normal"):
    import datetime
    import sys
    
    # Import the new Direct Post API module
    tools_dir = os.path.dirname(os.path.abspath(__file__))
    workspace = os.path.dirname(tools_dir)
    sys.path.insert(0, tools_dir)
    from meloscribe.backend import tiktok_poster
    
    print(f"🚀 Initializing TikTok API Upload module for '{song_name}'...")
    print(f"Profile: {profile.upper()}")
    
    settings = get_settings()
    tiktok_dir = settings.get("tiktok_dir", r"C:\Dev\meloscribe\TikToks")
    
    is_easy = song_name.lower().endswith(" easy")
    is_teaser = song_name.lower().endswith(" teaser") or profile == "hook"
    is_tut = profile == "tutorial" or "tutorial" in profile.lower()
    
    base_song = song_name
    if base_song.lower().endswith(" easy"):
        base_song = base_song[:-5].strip()
    elif base_song.lower().endswith(" teaser"):
        base_song = base_song[:-7].strip()
        
    label_parts = []
    if is_easy:
        label_parts.append(" Easy")
    if is_tut:
        label_parts.append(" Tutorial")
    elif is_teaser:
        label_parts.append(" Teaser")
    label = "".join(label_parts)
    
    prefix = ""
    if is_tut:
        prefix = " slow"
    elif is_teaser and not song_name.lower().endswith(" teaser"):
        prefix = " teaser"

    video_path = os.path.join(tiktok_dir, f"{song_name}{prefix}.mp4")
    
    if not os.path.exists(video_path):
        print(f"❌ Error: Video not found at {video_path}")
        sys.exit(1)

    # 2. Build Caption Text
    tiktok_tpl = settings.get("desc_template_tiktok") or (
        "🎹 {song} - {author}{label}\n\n"
        "Enjoy this piano arrangement! Whether you're here to listen or want to learn this piece yourself - I've got you covered.\n\n"
        "Sheet Music (PDF) & free Videos -> Link in Bio\n\n"
        "Check out my profile for more aesthetic piano covers and tutorials!\n\n"
        "#piano #pianocover #pianotutorial #music #synthesia #cover"
    )
    if is_tut:
        tiktok_tpl = tiktok_tpl.replace("Enjoy this piano arrangement", "Enjoy this piano tutorial")
    full_title = format_description_template(tiktok_tpl, song_name, author_name, label)
    
    # Resolve cover image path with full fallbacks (including hook.jpg for Teasers)
    covers_dir = settings.get("covers_dir")
    clean_base = song_name.replace(" Teaser", "").replace(" Easy", "").strip()
    image_path = None
    if covers_dir:
        candidates = [
            f"{song_name}{prefix}.jpg",
            f"{clean_base} hook.jpg",
            f"{clean_base} hook_wide.jpg",
            f"{clean_base}_clean.jpg",
            f"{clean_base}.jpg",
            f"{song_name}.jpg"
        ]
        for cand in candidates:
            cand_path = os.path.join(covers_dir, cand)
            cand_path = resolve_case_insensitive_path(cand_path)
            if cand_path and os.path.exists(cand_path):
                image_path = cand_path
                break

    # Send description and cover image to user's phone via ntfy
    print("Sending caption, hashtags, and cover image to ntfy...")
    send_ntfy_notification(
        title=f"TikTok: {song_name}",
        body=full_title,
        image_path=image_path
    )
    
    # 3. Upload via official API
    print("Uploading natively via TikTok Content Posting API (Private / SELF_ONLY)...")
    success = tiktok_poster.post_video(video_path, full_title, privacy="SELF_ONLY")
    
    if success:
        print("✅ TikTok Upload successful via API! (Video is private, you can publish it manually on your phone)")
    else:
        print("❌ TikTok API Upload failed.")
        sys.exit(1)

def upload_to_r2(song_name, format_mode="full_arrangement", hook_start=None, hook_end=None, force=False, subtitle_hook=None):
    import subprocess
    settings = get_settings()
    r2_account_id = settings.get("r2_account_id")
    r2_access_key = settings.get("r2_access_key") or settings.get("r2_access_key_id")
    r2_secret_key = settings.get("r2_secret_key") or settings.get("r2_secret_access_key")
    r2_bucket     = settings.get("r2_bucket") or settings.get("r2_bucket_name", "meloscribe-sheets")

    if not r2_account_id or not r2_access_key or not r2_secret_key:
        print("[R2 Upload] ERROR: R2 credentials missing in settings.json!")
        return False

    keysight_dir  = settings.get("keysight_dir",  r"C:\Dev\meloscribe\Keysight export")
    musescore_dir = settings.get("musescore_dir",  r"C:\Dev\meloscribe\Scores")
    cakewalk_dir  = os.path.join(settings.get("cakewalk_dir", r"C:\Cakewalk Projects"), song_name)
    audio_preview_dir = r"C:\Dev\meloscribe-frontend\website\public\audio-previews"

    # Generate preview clip + audio MP3 FIRST so they exist when we upload them
    print(f"[R2 Upload] Generating preview assets before upload...")
    try:
        generate_previews(song_name, format_mode, hook_start=hook_start, hook_end=hook_end, subtitle_hook=subtitle_hook)
    except Exception as e:
        print(f"[R2 Upload] Warning: preview generation error: {e}")

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

        def upload_file(local_path, key, content_type="application/octet-stream"):
            if not os.path.exists(local_path):
                print(f"[R2 Upload] Skipping (not found): {os.path.basename(local_path)}")
                return False
            local_size = os.path.getsize(local_path)
            size_mb = local_size / (1024 * 1024)
            print(f"[R2 Upload] Uploading {key} ({size_mb:.1f} MB)...")
            s3.upload_file(
                Filename=local_path, Bucket=r2_bucket, Key=key,
                ExtraArgs={"ContentType": content_type}
            )
            print(f"[R2 Upload] OK: {key}")
            return True

        prefix = song_name

        # PDF sheet music with automated /Title metadata correction
        pdf_path = os.path.join(musescore_dir, f"{song_name}.pdf")
        if os.path.exists(pdf_path):
            import tempfile
            temp_pdf_path = os.path.join(tempfile.gettempdir(), f"temp_{song_name}.pdf")
            try:
                from pypdf import PdfReader, PdfWriter
                reader = PdfReader(pdf_path)
                writer = PdfWriter()
                for page in reader.pages:
                    writer.add_page(page)
                existing_meta = reader.metadata or {}
                writer.add_metadata({
                    "/Title": song_name,
                    "/Author": existing_meta.get("/Author", "Meloscribe"),
                    "/Creator": existing_meta.get("/Creator", "Meloscribe"),
                    "/Producer": existing_meta.get("/Producer", "Meloscribe"),
                })
                with open(temp_pdf_path, "wb") as f_out:
                    writer.write(f_out)
                print(f"[R2 Upload] PDF metadata Title set to '{song_name}' successfully.")
                upload_file(temp_pdf_path, f"{prefix}/{song_name}.pdf", "application/pdf")
                try: os.remove(temp_pdf_path)
                except: pass
            except Exception as pdf_err:
                print(f"[R2 Upload] WARNING: Failed to adjust PDF metadata: {pdf_err}. Uploading original PDF.")
                upload_file(pdf_path, f"{prefix}/{song_name}.pdf", "application/pdf")
        else:
            print(f"[R2 Upload] PDF not found for upload: {pdf_path}")

        # MIDI files
        for mid_name in [f"{song_name}.mid", f"{song_name} slow.mid"]:
            upload_file(os.path.join(cakewalk_dir, mid_name),
                        f"{prefix}/{mid_name}", "audio/midi")

        # Load author from database or fallback for title card
        author = None
        try:
            import sqlite3
            db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "meloscribe", "backend", "analytics.db")
            if os.path.exists(db_path):
                conn = sqlite3.connect(db_path)
                row = conn.execute("SELECT author FROM batch_ingest_queue WHERE song_name=?", (song_name,)).fetchone()
                if not row:
                    row = conn.execute("SELECT author FROM tracks WHERE song_name=?", (song_name,)).fetchone()
                conn.close()
                if row:
                    author = row[0]
        except Exception:
            pass
        if not author:
            author = "Traditional"
            if 'args' in globals() and globals()['args'] is not None and hasattr(globals()['args'], 'author') and globals()['args'].author:
                author = globals()['args'].author

        # Keysight tutorial videos (compressed + watermarked on upload)
        temp_wm_paths = {}
        for vid_name in [f"{song_name}.mp4", f"{song_name} slow.mp4"]:
            local_vid_path = os.path.join(keysight_dir, vid_name)
            if os.path.exists(local_vid_path):
                # Check if uncompressed RAW source exists to avoid double compression
                base_name = os.path.splitext(vid_name)[0]
                raw_path = os.path.join(keysight_dir, "RAW", f"{base_name}_RAW.mp4")
                input_source = raw_path if os.path.exists(raw_path) else local_vid_path
                
                if input_source == raw_path:
                    print(f"[Watermark Local] Found RAW source for {vid_name}. Compressing directly from RAW to avoid double compression...")
                else:
                    print(f"[Watermark Local] No RAW source found for {vid_name}. Falling back to pre-compressed H265 as input...")

                # Create a temporary watermarked version of the video locally for upload
                temp_watermarked_path = os.path.join(keysight_dir, f"temp_wm_{vid_name}")
                print(f"[Watermark Local] Embedding title card & 'meloscribe.dev' watermark into {vid_name}...")
                
                # Get dimensions
                width, height = get_video_dimensions(input_source, ffmpeg_exe="ffmpeg")
                title_size = int(width * 0.05)
                artist_size = int(width * 0.024)
                
                # Resolve fonts
                tools_dir = Path(__file__).resolve().parent
                font_title = tools_dir / "fonts" / "arno_pro.ttf"
                font_artist = tools_dir / "fonts" / "montserrat.ttf"
                
                font_title_esc = escape_path_for_ffmpeg(str(font_title))
                font_artist_esc = escape_path_for_ffmpeg(str(font_artist))
                
                # Write text files to avoid ffmpeg escaping issues
                import tempfile
                import uuid
                uid = uuid.uuid4().hex[:8]
                temp_dir = tempfile.gettempdir()
                
                title_txt = os.path.join(temp_dir, f"_title_{uid}.txt")
                artist_txt = os.path.join(temp_dir, f"_artist_{uid}.txt")
                
                with open(title_txt, "w", encoding="utf-8") as f:
                    f.write(song_name)
                    
                is_slow = "slow" in vid_name.lower()
                subtitle_text = "Slowed Down Tutorial" if is_slow else author
                with open(artist_txt, "w", encoding="utf-8") as f:
                    f.write(subtitle_text)
                    
                title_txt_esc = escape_path_for_ffmpeg(title_txt)
                artist_txt_esc = escape_path_for_ffmpeg(artist_txt)
                
                # Video already contains metronome audio from Keysight recording — skip generating secondary click track to prevent double metronome audio
                metro_wav = None

                # Filter string with both title/subtitle fade-in/fade-out AND corner watermark
                filter_str = (
                    f"drawtext=fontfile='{font_title_esc}':textfile='{title_txt_esc}':fontcolor=white:fontsize={title_size}"
                    f":x=(w-text_w)/2:y=(h/2)-{int(height*0.06)}:shadowcolor=black@0.6:shadowx=4:shadowy=4"
                    f":alpha='if(lt(t,1),t,if(lt(t,3.5),1,if(lt(t,4.5),4.5-t,0)))',"
                    
                    f"drawtext=fontfile='{font_artist_esc}':textfile='{artist_txt_esc}':fontcolor=white:fontsize={artist_size}"
                    f":x=(w-text_w)/2:y=(h/2)+{int(height*0.05)}:shadowcolor=black@0.6:shadowx=3:shadowy=3"
                    f":alpha='if(lt(t,1),t,if(lt(t,3.5),1,if(lt(t,4.5),4.5-t,0)))',"
                    
                    f"drawtext=text='meloscribe':fontfile='{font_artist_esc}':fontcolor=white@0.18:fontsize=32:x=w-tw-60:y=60"
                )
                
                if metro_wav and os.path.exists(metro_wav):
                    cmd = [
                        "ffmpeg", "-y",
                        "-i", input_source,
                        "-i", metro_wav,
                        "-filter_complex", f"[0:v]{filter_str}[v_out]; [0:a][1:a]amix=inputs=2:duration=first:weights=1 0.8:normalize=0[a_out]",
                        "-map", "[v_out]", "-map", "[a_out]",
                        "-c:v", "libx264", "-preset", "fast", "-crf", "26",
                        "-c:a", "aac", "-b:a", "192k",
                        "-movflags", "+faststart",
                        temp_watermarked_path
                    ]
                else:
                    cmd = [
                        "ffmpeg", "-y",
                        "-i", input_source,
                        "-vf", filter_str,
                        "-c:v", "libx264", "-preset", "fast", "-crf", "26",
                        "-c:a", "copy",
                        temp_watermarked_path
                    ]
                
                try:
                    subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
                    print(f"[Watermark Local] Successfully watermarked {vid_name} (CRF 26). Uploading watermarked version...")
                    upload_file(temp_watermarked_path, f"{prefix}/{vid_name}", "video/mp4")
                    temp_wm_paths[vid_name] = temp_watermarked_path
                except Exception as wm_err:
                    print(f"[Watermark Local] WARNING: Failed to watermark {vid_name} locally: {wm_err}. Uploading original.")
                    upload_file(local_vid_path, f"{prefix}/{vid_name}", "video/mp4")
                finally:
                    # Clean up temp files
                    try:
                        os.remove(title_txt)
                        os.remove(artist_txt)
                        if metro_wav and os.path.exists(metro_wav):
                            os.remove(metro_wav)
                    except Exception:
                        pass
            else:
                print(f"[R2 Upload] Video not found for upload: {vid_name}")

        # Audio hover preview MP3 (generated above)
        upload_file(os.path.join(audio_preview_dir, f"{song_name}.mp3"),
                    f"{prefix}/{song_name}.mp3", "audio/mpeg")

        # Preview video if it is full_arrangement and exists
        if format_mode == "full_arrangement":
            preview_vid_path = os.path.join(keysight_dir, f"{song_name}_preview.mp4")
            if os.path.exists(preview_vid_path):
                upload_file(preview_vid_path, f"{prefix}/{song_name}_preview.mp4", "video/mp4")


            


        print(f"[R2 Upload] All assets for '{song_name}' uploaded to R2 bucket '{r2_bucket}'.")
        # Clean up any temporary watermarked files
        for temp_p in temp_wm_paths.values():
            if os.path.exists(temp_p):
                try: os.remove(temp_p)
                except: pass
        return True

    except Exception as e:
        print(f"[R2 Upload] Failed: {e}")
        # Clean up any temporary watermarked files
        for temp_p in temp_wm_paths.values():
            if os.path.exists(temp_p):
                try: os.remove(temp_p)
                except: pass
        return False


if __name__ == "__main__":
    import argparse
    import os
    import sys
    
    settings = get_settings()
    
    parser = argparse.ArgumentParser(description="Multi-Platform Upload Automation")
    parser.add_argument("--song", required=True)
    parser.add_argument("--author", default="Dave Kerr")
    parser.add_argument("--price", default="4.00")
    parser.add_argument('--mode', choices=['kofi', 'tiktok', 'youtube', 'instagram', 'facebook', 'threads', 'website', 'r2', 'pinterest'], required=True, help='Which bot to run')
    parser.add_argument("--datetime", default=None, help="Schedule datetime as 'YYYY-MM-DD HH:MM'")
    parser.add_argument("--schedule_date", default=None, help="Schedule date as 'YYYY-MM-DD'")
    parser.add_argument("--schedule_time", default=None, help="Schedule time as 'HH:MM'")
    parser.add_argument("--pinterest_board_id", default=None, help="Pinterest board ID to override JSON configuration")
    parser.add_argument("--tiktok_token", default=None, help="TikTok OAuth Token override")
    parser.add_argument("--profile", default="normal", choices=["normal", "tutorial", "easy", "easy_tutorial", "hook"])
    parser.add_argument("--format", default=None, choices=["viral_part", "full_arrangement"], help="Format upload mode")
    parser.add_argument("--full", action="store_true", help="Use full learning package description for Ko-Fi")
    parser.add_argument("--condensed", action="store_true", help="Unified square format upload mode")
    parser.add_argument("--youtube_url", help="The URL of the freshly uploaded YouTube video to paste into Ko-Fi")
    parser.add_argument("--kofi_id", help="The Ko-Fi product ID/slug for website sync mode")
    parser.add_argument("--force", action="store_true", help="Force upload even if file size matches in R2")
    parser.add_argument("--no-visualizer-hook", action="store_true", help="Disable visualizer on hook/preview video")
    parser.add_argument("--metro_offset", type=float, default=0.0, help="Shift metronome clicks by this many beats (e.g. 0.5)")
    parser.add_argument("--hook_start", type=float, default=None, help="Hook start time in seconds")
    parser.add_argument("--hook_end", type=float, default=None, help="Hook end time in seconds")
    parser.add_argument("--subtitle_hook", type=str, default=None, help="Custom subtitle for Hook/Teaser video")
    
    args = parser.parse_args()
    
    # Resolve datetime from schedule_date and schedule_time if available
    if args.schedule_date:
        args.datetime = f"{args.schedule_date} {args.schedule_time or '16:00'}"
        
    # Resolve format_mode
    format_mode = args.format
    if format_mode is None:
        if args.condensed:
            format_mode = "full_arrangement"
        elif args.full:
            format_mode = "full_arrangement"
        else:
            format_mode = "full_arrangement"  # default
            
    if args.mode == "kofi":
        run_kofi(args.song, args.price, is_full=(format_mode == "full_arrangement"), yt_shorts_url=args.youtube_url)
    elif args.mode == "website":
        if not args.kofi_id:
            print("Error: --kofi_id is required for website mode.")
            sys.exit(1)
        success = add_song_to_website(
            args.song, args.price, args.kofi_id, format_mode,
            author=args.author, enable_visualizer_hook=not args.no_visualizer_hook
        )
        if not success:
            sys.exit(1)
    elif args.mode == "pinterest":
        success = run_pinterest(args.song, args.profile, getattr(args, "author", "Dave Kerr"), board_id=args.pinterest_board_id)
        if not success:
            sys.exit(1)
    elif args.mode == "r2":
        success = upload_to_r2(args.song, format_mode=format_mode, hook_start=args.hook_start, hook_end=args.hook_end, force=args.force, subtitle_hook=args.subtitle_hook)
        if not success:
            sys.exit(1)
    elif args.mode == "youtube":
        try:
            from meloscribe.backend.yt_poster import post_video
        except ImportError:
            sys.path.append(os.path.join(os.path.dirname(__file__), "meloscribe", "backend"))
            from yt_poster import post_video
            
        import datetime
        dt_obj = None
        if hasattr(args, 'datetime') and args.datetime:
            try:
                from zoneinfo import ZoneInfo
                dt_obj = datetime.datetime.strptime(args.datetime, "%Y-%m-%d %H:%M")
                dt_obj = dt_obj.replace(tzinfo=ZoneInfo("Europe/Berlin"))
            except Exception as e:
                print(f"Warning: Failed to parse datetime '{args.datetime}': {e}")
        
        is_easy = args.song.lower().endswith(" easy")
        is_teaser = args.song.lower().endswith(" teaser") or args.profile == "hook"
        is_tut = args.profile == "tutorial"
        
        base_song = args.song
        if base_song.lower().endswith(" easy"):
            base_song = base_song[:-5].strip()
        elif base_song.lower().endswith(" teaser"):
            base_song = base_song[:-7].strip()
            
        label_parts = []
        if is_easy:
            label_parts.append(" Easy")
        if is_tut:
            label_parts.append(" Tutorial")
        elif is_teaser:
            label_parts.append(" Teaser")
        label = "".join(label_parts)
        
        suffix = ""
        if is_tut:
            suffix = " slow"
        elif is_teaser and not args.song.lower().endswith(" teaser"):
            suffix = " teaser"

        
        # Check duration dynamically to decide between Short and Long-form
        portrait_path = os.path.join(settings.get("tiktok_dir", r"C:\Dev\meloscribe\TikToks"), f"{args.song}{suffix}.mp4")
        duration = get_duration_seconds(portrait_path)
        print(f"[YouTube Uploader] Checked video duration: {duration:.2f} seconds")
        is_short = duration <= 60.0
        
        if is_short:
            print("[YouTube Uploader] Duration <= 60s -> Uploading as Short (Portrait 9:16).")
            video_path = portrait_path
            format_mode = "viral_part"
        else:
            print("[YouTube Uploader] Duration > 60s -> Uploading as Long-form (Widescreen 16:9).")
            video_path = os.path.join(settings.get("tiktok_dir", r"C:\Dev\meloscribe\TikToks"), f"{args.song}{suffix}_wide.mp4")
            if not os.path.exists(video_path):
                print(f"[YouTube Uploader] Warning: Widescreen video not found at {video_path}, using portrait fallback.")
                video_path = portrait_path
            format_mode = "full_arrangement"
            
        thumbnail_path = os.path.join(settings.get("covers_dir", r"C:\Dev\meloscribe\Covers"), f"{args.song}{suffix}.jpg")
        thumbnail_path = resolve_case_insensitive_path(thumbnail_path)
        yt_tpl = settings.get("desc_template_youtube") or (
            "🎹 {song} - {author}{label}\n\n"
            "Enjoy this piano arrangement! Whether you're here to listen or want to learn this piece yourself - I've got you covered.\n\n"
            "Sheet Music (PDF) & MIDI files -> Link in Bio\n\n"
            "Check out my channel for more aesthetic piano covers and tutorials!\n\n"
            "#piano #pianocover #pianotutorial #music #synthesia #keysight #{song}"
        )
        if is_tut:
            yt_tpl = yt_tpl.replace("Enjoy this piano arrangement", "Enjoy this piano tutorial")
        desc = format_description_template(yt_tpl, args.song, args.author, label)
        tags = ["piano", "tutorial", "synthesia", "cover", base_song, args.author, "music", "piano cover"]
        yt_url = post_video(video_path, f"{base_song} - {args.author}{label} | Piano Cover", desc, tags,
                   publish_at_dt=dt_obj, privacy="public", format=format_mode,
                   thumbnail_path=thumbnail_path)
        if not yt_url:
            sys.exit(1)
    elif args.mode == "instagram":
        try:
            from meloscribe.backend.ig_poster import post_reel
        except ImportError:
            sys.path.append(os.path.join(os.path.dirname(__file__), "meloscribe", "backend"))
            from ig_poster import post_reel
            
        import datetime
        dt_obj = None
        if hasattr(args, 'datetime') and args.datetime:
            try:
                from zoneinfo import ZoneInfo
                dt_obj = datetime.datetime.strptime(args.datetime, "%Y-%m-%d %H:%M")
                dt_obj = dt_obj.replace(tzinfo=ZoneInfo("Europe/Berlin"))
            except Exception as e:
                print(f"Warning: Failed to parse datetime '{args.datetime}': {e}")
        
        is_easy = args.song.lower().endswith(" easy")
        is_teaser = args.song.lower().endswith(" teaser") or args.profile == "hook"
        is_tut = args.profile == "tutorial"
        
        base_song = args.song
        if base_song.lower().endswith(" easy"):
            base_song = base_song[:-5].strip()
        elif base_song.lower().endswith(" teaser"):
            base_song = base_song[:-7].strip()
            
        label_parts = []
        if is_easy:
            label_parts.append(" Easy")
        if is_tut:
            label_parts.append(" Tutorial")
        elif is_teaser:
            label_parts.append(" Teaser")
        label = "".join(label_parts)
        
        suffix = ""
        if is_tut:
            suffix = " slow"
        elif is_teaser and not args.song.lower().endswith(" teaser"):
            suffix = " teaser"

        video_path = os.path.join(settings.get("tiktok_dir", r"C:\Dev\meloscribe\TikToks"), f"{args.song}{suffix}.mp4")

        ig_tpl = settings.get("desc_template_instagram") or (
            "🎹 {song} - {author}{label}\n\n"
            "Sheet Music & MIDI -> Link in Bio (Ko-Fi)\n\n"
            "#piano #pianocover #pianotutorial #synthesia #music #pianomusic #{song}"
        )
        if is_tut:
            ig_tpl = ig_tpl.replace("Enjoy this piano arrangement", "Enjoy this piano tutorial")
        caption = format_description_template(ig_tpl, args.song, args.author, label)
        success = post_reel(video_path, caption, publish_at_dt=dt_obj)
        if not success:
            sys.exit(1)
    elif args.mode == "facebook":
        try:
            from meloscribe.backend.fb_poster import post_video
        except ImportError:
            sys.path.append(os.path.join(os.path.dirname(__file__), "meloscribe", "backend"))
            from fb_poster import post_video
            
        import datetime
        dt_obj = None
        if hasattr(args, 'datetime') and args.datetime:
            try:
                from zoneinfo import ZoneInfo
                dt_obj = datetime.datetime.strptime(args.datetime, "%Y-%m-%d %H:%M")
                dt_obj = dt_obj.replace(tzinfo=ZoneInfo("Europe/Berlin"))
            except Exception as e:
                print(f"Warning: Failed to parse datetime '{args.datetime}': {e}")
        
        is_easy = ("easy" in args.song.lower()) or (hasattr(args, 'profile') and args.profile == "easy")
        is_teaser = args.song.lower().endswith(" teaser") or args.profile == "hook"
        is_tut = args.profile == "tutorial"
        
        base_song = args.song
        base_song = re.sub(r'(?i)\b(easy|teaser|tutorial)\b', '', base_song).strip()
        base_song = re.sub(r'\s+', ' ', base_song).strip()
            
        label_parts = []
        if is_easy:
            label_parts.append(" Easy")
        if is_tut:
            label_parts.append(" Tutorial")
        elif is_teaser:
            label_parts.append(" Teaser")
        label = "".join(label_parts)
        
        suffix = ""
        if is_tut:
            suffix = " slow"
        elif is_teaser and not args.song.lower().endswith(" teaser"):
            suffix = " teaser"

        
        # Check duration dynamically to decide between Reel and Video (Reels support up to 90s)
        portrait_path = os.path.join(settings.get("tiktok_dir", r"C:\Dev\meloscribe\TikToks"), f"{args.song}{suffix}.mp4")
        duration = get_duration_seconds(portrait_path)
        print(f"[Facebook Uploader] Checked video duration: {duration:.2f} seconds")
        
        is_short = duration <= 90.0
        
        if is_short:
            print("[Facebook Uploader] Duration <= 90s -> Uploading as Reel (Portrait 9:16).")
            video_path = portrait_path
            format_mode = "viral_part"
        else:
            print("[Facebook Uploader] Duration > 60s -> Uploading as Video (Widescreen 16:9).")
            video_path = os.path.join(settings.get("tiktok_dir", r"C:\Dev\meloscribe\TikToks"), f"{args.song}{suffix}_wide.mp4")
            if not os.path.exists(video_path):
                print(f"[Facebook Uploader] Warning: Widescreen video not found at {video_path}, using portrait fallback.")
                video_path = portrait_path
            format_mode = "full_arrangement"
            
        if format_mode == "full_arrangement":
            wide_thumb = os.path.join(settings.get("covers_dir", r"C:\Dev\meloscribe\Covers"), f"{args.song}{suffix}_wide.jpg")
            wide_thumb = resolve_case_insensitive_path(wide_thumb)
            if os.path.exists(wide_thumb):
                thumbnail_path = wide_thumb
            else:
                thumbnail_path = resolve_case_insensitive_path(os.path.join(settings.get("covers_dir", r"C:\Dev\meloscribe\Covers"), f"{args.song}{suffix}.jpg"))
        else:
            thumbnail_path = os.path.join(settings.get("covers_dir", r"C:\Dev\meloscribe\Covers"), f"{args.song}{suffix}.jpg")
            thumbnail_path = resolve_case_insensitive_path(thumbnail_path)
        
        fb_tpl = settings.get("desc_template_facebook") or (
            "🎹 {song} - {author}{label}\n\n"
            "Sheet Music (PDF) & free Videos → Link in Bio\n\n"
            "#music #song #piano #cover #cozy #learnpiano #pop #pianotutorial #{song}"
        )
        if is_tut:
            fb_tpl = fb_tpl.replace("Enjoy this piano arrangement", "Enjoy this piano tutorial")
        desc = format_description_template(fb_tpl, args.song, args.author, label, medium_arg='reel' if format_mode == "viral_part" else 'video')
        
        # Build direct sheet music link & comment text
        slug = re.sub(r'[^a-z0-9]+', '-', base_song.lower()).strip('-')
        version_param = "easy" if is_easy else "original"
        song_link = f"https://meloscribe.dev/sheets?song={slug}&version={version_param}"
        default_fb_comment = f"Sheet Music (Easy): {song_link}" if is_easy else f"Sheet Music: {song_link}"
        fb_comment_tpl = settings.get("comment_template_facebook")
        fb_comment = fb_comment_tpl.replace("{song_link}", song_link) if fb_comment_tpl else default_fb_comment

        success = post_video(video_path, f"{base_song} - {args.author}{label}", desc,
                   format=format_mode, thumbnail_path=thumbnail_path,
                   publish_at_dt=dt_obj, comment_text=fb_comment)
        if not success:
            sys.exit(1)
    elif args.mode == "tiktok":
        run_tiktok(args.song, args.author, schedule_dt=getattr(args, 'datetime', None), profile=args.profile)
    elif args.mode == "threads":
        try:
            from meloscribe.backend.threads_poster import post_video as threads_post
        except ImportError:
            sys.path.append(os.path.join(os.path.dirname(__file__), "meloscribe", "backend"))
            from threads_poster import post_video as threads_post
        
        is_easy = ("easy" in args.song.lower()) or (hasattr(args, 'profile') and args.profile == "easy")
        is_teaser = args.song.lower().endswith(" teaser") or args.profile == "hook"
        is_tut = args.profile == "tutorial"
        
        base_song = args.song
        base_song = re.sub(r'(?i)\b(easy|teaser|tutorial)\b', '', base_song).strip()
        base_song = re.sub(r'\s+', ' ', base_song).strip()
            
        label_parts = []
        if is_easy:
            label_parts.append(" Easy")
        if is_tut:
            label_parts.append(" Tutorial")
        elif is_teaser:
            label_parts.append(" Teaser")
        label = "".join(label_parts)
        
        suffix = ""
        if is_tut:
            suffix = " slow"
        elif is_teaser and not args.song.lower().endswith(" teaser"):
            suffix = " teaser"

        video_path = os.path.join(settings.get("tiktok_dir", r"C:\Dev\meloscribe\TikToks"), f"{args.song}{suffix}.mp4")

        th_tpl = settings.get("desc_template_threads") or (
            "🎹 {song} - {author}{label}\n\n"
            "Sheet Music & MIDI -> Ko-Fi (link in bio)\n\n"
            "#piano #pianocover #pianotutorial #synthesia #music #{song}"
        )
        if is_tut:
            th_tpl = th_tpl.replace("Enjoy this piano arrangement", "Enjoy this piano tutorial")
        caption = format_description_template(th_tpl, args.song, args.author, label)
        
        # Build direct sheet music link & comment text
        slug = re.sub(r'[^a-z0-9]+', '-', base_song.lower()).strip('-')
        version_param = "easy" if is_easy else "original"
        song_link = f"https://meloscribe.dev/sheets?song={slug}&version={version_param}"
        default_th_comment = f"Sheet Music (Easy): {song_link}" if is_easy else f"Sheet Music: {song_link}"
        th_comment_tpl = settings.get("comment_template_threads")
        th_comment = th_comment_tpl.replace("{song_link}", song_link) if th_comment_tpl else default_th_comment

        success = threads_post(video_path, caption, comment_text=th_comment)
        if not success:
            sys.exit(1)
