"""
Scraper per il Ticket Exchange di Brutal Assault.
Recupera gli annunci attivi dalla pagina ufficiale.
"""

import logging
from typing import Optional
import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

XCHANGE_URL = "https://brutalassault.cz/en/xchange"
BASE_URL = "https://brutalassault.cz"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


async def fetch_listings() -> Optional[list[dict]]:
    """
    Recupera gli annunci dal Ticket Exchange.

    Returns:
        Lista di dict con chiavi: id, product, price, url
        None in caso di errore di rete o parsing.
    """
    try:
        async with httpx.AsyncClient(headers=HEADERS, timeout=15.0, follow_redirects=True) as client:
            response = await client.get(XCHANGE_URL)
            response.raise_for_status()
    except httpx.HTTPError as e:
        logger.error(f"Errore HTTP durante il fetch: {e}")
        return None

    try:
        return parse_listings(response.text)
    except Exception as e:
        logger.error(f"Errore durante il parsing: {e}")
        return None


def parse_listings(html: str) -> list[dict]:
    """
    Parsa l'HTML della pagina xchange ed estrae gli annunci.

    Returns:
        Lista di dict con chiavi: id, product, price, url
    """
    soup = BeautifulSoup(html, "html.parser")
    listings = []

    # La tabella degli annunci ha il titolo "Tickets exchange"
    table = None
    for h5 in soup.find_all("h5"):
        if "Tickets exchange" in h5.get_text():
            table = h5.find_next("table")
            break

    if not table:
        logger.warning("Tabella 'Tickets exchange' non trovata nella pagina.")
        return listings

    for row in table.find_all("tr"):
        cells = row.find_all("td")
        if len(cells) < 3:
            continue  # salta header e righe vuote

        product = cells[0].get_text(strip=True)
        price_raw = cells[1].get_text(strip=True)
        detail_link = cells[2].find("a")

        if not detail_link:
            continue

        href = detail_link.get("href", "")
        full_url = BASE_URL + href if href.startswith("/") else href

        # Estrai l'ID dall'URL: /en/xchange/detail/id/XXXXXX
        listing_id = href.rstrip("/").split("/")[-1]

        # Pulisci il prezzo: "€ 197.83" -> "197.83"
        price = price_raw.replace("€", "").strip()

        listings.append({
            "id": listing_id,
            "product": product,
            "price": price,
            "url": full_url,
        })

    logger.info(f"Trovati {len(listings)} annunci in totale.")
    return listings