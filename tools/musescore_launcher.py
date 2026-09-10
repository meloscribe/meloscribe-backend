r"""
musescore_launcher.py
─────────────────────
Opens MuseScore for a given song:
  • Window 1 — the song's MIDI file (from Cakewalk Projects)
  • Window 2 — a fresh score pre-populated from the "meloscribe" template
    (copies template → C:\Dev\meloscribe\Scores\{song}.mscz if not already there)

Waits up to 30 minutes for the PDF to appear in musescore_dir, then exits 0.
If no PDF within timeout → exits 1 (fails the batch step).

Skip condition: if {song}.pdf already exists in musescore_dir → exits 0 immediately.
"""
import os
import sys
import time
import shutil
import argparse
import subprocess

sys.stdout.reconfigure(encoding='utf-8')

CREATE_NO_WINDOW = 0x08000000

def find_file_ci(directory, filename):
    """Case-insensitive file search inside a directory."""
    if not os.path.exists(directory):
        return None
    target = filename.lower()
    for f in os.listdir(directory):
        if f.lower() == target:
            return os.path.join(directory, f)
    return None

def get_midi_properties(midi_path):
    bpm = 120.0
    ts_numerator = 4
    ts_denominator = 4
    concert_key = 0
    
    if not midi_path or not os.path.exists(midi_path):
        return bpm, ts_numerator, ts_denominator, concert_key
        
    try:
        import mido
        mid = mido.MidiFile(midi_path)
        
        # 1. Parse standard meta events
        has_midi_key_sig = False
        midi_key_name = 'C'
        for track in mid.tracks:
            for msg in track:
                if msg.type == 'set_tempo':
                    bpm = mido.tempo2bpm(msg.tempo)
                elif msg.type == 'time_signature':
                    ts_numerator = msg.numerator
                    ts_denominator = msg.denominator
                elif msg.type == 'key_signature':
                    midi_key_name = msg.key
                    has_midi_key_sig = True
                    
        # 2. Count pitch classes to verify or auto-detect key signature
        pitch_counts = [0] * 12
        total_notes = 0
        for track in mid.tracks:
            for msg in track:
                if msg.type == 'note_on' and msg.velocity > 0:
                    pitch_class = msg.note % 12
                    pitch_counts[pitch_class] += 1
                    total_notes += 1
                    
        # Key signature pitch sets (concertKey index -> pitch classes)
        KEY_SIG_PITCHES = {
            0:  {0, 2, 4, 5, 7, 9, 11},     # C Major / A Minor
            1:  {7, 9, 11, 0, 2, 4, 6},     # G Major / E Minor (1 sharp: F#)
            2:  {2, 4, 6, 7, 9, 11, 1},     # D Major / B Minor (2 sharps: F#, C#)
            3:  {9, 11, 1, 2, 4, 6, 8},     # A Major / F# Minor (3 sharps: F#, C#, G#)
            4:  {4, 6, 8, 9, 11, 1, 3},     # E Major / C# Minor (4 sharps: F#, C#, G#, D#)
            5:  {11, 1, 3, 4, 6, 8, 10},    # B Major / G# Minor (5 sharps: F#, C#, G#, D#, A#)
            6:  {6, 8, 10, 11, 1, 3, 5},    # F# Major / D# Minor (6 sharps)
            -1: {5, 7, 9, 10, 0, 2, 4},     # F Major / D Minor (1 flat: Bb)
            -2: {10, 0, 2, 3, 5, 7, 9},     # Bb Major / G Minor (2 flats: Bb, Eb)
            -3: {3, 5, 7, 8, 10, 0, 2},     # Eb Major / C Minor (3 flats: Bb, Eb, Ab)
            -4: {8, 10, 0, 1, 3, 5, 7},     # Ab Major / F Minor (4 flats: Bb, Eb, Ab, Db)
            -5: {1, 3, 5, 6, 8, 10, 0},     # Db Major / Bb Minor (5 flats)
            -6: {6, 8, 10, 11, 1, 3, 5}     # Gb Major / Eb Minor (6 flats)
        }
        
        # Map explicit MIDI key name if present and not default 'C'
        MIDI_KEY_TO_CONCERT_KEY = {
            'C': 0, 'G': 1, 'D': 2, 'A': 3, 'E': 4, 'B': 5, 'F#': 6, 'C#': 7,
            'F': -1, 'Bb': -2, 'Eb': -3, 'Ab': -4, 'Db': -5, 'Gb': -6, 'Cb': -7,
            'Am': 0, 'Em': 1, 'Bm': 2, 'F#m': 3, 'C#m': 4, 'G#m': 5, 'D#m': 6, 'A#m': 7,
            'Dm': -1, 'Gm': -2, 'Cm': -3, 'Fm': -4, 'Bbm': -5, 'Ebm': -6, 'Abm': -7
        }
        
        if has_midi_key_sig and midi_key_name != 'C':
            concert_key = MIDI_KEY_TO_CONCERT_KEY.get(midi_key_name, 0)
            print(f"[MuseScore MIDI Parse] Using explicit MIDI key signature event: {midi_key_name} (concertKey: {concert_key})")
        elif total_notes > 10:
            # Run pitch distribution heuristic to detect key signature
            best_key = 0
            best_score = -1
            for key_val, pitch_set in KEY_SIG_PITCHES.items():
                score = sum(pitch_counts[p] for p in pitch_set)
                if score > best_score:
                    best_score = score
                    best_key = key_val
            concert_key = best_key
            print(f"[MuseScore MIDI Parse] Heuristic key signature analysis: concertKey {concert_key} (best score: {best_score}/{total_notes} notes)")
        else:
            concert_key = 0
            print("[MuseScore MIDI Parse] Defaulting to concertKey 0 (insufficient notes for key heuristic)")
            
    except Exception as e:
        print(f"[MuseScore MIDI Parse] Warning: Failed to parse MIDI: {e}")
        
    return bpm, ts_numerator, ts_denominator, concert_key

