import os
import sys
import json
import uuid
import sqlite3
import platform
import requests
import stripe
import boto3
import re
import threading
from pathlib import Path
from pydantic import BaseModel
from fastapi import APIRouter, Request, HTTPException, BackgroundTasks
from fastapi.responses import JSONResponse, HTMLResponse, StreamingResponse, FileResponse, RedirectResponse

from shared import (
    settings,
    db_path,
    is_rate_limited,
    get_stripe_api_key,
    load_settings,
    CREATION_FLAGS
)

router = APIRouter()

# In-memory store for tracking unique website visitors per day
unique_visitors_today = set()

# -------------------------------------------------------------------
# Pydantic Request Models
# -------------------------------------------------------------------
class CheckoutRequest(BaseModel):
    songId: str
    format: str = "full_arrangement"
    difficulty: str = "Original"
    language: str = "en"

class NewSuggestion(BaseModel):
    title: str
    artist: str

class StatsUpload(BaseModel):
    followers: int

class NotifySubscribeRequest(BaseModel):
    email: str

# -------------------------------------------------------------------
# Helper functions
# -------------------------------------------------------------------
def generate_watermark_page(text: str):
    import io
    from reportlab.pdfgen import canvas
    from reportlab.lib.pagesizes import A4
    packet = io.BytesIO()
    can = canvas.Canvas(packet, pagesize=A4)
    
    # Draw horizontal watermark at the bottom of the page
    can.saveState()
    can.setFont("Helvetica", 9)
    can.setFillColorCMYK(0, 0, 0, 0.4) # Dark gray watermark text
    can.drawCentredString(A4[0] / 2.0, 30, text)
    can.restoreState()
    
    can.showPage()
    can.save()
    packet.seek(0)
    return packet

def watermark_pdf(pdf_bytes: bytes, buyer_name: str, email: str, transaction_id: str) -> bytes:
    import io
    from pypdf import PdfReader, PdfWriter
    try:
        name_part = f"{buyer_name} " if buyer_name else ""
        text = f"Licensed to: {name_part}({email}) | Order #{transaction_id}"
        
        watermark_pdf_stream = generate_watermark_page(text)
        watermark_reader = PdfReader(watermark_pdf_stream)
        watermark_page = watermark_reader.pages[0]
        
        reader = PdfReader(io.BytesIO(pdf_bytes))
        writer = PdfWriter()
        
        for page in reader.pages:
            page.merge_page(watermark_page)
            writer.add_page(page)
            
        output_stream = io.BytesIO()
        writer.write(output_stream)
        return output_stream.getvalue()
    except Exception as e:
        print(f"[Watermark] Error watermarking PDF: {e}")
        return pdf_bytes

def watermark_video(original_video_bytes: bytes, song_name: str, type: str) -> bytes:
    import subprocess
    import tempfile
    
    ffmpeg_executable = "ffmpeg"
    if os.name == 'nt':
        ffmpeg_executable = "ffmpeg.exe"
        
    temp_dir = tempfile.gettempdir()
    input_path = os.path.join(temp_dir, f"input_{song_name}_{type}.mp4")
    output_path = os.path.join(temp_dir, f"output_{song_name}_{type}.mp4")
    
    try:
        with open(input_path, "wb") as f:
            f.write(original_video_bytes)
            
        font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
        if os.name == 'nt' or not os.path.exists(font_path):
            font_path = "Arial"
            
        filter_str = f"drawtext=text='meloscribe.dev':fontfile='{font_path}':fontcolor=white@0.25:fontsize=24:x=w-tw-30:y=30"
        
        cmd = [
            ffmpeg_executable, "-y",
            "-i", input_path,
            "-vf", filter_str,
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
            "-c:a", "copy",
            output_path
        ]
        
        print(f"[Watermark Video] Running: {' '.join(cmd)}")
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if res.returncode != 0:
            print(f"[Watermark Video] FFmpeg error: {res.stderr.decode('utf-8', errors='ignore')}")
            return original_video_bytes
            
        with open(output_path, "rb") as f:
            watermarked_bytes = f.read()
            
        return watermarked_bytes
    except Exception as e:
        print(f"[Watermark Video] Error watermarking video: {e}")
        return original_video_bytes
    finally:
        for path in (input_path, output_path):
            if os.path.exists(path):
                try:
                    os.remove(path)
                except Exception:
                    pass

def send_purchase_delivery_email(email: str, song_name: str, download_hash: str, locale: str = "en"):
    api_key = load_settings().get("resend_api_key", "")
    if not api_key:
        log_webhook("[Notify] WARNING: resend_api_key not set in settings.json. Skipping purchase email.")
        return False
        
    download_url = f"https://meloscribe.dev/order/{download_hash}"
    
    html_body = f"""
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="font-family: 'Helvetica Neue', Arial, sans-serif; background: #0a0a0f; color: #e0e0e0; max-width: 520px; margin: 0 auto; padding: 32px 16px;">
  <div style="text-align: center; margin-bottom: 32px; background: #12121c; border: 1px solid #2a2a3e; border-radius: 16px; padding: 24px 16px;">
    <table align="center" border="0" cellpadding="0" cellspacing="0" style="margin: 0 auto; border-collapse: collapse;">
      <tr>
        <td style="font-family: 'Helvetica Neue', Arial, sans-serif; font-size: 32px; font-weight: 900; color: #00f5ff; letter-spacing: 3px; text-transform: lowercase; padding: 0; text-align: right;">melo</td>
        <td style="font-family: 'Helvetica Neue', Arial, sans-serif; font-size: 32px; font-weight: 900; color: #ff2d92; letter-spacing: 3px; text-transform: lowercase; padding: 0; text-align: left;">scribe</td>
      </tr>
    </table>
    <p style="color: #888899; font-size: 12px; margin: 12px 0 0 0; letter-spacing: 1px; font-style: italic;">Arranged by ear. Played by you.</p>
  </div>
  <div style="background: #12121c; border: 1px solid #2a2a3e; border-radius: 16px; padding: 32px;">
    <h2 style="color: #ffffff; font-size: 20px; margin-top: 0; margin-bottom: 16px; font-weight: 700; text-align: center;">🎹 Your Sheets Are Ready!</h2>
    <p style="color: #b0b0c0; line-height: 1.8; font-size: 15px;">Hey!</p>
    <p style="color: #b0b0c0; line-height: 1.8; font-size: 15px;">
      Thank you so much for your purchase and supporting my arrangements! Your learning package for <strong>{song_name}</strong> is ready.
    </p>
    <p style="color: #b0b0c0; line-height: 1.6; font-size: 15px;">Click the button below to download your sheet music (PDF), MIDI files, and practice video tutorials:</p>
    
    <div style="text-align: center; margin: 28px 0;">
      <a href="{download_url}" style="display: inline-block; background-color: #12121c; border: 2px solid #00f5d4; color: #00f5d4; font-family: 'Helvetica Neue', Arial, sans-serif; font-weight: 700; font-size: 15px; padding: 14px 32px; border-radius: 10px; text-decoration: none; text-shadow: 0 0 8px rgba(0,245,212,0.35);">Download Learning Package</a>
    </div>
    
    <p style="color: #888; font-size: 13px; text-align: center;">
      This download link is permanent. You can access it anytime to download updates or get your files.
    </p>
    
    <p style="color: #b0b0c0; line-height: 1.6; font-size: 15px; margin-top: 24px;">Happy practicing,<br>meloscribe</p>
  </div>
  <p style="text-align: center; font-size: 11px; color: #555; margin-top: 24px;">
    Need help? Reply directly to this email or visit <a href="https://meloscribe.dev" style="color: #00f5d4;">meloscribe.dev</a>
  </p>
</body>
</html>
"""

    try:
        resp = requests.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "from": "meloscribe <info@meloscribe.dev>",
                "to": [email],
                "subject": f"🎹 Your learning package for {song_name} is ready!",
                "html": html_body
            },
            timeout=10.0
        )
        if resp.status_code in (200, 201):
            log_webhook(f"[Notify] Purchase email sent successfully to {email}")
            return True
        else:
            log_webhook(f"[Notify] Failed to send purchase email: {resp.status_code} - {resp.text}")
            return False
    except Exception as err:
        log_webhook(f"[Notify] Resend exception: {err}")
        return False

