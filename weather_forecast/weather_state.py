"""
Stato del report meteo automatico: data dell'ultimo report pubblicato.

Serve a garantire un solo report al giorno anche se il bot viene riavviato
(su Fly.io i redeploy sono frequenti) e a recuperare il report se l'orario
previsto è stato mancato per un riavvio o un errore temporaneo.
"""

import json
import logging

from storage import data_file

logger = logging.getLogger(__name__)

WEATHER_STATE_FILE = data_file("weather_state.json")


def load_last_report_date() -> str | None:
    """Data (ISO 'YYYY-MM-DD') dell'ultimo report pubblicato, None se mai pubblicato."""
    if not WEATHER_STATE_FILE.exists():
        return None
    try:
        with open(WEATHER_STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f).get("last_report_date")
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"Impossibile leggere {WEATHER_STATE_FILE}: {e}")
        return None


def save_last_report_date(day: str) -> None:
    try:
        WEATHER_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(WEATHER_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump({"last_report_date": day}, f)
    except OSError as e:
        logger.error(f"Impossibile salvare {WEATHER_STATE_FILE}: {e}")
