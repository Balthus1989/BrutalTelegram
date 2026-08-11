"""
Gestione dello stato persistente.
Tiene traccia degli annunci già notificati e dei message_id Telegram associati,
in modo da poter eliminare i messaggi quando un biglietto viene venduto.

Struttura del file JSON:
{
  "listings": {
    "<listing_id>": {
      "message_id": <telegram_message_id>,
      "chat_id": "<chat_id>",
      "product": "...",
      "price": "...",
      "url": "...",
      "first_seen": "2026-08-05T09:00:00+00:00",
      "missing_count": 0,       # cicli consecutivi in cui l'annuncio non compare più
      "delete_attempts": 0      # tentativi di rimozione del messaggio già effettuati
    },
    ...
  }
}

Il formato legacy { "<listing_id>": <message_id> } viene migrato automaticamente
alla prima lettura.
"""

import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from storage import data_file

logger = logging.getLogger(__name__)

STATE_FILE = data_file("seen_tickets.json")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def make_record(listing: dict, message_id: int | None, chat_id: str | None) -> dict:
    """Crea il record di stato per un annuncio appena notificato."""
    return {
        "message_id": message_id,
        "chat_id": str(chat_id) if chat_id is not None else None,
        "product": listing.get("product"),
        "price": listing.get("price"),
        "url": listing.get("url"),
        "first_seen": now_iso(),
        "missing_count": 0,
        "delete_attempts": 0,
    }


def _normalize(raw: dict) -> dict[str, dict]:
    """Converte il formato legacy { id -> message_id } nel formato a record."""
    state: dict[str, dict] = {}
    for listing_id, value in raw.items():
        if isinstance(value, dict):
            value.setdefault("message_id", None)
            value.setdefault("chat_id", None)
            value.setdefault("missing_count", 0)
            value.setdefault("delete_attempts", 0)
            state[str(listing_id)] = value
        else:
            # Formato vecchio: solo il message_id
            state[str(listing_id)] = {
                "message_id": value,
                "chat_id": None,
                "product": None,
                "price": None,
                "url": None,
                "first_seen": None,
                "missing_count": 0,
                "delete_attempts": 0,
            }
    return state


def load_state() -> dict[str, dict]:
    """
    Carica lo stato dal file.

    Returns:
        Dict { listing_id -> record }
    """
    if not STATE_FILE.exists():
        logger.info(f"Nessuno stato precedente in {STATE_FILE} — parto da zero.")
        return {}
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"Impossibile leggere il file di stato {STATE_FILE}: {e}. Ricomincio da capo.")
        return {}

    return _normalize(data.get("listings", {}))


def save_state(state: dict[str, dict]) -> bool:
    """
    Salva lo stato nel file in modo atomico (scrittura su file temporaneo + rename),
    così un riavvio a metà scrittura non lascia un JSON corrotto sul volume.

    Returns:
        True se il salvataggio è andato a buon fine.
    """
    payload = json.dumps({"listings": state}, indent=2, ensure_ascii=False)
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(dir=str(STATE_FILE.parent), prefix=".seen_tickets.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(payload)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, STATE_FILE)
        except BaseException:
            Path(tmp_path).unlink(missing_ok=True)
            raise
        logger.debug(f"Stato salvato ({len(state)} annunci tracciati) in {STATE_FILE}.")
        return True
    except OSError as e:
        logger.error(f"Impossibile salvare il file di stato {STATE_FILE}: {e}")
        return False


# Helpers per retrocompatibilità con il resto del codice
def load_seen_ids() -> set[str]:
    """Ritorna solo gli ID degli annunci tracciati."""
    return set(load_state().keys())


def save_seen_ids(seen_ids: set[str]) -> None:
    """Rimuove dallo stato gli annunci non più presenti nell'insieme dato."""
    state = load_state()
    updated = {k: v for k, v in state.items() if k in seen_ids}
    save_state(updated)
