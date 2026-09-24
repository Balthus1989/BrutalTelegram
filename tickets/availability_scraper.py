"""
Scraper della disponibilità dei prodotti in vendita sullo shop ufficiale.

Lo stesso motore serve due pagine: i biglietti del festival e gli alloggi
(hotel e campeggi). Sono sezioni diverse dello stesso shop — le schede stanno
tutte sotto /en/tickets/detail/id/ e usano lo stesso template — quindi il
parsing è identico e cambiano solo la pagina elenco e il filtro sui nomi.

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
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
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
# È un parametro e non una costante globale perché la pagina alloggi ne elenca
# già più di quanti bastino ai biglietti: con un tetto unico i prodotti oltre il
# limite verrebbero scartati in silenzio.
MAX_PRODUCT_FETCHES = 25

# Schede prodotto scaricate insieme, e pausa di ogni connessione tra una
# richiesta e la successiva. Con 8 schede alla volta e nessuna pausa il sito
# rispondeva 429 Too Many Requests a tutte le schede alloggi: il bot non vedeva
# più niente, nemmeno i sold out veri.
MAX_CONCURRENT_FETCHES = 2
REQUEST_PAUSE = 0.5

# Risposta 429: si aspetta quanto chiede il sito (Retry-After) o, se non lo
# dice, RETRY_BACKOFF secondi raddoppiati a ogni tentativo, mai oltre
# MAX_RETRY_WAIT. Dopo MAX_RETRIES tentativi falliti il ciclo smette di
# chiedere schede: insistere prolungherebbe solo il blocco.
MAX_RETRIES = 2
RETRY_BACKOFF = 5.0
MAX_RETRY_WAIT = 30.0

# Punti unici da cui passano attese e orologio, così i test non aspettano davvero.
_sleep = asyncio.sleep


def _now() -> float:
    return asyncio.get_running_loop().time()


# Una lettura dello shop alla volta, qualunque pagina sia. Biglietti e alloggi
# hanno la stessa cadenza e partivano nello stesso secondo: le due raffiche di
# schede sommate facevano rispondere al sito 429 Too Many Requests su quasi
# tutti i prodotti.
_SHOP_LOCK = asyncio.Lock()

FETCH_TIMEOUT = 20.0

# Scaglione delle notifiche: si avvisa a ogni multiplo di 5% attraversato.
ALERT_STEP = 5

_WIDTH_RE = re.compile(r"width\s*:\s*([0-9]+(?:[.,][0-9]+)?)\s*%")

# Un prodotto esaurito non mostra la barra: al suo posto la scheda scrive
#
#     <div class="product_availability"><span>Available</span>:
#       <strong style="color:#d50628">Sold out</strong>
#     </div>
#
# cioè un testo, senza nessuna classe CSS da cui riconoscerlo. È il caso di
# tutti i biglietti esauriti visti sul sito: senza questo controllo la loro
# disponibilità risultava semplicemente "non leggibile".
_SOLD_OUT_TEXT_RE = re.compile(r"sold\s*out", re.IGNORECASE)


def is_sold_out(product: dict) -> bool:
    """
    True se il biglietto risulta esaurito: badge del sito o disponibilità a zero.

    Una disponibilità non leggibile (percent None e nessun badge) non è un
    esaurimento. Definizione unica per scraper, stato e messaggi: quando ognuno
    aveva la sua, un prodotto esaurito senza barra risultava sold out per il
    comando /availability e "illeggibile" per il ciclo di controllo, che lo
    saltava senza annunciare niente.
    """
    percent = product.get("percent")
    return bool(product.get("sold_out")) or (percent is not None and percent <= 0)


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


def levels_crossed_up(previous_level: int, new_level: int) -> list[int]:
    """
    Soglie superate verso l'alto passando dalla banda `previous_level` alla
    banda `new_level`, dalla più bassa alla più alta.

    È il caso opposto di levels_crossed(): gli organizzatori immettono in
    vendita una nuova tranche e la disponibilità risale. Entrare nella banda 20
    significa essere tornati sopra il 20%: da 10.5% a 22% (banda 10 -> banda 20)
    le soglie superate sono [15, 20]. L'ultima della lista è quella da
    annunciare, le altre sono gli scaglioni risaliti tra due controlli.
    """
    if new_level <= previous_level:
        return []
    return list(range(previous_level + ALERT_STEP, new_level + ALERT_STEP, ALERT_STEP))


def _has_sold_out_badge(node) -> bool:
    """True se nel blocco compare il badge rosso 'SOLD OUT' del template."""
    if node is None:
        return False
    return bool(
        node.find(class_=lambda c: bool(c) and ("sold_out" in c or "product_sold_out" in c))
    )


def parse_product_links(html: str) -> Optional[list[dict]]:
    """
    Estrae i prodotti da una pagina elenco dello shop (biglietti o alloggi).

    Returns:
        Lista di dict con chiavi: id, title, url, sold_out.
        None se la struttura della pagina non è riconoscibile: in quel caso il
        chiamante NON deve dedurre che i prodotti tracciati siano esauriti.
    """
    soup = BeautifulSoup(html, "html.parser")
    anchors = soup.select("a.product_title")

    if not anchors:
        logger.warning(
            "Nessun prodotto trovato nella pagina elenco: struttura non riconosciuta, "
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
        il ciclo va saltato, non interpretato come esaurimento. Se invece il
        prodotto è dichiarato esaurito percent è 0.0, perché la barra non c'è
        proprio e senza questo la disponibilità resterebbe "non leggibile".
    """
    soup = BeautifulSoup(html, "html.parser")

    heading = soup.find("h1")
    name = heading.get_text(strip=True) if heading else None

    block = soup.find(class_="product_availability")
    sold_out = (
        _has_sold_out_badge(soup.find(class_="product_image-wrap"))
        or _has_sold_out_badge(block)
        # Il "Sold out" scritto in chiaro al posto della barra: cercato solo
        # dentro il blocco disponibilità, così un "sold out" nella descrizione
        # o in un altro punto della pagina non esaurisce il biglietto.
        or (block is not None and bool(_SOLD_OUT_TEXT_RE.search(block.get_text(" ", strip=True))))
    )

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


