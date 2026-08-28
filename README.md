# Brutal Assault Italia Bot

Bot Telegram che monitora il [Ticket Exchange ufficiale di Brutal Assault](https://brutalassault.cz/en/xchange) e la [pagina news ufficiale](https://brutalassault.cz/en/c/news), notificando automaticamente un gruppo Telegram ogni volta che appare un nuovo annuncio di vendita biglietti o una nuova notizia.

Quando un biglietto viene venduto, il messaggio corrispondente viene eliminato automaticamente dal gruppo. Le news vengono pubblicate con titolo e testo tradotti automaticamente in italiano, immagine di copertina e bottone al link originale.

Monitora anche la percentuale di biglietti ancora in vendita per la prossima edizione sul sito ufficiale, avvisando il thread dei biglietti a ogni scaglione del 5% superato verso il basso, fino al sold out.

Include inoltre un servizio meteo che fornisce previsioni per Jaroměř (sede del festival) tramite comando e report automatici giornalieri nei giorni che precedono l'evento, con snapshot dalla webcam locale.

## Funzionalita

- Polling automatico ogni 5 minuti sulla pagina del Ticket Exchange
- Polling automatico ogni 5 minuti sulla pagina delle news, con traduzione in italiano
- Notifica immediata nel gruppo Telegram per ogni nuovo annuncio e ogni nuova news
- Eliminazione automatica dei messaggi per biglietti venduti, con fallback: se Telegram
  non consente l'eliminazione (messaggio più vecchio di 48 ore o bot senza permessi di
  amministratore) il messaggio viene riscritto come "VENDUTO"
- Un annuncio è considerato venduto solo dopo 2 cicli consecutivi di assenza, e un
  errore di lettura della pagina non provoca mai eliminazioni di massa
- Persistenza dello stato su volume Fly.io (`/data`) con scrittura atomica, per evitare
  notifiche duplicate e messaggi "orfani" dopo un riavvio
- Supporto per Telegram Forum (topic mode), con topic separati per ticket, news e meteo
- Previsioni meteo 7 giorni per Jaroměř tramite API Open-Meteo (gratuita, senza API key)
- Report meteo automatico una volta al giorno dalle 08:00 (fuso Europe/Prague) nei 15 giorni
  prima del festival e durante lo stesso, con previsioni filtrate sui soli giorni del
  festival; se il bot era spento o l'invio è fallito, il report viene recuperato più tardi
  nella stessa giornata
- Open-Meteo prevede al massimo 16 giorni: nei primi giorni della finestra le ultime
  giornate del festival non sono ancora disponibili e il report lo segnala, invece di
  restare muto o di pubblicare un post senza previsioni
- Valori mancanti nella risposta dell'API vengono mostrati come `N/D` e non fanno più
  fallire l'intero report
- Monitoraggio della disponibilita dei biglietti della prossima edizione: la barra
  "Available" della scheda prodotto viene letta ogni 15 minuti e il gruppo riceve un
  alert a ogni multiplo di 5% superato verso il basso (sotto il 35%, sotto il 30%, ...)
  e uno finale al sold out
- Al primo avvio viene pubblicato un riepilogo con la percentuale attuale, anche se non
  e un multiplo di 5: serve anche a verificare che il bot scriva nel topic giusto
- Comando `/availability` per interrogare la disponibilita in qualsiasi momento, con
  indicazione della prossima soglia che fara scattare un alert; le percentuali sono
  sempre arrotondate per difetto, cosi il numero mostrato non promette mai piu
  biglietti di quanti ne restino
- Se tra due controlli la disponibilita crolla di piu scaglioni, l'alert resta uno solo
  ma cita le soglie bruciate; una risalita (nuova tranche in vendita) non genera alert
  ma rialza la soglia, cosi le discese successive tornano a essere notificate
- Una pagina non parsabile o una barra illeggibile non vengono mai interpretate come
  sold out, e un biglietto sparito dallo shop viene dichiarato esaurito solo dopo
  2 cicli consecutivi di assenza
- Snapshot dalla webcam live di Josefov allegata ai messaggi meteo, con fallback a solo
  testo se l'immagine non è pubblicabile

## Comandi