def populate_musescore_metadata(mscz_path, title, composer, midi_path=None):
    import zipfile
    import tempfile
    import shutil
    import re
    
    # Read MIDI properties
    bpm, ts_numerator, ts_denominator, concert_key = get_midi_properties(midi_path)
    print(f"[MuseScore Metadata] Injected MIDI properties - BPM: {bpm:.1f}, Time Signature: {ts_numerator}/{ts_denominator}, concertKey: {concert_key}")
    
    # Create temp directory
    temp_dir = tempfile.mkdtemp()
    try:
        # Extract MSCZ
        with zipfile.ZipFile(mscz_path, 'r') as zip_ref:
            zip_ref.extractall(temp_dir)
            
        # Find the .mscx file
        # Find all .mscx and .mss files in extracted temp directory
        target_files = [os.path.join(temp_dir, f) for f in os.listdir(temp_dir) if f.endswith('.mscx') or f.endswith('.mss')]
        
        for fpath in target_files:
            with open(fpath, 'r', encoding='utf-8') as file:
                content = file.read()
                
            # Clean title and detect if Easy
            title_clean = title
            is_easy = False
            if title.endswith(" Easy"):
                title_clean = title[:-5].strip()
                is_easy = True

            # 1. Turn off red frames, invisible guidelines, and unprintable borders
            content = re.sub(r'<showFrames>\d+</showFrames>', '<showFrames>0</showFrames>', content)
            content = re.sub(r'<showInvisible>\d+</showInvisible>', '<showInvisible>0</showInvisible>', content)
            content = re.sub(r'<showUnprintable>\d+</showUnprintable>', '<showUnprintable>0</showUnprintable>', content)

            # 1b. Completely remove embedded images and image VBoxes from the score
            content = re.sub(r'<VBox>\s*(?:<[^>]+>\s*)*<Image>.*?</Image>\s*</VBox>', '', content, flags=re.DOTALL)
            content = re.sub(r'<Image>.*?</Image>', '', content, flags=re.DOTALL)

            # 2. Safely replace or insert metaTag tags (MuseScore 4 requires metaTag, not meta)
            def set_meta_tag(xml_str, name, val):
                tag_pattern = rf'<metaTag name="{name}">.*?</metaTag>'
                new_tag = f'<metaTag name="{name}">{val}</metaTag>'
                if re.search(tag_pattern, xml_str):
                    return re.sub(tag_pattern, new_tag, xml_str)
                else:
                    return xml_str.replace('<Score>', f'<Score>\n    {new_tag}')

            subtitle_val = "Easy" if is_easy else ""
            content = set_meta_tag(content, "workTitle", title_clean)
            content = set_meta_tag(content, "subtitle", subtitle_val)
            content = set_meta_tag(content, "composer", composer)
            content = set_meta_tag(content, "arranger", composer)
                
            # 3. Update VBox Title Text
            content = re.sub(
                r'(<style>title</style>\s*<text>).*?(</text>)',
                rf'\g<1>{title_clean}\g<2>',
                content,
                flags=re.DOTALL
            )
            
            # 4. Update VBox Subtitle Text
            if is_easy:
                if '<style>subtitle</style>' in content:
                    content = re.sub(
                        r'(<style>subtitle</style>\s*<text>).*?(</text>)',
                        r'\g<1>E A S Y\g<2>',
                        content,
                        flags=re.DOTALL
                    )
                else:
                    title_text_pattern = r'(<style>title</style>\s*<text>.*?</text>\s*</Text>)'
                    content = re.sub(
                        title_text_pattern,
                        r'\1\n        <Text>\n          <style>subtitle</style>\n          <text>E A S Y</text>\n          </Text>',
                        content,
                        flags=re.DOTALL
                    )
            else:
                if '<style>subtitle</style>' in content:
                    content = re.sub(
                        r'(<style>subtitle</style>\s*<text>).*?(</text>)',
                        r'\g<1>\g<2>',
                        content,
                        flags=re.DOTALL
                    )
            
            # 5. Update VBox Composer Text
            content = re.sub(
                r'(<style>composer</style>\s*<text>).*?(</text>)',
                rf'\g<1>Composer: {composer}<br/>Arr.: meloscribe\g<2>',
                content,
                flags=re.DOTALL
            )
            
            # 6. Update Time Signature AND Measure Rest Durations to avoid XML corruption!
            content = re.sub(r'<sigN>\d+</sigN>', f'<sigN>{ts_numerator}</sigN>', content)
            content = re.sub(r'<sigD>\d+</sigD>', f'<sigD>{ts_denominator}</sigD>', content)
            content = re.sub(r'<duration>\d+/\d+</duration>', f'<duration>{ts_numerator}/{ts_denominator}</duration>', content)
            
            # 7. Update Key Signature
            content = re.sub(r'<concertKey>[-\d]+</concertKey>', f'<concertKey>{concert_key}</concertKey>', content)
            
            # 8. Update Tempo
            bps = bpm / 60.0
            content = re.sub(r'<tempo>[\d\.]+</tempo>', f'<tempo>{bps:.6f}</tempo>', content)
            content = re.sub(
                r'(<text><sym>metNoteQuarterUp</sym><font face="Edwin"/> = )\d+(</text>)',
                rf'\g<1>{int(bpm)}\g<2>',
                content
            )

            # 9a. Enforce Staff Space = 1.4 mm (Spatium)
            if '<spatium>' in content:
                content = re.sub(r'<spatium>.*?</spatium>', '<spatium>1.4</spatium>', content)
            elif '<Style>' in content:
                content = content.replace('<Style>', '<Style>\n    <spatium>1.4</spatium>')

            # 9b. Clear Top Headers (remove duplicate top page numbers)
            for h_tag in ['evenHeaderL', 'evenHeaderC', 'evenHeaderR', 'oddHeaderL', 'oddHeaderC', 'oddHeaderR',
                        'headerEvenLeft', 'headerEvenCenter', 'headerEvenRight', 'headerOddLeft', 'headerOddCenter', 'headerOddRight']:
                content = re.sub(rf'<{h_tag}>.*?</{h_tag}>', f'<{h_tag}></{h_tag}>', content)

            # 9c. Enforce Page Numbers on Bottom-Center Only
            if '<footerEvenCenter>' in content:
                content = re.sub(r'<footerEvenCenter>.*?</footerEvenCenter>', '<footerEvenCenter>$P</footerEvenCenter>', content)
            if '<footerOddCenter>' in content:
                content = re.sub(r'<footerOddCenter>.*?</footerOddCenter>', '<footerOddCenter>$P</footerOddCenter>', content)
            if '<evenFooterC>' in content:
                content = re.sub(r'<evenFooterC>.*?</evenFooterC>', '<evenFooterC>$p</evenFooterC>', content)
            if '<oddFooterC>' in content:
                content = re.sub(r'<oddFooterC>.*?</oddFooterC>', '<oddFooterC>$p</oddFooterC>', content)

            # 10. Enforce strict 4 measures per system (Takte-Lock) across Staff 1
            # Remove any existing line breaks to start completely clean
            content = re.sub(r'<LayoutBreak>\s*(?:<[^>]+>\s*)*<subtype>.*?</subtype>\s*</LayoutBreak>', '', content, flags=re.DOTALL)
            content = re.sub(r'<LayoutBreak>.*?</LayoutBreak>', '', content, flags=re.DOTALL)

            staff1_match = re.search(r'(<Staff id="1">)(.*?)(</Staff>)', content, flags=re.DOTALL)
            if staff1_match:
                pfx = staff1_match.group(1)
                staff1_body = staff1_match.group(2)
                sfx = staff1_match.group(3)
                
                measure_pattern = re.compile(r'(<Measure(?:\s+[^>]*)?>)(.*?)(</Measure>)', re.DOTALL)
                measure_matches = list(measure_pattern.finditer(staff1_body))
                
                if measure_matches:
                    new_body_chunks = []
                    last_pos = 0
                    for idx, match in enumerate(measure_matches, start=1):
                        m_start = match.start()
                        m_end = match.end()
                        new_body_chunks.append(staff1_body[last_pos:m_start])
                        
                        m_open = match.group(1)
                        m_inner = match.group(2)
                        m_close = match.group(3)
                        
                        # Every 4th measure gets a line break, except the final measure
                        if idx % 4 == 0 and idx < len(measure_matches):
                            layout_break = "\n        <LayoutBreak>\n          <subtype>line</subtype>\n        </LayoutBreak>"
                            eid_match = re.search(r'^\s*<eid>[^<]+</eid>', m_inner)
                            if eid_match:
                                eid_end = eid_match.end()
                                mod_inner = m_inner[:eid_end] + layout_break + m_inner[eid_end:]
                            else:
                                mod_inner = layout_break + m_inner
                            new_body_chunks.append(f"{m_open}{mod_inner}{m_close}")
                        else:
                            new_body_chunks.append(f"{m_open}{m_inner}{m_close}")
                        last_pos = m_end
                        
                    new_body_chunks.append(staff1_body[last_pos:])
                    rebuilt_staff1 = pfx + "".join(new_body_chunks) + sfx
                    content = content[:staff1_match.start()] + rebuilt_staff1 + content[staff1_match.end():]
                
            with open(fpath, 'w', encoding='utf-8') as file:
                file.write(content)
                
        # Remove any lingering Pictures folder
        shutil.rmtree(os.path.join(temp_dir, 'Pictures'), ignore_errors=True)

        # Re-pack MSCZ
        with zipfile.ZipFile(mscz_path, 'w', zipfile.ZIP_DEFLATED) as zip_out:
            for root, dirs, files in os.walk(temp_dir):
                for f in files:
                    file_path = os.path.join(root, f)
                    rel_path = os.path.relpath(file_path, temp_dir)
                    zip_out.write(file_path, rel_path)
                    
        print(f"[MuseScore Metadata] Injected Title: '{title}', Composer/Arranger: '{composer}', BPM: {int(bpm)}, Time Signature: {ts_numerator}/{ts_denominator} into {os.path.basename(mscz_path)}")
    except Exception as e:
        print(f"[MuseScore Metadata] Warning: Failed to inject metadata: {e}")
    finally:
        shutil.rmtree(temp_dir)

