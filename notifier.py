"""
Modulo per l'invio delle notifiche Telegram.
"""

import asyncio
import html
import math
import logging
import io
from dataclasses import dataclass
from telegram import Bot
from telegram.ext import Application
from telegram.error import BadRequest, TelegramError
from telegram.constants import ParseMode

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from news.news_scraper import fetch_article
from tickets.availability_scraper import ALERT_STEP, is_sold_out, level_of
from tickets.ticket_scraper import get_face_value
from weather_forecast.weather import (
    FESTIVAL_END,
    FESTIVAL_START,
    MESI_IT,
    fetch_weather_festival,
    format_weather_festival,
)
from weather_forecast.webcam import fetch_webcam_snapshot
from translator import translate

logger = logging.getLogger(__name__)

# Limite di Telegram per la caption di una foto (il testo di un messaggio arriva a 4096).
MAX_CAPTION_LENGTH = 1024

# Limite di Telegram per il testo di un messaggio. Superarlo non tronca: l'invio
# viene rifiutato e il messaggio non arriva affatto.
MAX_MESSAGE_LENGTH = 4096

# Rifiuti di Telegram che dipendono dalla configurazione del gruppo, non dal bot:
# da soli ("Topic_closed") non dicono cosa fare, e il messaggio continua a fallire
# a ogni ciclo finché qualcuno non interviene nel gruppo.
_ERROR_HINTS = (
    (
        "topic_closed",
        "il topic di destinazione è chiuso: riaprilo dal gruppo (topic → menu → "
        "Riapri argomento) oppure dai al bot il permesso di amministratore "
        "«Gestire i topic», che consente di pubblicare anche nei topic chiusi",
    ),
    (
        "message thread not found",
        "il topic indicato non esiste in questo gruppo: controlla TELEGRAM_*_TOPIC_ID, "
        "oppure lascialo vuoto per pubblicare nel topic General",
    ),
    (
        "not enough rights",
        "il bot non ha i permessi necessari nel gruppo: rendilo amministratore",
    ),
    (
        "chat not found",
        "TELEGRAM_CHAT_ID non corrisponde a nessun gruppo visibile al bot",
    ),
    (
        "bot was kicked",
        "il bot è stato rimosso dal gruppo: reinseriscilo e rendilo amministratore",
    ),
)


def explain(e) -> str:
    """Errore Telegram con, quando riconoscibile, l'azione che lo risolve."""
    testo = str(e)
    minuscolo = testo.lower()
    for marker, hint in _ERROR_HINTS:
        if marker in minuscolo:
            return f"{testo} — {hint}"
    return testo


def thread_kwargs(topic_id) -> dict:
    """
    Costruisce i kwargs per il thread di destinazione.

    Nel topic "General" dei forum (thread id 1) e nei gruppi/canali senza topic
    il parametro message_thread_id NON va passato, altrimenti Telegram risponde
    "Bad Request: message thread not found" e il messaggio non viene inviato.
    """
    if topic_id is None or topic_id == "":
        return {}
    try:
        tid = int(topic_id)
    except (TypeError, ValueError):
        logger.warning(f"Topic id non valido ({topic_id!r}): pubblico nel topic General.")
        return {}
    return {"message_thread_id": tid} if tid > 1 else {}


def format_price_delta(listing_price: float, face_value: float) -> str:
    delta = listing_price - face_value
    pct = (delta / face_value) * 100
    if abs(delta) < 0.01:
        return "🟰 Prezzo originale"
    elif delta > 0:
        return f"🔺 +€{delta:.2f} (+{pct:.0f}%) rispetto all'originale"
    else:
        return f"🔻 -€{abs(delta):.2f} (-{abs(pct):.0f}%) rispetto all'originale"


