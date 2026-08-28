"""
Stato della disponibilità dei biglietti in vendita.

Tiene traccia dell'ultima soglia (multiplo di 5%) notificata per ogni prodotto,
così un riavvio del bot non fa ri-annunciare soglie già comunicate al gruppo né
perde quelle attraversate nel frattempo.

Struttura del file JSON:
{
  "initialized": true,           # il messaggio iniziale è già stato pubblicato
  "products": {
    "<product_id>": {
      "name": "...",
      "url": "...",
      "level": 35,               # ultima soglia notificata (multiplo di 5)
      "percent": 35.01,          # ultima percentuale letta dal sito
      "sold_out": false,         # sold out già annunciato
      "missing_count": 0,        # cicli consecutivi in cui il prodotto non compare
      "updated": "2026-08-28T09:00:00+00:00"
    }
  }
}
"""

import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from storage import data_file

logger = logging.getLogger(__name__)

AVAILABILITY_STATE_FILE = data_file("ticket_availability.json")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def make_record(product: dict, level: int) -> dict:
    """
    Crea il record di stato per un prodotto appena messo sotto osservazione.

    Attenzione: `level` 0 significa "sotto il 5%", non esaurito. Il sold out è
    solo la disponibilità a zero o il badge del sito.
    """
    percent = product.get("percent")
    return {
        "name": product.get("name"),
        "url": product.get("url"),
        "level": level,
        "percent": percent,
        "sold_out": bool(product.get("sold_out")) or (percent is not None and percent <= 0),
        "missing_count": 0,
        "updated": now_iso(),
    }


def load_availability_state() -> dict:
    """
    Carica lo stato dal file.

    Returns:
        Dict con chiavi 'initialized' (bool) e 'products' ({ product_id -> record }).
        Stato vuoto e non inizializzato se il file manca o è illeggibile: al
        prossimo ciclo il bot ripubblica il messaggio iniziale invece di restare muto.
    """
    empty = {"initialized": False, "products": {}}

    if not AVAILABILITY_STATE_FILE.exists():
        logger.info(
            f"Nessuno stato disponibilità in {AVAILABILITY_STATE_FILE} — "
            f"pubblico il riepilogo iniziale al primo controllo."
        )
        return empty

    try:
        with open(AVAILABILITY_STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(
            f"Impossibile leggere {AVAILABILITY_STATE_FILE}: {e}. Ricomincio da capo."
        )
        return empty

    products: dict[str, dict] = {}
    for product_id, record in (data.get("products") or {}).items():
        if not isinstance(record, dict):
            continue
        record.setdefault("level", 100)
        record.setdefault("percent", None)
        record.setdefault("sold_out", False)
        record.setdefault("missing_count", 0)
        products[str(product_id)] = record

    return {"initialized": bool(data.get("initialized")), "products": products}


def save_availability_state(state: dict) -> bool:
    """
    Salva lo stato in modo atomico (file temporaneo + rename), così un riavvio a
    metà scrittura non lascia un JSON corrotto sul volume.

    Returns:
        True se il salvataggio è andato a buon fine.
    """
    payload = json.dumps(state, indent=2, ensure_ascii=False)
    try:
        AVAILABILITY_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(
            dir=str(AVAILABILITY_STATE_FILE.parent),
            prefix=".ticket_availability.",
            suffix=".tmp",
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(payload)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, AVAILABILITY_STATE_FILE)
        except BaseException:
            Path(tmp_path).unlink(missing_ok=True)
            raise
        logger.debug(
            f"Stato disponibilità salvato ({len(state.get('products', {}))} prodotti) "
            f"in {AVAILABILITY_STATE_FILE}."
        )
        return True
    except OSError as e:
        logger.error(f"Impossibile salvare {AVAILABILITY_STATE_FILE}: {e}")
        return False
