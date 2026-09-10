import os
import sys
import subprocess
import argparse
import cv2
import numpy as np
from scipy.interpolate import make_interp_spline
import random
import math
import time
import threading
import multiprocessing
from concurrent.futures import ProcessPoolExecutor, as_completed

sys.stdout.reconfigure(encoding='utf-8')

TOOL_DIR = os.path.dirname(os.path.abspath(__file__))

local_ffmpeg_bin = os.path.join(TOOL_DIR, "ffmpeg", "bin")
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

def extract_audio(video_path, out_wav):
    cmd = [get_ffmpeg_exe(), "-y", "-i", video_path, "-vn", "-acodec", "pcm_s16le", "-ar", "44100", "-ac", "1", out_wav]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def parse_midi_beats(midipath, delay, fps):
    """Parse MIDI beats respecting tempo changes (ritardando/accelerando)."""
    beat_frames_set = set()
    try:
        import mido
        mid = mido.MidiFile(midipath)
        ticks_per_beat = mid.ticks_per_beat
        current_tick, current_tempo, tiempo_sec, next_beat_tick = 0, 500000, 0.0, 0
        beat_times = []
        for msg in mid.merged_track:
            delta_sec = mido.tick2second(msg.time, ticks_per_beat, current_tempo)
            while next_beat_tick <= current_tick + msg.time:
                if next_beat_tick == 0:
                    beat_times.append(tiempo_sec)
                else:
                    beat_times.append(tiempo_sec + mido.tick2second(next_beat_tick - current_tick, ticks_per_beat, current_tempo))
                next_beat_tick += ticks_per_beat
            tiempo_sec += delta_sec
            current_tick += msg.time
            if msg.type == 'set_tempo':
                current_tempo = msg.tempo
        beat_frames_set = set([int((t + delay) * fps) for t in beat_times])
        print(f"  Parsed {len(beat_frames_set)} beats from MIDI (tempo-aware).")
    except Exception as e:
        print(f"Warning: MIDI beat parse failed: {e}")
    return beat_frames_set

def parse_midi_notes_for_visualizer(midipath, delay, fps, total_frames):
    midi_amps = np.zeros((total_frames, 88), dtype=np.float32)
    try:
        import mido
        mid = mido.MidiFile(midipath)
        ticks_per_beat = mid.ticks_per_beat
        current_tick, current_tempo, tiempo_sec = 0, 500000, 0.0
        
        for msg in mid.merged_track:
            delta_sec = mido.tick2second(msg.time, ticks_per_beat, current_tempo)
            tiempo_sec += delta_sec
            current_tick += msg.time
            
            if msg.type == 'set_tempo':
                current_tempo = msg.tempo
            elif msg.type == 'note_on' and msg.velocity > 0:
                note_idx = msg.note - 21
                if 0 <= note_idx < 88:
                    frame_idx = int((tiempo_sec + delay) * fps)
                    if 0 <= frame_idx < total_frames:
                        amp = max(0.4, (msg.velocity / 127.0) * 1.5)
                        if midi_amps[frame_idx, note_idx] < amp:
                            midi_amps[frame_idx, note_idx] = amp
                            
        print(f"  Parsed perfectly synced MIDI notes across {total_frames} frames.")
    except Exception as e:
        print(f"Warning: MIDI notes parse failed: {e}")
    return midi_amps

