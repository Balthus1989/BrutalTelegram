import json, os

NEWS_STATE_FILE = "seen_news.json"

def load_seen() -> set:
    if os.path.exists(NEWS_STATE_FILE):
        with open(NEWS_STATE_FILE) as f:
            return set(json.load(f))
    return set()

def save_seen(seen: set):
    with open(NEWS_STATE_FILE, "w") as f:
        json.dump(list(seen), f)