# meloscribe-backend — Learning & Bug Ledger

Technical insights, bugs, and resolved issues specific to the meloscribe API backend and Oracle VM server operations.

---

### 2026-06-25: Oracle VM missing packages & Git credential blocks
- **Issue**: Oracle VM lacked `git`, `nginx`, `certbot`, `sqlite3`. HTTPS `git pull` blocks indefinitely waiting for user/password in background tasks.
- **Lesson**: Never assume server tools are pre-installed. For automated/first-time deploys, use SCP. Configure SSH deploy keys or HTTPS with a GitHub Personal Access Token for future pulls.
- **Resolution**: Installed all packages via `apt-get`. Backend initially deployed via SCP. Server now pulls from the public `https://github.com/meloscribe/meloscribe-backend.git` repo (no auth needed for public repos).

---

### 2026-06-25: PowerShell single-quote quoting hell in SSH commands
- **Issue**: Passing Python one-liners with single quotes via PowerShell's SSH command causes `ParserError: UnexpectedToken` in PowerShell's own parser before the command even reaches SSH.
- **Lesson**: PowerShell's quoting rules conflict with Python's single-quote strings when embedded in double-quoted SSH command strings.
- **Resolution**: Standardized pattern: write a `.py` script locally → `scp` to `/tmp/` → `ssh python3 /tmp/script.py` → delete. Never inline complex Python in PowerShell SSH one-liners.

---

### 2026-06-25: Systemd Journalctl Hangs & Log Redirection
- **Issue**: `systemctl status` and `journalctl` hung on the OCI Free-Tier server due to large journal logs and PolicyKit wait blocks. FastAPI queries timed out.
- **Lesson**: Use `systemctl is-active` for lightweight health checks. Redirect service output to a plain file via `StandardOutput=append:/path/to/log` and read with `tail`.
- **Resolution**: All service checks use `systemctl is-active`. Services write logs to `/home/ubuntu/meloscribe/uploader.log`.

---

### 2026-06-25: SQLite Multi-threaded Locks in FastAPI
- **Issue**: Multiple background sync threads (TikTok, Instagram, YouTube, Facebook sync running concurrently) cause `OperationalError: database is locked` when they attempt concurrent SQLite writes.
- **Resolution**: All `sqlite3.connect()` calls now use `timeout=30.0`. Database initialized with `PRAGMA journal_mode=WAL` which allows one writer + multiple readers simultaneously without blocking.

---

### 2026-06-25: SQLite ALTER TABLE can't add UNIQUE constraints
- **Issue**: `ALTER TABLE purchases ADD COLUMN download_hash TEXT UNIQUE` fails. SQLite's `ALTER TABLE` does not support adding constraint keywords.
- **Resolution**: Added columns as plain `TEXT`/`INTEGER` without constraints. Uniqueness enforced at the application level (generate UUID hash, check before insert).

---

### 2026-06-25: Certbot requires DNS to resolve before SSL generation
- **Issue**: Running `certbot --nginx -d api.meloscribe.dev` fails if DNS hasn't propagated yet, even if the A record is set in Cloudflare.
- **Lesson**: Always wait for full DNS propagation (verify with `nslookup api.meloscribe.dev` resolving to `152.70.23.171`) before running certbot. Cloudflare DNS-Only (grey cloud) is required — the orange proxy cloud blocks certbot's HTTP-01 challenge.
- **Resolution**: Verified DNS first, then ran certbot successfully. Set Cloudflare records to "DNS Only" (grey cloud).

---

### 2026-06-25: R2 credentials stored in settings.json, not .env
- **Issue**: Some setup guides reference a `.env` file. Our backend reads all credentials from `settings.json` via `settings.py`.
- **Lesson**: Do not create a `.env` file — it would be an additional credentials location that is easy to miss in `.gitignore`. Single source: `settings.json` on the server.
- **Resolution**: All keys (R2, Paddle, Ko-Fi) are in `/home/ubuntu/meloscribe/tools/meloscribe/backend/settings.json`. This file is git-ignored and not in the public repo.

---

### 2026-06-25: Paddle webhook signature verification
- **Issue**: Paddle sends a `Paddle-Signature` header with each webhook. Requests without a valid signature must be rejected to prevent fake purchase events.
- **Lesson**: Use `paddle_billing_client` or manual HMAC-SHA256 verification against the webhook secret from `settings.json`. The secret is different from the API key.
- **Status**: Verify that signature verification is enabled in `main.py` `/api/paddle/webhook` handler before going live.

---

### 2026-06-26: Server-seitige settings.json ändern → Service zwingend neustarten
- **Issue**: Nach dem Eintragen neuer Credentials (z.B. R2-Keys, Paddle-Secret) in `/home/ubuntu/meloscribe/tools/meloscribe/backend/settings.json` hat FastAPI die alten Werte weiterhin verwendet.
- **Lesson**: FastAPI (Uvicorn) lädt `settings.json` beim Start einmalig in den Speicher. Änderungen an der Datei während der Laufzeit haben **keine Wirkung** bis der Prozess neu gestartet wird.
- **Resolution**: Nach jedem Update an `settings.json` auf dem Server immer:
  ```bash
  sudo systemctl restart meloscribe-backend
  ```
  Danach mit `tail -20 /home/ubuntu/meloscribe/uploader.log` prüfen, ob der Start erfolgreich war (kein `KeyError` oder `FileNotFoundError`).