def _send_confirmation_email(email: str, token: str):
    api_key = settings.get("resend_api_key", "")
    if not api_key:
        print("[Notify] WARNING: resend_api_key not set in settings.json. Skipping email.")
        return False
    
    confirm_url = f"https://api.meloscribe.dev/api/notify/confirm?token={token}"
    unsubscribe_url = f"https://api.meloscribe.dev/api/notify/unsubscribe?token={token}"
    
    html_body = f"""
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="font-family: 'Helvetica Neue', Arial, sans-serif; background: #0a0a0f; color: #e0e0e0; max-width: 520px; margin: 0 auto; padding: 32px 16px;">
  <div style="text-align: center; margin-bottom: 32px; background: #12121c; border: 1px solid #2a2a3e; border-radius: 16px; padding: 24px 16px;">
    <table align="center" border="0" cellpadding="0" cellspacing="0" style="margin: 0 auto; border-collapse: collapse;">
      <tr>
        <td style="font-family: 'Helvetica Neue', Arial, sans-serif; font-size: 32px; font-weight: 900; color: #00f5ff; letter-spacing: 3px; text-transform: lowercase; padding: 0 0 4px 0; border-bottom: 3px solid #00f5ff; text-align: right;">melo</td>
        <td style="font-family: 'Helvetica Neue', Arial, sans-serif; font-size: 32px; font-weight: 900; color: #ff2d92; letter-spacing: 3px; text-transform: lowercase; padding: 0 0 4px 0; border-bottom: 3px solid #ff2d92; text-align: left;">scribe</td>
      </tr>
    </table>
    <p style="color: #888899; font-size: 12px; margin: 12px 0 0 0; letter-spacing: 1px; font-style: italic;">Arranged by ear. Played by you.</p>
  </div>
  <div style="background: #12121c; border: 1px solid #2a2a3e; border-radius: 16px; padding: 32px;">
    <p style="color: #b0b0c0; line-height: 1.8; font-size: 15px;">Hey!</p>
    <p style="color: #b0b0c0; line-height: 1.8; font-size: 15px;">
      Thanks for your interest! Please confirm that you want to receive email notifications
      whenever new sheet music or practice assets are dropped on meloscribe.dev.
    </p>
    <p style="color: #b0b0c0; line-height: 1.6; font-size: 15px;">Click the link below to confirm your email:</p>
    <div style="text-align: center; margin: 28px 0;">
      <a href="{confirm_url}" style="display: inline-block; background-color: #12121c; border: 2px solid #00f5d4; color: #00f5d4; font-family: 'Helvetica Neue', Arial, sans-serif; font-weight: 700; font-size: 15px; padding: 14px 32px; border-radius: 10px; text-decoration: none; text-shadow: 0 0 8px rgba(0,245,212,0.35);">Confirm Subscription</a>
    </div>
    <p style="color: #888; font-size: 13px; text-align: center;">
      If you didn&apos;t request this, you can safely ignore this email. You won&apos;t be subscribed unless you click the link above.
    </p>
    <p style="color: #b0b0c0; line-height: 1.6; font-size: 15px; margin-top: 24px;">Best,<br>meloscribe</p>
  </div>
  <p style="text-align: center; font-size: 11px; color: #555; margin-top: 24px;">
    Unsubscribe anytime: <a href="{unsubscribe_url}" style="color: #555;">click here</a>
  </p>
</body>
</html>
"""

    try:
        resp = requests.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "from": "meloscribe <info@meloscribe.dev>",
                "to": [email],
                "subject": "Confirm your sheet music notifications — meloscribe",
                "html": html_body,
            },
            timeout=10
        )
        if resp.status_code in (200, 201):
            print(f"[Notify] Confirmation email sent to {email}")
            return True
        else:
            print(f"[Notify] Resend API error {resp.status_code}: {resp.text}")
            return False
    except Exception as e:
        print(f"[Notify] Email send failed: {e}")
        return False

# -------------------------------------------------------------------
# Checkout session & Webhooks
# -------------------------------------------------------------------
@router.post("/api/checkout/create-session")
async def create_checkout_session(req: CheckoutRequest, request: Request):
    try:
        songs_path = r"c:\Dev\meloscribe-frontend\website\src\data\songs.json"
        if not os.path.exists(songs_path):
            songs_path = Path(__file__).resolve().parent / "songs.json"
            
        with open(songs_path, "r", encoding="utf-8") as f:
            songs_list = json.load(f)
            
        song = next((s for s in songs_list if str(s.get("id")) == str(req.songId)), None)
        if not song:
            raise HTTPException(status_code=404, detail="Song not found")
            
        if song.get("paymentsDisabled") or song.get("hidden"):
            raise HTTPException(status_code=403, detail="Product is no longer available")
            
        price_str = song.get("price", "6 €")
        currency = "eur"
        if "$" in price_str:
            currency = "usd"
        elif "£" in price_str:
            currency = "gbp"
            
        try:
            digits = re.findall(r"\d+", price_str)
            if digits:
                amount_cents = int(digits[0]) * 100
            else:
                amount_cents = 600
        except Exception:
            amount_cents = 600
            
        download_hash = uuid.uuid4().hex
        origin = request.headers.get("origin") or "https://meloscribe.dev"
        
        stripe.api_key = get_stripe_api_key()
        if not stripe.api_key:
            raise HTTPException(status_code=500, detail="Stripe API key is not configured")
            
        product_name = f"{song.get('title')} ({req.format.replace('_', ' ').title()} - {req.difficulty})"
        product_desc = "Includes PDF Sheet Music, MIDI Files, and Practice Video Tutorials"
        
        cover_image_path = song.get("coverImage", "")
        product_image = None
        if cover_image_path:
            import urllib.parse
            quoted_path = urllib.parse.quote(cover_image_path)
            product_image = f"https://meloscribe.dev{quoted_path}"
            
        def to_slug(text):
            s = text.lower()
            s = re.sub(r'[^a-z0-9]+', '-', s)
            s = re.sub(r'(^-|-$)', '', s)
            return s

        session = stripe.checkout.Session.create(
            mode="payment",
            line_items=[{
                "price_data": {
                    "currency": currency,
                    "product_data": {
                        "name": product_name,
                        "description": product_desc,
                        "images": [product_image] if product_image else [],
                    },
                    "unit_amount": amount_cents,
                },
                "quantity": 1,
            }],
            invoice_creation={"enabled": True},
            billing_address_collection="required",
            success_url=f"{origin}/success?checkout_id={{CHECKOUT_SESSION_ID}}",
            cancel_url=f"{origin}/sheets?song={to_slug(song.get('title', ''))}&version={to_slug(req.difficulty)}",
            metadata={
                "song_title": song.get("title"),
                "download_hash": download_hash,
                "locale": req.language
            }
        )
        return {"url": session.url}
    except Exception as e:
        print(f"[Stripe Checkout] Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

def log_webhook(msg: str):
    import platform
    from pathlib import Path
    from datetime import datetime
    try:
        log_path = "/home/ubuntu/meloscribe/stripe_webhook.log"
        if platform.system() == "Windows":
            log_path = str(Path(__file__).resolve().parent / "stripe_webhook.log")
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"[{datetime.now().isoformat()}] {msg}\n")
    except Exception as e:
        print(f"Failed to write webhook log: {e}")

@router.post("/api/webhooks/stripe")
async def stripe_webhook(request: Request, background_tasks: BackgroundTasks):
    payload = await request.body()
    sig_header = request.headers.get("Stripe-Signature")
    
    s_settings = load_settings()
    is_sandbox = s_settings.get("environment", "sandbox") == "sandbox"
    if is_sandbox:
        webhook_secret = s_settings.get("stripe_sandbox_webhook_secret") or s_settings.get("stripe_webhook_secret") or os.environ.get("STRIPE_WEBHOOK_SECRET")
    else:
        webhook_secret = s_settings.get("stripe_live_webhook_secret") or s_settings.get("stripe_webhook_secret") or os.environ.get("STRIPE_WEBHOOK_SECRET")
    
    log_webhook(f"Received webhook request. Signature: {sig_header[:20] if sig_header else 'None'}. Environment sandbox: {is_sandbox}. Webhook secret used: {webhook_secret[:10] if webhook_secret else 'None'}...")
    stripe.api_key = get_stripe_api_key()
    
    try:
        if webhook_secret:
            event = stripe.Webhook.construct_event(
                payload, sig_header, webhook_secret
            )
        else:
            log_webhook("[Stripe Webhook] WARNING: stripe_webhook_secret not set. Proceeding without signature verification.")
            event = stripe.Event.construct_from(json.loads(payload.decode('utf-8')), stripe.api_key)
        log_webhook(f"Signature verified successfully. Event type: {event.type}")
    except Exception as e:
        log_webhook(f"Signature verification failed: {e}")
        return JSONResponse(status_code=400, content={"error": str(e)})

    event_type = event.type
    data_object_raw = event.data.object
    data_object = data_object_raw.to_dict() if hasattr(data_object_raw, "to_dict") else data_object_raw

    try:
        if event_type == "checkout.session.completed":
            session_id = data_object.get("id")
            payment_status = data_object.get("payment_status")
            log_webhook(f"Processing checkout.session.completed. Payment status: {payment_status}. Session ID: {session_id}")
            
            if payment_status == "paid":
                metadata = data_object.get("metadata", {})
                song_title = metadata.get("song_title") or "Unknown Song"
                download_hash = metadata.get("download_hash")
                locale = metadata.get("locale") or "en"
                
                try:
                    songs_json_path = r"c:\Dev\meloscribe-frontend\website\src\data\songs.json"
                    if not os.path.exists(songs_json_path):
                        songs_json_path = Path(__file__).resolve().parent / "songs.json"
                    if os.path.exists(songs_json_path):
                        with open(songs_json_path, "r", encoding="utf-8") as f:
                            songs_db = json.load(f)
                        matched_song = next((s for s in songs_db if s.get("title") == song_title), None)
                        if matched_song:
                            if matched_song.get("paymentsDisabled") or matched_song.get("hidden"):
                                log_webhook(f"[Stripe Webhook] REJECTED purchase for '{song_title}' (paymentsDisabled or hidden).")
                                return JSONResponse(content={"error": "Product is no longer available"}, status_code=403)
                except Exception as check_err:
                    log_webhook(f"[Stripe Webhook] Error checking song availability: {check_err}")
                
                if not download_hash:
                    download_hash = uuid.uuid4().hex

                customer_details = data_object.get("customer_details") or {}
                email = customer_details.get("email") or "customer@example.com"
                buyer_name = customer_details.get("name") or ""
                
                amount_total = float(data_object.get("amount_total", 0)) / 100.0
                currency = (data_object.get("currency") or "eur").upper()

                log_webhook(f"Recording purchase in DB: Email={email}, Song={song_title}, Amount={amount_total} {currency}, Hash={download_hash}")
                conn = sqlite3.connect(str(db_path))
                c = conn.cursor()
                c.execute(
                    "INSERT OR IGNORE INTO purchases (transaction_id, email, song_name, amount, currency, status, download_hash, locale, buyer_name) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (session_id, email, song_title, amount_total, currency, "🟢 Active", download_hash, locale, buyer_name)
                )
                is_new = c.rowcount > 0
                
                c.execute(
                    "UPDATE purchases SET locale = ?, buyer_name = ? WHERE transaction_id = ?",
                    (locale, buyer_name, session_id)
                )
                
                c.execute(
                    "INSERT INTO revenue (amount, currency, source, event_type, buyer, message, song_name) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (amount_total, currency, "stripe", event_type, email, f"Stripe txn {session_id}", song_title)
                )
                conn.commit()
                conn.close()
                log_webhook(f"[Stripe Webhook] Recorded purchase successfully (new: {is_new})")
                
                if is_new:
                    log_webhook(f"Triggering email delivery function for {email}...")
                    success = send_purchase_delivery_email(email, song_title, download_hash, locale)
                    log_webhook(f"Email delivery function completed. Result: {'SUCCESS' if success else 'FAILED'}")
                    
        elif event_type == "charge.refunded":
            charge_id = data_object.get("id")
            payment_intent_id = data_object.get("payment_intent")
            
            conn = sqlite3.connect(str(db_path))
            c = conn.cursor()
            c.execute("SELECT transaction_id FROM purchases WHERE transaction_id = ? OR transaction_id = ?", (payment_intent_id, charge_id))
            row = c.fetchone()
            
            if not row and payment_intent_id:
                try:
                    sessions = stripe.checkout.Session.list(payment_intent=payment_intent_id, limit=1)
                    if sessions and len(sessions.data) > 0:
                        stripe_session_id = sessions.data[0].id
                        c.execute("SELECT transaction_id FROM purchases WHERE transaction_id = ?", (stripe_session_id,))
                        row = c.fetchone()
                except Exception as search_err:
                    print(f"[Stripe Webhook] Error listing sessions for refund: {search_err}")
            
            if row:
                txn_id = row[0]
                c.execute("UPDATE purchases SET status = '🔴 Refunded' WHERE transaction_id = ?", (txn_id,))
                conn.commit()
                print(f"[Stripe Webhook] Refund recorded for transaction {txn_id}.")
            else:
                print(f"[Stripe Webhook] Warning: Could not find purchase for refund of payment intent {payment_intent_id} / charge {charge_id}.")
            conn.close()
            
    except Exception as e:
        print(f"[Stripe Webhook] Error processing webhook: {e}")
        return JSONResponse(status_code=500, content={"error": str(e)})

    return {"status": "success"}

