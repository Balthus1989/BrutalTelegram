# Changelog

Tutte le modifiche rilevanti di questo progetto sono annotate qui.

Il formato segue [Keep a Changelog](https://keepachangelog.com/it/1.1.0/) e la
numerazione il [versionamento semantico](https://semver.org/lang/it/):

- **MAJOR** — cambia il comportamento atteso dal gruppo (comandi rimossi o
  rinominati, notifiche che smettono di arrivare, formato dello stato non più
  leggibile dalla versione precedente)
- **MINOR** — nuove funzionalità compatibili (un comando in più, un nuovo tipo
  di alert)
- **PATCH** — correzioni che non cambiano cosa fa il bot, solo che lo faccia

Le voci nuove si scrivono sotto `[Non rilasciato]` man mano che si lavora:
`release.py` le sposta nella nuova versione al momento del rilascio.

## [Non rilasciato]

### Aggiunto

- Versionamento del bot: `version.py` come unica fonte di verità, comando
  `/version` con numero, data di rilascio e novità della versione in esecuzione,
  numero di versione anche in `/start`, `/status` e nei log all'avvio
- `release.py`: avanza la versione (`major`/`minor`/`patch`), sposta le voci di
  `[Non rilasciato]` nella nuova sezione del CHANGELOG, crea commit e tag `vX.Y.Z`

## [1.0.0] - 2026-08-28

Prima versione numerata: fotografa il bot già in esercizio su Fly.io.

### Aggiunto

- Monitoraggio del Ticket Exchange ogni 5 minuti, con notifica dei nuovi annunci
  ed eliminazione dei messaggi dei biglietti venduti (fallback a "VENDUTO" quando
  Telegram non consente l'eliminazione)
- Monitoraggio delle news ufficiali con traduzione automatica in italiano,
  immagine di copertina e link all'articolo originale
- Monitoraggio della disponibilità dei biglietti sul sito ufficiale, con alert a
  ogni scaglione del 5% superato verso il basso e al sold out
- Previsioni meteo per Jaroměř via Open-Meteo, report automatico giornaliero nei
  giorni intorno al festival e snapshot dalla webcam di Josefov
- Comandi `/start`, `/status`, `/listings`, `/availability`, `/news`, `/weather`,
  con menù "/" riscritto a ogni avvio
- Stato persistente sul volume Fly.io `/data` con scrittura atomica
- Supporto ai topic dei forum Telegram, separati per ticket, news e meteo

### Corretto

- News pubblicate in inglese quando il traduttore restituiva il testo originale:
  catena di traduttori con fallback e scarto delle non-traduzioni
- News bloccate dal parsing Markdown della caption
