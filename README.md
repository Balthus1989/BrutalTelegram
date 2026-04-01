# 🤘 Brutal Assault Italia Bot

Bot Telegram che monitora il [Ticket Exchange ufficiale](https://brutalassault.cz/en/xchange)
e notifica il gruppo ogni volta che appare un nuovo annuncio di vendita.

## Setup

### 1. Installa le dipendenze con uv

```bash
uv add python-telegram-bot httpx beautifulsoup4 apscheduler python-dotenv
```

### 2. Crea il file .env

```bash
cp .env.example .env
```

Modifica `.env` con i tuoi valori:

```
TELEGRAM_TOKEN=il_token_del_bot
TELEGRAM_CHAT_ID=id_del_gruppo
```

**Come ottenere il token:**
- Vai su Telegram, cerca `@BotFather`
- Scrivi `/newbot` e segui le istruzioni
- Copia il token che ti fornisce

**Come ottenere il Chat ID del gruppo:**
- Aggiungi `@userinfobot` al tuo gruppo Telegram
- Scrivi `/start` nel gruppo
- Il bot ti risponde con l'ID (sarà un numero negativo tipo `-1001234567890`)
- Rimuovi `@userinfobot` dal gruppo dopo aver preso l'ID

> ⚠️ Il bot deve essere **admin** del gruppo per poter inviare messaggi.

### 3. Avvia il bot

```bash
uv run main.py
```

## Comandi disponibili

| Comando | Descrizione |
|---------|-------------|
| `/start` | Mostra il messaggio di benvenuto |
| `/status` | Stato del bot e numero di annunci tracciati |
| `/listings` | Mostra gli annunci attualmente disponibili |

## Come funziona

1. All'avvio esegue subito un primo controllo
2. Ogni **5 minuti** controlla la pagina del Ticket Exchange
3. Se trova annunci con ID non ancora visti, invia una notifica nel gruppo
4. Salva gli ID visti in `seen_tickets.json` per non mandare duplicati

## Struttura progetto

```
brutal-telegram/
├── main.py          # Entry point, scheduler, comandi bot
├── scraper.py       # Fetch e parsing della pagina xchange
├── notifier.py      # Formattazione e invio messaggi Telegram
├── state.py         # Persistenza degli ID già notificati
├── config.py        # Caricamento variabili d'ambiente
├── .env             # Configurazione (non committare su git!)
└── seen_tickets.json  # Generato automaticamente
```

## Note

- Il bot deve essere aggiunto al gruppo come **amministratore**
- `seen_tickets.json` viene creato automaticamente al primo avvio
- Aggiungere `.env` e `seen_tickets.json` al `.gitignore`