# -------------------------------------------------------------------
# Order retrieval & Verification
# -------------------------------------------------------------------
@router.get("/api/order/hash-by-checkout")
def get_hash_by_checkout(checkout_id: str):
    conn = sqlite3.connect(str(db_path), timeout=30.0)
    c = conn.cursor()
    c.execute("SELECT download_hash FROM purchases WHERE transaction_id = ?", (checkout_id,))
    row = c.fetchone()
    conn.close()
    
    if row:
        return {"download_hash": row[0]}
        
    if checkout_id.startswith("demo_"):
        return {"download_hash": f"demo_hash_{checkout_id}"}
        
    if checkout_id.startswith("cs_"):
        try:
            stripe.api_key = get_stripe_api_key()
            if stripe.api_key:
                session_raw = stripe.checkout.Session.retrieve(checkout_id)
                session = session_raw.to_dict() if hasattr(session_raw, "to_dict") else session_raw
                if session.get("payment_status") == "paid":
                    metadata = session.get("metadata") or {}
                    song_title = metadata.get("song_title") or "Unknown Song"
                    download_hash = metadata.get("download_hash")
                    locale = metadata.get("locale") or "en"
                    
                    if not download_hash:
                        download_hash = uuid.uuid4().hex
                    
                    customer_details = session.get("customer_details") or {}
                    email = customer_details.get("email") or "customer@example.com"
                    buyer_name = customer_details.get("name") or ""
                    
                    amount_total = float(session.get("amount_total") or 0) / 100.0
                    currency = (session.get("currency") or "eur").upper()
                    
                    conn = sqlite3.connect(str(db_path), timeout=30.0)
                    c = conn.cursor()
                    c.execute(
                        "INSERT OR IGNORE INTO purchases (transaction_id, email, song_name, amount, currency, status, download_hash, locale, buyer_name) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (checkout_id, email, song_title, amount_total, currency, "🟢 Active", download_hash, locale, buyer_name)
                    )
                    is_new = c.rowcount > 0
                    
                    c.execute(
                        "UPDATE purchases SET locale = ?, buyer_name = ? WHERE transaction_id = ?",
                        (locale, buyer_name, checkout_id)
                    )
                    
                    c.execute(
                        "INSERT INTO revenue (amount, currency, source, event_type, buyer, message, song_name) VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (amount_total, currency, "stripe", "checkout.session.completed", email, f"Stripe txn {checkout_id} (API Fallback)", song_title)
                    )
                    conn.commit()
                    conn.close()
                    print(f"[Stripe API Fallback] Recorded purchase for '{song_title}' by {email} with hash {download_hash} (new: {is_new})")
                    
                    if is_new:
                        send_purchase_delivery_email(email, song_title, download_hash, locale)
                    
                    return {"download_hash": download_hash}
        except Exception as api_err:
            print(f"[Stripe API Fallback] Error verifying transaction: {api_err}")
            
    elif checkout_id.startswith("txn_"):
        try:
            s_settings = load_settings()
            is_sandbox = s_settings.get("environment", "sandbox") == "sandbox"
            api_key = s_settings.get("paddle_sandbox_api_key" if is_sandbox else "paddle_live_api_key")
            url_prefix = "https://sandbox-api.paddle.com" if is_sandbox else "https://api.paddle.com"
            
            if api_key:
                headers = {"Authorization": f"Bearer {api_key}"}
                tx_resp = requests.get(f"{url_prefix}/transactions/{checkout_id}", headers=headers, timeout=10.0)
                if tx_resp.status_code == 200:
                    tx_data = tx_resp.json().get("data", {})
                    status = tx_data.get("status")
                    if status == "completed":
                        customer_id = tx_data.get("customer_id")
                        email = "customer@example.com"
                        buyer_name = ""
                        if customer_id:
                            cust_resp = requests.get(f"{url_prefix}/customers/{customer_id}", headers=headers, timeout=10.0)
                            if cust_resp.status_code == 200:
                                cust_info = cust_resp.json().get("data") or {}
                                email = cust_info.get("email", email)
                                buyer_name = cust_info.get("name", "")
                        
                        if not buyer_name:
                            buyer_name = (tx_data.get("billing_details") or {}).get("name") or ""
                            
                        locale = tx_data.get("locale") or "en"
                        custom_data = tx_data.get("custom_data") or {}
                        song_title = custom_data.get("song_title") or "Unknown Song"
                        download_hash = custom_data.get("download_hash")
                        if not download_hash:
                            download_hash = uuid.uuid4().hex
                        
                        totals = (tx_data.get("details") or {}).get("totals") or {}
                        grand_total = float(totals.get("grand_total", 0)) / 100.0
                        currency = totals.get("currency_code", "EUR")
                        
                        conn = sqlite3.connect(str(db_path), timeout=30.0)
                        c = conn.cursor()
                        c.execute(
                            "INSERT OR IGNORE INTO purchases (transaction_id, email, song_name, amount, currency, status, download_hash, locale, buyer_name) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                            (checkout_id, email, song_title, grand_total, currency, status, download_hash, locale, buyer_name)
                        )
                        is_new = c.rowcount > 0
                        
                        c.execute(
                            "UPDATE purchases SET locale = ?, buyer_name = ? WHERE transaction_id = ?",
                            (locale, buyer_name, checkout_id)
                        )
                        
                        c.execute(
                            "INSERT INTO revenue (amount, currency, source, event_type, buyer, message, song_name) VALUES (?, ?, ?, ?, ?, ?, ?)",
                            (grand_total, currency, "paddle", "transaction.completed", email, f"Paddle txn {checkout_id} (API Fallback)", song_title)
                        )
                        conn.commit()
                        conn.close()
                        print(f"[Paddle API Fallback] Recorded purchase for '{song_title}' by {email} with hash {download_hash} (new: {is_new})")
                        
                        if is_new:
                            send_purchase_delivery_email(email, song_title, download_hash, locale)
                        
                        return {"download_hash": download_hash}
        except Exception as api_err:
            print(f"[Paddle API Fallback] Error verifying transaction: {api_err}")
            
    return JSONResponse(content={"error": "Transaction not found"}, status_code=404)

@router.get("/api/order/details")
def get_order_details(hash: str):
    conn = sqlite3.connect(str(db_path), timeout=30.0)
    c = conn.cursor()
    c.execute("SELECT song_name, email, download_count, created_at, status FROM purchases WHERE download_hash = ?", (hash,))
    row = c.fetchone()
    conn.close()
    
    if not row and hash.startswith("demo_hash_"):
        return {
            "song_name": "Sweetest Rain",
            "email": "demo_customer@example.com",
            "download_count": 0,
            "created_at": "2026-07-01T12:00:00Z",
            "status": "completed"
        }
        
    if not row:
        return JSONResponse(content={"error": "Order not found"}, status_code=404)
        
    status_val = (row[4] or "").strip().lower()
    if "inactive" in status_val or "deactivate" in status_val or "refund" in status_val:
        return JSONResponse(content={"error": "This order has been deactivated / refunded"}, status_code=403)
        
    return {
        "song_name": row[0],
        "email": row[1],
        "download_count": row[2],
        "created_at": row[3]
    }

