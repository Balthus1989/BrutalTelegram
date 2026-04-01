"""
Gestione dello stato persistente: tiene traccia degli ID già notificati.
"""

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

STATE_FILE = Path("seen_tickets.json")


def load_seen_ids() -> set[str]:
    """Carica gli ID già visti dal file di stato."""
    if not STATE_FILE.exists():
        return set()
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return set(data.get("seen_ids", []))
    except (json.JSONDecodeError, IOError) as e:
        logger.warning(f"Impossibile leggere il file di stato: {e}. Ricomincio da capo.")
        return set()


def save_seen_ids(seen_ids: set[str]) -> None:
    """Salva gli ID visti nel file di stato."""
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump({"seen_ids": list(seen_ids)}, f, indent=2)
    except IOError as e:
        logger.error(f"Impossibile salvare il file di stato: {e}")