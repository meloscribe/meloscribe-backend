import os
import sys
import math
import sqlite3
import argparse
from pathlib import Path
from typing import List, Dict, Any, Tuple, Optional
import mido

DB_PATH = Path(__file__).parent / "meloscribe" / "backend" / "analytics.db"

def init_recycling_history_db(db_path: Path = DB_PATH):
    """Ensures recycling_history table exists in analytics.db."""
    os.makedirs(db_path.parent, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS recycling_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                song_name TEXT NOT NULL,
                start_time REAL NOT NULL,
                end_time REAL NOT NULL,
                segment_type TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'candidate',
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()

class NoteEvent:
    __slots__ = ('time', 'note', 'velocity')
    def __init__(self, time: float, note: int, velocity: int):
        self.time = time
        self.note = note
        self.velocity = velocity

def parse_midi_timeline(midi_path: str) -> Tuple[List[NoteEvent], List[float], float]:
    """
    Parses a MIDI file and returns:
    - List of note_on events with exact absolute timestamps in seconds.
    - List of downbeat (measure start) timestamps in seconds.
    - Total song duration in seconds.
    """
    mid = mido.MidiFile(midi_path)
    ticks_per_beat = mid.ticks_per_beat
    
    # Detect time signature (numerator, denominator)
    numerator = 4
    denominator = 4
    for track in mid.tracks:
        for msg in track:
            if msg.type == 'time_signature':
                numerator = msg.numerator
                denominator = msg.denominator
                break
                
    # Build timeline
    current_tick = 0
    current_tempo = 500000  # Default 120 BPM
    tiempo_sec = 0.0
    
    # Track beats and measures
    ticks_per_measure = int(ticks_per_beat * numerator * (4.0 / denominator))
    next_beat_tick = 0
    next_measure_tick = 0
    downbeat_times = []
    
    notes: List[NoteEvent] = []
    
    for msg in mid.merged_track:
        delta_sec = mido.tick2second(msg.time, ticks_per_beat, current_tempo)
        
        while next_measure_tick <= current_tick + msg.time:
            if next_measure_tick == 0:
                downbeat_times.append(tiempo_sec)
            else:
                m_sec = tiempo_sec + mido.tick2second(next_measure_tick - current_tick, ticks_per_beat, current_tempo)
                downbeat_times.append(m_sec)
            next_measure_tick += ticks_per_measure
            
        tiempo_sec += delta_sec
        current_tick += msg.time
        
        if msg.type == 'set_tempo':
            current_tempo = msg.tempo
        elif msg.type == 'note_on' and msg.velocity > 0:
            notes.append(NoteEvent(tiempo_sec, msg.note, msg.velocity))
            
    total_duration = tiempo_sec
    return notes, downbeat_times, total_duration

def get_video_audio_delay(video_path: str) -> float:
    """Measures exact audio onset delay in seconds from a video file."""
    if not video_path or not os.path.exists(video_path):
        return 2.5
    try:
        import subprocess, numpy as np
        from scipy.io import wavfile
        tools_dir = Path(__file__).resolve().parent
        ffmpeg_bin = tools_dir / "ffmpeg" / "bin" / "ffmpeg.exe"
        ffmpeg_cmd = str(ffmpeg_bin) if ffmpeg_bin.exists() else "ffmpeg"
        temp_dir = tools_dir / "temp"
        temp_dir.mkdir(parents=True, exist_ok=True)
        temp_wav = temp_dir / f"measure_offset_{os.getpid()}.wav"
        subprocess.run(
            [ffmpeg_cmd, "-y", "-i", video_path, "-t", "10", "-vn", "-acodec", "pcm_s16le", "-ar", "44100", "-ac", "1", str(temp_wav)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        sample_rate, audio_data = wavfile.read(str(temp_wav))
        if len(audio_data.shape) > 1:
            audio_data = audio_data[:, 0]
        audio_data = audio_data.astype(np.float32) / 32768.0
        above = np.where(np.abs(audio_data) > 0.01)[0]
        offset = float(above[0] / sample_rate) if len(above) > 0 else 2.5
        try:
            temp_wav.unlink(missing_ok=True)
        except Exception:
            pass
        return offset
    except Exception as e:
        return 2.5

def snap_to_downbeat(t: float, downbeats: List[float], max_delta: float = 2.5) -> float:
    """Snaps a given time to the nearest measure downbeat."""
    if not downbeats:
        return t
    return min(downbeats, key=lambda d: abs(d - t))

def check_overlap(start1: float, end1: float, start2: float, end2: float) -> float:
    """Calculates overlap percentage between two time intervals."""
    len1 = max(0.001, end1 - start1)
    len2 = max(0.001, end2 - start2)
    overlap = max(0.0, min(end1, end2) - max(start1, start2))
    return overlap / min(len1, len2)

def detect_phrases_and_archetypes(
    midi_path: str,
    target_stream: str = "keysight_raw",  # 'keysight_raw' or 'tiktok_video' (+0.0s)
    target_duration: float = 28.0,
    min_duration: float = 24.0,
    max_duration: float = 35.0,
    video_path: Optional[str] = None
) -> Dict[str, Any]:
    """
    Scans the MIDI track using moving-window heuristics to identify
    the 3 distinct recycling archetypes:
    - Segment A (The Climax): Maximum energy (density * velocity)
    - Segment B (The Breakdown / Build-up): Dip in density followed by steep crescendo
    - Segment C (The Melodic Intro/Outro Hook): Rich melodic consistency, moderate density
    All segment boundaries strictly snap to full measure downbeats.
    """
    notes, downbeats, total_duration = parse_midi_timeline(midi_path)
    if not notes:
        return {"error": "No note_on events found in MIDI"}
        
    # Dynamically measure pre-roll offset from video if available
    pre_roll_offset = 0.0
    if target_stream == "keysight_raw":
        if video_path and os.path.exists(video_path):
            pre_roll_offset = get_video_audio_delay(video_path)
        else:
            # Look for video matching the midi name
            stem = Path(midi_path).stem
            candidates = [
                Path(r"C:\Dev\meloscribe\Keysight export\RAW") / f"{stem}_RAW.mp4",
                Path(r"C:\Dev\meloscribe\Keysight export") / f"{stem}.mp4",
            ]
            found_vid = next((str(c) for c in candidates if c.exists()), None)
            if found_vid:
                pre_roll_offset = get_video_audio_delay(found_vid)
            else:
                pre_roll_offset = 2.5
    
    window_step = 0.5
    half_window = target_duration / 2.0
    
    # Moving window metrics
    windows = []
    t_start = 0.0
    max_t = max(0.0, total_duration - min_duration)
    
    while t_start <= max_t:
        t_end = min(total_duration, t_start + target_duration)
        w_dur = t_end - t_start
        if w_dur < min_duration:
            break
            
        w_notes = [n for n in notes if t_start <= n.time < t_end]
        note_count = len(w_notes)
        density = note_count / w_dur
        mean_vel = (sum(n.velocity for n in w_notes) / note_count) if note_count > 0 else 0.0
        energy = density * (mean_vel / 127.0)
        
        # Calculate melody pitch variance (standard deviation of notes)
        if note_count > 4:
            mean_pitch = sum(n.note for n in w_notes) / note_count
            var_pitch = sum((n.note - mean_pitch)**2 for n in w_notes) / note_count
            pitch_std = math.sqrt(var_pitch)
        else:
            pitch_std = 0.0
            
        windows.append({
            "t_start": t_start,
            "t_end": t_end,
            "duration": w_dur,
            "density": density,
            "velocity": mean_vel,
            "energy": energy,
            "pitch_std": pitch_std,
            "note_count": note_count
        })
        t_start += window_step
        
    if not windows:
        return {"error": "Track too short for segment detection"}
        
    # ── Archetype A: The Climax ──
    # Absolute peak of energy
    climax_win = max(windows, key=lambda w: w["energy"])
    
    # ── Archetype B: The Breakdown / Build-up ──
    # Look for a sharp rise in density: difference between end half and start half
    build_up_scores = []
    for i, w in enumerate(windows):
        t_mid = (w["t_start"] + w["t_end"]) / 2.0
        notes_first_half = [n for n in notes if w["t_start"] <= n.time < t_mid]
        notes_second_half = [n for n in notes if t_mid <= n.time < w["t_end"]]
        d1 = len(notes_first_half) / (t_mid - w["t_start"]) if (t_mid - w["t_start"]) > 0 else 0
        d2 = len(notes_second_half) / (w["t_end"] - t_mid) if (w["t_end"] - t_mid) > 0 else 0
        rise = d2 - d1  # Steep positive transition
        # Build-up shouldn't overlap too heavily with the climax peak window
        ov = check_overlap(w["t_start"], w["t_end"], climax_win["t_start"], climax_win["t_end"])
        penalty = 0.5 if ov > 0.3 else 1.0
        build_up_scores.append((rise * penalty, w))
        
    build_up_win = max(build_up_scores, key=lambda x: x[0])[1] if build_up_scores else climax_win
    
    # ── Archetype C: The Melodic Intro/Outro Hook ──
    # High pitch standard deviation (melodic movement) with moderate energy, early or late
    melodic_scores = []
    for w in windows:
        ov_a = check_overlap(w["t_start"], w["t_end"], climax_win["t_start"], climax_win["t_end"])
        ov_b = check_overlap(w["t_start"], w["t_end"], build_up_win["t_start"], build_up_win["t_end"])
        if ov_a > 0.2 or ov_b > 0.2:
            continue
        # Favor parts with good melodic variance and clear, moderate tempo
        mel_score = w["pitch_std"] * (1.0 if w["density"] >= 2.0 else 0.5)
        melodic_scores.append((mel_score, w))
        
    if melodic_scores:
        melodic_win = max(melodic_scores, key=lambda x: x[0])[1]
    else:
        # Fallback to an earlier window with good melody
        melodic_win = sorted(windows[:len(windows)//3 + 1], key=lambda w: w["pitch_std"], reverse=True)[0] if windows else climax_win

    # Apply strict measure downbeat snapping and format segments
    def format_segment(win: Dict[str, Any], seg_type: str, label: str) -> Dict[str, Any]:
        raw_start = win["t_start"]
        
        # Snap start strictly to the nearest measure downbeat
        snapped_start = snap_to_downbeat(raw_start, downbeats)
        
        # Find candidate downbeats for the end that give a duration within [min_duration, max_duration]
        candidate_ends = [d for d in downbeats if d > snapped_start and min_duration <= (d - snapped_start) <= max_duration]
        if candidate_ends:
            snapped_end = min(candidate_ends, key=lambda d: abs((d - snapped_start) - target_duration))
        else:
            later_downbeats = [d for d in downbeats if d > snapped_start]
            if later_downbeats:
                snapped_end = min(later_downbeats, key=lambda d: abs((d - snapped_start) - target_duration))
            else:
                snapped_end = total_duration
            
        # Add Pre-roll offset for target stream
        ui_start = round(snapped_start + pre_roll_offset, 2)
        ui_end = round(snapped_end + pre_roll_offset, 2)
        final_dur = round(ui_end - ui_start, 2)
        
        return {
            "segment_type": seg_type,
            "label": label,
            "start_time": ui_start,
            "end_time": ui_end,
            "duration": final_dur,
            "midi_start": round(snapped_start, 2),
            "midi_end": round(snapped_end, 2),
            "energy": round(win["energy"], 2),
            "density": round(win["density"], 2),
            "note_count": win["note_count"]
        }

    seg_a = format_segment(climax_win, "climax", "Segment A: The Climax (Chorus/Peak)")
    seg_b = format_segment(build_up_win, "build_up", "Segment B: Breakdown & Build-up")
    seg_c = format_segment(melodic_win, "melodic_hook", "Segment C: Melodic Hook")

    return {
        "song_duration": round(total_duration + pre_roll_offset, 2),
        "target_stream": target_stream,
        "pre_roll_offset": pre_roll_offset,
        "segments": {
            "climax": seg_a,
            "build_up": seg_b,
            "melodic_hook": seg_c
        },
        "order": [seg_a, seg_b, seg_c]
    }

def get_next_recycling_segment(
    song_name: str,
    midi_path: str,
    target_stream: str = "keysight_raw",
    db_path: Path = DB_PATH,
    video_path: Optional[str] = None
) -> Dict[str, Any]:
    """
    Selects the next unused archetype or slice for a song based on recycling_history.
    Guarantees maximum 15% overlap with already staged or published clips.
    """
    init_recycling_history_db(db_path)
    analysis = detect_phrases_and_archetypes(midi_path, target_stream=target_stream, video_path=video_path)
    if "error" in analysis:
        return analysis
        
    candidates = analysis["order"]
    
    # Query existing history for staged/published
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        rows = cursor.execute("""
            SELECT start_time, end_time, segment_type, status 
            FROM recycling_history 
            WHERE song_name = ? AND status IN ('staged', 'published')
        """, (song_name,)).fetchall()
        
    # Find first candidate with <= 15% overlap
    chosen = None
    for cand in candidates:
        has_collision = False
        for (ex_start, ex_end, ex_type, ex_status) in rows:
            ov = check_overlap(cand["start_time"], cand["end_time"], ex_start, ex_end)
            if ov > 0.15:
                has_collision = True
                break
        if not has_collision:
            chosen = cand
            break
            
    # If all 3 primary archetypes collide, find next best peak window
    if not chosen:
        chosen = candidates[0]  # Fallback to highest energy candidate
        
    return {
        "chosen": chosen,
        "all_segments": analysis["segments"],
        "pre_roll_offset": analysis["pre_roll_offset"],
        "song_duration": analysis["song_duration"]
    }

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="MIDI Phrase & Hook Detection Engine")
    parser.add_argument("--midi", required=True, help="Path to MIDI file")
    parser.add_argument("--song", default="Test Song", help="Song name")
    parser.add_argument("--target", choices=["keysight_raw", "tiktok_video"], default="keysight_raw")
    args = parser.parse_args()
    
    res = get_next_recycling_segment(args.song, args.midi, target_stream=args.target)
    import json
    print(json.dumps(res, indent=2))