# -------------------------------------------------------------------
# Secure file downloads
# -------------------------------------------------------------------
@router.get("/api/download/request")
def request_download(hash: str, type: str, request: Request):
    if type not in ("pdf", "zip", "midi", "midi_slow", "video", "video_slow"):
        return JSONResponse(content={"error": "Invalid download type"}, status_code=400)
        
    conn = sqlite3.connect(str(db_path), timeout=30.0)
    c = conn.cursor()
    c.execute("SELECT song_name, download_count, downloaded_types, ip_addresses, status FROM purchases WHERE download_hash = ?", (hash,))
    row = c.fetchone()
    
    song_name = None
    download_count = 0
    downloaded_types = ""
    ip_addresses = ""
    status = ""
    
    if row:
        song_name = row[0]
        download_count = row[1]
        downloaded_types = row[2] or ""
        ip_addresses = row[3] or ""
        status = row[4] or ""
    elif hash.startswith("demo_hash_"):
        song_name = "Sweetest Rain"
        download_count = 0
        status = "completed"
        print(f"[Download Request] Sandbox hash '{hash}' resolved to '{song_name}'")
        
    if not song_name:
        conn.close()
        return JSONResponse(content={"error": "Order not found"}, status_code=404)
        
    status_val = status.strip().lower()
    if "inactive" in status_val or "deactivate" in status_val or "refund" in status_val:
        conn.close()
        return JSONResponse(content={"error": "This order has been deactivated / refunded"}, status_code=403)
        
    client_ip = request.headers.get("cf-connecting-ip") or request.headers.get("x-forwarded-for") or request.headers.get("x-real-ip") or request.client.host
    if row and not hash.startswith("demo_hash_"):
        ip_list = [ip.strip() for ip in ip_addresses.split(",") if ip.strip()]
        if client_ip not in ip_list:
            if len(ip_list) >= 3:
                conn.close()
                return JSONResponse(content={"error": "Link has been accessed from too many different devices or locations. Please contact support."}, status_code=403)
            ip_list.append(client_ip)
            new_ip_str = ",".join(ip_list)
            c.execute("UPDATE purchases SET ip_addresses = ? WHERE download_hash = ?", (new_ip_str, hash))
            conn.commit()
            
    if download_count >= 100:
        conn.close()
        return JSONResponse(content={"error": "Download limit reached (maximum 100 downloads allowed)"}, status_code=403)
        
    new_count = download_count
    if row:
        new_count = download_count + 1
        types_list = [t.strip() for t in downloaded_types.split(",") if t.strip()]
        if type not in types_list:
            types_list.append(type)
        new_types_str = ",".join(types_list)
        
        c.execute("UPDATE purchases SET download_count = ?, downloaded_types = ? WHERE download_hash = ?", (new_count, new_types_str, hash))
        conn.commit()
    conn.close()
    
    download_file_url = f"{request.base_url}api/download/file?hash={hash}&type={type}"
    return {"download_url": download_file_url, "download_count": new_count}

@router.get("/api/download/file")
def download_file(hash: str, type: str, request: Request):
    if type not in ("pdf", "zip", "midi", "midi_slow", "video", "video_slow"):
        return JSONResponse(content={"error": "Invalid download type"}, status_code=400)
        
    conn = sqlite3.connect(str(db_path), timeout=30.0)
    c = conn.cursor()
    c.execute("SELECT song_name, email, transaction_id, ip_addresses, buyer_name, status FROM purchases WHERE download_hash = ?", (hash,))
    row = c.fetchone()
    
    song_name = None
    email = None
    txn_id = None
    ip_addresses = ""
    buyer_name = ""
    status = ""
    
    if row:
        song_name = row[0]
        email = row[1]
        txn_id = row[2]
        ip_addresses = row[3] or ""
        buyer_name = row[4] or ""
        status = row[5] or ""
    elif hash.startswith("demo_hash_"):
        song_name = "Sweetest Rain"
        email = "demo_customer@example.com"
        txn_id = "demo_12345"
        buyer_name = "Jane Doe"
        status = "completed"
        
    if not song_name:
        conn.close()
        return JSONResponse(content={"error": "Order not found"}, status_code=404)
        
    status_val = status.strip().lower()
    if "inactive" in status_val or "deactivate" in status_val or "refund" in status_val:
        conn.close()
        return JSONResponse(content={"error": "This order has been deactivated / refunded"}, status_code=403)
        
    client_ip = request.headers.get("cf-connecting-ip") or request.headers.get("x-forwarded-for") or request.headers.get("x-real-ip") or request.client.host
    if row and not hash.startswith("demo_hash_"):
        ip_list = [ip.strip() for ip in ip_addresses.split(",") if ip.strip()]
        if client_ip not in ip_list:
            if len(ip_list) >= 3:
                conn.close()
                return JSONResponse(content={"error": "Link has been accessed from too many different devices or locations. Please contact support."}, status_code=403)
            ip_list.append(client_ip)
            new_ip_str = ",".join(ip_list)
            c.execute("UPDATE purchases SET ip_addresses = ? WHERE download_hash = ?", (new_ip_str, hash))
            conn.commit()
    conn.close()
    
    r2_account_id = settings.get("r2_account_id") or os.environ.get("R2_ACCOUNT_ID")
    r2_access_key = settings.get("r2_access_key") or settings.get("r2_access_key_id") or os.environ.get("R2_ACCESS_KEY_ID")
    r2_secret_key = settings.get("r2_secret_key") or settings.get("r2_secret_access_key") or os.environ.get("R2_SECRET_ACCESS_KEY")
    r2_bucket = settings.get("r2_bucket") or settings.get("r2_bucket_name", "meloscribe-sheets") or os.environ.get("R2_BUCKET_NAME", "meloscribe-sheets")
    
    if not r2_account_id or not r2_access_key or not r2_secret_key:
        print("[Download File] R2 credentials missing, using demo redirect fallback.")
        if type == "pdf":
            suffix = f"/{song_name}.pdf"
        elif type == "midi":
            suffix = f"/{song_name}.mid"
        elif type == "midi_slow":
            suffix = f"/{song_name} slow.mid"
        elif type == "video":
            suffix = f"/{song_name}.mp4"
        elif type == "video_slow":
            suffix = f"/{song_name} slow.mp4"
        else:
            suffix = " Full Package.zip"
        return RedirectResponse(url=f"https://example.com/demo-packages/{song_name}{suffix}")
        
    try:
        import boto3
        from botocore.config import Config
        
        if type == "pdf":
            file_key = f"{song_name}/{song_name}.pdf"
        elif type == "midi":
            file_key = f"{song_name}/{song_name}.mid"
        elif type == "midi_slow":
            file_key = f"{song_name}/{song_name} slow.mid"
        elif type == "video":
            file_key = f"{song_name}/{song_name}.mp4"
        elif type == "video_slow":
            file_key = f"{song_name}/{song_name} slow.mp4"
        else:
            file_key = f"{song_name} Full Package.zip"
            
        s3 = boto3.client(
            's3',
            endpoint_url=f'https://{r2_account_id}.r2.cloudflarestorage.com',
            aws_access_key_id=r2_access_key,
            aws_secret_access_key=r2_secret_key,
            region_name='auto',
            config=Config(signature_version='s3v4')
        )
        
        if type == "pdf":
            print(f"[Download File] Fetching '{file_key}' from R2 for watermarking...")
            pdf_obj = s3.get_object(Bucket=r2_bucket, Key=file_key)
            original_pdf_bytes = pdf_obj['Body'].read()
            
            watermarked_bytes = watermark_pdf(original_pdf_bytes, buyer_name, email, txn_id)
            
            from fastapi.responses import Response
            headers = {
                "Content-Disposition": f'attachment; filename="{song_name}.pdf"'
            }
            return Response(content=watermarked_bytes, media_type="application/pdf", headers=headers)
        else:
            filename = file_key.split('/')[-1]
            presigned_url = s3.generate_presigned_url(
                ClientMethod='get_object',
                Params={
                    'Bucket': r2_bucket, 
                    'Key': file_key,
                    'ResponseContentDisposition': f'attachment; filename="{filename}"'
                },
                ExpiresIn=900
            )
            response = RedirectResponse(url=presigned_url)
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
            return response
            
    except Exception as e:
        print(f"[Download File] Error serving file: {e}")
        return JSONResponse(content={"error": f"Failed to serve file: {str(e)}"}, status_code=500)

@router.get("/api/download/verify")
def verify_download(checkout_id: str):
    conn = sqlite3.connect(str(db_path))
    c = conn.cursor()
    c.execute("SELECT song_name FROM purchases WHERE transaction_id = ? AND status = 'completed'", (checkout_id,))
    row = c.fetchone()
    conn.close()
    
    song_name = None
    if row:
        song_name = row[0]
    elif checkout_id.startswith("demo_"):
        song_name = "Sweetest Rain"
        print(f"[Download Verify] Sandbox checkout '{checkout_id}' resolved to '{song_name}'")
        
    if not song_name:
        return JSONResponse(content={"error": "Purchase not found or not completed"}, status_code=403)
        
    r2_account_id = settings.get("r2_account_id") or os.environ.get("R2_ACCOUNT_ID")
    r2_access_key = settings.get("r2_access_key") or settings.get("r2_access_key_id") or os.environ.get("R2_ACCESS_KEY_ID")
    r2_secret_key = settings.get("r2_secret_key") or settings.get("r2_secret_access_key") or os.environ.get("R2_SECRET_ACCESS_KEY")
    r2_bucket = settings.get("r2_bucket") or settings.get("r2_bucket_name", "meloscribe-assets") or os.environ.get("R2_BUCKET_NAME", "meloscribe-assets")
    
    if not r2_account_id or not r2_access_key or not r2_secret_key:
        print("[Download Verify] R2 credentials missing, using demo redirect fallback.")
        return {
            "files": [],
            "message": "Demo mode: R2 credentials are not configured in settings.json"
        }
        
    try:
        import boto3
        from botocore.config import Config
        
        s3 = boto3.client(
            's3',
            endpoint_url=f'https://{r2_account_id}.r2.cloudflarestorage.com',
            aws_access_key_id=r2_access_key,
            aws_secret_access_key=r2_secret_key,
            region_name='auto',
            config=Config(signature_version='s3v4')
        )

        file_specs = [
            {"key": f"{song_name}/{song_name}.pdf",       "label": "Sheet Music (PDF)",          "type": "pdf"},
            {"key": f"{song_name}/{song_name}.mid",       "label": "MIDI – Normal Speed",         "type": "midi"},
            {"key": f"{song_name}/{song_name} slow.mid",  "label": "MIDI – Slow Practice",        "type": "midi"},
            {"key": f"{song_name}/{song_name}.mp4",       "label": "Practice Video – Normal Speed", "type": "video"},
            {"key": f"{song_name}/{song_name} slow.mp4",  "label": "Practice Video – Slow",       "type": "video"},
        ]

        files = []
        for spec in file_specs:
            try:
                s3.head_object(Bucket=r2_bucket, Key=spec["key"])
                url = s3.generate_presigned_url(
                    ClientMethod='get_object',
                    Params={'Bucket': r2_bucket, 'Key': spec["key"]},
                    ExpiresIn=900
                )
                files.append({"label": spec["label"], "url": url, "type": spec["type"]})
            except Exception:
                pass
        
        if not files:
            return JSONResponse(content={"error": "No download files found for this purchase. Please contact support."}, status_code=404)
        
        return {"files": files, "song_name": song_name}
    except Exception as e:
        print(f"Failed to generate presigned R2 URLs: {e}")
        return JSONResponse(content={"error": f"Failed to generate download URLs: {str(e)}"}, status_code=500)

