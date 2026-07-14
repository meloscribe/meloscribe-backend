import os
import sys
import time
import sqlite3
from pathlib import Path
from fastapi import Request, HTTPException

# -------------------------------------------------------------------
# Paths & Environments
# -------------------------------------------------------------------
TOOLS_DIR = Path(__file__).resolve().parent.parent.parent  # c:\Dev\meloscribe-app\tools
SETTINGS_PATH = Path(__file__).resolve().parent / "settings.json"

sys.path.append(str(Path(__file__).resolve().parent))
try:
    from settings import load_settings
    settings = load_settings()
except Exception as e:
    print(f"Error loading settings in shared.py: {e}")
    settings = {}

db_path = Path(__file__).resolve().parent / "analytics.db"
CREATION_FLAGS = 0x08000000 if os.name == 'nt' else 0

import collections
import datetime
from fastapi import WebSocket

# -------------------------------------------------------------------
# Logs & Auditing
# -------------------------------------------------------------------
SYSTEM_LOGS = collections.deque(maxlen=100)
_error_log = collections.deque(maxlen=100)

def log_error(source: str, message: str = None, level: str = "error"):
    """Log an API or system error to in-memory deques and a persistent log file on disk."""
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    # 1. Write to local file
    try:
        log_file = Path(__file__).resolve().parent / "backend_logs.txt"
        with open(log_file, "a", encoding="utf-8") as lf:
            if message is None:
                lf.write(f"[{timestamp}] [SYSTEM] {source}\n")
            else:
                lf.write(f"[{timestamp}] [{level.upper()}] [{source}] {message}\n")
    except Exception as e:
        print("Failed to write to persistent backend_logs.txt:", e)

    # 2. Add to in-memory queues
    if message is None:
        SYSTEM_LOGS.appendleft({"time": timestamp, "msg": source})
        print(f"[SYSTEM] {source}")
        return
        
    entry = {
        "timestamp": timestamp,
        "source": source,
        "message": str(message)[:500],
        "level": level
    }
    _error_log.appendleft(entry)

# -------------------------------------------------------------------
# Shared State Singletons
# -------------------------------------------------------------------
class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: dict):
        dead = []
        for conn in self.active_connections:
            try:
                await conn.send_json(message)
            except Exception:
                dead.append(conn)
        for d in dead:
            self.disconnect(d)

manager = ConnectionManager()
active_websockets = []
log_buffer = []
active_workflow_task = {
    "task": None,
    "stop_event": None,
    "current_process": None,
    "stop_requested": False
}

# -------------------------------------------------------------------
# Helpers
# -------------------------------------------------------------------
def append_log(log_entry: dict):
    log_buffer.append(log_entry)
    if len(log_buffer) > 200:
        log_buffer.pop(0)

async def broadcast_log(msg: str):
    import asyncio
    dead_sockets = []
    for ws in list(active_websockets):
        try:
            await ws.send_text(msg)
        except Exception:
            dead_sockets.append(ws)
    for dead in dead_sockets:
        if dead in active_websockets:
            active_websockets.remove(dead)

def verify_admin(request: Request):
    passcode = request.headers.get("x-admin-passcode")
    expected = load_settings().get("admin_passcode", "579110")
    if passcode != expected:
        raise HTTPException(status_code=401, detail="Unauthorized admin access")

def get_server_api_key():
    try:
        key_path = Path(__file__).resolve().parent / "api_key.txt"
        if key_path.exists():
            return key_path.read_text(encoding="utf-8").strip()
    except Exception:
        pass
    return None

def get_stripe_api_key():
    settings = load_settings()
    is_sandbox = settings.get("environment", "sandbox") == "sandbox"
    if is_sandbox:
        return settings.get("stripe_sandbox_secret_key") or os.environ.get("STRIPE_SECRET_KEY")
    else:
        return settings.get("stripe_live_secret_key") or os.environ.get("STRIPE_SECRET_KEY")

def is_rate_limited(ip: str, endpoint: str, max_requests: int, window_seconds: int) -> bool:
    now = time.time()
    cutoff = now - window_seconds
    try:
        conn = sqlite3.connect(str(db_path), timeout=30.0)
        c = conn.cursor()
        c.execute("DELETE FROM rate_limits WHERE timestamp < ?", (cutoff,))
        c.execute("SELECT COUNT(*) FROM rate_limits WHERE ip = ? AND endpoint = ?", (ip, endpoint))
        count = c.fetchone()[0]
        if count >= max_requests:
            conn.close()
            return True
        c.execute("INSERT INTO rate_limits (ip, endpoint, timestamp) VALUES (?, ?, ?)", (ip, endpoint, now))
        conn.commit()
        conn.close()
        return False
    except Exception as e:
        print(f"[Rate Limit] Database error: {e}")
        return False
