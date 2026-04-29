from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import requests
import concurrent.futures
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parent
load_dotenv(dotenv_path=ROOT_DIR / ".env", override=True)

from flask import Flask, Response, request  # noqa: E402

from database import TaskDatabase  # noqa: E402
from router import AgentRouter  # noqa: E402
from whatsapp_client import WhatsAppClientError, send_text_message, send_template_message, download_media  # noqa: E402
import google.generativeai as genai  # noqa: E402

os.makedirs("data", exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    handlers=[
        logging.FileHandler("data/agent.log"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("whatsapp_server")

app = Flask(__name__)
router = AgentRouter()
db = TaskDatabase()
processed_ids: set[str] = set()
processed_order: deque[str] = deque()
processed_lock = threading.Lock()
rate_windows: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=30))
user_locks: dict[str, threading.Lock] = defaultdict(threading.Lock)
executor = concurrent.futures.ThreadPoolExecutor(
    max_workers=int(os.getenv("MAX_WORKERS", "10")),
    thread_name_prefix="AgentWorker"
)
workers_started = False
workers_lock = threading.Lock()


MAX_RESPONSE_CHARS = int(os.getenv("MAX_RESPONSE_CHARS", "3500"))
VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN", "")
APP_SECRET = os.getenv("WHATSAPP_APP_SECRET", "")
DEDUPE_MAX_IDS = int(os.getenv("DEDUPE_MAX_IDS", "10000"))


def _masked(value: str, keep: int = 8) -> str:
    v = (value or "").strip()
    if not v:
        return "(empty)"
    if len(v) <= keep:
        return "*" * len(v)
    return "*" * (len(v) - keep) + v[-keep:]


logger.info(
    "Config loaded from %s | api=%s | phone_id=%s | token=%s",
    str(ROOT_DIR / ".env"),
    os.getenv("WHATSAPP_API_VERSION", "v21.0").strip(),
    os.getenv("WHATSAPP_PHONE_NUMBER_ID", "").strip(),
    _masked(os.getenv("WHATSAPP_ACCESS_TOKEN", "").strip()),
)


def _rate_limit_ok(user_id: str, per_minute: int = 15) -> bool:
    now = time.time()
    q = rate_windows[user_id]
    while q and now - q[0] > 60:
        q.popleft()
    if len(q) >= per_minute:
        return False
    q.append(now)
    return True


def _verify_signature(raw_body: bytes, signature_header: str | None) -> bool:
    if not APP_SECRET:
        logger.warning("WHATSAPP_APP_SECRET not set; signature check bypassed in dev.")
        return True
    if not signature_header:
        return False
    try:
        method, provided_sig = signature_header.split("=", 1)
    except ValueError:
        return False
    if method != "sha256":
        return False
    expected = hmac.new(APP_SECRET.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, provided_sig)


def _extract_messages(payload: dict) -> list[dict]:
    out: list[dict] = []
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            for message in value.get("messages", []) or []:
                out.append(message)
    return out


def _remember_processed_message(msg_id: str) -> bool:
    """LRU dedupe store. Returns False if already seen."""
    with processed_lock:
        if msg_id in processed_ids:
            return False
        processed_ids.add(msg_id)
        processed_order.append(msg_id)
        while len(processed_order) > DEDUPE_MAX_IDS:
            oldest = processed_order.popleft()
            processed_ids.discard(oldest)
    return True

def _is_within_24h(iso_time_str: str | None) -> bool:
    if not iso_time_str:
        return False
    try:
        last_time = datetime.fromisoformat(iso_time_str)
        now = datetime.now(timezone.utc)
        return (now - last_time).total_seconds() < 86400
    except ValueError:
        return False

def _forget_processed_message(msg_id: str) -> None:
    """Rollback dedupe reservation (e.g. queue was full)."""
    with processed_lock:
        if msg_id in processed_ids:
            processed_ids.remove(msg_id)
        try:
            processed_order.remove(msg_id)
        except ValueError:
            pass


def _start_reminder_thread() -> None:
    def worker() -> None:
        logger.info("Reminder scheduler started.")
        while True:
            try:
                # Dynamically convert upcoming events into standard actionable reminders
                db.expand_events_to_reminders()

                due = db.get_due_reminders()
                for reminder in due:
                    text = f"Reminder: {reminder.message}"
                    try:
                        last_inbound = db.get_last_inbound_time(reminder.user_id)
                        if _is_within_24h(last_inbound):
                            send_text_message(reminder.user_id, text[:MAX_RESPONSE_CHARS])
                        else:
                            # Use pre-approved template for messages outside 24h window
                            send_template_message(reminder.user_id, "reminder_v1", [reminder.message[:1024]])
                        db.mark_reminder_sent(reminder.id)
                        logger.info("reminder_sent id=%s user=%s", reminder.id, reminder.user_id)
                    except Exception as exc:
                        logger.exception("reminder_send_failed id=%s err=%s", reminder.id, exc)
            except Exception:
                logger.exception("reminder_loop_failed")
            time.sleep(20)

    t = threading.Thread(target=worker, name="reminder-loop", daemon=True)
    t.start()


def _start_digest_thread() -> None:
    def worker() -> None:
        logger.info("Proactive grocery digest scheduler started.")
        while True:
            try:
                now_utc = datetime.now(timezone.utc)
                households = db.get_all_households()
                for hh in households:
                    # 1. Grace period (Don't spam if sent within 5 days)
                    if hh.get("last_digest_sent_at"):
                        try:
                            last_sent = datetime.fromisoformat(hh["last_digest_sent_at"])
                            if (now_utc - last_sent).days < 5:
                                continue
                        except ValueError:
                            pass

                    # 2. Get Owner and Timezone
                    owner_id = db.get_household_owner(hh["id"])
                    if not owner_id:
                        continue
                    
                    settings = db.get_user_settings(owner_id) or {}
                    tz_str = settings.get("timezone", "UTC")
                    try:
                        from zoneinfo import ZoneInfo
                        tz = ZoneInfo(tz_str)
                    except Exception:
                        tz = timezone.utc
                        
                    local_now = now_utc.astimezone(tz)
                    
                    # 3. Only calculate & send digests between 5PM and 8PM local time
                    if not (17 <= local_now.hour <= 20):
                        continue
                        
                    # 4. Shopping Pattern Histogram (Last 60 Days)
                    events = db.get_recent_bought_events(hh["id"], days=60)
                    if len(events) < 3:
                        continue # Not enough data
                        
                    weights = {i: 0.0 for i in range(7)}
                    last_bought_dt = None
                    
                    for iso in events:
                        dt = datetime.fromisoformat(iso).astimezone(tz)
                        if not last_bought_dt or dt > last_bought_dt:
                            last_bought_dt = dt
                        
                        days_ago = (local_now - dt).days
                        weight = 1.5 if days_ago <= 14 else 1.0 # 2-week recency bias
                        weights[dt.weekday()] += weight
                        
                    # 5. Safety Net: If they bought something in the last 3 days, don't nag them
                    if last_bought_dt and (local_now - last_bought_dt).days < 3:
                        continue
                        
                    total_weight = sum(weights.values())
                    if total_weight == 0:
                        continue
                        
                    best_dow = max(weights, key=weights.get)
                    confidence = weights[best_dow] / total_weight
                    tomorrow_dow = (local_now.weekday() + 1) % 7
                    
                    # 6. If tomorrow is shopping day (>60% confidence), generate digest
                    if best_dow == tomorrow_dow and confidence >= 0.60:
                        pending = db.list_grocery_items(owner_id, "pending", override_hh_id=hh["id"])
                        suggestions = db.suggest_rebuy_candidates(owner_id, limit=20, override_hh_id=hh["id"])
                        due_items = [s for s in suggestions if s.get("is_due")]
                        
                        if len(pending) > 0 or len(due_items) > 0:
                            days_map = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
                            day_name = days_map[tomorrow_dow]
                            try:
                                # You will need to register a WhatsApp template called "grocery_digest_v1" via Meta
                                send_template_message(
                                    owner_id, 
                                    "grocery_digest_v1", 
                                    [day_name, str(len(pending)), str(len(due_items))]
                                )
                                db.mark_digest_sent(hh["id"])
                                logger.info("Sent proactive weekly digest to owner=%s for hh=%s", owner_id, hh["id"])
                            except Exception as e:
                                logger.error("Failed to send digest to %s: %s", owner_id, e)
            except Exception as e:
                logger.exception("digest_loop_failed: %s", e)
            time.sleep(3600) # Check every hour

    t = threading.Thread(target=worker, name="digest-loop", daemon=True)
    t.start()


def _handle_message(msg: dict) -> None:
    sender = msg.get("from", "").strip()
    msg_type = msg.get("type", "")
    if not sender:
        return

    with user_locks[sender]:
        if not _rate_limit_ok(sender):
            try:
                send_text_message(sender, "Too many messages. Please wait a minute.")
            except Exception:
                logger.exception("rate_limit_reply_failed")
            return

        if msg_type not in ("text", "audio", "image", "document"):
            try:
                send_text_message(sender, "I can only process text, voice, image, and document messages right now.")
            except Exception:
                logger.exception("unsupported_message_type_reply_failed")
            return

        text = ""
        media_bytes = None
        mime_type = None
        if msg_type == "text":
            text = (msg.get("text", {}) or {}).get("body", "").strip()
        elif msg_type == "audio":
            audio_id = (msg.get("audio", {}) or {}).get("id", "")
            if not audio_id:
                return
            try:
                # Download and pass directly to Gemini for free transcription
                audio_bytes = download_media(audio_id)
                
                groq_api_key = os.getenv("GROQ_API_KEY", "").strip()
                if not groq_api_key:
                    logger.error("GROQ_API_KEY not set. Cannot transcribe audio.")
                    send_text_message(sender, "Voice notes are currently disabled. Missing Groq API key.")
                    return

                files = {
                    "file": ("audio.ogg", audio_bytes, "audio/ogg")
                }
                data = {
                    "model": "whisper-large-v3-turbo",
                    "response_format": "json"
                }
                headers = {
                    "Authorization": f"Bearer {groq_api_key}"
                }
                
                response = requests.post(
                    "https://api.groq.com/openai/v1/audio/transcriptions",
                    headers=headers,
                    files=files,
                    data=data,
                    timeout=30
                )
                response.raise_for_status()
                text = response.json().get("text", "").strip()
                
            except Exception:
                logger.exception("audio_transcription_failed")
                send_text_message(sender, "Sorry, I couldn't transcribe your voice note.")
                return
        elif msg_type == "image":
            img_obj = msg.get("image", {}) or {}
            image_id = img_obj.get("id", "")
            mime_type = img_obj.get("mime_type", "image/jpeg")
            text = img_obj.get("caption", "").strip()
            if not text:
                text = "Please analyze this image."
            if image_id:
                try:
                    media_bytes = download_media(image_id)
                except Exception:
                    logger.exception("image_download_failed")
                    send_text_message(sender, "Sorry, I couldn't download your image.")
                    return
        elif msg_type == "document":
            doc_obj = msg.get("document", {}) or {}
            document_id = doc_obj.get("id", "")
            mime_type = doc_obj.get("mime_type", "text/plain")
            text = doc_obj.get("caption", "").strip()
            if not text:
                text = "Please analyze this document and log the expenses."
            if document_id:
                try:
                    send_text_message(sender, "📄 Received your document! Analyzing it now... this might take a few seconds.")
                    media_bytes = download_media(document_id)
                except Exception:
                    logger.exception("document_download_failed")
                    send_text_message(sender, "Sorry, I couldn't download your document.")
                    return

        if not text and not media_bytes:
            return

        try:
            answer = router.route(sender, text, media_bytes=media_bytes, mime_type=mime_type)
        except Exception:
            logger.exception("agent_route_failed user=%s", sender)
            answer = "Something went wrong while processing your message."

        answer = answer[:MAX_RESPONSE_CHARS]
        try:
            send_text_message(sender, answer)
        except WhatsAppClientError as exc:
            logger.error("whatsapp_send_failed user=%s err=%s", sender, exc)
        except Exception:
            logger.exception("send_reply_failed user=%s", sender)


def _start_background_workers() -> None:
    global workers_started
    with workers_lock:
        if workers_started:
            return
        _start_reminder_thread()
        _start_digest_thread()
        workers_started = True


@app.get("/health")
def health() -> Response:
    return Response("ok", status=200)


@app.get("/webhook")
def verify_webhook() -> Response:
    mode = request.args.get("hub.mode", "")
    token = request.args.get("hub.verify_token", "")
    challenge = request.args.get("hub.challenge", "")
    if not VERIFY_TOKEN:
        logger.error("WHATSAPP_VERIFY_TOKEN is empty. Rejecting verification by default.")
        return Response("Server misconfigured: verify token is empty", status=403)
    if mode == "subscribe" and token == VERIFY_TOKEN and challenge:
        return Response(challenge, status=200)
    return Response("Verification failed", status=403)


@app.post("/webhook")
def receive_webhook() -> Response:
    _start_background_workers()
    raw = request.get_data()
    sig = request.headers.get("X-Hub-Signature-256")
    if not _verify_signature(raw, sig):
        logger.warning("signature_invalid")
        return Response("Invalid signature", status=403)

    payload = request.get_json(silent=True) or {}
    logger.info("payload=%s", json.dumps(payload)[:1000])

    try:
        messages = _extract_messages(payload)
    except Exception:
        logger.exception("payload_parse_failed")
        return Response("ok", status=200)

    if not messages:
        return Response("ok", status=200)

    for msg in messages:
        msg_id = str(msg.get("id", "")).strip()
        if msg_id and not _remember_processed_message(msg_id):
            logger.info("duplicate_message id=%s", msg_id)
            continue
        try:
            executor.submit(_handle_message, msg)
        except Exception:
            if msg_id:
                _forget_processed_message(msg_id)
            logger.exception("executor_submit_failed dropping_message id=%s", msg_id or "(none)")

    # Acknowledge Meta immediately; async worker handles processing.
    return Response("ok", status=200)


if __name__ == "__main__":
    _start_background_workers()
    port = int(os.getenv("PORT", "8000"))
    logger.info("Starting Flask server on port=%s at %s", port, datetime.now().isoformat())
    debug_mode = os.getenv("FLASK_DEBUG", "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    if debug_mode:
        logger.warning("FLASK_DEBUG is enabled; do not use this in public/ngrok setups.")
    app.run(host="0.0.0.0", port=port, debug=debug_mode)
