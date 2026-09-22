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
from typing import Optional
from pathlib import Path
from pydantic import BaseModel
from fastapi import APIRouter, Request, HTTPException, BackgroundTasks
from fastapi.responses import JSONResponse, HTMLResponse, StreamingResponse, FileResponse, RedirectResponse
from starlette.concurrency import run_in_threadpool
from mail_verifier import validate_email_deliverability

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
    embedded: bool = False

class NewSuggestion(BaseModel):
    title: str
    artist: str

class StatsUpload(BaseModel):
    followers: int

class NotifySubscribeRequest(BaseModel):
    email: str
    song_id: Optional[str] = None
    song_title: Optional[str] = None
    difficulty: Optional[str] = "Original"
    locale: Optional[str] = "en"

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
        name_str = (buyer_name or "").strip()
        email_str = (email or "").strip()
        if name_str and email_str:
            text = f"Licensed to: {name_str} ({email_str})"
        elif name_str:
            text = f"Licensed to: {name_str}"
        elif email_str:
            text = f"Licensed to: {email_str}"
        else:
            text = "Licensed to: meloscribe customer"
        
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
            
        filter_str = f"drawtext=text='meloscribesheets.com':fontfile='{font_path}':fontcolor=white@0.25:fontsize=24:x=w-tw-30:y=30"
        
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

EMAIL_TEMPLATES = {
    "en": {
        "purchase_subject": "🎹 Your learning package for {song_name} is ready!",
        "gift_subject": "🎁 A music gift for you: {song_name} sheets!",
        "heading_purchase": "🎹 Your Sheets Are Ready!",
        "heading_gift": "🎁 A Music Gift for You!",
        "intro_gift": "Hey {buyer_name}!",
        "intro_purchase": "Hey!",
        "body_purchase": "Thanks for supporting my arrangements! Your learning package for <strong>{song_name}</strong> is ready to practice:",
        "body_gift": "You have received a learning package for <strong>{song_name}</strong> as a gift – ready to practice:",
        "action": "",
        "button": "Download Learning Package",
        "footer": "This download link is permanent – you can access it anytime.",
        "ps": "PS: Any issues with the files, or ideas to improve? Just hit reply – every feedback helps!",
        "happy_practicing": "Happy practicing,",
        "help_text": "Need help? Reply directly to this email or visit"
    },
    "de": {
        "purchase_subject": "🎹 Dein Lernpaket für {song_name} ist bereit!",
        "gift_subject": "🎁 Ein Musikgeschenk für dich: Noten für {song_name}!",
        "heading_purchase": "🎹 Deine Klaviernoten sind bereit!",
        "heading_gift": "🎁 Ein Musikgeschenk für dich!",
        "intro_gift": "Hallo {buyer_name}!",
        "intro_purchase": "Hey!",
        "body_purchase": "Danke für die Unterstützung meiner Arrangements! Dein Lernpaket für <strong>{song_name}</strong> steht bereit zum Üben:",
        "body_gift": "Du hast ein Lernpaket für <strong>{song_name}</strong> als Geschenk erhalten – bereit zum Üben:",
        "action": "",
        "button": "Lernpaket herunterladen",
        "footer": "Dieser Download-Link ist dauerhaft gültig – du kannst jederzeit darauf zugreifen.",
        "ps": "PS: Probleme mit den Dateien oder Ideen zur Verbesserung? Antworte einfach auf diese Mail – jedes Feedback hilft!",
        "happy_practicing": "Viel Spaß beim Üben,",
        "help_text": "Brauchst du Hilfe? Antworte direkt auf diese E-Mail oder besuche"
    },
    "fr": {
        "purchase_subject": "🎹 Votre pack musical pour {song_name} est prêt !",
        "gift_subject": "🎁 Un cadeau musical pour vous : partitions de {song_name} !",
        "heading_purchase": "🎹 Vos partitions sont prêtes !",
        "heading_gift": "🎁 Un cadeau musical pour vous !",
        "intro_gift": "Bonjour {buyer_name} !",
        "intro_purchase": "Bonjour !",
        "body_purchase": "Merci de soutenir mes arrangements ! Votre pack d'apprentissage pour <strong>{song_name}</strong> est prêt pour la pratique :",
        "body_gift": "Vous avez reçu un pack d'apprentissage pour <strong>{song_name}</strong> en cadeau – prêt pour la pratique :",
        "action": "",
        "button": "Télécharger le pack d'apprentissage",
        "footer": "Ce lien de téléchargement est permanent – vous pouvez y accéder à tout moment.",
        "ps": "PS : Un problème avec les fichiers ou une idée d'amélioration ? Répondez simplement à cet e-mail – chaque retour compte !",
        "happy_practicing": "Bonne pratique,",
        "help_text": "Besoin d'aide ? Répondez directement à cet e-mail ou visitez"
    },
    "es": {
        "purchase_subject": "🎹 ¡Tu paquete de música para {song_name} está listo!",
        "gift_subject": "🎁 Un regalo musical para ti: ¡partituras de {song_name}!",
        "heading_purchase": "🎹 ¡Tus partituras están listas!",
        "heading_gift": "🎁 ¡Un regalo musical para ti!",
        "intro_gift": "¡Hola {buyer_name}!",
        "intro_purchase": "¡Hola!",
        "body_purchase": "¡Gracias por apoyar mis arreglos! Tu paquete de aprendizaje para <strong>{song_name}</strong> está listo para practicar:",
        "body_gift": "¡Has recibido un paquete de aprendizaje para <strong>{song_name}</strong> como regalo – listo para practicar:",
        "action": "",
        "button": "Descargar paquete de aprendizaje",
        "footer": "Este enlace de descarga es permanente – puedes acceder en cualquier momento.",
        "ps": "PD: ¿Algún problema con los archivos o ideas para mejorar? Solo responde a este correo – ¡cada comentario ayuda!",
        "happy_practicing": "¡Disfruta practicando!,",
        "help_text": "¿Necesitas ayuda? Responde directamente a este correo o visita"
    },
    "it": {
        "purchase_subject": "🎹 Il tuo pacchetto musicale per {song_name} è pronto!",
        "gift_subject": "🎁 Un regalo musical per te: spartiti di {song_name}!",
        "heading_purchase": "🎹 I tuoi spartiti sono pronti!",
        "heading_gift": "🎁 Un regalo musical per te!",
        "intro_gift": "Ciao {buyer_name}!",
        "intro_purchase": "Ciao!",
        "body_purchase": "Grazie per supportare i miei arrangiamenti! Il tuo pacchetto di apprendimento per <strong>{song_name}</strong> è pronto per esercitarti:",
        "body_gift": "Hai ricevuto un pacchetto di apprendimento per <strong>{song_name}</strong> in regalo – pronto per esercitarti:",
        "action": "",
        "button": "Scarica pacchetto di apprendimento",
        "footer": "Questo link di download è permanente – puoi accedervi in qualsiasi momento.",
        "ps": "PS: Qualche problema con i file o idee per migliorare? Rispondi pure a questa email – ogni feedback è utile!",
        "happy_practicing": "Buon esercizio,",
        "help_text": "Hai bisogno di aiuto? Rispondi direttamente a questa email o visita"
    }
}

