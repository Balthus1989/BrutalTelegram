"""
Brutal Assault Italia Bot
Monitora il Ticket Exchange ufficiale e notifica il gruppo Telegram
per ogni nuovo annuncio di vendita.
"""

import asyncio
import html
import io

import logging
from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telegram import BotCommand
from telegram.error import TelegramError
from telegram.ext import Application, CommandHandler

from config import load_config
from version import __release_date__, __version__, release_notes
from tickets.ticket_scraper import fetch_listings, get_face_value
from tickets.ticket_state import STATE_FILE, load_state, save_state, make_record
from tickets.availability_scraper import (
    ALERT_STEP,
    PRODUCT_MATCH,
    fetch_ticket_availability,
    level_of,
    levels_crossed,
)
from tickets.availability_state import (
    load_availability_state,
    make_record as make_availability_record,
    save_availability_state,
)

from news.news_scraper import fetch_news
from news.news_state import load_failures, load_seen, save_failures, save_seen

from weather_forecast.weather import (
    FESTIVAL_END,
    FESTIVAL_START,
    FESTIVAL_TZ,
    REPORT_WINDOW_DAYS,
    fetch_weather_command,
    festival_window_open,
    format_weather_command,
)
from weather_forecast.weather_state import load_last_report_date, save_last_report_date
from weather_forecast.webcam import fetch_webcam_snapshot

from notifier import (
    format_availability_alert,
    format_availability_intro,
    format_availability_new,
    format_availability_status,
    format_availability_sold_out,
    format_percent,
    format_price_delta,
    notify_new_listings,
    resolve_sold_messages,
    send_availability_message,
    send_news,
    send_weather,
)

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# Intervallo di polling in minuti
POLL_INTERVAL_MINUTES = 5

# Cicli consecutivi in cui un annuncio deve risultare assente prima di considerarlo venduto
# (protegge da pagine caricate parzialmente o da risposte incomplete del sito).
MISSING_POLLS_BEFORE_SOLD = 2

# Dopo questi tentativi falliti si smette di ritentare la rimozione del messaggio
MAX_DELETE_ATTEMPTS = 10

# Frequenza del controllo sulla disponibilità dei biglietti in vendita.
# Più rado del Ticket Exchange: la percentuale si muove lentamente e ogni ciclo
# costa una richiesta all'elenco più una per ogni scheda prodotto.
AVAILABILITY_CHECK_MINUTES = 15

# Report meteo automatico: ora locale del festival e frequenza dei tentativi
WEATHER_REPORT_HOUR = 8
WEATHER_CHECK_MINUTES = 15


async def check_exchange(app: Application, chat_id: str, topic_id: int = None) -> None:
    """Controlla il Ticket Exchange, notifica nuovi annunci ed elimina i venduti."""
    logger.info("Controllo Ticket Exchange...")

    listings = await fetch_listings()
    if listings is None:
        logger.warning("Impossibile recuperare i listing. Riprovo al prossimo ciclo.")
        return

    state = load_state()  # { listing_id -> record }
    current_ids = {listing["id"] for listing in listings}
    known_ids = set(state.keys())

    # Annunci nuovi: presenti ora ma non ancora tracciati
    new_listings = [l for l in listings if l["id"] not in known_ids]

    # Notifica nuovi annunci
    if new_listings:
        logger.info(f"Trovati {len(new_listings)} nuovi annunci.")
        sent = await notify_new_listings(app.bot, chat_id, new_listings, topic_id)
        for listing in new_listings:
            message_id = sent.get(listing["id"])
            if message_id is None:
                # Invio fallito: non tracciare, così verrà ritentato al prossimo ciclo
                continue
            state[listing["id"]] = make_record(listing, message_id, chat_id)
    else:
        logger.info("Nessun nuovo annuncio.")

    # Annuncio ricomparso (o mai scomparso): azzera il contatore delle assenze
    for listing_id in current_ids & known_ids:
        if state[listing_id].get("missing_count"):
            state[listing_id]["missing_count"] = 0

    # Annunci assenti dalla pagina: candidati venduti dopo N cicli consecutivi
    sold_ids = set()
    for listing_id in known_ids - current_ids:
        record = state[listing_id]
        record["missing_count"] = record.get("missing_count", 0) + 1
        if record["missing_count"] >= MISSING_POLLS_BEFORE_SOLD:
            sold_ids.add(listing_id)
        else:
            logger.info(
                f"Annuncio {listing_id} non più visibile "
                f"({record['missing_count']}/{MISSING_POLLS_BEFORE_SOLD}): attendo conferma."
            )

    if sold_ids:
        logger.info(f"{len(sold_ids)} biglietti venduti — rimuovo i messaggi.")
        resolved, retry = await resolve_sold_messages(
            app.bot, {lid: state[lid] for lid in sold_ids}, chat_id
        )
        for listing_id in resolved:
            state.pop(listing_id, None)
        for listing_id in retry:
            record = state[listing_id]
            record["delete_attempts"] = record.get("delete_attempts", 0) + 1
            if record["delete_attempts"] >= MAX_DELETE_ATTEMPTS:
                logger.error(
                    f"Rinuncio a rimuovere il messaggio {record.get('message_id')} "
                    f"dell'annuncio {listing_id} dopo {record['delete_attempts']} tentativi."
                )
                state.pop(listing_id, None)
    else:
        logger.info("Nessun biglietto venduto.")

    if not save_state(state):
        logger.error(
            "Stato non salvato: al prossimo ciclo gli annunci potrebbero essere "
            "ri-notificati e i messaggi venduti non più eliminabili."
        )