def format_listing_message(listing: dict, plain: bool = False) -> str:
    """
    Formatta un annuncio come messaggio Telegram.

    Args:
        plain: senza formattazione Markdown, usato come fallback quando il nome
               del prodotto contiene caratteri che Telegram non riesce a parsare.
    """
    try:
        listing_price = float(listing["price"])
    except (ValueError, TypeError):
        listing_price = None

    face_value = get_face_value(listing["product"])

    delta_line = ""
    if listing_price is not None and face_value is not None:
        delta_line = f"\n📊 {format_price_delta(listing_price, face_value)}"

    if plain:
        return (
            f"🔔 Nuovo annuncio sul Ticket Exchange!\n\n"
            f"🎟️ {listing['product']}\n"
            f"💶 Prezzo: € {listing['price']}{delta_line}\n\n"
            f"👉 {listing['url']}\n\n"
            f"🏰 Brutal Assault — Josefov"
        )

    return (
        f"🔔 *Nuovo annuncio sul Ticket Exchange!*\n\n"
        f"🎟️ *{listing['product']}*\n"
        f"💶 Prezzo: *€ {listing['price']}*{delta_line}\n\n"
        f"👉 [Vedi annuncio]({listing['url']})\n\n"
        f"🏰 _Brutal Assault {FESTIVAL_START.year} — Josefov, "
        f"{FESTIVAL_START.day}-{FESTIVAL_END.day} {MESI_IT[FESTIVAL_END.month - 1].capitalize()}_"
    )


async def notify_new_listings(
    bot: Bot,
    chat_id: str,
    listings: list[dict],
    topic_id: int = None,
) -> dict[str, int]:
    """
    Invia una notifica per ogni nuovo annuncio.

    Returns:
        Dict { listing_id -> telegram_message_id } per gli annunci notificati con successo.
    """
    sent = {}
    kwargs = thread_kwargs(topic_id)

    for listing in listings:
        try:
            message = format_listing_message(listing)
            result = await bot.send_message(
                chat_id=chat_id,
                text=message,
                parse_mode=ParseMode.MARKDOWN,
                disable_web_page_preview=False,
                **kwargs,
            )
            logger.info(
                f"Notifica inviata per annuncio ID: {listing['id']} "
                f"(message_id: {result.message_id}) — topic: {topic_id or 'generale'}"
            )
            sent[listing["id"]] = result.message_id

        except BadRequest as e:
            # Un nome prodotto con caratteri Markdown (* _ [ ) farebbe fallire l'invio
            # a ogni ciclo: ripubblica senza formattazione invece di riprovare all'infinito.
            if "parse" not in str(e).lower():
                logger.error(f"Errore Telegram per annuncio {listing['id']}: {explain(e)}")
                continue
            logger.warning(
                f"Markdown non valido per annuncio {listing['id']} ({e}) — invio senza formattazione."
            )
            try:
                result = await bot.send_message(
                    chat_id=chat_id,
                    text=format_listing_message(listing, plain=True),
                    disable_web_page_preview=False,
                    **kwargs,
                )
                sent[listing["id"]] = result.message_id
            except TelegramError as e2:
                logger.error(f"Errore Telegram per annuncio {listing['id']}: {explain(e2)}")

        except TelegramError as e:
            logger.error(f"Errore Telegram per annuncio {listing['id']}: {explain(e)}")

    return sent


# Errori che indicano un messaggio già assente: l'annuncio è da considerare chiuso
_GONE_MARKERS = (
    "message to delete not found",
    "message to edit not found",
    "message can't be found",
    "message identifier is not specified",
)

# Errori per cui delete non è possibile ma il messaggio esiste ancora:
# tipicamente il limite di 48 ore o permessi di amministratore mancanti.
_UNDELETABLE_MARKERS = (
    "message can't be deleted",
    "not enough rights",
    "message can't be deleted for everyone",
)


def format_sold_message(record: dict) -> str:
    """
    Testo con cui sostituire l'annuncio quando il messaggio non può essere eliminato.
    Senza Markdown: il nome del prodotto arriva dal sito e potrebbe contenere
    caratteri che farebbero fallire anche la modifica.
    """
    product = record.get("product") or "Biglietto"
    price = record.get("price")
    price_line = f"\n💶 Prezzo richiesto: € {price}" if price else ""
    return (
        f"❌ VENDUTO — annuncio non più disponibile\n\n"
        f"🎟️ {product}{price_line}\n\n"
        f"🏰 Brutal Assault — Josefov"
    )


