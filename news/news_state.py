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


# Tentativi di pubblicazione falliti per ogni news: { news_id -> conteggio }.
# Persistente perché il polling riparte a ogni riavvio: senza questo file una
# news impubblicabile ricomincerebbe da zero a ogni deploy.
NEWS_FAILURES_FILE = data_file("news_failures.json")


def load_failures() -> dict:
    try:
        with open(NEWS_FAILURES_FILE, encoding="utf-8") as f:
            content = f.read().strip()
            if not content:
                return {}
            data = json.loads(content)
            return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except (json.JSONDecodeError, ValueError, OSError) as e:
        logger.warning(f"Impossibile leggere {NEWS_FAILURES_FILE}: {e}")
        return {}


def save_failures(failures: dict):
    try:
        NEWS_FAILURES_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(NEWS_FAILURES_FILE, "w", encoding="utf-8") as f:
            json.dump(failures, f)
    except OSError as e:
        logger.error(f"Impossibile salvare {NEWS_FAILURES_FILE}: {e}")