def send_purchase_delivery_email(email: str, song_name: str, download_hash: str, locale: str = "en", is_gift: bool = False, buyer_name: str = ""):
    api_key = load_settings().get("resend_api_key") or settings.get("resend_api_key", "")
    if not api_key:
        log_webhook("[Notify] WARNING: resend_api_key not set in settings.json. Skipping purchase email.")
        return False
        
    download_url = f"https://meloscribesheets.com/order/{download_hash}"
    
    lang = locale.lower()[:2] if locale else "en"
    if lang not in EMAIL_TEMPLATES:
        lang = "en"
        
    tpl = EMAIL_TEMPLATES[lang]
    
    subject = tpl["gift_subject"].format(song_name=song_name) if is_gift else tpl["purchase_subject"].format(song_name=song_name)
    heading = tpl["heading_gift"] if is_gift else tpl["heading_purchase"]
    
    if is_gift and buyer_name:
        intro = tpl["intro_gift"].format(buyer_name=buyer_name)
    else:
        intro = tpl["intro_purchase"]
        
    body = tpl["body_gift"].format(song_name=song_name) if is_gift else tpl["body_purchase"].format(song_name=song_name)
    
    html_body = f"""
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="font-family: 'Helvetica Neue', Arial, sans-serif; background: #0a0a0f; color: #e0e0e0; max-width: 520px; margin: 0 auto; padding: 32px 16px;">
  <div style="background: #12121c; border: 1px solid #2a2a3e; border-radius: 16px; padding: 32px;">
    <h2 style="color: #ffffff; font-size: 20px; margin-top: 0; margin-bottom: 20px; font-weight: 700; text-align: center;">{heading}</h2>
    <p style="color: #b0b0c0; line-height: 1.7; font-size: 15px; margin-top: 0; margin-bottom: 12px;">{intro}</p>
    <p style="color: #b0b0c0; line-height: 1.7; font-size: 15px; margin-top: 0; margin-bottom: 24px;">{body}</p>
    
    <div style="text-align: center; margin: 24px 0 16px 0;">
      <a href="{download_url}" style="display: inline-block; background-color: #12121c; border: 2px solid #00f5d4; color: #00f5d4; font-family: 'Helvetica Neue', Arial, sans-serif; font-weight: 700; font-size: 15px; padding: 14px 32px; border-radius: 10px; text-decoration: none; text-shadow: 0 0 8px rgba(0,245,212,0.35);">{tpl["button"]}</a>
    </div>
    
    <p style="color: #888899; font-size: 13px; text-align: center; margin-top: 0; margin-bottom: 28px;">
      {tpl["footer"]}
    </p>
    
    <p style="color: #b0b0c0; line-height: 1.6; font-size: 14px; margin-top: 0; margin-bottom: 24px;">
      {tpl["ps"]}
    </p>
    
    <p style="color: #b0b0c0; line-height: 1.6; font-size: 15px; margin-top: 24px; margin-bottom: 0;">
      {tpl["happy_practicing"]}<br><br>
      meloscribe
    </p>
  </div>
  <p style="text-align: center; font-size: 11px; color: #555; margin-top: 24px;">
    {tpl["help_text"]} <a href="https://meloscribesheets.com" style="color: #00f5d4;">meloscribesheets.com</a>
  </p>
</body>
</html>
"""

    plain_body = body.replace("<strong>", "").replace("</strong>", "")
    text_body = f"""{heading}

{intro}

{plain_body}

{download_url}

{tpl["footer"]}

{tpl["ps"]}

{tpl["happy_practicing"]}

meloscribe

{tpl["help_text"]} https://meloscribesheets.com
"""

    try:
        resp = requests.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "from": "meloscribe <info@meloscribe.dev>",
                "to": [email],
                "reply_to": "info@meloscribe.dev",
                "subject": subject,
                "html": html_body,
                "text": text_body
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

def _send_confirmation_email(email: str, token: str, song_name: str = None, locale: str = "en"):
    api_key = load_settings().get("resend_api_key") or settings.get("resend_api_key", "")
    if not api_key:
        print("[Notify] WARNING: resend_api_key not set in settings.json. Skipping email.")
        return False
    
    confirm_url = f"https://api.meloscribe.dev/api/notify/confirm?token={token}"
    unsubscribe_url = f"https://api.meloscribe.dev/api/notify/unsubscribe?token={token}"
    
    if song_name:
        subject = f"Download your sheet music: {song_name} — meloscribe"
        message_intro = f"Here is the confirmation link for your sheet music arrangement of <strong>{song_name}</strong>."
        message_body = "Click the button below to confirm your request and download your files (PDF, MIDI, and video tutorials)."
        cta_text = f"Download {song_name}"
        disclaimer = "You will also receive notifications when new piano arrangements are released. You can unsubscribe at any time using the link below. If you didn't request this, you can safely ignore this email."
        plain_intro = f"Here is the confirmation link for your sheet music arrangement of {song_name}."
        plain_body = "Confirm your request and download your files (PDF, MIDI, and video tutorials) using the link below:"
    else:
        subject = "Confirm your subscription — meloscribe"
        message_intro = "Welcome to meloscribe!"
        message_body = "Please confirm your email address to receive notifications whenever new piano sheet music and tutorials are released."
        cta_text = "Confirm Subscription"
        disclaimer = "If you didn't request this, you can safely ignore this email. You will not be subscribed unless you click the link above."
        plain_intro = "Welcome to meloscribe!"
        plain_body = "Please confirm your email address to receive notifications whenever new piano sheet music and tutorials are released:"

    html_body = f"""
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background-color: #08090d; color: #e2e8f0; max-width: 520px; margin: 0 auto; padding: 32px 16px;">
  <div style="text-align: center; margin-bottom: 24px; background-color: #10131d; border: 1px solid #1e293b; border-radius: 16px; padding: 24px 16px;">
    <table align="center" border="0" cellpadding="0" cellspacing="0" style="margin: 0 auto; border-collapse: collapse;">
      <tr>
        <td style="font-size: 30px; font-weight: 900; color: #00f5ff; letter-spacing: 2px; text-transform: lowercase; padding: 0; text-align: right;">melo</td>
        <td style="font-size: 30px; font-weight: 900; color: #ff2d92; letter-spacing: 2px; text-transform: lowercase; padding: 0; text-align: left;">scribe</td>
      </tr>
    </table>
    <p style="color: #94a3b8; font-size: 13px; margin: 10px 0 0 0; font-style: italic;">Arranged by ear. Played by you.</p>
  </div>
  <div style="background-color: #10131d; border: 1px solid #1e293b; border-radius: 16px; padding: 32px;">
    <p style="color: #ffffff; font-size: 16px; font-weight: 600; margin-top: 0;">Hi,</p>
    <p style="color: #cbd5e1; line-height: 1.7; font-size: 15px;">
      {message_intro} {message_body}
    </p>
    <div style="text-align: center; margin: 28px 0;">
      <a href="{confirm_url}" style="display: inline-block; background-color: #00f5d4; color: #050508; font-weight: 700; font-size: 15px; padding: 14px 32px; border-radius: 10px; text-decoration: none;">{cta_text}</a>
    </div>
    <p style="color: #94a3b8; font-size: 13px; text-align: center; line-height: 1.6; margin-bottom: 0;">
      {disclaimer}
    </p>
  </div>
  <p style="text-align: center; font-size: 12px; color: #94a3b8; margin-top: 24px; line-height: 1.6;">
    meloscribe • Arranged by ear. Played by you.<br>
    <a href="https://meloscribesheets.com" style="color: #94a3b8; text-decoration: underline;">meloscribesheets.com</a> • <a href="{unsubscribe_url}" style="color: #94a3b8; text-decoration: underline;">Unsubscribe</a>
  </p>
</body>
</html>
"""

    text_body = f"""Hi,

{plain_intro}
{plain_body}

{confirm_url}

{disclaimer}

meloscribe • Arranged by ear. Played by you.
https://meloscribesheets.com

Unsubscribe:
{unsubscribe_url}
"""

    try:
        resp = requests.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "from": "meloscribe <info@meloscribe.dev>",
                "to": [email],
                "subject": subject,
                "html": html_body,
                "text": text_body,
                "headers": {
                    "List-Unsubscribe": f"<{unsubscribe_url}>",
                    "List-Unsubscribe-Post": "List-Unsubscribe=One-Click"
                }
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

