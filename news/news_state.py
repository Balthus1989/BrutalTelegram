import json
import logging
import os

from storage import data_file

logger = logging.getLogger(__name__)

NEWS_STATE_FILE = data_file("seen_news.json")

def load_seen() -> set:
    if not os.path.exists(NEWS_STATE_FILE):
        save_seen(set())
        return set()
    try:
        with open(NEWS_STATE_FILE, encoding="utf-8") as f:
            content = f.read().strip()
            if not content:
                return set()
            return set(json.loads(content))
    except (json.JSONDecodeError, ValueError, OSError) as e:
        logger.warning(f"Impossibile leggere {NEWS_STATE_FILE}: {e}")
        return set()

def save_seen(seen: set):
    try:
        NEWS_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(NEWS_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(list(seen), f)
    except OSError as e:
        logger.error(f"Impossibile salvare {NEWS_STATE_FILE}: {e}")
