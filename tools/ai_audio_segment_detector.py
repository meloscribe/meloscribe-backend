#!/usr/bin/env python3
"""
AI Multimodal Audio Hook & Segment Detector for MeloScribe.
Extracts compressed audio from the Keysight video and utilizes Gemini 2.5 Flash
with multimodal audio listening to accurately identify the 1-3 best musical hooks/choruses,
avoiding mathematical density traps (e.g. 16th-note verse arpeggios in 'River Flows in You').
Snaps detected boundaries to exact measure downbeats using MIDI timeline analysis.
"""

import os
import sys
import re
import json
import time
import shutil
import logging
import argparse
import subprocess
from pathlib import Path
from typing import List, Dict, Any, Optional

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR.parent) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR.parent))
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import google.generativeai as genai
try:
    from tools.midi_hook_detector import (
        parse_midi_timeline,
        get_video_audio_delay,
        snap_to_downbeat,
        detect_phrases_and_archetypes
    )
except ImportError:
    from midi_hook_detector import (
        parse_midi_timeline,
        get_video_audio_delay,
        snap_to_downbeat,
        detect_phrases_and_archetypes
    )

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("meloscribe.ai_audio_segment_detector")

SETTINGS_FILE = TOOLS_DIR / "meloscribe" / "backend" / "settings.json"
FFMPEG_BIN = TOOLS_DIR / "ffmpeg" / "bin" / "ffmpeg.exe"
FFMPEG_CMD = str(FFMPEG_BIN) if FFMPEG_BIN.exists() else "ffmpeg"
TEMP_DIR = TOOLS_DIR / "temp"

def load_settings() -> Dict[str, Any]:
    if SETTINGS_FILE.exists():
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"Failed to load settings.json: {e}")
    return {}

def get_gemini_key() -> str:
    settings = load_settings()
    key = settings.get("gemini_api_key")
    if key:
        return key.strip()
    return os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or ""

def resolve_video_and_midi(
    song_name: str,
    video_path: Optional[str] = None,
    midi_path: Optional[str] = None
) -> tuple[Optional[str], Optional[str]]:
    """Resolves Keysight video and Cakewalk MIDI paths for a given song."""
    clean_song = song_name.strip()
    base_name = re.sub(r'(?i)\s*(slow|easy|normal|tutorial|hook|teaser).*', '', clean_song).strip()
    
    # 1. Video resolution
    resolved_video = video_path
    if not resolved_video or not os.path.exists(resolved_video):
        vid_candidates = [
            Path(r"C:\Dev\meloscribe\Keysight export\RAW") / f"{clean_song}_RAW.mp4",
            Path(r"C:\Dev\meloscribe\Keysight export") / f"{clean_song}.mp4",
            Path(r"C:\Dev\meloscribe\Keysight export\RAW") / f"{base_name}_RAW.mp4",
            Path(r"C:\Dev\meloscribe\Keysight export") / f"{base_name}.mp4",
        ]
        resolved_video = next((str(c) for c in vid_candidates if c.exists()), None)
        
    # 2. MIDI resolution
    resolved_midi = midi_path
    if not resolved_midi or not os.path.exists(resolved_midi):
        midi_candidates = [
            Path(r"C:\Cakewalk Projects") / clean_song / f"{clean_song}.mid",
            Path(r"C:\Cakewalk Projects") / base_name / f"{clean_song}.mid",
            Path(r"C:\Cakewalk Projects") / base_name / f"{base_name}.mid",
            Path(r"C:\Dev\meloscribe\legacy packages") / f"{clean_song}.mid",
            Path(r"C:\Dev\meloscribe\legacy packages") / f"{base_name}.mid",
        ]
        resolved_midi = next((str(c) for c in midi_candidates if c.exists()), None)
        
    return resolved_video, resolved_midi

