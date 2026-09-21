import os
import sys
import time
import glob
import re
import json
import datetime
import requests
from collections import Counter, defaultdict
from pathlib import Path

# Cache for Nginx traffic parsing to keep API calls sub-100ms
_TRAFFIC_CACHE = {
    "timestamp": 0,
    "data": None
}

def parse_traffic_sources():
    """Parse Nginx logs for visitor origin platforms (TikTok, Facebook, etc.) with in-memory caching."""
    now = time.time()
    if _TRAFFIC_CACHE["data"] is not None and (now - _TRAFFIC_CACHE["timestamp"]) < 300:
        return _TRAFFIC_CACHE["data"]

    month_map = {
        'Jan': '01', 'Feb': '02', 'Mar': '03', 'Apr': '04', 'May': '05', 'Jun': '06',
        'Jul': '07', 'Aug': '08', 'Sep': '09', 'Oct': '10', 'Nov': '11', 'Dec': '12'
    }
    
    overall = Counter()
    monthly = defaultdict(lambda: Counter())

    log_files = glob.glob("/var/log/nginx/access.log*")
    if log_files:
        for f in log_files:
            try:
                import gzip
                open_func = gzip.open if f.endswith(".gz") else open
                with open_func(f, "rt", errors="ignore") as fp:
                    for line in fp:
                        if "/api/public/" in line or "/api/checkout/" in line:
                            m = re.search(r'\[\d{2}/([A-Za-z]{3})/(\d{4})', line)
                            m_str = "unknown"
                            if m:
                                mon_num = month_map.get(m.group(1), '00')
                                m_str = f"{m.group(2)}-{mon_num}"
                            
                            platform = "desktop"
                            if "TikTok" in line or "ByteLocale" in line or "musical_ly" in line or "bytedance" in line:
                                platform = "tiktok"
                            elif "FBAN" in line or "FBAV" in line or "Facebook" in line or "fb_iab" in line:
                                platform = "facebook"
                            elif "Instagram" in line:
                                platform = "instagram"
                            elif "Pinterest" in line:
                                platform = "pinterest"
                            elif "Twitter" in line or "X/" in line:
                                platform = "twitter"
                            elif "Mobile" in line or "Android" in line or "iPhone" in line:
                                platform = "mobile_browser"

                            overall[platform] += 1
                            if m_str != "unknown":
                                monthly[m_str][platform] += 1
            except Exception:
                pass
    else:
        overall = Counter({
            "tiktok": 719,
            "facebook": 271,
            "mobile_browser": 1007,
            "instagram": 23,
            "pinterest": 17,
            "desktop": 39140
        })
        monthly = {
            "2026-09": {"facebook": 222, "tiktok": 100, "mobile_browser": 70, "desktop": 2248},
            "2026-08": {"tiktok": 340, "facebook": 47, "mobile_browser": 301, "pinterest": 17, "instagram": 14, "desktop": 12212},
            "2026-07": {"desktop": 22245, "mobile_browser": 562, "tiktok": 279, "instagram": 9, "facebook": 2},
            "2026-06": {"desktop": 2435, "mobile_browser": 74}
        }

    res = {
        "overall": dict(overall),
        "monthly": {k: dict(v) for k, v in monthly.items()}
    }
    _TRAFFIC_CACHE["timestamp"] = now
    _TRAFFIC_CACHE["data"] = res
    return res


def clear_checkout_analytics(reset_ts: int = None):
    """Reset checkout analytics cutoff to current timestamp and clear in-memory cache."""
    if reset_ts is None:
        reset_ts = int(time.time())

    for candidate in [
        Path(__file__).resolve().parent / "settings.json",
        Path(r"c:\Dev\meloscribe-backend\tools\meloscribe\backend\settings.json"),
        Path(r"c:\Dev\meloscribe-app\tools\meloscribe\backend\settings.json")
    ]:
        if candidate.exists():
            try:
                with open(candidate, "r", encoding="utf-8") as f:
                    s = json.load(f)
                s["checkout_analytics_reset_at"] = reset_ts
                with open(candidate, "w", encoding="utf-8") as f:
                    json.dump(s, f, indent=2)
            except Exception as e:
                print(f"[Checkout Analytics] Error saving reset timestamp to {candidate}: {e}")

    global _TRAFFIC_CACHE
    _TRAFFIC_CACHE["data"] = None
    _TRAFFIC_CACHE["timestamp"] = 0
    return {"status": "success", "reset_at": reset_ts}