async def _mark_as_sold(bot: Bot, chat_id: str, message_id: int, record: dict) -> bool:
    """
    Fallback quando delete_message non è possibile (messaggio più vecchio di 48 ore
    o bot senza permessi di amministratore): riscrive il messaggio come "venduto".
    A differenza dell'eliminazione, la modifica dei propri messaggi non ha limiti di tempo.

    Returns:
        True se il messaggio è stato aggiornato (o non esiste più).
    """
    try:
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=format_sold_message(record),
            disable_web_page_preview=True,
        )
        logger.info(f"Messaggio {message_id} marcato come VENDUTO (eliminazione non consentita).")
        return True
    except BadRequest as e:
        text = str(e).lower()
        if "message is not modified" in text or any(m in text for m in _GONE_MARKERS):
            return True
        logger.warning(f"Impossibile marcare come venduto il messaggio {message_id}: {e}")
        return False
    except TelegramError as e:
        logger.warning(f"Errore Telegram marcando come venduto il messaggio {message_id}: {e}")
        return False


async def resolve_sold_messages(
    bot: Bot,
    sold_records: dict[str, dict],
    default_chat_id: str,
) -> tuple[set[str], set[str]]:
    """
    Rimuove dal gruppo i messaggi dei biglietti venduti.

    Prova prima l'eliminazione; se Telegram la rifiuta (limite di 48 ore o permessi
    mancanti) riscrive il messaggio segnalando che l'annuncio è venduto, così non
    resta mai un annuncio non più valido nel gruppo.

    Args:
        bot: istanza del bot Telegram
        sold_records: Dict { listing_id -> record di stato }
        default_chat_id: chat da usare per i record salvati senza chat_id

    Returns:
        (resolved, retry): gli id gestiti definitivamente e quelli da ritentare
        al ciclo successivo (errori di rete o temporanei).
    """
    resolved: set[str] = set()
    retry: set[str] = set()

    for listing_id, record in sold_records.items():
        message_id = record.get("message_id")
        chat = record.get("chat_id") or default_chat_id

        if not message_id:
            # Nessun messaggio associato: niente da eliminare
            resolved.add(listing_id)
            continue

        try:
            await bot.delete_message(chat_id=chat, message_id=message_id)
            logger.info(
                f"Messaggio eliminato per biglietto venduto ID: {listing_id} (message_id: {message_id})"
            )
            resolved.add(listing_id)
            continue
        except BadRequest as e:
            text = str(e).lower()
            if any(marker in text for marker in _GONE_MARKERS):
                logger.info(f"Messaggio {message_id} già assente per annuncio {listing_id}.")
                resolved.add(listing_id)
                continue
            if not any(marker in text for marker in _UNDELETABLE_MARKERS):
                logger.warning(
                    f"Eliminazione rifiutata per il messaggio {message_id} "
                    f"(annuncio {listing_id}): {e}"
                )
            else:
                logger.info(
                    f"Messaggio {message_id} non eliminabile ({e}) — "
                    f"provo a marcarlo come venduto."
                )
        except TelegramError as e:
            # Errore di rete/temporaneo: mantieni lo stato e ritenta al prossimo ciclo
            logger.warning(
                f"Errore temporaneo eliminando il messaggio {message_id} "
                f"per annuncio {listing_id}: {e}"
            )
            retry.add(listing_id)
            continue

        if await _mark_as_sold(bot, chat, message_id, record):
            resolved.add(listing_id)
        else:
            retry.add(listing_id)

    return resolved, retry


async def delete_sold_messages(
    bot: Bot,
    chat_id: str,
    sold_listings: dict[str, int],
) -> None:
    """
    Compatibilità: elimina i messaggi a partire da { listing_id -> message_id }.
    """
    records = {lid: {"message_id": mid} for lid, mid in sold_listings.items()}
    await resolve_sold_messages(bot, records, chat_id)


# Lunghezza massima del testo tradotto in caption: il resto dei 1024 caratteri
# ammessi da Telegram serve al titolo e all'emoji.
MAX_NEWS_TEXT_LENGTH = 900


def format_news_caption(titolo: str, testo: str, plain: bool = False) -> str:
    """
    Caption di una news: titolo in grassetto e testo tradotto.

    Titolo e testo arrivano dal sito e passano dal traduttore: contengono
    caratteri (* _ [ < &) che Telegram interpreterebbe come formattazione e che
    con Markdown facevano fallire l'invio ("can't find end of the entity").
    Come per gli alert di disponibilità si usa HTML, con tutto il testo che
    viene dal sito passato per html.escape.

    Args:
        plain: senza formattazione, ultima rete di sicurezza se Telegram
               rifiuta anche l'HTML.
    """
    testo_breve = (
        testo[:MAX_NEWS_TEXT_LENGTH] + "..." if len(testo) > MAX_NEWS_TEXT_LENGTH else testo
    )
    if plain:
        return f"🤘 {titolo}\n\n{testo_breve}"
    return f"🤘 <b>{html.escape(titolo)}</b>\n\n{html.escape(testo_breve)}"