# -------------------------------------------------------------------
# E-Mail Opt-in (double opt-in)
# -------------------------------------------------------------------
@router.post("/api/notify/subscribe")
async def notify_subscribe(req: NotifySubscribeRequest):
    email = req.email.strip().lower()
    if not email or "@" not in email or "." not in email.split("@")[-1]:
        return JSONResponse(content={"error": "Invalid email address."}, status_code=400)
    
    token = uuid.uuid4().hex
    
    try:
        conn = sqlite3.connect(str(db_path), timeout=30.0)
        c = conn.cursor()
        c.execute("SELECT status FROM notify_subscribers WHERE email = ?", (email,))
        row = c.fetchone()
        if row:
            if row[0] == "active":
                conn.close()
                return {"status": "already_active", "message": "This email is already subscribed."}
            else:
                c.execute("UPDATE notify_subscribers SET token = ?, status = 'pending' WHERE email = ?", (token, email))
        else:
            c.execute("INSERT INTO notify_subscribers (email, token, status) VALUES (?, ?, 'pending')", (email, token))
        conn.commit()
        conn.close()
    except Exception as e:
        return JSONResponse(content={"error": f"Database error: {str(e)}"}, status_code=500)
    
    threading.Thread(target=lambda: _send_confirmation_email(email, token), daemon=True).start()
    return {"status": "pending", "message": "Confirmation email sent. Please check your inbox."}

@router.get("/api/notify/confirm")
def notify_confirm(token: str):
    try:
        conn = sqlite3.connect(str(db_path), timeout=30.0)
        c = conn.cursor()
        c.execute("UPDATE notify_subscribers SET status = 'active', confirmed_at = CURRENT_TIMESTAMP WHERE token = ?", (token,))
        row_count = c.rowcount
        conn.commit()
        conn.close()
        
        if row_count == 0:
            return HTMLResponse(content="""
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>meloscribe</title>
  <style>
    body {
      background: linear-gradient(135deg, #0a0a14 0%, #050508 100%);
      color: #ffffff;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      margin: 0; padding: 0;
      display: flex; justify-content: center; align-items: center;
      min-height: 100vh; overflow: hidden;
    }
    .glow-orb {
      position: absolute; width: 400px; height: 400px; border-radius: 50%;
      filter: blur(150px); z-index: 1; opacity: 0.15;
    }
    .orb-1 { background: #ff4d8d; top: -150px; left: -150px; }
    .orb-2 { background: #00f5d4; bottom: -150px; right: -150px; }
    .container {
      background: rgba(18, 18, 28, 0.45);
      backdrop-filter: blur(16px);
      border: 1px solid rgba(255, 255, 255, 0.08); border-radius: 24px;
      padding: 48px; text-align: center; max-width: 420px; width: 90%; z-index: 10;
      box-shadow: 0 20px 40px rgba(0, 0, 0, 0.5);
    }
    .title { font-size: 24px; font-weight: 700; margin-bottom: 16px; }
    .desc { font-size: 14px; color: #888; line-height: 1.6; }
  </style>
</head>
<body>
  <div class="glow-orb orb-1"></div>
  <div class="glow-orb orb-2"></div>
  <div class="container">
    <div class="title">Invalid link</div>
    <div class="desc">This subscription confirmation link is invalid or has expired.</div>
  </div>
</body>
</html>""")
            
        return HTMLResponse(content="""
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>meloscribe</title>
  <style>
    body {
      background: linear-gradient(135deg, #0a0a14 0%, #050508 100%);
      color: #ffffff;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      margin: 0; padding: 0;
      display: flex; justify-content: center; align-items: center;
      min-height: 100vh; overflow: hidden;
    }
    .glow-orb {
      position: absolute; width: 400px; height: 400px; border-radius: 50%;
      filter: blur(150px); z-index: 1; opacity: 0.15;
    }
    .orb-1 { background: #ff4d8d; top: -150px; left: -150px; }
    .orb-2 { background: #00f5d4; bottom: -150px; right: -150px; }
    .container {
      background: rgba(18, 18, 28, 0.45);
      backdrop-filter: blur(16px); -webkit-backdrop-filter: blur(16px);
      border: 1px solid rgba(255, 255, 255, 0.08); border-radius: 24px;
      padding: 48px; text-align: center; max-width: 420px; width: 90%; z-index: 10;
      box-shadow: 0 20px 40px rgba(0, 0, 0, 0.5);
      animation: fadeIn 0.6s ease-out;
    }
    .check-icon { width: 48px; height: 48px; color: #00f5d4; margin: 0 auto 16px; }
    .badge { display: inline-block; background: rgba(0,245,212,0.1); color: #00f5d4; border: 1px solid rgba(0,245,212,0.3); padding: 4px 12px; border-radius: 12px; font-size: 11px; margin-bottom: 16px; font-weight: 600; }
    .title { font-size: 28px; font-weight: 700; margin-bottom: 12px; }
    .desc { font-size: 14px; color: rgba(255,255,255,0.6); line-height: 1.6; margin-bottom: 24px; }
    .btn { display: inline-block; background: #00f5d4; color: #000; padding: 12px 32px; border-radius: 10px; text-decoration: none; font-weight: 700; }
  </style>
</head>
<body>
  <div class="glow-orb orb-1"></div>
  <div class="glow-orb orb-2"></div>
  <div class="container">
    <svg class="check-icon" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="2">
      <path stroke-linecap="round" stroke-linejoin="round" d="M5 13l4 4L19 7" />
    </svg>
    <div class="badge">Success</div>
    <div class="title">You're in!</div>
    <div class="desc">You'll be notified when new sheet music and practice assets drop on meloscribe.dev.</div>
    <a href="https://meloscribe.dev" class="btn">Go to meloscribe.dev</a>
  </div>
</body>
</html>""")
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)

@router.get("/api/notify/unsubscribe")
def notify_unsubscribe(token: str):
    try:
        conn = sqlite3.connect(str(db_path), timeout=30.0)
        c = conn.cursor()
        c.execute("DELETE FROM notify_subscribers WHERE token = ?", (token,))
        found = c.rowcount > 0
        conn.commit()
        conn.close()
        
        badge_text = "Unsubscribed" if found else "Not Found"
        title_text = "Unsubscribed" if found else "Link Expired"
        desc_text = "You will no longer receive sheet music drops or email alerts." if found else "This unsubscribe link is invalid or has already been used."
        badge_color = "#ff4d8d" if found else "#b0b0c0"
        badge_bg = "rgba(255, 77, 141, 0.1)" if found else "rgba(255, 255, 255, 0.05)"
        badge_border = "rgba(255, 77, 141, 0.25)" if found else "rgba(255, 255, 255, 0.15)"
        
        return HTMLResponse(content=f"""
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>meloscribe</title>
  <style>
    body {{
      background: linear-gradient(135deg, #0a0a14 0%, #050508 100%);
      color: #ffffff;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      margin: 0; padding: 0;
      display: flex; justify-content: center; align-items: center;
      min-height: 100vh; overflow: hidden;
    }}
    .glow-orb {{
      position: absolute; width: 400px; height: 400px; border-radius: 50%;
      filter: blur(150px); z-index: 1; opacity: 0.15;
    }}
    .orb-1 {{ background: #ff4d8d; top: -150px; left: -150px; }}
    .orb-2 {{ background: #00f5d4; bottom: -150px; right: -150px; }}
    .container {{
      background: rgba(18, 18, 28, 0.45);
      backdrop-filter: blur(16px);
      border: 1px solid rgba(255, 255, 255, 0.08); border-radius: 24px;
      padding: 48px; text-align: center; max-width: 420px; width: 90%; z-index: 10;
      box-shadow: 0 20px 40px rgba(0, 0, 0, 0.5);
    }}
    .badge {{ display: inline-block; background: {badge_bg}; color: {badge_color}; border: 1px solid {badge_border}; padding: 4px 12px; border-radius: 12px; font-size: 11px; margin-bottom: 16px; font-weight: 600; }}
    .title {{ font-size: 28px; font-weight: 700; margin-bottom: 12px; }}
    .desc {{ font-size: 14px; color: rgba(255,255,255,0.6); line-height: 1.6; margin-bottom: 24px; }}
    .btn {{ display: inline-block; border: 1px solid rgba(255,255,255,0.15); color: #fff; padding: 12px 32px; border-radius: 10px; text-decoration: none; font-weight: 600; }}
  </style>
</head>
<body>
  <div class="glow-orb orb-1"></div>
  <div class="glow-orb orb-2"></div>
  <div class="container">
    <div class="badge">{badge_text}</div>
    <div class="title">{title_text}</div>
    <div class="desc">{desc_text}</div>
    <a href="https://meloscribe.dev" class="btn">Go to meloscribe.dev</a>
  </div>
</body>
</html>""")
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)

