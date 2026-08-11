"""
Percorso della directory dati persistente.

Su Fly.io i file di stato vivono sul volume montato in /data (vedi fly.toml).
In locale, o se il volume non è montato, si ripiega sulla directory del progetto
segnalandolo nei log: meglio uno stato non persistente che un crash all'avvio.
"""

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_DATA_DIR = Path(os.getenv("BOT_DATA_DIR", "/data"))
FALLBACK_DATA_DIR = Path(__file__).parent


def _resolve_data_dir() -> Path:
    try:
        DEFAULT_DATA_DIR.mkdir(parents=True, exist_ok=True)
        probe = DEFAULT_DATA_DIR / ".write_test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return DEFAULT_DATA_DIR
    except OSError as e:
        logger.warning(
            f"Directory dati '{DEFAULT_DATA_DIR}' non utilizzabile ({e}). "
            f"Uso '{FALLBACK_DATA_DIR}' — lo stato NON sarà persistente tra i riavvii!"
        )
        return FALLBACK_DATA_DIR


DATA_DIR = _resolve_data_dir()


def data_file(name: str) -> Path:
    """Percorso completo di un file di stato nella directory dati."""
    return DATA_DIR / name