def retry_wait(response: httpx.Response, attempt: int) -> float:
    """
    Secondi da aspettare dopo un 429.

    Retry-After può essere un numero di secondi o una data HTTP; se manca o non
    si legge si ripiega sul backoff esponenziale. In ogni caso mai oltre
    MAX_RETRY_WAIT: il ciclo tiene il lock dello shop mentre aspetta.
    """
    value = response.headers.get("Retry-After", "").strip()
    wait = None
    if value.isdigit():
        wait = float(value)
    elif value:
        try:
            when = parsedate_to_datetime(value)
            if when.tzinfo is None:
                when = when.replace(tzinfo=timezone.utc)
            wait = (when - datetime.now(timezone.utc)).total_seconds()
        except (TypeError, ValueError):
            wait = None
    if wait is None or wait < 0:
        wait = RETRY_BACKOFF * (2 ** attempt)
    return min(wait, MAX_RETRY_WAIT)


class _Throttle:
    """
    Stato condiviso da un ciclo di letture.

    resume_at: istante (orologio del loop) prima del quale nessuna connessione
    manda richieste. Un 429 ferma tutte le connessioni, non solo quella che l'ha
    ricevuto: altrimenti, mentre una aspetta, l'altra continua a chiedere schede
    nuove e ognuna si prende il suo primo 429.
    strikes: 429 consecutivi nel ciclo, qualunque sia la pagina. Contati per
    ciclo e non per pagina: il semaforo serve le richieste in ordine di arrivo,
    e il ritentativo di una scheda finisce in coda dietro a tutte le altre, che
    farebbero comunque il loro primo tentativo prima che una arrivi al terzo.
    hit: troppi 429 di fila, il ciclo smette di chiedere.
    """

    def __init__(self) -> None:
        self.hit = False
        self.strikes = 0
        self.resume_at = 0.0

    def pause(self, seconds: float) -> None:
        self.resume_at = max(self.resume_at, _now() + seconds)

    async def wait(self) -> None:
        delay = self.resume_at - _now()
        if delay > 0:
            await _sleep(delay)


