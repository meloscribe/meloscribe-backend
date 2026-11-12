import os
import sys
import io
import re
import time
import json
import base64
import sqlite3
import platform
import datetime
import urllib.parse
import subprocess
import threading
from pathlib import Path
from typing import Optional, Dict, Any, List

import requests
from fastapi import APIRouter, Request, HTTPException, Response, BackgroundTasks
from pydantic import BaseModel
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from py_vapid import Vapid
from pywebpush import webpush, WebPushException
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

# Ensure backend root is on sys.path
CURRENT_DIR = Path(__file__).resolve().parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.append(str(CURRENT_DIR))

try:
    from shared import db_path, load_settings, get_server_api_key, CREATION_FLAGS
    from settings import save_settings
except Exception:
    db_path = CURRENT_DIR / "analytics.db"
    CREATION_FLAGS = 0x08000000 if os.name == "nt" else 0
    def load_settings():
        sf = CURRENT_DIR / "settings.json"
        if sf.exists():
            with open(sf, "r", encoding="utf-8") as f:
                return json.load(f)
        return {}
    def save_settings(d):
        sf = CURRENT_DIR / "settings.json"
        with open(sf, "w", encoding="utf-8") as f:
            json.dump(d, f, indent=4)
    def get_server_api_key():
        kp = CURRENT_DIR / "api_key.txt"
        return kp.read_text(encoding="utf-8").strip() if kp.exists() else None

router = APIRouter()

# In-memory caches to keep pulse-summary and card rendering ultra-fast
_COVER_CACHE: Dict[str, bytes] = {}
_CHECKOUT_CACHE: Dict[str, Any] = {
    "ts": 0.0,
    "updating": False,
    "data": None,
    "traffic_overall": {}
}

def warm_pulse_caches_async(force: bool = False) -> None:
    """Refresh slow Stripe checkout analytics and Nginx log parsing asynchronously."""
    now = time.time()
    if _CHECKOUT_CACHE["updating"]:
        return
    if not force and _CHECKOUT_CACHE["data"] is not None and (now - _CHECKOUT_CACHE["ts"]) < 300:
        return

    _CHECKOUT_CACHE["updating"] = True

    def _worker():
        try:
            from checkout_analytics import parse_traffic_sources, get_checkout_analytics_data
            try:
                t_data = parse_traffic_sources()
                _CHECKOUT_CACHE["traffic_overall"] = t_data.get("overall", {}) or {}
            except Exception as err:
                print(f"[Pulse Cache] Traffic sources refresh warning: {err}")

            try:
                ca_data = get_checkout_analytics_data()
                if ca_data:
                    _CHECKOUT_CACHE["data"] = ca_data
                    _CHECKOUT_CACHE["ts"] = time.time()
            except Exception as err:
                print(f"[Pulse Cache] Checkout analytics refresh warning: {err}")
        finally:
            _CHECKOUT_CACHE["updating"] = False

    threading.Thread(target=_worker, daemon=True).start()

# -------------------------------------------------------------------
# Database Initialization & Migration (WAL mode, timeout=30.0)
# -------------------------------------------------------------------
def get_db_connection():
    conn = sqlite3.connect(str(db_path), timeout=30.0)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.row_factory = sqlite3.Row
    return conn

