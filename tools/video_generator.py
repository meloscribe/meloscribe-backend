import os
import sys
import re
import json
import time
import subprocess
import argparse

sys.stdout.reconfigure(encoding='utf-8')

CREATE_NO_WINDOW = 0x08000000

tools_dir = os.path.dirname(os.path.abspath(__file__))
local_ffmpeg_bin = os.path.join(tools_dir, "ffmpeg", "bin")
if os.path.exists(local_ffmpeg_bin) and local_ffmpeg_bin not in os.environ.get("PATH", ""):
    os.environ["PATH"] = local_ffmpeg_bin + os.pathsep + os.environ.get("PATH", "")

def get_ffprobe_exe():
    if os.path.exists(os.path.join(local_ffmpeg_bin, "ffprobe.exe")):
        return os.path.join(local_ffmpeg_bin, "ffprobe.exe")
    return "ffprobe"

def get_ffmpeg_exe():
    if os.path.exists(os.path.join(local_ffmpeg_bin, "ffmpeg.exe")):
        return os.path.join(local_ffmpeg_bin, "ffmpeg.exe")
    return "ffmpeg"

def escape_path_for_ffmpeg(p):
    return p.replace("\\", "/").replace(":", "\\:")

def split_title_lines(raw_title, font_path=None, max_width=1080):
    """
    Intelligently splits a song title into 1 or 2 lines.
    - Preserves explicit newlines ('\\n' or '\n').
    - If font_path exists and PIL is available, measures rendered pixel width.
    - If width > max_width, finds the optimal word split that balances the two lines visually.
    - If PIL unavailable, falls back to length heuristic.
    Returns (line1, line2). If 1 line, line2 is empty string.
    """
    title = raw_title.replace('\\n', '\n')
    if '\n' in title:
        parts = [p.strip() for p in title.split('\n', 1)]
        return parts[0], parts[1]

    words = title.split()
    if len(words) <= 1:
        return title.strip(), ""

    measured = False
    if font_path and os.path.exists(font_path):
        try:
            from PIL import ImageFont
            font = ImageFont.truetype(font_path, 130)
            def get_w(text):
                bbox = font.getbbox(text)
                return bbox[2] - bbox[0]

            full_w = get_w(title)
            if full_w <= max_width:
                return title.strip(), ""

            best_split = len(words) // 2
            best_diff = float('inf')
            for i in range(1, len(words)):
                l1 = ' '.join(words[:i])
                l2 = ' '.join(words[i:])
                w1 = get_w(l1)
                w2 = get_w(l2)
                diff = abs(w1 - w2)
                if max(w1, w2) < 1250 and diff < best_diff:
                    best_diff = diff
                    best_split = i
            measured = True
            return ' '.join(words[:best_split]), ' '.join(words[best_split:])
        except Exception:
            pass

    if not measured:
        if len(title) > 22:
            mid = len(words) // 2
            return ' '.join(words[:mid]), ' '.join(words[mid:])
        return title.strip(), ""

def get_video_duration(video_path):
    cmd = [get_ffprobe_exe(), '-v', 'error', '-show_entries', 'format=duration',
           '-of', 'default=noprint_wrappers=1:nokey=1', video_path]
    result = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8',
                            errors='replace', creationflags=CREATE_NO_WINDOW)
    return float(result.stdout.strip())

def get_video_dimensions(video_path):
    try:
        cmd = [get_ffprobe_exe(), '-v', 'error', '-select_streams', 'v:0',
               '-show_entries', 'stream=width,height',
               '-of', 'json', video_path]
        result = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8',
                                errors='replace', creationflags=CREATE_NO_WINDOW)
        data = json.loads(result.stdout)
        return int(data['streams'][0]['width']), int(data['streams'][0]['height'])
    except Exception:
        return 2560, 1440

def time_to_seconds(time_str):
    try:
        parts = time_str.strip().split(':')
        h, m = int(parts[0]), int(parts[1])
        s = float(parts[2])
        return h * 3600 + m * 60 + s
    except Exception:
        return 0.0