def extract_compressed_audio(video_path: str, song_name: str) -> Optional[Path]:
    """Extracts a light 64kbps mono MP3 from the video for ultra-fast Gemini upload."""
    TEMP_DIR.mkdir(parents=True, exist_ok=True)
    clean_stem = re.sub(r'[^a-zA-Z0-9_\-]', '_', song_name)
    output_mp3 = TEMP_DIR / f"ai_audio_{clean_stem}_{os.getpid()}.mp3"
    
    cmd = [
        FFMPEG_CMD, "-y",
        "-i", video_path,
        "-vn",
        "-acodec", "libmp3lame",
        "-b:a", "64k",
        "-ar", "22050",
        "-ac", "1",
        str(output_mp3)
    ]
    
    try:
        res = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        if output_mp3.exists() and output_mp3.stat().st_size > 1024:
            return output_mp3
    except Exception as e:
        logger.error(f"FFmpeg audio extraction failed: {e}")
    return None

def snap_segments_to_measures(
    segments: List[Dict[str, Any]],
    midi_path: Optional[str],
    video_path: Optional[str],
    min_dur: float = 20.0,
    max_dur: float = 36.0
) -> List[Dict[str, Any]]:
    """Snaps detected segment timestamps to exact musical measure downbeats via MIDI."""
    if not midi_path or not os.path.exists(midi_path):
        logger.info("No MIDI found; keeping raw AI timestamps.")
        return segments
        
    try:
        notes, downbeats, total_dur = parse_midi_timeline(midi_path)
        if not downbeats:
            return segments
            
        pre_roll = get_video_audio_delay(video_path) if video_path else 2.5
        logger.info(f"Snapping segments with pre-roll offset: {pre_roll:.2f}s ({len(downbeats)} measure downbeats found)")
        
        snapped_list = []
        for seg in segments:
            raw_start = float(seg["start_time"])
            raw_end = float(seg["end_time"])
            
            # Map video timeline -> MIDI timeline
            midi_s = max(0.0, raw_start - pre_roll)
            midi_e = max(0.0, raw_end - pre_roll)
            
            # Snap to downbeats
            snapped_midi_s = snap_to_downbeat(midi_s, downbeats)
            snapped_midi_e = snap_to_downbeat(midi_e, downbeats)
            
            # Ensure duration bounds
            dur = snapped_midi_e - snapped_midi_s
            if dur < min_dur:
                # 1. Try extending end forward
                later = [d for d in downbeats if d - snapped_midi_s >= min_dur]
                if later:
                    snapped_midi_e = later[0]
                else:
                    # 2. If near end of song, shift start backwards to capture climax build-up
                    earlier_starts = [d for d in downbeats if min_dur <= (snapped_midi_e - d) <= max_dur]
                    if earlier_starts:
                        snapped_midi_s = earlier_starts[0]
                    else:
                        before = [d for d in downbeats if d < snapped_midi_e]
                        if before:
                            snapped_midi_s = before[max(0, len(before) - 14)]
            elif dur > max_dur:
                # Find earlier downbeat within max duration
                earlier = [d for d in downbeats if min_dur <= (d - snapped_midi_s) <= max_dur]
                if earlier:
                    snapped_midi_e = earlier[-1]
                    
            v_start = round(snapped_midi_s + pre_roll, 2)
            v_end = round(snapped_midi_e + pre_roll, 2)
            final_dur = round(v_end - v_start, 2)
            
            # Skip invalid or ultra-short segments
            if final_dur < 15.0:
                logger.warning(f"Discarding segment '{seg.get('name')}' with invalid duration {final_dur}s")
                continue

            snapped_seg = dict(seg)
            snapped_seg["start_time"] = v_start
            snapped_seg["end_time"] = v_end
            snapped_seg["duration"] = final_dur
            snapped_seg["snapped_to_measure"] = True
            snapped_list.append(snapped_seg)
            
        return snapped_list
    except Exception as e:
        logger.warning(f"Measure snapping failed: {e}; returning raw segments.")
        return segments

