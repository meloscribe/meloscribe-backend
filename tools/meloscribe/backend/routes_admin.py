import os
import sys
import json
import sqlite3
import platform
import requests
import boto3
import datetime
import collections
from pathlib import Path
from fastapi import APIRouter, Request, HTTPException, Form, UploadFile, File, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from shared import (
    settings,
    db_path,
    verify_admin,
    get_server_api_key,
    TOOLS_DIR
)

router = APIRouter()

VM_API_BASE = "https://api.meloscribe.dev"

def get_proxy_headers():
    headers = {}
    api_key = get_server_api_key()
    if api_key:
        headers["X-Meloscribe-Key"] = api_key
    return headers

if platform.system() == "Windows":
    # -------------------------------------------------------------------
    # Local Windows Admin Proxy Router
    # -------------------------------------------------------------------
    @router.get("/api/notify/subscribers")
    def get_local_subscribers(request: Request):
        try:
            headers = get_proxy_headers()
            if "x-admin-passcode" in request.headers:
                headers["x-admin-passcode"] = request.headers["x-admin-passcode"]
            r = requests.get(f"{VM_API_BASE}/api/notify/subscribers", headers=headers, timeout=5.0)
            return JSONResponse(content=r.json(), status_code=r.status_code)
        except Exception as e:
            return JSONResponse(content={"error": f"Proxy error: {e}"}, status_code=500)

    @router.get("/api/stripe/sales")
    def get_local_stripe_sales(request: Request):
        try:
            headers = get_proxy_headers()
            if "x-admin-passcode" in request.headers:
                headers["x-admin-passcode"] = request.headers["x-admin-passcode"]
            r = requests.get(f"{VM_API_BASE}/api/stripe/sales", headers=headers, timeout=5.0)
            return JSONResponse(content=r.json(), status_code=r.status_code)
        except Exception as e:
            return JSONResponse(content={"error": f"Proxy error: {e}"}, status_code=500)

    @router.get("/api/paddle/sales")
    def get_local_paddle_sales(request: Request):
        try:
            headers = get_proxy_headers()
            if "x-admin-passcode" in request.headers:
                headers["x-admin-passcode"] = request.headers["x-admin-passcode"]
            r = requests.get(f"{VM_API_BASE}/api/paddle/sales", headers=headers, timeout=5.0)
            return JSONResponse(content=r.json(), status_code=r.status_code)
        except Exception as e:
            return JSONResponse(content={"error": f"Proxy error: {e}"}, status_code=500)

    @router.delete("/api/public/suggestions/{sug_id}")
    def delete_suggestion_proxy(sug_id: str, request: Request):
        try:
            headers = get_proxy_headers()
            if "x-admin-passcode" in request.headers:
                headers["x-admin-passcode"] = request.headers["x-admin-passcode"]
            r = requests.delete(f"{VM_API_BASE}/api/public/suggestions/{sug_id}", headers=headers, timeout=5.0)
            return JSONResponse(content=r.json(), status_code=r.status_code)
        except Exception as e:
            return JSONResponse(content={"error": f"Proxy error: {e}"}, status_code=500)

    @router.get("/api/analytics")
    def get_local_analytics(request: Request, range: str = "30d"):
        try:
            headers = get_proxy_headers()
            if "x-admin-passcode" in request.headers:
                headers["x-admin-passcode"] = request.headers["x-admin-passcode"]
            r = requests.get(f"{VM_API_BASE}/api/analytics?range={range}", headers=headers, timeout=5.0)
            return JSONResponse(content=r.json(), status_code=r.status_code)
        except Exception as e:
            return JSONResponse(content={"error": f"Proxy error: {e}"}, status_code=500)

    @router.post("/api/demographics/sync")
    def sync_local_demographics(request: Request):
        try:
            headers = get_proxy_headers()
            if "x-admin-passcode" in request.headers:
                headers["x-admin-passcode"] = request.headers["x-admin-passcode"]
            r = requests.post(f"{VM_API_BASE}/api/demographics/sync", headers=headers, timeout=180.0)
            return JSONResponse(content=r.json(), status_code=r.status_code)
        except Exception as e:
            return JSONResponse(content={"error": f"Proxy error: {e}"}, status_code=500)

    @router.get("/api/todos")
    def get_local_todos(request: Request):
        try:
            headers = get_proxy_headers()
            if "x-admin-passcode" in request.headers:
                headers["x-admin-passcode"] = request.headers["x-admin-passcode"]
            r = requests.get(f"{VM_API_BASE}/api/todos", headers=headers, timeout=10.0)
            return JSONResponse(content=r.json(), status_code=r.status_code)
        except Exception as e:
            return JSONResponse(content={"error": f"Proxy error: {e}"}, status_code=500)

    @router.post("/api/todos")
    async def add_local_todo(request: Request):
        try:
            headers = get_proxy_headers()
            if "x-admin-passcode" in request.headers:
                headers["x-admin-passcode"] = request.headers["x-admin-passcode"]
            if "content-type" in request.headers:
                headers["content-type"] = request.headers["content-type"]
            body = await request.body()
            r = requests.post(f"{VM_API_BASE}/api/todos", data=body, headers=headers, timeout=10.0)
            return JSONResponse(content=r.json(), status_code=r.status_code)
        except Exception as e:
            return JSONResponse(content={"error": f"Proxy error: {e}"}, status_code=500)

    @router.delete("/api/todos/{todo_id}")
    def delete_local_todo(todo_id: int, request: Request):
        try:
            headers = get_proxy_headers()
            if "x-admin-passcode" in request.headers:
                headers["x-admin-passcode"] = request.headers["x-admin-passcode"]
            r = requests.delete(f"{VM_API_BASE}/api/todos/{todo_id}", headers=headers, timeout=10.0)
            return JSONResponse(content=r.json(), status_code=r.status_code)
        except Exception as e:
            return JSONResponse(content={"error": f"Proxy error: {e}"}, status_code=500)

    @router.get("/api/dismissed-suggestions")
    def get_local_dismissed(request: Request):
        try:
            headers = get_proxy_headers()
            if "x-admin-passcode" in request.headers:
                headers["x-admin-passcode"] = request.headers["x-admin-passcode"]
            r = requests.get(f"{VM_API_BASE}/api/dismissed-suggestions", headers=headers, timeout=10.0)
            return JSONResponse(content=r.json(), status_code=r.status_code)
        except Exception as e:
            return JSONResponse(content={"error": f"Proxy error: {e}"}, status_code=500)

    @router.post("/api/dismissed-suggestions")
    async def dismiss_local_suggestion(request: Request):
        try:
            headers = get_proxy_headers()
            if "x-admin-passcode" in request.headers:
                headers["x-admin-passcode"] = request.headers["x-admin-passcode"]
            if "content-type" in request.headers:
                headers["content-type"] = request.headers["content-type"]
            body = await request.body()
            r = requests.post(f"{VM_API_BASE}/api/dismissed-suggestions", data=body, headers=headers, timeout=10.0)
            return JSONResponse(content=r.json(), status_code=r.status_code)
        except Exception as e:
            return JSONResponse(content={"error": f"Proxy error: {e}"}, status_code=500)

    @router.get("/api/ai/briefing")
    def get_local_ai_briefing(request: Request):
        try:
            headers = get_proxy_headers()
            if "x-admin-passcode" in request.headers:
                headers["x-admin-passcode"] = request.headers["x-admin-passcode"]
            r = requests.get(f"{VM_API_BASE}/api/ai/briefing", headers=headers, timeout=30.0)
            return JSONResponse(content=r.json(), status_code=r.status_code)
        except Exception as e:
            return JSONResponse(content={"error": f"Proxy error: {e}"}, status_code=500)

    @router.post("/api/ai/briefing/force")
    def force_local_ai_briefing(request: Request):
        try:
            headers = get_proxy_headers()
            if "x-admin-passcode" in request.headers:
                headers["x-admin-passcode"] = request.headers["x-admin-passcode"]
            r = requests.post(f"{VM_API_BASE}/api/ai/briefing/force", headers=headers, timeout=90.0)
            return JSONResponse(content=r.json(), status_code=r.status_code)
        except Exception as e:
            return JSONResponse(content={"error": f"Proxy error: {e}"}, status_code=500)

    @router.post("/api/ai/chat")
    async def chat_local_with_ai(request: Request):
        try:
            headers = get_proxy_headers()
            if "x-admin-passcode" in request.headers:
                headers["x-admin-passcode"] = request.headers["x-admin-passcode"]
            if "content-type" in request.headers:
                headers["content-type"] = request.headers["content-type"]
            body = await request.body()
            r = requests.post(f"{VM_API_BASE}/api/ai/chat", data=body, headers=headers, timeout=60.0)
            return JSONResponse(content=r.json(), status_code=r.status_code)
        except Exception as e:
            return JSONResponse(content={"error": f"Proxy error: {e}"}, status_code=500)

    @router.post("/api/actions/run")
    def run_local_action_engine(request: Request):
        try:
            headers = get_proxy_headers()
            if "x-admin-passcode" in request.headers:
                headers["x-admin-passcode"] = request.headers["x-admin-passcode"]
            r = requests.post(f"{VM_API_BASE}/api/actions/run", headers=headers, timeout=30.0)
            return JSONResponse(content=r.json(), status_code=r.status_code)
        except Exception as e:
            return JSONResponse(content={"error": f"Proxy error: {e}"}, status_code=500)

    @router.delete("/api/admin/orders/{transaction_id}")
    def delete_local_order(transaction_id: str, request: Request):
        try:
            headers = get_proxy_headers()
            if "x-admin-passcode" in request.headers:
                headers["x-admin-passcode"] = request.headers["x-admin-passcode"]
            r = requests.delete(f"{VM_API_BASE}/api/admin/orders/{transaction_id}", headers=headers, timeout=10.0)
            return JSONResponse(content=r.json(), status_code=r.status_code)
        except Exception as e:
            return JSONResponse(content={"error": f"Proxy error: {e}"}, status_code=500)

    @router.api_route("/api/admin/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
    async def proxy_admin_routes(path: str, request: Request):
        try:
            method = request.method
            headers = get_proxy_headers()
            
            if "x-admin-passcode" in request.headers:
                headers["x-admin-passcode"] = request.headers["x-admin-passcode"]
                
            if "content-type" in request.headers:
                headers["content-type"] = request.headers["content-type"]

            url = f"{VM_API_BASE}/api/admin/{path}"
            params = dict(request.query_params)
            body = await request.body()
            
            r = requests.request(
                method=method,
                url=url,
                headers=headers,
                params=params,
                data=body,
                timeout=15.0
            )
            
            try:
                content = r.json()
                return JSONResponse(content=content, status_code=r.status_code)
            except Exception:
                return Response(content=r.content, status_code=r.status_code, media_type=r.headers.get("content-type"))
        except Exception as e:
            return JSONResponse(content={"error": f"Proxy error: {e}"}, status_code=500)

    CREATION_FLAGS = 0x08000000

    class ServerActionRequest(BaseModel):
        action: str

    @router.get("/api/server/sniper-status")
    def get_sniper_status():
        import subprocess
        key_path = r"C:\Dev\ssh-key-2026-05-07.key"
        server_ip = "152.70.23.171"
        if not os.path.exists(key_path):
            return {"status": "error", "message": f"SSH Key not found at {key_path}"}
        
        cmd = [
            "ssh", "-i", key_path, 
            "-o", "StrictHostKeyChecking=accept-new", 
            "-o", "ConnectTimeout=5", 
            "-o", "IdentitiesOnly=yes", 
            f"ubuntu@{server_ip}", 
            "systemctl is-active oci-sniper && echo --- LOGS --- && tail -n 100 /home/ubuntu/oci-sniper/sniper.log 2>/dev/null"
        ]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=12, creationflags=CREATION_FLAGS)
            if res.returncode == 0 or "inactive" in res.stdout or "active" in res.stdout:
                stdout_str = res.stdout.strip()
                status_line = "inactive"
                logs_content = "No logs available."
                if "--- LOGS ---" in stdout_str:
                    parts = stdout_str.split("--- LOGS ---", 1)
                    status_line = parts[0].strip()
                    logs_content = parts[1].strip()
                else:
                    status_line = stdout_str
                
                is_active = status_line == "active"
                return {
                    "status": "success" if is_active else "warning",
                    "output": f"Service is-active: {status_line}\n\n--- RECENT LOGS ---\n{logs_content}"
                }
            else:
                status_line = res.stdout.strip() if res.stdout else "unknown"
                return {
                    "status": "warning" if status_line == "inactive" else "error",
                    "output": f"Service status: {status_line}\nErrors: {res.stderr}"
                }
        except subprocess.TimeoutExpired:
            return {"status": "error", "message": "Connection timed out"}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    @router.post("/api/server/sniper-action")
    def run_sniper_action(req: ServerActionRequest):
        import subprocess
        key_path = r"C:\Dev\ssh-key-2026-05-07.key"
        server_ip = "152.70.23.171"
        if not os.path.exists(key_path):
            return {"status": "error", "message": f"SSH Key not found at {key_path}"}
        
        if req.action not in ("start", "stop", "restart"):
            return {"status": "error", "message": "Invalid action"}
            
        ssh_cmd = f"sudo systemctl {req.action} oci-sniper"
        
        cmd = [
            "ssh", "-i", key_path, 
            "-o", "StrictHostKeyChecking=accept-new", 
            "-o", "ConnectTimeout=5", 
            "-o", "IdentitiesOnly=yes", 
            f"ubuntu@{server_ip}", 
            ssh_cmd
        ]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=12, creationflags=CREATION_FLAGS)
            if res.returncode == 0:
                return {"status": "success", "message": f"Service successfully {req.action}ed."}
            else:
                return {"status": "error", "message": f"Action failed: {res.stderr or res.stdout}"}
        except subprocess.TimeoutExpired:
            return {"status": "error", "message": "Connection timed out"}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    @router.get("/api/server/uploader-status")
    def get_uploader_status():
        import subprocess
        key_path = r"C:\Dev\ssh-key-2026-05-07.key"
        server_ip = "152.70.23.171"
        if not os.path.exists(key_path):
            return {"status": "error", "message": f"SSH Key not found at {key_path}"}
        
        cmd = [
            "ssh", "-i", key_path, 
            "-o", "StrictHostKeyChecking=accept-new", 
            "-o", "ConnectTimeout=5", 
            "-o", "IdentitiesOnly=yes", 
            f"ubuntu@{server_ip}", 
            "systemctl is-active oci-uploader && echo --- LOGS --- && tail -n 100 /home/ubuntu/meloscribe/uploader.log 2>/dev/null"
        ]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=12, creationflags=CREATION_FLAGS)
            if res.returncode == 0 or "inactive" in res.stdout or "active" in res.stdout:
                stdout_str = res.stdout.strip()
                status_line = "inactive"
                logs_content = "No logs available."
                if "--- LOGS ---" in stdout_str:
                    parts = stdout_str.split("--- LOGS ---", 1)
                    status_line = parts[0].strip()
                    logs_content = parts[1].strip()
                else:
                    status_line = stdout_str
                
                is_active = status_line == "active"
                return {
                    "status": "success" if is_active else "warning",
                    "output": f"Service is-active: {status_line}\n\n--- RECENT LOGS ---\n{logs_content}"
                }
            else:
                status_line = res.stdout.strip() if res.stdout else "unknown"
                return {
                    "status": "warning" if status_line == "inactive" else "error",
                    "output": f"Service status: {status_line}\nErrors: {res.stderr}"
                }
        except subprocess.TimeoutExpired:
            return {"status": "error", "message": "Connection timed out"}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    @router.post("/api/server/uploader-action")
    def run_uploader_action(req: ServerActionRequest):
        import subprocess
        key_path = r"C:\Dev\ssh-key-2026-05-07.key"
        server_ip = "152.70.23.171"
        if not os.path.exists(key_path):
            return {"status": "error", "message": f"SSH Key not found at {key_path}"}
        
        if req.action not in ("start", "stop", "restart"):
            return {"status": "error", "message": "Invalid action"}
            
        ssh_cmd = f"sudo systemctl {req.action} oci-uploader"
        
        cmd = [
            "ssh", "-i", key_path, 
            "-o", "StrictHostKeyChecking=accept-new", 
            "-o", "ConnectTimeout=5", 
            "-o", "IdentitiesOnly=yes", 
            f"ubuntu@{server_ip}", 
            ssh_cmd
        ]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=12, creationflags=CREATION_FLAGS)
            if res.returncode == 0:
                return {"status": "success", "message": f"Service successfully {req.action}ed."}
            else:
                return {"status": "error", "message": f"Action failed: {res.stderr or res.stdout}"}
        except subprocess.TimeoutExpired:
            return {"status": "error", "message": "Connection timed out"}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    @router.get("/api/server/queue")
    def get_server_queue():
        import subprocess
        key_path = r"C:\Dev\ssh-key-2026-05-07.key"
        server_ip = "152.70.23.171"
        if not os.path.exists(key_path):
            return {"status": "error", "message": f"SSH Key not found at {key_path}"}
            
        py_query = "import sqlite3, json; conn=sqlite3.connect('/home/ubuntu/meloscribe/queue.db'); conn.row_factory=sqlite3.Row; cursor=conn.cursor(); cursor.execute('SELECT * FROM upload_queue ORDER BY datetime(schedule_time) DESC LIMIT 100'); print(json.dumps([dict(r) for r in cursor.fetchall()]))"
        cmd_str = f"python3 -c \"{py_query}\" && echo \"---FILES---\" && find /home/ubuntu/meloscribe/staging -type f 2>/dev/null"
        
        cmd = [
            "ssh", "-i", key_path, 
            "-o", "StrictHostKeyChecking=accept-new", 
            "-o", "ConnectTimeout=5", 
            "-o", "IdentitiesOnly=yes", 
            f"ubuntu@{server_ip}", 
            cmd_str
        ]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=12, creationflags=CREATION_FLAGS)
            if res.returncode == 0:
                stdout_str = res.stdout.strip()
                parts = stdout_str.split("---FILES---")
                queue_data = []
                file_paths = []
                
                if len(parts) > 0:
                    try:
                        queue_data = json.loads(parts[0].strip())
                    except Exception as parse_err:
                        print(f"Error parsing queue json: {parse_err}")
                
                if len(parts) > 1:
                    file_paths = [line.strip() for line in parts[1].strip().split("\n") if line.strip()]
                
                staged_files = {}
                for path in file_paths:
                    if "/staging/" in path:
                        rel = path.split("/staging/", 1)[1]
                        path_parts = rel.split("/")
                        if len(path_parts) >= 3:
                            song_name = path_parts[0]
                            category = path_parts[1].lower()
                            filename = path_parts[2]
                            
                            if song_name not in staged_files:
                                staged_files[song_name] = {"tiktoks": [], "packages": [], "covers": []}
                            
                            if category in staged_files[song_name]:
                                staged_files[song_name][category].append(filename)
                
                for item in queue_data:
                    song = item.get("song")
                    mode = item.get("mode")
                    profile = item.get("profile")
                    item_files = []
                    
                    if song in staged_files:
                        song_data = staged_files[song]
                        if mode == "kofi":
                            item_files = song_data.get("packages", [])
                        else:
                            videos = song_data.get("tiktoks", [])
                            if profile == "tutorial":
                                item_files = [f for f in videos if "slow" in f.lower()]
                            else:
                                item_files = [f for f in videos if "slow" not in f.lower()]
                    
                    item["files"] = item_files
                    
                return JSONResponse(content=queue_data)
            else:
                return JSONResponse(content=[])
        except Exception as e:
            return JSONResponse(content={"error": str(e)}, status_code=500)

    class RescheduleRequest(BaseModel):
        schedule_time: str

    @router.post("/api/server/queue/{task_id}/reschedule")
    def reschedule_server_task(task_id: int, req: RescheduleRequest):
        import subprocess
        key_path = r"C:\Dev\ssh-key-2026-05-07.key"
        server_ip = "152.70.23.171"
        if not os.path.exists(key_path):
            return {"status": "error", "message": f"SSH Key not found at {key_path}"}
            
        try:
            datetime.datetime.strptime(req.schedule_time, "%Y-%m-%d %H:%M")
        except ValueError:
            return {"status": "error", "message": "Invalid schedule_time format. Must be 'YYYY-MM-DD HH:MM'."}
            
        ssh_cmd = f"sqlite3 /home/ubuntu/meloscribe/queue.db \"UPDATE upload_queue SET schedule_time = '{req.schedule_time}' WHERE id = {task_id};\""
        cmd = [
            "ssh", "-i", key_path, 
            "-o", "StrictHostKeyChecking=accept-new", 
            "-o", "ConnectTimeout=5", 
            "-o", "IdentitiesOnly=yes", 
            f"ubuntu@{server_ip}", 
            ssh_cmd
        ]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=12, creationflags=CREATION_FLAGS)
            if res.returncode == 0:
                return {"status": "success", "message": f"Task {task_id} successfully rescheduled to {req.schedule_time}."}
            else:
                return {"status": "error", "message": f"Update failed: {res.stderr or res.stdout}"}
        except subprocess.TimeoutExpired:
            return {"status": "error", "message": "Connection timed out"}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    @router.delete("/api/server/queue/{task_id}")
    def delete_server_task(task_id: int):
        import subprocess
        key_path = r"C:\Dev\ssh-key-2026-05-07.key"
        server_ip = "152.70.23.171"
        if not os.path.exists(key_path):
            return {"status": "error", "message": f"SSH Key not found at {key_path}"}
            
        ssh_cmd = f"sqlite3 /home/ubuntu/meloscribe/queue.db \"DELETE FROM upload_queue WHERE id = {task_id};\""
        cmd = [
            "ssh", "-i", key_path, 
            "-o", "StrictHostKeyChecking=accept-new", 
            "-o", "ConnectTimeout=5", 
            "-o", "IdentitiesOnly=yes", 
            f"ubuntu@{server_ip}", 
            ssh_cmd
        ]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=12, creationflags=CREATION_FLAGS)
            if res.returncode == 0:
                return {"status": "success", "message": f"Task {task_id} successfully deleted."}
            else:
                return {"status": "error", "message": f"Deletion failed: {res.stderr or res.stdout}"}
        except subprocess.TimeoutExpired:
            return {"status": "error", "message": "Connection timed out"}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    @router.get("/api/server/disk")
    def get_server_disk():
        import subprocess
        key_path = r"C:\Dev\ssh-key-2026-05-07.key"
        server_ip = "152.70.23.171"
        if not os.path.exists(key_path):
            return {"status": "error", "message": f"SSH Key not found at {key_path}"}
            
        cmd = [
            "ssh", "-i", key_path, 
            "-o", "StrictHostKeyChecking=accept-new", 
            "-o", "ConnectTimeout=5", 
            "-o", "IdentitiesOnly=yes", 
            f"ubuntu@{server_ip}", 
            "df -h /home/ubuntu"
        ]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=12, creationflags=CREATION_FLAGS)
            return {"status": "success", "output": res.stdout}
        except Exception as e:
            return {"status": "error", "message": str(e)}

