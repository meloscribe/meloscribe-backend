"""
Threads API Poster
-------------------
Posts videos (Reels) and text to Threads using the Threads Graph API.
Supports video upload via temporary public hosting (required by Threads API).

Threads API requires a publicly accessible video URL, unlike Instagram/Facebook 
which accept binary uploads. We solve this by temporarily hosting the video 
on the existing FastAPI backend with a public endpoint.
"""
import os
import json
import time
import threading
import requests
from pathlib import Path
from http.server import HTTPServer, SimpleHTTPRequestHandler

TOKENS_PATH = Path(__file__).parent / "threads_tokens.json"
THREADS_API = "https://graph.threads.net/v1.0"


def _get_creds():
    if not TOKENS_PATH.exists():
        return None, None
    with open(TOKENS_PATH, "r") as f:
        d = json.load(f)
    return d.get("access_token"), d.get("threads_user_id")


def refresh_token():
    """Refresh the long-lived Threads token (call periodically, e.g. every 30 days)."""
    if not TOKENS_PATH.exists():
        return False
    with open(TOKENS_PATH, "r") as f:
        d = json.load(f)
    
    token = d.get("access_token")
    if not token:
        return False
    
    resp = requests.get(f"{THREADS_API.replace('/v1.0', '')}/refresh_access_token", params={
        "grant_type": "th_refresh_token",
        "access_token": token
    })
    
    if resp.status_code == 200:
        new_token = resp.json().get("access_token")
        if new_token:
            d["access_token"] = new_token
            with open(TOKENS_PATH, "w") as f:
                json.dump(d, f, indent=4)
            print(f"[Threads] Token refreshed successfully (expires in {resp.json().get('expires_in', '?')}s)")
            return True
    
    print(f"[Threads] Token refresh failed: {resp.status_code} {resp.text[:200]}")
    return False


def _upload_to_temp_host(video_path: str) -> str | None:
    """Upload video to a temporary file hosting service and return public URL."""
    print(f"[Threads] Uploading video to temporary host...")
    
    # 1. Try file.io
    try:
        print("[Threads] Trying file.io...")
        with open(video_path, "rb") as f:
            resp = requests.post(
                "https://file.io",
                files={"file": (os.path.basename(video_path), f, "video/mp4")},
                data={"expires": "1h"},
                timeout=120
            )
        if resp.status_code == 200 and resp.json().get("success"):
            url = resp.json().get("link")
            print(f"[Threads] Video uploaded to file.io: {url}")
            return url
        else:
            print(f"[Threads] file.io upload failed: {resp.text[:200]}")
    except Exception as e:
        print(f"[Threads] file.io upload error: {e}")

    # 2. Fallback to tmpfiles.org
    try:
        print("[Threads] Fallback: Trying tmpfiles.org...")
        with open(video_path, "rb") as f:
            resp = requests.post(
                "https://tmpfiles.org/api/v1/upload",
                files={"file": f},
                timeout=120
            )
        if resp.status_code == 200:
            data = resp.json()
            if data.get("status") == "success":
                view_url = data["data"]["url"]
                # Convert view URL to direct download URL (needed for Threads)
                # e.g., https://tmpfiles.org/w2w91vvJZUIL/morph.txt -> https://tmpfiles.org/dl/w2w91vvJZUIL/morph.txt
                direct_url = view_url.replace("tmpfiles.org/", "tmpfiles.org/dl/")
                print(f"[Threads] Video uploaded to tmpfiles.org: {direct_url}")
                return direct_url
            else:
                print(f"[Threads] tmpfiles.org upload failed: {data}")
        else:
            print(f"[Threads] tmpfiles.org HTTP failed: {resp.status_code} - {resp.text[:200]}")
    except Exception as e:
        print(f"[Threads] tmpfiles.org fallback error: {e}")

    return None


