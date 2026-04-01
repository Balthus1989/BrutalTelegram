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


async def notify_new_listings(
    bot: Bot,
    chat_id: str,
    listings: list[dict],
    topic_id: int = None,
) -> dict[str, int]:
    """
    Invia una notifica per ogni nuovo annuncio.

    Returns:
        Dict { listing_id -> telegram_message_id } per gli annunci notificati con successo.
    """
    sent = {}

    for listing in listings:
        try:
            message = format_listing_message(listing)
            result = await bot.send_message(
                chat_id=chat_id,
                text=message,
                parse_mode=ParseMode.MARKDOWN,
                disable_web_page_preview=False,
                message_thread_id=topic_id,
            )
            logger.info(
                f"Notifica inviata per annuncio ID: {listing['id']} "
                f"(message_id: {result.message_id}) — topic: {topic_id or 'generale'}"
            )
            sent[listing["id"]] = result.message_id

        except TelegramError as e:
            logger.error(f"Errore Telegram per annuncio {listing['id']}: {e}")

    return sent


async def delete_sold_messages(
    bot: Bot,
    chat_id: str,
    sold_listings: dict[str, int],
) -> None:
    """
    Elimina i messaggi Telegram relativi ai biglietti venduti.

    Args:
        bot: istanza del bot Telegram
        chat_id: ID del gruppo
        sold_listings: Dict { listing_id -> telegram_message_id } da eliminare
    """
    for listing_id, message_id in sold_listings.items():
        try:
            await bot.delete_message(chat_id=chat_id, message_id=message_id)
            logger.info(f"Messaggio eliminato per biglietto venduto ID: {listing_id} (message_id: {message_id})")
        except TelegramError as e:
            logger.warning(f"Impossibile eliminare il messaggio {message_id} per annuncio {listing_id}: {e}")