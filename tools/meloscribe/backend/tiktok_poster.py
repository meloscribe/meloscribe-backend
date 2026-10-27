"""
TikTok API Poster Module
------------------------
Replaces Playwright browser automation with the official TikTok Direct Post API.
Uses chunked uploading (FILE_UPLOAD) to support large video files.
"""
import os
import math
import time
import requests
import subprocess
from pathlib import Path

# Try to reuse the token management from tiktok_auth
try:
    from meloscribe.backend.tiktok_auth import get_valid_token
except ImportError:
    from tiktok_auth import get_valid_token

INIT_URL = "https://open.tiktokapis.com/v2/post/publish/video/init/"
INBOX_URL = "https://open.tiktokapis.com/v2/post/publish/inbox/video/init/"

def get_ffprobe_exe() -> str:
    # Check local ffmpeg bin relative to tools
    backend_dir = Path(__file__).resolve().parent
    tools_dir = backend_dir.parent.parent / "tools"
    local_probe = tools_dir / "ffmpeg" / "bin" / "ffprobe.exe"
    if local_probe.exists():
        return str(local_probe)
    # Also check backend parent
    alt_probe = backend_dir.parent / "ffmpeg" / "bin" / "ffprobe.exe"
    if alt_probe.exists():
        return str(alt_probe)
    return "ffprobe"

def get_video_duration_ms(video_path: str) -> int:
    try:
        cmd = [get_ffprobe_exe(), '-v', 'error', '-show_entries', 'format=duration',
               '-of', 'default=noprint_wrappers=1:nokey=1', video_path]
        result = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8')
        duration_sec = float(result.stdout.strip())
        return int(duration_sec * 1000)
    except Exception as e:
        print(f"[TikTok API] Warning: Failed to get duration, defaulting cover to 0ms. ({e})")
        return 0

