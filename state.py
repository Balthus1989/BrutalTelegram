"""
Gestione dello stato persistente.
Tiene traccia degli ID già notificati e dei message_id Telegram associati,
in modo da poter eliminare i messaggi quando un biglietto viene venduto.

Struttura del file JSON:
{
  "listings": {
    "<listing_id>": <telegram_message_id>,
    ...
  }
}
"""

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

STATE_FILE = Path("seen_tickets.json")


def load_state() -> dict[str, int]:
    """
    Carica lo stato dal file.

    Returns:
        Dict { listing_id -> telegram_message_id }
    """
    if not STATE_FILE.exists():
        return {}
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data.get("listings", {})
    except (json.JSONDecodeError, IOError) as e:
        logger.warning(f"Impossibile leggere il file di stato: {e}. Ricomincio da capo.")
        return {}


def save_state(state: dict[str, int]) -> None:
    """
    Salva lo stato nel file.

    Args:
        state: Dict { listing_id -> telegram_message_id }
    """
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump({"listings": state}, f, indent=2)
    except IOError as e:
        logger.error(f"Impossibile salvare il file di stato: {e}")


# Helpers per retrocompatibilità con il resto del codice
def load_seen_ids() -> set[str]:
    """Ritorna solo gli ID degli annunci tracciati."""
    return set(load_state().keys())


def save_seen_ids(seen_ids: set[str]) -> None:
    """Salva solo gli ID, senza message_id (usato per pulizia)."""
    state = load_state()
    # Rimuovi dal state gli ID non più presenti
    updated = {k: v for k, v in state.items() if k in seen_ids}
    save_state(updated)