def main():
    parser = argparse.ArgumentParser(description='MuseScore Launcher — opens MIDI + meloscribe template')
    parser.add_argument('--song',   required=True, help='Song name (exact, no extension)')
    parser.add_argument('--author', default='Dave Kerr', help='Author name (for logging)')
    parser.add_argument('--no_wait', action='store_true', help='Launch MuseScore and exit immediately without waiting for PDF')
    args = parser.parse_args()
    song = args.song

    # ── Load settings ─────────────────────────────────────────────────────────
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "meloscribe", "backend"))
    from settings import load_settings
    settings = load_settings()

    musescore_dir  = settings.get("musescore_dir",  r"C:\Dev\meloscribe\Scores")
    cakewalk_dir   = os.path.join(settings.get("cakewalk_dir", r"C:\Cakewalk Projects"), song)
    musescore_exe  = settings.get("musescore_exe",  r"C:\Program Files\MuseScore 4\bin\MuseScore4.exe")

    os.makedirs(musescore_dir, exist_ok=True)

    # ── Skip if PDF already exists ─────────────────────────────────────────────
    pdf_path = find_file_ci(musescore_dir, f"{song}.pdf")
    if pdf_path:
        print(f"[MuseScore] PDF already exists at {pdf_path} — skipping MuseScore launch.")
        sys.exit(0)

    # ── Validate MuseScore executable ─────────────────────────────────────────
    if not os.path.exists(musescore_exe):
        print(f"[MuseScore] ERROR: MuseScore executable not found at '{musescore_exe}'.")
        sys.exit(1)

    # ── Locate MIDI ───────────────────────────────────────────────────────────
    midi_path = find_file_ci(cakewalk_dir, f"{song}.mid")
    if not midi_path:
        print(f"[MuseScore] WARNING: MIDI not found in '{cakewalk_dir}'. MuseScore will open without reference MIDI.")

    # ── Locate / create score from template ───────────────────────────────────
    score_path = os.path.join(musescore_dir, f"{song}.mscz")

    template_paths = [
        os.path.expanduser(r"~\Documents\MuseScore4\Templates\meloscribe.mscz"),
        os.path.expanduser(r"~\Documents\MuseScore4\Templates\meloscribe_template.mscz"),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "musescore cfg", "meloscribe.mscz"),
    ]

    if not os.path.exists(score_path):
        template_found = None
        for tp in template_paths:
            if os.path.exists(tp):
                template_found = tp
                break
        if template_found:
            try:
                shutil.copy2(template_found, score_path)
                print(f"[MuseScore] Template copied: {template_found} → {score_path}")
            except Exception as e:
                print(f"[MuseScore] WARNING: Could not copy template: {e}. Opening blank score.")
                score_path = None
        else:
            print(f"[MuseScore] WARNING: No template found at any of these paths:")
            for tp in template_paths:
                print(f"  {tp}")
            score_path = None
    else:
        print(f"[MuseScore] Score already exists at {score_path} — updating metadata and key signature in-place.")

    if score_path and os.path.exists(score_path):
        try:
            populate_musescore_metadata(score_path, song, args.author, midi_path)
        except Exception as e:
            print(f"[MuseScore] WARNING: Could not update score metadata: {e}")

    # ── Launch MuseScore windows ───────────────────────────────────────────────
    print(f"[MuseScore] Launching MuseScore for '{song}'...")
    print("[MuseScore] Window 1: MIDI reference")
    print("[MuseScore] Window 2: meloscribe template score")

    # Window 1 — MIDI
    if midi_path:
        subprocess.Popen([musescore_exe, midi_path])
    else:
        subprocess.Popen([musescore_exe])

    # Short delay so MuseScore has time to start before the second window opens
    time.sleep(2)

    # Window 2 — meloscribe template (or blank if template missing)
    if score_path and os.path.exists(score_path):
        subprocess.Popen([musescore_exe, score_path])
        print(f"[MuseScore] Opened template score: {score_path}")
    else:
        subprocess.Popen([musescore_exe])
        print("[MuseScore] Opened blank MuseScore window (no template available).")

    # ── Wait for PDF ──────────────────────────────────────────────────────────
    if args.no_wait:
        print("[MuseScore] --no_wait specified. Exiting launcher process immediately.")
        sys.exit(0)
    timeout_minutes = 30
    poll_interval   = 5
    max_polls       = (timeout_minutes * 60) // poll_interval

    print(f"\n[MuseScore] Waiting for '{song}.pdf' in: {musescore_dir}")
    print(f"[MuseScore] (Take your time — I'll wait up to {timeout_minutes} minutes)\n")

    for attempt in range(max_polls):
        pdf_path = find_file_ci(musescore_dir, f"{song}.pdf")
        if pdf_path:
            print(f"[MuseScore] PDF found: {pdf_path}")
            sys.exit(0)
        time.sleep(poll_interval)

    print(f"[MuseScore] TIMEOUT: No PDF found after {timeout_minutes} minutes.")
    sys.exit(1)

if __name__ == '__main__':
    main()
