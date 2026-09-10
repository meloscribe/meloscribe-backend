"""
Instagram Graph API Poster
--------------------------
Uploads Reels to Instagram using the Graph API.
Supports scheduling via scheduled_publish_time.
"""
import os
import json
import time
import subprocess
import requests
from pathlib import Path
from datetime import datetime, timezone

TOKENS_PATH = Path(__file__).parent / "ig_tokens.json"
GRAPH_URL = "https://graph.facebook.com/v19.0"


def _get_video_duration_ms(video_path: str) -> int:
    """Get video duration in milliseconds using ffprobe."""
    try:
        cmd = ['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
               '-of', 'default=noprint_wrappers=1:nokey=1', video_path]
        result = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8')
        return int(float(result.stdout.strip()) * 1000)
    except Exception:
        return 0


def _get_creds():
    if not TOKENS_PATH.exists():
        return None, None
    with open(TOKENS_PATH, "r") as f:
        d = json.load(f)
    return d.get("access_token"), d.get("ig_business_id")


def post_reel(video_path: str, caption: str, publish_at_dt: datetime = None) -> bool:
    """
    Upload a Reel to Instagram.
    If publish_at_dt is given, the post is scheduled (must be 10min–75 days in the future).
    Returns True on success.
    """
    if not os.path.exists(video_path):
        print(f"[Instagram API] ERROR: Video not found at {video_path}")
        return False

    token, ig_id = _get_creds()
    if not token or not ig_id:
        print("[Instagram API] ERROR: No valid IG token. Run ig_setup.py first.")
        return False

    # Instagram requires a publicly accessible video URL.
    # We upload the video to Cloudflare R2 temporarily to get a presigned URL.
    from settings import load_settings
    settings = load_settings()
    
    r2_account_id = settings.get("r2_account_id")
    r2_access_key = settings.get("r2_access_key") or settings.get("r2_access_key_id")
    r2_secret_key = settings.get("r2_secret_key") or settings.get("r2_secret_access_key")
    r2_bucket     = settings.get("r2_bucket") or settings.get("r2_bucket_name", "meloscribe-sheets")
    
    if not r2_account_id or not r2_access_key or not r2_secret_key:
        print("[Instagram API] ERROR: R2 credentials missing in settings.json!")
        return False
        
    import boto3
    from botocore.config import Config
    import uuid
    
    s3 = boto3.client(
        's3',
        endpoint_url=f'https://{r2_account_id}.r2.cloudflarestorage.com',
        aws_access_key_id=r2_access_key,
        aws_secret_access_key=r2_secret_key,
        region_name='auto',
        config=Config(signature_version='s3v4')
    )
    
    filename = os.path.basename(video_path)
    temp_key = f"temp_uploads/{uuid.uuid4().hex[:8]}_{filename}"
    
    try:
        print(f"[Instagram API] Uploading temporary video to R2: {temp_key}...")
        s3.upload_file(video_path, r2_bucket, temp_key, ExtraArgs={"ContentType": "video/mp4"})
        
        video_url = s3.generate_presigned_url(
            ClientMethod='get_object',
            Params={'Bucket': r2_bucket, 'Key': temp_key},
            ExpiresIn=3600
        )
        print(f"[Instagram API] Generated public R2 video URL.")
        
        # Step 1: Create a video container
        print(f"\n[Instagram API] Creating Reel container for '{filename}'...")
        
        container_params = {
            "media_type": "REELS",
            "caption": caption,
            "access_token": token,
            "share_to_feed": "true",
            "video_url": video_url
        }

        # Set cover to the last frame of the video (avoid black fade-in as cover)
        duration_ms = _get_video_duration_ms(video_path)
        if duration_ms > 0:
            container_params["thumb_offset"] = max(0, duration_ms - 100)  # 100ms before end
            print(f"[Instagram API] Cover set to last frame (offset: {container_params['thumb_offset']}ms)")

        if publish_at_dt:
            unix_ts = int(publish_at_dt.astimezone(timezone.utc).timestamp())
            container_params["scheduled_publish_time"] = unix_ts
            container_params["media_type"] = "REELS"  # Must be reels for scheduling
            print(f"[Instagram API] Scheduling for {publish_at_dt.strftime('%Y-%m-%d %H:%M')} UTC")

        upload_resp = requests.post(
            f"{GRAPH_URL}/{ig_id}/media",
            data=container_params
        )

        if upload_resp.status_code != 200:
            print(f"[Instagram API] Container creation failed: {upload_resp.status_code} {upload_resp.text[:300]}")
            return False

        container_id = upload_resp.json().get("id")
        if not container_id:
            print(f"[Instagram API] No container ID in response: {upload_resp.text[:200]}")
            return False

        print(f"[Instagram API] Container created (ID: {container_id}). Waiting for processing...")

        # Step 2: Wait for container to be ready
        for attempt in range(15):
            time.sleep(10)
            status_resp = requests.get(
                f"{GRAPH_URL}/{container_id}",
                params={"fields": "status_code,status", "access_token": token}
            )
            status_data = status_resp.json()
            status_code = status_data.get("status_code", "")
            print(f"  Container status: {status_code} (attempt {attempt+1}/15)")

            if status_code == "FINISHED":
                break
            elif status_code in ("ERROR", "EXPIRED"):
                print(f"[Instagram API] Container failed: {status_data}")
                return False

        # Step 3: Publish
        publish_resp = requests.post(
            f"{GRAPH_URL}/{ig_id}/media_publish",
            data={"creation_id": container_id, "access_token": token}
        )

        if publish_resp.status_code == 200 and publish_resp.json().get("id"):
            post_id = publish_resp.json()["id"]
            print(f"[Instagram API] SUCCESS! Reel published (Post ID: {post_id})")
            return True
        else:
            print(f"[Instagram API] Publish failed: {publish_resp.status_code} {publish_resp.text[:300]}")
            return False
            
    finally:
        try:
            print(f"[Instagram API] Deleting temporary R2 file: {temp_key}...")
            s3.delete_object(Bucket=r2_bucket, Key=temp_key)
        except Exception as del_err:
            print(f"[Instagram API] Warning: Failed to delete temp R2 file: {del_err}")
