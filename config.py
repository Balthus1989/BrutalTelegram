"""
Caricamento della configurazione da variabili d'ambiente (.env).
"""

import os
import logging
from dotenv import load_dotenv

logger = logging.getLogger(__name__)


def _parse_topic_id(value: str | None) -> int | None:
    """Converte il topic_id in intero, restituisce None se assente o non valido."""
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        logger.warning(f"TELEGRAM_TOPIC_ID non valido: '{value}'. Verrà ignorato.")
        return None


def load_config() -> dict:
    """
    Carica la configurazione dal file .env.

    Variabili richieste:
        TELEGRAM_TOKEN      — token del bot (da @BotFather)
        TELEGRAM_CHAT_ID    — ID del gruppo/canale da notificare
        TELEGRAM_TOPIC_ID   — ID del topic dove pubblicare (forum mode)

    Returns:
        Dict con chiavi: token, chat_id, topic_id
    """
    load_dotenv()

    token = os.getenv("TELEGRAM_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    topic_id = os.getenv("TELEGRAM_TOPIC_ID")

    missing = []
    if not token:
        missing.append("TELEGRAM_TOKEN")
    if not chat_id:
        missing.append("TELEGRAM_CHAT_ID")
    if not topic_id:
        missing.append("TELEGRAM_TOPIC_ID")

    if missing:
        raise EnvironmentError(
            f"Variabili d'ambiente mancanti nel file .env: {', '.join(missing)}\n"
            f"Crea un file .env con:\n"
            f"  TELEGRAM_TOKEN=il_tuo_token\n"
            f"  TELEGRAM_CHAT_ID=id_del_gruppo\n"
            f"  TELEGRAM_TOPIC_ID=id_del_topic\n"
        )

    logger.info(f"Configurazione caricata. Chat ID: {chat_id} — Topic ID: {topic_id or 'non impostato'}")
    return {
        "token": token,
        "chat_id": chat_id,
        "topic_id": _parse_topic_id(topic_id),
    }