async def check_ticket_availability(app: Application, chat_id: str, topic_id: int = None) -> None:
    """
    Controlla quanti biglietti restano in vendita sul sito ufficiale e avvisa il
    topic dei biglietti a ogni scaglione del 5% superato verso il basso, fino al
    sold out.

    Al primo avvio (nessuno stato salvato) pubblica un riepilogo con la
    percentuale attuale, anche se non è un multiplo di 5.
    """
    logger.info("Controllo disponibilità biglietti...")

    products = await fetch_ticket_availability()
    if products is None:
        logger.warning(
            "Impossibile leggere la disponibilità dei biglietti. Riprovo al prossimo ciclo."
        )
        return

    state = load_availability_state()
    known = state["products"]

    # Una barra illeggibile non è un esaurimento: quei prodotti vengono saltati
    # per questo ciclo, ma restano "visti" e non maturano un sold out.
    seen_ids = {p["id"] for p in products}
    readable = [p for p in products if p["percent"] is not None]
    for p in products:
        if p["percent"] is None:
            logger.warning(
                f"Disponibilità non leggibile per '{p['name']}': prodotto saltato in questo ciclo."
            )

    if not state["initialized"]:
        # Messaggio iniziale: serve anche a verificare che il bot scriva nel
        # topic giusto. Lo stato viene salvato solo se il messaggio è partito,
        # altrimenti al prossimo ciclo si ritenta.
        if not await send_availability_message(
            app.bot, chat_id, topic_id, format_availability_intro(readable)
        ):
            logger.warning("Riepilogo iniziale non pubblicato: ritento al prossimo ciclo.")
            return
        for p in readable:
            known[p["id"]] = make_availability_record(p, level_of(p["percent"]))
        state["initialized"] = True
        if not save_availability_state(state):
            logger.error("Stato disponibilità non salvato: il riepilogo verrà ripubblicato.")
        return

    for p in readable:
        percent = p["percent"]
        level = level_of(percent)
        record = known.get(p["id"])

        if record is None:
            # Biglietto comparso dopo l'avvio del monitoraggio (nuova tipologia
            # messa in vendita): lo si annuncia e si parte a tracciarlo da qui.
            if await send_availability_message(
                app.bot, chat_id, topic_id, format_availability_new(p)
            ):
                known[p["id"]] = make_availability_record(p, level)
            continue

        record["missing_count"] = 0
        record["name"] = p["name"] or record.get("name")
        record["url"] = p["url"] or record.get("url")
        previous_level = record.get("level", 100)
        previous_percent = record.get("percent")

        # Il sold out va verificato a parte: sotto il 5% la banda è già 0, quindi
        # l'esaurimento non produrrebbe nessun cambio di scaglione da notificare.
        sold_out = p["sold_out"] or percent <= 0

        if sold_out and not record.get("sold_out"):
            logger.info(f"'{record['name']}': SOLD OUT ({previous_percent}% → {percent}%).")
            testo = format_availability_sold_out({**record, "percent": percent}, still_listed=True)
            if await send_availability_message(app.bot, chat_id, topic_id, testo):
                record["level"] = level
                record["percent"] = percent
                record["sold_out"] = True
            else:
                logger.warning(f"Sold out di '{record['name']}' non inviato: ritento.")
            continue

        if level >= previous_level:
            # Disponibilità stabile o risalita (nuova tranche in vendita): niente
            # alert, ma lo scaglione va rialzato o le discese successive resterebbero mute.
            if level > previous_level:
                logger.info(
                    f"Disponibilità di '{record['name']}' risalita a {percent}% "
                    f"(scaglione {previous_level}% → {level}%)."
                )
                record["level"] = level
                record["sold_out"] = False
            record["percent"] = percent
            continue

        crossed = levels_crossed(previous_level, level)
        soglia = crossed[-1]  # la più bassa effettivamente superata
        logger.info(
            f"'{record['name']}': {previous_percent}% → {percent}% — "
            f"sotto il {soglia}% (soglie superate: {crossed})."
        )

        testo = format_availability_alert(p, soglia, previous_percent, crossed)
        if await send_availability_message(app.bot, chat_id, topic_id, testo):
            record["level"] = level
            record["percent"] = percent
        else:
            # Scaglione non registrato: l'alert viene ritentato al prossimo ciclo.
            logger.warning(f"Alert {soglia}% per '{record['name']}' non inviato: ritento.")

    # Biglietti spariti dalla pagina: come per il Ticket Exchange servono più
    # cicli consecutivi di assenza prima di dichiararli esauriti.
    for product_id in list(known.keys() - seen_ids):
        record = known[product_id]
        record["missing_count"] = record.get("missing_count", 0) + 1
        if record["missing_count"] < MISSING_POLLS_BEFORE_SOLD:
            logger.info(
                f"Biglietto '{record.get('name')}' non più in pagina "
                f"({record['missing_count']}/{MISSING_POLLS_BEFORE_SOLD}): attendo conferma."
            )
            continue

        if record.get("sold_out"):
            # Sold out già annunciato quando la barra era a 0: niente doppione.
            known.pop(product_id, None)
            continue

        if await send_availability_message(
            app.bot, chat_id, topic_id, format_availability_sold_out(record, still_listed=False)
        ):
            known.pop(product_id, None)

    if not save_availability_state(state):
        logger.error(
            "Stato disponibilità non salvato: al prossimo ciclo le soglie già "
            "annunciate potrebbero essere ripubblicate."
        )