# In-memory cache for IP to currency mappings
ip_currency_cache = {}

def get_currency_from_country(country: str) -> str:
    eurozone = {"AT", "BE", "CY", "EE", "FI", "FR", "DE", "GR", "HR", "IE", "IT", "LV", "LT", "LU", "MT", "NL", "PT", "SK", "SI", "ES"}
    if country in eurozone:
        return "eur"
    elif country == "GB":
        return "gbp"
    else:
        return "usd"

def get_currency_from_request(request: Request) -> str:
    # 1. Check Cloudflare country header first
    country = request.headers.get("cf-ipcountry")
    if country:
        country = country.upper().strip()
        print(f"[GeoIP] Resolved country via CF-IPCountry: {country}")
        return get_currency_from_country(country)

    # 2. Extract real client IP
    client_ip = request.headers.get("cf-connecting-ip") or request.headers.get("x-forwarded-for") or request.headers.get("x-real-ip") or (request.client.host if request.client else None)
    if client_ip:
        if "," in client_ip:
            client_ip = client_ip.split(",")[0].strip()
        
        # Avoid local/internal IPs
        if client_ip in ("127.0.0.1", "localhost", "::1") or client_ip.startswith("192.168.") or client_ip.startswith("10."):
            return "eur"

        # Check in-memory cache
        global ip_currency_cache
        if client_ip in ip_currency_cache:
            return ip_currency_cache[client_ip]

        # Query high-speed GeoIP API
        try:
            r = requests.get(f"http://ip-api.com/json/{client_ip}", timeout=1.5)
            if r.status_code == 200:
                data = r.json()
                country = data.get("countryCode", "").upper()
                if country:
                    currency = get_currency_from_country(country)
                    ip_currency_cache[client_ip] = currency
                    print(f"[GeoIP] Resolved country via API for IP {client_ip}: {country} -> {currency}")
                    return currency
        except Exception as e:
            print(f"[GeoIP] Error resolving IP {client_ip}: {e}")

    return "eur"

