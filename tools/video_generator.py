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

def get_video_duration(video_path):
    cmd = [get_ffprobe_exe(), '-v', 'error', '-show_entries', 'format=duration',
           '-of', 'default=noprint_wrappers=1:nokey=1', video_path]
    result = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8',
                            errors='replace', creationflags=CREATE_NO_WINDOW)
    return float(result.stdout.strip())

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
    parser.add_argument('--use_portrait_addon', action='store_true', help='Use dynamically scaled custom portrait addon images')
    parser.add_argument('--timesig', type=str, default='auto', help='Time signature override (e.g. 3/4, 4/4). Default: auto-detect from MIDI')
    parser.add_argument('--force', action='store_true', help='Force overwrite existing output video')
    parser.add_argument('--metro_offset', type=float, default=0.0, help='Shift metronome clicks by this many beats (e.g. 0.5)')
    parser.add_argument('--has_easy', action='store_true', help='Specify if an easy version of this song exists')
    parser.add_argument('--subtitle', type=str, default=None, help='Custom subtitle text displayed below title in portrait video. If empty/unset, no subtitle is displayed.')
    parser.add_argument('--start_time', type=float, default=None, help='Optional clip start time in seconds')
    parser.add_argument('--end_time', type=float, default=None, help='Optional clip end time in seconds')
    args = parser.parse_args()

    
    # Load settings from meloscribe backend
    import sys
    sys.path.append(os.path.join(os.path.dirname(__file__), "meloscribe", "backend"))
    from settings import load_settings
    settings = load_settings()
    
    tiktok_dir = settings.get("tiktok_dir", r"C:\Dev\meloscribe\TikToks")
    covers_dir = settings.get("covers_dir", r"C:\Dev\meloscribe\Covers")
    
    # Early Parameter Cache Check
    raw_name = os.path.splitext(os.path.basename(args.video))[0]
    clean_name = raw_name[:-4] if raw_name.lower().endswith("_raw") else raw_name
    out_base = args.title.strip() if (args.title and args.title.strip()) else clean_name
    output_name = f"{out_base}_wide.mp4" if args.wide else (f"{out_base}.mp4" if not out_base.lower().endswith(".mp4") else out_base)
    output_path = os.path.join(tiktok_dir, output_name)
    cache_path = os.path.join(tiktok_dir, ".render_cache.json")
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

    mask_path = os.path.join(temp_dir, f"waves_mask_{uid}.mp4")
    metronome_path = os.path.join(temp_dir, f"metronome_{uid}.wav")
    
    title_txt = os.path.join(temp_dir, f"title_{uid}.txt")
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
                elif args.midipath and "god rest ye merry" in args.midipath.lower():
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
        
        title_text = re.sub(r'(?i)\beasy\b', '', args.title).strip()
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
        
        # Write text files
        with open(title_txt, "w", encoding="utf-8") as f: f.write(title_text)
        with open(author_txt, "w", encoding="utf-8") as f: f.write(author_text)
        with open(outro1_txt, "w", encoding="utf-8") as f: f.write(outro1_text)
        with open(outro2_txt, "w", encoding="utf-8") as f: f.write(outro2_text)
        with open(sheets_txt, "w", encoding="utf-8") as f: f.write("Sheets in bio ↓")
        with open(watermark_txt, "w", encoding="utf-8") as f: f.write("@meloscribe")
        
        t_title_esc = escape_path_for_ffmpeg(title_txt)
        t_author_esc = escape_path_for_ffmpeg(author_txt)
        t_outro1_esc = escape_path_for_ffmpeg(outro1_txt)
        t_outro2_esc = escape_path_for_ffmpeg(outro2_txt)
        t_sheets_esc = escape_path_for_ffmpeg(sheets_txt)
        t_wm_esc = escape_path_for_ffmpeg(watermark_txt)
        
        # Widescreen: keep native 16:9 resolution, no crop, no zoom, no shift
        # Title + Author + Sheets visible for 3s then fade out over 1s (visible 0-3, fade 3-4)
        # Outro text visible near the end. No watermark.
        v_stream = "v_src"
        pre_filters = f"[0:v]setpts=PTS-STARTPTS[v_src]; "
        
        if args.visualizer and os.path.exists(mask_path):
            extra_inputs.extend(["-i", mask_path])
            mask_idx = current_input_idx
            current_input_idx += 1
            # V4 widescreen mask is full 2560x1440 — direct screen blend, no overlay positioning
            pre_filters += f"[v_src]format=gbrp[v_rgb]; [{mask_idx}:v]setpts=PTS-STARTPTS,format=gbrp[mask_rgb]; [v_rgb][mask_rgb]blend=all_mode=screen,format=yuv420p[v_blended]; "
            v_stream = "v_blended"
            
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
            f"[{v_stream}]drawtext=fontfile='fonts/arno_pro.ttf':textfile='{t_title_esc}':fontcolor=white:fontsize=150"
            f":x=(w-text_w)/2:y=(h/2)-120"
            f":alpha='if(lt(t,3),1,if(lt(t,4),4-t,0))'[t1]; "
            
            # Author: below title, same fade timing
            f"[t1]drawtext=fontfile='fonts/montserrat.ttf':textfile='{t_author_esc}':fontcolor=white:fontsize=75"
            f":x=(w-text_w)/2:y=(h/2)+50"
            f":alpha='if(lt(t,3),1,if(lt(t,4),4-t,0))'[t2]; "
            
            # Sheets in bio: bottom area, same fade timing
            f"[t2]drawtext=fontfile='fonts/Montserrat-Bold.ttf':textfile='{t_sheets_esc}':fontcolor=white:fontsize=72"
            f":x=(w-text_w)/2:y=h-120"
            f":alpha='if(lt(t,3),1,if(lt(t,4),4-t,0))'[t3]; "
            
            # Watermark (top right, lower opacity)
            f"[t3]drawtext=fontfile='fonts/montserrat.ttf':textfile='{t_wm_esc}':fontcolor=white@0.15:fontsize=44:x=w-text_w-80:y=60:alpha='1'[t_wm]; "
            
            # Outro gets drawn as two separate centered lines to ensure perfect alignment
            f"[t_wm]drawtext=fontfile='fonts/arno_pro.ttf':textfile='{t_outro1_esc}':fontcolor=white:fontsize=100:x=(w-text_w)/2:y=(h/2)-60:enable='between(t,{outro_start},{new_duration})':alpha='if(lt(t,{outro_start}),0,min(1,(t-{outro_start})/1))'[t4]; "
            f"[t4]drawtext=fontfile='fonts/arno_pro.ttf':textfile='{t_outro2_esc}':fontcolor=white:fontsize=100:x=(w-text_w)/2:y=(h/2)+60:enable='between(t,{outro_start},{new_duration})':alpha='if(lt(t,{outro_start}),0,min(1,(t-{outro_start})/1))'[with_outro]; "
            
            # Final video fade out at the very end and force SAR 1:1 and yuv420p
            f"[with_outro]fade=t=out:st={fade_start}:d=1.5[v_fade]; "
            f"[v_fade]setsar=1,format=yuv420p[final_v]; "
            f"[{a_stream}]afade=t=out:st={fade_start}:d=1.5[final_a]"
        )
        
        output_dir = tiktok_dir
        os.makedirs(output_dir, exist_ok=True)
        base_name = os.path.splitext(os.path.basename(args.video))[0]
        output_name = f"{base_name}_wide.mp4"
        output_path = os.path.join(output_dir, output_name)
    
    # ==================== STANDARD PORTRAIT MODE ====================
    else:
        if args.start_time is not None and args.end_time is not None and float(args.end_time) > float(args.start_time):
            trim_start = float(args.start_time)
            trim_end = float(args.end_time)
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
        if is_easy:
            title_text = re.sub(r'(?i)\beasy\b', '', title_text).strip()
            title_text = re.sub(r'\s+', ' ', title_text)
        elif is_teaser:
            title_text = re.sub(r'(?i)\bteaser\b', '', title_text).strip()
            title_text = re.sub(r'\s+', ' ', title_text)

        if args.subtitle is not None:
            author_text = args.subtitle.strip(" -")
        else:
            author_text = ""
        
        if is_teaser:
            outro1_text = "Full video coming soon"
            outro2_text = "Follow me <3"
        elif args.type == 'normal':
            outro1_text = "Don't miss the tutorial"
            outro2_text = "Follow me <3"
        else:
            outro1_text = "Support me with"
            outro2_text = "a follow <3"
            
        if args.type == 'tutorial':
            morph_text = "save to practice later"
        elif is_easy:
            morph_text = "too easy? try original version"
        elif args.has_easy:
            morph_text = "too hard? try easy version"
        else:
            morph_text = ""

        # Write text files to temp_dir
        with open(title_txt, "w", encoding="utf-8") as f: f.write(title_text)
        with open(author_txt, "w", encoding="utf-8") as f: f.write(author_text)
        with open(outro1_txt, "w", encoding="utf-8") as f: f.write(outro1_text)
        with open(outro2_txt, "w", encoding="utf-8") as f: f.write(outro2_text)
        with open(sheets_txt, "w", encoding="utf-8") as f: f.write("Sheets in bio →")
        with open(morph_txt, "w", encoding="utf-8") as f: f.write(morph_text)
        with open(watermark_txt, "w", encoding="utf-8") as f: f.write("@meloscribe")
        
        t_title_esc = escape_path_for_ffmpeg(title_txt)
        t_author_esc = escape_path_for_ffmpeg(author_txt)
        t_outro1_esc = escape_path_for_ffmpeg(outro1_txt)
        t_outro2_esc = escape_path_for_ffmpeg(outro2_txt)
        t_sheets_esc = escape_path_for_ffmpeg(sheets_txt)
        t_morph_esc = escape_path_for_ffmpeg(morph_txt)
        t_wm_esc = escape_path_for_ffmpeg(watermark_txt)

        
        scale_w = int(1440 * args.zoom)
        if scale_w % 2 != 0: scale_w += 1
        
        scale_h = int(810 * args.zoom)
        if scale_h % 2 != 0: scale_h += 1
        
        x_crop = (scale_w - 1440) // 2 + args.shift
        if x_crop < 0: x_crop = 0
        if x_crop + 1440 > scale_w: x_crop = scale_w - 1440
        
        y_overlay = (2560 - scale_h) // 2
        bg_black_start_y = y_overlay + scale_h
        
        # Position title lower for aesthetic optical balance
        if not author_text:
            title_y = 440
            author_y = y_overlay - 120
        else:
            title_y = 390
            author_y = y_overlay - 110
        
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
                # Must trim custom metronome by 2.5s since main video is also trimmed
                pre_filters += f"[{metronome_idx}:a]atrim=start=2.5:end={trim_end},asetpts=PTS-STARTPTS[click_track]; "
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
            pre_filters += f"[{mask_idx}:v]trim=start=2.5:end={trim_end},setpts=PTS-STARTPTS,scale={scale_w}:500,crop=1440:500:{x_crop}:0,format=yuva420p,colorchannelmixer=1:0:0:0:0:1:0:0:0:0:1:0:0.3:0.59:0.11:0[mask_alpha]; "
            
            pre_filters += f"[fg_orig]scale={scale_w}:{scale_h},crop=1440:{scale_h}:{x_crop}:0[fg_scaled]; "
            post_merge = f"[merged][mask_alpha]overlay=x=0:y=0:eof_action=repeat[merged_vis]; "
            merged_stream = "merged_vis"
        else:
            pre_filters += f"[fg_orig]scale={scale_w}:{scale_h},crop=1440:{scale_h}:{x_crop}:0[fg_scaled]; "
            post_merge = ""
            merged_stream = "merged"

        addon_merge_str = ""
        base_merged = "merged"
        if args.use_portrait_addon:
            addon_img = os.path.join(tools_dir, f"{args.theme} addon.png")
            if not os.path.exists(addon_img):
                addon_img = os.path.join(tools_dir, f"{args.theme} addon.jpg")
            if os.path.exists(addon_img):
                extra_inputs.extend(["-i", addon_img])
                addon_idx = current_input_idx
                current_input_idx += 1
                addon_y = bg_black_start_y
                addon_h = 2560 - addon_y
                scale_expr = f"'max(1440, iw*{addon_h}/ih)':'max({addon_h}, ih*1440/iw)'"
                crop_expr = f"1440:{addon_h}:'(iw-1440)/2':'(ih-{addon_h})/2'"
                pre_filters += f"[{addon_idx}:v]scale={scale_expr},crop={crop_expr}[addon_cropped]; "
                addon_merge_str = f"[merged_base][addon_cropped]overlay=0:{addon_y}[merged]; "
                base_merged = "merged_base"

        filter_complex = (
            pre_filters + 
            f"[bg_orig]scale=360:640,boxblur=10:3,scale=1440:2560:flags=bicubic,colorchannelmixer=rr=0.5:gg=0.5:bb=0.5,drawbox=x=0:y={bg_black_start_y}:w=1440:h=2560:color=black:t=fill[bg_blur]; "
            
            f"[bg_blur][{fg_stream}]overlay=0:{y_overlay}[{base_merged}]; "
            + addon_merge_str
            + post_merge +
            
            f"[{merged_stream}]drawtext=fontfile='fonts/arno_pro.ttf':textfile='{t_title_esc}':fontcolor=white:fontsize=130:x=(w-text_w)/2:y={title_y}:alpha='1'[t1]; "
            f"[t1]drawtext=fontfile='fonts/montserrat.ttf':textfile='{t_author_esc}':fontcolor=white:fontsize=65:x=(w-text_w)/2:y={author_y}:alpha='1'[t2]; "
            f"[t2]drawtext=fontfile='fonts/Montserrat-Bold.ttf':textfile='{t_sheets_esc}':fontcolor=white:fontsize=78:x=(w-text_w)/2:y=2150:alpha='if(lt(t,3),0,if(lt(t,4),t-3,if(lt(t,5.8),1,if(lt(t,6.3),(6.3-t)/0.5,0))))'[{'t2_sheets' if morph_text else 't3'}]; "
            + (f"[t2_sheets]drawtext=fontfile='fonts/Montserrat-Bold.ttf':textfile='{t_morph_esc}':fontcolor=white:fontsize=78:x=(w-text_w)/2:y=2150:alpha='if(lt(t,6.3),0,if(lt(t,6.8),(t-6.3)/0.5,if(lt(t,8.8),1,if(lt(t,9.3),(9.3-t)/0.5,0))))'[t3]; " if morph_text else "") +
            f"[t3]drawtext=fontfile='fonts/montserrat.ttf':textfile='{t_wm_esc}':fontcolor=white@0.25:fontsize=48:x=(w-text_w)/2:y=h-text_h-100:alpha='1'[t4]; "

            
            # Outro gets drawn as two separate centered lines to ensure perfect alignment
            f"[t4]drawtext=fontfile='fonts/arno_pro.ttf':textfile='{t_outro1_esc}':fontcolor=white:fontsize=80:x=(w-text_w)/2:y=1000:enable='between(t,{outro_start},{new_duration})':alpha='if(lt(t,{outro_start}),0,min(1,(t-{outro_start})/1))'[t5]; "
            f"[t5]drawtext=fontfile='fonts/arno_pro.ttf':textfile='{t_outro2_esc}':fontcolor=white:fontsize=80:x=(w-text_w)/2:y=1120:enable='between(t,{outro_start},{new_duration})':alpha='if(lt(t,{outro_start}),0,min(1,(t-{outro_start})/1))'[with_outro]; "
            
            f"[with_outro]fade=t=out:st={fade_start}:d=1.5[v_fade]; "
            f"[v_fade]setsar=1,format=yuv420p[final_v]; "
            f"[{a_stream}]afade=t=out:st={fade_start}:d=1.5[final_a]"
        )
        
        output_dir = tiktok_dir
        os.makedirs(output_dir, exist_ok=True)
        raw_name = os.path.splitext(os.path.basename(args.video))[0]
        clean_name = raw_name[:-4] if raw_name.lower().endswith("_raw") else raw_name
        out_base = args.title.strip() if (args.title and args.title.strip()) else clean_name
        output_name = f"{out_base}.mp4" if not out_base.lower().endswith(".mp4") else out_base
        output_path = os.path.join(output_dir, output_name)
        
        # Inject TikTok Cover at the very end of the video ONLY for Hochkant / Portrait (9:16) videos (Currently disabled via ENABLE_LAST_FRAME_COVER)
        ENABLE_LAST_FRAME_COVER = False  # Set to True when TikTok developer app audit is completed!
        cover_path = os.path.join(covers_dir, f"{out_base}.jpg")
        if ENABLE_LAST_FRAME_COVER and not args.wide and os.path.exists(cover_path):
            extra_inputs.extend(["-loop", "1", "-t", str(new_duration), "-i", cover_path])
            cover_idx = current_input_idx
            current_input_idx += 1
            cover_t_start = max(0.0, new_duration - 0.2)
            filter_complex += f"; [{cover_idx}:v]scale=1440:-1[cover_scaled]; "
            filter_complex += f"[pre_cover][cover_scaled]overlay=(main_w-overlay_w)/2:(main_h-overlay_h)/2:enable='between(t,{cover_t_start:.4f},{new_duration:.4f})'[with_cover]; "
            filter_complex += f"[with_cover]setsar=1,format=yuv420p[final_v]"
            filter_complex = filter_complex.replace("[v_fade]setsar=1,format=yuv420p[final_v];", "[v_fade]setsar=1,format=yuv420p[pre_cover];")
    
    tmp_output_path = output_path + ".tmp.mp4"

    try:
        nv_check = subprocess.run([get_ffmpeg_exe(), "-h", "encoder=h264_nvenc"], capture_output=True, text=True)
        if "h264_nvenc" in nv_check.stdout:
            target_br = "6.5M" if args.wide else "5M"
            max_br = "9M" if args.wide else "7M"
            buf_sz = "18M" if args.wide else "14M"
            print(f"Rendering with GPU NVENC h264_nvenc (High-Quality P6, Target {target_br}, MaxBitrate {max_br})...")
            vcodec_args = [
                "-c:v", "h264_nvenc",
                "-preset", "p6",
                "-tune", "hq",
                "-rc", "vbr",
                "-cq", "22",
                "-b:v", target_br,
                "-maxrate:v", max_br,
                "-bufsize:v", buf_sz,
                "-spatial-aq", "1",
                "-temporal-aq", "1",
                "-pix_fmt", "yuv420p"
            ]
        else:
            print("Rendering with multi-threaded CPU libx264 CRF 20 (yuv420p)...")
            vcodec_args = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p"]
    except Exception:
        vcodec_args = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p"]

    # Build ffmpeg command directly as a list
    cmd = [
        get_ffmpeg_exe(), "-y", "-hide_banner",
        "-i", input_video_path
    ] + extra_inputs + [
        "-filter_complex", filter_complex,
        "-map", "[final_v]", "-map", "[final_a]"
    ] + vcodec_args + [
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
