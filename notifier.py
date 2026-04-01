"""
Modulo per l'invio delle notifiche Telegram.
"""

import logging
from telegram import Bot
from telegram.error import TelegramError
from telegram.constants import ParseMode

logger = logging.getLogger(__name__)


def format_listing_message(listing: dict) -> str:
    """Formatta un annuncio come messaggio Telegram."""
    return (
        f"🔔 *Nuovo annuncio sul Ticket Exchange!*\n\n"
        f"🎟️ *{listing['product']}*\n"
        f"💶 Prezzo: *€ {listing['price']}*\n\n"
        f"👉 [Vedi annuncio]({listing['url']})\n\n"
        f"🏰 _Brutal Assault 2026 — Josefov, 5-8 Agosto_"
    )


async def notify_new_listings(bot: Bot, chat_id: str, listings: list[dict]) -> bool:
    """
    Invia una notifica per ogni nuovo annuncio.

    Returns:
        True se almeno un messaggio è stato inviato con successo.
    """
    if not listings:
        return False

    sent_any = False

    for listing in listings:
        try:
            message = format_listing_message(listing)
            await bot.send_message(
                chat_id=chat_id,
                text=message,
                parse_mode=ParseMode.MARKDOWN,
                disable_web_page_preview=False,
            )
            logger.info(f"Notifica inviata per annuncio ID: {listing['id']}")
            sent_any = True

        except TelegramError as e:
            logger.error(f"Errore Telegram per annuncio {listing['id']}: {e}")

    return sent_any