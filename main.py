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
from scraper import fetch_listings
from notifier import notify_new_listings
from state import load_seen_ids, save_seen_ids

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# Intervallo di polling in minuti
POLL_INTERVAL_MINUTES = 5


async def check_exchange(app: Application, chat_id: str) -> None:
    """Controlla il Ticket Exchange e notifica nuovi annunci."""
    logger.info("Controllo Ticket Exchange...")

    listings = await fetch_listings()
    if listings is None:
        logger.warning("Impossibile recuperare i listing. Riprovo al prossimo ciclo.")
        return

    seen_ids = load_seen_ids()
    new_listings = [l for l in listings if l["id"] not in seen_ids]

    if new_listings:
        logger.info(f"Trovati {len(new_listings)} nuovi annunci.")
        sent = await notify_new_listings(app.bot, chat_id, new_listings)
        if sent:
            # Aggiorna lo stato solo se le notifiche sono state inviate
            for listing in new_listings:
                seen_ids.add(listing["id"])
            save_seen_ids(seen_ids)
    else:
        logger.info("Nessun nuovo annuncio.")

    # Aggiorna comunque lo stato con tutti gli ID attuali
    # (rimuove automaticamente quelli non più in lista)
    all_current_ids = {l["id"] for l in listings}
    # Mantieni solo quelli ancora attivi + quelli nuovi già salvati
    seen_ids = seen_ids.intersection(all_current_ids) | {l["id"] for l in listings}
    save_seen_ids(seen_ids)


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
    for l in listings:
        lines.append(
            f"• {l['product']} — *€ {l['price']}*\n"
            f"  [Vedi dettaglio]({l['url']})"
        )

    await update.message.reply_text(
        "\n".join(lines),
        parse_mode="Markdown",
        disable_web_page_preview=True,
    )


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
        args=[app, config["chat_id"]],
        id="exchange_poll",
        replace_existing=True,
    )

    logger.info("Avvio bot Brutal Assault Italia...")
    scheduler.start()

    # Esegui subito un primo controllo all'avvio
    async with app:
        await check_exchange(app, config["chat_id"])
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