async def _get_page(
    client: httpx.AsyncClient,
    url: str,
    semaphore: asyncio.Semaphore,
    throttle: _Throttle,
) -> Optional[httpx.Response]:
    """
    GET con pausa tra le richieste e nuovi tentativi sul 429.

    Returns:
        La risposta, oppure None se il sito continua a rispondere 429: la
        pagina va trattata come non letta. Gli altri errori HTTP vengono
        sollevati.
    """
    for _ in range(MAX_RETRIES + 1):
        async with semaphore:
            await throttle.wait()
            if throttle.hit:
                return None
            response = await client.get(url)
            if response.status_code == 429:
                throttle.strikes += 1
                if throttle.strikes > MAX_RETRIES:
                    throttle.hit = True
                    logger.warning(
                        f"Il sito risponde 429 a {throttle.strikes} richieste di fila: "
                        f"le schede restanti si leggono al prossimo ciclo."
                    )
                    return None
                # Prima di liberare il posto: chi entra dopo trova già la pausa.
                wait = retry_wait(response, throttle.strikes - 1)
                throttle.pause(wait)
                logger.info(f"429 da {url}: tutte le richieste in pausa per {wait:.0f}s.")
            else:
                throttle.strikes = 0
            await _sleep(REQUEST_PAUSE)
        if response.status_code != 429:
            response.raise_for_status()
            return response

    # Tentativi di questa pagina finiti mentre le altre passano: si rinuncia
    # solo a lei, il ciclo continua.
    logger.warning(f"{url} ancora rifiutata con 429: non letta in questo ciclo.")
    return None


async def _fetch_product(
    client: httpx.AsyncClient,
    product: dict,
    semaphore: asyncio.Semaphore,
    throttle: _Throttle,
) -> dict:
    """
    Scarica una scheda prodotto e ne estrae nome e disponibilità.

    Una scheda non scaricata o non parsabile esce come disponibilità non
    leggibile (percent None), mai scartata: il prodotto è comunque nell'elenco,
    e se sparisse dal risultato il ciclo lo conterebbe come assente. Bastavano
    due cicli di 429 dal sito per un falso SOLD OUT, seguito da un secondo
    annuncio "nuovo biglietto" quando la scheda tornava leggibile.
    """
    unreadable = {
        "id": product["id"],
        "name": product["title"],
        "url": product["url"],
        "percent": None,
        # Nemmeno il badge dell'elenco basta a dichiararlo esaurito: con la
        # scheda mancante il nome è quello troncato dell'elenco, e un sold out
        # si annuncia solo su una lettura completa.
        "sold_out": False,
        "unreadable": True,
    }

    try:
        response = await _get_page(client, product["url"], semaphore, throttle)
    except httpx.HTTPError as e:
        logger.warning(f"Scheda prodotto {product['id']} non raggiungibile: {e}")
        return unreadable
    if response is None:
        # Ciclo fermato dal 429, già segnalato una volta sola.
        return unreadable

    try:
        percent, sold_out, name = parse_availability(response.text)
    except Exception as e:
        logger.warning(f"Scheda prodotto {product['id']} non parsabile: {e}")
        return unreadable

    # Il badge <span class="sold_out"> dell'elenco vale quanto la scheda: sono
    # due segnali indipendenti dello stesso esaurimento, e basta che uno dei due
    # regga a un cambio di template perché il sold out venga comunque visto.
    sold_out = sold_out or product["sold_out"]

    # Senza questo ripiego il prodotto esaurito resterebbe con percent None,
    # cioè "disponibilità non leggibile", e il ciclo lo salterebbe a ogni
    # controllo senza mai annunciare il sold out.
    if percent is None and sold_out:
        percent = 0.0

    return {
        "id": product["id"],
        "name": name or product["title"],
        "url": product["url"],
        "percent": percent,
        "sold_out": sold_out,
    }


def _matches(text: Optional[str], match: str) -> bool:
    """
    True se il nome del prodotto rientra nel filtro.

    Un filtro vuoto non filtra niente: è il caso della pagina alloggi, dove
    tutto quello che è elencato riguarda l'edizione in corso e non ci sono
    voucher da escludere.
    """
    if not match:
        return True
    return bool(text) and match.lower() in text.lower()