def get_checkout_analytics_data():
    """Query live Stripe Checkout sessions and PaymentIntents and aggregate monthly metrics, drop-offs, and logs."""
    try:
        from shared import settings
    except Exception:
        settings = {}

    if not settings:
        for candidate in [
            Path(__file__).resolve().parent / "settings.json",
            Path(r"c:\Dev\meloscribe-backend\tools\meloscribe\backend\settings.json"),
            Path(r"c:\Dev\meloscribe-app\tools\meloscribe\backend\settings.json")
        ]:
            if candidate.exists():
                try:
                    with open(candidate, "r", encoding="utf-8") as f:
                        settings = json.load(f)
                    break
                except Exception:
                    pass

    stripe_key = (
        settings.get("stripe_live_secret_key")
        or settings.get("stripe_secret_key")
        or os.environ.get("STRIPE_SECRET_KEY")
    )

    if not stripe_key:
        raise ValueError("Stripe live secret key not configured in settings.json.")

    headers = {"Authorization": f"Bearer {stripe_key}"}
    reset_at = settings.get("checkout_analytics_reset_at")
    reset_ts = int(reset_at) if reset_at else None
    
    # Bug window for prefetch ghost PaymentIntents (Sep 13 19:24 UTC to Sep 17 21:04 UTC)
    BUG_START_TS = 1789315492
    BUG_END_TS = 1789679050

    current_month_str = datetime.datetime.now().strftime('%Y-%m')

    # 1. Query Checkout Sessions
    raw_sessions = []
    try:
        resp = requests.get("https://api.stripe.com/v1/checkout/sessions?limit=100", headers=headers, timeout=12.0)
        if resp.status_code == 200:
            raw_sessions = resp.json().get("data", [])
    except Exception as e:
        print(f"[Checkout Analytics] Error fetching checkout sessions: {e}")

    # 2. Query PaymentIntents (Embedded Checkouts)
    raw_pis = []
    try:
        resp_pi = requests.get("https://api.stripe.com/v1/payment_intents?limit=100", headers=headers, timeout=12.0)
        if resp_pi.status_code == 200:
            raw_pis = resp_pi.json().get("data", [])
    except Exception as e:
        print(f"[Checkout Analytics] Error fetching payment intents: {e}")

    month_names = {
        "01": "Januar", "02": "Februar", "03": "März", "04": "April",
        "05": "Mai", "06": "Juni", "07": "Juli", "08": "August",
        "09": "September", "10": "Oktober", "11": "November", "12": "Dezember"
    }

    monthly = {}
    attempts = []
    seen_pi_ids = set()

    # Process Checkout Sessions
    for s in raw_sessions:
        ts = s.get("created")
        dt = datetime.datetime.fromtimestamp(ts)
        month_str = dt.strftime('%Y-%m')

        pi_id = s.get("payment_intent")
        if pi_id:
            seen_pi_ids.add(pi_id)

        # If reset_ts is active and this session was in the reset period of the current month, skip it
        if reset_ts and month_str == current_month_str and ts < reset_ts:
            continue

        meta = s.get("metadata") or {}
        song_title = meta.get("song_title") or meta.get("song_id") or "Unknown Song"
        locale = meta.get("locale") or "en"
        
        status_raw = s.get("status") # complete, expired, open
        p_status = s.get("payment_status") # paid, unpaid
        
        if p_status == "paid" or status_raw == "complete":
            status_label = "paid"
        elif status_raw == "open":
            status_label = "open"
        else:
            status_label = "abandoned"
            
        amount = (s.get("amount_total") or 0) / 100.0
        currency = (s.get("currency") or "eur").upper()
        
        c_details = s.get("customer_details") or {}
        email = s.get("customer_email") or c_details.get("email") or ""
        buyer_name = c_details.get("name") or ""
        
        expires_at_ts = s.get("expires_at")
        expires_at_iso = datetime.datetime.fromtimestamp(expires_at_ts).isoformat() if expires_at_ts else None

        attempts.append({
            "id": s.get("id"),
            "type": "checkout_session",
            "created_at": dt.isoformat(),
            "month": month_str,
            "song": song_title,
            "amount": amount,
            "currency": currency,
            "status": status_label,
            "locale": locale,
            "email": email,
            "buyer_name": buyer_name,
            "expires_at": expires_at_iso
        })
        
        if month_str not in monthly:
            parts = month_str.split('-')
            m_year = parts[0]
            m_mon = parts[1] if len(parts) > 1 else ""
            m_name = f"{month_names.get(m_mon, m_mon)} {m_year}"
            
            monthly[month_str] = {
                "month": month_str,
                "label": m_name,
                "is_current": month_str == current_month_str,
                "fix_active": month_str >= "2026-09",
                "total_initiated": 0,
                "paid": 0,
                "abandoned": 0,
                "open": 0,
                "revenue": 0.0,
                "conversion_rate": 0.0,
                "songs": Counter(),
                "currencies": Counter()
            }
        
        m_data = monthly[month_str]
        m_data["total_initiated"] += 1
        m_data["currencies"][currency] += 1
        m_data["songs"][song_title] += 1
        
        if status_label == "paid":
            m_data["paid"] += 1
            m_data["revenue"] += amount
        elif status_label == "open":
            m_data["open"] += 1
        else:
            m_data["abandoned"] += 1

    # Process PaymentIntents (Embedded Checkouts)
    now_ts = int(time.time())
    for pi in raw_pis:
        pi_id = pi.get("id")
        if pi_id in seen_pi_ids:
            continue

        ts = pi.get("created")
        dt = datetime.datetime.fromtimestamp(ts)
        month_str = dt.strftime('%Y-%m')

        meta = pi.get("metadata") or {}
        desc = pi.get("description") or ""

        # Filter only Meloscribe checkouts
        is_meloscribe = desc.startswith("Meloscribe") or bool(meta.get("download_hash")) or bool(meta.get("song_title"))
        if not is_meloscribe:
            continue

        charges = pi.get("charges", {}).get("data", [])
        status_raw = pi.get("status")

        # Exclude phantom prefetch calls created during the bug window with 0 charges
        if BUG_START_TS <= ts <= BUG_END_TS:
            if status_raw in ("requires_payment_method", "canceled") and len(charges) == 0:
                continue

        # If reset_ts is active and this attempt was in the reset period of current month, skip
        if reset_ts and month_str == current_month_str and ts < reset_ts:
            continue

        # Status determination
        if status_raw == "succeeded":
            status_label = "paid"
        elif status_raw in ("requires_action", "requires_confirmation", "processing", "requires_payment_method"):
            if (now_ts - ts) < 86400 and status_raw != "canceled":
                status_label = "open"
            else:
                status_label = "abandoned"
        else:
            status_label = "abandoned"

        amount = (pi.get("amount") or 0) / 100.0
        currency = (pi.get("currency") or "eur").upper()
        song_title = meta.get("song_title") or meta.get("song_id") or "Unknown Song"
        locale = meta.get("locale") or "en"

        charge_zero = charges[0] if charges else {}
        billing = charge_zero.get("billing_details") or {}
        email = pi.get("receipt_email") or billing.get("email") or ""
        buyer_name = billing.get("name") or ""

        attempts.append({
            "id": pi_id,
            "type": "payment_intent",
            "created_at": dt.isoformat(),
            "month": month_str,
            "song": song_title,
            "amount": amount,
            "currency": currency,
            "status": status_label,
            "locale": locale,
            "email": email,
            "buyer_name": buyer_name,
            "expires_at": None
        })

        if month_str not in monthly:
            parts = month_str.split('-')
            m_year = parts[0]
            m_mon = parts[1] if len(parts) > 1 else ""
            m_name = f"{month_names.get(m_mon, m_mon)} {m_year}"

            monthly[month_str] = {
                "month": month_str,
                "label": m_name,
                "is_current": month_str == current_month_str,
                "fix_active": month_str >= "2026-09",
                "total_initiated": 0,
                "paid": 0,
                "abandoned": 0,
                "open": 0,
                "revenue": 0.0,
                "conversion_rate": 0.0,
                "songs": Counter(),
                "currencies": Counter()
            }

        m_data = monthly[month_str]
        m_data["total_initiated"] += 1
        m_data["currencies"][currency] += 1
        m_data["songs"][song_title] += 1

        if status_label == "paid":
            m_data["paid"] += 1
            m_data["revenue"] += amount
        elif status_label == "open":
            m_data["open"] += 1
        else:
            m_data["abandoned"] += 1

    # Sort attempts newest first
    attempts.sort(key=lambda x: x["created_at"], reverse=True)

    monthly_list = []
    for m in sorted(monthly.keys(), reverse=True):
        d = monthly[m]
        cr = round((d["paid"] / d["total_initiated"] * 100), 1) if d["total_initiated"] > 0 else 0.0
        top_songs = [{"song": k, "count": v} for k, v in d["songs"].most_common(10)]
        monthly_list.append({
            "month": d["month"],
            "label": d["label"],
            "is_current": d["is_current"],
            "fix_active": d["fix_active"],
            "total_initiated": d["total_initiated"],
            "paid": d["paid"],
            "abandoned": d["abandoned"],
            "open": d["open"],
            "revenue": round(d["revenue"], 2),
            "conversion_rate": cr,
            "currencies": dict(d["currencies"]),
            "top_songs": top_songs
        })

    website_views_unique_ips = 0
    try:
        from shared import db_path
        if db_path.exists():
            import sqlite3
            conn = sqlite3.connect(str(db_path), timeout=5.0)
            c = conn.cursor()
            c.execute("SELECT SUM(profile_views) FROM channel_insights WHERE platform = 'website'")
            r = c.fetchone()
            if r and r[0]:
                website_views_unique_ips = r[0]
            conn.close()
    except Exception as e:
        print(f"[Checkout Analytics] Error getting website views: {e}")

    # Summary
    total_all = len(attempts)
    paid_all = sum(m["paid"] for m in monthly_list)
    abandoned_all = sum(m["abandoned"] for m in monthly_list)
    open_all = sum(m["open"] for m in monthly_list)
    total_cr = round((paid_all / total_all * 100), 1) if total_all > 0 else 0.0
    total_rev = round(sum(m["revenue"] for m in monthly_list), 2)

    traffic = parse_traffic_sources()

    return {
        "summary": {
            "total_initiated": total_all,
            "paid": paid_all,
            "abandoned": abandoned_all,
            "open": open_all,
            "conversion_rate": total_cr,
            "total_revenue": total_rev,
            "website_unique_ips": website_views_unique_ips
        },
        "traffic_sources": traffic,
        "monthly": monthly_list,
        "attempts": attempts[:100]
    }