async def send_news(bot: Bot, chat_id: str, topic_id: int, articolo: dict):
    """Invia una news con immagine, testo tradotto e bottone link"""

    # Scrapa e traduce
    dettagli = await fetch_article(articolo["url"])
    # translate() usa requests (sincrono): eseguito nel loop bloccherebbe lo
    # scheduler, facendo saltare i tick di ticket e meteo.
    titolo_it = await asyncio.to_thread(translate, articolo["titolo"])
    testo_it = await asyncio.to_thread(translate, dettagli["testo"])

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📰 Leggi l'articolo originale", url=articolo["url"])]
    ])

    # Nel topic "General" dei forum non va passato message_thread_id
    kwargs = thread_kwargs(topic_id)

    async def pubblica(testo: str, parse_mode: str | None) -> None:
        formato = {"parse_mode": parse_mode} if parse_mode else {}
        if dettagli["image_url"]:
            await bot.send_photo(
                chat_id=chat_id,
                photo=dettagli["image_url"],
                caption=testo,
                reply_markup=keyboard,
                **formato,
                **kwargs,
            )
        else:
            await bot.send_message(
                chat_id=chat_id,
                text=testo,
                reply_markup=keyboard,
                **formato,
                **kwargs,
            )

    try:
        await pubblica(format_news_caption(titolo_it, testo_it), ParseMode.HTML)
    except BadRequest as e:
        # Senza questo fallback la news non veniva pubblicata e, non entrando
        # tra quelle viste, veniva ritentata a ogni ciclo di polling.
        if "parse" not in str(e).lower():
            logger.exception(f"Errore invio news: {explain(e)}")
            raise
        logger.warning(
            f"Formattazione non valida per la news {articolo.get('url')} ({e}) "
            f"— invio senza formattazione."
        )
        await pubblica(format_news_caption(titolo_it, testo_it, plain=True), None)
    except Exception:
        logger.exception("Errore invio news")
        raise


async def send_weather_message(bot: Bot, chat_id: str, topic_id: int | None, testo: str) -> bool:
    """
    Pubblica il report meteo, con snapshot webcam se disponibile.
    Se l'invio della foto fallisce (webcam non valida, caption troppo lunga, ecc.)
    ripiega sul solo testo: il meteo deve arrivare comunque nel gruppo.

    Returns:
        True se il messaggio è stato pubblicato.
    """
    kwargs = thread_kwargs(topic_id)
    snapshot = await fetch_webcam_snapshot()

    # Telegram accetta al massimo 1024 caratteri di caption: oltre quel limite
    # send_photo fallisce sempre e il meteo arriverebbe solo come testo.
    if snapshot and len(testo) > MAX_CAPTION_LENGTH:
        logger.info(
            f"Report meteo di {len(testo)} caratteri: supera il limite di "
            f"{MAX_CAPTION_LENGTH} per le caption, pubblico senza snapshot webcam."
        )
        snapshot = None

    if snapshot:
        try:
            await bot.send_photo(
                chat_id=chat_id,
                photo=io.BytesIO(snapshot),
                caption=testo,
                parse_mode="HTML",
                **kwargs,
            )
            logger.info(f"Meteo pubblicato con snapshot webcam — topic: {kwargs.get('message_thread_id', 'General')}")
            return True
        except TelegramError as e:
            logger.warning(f"Invio meteo con foto fallito ({explain(e)}) — riprovo come solo testo.")

    try:
        await bot.send_message(
            chat_id=chat_id,
            text=testo,
            parse_mode="HTML",
            disable_web_page_preview=True,
            **kwargs,
        )
        logger.info(f"Meteo pubblicato — topic: {kwargs.get('message_thread_id', 'General')}")
        return True
    except TelegramError as e:
        logger.error(f"Impossibile pubblicare il meteo nel gruppo {chat_id}: {explain(e)}")
        return False


