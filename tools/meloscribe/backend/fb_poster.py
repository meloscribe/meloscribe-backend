"""
Facebook Graph API Poster
--------------------------
Uploads Videos / Reels to a Facebook Page using the Graph API.
Supports scheduling via scheduled_publish_time.
condensed=True  → posts as a Reel (short vertical video)
condensed=False → posts as a regular video with thumbnail
"""
import os
import json
import time
import requests
from pathlib import Path
from datetime import datetime, timezone

TOKENS_PATH = Path(__file__).parent / "ig_tokens.json"
GRAPH_URL = "https://graph.facebook.com/v19.0"


def _get_creds():
    if not TOKENS_PATH.exists():
        return None, None
    with open(TOKENS_PATH, "r") as f:
        d = json.load(f)
    return d.get("access_token"), d.get("fb_page_id")


def post_video(video_path: str, title: str, description: str,
               format: str = "viral_part", thumbnail_path: str = None,
               publish_at_dt: datetime = None) -> bool:
    """
    Upload a video to a Facebook Page.
    format="viral_part"       → Reel (no thumbnail required, uses vertical video)
    format="full_arrangement"  → Long-form video with optional thumbnail
    publish_at_dt              → Schedule the post (must be in the future)
    """
    if not os.path.exists(video_path):
        print(f"[Facebook API] ERROR: Video not found at {video_path}")
        return False

    token, page_id = _get_creds()
    if not token or not page_id:
        print("[Facebook API] ERROR: No valid FB token. Run ig_setup.py first.")
        return False

    is_reel = (format == "viral_part")
    print(f"\n[Facebook API] Uploading '{os.path.basename(video_path)}' as {'Reel' if is_reel else 'Video'}...")

    if is_reel:
        # Upload as Reel via the /reels endpoint
        upload_url = f"{GRAPH_URL}/{page_id}/video_reels"
        params = {
            "upload_phase": "start",
            "access_token": token
        }
        init_resp = requests.post(upload_url, data=params)
        if init_resp.status_code != 200:
            print(f"[Facebook API] Reel init failed: {init_resp.status_code} {init_resp.text[:200]}")
            return False
        
        reel_init = init_resp.json()
        upload_session_id = reel_init.get("video_id")
        upload_target = reel_init.get("upload_url", f"{GRAPH_URL}/{page_id}/video_reels")

        # Upload binary
        file_size = os.path.getsize(video_path)
        with open(video_path, "rb") as vf:
            upload_resp = requests.post(
                upload_target,
                headers={
                    "Authorization": f"OAuth {token}",
                    "offset": "0",
                    "file_size": str(file_size)
                },
                data=vf
            )
        if upload_resp.status_code not in (200, 201):
            print(f"[Facebook API] Reel upload failed: {upload_resp.status_code} {upload_resp.text[:200]}")
            return False

        # Finish / schedule
        finish_params = {
            "upload_phase": "finish",
            "video_id": upload_session_id,
            "access_token": token,
            "video_state": "SCHEDULED" if publish_at_dt else "PUBLISHED",
            "description": description,
            "title": title
        }
        if publish_at_dt:
            finish_params["scheduled_publish_time"] = int(publish_at_dt.astimezone(timezone.utc).timestamp())

        finish_resp = requests.post(upload_url, data=finish_params)
        if finish_resp.status_code == 200 and finish_resp.json().get("success"):
            print(f"[Facebook API] SUCCESS! Reel {'scheduled' if publish_at_dt else 'published'}.")
            return True
        else:
            print(f"[Facebook API] Finish failed: {finish_resp.status_code} {finish_resp.text[:300]}")
            return False

    else:
        # Upload as a regular long-form Video
        upload_url = f"{GRAPH_URL}/{page_id}/videos"
        params = {
            "title": title,
            "description": description,
            "access_token": token,
        }
        if publish_at_dt:
            params["scheduled_publish_time"] = int(publish_at_dt.astimezone(timezone.utc).timestamp())
            params["published"] = "false"

        files = {"source": (os.path.basename(video_path), open(video_path, "rb"), "video/mp4")}
        if thumbnail_path and os.path.exists(thumbnail_path) and not publish_at_dt:
            # Thumbnail can only be set post-upload on regular videos
            pass

        resp = requests.post(upload_url, data=params, files=files)
        
        if resp.status_code == 200 and resp.json().get("id"):
            video_id = resp.json()["id"]
            print(f"[Facebook API] SUCCESS! Video uploaded (ID: {video_id})")

            # Set thumbnail if long-form and provided
            if thumbnail_path and os.path.exists(thumbnail_path):
                with open(thumbnail_path, "rb") as tf:
                    thumb_resp = requests.post(
                        f"{GRAPH_URL}/{video_id}",
                        data={"access_token": token},
                        files={"thumb": tf}
                    )
                    if thumb_resp.status_code == 200:
                        print("[Facebook API] Thumbnail set.")
            return True
        else:
            print(f"[Facebook API] Upload failed: {resp.status_code} {resp.text[:300]}")
            return False
