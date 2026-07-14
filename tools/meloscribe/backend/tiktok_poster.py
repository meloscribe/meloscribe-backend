"""
TikTok API Poster Module
------------------------
Replaces Playwright browser automation with the official TikTok Direct Post API.
Uses chunked uploading (FILE_UPLOAD) to support large video files.
"""
import os
import time
import requests
import subprocess
from pathlib import Path

# Try to reuse the token management from tiktok_auth
try:
    from meloscribe.backend.tiktok_auth import get_valid_token
except ImportError:
    from tiktok_auth import get_valid_token

INIT_URL = "https://open.tiktokapis.com/v2/post/publish/inbox/video/init/"

def get_video_duration_ms(video_path: str) -> int:
    try:
        cmd = ['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
               '-of', 'default=noprint_wrappers=1:nokey=1', video_path]
        result = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8')
        duration_sec = float(result.stdout.strip())
        return int(duration_sec * 1000)
    except Exception as e:
        print(f"[TikTok API] Warning: Failed to get duration, defaulting cover to 0ms. ({e})")
        return 0

def post_video(video_path: str, title: str, privacy: str = "PUBLIC_TO_EVERYONE") -> bool:
    """
    Uploads an MP4 video to TikTok inbox/drafts.
    Uses chunked file upload to bypass timeouts.
    """
    if not os.path.exists(video_path):
        print(f"[TikTok API] ERROR: Video not found at {video_path}")
        return False

    token = get_valid_token()
    if not token:
        print("[TikTok API] ERROR: No valid auth token. Please run tiktok_setup.py first.")
        return False

    # 1. Prepare File Info
    file_size = os.path.getsize(video_path)
    
    # TikTok recommends chunks between 5MB and 64MB. Let's use 20MB.
    # But if the file is smaller than 20MB, we can just do 1 chunk.
    chunk_size = 20 * 1024 * 1024 
    if chunk_size > file_size:
        chunk_size = file_size

    # TikTok expects total_chunk_count to be floor(file_size / chunk_size).
    # Except if that is 0 (for small files), it must be at least 1.
    total_chunks = file_size // chunk_size
    if total_chunks == 0:
        total_chunks = 1

    print(f"\n[TikTok API] Initializing Upload for '{os.path.basename(video_path)}'")
    print(f"             Size: {file_size / (1024*1024):.2f} MB | Chunks: {total_chunks}")

    # 2. Init Upload
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=UTF-8"
    }

    # Ensure title isn't too long (TikTok limit: ~2200 chars, but let's be safe)
    if len(title) > 2000:
        title = title[:1997] + "..."

    # Get duration for cover frame (we inject the cover frame at the very end of the video)
    duration_ms = get_video_duration_ms(video_path)
    cover_timestamp = max(0, duration_ms - 50) # 50ms before the absolute end

    init_payload = {
        "post_info": {
            "title": title,
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

    try:
        resp = requests.post(INIT_URL, json=init_payload, headers=headers)
        if resp.status_code != 200:
            print(f"[TikTok API] Init failed! {resp.status_code}: {resp.text}")
            return False
            
        data = resp.json()
        if "error" in data and data["error"].get("code") != "ok":
            print(f"[TikTok API] Init error: {data['error']}")
            return False
            
        upload_url = data["data"]["upload_url"]
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