def init_push_db():
    conn = get_db_connection()
    c = conn.cursor()
    # Check existing tables first, commit after SELECT before DDL
    c.execute("SELECT name FROM sqlite_master WHERE type='table'")
    _ = c.fetchall()
    conn.commit()

    c.execute("""
    CREATE TABLE IF NOT EXISTS push_subscriptions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        endpoint TEXT UNIQUE NOT NULL,
        p256dh TEXT NOT NULL,
        auth TEXT NOT NULL,
        user_agent TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)
    conn.commit()

    c.execute("""
    CREATE TABLE IF NOT EXISTS pulse_notifications (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        type TEXT NOT NULL,
        title TEXT NOT NULL,
        subtitle TEXT,
        amount TEXT,
        song_name TEXT,
        cover_url TEXT,
        meta_json TEXT,
        is_read INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)
    conn.commit()
    conn.close()

# -------------------------------------------------------------------
# VAPID Key Management (ECDSA P-256)
# -------------------------------------------------------------------
def get_or_create_vapid_keys() -> tuple[str, str, str]:
    """Retrieve VAPID keys from settings.json or auto-generate ECDSA P-256 keypair."""
    settings = load_settings()
    pub_key = settings.get("vapid_public_key")
    priv_key = settings.get("vapid_private_key")
    claims_email = settings.get("vapid_claims_email") or "mailto:info@meloscribe.dev"

    if pub_key and priv_key:
        return pub_key, priv_key, claims_email

    print("[WebPush] Generating new ECDSA P-256 VAPID keypair...")
    v = Vapid()
    v.generate_keys()

    raw_point = v.public_key.public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
    pub_key = base64.urlsafe_b64encode(raw_point).decode("utf-8").rstrip("=")
    priv_key = v.private_pem().decode("utf-8")

    settings["vapid_public_key"] = pub_key
    settings["vapid_private_key"] = priv_key
    settings["vapid_claims_email"] = claims_email
    save_settings(settings)

    for backup_file in [Path("C:/Dev/credentials.json"), Path("C:/Dev/meloscribe_credentials_backup.json")]:
        if backup_file.exists():
            try:
                with open(backup_file, "r", encoding="utf-8") as f:
                    b_data = json.load(f)
                b_data["vapid"] = {
                    "public_key": pub_key,
                    "private_key": priv_key,
                    "claims_email": claims_email
                }
                with open(backup_file, "w", encoding="utf-8") as f:
                    json.dump(b_data, f, indent=4)
                print(f"[WebPush] Backed up VAPID keys to {backup_file}")
            except Exception as e:
                print(f"[WebPush] Could not backup keys to {backup_file}: {e}")

    return pub_key, priv_key, claims_email

# -------------------------------------------------------------------
# Auth Verification for Pulse Endpoints
# -------------------------------------------------------------------
def verify_pulse_admin(request: Request):
    """Allow access if X-Meloscribe-Key matches server key OR x-admin-passcode matches admin passcode."""
    passcode = request.headers.get("x-admin-passcode")
    api_key = request.headers.get("X-Meloscribe-Key")
    stored_key = get_server_api_key()
    expected_passcode = load_settings().get("admin_passcode", "579110")

    if passcode and str(passcode) == str(expected_passcode):
        return True
    if stored_key and api_key and api_key == stored_key:
        return True
    raise HTTPException(status_code=401, detail="Unauthorized: invalid admin credentials.")

# -------------------------------------------------------------------
# Cover Asset Name Resolution
# -------------------------------------------------------------------
def clean_cover_song_name(song_name: str) -> str:
    if not song_name:
        return ""
    cleaned = re.sub(r"\s*\((All Parts|Part \d+|Easy|Easy Version|Original|Original / Easy)\)", "", song_name, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+(Easy|Easy Version|Original)$", "", cleaned, flags=re.IGNORECASE)
    return cleaned.strip()

def resolve_cover_url(song_name: str) -> str:
    cleaned = clean_cover_song_name(song_name)
    if not cleaned:
        return "https://api.meloscribe.dev/api/push/badge.png"
    return f"https://www.meloscribesheets.com/covers/{urllib.parse.quote(cleaned)}_clean.jpg"

def load_cover_image_bytes(song_name: str) -> Optional[bytes]:
    cleaned = clean_cover_song_name(song_name)
    if not cleaned:
        return None
    if cleaned in _COVER_CACHE:
        return _COVER_CACHE[cleaned]

    cover_candidates = [
        CURRENT_DIR.parent.parent / "Covers" / f"{cleaned}_wide.jpg",
        CURRENT_DIR.parent.parent / "Covers" / f"{cleaned}_clean.jpg",
        CURRENT_DIR.parent.parent / "Covers" / f"{cleaned}.jpg",
        Path(f"C:/Dev/meloscribe/Covers/{cleaned}_wide.jpg"),
        Path(f"C:/Dev/meloscribe/Covers/{cleaned}_clean.jpg"),
        Path(f"C:/Dev/meloscribe/Covers/{cleaned}.jpg"),
        Path(f"/home/ubuntu/meloscribe/staging/{cleaned}/Covers/{cleaned}_wide.jpg"),
        Path(f"/home/ubuntu/meloscribe/staging/{cleaned}/Covers/{cleaned}_clean.jpg"),
        Path(f"/home/ubuntu/meloscribe/staging/{cleaned}/Covers/{cleaned}.jpg"),
    ]
    for p in cover_candidates:
        if p.exists():
            try:
                data = p.read_bytes()
                if len(_COVER_CACHE) > 64:
                    _COVER_CACHE.clear()
                _COVER_CACHE[cleaned] = data
                return data
            except Exception:
                pass

    # Fallback: fetch from public meloscribesheets.com CDN
    for suffix in ["_clean.jpg", ".jpg"]:
        url = f"https://www.meloscribesheets.com/covers/{urllib.parse.quote(cleaned)}{suffix}"
        try:
            resp = requests.get(url, timeout=2.0)
            if resp.status_code == 200 and resp.content:
                if len(_COVER_CACHE) > 64:
                    _COVER_CACHE.clear()
                _COVER_CACHE[cleaned] = resp.content
                return resp.content
        except Exception:
            pass
    return None

# -------------------------------------------------------------------
# Dynamic Pillow Glass Card & Monochrome Badge Renderer
# -------------------------------------------------------------------
def get_system_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    """Resolve cross-platform font (Windows Segoe UI / Arial vs Ubuntu DejaVu Sans)."""
    if bold:
        font_candidates = [
            r"C:\Windows\Fonts\segoeuib.ttf",
            r"C:\Windows\Fonts\arialbd.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf"
        ]
    else:
        font_candidates = [
            r"C:\Windows\Fonts\segoeui.ttf",
            r"C:\Windows\Fonts\arial.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/freefont/FreeSans.ttf"
        ]
    for c in font_candidates:
        if os.path.exists(c):
            try:
                return ImageFont.truetype(c, size)
            except Exception:
                pass
    return ImageFont.load_default()

def render_monochrome_badge() -> bytes:
    """Generate pure white 96x96 RGBA alpha mask so Android status bar renders crisp M-monogram."""
    badge_candidates = [
        CURRENT_DIR.parent.parent / "pfps" / "monogram-transparent.png",
        CURRENT_DIR / "monogram-transparent.png",
        Path("C:/Dev/meloscribe-app/tools/pfps/monogram-transparent.png"),
        Path("/home/ubuntu/meloscribe/tools/pfps/monogram-transparent.png")
    ]
    badge_path = next((p for p in badge_candidates if p.exists()), None)

    if badge_path:
        src = Image.open(badge_path).convert("RGBA").resize((96, 96), Image.Resampling.LANCZOS)
        _, _, _, a = src.split()
        white = Image.new("RGB", (96, 96), (255, 255, 255))
        badge = Image.merge("RGBA", (*white.split(), a))
    else:
        badge = Image.new("RGBA", (96, 96), (0, 0, 0, 0))
        draw = ImageDraw.Draw(badge)
        draw.ellipse([8, 8, 88, 88], fill=(255, 255, 255, 255))

    buf = io.BytesIO()
    badge.save(buf, format="PNG")
    return buf.getvalue()

def _draw_centered_text(draw: ImageDraw.ImageDraw, cx: int, y: int, text: str, font: ImageFont.FreeTypeFont, fill: tuple) -> None:
    bbox = draw.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    draw.text((cx - tw // 2, y), text, font=font, fill=fill)

def render_glass_card(
    notif_id: int,
    notif_type: str,
    title: str,
    subtitle: str,
    amount: str = "",
    song_name: str = "",
    meta: Optional[Dict[str, Any]] = None
) -> bytes:
    """
    Renders a distinct 1200x600 px (2:1) notification banner for Android SystemUI:
      - Paid Sale: Stripe-style ultra-minimalist dark ledger card with emerald stripe & giant hero amount.
      - Subscriber / Free Claim: Ice-Cyan asymmetric lead unlock card with circular M-monogram ring.
      - Suggestion: Neon Pink/Violet vote-counter ticket card.
    """
    meta = meta or {}
    W, H = 1200, 600

    # Determine effective visual identity: a 0.00 amount order renders as subscriber/free unlock, never as a paid Stripe sale
    numeric_amt = 0.0
    if amount:
        try:
            numeric_amt = float(str(amount).replace(",", ".").strip())
        except Exception:
            numeric_amt = 0.0

    visual_type = notif_type
    if notif_type == "sale" and numeric_amt <= 0.0:
        visual_type = "subscriber"

    cov_bytes = load_cover_image_bytes(song_name)
    raw_cov_img = None
    if cov_bytes:
        try:
            raw_cov_img = Image.open(io.BytesIO(cov_bytes)).convert("RGB")
        except Exception as e:
            print(f"[Pillow Card] Error processing cover: {e}")

    # ------------------------------------------------------------------
    # VARIANT 1: PAID SALE — Minimalist Stripe-Style Dopamine Card
    # ------------------------------------------------------------------
    if visual_type == "sale":
        bg_img = Image.new("RGBA", (W, H), (6, 9, 17, 255))
        card_x0, card_y0, card_x1, card_y1 = 68, 48, 1132, 552

        # Soft emerald radial corner glow
        bloom = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        bdraw = ImageDraw.Draw(bloom)
        bdraw.ellipse([card_x0 - 80, card_y0 - 40, card_x0 + 360, card_y1 + 40], fill=(16, 185, 129, 32))
        bloom = bloom.filter(ImageFilter.GaussianBlur(radius=60))
        bg_img = Image.alpha_composite(bg_img, bloom)

        card_layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        cdraw = ImageDraw.Draw(card_layer)
        cdraw.rounded_rectangle(
            [card_x0, card_y0, card_x1, card_y1],
            radius=28,
            fill=(11, 16, 28, 245),
            outline=(16, 185, 129, 135),
            width=2
        )
        # Clean vertical Emerald Payout bar on the left inside the card
        cdraw.rounded_rectangle(
            [card_x0 + 28, card_y0 + 36, card_x0 + 36, card_y1 - 36],
            radius=4,
            fill=(16, 185, 129, 255)
        )
        bg_img = Image.alpha_composite(bg_img, card_layer)
        draw = ImageDraw.Draw(bg_img)

        font_micro = get_system_font(21, bold=True)
        font_hero = get_system_font(94, bold=True)
        font_song = get_system_font(34, bold=True)
        font_meta = get_system_font(23, bold=False)

        tx = card_x0 + 68
        # Status dot + STRIPE micro header
        draw.ellipse([tx, card_y0 + 62, tx + 14, card_y0 + 76], fill=(16, 185, 129, 255))
        draw.text((tx + 24, card_y0 + 56), "STRIPE  •  PAID ORDER", font=font_micro, fill=(52, 211, 153, 255))

        # Huge clean hero payout number
        curr = str(meta.get("currency", "EUR") or "EUR").upper()
        symbol = "€" if curr == "EUR" else ("$" if curr == "USD" else f" {curr}")
        amt_text = f"+{numeric_amt:.2f}{symbol}" if curr in ("EUR", "USD") else f"+{numeric_amt:.2f} {curr}"
        draw.text((tx, card_y0 + 126), amt_text, font=font_hero, fill=(255, 255, 255, 255))

        # Song title (minimalist)
        song_disp = clean_cover_song_name(song_name) if song_name else title
        if len(song_disp) > 30:
            song_disp = song_disp[:28] + "..."
        draw.text((tx, card_y0 + 266), song_disp, font=font_song, fill=(226, 232, 240, 255))

        # Quiet footer: email + locale
        meta_parts = []
        if meta.get("email"):
            meta_parts.append(str(meta["email"]))
        if meta.get("locale"):
            meta_parts.append(str(meta["locale"]).upper())
        meta_line = "  •  ".join(meta_parts) if meta_parts else subtitle
        if len(meta_line) > 46:
            meta_line = meta_line[:43] + "..."
        draw.text((tx, card_y1 - 86), meta_line, font=font_meta, fill=(100, 116, 139, 255))

        # Crisp square cover art on the right with generous breathing room
        if raw_cov_img:
            try:
                thumb_w = 240
                cw, ch = raw_cov_img.size
                side = min(cw, ch)
                sq = raw_cov_img.crop(((cw - side) // 2, (ch - side) // 2, (cw + side) // 2, (ch + side) // 2))
                thumb = sq.convert("RGBA").resize((thumb_w, thumb_w), Image.Resampling.LANCZOS)
                mask = Image.new("L", (thumb_w, thumb_w), 0)
                ImageDraw.Draw(mask).rounded_rectangle([0, 0, thumb_w, thumb_w], radius=24, fill=255)
                thumb_x = card_x1 - thumb_w - 52
                thumb_y = (H - thumb_w) // 2
                bg_img.paste(thumb, (thumb_x, thumb_y), mask)
                draw.rounded_rectangle(
                    [thumb_x, thumb_y, thumb_x + thumb_w, thumb_y + thumb_w],
                    radius=24,
                    outline=(255, 255, 255, 55),
                    width=1
                )
            except Exception:
                pass

        out_buf = io.BytesIO()
        bg_img.save(out_buf, format="PNG")
        return out_buf.getvalue()

    # ------------------------------------------------------------------
    # VARIANT 2: SUBSCRIBER / FREE UNLOCK — Ice-Cyan Circular Identity Card
    # ------------------------------------------------------------------
    if visual_type == "subscriber":
        bg_img = Image.new("RGBA", (W, H), (4, 12, 23, 255))
        draw_bg = ImageDraw.Draw(bg_img)
        for y in range(H):
            t = y / H
            draw_bg.line([(0, y), (W, y)], fill=(int(4 + t * 5), int(12 + t * 18), int(23 + t * 28), 255))

        card_x0, card_y0, card_x1, card_y1 = 76, 68, 1124, 532
        bloom = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        ImageDraw.Draw(bloom).ellipse([120, 120, 440, 480], fill=(0, 245, 255, 45))
        bloom = bloom.filter(ImageFilter.GaussianBlur(radius=55))
        bg_img = Image.alpha_composite(bg_img, bloom)

        card_layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        cdraw = ImageDraw.Draw(card_layer)
        cdraw.rounded_rectangle(
            [card_x0, card_y0, card_x1, card_y1],
            radius=32,
            fill=(8, 22, 38, 235),
            outline=(0, 245, 255, 165),
            width=2
        )
        # Top Cyan Specular Line
        cdraw.line([card_x0 + 48, card_y0 + 1, card_x1 - 48, card_y0 + 1], fill=(0, 245, 255, 230), width=3)
        bg_img = Image.alpha_composite(bg_img, card_layer)
        draw = ImageDraw.Draw(bg_img)

        # Circular Monogram Avatar on the LEFT (completely distinct silhouette from Sale)
        cx, cy, cr = card_x0 + 132, H // 2, 74
        draw.ellipse([cx - cr, cy - cr, cx + cr, cy + cr], fill=(0, 245, 255, 24), outline=(0, 245, 255, 210), width=3)
        try:
            badge_img = Image.open(io.BytesIO(render_monochrome_badge())).convert("RGBA").resize((74, 74), Image.Resampling.LANCZOS)
            bg_img.paste(badge_img, (cx - 37, cy - 37), badge_img)
        except Exception:
            _draw_centered_text(draw, cx, cy - 24, "M", get_system_font(44, bold=True), (255, 255, 255, 255))

        tx = card_x0 + 250
        font_pill = get_system_font(20, bold=True)
        font_email = get_system_font(40, bold=True)
        font_song = get_system_font(28, bold=False)
        font_loc = get_system_font(21, bold=True)

        pill_txt = "FREE SHEET UNLOCK  •  NEW LEAD"
        bbox = draw.textbbox((0, 0), pill_txt, font=font_pill)
        pw = (bbox[2] - bbox[0]) + 34
        draw.rounded_rectangle([tx, card_y0 + 78, tx + pw, card_y0 + 116], radius=19, fill=(0, 245, 255, 36), outline=(56, 189, 248, 200), width=1)
        draw.text((tx + 17, card_y0 + 86), pill_txt, font=font_pill, fill=(125, 211, 252, 255))

        email_disp = str(meta.get("email") or title or "New Subscriber")
        if len(email_disp) > 29:
            email_disp = email_disp[:26] + "..."
        draw.text((tx, card_y0 + 162), email_disp, font=font_email, fill=(255, 255, 255, 255))

        unlocked_song = clean_cover_song_name(song_name) if song_name else subtitle
        sub_line = f"Unlocked: {unlocked_song}" if unlocked_song else "Double Opt-In Confirmed"
        if len(sub_line) > 40:
            sub_line = sub_line[:37] + "..."
        draw.text((tx, card_y0 + 234), sub_line, font=font_song, fill=(186, 230, 253, 255))

        loc_str = str(meta.get("locale") or "en").upper()
        draw.rounded_rectangle([tx, card_y1 - 102, tx + 132, card_y1 - 62], radius=12, fill=(255, 255, 255, 18), outline=(56, 189, 248, 120), width=1)
        draw.text((tx + 18, card_y1 - 94), f"LANG: {loc_str}", font=font_loc, fill=(148, 163, 184, 255))

        out_buf = io.BytesIO()
        bg_img.save(out_buf, format="PNG")
        return out_buf.getvalue()

    # ------------------------------------------------------------------
    # VARIANT 3: COMMUNITY SUGGESTION — Neon Pink / Violet Vote Ticket
    # ------------------------------------------------------------------
    bg_img = None
    if raw_cov_img:
        try:
            cov_w, cov_h = raw_cov_img.size
            target_ratio = W / H
            current_ratio = cov_w / cov_h
            if current_ratio > target_ratio:
                new_w = int(cov_h * target_ratio)
                offset = (cov_w - new_w) // 2
                cropped = raw_cov_img.crop((offset, 0, offset + new_w, cov_h))
            else:
                new_h = int(cov_w / target_ratio)
                offset = (cov_h - new_h) // 2
                cropped = raw_cov_img.crop((0, offset, cov_w, offset + new_h))
            resized = cropped.resize((W, H), Image.Resampling.LANCZOS)
            bg_img = resized.filter(ImageFilter.GaussianBlur(radius=32)).point(lambda p: int(p * 0.28)).convert("RGBA")
        except Exception:
            bg_img = None

    if not bg_img:
        bg_img = Image.new("RGBA", (W, H), (11, 5, 20, 255))

    bloom = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    bdraw = ImageDraw.Draw(bloom)
    bdraw.ellipse([80, 60, 480, 500], fill=(255, 45, 146, 52))
    bdraw.ellipse([740, 120, 1140, 540], fill=(168, 85, 247, 45))
    bloom = bloom.filter(ImageFilter.GaussianBlur(radius=60))
    bg_img = Image.alpha_composite(bg_img, bloom)

    card_x0, card_y0, card_x1, card_y1 = 68, 52, 1132, 548
    card_layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    cdraw = ImageDraw.Draw(card_layer)
    cdraw.rounded_rectangle(
        [card_x0, card_y0, card_x1, card_y1],
        radius=30,
        fill=(18, 10, 30, 224),
        outline=(255, 45, 146, 200),
        width=2
    )
    bg_img = Image.alpha_composite(bg_img, card_layer)
    draw = ImageDraw.Draw(bg_img)

    # Prominent Neon Pink Vote Counter Box on the LEFT
    votes_val = int(meta.get("votes") or 1)
    vx0, vy0, vx1, vy1 = card_x0 + 48, card_y0 + 115, card_x0 + 236, card_y0 + 345
    draw.rounded_rectangle([vx0, vy0, vx1, vy1], radius=26, fill=(255, 45, 146, 36), outline=(255, 45, 146, 235), width=2)
    vcx = (vx0 + vx1) // 2
    _draw_centered_text(draw, vcx, vy0 + 42, str(votes_val), get_system_font(78, bold=True), (255, 255, 255, 255))
    _draw_centered_text(draw, vcx, vy1 - 54, "VOTE" if votes_val == 1 else "VOTES", get_system_font(21, bold=True), (244, 114, 182, 255))

    tx = vx1 + 46
    font_pill = get_system_font(20, bold=True)
    font_title = get_system_font(44, bold=True)
    font_artist = get_system_font(28, bold=False)
    font_hint = get_system_font(21, bold=False)

    draw.rounded_rectangle([tx, vy0 + 4, tx + 292, vy0 + 42], radius=19, fill=(168, 85, 247, 45), outline=(192, 132, 252, 200), width=1)
    draw.text((tx + 18, vy0 + 12), "COMMUNITY REQUEST", font=font_pill, fill=(249, 168, 212, 255))

    req_title = clean_cover_song_name(song_name) if song_name else title
    if len(req_title) > 28:
        req_title = req_title[:25] + "..."
    draw.text((tx, vy0 + 74), req_title, font=font_title, fill=(255, 255, 255, 255))

    artist_str = f"by {meta['artist']}" if meta.get("artist") else subtitle
    if len(artist_str) > 38:
        artist_str = artist_str[:35] + "..."
    draw.text((tx, vy0 + 140), artist_str, font=font_artist, fill=(203, 213, 225, 255))

    draw.text((tx, card_y1 - 82), "Tap notification action to add directly to To-Do", font=font_hint, fill=(148, 163, 184, 215))

    out_buf = io.BytesIO()
    bg_img.save(out_buf, format="PNG")
    return out_buf.getvalue()

# -------------------------------------------------------------------
# Web Push Dispatch Engine
# -------------------------------------------------------------------
def record_pulse_notification(
    notif_type: str,
    title: str,
    subtitle: str,
    amount: str = "",
    song_name: str = "",
    cover_url: str = "",
    meta: Optional[Dict[str, Any]] = None
) -> int:
    """Insert notification into pulse_notifications and return its ID."""
    conn = get_db_connection()
    c = conn.cursor()
    meta_json = json.dumps(meta or {})
    c.execute("""
        INSERT INTO pulse_notifications (type, title, subtitle, amount, song_name, cover_url, meta_json, is_read)
        VALUES (?, ?, ?, ?, ?, ?, ?, 0)
    """, (notif_type, title, subtitle, amount, song_name, cover_url, meta_json))
    conn.commit()
    notif_id = c.lastrowid
    conn.close()
    return notif_id

def send_web_push(
    notif_type: str,
    title: str,
    body: str,
    song_name: str = "",
    amount: str = "",
    currency: str = "EUR",
    url: str = "/?tab=pulse",
    actions: Optional[List[Dict[str, str]]] = None,
    meta: Optional[Dict[str, Any]] = None
):
    """
    Broadcasts a real-time Web Push notification to all subscribed devices and records it in SQLite.
    Applies distinct minimalist Stripe-style copy for paid sales, Ice-Cyan copy for subscribers/free unlocks,
    and Neon-Pink vote copy for community requests.
    Prunes expired subscriptions (HTTP 404 / 410 Gone) automatically.
    """
    init_push_db()
    pub_key, priv_key, claims_email = get_or_create_vapid_keys()

    meta_payload = dict(meta or {})
    if currency and "currency" not in meta_payload:
        meta_payload["currency"] = currency

    numeric_amt = 0.0
    if amount:
        try:
            numeric_amt = float(str(amount).replace(",", ".").strip())
        except Exception:
            numeric_amt = 0.0

    effective_type = notif_type
    if notif_type == "sale" and numeric_amt <= 0.0:
        effective_type = "subscriber"

    resolved_cover = resolve_cover_url(song_name)
    badge_url = "https://pulse.meloscribesheets.com/badge-96.png"

    # Normalize compact, Stripe-style push title, single-line body, icon, and deep-link tab by event identity
    email_str = str(meta_payload.get("email") or "").strip()
    loc_str = str(meta_payload.get("locale") or "en").upper()
    clean_song = clean_cover_song_name(song_name) or song_name
    has_song_cover = bool(clean_cover_song_name(song_name))

    if effective_type == "sale":
        curr_up = (currency or "EUR").upper()
        sym = "€" if curr_up == "EUR" else ("$" if curr_up == "USD" else f" {curr_up}")
        amt_fmt = f"+{numeric_amt:.2f}{sym}" if curr_up in ("EUR", "USD") else f"+{numeric_amt:.2f} {curr_up}"
        push_title = f"{amt_fmt}  •  {clean_song or 'Sheet Order'}"
        push_body = f"{email_str} • Stripe ({loc_str})" if email_str else f"Paid order via Stripe ({loc_str})"
        push_icon = resolved_cover
        push_url = "/?tab=sales"
        vibrate = [90, 45, 90, 45, 180]
    elif effective_type == "subscriber":
        push_title = f"Free Unlock  •  {clean_song}" if clean_song else f"New Subscriber  •  {email_str or 'Lead'}"
        if clean_song and email_str:
            push_body = f"{email_str} • {loc_str}"
        elif email_str:
            push_body = f"Double opt-in confirmed • {loc_str}"
        else:
            push_body = f"Free sheet unlock • {loc_str}"
        push_icon = resolved_cover if has_song_cover else "https://pulse.meloscribesheets.com/icon-192.png"
        push_url = "/?tab=sales"
        vibrate = [60, 40, 60]
    else:
        votes_n = int(meta_payload.get("votes") or 1)
        artist_str = str(meta_payload.get("artist") or "").strip()
        push_title = f"Request ({votes_n} Votes)  •  {clean_song or title}"
        push_body = f"{artist_str} • Tap to add to To-Do" if artist_str else "Tap to add to creator To-Do"
        push_icon = "https://pulse.meloscribesheets.com/icon-192.png"
        push_url = "/?tab=growth"
        vibrate = [50, 50, 120]

    notif_id = record_pulse_notification(
        notif_type=effective_type,
        title=push_title,
        subtitle=push_body,
        amount=f"{numeric_amt:.2f}" if effective_type == "sale" else "",
        song_name=song_name,
        cover_url=resolved_cover,
        meta=meta_payload
    )

    artist_suffix = f" - {meta_payload['artist']}" if meta_payload.get("artist") else ""
    song_title_full = f"{song_name}{artist_suffix}".strip()

    # Keep Android notification compact and native (Stripe-style) without redundant BigPicture banner box
    payload = {
        "title": push_title,
        "body": push_body,
        "icon": push_icon,
        "badge": badge_url,
        "vibrate": vibrate,
        "actions": actions or [],
        "data": {
            "notif_id": notif_id,
            "type": effective_type,
            "url": url or push_url,
            "amount": f"{numeric_amt:.2f}" if effective_type == "sale" else "",
            "currency": currency,
            "song_name": song_name,
            "song_title": song_title_full,
            "meta": meta_payload,
            "created_at": datetime.datetime.utcnow().isoformat()
        }
    }
    payload_str = json.dumps(payload)

    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT id, endpoint, p256dh, auth FROM push_subscriptions")
    subs = c.fetchall()
    conn.close()

    if not subs:
        print(f"[WebPush] No push subscriptions registered. Notification #{notif_id} saved to DB.")
        return

    try:
        if "-----BEGIN" in priv_key:
            vapid_signer = Vapid.from_pem(priv_key.encode("utf-8"))
        else:
            vapid_signer = Vapid.from_string(priv_key)
    except Exception as key_err:
        print(f"[WebPush] Invalid VAPID private key format: {key_err}")
        return

    dead_subscription_ids = []
    sent_count = 0

    for sub in subs:
        sub_id = sub["id"]
        sub_info = {
            "endpoint": sub["endpoint"],
            "keys": {
                "p256dh": sub["p256dh"],
                "auth": sub["auth"]
            }
        }
        try:
            webpush(
                subscription_info=sub_info,
                data=payload_str,
                vapid_private_key=vapid_signer,
                vapid_claims={"sub": claims_email},
                ttl=86400
            )
            sent_count += 1
        except WebPushException as ex:
            status_code = getattr(ex.response, "status_code", None)
            if status_code in (404, 410):
                dead_subscription_ids.append(sub_id)
                print(f"[WebPush] Pruning expired subscription #{sub_id} (HTTP {status_code})")
            else:
                print(f"[WebPush] Delivery failed for subscription #{sub_id}: {ex}")
        except Exception as gen_err:
            print(f"[WebPush] Unexpected delivery error for subscription #{sub_id}: {gen_err}")

    if dead_subscription_ids:
        conn = get_db_connection()
        c = conn.cursor()
        c.executemany("DELETE FROM push_subscriptions WHERE id = ?", [(sid,) for sid in dead_subscription_ids])
        conn.commit()
        conn.close()

    print(f"[WebPush] Notification #{notif_id} dispatched to {sent_count}/{len(subs)} devices.")

# -------------------------------------------------------------------
# FastAPI Route Handlers
# -------------------------------------------------------------------
class SubscribeRequest(BaseModel):
    endpoint: str
    keys: Dict[str, str]
    user_agent: Optional[str] = ""

class UnsubscribeRequest(BaseModel):
    endpoint: Optional[str] = None
    device_id: Optional[int] = None
    all: bool = False

class TestPushRequest(BaseModel):
    type: Optional[str] = "sale"

class SuggestionManageRequest(BaseModel):
    action: str  # add_todo, complete, delete
    sug_id: Optional[str] = None
    song_name: Optional[str] = None
    title: Optional[str] = None
    artist: Optional[str] = None

class QueueRescheduleRequest(BaseModel):
    schedule_time: str

class TodoManageRequest(BaseModel):
    action: str  # add, complete, delete, reopen
    todo_id: Optional[int] = None
    title: Optional[str] = None
    song_name: Optional[str] = None
    priority: Optional[str] = "normal"

@router.get("/api/push/vapid-public-key")
def get_vapid_public_key():
    """Return VAPID Public Key for PWA PushManager subscription (Public endpoint)."""
    pub_key, _, _ = get_or_create_vapid_keys()
    return {"publicKey": pub_key}

@router.get("/api/push/subscriptions/count")
def get_subscription_count(request: Request):
    """Return count of active registered push devices."""
    verify_pulse_admin(request)
    init_push_db()
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT COUNT(*) as cnt FROM push_subscriptions")
    cnt = c.fetchone()["cnt"] or 0
    conn.close()
    return {"count": cnt}

def _summarize_user_agent(ua: str) -> Dict[str, str]:
    ua_l = (ua or "").lower()
    if "brave" in ua_l:
        browser = "Brave"
    elif "edg" in ua_l:
        browser = "Edge"
    elif "firefox" in ua_l:
        browser = "Firefox"
    elif "safari" in ua_l and "chrome" not in ua_l and "crios" not in ua_l:
        browser = "Safari"
    elif "chrome" in ua_l or "crios" in ua_l:
        browser = "Chrome"
    else:
        browser = "Browser"

    if "android" in ua_l:
        platform = "Android"
    elif "iphone" in ua_l or "ipad" in ua_l:
        platform = "iOS"
    elif "windows" in ua_l:
        platform = "Windows"
    elif "macintosh" in ua_l or "mac os" in ua_l:
        platform = "macOS"
    elif "linux" in ua_l:
        platform = "Linux"
    else:
        platform = "Device"

    return {"browser": browser, "platform": platform, "device_label": f"{browser} • {platform}"}

@router.get("/api/push/subscriptions")
def list_push_subscriptions(request: Request):
    """List registered Web Push devices without exposing cryptographic keys."""
    verify_pulse_admin(request)
    init_push_db()
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT id, endpoint, user_agent, created_at FROM push_subscriptions ORDER BY id DESC")
    rows = c.fetchall()
    conn.close()
    devices = []
    for row in rows:
        meta = _summarize_user_agent(row["user_agent"] or "")
        ep = row["endpoint"] or ""
        devices.append({
            "id": row["id"],
            "endpoint": ep,
            "endpoint_tail": ep[-18:] if len(ep) > 18 else ep,
            "browser": meta["browser"],
            "platform": meta["platform"],
            "device_label": meta["device_label"],
            "created_at": row["created_at"],
        })
    return {"devices": devices, "count": len(devices)}

@router.post("/api/push/subscribe")
def subscribe_push(req: SubscribeRequest, request: Request):
    """Upsert browser Web Push subscription into SQLite."""
    verify_pulse_admin(request)
    init_push_db()
    p256dh = req.keys.get("p256dh", "")
    auth = req.keys.get("auth", "")
    if not req.endpoint or not p256dh or not auth:
        raise HTTPException(status_code=400, detail="Invalid push subscription credentials")

    conn = get_db_connection()
    c = conn.cursor()
    c.execute("""
        INSERT INTO push_subscriptions (endpoint, p256dh, auth, user_agent)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(endpoint) DO UPDATE SET
            p256dh = excluded.p256dh,
            auth = excluded.auth,
            user_agent = excluded.user_agent
    """, (req.endpoint, p256dh, auth, req.user_agent or request.headers.get("user-agent", "")))
    conn.commit()
    conn.close()
    return {"status": "subscribed", "endpoint": req.endpoint}

@router.post("/api/push/unsubscribe")
def unsubscribe_push(req: UnsubscribeRequest, request: Request):
    """Remove a push subscription by endpoint, device_id, or clear all subscriptions."""
    verify_pulse_admin(request)
    init_push_db()
    conn = get_db_connection()
    c = conn.cursor()
    removed = 0
    if req.all:
        c.execute("DELETE FROM push_subscriptions")
        removed = c.rowcount
    elif req.device_id is not None:
        c.execute("DELETE FROM push_subscriptions WHERE id = ?", (req.device_id,))
        removed = c.rowcount
    elif req.endpoint:
        c.execute("DELETE FROM push_subscriptions WHERE endpoint = ?", (req.endpoint,))
        removed = c.rowcount
    else:
        conn.close()
        raise HTTPException(status_code=400, detail="Provide endpoint, device_id, or all=true")
    conn.commit()
    c.execute("SELECT COUNT(*) as cnt FROM push_subscriptions")
    cnt = c.fetchone()["cnt"] or 0
    conn.close()
    return {"status": "unsubscribed", "removed": removed, "count": cnt}

@router.post("/api/push/test")
def trigger_test_push(req: TestPushRequest, request: Request, background_tasks: BackgroundTasks):
    """Trigger an immediate live test notification (uses latest real record when available)."""
    verify_pulse_admin(request)
    init_push_db()
    notif_type = req.type or "sale"

    conn = get_db_connection()
    c = conn.cursor()

    if notif_type == "sale":
        c.execute("""
            SELECT song_name, amount, currency, email, locale, buyer_name
            FROM purchases
            WHERE transaction_id NOT LIKE 'cs_test_%' AND email != '2beers.sm@gmail.com' AND status = 'active' AND amount > 0
            ORDER BY created_at DESC LIMIT 1
        """)
        row = c.fetchone()
        song = (row["song_name"] if row else "Experience") or "Experience"
        amt = float(row["amount"]) if row and row["amount"] else 6.00
        curr = (row["currency"] if row else "EUR") or "EUR"
        email = (row["email"] if row else "customer@example.com") or "customer@example.com"
        locale = (row["locale"] if row else "de") or "de"
        buyer_name = (row["buyer_name"] if row else "Piano Lover") or "Piano Lover"

        sym = "€" if curr.upper() == "EUR" else ("$" if curr.upper() == "USD" else f" {curr.upper()}")
        amt_label = f"+{amt:.2f}{sym}" if curr.upper() in ("EUR", "USD") else f"+{amt:.2f} {curr.upper()}"
        title = f"{amt_label}  •  {song}"
        body = f"{email} • Stripe ({locale.upper()})"
        amount_str = f"{amt:.2f}"
        meta = {"email": email, "locale": locale, "buyer_name": buyer_name, "currency": curr, "payment_method": "Stripe"}
        actions = []
        preview_extra = {"email": email, "locale": locale, "votes": 0}
    elif notif_type == "subscriber":
        c.execute("SELECT email, locale, song_name FROM notify_subscribers WHERE status = 'active' ORDER BY COALESCE(confirmed_at, created_at) DESC LIMIT 1")
        row = c.fetchone()
        email = (row["email"] if row else "fan@music.com") or "fan@music.com"
        locale = (row["locale"] if row and "locale" in row.keys() else "en") or "en"
        song = (row["song_name"] if row and "song_name" in row.keys() and row["song_name"] else "River Flows in You")

        title = f"Free Unlock  •  {song}"
        body = f"{email} • {locale.upper()}"
        amount_str = ""
        curr = "EUR"
        meta = {"email": email, "locale": locale}
        actions = []
        preview_extra = {"email": email, "locale": locale, "votes": 0}
    else:
        c.execute("SELECT id, title, artist, votes FROM suggestions WHERE status != 'completed' OR status IS NULL ORDER BY votes DESC LIMIT 1")
        row = c.fetchone()
        sug_id = (row["id"] if row else None)
        song = (row["title"] if row else "Interstellar (Main Theme)") or "Interstellar (Main Theme)"
        artist = (row["artist"] if row else "Hans Zimmer") or "Hans Zimmer"
        votes = (row["votes"] if row else 12) or 12

        title = f"Request ({votes} Votes)  •  {song}"
        body = f"{artist} • Tap to add to To-Do"
        amount_str = ""
        curr = "EUR"
        meta = {"artist": artist, "votes": votes, "title": song}
        actions = [{"action": "add_todo", "title": "+ Add to To-Do"}]
        preview_extra = {"email": "", "locale": "en", "votes": votes, "artist": artist, "sug_id": sug_id}

    conn.close()

    background_tasks.add_task(
        send_web_push,
        notif_type=notif_type,
        title=title,
        body=body,
        song_name=song,
        amount=amount_str,
        currency=curr,
        url="/?tab=pulse",
        actions=actions,
        meta=meta
    )
    return {
        "status": "success",
        "message": f"Test push '{notif_type}' dispatched.",
        "preview": {
            "type": notif_type,
            "title": title,
            "body": body,
            "song_name": song,
            "amount": amount_str,
            "currency": curr,
            **preview_extra
        }
    }

@router.post("/api/push/mark-read")
def mark_notifications_read(request: Request):
    """Mark all pulse_notifications as read."""
    verify_pulse_admin(request)
    init_push_db()
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("UPDATE pulse_notifications SET is_read = 1 WHERE is_read = 0")
    conn.commit()
    conn.close()
    return {"status": "success"}

@router.get("/api/push/badge.png")
def get_push_badge():
    """Serve pure white 96x96 status bar alpha mask (Public endpoint)."""
    png_bytes = render_monochrome_badge()
    return Response(content=png_bytes, media_type="image/png", headers={"Cache-Control": "public, max-age=86400"})

@router.get("/api/push/card/{notif_id}.png")
def get_notification_card_image(notif_id: int):
    """Serve dynamic 1200x600 px dark glassmorphic banner for Android SystemUI notification (Public endpoint)."""
    init_push_db()
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT * FROM pulse_notifications WHERE id = ?", (notif_id,))
    row = c.fetchone()
    conn.close()

    if not row:
        png_bytes = render_glass_card(notif_id, "sale", "meloscribe pulse", "Real-time Creator Analytics", amount="6.00")
        return Response(content=png_bytes, media_type="image/png")

    meta = {}
    if row["meta_json"]:
        try:
            meta = json.loads(row["meta_json"])
        except Exception:
            pass

    png_bytes = render_glass_card(
        notif_id=row["id"],
        notif_type=row["type"],
        title=row["title"],
        subtitle=row["subtitle"] or "",
        amount=row["amount"] or "",
        song_name=row["song_name"] or "",
        meta=meta
    )
    return Response(content=png_bytes, media_type="image/png", headers={"Cache-Control": "public, max-age=3600"})

# -------------------------------------------------------------------
# Actionable Suggestions & Queue Management for PWA
# -------------------------------------------------------------------
@router.post("/api/admin/suggestions/manage")
def manage_suggestion_action(req: SuggestionManageRequest, request: Request):
    """Manage community suggestions (add_todo, complete, delete) from PWA and ServiceWorker action."""
    verify_pulse_admin(request)
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT name FROM sqlite_master WHERE type='table'")
    _ = c.fetchall()
    conn.commit()

    c.execute("""
        CREATE TABLE IF NOT EXISTS todos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            song_name TEXT,
            added_date TEXT,
            status TEXT DEFAULT 'pending'
        )
    """)
    conn.commit()

    action = (req.action or "").lower().strip()
    if action == "add_todo":
        song_label = req.song_name
        if not song_label:
            if req.title and req.artist:
                song_label = f"[Wishlist] {req.title} - {req.artist}"
            elif req.title:
                song_label = f"[Wishlist] {req.title}"
            elif req.sug_id:
                row = c.execute("SELECT title, artist, votes FROM suggestions WHERE id = ?", (req.sug_id,)).fetchone()
                if row:
                    song_label = f"[Wishlist • {row['votes'] or 1} Votes] {row['title']} - {row['artist']}"
        if not song_label:
            conn.close()
            raise HTTPException(status_code=400, detail="Song title or suggestion ID required.")

        # Avoid duplicates in pending todos
        existing = c.execute("SELECT id FROM todos WHERE status = 'pending' AND LOWER(song_name) = LOWER(?)", (song_label,)).fetchone()
        if not existing:
            c.execute("INSERT INTO todos (song_name, added_date, status) VALUES (?, ?, 'pending')",
                      (song_label, datetime.datetime.utcnow().isoformat()))
            conn.commit()
        conn.close()
        return {"status": "success", "action": "add_todo", "song_name": song_label}

    elif action == "complete":
        if req.sug_id:
            row = c.execute("SELECT title FROM suggestions WHERE id = ?", (req.sug_id,)).fetchone()
            c.execute("UPDATE suggestions SET status = 'completed' WHERE id = ?", (req.sug_id,))
            if row and row["title"]:
                c.execute("UPDATE todos SET status = 'completed' WHERE LOWER(song_name) LIKE ?", (f"%{row['title'].lower()}%",))
        elif req.title:
            c.execute("UPDATE suggestions SET status = 'completed' WHERE LOWER(title) = LOWER(?)", (req.title,))
            c.execute("UPDATE todos SET status = 'completed' WHERE LOWER(song_name) LIKE ?", (f"%{req.title.lower()}%",))
        conn.commit()
        conn.close()
        return {"status": "success", "action": "complete"}

    elif action == "delete":
        if not req.sug_id:
            conn.close()
            raise HTTPException(status_code=400, detail="Suggestion ID required for deletion.")
        c.execute("DELETE FROM suggestions WHERE id = ?", (req.sug_id,))
        conn.commit()
        conn.close()
        return {"status": "success", "action": "delete"}

    conn.close()
    raise HTTPException(status_code=400, detail=f"Unsupported action: {req.action}")

def _extract_clean_todo_song(raw_name: str) -> str:
    cleaned = re.sub(r"^\[(PRIORITY|FORMAT-SHIFT|RE-PURPOSE|Wishlist[^\]]*)\]\s*", "", raw_name or "", flags=re.IGNORECASE).strip()
    return cleaned.split(" - ")[0].strip()

def _ensure_todos_table(conn: sqlite3.Connection, c: sqlite3.Cursor) -> None:
    c.execute("SELECT name FROM sqlite_master WHERE type='table'")
    _ = c.fetchall()
    conn.commit()
    c.execute("""
        CREATE TABLE IF NOT EXISTS todos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            song_name TEXT,
            added_date TEXT,
            status TEXT DEFAULT 'pending'
        )
    """)
    conn.commit()

@router.post("/api/admin/pulse/todos/manage")
def manage_pulse_todos(req: TodoManageRequest, request: Request):
    """Add, complete, reopen, or delete creator To-Do items and sync wishlist completion."""
    verify_pulse_admin(request)
    conn = get_db_connection()
    c = conn.cursor()
    _ensure_todos_table(conn, c)

    action = (req.action or "").lower().strip()
    if action == "add":
        raw_title = (req.title or req.song_name or "").strip()
        if not raw_title:
            conn.close()
            raise HTTPException(status_code=400, detail="To-Do title is required.")
        is_high = (req.priority or "normal").lower().strip() == "high"
        stored_name = f"[PRIORITY] {raw_title}" if is_high and not raw_title.startswith("[PRIORITY]") else raw_title
        existing = c.execute(
            "SELECT id FROM todos WHERE status = 'pending' AND LOWER(song_name) = LOWER(?)",
            (stored_name,)
        ).fetchone()
        if not existing:
            c.execute(
                "INSERT INTO todos (song_name, added_date, status) VALUES (?, ?, 'pending')",
                (stored_name, datetime.datetime.utcnow().isoformat())
            )
            conn.commit()
            todo_id = c.lastrowid
        else:
            todo_id = existing["id"]
        conn.close()
        return {"status": "success", "action": "add", "todo_id": todo_id, "song_name": stored_name}

    elif action == "complete":
        if not req.todo_id:
            conn.close()
            raise HTTPException(status_code=400, detail="todo_id required.")
        row = c.execute("SELECT song_name FROM todos WHERE id = ?", (req.todo_id,)).fetchone()
        c.execute("UPDATE todos SET status = 'completed' WHERE id = ?", (req.todo_id,))
        if row and row["song_name"] and "[Wishlist" in row["song_name"]:
            clean_title = re.sub(r"^\[(PRIORITY|FORMAT-SHIFT|RE-PURPOSE|Wishlist[^\]]*)\]\s*", "", row["song_name"], flags=re.IGNORECASE).strip()
            song_base = clean_title.split(" - ")[0].strip()
            c.execute(
                "UPDATE suggestions SET status = 'completed' WHERE LOWER(title) = LOWER(?) OR LOWER(title) = LOWER(?)",
                (clean_title, song_base)
            )
        conn.commit()
        conn.close()
        return {"status": "success", "action": "complete", "todo_id": req.todo_id}

    elif action == "reopen":
        if not req.todo_id:
            conn.close()
            raise HTTPException(status_code=400, detail="todo_id required.")
        c.execute("UPDATE todos SET status = 'pending' WHERE id = ?", (req.todo_id,))
        conn.commit()
        conn.close()
        return {"status": "success", "action": "reopen", "todo_id": req.todo_id}

    elif action == "delete":
        if not req.todo_id:
            conn.close()
            raise HTTPException(status_code=400, detail="todo_id required.")
        c.execute("DELETE FROM todos WHERE id = ?", (req.todo_id,))
        conn.commit()
        conn.close()
        return {"status": "success", "action": "delete", "todo_id": req.todo_id}

    conn.close()
    raise HTTPException(status_code=400, detail=f"Unsupported todo action: {req.action}")

def _get_queue_db_path() -> Optional[Path]:
    for p in [Path("/home/ubuntu/meloscribe/queue.db"), CURRENT_DIR.parent.parent.parent / "queue.db"]:
        if p.exists():
            return p
    return None

@router.get("/api/admin/queue")
def get_admin_queue(request: Request):
    """Fetch Oracle VM upload queue items across pending, failed, disabled, and completed states."""
    verify_pulse_admin(request)
    q_path = _get_queue_db_path()
    if q_path:
        q_conn = sqlite3.connect(str(q_path), timeout=15.0)
        q_conn.execute("PRAGMA journal_mode=WAL;")
        q_conn.row_factory = sqlite3.Row
        qc = q_conn.cursor()
        qc.execute("""
            SELECT id, song, mode, profile, schedule_time, status, error, attempts
            FROM upload_queue
            ORDER BY
              CASE
                WHEN status = 'pending' THEN 0
                WHEN status = 'failed' THEN 1
                WHEN status = 'disabled' THEN 2
                ELSE 3
              END,
              CASE WHEN status = 'completed' THEN datetime(schedule_time) END DESC,
              datetime(schedule_time) ASC
            LIMIT 100
        """)
        items = [dict(r) for r in qc.fetchall()]
        q_conn.close()
        return {"queue": items}

    if platform.system() == "Windows":
        key_path = r"C:\Dev\meloscribe-app\ssh-key-2026-05-07.key"
        if not os.path.exists(key_path):
            key_path = r"C:\Dev\ssh-key-2026-05-07.key"
        if os.path.exists(key_path):
            py_query = "import sqlite3, json; conn=sqlite3.connect('/home/ubuntu/meloscribe/queue.db'); conn.row_factory=sqlite3.Row; cursor=conn.cursor(); cursor.execute('SELECT id, song, mode, profile, schedule_time, status, error, attempts FROM upload_queue ORDER BY CASE WHEN status = \"pending\" THEN 0 WHEN status = \"failed\" THEN 1 WHEN status = \"disabled\" THEN 2 ELSE 3 END, datetime(schedule_time) ASC LIMIT 100'); print(json.dumps([dict(r) for r in cursor.fetchall()]))"
            cmd = ["ssh", "-i", key_path, "-o", "StrictHostKeyChecking=accept-new", "-o", "ConnectTimeout=5", "-o", "IdentitiesOnly=yes", "ubuntu@152.70.23.171", f"python3 -c \"{py_query}\""]
            try:
                res = subprocess.run(cmd, capture_output=True, text=True, timeout=10, creationflags=CREATION_FLAGS)
                if res.returncode == 0 and res.stdout.strip():
                    return {"queue": json.loads(res.stdout.strip())}
            except Exception as e:
                print(f"[Pulse Queue] SSH fallback error: {e}")

    return {"queue": []}

@router.post("/api/admin/queue/retry/{task_id}")
def retry_admin_queue_task(task_id: int, request: Request):
    """Reset a failed or cancelled upload task back to 'pending' so oci-uploader picks it up."""
    verify_pulse_admin(request)
    q_path = _get_queue_db_path()
    if q_path:
        q_conn = sqlite3.connect(str(q_path), timeout=15.0)
        q_conn.execute("PRAGMA journal_mode=WAL;")
        qc = q_conn.cursor()
        qc.execute("""
            UPDATE upload_queue
            SET status = 'pending', error = NULL, attempts = 0
            WHERE id = ? AND status IN ('failed', 'cancelled', 'disabled')
        """, (task_id,))
        q_conn.commit()
        qc.execute("SELECT status FROM upload_queue WHERE id = ?", (task_id,))
        row = qc.fetchone()
        q_conn.close()
        return {"status": "success", "new_status": row[0] if row else "unknown", "task_id": task_id}
    raise HTTPException(status_code=404, detail="Queue database not found on this host.")

@router.post("/api/admin/queue/{task_id}/toggle-status")
def toggle_admin_queue_status(task_id: int, request: Request):
    """Toggle queue task status between 'pending' and 'disabled'."""
    verify_pulse_admin(request)
    q_path = _get_queue_db_path()
    if q_path:
        q_conn = sqlite3.connect(str(q_path), timeout=15.0)
        q_conn.execute("PRAGMA journal_mode=WAL;")
        qc = q_conn.cursor()
        qc.execute("""
            UPDATE upload_queue
            SET status = CASE WHEN status = 'disabled' THEN 'pending' ELSE 'disabled' END
            WHERE id = ? AND status IN ('pending', 'failed', 'disabled')
        """, (task_id,))
        q_conn.commit()
        qc.execute("SELECT status FROM upload_queue WHERE id = ?", (task_id,))
        row = qc.fetchone()
        q_conn.close()
        return {"status": "success", "new_status": row[0] if row else "unknown"}
    raise HTTPException(status_code=404, detail="Queue database not found on this host.")

@router.post("/api/admin/queue/{task_id}/reschedule")
def reschedule_admin_queue_task(task_id: int, req: QueueRescheduleRequest, request: Request):
    """Reschedule a pending queue task ('YYYY-MM-DD HH:MM')."""
    verify_pulse_admin(request)
    try:
        datetime.datetime.strptime(req.schedule_time, "%Y-%m-%d %H:%M")
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid schedule_time format. Must be 'YYYY-MM-DD HH:MM'.")

    q_path = _get_queue_db_path()
    if q_path:
        q_conn = sqlite3.connect(str(q_path), timeout=15.0)
        q_conn.execute("PRAGMA journal_mode=WAL;")
        qc = q_conn.cursor()
        qc.execute("UPDATE upload_queue SET schedule_time = ? WHERE id = ?", (req.schedule_time, task_id))
        q_conn.commit()
        q_conn.close()
        return {"status": "success", "schedule_time": req.schedule_time}
    raise HTTPException(status_code=404, detail="Queue database not found on this host.")

# -------------------------------------------------------------------
# Consolidated Pulse Analytics Summary Endpoint
# -------------------------------------------------------------------
@router.get("/api/admin/pulse-summary")
def get_pulse_summary(request: Request):
    """
    Consolidated ultra-fast summary payload for meloscribe-pulse.
    Filters out sandbox/test transactions and returns KPIs, timeline, activity feed, and top songs.
    """
    verify_pulse_admin(request)
    init_push_db()
    conn = get_db_connection()
    c = conn.cursor()

    # 1. KPIs
    sales_filter = "transaction_id NOT LIKE 'cs_test_%' AND email != '2beers.sm@gmail.com' AND status = 'active'"

    c.execute(f"SELECT SUM(amount) as rev_all, COUNT(id) as count_all FROM purchases WHERE {sales_filter}")
    r_all = c.fetchone()
    revenue_all_time = round(r_all["rev_all"] or 0.0, 2)
    sales_count_all_time = r_all["count_all"] or 0

    c.execute(f"SELECT SUM(amount) as rev_30d, COUNT(id) as count_30d FROM purchases WHERE {sales_filter} AND created_at >= date('now', '-30 days')")
    r_30d = c.fetchone()
    revenue_30d = round(r_30d["rev_30d"] or 0.0, 2)
    sales_count_30d = r_30d["count_30d"] or 0

    c.execute(f"SELECT SUM(amount) as rev_7d FROM purchases WHERE {sales_filter} AND created_at >= date('now', '-7 days')")
    r_7d = c.fetchone()
    revenue_7d = round(r_7d["rev_7d"] or 0.0, 2)

    c.execute("SELECT COUNT(*) as sub_cnt FROM notify_subscribers WHERE status = 'active'")
    subscribers_active_count = c.fetchone()["sub_cnt"] or 0

    c.execute("SELECT COUNT(*) as sug_cnt FROM suggestions WHERE status != 'completed' OR status IS NULL")
    open_suggestions_count = c.fetchone()["sug_cnt"] or 0

    # Platform video breakdown
    c.execute("SELECT platform, SUM(views) as views, SUM(likes) as likes, SUM(comments) as comments, SUM(shares) as shares, SUM(saves) as saves FROM videos GROUP BY platform")
    plat_rows = c.fetchall()
    platform_breakdown = {}
    total_views_all_platforms = 0
    for pr in plat_rows:
        p_name = pr["platform"].lower()
        v = pr["views"] or 0
        total_views_all_platforms += v
        platform_breakdown[p_name] = {
            "views": v,
            "likes": pr["likes"] or 0,
            "comments": pr["comments"] or 0,
            "shares": pr["shares"] or 0,
            "saves": pr["saves"] or 0,
        }

    for extra_plat in ["threads", "pinterest"]:
        if extra_plat not in platform_breakdown:
            c.execute("SELECT views FROM snapshots WHERE platform = ? ORDER BY snapshot_date DESC LIMIT 1", (extra_plat,))
            snap_row = c.fetchone()
            ext_views = snap_row["views"] if snap_row and snap_row["views"] else 0
            total_views_all_platforms += ext_views
            platform_breakdown[extra_plat] = {
                "views": ext_views,
                "likes": 0,
                "comments": 0,
                "shares": 0,
                "saves": 0
            }

    # Checkout conversion rate & social attribution (never block request; refresh in background)
    warm_pulse_caches_async()
    checkout_conv_rate = 0.0
    social_traffic_attribution = _CHECKOUT_CACHE.get("traffic_overall") or {}
    ca_data = _CHECKOUT_CACHE.get("data")

    if ca_data and ca_data.get("summary"):
        checkout_conv_rate = float(ca_data["summary"].get("conversion_rate", 0.0))
    else:
        c.execute("SELECT SUM(profile_views) as web_views FROM channel_insights WHERE platform = 'website' AND date >= date('now', '-30 days')")
        web_row = c.fetchone()
        web_views = web_row["web_views"] or 0
        if web_views > 0:
            checkout_conv_rate = round((sales_count_30d / web_views) * 100.0, 2)

    # 2. Revenue & Views Timeline (Contiguous 30 days)
    timeline_dict = {}
    today = datetime.date.today()
    for i in range(29, -1, -1):
        d_str = (today - datetime.timedelta(days=i)).isoformat()
        timeline_dict[d_str] = {"date": d_str, "revenue": 0.0, "sales": 0, "views": 0}

    c.execute(f"""
        SELECT date(created_at) as day, SUM(amount) as daily_rev, COUNT(*) as daily_sales
        FROM purchases
        WHERE {sales_filter} AND created_at >= date('now', '-30 days')
        GROUP BY day
    """)
    for r in c.fetchall():
        d_str = r["day"]
        if d_str in timeline_dict:
            timeline_dict[d_str]["revenue"] = round(r["daily_rev"] or 0.0, 2)
            timeline_dict[d_str]["sales"] = r["daily_sales"] or 0

    c.execute("""
        SELECT snapshot_date as day, SUM(views) as daily_views
        FROM snapshots
        WHERE snapshot_date >= date('now', '-30 days')
        GROUP BY day
    """)
    for r in c.fetchall():
        d_str = r["day"]
        if d_str in timeline_dict:
            timeline_dict[d_str]["views"] = r["daily_views"] or 0

    revenue_timeline = list(timeline_dict.values())

    # 3. Activity Feed (Unified chronological stream, latest 60 items)
    activity_items = []

    c.execute(f"""
        SELECT id, transaction_id, song_name, amount, currency, email, created_at, buyer_name, locale, rating
        FROM purchases
        WHERE {sales_filter}
        ORDER BY created_at DESC LIMIT 40
    """)
    for p in c.fetchall():
        song = p["song_name"] or "Unknown Song"
        amt_val = float(p["amount"] or 0.0)
        curr_val = (p["currency"] or "EUR").upper()
        loc_val = (p["locale"] or "en").lower()
        if amt_val > 0.0:
            activity_items.append({
                "id": f"sale_{p['id']}",
                "type": "sale",
                "title": f"+{amt_val:.2f} {curr_val}",
                "subtitle": song,
                "song_name": song,
                "amount": f"{amt_val:.2f}",
                "currency": curr_val,
                "email": p["email"],
                "buyer_name": p["buyer_name"] or "",
                "locale": loc_val,
                "cover_url": resolve_cover_url(song),
                "timestamp": p["created_at"],
                "is_read": 1
            })
        else:
            activity_items.append({
                "id": f"free_{p['id']}",
                "type": "subscriber",
                "title": p["email"] or "Free Unlock",
                "subtitle": f"Unlocked free sheet: {song}",
                "song_name": song,
                "amount": "",
                "currency": curr_val,
                "email": p["email"] or "",
                "buyer_name": p["buyer_name"] or "",
                "locale": loc_val,
                "cover_url": resolve_cover_url(song),
                "timestamp": p["created_at"],
                "is_read": 1
            })

    # Customer Checkout Ratings (from purchases.rating + optional checkout_ratings table)
    checkout_ratings: List[Dict[str, Any]] = []
    c.execute("SELECT name FROM sqlite_master WHERE type='table'")
    existing_tables = {r["name"] for r in c.fetchall()}
    conn.commit()

    if "checkout_ratings" in existing_tables:
        try:
            c.execute("SELECT id, song_name, rating, comment, email, locale, created_at FROM checkout_ratings ORDER BY created_at DESC LIMIT 30")
            for cr in c.fetchall():
                checkout_ratings.append({
                    "id": f"cr_{cr['id']}",
                    "song_name": cr["song_name"] or "Order",
                    "rating": str(cr["rating"] or "up"),
                    "comment": cr["comment"] or "",
                    "email": cr["email"] or "",
                    "locale": (cr["locale"] or "en").lower(),
                    "timestamp": cr["created_at"]
                })
        except Exception as cr_err:
            print(f"[Pulse Summary] checkout_ratings read error: {cr_err}")

    c.execute(f"""
        SELECT id, song_name, rating, email, locale, created_at
        FROM purchases
        WHERE {sales_filter} AND rating IN ('up', 'down')
        ORDER BY created_at DESC LIMIT 30
    """)
    for rp in c.fetchall():
        rating_entry = {
            "id": f"pr_{rp['id']}",
            "song_name": rp["song_name"] or "Order",
            "rating": str(rp["rating"]),
            "comment": "",
            "email": rp["email"] or "",
            "locale": (rp["locale"] or "en").lower(),
            "timestamp": rp["created_at"]
        }
        checkout_ratings.append(rating_entry)
        activity_items.append({
            "id": f"rating_{rp['id']}",
            "type": "rating",
            "title": f"Rated {'Positive' if rp['rating'] == 'up' else 'Negative'}: {rp['song_name'] or 'Order'}",
            "subtitle": rp["email"] or "",
            "song_name": rp["song_name"] or "",
            "rating": str(rp["rating"]),
            "amount": "",
            "currency": "",
            "email": rp["email"] or "",
            "buyer_name": "",
            "locale": (rp["locale"] or "en").lower(),
            "cover_url": resolve_cover_url(rp["song_name"] or ""),
            "timestamp": rp["created_at"],
            "is_read": 1
        })

    c.execute("""
        SELECT id, email, song_name, locale, status, created_at, confirmed_at
        FROM notify_subscribers
        ORDER BY id DESC LIMIT 50
    """)
    sub_rows = c.fetchall()
    subscribers_list: List[Dict[str, Any]] = []
    for s in sub_rows:
        s_song = (s["song_name"] if "song_name" in s.keys() else "") or ""
        s_locale = (s["locale"] if "locale" in s.keys() else "en") or "en"
        subscribers_list.append({
            "id": s["id"],
            "email": s["email"],
            "song_name": s_song,
            "locale": s_locale.lower(),
            "status": str(s["status"] or "pending"),
            "created_at": s["created_at"],
            "confirmed_at": s["confirmed_at"]
        })
        if s["status"] == "active" and len(activity_items) < 120:
            activity_items.append({
                "id": f"sub_{s['id']}",
                "type": "subscriber",
                "title": s["email"],
                "subtitle": f"Unlocked free sheet: {s_song}" if s_song else "Double Opt-In Confirmed",
                "song_name": s_song,
                "amount": "",
                "currency": "",
                "email": s["email"],
                "buyer_name": "",
                "locale": s_locale.lower(),
                "cover_url": resolve_cover_url(s_song) if s_song else "https://api.meloscribe.dev/api/push/badge.png",
                "timestamp": s["confirmed_at"] or s["created_at"],
                "is_read": 1
            })

    # Creator To-Dos List
    _ensure_todos_table(conn, c)
    c.execute("""
        SELECT id, song_name, status, added_date
        FROM todos
        ORDER BY CASE WHEN status = 'pending' THEN 0 ELSE 1 END, id DESC
        LIMIT 100
    """)
    todos_list: List[Dict[str, Any]] = []
    for td in c.fetchall():
        raw_td = td["song_name"] or ""
        is_prio = "[PRIORITY]" in raw_td
        clean_td = re.sub(r"^\[PRIORITY\]\s*", "", raw_td).strip()
        todos_list.append({
            "id": td["id"],
            "title": clean_td,
            "song_name": raw_td,
            "priority": "high" if is_prio else "normal",
            "status": str(td["status"] or "pending"),
            "created_at": td["added_date"] or ""
        })

    c.execute("""
        SELECT id, title, artist, votes, created_at, status
        FROM suggestions
        ORDER BY created_at DESC LIMIT 30
    """)
    for sug in c.fetchall():
        activity_items.append({
            "id": f"sug_{sug['id']}",
            "sug_id": sug["id"],
            "type": "suggestion",
            "title": f"Request ({sug['votes']} Votes): {sug['title']}",
            "subtitle": f"{sug['title']} — {sug['artist']}",
            "song_name": sug["title"],
            "artist": sug["artist"],
            "votes": sug["votes"],
            "status": sug["status"] or "open",
            "amount": "",
            "currency": "",
            "email": "",
            "buyer_name": "",
            "locale": "en",
            "cover_url": "https://api.meloscribe.dev/api/push/badge.png",
            "timestamp": sug["created_at"],
            "is_read": 1
        })

    c.execute("SELECT * FROM pulse_notifications ORDER BY created_at DESC LIMIT 30")
    for pn in c.fetchall():
        meta = {}
        if pn["meta_json"]:
            try:
                meta = json.loads(pn["meta_json"])
            except Exception:
                pass
        pn_amt = pn["amount"] or ""
        pn_type = pn["type"]
        if pn_type == "sale":
            try:
                if float(pn_amt or 0) <= 0:
                    pn_type = "subscriber"
            except Exception:
                pass
        activity_items.append({
            "id": f"notif_{pn['id']}",
            "notif_id": pn["id"],
            "type": pn_type,
            "title": pn["title"],
            "subtitle": pn["subtitle"] or "",
            "song_name": pn["song_name"] or "",
            "artist": meta.get("artist", ""),
            "votes": meta.get("votes", 0),
            "amount": pn_amt,
            "currency": meta.get("currency", "EUR"),
            "email": meta.get("email", ""),
            "buyer_name": meta.get("buyer_name", ""),
            "locale": meta.get("locale", "en"),
            "cover_url": pn["cover_url"] or "https://api.meloscribe.dev/api/push/badge.png",
            "timestamp": pn["created_at"],
            "is_read": pn["is_read"]
        })

    activity_items.sort(key=lambda x: str(x.get("timestamp") or ""), reverse=True)
    activity_feed = activity_items[:60]

    # 4. Top Songs
    c.execute(f"""
        SELECT song_name, COUNT(*) as p_30d, SUM(amount) as rev_30d
        FROM purchases
        WHERE {sales_filter} AND created_at >= date('now', '-30 days')
        GROUP BY song_name
    """)
    p_30_dict = {r["song_name"]: (r["p_30d"] or 0) for r in c.fetchall()}

    c.execute(f"""
        SELECT song_name, COUNT(*) as p_all, SUM(amount) as rev_all
        FROM purchases
        WHERE {sales_filter}
        GROUP BY song_name
    """)
    p_all_dict = {r["song_name"]: (r["p_all"] or 0) for r in c.fetchall()}

    c.execute("SELECT song_name, SUM(views) as v_all FROM videos GROUP BY song_name")
    v_all_dict = {r["song_name"]: (r["v_all"] or 0) for r in c.fetchall()}

    all_song_names = set(p_all_dict.keys()) | set(v_all_dict.keys())
    top_songs = []
    for s_name in all_song_names:
        if not s_name:
            continue
        p30 = p_30_dict.get(s_name, 0)
        pall = p_all_dict.get(s_name, 0)
        vall = v_all_dict.get(s_name, 0)
        v30 = int(vall * 0.25)
        velocity = (p30 * 100000) + v30
        top_songs.append({
            "song": s_name,
            "purchases_30d": p30,
            "purchases_all_time": pall,
            "views_all_time": vall,
            "views_30d": v30,
            "velocity": velocity,
            "cover_url": resolve_cover_url(s_name)
        })
    top_songs.sort(key=lambda x: x["velocity"], reverse=True)

    # 5. Upcoming Upload Queue (pending + failed + completed preview)
    upcoming_queue = []
    q_path = _get_queue_db_path()
    if q_path:
        try:
            q_conn = sqlite3.connect(str(q_path), timeout=10.0)
            q_conn.execute("PRAGMA journal_mode=WAL;")
            q_conn.row_factory = sqlite3.Row
            qc = q_conn.cursor()
            qc.execute("""
                SELECT id, song, mode, profile, schedule_time, status, error, attempts
                FROM upload_queue
                ORDER BY
                  CASE WHEN status = 'pending' THEN 0 WHEN status = 'failed' THEN 1 ELSE 2 END,
                  datetime(schedule_time) ASC
                LIMIT 60
            """)
            upcoming_queue = [dict(r) for r in qc.fetchall()]
            q_conn.close()
        except Exception as q_err:
            print(f"[Pulse Summary] Queue read error: {q_err}")

    # 6. VM Services health (non-blocking)
    vm_services: Dict[str, str] = {
        "meloscribe-backend": "unknown",
        "oci-uploader": "unknown",
        "oci-sniper": "unknown",
    }
    if platform.system() != "Windows":
        try:
            proc = subprocess.run(
                ["systemctl", "is-active", "meloscribe-backend", "oci-uploader", "oci-sniper"],
                capture_output=True,
                text=True,
                timeout=1.5,
            )
            lines = [ln.strip() for ln in proc.stdout.strip().splitlines() if ln.strip()]
            for idx, svc in enumerate(("meloscribe-backend", "oci-uploader", "oci-sniper")):
                if idx < len(lines):
                    vm_services[svc] = lines[idx]
        except Exception as svc_err:
            print(f"[Pulse Summary] VM service check error: {svc_err}")

    conn.close()

    return {
        "kpis": {
            "revenue_all_time": revenue_all_time,
            "revenue_30d": revenue_30d,
            "revenue_7d": revenue_7d,
            "sales_count_all_time": sales_count_all_time,
            "sales_count_30d": sales_count_30d,
            "subscribers_active_count": subscribers_active_count,
            "open_suggestions_count": open_suggestions_count,
            "total_views_all_platforms": total_views_all_platforms,
            "platform_breakdown": platform_breakdown,
            "checkout_conversion_rate_30d": checkout_conv_rate,
            "social_visitor_attribution": social_traffic_attribution
        },
        "revenue_timeline": revenue_timeline,
        "activity_feed": activity_feed,
        "top_songs": top_songs[:15],
        "upcoming_queue": upcoming_queue,
        "subscribers_list": subscribers_list,
        "todos_list": todos_list,
        "checkout_ratings": checkout_ratings[:30],
        "vm_services": vm_services,
        "timestamp": datetime.datetime.utcnow().isoformat()
    }