def post_video(video_path: str, caption: str) -> bool:
    """
    Upload a video to Threads as a video post.
    Threads requires a publicly accessible video URL.
    """
    if not os.path.exists(video_path):
        print(f"[Threads API] ERROR: Video not found at {video_path}")
        return False

    token, user_id = _get_creds()
    if not token or not user_id:
        print("[Threads API] ERROR: No valid Threads token. Save threads_tokens.json first.")
        return False

    # Step 0: Upload video to temp host
    video_url = _upload_to_temp_host(video_path)
    if not video_url:
        print("[Threads API] Could not upload video to temporary host.")
        return False

    # Step 1: Create media container
    print(f"[Threads API] Creating video container...")
    container_resp = requests.post(
        f"{THREADS_API}/{user_id}/threads",
        params={
            "media_type": "VIDEO",
            "video_url": video_url,
            "text": caption,
            "access_token": token
        }
    )

    if container_resp.status_code != 200:
        print(f"[Threads API] Container creation failed: {container_resp.status_code} {container_resp.text[:300]}")
        return False

    container_id = container_resp.json().get("id")
    if not container_id:
        print(f"[Threads API] No container ID returned: {container_resp.text[:200]}")
        return False

    print(f"[Threads API] Container created (ID: {container_id}). Waiting for processing...")

    # Step 2: Wait for processing
    for attempt in range(20):
        time.sleep(15)  # Threads video processing takes longer
        status_resp = requests.get(
            f"{THREADS_API}/{container_id}",
            params={"fields": "status", "access_token": token}
        )
        if status_resp.status_code != 200:
            print(f"  Status check failed: {status_resp.text[:100]}")
            continue
            
        status = status_resp.json().get("status", "")
        print(f"  Container status: {status} (attempt {attempt+1}/20)")

        if status == "FINISHED":
            break
        elif status in ("ERROR", "EXPIRED"):
            print(f"[Threads API] Container failed: {status_resp.json()}")
            return False
    else:
        print("[Threads API] Timed out waiting for video processing.")
        return False

    # Step 3: Publish
    print("[Threads API] Publishing...")
    publish_resp = requests.post(
        f"{THREADS_API}/{user_id}/threads_publish",
        params={
            "creation_id": container_id,
            "access_token": token
        }
    )

    if publish_resp.status_code == 200 and publish_resp.json().get("id"):
        post_id = publish_resp.json()["id"]
        print(f"[Threads API] SUCCESS! Video posted (Post ID: {post_id})")
        return True
    else:
        print(f"[Threads API] Publish failed: {publish_resp.status_code} {publish_resp.text[:300]}")
        return False


def post_text(text: str) -> bool:
    """Post a text-only post to Threads."""
    token, user_id = _get_creds()
    if not token or not user_id:
        print("[Threads API] ERROR: No valid Threads token.")
        return False

    # Step 1: Create container
    container_resp = requests.post(
        f"{THREADS_API}/{user_id}/threads",
        params={
            "media_type": "TEXT",
            "text": text,
            "access_token": token
        }
    )

    if container_resp.status_code != 200:
        print(f"[Threads API] Text container failed: {container_resp.text[:200]}")
        return False

    container_id = container_resp.json().get("id")
    
    # Step 2: Publish (text is usually instant)
    time.sleep(2)
    publish_resp = requests.post(
        f"{THREADS_API}/{user_id}/threads_publish",
        params={"creation_id": container_id, "access_token": token}
    )

    if publish_resp.status_code == 200 and publish_resp.json().get("id"):
        print(f"[Threads API] Text post published!")
        return True
    else:
        print(f"[Threads API] Text publish failed: {publish_resp.text[:200]}")
        return False


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--refresh":
        refresh_token()
    else:
        print("Threads poster ready. Use post_video() or post_text().")
        print(f"Token file: {TOKENS_PATH}")
        token, uid = _get_creds()
        if token and uid:
            print(f"User ID: {uid}")
            r = requests.get(f"{THREADS_API}/me", params={"fields": "id,username", "access_token": token})
            print(f"Account: {r.json()}")
        else:
            print("No credentials found.")