# Report automatico — solo vicino al festival
async def send_weather(app: Application, chat_id: str, topic_id: int = None) -> bool:
    # Anche la formattazione sta nel try: un valore mancante nella risposta
    # dell'API faceva risalire l'eccezione fino allo scheduler, che saltava il
    # report senza che nei log comparisse nulla di riferibile al meteo.
    try:
        data = await fetch_weather_festival()
        if data is None:
            logger.info("Report meteo non previsto oggi (fuori dalla finestra del festival).")
            return False
        testo = format_weather_festival(data)
    except Exception as e:
        logger.exception(f"Impossibile preparare il report meteo: {e}")
        return False

    return await send_weather_message(app.bot, chat_id, topic_id, testo)


# ---------------------------------------------------------------------------
# Disponibilità dei prodotti in vendita sul sito ufficiale
# ---------------------------------------------------------------------------

XCHANGE_PAGE = "https://brutalassault.cz/en/xchange"
ACCOMMODATION_PAGE = "https://brutalassault.cz/en/accommodation"


@dataclass(frozen=True)
class AvailabilityLabels:
    """
    Come chiamare le cose nei messaggi sulla disponibilità.

    Biglietti e alloggi hanno lo stesso ciclo di monitoraggio e gli stessi
    messaggi: cambiano solo le parole. Tenerle in un bundle evita una seconda
    copia dei formatter, che divergerebbe alla prima correzione fatta su una
    sola delle due — è già successo con is_sold_out().
    """
    emoji: str            # accanto al nome del prodotto
    item: str             # "Biglietto": nome di ripiego e "Nuovo <item> in vendita"
    plural: str           # "Biglietti": titolo dell'alert di soglia
    section: str          # "biglietti": "Monitoraggio <section> attivo"
    link_text: str        # testo del link alla scheda
    status_title: str     # titolo della risposta al comando
    none_on_sale: str     # frase per "niente in vendita al momento"
    sold_out_reason: str  # perché non si compra più, se il prodotto è ancora in pagina
    sold_out_tail: str    # dove mandare il gruppo dopo un sold out


TICKET_LABELS = AvailabilityLabels(
    emoji="🎟️",
    item="Biglietto",
    plural="Biglietti",
    section="biglietti",
    link_text="Vai al biglietto",
    status_title="Biglietti in vendita sul sito ufficiale",
    none_on_sale="non risulta in vendita nessun biglietto",
    sold_out_reason="I biglietti sono esauriti sul sito ufficiale.",
    sold_out_tail=(
        f"🎟️ Resta il <a href=\"{XCHANGE_PAGE}\">Ticket Exchange</a>: "
        "gli annunci di rivendita vengono pubblicati qui in automatico."
    ),
)

ACCOMMODATION_LABELS = AvailabilityLabels(
    emoji="🏨",
    item="Alloggio",
    plural="Posti",
    section="alloggi",
    link_text="Vai all'alloggio",
    status_title="Hotel e campeggi sul sito ufficiale",
    none_on_sale="non risulta in vendita nessun alloggio",
    sold_out_reason="I posti sono esauriti sul sito ufficiale.",
    # Per gli alloggi non esiste un mercato di rivendita come il Ticket
    # Exchange: l'unica cosa vera da dire è che il gruppo verrà avvisato se il
    # sito rimette in vendita dei posti.
    sold_out_tail=(
        f"🏨 Se sulla <a href=\"{ACCOMMODATION_PAGE}\">pagina alloggi</a> tornano "
        "posti disponibili, lo scrivo qui."
    ),
)


def format_percent(percent: float | None) -> str:
    """
    Percentuale in formato italiano ('34,9%'), 'n/d' se non disponibile.

    Arrotondata per difetto, non al più vicino: con l'arrotondamento normale il
    34,957% del sito diventava "35,0%" mentre lo scaglione era già sceso a 30, e
    l'alert "sotto il 35%" finiva accanto a un "Disponibili: 35,0%". Per difetto
    il numero mostrato non promette mai più biglietti di quanti ce ne siano.
    """
    if percent is None:
        return "n/d"
    return f"{math.floor(percent * 10) / 10:.1f}".replace(".", ",") + "%"


