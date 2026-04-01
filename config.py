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
        TELEGRAM_TOKEN  — token del bot (da @BotFather)
        TELEGRAM_CHAT_ID — ID del gruppo/canale da notificare

    Returns:
        Dict con chiavi: token, chat_id
    """
    load_dotenv()

    token = os.getenv("TELEGRAM_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")

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
            f"  TELEGRAM_CHAT_ID=id_del_gruppo"
        )

    logger.info(f"Configurazione caricata. Chat ID: {chat_id}")
    return {"token": token, "chat_id": chat_id}