def get_audio_delay(video_path):
    import subprocess, numpy as np, os
    from scipy.io import wavfile
    tools_dir = os.path.dirname(os.path.abspath(__file__))
    temp_dir = os.path.join(tools_dir, "temp")
    os.makedirs(temp_dir, exist_ok=True)
    temp_wav = os.path.join(temp_dir, "temp_offset.wav")
    subprocess.run(['ffmpeg', '-y', '-i', video_path, '-t', '10', '-vn', '-acodec', 'pcm_s16le', '-ar', '44100', '-ac', '1', temp_wav], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
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

def main():
    parser = argparse.ArgumentParser(description='Antigravity Music Video Generator')
    parser.add_argument('--video', required=True, help='Path zum rohen Keysight MP4 Video')
    parser.add_argument('--title', required=True, help='Song Titel')
    parser.add_argument('--author', required=True, help='Autor (ohne Bindestriche, die fuegt das Skript hinzu)')
    parser.add_argument('--type', choices=['normal', 'tutorial'], default='normal', help='Normal oder Tutorial Variante')
    parser.add_argument('--zoom', type=float, default=1.0, help='Zoomfaktor (z.B. 1.3 fuer naeher ran)')
    parser.add_argument('--shift', type=int, default=0, help='Verschiebung in Pixeln (negativ = links, positiv = rechts)')
    parser.add_argument('--wide', action='store_true', help='16:9 Widescreen version (no crop, no watermark, 3s text fadeout)')
    parser.add_argument('--midipath', type=str, default="", help="Path to original MIDI file to parse dynamic tempo")
    parser.add_argument('--visualizer', action='store_true', help='Enable fluid audio visualizer')
    parser.add_argument('--metronome', action='store_true', help='Enable audio metronome (tutorial mode only)')
    parser.add_argument('--theme', type=str, default='warm', help='Color theme to pass downwards')
    parser.add_argument('--use_portrait_addon', action='store_true', help='Deprecated: piano concert stage reflection is now standard for all portrait videos')
    parser.add_argument('--timesig', type=str, default='auto', help='Time signature override (e.g. 3/4, 4/4). Default: auto-detect from MIDI')
    parser.add_argument('--force', action='store_true', help='Force overwrite existing output video')
    parser.add_argument('--metro_offset', type=float, default=0.0, help='Shift metronome clicks by this many beats (e.g. 0.5)')
    parser.add_argument('--has_easy', action='store_true', help='Specify if an easy version of this song exists')
    parser.add_argument('--subtitle', type=str, default=None, help='Custom subtitle text displayed below title in portrait video. If empty/unset, no subtitle is displayed.')
    parser.add_argument('--start_time', type=float, default=None, help='Optional clip start time in seconds')
    parser.add_argument('--end_time', type=float, default=None, help='Optional clip end time in seconds')
    parser.add_argument('--output_dir', type=str, default=None, help='Custom output directory for generated video')
    parser.add_argument('--time_offset', type=float, default=2.5, help='Lead-in offset in seconds between preview player and raw Keysight input')
    parser.add_argument('--outro_text', type=str, default=None, help='Custom outro/end text to display during final fadeout')
    parser.add_argument('--title_image', type=str, default=None, help='Path to custom PNG title image (replaces text title)')
    parser.add_argument('--cta_text', type=str, default=None, help='Custom CTA text override (defaults to "Want to play this? Sheets in bio →")')
    parser.add_argument('--no_reflection', action='store_true', help='Disable dynamic concert stage keyboard reflection')
    args = parser.parse_args()

    
    # Load settings from meloscribe backend
    import sys
    sys.path.append(os.path.join(os.path.dirname(__file__), "meloscribe", "backend"))
    from settings import load_settings
    settings = load_settings()
    
    tiktok_dir = settings.get("tiktok_dir", r"C:\Dev\meloscribe\TikToks")
    
    # Early Parameter Cache Check
    raw_name = os.path.splitext(os.path.basename(args.video))[0]
    clean_name = raw_name[:-4] if raw_name.lower().endswith("_raw") else raw_name
    out_base = args.title.strip() if (args.title and args.title.strip()) else clean_name
    if args.type == "tutorial" and not out_base.lower().endswith(" slow"):
        out_base = f"{out_base} slow"

    # Auto-resolve theme if not specified or defaulted to warm
    song_lookup_name = re.sub(r'(?i)\b(easy|teaser|slow|recycled(\s+\d+)?)\b', '', args.title or clean_name).strip()
    if not args.theme or args.theme.lower() == "warm":
        try:
            db_path = os.path.join(tools_dir, "meloscribe", "backend", "analytics.db")
            if os.path.exists(db_path):
                import sqlite3
                with sqlite3.connect(db_path, timeout=5.0) as conn:
                    c = conn.cursor()
                    c.execute("SELECT theme FROM tracks WHERE LOWER(song_name) = LOWER(?)", (song_lookup_name,))
                    row = c.fetchone()
                    if not row or not row[0]:
                        c.execute("SELECT theme FROM batch_ingest_queue WHERE LOWER(song_name) = LOWER(?)", (song_lookup_name,))
                        row = c.fetchone()
                    if row and row[0] and row[0].strip():
                        args.theme = row[0].strip().lower()
        except Exception:
            pass

    if args.theme:
        args.theme = args.theme.strip().lower()
        if args.theme == "purple":
            args.theme = "violet"
    else:
        args.theme = settings.get("theme", "warm").strip().lower()
        if args.theme == "purple":
            args.theme = "violet"

    output_name = f"{out_base}_wide.mp4" if args.wide else (f"{out_base}.mp4" if not out_base.lower().endswith(".mp4") else out_base)
    active_out_dir = args.output_dir if args.output_dir else tiktok_dir
    output_path = os.path.join(active_out_dir, output_name)
    cache_path = os.path.join(active_out_dir, ".render_cache.json")
    params_match = False
    current_params = {
        "zoom": args.zoom,
        "shift": args.shift,
        "theme": args.theme,
        "title": args.title,
        "author": args.author,
        "type": args.type,
        "use_portrait_addon": getattr(args, "use_portrait_addon", False),
        "has_easy": getattr(args, "has_easy", False),
        "visualizer": getattr(args, "visualizer", False)
    }

    if os.path.exists(output_path) and os.path.getsize(output_path) > 1000000 and not getattr(args, 'force', False):
        print(f"Output video already exists on disk ({os.path.getsize(output_path) / (1024*1024):.1f} MB): {output_path} — skipping rendering.")
        sys.exit(0)

    input_video_path = args.video
    base_dir = os.path.dirname(args.video)
    base_name_str = os.path.splitext(os.path.basename(args.video))[0]
    raw_path = os.path.join(base_dir, "RAW", f"{base_name_str}_RAW.mp4")
    if os.path.exists(raw_path):
        print(f"Using pristine RAW input video (preventing double compression): {raw_path}")
        input_video_path = raw_path
    elif not os.path.exists(input_video_path):
        print(f"ERROR: Video {args.video} nicht gefunden!")
        sys.exit(1)
        
    tools_dir = os.path.dirname(os.path.abspath(__file__))
    fonts_dir = os.path.join(tools_dir, 'fonts')
    os.makedirs(fonts_dir, exist_ok=True)
    
    arno_pro = os.path.join(fonts_dir, 'arno_pro.ttf')
    montserrat = os.path.join(fonts_dir, 'montserrat.ttf')
    
    if not os.path.exists(arno_pro) or not os.path.exists(montserrat):
        print(f"FEHLER: Schriftarten fehlen im Ordner: {fonts_dir}")
        sys.exit(1)
        
    duration = get_video_duration(input_video_path)
    
    print("Calculating Audio Pre-roll Delay...")
    audio_delay = get_audio_delay(input_video_path)
    print(f"Detected Audio Delay offset: {audio_delay:.3f}s")
    
    # --- AUTO-METADATA EXTRACTION ---
    detected_bpm = 0
    detected_timesig = "4/4"
    if args.midipath and os.path.exists(args.midipath):
        try:
            import mido
            mid = mido.MidiFile(args.midipath)
            for track in mid.tracks:
                for msg in track:
                    if msg.type == 'set_tempo' and detected_bpm == 0:
                        detected_bpm = int(round(60_000_000.0 / msg.tempo))
                    if msg.type == 'time_signature':
                        detected_timesig = f"{msg.numerator}/{msg.denominator}"
        except Exception as e:
            print(f"Warning: Failed to extract base metadata from MIDI: {e}")
            
    if args.timesig and args.timesig != 'auto':
        detected_timesig = args.timesig
    
    import uuid
    uid = uuid.uuid4().hex[:8]
    temp_dir = os.path.join(tools_dir, "temp")
    os.makedirs(temp_dir, exist_ok=True)
    try:
        now_ts = time.time()
        for f in os.listdir(temp_dir):
            if f.startswith(("waves_mask_", "metronome_", "title_", "author_", "outro", "sheets_", "morph_", "watermark_")) or f.endswith(".txt"):
                fp = os.path.join(temp_dir, f)
                if os.path.isfile(fp) and (now_ts - os.path.getmtime(fp)) > 900:
                    try: os.remove(fp)
                    except: pass
    except: pass

    mask_path = os.path.join(temp_dir, f"waves_mask_{uid}.mp4")
    metronome_path = os.path.join(temp_dir, f"metronome_{uid}.wav")
    
    title_txt = os.path.join(temp_dir, f"title_{uid}.txt")
    title1_txt = os.path.join(temp_dir, f"title1_{uid}.txt")
    title2_txt = os.path.join(temp_dir, f"title2_{uid}.txt")
    author_txt = os.path.join(temp_dir, f"author_{uid}.txt")
    outro1_txt = os.path.join(temp_dir, f"outro1_{uid}.txt")
    outro2_txt = os.path.join(temp_dir, f"outro2_{uid}.txt")
    sheets_txt = os.path.join(temp_dir, f"sheets_{uid}.txt")
    morph_txt = os.path.join(temp_dir, f"morph_{uid}.txt")
    watermark_txt = os.path.join(temp_dir, f"watermark_{uid}.txt")
    
    if args.visualizer:
        print("Pre-rendering Audio Visualizer Mask...", flush=True)
        py_exe = sys.executable
        venv_py = os.path.join(tools_dir, "meloscribe", "backend", ".venv", "Scripts", "python.exe")
        if os.path.exists(venv_py):
            py_exe = venv_py
        vis_cmd = [
            py_exe, "-u", os.path.join(tools_dir, "audio_visualizer.py"),
            "--video", args.video, "--type", args.type, "--theme", args.theme,
            "--delay", str(audio_delay), "--outmask", mask_path
        ]
        if args.midipath:
            vis_cmd.extend(["--midipath", args.midipath])
        if args.wide: vis_cmd.append("--wide")
        # Pass zoom/shift so the visualizer can constrain tutorial peaks to visible bands
        vis_cmd.extend(["--zoom", str(args.zoom), "--shift", str(args.shift)])
        # Capture and forward output manually to bypass any pipe buffering issues
        v_proc = subprocess.Popen(vis_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace')
        while True:
            line = v_proc.stdout.readline()
            if not line:
                if v_proc.poll() is not None:
                    break
                time.sleep(0.05)
                continue
            print(line, end='', flush=True)
        v_proc.wait()
        if v_proc.returncode is not None and v_proc.returncode != 0:
            print(f"Visualizer generation failed with exit code {v_proc.returncode}.")
            sys.exit(1)
    use_custom_metronome = False
    if args.metronome and args.type == 'tutorial':
        if args.midipath and os.path.exists(args.midipath):
            try:
                import mido
                import numpy as np
                from scipy.io import wavfile
                print("Generating dynamic MIDI metronome track...")
                mid = mido.MidiFile(args.midipath)
                ticks_per_beat = mid.ticks_per_beat
                
                # --- Detect time signature from MIDI or CLI override ---
                beats_per_measure = 4  # default 4/4
                denominator = 4
                if args.timesig and args.timesig != 'auto':
                    try:
                        num = int(args.timesig.split('/')[0])
                        den = int(args.timesig.split('/')[1])
                        beats_per_measure = num
                        denominator = den
                        print(f"  Time signature override: {args.timesig} ({num} beats/measure, denominator {den})")
                    except: pass
                else:
                    # Auto-detect from MIDI time_signature events
                    for track in mid.tracks:
                        for msg in track:
                            if msg.type == 'time_signature':
                                beats_per_measure = msg.numerator
                                denominator = msg.denominator
                                print(f"  Auto-detected time signature: {msg.numerator}/{msg.denominator}")
                                break
                        else: continue
                        break
                
                # Determine beat step in ticks
                beat_step = ticks_per_beat
                if denominator == 8:
                    beat_step = ticks_per_beat / 2
                elif denominator == 16:
                    beat_step = ticks_per_beat / 4
                
                # --- Parse beat times (tempo-aware for ritardando) ---
                beat_times = []
                current_tick, current_tempo, tiempo_sec, next_beat_tick = 0, 500000, 0.0, 0
                metro_offset = 0.0
                if hasattr(args, "metro_offset") and args.metro_offset:
                    metro_offset = args.metro_offset
                elif args.midipath and any(s in args.midipath.lower() for s in ["god rest ye merry", "3 fragezeichen", "fragezeichen"]):
                    metro_offset = 0.5
                next_beat_tick = int(ticks_per_beat * metro_offset)
                for msg in mid.merged_track:
                    delta_sec = mido.tick2second(msg.time, ticks_per_beat, current_tempo)
                    while next_beat_tick <= current_tick + msg.time:
                        if next_beat_tick == 0: beat_times.append(tiempo_sec)
                        else:
                            beat_sec = tiempo_sec + mido.tick2second(next_beat_tick - current_tick, ticks_per_beat, current_tempo)
                            beat_times.append(beat_sec)
                        next_beat_tick += beat_step
                    tiempo_sec += delta_sec
                    current_tick += msg.time
                    if msg.type == 'set_tempo': current_tempo = msg.tempo
                
                # Shift all beat times by the detected audio start delay
                beat_times = [t + audio_delay for t in beat_times]
                
                # --- Render metronome audio (elegant warm woodblock/side-stick click, all beats identical) ---
                sample_rate = 44100
                total_dur = beat_times[-1] + 2.0 if beat_times else duration
                audio = np.zeros(int(total_dur * sample_rate), dtype=np.float32)
                
                dur_beat = 0.06
                t_beat = np.linspace(0, dur_beat, int(dur_beat * sample_rate), endpoint=False)
                # Primary body frequency around 1100 Hz, second harmonic at 2200 Hz for clarity
                beep = (np.sin(2 * np.pi * 1100 * t_beat) * 0.7 + np.sin(2 * np.pi * 2200 * t_beat) * 0.3)
                beep *= np.exp(-180 * t_beat)
                # Stick impact texture noise
                noise = np.random.normal(0, 0.15, len(t_beat)) * np.exp(-350 * t_beat)
                beep += noise
                beep = beep * 1.2
                
                for beat_idx, t in enumerate(beat_times):
                    idx = int(t * sample_rate)
                    end_idx = idx + len(beep)
                    if end_idx <= len(audio): audio[idx:end_idx] += beep
                
                wavfile.write(metronome_path, sample_rate, audio)
                use_custom_metronome = True
                print(f"  Metronome: {len(beat_times)} beats, {beats_per_measure} per measure")
            except Exception as e:
                print(f"Warning: MIDI parsing failed for metronome: {e}")
                
    extra_inputs = []
    current_input_idx = 1
    mask_idx = -1
    metronome_idx = -1

    # ==================== WIDESCREEN MODE ====================
    if args.wide:
        # Full duration - no trimming at start/end
        new_duration = duration
        fade_start = new_duration - 1.5
        
        title_text = re.sub(r'(?i)\b(easy|slow)\b', '', args.title).strip()
        title_text = re.sub(r'\s+', ' ', title_text)
        
        if args.subtitle and args.subtitle.strip(" -"):
            author_text = args.subtitle.strip(" -")
        else:
            is_easy_vid = "easy" in args.title.lower() or "easy" in os.path.basename(args.video).lower()
            if is_easy_vid:
                author_text = "Easy Version" if args.type == "normal" else "Easy Tutorial"
            elif args.type != 'normal':
                author_text = "Slow Tutorial"
            else:
                author_text = args.author.strip(" -")
            
        outro_start = fade_start
        if args.type == 'normal':
            outro1_text = "Don't miss the tutorial"
            outro2_text = "Follow me <3"
        else:
            outro1_text = "Support me with"
            outro2_text = "a follow <3"
        
        cta_text_wide_val = args.cta_text if args.cta_text else "Sheets in bio ↓"
        with open(title_txt, "w", encoding="utf-8") as f: f.write(title_text)
        with open(author_txt, "w", encoding="utf-8") as f: f.write(author_text)
        with open(outro1_txt, "w", encoding="utf-8") as f: f.write(outro1_text)
        with open(outro2_txt, "w", encoding="utf-8") as f: f.write(outro2_text)
        with open(sheets_txt, "w", encoding="utf-8") as f: f.write(cta_text_wide_val)
        
        t_title_esc = escape_path_for_ffmpeg(title_txt)
        t_author_esc = escape_path_for_ffmpeg(author_txt)
        t_outro1_esc = escape_path_for_ffmpeg(outro1_txt)
        t_outro2_esc = escape_path_for_ffmpeg(outro2_txt)
        t_sheets_esc = escape_path_for_ffmpeg(sheets_txt)
        f_arno_esc = escape_path_for_ffmpeg(arno_pro)
        f_montserrat_esc = escape_path_for_ffmpeg(montserrat)
        f_montserrat_bold_esc = escape_path_for_ffmpeg(os.path.join(fonts_dir, 'Montserrat-Bold.ttf'))
        
        # Widescreen: keep native 16:9 resolution, no crop, no zoom, no shift
        # Title + Author + Sheets visible for 3s then fade out over 1s (visible 0-3, fade 3-4)
        # Outro text visible near the end. Subtle icon watermark in corner.
        v_stream = "v_src"
        pre_filters = f"[0:v]setpts=PTS-STARTPTS[v_src]; "
        
        if args.visualizer and os.path.exists(mask_path):
            extra_inputs.extend(["-i", mask_path])
            mask_idx = current_input_idx
            current_input_idx += 1
            # V4 widescreen mask is full 2560x1440 — direct screen blend, no overlay positioning
            pre_filters += f"[v_src]format=gbrp[v_rgb]; [{mask_idx}:v]setpts=PTS-STARTPTS,format=gbrp[mask_rgb]; [v_rgb][mask_rgb]blend=all_mode=screen,format=yuv420p[v_blended]; "
            v_stream = "v_blended"
            
        icon_wm_path = os.path.join(tools_dir, "pfps", "monogram-transparent.png")
        if os.path.exists(icon_wm_path):
            extra_inputs.extend(["-loop", "1", "-i", icon_wm_path])
            wm_idx = current_input_idx
            current_input_idx += 1
            pre_filters += f"[{wm_idx}:v]scale=68:-2,format=yuva420p,colorchannelmixer=aa=0.22[icon_wm_wide]; "
            wm_filter_wide = "[t3][icon_wm_wide]overlay=x=main_w-overlay_w-70:y=60:eof_action=repeat[t_wm]; "
        else:
            wm_filter_wide = "[t3]null[t_wm]; "

        # In-Video Subscribe/Like Hint (centered lower-third, appears at t=55.0s for 5.5s on longform videos >= 90s)
        sub_hint_path = os.path.join(tools_dir, "pfps", "subscribe_hint_animated.mov")
        sub_filter_wide = ""
        sub_v_stream = "t_wm"
        if os.path.exists(sub_hint_path) and new_duration >= 90.0:
            extra_inputs.extend(["-itsoffset", "55.0", "-i", sub_hint_path])
            sub_idx = current_input_idx
            current_input_idx += 1
            main_vid_w, _ = get_video_dimensions(input_video_path)
            sub_w = min(860, int(round(main_vid_w * 0.336)))
            if sub_w % 2 != 0:
                sub_w += 1
            pre_filters += f"[{sub_idx}:v]scale={sub_w}:-2[sub_ready]; "
            sub_filter_wide = f"[t_wm][sub_ready]overlay=x=(main_w-overlay_w)/2:y='main_h*0.65':eof_action=pass[t_sub]; "
            sub_v_stream = "t_sub"

        a_stream = "a_src"
        if args.metronome and args.type == 'tutorial':
            if use_custom_metronome:
                extra_inputs.extend(["-i", metronome_path])
                metronome_idx = current_input_idx
                current_input_idx += 1
                pre_filters += f"[{metronome_idx}:a]atrim=start=0:end={new_duration},asetpts=PTS-STARTPTS[click_track]; "
            else:
                # Failsafe if midi metronome failed: fallback silent audio track of new_duration
                pre_filters += f"anullsrc=r=44100:cl=stereo:d={new_duration}[click_track]; "
            
            pre_filters += f"[0:a][click_track]amix=inputs=2:duration=first:weights=1 0.8:normalize=0[a_src_mix]; "
            a_stream = "a_src_mix"
        else:
            pre_filters += f"[0:a]asetpts=PTS-STARTPTS[a_src]; "
        
        filter_complex = (
            pre_filters +
            
            # Intro: larger centered, fades out after 3s
            f"[{v_stream}]drawtext=fontfile='{f_arno_esc}':textfile='{t_title_esc}':fontcolor=white:fontsize=150"
            f":x=(w-text_w)/2:y=(h/2)-120"
            f":alpha='if(lt(t,3),1,if(lt(t,4),4-t,0))'[t1]; "
            
            # Author: below title, same fade timing
            f"[t1]drawtext=fontfile='{f_montserrat_esc}':textfile='{t_author_esc}':fontcolor=white:fontsize=75"
            f":x=(w-text_w)/2:y=(h/2)+50"
            f":alpha='if(lt(t,3),1,if(lt(t,4),4-t,0))'[t2]; "
            
            # Sheets in bio: bottom area, same fade timing
            f"[t2]drawtext=fontfile='{f_montserrat_bold_esc}':textfile='{t_sheets_esc}':fontcolor=white:fontsize=72"
            f":x=(w-text_w)/2:y=h-120"
            f":alpha='if(lt(t,3),1,if(lt(t,4),4-t,0))'[t3]; "
            
            # Subtle Icon Watermark (top right)
            + wm_filter_wide
            + sub_filter_wide +
            
            # Widescreen: keep clean video and audio ending naturally (no black fade-out or white outro slate)
            f"[{sub_v_stream}]setsar=1,format=yuv420p[final_v]; "
            f"[{a_stream}]anull[final_a]"
        )
        
        output_dir = args.output_dir if args.output_dir else tiktok_dir
        os.makedirs(output_dir, exist_ok=True)
        output_name = f"{out_base}_wide.mp4"
        output_path = os.path.join(output_dir, output_name)
    
    # ==================== STANDARD PORTRAIT MODE ====================
    else:
        if args.start_time is not None and args.end_time is not None and float(args.end_time) > float(args.start_time):
            # args.start_time and args.end_time are selected in the UI preview player.
            # args.time_offset compensates for the lead-in difference between preview player and raw Keysight input (default 2.5s for TikTok preview, 0.0s for raw Keysight).
            offset = float(getattr(args, 'time_offset', 2.5))
            trim_start = float(args.start_time) + offset
            trim_end = float(args.end_time) + offset
            new_duration = trim_end - trim_start
        else:
            trim_start = 2.5
            trim_end = duration
            if args.type != 'normal':
                # Cut 2 additional seconds off the end if it's the slow tutorial version
                trim_end -= 2.0
            new_duration = trim_end - 2.5
            
        fade_start = max(0.0, new_duration - 1.5)
        outro_start = fade_start
        
        is_easy = "easy" in args.title.lower() or "easy" in os.path.basename(args.video).lower()
        is_teaser = "teaser" in args.title.lower() or "teaser" in os.path.basename(args.video).lower() or (args.start_time is not None and args.end_time is not None)
        
        title_text = args.title
        title_text = re.sub(r'(?i)\b(easy|teaser|slow|recycled(\s+\d+)?)\b', '', title_text).strip()
        title_clean = title_text.replace('\\n', '\n')
        title_lines = [re.sub(r'[ \t]+', ' ', line).strip() for line in title_clean.split('\n') if line.strip()]
        title_clean = '\n'.join(title_lines)
        title1_text, title2_text = split_title_lines(title_clean, arno_pro, max_width=1080)
        is_two_line_title = bool(title2_text)

        if args.subtitle is not None and args.subtitle.strip(" -"):
            author_text = args.subtitle.strip(" -")
        elif args.author and args.author.strip(" -"):
            author_text = args.author.strip(" -")
        else:
            author_text = ""
        
        is_recycling = bool(args.output_dir and "recycling" in args.output_dir.lower()) or (args.outro_text is not None)
        
        if args.outro_text:
            outro_raw = args.outro_text.strip()
        elif is_recycling:
            outro_raw = "Rate this 1-10 👇"
        elif is_teaser:
            outro_raw = "Full video coming soon\nFollow me <3"
        elif args.type == 'normal':
            outro_raw = "Don't miss the tutorial\nFollow me <3"
        else:
            outro_raw = "Support me with\na follow <3"

        # Sanitize: remove any 'or suggest a song'
        outro_raw = re.sub(r'(?i)\s+or\s+suggest\s+a\s+song', '', outro_raw).strip()

        if "\n" in outro_raw:
            parts = outro_raw.split("\n", 1)
            outro1_text = parts[0].strip()
            outro2_text = parts[1].strip()
        elif " or " in outro_raw and len(outro_raw) > 24:
            parts = outro_raw.split(" or ", 1)
            outro1_text = parts[0].strip() + " or"
            outro2_text = parts[1].strip()
        else:
            outro1_text = outro_raw
            outro2_text = ""
            
        has_emoji = any(ord(c) > 127 for c in (outro1_text + outro2_text))
        segui_font = "C:/Windows/Fonts/seguiemj.ttf"
        if has_emoji and os.path.exists(segui_font):
            outro_font = segui_font
        else:
            outro_font = "fonts/arno_pro.ttf"
            
        if args.type == 'tutorial':
            morph_text = "save to practice later"
        elif is_easy:
            morph_text = "too easy? try original version"
        elif args.has_easy:
            morph_text = "too hard? try easy version"
        else:
            morph_text = ""

        if args.cta_text:
            cta_text_val = args.cta_text.strip()
            if not any(a in cta_text_val for a in ["→", "->", "=>"]):
                cta_text_val = f"{cta_text_val} →"
        else:
            cta_text_val = "Want to play this? Sheets in bio →"

        with open(title_txt, "w", encoding="utf-8") as f: f.write(title_clean)
        with open(title1_txt, "w", encoding="utf-8") as f: f.write(title1_text)
        if is_two_line_title:
            with open(title2_txt, "w", encoding="utf-8") as f: f.write(title2_text)
        with open(author_txt, "w", encoding="utf-8") as f: f.write(author_text)
        with open(outro1_txt, "w", encoding="utf-8") as f: f.write(outro1_text)
        with open(outro2_txt, "w", encoding="utf-8") as f: f.write(outro2_text)
        with open(sheets_txt, "w", encoding="utf-8") as f: f.write(cta_text_val)
        with open(morph_txt, "w", encoding="utf-8") as f: f.write(morph_text)
        with open(watermark_txt, "w", encoding="utf-8") as f: f.write("@meloscribe")
        
        t_title_esc = escape_path_for_ffmpeg(title_txt)
        t_title1_esc = escape_path_for_ffmpeg(title1_txt)
        t_title2_esc = escape_path_for_ffmpeg(title2_txt) if is_two_line_title else ""
        t_author_esc = escape_path_for_ffmpeg(author_txt)
        t_outro1_esc = escape_path_for_ffmpeg(outro1_txt)
        t_outro2_esc = escape_path_for_ffmpeg(outro2_txt)
        t_sheets_esc = escape_path_for_ffmpeg(sheets_txt)
        t_morph_esc = escape_path_for_ffmpeg(morph_txt)
        t_wm_esc = escape_path_for_ffmpeg(watermark_txt)
        f_outro_esc = escape_path_for_ffmpeg(outro_font)
        f_arno_esc = escape_path_for_ffmpeg(arno_pro)
        f_montserrat_esc = escape_path_for_ffmpeg(montserrat)
        f_montserrat_bold_esc = escape_path_for_ffmpeg(os.path.join(fonts_dir, 'Montserrat-Bold.ttf'))

        
        scale_w = int(1440 * args.zoom)
        if scale_w % 2 != 0: scale_w += 1
        
        scale_h = int(810 * args.zoom)
        if scale_h % 2 != 0: scale_h += 1
        
        x_crop = (scale_w - 1440) // 2 + args.shift
        if x_crop < 0: x_crop = 0
        if x_crop + 1440 > scale_w: x_crop = scale_w - 1440
        
        y_overlay = (2560 - scale_h) // 2
        bg_black_start_y = y_overlay + scale_h
        
        # Position title & subtitle dynamically relative to video top edge (y_overlay)
        # to ensure constant, perfect spacing across all zoom & crop values
        if is_two_line_title:
            title_fontsize = 108
            subtitle_fontsize = 60
            if not author_text:
                title2_y = y_overlay - 100
                title1_y = title2_y - 105
                author_y = y_overlay - 50
            else:
                author_y = y_overlay - 60
                title2_y = author_y - 120
                title1_y = title2_y - 105
            
            # TikTok top search bar safe zone clamp (y >= 365)
            if title1_y < 365:
                shift = 365 - title1_y
                title1_y += shift
                title2_y += min(shift, 20)
            title_y = title1_y
        else:
            title_fontsize = 130
            subtitle_fontsize = 62
            if not author_text:
                title_y = y_overlay - 120
                author_y = y_overlay - 50
            else:
                author_y = y_overlay - 65
                title_y = author_y - 150
            
            # TikTok top search bar safe zone clamp (y >= 365)
            if title_y < 365:
                title_y = 365
        
        pre_filters = (
            f"[0:v]trim=start={trim_start}:end={trim_end},setpts=PTS-STARTPTS[v_trimmed]; "
            f"[0:a]atrim=start={trim_start}:end={trim_end},asetpts=PTS-STARTPTS[a_trimmed]; "
            f"[v_trimmed]split=2[bg_orig][fg_orig]; "
        )
        a_stream = "a_trimmed"
        
        if args.metronome and args.type == 'tutorial':
            if use_custom_metronome:
                extra_inputs.extend(["-i", metronome_path])
                metronome_idx = current_input_idx
                current_input_idx += 1
                # Must trim custom metronome to match trim_start since main video is also trimmed
                pre_filters += f"[{metronome_idx}:a]atrim=start={trim_start}:end={trim_end},asetpts=PTS-STARTPTS[click_track]; "
            else:
                # Failsafe if midi metronome failed: fallback silent track
                pre_filters += f"anullsrc=r=44100:cl=stereo:d={new_duration}[click_track]; "
            pre_filters += f"[{a_stream}][click_track]amix=inputs=2:duration=first:weights=1 0.8:normalize=0[a_mixed]; "
            a_stream = "a_mixed"
            
        fg_stream = "fg_scaled"
        if args.visualizer and os.path.exists(mask_path):
            extra_inputs.extend(["-i", mask_path])
            mask_idx = current_input_idx
            current_input_idx += 1
            pre_filters += f"[{mask_idx}:v]trim=start={trim_start}:end={trim_end},setpts=PTS-STARTPTS,scale={scale_w}:500,crop=1440:500:{x_crop}:0,format=yuva420p,colorchannelmixer=1:0:0:0:0:1:0:0:0:0:1:0:0.3:0.59:0.11:0[mask_alpha]; "
            
            pre_filters += f"[fg_orig]scale={scale_w}:{scale_h},crop=1440:{scale_h}:{x_crop}:0[fg_scaled]; "
            post_merge = f"[merged][mask_alpha]overlay=x=0:y=0:eof_action=repeat[merged_vis]; "
            merged_stream = "merged_vis"
        else:
            pre_filters += f"[fg_orig]scale={scale_w}:{scale_h},crop=1440:{scale_h}:{x_crop}:0[fg_scaled]; "
            post_merge = ""
            merged_stream = "merged"

        ref_merge_str = ""
        base_merged = "merged"
        # Glossy concert stage floor keyboard reflection (soft ambient satin sheen)
        ref_mask_path = os.path.join(tools_dir, "img", "ref_mask_1440x240.png")
        if not os.path.exists(ref_mask_path):
            try:
                from PIL import Image, ImageDraw
                m = Image.new('L', (1440, 240))
                d = ImageDraw.Draw(m)
                fade_px = 18
                start_ratio = 0.35
                for my in range(240):
                    decay = (1.0 - (my / 240.0)) ** 1.8
                    if my < fade_px:
                        t = my / float(fade_px)
                        smooth = t * t * (3.0 - 2.0 * t)
                        fade = start_ratio + (1.0 - start_ratio) * smooth
                        decay *= fade
                    val = int(65 * decay)
                    d.line([(0, my), (1440, my)], fill=max(0, min(255, val)))
                m.save(ref_mask_path)
            except Exception:
                pass
        if os.path.exists(ref_mask_path) and not getattr(args, "no_reflection", False):
            extra_inputs.extend(["-loop", "1", "-t", str(new_duration), "-i", ref_mask_path])
            mask_idx = current_input_idx
            current_input_idx += 1
            ref_h = 240
            pre_filters += (
                f"[{fg_stream}]split=2[fg_main][fg_for_ref]; "
                f"[fg_for_ref]crop=1440:{ref_h}:0:{scale_h - ref_h},vflip,boxblur=10:2,format=rgba[ref_flipped]; "
                f"[{mask_idx}:v]format=gray[mask_gray]; "
                f"[ref_flipped][mask_gray]alphamerge[ref_ready]; "
            )
            fg_stream = "fg_main"
            ref_merge_str = f"[merged_base][ref_ready]overlay=0:{bg_black_start_y}[merged]; "
            base_merged = "merged_base"

        # Title Image vs Text
        timg_path = os.path.abspath(args.title_image) if args.title_image else None
        if timg_path and os.path.exists(timg_path):
            extra_inputs.extend(["-i", timg_path])
            timg_idx = current_input_idx
            current_input_idx += 1
            timg_w = 460
            timg_y = 240
            pre_filters += f"[{timg_idx}:v]scale={timg_w}:-1[title_img_scaled]; "
            title_layer = f"[{merged_stream}][title_img_scaled]overlay=x=(main_w-overlay_w)/2:y={timg_y}[t1]; "
            author_y = 560
        elif is_two_line_title:
            title_layer = (
                f"[{merged_stream}]drawtext=fontfile='{f_arno_esc}':textfile='{t_title1_esc}':fontcolor=white:fontsize={title_fontsize}:x=(w-text_w)/2:y={title1_y}:alpha='1'[t0_l1]; "
                f"[t0_l1]drawtext=fontfile='{f_arno_esc}':textfile='{t_title2_esc}':fontcolor=white:fontsize={title_fontsize}:x=(w-text_w)/2:y={title2_y}:alpha='1'[t1]; "
            )
        else:
            title_layer = f"[{merged_stream}]drawtext=fontfile='{f_arno_esc}':textfile='{t_title1_esc}':fontcolor=white:fontsize={title_fontsize}:x=(w-text_w)/2:y={title_y}:alpha='1'[t1]; "

        # Subtle brand icon watermark overlay (bottom right corner)
        icon_wm_path = os.path.join(tools_dir, "pfps", "monogram-transparent.png")
        if os.path.exists(icon_wm_path):
            extra_inputs.extend(["-loop", "1", "-i", icon_wm_path])
            wm_idx = current_input_idx
            current_input_idx += 1
            pre_filters += f"[{wm_idx}:v]scale=76:-1,format=yuva420p,colorchannelmixer=aa=0.28[icon_wm_scaled]; "
            wm_overlay_portrait = "[t3][icon_wm_scaled]overlay=x=main_w-overlay_w-75:y=main_h-overlay_h-80:eof_action=repeat[t4]; "
        else:
            wm_overlay_portrait = "[t3]null[t4]; "

        cta_fontsize = 62 if len(cta_text_val) > 20 else 78
        morph_fontsize = 62 if len(morph_text) > 20 else 78

        # CTA Timing: Appear after 3s, disappear cleanly after a few seconds,
        # and if the video is long enough, reappear briefly for ~3.5s before the outro.
        if morph_text:
            has_end_cta = (fade_start - 3.5) >= 12.0
            if has_end_cta:
                end_cta_st = round(fade_start - 3.5, 2)
                end_cta_fd = round(end_cta_st + 0.5, 2)
                end_cta_alpha = f"if(lt(t,{end_cta_st}),0,if(lt(t,{end_cta_fd}),(t-{end_cta_st})/0.5,1))"
                cta_layers = (
                    f"[t2]drawtext=fontfile='{f_montserrat_bold_esc}':textfile='{t_sheets_esc}':fontcolor=white:fontsize={cta_fontsize}:x=(w-text_w)/2:y=2150:alpha='if(lt(t,3),0,if(lt(t,4),t-3,if(lt(t,5.8),1,if(lt(t,6.3),(6.3-t)/0.5,0))))'[t2_sheets]; "
                    f"[t2_sheets]drawtext=fontfile='{f_montserrat_bold_esc}':textfile='{t_morph_esc}':fontcolor=white:fontsize={morph_fontsize}:x=(w-text_w)/2:y=2150:alpha='if(lt(t,6.3),0,if(lt(t,6.8),(t-6.3)/0.5,if(lt(t,8.8),1,if(lt(t,9.3),(9.3-t)/0.5,0))))'[t2_morph]; "
                    f"[t2_morph]drawtext=fontfile='{f_montserrat_bold_esc}':textfile='{t_sheets_esc}':fontcolor=white:fontsize={cta_fontsize}:x=(w-text_w)/2:y=2150:alpha='{end_cta_alpha}'[t3]; "
                )
            else:
                cta_layers = (
                    f"[t2]drawtext=fontfile='{f_montserrat_bold_esc}':textfile='{t_sheets_esc}':fontcolor=white:fontsize={cta_fontsize}:x=(w-text_w)/2:y=2150:alpha='if(lt(t,3),0,if(lt(t,4),t-3,if(lt(t,5.8),1,if(lt(t,6.3),(6.3-t)/0.5,0))))'[t2_sheets]; "
                    f"[t2_sheets]drawtext=fontfile='{f_montserrat_bold_esc}':textfile='{t_morph_esc}':fontcolor=white:fontsize={morph_fontsize}:x=(w-text_w)/2:y=2150:alpha='if(lt(t,6.3),0,if(lt(t,6.8),(t-6.3)/0.5,if(lt(t,8.8),1,if(lt(t,9.3),(9.3-t)/0.5,0))))'[t3]; "
                )
        else:
            has_end_cta = (fade_start - 3.5) >= 9.0
            intro_expr = "if(lt(t,3),0,if(lt(t,3.5),(t-3)/0.5,if(lt(t,6.0),1,if(lt(t,6.5),(6.5-t)/0.5,0))))"
            if has_end_cta:
                end_cta_st = round(fade_start - 3.5, 2)
                end_cta_fd = round(end_cta_st + 0.5, 2)
                outro_expr = f"if(lt(t,{end_cta_st}),0,if(lt(t,{end_cta_fd}),(t-{end_cta_st})/0.5,1))"
                cta_alpha = f"max({intro_expr},{outro_expr})"
            else:
                cta_alpha = intro_expr
            cta_layers = (
                f"[t2]drawtext=fontfile='{f_montserrat_bold_esc}':textfile='{t_sheets_esc}':fontcolor=white:fontsize={cta_fontsize}:x=(w-text_w)/2:y=2150:alpha='{cta_alpha}'[t3]; "
            )

        filter_complex = (
            pre_filters + 
            f"[bg_orig]scale=360:640,boxblur=10:3,scale=1440:2560:flags=bicubic,colorchannelmixer=rr=0.58:gg=0.58:bb=0.58,drawbox=x=0:y={bg_black_start_y}:w=1440:h=2560:color=black@0.80:t=fill[bg_blur]; "
            
            f"[bg_blur][{fg_stream}]overlay=0:{y_overlay}[{base_merged}]; "
            + ref_merge_str
            + post_merge
            + title_layer +
            f"[t1]drawtext=fontfile='{f_montserrat_esc}':textfile='{t_author_esc}':fontcolor=white:fontsize={subtitle_fontsize}:x=(w-text_w)/2:y={author_y}:alpha='1'[t2]; "
            + cta_layers
            + wm_overlay_portrait +
            
            # Fade out the background video canvas behind the outro text
            f"[t4]fade=t=out:st={fade_start}:d=1.5[v_faded]; "
            
            # Outro gets drawn as centered text on top of the faded video canvas (text stays solid white)
            f"[v_faded]drawtext=fontfile='{f_outro_esc}':textfile='{t_outro1_esc}':fontcolor=white:fontsize=80:x=(w-text_w)/2:y={1060 if not outro2_text else 1000}:enable='between(t,{outro_start},{new_duration})':alpha='if(lt(t,{outro_start}),0,min(1,(t-{outro_start})/0.5))'[t5]; "
            f"[t5]drawtext=fontfile='{f_outro_esc}':textfile='{t_outro2_esc}':fontcolor=white:fontsize=80:x=(w-text_w)/2:y=1120:enable='between(t,{outro_start},{new_duration})':alpha='if(lt(t,{outro_start}),0,min(1,(t-{outro_start})/0.5))'[with_outro]; "
            
            f"[with_outro]setsar=1,format=yuv420p[final_v]; "
            f"[{a_stream}]afade=t=out:st={fade_start}:d=1.5[final_a]"
        )
        
        output_dir = args.output_dir if args.output_dir else tiktok_dir
        os.makedirs(output_dir, exist_ok=True)
        output_name = f"{out_base}.mp4" if not out_base.lower().endswith(".mp4") else out_base
        output_path = os.path.join(output_dir, output_name)
        

    
    tmp_output_path = output_path + ".tmp.mp4"

    try:
        nv_check = subprocess.run([get_ffmpeg_exe(), "-h", "encoder=h264_nvenc"], capture_output=True, text=True)
        if "h264_nvenc" in nv_check.stdout:
            target_br = "8M" if args.wide else "6M"
            max_br = "15M" if args.wide else "12M"
            buf_sz = "30M" if args.wide else "24M"
            print(f"Rendering with GPU NVENC h264_nvenc (High-Quality P6, Target {target_br}, MaxBitrate {max_br})...")
            vcodec_args = [
                "-c:v", "h264_nvenc",
                "-preset", "p6",
                "-tune", "hq",
                "-rc", "vbr",
                "-cq", "20",
                "-b:v", target_br,
                "-maxrate:v", max_br,
                "-bufsize:v", buf_sz,
                "-spatial-aq", "1",
                "-temporal-aq", "0",
                "-g", "120",
                "-keyint_min", "60",
                "-forced-idr", "1",
                "-flags", "+cgop",
                "-pix_fmt", "yuv420p"
            ]
        else:
            print("Rendering with multi-threaded CPU libx264 CRF 19 (yuv420p)...")
            vcodec_args = [
                "-c:v", "libx264",
                "-preset", "veryfast",
                "-crf", "19",
                "-g", "120",
                "-keyint_min", "60",
                "-flags", "+cgop",
                "-pix_fmt", "yuv420p"
            ]
    except Exception:
        vcodec_args = [
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-crf", "19",
            "-g", "120",
            "-keyint_min", "60",
            "-flags", "+cgop",
            "-pix_fmt", "yuv420p"
        ]

    # Build ffmpeg command directly as a list
    cmd = [
        get_ffmpeg_exe(), "-y", "-hide_banner",
        "-i", input_video_path
    ] + extra_inputs + [
        "-filter_complex", filter_complex,
        "-map", "[final_v]", "-map", "[final_a]"
    ] + vcodec_args + [
        "-fps_mode", "cfr",
        "-r", "60",
        "-t", f"{new_duration:.3f}",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        tmp_output_path
    ]
    
    print(f"Starting Video Generation for '{out_base}' ({args.type})...")
    print(f"Zoom Factor: {args.zoom} | Shift: {args.shift}")
    if new_duration > 0:
        minutes = int(new_duration // 60)
        secs = int(new_duration % 60)
        print(f"Output duration: {minutes}m {secs}s")
    
    try:
        # Run FFmpeg with hidden window and progress parsing
        process = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            cwd=tools_dir, creationflags=CREATE_NO_WINDOW
        )
        
        last_pct = -1
        buf = b''
        while True:
            byte = process.stdout.read(1)
            if not byte:
                break
            if byte in (b'\r', b'\n'):
                if buf:
                    line = buf.decode('utf-8', errors='replace')
                    buf = b''
                    
                    # Parse FFmpeg progress: look for time=HH:MM:SS.xx
                    time_match = re.search(r'time=(\d+:\d+:\d+\.\d+)', line)
                    if time_match and new_duration > 0:
                        current = time_to_seconds(time_match.group(1))
                        pct = min(int((current / new_duration) * 100), 100)
                        if pct != last_pct and pct % 5 == 0:
                            print(f"PROGRESS:{pct}% ({int(current)}s / {int(new_duration)}s)")
                            sys.stdout.flush()
                            last_pct = pct
                    elif 'error' in line.lower() and 'no such' not in line.lower():
                        print(line.rstrip())
                        sys.stdout.flush()
            else:
                buf += byte
        
        process.wait()
        if process.returncode != 0:
            print(f"DEBUG FILTER COMPLEX: {filter_complex}")
            print(f"FFmpeg video generation failed with returncode {process.returncode}!")
            if os.path.exists(tmp_output_path):
                try: os.remove(tmp_output_path)
                except: pass
            sys.exit(1)
        
        # Robust atomic replace on Windows (with retry for antivirus/player locks)
        if os.path.exists(tmp_output_path):
            replaced = False
            for attempt in range(10):
                try:
                    if os.path.exists(output_path):
                        try: os.remove(output_path)
                        except Exception: pass
                    os.replace(tmp_output_path, output_path)
                    replaced = True
                    break
                except Exception:
                    time.sleep(0.5)
            if not replaced:
                import shutil
                try:
                    shutil.copy2(tmp_output_path, output_path)
                    os.remove(tmp_output_path)
                except Exception as ce:
                    print(f"Warning: Could not replace destination directly ({ce}), saving temporary copy.")

        print(f"PROGRESS:100%")
        print(f"Done! Saved at: {output_path}")

        # When widescreen is rendered, generate dedicated YouTube file with animated MeloScribe outro
        if args.wide:
            try:
                from meloscribe_outro import create_and_append_youtube_outro
                yt_output_path = output_path.replace("_wide.mp4", "_wide_yt.mp4")
                print(f"[YouTube Pipeline] Assembling dedicated YouTube widescreen video with outro...")
                create_and_append_youtube_outro(
                    clean_wide_path=output_path,
                    output_yt_path=yt_output_path,
                    song_title=args.title,
                    song_author=author_text
                )
                print(f"[YouTube Pipeline] YouTube widescreen video ready at: {yt_output_path}")
            except Exception as yte:
                print(f"[YouTube Pipeline] Warning: Could not generate YouTube widescreen outro: {yte}")
    except Exception as fe:
        print(f"FFmpeg execution error: {fe}")
        sys.exit(1)

    # --- LOG TO ANALYTICS ENGINE DATABASE ---
    try:
        import sqlite3
        db_path = os.path.join(tools_dir, "meloscribe", "backend", "analytics.db")
        if os.path.exists(db_path):
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            cursor.execute('''
                INSERT OR REPLACE INTO tracks (song_name, author, theme, bpm, duration_sec, time_signature)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (args.title, args.author, args.theme, detected_bpm, new_duration, detected_timesig))
            conn.commit()
            conn.close()
            print(f"Analytics Update: Tracked [{args.title}] by [{args.author}] with {detected_bpm} BPM, {detected_timesig}, {int(new_duration)}s duration.")
        
        # Update render cache on successful video generation
        try:
            cache = {}
            if os.path.exists(cache_path):
                with open(cache_path, "r", encoding="utf-8") as f:
                    cache = json.load(f)
            cache[output_name] = current_params
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump(cache, f, indent=2, ensure_ascii=False)
        except Exception as ce:
            print(f"Warning: Failed to update render cache: {ce}")
    except Exception as e:
        print(f"Warning: Analytics engine DB update failed: {e}")

    finally:
        # cleanup temp UUID files from temp_dir
        for temp_f in [
            title_txt, author_txt, outro1_txt, outro2_txt,
            sheets_txt, morph_txt, watermark_txt,
            mask_path, metronome_path
        ]:
            try:
                if os.path.exists(temp_f):
                    os.remove(temp_f)
            except: pass

if __name__ == '__main__':
    main()
