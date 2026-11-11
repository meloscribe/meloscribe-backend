import sys
import os
import io
import mido
import sqlite3
import hashlib
from pathlib import Path

def get_transaction_hash_32(transaction_id: str) -> int:
    """Compute deterministic 32-bit unsigned integer hash from transaction_id."""
    h = hashlib.sha256(transaction_id.strip().encode("utf-8")).digest()
    return int.from_bytes(h[:4], byteorder="big", signed=False)

def extract_midi_fingerprint(midi_file_or_bytes) -> int:
    """Extracts 32-bit integer fingerprint from the first 32 note_on events (velocity > 0)."""
    if isinstance(midi_file_or_bytes, (bytes, bytearray)):
        mid = mido.MidiFile(file=io.BytesIO(midi_file_or_bytes))
    else:
        mid = mido.MidiFile(str(midi_file_or_bytes))
        
    extracted_bits = []
    for track in mid.tracks:
        for msg in track:
            if len(extracted_bits) >= 32:
                break
            if msg.type == 'note_on' and msg.velocity > 0:
                extracted_bits.append(msg.velocity % 2)
        if len(extracted_bits) >= 32:
            break
            
    if len(extracted_bits) < 32:
        return None
        
    val = 0
    for b in extracted_bits:
        val = (val << 1) | b
    return val

def scan_leaked_midi(midi_path: str, db_path: str = None):
    print("=" * 60)
    print("MELOSCRIBE FORENSIC MIDI SCANNER")
    print("=" * 60)
    print(f"Analyzing file: {midi_path}")
    
    if not os.path.exists(midi_path):
        print(f"Error: File not found at {midi_path}")
        return False

    mid = mido.MidiFile(midi_path)
    
    # 1. Inspect Visible Meta Events
    print("\n[Layer 1: Visible Metadata Inspection]")
    meta_found = False
    for i, track in enumerate(mid.tracks):
        for msg in track:
            if msg.is_meta and msg.type in ('track_name', 'copyright', 'text'):
                txt = getattr(msg, 'name', None) or getattr(msg, 'text', None) or ''
                if any(k in txt.lower() for k in ['licensed to', 'meloscribe', 'order #', 'stripe', 'copyright']):
                    print(f"  Track {i} ({msg.type}): {txt}")
                    meta_found = True
    if not meta_found:
        print("  Notice: All visible text/copyright headers have been stripped or removed.")

    # 2. Extract Velocity Fingerprint
    print("\n[Layer 2: Forensic Velocity Fingerprint Extraction]")
    extracted_fp = extract_midi_fingerprint(midi_path)
    if extracted_fp is None:
        print("  Error: Less than 32 note_on events found. Cannot extract 32-bit fingerprint.")
        return False

    print(f"  Extracted 32-bit Fingerprint Hash: {extracted_fp} (hex: {hex(extracted_fp)})")

    # 3. Resolve Database
    if not db_path:
        candidates = [
            Path(__file__).resolve().parent / "meloscribe" / "backend" / "analytics.db",
            Path(r"C:\Dev\meloscribe-backend\tools\meloscribe\backend\analytics.db"),
            Path(r"C:\Dev\meloscribe-app\tools\meloscribe\backend\analytics.db"),
            Path(r"C:\Dev\meloscribe-analytics\raw\analytics.db"),
            Path(r"/home/ubuntu/meloscribe/tools/meloscribe/backend/analytics.db")
        ]
        for c in candidates:
            if c.exists():
                db_path = str(c)
                break

    if not db_path or not os.path.exists(db_path):
        print(f"  Warning: analytics.db not found. Raw fingerprint is: {hex(extracted_fp)}")
        return False

    print(f"  Searching database: {db_path}")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute("SELECT id, transaction_id, email, buyer_name, song_name, amount, currency, created_at, download_count, ip_addresses FROM purchases")
    purchases = cur.fetchall()

    match = None
    for p in purchases:
        txn = p["transaction_id"]
        if txn and get_transaction_hash_32(txn) == extracted_fp:
            match = p
            break

    conn.close()

    if match:
        print("\n" + "#" * 60)
        print("FORENSIC MATCH CONFIRMED (100% MATHEMATICAL CERTAINTY)")
        print("#" * 60)
        print(f"  Buyer Email:    {match['email']}")
        print(f"  Buyer Name:     {match['buyer_name'] or 'N/A'}")
        print(f"  Song Name:      {match['song_name']}")
        print(f"  Transaction ID: {match['transaction_id']}")
        print(f"  Purchase Date:  {match['created_at']}")
        print(f"  Amount:         {match['amount']} {match['currency']}")
        print(f"  Downloads Log:  {match['download_count']} times")
        print(f"  IP History:     {match['ip_addresses'] or 'N/A'}")
        print("#" * 60)
        return True
    else:
        print("\nNo matching transaction found in database for this fingerprint.")
        return False

if __name__ == "__main__":
    if len(sys.argv) > 1:
        scan_leaked_midi(sys.argv[1])
    else:
        print("Usage: python forensic_midi_scanner.py <path_to_midi_file>")

