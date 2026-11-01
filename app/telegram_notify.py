from __future__ import annotations

import logging
import threading
from typing import Optional

import requests

logger = logging.getLogger(__name__)


def send_telegram_message(
    bot_token: Optional[str],
    chat_id: Optional[str],
    text: str,
    *,
    timeout_s: float = 15.0,
) -> None:
    if not bot_token or not chat_id:
        return
    try:
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        resp = requests.post(
            url,
            json={"chat_id": chat_id, "text": text, "disable_web_page_preview": True},
            timeout=timeout_s,
        )
        if resp.status_code >= 400:
            logger.warning("Telegram sendMessage failed: %s %s", resp.status_code, resp.text[:300])
    except Exception:
        logger.exception("Telegram sendMessage exception")


def send_telegram_message_async(
    bot_token: Optional[str],
    chat_id: Optional[str],
    text: str,
    *,
    timeout_s: float = 15.0,
) -> None:
    # Fire-and-forget so API responses are never blocked by Telegram slowness.
    t = threading.Thread(
        target=send_telegram_message,
        kwargs={"bot_token": bot_token, "chat_id": chat_id, "text": text, "timeout_s": timeout_s},
        daemon=True,
    )
    t.start()