| Comando | Descrizione |
|---------|-------------|
| `/start` | Messaggio di benvenuto |
| `/status` | Stato del bot e annunci tracciati |
| `/listings` | Annunci attualmente disponibili |
| `/availability` | Percentuale di biglietti ancora in vendita sul sito ufficiale |
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

Variabili opzionali:

| Variabile | Default | Descrizione |
|-----------|---------|-------------|
| `FESTIVAL_START` | `2026-08-05` | Primo giorno del festival (`YYYY-MM-DD`) |
| `FESTIVAL_END` | `2026-08-08` | Ultimo giorno del festival (`YYYY-MM-DD`) |
| `BOT_DATA_DIR` | `/data` | Directory dei file di stato (volume Fly.io) |
| `TICKET_PRODUCT_MATCH` | `2027` | Testo che il nome di un prodotto deve contenere per essere monitorato |

> **Nota sui topic id nei forum Telegram:** il topic "General" non ha un thread id valido. Se vuoi pubblicare nel General, lascia il topic id vuoto o impostalo a `1` — il bot omettera automaticamente il `message_thread_id`. Per topic reali, apri un messaggio del topic, clicca "Copia link" e il numero dopo `/c/<chat_id>/` e il thread id da usare.
>
> Se `TELEGRAM_WEATHER_TOPIC_ID` non e impostato, il report meteo viene pubblicato nello stesso topic delle news.

> **Permessi del bot:** per eliminare i messaggi dei biglietti venduti il bot deve essere amministratore del gruppo con il permesso "Delete messages". Senza quel permesso Telegram rifiuta l'eliminazione dei messaggi piu vecchi di 48 ore e il bot si limita a riscriverli come "VENDUTO".

> **Fine edizione (biglietti):** gli alert sulla disponibilita seguono `TICKET_PRODUCT_MATCH`, che filtra i prodotti dello shop per nome (default `2027`). Senza filtro finirebbero sotto osservazione anche i gift voucher, la cui percentuale e impostata a mano. Quando parte la vendita dell'edizione successiva, aggiorna quella variabile (o il default in `tickets/availability_scraper.py`): altrimenti il bot continua a seguire biglietti non piu in vendita e li dichiara sold out.

> **Fine edizione:** al termine del festival aggiorna `FESTIVAL_START` / `FESTIVAL_END` (o i valori di default in `weather_forecast/weather.py`), altrimenti il report meteo automatico resta silente. All'avvio il bot logga le date in uso e se la finestra del meteo e attiva oggi: e il primo posto dove guardare se il report non arriva.

## Avvio

```bash
uv run main.py
```

## Test

```bash
python test_bot.py
```

Test funzionali offline (bot Telegram e siti simulati) su: notifica dei nuovi annunci,
conferma della vendita, eliminazione dei messaggi con i vari fallback, resistenza agli
errori di scraping, pubblicazione del report meteo e alert sulla disponibilita dei
biglietti (riepilogo iniziale, soglie del 5%, crolli multi-soglia, sold out, invii falliti).

## Struttura

```
BrutalTelegram/
├── main.py                        # Entry point, scheduler, comandi bot
├── config.py                      # Caricamento configurazione da .env
├── storage.py                     # Directory dati persistente (volume Fly.io /data)
├── notifier.py                    # Formattazione e invio messaggi Telegram (ticket + news + meteo)
├── translator.py                  # Traduzione testi in italiano (Google Translate)
├── tickets/
│   ├── ticket_scraper.py          # Fetch e parsing della pagina xchange
│   ├── ticket_state.py            # Persistenza stato ticket (seen_tickets.json)
│   ├── availability_scraper.py    # Percentuale di biglietti ancora in vendita + soglie
│   └── availability_state.py      # Soglie gia notificate (ticket_availability.json)
├── news/
│   ├── news_scraper.py            # Fetch e parsing delle news + articoli
│   └── news_state.py              # Persistenza stato news (seen_news.json)
├── weather_forecast/
│   ├── weather.py                 # Previsioni meteo via Open-Meteo API
│   ├── weather_state.py           # Data dell'ultimo report meteo (weather_state.json)
│   └── webcam.py                  # Snapshot webcam live da Josefov
├── test_bot.py                    # Test funzionali offline
└── .env                           # Configurazione (non tracciato da git)
```

## Licenza

Uso privato.
