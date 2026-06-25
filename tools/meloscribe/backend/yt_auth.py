"""
YouTube API Authentication
--------------------------
Handles the OAuth 2.0 flow for YouTube Data API v3.
Runs a local server to catch the redirect.
"""
import os
import json
import socket
import webbrowser
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from google_auth_oauthlib.flow import InstalledAppFlow
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request

# Scopes for Uploading and Analytics
SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/yt-analytics.readonly"
]

SECRETS_PATH = Path(__file__).parent / "yt_client_secret.json"
TOKENS_PATH = Path(__file__).parent / "yt_tokens.json"


def _open_browser(url: str):
    try:
        import os
        if hasattr(os, "startfile"):
            os.startfile(url)
            print(f"[Auth] Opened browser via os.startfile: {url}")
            return
    except Exception as e:
        print(f"[Auth] os.startfile failed: {e}")

    try:
        import sys
        sys.path.insert(0, str(Path(__file__).parent))
        from settings import load_settings
        settings = load_settings()
        browser_exe = settings.get("browser_exec")
        if browser_exe and os.path.exists(browser_exe):
            import subprocess
            subprocess.Popen([browser_exe, url])
            print(f"[Auth] Opened browser via executable: {browser_exe}")
            return
    except Exception as e:
        print(f"[Auth] Failed to launch configured browser: {e}")
        
    import webbrowser
    webbrowser.open(url)
    print("[Auth] Opened browser via system default webbrowser module")

def get_authenticated_service():
    """
    Returns valid Google Credentials. 
    If none exist or they are expired, it triggers the OAuth flow.
    """
    creds = None
    
    # Load existing tokens if available
    if TOKENS_PATH.exists():
        try:
            with open(TOKENS_PATH, "r") as f:
                creds_data = json.load(f)
            creds = Credentials.from_authorized_user_info(creds_data, SCOPES)
        except Exception as e:
            print(f"[YouTube Auth] Error loading existing tokens: {e}")

    # If no valid credentials available, let the user log in.
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            print("[YouTube Auth] Refreshing expired token...")
            try:
                creds.refresh(Request())
            except Exception as e:
                print(f"[YouTube Auth] Refresh failed, triggering full auth: {e}")
                creds = None
                
        if not creds:
            if not SECRETS_PATH.exists():
                raise FileNotFoundError(f"Missing {SECRETS_PATH.name}! Please place the Google OAuth JSON file here.")
                
            print("[YouTube Auth] Starting new OAuth flow. Check your browser...", flush=True)
            flow = InstalledAppFlow.from_client_secrets_file(str(SECRETS_PATH), SCOPES)
            
            class CustomBrowser:
                def open(self, url):
                    _open_browser(url)
                    return True
                    
            creds = flow.run_local_server(port=0, browser=CustomBrowser())

        # Save the credentials for the next run
        print(f"[YouTube Auth] Saving new tokens to {TOKENS_PATH.name}...", flush=True)
        with open(TOKENS_PATH, "w") as f:
            f.write(creds.to_json())

    return creds

if __name__ == "__main__":
    print("--- YouTube API Setup ---")
    creds = get_authenticated_service()
    if creds and creds.valid:
        print("[OK] YouTube is successfully authenticated!")
    else:
        print("[X] YouTube authentication failed.")