# Elenco unico dei comandi: alimenta sia il menù "/" di Telegram (via
# set_my_commands all'avvio) sia il testo di /start. Il menù non si aggiorna da
# solo: senza set_my_commands resta quello configurato a mano in @BotFather e i
# comandi aggiunti al codice non compaiono mai nell'autocompletamento.
BOT_COMMANDS = [
    BotCommand("start", "Mostra il messaggio di benvenuto"),
    BotCommand("status", "Stato del bot e annunci tracciati"),
    BotCommand("listings", "Annunci attuali sul Ticket Exchange"),
    BotCommand("availability", "Biglietti ancora in vendita sul sito ufficiale"),
    BotCommand("news", "Ultime notizie di Brutal Assault"),
    BotCommand("weather", "Previsioni meteo per i giorni del festival"),
    BotCommand("version", "Versione del bot e novità dell'ultimo rilascio"),
]


async def cmd_start(update, context) -> None:
    elenco = "\n".join(f"/{c.command} - {c.description}" for c in BOT_COMMANDS)
    await update.message.reply_text(
        f"🤘 *Brutal Assault Italia Bot* v{__version__} attivo!\n\n"
        "Monitoro il Ticket Exchange ufficiale e ti avviso appena esce un nuovo annuncio.\n\n"
        f"Comandi disponibili:\n{elenco}",
        parse_mode="Markdown",
    )