else:
    # -------------------------------------------------------------------
    # Production Server Admin Route Handlers
    # -------------------------------------------------------------------
    @router.get("/api/notify/subscribers")
    def notify_list_subscribers(request: Request):
        verify_admin(request)
        try:
            conn = sqlite3.connect(str(db_path), timeout=30.0)
            c = conn.cursor()
            c.execute("""
                CREATE TABLE IF NOT EXISTS notify_subscribers (
                    email TEXT PRIMARY KEY,
                    status TEXT,
                    created_at TEXT,
                    confirmed_at TEXT
                )
            """)
            conn.commit()
            c.execute("SELECT email, status, created_at, confirmed_at FROM notify_subscribers ORDER BY created_at DESC")
            rows = [{"email": r[0], "status": r[1], "created_at": r[2], "confirmed_at": r[3]} for r in c.fetchall()]
            conn.close()
            return {"subscribers": rows, "total": len(rows), "active": sum(1 for r in rows if r["status"] == "active")}
        except Exception as e:
            return JSONResponse(content={"error": str(e)}, status_code=500)

    @router.get("/api/stripe/sales")
    @router.get("/api/paddle/sales")
    def get_paddle_sales(request: Request):
        verify_admin(request)
        try:
            conn = sqlite3.connect(str(db_path), timeout=30.0)
            c = conn.cursor()
            c.execute("SELECT id, song_name, amount, currency, email, created_at, status FROM purchases ORDER BY created_at DESC LIMIT 50")
            rows = [{"id": r[0], "song_name": r[1], "amount": r[2], "currency": r[3], "email": r[4], "created_at": r[5], "status": r[6]} for r in c.fetchall()]
            conn.close()
            return rows
        except Exception as e:
            return JSONResponse(content={"error": str(e)}, status_code=500)

    @router.delete("/api/public/suggestions/{sug_id}")
    def delete_suggestion(sug_id: str, request: Request):
        verify_admin(request)
        try:
            conn = sqlite3.connect(str(db_path), timeout=30.0)
            c = conn.cursor()
            c.execute("DELETE FROM suggestions WHERE id = ?", (sug_id,))
            conn.commit()
            conn.close()
            return {"status": "success"}
        except Exception as e:
            return JSONResponse(content={"error": str(e)}, status_code=500)

    @router.get("/api/analytics")
    def get_analytics(request: Request, range: str = "30d"):
        verify_admin(request)
        if not db_path.exists():
            return {"error": "Analytics database not found."}
        
        try:
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            cursor.execute("SELECT SUM(views) as v, SUM(likes) as l, SUM(comments) as c, SUM(shares) as sh, SUM(saves) as sa, COUNT(id) as cnt FROM videos")
            totals = cursor.fetchone()
            
            cursor.execute("SELECT platform, SUM(views) as views, SUM(likes) as likes, SUM(comments) as comments, SUM(shares) as shares, SUM(saves) as saves FROM videos GROUP BY platform")
            platforms = [dict(r) for r in cursor.fetchall()]
            
            has_threads = any(p["platform"].lower() == "threads" for p in platforms)
            if not has_threads:
                cursor.execute("SELECT views FROM snapshots WHERE platform = 'threads' ORDER BY snapshot_date DESC LIMIT 1")
                threads_views_row = cursor.fetchone()
                threads_views = threads_views_row[0] if (threads_views_row and threads_views_row[0] is not None) else 0
                platforms.append({
                    "platform": "threads",
                    "views": threads_views,
                    "likes": 0,
                    "comments": 0,
                    "shares": 0,
                    "saves": 0
                })
            
            has_pinterest = any(p["platform"].lower() == "pinterest" for p in platforms)
            if not has_pinterest:
                cursor.execute("SELECT views FROM snapshots WHERE platform = 'pinterest' ORDER BY snapshot_date DESC LIMIT 1")
                pinterest_views_row = cursor.fetchone()
                pinterest_views = pinterest_views_row[0] if (pinterest_views_row and pinterest_views_row[0] is not None) else 0
                platforms.append({
                    "platform": "pinterest",
                    "views": pinterest_views,
                    "likes": 0,
                    "comments": 0,
                    "shares": 0,
                    "saves": 0
                })
            
            cursor.execute('''
                SELECT v.song_name as song, v.author, v.language, v.format,
                       SUM(v.views) as totalViews, SUM(v.likes) as totalLikes, SUM(v.saves) as totalSaves,
                       MAX(v.publish_date) as latest_publish,
                       t.bpm, t.theme, st.difficulty
                FROM videos v
                LEFT JOIN tracks t ON v.song_name = t.song_name
                LEFT JOIN song_tags st ON v.song_name = st.song_name
                GROUP BY v.song_name, v.format
            ''')
            songs_raw = cursor.fetchall()
            
            matrix_dict = {}
            for r in songs_raw:
                s_name = r["song"]
                if s_name not in matrix_dict:
                    matrix_dict[s_name] = {
                        "song": s_name, "author": r["author"], "language": r["language"],
                        "bpm": r["bpm"], "theme": r["theme"], "difficulty": r["difficulty"],
                        "totalViews": 0, "totalLikes": 0, "totalSaves": 0,
                        "latest_publish": r["latest_publish"]
                    }
                matrix_dict[s_name]["totalViews"] += r["totalViews"] or 0
                matrix_dict[s_name]["totalLikes"] += r["totalLikes"] or 0
                matrix_dict[s_name]["totalSaves"] += r["totalSaves"] or 0
                if r["latest_publish"] and (not matrix_dict[s_name]["latest_publish"] or r["latest_publish"] > matrix_dict[s_name]["latest_publish"]):
                    matrix_dict[s_name]["latest_publish"] = r["latest_publish"]
                matrix_dict[s_name][f"{r['format']} Views"] = r["totalViews"] or 0

            cursor.execute("SELECT song_name, platform, SUM(views) as views FROM videos GROUP BY song_name, platform")
            for row in cursor.fetchall():
                if row["song_name"] in matrix_dict:
                    matrix_dict[row["song_name"]][f"{row['platform'].capitalize()} Views"] = row["views"]
                    
            songs = list(matrix_dict.values())
            songs.sort(key=lambda x: x["latest_publish"] or "", reverse=True)
            
            formats_data = {r["format"].strip(): dict(r) for r in cursor.execute("SELECT format, AVG(views) as avgViews, COUNT(id) as count FROM videos GROUP BY format").fetchall() if r["format"]}
            all_possible_formats = ["Standard", "Tutorial", "Easy", "Easy Tutorial", "Hook/Teaser"]
            byFormat = []
            for fmt in all_possible_formats:
                found = False
                for db_fmt, db_data in formats_data.items():
                    if db_fmt.lower() == fmt.lower() or (fmt == "Hook/Teaser" and db_fmt.lower() in ["hook", "teaser"]):
                        byFormat.append({
                            "format": fmt,
                            "avgViews": db_data["avgViews"] or 0,
                            "count": db_data["count"] or 0
                        })
                        found = True
                        break
                if not found:
                    byFormat.append({
                        "format": fmt,
                        "avgViews": 0.0,
                        "count": 0
                    })

            correlations = {
                "byLanguage": [dict(r) for r in cursor.execute("SELECT language, AVG(views) as avgViews, COUNT(id) as count FROM videos GROUP BY language").fetchall()],
                "byAuthor": [dict(r) for r in cursor.execute("SELECT author, AVG(views) as avgViews, SUM(views) as totalViews FROM videos GROUP BY author").fetchall()],
                "byBpm": [dict(r) for r in cursor.execute("SELECT t.bpm, AVG(v.views) as avgViews FROM videos v JOIN tracks t ON v.song_name = t.song_name GROUP BY t.bpm").fetchall()],
                "byFormat": byFormat,
                "byVideoType": [dict(r) for r in cursor.execute("SELECT CASE WHEN duration_sec < 61 AND duration_sec > 0 THEN 'Short (<60s)' ELSE 'Long-form' END as videoType, AVG(views) as avgViews, COUNT(id) as count FROM videos WHERE duration_sec > 0 GROUP BY videoType").fetchall()]
            }
            
            cursor.execute("SELECT snapshot_date as date, platform, SUM(views) as views FROM snapshots GROUP BY snapshot_date, platform ORDER BY snapshot_date ASC")
            snapshot_rows = cursor.fetchall()
            
            platforms_to_track = ["youtube", "instagram", "tiktok", "facebook", "threads", "pinterest"]
            growth_dict = {}
            last_known = {p: 0 for p in platforms_to_track}
            
            dates_sorted = sorted(list(set(r["date"] for r in snapshot_rows)))
            
            rows_by_date = {}
            for r in snapshot_rows:
                dt = r["date"]
                if dt not in rows_by_date:
                    rows_by_date[dt] = {}
                rows_by_date[dt][r["platform"].lower()] = r["views"]
                
            for dt in dates_sorted:
                growth_dict[dt] = {"date": dt}
                for p in platforms_to_track:
                    if p in rows_by_date[dt]:
                        last_known[p] = rows_by_date[dt][p]
                    growth_dict[dt][p] = last_known[p]
                    
            growthData = list(growth_dict.values())
            
            trending = []
            if len(dates_sorted) >= 2:
                latest = dates_sorted[-1]
                cursor.execute('''
                    SELECT song_name, SUM(views) as views_now 
                    FROM snapshots WHERE snapshot_date = ? GROUP BY song_name
                ''', (latest,))
                now_views = {r["song_name"]: r["views_now"] for r in cursor.fetchall()}
                
                latest_dt = datetime.datetime.strptime(latest, "%Y-%m-%d").date()
                target_dt = latest_dt - datetime.timedelta(days=7)
                
                closest_date = None
                min_diff = None
                for d_str in dates_sorted:
                    if d_str == dates_sorted[0]:
                        continue
                    d = datetime.datetime.strptime(d_str, "%Y-%m-%d").date()
                    diff = abs((d - target_dt).days)
                    if min_diff is None or diff < min_diff:
                        min_diff = diff
                        closest_date = d_str
                        
                if closest_date:
                    target_date = closest_date
                    cursor.execute('''
                        SELECT song_name, SUM(views) as views_past 
                        FROM snapshots WHERE snapshot_date = ? GROUP BY song_name
                    ''', (target_date,))
                    past_views = {r["song_name"]: r["views_past"] for r in cursor.fetchall()}
                    
                    days_diff = (latest_dt - datetime.datetime.strptime(target_date, "%Y-%m-%d").date()).days
                    
                    for s in now_views:
                        if s in past_views:
                            diff = now_views[s] - past_views[s]
                        else:
                            cursor.execute('''
                                SELECT views FROM snapshots 
                                WHERE song_name = ? ORDER BY snapshot_date ASC LIMIT 1
                            ''', (s,))
                            row_first = cursor.fetchone()
                            if row_first:
                                diff = now_views[s] - row_first["views"]
                            else:
                                diff = 0
                                
                        if diff > 0:
                            trending.append({"song": s, "growth": diff, "days": days_diff})
                    trending.sort(key=lambda x: x["growth"], reverse=True)

            cursor.execute("SELECT SUM(amount) as total FROM revenue")
            rev_total = cursor.fetchone()["total"] or 0
            
            cursor.execute("SELECT strftime('%Y-%m', date) as month, SUM(amount) as amount FROM revenue GROUP BY month ORDER BY month ASC")
            rev_by_month = [dict(r) for r in cursor.fetchall()]
            
            cursor.execute("SELECT song_name, SUM(amount) as revenue FROM revenue WHERE song_name IS NOT NULL AND song_name != '' GROUP BY song_name ORDER BY revenue DESC LIMIT 10")
            top_selling = [dict(r) for r in cursor.fetchall()]

            cursor.execute("SELECT platform, followers, profile_views, website_clicks FROM channel_insights WHERE date = (SELECT MAX(date) FROM channel_insights)")
            channel = [dict(r) for r in cursor.fetchall()]
            
            bestPostingTime = []
            try:
                cursor.execute('''
                    SELECT 
                        CAST(strftime('%w', publish_date) AS INTEGER) as dow,
                        CAST(strftime('%H', publish_date) AS INTEGER) as hour,
                        AVG(views) as avgViews,
                        COUNT(*) as count
                    FROM videos 
                    WHERE publish_date IS NOT NULL AND publish_date != ''
                    GROUP BY dow, hour
                ''')
                bestPostingTime = [dict(r) for r in cursor.fetchall()]
            except Exception:
                pass

            for s in songs:
                v = s.get("totalViews", 0) or 0
                l = s.get("totalLikes", 0) or 0
                sv = s.get("totalSaves", 0) or 0
                if v > 0:
                    s["engagementRate"] = round((l + sv) / v * 100, 2)
                else:
                    s["engagementRate"] = 0

            competitors = []
            try:
                cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='competitors'")
                if cursor.fetchone():
                    cursor.execute('''
                        SELECT c.channel_id, c.channel_name, 
                               cv.title, cv.views, cv.likes, cv.published_at, cv.video_id,
                               cv.snapshot_date
                        FROM competitors c
                        LEFT JOIN competitor_videos cv ON c.channel_id = cv.channel_id
                        ORDER BY c.channel_name, cv.published_at DESC
                    ''')
                    comp_dict = {}
                    for r in cursor.fetchall():
                        cid = r["channel_id"]
                        if cid not in comp_dict:
                            comp_dict[cid] = {"channelId": cid, "channelName": r["channel_name"], "videos": []}
                        if r["title"]:
                            comp_dict[cid]["videos"].append({
                                "title": r["title"], "views": r["views"], "likes": r["likes"],
                                "publishedAt": r["published_at"], "videoId": r["video_id"]
                            })
                    competitors = list(comp_dict.values())
            except Exception:
                pass

            demographics = {}
            try:
                cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='audience_demographics'")
                if cursor.fetchone():
                    cursor.execute('''
                        SELECT platform, metric_type, metric_key, metric_value 
                        FROM audience_demographics 
                        WHERE snapshot_date = (SELECT MAX(snapshot_date) FROM audience_demographics)
                        ORDER BY metric_value DESC
                    ''')
                    for r in cursor.fetchall():
                        plat = r["platform"]
                        mtype = r["metric_type"]
                        if plat not in demographics:
                            demographics[plat] = {}
                        if mtype not in demographics[plat]:
                            demographics[plat][mtype] = []
                        demographics[plat][mtype].append({"key": r["metric_key"], "value": r["metric_value"]})
            except Exception:
                pass

            conn.close()
            
            return {
                "kpi": {
                    "totalViews": totals["v"] or 0,
                    "totalLikes": totals["l"] or 0,
                    "totalComments": totals["c"] or 0,
                    "totalShares": totals["sh"] or 0,
                    "totalSaves": totals["sa"] or 0,
                    "totalVideos": totals["cnt"] or 0
                },
                "platformBreakdown": platforms,
                "songPerformance": songs,
                "growthData": growthData,
                "correlations": correlations,
                "trending": trending[:5],
                "revenue": {
                    "total": rev_total,
                    "byMonth": rev_by_month,
                    "topSelling": top_selling
                },
                "channelInsights": channel,
                "bestPostingTime": bestPostingTime,
                "competitors": competitors,
                "demographics": demographics
            }
        except Exception as e:
            return {"error": f"Database read error: {e}"}

    @router.post("/api/demographics/sync")
    def sync_demographics(request: Request):
        verify_admin(request)
        import platform as pf
        
        python_exe = str(TOOLS_DIR / "meloscribe" / "backend" / ".venv" / "Scripts" / "python.exe")
        if pf.system() != "Windows":
            python_exe = str(TOOLS_DIR / "meloscribe" / "backend" / ".venv" / "bin" / "python")
            
        script_path = str(TOOLS_DIR / "scrape_demographics.py")
        
        try:
            print(f"Running demographics sync script: {script_path}")
            res = subprocess.run([python_exe, script_path], capture_output=True, text=True, timeout=180)
            
            try:
                if db_path.exists():
                    conn = sqlite3.connect(db_path)
                    conn.row_factory = sqlite3.Row
                    cursor = conn.cursor()
                    cursor.execute("SELECT platform, metric_type, metric_key, metric_value, snapshot_date FROM audience_demographics WHERE snapshot_date = (SELECT MAX(snapshot_date) FROM audience_demographics)")
                    rows = [dict(r) for r in cursor.fetchall()]
                    conn.close()
                    
                    if rows:
                        requests.post("https://api.meloscribe.dev/api/demographics/sync-raw", json={"demographics": rows}, timeout=10.0)
                        print("[Demographics Sync] Successfully pushed demographic data to Oracle VM.")
            except Exception as push_err:
                print(f"[Demographics Sync] Warning: Failed to push demographics to Oracle: {push_err}")
                
            if res.returncode == 0:
                return {"status": "ok", "output": res.stdout}
            else:
                return {"status": "error", "message": res.stderr or res.stdout}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    @router.post("/api/demographics/sync-raw")
    def sync_raw_demographics(payload: dict, request: Request):
        verify_admin(request)
        rows = payload.get("demographics", [])
        if not rows:
            return {"status": "error", "message": "No demographic data in payload."}
            
        try:
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS audience_demographics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    platform TEXT,
                    metric_type TEXT,
                    metric_key TEXT,
                    metric_value REAL,
                    snapshot_date TEXT
                )
            ''')
            
            for r in rows:
                cursor.execute('''
                    DELETE FROM audience_demographics 
                    WHERE platform = ? AND metric_type = ? AND metric_key = ? AND snapshot_date = ?
                ''', (r["platform"], r["metric_type"], r["metric_key"], r["snapshot_date"]))
                
                cursor.execute('''
                    INSERT INTO audience_demographics (platform, metric_type, metric_key, metric_value, snapshot_date)
                    VALUES (?, ?, ?, ?, ?)
                ''', (r["platform"], r["metric_type"], r["metric_key"], r["metric_value"], r["snapshot_date"]))
                
            conn.commit()
            conn.close()
            return {"status": "ok", "message": f"Successfully imported {len(rows)} demographic records."}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    @router.get("/api/admin/packages")
    def admin_list_packages(request: Request):
        verify_admin(request)
        
        r2_account_id = settings.get("r2_account_id") or os.environ.get("R2_ACCOUNT_ID")
        r2_access_key = settings.get("r2_access_key") or settings.get("r2_access_key_id") or os.environ.get("R2_ACCESS_KEY_ID")
        r2_secret_key = settings.get("r2_secret_key") or settings.get("r2_secret_access_key") or os.environ.get("R2_SECRET_ACCESS_KEY")
        r2_bucket = settings.get("r2_bucket") or settings.get("r2_bucket_name", "meloscribe-sheets") or os.environ.get("R2_BUCKET_NAME", "meloscribe-sheets")
        
        if not r2_account_id or not r2_access_key or not r2_secret_key:
            raise HTTPException(status_code=500, detail="Cloudflare R2 credentials are not configured in settings.json")
            
        import boto3
        from botocore.config import Config
        s3 = boto3.client(
            's3',
            endpoint_url=f'https://{r2_account_id}.r2.cloudflarestorage.com',
            aws_access_key_id=r2_access_key,
            aws_secret_access_key=r2_secret_key,
            config=Config(signature_version='s3v4')
        )
        
        try:
            res = s3.list_objects_v2(Bucket=r2_bucket)
            files = []
            if 'Contents' in res:
                for obj in res['Contents']:
                    files.append({
                        "key": obj['Key'],
                        "size": obj['Size'],
                        "last_modified": obj['LastModified'].isoformat()
                    })
            return {"files": files}
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to list Cloudflare R2 files: {str(e)}")

    @router.post("/api/admin/upload")
    async def admin_upload_file(
        request: Request,
        song_name: str = Form(...),
        type: str = Form(...),
        file: UploadFile = File(...)
    ):
        verify_admin(request)
        
        filename = f"{song_name}.pdf" if type == "pdf" else \
                   f"{song_name}.mid" if type == "midi" else \
                   f"{song_name} slow.mid" if type == "midi_slow" else \
                   f"{song_name}.mp4" if type == "video" else \
                   f"{song_name} slow.mp4" if type == "video_slow" else \
                   f"{song_name}.mp3" if type == "audio_preview" else \
                   f"{song_name}_preview.mp4" if type == "video_preview" else file.filename
                   
        r2_key = f"{song_name}/{filename}"
            
        r2_account_id = settings.get("r2_account_id") or os.environ.get("R2_ACCOUNT_ID")
        r2_access_key = settings.get("r2_access_key") or settings.get("r2_access_key_id") or os.environ.get("R2_ACCESS_KEY_ID")
        r2_secret_key = settings.get("r2_secret_key") or settings.get("r2_secret_access_key") or os.environ.get("R2_SECRET_ACCESS_KEY")
        r2_bucket = settings.get("r2_bucket") or settings.get("r2_bucket_name", "meloscribe-sheets") or os.environ.get("R2_BUCKET_NAME", "meloscribe-sheets")
        
        if not r2_account_id or not r2_access_key or not r2_secret_key:
            raise HTTPException(status_code=500, detail="Cloudflare R2 credentials are not configured in settings.json")
            
        import boto3
        from botocore.config import Config
        s3 = boto3.client(
            's3',
            endpoint_url=f'https://{r2_account_id}.r2.cloudflarestorage.com',
            aws_access_key_id=r2_access_key,
            aws_secret_access_key=r2_secret_key,
            config=Config(signature_version='s3v4')
        )
        
        try:
            content = await file.read()
            content_type = "application/pdf" if type == "pdf" else \
                           "audio/midi" if "midi" in type else \
                           "video/mp4" if "video" in type or "preview" in type else \
                           "audio/mpeg" if type == "audio_preview" else "application/octet-stream"
                           
            s3.put_object(
                Bucket=r2_bucket,
                Key=r2_key,
                Body=content,
                ContentType=content_type
            )
            print(f"[Admin Upload] Successfully uploaded {r2_key} to R2")
            return {"success": True, "key": r2_key}
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to upload file to Cloudflare R2: {str(e)}")

    @router.post("/api/admin/delete")
    def admin_delete_file(request: Request, payload: dict):
        verify_admin(request)
        r2_key = payload.get("key")
        if not r2_key:
            raise HTTPException(status_code=400, detail="R2 Key is required")
            
        r2_account_id = settings.get("r2_account_id") or os.environ.get("R2_ACCOUNT_ID")
        r2_access_key = settings.get("r2_access_key") or settings.get("r2_access_key_id") or os.environ.get("R2_ACCESS_KEY_ID")
        r2_secret_key = settings.get("r2_secret_key") or settings.get("r2_secret_access_key") or os.environ.get("R2_SECRET_ACCESS_KEY")
        r2_bucket = settings.get("r2_bucket") or settings.get("r2_bucket_name", "meloscribe-sheets") or os.environ.get("R2_BUCKET_NAME", "meloscribe-sheets")
        
        if not r2_account_id or not r2_access_key or not r2_secret_key:
            raise HTTPException(status_code=500, detail="Cloudflare R2 credentials are not configured in settings.json")
            
        import boto3
        from botocore.config import Config
        s3 = boto3.client(
            's3',
            endpoint_url=f'https://{r2_account_id}.r2.cloudflarestorage.com',
            aws_access_key_id=r2_access_key,
            aws_secret_access_key=r2_secret_key,
            config=Config(signature_version='s3v4')
        )
        
        try:
            s3.delete_object(Bucket=r2_bucket, Key=r2_key)
            print(f"[Admin Delete] Deleted {r2_key} from R2")
            return {"success": True}
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to delete file from Cloudflare R2: {str(e)}")

    @router.get("/api/admin/orders")
    def admin_list_orders(request: Request):
        verify_admin(request)
        
        conn = sqlite3.connect(str(db_path))
        c = conn.cursor()
        c.execute("SELECT transaction_id, email, song_name, amount, currency, status, download_hash, locale, buyer_name, download_count, created_at FROM purchases ORDER BY created_at DESC")
        rows = c.fetchall()
        conn.close()
        
        orders = []
        for row in rows:
            orders.append({
                "transaction_id": row[0],
                "email": row[1],
                "song_name": row[2],
                "amount": row[3],
                "currency": row[4],
                "status": row[5],
                "download_hash": row[6],
                "locale": row[7],
                "buyer_name": row[8],
                "download_count": row[9],
                "created_at": row[10]
            })
        return {"orders": orders}

    @router.post("/api/admin/orders/reset")
    async def admin_reset_order_downloads(request: Request):
        verify_admin(request)
        payload = await request.json()
        transaction_id = payload.get("transaction_id")
        if not transaction_id:
            raise HTTPException(status_code=400, detail="Transaction ID required")
            
        conn = sqlite3.connect(str(db_path))
        c = conn.cursor()
        c.execute("UPDATE purchases SET download_count = 0, downloaded_types = '' WHERE transaction_id = ?", (transaction_id,))
        conn.commit()
        conn.close()
        return {"success": True}

    @router.post("/api/admin/orders/toggle-status")
    async def admin_toggle_order_status(request: Request):
        verify_admin(request)
        payload = await request.json()
        transaction_id = payload.get("transaction_id")
        new_status = payload.get("status")
        if not transaction_id or not new_status:
            raise HTTPException(status_code=400, detail="Transaction ID and status required")
            
        conn = sqlite3.connect(str(db_path))
        c = conn.cursor()
        c.execute("UPDATE purchases SET status = ? WHERE transaction_id = ?", (new_status, transaction_id))
        conn.commit()
        conn.close()
        return {"success": True, "status": new_status}

    @router.delete("/api/admin/orders/{transaction_id}")
    def admin_delete_order(transaction_id: str, request: Request):
        verify_admin(request)
        conn = sqlite3.connect(str(db_path))
        c = conn.cursor()
        c.execute("DELETE FROM purchases WHERE transaction_id = ?", (transaction_id,))
        c.execute("DELETE FROM revenue WHERE message = ? OR message = ?", 
                  (f"Stripe txn {transaction_id}", f"Paddle txn {transaction_id}"))
        conn.commit()
        conn.close()
        return {"success": True}

# -------------------------------------------------------------------
# Competitor Tracker Endpoints (Global)
# -------------------------------------------------------------------
@router.post("/api/competitors")
async def add_competitor(req: Request):
    data = await req.json()
    channel_input = data.get("channel", "").strip()
    if not channel_input:
        return JSONResponse(content={"error": "No channel provided"}, status_code=400)
    
    try:
        from yt_auth import get_authenticated_service
        from googleapiclient.discovery import build
        creds = get_authenticated_service()
        youtube = build("youtube", "v3", credentials=creds)
        
        channel_id = channel_input
        channel_name = channel_input
        
        if "/" in channel_input:
            channel_input = channel_input.rstrip("/").split("/")[-1]
        
        if channel_input.startswith("@"):
            resp = youtube.search().list(part="snippet", q=channel_input, type="channel", maxResults=1).execute()
            if resp.get("items"):
                channel_id = resp["items"][0]["snippet"]["channelId"]
                channel_name = resp["items"][0]["snippet"]["channelTitle"]
        elif channel_input.startswith("UC"):
            channel_id = channel_input
            resp = youtube.channels().list(part="snippet", id=channel_id).execute()
            if resp.get("items"):
                channel_name = resp["items"][0]["snippet"]["title"]
        else:
            resp = youtube.search().list(part="snippet", q=channel_input, type="channel", maxResults=1).execute()
            if resp.get("items"):
                channel_id = resp["items"][0]["snippet"]["channelId"]
                channel_name = resp["items"][0]["snippet"]["channelTitle"]
        
        conn = sqlite3.connect(str(db_path))
        c = conn.cursor()
        c.execute("CREATE TABLE IF NOT EXISTS competitors (channel_id TEXT PRIMARY KEY, channel_name TEXT, added_date TEXT)")
        c.execute("INSERT OR IGNORE INTO competitors (channel_id, channel_name, added_date) VALUES (?, ?, ?)",
                  (channel_id, channel_name, datetime.datetime.now().isoformat()))
        conn.commit()
        conn.close()
        
        return JSONResponse(content={"success": True, "channelId": channel_id, "channelName": channel_name})
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)

@router.delete("/api/competitors/{channel_id}")
async def delete_competitor(channel_id: str):
    conn = sqlite3.connect(str(db_path))
    c = conn.cursor()
    c.execute("DELETE FROM competitors WHERE channel_id=?", (channel_id,))
    c.execute("DELETE FROM competitor_videos WHERE channel_id=?", (channel_id,))
    conn.commit()
    conn.close()
    return JSONResponse(content={"success": True})

@router.post("/api/competitors/sync")
async def sync_competitors():
    try:
        from yt_auth import get_authenticated_service
        from googleapiclient.discovery import build
        creds = get_authenticated_service()
        youtube = build("youtube", "v3", credentials=creds)
        
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        
        c.execute("SELECT channel_id, channel_name FROM competitors")
        comps = c.fetchall()
        today = datetime.date.today().isoformat()
        total_synced = 0
        
        for comp in comps:
            cid = comp["channel_id"]
            try:
                search_resp = youtube.search().list(
                    part="snippet", channelId=cid, order="date",
                    type="video", maxResults=10
                ).execute()
                
                video_ids = [item["id"]["videoId"] for item in search_resp.get("items", [])]
                if not video_ids:
                    continue
                    
                stats_resp = youtube.videos().list(
                    part="statistics,snippet", id=",".join(video_ids)
                ).execute()
                
                for item in stats_resp.get("items", []):
                    vid_id = item["id"]
                    title = item["snippet"]["title"]
                    published = item["snippet"]["publishedAt"][:10]
                    views = int(item["statistics"].get("viewCount", 0))
                    likes = int(item["statistics"].get("likeCount", 0))
                    
                    c.execute("""INSERT OR REPLACE INTO competitor_videos 
                               (channel_id, video_id, title, views, likes, published_at, snapshot_date)
                               VALUES (?, ?, ?, ?, ?, ?, ?)""",
                              (cid, vid_id, title, views, likes, published, today))
                    total_synced += 1
            except Exception as e:
                print(f"[Competitor Sync] Failed for {comp['channel_name']}: {e}")
        
        conn.commit()
        conn.close()
        return JSONResponse(content={"success": True, "synced": total_synced})
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)

# -------------------------------------------------------------------
# Todos Tracker Endpoints (Server Only)
# -------------------------------------------------------------------
@router.get("/api/todos")
async def get_todos(request: Request):
    verify_admin(request)
    published_titles = set()
    songs_path = TOOLS_DIR / "meloscribe" / "backend" / "songs.json"
    if not os.path.exists(songs_path):
        songs_path = Path("/home/ubuntu/meloscribe/tools/meloscribe/backend/songs.json")
        
    if os.path.exists(songs_path):
        try:
            with open(songs_path, "r", encoding="utf-8") as f:
                songs_list = json.load(f)
                for s in songs_list:
                    if isinstance(s, dict) and "title" in s:
                        published_titles.add(s["title"])
        except Exception as e:
            print(f"[Todo Auto-Complete] Error reading songs.json: {e}")

    def clean_name(todo_name):
        name = todo_name
        for prefix in ["[PRIORITY] ", "[FORMAT-SHIFT] ", "[RE-PURPOSE] "]:
            if name.startswith(prefix):
                name = name[len(prefix):]
        return "".join(c for c in name.lower() if c.isalnum())

    published_cleaned = {clean_name(t) for t in published_titles}

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    
    todos_raw_db = [dict(r) for r in c.execute("SELECT * FROM todos WHERE status='pending'").fetchall()]
    
    completed_ids = []
    todos_raw = []
    
    for t in todos_raw_db:
        t_cleaned = clean_name(t["song_name"])
        is_completed = False
        for p_clean in published_cleaned:
            if p_clean and (p_clean == t_cleaned or p_clean in t_cleaned or t_cleaned in p_clean):
                is_completed = True
                break
        
        if is_completed:
            completed_ids.append(t["id"])
        else:
            todos_raw.append(t)
            
    if completed_ids:
        c.executemany("UPDATE todos SET status='completed' WHERE id=?", [(tid,) for tid in completed_ids])
        conn.commit()
        print(f"[Todo Auto-Complete] Auto-completed {len(completed_ids)} todos: {completed_ids}")
    
    for t in todos_raw:
        song = t["song_name"].replace("[PRIORITY] ", "").replace("[FORMAT-SHIFT] ", "").replace("[RE-PURPOSE] ", "")
        row = c.execute("SELECT AVG(views) as avg_v FROM videos WHERE song_name LIKE ?", (f"%{song.split(' - ')[0].strip()}%",)).fetchone()
        t["_score"] = row["avg_v"] if row and row["avg_v"] else 0
        
        if "[PRIORITY]" in t["song_name"]:
            t["_score"] = (t["_score"] or 0) + 999999
    
    todos_raw.sort(key=lambda x: x.get("_score", 0), reverse=True)
    
    for t in todos_raw:
        t.pop("_score", None)
    
    conn.close()
    return JSONResponse(content=todos_raw)

@router.post("/api/todos")
async def add_todo(req: Request):
    verify_admin(req)
    data = await req.json()
    song_name = data.get("song_name")
    if not song_name:
        return JSONResponse(content={"error": "No song_name provided"}, status_code=400)
    
    conn = sqlite3.connect(str(db_path))
    c = conn.cursor()
    import datetime as _dt
    c.execute("INSERT INTO todos (song_name, added_date) VALUES (?, ?)", (song_name, _dt.datetime.now().isoformat()))
    conn.commit()
    new_id = c.lastrowid
    conn.close()
    return JSONResponse(content={"success": True, "id": new_id, "song_name": song_name, "status": "pending"})

@router.delete("/api/todos/{todo_id}")
async def delete_todo(todo_id: int, req: Request):
    verify_admin(req)
    conn = sqlite3.connect(str(db_path))
    c = conn.cursor()
    c.execute("DELETE FROM todos WHERE id=?", (todo_id,))
    conn.commit()
    conn.close()
    return JSONResponse(content={"success": True})

# -------------------------------------------------------------------
# Dismissed Suggestions Endpoints (Server Only)
# -------------------------------------------------------------------
@router.get("/api/dismissed-suggestions")
async def get_dismissed(request: Request):
    verify_admin(request)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("CREATE TABLE IF NOT EXISTS dismissed_suggestions (id INTEGER PRIMARY KEY AUTOINCREMENT, song_name TEXT UNIQUE, dismissed_date TEXT)")
    dismissed = [r["song_name"] for r in c.execute("SELECT song_name FROM dismissed_suggestions").fetchall()]
    conn.close()
    return JSONResponse(content=dismissed)

@router.post("/api/dismissed-suggestions")
async def dismiss_suggestion(req: Request):
    verify_admin(req)
    data = await req.json()
    song_name = data.get("song_name", "").strip()
    if not song_name:
        return JSONResponse(content={"error": "No song_name"}, status_code=400)
    conn = sqlite3.connect(str(db_path))
    c = conn.cursor()
    import datetime as _dt
    c.execute("CREATE TABLE IF NOT EXISTS dismissed_suggestions (id INTEGER PRIMARY KEY AUTOINCREMENT, song_name TEXT UNIQUE, dismissed_date TEXT)")
    c.execute("INSERT OR IGNORE INTO dismissed_suggestions (song_name, dismissed_date) VALUES (?, ?)", (song_name, _dt.datetime.now().isoformat()))
    conn.commit()
    conn.close()
    return JSONResponse(content={"success": True})

# -------------------------------------------------------------------
# AI Briefing & Chat Endpoints (Server Only)
# -------------------------------------------------------------------
@router.get("/api/ai/briefing")
async def get_ai_briefing(request: Request):
    verify_admin(request)
    try:
        from ai_agent import get_latest_briefing
        briefing = get_latest_briefing()
        if not briefing:
            return JSONResponse(content={"error": "Failed to generate briefing"}, status_code=500)
        return JSONResponse(content=briefing)
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)

@router.post("/api/ai/briefing/force")
async def force_ai_briefing(request: Request):
    verify_admin(request)
    try:
        from ai_agent import generate_daily_briefing, get_latest_briefing
        try:
            briefing = generate_daily_briefing()
            if not briefing:
                raise Exception("generate_daily_briefing returned None.")
        except Exception as e:
            print(f"[API] Force briefing failed (Rate Limit?), falling back to cache. Error: {e}")
            briefing = get_latest_briefing()
            
        return JSONResponse(content=briefing)
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)

@router.post("/api/ai/chat")
async def chat_with_ai(req: Request):
    verify_admin(req)
    data = await req.json()
    message = data.get("message")
    history = data.get("history", [])
    if not message:
        return JSONResponse(content={"error": "No message provided"}, status_code=400)
        
    try:
        from ai_agent import chat_with_agent
        reply = chat_with_agent(message, history)
        return JSONResponse(content={"reply": reply})
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)

@router.post("/api/actions/run")
async def run_action_engine(request: Request):
    verify_admin(request)
    try:
        import sync_utils
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()
        count = sync_utils.evaluate_action_triggers(cursor)
        conn.commit()
        conn.close()
        return JSONResponse(content={"success": True, "actions_created": count})
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)