# -------------------------------------------------------------------
# Suggestions endpoints (Windows Proxy vs direct SQLite Server handlers)
# -------------------------------------------------------------------
VM_API_BASE = "https://api.meloscribe.dev"

def get_proxy_headers():
    from shared import get_server_api_key
    headers = {}
    api_key = get_server_api_key()
    if api_key:
        headers["X-Meloscribe-Key"] = api_key
    return headers

@router.get("/api/public/suggestions")
def get_suggestions():
    if platform.system() == "Windows":
        try:
            r = requests.get(f"{VM_API_BASE}/api/public/suggestions", headers=get_proxy_headers(), timeout=5.0)
            return JSONResponse(content=r.json(), status_code=r.status_code)
        except Exception as e:
            return JSONResponse(content={"error": f"Proxy error: {e}"}, status_code=500)
    else:
        try:
            conn = sqlite3.connect(str(db_path), timeout=30.0)
            c = conn.cursor()
            c.execute("""
                CREATE TABLE IF NOT EXISTS suggestions (
                    id TEXT PRIMARY KEY,
                    title TEXT,
                    artist TEXT,
                    votes INTEGER DEFAULT 0,
                    created_at TEXT
                )
            """)
            conn.commit()
            c.execute("SELECT id, title, artist, votes, created_at FROM suggestions ORDER BY votes DESC, created_at DESC")
            rows = [{"id": r[0], "title": r[1], "artist": r[2], "votes": r[3], "created_at": r[4]} for r in c.fetchall()]
            conn.close()
            return rows
        except Exception as e:
            return JSONResponse(content={"error": str(e)}, status_code=500)

@router.post("/api/public/suggestions")
def create_suggestion(sug: NewSuggestion):
    if platform.system() == "Windows":
        try:
            r = requests.post(f"{VM_API_BASE}/api/public/suggestions", json=sug.dict(), headers=get_proxy_headers(), timeout=5.0)
            return JSONResponse(content=r.json(), status_code=r.status_code)
        except Exception as e:
            return JSONResponse(content={"error": f"Proxy error: {e}"}, status_code=500)
    else:
        import uuid
        from datetime import datetime
        sug_id = str(uuid.uuid4())
        created_at = datetime.now().isoformat()
        try:
            conn = sqlite3.connect(str(db_path), timeout=30.0)
            c = conn.cursor()
            c.execute("SELECT id, title, artist, votes FROM suggestions WHERE LOWER(title) = ? AND LOWER(artist) = ?", (sug.title.strip().lower(), sug.artist.strip().lower()))
            existing = c.fetchone()
            if existing:
                new_votes = existing[3] + 1
                c.execute("UPDATE suggestions SET votes = ? WHERE id = ?", (new_votes, existing[0]))
                conn.commit()
                conn.close()
                return {"id": existing[0], "title": existing[1], "artist": existing[2], "votes": new_votes, "created_at": created_at}
                
            c.execute("INSERT INTO suggestions (id, title, artist, votes, created_at) VALUES (?, ?, ?, ?, ?)",
                      (sug_id, sug.title.strip(), sug.artist.strip(), 1, created_at))
            conn.commit()
            conn.close()
            return {"id": sug_id, "title": sug.title.strip(), "artist": sug.artist.strip(), "votes": 1, "created_at": created_at}
        except Exception as e:
            return JSONResponse(content={"error": str(e)}, status_code=500)

@router.post("/api/public/suggestions/{sug_id}/vote")
def upvote_suggestion(sug_id: str):
    if platform.system() == "Windows":
        try:
            r = requests.post(f"{VM_API_BASE}/api/public/suggestions/{sug_id}/vote", headers=get_proxy_headers(), timeout=5.0)
            return JSONResponse(content=r.json(), status_code=r.status_code)
        except Exception as e:
            return JSONResponse(content={"error": f"Proxy error: {e}"}, status_code=500)
    else:
        try:
            conn = sqlite3.connect(str(db_path), timeout=30.0)
            c = conn.cursor()
            c.execute("SELECT votes FROM suggestions WHERE id = ?", (sug_id,))
            row = c.fetchone()
            if not row:
                conn.close()
                return JSONResponse(content={"error": "Suggestion not found"}, status_code=404)
            new_votes = row[0] + 1
            c.execute("UPDATE suggestions SET votes = ? WHERE id = ?", (new_votes, sug_id))
            conn.commit()
            conn.close()
            return {"id": sug_id, "votes": new_votes}
        except Exception as e:
            return JSONResponse(content={"error": str(e)}, status_code=500)

@router.post("/api/public/suggestions/{sug_id}/unvote")
def downvote_suggestion(sug_id: str):
    if platform.system() == "Windows":
        try:
            r = requests.post(f"{VM_API_BASE}/api/public/suggestions/{sug_id}/unvote", headers=get_proxy_headers(), timeout=5.0)
            return JSONResponse(content=r.json(), status_code=r.status_code)
        except Exception as e:
            return JSONResponse(content={"error": f"Proxy error: {e}"}, status_code=500)
    else:
        try:
            conn = sqlite3.connect(str(db_path), timeout=30.0)
            c = conn.cursor()
            c.execute("SELECT votes FROM suggestions WHERE id = ?", (sug_id,))
            row = c.fetchone()
            if not row:
                conn.close()
                return JSONResponse(content={"error": "Suggestion not found"}, status_code=404)
            new_votes = max(0, row[0] - 1)
            c.execute("UPDATE suggestions SET votes = ? WHERE id = ?", (new_votes, sug_id))
            conn.commit()
            conn.close()
            return {"id": sug_id, "votes": new_votes}
        except Exception as e:
            return JSONResponse(content={"error": str(e)}, status_code=500)

# -------------------------------------------------------------------
# Public Stats (Windows Proxy vs direct SQLite Server handlers)
# -------------------------------------------------------------------
@router.get("/api/public/stats")
def get_public_stats(request: Request):
    if platform.system() == "Windows":
        try:
            r = requests.get(f"{VM_API_BASE}/api/public/stats", headers=get_proxy_headers(), timeout=5.0)
            return JSONResponse(content=r.json(), status_code=r.status_code)
        except Exception as e:
            return JSONResponse(content={"error": f"Proxy error: {e}"}, status_code=500)
    else:
        # Increment website views (only for unique daily IP hits)
        try:
            client_ip = request.headers.get("cf-connecting-ip") or request.headers.get("x-forwarded-for") or request.headers.get("x-real-ip") or request.client.host
            from datetime import date
            today_str = date.today().isoformat()
            
            # Housekeep old dates from memory
            for k in list(unique_visitors_today):
                if k[1] != today_str:
                    unique_visitors_today.discard(k)
            
            visitor_key = (client_ip, today_str)
            if visitor_key not in unique_visitors_today:
                unique_visitors_today.add(visitor_key)
                
                conn = sqlite3.connect(str(db_path), timeout=30.0)
                c = conn.cursor()
                c.execute("SELECT profile_views FROM channel_insights WHERE platform = ? AND date = ?", ("website", today_str))
                row = c.fetchone()
                if row:
                    c.execute("UPDATE channel_insights SET profile_views = profile_views + 1 WHERE platform = ? AND date = ?", ("website", today_str))
                else:
                    c.execute("INSERT INTO channel_insights (platform, date, followers, profile_views, website_clicks) VALUES (?, ?, 0, 1, 0)", ("website", today_str))
                conn.commit()
                conn.close()
        except Exception as e:
            print(f"[Stats Track] Error logging website visitor views: {e}")
            
        try:
            conn = sqlite3.connect(str(db_path))
            c = conn.cursor()
            c.execute("SELECT COUNT(*) FROM purchases")
            customers = c.fetchone()[0]
            
            c.execute("""
                SELECT SUM(followers) 
                FROM (
                    SELECT followers FROM channel_insights 
                    WHERE (platform, date) IN (
                        SELECT platform, MAX(date) FROM channel_insights GROUP BY platform
                    )
                )
            """)
            row = c.fetchone()
            db_followers = row[0] if (row and row[0] is not None) else 0
            
            downloads = 0
            try:
                c.execute("SELECT COUNT(*) FROM revenue")
                downloads = c.fetchone()[0]
            except Exception:
                pass
            conn.close()
            
            return {
                "customers": max(14, customers),
                "followers": max(75, db_followers),
                "downloads": max(14, downloads)
            }
        except Exception:
            return {"customers": 14, "followers": 75, "downloads": 14}

@router.post("/api/public/stats")
def update_public_stats(stats: StatsUpload):
    if platform.system() == "Windows":
        try:
            r = requests.post(f"{VM_API_BASE}/api/public/stats", json=stats.dict(), headers=get_proxy_headers(), timeout=5.0)
            return JSONResponse(content=r.json(), status_code=r.status_code)
        except Exception as e:
            return JSONResponse(content={"error": f"Proxy error: {e}"}, status_code=500)
    else:
        try:
            conn = sqlite3.connect(str(db_path), timeout=30.0)
            c = conn.cursor()
            from datetime import date
            today_str = date.today().isoformat()
            c.execute("DELETE FROM channel_insights WHERE platform = ? AND date = ?", ("all", today_str))
            c.execute("INSERT INTO channel_insights (platform, date, followers) VALUES (?, ?, ?)",
                      ("all", today_str, stats.followers))
            conn.commit()
            conn.close()
            print(f"[Stats Upload] Saved live followers count: {stats.followers}")
            return {"status": "success"}
        except Exception as e:
            return JSONResponse(content={"error": str(e)}, status_code=500)