async def cmd_version(update, context) -> None:
    """Versione in esecuzione e novità che ha portato."""
    righe = [
        f"🏷️ <b>Brutal Assault Italia Bot</b> v{__version__}",
        f"Rilasciata il {__release_date__}",
    ]

    # Le novità vengono dal CHANGELOG: se il file manca nell'immagine, o la
    # versione non è ancora annotata, il comando risponde comunque col numero.
    # Le voci sono testo scritto a mano: passano per html.escape perché un "<"
    # o una "&" di troppo farebbero fallire l'invio invece di pubblicare.
    novita = release_notes()
    if novita:
        righe.append("")
        righe.append("<b>Novità di questa versione:</b>")
        righe += [f"• {html.escape(voce.replace('`', ''))}" for voce in novita[:10]]

    await update.message.reply_text(
        "\n".join(righe), parse_mode="HTML", disable_web_page_preview=True
    )


async def cmd_status(update, context) -> None:
    state = load_state()
    pendenti = sum(1 for r in state.values() if r.get("delete_attempts"))
    righe = [
        "✅ Bot attivo e funzionante.",
        f"🏷️ Versione {__version__} (rilasciata il {__release_date__})",
        f"🎟️ Annunci tracciati: {len(state)}",
        f"🔄 Controllo ogni {POLL_INTERVAL_MINUTES} minuti.",
        f"💾 Stato: {STATE_FILE}",
    ]
    if pendenti:
        righe.append(f"⚠️ Messaggi venduti in attesa di rimozione: {pendenti}")

    for record in load_availability_state()["products"].values():
        stato = "SOLD OUT" if record.get("sold_out") else format_percent(record.get("percent"))
        righe.append(f"📊 {record.get('name')}: {stato}")

    await update.message.reply_text("\n".join(righe))


async def cmd_listings(update, context) -> None:
    """Mostra gli annunci attualmente disponibili."""
    await update.message.reply_text("🔍 Recupero annunci in corso...")
    listings = await fetch_listings()

    if listings is None:
        await update.message.reply_text("❌ Impossibile recuperare gli annunci. Riprova più tardi.")
        return

    if not listings:
        await update.message.reply_text("📭 Nessun annuncio disponibile al momento.")
        return

    lines = ["🎟️ *Annunci disponibili sul Ticket Exchange:*\n"]
    for listing in listings:
        delta_str = ""
        face_value = get_face_value(listing["product"])
        try:
            listing_price = float(listing["price"])
        except (ValueError, TypeError):
            listing_price = None
        if listing_price is not None and face_value is not None:
            delta_str = f"\n  📊 {format_price_delta(listing_price, face_value)}"
        lines.append(
            f"• {listing['product']} — *€ {listing['price']}*{delta_str}\n"
            f"  [Vedi dettaglio]({listing['url']})"
        )

    await update.message.reply_text(
        "\n".join(lines),
        parse_mode="Markdown",
        disable_web_page_preview=True,
    )