def _product_block(
    name: str | None,
    url: str | None,
    percent: float | None,
    sold_out: bool = False,
    labels: AvailabilityLabels = TICKET_LABELS,
) -> str:
    """
    Nome, disponibilità e link di un prodotto.

    Di un prodotto esaurito si dice il sold out e non "Disponibili: 0,0%", e si
    omette il link all'acquisto: manda su una pagina dove non si compra nulla.

    I nomi arrivano dal sito e contengono parentesi quadre ("[e-ticket]") che in
    Markdown verrebbero interpretate come link: questi messaggi usano HTML, con
    il nome sempre passato per html.escape.
    """
    righe = [f"{labels.emoji} <b>{html.escape(name or labels.item)}</b>"]
    if sold_out:
        righe.append("🔴 <b>SOLD OUT</b>")
        return "\n".join(righe)
    righe.append(f"📊 Disponibili: <b>{format_percent(percent)}</b>")
    if url:
        righe.append(
            f"👉 <a href=\"{html.escape(url, quote=True)}\">{labels.link_text}</a>"
        )
    return "\n".join(righe)


def format_availability_intro(
    products: list[dict],
    labels: AvailabilityLabels = TICKET_LABELS,
) -> str:
    """Riepilogo pubblicato la prima volta che il monitoraggio parte."""
    if not products:
        return (
            f"🔎 <b>Monitoraggio {labels.section} attivo</b>\n\n"
            f"Al momento sul sito ufficiale {labels.none_on_sale} "
            "per la nuova edizione: vi avviso appena compare.\n\n"
            f"🔔 Poi vi aggiorno a ogni scaglione del {ALERT_STEP}% di disponibilità, "
            "fino al sold out."
        )

    blocchi = [
        _product_block(p.get("name"), p.get("url"), p.get("percent"), is_sold_out(p), labels)
        for p in products
    ]
    return (
        f"🔎 <b>Monitoraggio {labels.section} attivo</b>\n\n"
        "Disponibilità attuale sul sito ufficiale:\n\n"
        + "\n\n".join(blocchi)
        + f"\n\n🔔 Vi avviso a ogni scaglione del {ALERT_STEP}% "
        "(es. 30%, 25%, 20%...) fino al sold out."
    )


def format_availability_status(
    products: list[dict],
    labels: AvailabilityLabels = TICKET_LABELS,
) -> str:
    """
    Risposta ai comandi /availability e /accommodation: la disponibilità del momento.

    Diverso da format_availability_intro(), che annuncia l'avvio del
    monitoraggio e ha senso una volta sola.
    """
    if not products:
        return (
            f"{labels.emoji} <b>{labels.status_title}</b>\n\n"
            f"Al momento {labels.none_on_sale} per la prossima edizione."
        )

    blocchi = []
    for p in products:
        percent = p.get("percent")
        sold_out = is_sold_out(p)

        blocco = _product_block(p.get("name"), p.get("url"), percent, sold_out, labels)
        if not sold_out and percent is not None:
            # Il prossimo alert scatta uscendo dallo scaglione attuale; sotto il
            # 5% non resta nessuna soglia intermedia, solo l'esaurimento.
            soglia = level_of(percent)
            blocco += f"\n🔔 Prossimo avviso: {f'sotto il {soglia}%' if soglia else 'il sold out'}"
        blocchi.append(blocco)

    return f"{labels.emoji} <b>{labels.status_title}</b>\n\n" + "\n\n".join(blocchi)


def format_availability_new(
    product: dict,
    labels: AvailabilityLabels = TICKET_LABELS,
) -> str:
    """Un prodotto dell'edizione monitorata è appena comparso in vendita."""
    return (
        f"🆕 <b>Nuovo {labels.item.lower()} in vendita sul sito ufficiale!</b>\n\n"
        + _product_block(
            product.get("name"), product.get("url"), product.get("percent"), False, labels
        )
        + "\n\n🏰 <i>Brutal Assault — Josefov</i>"
    )