# -------------------------------------------------------------------
# Video / Audio Preview streaming (Windows Proxy vs direct SQLite Server handlers)
# -------------------------------------------------------------------
@router.get("/api/public/preview-video")
def get_preview_video(song_name: str):
    clean_name = song_name
    for suffix in (" (Easy Version)", " (Easy)", "(Easy Version)", "(Easy)"):
        if clean_name.endswith(suffix):
            clean_name = clean_name[:-len(suffix)].strip()
            
    r2_account_id = settings.get("r2_account_id") or os.environ.get("R2_ACCOUNT_ID")
    r2_access_key = settings.get("r2_access_key") or settings.get("r2_access_key_id") or os.environ.get("R2_ACCESS_KEY_ID")
    r2_secret_key = settings.get("r2_secret_key") or settings.get("r2_secret_access_key") or os.environ.get("R2_SECRET_ACCESS_KEY")
    r2_bucket = settings.get("r2_bucket") or settings.get("r2_bucket_name", "meloscribe-sheets") or os.environ.get("R2_BUCKET_NAME", "meloscribe-sheets")

    if not r2_account_id or not r2_access_key or not r2_secret_key:
        print("[Preview Request] R2 credentials missing, using demo redirect fallback.")
        return {
            "download_url": f"https://example.com/demo-packages/{clean_name}/{clean_name}.mp4",
            "message": "Demo mode: R2 credentials are not configured"
        }

    try:
        s3 = boto3.client(
            's3',
            endpoint_url=f'https://{r2_account_id}.r2.cloudflarestorage.com',
            aws_access_key_id=r2_access_key,
            aws_secret_access_key=r2_secret_key,
            region_name='auto',
            config=boto3.session.Config(signature_version='s3v4')
        )

        file_key = f"{clean_name}/{clean_name}_preview.mp4"
        try:
            s3.head_object(Bucket=r2_bucket, Key=file_key)
        except Exception:
            file_key = f"{clean_name}/{clean_name}.mp4"
            try:
                s3.head_object(Bucket=r2_bucket, Key=file_key)
            except Exception as head_err:
                print(f"[Preview Video] Video key '{file_key}' not found in R2 bucket '{r2_bucket}'.")
                return JSONResponse(content={"error": f"Preview video '{file_key}' not found in R2"}, status_code=404)

        presigned_url = s3.generate_presigned_url(
            ClientMethod='get_object',
            Params={'Bucket': r2_bucket, 'Key': file_key},
            ExpiresIn=900
        )
        return {"download_url": presigned_url}
    except Exception as e:
        print(f"Failed to generate presigned R2 preview URL: {e}")
        return JSONResponse(content={"error": f"Failed to generate preview URL: {str(e)}"}, status_code=500)

@router.get("/api/public/video-stream")
def stream_preview_video(song_name: str, request: Request):
    if platform.system() == "Windows":
        try:
            req_headers = {}
            range_header = request.headers.get("range")
            if range_header:
                req_headers["range"] = range_header
            r = requests.get(f"{VM_API_BASE}/api/public/video-stream?song_name={song_name}", headers=req_headers, stream=True, timeout=15)
            
            def chunk_generator():
                try:
                    for chunk in r.iter_content(chunk_size=65536):
                        if chunk:
                            yield chunk
                finally:
                    r.close()
            resp_headers = {}
            for h in ("content-type", "content-length", "content-range", "accept-ranges", "etag"):
                if h in r.headers:
                    resp_headers[h] = r.headers[h]
            return StreamingResponse(chunk_generator(), status_code=r.status_code, headers=resp_headers)
        except Exception as e:
            return JSONResponse(content={"error": f"Proxy error: {e}"}, status_code=500)
    else:
        def get_local_video(name):
            local_path = f"/home/ubuntu/meloscribe/Scores/{name}_preview.mp4"
            if os.path.exists(local_path):
                print(f"[Preview Video] Serving local file: {local_path}")
                return FileResponse(local_path, media_type="video/mp4", headers={"Cache-Control": "public, max-age=86400"})
            return None

        res = get_preview_video(song_name)
        if isinstance(res, JSONResponse):
            fb = get_local_video(song_name)
            if fb:
                return fb
            return JSONResponse(content={"error": f"Preview video not available for '{song_name}'"}, status_code=404)
        if not isinstance(res, dict):
            fb = get_local_video(song_name)
            if fb:
                return fb
            return JSONResponse(content={"error": "Invalid preview video response"}, status_code=500)
        download_url = res.get("download_url")
        if not download_url or "example.com" in download_url:
            fb = get_local_video(song_name)
            if fb:
                return fb
            return JSONResponse(content={"error": f"Preview video not available for '{song_name}'"}, status_code=404)

        req_headers = {}
        range_header = request.headers.get("range")
        if range_header:
            req_headers["range"] = range_header

        try:
            r2_resp = requests.get(download_url, headers=req_headers, stream=True, timeout=15)
            if r2_resp.status_code >= 400:
                print(f"[Preview Video] R2 returned {r2_resp.status_code} for {download_url}. Trying local video.")
                fb = get_local_video(song_name)
                if fb:
                    return fb
                return JSONResponse(content={"error": f"Preview video not available for '{song_name}'"}, status_code=404)

            def chunk_generator():
                try:
                    for chunk in r2_resp.iter_content(chunk_size=65536):
                        if chunk:
                            yield chunk
                finally:
                    r2_resp.close()

            resp_headers = {}
            for h in ("content-type", "content-length", "content-range", "accept-ranges", "etag"):
                if h in r2_resp.headers:
                    resp_headers[h] = r2_resp.headers[h]
            resp_headers["Cache-Control"] = "public, max-age=86400"
            if "content-type" not in resp_headers:
                resp_headers["content-type"] = "video/mp4"

            return StreamingResponse(
                chunk_generator(),
                status_code=r2_resp.status_code,
                headers=resp_headers
            )
        except Exception as e:
            print(f"[Preview Video] Failed streaming from R2: {e}. Trying local video.")
            fb = get_local_video(song_name)
            if fb:
                return fb
            return JSONResponse(content={"error": f"Failed to stream video: {str(e)}"}, status_code=500)

@router.get("/api/public/audio-stream")
def stream_preview_audio(song_name: str, request: Request):
    if platform.system() == "Windows":
        try:
            req_headers = {}
            range_header = request.headers.get("range")
            if range_header:
                req_headers["range"] = range_header
            r = requests.get(f"{VM_API_BASE}/api/public/audio-stream?song_name={song_name}", headers=req_headers, stream=True, timeout=15)
            
            def chunk_generator():
                try:
                    for chunk in r.iter_content(chunk_size=65536):
                        if chunk:
                            yield chunk
                finally:
                    r.close()
            resp_headers = {}
            for h in ("content-type", "content-length", "content-range", "accept-ranges", "etag"):
                if h in r.headers:
                    resp_headers[h] = r.headers[h]
            return StreamingResponse(chunk_generator(), status_code=r.status_code, headers=resp_headers)
        except Exception as e:
            return JSONResponse(content={"error": f"Proxy error: {e}"}, status_code=500)
    else:
        def get_local_fallback():
            dest_mp3 = Path(r"C:\Dev\meloscribe-frontend\website\public\audio-previews") / f"{song_name}.mp3"
            if dest_mp3.exists():
                print(f"[Preview Audio] Serving local fallback: {dest_mp3}")
                return FileResponse(dest_mp3, media_type="audio/mpeg", headers={"Cache-Control": "public, max-age=86400"})
            return None

        r2_account_id = settings.get("r2_account_id") or os.environ.get("R2_ACCOUNT_ID")
        r2_access_key = settings.get("r2_access_key") or settings.get("r2_access_key_id") or os.environ.get("R2_ACCESS_KEY_ID")
        r2_secret_key = settings.get("r2_secret_key") or settings.get("r2_secret_access_key") or os.environ.get("R2_SECRET_ACCESS_KEY")
        r2_bucket = settings.get("r2_bucket") or settings.get("r2_bucket_name", "meloscribe-sheets") or os.environ.get("R2_BUCKET_NAME", "meloscribe-sheets")

        if not r2_account_id or not r2_access_key or not r2_secret_key:
            fb = get_local_fallback()
            if fb:
                return fb
            return JSONResponse(content={"error": "R2 credentials missing"}, status_code=500)

        try:
            s3 = boto3.client(
                's3',
                endpoint_url=f'https://{r2_account_id}.r2.cloudflarestorage.com',
                aws_access_key_id=r2_access_key,
                aws_secret_access_key=r2_secret_key,
                region_name='auto',
                config=boto3.session.Config(signature_version='s3v4')
            )
            clean_name = song_name
            for suffix in (" (Easy Version)", " (Easy)", "(Easy Version)", "(Easy)"):
                if clean_name.endswith(suffix):
                    clean_name = clean_name[:-len(suffix)].strip()
            file_key = f"{clean_name}/{clean_name}.mp3"
            
            try:
                s3.head_object(Bucket=r2_bucket, Key=file_key)
            except Exception:
                fb = get_local_fallback()
                if fb:
                    return fb
                return JSONResponse(content={"error": "Audio preview not found in R2"}, status_code=404)

            download_url = s3.generate_presigned_url(
                ClientMethod='get_object',
                Params={'Bucket': r2_bucket, 'Key': file_key},
                ExpiresIn=900
            )
        except Exception as e:
            print(f"Failed to generate presigned R2 audio preview URL: {e}")
            fb = get_local_fallback()
            if fb:
                return fb
            return JSONResponse(content={"error": str(e)}, status_code=500)

        req_headers = {}
        range_header = request.headers.get("range")
        if range_header:
            req_headers["range"] = range_header

        try:
            r2_resp = requests.get(download_url, headers=req_headers, stream=True, timeout=15)
            if r2_resp.status_code >= 400:
                fb = get_local_fallback()
                if fb:
                    return fb
                return JSONResponse(content={"error": "R2 stream failed"}, status_code=r2_resp.status_code)

            def chunk_generator():
                try:
                    for chunk in r2_resp.iter_content(chunk_size=65536):
                        if chunk:
                            yield chunk
                finally:
                    r2_resp.close()

            resp_headers = {}
            for h in ("content-type", "content-length", "content-range", "accept-ranges", "etag"):
                if h in r2_resp.headers:
                    resp_headers[h] = r2_resp.headers[h]
            resp_headers["Cache-Control"] = "public, max-age=86400"
            if "content-type" not in resp_headers:
                resp_headers["content-type"] = "audio/mpeg"

            return StreamingResponse(chunk_generator(), status_code=r2_resp.status_code, headers=resp_headers)
        except Exception as e:
            fb = get_local_fallback()
            if fb:
                return fb
            return JSONResponse(content={"error": str(e)}, status_code=500)

