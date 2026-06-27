# meloscribe-backend — Project Status & Roadmap

Living documentation for the meloscribe public API backend (`C:\Dev\meloscribe-backend`).

**GitHub repo:** https://github.com/meloscribe/meloscribe-backend (public)
**Deployed on:** Oracle Cloud VM — `ubuntu@152.70.23.171`
**API base:** `https://api.meloscribe.dev`

> Credentials (API keys, R2 secrets, SSH key, Paddle key) are stored exclusively in
> `C:\Dev\meloscribe_credentials_backup.json` — never in this repo.

---

## Architecture

```
Client (meloscribe.dev)
    │
    ▼ HTTPS (443)
Nginx (reverse proxy on Oracle VM)
    │
    ▼ localhost:8787
Uvicorn / FastAPI  (main.py)
    │
    ├── SQLite (analytics.db + purchases table)
    ├── Cloudflare R2 (presigned 15-min download URLs)
    └── Paddle webhook (purchase verification)
```

**Server paths:**
| Path | Purpose |
|---|---|
| `/home/ubuntu/meloscribe/` | Git working directory (pulls from this repo) |
| `/home/ubuntu/meloscribe/tools/meloscribe/backend/` | FastAPI app root |
| `/home/ubuntu/meloscribe/tools/meloscribe/backend/main.py` | Entry point |
| `/home/ubuntu/meloscribe/tools/meloscribe/backend/settings.json` | Runtime config (credentials, NOT in git) |
| `/home/ubuntu/meloscribe/tools/meloscribe/backend/analytics.db` | SQLite DB |
| `/home/ubuntu/meloscribe/uploader.log` | Combined stdout log for all services |
| `/home/ubuntu/meloscribe/venv/` | Python virtual environment |

---

## Systemd Services

| Service | Command | Log |
|---|---|---|
| `meloscribe-backend.service` | `uvicorn main:app --host 0.0.0.0 --port 8787` | `/home/ubuntu/meloscribe/uploader.log` |
| `oci-uploader.service` | Upload queue daemon | same log |
| `oci-sniper.service` | OCI instance sniper | `/home/ubuntu/oci-sniper/` |

**Restart all services:**
```bash
sudo systemctl restart meloscribe-backend oci-uploader
```
**Check status:**
```bash
systemctl is-active meloscribe-backend
tail -100 /home/ubuntu/meloscribe/uploader.log
```

---

## Deployment Flow

```bash
# On Oracle VM:
cd /home/ubuntu/meloscribe
git pull origin main
sudo systemctl restart meloscribe-backend oci-uploader
```

**Local deploy prep:**
```bash
# Push from local backend repo
cd C:\Dev\meloscribe-backend
git add . && git commit -m "..." && git push
# Then SSH to server and git pull
```

---

## Infrastructure & Networking

### DNS (Cloudflare — DNS Only, no proxy)
- `meloscribe.dev` → `A` → `76.76.21.21` (Vercel)
- `www.meloscribe.dev` → `CNAME` → `cname.vercel-dns.com`
- `api.meloscribe.dev` → `A` → `152.70.23.171` (Oracle VM)

### SSL (Let's Encrypt via Certbot)
- Full chain: `/etc/letsencrypt/live/api.meloscribe.dev/fullchain.pem`
- Private key: `/etc/letsencrypt/live/api.meloscribe.dev/privkey.pem`
- Auto-renewal: `certbot.timer` systemd service
- Manual renew: `sudo certbot renew && sudo systemctl restart nginx`

### Firewall (OCI Security List + iptables)
- Port 80 open (Let's Encrypt validation)
- Port 443 open (public HTTPS)
- Persisted via `netfilter-persistent save`

---

## Key API Endpoints

| Endpoint | Method | Purpose |
|---|---|---|
| `/api/paddle/webhook` | POST | Receive Paddle purchase events, create `purchases` row, generate `download_hash` |
| `/order/{hash}` | GET | Serve success page — validates hash, returns presigned R2 download URLs (15 min, max 20 downloads) |
| `/api/songs` | GET | Return `songs.json` catalog |
| `/api/songs/sync` | POST | Sync song from desktop app upload pipeline |
| `/api/kofi/webhook` | POST | Ko-Fi donation/purchase → `analytics.db` revenue |
| `/api/server/status` | GET | Service health check |
| `/api/notify/subscribe` | POST | Register email for new sheet music alerts. Sends Double Opt-In confirmation email via Resend API |
| `/api/notify/confirm` | GET | Confirm subscription token, mark subscriber active |
| `/api/notify/unsubscribe` | GET | Remove subscriber by token immediately |
| `/api/notify/subscribers` | GET | Admin: list all active email subscribers |

---

## Database Schema (analytics.db)

**`purchases` table** (payment + download tracking):
- `id`, `paddle_order_id`, `product_id`, `buyer_email`
- `download_hash` (unique URL token)
- `download_count` (max 20)
- `created_at`

**`notify_subscribers` table** (opt-in email alert list):
- `id` (INTEGER PRIMARY KEY AUTOINCREMENT)
- `email` (TEXT UNIQUE)
- `token` (TEXT UNIQUE)
- `status` (TEXT DEFAULT 'pending' - active/pending)
- `created_at` (TIMESTAMP DEFAULT CURRENT_TIMESTAMP)
- `confirmed_at` (TIMESTAMP)

**`videos` table:** song metadata (BPM, duration, title, takt)
**`revenue` table:** Ko-Fi donations/purchases
**`tiktok_analytics`, `ig_analytics`, `fb_analytics`, `yt_analytics`:** platform engagement

---

## Completed Milestones

- [x] Install git, nginx, certbot, sqlite3 on Oracle VM
- [x] Configure Nginx reverse proxy (api.meloscribe.dev → port 8787)
- [x] Let's Encrypt SSL certificate + auto-renewal
- [x] OCI Security List + iptables firewall rules
- [x] Deploy FastAPI backend via public GitHub repo
- [x] Paddle webhook endpoint + purchase recording
- [x] Cloudflare R2 presigned download URL generation (15 min, max 20 hits)
- [x] SQLite WAL mode + connection timeout to prevent locking under concurrent load
- [x] `download_hash` + `download_count` columns migrated into `purchases` table
- [x] Inject R2 credentials + Paddle API key into server `settings.json`
- [x] Separated backend into public repo (clean history — no credentials ever committed)
- [x] Added `notify_subscribers` table migration to `db_setup.py`
- [x] Implemented Double Opt-In subscription email system (`/api/notify/*` endpoints) using Resend API
- [x] Deployed and verified backend updates live on Oracle Cloud VM

## Active Blockers / Next Steps

- **BLOCKED — Paddle Domain Verification abgelehnt**: Paddle Dashboard zeigt "Action required" für meloscribe.dev. Kein Live-Webhook-Test möglich bis Support-Ticket (sellers@paddle.com) gelöst ist. Klärung: Anforderungen für Domain-Freischaltung + undokumentierter 10%-Flat-Fee-Tarif.
- Paddle-Webhook-Signaturprüfung (HMAC-SHA256) ist implementiert — sobald Freischaltung erfolgt, End-to-End-Test durchführen.
- Email verification flow verified with simulated Resend API key — verify live with real opt-ins when users join.
- Presigned URL Expiry und Download-Counter-Dekrement in Produktion verifizieren.

