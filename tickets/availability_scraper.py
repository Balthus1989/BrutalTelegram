"""
Scraper della disponibilità dei biglietti in vendita sul sito ufficiale.

Ogni prodotto dello shop mostra una barra "Available" con la percentuale di
biglietti ancora acquistabili:

    <div class="product_availability"><span>Available</span>:
      <div id="progress" class="graph">
        <div id="bar" class="orange" style="width:35.012386457473%"><p>35%</p></div>
      </div>
    </div>

La percentuale precisa sta nello stile inline (il <p> è arrotondato all'intero),
ed è quella usata per calcolare le soglie di allerta.
"""

import asyncio
import logging
import os
import re
from typing import Optional

import httpx
from bs4 import BeautifulSoup

from tickets.ticket_scraper import BASE_URL, HEADERS

logger = logging.getLogger(__name__)

TICKETS_URL = "https://brutalassault.cz/en/tickets"

# Solo i prodotti il cui nome contiene questo testo vengono monitorati: senza
# filtro finirebbero sotto osservazione anche i gift voucher, la cui percentuale
# è impostata a mano e non dice nulla sui biglietti del festival.
# Da aggiornare a ogni edizione (o via ambiente).
PRODUCT_MATCH = os.getenv("TICKET_PRODUCT_MATCH", "2027")

# Tetto ai fetch delle schede prodotto in un ciclo: se un giorno lo shop
# pubblicasse decine di articoli, il polling ogni 5 minuti resta sostenibile.
MAX_PRODUCT_FETCHES = 25

FETCH_TIMEOUT = 20.0

# Scaglione delle notifiche: si avvisa a ogni multiplo di 5% attraversato.
ALERT_STEP = 5

_WIDTH_RE = re.compile(r"width\s*:\s*([0-9]+(?:[.,][0-9]+)?)\s*%")