def select_candidates(
    products: list[dict],
    match: str,
    max_fetches: int,
    label: str = "prodotti",
) -> list[dict]:
    """
    Prodotti della pagina elenco di cui vale la pena scaricare la scheda.

    Il titolo nell'elenco può essere troncato: un prodotto con titolo tagliato
    va verificato sulla scheda completa, altrimenti un "... 2027 ..." oltre il
    troncamento sfuggirebbe al filtro.
    """
    candidates = [
        p for p in products if _matches(p["title"], match) or p["title"].endswith("...")
    ]

    # Il taglio va segnalato: i prodotti oltre il tetto non vengono controllati,
    # e senza una riga nei log il loro sold out mancante sembrerebbe un bug
    # dello scraper. È il caso in cui è già incappata la pagina alloggi, che da
    # sola elenca più prodotti del tetto pensato per i biglietti.
    if len(candidates) > max_fetches:
        logger.warning(
            f"Pagina {label}: {len(candidates)} prodotti da controllare, "
            f"tetto a {max_fetches}. I restanti non vengono monitorati: "
            f"alza max_fetches."
        )
        candidates = candidates[:max_fetches]

    return candidates


async def fetch_shop_availability(
    page_url: str,
    match: str = "",
    label: str = "prodotti",
    max_fetches: int = MAX_PRODUCT_FETCHES,
) -> Optional[list[dict]]:
    """
    Recupera la disponibilità dei prodotti elencati in una pagina dello shop.

    Args:
        page_url: pagina elenco da leggere (biglietti o alloggi).
        match: testo che il nome deve contenere; stringa vuota per non filtrare.
        label: come chiamare questi prodotti nei log.
        max_fetches: tetto alle schede prodotto scaricate in un ciclo.

    Returns:
        Lista di dict con chiavi: id, name, url, percent, sold_out. Un prodotto
        in elenco la cui scheda non è stata letta c'è comunque, con percent
        None e unreadable True.
        Lista vuota se nessun prodotto corrisponde (niente in vendita).
        None in caso di errore di rete o di pagina non riconoscibile — in quel
        caso il chiamante NON deve dedurre nessun esaurimento.
    """
    async with _SHOP_LOCK:
        return await _fetch_shop_availability(page_url, match, label, max_fetches)


async def _fetch_shop_availability(
    page_url: str, match: str, label: str, max_fetches: int
) -> Optional[list[dict]]:
    try:
        async with httpx.AsyncClient(
            headers=HEADERS, timeout=FETCH_TIMEOUT, follow_redirects=True
        ) as client:
            semaphore = asyncio.Semaphore(MAX_CONCURRENT_FETCHES)
            throttle = _Throttle()

            response = await _get_page(client, page_url, semaphore, throttle)
            if response is None:
                logger.error(f"Pagina {label} rifiutata con 429: ciclo saltato.")
                return None

            try:
                products = parse_product_links(response.text)
            except Exception as e:
                logger.error(f"Errore durante il parsing della pagina {label}: {e}")
                return None

            if products is None:
                return None

            candidates = select_candidates(products, match, max_fetches, label)

            if not candidates:
                logger.info(
                    f"Nessun prodotto{f' “{match}”' if match else ''} nella pagina {label}."
                )
                return []

            results = await asyncio.gather(
                *(_fetch_product(client, p, semaphore, throttle) for p in candidates)
            )
    except httpx.HTTPError as e:
        logger.error(f"Errore HTTP sulla pagina {label}: {e}")
        return None

    # Le schede non lette restano tutte, anche se il nome troncato dell'elenco
    # non passa il filtro: potrebbe essere un prodotto tracciato, e toglierlo
    # dal risultato vorrebbe dire contarlo come sparito. Il ciclo le salta senza
    # dedurne niente.
    found = [r for r in results if r.get("unreadable") or _matches(r["name"], match)]
    unreadable = sum(1 for r in found if r.get("unreadable"))
    logger.info(
        f"Disponibilità {label}: {len(found)} prodotti monitorati"
        + (f", {unreadable} con scheda non letta" if unreadable else "")
        + ". "
        + " | ".join(
            f"{r['name'][:40]}: "
            f"{'non letta' if r.get('unreadable') else 'SOLD OUT' if r['sold_out'] else r['percent']}"
            for r in found
        )
    )
    return found


async def fetch_ticket_availability() -> Optional[list[dict]]:
    """Disponibilità dei biglietti dell'edizione monitorata."""
    return await fetch_shop_availability(
        TICKETS_URL,
        match=PRODUCT_MATCH,
        label=f"biglietti “{PRODUCT_MATCH}”",
        max_fetches=MAX_PRODUCT_FETCHES,
    )