# -------------------------------------------------------------------
# Public Direct Free Downloads
# -------------------------------------------------------------------
@router.get("/api/public/download")
def public_free_download(song_id: str, type: str, request: Request):
    return public_free_download_internal(song_id, type, request)

def public_free_download_internal(song_id: str, type: str, request: Request):
    if type not in ("pdf", "zip", "midi", "midi_slow", "video", "video_slow"):
        return JSONResponse(content={"error": "Invalid download type"}, status_code=400)

    songs_path = r"c:\Dev\meloscribe-frontend\website\src\data\songs.json"
    if not os.path.exists(songs_path):
        songs_path = Path(__file__).resolve().parent / "songs.json"
    
    songs_list = []
    if os.path.exists(songs_path):
        with open(songs_path, "r", encoding="utf-8") as f:
            songs_list = json.load(f)

    target_song = None
    for song in songs_list:
        if str(song.get("id")) == str(song_id):
            target_song = song
            break

    if not target_song:
        return JSONResponse(content={"error": "Song not found"}, status_code=404)

    price_str = str(target_song.get("price", "")).strip().lower()
    is_free = False
    if not price_str or "free" in price_str or price_str.startswith("0") or price_str == "0" or "0 €" in price_str or "0$" in price_str:
        is_free = True

    if not is_free:
        return JSONResponse(content={"error": "This song is not free"}, status_code=403)

    song_name = target_song.get("title")

    r2_account_id = settings.get("r2_account_id") or os.environ.get("R2_ACCOUNT_ID")
    r2_access_key = settings.get("r2_access_key") or settings.get("r2_access_key_id") or os.environ.get("R2_ACCESS_KEY_ID")
    r2_secret_key = settings.get("r2_secret_key") or settings.get("r2_secret_access_key") or os.environ.get("R2_SECRET_ACCESS_KEY")
    r2_bucket = settings.get("r2_bucket") or settings.get("r2_bucket_name", "meloscribe-assets") or os.environ.get("R2_BUCKET_NAME", "meloscribe-assets")

    if not r2_account_id or not r2_access_key or not r2_secret_key:
        if type == "pdf":
            suffix = f"/{song_name}.pdf"
        elif type == "midi":
            suffix = f"/{song_name}.mid"
        elif type == "midi_slow":
            suffix = f"/{song_name} slow.mid"
        elif type == "video":
            suffix = f"/{song_name}.mp4"
        elif type == "video_slow":
            suffix = f"/{song_name} slow.mp4"
        else:
            suffix = " Full Package.zip"
        return {"download_url": f"https://example.com/demo-packages/{song_name}{suffix}"}

    try:
        if type == "pdf":
            file_key = f"{song_name}/{song_name}.pdf"
        elif type == "midi":
            file_key = f"{song_name}/{song_name}.mid"
        elif type == "midi_slow":
            file_key = f"{song_name}/{song_name} slow.mid"
        elif type == "video":
            file_key = f"{song_name}/{song_name}.mp4"
        elif type == "video_slow":
            file_key = f"{song_name}/{song_name} slow.mp4"
        else:
            file_key = f"{song_name} Full Package.zip"

        s3 = boto3.client(
            's3',
            endpoint_url=f'https://{r2_account_id}.r2.cloudflarestorage.com',
            aws_access_key_id=r2_access_key,
            aws_secret_access_key=r2_secret_key,
            region_name='auto',
            config=boto3.session.Config(signature_version='s3v4')
        )

        filename = file_key.split('/')[-1]
        presigned_url = s3.generate_presigned_url(
            ClientMethod='get_object',
            Params={
                'Bucket': r2_bucket,
                'Key': file_key,
                'ResponseContentDisposition': f'attachment; filename="{filename}"'
            },
            ExpiresIn=3600
        )
        return {"download_url": presigned_url}
    except Exception as e:
        print(f"Failed to generate free presigned url: {e}")
        return JSONResponse(content={"error": f"Failed to generate download URL: {str(e)}"}, status_code=500)

# -------------------------------------------------------------------
# OAuth Auth Callback URL
# -------------------------------------------------------------------
@router.get("/callback")
def oauth_callback(code: str, state: str = "fb"):
    """
    Handle authorization callback codes (for Facebook Graph / Threads APIs).
    Renders a premium success HTML block with micro-animations.
    """
    db_path = Path(__file__).resolve().parent / "analytics.db"
    conn = sqlite3.connect(str(db_path))
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS auth_codes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            state TEXT UNIQUE,
            code TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    c.execute("INSERT OR REPLACE INTO auth_codes (state, code) VALUES (?, ?)", (state, code))
    conn.commit()
    conn.close()
    
    print(f"[OAuth Callback] Successfully captured code for state '{state}': {code[:15]}...")
    
    return HTMLResponse(content=f"""
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Verbindung Erfolgreich | Meloscribe</title>
  <link rel="preconnect" href="https://fonts.googleapis.com" />
  <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;600;700;800&display=swap" rel="stylesheet" />
  <style>
    *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: 'Outfit', sans-serif;
      background: #05050a;
      min-height: 100vh;
      display: flex;
      align-items: center;
      justify-content: center;
      overflow: hidden;
      color: #fff;
    }}
    .glow-orb {{
      position: absolute; width: 600px; height: 600px; border-radius: 50%;
      filter: blur(140px); z-index: 0; pointer-events: none; opacity: 0.15;
    }}
    .orb-cyan {{ background: #00f5ff; top: -150px; left: -100px; }}
    .orb-pink {{ background: #ff2d92; bottom: -100px; right: -100px; }}
    .card {{
      position: relative; z-index: 10;
      background: rgba(255, 255, 255, 0.03);
      border: 1px solid rgba(0, 245, 255, 0.2);
      border-radius: 28px;
      padding: 56px;
      max-width: 440px;
      width: 90%;
      text-align: center;
      backdrop-filter: blur(24px) saturate(180%);
      box-shadow: 0 0 60px rgba(0,245,255,0.05), 0 30px 70px rgba(0,0,0,0.6);
      animation: zoomIn 0.5s cubic-bezier(0.16, 1, 0.3, 1) both;
    }}
    @keyframes zoomIn {{
      from {{ opacity: 0; transform: scale(0.96) translateY(20px); }}
      to   {{ opacity: 1; transform: scale(1) translateY(0); }}
    }}
    .icon-box {{
      width: 80px; height: 80px;
      margin: 0 auto 32px;
      background: rgba(0,245,255,0.06);
      border: 1px solid rgba(0,245,255,0.25);
      border-radius: 24px;
      display: flex; align-items: center; justify-content: center;
      box-shadow: 0 0 40px rgba(0,245,255,0.15);
      animation: rotateIcon 0.8s cubic-bezier(0.16, 1, 0.3, 1) 0.2s both;
    }}
    @keyframes rotateIcon {{
      from {{ transform: rotate(-15deg) scale(0.8); opacity: 0; }}
      to   {{ transform: rotate(0) scale(1); opacity: 1; }}
    }}
    .icon-box svg {{ width: 42px; height: 42px; }}
    .badge {{
      display: inline-flex; align-items: center; gap: 8px;
      background: rgba(0,245,212,0.1); border: 1px solid rgba(0,245,212,0.25);
      border-radius: 999px; padding: 6px 16px; font-size: 12px; color: #00f5d4;
      font-weight: 600; letter-spacing: 0.05em; margin-bottom: 24px;
      text-transform: uppercase;
    }}
    .title {{ font-size: 26px; font-weight: 700; color: #f8fafc; margin-bottom: 12px; }}
    .desc {{ font-size: 14px; color: rgba(255,255,255,0.5); line-height: 1.7; margin-bottom: 36px; }}
    .close-btn {{
      display: block; width: 100%; padding: 14px;
      background: linear-gradient(135deg, #00f5ff 0%, #ff2d92 100%);
      color: #000; text-decoration: none; border-radius: 12px;
      font-weight: 700; font-size: 14px; letter-spacing: 0.05em;
      transition: all 0.3s ease;
      box-shadow: 0 4px 20px rgba(0,245,255,0.25);
    }}
    .close-btn:hover {{
      transform: translateY(-2px);
      box-shadow: 0 8px 30px rgba(0,245,255,0.4);
    }}
  </style>
</head>
<body>
  <div class="glow-orb orb-cyan"></div>
  <div class="glow-orb orb-pink"></div>
  <div class="card">
    <div class="badge">Erfolgreich</div>
    <div class="icon-box">
      <svg viewBox="0 0 24 24" fill="none" stroke="#00f5ff" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/>
        <polyline points="22 4 12 14.01 9 11.01"/>
      </svg>
    </div>
    <h1 class="title">Kanal Verbunden</h1>
    <p class="desc">Dein Token für die Plattform <strong>{state.upper()}</strong> wurde erfolgreich erfasst. Du kannst dieses Browserfenster jetzt schließen und die Meloscribe Desktop App nutzen.</p>
    <a href="javascript:window.close();" class="close-btn">Fenster Schließen</a>
  </div>
</body>
</html>
""")
