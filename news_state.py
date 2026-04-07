import json, os

NEWS_STATE_FILE = "seen_news.json"

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
    except (json.JSONDecodeError, ValueError):
        return set()

def save_seen(seen: set):
    with open(NEWS_STATE_FILE, "w") as f:
        json.dump(list(seen), f)