"""
Pinterest API Poster Module
---------------------------
Handles automated video and image Pin publishing to Pinterest using Pinterest API v5.
Supports:
- OAuth token auto-refresh
- Multi-step S3 Video Upload (Register -> Upload -> Poll -> Pin)
- Fallback to clean Cover Image Pin if video not found
- Pinterest SEO Titles & Descriptions with keywords & hashtags
- Dynamic routing to Easy and Intermediate Boards
- Automated spam-link mitigation (re-tries without link if domain filter triggers)
"""

import os
import sys
import json
import time
import re
import urllib.parse
import base64
import requests
from pathlib import Path

# Path to Pinterest credentials
TOKENS_PATH = Path(__file__).resolve().parent / "pinterest_tokens.json"

def refresh_pinterest_token():
    """Auto-refreshes the Pinterest OAuth access token if expired or before publishing."""
    if not TOKENS_PATH.exists():
        print(f"[Pinterest API] Warning: Tokens file not found at {TOKENS_PATH}")
        return False
        
    try:
        with open(TOKENS_PATH, "r", encoding="utf-8") as f:
            tokens = json.load(f)
            
        app_id = tokens.get("pinterest_app_id")
        app_secret = tokens.get("pinterest_app_secret")
        refresh_token = tokens.get("pinterest_refresh_token")
        
        if not app_id or not app_secret or not refresh_token:
            return False
            
        auth_str = f"{app_id}:{app_secret}"
        b64_auth = base64.b64encode(auth_str.encode("utf-8")).decode("utf-8")
        
        resp = requests.post(
            "https://api.pinterest.com/v5/oauth/token",
            headers={
                "Authorization": f"Basic {b64_auth}",
                "Content-Type": "application/x-www-form-urlencoded"
            },
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token
            },
            timeout=15
        )
        
        if resp.status_code == 200:
            res_data = resp.json()
            new_access = res_data.get("access_token")
            if new_access:
                tokens["pinterest_access_token"] = new_access
                if "refresh_token" in res_data:
                    tokens["pinterest_refresh_token"] = res_data["refresh_token"]
                with open(TOKENS_PATH, "w", encoding="utf-8") as f:
                    json.dump(tokens, f, indent=2, ensure_ascii=False)
                print("[Pinterest API] OAuth token refreshed successfully.")
                return True
        else:
            print(f"[Pinterest API] Token refresh notice: {resp.status_code} - {resp.text}")
            return False
    except Exception as e:
        print(f"[Pinterest API] Exception during token refresh: {e}")
        return False

def upload_video_media(video_path: str, access_token: str) -> str:
    """
    Executes the 3-step Pinterest Video upload:
    1. Register media on Pinterest -> receives media_id, upload_url, and S3 upload_parameters
    2. Uploads the MP4 file to AWS S3 with exact form field order
    3. Polls GET /v5/media/{media_id} until status == 'succeeded'
    Returns media_id or None on error.
    """
    if not os.path.exists(video_path):
        print(f"[Pinterest API] Video file not found: {video_path}")
        return None
        
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }
    
    file_size_mb = os.path.getsize(video_path) / (1024 * 1024)
    print(f"[Pinterest API] Step 1/3: Registering media for '{os.path.basename(video_path)}' ({file_size_mb:.1f} MB)...")
    reg_resp = requests.post(
        "https://api.pinterest.com/v5/media",
        headers=headers,
        json={"media_type": "video"},
        timeout=15
    )
    
    if reg_resp.status_code not in (200, 201):
        print(f"[Pinterest API] Failed to register video media ({reg_resp.status_code}): {reg_resp.text}")
        return None
        
    reg_data = reg_resp.json()
    media_id = reg_data.get("media_id")
    upload_url = reg_data.get("upload_url")
    upload_params = reg_data.get("upload_parameters", {})
    
    if not media_id or not upload_url:
        print("[Pinterest API] Invalid media registration response from Pinterest.")
        return None
        
    print(f"[Pinterest API] Step 2/3: Uploading video to S3 storage (Media ID: {media_id})...")
    try:
        with open(video_path, "rb") as f:
            files = [
                ("file", (os.path.basename(video_path), f, "video/mp4"))
            ]
            s3_resp = requests.post(upload_url, data=upload_params, files=files, timeout=300)
            
        if s3_resp.status_code not in (200, 204):
            print(f"[Pinterest API] S3 Upload failed ({s3_resp.status_code}): {s3_resp.text}")
            return None
    except Exception as upload_err:
        print(f"[Pinterest API] Exception during S3 video upload: {upload_err}")
        return None
        
    print(f"[Pinterest API] Step 3/3: Waiting for Pinterest to transcode and finalize video...")
    start_time = time.time()
    max_wait_seconds = 420  # 7 minutes to accommodate large 350+ MB videos and high-load transcode queues
    
    while time.time() - start_time < max_wait_seconds:
        time.sleep(5)
        poll_resp = requests.get(
            f"https://api.pinterest.com/v5/media/{media_id}",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=10
        )
        if poll_resp.status_code == 200:
            status = poll_resp.json().get("status")
            if status == "succeeded":
                elapsed = int(time.time() - start_time)
                print(f"[Pinterest API] Video processing succeeded in {elapsed}s! Waiting 8s for CDN sync...")
                time.sleep(8)
                return media_id
            elif status == "failed":
                print(f"[Pinterest API] Video processing failed on Pinterest servers: {poll_resp.text}")
                return None
            else:
                # Still processing
                continue
        else:
            print(f"[Pinterest API] Media poll returned {poll_resp.status_code}")
            
    print(f"[Pinterest API] Timeout after {max_wait_seconds}s waiting for video processing.")
    return None

