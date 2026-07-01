"""
TikTok One-Time OAuth Setup — Static ngrok Domain
---------------------------------------------------
Uses a permanent static ngrok domain so the redirect URI never changes.
Run once: python tiktok_setup.py
After this, tiktok_sync.py handles everything automatically.
"""
import json
import time
import base64
import hashlib
import secrets
import subprocess
import threading
import webbrowser
import urllib.parse
import http.server
import requests
from pathlib import Path

# -------------------------------------------------------
NGROK_EXE      = Path(__file__).parent.parent.parent / "ngrok" / "ngrok.exe"
NGROK_DOMAIN   = "wooing-encrust-ladle.ngrok-free.dev"   # Permanent static domain
CLIENT_KEY     = "sbawwonaqqe71vhfgd"
CLIENT_SECRET  = ""
LOCAL_PORT     = 8080
REDIRECT_URI   = f"https://{NGROK_DOMAIN}/callback"      # Never changes
SCOPES         = "user.info.basic,video.list"
TOKENS_PATH    = Path(__file__).parent / "tiktok_tokens.json"
TOKEN_URL      = "https://open.tiktokapis.com/v2/oauth/token/"
AUTH_BASE      = "https://www.tiktok.com/v2/auth/authorize/"

try:
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from settings import load_settings
    _settings = load_settings()
    if _settings.get("tiktok_client_key"):
        CLIENT_KEY = str(_settings.get("tiktok_client_key"))
    if _settings.get("tiktok_client_secret"):
        CLIENT_SECRET = str(_settings.get("tiktok_client_secret"))
except Exception:
    pass
# -------------------------------------------------------


def _generate_pkce():
    code_verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b'=').decode('ascii')
    digest = hashlib.sha256(code_verifier.encode('ascii')).digest()
    code_challenge = base64.urlsafe_b64encode(digest).rstrip(b'=').decode('ascii')
    return code_verifier, code_challenge


def _start_ngrok():
    """Start ngrok with the static domain."""
    print(f"[ngrok] Starting tunnel: {REDIRECT_URI}")
    proc = subprocess.Popen(
        [str(NGROK_EXE), "http", f"--domain={NGROK_DOMAIN}", str(LOCAL_PORT)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW
    )
    time.sleep(3)
    # Verify tunnel is up
    for _ in range(10):
        try:
            resp = requests.get("http://localhost:4040/api/tunnels", timeout=2)
            tunnels = resp.json().get("tunnels", [])
            if any(NGROK_DOMAIN in t.get("public_url", "") for t in tunnels):
                print(f"[ngrok] Tunnel confirmed active!")
                return proc
        except:
            pass
        time.sleep(1)
    print("[ngrok] Warning: could not confirm tunnel, continuing anyway...")
    return proc


def run_setup():
    if TOKENS_PATH.exists():
        print(f"[Setup] Tokens already exist — delete tiktok_tokens.json to re-authorize.")
        return

    print(f"\n{'='*60}")
    print(f"Redirect URI (already registered in TikTok portal):")
    print(f"  {REDIRECT_URI}")
    print(f"{'='*60}\n")

    # 1. Start ngrok with static domain
    ngrok_proc = _start_ngrok()

    # 2. Generate PKCE
    code_verifier, code_challenge = _generate_pkce()
    captured = {}

    # 3. Start local callback server
    class CallbackHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = urllib.parse.urlparse(self.path)
            params = urllib.parse.parse_qs(parsed.query)
            if "code" in params:
                captured["code"] = params["code"][0]
                self.send_response(200)
                self.end_headers()
                self.wfile.write(
                    b"<h1 style='font-family:sans-serif;color:green;padding:40px'>"
                    b"TikTok Authorization Successful!<br>"
                    b"<small>You can close this tab.</small></h1>"
                )
                print("\n[Setup] Authorization code received!")
            else:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b"<h1>No code received.</h1>")
        def log_message(self, *args): pass

    server = http.server.HTTPServer(("0.0.0.0", LOCAL_PORT), CallbackHandler)
    server_thread = threading.Thread(target=server.handle_request)
    server_thread.daemon = True
    server_thread.start()

    # 4. Build auth URL and open browser
    auth_url = AUTH_BASE + "?" + urllib.parse.urlencode({
        "client_key":            CLIENT_KEY,
        "response_type":         "code",
        "scope":                 SCOPES,
        "redirect_uri":          REDIRECT_URI,
        "state":                 "meloscribe",
        "code_challenge":        code_challenge,
        "code_challenge_method": "S256",
    })

    print("[Setup] Opening TikTok login in browser...")
    webbrowser.open(auth_url)
    print("[Setup] Waiting for you to log in (max 120s)...")

    server_thread.join(timeout=120)

    if "code" not in captured:
        print("[Setup] ERROR: No code received within 120 seconds.")
        ngrok_proc.terminate()
        return

    code = captured["code"]
    print(f"[Setup] Exchanging code for tokens...")

    # 5. Exchange code → tokens
    resp = requests.post(TOKEN_URL, data={
        "client_key":    CLIENT_KEY,
        "client_secret": CLIENT_SECRET,
        "code":          code,
        "grant_type":    "authorization_code",
        "redirect_uri":  REDIRECT_URI,
        "code_verifier": code_verifier,
    }, headers={"Content-Type": "application/x-www-form-urlencoded"})

    ngrok_proc.terminate()
    print("[ngrok] Tunnel closed.")

    if resp.status_code != 200:
        print(f"[Setup] Token exchange failed: {resp.status_code} — {resp.text}")
        return

    data = resp.json()
    tokens = {
        "access_token":  data["access_token"],
        "refresh_token": data["refresh_token"],
        "open_id":       data["open_id"],
        "expires_at":    time.time() + data.get("expires_in", 86400),
    }
    with open(TOKENS_PATH, "w") as f:
        json.dump(tokens, f, indent=2)

    print(f"\n{'='*60}")
    print("SUCCESS! TikTok authorization complete.")
    print(f"Tokens saved → {TOKENS_PATH}")
    print("Meloscribe will now auto-sync TikTok stats on every startup.")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    print("=== Meloscribe TikTok Setup ===")
    run_setup()
