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

## [1.2.0] - 2026-09-09

### Aggiunto

- Monitoraggio della disponibilità di hotel e campeggi della pagina
  accommodation, con le stesse regole dei biglietti: riepilogo iniziale con la
  percentuale attuale, un alert a ogni multiplo del 5% superato verso il basso
  e l'annuncio del sold out. Comando `/accommodation` per interrogarla a
  richiesta, e gli alloggi tracciati compaiono anche in `/status`
- `TELEGRAM_ACCOMMODATION_TOPIC_ID` per pubblicare gli alert sugli alloggi in
  un topic dedicato; se non è impostata seguono il topic dei biglietti
- `ACCOMMODATION_PRODUCT_MATCH` per filtrare gli alloggi per nome. Vuota di
  default: nella pagina alloggi non ci sono voucher da escludere e due prodotti
  hanno l'anno sbagliato nel nome (`BA 2026`), uno dei quali è una piazzola in
  vendita al 98% — un filtro per anno l'avrebbe resa invisibile

### Modificato

- Lo scraper della disponibilità, il file di stato e il ciclo di controllo sono
  ora parametrici e servono sia i biglietti sia gli alloggi: gli alloggi sono
  una sezione dello stesso shop e usano lo stesso template, quindi il parsing
  non è stato duplicato. Una seconda copia sarebbe divergiuta alla prima
  correzione applicata a una sola delle due, che è esattamente come il sold out
  dei biglietti è rimasto per mesi non annunciato
- Il tetto alle schede prodotto scaricate in un ciclo è diventato un parametro:
  la pagina alloggi ne elenca 28, più del tetto di 25 pensato per i biglietti,
  e i prodotti oltre il limite venivano scartati senza una riga nei log
- Le schede prodotto non vengono più scaricate tutte insieme: al massimo 8 per
  volta, per non aprire quasi trenta connessioni simultanee al sito

### Corretto

- Un messaggio oltre i 4096 caratteri viene ora pubblicato come più messaggi
  consecutivi, spezzato tra un prodotto e l'altro. Telegram non tronca i
  messaggi troppo lunghi, li rifiuta: il riepilogo iniziale degli alloggi
  (5146 caratteri con i 28 prodotti in pagina) non sarebbe mai arrivato, lo
  stato non sarebbe stato salvato e il bot avrebbe ritentato in silenzio a ogni
  ciclo senza che il monitoraggio partisse mai. Vale anche per le risposte di
  `/accommodation`, `/availability` e `/status`

## [1.1.0] - 2026-09-07

### Aggiunto

- Versionamento del bot: `version.py` come unica fonte di verità, comando
  `/version` con numero, data di rilascio e novità della versione in esecuzione,
  numero di versione anche in `/start`, `/status` e nei log all'avvio
- `release.py`: avanza la versione (`major`/`minor`/`patch`), sposta le voci di
  `[Non rilasciato]` nella nuova sezione del CHANGELOG, crea commit e tag `vX.Y.Z`

### Corretto

- Il sold out di una tipologia di biglietto non veniva mai annunciato nel
  gruppo: un biglietto esaurito non mostra la barra di disponibilità ma la
  scritta "Sold out", che veniva letta come disponibilità non leggibile e
  faceva saltare il prodotto a ogni controllo. Ora l'esaurimento è riconosciuto
  dalla scritta nella scheda e dal badge nella pagina elenco, e vale come
  disponibilità zero
- Un biglietto esaurito non viene più mostrato come "Disponibili: 0,0%" con il
  link all'acquisto nel riepilogo iniziale
- Un biglietto che compare già esaurito non genera nessun annuncio — il gruppo
  non ha mai saputo che esistesse — e viene annunciato come nuovo solo se torna
  acquistabile
- Un biglietto tornato in vendita sotto il 5% dopo un esaurimento restava
  marcato come sold out: `/status` lo dava per esaurito e l'esaurimento
  successivo non veniva più annunciato

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