def build_color_map(w, t_key):
    cm = np.zeros((w, 3), dtype=np.float32)
    for x in range(w):
        ratio = x / w
        if ratio < 0.5:
            f = ratio * 2.0
            if t_key in ['cold', 'ice']:
                cm[x] = [150*(1-f)+240*f, 30*(1-f)+110*f, 10*(1-f)+50*f]
            elif t_key in ['green', 'emerald']:
                cm[x] = [20*(1-f)+50*f, 80*(1-f)+200*f, 10*(1-f)+50*f]
            elif t_key in ['violet', 'purple']:
                cm[x] = [100*(1-f)+200*f, 10*(1-f)+30*f, 80*(1-f)+140*f]
            elif t_key in ['platinum', 'silver']:
                cm[x] = [45*(1-f)+180*f, 45*(1-f)+180*f, 45*(1-f)+180*f]
            else:
                cm[x] = [20*(1-f)+50*f, 40*(1-f)+140*f, 150*(1-f)+240*f]
        else:
            f = (ratio - 0.5) * 2.0
            if t_key in ['cold', 'ice']:
                cm[x] = [240*(1-f)+255*f, 110*(1-f)+230*f, 50*(1-f)+180*f]
            elif t_key in ['green', 'emerald']:
                cm[x] = [50*(1-f)+50*f, 200*(1-f)+255*f, 50*(1-f)+180*f]
            elif t_key in ['violet', 'purple']:
                cm[x] = [200*(1-f)+255*f, 30*(1-f)+100*f, 140*(1-f)+230*f]
            elif t_key in ['platinum', 'silver']:
                cm[x] = [180*(1-f)+255*f, 180*(1-f)+255*f, 180*(1-f)+255*f]
            else:
                cm[x] = [50*(1-f)+150*f, 140*(1-f)+210*f, 240*(1-f)+255*f]
    return cm

