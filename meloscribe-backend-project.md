# meloscribe-backend — Project Status & Roadmap

Living documentation for the meloscribe public API backend (`C:\Dev\meloscribe-backend`).

**GitHub repo:** https://github.com/meloscribe/meloscribe-backend (public)
**Deployed on:** Oracle Cloud VM — `ubuntu@152.70.23.171`
**API base:** `https://api.meloscribe.dev`

> Credentials (API keys, R2 secrets, SSH key, Stripe keys) are stored exclusively in
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
    └── Stripe webhook (purchase verification)

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
| `/api/webhooks/stripe` | POST | Receive Stripe purchase events, create `purchases` row, generate `download_hash` |
| `/order/{hash}` | GET | Serve success page — validates hash, returns presigned R2 download URLs (15 min, max 50 downloads) |
| `/api/public/songs` | GET | Return dynamically localized `songs.json` catalog (replaces € price symbol with $ or £ based on IP location) |
| `/api/checkout/create-session` | POST | Generate a Stripe Checkout Session mapped to user's local IP currency (EUR/USD/GBP) |
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
- `id`, `transaction_id`, `email`, `song_name`, `amount`, `currency`, `status`, `download_hash`, `download_count` (max 50)
- `downloaded_types`, `locale`, `ip_addresses`, `buyer_name`

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
- [x] Stripe webhook endpoint + purchase recording
- [x] Cloudflare R2 presigned download URL generation (15 min, max 50 hits)
- [x] SQLite WAL mode + connection timeout to prevent locking under concurrent load
- [x] `download_hash` + `download_count` + `downloaded_types` columns migrated into `purchases` table
- [x] Inject R2 credentials + Stripe API keys into server `settings.json`
- [x] Separated backend into public repo (clean history — no credentials ever committed)
- [x] Added `notify_subscribers` table migration to `db_setup.py`
- [x] Implemented Double Opt-In subscription email system (`/api/notify/*` endpoints) using Resend API
- [x] Deployed and verified backend updates live on Oracle Cloud VM
- [x] Implemented unique file downloaded types tracking to prevent double count decrements on duplicate downloads
- [x] Applied brand gradient header styling to delivery emails with a solid cyan fallback
- [x] Reduced default download limit from 100 to 50 hits, added informative help tooltips, and routed support mailto links to info@meloscribe.dev
- [x] Updated Stripe webhook refund processing to handle charge/refund events, setting the purchase status specifically to 'refunded'