def post_video(video_path: str, title: str, privacy: str = "PUBLIC_TO_EVERYONE", cover_timestamp_ms: int = None) -> bool:
    """
    Uploads an MP4 video to TikTok using the Direct Post API with custom cover timestamp.
    Defaults to PUBLIC_TO_EVERYONE.
    Uses chunked file upload to support large video files.
    """
    if not os.path.exists(video_path):
        print(f"[TikTok API] ERROR: Video not found at {video_path}")
        return False

    token = get_valid_token()
    if not token:
        print("[TikTok API] ERROR: No valid auth token. Please run tiktok_setup.py first.")
        return False

    # Validate privacy level against allowed options
    valid_privacies = ("PUBLIC_TO_EVERYONE", "MUTUAL_FOLLOW_FRIENDS", "SELF_ONLY")
    if privacy not in valid_privacies:
        privacy = "PUBLIC_TO_EVERYONE"

    # 1. Prepare File Info
    file_size = os.path.getsize(video_path)
    
    # TikTok API Chunk Size Rules:
    # 1. If file_size <= 64MB, we send 1 chunk with chunk_size == file_size.
    # 2. If file_size > 64MB:
    #    - chunk_size is 20MB (20 * 1024 * 1024 bytes).
    #    - total_chunk_count MUST BE integer division: file_size // chunk_size.
    if file_size <= 64 * 1024 * 1024:
        chunk_size = file_size
        total_chunks = 1
    else:
        chunk_size = 20 * 1024 * 1024
        total_chunks = file_size // chunk_size

    # Default cover timestamp to 0ms (uses authentic opening video frame)
    duration_ms = get_video_duration_ms(video_path)
    if cover_timestamp_ms is not None:
        cover_timestamp = max(0, min(cover_timestamp_ms, duration_ms))
    else:
        cover_timestamp = 0

    # Trim title to safe limit (TikTok API max 2200 chars)
    safe_title = (title[:1990] + "...") if len(title) > 2000 else title

    print(f"\n[TikTok API] Initializing Upload for '{os.path.basename(video_path)}'")
    print(f"             Size: {file_size / (1024*1024):.2f} MB | Chunks: {total_chunks}")
    print(f"             Privacy: {privacy} | Cover Timestamp: {cover_timestamp}ms (Duration: {duration_ms}ms)")

    # 2. Init Upload: Direct Post via TikTok Content Posting API v2
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=UTF-8"
    }

    init_payload = {
        "post_info": {
            "title": safe_title,
            "privacy_level": privacy,
            "disable_duet": False,
            "disable_comment": False,
            "disable_stitch": False,
            "video_cover_timestamp_ms": cover_timestamp
        },
        "source_info": {
            "source": "FILE_UPLOAD",
            "video_size": file_size,
            "chunk_size": chunk_size,
            "total_chunk_count": total_chunks
        }
    }

    upload_mode = f"Direct Post ({privacy})"
    try:
        # Check creator info first
        creator_info_url = "https://open.tiktokapis.com/v2/post/publish/creator_info/query/"
        c_resp = requests.post(creator_info_url, headers=headers)
        if c_resp.status_code == 200:
            c_data = c_resp.json().get("data", {})
            print(f"[TikTok API] Creator: @{c_data.get('creator_username')} | Allowed privacy: {c_data.get('privacy_level_options')}")
        else:
            print(f"[TikTok API] Warning: creator_info query returned {c_resp.status_code}")

        resp = requests.post(INIT_URL, json=init_payload, headers=headers)
        data = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}

        if resp.status_code != 200 or (data.get("error", {}).get("code") != "ok"):
            err_code = data.get("error", {}).get("code", resp.status_code)
            err_msg = data.get("error", {}).get("message", resp.text)
            print(f"\n[TikTok API] ERROR: Direct Post initialization failed ({resp.status_code})!")
            print(f"             Code: {err_code}")
            print(f"             Message: {err_msg}")
            if err_code == "unaudited_client_can_only_post_to_private_accounts":
                print("\n[TikTok API] HINT: Your TikTok Developer Client is currently in Sandbox/Unaudited mode.")
                print("             TikTok requires the TikTok mobile account (@meloscribe) to be set to")
                print("             'Privates Konto' (Private Account = ON) in the mobile app settings")
                print("             (Profil -> Einstellungen und Datenschutz -> Datenschutz -> Privates Konto).")
                print("             Alternatively, configure the audited Production Client Key/Secret in settings.json.")
            return False

        upload_url = data["data"]["upload_url"]
        publish_id = data["data"].get("publish_id", "")
        print(f"[TikTok API] Initialized via {upload_mode} (publish_id: {publish_id})")
    except Exception as e:
        print(f"[TikTok API] Exception during initialization: {e}")
        return False

    print(f"[TikTok API] Upload initialized! Streaming bytes...")

    # 3. Stream File Chunks
    with open(video_path, "rb") as f:
        for chunk_idx in range(total_chunks):
            # Calculate range
            start_byte = chunk_idx * chunk_size
            
            # Read chunk
            f.seek(start_byte)
            if chunk_idx == total_chunks - 1:
                chunk_data = f.read() # Read all remaining bytes
            else:
                chunk_data = f.read(chunk_size)
            end_byte = start_byte + len(chunk_data) - 1

            put_headers = {
                "Content-Range": f"bytes {start_byte}-{end_byte}/{file_size}",
                "Content-Type": "video/mp4"
            }

            print(f"             Uploading chunk {chunk_idx + 1}/{total_chunks} ({start_byte}-{end_byte})...")
            
            try:
                put_resp = requests.put(upload_url, headers=put_headers, data=chunk_data)
                
                # HTTP 201 Created or 206 Partial Content are success codes for chunked upload
                if put_resp.status_code not in (200, 201, 206):
                    print(f"[TikTok API] Chunk {chunk_idx+1} upload failed: {put_resp.status_code} - {put_resp.text}")
                    return False
            except Exception as e:
                print(f"[TikTok API] Exception during chunk upload: {e}")
                return False

    print(f"[TikTok API] SUCCESS! Video '{os.path.basename(video_path)}' published successfully.")
    return True

if __name__ == "__main__":
    # Test script if executed directly
    print("This module is meant to be imported.")
