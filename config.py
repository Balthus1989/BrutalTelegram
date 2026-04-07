"""
Caricamento della configurazione da variabili d'ambiente (.env).
"""

import os
import logging
from dotenv import load_dotenv

logger = logging.getLogger(__name__)


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
    load_dotenv(override=False)  # .env opzionale — su Railway le variabili arrivano dall'ambiente

    token = os.getenv("TELEGRAM_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    topic_id = os.getenv("TELEGRAM_TOPIC_ID")
    news_topic_id = os.getenv("TELEGRAM_NEWS_TOPIC_ID")

    missing = []
    if not token:
        missing.append("TELEGRAM_TOKEN")
    if not chat_id:
        missing.append("TELEGRAM_CHAT_ID")

    if missing:
        raise EnvironmentError(
            f"Variabili d'ambiente mancanti nel file .env: {', '.join(missing)}\n"
            f"Crea un file .env con:\n"
            f"  TELEGRAM_TOKEN=il_tuo_token\n"
            f"  TELEGRAM_CHAT_ID=id_del_gruppo\n"
            f"  TELEGRAM_TOPIC_ID=id_del_topic\n"
            f"  TELEGRAM_NEWS_TOPIC_ID=id_del_topic_news"
        )

    logger.info(f"Configurazione caricata. Chat ID: {chat_id} — Topic ID: {topic_id or 'non impostato'}")
    return {
        "token": token,
        "chat_id": chat_id,
        "topic_id": int(topic_id) if topic_id else None,
        "news_topic_id": int(news_topic_id) if news_topic_id else None,
    }