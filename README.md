# Brutal Assault Italia Bot

Bot Telegram che monitora il [Ticket Exchange ufficiale di Brutal Assault](https://brutalassault.cz/en/xchange) e notifica automaticamente un gruppo Telegram ogni volta che appare un nuovo annuncio di vendita biglietti.

Quando un biglietto viene venduto, il messaggio corrispondente viene eliminato automaticamente dal gruppo.

## Funzionalita

- Polling automatico ogni 5 minuti sulla pagina del Ticket Exchange
- Notifica immediata nel gruppo Telegram per ogni nuovo annuncio
- Eliminazione automatica dei messaggi per biglietti venduti
- Persistenza dello stato per evitare notifiche duplicate
- Supporto per Telegram Forum (topic mode)

## Comandi

| Comando | Descrizione |
|---------|-------------|
| `/start` | Messaggio di benvenuto |
| `/status` | Stato del bot e annunci tracciati |
| `/listings` | Annunci attualmente disponibili |

## Requisiti

- Python 3.10+
- [uv](https://github.com/astral-sh/uv) (package manager)
- Un bot Telegram (creato tramite @BotFather) con permessi di amministratore nel gruppo

## Installazione

```bash
git clone <repo-url>
cd BrutalTelegram
uv add python-telegram-bot httpx beautifulsoup4 apscheduler python-dotenv
```

## Configurazione

Crea un file `.env` nella root del progetto:

```
TELEGRAM_TOKEN=il_token_del_bot
TELEGRAM_CHAT_ID=id_del_gruppo
TELEGRAM_TOPIC_ID=id_del_topic
```

## Avvio

```bash
uv run main.py
```

## Struttura

```
BrutalTelegram/
├── main.py        # Entry point, scheduler, comandi bot
├── scraper.py     # Fetch e parsing della pagina xchange
├── notifier.py    # Formattazione e invio messaggi Telegram
├── state.py       # Persistenza stato (seen_tickets.json)
├── config.py      # Caricamento configurazione da .env
└── .env           # Configurazione (non tracciato da git)
```

## Licenza

Uso privato.
