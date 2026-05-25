"""Telegram notifications for OnClock.

Sends operational alerts to the owner via Telegram bot:
- Session started / ended
- Advance booking created / confirmed / cancelled
- Errors (auto-expire failures, etc.)

Env vars (optional — notifications silently skip if unset):
  TELEGRAM_BOT_TOKEN — bot token from @BotFather
  TELEGRAM_CHAT_ID — owner's Telegram chat ID
"""

import os
import logging
from typing import Optional

import httpx

logger = logging.getLogger("onclock.notifications")

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
ENABLED = bool(BOT_TOKEN and CHAT_ID)


async def send_message(text: str, parse_mode: str = "HTML") -> bool:
    """Send a Telegram message to the configured owner chat.

    Returns True if sent successfully, False if disabled or failed.
    Silently no-ops if TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID are not set.
    """
    if not ENABLED:
        return False

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                json={
                    "chat_id": CHAT_ID,
                    "text": text,
                    "parse_mode": parse_mode,
                    "disable_web_page_preview": True,
                },
            )
            if resp.status_code != 200:
                logger.warning("Telegram API error: %s %s", resp.status_code, resp.text)
                return False
            return True
    except Exception as e:
        logger.warning("Telegram send failed: %s", e)
        return False


# ─── Convenience builders ────────────────────────────────────

def fmt_session_start(
    station_name: str,
    station_number: int,
    player_name: str,
    player_phone: str,
    num_players: int,
    hours: float,
    total: float,
    currency_symbol: str = "₹",
    session_id: int = 0,
) -> str:
    """Build HTML message for a session start event."""
    return (
        f"🟢 <b>Session Started</b>\n"
        f"━━━━━━━━━━━━━━━\n"
        f"🏪 <b>Station:</b> #{station_number} — {station_name}\n"
        f"👤 <b>Player:</b> {player_name}"
        + (f" ({player_phone})" if player_phone else "")
        + f"\n"
        f"👥 <b>Players:</b> {num_players}\n"
        f"⏱ <b>Hours:</b> {hours}h\n"
        f"💰 <b>Total:</b> {currency_symbol}{total:.0f}\n"
        + (f"🆔 <b>Session:</b> #{session_id}" if session_id else "")
    )


def fmt_session_end(
    station_name: str,
    station_number: int,
    player_name: str,
    hours: float,
    total: float,
    currency_symbol: str = "₹",
    reason: str = "",
    session_id: int = 0,
) -> str:
    """Build HTML message for a session end event."""
    text = (
        f"🔴 <b>Session Ended</b>\n"
        f"━━━━━━━━━━━━━━━\n"
        f"🏪 <b>Station:</b> #{station_number} — {station_name}\n"
        f"👤 <b>Player:</b> {player_name}\n"
        f"⏱ <b>Booked:</b> {hours}h\n"
        f"💰 <b>Charged:</b> {currency_symbol}{total:.0f}\n"
    )
    if reason:
        text += f"📝 <b>Reason:</b> {reason}\n"
    if session_id:
        text += f"🆔 <b>Session:</b> #{session_id}"
    return text


def fmt_booking_created(
    station_name: str,
    station_number: int,
    player_name: str,
    scheduled_start: str,
    hours: float,
    total: float,
    currency_symbol: str = "₹",
    booking_id: int = 0,
) -> str:
    """Build HTML message for a new advance booking."""
    text = (
        f"📅 <b>Advance Booking</b>\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"🏪 <b>Station:</b> #{station_number} — {station_name}\n"
        f"👤 <b>Player:</b> {player_name}\n"
        f"🕐 <b>Scheduled:</b> {scheduled_start}\n"
        f"⏱ <b>Hours:</b> {hours}h\n"
        f"💰 <b>Total:</b> {currency_symbol}{total:.0f}\n"
        f"💳 <b>Status:</b> ⏳ Pending payment verification"
    )
    if booking_id:
        text += f"\n🆔 <b>Booking:</b> #{booking_id}"
    return text