# ======================================================================
# MULTI-CORE WORKER FUNCTION: Renders a slice of frames
# ======================================================================
def _render_chunk_worker(
    chunk_idx, start_frame, end_frame, width, height, fps,
    is_wide, is_tutorial, is_midi, theme, amplitudes_all, vis_band_min, vis_band_max, temp_chunk_path,
    prog_array=None, ambient_fx_all=None, portrait_fx_all=None
):
    t_key = theme.lower().strip()
    color_map = build_color_map(width, t_key)
    n_bands = 88

    margin = 15
    x_sparse = np.linspace(margin, width - margin, n_bands)
    x_sparse = np.concatenate(([0], x_sparse, [width]))
    x_dense = np.arange(width)

    fourcc = cv2.VideoWriter_fourcc(*'mp4v')
    out = cv2.VideoWriter(temp_chunk_path, fourcc, fps, (width, height))

    canvas_float = np.zeros((height, width, 3), dtype=np.float32)
    temp_layer = np.zeros((height, width, 3), dtype=np.uint8)

    if is_wide:
        # V4 Widescreen Engine (2560x1440 ambient floating spores + glow)
        for frame_idx in range(start_frame, end_frame):
            amplitudes_scaled = amplitudes_all[frame_idx]
            canvas_float.fill(0)

            frame_particles = ambient_fx_all[frame_idx] if (ambient_fx_all and frame_idx < len(ambient_fx_all)) else []
            for s in frame_particles:
                fade_start = s.get('fade_y', 850)
                fade_factor = max(0.0, 1.0 - (s['y'] - fade_start) / 200.0) if s['y'] > fade_start else 1.0
                al = s['alpha'] * fade_factor

                if al > 0.01 and 0 <= s['x'] < width and 0 <= s['y'] < height:
                    if t_key in ['warm', 'amber', 'gold', 'sunset']:
                        c_fx = (int(40*al + 10), int(180*al + 30), int(255*al + 30))
                    elif t_key in ['violet', 'purple']:
                        c_fx = (int(240*al), int(40*al), int(180*al))
                    elif t_key in ['green', 'emerald']:
                        c_fx = (int(60*al), int(255*al), int(140*al))
                    elif t_key in ['cold', 'ice', 'platinum', 'silver']:
                        c_fx = (int(255*al), int(250*al), int(240*al))
                    else:
                        c_fx = (int(180*al), int(220*al), int(255*al))
                    cv2.circle(canvas_float, (int(s['x']), int(s['y'])), int(s['size']), c_fx, -1, cv2.LINE_AA)

            small = cv2.resize(canvas_float, (width // 2, height // 2), interpolation=cv2.INTER_LINEAR)
            glow_small = cv2.GaussianBlur(small, (11, 11), 0)
            glow = cv2.resize(glow_small, (width, height), interpolation=cv2.INTER_LINEAR)
            final_canvas = canvas_float * 0.9 + glow * 1.3
            out.write(np.clip(final_canvas, 0, 255).astype(np.uint8))

            if prog_array is not None and ((frame_idx - start_frame) % 15 == 0 or frame_idx == end_frame - 1):
                try:
                    prog_array[chunk_idx] = frame_idx - start_frame + 1
                except Exception:
                    pass
    else:
        # V3 Portrait Engine (2560x500 Glowing Baseline + Waterfall Ribbons + Dust Particles + Theme Ambient)
        if t_key in ['cold', 'ice']:
            wf_bright, wf_mid, wf_dark = (255, 200, 120), (200, 80, 20), (30, 10, 0)
            bl_thick, bl_thin = (140, 40, 10), (255, 150, 50)
        elif t_key in ['green', 'emerald']:
            wf_bright, wf_mid, wf_dark = (50, 255, 150), (20, 100, 30), (5, 20, 5)
            bl_thick, bl_thin = (10, 60, 20), (50, 220, 100)
        elif t_key in ['violet', 'purple']:
            wf_bright, wf_mid, wf_dark = (255, 120, 200), (140, 20, 100), (40, 5, 30)
            bl_thick, bl_thin = (80, 10, 60), (255, 100, 220)
        elif t_key in ['platinum', 'silver']:
            wf_bright, wf_mid, wf_dark = (240, 240, 240), (120, 120, 120), (30, 30, 30)
            bl_thick, bl_thin = (60, 60, 60), (220, 220, 220)
        else: # warm, amber, gold, sunset
            wf_bright, wf_mid, wf_dark = (60, 140, 240), (20, 40, 120), (0, 0, 0)
            bl_thick, bl_thin = (15, 30, 90), (40, 100, 210)

        max_history = 20
        history_pts = []
        particles = []
        ambient_snow = []
        ambient_spores = []

        envelope_mult = 55.0 if is_midi else (50.0 if is_tutorial else 9.0)
        envelope_max = 120

        # Pre-warm history_pts for 20 frames before start_frame to ensure continuous waterfall ribbons
        warmup_start = max(0, start_frame - max_history)
        for w_idx in range(warmup_start, start_frame):
            w_amps = amplitudes_all[w_idx]
            edge_l = w_amps[0]
            edge_r = w_amps[-1]
            amp_anchored = np.concatenate(([edge_l], w_amps, [edge_r]))
            envelope_px = np.clip(amp_anchored * envelope_mult, 0, envelope_max)
            y_dense_audio = make_interp_spline(x_sparse, envelope_px, k=3)(x_dense)
            fluid_base = np.sin(x_dense * 0.01) * np.sin(w_idx * 0.03) * 2.0
            y_line = 3.0 + (y_dense_audio + fluid_base)
            pts_line = np.column_stack((x_dense, np.clip(y_line, 0, height - 1))).astype(np.float32)
            history_pts.insert(0, pts_line.copy())
            if len(history_pts) > max_history:
                history_pts.pop()

        for frame_idx in range(start_frame, end_frame):
            amplitudes_scaled = amplitudes_all[frame_idx]
            canvas_float.fill(0)

            # --- WAVE ENVELOPE ---
            edge_l = amplitudes_scaled[0]
            edge_r = amplitudes_scaled[-1]
            amp_anchored = np.concatenate(([edge_l], amplitudes_scaled, [edge_r]))
            envelope_px = np.clip(amp_anchored * envelope_mult, 0, envelope_max)
            y_dense_audio = make_interp_spline(x_sparse, envelope_px, k=3)(x_dense)

            fluid_base = np.sin(x_dense * 0.01) * np.sin(frame_idx * 0.03) * 2.0
            y_line = 3.0 + (y_dense_audio + fluid_base)
            pts_line = np.column_stack((x_dense, np.clip(y_line, 0, height - 1))).astype(np.float32)

            history_pts.insert(0, pts_line.copy())
            if len(history_pts) > max_history:
                history_pts.pop()

            # --- WATERFALL RIBBONS ---
            for i in range(len(history_pts) - 1, 0, -1):
                pts_top = history_pts[i-1].copy()
                pts_bot = history_pts[i].copy()
                fall_offset = (i * 2.2 + (i ** 1.3) * 0.4)
                pts_top[:, 1] += (fall_offset - 2.2)
                pts_bot[:, 1] += fall_offset
                drift = np.sin(i * 0.15 + frame_idx * 0.04) * i * 0.4
                pts_top[:, 0] += drift
                pts_bot[:, 0] += drift

                progress = i / float(max_history)
                if progress < 0.3:
                    f = progress / 0.3
                    b = wf_bright[0]*(1-f) + wf_mid[0]*f
                    g = wf_bright[1]*(1-f) + wf_mid[1]*f
                    r = wf_bright[2]*(1-f) + wf_mid[2]*f
                else:
                    f = (progress - 0.3) / 0.7
                    b = wf_mid[0]*(1-f) + wf_dark[0]*f
                    g = wf_mid[1]*(1-f) + wf_dark[1]*f
                    r = wf_mid[2]*(1-f) + wf_dark[2]*f
                alpha = (1.0 - progress ** 1.2) * 0.65
                color = (int(b * alpha), int(g * alpha), int(r * alpha))
                poly = np.vstack((pts_top.astype(np.int32), pts_bot.astype(np.int32)[::-1]))
                temp_layer.fill(0)
                cv2.fillPoly(temp_layer, [poly], color)
                canvas_float += temp_layer.astype(np.float32)

            # --- BASELINE GLOWING LINE ---
            if len(history_pts) > 0:
                pts_core = history_pts[0].astype(np.int32)
                temp_layer.fill(0)
                cv2.polylines(temp_layer, [pts_core], False, bl_thick, 16, cv2.LINE_AA)
                cv2.polylines(temp_layer, [pts_core], False, bl_thin, 6, cv2.LINE_AA)
                canvas_float += temp_layer.astype(np.float32) * 0.8

            # --- DUST PARTICLES (Shooting down from vibrating notes on y_line) ---
            for i in range(n_bands):
                amp = amplitudes_scaled[i]
                if amp > 0.12:
                    x_pos = int(x_sparse[i + 1])
                    c_base = color_map[x_pos]
                    if random.random() < amp * 0.3:
                        particles.append({
                            'x': x_pos + random.uniform(-40, 40),
                            'y': y_line[x_pos] + random.uniform(-5, 15),
                            'life': 1.0, 'decay': random.uniform(0.01, 0.025),
                            'size': random.uniform(0.5, 1.8), 'color': c_base
                        })

            t = frame_idx * 0.02
            audio_push = float(np.mean(amplitudes_scaled)) * 8.0
            for p in particles:
                vx = np.sin(p['y'] * 0.015 + t) * np.cos(p['x'] * 0.01 - t * 1.2) * 4.0
                vy = (0.5 + audio_push) + np.sin(p['x'] * 0.02 + t * 0.8) * 1.5
                p['x'] += vx; p['y'] += vy; p['life'] -= p['decay']
                if p['life'] > 0 and 0 <= p['x'] < width and p['y'] < height:
                    a = p['life'] ** 1.5
                    c = p['color']
                    pc = (float(c[0]*a*1.5), float(c[1]*a*1.5), float(c[2]*a*1.5))
                    tx, ty = int(p['x'] - vx), int(p['y'] - vy)
                    cv2.line(canvas_float, (int(p['x']), int(p['y'])), (tx, ty),
                             pc, int(max(1, p['size'])), cv2.LINE_AA)
            particles = [p for p in particles if p['life'] > 0 and p['y'] < height]

            # --- SPECIAL: Snowfall / Ash (cold and platinum only) ---
            if t_key in ('cold', 'ice', 'platinum', 'silver'):
                if random.random() < 0.15:
                    ambient_snow.append({
                        'x': random.uniform(0, width), 'y': random.uniform(-10, 0),
                        'vx': random.uniform(-0.3, 0.3) if t_key in ('cold', 'ice') else random.uniform(-0.2, 0.2),
                        'vy': random.uniform(0.4, 0.9) if t_key in ('cold', 'ice') else random.uniform(0.3, 0.7),
                        'size': random.uniform(0.5, 1.5)
                    })
                for s in ambient_snow:
                    if t_key in ('cold', 'ice'):
                        s['x'] += s['vx'] + np.sin(frame_idx * 0.03 + s['y'] * 0.02) * 0.5
                    else:
                        s['x'] += s['vx'] + np.sin(frame_idx * 0.02 + s['y'] * 0.02) * 0.3
                    s['y'] += s['vy']
                    if 0 <= s['x'] < width and 0 <= s['y'] < height:
                        col_bgr = (255, 250, 240) if t_key in ('cold', 'ice') else (200, 200, 200)
                        cv2.circle(canvas_float, (int(s['x']), int(s['y'])),
                                   int(s['size']), col_bgr, -1, cv2.LINE_AA)
                ambient_snow = [s for s in ambient_snow if s['y'] < height]

            # --- SPECIAL: Pulsating Spores (green and violet only) ---
            if t_key in ('green', 'emerald', 'violet', 'purple'):
                if random.random() < 0.15:
                    ambient_spores.append({
                        'x': random.uniform(0, width), 'y': random.uniform(-10, 0),
                        'vx': random.uniform(-0.3, 0.3), 'vy': random.uniform(0.5, 1.2),
                        'size': random.uniform(0.8, 2.5),
                        'phase': random.uniform(0, math.pi * 2)
                    })
                for s in ambient_spores:
                    fvx, fvy = 0, 0
                    if 0 <= s['x'] < width and 0 <= s['y'] < height:
                        fvx = np.sin(s['y'] * 0.015 + t) * np.cos(s['x'] * 0.01 - t * 1.2) * 2.0
                        fvy = (audio_push * 0.3) + np.sin(s['x'] * 0.02 + t * 0.8) * 0.5
                    s['x'] += s['vx'] + fvx + np.sin(frame_idx * 0.03 + s['phase']) * 0.8
                    s['y'] += s['vy'] + fvy
                    if 0 <= s['x'] < width and 0 <= s['y'] < height:
                        gi = (np.sin(frame_idx * 0.08 + s['phase']) + 1) / 2
                        if gi > 0.1:
                            col_bgr = (int(220*gi), int(30*gi), int(150*gi)) if t_key in ('violet', 'purple') else (int(50*gi), int(255*gi), int(120*gi))
                            cv2.circle(canvas_float, (int(s['x']), int(s['y'])),
                                       int(s['size']), col_bgr,
                                       -1, cv2.LINE_AA)
                ambient_spores = [s for s in ambient_spores if s['y'] < height]

            small = cv2.resize(canvas_float, (width // 2, height // 2), interpolation=cv2.INTER_LINEAR)
            glow_small = cv2.GaussianBlur(small, (11, 11), 0)
            glow = cv2.resize(glow_small, (width, height), interpolation=cv2.INTER_LINEAR)
            final_canvas = canvas_float * 0.9 + glow * 1.3
            out.write(np.clip(final_canvas, 0, 255).astype(np.uint8))

            if prog_array is not None and ((frame_idx - start_frame) % 15 == 0 or frame_idx == end_frame - 1):
                try:
                    prog_array[chunk_idx] = frame_idx - start_frame + 1
                except Exception:
                    pass

    out.release()
    return chunk_idx, end_frame - start_frame

def generate_fluid_mask(video_path, duration=30, fps=60, is_tutorial=False, is_wide=False, theme="warm", midipath="", delay=0.0, outmask="", zoom=1.0, shift=0):
    import uuid
    uid = uuid.uuid4().hex[:8]
    temp_dir = os.path.join(TOOL_DIR, "temp")
    os.makedirs(temp_dir, exist_ok=True)
    mask_path = outmask if outmask else os.path.join(temp_dir, f"waves_mask_{uid}.mp4")
    wav_path = os.path.join(temp_dir, f"temp_audio_{uid}.wav")

    print("Extracting Audio for Analysis...")
    extract_audio(video_path, wav_path)
    from scipy.io import wavfile
    sample_rate, audio_data = wavfile.read(wav_path)
    if len(audio_data.shape) > 1:
        audio_data = audio_data[:, 0]
    audio_data = audio_data.astype(np.float32) / 32768.0
    samples_per_frame = sample_rate // fps
    total_frames = int(duration * fps)

    beat_frames_set = None
    if (not is_wide) and is_tutorial and midipath and os.path.exists(midipath):
        print(f"Parsing MIDI beats from '{os.path.basename(midipath)}' for metronome visualizer...")
        beat_frames_set = parse_midi_beats(midipath, delay, fps)

    midi_amps = None
    if (not is_wide) and (not is_tutorial) and midipath and os.path.exists(midipath):
        print(f"Parsing MIDI notes from '{os.path.basename(midipath)}' for perfect piano syncing...")
        midi_amps = parse_midi_notes_for_visualizer(midipath, delay, fps, total_frames)

    n_bands = 88
    min_freq, max_freq = 27.5, 4186
    n_fft = 4096
    freqs = np.fft.rfftfreq(n_fft, 1 / sample_rate)
    bin_edges = np.logspace(np.log10(min_freq), np.log10(max_freq), n_bands + 1)

    SENSITIVITY = 0.14
    NOISE_FLOOR = 0.7
    DECAY = 0.82
    ATTACK = 0.35

    def get_fft_amplitudes(frame_idx):
        start_samp = max(0, frame_idx * samples_per_frame - n_fft // 2)
        end_samp = start_samp + n_fft
        if end_samp > len(audio_data):
            chunk = np.zeros(n_fft)
            actual = len(audio_data) - start_samp
            if actual > 0: chunk[:actual] = audio_data[start_samp:]
        else:
            chunk = audio_data[start_samp:end_samp]
        window = np.hanning(len(chunk))
        spectrum = np.abs(np.fft.rfft(chunk * window))
        amps = np.zeros(n_bands)
        for i in range(n_bands):
            s, e = np.searchsorted(freqs, bin_edges[i]), np.searchsorted(freqs, bin_edges[i+1])
            if e > s: 
                amps[i] = np.mean(spectrum[s:e])
        return amps

    # Dimensions
    if is_wide:
        width, height = 2560, 1440
    else:
        width, height = 2560, 500

    margin = 15
    scale_w = max(1440, int(1440 * zoom))
    x_crop = (scale_w - 1440) // 2 + shift
    x_crop = max(0, min(x_crop, scale_w - 1440))
    vis_x_min = (x_crop / scale_w) * width
    vis_x_max = ((x_crop + 1440) / scale_w) * width
    vis_band_min = max(2, int((vis_x_min - margin) / (width - 2*margin) * n_bands))
    vis_band_max = min(n_bands - 3, int((vis_x_max - margin) / (width - 2*margin) * n_bands))
    if vis_band_min >= vis_band_max:
        vis_band_min, vis_band_max = 2, n_bands - 3
    print(f"  Visible band range: {vis_band_min}-{vis_band_max} (zoom={zoom}, shift={shift})")

    # Precalculate all amplitudes sequentially (fast: ~0.05s)
    print("Precalculating visualizer envelope...")
    amplitudes_all = np.zeros((total_frames, n_bands), dtype=np.float32)
    prev_amplitudes = np.zeros(n_bands)

    for frame_idx in range(total_frames):
        if beat_frames_set is not None:
            is_beat = frame_idx in beat_frames_set
            if is_beat:
                target = np.zeros(n_bands)
                num_peaks = random.randint(1, 4)
                for _ in range(num_peaks):
                    c = random.randint(vis_band_min, vis_band_max)
                    peak_h = random.uniform(0.1, 2.8)
                    if target[c] < peak_h:
                        target[c] = peak_h
                        target[c-1] = peak_h * random.uniform(0.2, 0.6)
                        target[c+1] = peak_h * random.uniform(0.2, 0.6)
                        if c-2 >= 0: target[c-2] = target[c-1] * random.uniform(0.1, 0.4)
                        if c+2 < n_bands: target[c+2] = target[c+1] * random.uniform(0.1, 0.4)
                raw_amps = np.where(target > prev_amplitudes,
                                    prev_amplitudes * (1 - ATTACK) + target * ATTACK,
                                    prev_amplitudes * DECAY)
            else:
                raw_amps = prev_amplitudes * DECAY
            prev_amplitudes = raw_amps.copy()
            amplitudes_all[frame_idx] = np.maximum(0, raw_amps)
        elif midi_amps is not None:
            f_idx = min(frame_idx, len(midi_amps) - 1)
            target = midi_amps[f_idx]
            raw_amps = np.where(target > prev_amplitudes, target, prev_amplitudes * DECAY)
            prev_amplitudes = raw_amps.copy()
            amplitudes_all[frame_idx] = raw_amps
        else:
            raw_amps = get_fft_amplitudes(frame_idx)
            raw_amps = np.where(raw_amps > prev_amplitudes,
                                prev_amplitudes * (1 - ATTACK) + raw_amps * ATTACK,
                                prev_amplitudes * DECAY)
            prev_amplitudes = raw_amps.copy()
            amplitudes_all[frame_idx] = np.maximum(0, (raw_amps * SENSITIVITY - NOISE_FLOOR)) ** 1.5

    ambient_fx_all = None
    if is_wide:
        print("Precalculating seamless fluid particle dynamics across all frames...")
        random.seed(42)
        np.random.seed(42)
        t_key = theme.lower().strip()
        ambient_fx_all = [None] * total_frames
        ambient_fx = []
        for frame_idx in range(total_frames):
            amps = amplitudes_all[frame_idx]
            t_val = frame_idx * 0.02
            peak_amp = float(np.max(amps)) if len(amps) > 0 else 0.0
            audio_push = (peak_amp ** 1.2) * 0.18
            audio_down_boost = audio_push * 0.06

            if random.random() < 0.05:
                sz_range = (1.4, 3.2) if t_key in ['green', 'emerald'] else (1.0, 2.6)
                ambient_fx.append({
                    'x': random.uniform(0, width), 'y': random.uniform(-10, 0),
                    'vx': random.uniform(-0.35, 0.35),
                    'vy': random.uniform(0.25, 0.45),
                    'size': random.uniform(*sz_range),
                    'alpha': random.uniform(0.75, 0.95),
                    'boost_mult': random.uniform(0.3, 1.0),
                    'phase': random.uniform(0, 6.28),
                    'fade_y': random.uniform(750, 1020)
                })

            for s in ambient_fx:
                fluid_vx = s['vx'] + np.sin(s['y'] * 0.002 + t_val * 0.3 + s.get('phase', 0)) * 0.15
                fluid_vy = (audio_push * 0.015) + np.sin(s['x'] * 0.02 + t_val * 0.8) * 0.03
                s['x'] += fluid_vx
                s['y'] += s['vy'] + fluid_vy + (audio_down_boost * s.get('boost_mult', 1.0))

            ambient_fx = [s for s in ambient_fx if s['y'] < height and (s['y'] <= s.get('fade_y', 1000) + 220)]
            ambient_fx_all[frame_idx] = [dict(s) for s in ambient_fx]

    # Determine optimal worker count (utilize multi-core CPU)
    num_workers = min(12, max(4, os.cpu_count() or 8))
    chunk_size = int(math.ceil(total_frames / num_workers))
    print(f"Parallelizing Visualizer Render: {total_frames} frames across {num_workers} CPU cores...")

    chunks = []
    chunk_files = []
    for i in range(num_workers):
        sf = i * chunk_size
        ef = min(total_frames, (i + 1) * chunk_size)
        if sf >= total_frames:
            break
        c_path = os.path.join(temp_dir, f"temp_mask_chunk_{uid}_{i}.mp4")
        chunk_files.append(c_path)
        chunks.append((i, sf, ef, c_path))

    t_start = time.time()
    manager = multiprocessing.Manager()
    prog_array = manager.Array('i', [0] * num_workers)
    stop_monitor = threading.Event()

    def monitor_progress():
        last_pct = -1
        t0 = time.time()
        while not stop_monitor.is_set():
            time.sleep(1.0)
            try:
                done = sum(prog_array)
                if done > 0 and total_frames > 0:
                    pct = min(99, int((done / total_frames) * 100))
                    if pct != last_pct and pct > 0:
                        elapsed = time.time() - t0
                        fps_val = done / max(0.1, elapsed)
                        rem_sec = int((total_frames - done) / max(0.1, fps_val))
                        mins, secs = divmod(rem_sec, 60)
                        print(f"VIS_PROGRESS:{pct}% ({done}/{total_frames} frames - {fps_val:.1f} FPS - ~{mins}m {secs:02d}s remaining)", flush=True)
                        last_pct = pct
            except Exception:
                pass

    mon_t = threading.Thread(target=monitor_progress, daemon=True)
    mon_t.start()

    is_midi = (midi_amps is not None)
    with ProcessPoolExecutor(max_workers=num_workers) as executor:
        futures = {
            executor.submit(
                _render_chunk_worker,
                c_idx, sf, ef, width, height, fps,
                is_wide, is_tutorial, is_midi, theme, amplitudes_all, vis_band_min, vis_band_max, c_path, prog_array, ambient_fx_all
            ): (c_idx, ef - sf) for c_idx, sf, ef, c_path in chunks
        }

        for future in as_completed(futures):
            c_idx, num_f = futures[future]
            try:
                future.result()
            except Exception as e:
                print(f"Error in visualizer chunk {c_idx}: {e}")

    stop_monitor.set()
    mon_t.join(timeout=1.0)
    print("VIS_PROGRESS:100%", flush=True)

    # Concatenate chunk videos instantly with FFmpeg concat demuxer
    concat_list_path = os.path.join(temp_dir, f"temp_concat_{uid}.txt")
    with open(concat_list_path, "w", encoding="utf-8") as f:
        for cf in chunk_files:
            if os.path.exists(cf):
                clean_p = cf.replace("\\", "/")
                f.write(f"file '{clean_p}'\n")

    concat_cmd = [
        get_ffmpeg_exe(), "-y", "-f", "concat", "-safe", "0",
        "-i", concat_list_path, "-c", "copy", mask_path
    ]
    subprocess.run(concat_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # Cleanup temporary files
    for cf in chunk_files:
        try: os.remove(cf)
        except: pass
    try: os.remove(concat_list_path)
    except: pass
    try: os.remove(wav_path)
    except: pass

    elapsed = time.time() - t_start
    print(f"VIS_PROGRESS:100%", flush=True)
    print(f"Visualizer Mask Ready in {elapsed:.1f}s ({total_frames / max(0.1, elapsed):.1f} FPS)! Saved at: {mask_path}")
    return mask_path

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--video", required=True)
    parser.add_argument("--type", choices=['normal', 'tutorial'], default='normal')
    parser.add_argument("--wide", action="store_true")
    parser.add_argument("--theme", type=str, default="warm")
    parser.add_argument("--midipath", type=str, default="")
    parser.add_argument("--delay", type=float, default=0.0)
    parser.add_argument("--outmask", type=str, default="")
    parser.add_argument("--zoom", type=float, default=1.0)
    parser.add_argument("--shift", type=int, default=0)
    args = parser.parse_args()

    # Get duration from video
    cmd_dur = [get_ffprobe_exe(), "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", args.video]
    res = subprocess.run(cmd_dur, capture_output=True, text=True)
    duration = float(res.stdout.strip()) if res.stdout.strip() else 30.0

    mask_path = generate_fluid_mask(
        video_path=args.video, duration=duration, fps=60,
        is_tutorial=(args.type == 'tutorial'), is_wide=args.wide,
        theme=args.theme, midipath=args.midipath, delay=args.delay,
        outmask=args.outmask, zoom=args.zoom, shift=args.shift
    )
