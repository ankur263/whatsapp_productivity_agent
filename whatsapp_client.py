from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

import requests


class WhatsAppClientError(RuntimeError):
    pass


def _normalize_recipient_phone(to: str) -> str:
    # Graph API expects international format without "+" or separators.
    digits = re.sub(r"\D+", "", to or "")
    return digits


@dataclass
class WhatsAppConfig:
    api_version: str = field(
        default_factory=lambda: os.getenv("WHATSAPP_API_VERSION", "v21.0").strip()
    )
    phone_number_id: str = field(
        default_factory=lambda: os.getenv("WHATSAPP_PHONE_NUMBER_ID", "").strip()
    )
    access_token: str = field(
        default_factory=lambda: os.getenv("WHATSAPP_ACCESS_TOKEN", "").strip()
    )


def send_text_message(to: str, body: str) -> None:
    cfg = WhatsAppConfig()
    if not cfg.phone_number_id or not cfg.access_token:
        raise WhatsAppClientError(
            "Missing WHATSAPP_PHONE_NUMBER_ID or WHATSAPP_ACCESS_TOKEN in environment."
        )
    to_norm = _normalize_recipient_phone(to)
    if not to_norm:
        raise WhatsAppClientError("Recipient phone number is empty or invalid.")

    url = (
        f"https://graph.facebook.com/{cfg.api_version}/"
        f"{cfg.phone_number_id}/messages"
    )
    payload = {
        "messaging_product": "whatsapp",
        "to": to_norm,
        "type": "text",
        "text": {"body": body},
    }
    headers = {
        "Authorization": f"Bearer {cfg.access_token}",
        "Content-Type": "application/json",
    }
    resp = requests.post(url, json=payload, headers=headers, timeout=20)
    if resp.status_code >= 400:
        msg = f"WhatsApp send failed ({resp.status_code}): {resp.text[:500]}"
        if resp.status_code == 401:
            msg += " | token may be expired."
        raise WhatsAppClientError(msg)

def send_template_message(to: str, template_name: str, params: list[str]) -> None:
    cfg = WhatsAppConfig()
    if not cfg.phone_number_id or not cfg.access_token:
        raise WhatsAppClientError(
            "Missing WHATSAPP_PHONE_NUMBER_ID or WHATSAPP_ACCESS_TOKEN in environment."
        )
    to_norm = _normalize_recipient_phone(to)
    if not to_norm:
        raise WhatsAppClientError("Recipient phone number is empty or invalid.")

    url = (
        f"https://graph.facebook.com/{cfg.api_version}/"
        f"{cfg.phone_number_id}/messages"
    )
    payload = {
        "messaging_product": "whatsapp",
        "to": to_norm,
        "type": "template",
        "template": {
            "name": template_name,
            "language": {"code": "en"},
            "components": [
                {
                    "type": "body",
                    "parameters": [{"type": "text", "text": p} for p in params]
                }
            ] if params else []
        }
    }
    headers = {
        "Authorization": f"Bearer {cfg.access_token}",
        "Content-Type": "application/json",
    }
    resp = requests.post(url, json=payload, headers=headers, timeout=20)
    if resp.status_code >= 400:
        msg = f"WhatsApp template send failed ({resp.status_code}): {resp.text[:500]}"
        if resp.status_code == 401:
            msg += " | token may be expired."
        raise WhatsAppClientError(msg)

def download_media(media_id: str) -> bytes:
    """Fetches media URL from Meta and downloads the raw bytes."""
    cfg = WhatsAppConfig()
    if not cfg.access_token:
        raise WhatsAppClientError("Missing WHATSAPP_ACCESS_TOKEN in environment.")

    url = f"https://graph.facebook.com/{cfg.api_version}/{media_id}"
    headers = {"Authorization": f"Bearer {cfg.access_token}"}
    
    resp = requests.get(url, headers=headers, timeout=20)
    if resp.status_code >= 400:
        raise WhatsAppClientError(f"Failed to get media info ({resp.status_code}): {resp.text[:500]}")
        
    media_url = resp.json().get("url")
    dl_resp = requests.get(media_url, headers=headers, timeout=20)
    if dl_resp.status_code >= 400:
        raise WhatsAppClientError(f"Failed to download media bytes ({dl_resp.status_code}): {dl_resp.text[:500]}")
        
    return dl_resp.content