def post_pin(song_name: str, author: str = "Dave Kerr", profile: str = "normal", is_easy: bool = False, board_id: str = None, video_path: str = None) -> bool:
    """
    Creates a Pin for a song on Pinterest.
    Uploads the video if available; falls back to the clean cover image.
    Automatically applies Pinterest SEO best practices for title and description.
    """
    refresh_pinterest_token()
    
    if not TOKENS_PATH.exists():
        print("[Pinterest API] Error: 'pinterest_tokens.json' not found.")
        return False
        
    with open(TOKENS_PATH, "r", encoding="utf-8") as f:
        tokens = json.load(f)
        
    access_token = tokens.get("pinterest_access_token")
    if not access_token:
        print("[Pinterest API] Error: No access token found in tokens file.")
        return False
        
    # Check if song is marked easy
    if song_name.lower().endswith(" easy"):
        is_easy = True
        
    clean_song = song_name
    if clean_song.lower().endswith(" recycled"):
        clean_song = clean_song[:-9].strip()
    if clean_song.lower().endswith(" easy"):
        clean_song = clean_song[:-5].strip()
    elif clean_song.lower().endswith(" teaser"):
        clean_song = clean_song[:-7].strip()
        
    # 1. Resolve Board ID
    if not board_id:
        if is_easy:
            board_id = tokens.get("pinterest_board_easy") or "1084101010242508267"
        else:
            board_id = tokens.get("pinterest_board_intermediate") or "1084101010242508268"
            
    # 2. Resolve Video Path if not provided
    if not video_path:
        search_dirs = []
        import platform
        if platform.system() == "Linux":
            staging_base = "/home/ubuntu/meloscribe/staging"
            search_dirs.extend([
                os.path.join(staging_base, clean_song, "TikToks"),
                os.path.join(staging_base, song_name, "TikToks")
            ])
            
        try:
            from settings import load_settings
            cfg = load_settings()
            cfg_dir = cfg.get("tiktok_dir")
            if cfg_dir and cfg_dir not in search_dirs:
                search_dirs.append(cfg_dir)
        except Exception:
            pass
            
        if r"C:\Dev\meloscribe\TikToks" not in search_dirs:
            search_dirs.append(r"C:\Dev\meloscribe\TikToks")
            
        candidates = []
        if is_easy:
            candidates.extend([f"{clean_song} Easy.mp4", f"{clean_song} easy.mp4"])
        else:
            candidates.extend([f"{clean_song}.mp4", f"{song_name}.mp4"])
            
        for sdir in search_dirs:
            if not os.path.exists(sdir):
                continue
            for cand in candidates:
                p = os.path.join(sdir, cand)
                if os.path.exists(p):
                    video_path = p
                    break
            if video_path:
                break
                
            # Case-insensitive scan
            target_cand = (f"{clean_song} easy.mp4" if is_easy else f"{clean_song}.mp4").lower()
            for fname in os.listdir(sdir):
                if fname.lower() == target_cand:
                    video_path = os.path.join(sdir, fname)
                    break
            if video_path:
                break

    # 3. Clean Cover URL
    encoded_name = urllib.parse.quote(clean_song)
    cover_url = f"https://meloscribesheets.com/covers/{encoded_name}_clean.jpg"
    
    # 4. Construct High-Ranking Pinterest SEO Title (Max 100 chars)
    if is_easy:
        title = f"🎹 {clean_song} - Easy Piano Sheet Music & Tutorial | {author}"
    else:
        title = f"🎹 {clean_song} - Piano Sheet Music & Tutorial | {author}"
        
    if len(title) > 100:
        label = "Easy Piano Sheets" if is_easy else "Piano Sheet Music"
        title = f"🎹 {clean_song} - {label} | {author}"
    if len(title) > 100:
        title = title[:97] + "..."
        
    # 5. Destination Link (Direct product link that automatically opens song modal)
    slug = clean_song.lower()
    slug = re.sub(r'[^a-z0-9]+', '-', slug).strip('-')
    version_suffix = "&version=easy" if is_easy else ""
    song_link = f"https://meloscribesheets.com/?song={slug}{version_suffix}"
    
    # 6. Construct High-Ranking Pinterest SEO Description (Max 500 chars)
    clean_tag = re.sub(r'[^a-zA-Z0-9]', '', clean_song).lower()
    author_tag = re.sub(r'[^a-zA-Z0-9]', '', author).lower()
    
    if is_easy:
        desc = (
            f"Learn how to play '{clean_song}' by {author} on piano! 🎹\n\n"
            f"✨ Click the Pin to get the Sheet Music (PDF), MIDI & practice tutorial!\n\n"
            f"Arranged with love. Check out our Easy Piano boards for more aesthetic piano sheets & beginner tutorials.\n\n"
            f"#piano #pianocover #pianotutorial #sheetmusic #{clean_tag} #{author_tag} #easypiano #learnpiano #pianomusic #aestheticpiano #pianonotes"
        )
    else:
        desc = (
            f"Learn how to play '{clean_song}' by {author} on piano! 🎹\n\n"
            f"✨ Click the Pin to get the Sheet Music (PDF), MIDI & practice tutorial!\n\n"
            f"Arranged with love. Check out our boards for more aesthetic piano sheets & tutorials.\n\n"
            f"#piano #pianocover #pianotutorial #sheetmusic #{clean_tag} #{author_tag} #pianomusic #learnpiano #aestheticpiano #solopiano #pianopractice"
        )
    if len(desc) > 500:
        desc = desc[:495] + "..."

    # 7. Media Source Handling
    media_source = None
    if video_path and os.path.exists(video_path):
        media_id = upload_video_media(video_path, access_token)
        if media_id:
            media_source = {
                "source_type": "video_id",
                "media_id": media_id,
                "cover_image_key_frame_time": 2
            }
        else:
            print("[Pinterest API] Video upload failed, falling back to clean Cover Image Pin.")
            
    if not media_source:
        media_source = {
            "source_type": "image_url",
            "url": cover_url
        }
        
    pin_data = {
        "board_id": board_id,
        "title": title,
        "description": desc,
        "link": song_link,
        "media_source": media_source
    }
    
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }
    
    print(f"\n[Pinterest API] Publishing Pin to Board '{board_id}'...")
    print(f"  Title: {title}")
    print(f"  Media: {media_source['source_type']}")
    
    resp = requests.post("https://api.pinterest.com/v5/pins", headers=headers, json=pin_data, timeout=20)
    
    # Check if media_id is still propagating across Pinterest CDN cluster
    if resp.status_code == 400 and "media_id" in resp.text:
        print("[Pinterest API] Notice: Media ID not yet fully propagated on Pinterest cluster. Waiting 10s before retry...")
        time.sleep(10)
        resp = requests.post("https://api.pinterest.com/v5/pins", headers=headers, json=pin_data, timeout=20)
        
    # Check if Pinterest automated spam filter blocked the direct destination link
    if (resp.status_code == 429 or resp.status_code == 400) and ("spam" in resp.text.lower() or "blocked this link" in resp.text.lower()):
        print(f"[Pinterest API] Note: Pinterest automated spam filter flagged direct link '{song_link}'.")
        print("[Pinterest API] Retrying without destination link button...")
        pin_data["link"] = None
        resp = requests.post("https://api.pinterest.com/v5/pins", headers=headers, json=pin_data, timeout=20)
        
    if resp.status_code in (200, 201):
        pin_info = resp.json()
        pin_id = pin_info.get("id")
        print(f"[Pinterest API] SUCCESS! Pin created successfully (Pin ID: {pin_id}) on Board {board_id}!\n")
        return True
    else:
        print(f"[Pinterest API] ERROR ({resp.status_code}): {resp.text}")
        return False

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Pinterest Poster CLI")
    parser.add_argument("--song", required=True, help="Song name")
    parser.add_argument("--author", default="Dave Kerr", help="Song author/artist")
    parser.add_argument("--profile", default="normal", help="Profile (normal, tutorial, easy)")
    parser.add_argument("--board_id", default=None, help="Explicit Pinterest Board ID")
    parser.add_argument("--video_path", default=None, help="Explicit video file path")
    args = parser.parse_args()
    
    is_easy = "easy" in args.song.lower() or args.profile == "easy"
    success = post_pin(args.song, args.author, args.profile, is_easy=is_easy, board_id=args.board_id, video_path=args.video_path)
    sys.exit(0 if success else 1)