def format_availability_alert(
    product: dict,
    soglia: int,
    previous_percent: float | None,
    crossed: list[int],
    labels: AvailabilityLabels = TICKET_LABELS,
) -> str:
    """
    Alert per una soglia di disponibilità appena superata verso il basso.

    Args:
        soglia: la soglia più bassa effettivamente superata (l'ultima di `crossed`).
    """
    righe = [f"⚠️ <b>{labels.plural} sotto il {soglia}%!</b>", ""]
    righe.append(
        _product_block(
            product.get("name"), product.get("url"), product.get("percent"), False, labels
        )
    )

    if previous_percent is not None:
        righe.append(f"📉 All'ultimo controllo erano al {format_percent(previous_percent)}")

    # Un crollo tra due controlli può bruciare più scaglioni insieme: l'alert
    # resta uno solo, ma le soglie saltate vengono comunque dette.
    if len(crossed) > 1:
        soglie = ", ".join(f"{s}%" for s in crossed)
        righe.append(f"⏬ Soglie superate in un colpo solo: {soglie}")

    righe.append("")
    righe.append("🏰 <i>Brutal Assault — Josefov</i>")
    return "\n".join(righe)


def format_availability_sold_out(
    record: dict,
    still_listed: bool,
    labels: AvailabilityLabels = TICKET_LABELS,
) -> str:
    """
    Messaggio di sold out.

    Args:
        still_listed: il prodotto è ancora sulla pagina (disponibilità a 0),
            contro il caso in cui sia proprio sparito dallo shop.
    """
    name = html.escape(record.get("name") or labels.item)
    motivo = (
        labels.sold_out_reason
        if still_listed
        else f"{labels.item} non più in vendita sul sito ufficiale "
        f"(ultima disponibilità rilevata: {format_percent(record.get('percent'))})."
    )
    return (
        f"🔴 <b>SOLD OUT — {name}</b>\n\n"
        f"{motivo}\n\n"
        f"{labels.sold_out_tail}\n\n"
        "🏰 <i>Brutal Assault — Josefov</i>"
    )


def split_message(testo: str, limit: int = MAX_MESSAGE_LENGTH) -> list[str]:
    """
    Spezza in più messaggi un testo troppo lungo per Telegram.

    Serve al riepilogo degli alloggi: la pagina ne elenca quasi trenta e il
    riepilogo supera i 4096 caratteri. Telegram non tronca, rifiuta — e un
    riepilogo iniziale rifiutato non salva lo stato, quindi il bot lo
    ritenterebbe a ogni ciclo senza mai riuscirci e senza mai avvisare nessuno.

    Il taglio cade solo sui confini tra paragrafi, che in questi messaggi
    separano un prodotto dall'altro: tagliare a lunghezza fissa spezzerebbe un
    tag HTML a metà e Telegram rifiuterebbe il pezzo.
    """
    if len(testo) <= limit:
        return [testo]

    separatore = "\n\n"
    parti: list[str] = []
    corrente = ""

    for paragrafo in testo.split(separatore):
        candidato = corrente + separatore + paragrafo if corrente else paragrafo
        if len(candidato) <= limit:
            corrente = candidato
            continue

        if corrente:
            parti.append(corrente)
            corrente = ""

        if len(paragrafo) <= limit:
            corrente = paragrafo
            continue

        # Un singolo paragrafo oltre il limite non capita con i blocchi di
        # prodotto (poche righe ciascuno), ma se capitasse va spezzato riga per
        # riga: meglio una formattazione imprecisa che perdere il messaggio.
        logger.warning(
            f"Paragrafo di {len(paragrafo)} caratteri oltre il limite di {limit}: "
            f"spezzato per righe."
        )
        for riga in paragrafo.split("\n"):
            if corrente and len(corrente) + 1 + len(riga) > limit:
                parti.append(corrente)
                corrente = ""
            corrente = corrente + "\n" + riga if corrente else riga[:limit]

    if corrente:
        parti.append(corrente)
    return parti


async def send_availability_message(
    bot: Bot,
    chat_id: str,
    topic_id: int | None,
    testo: str,
) -> bool:
    """
    Pubblica un messaggio sulla disponibilità nel topic dei biglietti.

    Un testo oltre il limite di Telegram viene pubblicato come più messaggi
    consecutivi, spezzato tra un prodotto e l'altro.

    Returns:
        True se il messaggio è stato pubblicato per intero: solo in quel caso il
        chiamante deve registrare la soglia come già notificata.
    """
    try:
        for parte in split_message(testo):
            await bot.send_message(
                chat_id=chat_id,
                text=parte,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
                **thread_kwargs(topic_id),
            )
        return True
    except TelegramError as e:
        logger.error(f"Impossibile pubblicare l'alert disponibilità nel gruppo {chat_id}: {e}")
        return False
