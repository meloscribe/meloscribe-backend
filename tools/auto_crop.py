import os
import mido
from pathlib import Path

WHITE_NOTES = {
    21, 23, 24, 26, 28, 29, 31, 33, 35, 36, 38, 40, 41, 43, 45, 47, 48, 50,
    52, 53, 55, 57, 59, 60, 62, 64, 65, 67, 69, 71, 72, 74, 76, 77, 79, 81,
    83, 84, 86, 88, 89, 91, 93, 95, 96, 98, 100, 101, 103, 105, 107, 108
}

def get_note_white_index(note: int) -> float:
    """Returns approximate white key index (0..51) for any MIDI note (21..108)."""
    white_count = sum(1 for n in range(21, note) if n in WHITE_NOTES)
    if note in WHITE_NOTES:
        return float(white_count)
    else:
        return float(white_count) + 0.5

def get_midi_note_range(midi_path: str) -> tuple[int, int]:
    """Reads a MIDI file and returns (min_note, max_note)."""
    if not os.path.exists(midi_path):
        return 21, 108
    try:
        mid = mido.MidiFile(midi_path)
        notes = []
        for track in mid.tracks:
            for msg in track:
                if msg.type == 'note_on' and msg.velocity > 0:
                    notes.append(msg.note)
        if not notes:
            return 21, 108
        return min(notes), max(notes)
    except Exception as e:
        print(f"[AutoCrop] Warning reading MIDI '{midi_path}': {e}")
        return 21, 108

def calculate_auto_crop(midi_path: str, left_tolerance_keys: float = 1.5, right_tolerance_keys: float = 6.5, min_zoom: float = 1.0, max_zoom: float = 2.2) -> tuple[float, int]:
    """
    Calculates optimal zoom and horizontal shift ensuring:
    1. The lowest played note has at least `left_tolerance_keys` (default 1.5) margin on the left (never clipped).
    2. The highest played note has at least `right_tolerance_keys` (default 6.5) margin on the right (room for right-hand action & social media UI).
    """
    min_note, max_note = get_midi_note_range(midi_path)
    
    total_white_keys = 52.0
    min_w_idx = get_note_white_index(min_note)
    max_w_idx = get_note_white_index(max_note)
    
    # Left edge target in normalized coordinates [0.0, 1.0]
    target_left = max(0.0, min_w_idx - left_tolerance_keys) / total_white_keys
    # Right edge target in normalized coordinates [0.0, 1.0]
    target_right = min(total_white_keys, max_w_idx + 1.0 + right_tolerance_keys) / total_white_keys
    
    required_span = target_right - target_left
    ideal_zoom = 1.0 / max(0.01, required_span)
    
    zoom = max(min_zoom, min(max_zoom, ideal_zoom))
    zoom = round(zoom, 2)
    
    # Distribute slack (if any) giving ample breathing room to the right for social UI
    visible_span = 1.0 / zoom
    slack = max(0.0, visible_span - required_span)
    target_left_adjusted = max(0.0, target_left - slack * 0.35)
    
    # Calculate shift so that visible left border matches target_left_adjusted
    shift = int(round((target_left_adjusted - 0.5) * 1440 * zoom + 720))
    
    # Prevent shift from pulling in black bars outside the video
    scale_w = int(1440 * zoom)
    max_allowed_shift = (scale_w - 1440) // 2
    shift = max(-max_allowed_shift, min(max_allowed_shift, shift))
    
    return zoom, shift

def calculate_recycling_crop(midi_path: str, jitter: bool = True) -> tuple[float, int, float, int]:
    """
    Calculates auto-crop for content recycling with safe micro-jitter.
    Ensures that the lowest and highest notes are never clipped and no black borders appear,
    while slightly altering the crop matrix (zoom & shift) to bypass duplicate hash filters.
    Returns: (jittered_zoom, jittered_shift, base_zoom, base_shift)
    """
    import random
    base_zoom, base_shift = calculate_auto_crop(midi_path, left_tolerance_keys=1.5, right_tolerance_keys=6.5)
    
    if not jitter:
        return base_zoom, base_shift, base_zoom, base_shift
        
    # Strictly positive zoom jitter: 1.005 to 1.015 (always zoomed in slightly, never < 1.0 to prevent black bars)
    import random
    jitter_factor = round(random.uniform(1.005, 1.015), 4)
    zoom = max(1.005, min(2.5, round(base_zoom * jitter_factor, 2)))
    
    # Safe shift jitter: between -8 and +8 px, strictly clamped to max allowed shift
    scale_w = int(1440 * zoom)
    max_allowed_shift = max(0, (scale_w - 1440) // 2)
    delta_shift = random.randint(-8, 8)
    shift = max(-max_allowed_shift, min(max_allowed_shift, base_shift + delta_shift))
    
    return zoom, shift, base_zoom, base_shift

if __name__ == "__main__":
    test_path = r"C:\Cakewalk Projects\Golden Brown\Golden Brown.mid"
    z, s = calculate_auto_crop(test_path, left_tolerance_keys=1.0, right_tolerance_keys=4.0)
    print(f"Golden Brown -> Base Zoom: {z}x, Shift: {s}px")
    jz, js, bz, bs = calculate_recycling_crop(test_path, jitter=True)
    print(f"Golden Brown -> Recycling Jitter: {jz}x, Shift: {js}px")