- [x] Cleaned up public backend .gitignore to ensure token, credential, and settings files are strictly ignored and never exposed
- [x] Updated get_preview_video endpoint to dynamically check and stream lightweight preview video files ([Song]_preview.mp4) from Cloudflare R2 bucket with full video fallback
- [x] Implemented direct download button flow for free ($0.00) songs, bypassing Stripe payment screen on the website
- [x] Upgraded batch processor loop to automatically abort queue execution on step failures to prevent cascaded errors
- [x] Enriched logs proxying to merge local and remote log streams, and added subprocess execution details capture for precise failure diagnostics
- [x] Swapped `pythonw.exe` for standard `python.exe` in Electron's `main.js` to ensure the backend spawns inside an interactive Windows session with screen capture privileges
- [x] Implemented dynamic IP-based checkout currency routing on Stripe (`routes_public.py`), preserving round numeric values (e.g. `4 €` -> `4 $` for US or `4 £` for UK) using Cloudflare `CF-IPCountry` and `ip-api.com` fallback with in-memory caching.
- [x] Added public catalog endpoint `/api/public/songs` to dynamically convert the `price` field currency symbols (`€` to `$` or `£`) based on client IP, allowing the website catalog to automatically display the correct currency to international users.
- [x] Synchronized and deployed changes to local and remote production OCI VM servers, restarted FastAPI services, and verified dynamic routing with custom test cases.
- [x] Updated TikTok API uploader integration to use Content Posting API v2 Inbox Drafts (`/v2/post/publish/inbox/video/init/`) with integer floor chunking (`file_size // chunk_size`), enabling inbox draft delivery for creators.
- [x] Resolved Facebook Graph API Reels reach suppression: migrated Meta App to Live Mode, raised duration cutoff to 90s (`is_short = duration <= 90.0`), and enriched SEO hashtags in `settings.json` and `settings.py`.
- [x] Executed Facebook Page cleanup of low-view/duplicate dev-mode Reels via Graph API and scheduled 12 staged songs in `queue.db` on OCI VM server with a daily 17:00 cadence.
- [x] Fixed mobile checkout abandonment: Removed billing_address_collection="required" and forced invoice_creation in routes_public.py, switching to billing_address_collection="auto" to allow 1-click Apple Pay/Google Pay/Card payments without physical street address entry. Deployed live to Oracle VM and restarted backend service.
- [x] Implemented Checkout & Funnel Analytics: Added `checkout_analytics.py` and `/api/admin/checkout-analytics` in `routes_admin.py` with in-memory caching. Analyzes live Stripe Checkout sessions (monthly aggregates, status tracking, drop-offs) and Nginx social visitor attribution (TikTok: 719 / 70.1%, Facebook: 271 / 26.4%, Instagram: 23, Pinterest: 17). Deployed live to Oracle VM.
- [x] Added `/api/facebook/sync` in `routes_settings.py` and synced Facebook Page video metrics (65 videos updated, Golden Brown 229k views, River Flows in You 281k views, Sweetest Rain 82k views). Consolidated website analytics into `WebsiteTab` with traffic transparency.
- [x] Facebook Engagement Breakdown & Rich Metrics: Enhanced `fb_sync.py` to retrieve `likes.summary(true)` and `comments.summary(true)` via Graph API, recording 24,406 likes and 214 comments across 65 Facebook videos into SQLite and VM production DB.
- [x] Supported Multi-State Catalog Management: Synchronized `songs.json` supporting direct sales, Sheet Music Direct external redirects (`arrangemeUrl`), and `paymentsDisabled: true`. Preserved metadata keys across automated bot operations.
- [x] Complete Ecosystem Transition to Full Arrangements: Defaulted `format: 'full_arrangement'` in `routes_workflow.py` and `settings.py`, eliminating legacy `viral_part` dependencies. Verified all SQL queries across routes operate without format filters.
- [x] Set High-Risk Major-Label Titles to Currently Unavailable: Flagged Sonne, Hallelujah, The Scientist, Mockingbird, and Believer with `paymentsDisabled: true` in production `songs.json` on Oracle VM pending official ArrangeMe/Sheet Music Direct approvals.
- [x] Consolidated Single-Card Catalog Sync: Synced updated 27-item `songs.json` catalog with unified `hasEasy`, `easyPrice`, `easyStripePriceId`, and `difficulty: "Original / Easy"` to Oracle VM production and local backend, preserving existing Stripe Price IDs and R2 bucket storage paths.
- [x] Automated Hal Leonard ArrangeMe Publishing Pipeline: Validated Playwright automation bot (`tools/arrangeme_bot.py`) for publishing high-risk copyrighted titles (*Sonne* by Rammstein) directly to Hal Leonard ArrangeMe, creating complete published drafts ready to replace `paymentsDisabled` flags with live Sheet Music Direct partner URLs once approved.
- [x] Upgraded AI Engine to Gemini 3.8 Flash: Updated `ai_agent.py` to `models/gemini-3.8-flash` for lower latency, higher reasoning capacity, and reliable agentic capabilities.

## Active Blockers / Next Steps

- Keine aktiven Blockaden. Das Payment-Gateway wurde auf Stripe Checkout (redirects via FastAPI-Sessions) migriert. Die Domain-Verifizierung läuft fehlerfrei.
- End-to-end sandbox checkout flows have been fully verified with client event redirection and direct transaction lookup fallback; live webhook sign verification is active. Preview video R2 streaming logic is fully functional.