# -------------------------------------------------------------------
# Checkout session & Webhooks
# -------------------------------------------------------------------
@router.post("/api/checkout/create-session")
async def create_checkout_session(req: CheckoutRequest, request: Request):
    client_ip = request.headers.get("cf-connecting-ip") or request.headers.get("x-forwarded-for") or request.headers.get("x-real-ip") or (request.client.host if request.client else "unknown")
    if "," in client_ip:
        client_ip = client_ip.split(",")[0].strip()
    if is_rate_limited(client_ip, "create_checkout_session", 25, 600):
        return JSONResponse(status_code=429, content={"error": "Too many checkout requests. Please wait a few minutes."})
    try:
        songs_path = r"c:\Dev\meloscribe-frontend\website\src\data\songs.json"
        if not os.path.exists(songs_path):
            songs_path = Path(__file__).resolve().parent / "songs.json"
            
        with open(songs_path, "r", encoding="utf-8") as f:
            songs_list = json.load(f)
            
        song = next((s for s in songs_list if str(s.get("id")) == str(req.songId) or (s.get("easyId") and str(s.get("easyId")) == str(req.songId))), None)
        if not song:
            raise HTTPException(status_code=404, detail="Song not found")

        if song and song.get("easyId") and str(song.get("easyId")) == str(req.songId):
            req.difficulty = "Easy"
            
        if song.get("paymentsDisabled") or song.get("hidden"):
            raise HTTPException(status_code=403, detail="Product is no longer available")
            
        price_str = song.get("price", "6 €")
        if req.difficulty == "Easy" and song.get("easyPrice"):
            price_str = song.get("easyPrice")
            
        if "$" in price_str:
            currency = "usd"
        elif "£" in price_str:
            currency = "gbp"
        else:
            currency = get_currency_from_request(request)
            
        try:
            digits = re.findall(r"\d+", price_str)
            if digits:
                amount_cents = int(digits[0]) * 100
            else:
                amount_cents = 600
        except Exception:
            amount_cents = 600
            
        download_hash = uuid.uuid4().hex
        origin = request.headers.get("origin") or "https://www.meloscribesheets.com"
        if origin in ("https://meloscribe.dev", "https://meloscribesheets.com"):
            origin = "https://www.meloscribesheets.com"
        elif origin == "https://www.meloscribe.dev":
            origin = "https://www.meloscribesheets.com"
        
        s_settings = load_settings()
        is_sandbox = s_settings.get("environment", "sandbox") == "sandbox"
        is_local_origin = any(origin.startswith(h) for h in ("http://localhost", "http://127.0.0.1"))
        use_sandbox = is_sandbox or (is_local_origin and bool(s_settings.get("stripe_sandbox_secret_key")))

        if use_sandbox:
            stripe.api_key = s_settings.get("stripe_sandbox_secret_key")
            publishable_key = s_settings.get("stripe_sandbox_publishable_key")
        else:
            stripe.api_key = s_settings.get("stripe_live_secret_key") or get_stripe_api_key()
            publishable_key = s_settings.get("stripe_live_publishable_key")

        if not stripe.api_key:
            raise HTTPException(status_code=500, detail="Stripe API key is not configured")
            
        if req.difficulty == "Easy":
            product_name = f"{song.get('title')} (Easy Version)"
        else:
            product_name = f"{song.get('title')} (Original Version)"
        product_desc = "Includes PDF Sheet Music, MIDI Files, and Practice Video Tutorials"
        
        cover_image_path = song.get("coverImage", "")
        product_image = None
        if cover_image_path:
            import urllib.parse
            quoted_path = urllib.parse.quote(cover_image_path)
            product_image = f"https://www.meloscribesheets.com{quoted_path}"
            
        def to_slug(text):
            s = text.lower()
            s = re.sub(r'[^a-z0-9]+', '-', s)
            s = re.sub(r'(^-|-$)', '', s)
            return s

        song_name_meta = song.get("title")
        if req.difficulty == "Easy":
            song_name_meta = f"{song_name_meta} Easy"

        excluded_methods = [
            "amazon_pay",
            "bancontact",
            "blik",
            "kakao_pay",
            "naver_pay",
            "payco",
            "mb_way",
            "satispay"
        ]

        branding = {
            "background_color": "#0B0F17",
            "button_color": "#00F5FF",
            "font_family": "inter",
            "border_style": "rounded"
        }

        if req.embedded:
            # Payment methods: Card, PayPal for all currencies; iDEAL and EPS only for EUR; Bancontact only for BE
            if currency == "eur":
                pm_types = ["card", "paypal", "ideal", "eps"]
                cf_country = (request.headers.get("cf-ipcountry") or "").upper().strip()
                if cf_country == "BE":
                    pm_types.append("bancontact")
            else:
                pm_types = ["card", "paypal"]

            intent = stripe.PaymentIntent.create(
                amount=amount_cents,
                currency=currency,
                payment_method_types=pm_types,
                metadata={
                    "song_title": song_name_meta,
                    "download_hash": download_hash,
                    "locale": req.language
                },
                description=f"Meloscribe - {product_name}"
            )
            return {
                "clientSecret": intent.client_secret,
                "publishableKey": publishable_key,
                "paymentIntentId": intent.id
            }
        else:
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
                allow_promotion_codes=True,
                billing_address_collection="auto",
                branding_settings=branding,
                excluded_payment_method_types=excluded_methods,
                success_url=f"{origin}/success?checkout_id={{CHECKOUT_SESSION_ID}}",
                cancel_url=f"{origin}/sheets?song={to_slug(song.get('title', ''))}&version={to_slug(req.difficulty)}",
                metadata={
                    "song_title": song_name_meta,
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
            if platform.system() != "Windows":
                # Enforce signature verification on production server
                log_webhook("[Stripe Webhook] ERROR: stripe_webhook_secret is missing. Aborting payload handling.")
                return JSONResponse(status_code=400, content={"error": "Stripe Webhook Secret not configured."})
                
            log_webhook("[Stripe Webhook] WARNING: stripe_webhook_secret not set. Proceeding without signature verification (Development).")
            event = stripe.Event.construct_from(json.loads(payload.decode('utf-8')), stripe.api_key)
        log_webhook(f"Signature verified successfully. Event type: {event.type}")
    except Exception as e:
        log_webhook(f"Signature verification failed: {e}")
        return JSONResponse(status_code=400, content={"error": str(e)})

    event_type = event.type
    data_object_raw = event.data.object
    data_object = data_object_raw.to_dict() if hasattr(data_object_raw, "to_dict") else data_object_raw

    try:
        if event_type in ("checkout.session.completed", "payment_intent.succeeded"):
            is_pi = event_type == "payment_intent.succeeded"
            session_id = data_object.get("id")
            
            if is_pi:
                is_paid = data_object.get("status") == "succeeded"
            else:
                payment_status = data_object.get("payment_status")
                is_paid = payment_status == "paid"
                
            log_webhook(f"Processing {event_type}. Status: {data_object.get('status') if is_pi else data_object.get('payment_status')}. ID: {session_id}")
            
            if is_paid:
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

                if is_pi:
                    email = data_object.get("receipt_email")
                    buyer_name = ""
                    charges_obj = data_object.get("charges")
                    charges = charges_obj.get("data", []) if isinstance(charges_obj, dict) else []
                    if charges:
                        ch = charges[0]
                        billing = ch.get("billing_details") or {}
                        if not email:
                            email = billing.get("email") or ch.get("receipt_email")
                        if not email:
                            pm_details = ch.get("payment_method_details") or {}
                            email = pm_details.get("paypal", {}).get("payer_email")
                        if not buyer_name:
                            buyer_name = billing.get("name") or ""

                    latest_charge = data_object.get("latest_charge")
                    if (not email or "@" not in email) and latest_charge and stripe:
                        try:
                            ch = stripe.Charge.retrieve(latest_charge)
                            ch_dict = ch.to_dict() if hasattr(ch, "to_dict") else ch
                            billing = ch_dict.get("billing_details") or {}
                            email = ch_dict.get("receipt_email") or billing.get("email")
                            if not email:
                                pm_details = ch_dict.get("payment_method_details") or {}
                                email = pm_details.get("paypal", {}).get("payer_email")
                            if not buyer_name:
                                buyer_name = billing.get("name") or ""
                        except Exception as ch_err:
                            log_webhook(f"[Stripe Webhook] Error fetching latest_charge {latest_charge}: {ch_err}")

                    if (not email or "@" not in email) and data_object.get("customer") and stripe:
                        try:
                            cust = stripe.Customer.retrieve(data_object.get("customer"))
                            cust_dict = cust.to_dict() if hasattr(cust, "to_dict") else cust
                            email = cust_dict.get("email")
                            if not buyer_name:
                                buyer_name = cust_dict.get("name") or ""
                        except Exception:
                            pass

                    email = email or "customer@example.com"
                    amount_total = float(data_object.get("amount", 0)) / 100.0
                else:
                    customer_details = data_object.get("customer_details") or {}
                    email = customer_details.get("email") or data_object.get("customer_email") or "customer@example.com"
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
        
    if checkout_id.startswith("cs_") or checkout_id.startswith("pi_"):
        try:
            s_settings = load_settings()
            is_sandbox = s_settings.get("environment", "sandbox") == "sandbox"
            sandbox_key = s_settings.get("stripe_sandbox_secret_key")
            live_key = get_stripe_api_key() or s_settings.get("stripe_live_secret_key")

            def retrieve_stripe_object(key):
                if not key:
                    raise ValueError("No Stripe key available")
                stripe.api_key = key
                if checkout_id.startswith("pi_"):
                    return "pi", stripe.PaymentIntent.retrieve(checkout_id)
                else:
                    return "cs", stripe.checkout.Session.retrieve(checkout_id)

            primary_key = sandbox_key if (is_sandbox or checkout_id.startswith("cs_test_") or "_test_" in checkout_id) else (live_key or sandbox_key)
            fallback_key = live_key if primary_key == sandbox_key else sandbox_key

            obj_type = None
            obj = None
            try:
                obj_type, obj = retrieve_stripe_object(primary_key)
            except Exception:
                if fallback_key and fallback_key != primary_key:
                    obj_type, obj = retrieve_stripe_object(fallback_key)

            if obj:
                if obj_type == "pi":
                    pi = obj.to_dict() if hasattr(obj, "to_dict") else obj
                    is_paid = pi.get("status") == "succeeded"
                    metadata = pi.get("metadata") or {}
                    amount_total = float(pi.get("amount") or 0) / 100.0
                    currency = (pi.get("currency") or "eur").upper()
                    email = pi.get("receipt_email")
                    buyer_name = ""
                    charges_obj = pi.get("charges")
                    charges = charges_obj.get("data", []) if isinstance(charges_obj, dict) else []
                    if charges:
                        ch = charges[0]
                        billing = ch.get("billing_details") or {}
                        if not email:
                            email = billing.get("email") or ch.get("receipt_email")
                        if not email:
                            pm_details = ch.get("payment_method_details") or {}
                            email = pm_details.get("paypal", {}).get("payer_email")
                        if not buyer_name:
                            buyer_name = billing.get("name") or ""

                    latest_charge = pi.get("latest_charge")
                    if (not email or "@" not in email) and latest_charge and stripe:
                        try:
                            ch = stripe.Charge.retrieve(latest_charge)
                            ch_dict = ch.to_dict() if hasattr(ch, "to_dict") else ch
                            billing = ch_dict.get("billing_details") or {}
                            email = ch_dict.get("receipt_email") or billing.get("email")
                            if not email:
                                pm_details = ch_dict.get("payment_method_details") or {}
                                email = pm_details.get("paypal", {}).get("payer_email")
                            if not buyer_name:
                                buyer_name = billing.get("name") or ""
                        except Exception as ch_err:
                            print(f"[Order Details] Error fetching latest_charge {latest_charge}: {ch_err}")

                    if (not email or "@" not in email) and pi.get("customer") and stripe:
                        try:
                            cust = stripe.Customer.retrieve(pi.get("customer"))
                            cust_dict = cust.to_dict() if hasattr(cust, "to_dict") else cust
                            email = cust_dict.get("email")
                            if not buyer_name:
                                buyer_name = cust_dict.get("name") or ""
                        except Exception:
                            pass

                    email = email or "customer@example.com"
                else:
                    session = obj.to_dict() if hasattr(obj, "to_dict") else obj
                    is_paid = session.get("payment_status") == "paid"
                    metadata = session.get("metadata") or {}
                    amount_total = float(session.get("amount_total") or 0) / 100.0
                    currency = (session.get("currency") or "eur").upper()
                    customer_details = session.get("customer_details") or {}
                    email = customer_details.get("email") or session.get("customer_email") or "customer@example.com"
                    buyer_name = customer_details.get("name") or ""

                if is_paid:
                    song_title = metadata.get("song_title") or "Unknown Song"
                    download_hash = metadata.get("download_hash") or uuid.uuid4().hex
                    locale = metadata.get("locale") or "de"
                    
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
def get_order_details(hash: str, request: Request):
    client_ip = request.headers.get("cf-connecting-ip") or request.headers.get("x-forwarded-for") or request.headers.get("x-real-ip") or (request.client.host if request.client else "unknown")
    if "," in client_ip:
        client_ip = client_ip.split(",")[0].strip()
    if is_rate_limited(client_ip, "order_details", 60, 600):
        return JSONResponse(status_code=429, content={"error": "Too many requests. Please try again later."})
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
    client_ip = request.headers.get("cf-connecting-ip") or request.headers.get("x-forwarded-for") or request.headers.get("x-real-ip") or (request.client.host if request.client else "unknown")
    if "," in client_ip:
        client_ip = client_ip.split(",")[0].strip()
    if is_rate_limited(client_ip, "request_download", 60, 600):
        return JSONResponse(status_code=429, content={"error": "Too many download requests. Please try again later."})
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
    
    # For PDF, use proxy endpoint to apply watermark on demand.
    # For videos and MIDIs, generate direct presigned R2 URL instantly (1 ms) to make download start immediately!
    if type == "pdf":
        download_file_url = f"{request.base_url}api/download/file?hash={hash}&type={type}"
        return {"download_url": download_file_url, "download_count": new_count}
    else:
        r2_account_id = settings.get("r2_account_id") or os.environ.get("R2_ACCOUNT_ID")
        r2_access_key = settings.get("r2_access_key") or settings.get("r2_access_key_id") or os.environ.get("R2_ACCESS_KEY_ID")
        r2_secret_key = settings.get("r2_secret_key") or settings.get("r2_secret_access_key") or os.environ.get("R2_SECRET_ACCESS_KEY")
        r2_bucket = settings.get("r2_bucket") or settings.get("r2_bucket_name", "meloscribe-assets") or os.environ.get("R2_BUCKET_NAME", "meloscribe-assets")

        if r2_account_id and r2_access_key and r2_secret_key:
            try:
                import boto3, re
                from botocore.config import Config
                clean_base = re.sub(r'\s*\((Original|Easy|Easy Version|All Parts|Part \d+|Original / Easy)\)', '', song_name, flags=re.IGNORECASE).strip()
                clean_base = re.sub(r'\s+(Easy|Original)$', '', clean_base, flags=re.IGNORECASE).strip()
                is_easy = bool(re.search(r'\b(Easy|Easy Version)\b', song_name, re.IGNORECASE))
                r2_base = f"{clean_base} Easy" if is_easy else clean_base
                
                if type == "midi":
                    file_key = f"{r2_base}/{r2_base}.mid"
                elif type == "midi_slow":
                    file_key = f"{r2_base}/{r2_base} slow.mid"
                elif type == "video":
                    file_key = f"{r2_base}/{r2_base}.mp4"
                elif type == "video_slow":
                    file_key = f"{r2_base}/{r2_base} slow.mp4"
                else:
                    file_key = f"{r2_base} Full Package.zip"

                s3 = boto3.client(
                    's3',
                    endpoint_url=f'https://{r2_account_id}.r2.cloudflarestorage.com',
                    aws_access_key_id=r2_access_key,
                    aws_secret_access_key=r2_secret_key,
                    region_name='auto',
                    config=Config(signature_version='s3v4')
                )
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
                return {"download_url": presigned_url, "download_count": new_count}
            except Exception as e:
                print(f"[Download Request] Error generating presigned URL: {e}")

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
    r2_bucket = settings.get("r2_bucket") or settings.get("r2_bucket_name", "meloscribe-assets") or os.environ.get("R2_BUCKET_NAME", "meloscribe-assets")
    
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
        
        import re
        clean_base = re.sub(r'\s*\((Original|Easy|Easy Version|All Parts|Part \d+|Original / Easy)\)', '', song_name, flags=re.IGNORECASE).strip()
        clean_base = re.sub(r'\s+(Easy|Original)$', '', clean_base, flags=re.IGNORECASE).strip()
        is_easy = bool(re.search(r'\b(Easy|Easy Version)\b', song_name, re.IGNORECASE))
        r2_base = f"{clean_base} Easy" if is_easy else clean_base

        if type == "pdf":
            file_key = f"{r2_base}/{r2_base}.pdf"
        elif type == "midi":
            file_key = f"{r2_base}/{r2_base}.mid"
        elif type == "midi_slow":
            file_key = f"{r2_base}/{r2_base} slow.mid"
        elif type == "video":
            file_key = f"{r2_base}/{r2_base}.mp4"
        elif type == "video_slow":
            file_key = f"{r2_base}/{r2_base} slow.mp4"
        else:
            file_key = f"{r2_base} Full Package.zip"
            
        s3 = boto3.client(
            's3',
            endpoint_url=f'https://{r2_account_id}.r2.cloudflarestorage.com',
            aws_access_key_id=r2_access_key,
            aws_secret_access_key=r2_secret_key,
            region_name='auto',
            config=Config(signature_version='s3v4')
        )
        
        # Verify key exists, or resolve dynamically by prefix and extension
        try:
            s3.head_object(Bucket=r2_bucket, Key=file_key)
        except Exception:
            print(f"[Download File] Key '{file_key}' not found directly, resolving dynamically under '{r2_base}/'...")
            ext = ".pdf" if type == "pdf" else (".mid" if "midi" in type else (".mp4" if "video" in type else ".zip"))
            is_slow = "slow" in type
            res_objs = s3.list_objects_v2(Bucket=r2_bucket, Prefix=f"{r2_base}/")
            found_key = None
            for obj in res_objs.get("Contents", []):
                k = obj["Key"]
                if k.endswith(ext):
                    if is_slow and ("slow" in k.lower() or "tuto" in k.lower()):
                        found_key = k
                        break
                    elif not is_slow and "slow" not in k.lower() and "tuto" not in k.lower() and "preview" not in k.lower():
                        found_key = k
                        break
            if found_key:
                print(f"[Download File] Resolved alternate R2 key: '{found_key}'")
                file_key = found_key

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
    c.execute("SELECT song_name FROM purchases WHERE transaction_id = ? AND (LOWER(status) = 'completed' OR LOWER(status) LIKE '%active%')", (checkout_id,))
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

        import re
        clean_base = re.sub(r'\s*\((Original|Easy|Easy Version|All Parts|Part \d+|Original / Easy)\)', '', song_name, flags=re.IGNORECASE).strip()
        clean_base = re.sub(r'\s+(Easy|Original)$', '', clean_base, flags=re.IGNORECASE).strip()
        is_easy = bool(re.search(r'\b(Easy|Easy Version)\b', song_name, re.IGNORECASE))
        r2_base = f"{clean_base} Easy" if is_easy else clean_base

        file_specs = [
            {"key": f"{r2_base}/{r2_base}.pdf",       "label": "Sheet Music (PDF)",          "type": "pdf"},
            {"key": f"{r2_base}/{r2_base}.mid",       "label": "MIDI – Normal Speed",         "type": "midi"},
            {"key": f"{r2_base}/{r2_base} slow.mid",  "label": "MIDI – Slow Practice",        "type": "midi"},
            {"key": f"{r2_base}/{r2_base}.mp4",       "label": "Practice Video – Normal Speed", "type": "video"},
            {"key": f"{r2_base}/{r2_base} slow.mp4",  "label": "Practice Video – Slow",       "type": "video"},
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
async def notify_subscribe(req: NotifySubscribeRequest, request: Request):
    email = req.email.strip().lower()
    
    # 1. Rate limit check (5 subscription requests per hour per IP)
    client_ip = request.headers.get("cf-connecting-ip") or request.headers.get("x-forwarded-for") or request.headers.get("x-real-ip") or (request.client.host if request.client else "unknown")
    if "," in client_ip:
        client_ip = client_ip.split(",")[0].strip()
    if is_rate_limited(client_ip, "notify_subscribe", 5, 3600):
        return JSONResponse(content={"error": "Too many subscription attempts. Please wait a while."}, status_code=429)
    
    # 2. Async threadpool execution for email deliverability & anti-abuse validation
    is_valid, err_msg = await run_in_threadpool(validate_email_deliverability, email)
    if not is_valid:
        return JSONResponse(content={"error": err_msg}, status_code=400)
        
    token = uuid.uuid4().hex
    song_name = req.song_title.strip() if req.song_title else None
    song_id = req.song_id.strip() if req.song_id else None
    locale = req.locale.strip() if req.locale else "en"
    
    try:
        conn = sqlite3.connect(str(db_path), timeout=30.0)
        c = conn.cursor()
        
        # Ensure schema has song_id, song_name, locale columns
        try:
            c.execute("ALTER TABLE notify_subscribers ADD COLUMN song_id TEXT")
        except Exception:
            pass
        try:
            c.execute("ALTER TABLE notify_subscribers ADD COLUMN song_name TEXT")
        except Exception:
            pass
        try:
            c.execute("ALTER TABLE notify_subscribers ADD COLUMN locale TEXT DEFAULT 'en'")
        except Exception:
            pass
            
        c.execute("SELECT status FROM notify_subscribers WHERE email = ?", (email,))
        row = c.fetchone()
        if row:
            if row[0] == "active":
                # Already verified active subscriber!
                # If a song was requested, create active purchase immediately so they can download it
                if song_name:
                    download_hash = uuid.uuid4().hex
                    c.execute("SELECT download_hash FROM purchases WHERE email = ? AND song_name = ?", (email, song_name))
                    p_row = c.fetchone()
                    if not p_row or not p_row[0]:
                        # Critical Rule: Pure string 'active' (NO emojis!)
                        c.execute(
                            "INSERT OR IGNORE INTO purchases (transaction_id, email, song_name, amount, currency, status, download_hash, locale, buyer_name) VALUES (?, ?, ?, 0.0, 'EUR', 'active', ?, ?, '')",
                            (f"free_{token}", email, song_name, download_hash, locale)
                        )
                        conn.commit()
                    else:
                        download_hash = p_row[0]
                    conn.close()
                    # Delivery-only via inbox: send single delivery email for already active subscriber
                    send_purchase_delivery_email(email, song_name, download_hash, locale)
                    return {"status": "active", "message": "Download link sent to your inbox!"}
                
                conn.close()
                return {"status": "already_active", "message": "This email is already subscribed."}
            else:
                c.execute(
                    "UPDATE notify_subscribers SET token = ?, status = 'pending', song_id = ?, song_name = ?, locale = ? WHERE email = ?",
                    (token, song_id, song_name, locale, email)
                )
        else:
            c.execute(
                "INSERT INTO notify_subscribers (email, token, status, song_id, song_name, locale) VALUES (?, ?, 'pending', ?, ?, ?)",
                (email, token, song_id, song_name, locale)
            )
        conn.commit()
        conn.close()
    except Exception as e:
        return JSONResponse(content={"error": f"Database error: {str(e)}"}, status_code=500)
    
    # Trigger single DOI confirmation email
    threading.Thread(target=lambda: _send_confirmation_email(email, token, song_name, locale), daemon=True).start()
    return {"status": "pending", "message": "Confirmation email sent. Please check your inbox."}

@router.get("/api/notify/confirm")
def notify_confirm(token: str):
    try:
        conn = sqlite3.connect(str(db_path), timeout=30.0)
        c = conn.cursor()
        c.execute("SELECT email, song_name, song_id, locale FROM notify_subscribers WHERE token = ?", (token,))
        sub_row = c.fetchone()
        
        if not sub_row:
            conn.close()
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
            
        email, song_name, song_id, locale = sub_row[0], sub_row[1], sub_row[2], sub_row[3] or "en"
        c.execute("UPDATE notify_subscribers SET status = 'active', confirmed_at = CURRENT_TIMESTAMP WHERE token = ?", (token,))
        conn.commit()
        
        # If this DOI confirmation was triggered for a free song, create an active purchase
        if song_name:
            download_hash = uuid.uuid4().hex
            c.execute("SELECT download_hash FROM purchases WHERE email = ? AND song_name = ?", (email, song_name))
            p_row = c.fetchone()
            if p_row and p_row[0]:
                download_hash = p_row[0]
            else:
                # Critical Rule: Pure string 'active' (NO emojis!)
                c.execute(
                    "INSERT OR IGNORE INTO purchases (transaction_id, email, song_name, amount, currency, status, download_hash, locale, buyer_name) VALUES (?, ?, ?, 0.0, 'EUR', 'active', ?, ?, '')",
                    (f"free_{token}", email, song_name, download_hash, locale)
                )
                conn.commit()
            conn.close()
            
            # Quota-saving 302 Redirect directly to /order/{download_hash}: Zero second email!
            frontend_url = "https://meloscribesheets.com" if platform.system() != "Windows" else "http://localhost:5173"
            return RedirectResponse(url=f"{frontend_url}/order/{download_hash}", status_code=302)
            
        conn.close()
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
    <div class="desc">You'll be notified when new sheet music and practice assets drop on meloscribesheets.com.</div>
    <a href="https://meloscribesheets.com" class="btn">Go to meloscribesheets.com</a>
  </div>
</body>
</html>""")
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)

@router.api_route("/api/notify/unsubscribe", methods=["GET", "POST"])
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
    <a href="https://meloscribesheets.com" class="btn">Go to meloscribesheets.com</a>
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
            data = r.json()
            if r.status_code == 200 and isinstance(data, list):
                try:
                    conn = sqlite3.connect(str(db_path), timeout=5.0)
                    c = conn.cursor()
                    c.execute("""
                        CREATE TABLE IF NOT EXISTS suggestions (
                            id TEXT PRIMARY KEY,
                            title TEXT NOT NULL,
                            artist TEXT NOT NULL,
                            votes INTEGER DEFAULT 1,
                            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                            status TEXT DEFAULT 'open'
                        )
                    """)
                    for sug in data:
                        c.execute("""
                            INSERT OR REPLACE INTO suggestions (id, title, artist, votes, created_at, status)
                            VALUES (?, ?, ?, ?, ?, ?)
                        """, (sug.get("id"), sug.get("title"), sug.get("artist"), sug.get("votes", 1), sug.get("created_at"), sug.get("status", "open")))
                    conn.commit()
                    conn.close()
                except Exception as sync_e:
                    print(f"[Proxy] Error syncing suggestions to local db: {sync_e}")
            return JSONResponse(content=data, status_code=r.status_code)
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
                    created_at TEXT,
                    status TEXT DEFAULT 'open'
                )
            """)
            try:
                c.execute("ALTER TABLE suggestions ADD COLUMN status TEXT DEFAULT 'open'")
            except Exception:
                pass
            conn.commit()
            c.execute("SELECT id, title, artist, votes, created_at, COALESCE(status, 'open') FROM suggestions ORDER BY votes DESC, created_at DESC")
            rows = [{"id": r[0], "title": r[1], "artist": r[2], "votes": r[3], "created_at": r[4], "status": r[5]} for r in c.fetchall()]
            conn.close()
            return rows
        except Exception as e:
            return JSONResponse(content={"error": str(e)}, status_code=500)

@router.post("/api/public/suggestions")
def create_suggestion(sug: NewSuggestion, request: Request):
    if platform.system() == "Windows":
        try:
            r = requests.post(f"{VM_API_BASE}/api/public/suggestions", json=sug.dict(), headers=get_proxy_headers(), timeout=5.0)
            return JSONResponse(content=r.json(), status_code=r.status_code)
        except Exception as e:
            return JSONResponse(content={"error": f"Proxy error: {e}"}, status_code=500)
    else:
        client_ip = request.headers.get("cf-connecting-ip") or request.headers.get("x-forwarded-for") or request.headers.get("x-real-ip") or request.client.host
        if is_rate_limited(client_ip, "create_suggestion", 5, 3600):
            return JSONResponse(content={"error": "Too many requests. Please wait before suggesting more songs."}, status_code=429)
            
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
def upvote_suggestion(sug_id: str, request: Request):
    if platform.system() == "Windows":
        try:
            r = requests.post(f"{VM_API_BASE}/api/public/suggestions/{sug_id}/vote", headers=get_proxy_headers(), timeout=5.0)
            return JSONResponse(content=r.json(), status_code=r.status_code)
        except Exception as e:
            return JSONResponse(content={"error": f"Proxy error: {e}"}, status_code=500)
    else:
        client_ip = request.headers.get("cf-connecting-ip") or request.headers.get("x-forwarded-for") or request.headers.get("x-real-ip") or request.client.host
        if is_rate_limited(client_ip, f"vote_{sug_id}", 20, 600):
            return JSONResponse(content={"error": "Too many votes. Please wait a few minutes."}, status_code=429)
            
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
def downvote_suggestion(sug_id: str, request: Request):
    if platform.system() == "Windows":
        try:
            r = requests.post(f"{VM_API_BASE}/api/public/suggestions/{sug_id}/unvote", headers=get_proxy_headers(), timeout=5.0)
            return JSONResponse(content=r.json(), status_code=r.status_code)
        except Exception as e:
            return JSONResponse(content={"error": f"Proxy error: {e}"}, status_code=500)
    else:
        client_ip = request.headers.get("cf-connecting-ip") or request.headers.get("x-forwarded-for") or request.headers.get("x-real-ip") or request.client.host
        if is_rate_limited(client_ip, f"vote_{sug_id}", 20, 600):
            return JSONResponse(content={"error": "Too many votes. Please wait a few minutes."}, status_code=429)
            
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

def annotate_trending_metrics(songs: list, db_file: Path) -> list:
    """
    Computes data-driven trending metrics for songs using 30-day velocity from analytics.db:
    Score = (purchases_30d * 100_000) + views_30d
    A song is eligible for trending ONLY IF:
    - it has >= 1 purchase in the last 30 days, OR
    - it has high recent view momentum (>= 50_000 views in the last 30 days)
    Top scoring eligible songs (max 3) receive trending = True.
    """
    if not db_file or not db_file.exists():
        return songs

    def norm_title(t: str) -> str:
        if not t:
            return ""
        t = t.lower()
        for sfx in [" (easy version)", " (easy)", " easy", " (original)", " original", " (all parts)", " (part 1)", " (part 2)"]:
            if t.endswith(sfx):
                t = t[:-len(sfx)].strip()
        return "".join(c for c in t if c.isalnum() or c.isspace()).strip()

    try:
        from datetime import datetime, timedelta
        conn = sqlite3.connect(str(db_file), timeout=5.0)
        c = conn.cursor()
        
        # 1. Real purchases in last 30 days
        p_cutoff = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
        c.execute("""
            SELECT song_name, COUNT(id) 
            FROM purchases 
            WHERE status NOT LIKE '%Refund%' AND status NOT LIKE '%refund%' AND status NOT LIKE '%failed%'
              AND created_at >= ?
            GROUP BY song_name
        """, (p_cutoff,))
        purchases_raw = c.fetchall()
        
        # 2. View momentum in last 30 days from snapshots
        v_cutoff = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
        views_raw = []
        try:
            c.execute("""
                WITH recent AS (
                    SELECT video_id, song_name, MAX(views) as max_v, MIN(views) as min_v
                    FROM snapshots
                    WHERE snapshot_date >= ?
                    GROUP BY video_id, song_name
                )
                SELECT song_name, SUM(max_v - min_v) as view_growth
                FROM recent
                GROUP BY song_name
                HAVING view_growth > 0
            """, (v_cutoff,))
            views_raw = c.fetchall()
        except Exception:
            pass # Fall back gracefully if snapshots table is not yet populated
            
        conn.close()

        # Build normalized maps
        purchases_map = {}
        for sname, cnt in purchases_raw:
            if sname:
                key = norm_title(sname)
                purchases_map[key] = purchases_map.get(key, 0) + (cnt or 0)
                
        views_map = {}
        for sname, vcnt in views_raw:
            if sname:
                key = norm_title(sname)
                views_map[key] = views_map.get(key, 0) + (vcnt or 0)

        now = datetime.now()
        is_holiday_season = now.month in (11, 12)
        holiday_keywords = ["carol of the bells", "silent night", "god rest ye merry", "we wish you a merry xmas"]

        # Score and qualify catalog items
        catalog_candidates = []
        for s in songs:
            if s.get("id") == "global_settings" or s.get("hidden"):
                continue
            title_lower = (s.get("title") or "").lower()
            t_key = norm_title(s.get("title", ""))
            p_cnt = purchases_map.get(t_key, 0)
            v_cnt = views_map.get(t_key, 0)
            
            # Holiday songs require actual purchases if outside of Nov/Dec
            is_holiday_track = any(kw in title_lower for kw in holiday_keywords)
            if is_holiday_track and not is_holiday_season and p_cnt == 0:
                continue

            score = (p_cnt * 100_000) + v_cnt
            catalog_candidates.append((s.get("id"), score))

        # Determine top 3 eligible IDs with score > 0
        catalog_candidates.sort(key=lambda x: x[1], reverse=True)
        top_trending_ids = {item[0] for item in catalog_candidates[:3] if item[1] > 0}

        for s in songs:
            is_trending = s.get("id") in top_trending_ids
            s["trending"] = is_trending
            s["isTrending"] = is_trending

    except Exception as e:
        print(f"[Trending Ranking] Warning: failed to compute trending: {e}")

    return songs

@router.get("/api/public/songs")
def get_public_songs(request: Request):
    if platform.system() == "Windows":
        try:
            r = requests.get(f"{VM_API_BASE}/api/public/songs", headers=get_proxy_headers(), timeout=5.0)
            if r.status_code == 200:
                return JSONResponse(content=r.json(), status_code=200)
        except Exception:
            pass # Fall back to local calculation if VM proxy is unavailable
            
    try:
        if platform.system() == "Windows":
            songs_path = Path(r"c:\Dev\meloscribe-frontend\website\src\data\songs.json")
            if not songs_path.exists():
                songs_path = Path(__file__).resolve().parent / "songs.json"
        else:
            songs_path = Path(__file__).resolve().parent / "songs.json"
            
        with open(songs_path, "r", encoding="utf-8") as f:
            songs_list = json.load(f)
            
        filtered_songs = [s for s in songs_list if s.get("id") != "global_settings"]
        
        # Dynamically compute and inject trending flags based on purchases & views from analytics.db
        annotate_trending_metrics(filtered_songs, db_path)
        
        currency = get_currency_from_request(request)
        for song in filtered_songs:
            price = song.get("price", "")
            if price and "€" in price:
                if currency == "usd":
                    song["price"] = price.replace("€", "$")
                elif currency == "gbp":
                    song["price"] = price.replace("€", "£")
            easy_price = song.get("easyPrice", "")
            if easy_price and "€" in easy_price:
                if currency == "usd":
                    song["easyPrice"] = easy_price.replace("€", "$")
                elif currency == "gbp":
                    song["easyPrice"] = easy_price.replace("€", "£")
                    
        return JSONResponse(
            content=filtered_songs,
            headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"}
        )
    except Exception as e:
        print(f"[Public Songs] Error: {e}")
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
    clean_name = re.sub(r'[/\\?%*:|"<>.]', '', song_name).strip()
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

        # Determine format of the song from catalog to decide file key type
        format_mode = "full_arrangement"
        songs_path = Path(__file__).resolve().parent / "songs.json"
        if not songs_path.exists():
            songs_path = Path(r"c:\Dev\meloscribe-frontend\website\src\data\songs.json")
        if songs_path.exists():
            try:
                with open(songs_path, "r", encoding="utf-8") as f:
                    catalog = json.load(f)
                    for s in catalog:
                        title = s.get("title", "")
                        slug = s.get("id", "")
                        if title.lower() == clean_name.lower() or slug.lower() == clean_name.lower():
                            format_mode = s.get("format", "full_arrangement")
                            break
            except Exception as e:
                print(f"Error reading format from catalog: {e}")

        file_key = f"{clean_name}/{clean_name}_preview.mp4"
        try:
            s3.head_object(Bucket=r2_bucket, Key=file_key)
        except Exception:
            alt_key = f"{clean_name}/{clean_name}.mp4"
            try:
                s3.head_object(Bucket=r2_bucket, Key=alt_key)
                file_key = alt_key
            except Exception as head_err:
                print(f"[Preview Video] Video key '{file_key}' (nor '{alt_key}') not found in R2 bucket '{r2_bucket}'.")
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
    song_name = re.sub(r'[/\\?%*:|"<>.]', '', song_name).strip()
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
            # Check format first
            format_mode = "full_arrangement"
            songs_path = Path(__file__).resolve().parent / "songs.json"
            if not songs_path.exists():
                songs_path = Path(r"c:\Dev\meloscribe-frontend\website\src\data\songs.json")
            if songs_path.exists():
                try:
                    with open(songs_path, "r", encoding="utf-8") as f:
                        catalog = json.load(f)
                        for s in catalog:
                            title = s.get("title", "")
                            slug = s.get("id", "")
                            if title.lower() == name.lower() or slug.lower() == name.lower():
                                format_mode = s.get("format", "full_arrangement")
                                break
                except:
                    pass
            
            paths_to_try = []
            if format_mode == "viral_part":
                paths_to_try = [f"/home/ubuntu/meloscribe/Scores/{name}.mp4", f"/home/ubuntu/meloscribe/Scores/{name}_preview.mp4"]
            else:
                paths_to_try = [f"/home/ubuntu/meloscribe/Scores/{name}_preview.mp4", f"/home/ubuntu/meloscribe/Scores/{name}.mp4"]
                
            for local_path in paths_to_try:
                if os.path.exists(local_path):
                    print(f"[Preview Video] Serving local file: {local_path}")
                    return FileResponse(local_path, media_type="video/mp4", headers={"Cache-Control": "no-cache, no-store, must-revalidate, max-age=0"})
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
            resp_headers["Cache-Control"] = "no-cache, no-store, must-revalidate, max-age=0"
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
    song_name = re.sub(r'[/\\?%*:|"<>.]', '', song_name).strip()
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
    return JSONResponse(
        content={"error": "Direct downloads are disabled. Please enter your email to receive the complete download package."},
        status_code=403
    )

    song_title = target_song.get("title")
    difficulty = target_song.get("difficulty", "Original")
    if difficulty == "Easy":
        song_name = f"{song_title} Easy"
    else:
        song_name = song_title

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

        # Dynamic fallback verification: make sure key exists in R2
        try:
            s3.head_object(Bucket=r2_bucket, Key=file_key)
        except Exception:
            ext = ".pdf" if type == "pdf" else (".mid" if "midi" in type else (".mp4" if "video" in type else ".zip"))
            is_slow = "slow" in type
            res_objs = s3.list_objects_v2(Bucket=r2_bucket, Prefix=f"{song_name}/")
            for obj in res_objs.get("Contents", []):
                k = obj["Key"]
                if k.endswith(ext):
                    if is_slow and ("slow" in k.lower() or "tuto" in k.lower()):
                        file_key = k
                        break
                    elif not is_slow and "slow" not in k.lower() and "preview" not in k.lower():
                        file_key = k
                        break

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
def oauth_callback(code: str = None, state: str = "fb", error: str = None, error_description: str = None):
    """
    Handle authorization callback codes (for Facebook Graph / Threads APIs).
    Renders a premium success HTML block with micro-animations.
    """
    try:
        db_path = Path(__file__).resolve().parent / "analytics.db"
        conn = sqlite3.connect(str(db_path), timeout=30.0)
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
    except Exception as db_err:
        print(f"[OAuth Callback] Warning: Could not write auth code to DB: {db_err}")
    
    print(f"[OAuth Callback] Successfully captured code for state '{state}': {code[:15]}...")
    
    # Forward the callback to localhost:8080 (e.g. for TikTok/Pinterest local auth)
    # so that background setup scripts can receive the code
    try:
        forward_url = f"http://localhost:8080/?code={code}"
        if state:
            forward_url += f"&state={state}"
        requests.get(forward_url, timeout=2)
        print(f"[OAuth Callback] Successfully forwarded code to {forward_url}")
    except Exception as e:
        print(f"[OAuth Callback] Forwarding to localhost:8080 failed/skipped: {e}")
    
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
    <p class="desc">Dein Token für die Plattform <strong>{state.upper() if state else "APP"}</strong> wurde erfolgreich erfasst. Du kannst dieses Browserfenster jetzt schließen und die Meloscribe Desktop App nutzen.</p>
    <a href="javascript:window.close();" class="close-btn">Fenster Schließen</a>
  </div>
</body>
</html>
""")


@router.get("/api/oauth/code")
def get_oauth_code(state: str):
    """
    Polled by desktop app to retrieve authorization code captured by public callback.
    Deletes the code upon retrieval (one-time use).
    """
    try:
        db_path = Path(__file__).resolve().parent / "analytics.db"
        conn = sqlite3.connect(str(db_path), timeout=30.0)
        c = conn.cursor()
        c.execute("SELECT code FROM auth_codes WHERE state = ?", (state,))
        row = c.fetchone()
        if row:
            code = row[0]
            c.execute("DELETE FROM auth_codes WHERE state = ?", (state,))
            conn.commit()
            conn.close()
            return {"status": "ok", "code": code}
        conn.close()
        return {"status": "pending", "code": None}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})