async def cmd_availability(update, context) -> None:
    """Disponibilità attuale dei biglietti sul sito ufficiale, letta al momento."""
    await update.message.reply_text("🔍 Controllo la disponibilità dei biglietti...")

    products = await fetch_ticket_availability()
    if products is None:
        await update.message.reply_text(
            "❌ Impossibile leggere la disponibilità dal sito ufficiale. Riprova più tardi."
        )
        return

    # Un prodotto esaurito può non avere più la barra: va mostrato lo stesso.
    leggibili = [p for p in products if p["percent"] is not None or p["sold_out"]]
    if products and not leggibili:
        await update.message.reply_text(
            "❌ Pagina raggiungibile ma barra di disponibilità illeggibile. Riprova più tardi."
        )
        return

    await update.message.reply_text(
        format_availability_status(leggibili),
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


async def cmd_news(update, context) -> None:
    """Mostra le ultime notizie di Brutal Assault."""
    await update.message.reply_text("📰 Recupero notizie in corso...")
    try:
        articoli = await fetch_news()
    except Exception as e:
        await update.message.reply_text(f"❌ Impossibile recuperare le notizie: {e}")
        return

    if not articoli:
        await update.message.reply_text("📭 Nessuna notizia disponibile al momento.")
        return

    lines = ["📰 *Ultime notizie Brutal Assault:*\n"]
    for art in articoli[:10]:
        lines.append(f"• [{art['titolo']}]({art['url']})")

    await update.message.reply_text(
        "\n".join(lines),
        parse_mode="Markdown",
        disable_web_page_preview=True,
    )


# Comando /meteo — sempre disponibile, 7 giorni generici
async def cmd_weather(update, context) -> None:
    data = await fetch_weather_command()
    testo = format_weather_command(data)
    snapshot = await fetch_webcam_snapshot()

    if snapshot:
        await update.message.reply_photo(
            photo=io.BytesIO(snapshot),
            caption=testo,
            parse_mode="HTML",
        )
    else:
        await update.message.reply_text(testo, parse_mode="HTML", disable_web_page_preview=True)




# Dopo questi tentativi consecutivi falliti la news viene segnata come vista:
# una news impubblicabile (immagine rotta, articolo irraggiungibile) verrebbe
# altrimenti ritentata a ogni ciclo di polling per sempre.
MAX_NEWS_FAILURES = 5


async def check_news(app: Application, chat_id: str, news_topic_id: int) -> None:
    """Controlla le news di Brutal Assault e notifica il gruppo Telegram."""
    logger.info("Controllo news Brutal Assault...")
    try:
        articoli = await fetch_news()
    except Exception as e:
        logger.warning(f"Impossibile recuperare le news: {e}")
        return

    seen = load_seen()
    nuovi = [a for a in reversed(articoli) if a["id"] not in seen]

    if not nuovi:
        logger.info("Nessuna nuova news.")
        return

    logger.info(f"Trovate {len(nuovi)} nuove news.")
    failures = load_failures()
    for art in nuovi:
        news_id = art["id"]
        try:
            await send_news(app.bot, chat_id, news_topic_id, art)
            seen.add(news_id)
            failures.pop(news_id, None)
        except Exception as e:
            logger.exception(f"Errore invio news {art.get('id')}: {e}")
            tentativi = failures.get(news_id, 0) + 1
            if tentativi >= MAX_NEWS_FAILURES:
                logger.error(
                    f"News {news_id} non pubblicata dopo {tentativi} tentativi: "
                    f"la segno come vista per non ritentarla a ogni ciclo."
                )
                seen.add(news_id)
                failures.pop(news_id, None)
            else:
                failures[news_id] = tentativi

    save_seen(seen)
    # Le news che nel frattempo sono sparite dal sito non vanno contate per sempre
    save_failures({k: v for k, v in failures.items() if k in {a["id"] for a in nuovi}})



async def run_job(name: str, func, *args) -> None:
    """
    Esegue un job dello scheduler isolandone gli errori: senza questo wrapper
    un'eccezione (es. topic inesistente) fa fallire il job in silenzio.
    """
    try:
        await func(*args)
    except Exception as e:
        logger.exception(f"Job '{name}' terminato con errore: {e}")


async def weather_tick(app: Application, chat_id: str, topic_id: int = None) -> None:
    """
    Pubblica il report meteo giornaliero, una sola volta al giorno.

    Viene eseguito periodicamente invece di una sola volta alle 08:00: se il bot
    era spento o l'invio è fallito a quell'ora (riavvio, deploy, errore di rete),
    il report viene recuperato al primo tentativo utile della giornata.
    """
    now = datetime.now(FESTIVAL_TZ)

    # Ogni uscita anticipata è tracciata: senza questi log un report mancante
    # era indistinguibile da un job che non era mai partito.
    if not festival_window_open():
        logger.debug("Report meteo: fuori dalla finestra del festival, niente da pubblicare.")
        return

    if now.hour < WEATHER_REPORT_HOUR:
        logger.info(
            f"Report meteo: a Praga sono le {now:%H:%M}, "
            f"attendo le {WEATHER_REPORT_HOUR:02d}:00."
        )
        return

    today = now.date().isoformat()
    if load_last_report_date() == today:
        logger.debug(f"Report meteo del {today} già pubblicato.")
        return

    logger.info(f"Pubblico il report meteo del {today}...")
    if await send_weather(app, chat_id, topic_id):
        save_last_report_date(today)
    else:
        logger.warning("Report meteo non pubblicato: ritento al prossimo ciclo.")


async def main() -> None:
    config = load_config()

    app = Application.builder().token(config["token"]).build()

    # Registra i comandi
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("listings", cmd_listings))
    app.add_handler(CommandHandler("availability", cmd_availability))
    app.add_handler(CommandHandler("news", cmd_news))
    app.add_handler(CommandHandler("weather", cmd_weather))
    app.add_handler(CommandHandler("version", cmd_version))

    # Scheduler per il polling.
    # misfire_grace_time: di default APScheduler scarta un job che parte con più
    # di 1 secondo di ritardo. Scraping e traduzioni tengono occupato il loop ben
    # oltre quella soglia, e i tick del meteo venivano saltati senza traccia.
    scheduler = AsyncIOScheduler(
        job_defaults={
            "coalesce": True,
            "max_instances": 1,
            "misfire_grace_time": 300,
        }
    )

    scheduler.add_job(
        run_job,
        trigger="interval",
        minutes=POLL_INTERVAL_MINUTES,
        args=["exchange_poll", check_exchange, app, config["chat_id"], config["topic_id"]],
        id="exchange_poll",
        replace_existing=True,
    )

    scheduler.add_job(
        run_job,
        trigger="interval",
        minutes=POLL_INTERVAL_MINUTES,
        args=["news_check", check_news, app, config["chat_id"], config["news_topic_id"]],
        id="news_check",
        replace_existing=True,
    )

    # Disponibilità dei biglietti sul sito ufficiale: stesso topic degli annunci
    # del Ticket Exchange.
    scheduler.add_job(
        run_job,
        trigger="interval",
        minutes=AVAILABILITY_CHECK_MINUTES,
        args=[
            "ticket_availability",
            check_ticket_availability,
            app,
            config["chat_id"],
            config["topic_id"],
        ],
        id="ticket_availability",
        replace_existing=True,
    )

    # Il report meteo è schedulato a intervalli e non come cron alle 08:00:
    # weather_tick pubblica una sola volta al giorno e recupera l'invio se
    # l'orario previsto è stato mancato (riavvio del bot, deploy, errore di rete).
    scheduler.add_job(
        run_job,
        trigger="interval",
        minutes=WEATHER_CHECK_MINUTES,
        args=["daily_weather", weather_tick, app, config["chat_id"], config["weather_topic_id"]],
        id="daily_weather",
        replace_existing=True,
    )

    # Una data del festival sbagliata (o non aggiornata a fine edizione) rende il
    # report meteo silente: meglio vederla nei log all'avvio.
    logger.info(
        f"Report meteo: festival {FESTIVAL_START} → {FESTIVAL_END}, "
        f"pubblicazione dalle {WEATHER_REPORT_HOUR:02d}:00 (Europe/Prague) nei "
        f"{REPORT_WINDOW_DAYS} giorni precedenti e durante — "
        f"finestra attiva oggi: {'sì' if festival_window_open() else 'no'}."
    )
    logger.info(
        f"Disponibilità biglietti: prodotti con '{PRODUCT_MATCH}' nel nome, "
        f"controllo ogni {AVAILABILITY_CHECK_MINUTES} minuti, alert a ogni "
        f"{ALERT_STEP}% nel topic ticket ({config['topic_id'] or 'General'})."
    )
    logger.info(
        f"Avvio bot Brutal Assault Italia v{__version__} "
        f"(rilascio del {__release_date__})..."
    )
    scheduler.start()

    # Esegui subito un primo controllo all'avvio
    async with app:
        # Il menù "/" della chat vive lato Telegram, non nel codice: va riscritto
        # a ogni avvio, altrimenti i comandi nuovi restano invisibili anche se
        # l'handler è attivo e il comando funziona digitandolo a mano.
        try:
            await app.bot.set_my_commands(BOT_COMMANDS)
            logger.info(
                "Menù comandi aggiornato: "
                + ", ".join(f"/{c.command}" for c in BOT_COMMANDS)
            )
        except TelegramError as e:
            logger.warning(f"Impossibile aggiornare il menù comandi: {e}")

        await run_job("exchange_poll", check_exchange, app, config["chat_id"], config["topic_id"])
        await run_job("news_check", check_news, app, config["chat_id"], config["news_topic_id"])
        await run_job(
            "ticket_availability",
            check_ticket_availability,
            app,
            config["chat_id"],
            config["topic_id"],
        )
        await run_job("daily_weather", weather_tick, app, config["chat_id"], config["weather_topic_id"])
        await app.start()
        await app.updater.start_polling()
        logger.info("Bot in ascolto. Premi Ctrl+C per fermare.")
        try:
            await asyncio.Event().wait()
        except (KeyboardInterrupt, SystemExit):
            logger.info("Arresto bot...")
        finally:
            scheduler.shutdown()
            await app.updater.stop()
            await app.stop()


if __name__ == "__main__":
    asyncio.run(main())