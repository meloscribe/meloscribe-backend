"""
Threads OAuth Flow + Token Manager
----------------------------------
Opens browser for Threads Login, captures code via ngrok proxy,
exchanges it for a short-lived token, then exchanges it for a 60-day long-lived token,
and saves the credentials.
"""
import os
import json
import time
import threading
import webbrowser
import urllib.parse
import http.server
import requests
from pathlib import Path

APP_ID = "26975285422066567"  # Meta App ID is also Threads Client ID
APP_SECRET = "70752bda986825b8e63b8ad2c07c93fc"  # Meta App Secret
REDIRECT_URI = "https://wooing-encrust-ladle.ngrok-free.dev/callback"
SCOPES = "threads_basic,threads_content_publish,threads_manage_insights"
TOKENS_PATH = Path(__file__).parent / "threads_tokens.json"

try:
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from settings import load_settings
    _settings = load_settings()
    if _settings.get("threads_app_id"):
        APP_ID = str(_settings.get("threads_app_id"))
    if _settings.get("threads_app_secret"):
        APP_SECRET = str(_settings.get("threads_app_secret"))
except Exception as _e:
    print(f"[Threads Auth] Warning: could not load app credentials from settings: {_e}")

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

def run_threads_auth() -> bool:
    captured = {}

    class CallbackHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = urllib.parse.urlparse(self.path)
            params = urllib.parse.parse_qs(parsed.query)
            if "code" in params:
                captured["code"] = params["code"][0]
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"<h1>Authorization successful! You can close this tab.</h1>")
            else:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b"<h1>No code received.</h1>")
        def log_message(self, *args): pass

    server = http.server.HTTPServer(("localhost", 8080), CallbackHandler)
    server_thread = threading.Thread(target=server.handle_request)
    server_thread.daemon = True
    server_thread.start()

    auth_url = (
        "https://www.threads.net/oauth/authorize?"
        + urllib.parse.urlencode({
            "client_id": APP_ID,
            "redirect_uri": REDIRECT_URI,
            "scope": SCOPES,
            "response_type": "code",
            "state": "threads"
        })
    )

    print(f"[Threads Auth] Opening browser for authorization...")
    _open_browser(auth_url)

    print("[Threads Auth] Waiting for callback (max 120s)...")
    server_thread.join(timeout=120)

    if "code" not in captured:
        print("[Threads Auth] ERROR: No authorization code received within timeout.")
        return False

    code = captured["code"]
    print(f"[Threads Auth] Code received. Exchanging for short-lived token...")

    # Exchange code for short-lived token
    resp = requests.post("https://graph.threads.net/oauth/access_token", data={
        "client_id": APP_ID,
        "client_secret": APP_SECRET,
        "grant_type": "authorization_code",
        "redirect_uri": REDIRECT_URI,
        "code": code
    })

    if resp.status_code != 200:
        print(f"[Threads Auth] Short-lived exchange failed: {resp.status_code} - {resp.text}")
        return False

    short_token_data = resp.json()
    short_lived_token = short_token_data.get("access_token")
    threads_user_id = short_token_data.get("user_id")

    if not short_lived_token or not threads_user_id:
        print("[Threads Auth] Missing access token or user ID in short-lived response.")
        return False

    print("[Threads Auth] Exchanging short-lived token for long-lived token...")
    # Exchange short-lived token for long-lived token
    long_resp = requests.get("https://graph.threads.net/access_token", params={
        "grant_type": "th_exchange_token",
        "client_secret": APP_SECRET,
        "access_token": short_lived_token
    })

    if long_resp.status_code != 200:
        print(f"[Threads Auth] Long-lived exchange failed: {long_resp.status_code} - {long_resp.text}")
        return False

    long_token_data = long_resp.json()
    long_lived_token = long_token_data.get("access_token")

    if not long_lived_token:
        print("[Threads Auth] Missing access token in long-lived response.")
        return False

    # Retrieve username
    print("[Threads Auth] Retrieving Threads profile details...")
    me_resp = requests.get("https://graph.threads.net/v1.0/me", params={
        "fields": "id,username",
        "access_token": long_lived_token
    })

    username = "unknown"
    if me_resp.status_code == 200:
        username = me_resp.json().get("username", "unknown")

    # Save tokens
    save_data = {
        "access_token": long_lived_token,
        "threads_user_id": str(threads_user_id),
        "username": username
    }

    with open(TOKENS_PATH, "w") as f:
        json.dump(save_data, f, indent=4)

    print(f"[Threads Auth] SUCCESS! Connected as '{username}'. Saved to {TOKENS_PATH.name}")
    return True

if __name__ == "__main__":
    run_threads_auth()
