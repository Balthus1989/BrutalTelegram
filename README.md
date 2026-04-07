# Brutal Assault Italia Bot

Bot Telegram che monitora il [Ticket Exchange ufficiale di Brutal Assault](https://brutalassault.cz/en/xchange) e la [pagina news ufficiale](https://brutalassault.cz/en/c/news), notificando automaticamente un gruppo Telegram ogni volta che appare un nuovo annuncio di vendita biglietti o una nuova notizia.

Quando un biglietto viene venduto, il messaggio corrispondente viene eliminato automaticamente dal gruppo. Le news vengono pubblicate con titolo e testo tradotti automaticamente in italiano, immagine di copertina e bottone al link originale.

## Funzionalita

- Polling automatico ogni 5 minuti sulla pagina del Ticket Exchange
- Polling automatico ogni 5 minuti sulla pagina delle news, con traduzione in italiano
- Notifica immediata nel gruppo Telegram per ogni nuovo annuncio e ogni nuova news
- Eliminazione automatica dei messaggi per biglietti venduti
- Persistenza dello stato per evitare notifiche duplicate (biglietti e news)
- Supporto per Telegram Forum (topic mode), con topic separati per ticket e news

## Comandi

| Comando | Descrizione |
|---------|-------------|
| `/start` | Messaggio di benvenuto |
| `/status` | Stato del bot e annunci tracciati |
| `/listings` | Annunci attualmente disponibili |
| `/news` | Ultime notizie di Brutal Assault |

## Requisiti

- Python 3.10+
- [uv](https://github.com/astral-sh/uv) (package manager)
- Un bot Telegram (creato tramite @BotFather) con permessi di amministratore nel gruppo

## Installazione

```bash
git clone <repo-url>
cd BrutalTelegram
uv sync
```

In alternativa, con `pip`:

```bash
pip install -r requirements.txt
```

## Configurazione

Crea un file `.env` nella root del progetto:

```
TELEGRAM_TOKEN=il_token_del_bot
TELEGRAM_CHAT_ID=id_del_gruppo
TELEGRAM_TOPIC_ID=id_del_topic_ticket
TELEGRAM_NEWS_TOPIC_ID=id_del_topic_news
```

> **Nota sui topic id nei forum Telegram:** il topic "General" non ha un thread id valido. Se vuoi pubblicare nel General, lascia `TELEGRAM_NEWS_TOPIC_ID` vuoto o impostalo a `1` — il bot ometterà automaticamente il `message_thread_id`. Per topic reali, apri un messaggio del topic → "Copia link" → il numero dopo `/c/<chat_id>/` è il thread id da usare.

## Avvio

```bash
uv run main.py
```

## Struttura

```
BrutalTelegram/
├── main.py            # Entry point, scheduler, comandi bot
├── ticket_scraper.py  # Fetch e parsing della pagina xchange
├── news_scraper.py    # Fetch e parsing delle news + articoli
├── translator.py      # Traduzione testi in italiano
├── notifier.py        # Formattazione e invio messaggi Telegram (ticket + news)
├── ticket_state.py    # Persistenza stato ticket (seen_tickets.json)
├── news_state.py      # Persistenza stato news (seen_news.json)
├── config.py          # Caricamento configurazione da .env
└── .env               # Configurazione (non tracciato da git)
```

## Licenza

Uso privato.
