"""
Stato della disponibilità dei prodotti in vendita sullo shop ufficiale.

Tiene traccia dell'ultima soglia (multiplo di 5%) notificata per ogni prodotto,
così un riavvio del bot non fa ri-annunciare soglie già comunicate al gruppo né
perde quelle attraversate nel frattempo.

Le funzioni prendono il file su cui lavorare, così biglietti e alloggi tengono
stati separati con lo stesso codice: due copie divergenti di questa logica sono
già state la causa di un sold out mai annunciato.

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
from tickets.availability_scraper import is_sold_out

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
        "sold_out": is_sold_out(product),
        "missing_count": 0,
        "updated": now_iso(),
    }


def load_availability_state(path: Path = AVAILABILITY_STATE_FILE) -> dict:
    """
    Carica lo stato dal file indicato.

    Returns:
        Dict con chiavi 'initialized' (bool) e 'products' ({ product_id -> record }).
        Stato vuoto e non inizializzato se il file manca o è illeggibile: al
        prossimo ciclo il bot ripubblica il messaggio iniziale invece di restare muto.
    """
    empty = {"initialized": False, "products": {}}

    if not path.exists():
        logger.info(
            f"Nessuno stato disponibilità in {path} — "
            f"pubblico il riepilogo iniziale al primo controllo."
        )
        return empty

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"Impossibile leggere {path}: {e}. Ricomincio da capo.")
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


def save_availability_state(state: dict, path: Path = AVAILABILITY_STATE_FILE) -> bool:
    """
    Salva lo stato in modo atomico (file temporaneo + rename), così un riavvio a
    metà scrittura non lascia un JSON corrotto sul volume.

    Returns:
        True se il salvataggio è andato a buon fine.
    """
    payload = json.dumps(state, indent=2, ensure_ascii=False)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(
            dir=str(path.parent),
            prefix=f".{path.stem}.",
            suffix=".tmp",
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(payload)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, path)
        except BaseException:
            Path(tmp_path).unlink(missing_ok=True)
            raise
        logger.debug(
            f"Stato disponibilità salvato ({len(state.get('products', {}))} prodotti) in {path}."
        )
        return True
    except OSError as e:
        logger.error(f"Impossibile salvare {path}: {e}")
        return False
