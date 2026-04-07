"""
Brutal Assault Italia Bot
Monitora il Ticket Exchange ufficiale e notifica il gruppo Telegram
per ogni nuovo annuncio di vendita.
"""

import asyncio
import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telegram.ext import Application, CommandHandler

from config import load_config
from ticket_scraper import fetch_listings
from notifier import notify_new_listings, delete_sold_messages, send_news
from ticket_state import load_state, save_state, load_seen_ids

from news_scraper import fetch_news
from news_state import load_seen, save_seen

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# Intervallo di polling in minuti
POLL_INTERVAL_MINUTES = 5


async def check_exchange(app: Application, chat_id: str, topic_id: int = None) -> None:
    """Controlla il Ticket Exchange, notifica nuovi annunci ed elimina i venduti."""
    logger.info("Controllo Ticket Exchange...")

    listings = await fetch_listings()
    if listings is None:
        logger.warning("Impossibile recuperare i listing. Riprovo al prossimo ciclo.")
        return

    state = load_state()  # { listing_id -> message_id }
    current_ids = {listing["id"] for listing in listings}
    known_ids = set(state.keys())

    # Annunci nuovi: presenti ora ma non ancora tracciati
    new_listings = [l for l in listings if l["id"] not in known_ids]

    # Annunci venduti: erano tracciati ma non compaiono più
    sold_ids = known_ids - current_ids
    sold_listings = {lid: state[lid] for lid in sold_ids if state[lid] is not None}

    # Notifica nuovi annunci
    if new_listings:
        logger.info(f"Trovati {len(new_listings)} nuovi annunci.")
        sent = await notify_new_listings(app.bot, chat_id, new_listings, topic_id)
        for listing_id, message_id in sent.items():
            state[listing_id] = message_id
    else:
        logger.info("Nessun nuovo annuncio.")

    # Elimina messaggi dei biglietti venduti
    if sold_listings:
        logger.info(f"{len(sold_listings)} biglietti venduti — elimino i messaggi.")
        await delete_sold_messages(app.bot, chat_id, sold_listings)
        for listing_id in sold_ids:
            state.pop(listing_id, None)
    else:
        logger.info("Nessun biglietto venduto.")

    save_state(state)


async def cmd_start(update, context) -> None:
    await update.message.reply_text(
        "🤘 *Brutal Assault Italia Bot* attivo!\n\n"
        "Monitoro il Ticket Exchange ufficiale e ti avviso appena esce un nuovo annuncio.\n\n"
        "Comandi disponibili:\n"
        "/start - Mostra questo messaggio\n"
        "/status - Stato del bot\n"
        "/listings - Mostra gli annunci attuali",
        parse_mode="Markdown",
    )


async def cmd_status(update, context) -> None:
    seen_ids = load_seen_ids()
    await update.message.reply_text(
        f"✅ Bot attivo e funzionante.\n"
        f"🎟️ Annunci tracciati: {len(seen_ids)}\n"
        f"🔄 Controllo ogni {POLL_INTERVAL_MINUTES} minuti.",
    )


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
        lines.append(
            f"• {listing['product']} — *€ {listing['price']}*\n"
            f"  [Vedi dettaglio]({listing['url']})"
        )

    await update.message.reply_text(
        "\n".join(lines),
        parse_mode="Markdown",
        disable_web_page_preview=True,
    )


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
    for art in nuovi:
        try:
            await send_news(app.bot, chat_id, news_topic_id, art)
            seen.add(art["id"])
        except Exception as e:
            logger.exception(f"Errore invio news {art.get('id')}: {e}")

    save_seen(seen)


async def main() -> None:
    config = load_config()

    app = Application.builder().token(config["token"]).build()

    # Registra i comandi
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("listings", cmd_listings))

    # Scheduler per il polling
    scheduler = AsyncIOScheduler()

    scheduler.add_job(
        check_exchange,
        trigger="interval",
        minutes=POLL_INTERVAL_MINUTES,
        args=[app, config["chat_id"], config["topic_id"]],
        id="exchange_poll",
        replace_existing=True,
    )

    scheduler.add_job(
        check_news,
        trigger="interval",
        minutes=POLL_INTERVAL_MINUTES,
        args=[app, config["chat_id"], config["news_topic_id"]],
        id="news_check",
        replace_existing=True,
    )

    logger.info("Avvio bot Brutal Assault Italia...")
    scheduler.start()

    # Esegui subito un primo controllo all'avvio
    async with app:
        await check_exchange(app, config["chat_id"], config["topic_id"])
        await check_news(app, config["chat_id"], config["news_topic_id"])
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