def level_of(percent: float) -> int:
    """
    Scaglione di appartenenza di una percentuale: il multiplo di ALERT_STEP
    subito inferiore o uguale (35.01 -> 35, 34.99 -> 30, 4.2 -> 0).

    Non è la soglia da annunciare ma la banda in cui si trova la disponibilità:
    le soglie effettivamente superate le calcola levels_crossed().
    """
    if percent <= 0:
        return 0
    return min(100, int(percent // ALERT_STEP) * ALERT_STEP)


def levels_crossed(previous_level: int, new_level: int) -> list[int]:
    """
    Soglie superate verso il basso passando dalla banda `previous_level` alla
    banda `new_level`, dalla più alta alla più bassa.

    Uscire dalla banda 35 significa essere scesi sotto il 35%: da 35.01% a 29.4%
    (banda 35 -> banda 25) le soglie superate sono [35, 30], perché il 29,4% è
    sotto il 30% ma non ancora sotto il 25%. L'ultima della lista è quella da
    annunciare; le altre sono gli scaglioni bruciati tra due controlli.
    """
    if new_level >= previous_level:
        return []
    return list(range(previous_level, new_level, -ALERT_STEP))


def _has_sold_out_badge(node) -> bool:
    """True se nel blocco compare il badge rosso 'SOLD OUT' del template."""
    if node is None:
        return False
    return bool(
        node.find(class_=lambda c: bool(c) and ("sold_out" in c or "product_sold_out" in c))
    )


def parse_product_links(html: str) -> Optional[list[dict]]:
    """
    Estrae i prodotti dalla pagina elenco dei biglietti.

    Returns:
        Lista di dict con chiavi: id, title, url, sold_out.
        None se la struttura della pagina non è riconoscibile: in quel caso il
        chiamante NON deve dedurre che i biglietti tracciati siano esauriti.
    """
    soup = BeautifulSoup(html, "html.parser")
    anchors = soup.select("a.product_title")

    if not anchors:
        logger.warning(
            "Nessun prodotto trovato nella pagina biglietti: struttura non riconosciuta, "
            "ciclo saltato per non annunciare sold out inesistenti."
        )
        return None

    products: dict[str, dict] = {}
    for a in anchors:
        href = a.get("href", "")
        if "/detail/id/" not in href:
            continue
        url = BASE_URL + href if href.startswith("/") else href
        product_id = href.rstrip("/").split("/")[-1]
        # Il titolo nell'elenco è troncato: serve solo per un primo filtro,
        # il nome completo viene letto dalla scheda prodotto.
        title = a.get_text(strip=True)
        products[product_id] = {
            "id": product_id,
            "title": title,
            "url": url,
            "sold_out": _has_sold_out_badge(a.find_parent(class_="product-item")),
        }

    return list(products.values())


def parse_availability(html: str) -> tuple[Optional[float], bool, Optional[str]]:
    """
    Legge la scheda di un prodotto.

    Returns:
        (percent, sold_out, name)
        percent è None se la barra di disponibilità non è leggibile: in quel caso
        il ciclo va saltato, non interpretato come esaurimento.
    """
    soup = BeautifulSoup(html, "html.parser")

    heading = soup.find("h1")
    name = heading.get_text(strip=True) if heading else None

    block = soup.find(class_="product_availability")
    sold_out = _has_sold_out_badge(soup.find(class_="product_image-wrap")) or _has_sold_out_badge(block)

    percent = None
    if block is not None:
        bar = block.find(id="bar") or block.find(style=_WIDTH_RE)
        match = _WIDTH_RE.search(bar.get("style", "")) if bar is not None else None
        if match:
            percent = float(match.group(1).replace(",", "."))
            percent = max(0.0, min(100.0, percent))

    if percent is None and sold_out:
        percent = 0.0

    return percent, sold_out, name


async def _fetch_product(client: httpx.AsyncClient, product: dict) -> Optional[dict]:
    """Scarica una scheda prodotto e ne estrae nome e disponibilità."""
    try:
        response = await client.get(product["url"])
        response.raise_for_status()
    except httpx.HTTPError as e:
        logger.warning(f"Scheda prodotto {product['id']} non raggiungibile: {e}")
        return None

    try:
        percent, sold_out, name = parse_availability(response.text)
    except Exception as e:
        logger.warning(f"Scheda prodotto {product['id']} non parsabile: {e}")
        return None

    return {
        "id": product["id"],
        "name": name or product["title"],
        "url": product["url"],
        "percent": percent,
        "sold_out": sold_out or product["sold_out"],
    }


def _matches(text: Optional[str]) -> bool:
    return bool(text) and PRODUCT_MATCH.lower() in text.lower()


async def fetch_ticket_availability() -> Optional[list[dict]]:
    """
    Recupera la disponibilità dei biglietti dell'edizione monitorata.

    Returns:
        Lista di dict con chiavi: id, name, url, percent, sold_out.
        Lista vuota se nessun prodotto corrisponde (nessun biglietto in vendita).
        None in caso di errore di rete o di pagina non riconoscibile.
    """
    try:
        async with httpx.AsyncClient(
            headers=HEADERS, timeout=FETCH_TIMEOUT, follow_redirects=True
        ) as client:
            response = await client.get(TICKETS_URL)
            response.raise_for_status()

            try:
                products = parse_product_links(response.text)
            except Exception as e:
                logger.error(f"Errore durante il parsing della pagina biglietti: {e}")
                return None

            if products is None:
                return None

            # Il titolo nell'elenco è troncato: un prodotto con titolo tagliato va
            # verificato sulla scheda completa, altrimenti un "... 2027 ..." oltre
            # il troncamento sfuggirebbe al filtro.
            candidates = [
                p for p in products if _matches(p["title"]) or p["title"].endswith("...")
            ][:MAX_PRODUCT_FETCHES]

            if not candidates:
                logger.info(f"Nessun prodotto '{PRODUCT_MATCH}' nella pagina biglietti.")
                return []

            results = await asyncio.gather(*(_fetch_product(client, p) for p in candidates))
    except httpx.HTTPError as e:
        logger.error(f"Errore HTTP sulla pagina biglietti: {e}")
        return None

    found = [r for r in results if r is not None and _matches(r["name"])]
    logger.info(
        f"Disponibilità biglietti '{PRODUCT_MATCH}': {len(found)} prodotti monitorati. "
        + " | ".join(
            f"{r['name'][:40]}: {'SOLD OUT' if r['sold_out'] else r['percent']}" for r in found
        )
    )
    return found
