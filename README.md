# Brutal Assault Italia Bot

Bot Telegram che monitora il [Ticket Exchange ufficiale di Brutal Assault](https://brutalassault.cz/en/xchange) e la [pagina news ufficiale](https://brutalassault.cz/en/c/news), notificando automaticamente un gruppo Telegram ogni volta che appare un nuovo annuncio di vendita biglietti o una nuova notizia.

Quando un biglietto viene venduto, il messaggio corrispondente viene eliminato automaticamente dal gruppo. Le news vengono pubblicate con titolo e testo tradotti automaticamente in italiano, immagine di copertina e bottone al link originale.

Include inoltre un servizio meteo che fornisce previsioni per Jaroměř (sede del festival) tramite comando e report automatici giornalieri nei giorni che precedono l'evento, con snapshot dalla webcam locale.

## Funzionalita

- Polling automatico ogni 5 minuti sulla pagina del Ticket Exchange
- Polling automatico ogni 5 minuti sulla pagina delle news, con traduzione in italiano
- Notifica immediata nel gruppo Telegram per ogni nuovo annuncio e ogni nuova news
- Eliminazione automatica dei messaggi per biglietti venduti
- Persistenza dello stato per evitare notifiche duplicate (biglietti e news)
- Supporto per Telegram Forum (topic mode), con topic separati per ticket, news e meteo
- Previsioni meteo 7 giorni per Jaroměř tramite API Open-Meteo (gratuita, senza API key)
- Report meteo automatico giornaliero alle 08:00 (fuso Europe/Prague) nei 15 giorni prima del festival e durante lo stesso, con previsioni filtrate sui soli giorni del festival (5-8 agosto 2026)
- Snapshot dalla webcam live di Josefov allegata ai messaggi meteo

## Comandi

| Comando | Descrizione |
|---------|-------------|
| `/start` | Messaggio di benvenuto |
| `/status` | Stato del bot e annunci tracciati |
| `/listings` | Annunci attualmente disponibili |
| `/news` | Ultime notizie di Brutal Assault |
| `/weather` | Previsioni meteo 7 giorni per Jaroměř con countdown al festival |

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
TELEGRAM_WEATHER_TOPIC_ID=id_del_topic_meteo
```

> **Nota sui topic id nei forum Telegram:** il topic "General" non ha un thread id valido. Se vuoi pubblicare nel General, lascia il topic id vuoto o impostalo a `1` — il bot omettera automaticamente il `message_thread_id`. Per topic reali, apri un messaggio del topic, clicca "Copia link" e il numero dopo `/c/<chat_id>/` e il thread id da usare.

## Avvio

```bash
uv run main.py
```

## Struttura

```
BrutalTelegram/
├── main.py                        # Entry point, scheduler, comandi bot
├── config.py                      # Caricamento configurazione da .env
├── notifier.py                    # Formattazione e invio messaggi Telegram (ticket + news)
├── translator.py                  # Traduzione testi in italiano (Google Translate)
├── tickets/
│   ├── ticket_scraper.py          # Fetch e parsing della pagina xchange
│   └── ticket_state.py            # Persistenza stato ticket (seen_tickets.json)
├── news/
│   ├── news_scraper.py            # Fetch e parsing delle news + articoli
│   └── news_state.py              # Persistenza stato news (seen_news.json)
├── weather_forecast/
│   ├── weather.py                 # Previsioni meteo via Open-Meteo API
│   └── webcam.py                  # Snapshot webcam live da Josefov
└── .env                           # Configurazione (non tracciato da git)
```

## Licenza

Uso privato.
