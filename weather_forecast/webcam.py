import httpx
import random

WEBCAM_BASE_URL = "https://webcam.josefov.com/snimek.php"

async def fetch_webcam_snapshot() -> bytes | None:
    """
    Scarica il frame corrente della webcam.
    Il parametro rand è un cache-buster — va rigenerato ad ogni richiesta.
    """
    params = {"rand": random.randint(1000, 99999)}
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; BrutalAssaultBot/1.0)",
        "Referer": "https://webcam.josefov.com/",
    }
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(WEBCAM_BASE_URL, params=params, headers=headers)
            r.raise_for_status()
            if "image" not in r.headers.get("content-type", ""):
                return None
            return r.content
    except Exception as e:
        print(f"Errore webcam: {e}")
        return None


if __name__ == "__main__":
    import asyncio
    snapshot = asyncio.run(fetch_webcam_snapshot())
    if snapshot:
        with open("webcam.jpg", "wb") as f:
            f.write(snapshot)
        print("Snapshot salvato in webcam.jpg")
    else:
        print("Errore nel recuperare il frame della webcam")