def detect_audio_segments(
    song_name: str,
    author: str = "",
    video_path: Optional[str] = None,
    midi_path: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    Multimodal AI Audio Hook Detector.
    Extracts audio from video, feeds to Gemini 2.5 Flash, and returns 1-3 musically
    coherent hooks snapped to measure downbeats.
    """
    clean_song = song_name.strip()
    v_path, m_path = resolve_video_and_midi(clean_song, video_path, midi_path)
    
    if not v_path:
        logger.warning(f"No video found for song '{clean_song}'. Falling back to MIDI heuristics.")
        if m_path:
            return fallback_to_midi(m_path, v_path)
        return []

    api_key = get_gemini_key()
    if not api_key:
        logger.warning("No Gemini API key found. Falling back to MIDI heuristics.")
        if m_path:
            return fallback_to_midi(m_path, v_path)
        return []

    # 1. Extract lightweight MP3
    logger.info(f"Extracting compressed audio from: {v_path}")
    audio_mp3 = extract_compressed_audio(v_path, clean_song)
    if not audio_mp3:
        logger.warning("Audio extraction failed. Falling back to MIDI heuristics.")
        if m_path:
            return fallback_to_midi(m_path, v_path)
        return []

    remote_file = None
    try:
        genai.configure(api_key=api_key)
        logger.info(f"Uploading audio ({audio_mp3.stat().st_size / 1024:.1f} KB) to Gemini API...")
        remote_file = genai.upload_file(path=str(audio_mp3), mime_type="audio/mp3")
        
        # Wait for file to become active
        retries = 10
        while remote_file.state.name == "PROCESSING" and retries > 0:
            time.sleep(1)
            remote_file = genai.get_file(remote_file.name)
            retries -= 1

        if remote_file.state.name != "ACTIVE":
            logger.warning(f"Audio file state is {remote_file.state.name}. Falling back to MIDI heuristics.")
            if m_path:
                return fallback_to_midi(m_path, v_path)
            return []

        prompt = f"""Du bist ein professioneller Konzertpianist, Musikproduzent und Content-Stratege für virale Klaviervideos (TikTok, Instagram Reels, YouTube Shorts).
Vor dir liegt die Audioaufnahme eines Klavierarrangements des Songs "{clean_song}" von "{author or 'Unknown'}".

DEINE AUFGABE:
Höre dir die Aufnahme genau an und identifiziere die 1 bis maximal 3 musikalisch und emotional stärksten, einprägsamsten Passagen (Hooks, Refrains, ikonische Hauptthemen oder kraftvolle Höhepunkte).

KRITISCHE REGELN GEGEN NOTENDICHTE-TÄUSCHUNG:
1. TÄUSCHE DICH NICHT DURCH ARPEGGIOS: Viele Stücke (wie 'River Flows in You', 'Golden Brown' oder Pop-Balladen) haben in den Strophen oder im Intro schnelle 16tel-Begleitakkorde. Ein mathematischer Notenzähler verwechselt das fälschlicherweise mit Energie. Du hörst jedoch das Audio: Suche nach dem ECHTEN Höhepunkt / der einprägsamen Hauptmelodie (dem Hook/Chorus), bei dem die Melodie am intensivsten ist und den stärksten emotionalen Wiedererkennungswert hat!
2. DAUER: Jedes Segment MUSS eine vollständige musikalische Phrase von 22 bis 35 Sekunden Dauer sein.
3. TIMELINE: Gib die Start- und Endzeiten (start_time und end_time) exakt in Sekunden bezogen auf dieses Audio an.
4. QUALITÄT: Gib nur WIRKLICH starke Segmente zurück (1 bis 3 Segmente). Sortiere sie mit dem besten/wichtigsten Segment an erster Stelle.
5. SEGMENT-TYP: Weise jedem Segment einen der folgenden Typen zu:
   - "climax": Der kraftvollste Refrain / Haupt-Drop / Peak des Stücks.
   - "melodic_hook": Die eingängigste, emotionalste Melodie / das Hauptthema.
   - "build_up": Eine spannungsaufbauende Steigerung (Bridge/Crescendo).

Antworte AUSSCHLIESSLICH im folgenden JSON-Format ohne zusätzliche Erklärungen oder Markdown-Codeblöcke:
[
  {{
    "name": "Chorus 1",
    "segment_type": "climax",
    "start_time": 45.2,
    "end_time": 73.5,
    "reason": "Vollmundiger Refrain mit starker Melodieführung und hohem Wiedererkennungswert"
  }}
]
"""

        logger.info("Calling Gemini 2.5 Flash for multimodal audio hook detection...")
        model = genai.GenerativeModel("models/gemini-2.5-flash")
        resp = model.generate_content([remote_file, prompt])
        
        if not resp or not resp.text:
            logger.warning("Empty response from Gemini audio detector.")
            if m_path:
                return fallback_to_midi(m_path, v_path)
            return []

        raw_text = resp.text.strip()
        clean_json = re.sub(r'^```json\s*', '', raw_text, flags=re.MULTILINE)
        clean_json = re.sub(r'^```\s*', '', clean_json, flags=re.MULTILINE)
        data = json.loads(clean_json.strip())

        if not isinstance(data, list) or len(data) == 0:
            logger.warning("Gemini returned invalid list format.")
            if m_path:
                return fallback_to_midi(m_path, v_path)
            return []

        # Sanitize items
        parsed_segments = []
        for idx, item in enumerate(data[:3]):
            st = float(item.get("start_time", 0.0))
            et = float(item.get("end_time", st + 28.0))
            if et - st < 15.0:
                et = st + 25.0
            parsed_segments.append({
                "id": f"ai_seg_{idx + 1}",
                "name": item.get("name", f"AI Segment {idx + 1}"),
                "segment_type": item.get("segment_type", "climax"),
                "start_time": round(st, 2),
                "end_time": round(et, 2),
                "duration": round(et - st, 2),
                "reason": item.get("reason", "Detected by Gemini Multimodal Audio Model")
            })

        logger.info(f"Gemini detected {len(parsed_segments)} audio hook candidate(s).")
        
        # 4. Snap to musical measure downbeats
        final_segments = snap_segments_to_measures(parsed_segments, m_path, v_path)
        return final_segments

    except Exception as e:
        logger.error(f"Error in multimodal audio detection: {e}")
        if m_path:
            return fallback_to_midi(m_path, v_path)
        return []
    finally:
        # Cleanup remote file and local temp mp3
        if remote_file:
            try:
                genai.delete_file(remote_file.name)
            except Exception:
                pass
        if audio_mp3 and audio_mp3.exists():
            try:
                audio_mp3.unlink(missing_ok=True)
            except Exception:
                pass

def fallback_to_midi(midi_path: str, video_path: Optional[str]) -> List[Dict[str, Any]]:
    """Fallback to classical MIDI phrase detection if AI audio is unavailable."""
    logger.info("Using MIDI timeline phrase detection fallback...")
    res = detect_phrases_and_archetypes(midi_path, target_stream="keysight_raw", video_path=video_path)
    segs = res.get("segments", [])
    out = []
    for s in segs:
        out.append({
            "id": s["id"],
            "name": s["name"],
            "segment_type": s["segment_type"],
            "start_time": s["start_time"],
            "end_time": s["end_time"],
            "duration": s["duration"],
            "reason": s.get("focus_technique", "MIDI velocity & density heuristic")
        })
    return out

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="MeloScribe AI Audio Segment Detector")
    parser.add_argument("--song", type=str, default="Believer", help="Song title")
    parser.add_argument("--author", type=str, default="Imagine Dragons", help="Song artist")
    parser.add_argument("--video", type=str, default=None, help="Explicit video path")
    parser.add_argument("--midi", type=str, default=None, help="Explicit MIDI path")
    args = parser.parse_args()

    results = detect_audio_segments(args.song, args.author, args.video, args.midi)
    print(f"\n=======================================================")
    print(f"Detected {len(results)} AI Audio Hook(s) for '{args.song}' by '{args.author}':")
    print(f"=======================================================")
    for r in results:
        print(f"• [{r['segment_type'].upper()}] {r['name']}")
        print(f"  Time: {r['start_time']}s - {r['end_time']}s (Duration: {r.get('duration', 0)}s)")
        print(f"  Reason: {r.get('reason')}")
        print("-" * 55)
