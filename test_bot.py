"""
Test funzionali senza dipendenze esterne: ciclo del Ticket Exchange (notifica,
conferma vendita, eliminazione/fallback dei messaggi) e report meteo giornaliero.

Uso:
    python test_bot.py

Non contatta né Telegram né i siti reali: usa un bot finto e dati simulati.
Lo stato viene scritto in una directory temporanea, mai su /data.
"""
import asyncio, json, os, re, sys, logging, tempfile
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

logging.basicConfig(level=logging.INFO, format="    | %(levelname)s %(name)s - %(message)s")

# Deve essere impostata prima di importare i moduli: lo stato non deve toccare /data
os.environ["BOT_DATA_DIR"] = tempfile.mkdtemp(prefix="brutalbot-test-")
sys.path.insert(0, str(Path(__file__).parent))

import main, notifier
from telegram.error import BadRequest, NetworkError
from tickets import availability_state, ticket_state
from weather_forecast import weather, weather_state

CHAT = "-100123456789"
ok = []
fail = []

def check(label, cond):
    (ok if cond else fail).append(label)
    print(("  PASS " if cond else "  FAIL ") + label)


class FakeBot:
    """Bot finto: registra le chiamate e simula gli errori richiesti."""
    def __init__(self, delete_error=None, edit_error=None):
        self.sent = []
        self.deleted = []
        self.edited = []
        self.photos = []
        self.delete_error = delete_error
        self.edit_error = edit_error
        self._next_id = 1000

    async def send_message(self, chat_id, text, **kw):
        self._next_id += 1
        self.sent.append({"chat_id": chat_id, "text": text, **kw})
        return SimpleNamespace(message_id=self._next_id)

    async def send_photo(self, chat_id, photo, **kw):
        self._next_id += 1
        self.photos.append({"chat_id": chat_id, **kw})
        return SimpleNamespace(message_id=self._next_id)

    async def delete_message(self, chat_id, message_id):
        if self.delete_error:
            raise self.delete_error
        self.deleted.append(message_id)
        return True

    async def edit_message_text(self, chat_id, message_id, text, **kw):
        if self.edit_error:
            raise self.edit_error
        self.edited.append({"message_id": message_id, "text": text})
        return True


def fake_app(bot):
    return SimpleNamespace(bot=bot)


def listing(i, price="100.00"):
    return {"id": str(i), "product": f"Festival Pass {i}", "price": price,
            "url": f"https://brutalassault.cz/en/xchange/detail/id/{i}"}


def reset_state():
    ticket_state.STATE_FILE.unlink(missing_ok=True)
    weather_state.WEATHER_STATE_FILE.unlink(missing_ok=True)


async def run_cycle(bot, listings):
    """Esegue check_exchange con fetch_listings che ritorna `listings`."""
    async def fake_fetch():
        return listings
    main.fetch_listings = fake_fetch
    await main.check_exchange(fake_app(bot), CHAT, 456)


# ---------------------------------------------------------------- ciclo normale
print("\n=== 1. Nuovo annuncio -> notificato e tracciato ===")
reset_state()
bot = FakeBot()
asyncio.run(run_cycle(bot, [listing(1), listing(2)]))
state = ticket_state.load_state()
check("2 messaggi inviati", len(bot.sent) == 2)
check("message_thread_id passato per topic reale", bot.sent[0].get("message_thread_id") == 456)
check("2 annunci tracciati con record", len(state) == 2 and isinstance(state["1"], dict))
check("record contiene message_id e chat_id", state["1"]["message_id"] == 1001 and state["1"]["chat_id"] == CHAT)

print("\n=== 2. Nessun annuncio nuovo -> nessun re-invio ===")
bot2 = FakeBot()
asyncio.run(run_cycle(bot2, [listing(1), listing(2)]))
check("nessun duplicato", len(bot2.sent) == 0)

print("\n=== 3. Annuncio sparito: primo ciclo attende conferma ===")
bot3 = FakeBot()
asyncio.run(run_cycle(bot3, [listing(2)]))
state = ticket_state.load_state()
check("nessuna eliminazione al primo ciclo", bot3.deleted == [])
check("annuncio ancora tracciato con missing_count=1", state["1"]["missing_count"] == 1)

print("\n=== 4. Secondo ciclo consecutivo -> venduto, messaggio eliminato ===")
bot4 = FakeBot()
asyncio.run(run_cycle(bot4, [listing(2)]))
state = ticket_state.load_state()
check("messaggio eliminato", bot4.deleted == [1001])
check("annuncio rimosso dallo stato", "1" not in state)
check("l'altro annuncio resta", "2" in state and state["2"]["missing_count"] == 0)

print("\n=== 5. Annuncio riapparso -> missing_count azzerato ===")
reset_state()
bot5 = FakeBot()
asyncio.run(run_cycle(bot5, [listing(1)]))
asyncio.run(run_cycle(bot5, []))                      # sparito (1/2)
asyncio.run(run_cycle(bot5, [listing(1)]))            # riapparso
state = ticket_state.load_state()
check("missing_count azzerato", state["1"]["missing_count"] == 0)
check("nessuna eliminazione", bot5.deleted == [])
asyncio.run(run_cycle(bot5, []))                      # sparito (1/2)
check("serve di nuovo la conferma", ticket_state.load_state()["1"]["missing_count"] == 1)

# ---------------------------------- delete rifiutato -> fallback "venduto"
print("\n=== 6. delete vietato (>48h / no admin) -> messaggio marcato VENDUTO ===")
reset_state()
bot6 = FakeBot(delete_error=BadRequest("Message can't be deleted for everyone"))
asyncio.run(run_cycle(bot6, [listing(7)]))
asyncio.run(run_cycle(bot6, []))
asyncio.run(run_cycle(bot6, []))
state = ticket_state.load_state()
check("messaggio modificato invece che eliminato", len(bot6.edited) == 1)
check("testo marcato come VENDUTO", "VENDUTO" in bot6.edited[0]["text"])
check("prodotto conservato nel messaggio", "Festival Pass 7" in bot6.edited[0]["text"])
check("annuncio rimosso dallo stato", state == {})

print("\n=== 7. Messaggio già assente -> chiuso senza errori ===")
reset_state()
bot7 = FakeBot(delete_error=BadRequest("Message to delete not found"))
asyncio.run(run_cycle(bot7, [listing(8)]))
asyncio.run(run_cycle(bot7, []))
asyncio.run(run_cycle(bot7, []))
check("nessun tentativo di modifica", bot7.edited == [])
check("stato pulito", ticket_state.load_state() == {})

print("\n=== 8. Errore di rete -> ritentato, non perso ===")
reset_state()
bot8 = FakeBot(delete_error=NetworkError("connection reset"))
asyncio.run(run_cycle(bot8, [listing(9)]))
asyncio.run(run_cycle(bot8, []))
asyncio.run(run_cycle(bot8, []))
state = ticket_state.load_state()
check("annuncio conservato per il retry", "9" in state)
check("delete_attempts incrementato", state["9"]["delete_attempts"] == 1)
# il delete poi funziona
bot8.delete_error = None
asyncio.run(run_cycle(bot8, []))
check("eliminato al tentativo successivo", bot8.deleted == [1001])
check("stato pulito", ticket_state.load_state() == {})

print("\n=== 9. Fallimenti ripetuti -> abbandono dopo MAX_DELETE_ATTEMPTS ===")
reset_state()
bot9 = FakeBot(delete_error=NetworkError("down"))
asyncio.run(run_cycle(bot9, [listing(10)]))
for _ in range(main.MAX_DELETE_ATTEMPTS + 2):
    asyncio.run(run_cycle(bot9, []))
check("stato non cresce all'infinito", ticket_state.load_state() == {})

# ---------------------------------------------- pagina non parsabile
print("\n=== 10. Scraping fallito (None) -> nessuna eliminazione di massa ===")
reset_state()
bot10 = FakeBot()
asyncio.run(run_cycle(bot10, [listing(11), listing(12)]))
asyncio.run(run_cycle(bot10, None))
asyncio.run(run_cycle(bot10, None))
state = ticket_state.load_state()
check("nessun messaggio eliminato", bot10.deleted == [])
check("annunci ancora tracciati", len(state) == 2)
check("missing_count intatto", state["11"]["missing_count"] == 0)

print("\n=== 11. Mercato chiuso (lista vuota) -> messaggi rimossi dopo conferma ===")
bot11 = FakeBot()
asyncio.run(run_cycle(bot11, []))
asyncio.run(run_cycle(bot11, []))
check("entrambi i messaggi eliminati", sorted(bot11.deleted) == [1001, 1002])
check("stato pulito", ticket_state.load_state() == {})

print("\n=== 12. Parsing: struttura ignota -> None, mercato chiuso -> [] ===")
from tickets.ticket_scraper import parse_listings
check("HTML ignoto -> None", parse_listings("<html><body><p>boh</p></body></html>") is None)
check("mercato chiuso -> []", parse_listings("<html><body><h1>Xchange tickets market is closed!</h1></body></html>") == [])

print("\n=== 13. Stato legacy { id: message_id } migrato ===")
reset_state()
ticket_state.STATE_FILE.write_text(json.dumps({"listings": {"55": 777}}), encoding="utf-8")
state = ticket_state.load_state()
check("record migrato", state["55"]["message_id"] == 777 and state["55"]["missing_count"] == 0)
bot13 = FakeBot()
asyncio.run(run_cycle(bot13, []))
asyncio.run(run_cycle(bot13, []))
check("messaggio legacy eliminato usando chat_id di default", bot13.deleted == [777])

# ------------------------------------------------------------------ meteo
print("\n=== 14. Meteo: pubblicato nel topic General senza message_thread_id ===")
reset_state()
OGGI = weather.today()   # stesso fuso usato da weather_tick (Europe/Prague)
FAKE_DATA = {"daily": {
    "time": [(OGGI + timedelta(days=i)).isoformat() for i in range(4)],
    "weathercode": [0, 3, 61, 95],
    "temperature_2m_max": [28.0, 26.0, 22.0, 24.0],
    "temperature_2m_min": [14.0, 15.0, 13.0, 12.0],
    "precipitation_sum": [0.0, 0.2, 8.0, 3.0],
    "windspeed_10m_max": [10.0, 12.0, 30.0, 25.0],
}}
weather.FESTIVAL_START = OGGI
weather.FESTIVAL_END = OGGI + timedelta(days=3)
notifier.fetch_weather_festival = lambda: asyncio.sleep(0, result=FAKE_DATA)
notifier.format_weather_festival = weather.format_weather_festival
notifier.fetch_webcam_snapshot = lambda: asyncio.sleep(0, result=None)
main.festival_window_open = weather.festival_window_open
# I test girano a qualsiasi ora: senza questo, prima delle 08:00 di Praga
# weather_tick uscirebbe subito e i controlli sul meteo fallirebbero tutti.
main.WEATHER_REPORT_HOUR = 0

botw = FakeBot()
asyncio.run(main.weather_tick(fake_app(botw), CHAT, 1))   # topic General
check("meteo pubblicato", len(botw.sent) == 1)
check("nessun message_thread_id per General", "message_thread_id" not in botw.sent[0])
check("giorni in italiano", any(g in botw.sent[0]["text"] for g in weather.GIORNI_IT))
check("solo giorni del festival", botw.sent[0]["text"].count("🌡️") == 4)

print("\n=== 15. Meteo: una sola pubblicazione al giorno ===")
asyncio.run(main.weather_tick(fake_app(botw), CHAT, 1))
check("nessun doppione", len(botw.sent) == 1)

print("\n=== 16. Meteo: recupero dopo riavvio (stato azzerato) ===")
weather_state.WEATHER_STATE_FILE.unlink(missing_ok=True)
botw2 = FakeBot()
asyncio.run(main.weather_tick(fake_app(botw2), CHAT, None))
check("report recuperato dopo il riavvio", len(botw2.sent) == 1)

print("\n=== 17. Meteo: send_photo fallito -> fallback testo ===")
class PhotoFailBot(FakeBot):
    async def send_photo(self, *a, **kw):
        raise BadRequest("IMAGE_PROCESS_FAILED")
notifier.fetch_webcam_snapshot = lambda: asyncio.sleep(0, result=b"\xff\xd8\xff notjpeg")
weather_state.WEATHER_STATE_FILE.unlink(missing_ok=True)
botp = PhotoFailBot()
asyncio.run(main.weather_tick(fake_app(botp), CHAT, 456))
check("fallback su messaggio di testo", len(botp.sent) == 1)
check("thread id corretto nel fallback", botp.sent[0].get("message_thread_id") == 456)

print("\n=== 18. Meteo: invio fallito -> ritentato al ciclo dopo ===")
class DeadBot(FakeBot):
    async def send_message(self, *a, **kw):
        raise BadRequest("message thread not found")
notifier.fetch_webcam_snapshot = lambda: asyncio.sleep(0, result=None)
weather_state.WEATHER_STATE_FILE.unlink(missing_ok=True)
asyncio.run(main.weather_tick(fake_app(DeadBot()), CHAT, 999))
check("data non registrata dopo il fallimento", weather_state.load_last_report_date() is None)
botr = FakeBot()
asyncio.run(main.weather_tick(fake_app(botr), CHAT, 1))
check("ritentato con successo", len(botr.sent) == 1)

print("\n=== 19. Meteo: valori mancanti nell'API -> report pubblicato lo stesso ===")
# Open-Meteo restituisce null per le giornate al limite dell'orizzonte del
# modello: un None dentro le f-string faceva fallire l'intero report.
BUCATA = {"daily": {
    "time": FAKE_DATA["daily"]["time"],
    "weathercode": [0, 3, None, 95],
    "temperature_2m_max": [28.0, 26.0, None, 24.0],
    "temperature_2m_min": [14.0, 15.0, 13.0, 12.0],
    "precipitation_sum": [0.0, 0.2, None, 3.0],
    "windspeed_10m_max": [10.0, 12.0, 30.0, None],
}}
notifier.fetch_weather_festival = lambda: asyncio.sleep(0, result=BUCATA)
weather_state.WEATHER_STATE_FILE.unlink(missing_ok=True)
botn = FakeBot()
asyncio.run(main.weather_tick(fake_app(botn), CHAT, 1))
check("report pubblicato con valori nulli", len(botn.sent) == 1)
check("tutti i giorni presenti", botn.sent[0]["text"].count("🌡️") == 4)
check("valore mancante segnato N/D", "N/D" in botn.sent[0]["text"])
check("data registrata", weather_state.load_last_report_date() is not None)

print("\n=== 19b. Meteo: giorni del festival oltre l'orizzonte -> avviso, non un post vuoto ===")
PARZIALE = {"daily": {
    "time": FAKE_DATA["daily"]["time"][:2],
    "weathercode": [0, 3],
    "temperature_2m_max": [28.0, 26.0],
    "temperature_2m_min": [14.0, 15.0],
    "precipitation_sum": [0.0, 0.2],
    "windspeed_10m_max": [10.0, 12.0],
}}
notifier.fetch_weather_festival = lambda: asyncio.sleep(0, result=PARZIALE)
weather_state.WEATHER_STATE_FILE.unlink(missing_ok=True)
botpz = FakeBot()
asyncio.run(main.weather_tick(fake_app(botpz), CHAT, 1))
check("report pubblicato con i giorni disponibili", len(botpz.sent) == 1)
check("solo i 2 giorni previsti", botpz.sent[0]["text"].count("🌡️") == 2)
check("giornate mancanti segnalate", "oltre l'orizzonte" in botpz.sent[0]["text"])

print("\n=== 19c. Meteo: caption troppo lunga -> pubblicato come testo, senza webcam ===")
notifier.fetch_weather_festival = lambda: asyncio.sleep(0, result=FAKE_DATA)
notifier.fetch_webcam_snapshot = lambda: asyncio.sleep(0, result=b"\xff\xd8\xff jpeg")
weather_state.WEATHER_STATE_FILE.unlink(missing_ok=True)
botlong = FakeBot()
asyncio.run(notifier.send_weather_message(botlong, CHAT, 1, "x" * (notifier.MAX_CAPTION_LENGTH + 1)))
check("nessuna foto oltre il limite di caption", botlong.photos == [])
check("pubblicato come messaggio di testo", len(botlong.sent) == 1)
notifier.fetch_webcam_snapshot = lambda: asyncio.sleep(0, result=None)

print("\n=== 19f. Topic chiuso: l'errore dice cosa fare ===")
class ClosedTopicBot(FakeBot):
    async def send_message(self, *a, **kw):
        raise BadRequest("Topic_closed")

    async def send_photo(self, *a, **kw):
        raise BadRequest("Topic_closed")

weather_state.WEATHER_STATE_FILE.unlink(missing_ok=True)
asyncio.run(main.weather_tick(fake_app(ClosedTopicBot()), CHAT, 456))
check("report non registrato: ritentato al ciclo dopo",
      weather_state.load_last_report_date() is None)
check("errore su topic chiuso spiegato",
      "«Gestire i topic»" in notifier.explain(BadRequest("Topic_closed")))
check("errore su topic inesistente spiegato",
      "TELEGRAM_*_TOPIC_ID" in notifier.explain(BadRequest("Message thread not found")))
check("errore sconosciuto lasciato intatto",
      notifier.explain(BadRequest("Boom")) == "Boom")

print("\n=== 19d. Meteo: fuori finestra -> niente ===")
weather.FESTIVAL_START = OGGI + timedelta(days=200)
weather.FESTIVAL_END = OGGI + timedelta(days=203)
weather_state.WEATHER_STATE_FILE.unlink(missing_ok=True)
boto = FakeBot()
asyncio.run(main.weather_tick(fake_app(boto), CHAT, 1))
check("nessun messaggio fuori finestra", boto.sent == [] and boto.photos == [])

print("\n=== 19e. Meteo: finestra aperta a 15 giorni, chiusa a 16 ===")
weather.FESTIVAL_START = OGGI + timedelta(days=15)
weather.FESTIVAL_END = OGGI + timedelta(days=18)
check("finestra aperta a 15 giorni", weather.festival_window_open())
weather.FESTIVAL_START = OGGI + timedelta(days=16)
weather.FESTIVAL_END = OGGI + timedelta(days=19)
check("finestra chiusa a 16 giorni", not weather.festival_window_open())
weather.FESTIVAL_START = OGGI - timedelta(days=3)
weather.FESTIVAL_END = OGGI
check("finestra aperta l'ultimo giorno del festival", weather.festival_window_open())
weather.FESTIVAL_START = OGGI - timedelta(days=4)
weather.FESTIVAL_END = OGGI - timedelta(days=1)
check("finestra chiusa a festival concluso", not weather.festival_window_open())

print("\n=== 20. Markdown non valido -> re-invio senza formattazione ===")
reset_state()
class MdFailBot(FakeBot):
    async def send_message(self, chat_id, text, **kw):
        if kw.get("parse_mode"):
            raise BadRequest("Can't parse entities: can't find end of the entity")
        return await super().send_message(chat_id, text, **kw)
botmd = MdFailBot()
asyncio.run(run_cycle(botmd, [{"id": "77", "product": "Pass *VIP_2026*", "price": "216.66",
                               "url": "https://brutalassault.cz/en/xchange/detail/id/77"}]))
state = ticket_state.load_state()
check("annuncio pubblicato in chiaro", len(botmd.sent) == 1 and "parse_mode" not in botmd.sent[0])
check("annuncio tracciato (nessun loop di re-invio)", state.get("77", {}).get("message_id") == 1001)
botmd2 = MdFailBot()
asyncio.run(run_cycle(botmd2, [{"id": "77", "product": "Pass *VIP_2026*", "price": "216.66",
                               "url": "https://brutalassault.cz/en/xchange/detail/id/77"}]))
check("nessun duplicato al ciclo dopo", botmd2.sent == [])

# ------------------------------------------- disponibilità biglietti in vendita
PASS_2027 = "first edition BRUTAL ASSAULT 2027 festival pass [e-ticket]"


def prodotto(percent, pid="1104", name=PASS_2027, sold_out=False):
    return {
        "id": pid,
        "name": name,
        "url": f"https://brutalassault.cz/en/tickets/detail/id/{pid}",
        "percent": percent,
        "sold_out": sold_out,
    }


def reset_avail():
    availability_state.AVAILABILITY_STATE_FILE.unlink(missing_ok=True)


async def run_avail(bot, prodotti, topic=456):
    """Esegue check_ticket_availability con fetch_ticket_availability simulata."""
    async def fake_fetch():
        return prodotti
    main.fetch_ticket_availability = fake_fetch
    await main.check_ticket_availability(fake_app(bot), CHAT, topic)


print("\n=== 22. Primo avvio -> riepilogo con la percentuale attuale ===")
reset_avail()
bota = FakeBot()
asyncio.run(run_avail(bota, [prodotto(35.012386457473)]))
stato = availability_state.load_availability_state()
check("messaggio iniziale pubblicato", len(bota.sent) == 1)
check("percentuale attuale non multipla di 5", "35,0%" in bota.sent[0]["text"])
check("inviato in HTML", bota.sent[0].get("parse_mode") == "HTML")
check("thread dei biglietti", bota.sent[0].get("message_thread_id") == 456)
check("stato inizializzato con soglia 35", stato["initialized"] and stato["products"]["1104"]["level"] == 35)

print("\n=== 23. Dentro lo stesso scaglione -> nessun messaggio ===")
botb = FakeBot()
asyncio.run(run_avail(botb, [prodotto(35.0)]))     # ancora nella banda 35
check("nessun alert entro lo stesso scaglione", botb.sent == [])
asyncio.run(run_avail(botb, [prodotto(33.4)]))     # sceso sotto il 35%
check("un alert alla discesa", len(botb.sent) == 1)
check("annuncia la soglia superata", "sotto il 35%" in botb.sent[0]["text"])
check("non annuncia soglie non superate", "30%" not in botb.sent[0]["text"])

print("\n=== 24. Discesa di più soglie insieme -> un solo messaggio ===")
reset_avail()
botc = FakeBot()
asyncio.run(run_avail(botc, [prodotto(35.0)]))     # riepilogo iniziale
botc.sent.clear()
asyncio.run(run_avail(botc, [prodotto(22.0)]))
check("un solo alert", len(botc.sent) == 1)
check("annuncia la soglia più bassa superata", "sotto il 25%" in botc.sent[0]["text"])
check("cita le soglie bruciate", "35%" in botc.sent[0]["text"] and "30%" in botc.sent[0]["text"])
check("scaglione registrato", availability_state.load_availability_state()["products"]["1104"]["level"] == 20)

print("\n=== 25. Disponibilità risalita -> nessun alert, scaglione rialzato ===")
botd = FakeBot()
asyncio.run(run_avail(botd, [prodotto(41.0)]))     # nuova tranche in vendita
check("nessun alert in risalita", botd.sent == [])
check("scaglione rialzato a 40", availability_state.load_availability_state()["products"]["1104"]["level"] == 40)
asyncio.run(run_avail(botd, [prodotto(38.0)]))
check("la discesa successiva riallerta", len(botd.sent) == 1 and "sotto il 40%" in botd.sent[0]["text"])

print("\n=== 26. Zero per cento -> sold out, annunciato una volta sola ===")
reset_avail()
bote = FakeBot()
# Sotto il 5% lo scaglione è già 0: l'esaurimento non cambia banda e va
# riconosciuto dalla percentuale, non dal salto di soglia.
asyncio.run(run_avail(bote, [prodotto(4.0)]))      # riepilogo iniziale
check("sotto il 5% non è un sold out", not availability_state.load_availability_state()["products"]["1104"]["sold_out"])
bote.sent.clear()
asyncio.run(run_avail(bote, [prodotto(0.0, sold_out=True)]))
check("sold out annunciato", len(bote.sent) == 1 and "SOLD OUT" in bote.sent[0]["text"])
check("rimanda al Ticket Exchange", "xchange" in bote.sent[0]["text"])
asyncio.run(run_avail(bote, [prodotto(0.0, sold_out=True)]))
check("nessun doppione di sold out", len(bote.sent) == 1)

print("\n=== 27. Biglietto sparito dalla pagina -> sold out dopo conferma ===")
reset_avail()
botf = FakeBot()
asyncio.run(run_avail(botf, [prodotto(3.0)]))      # riepilogo iniziale
botf.sent.clear()
asyncio.run(run_avail(botf, []))
check("nessun annuncio al primo ciclo di assenza", botf.sent == [])
check("assenza contata", availability_state.load_availability_state()["products"]["1104"]["missing_count"] == 1)
asyncio.run(run_avail(botf, []))
check("sold out al secondo ciclo", len(botf.sent) == 1 and "SOLD OUT" in botf.sent[0]["text"])
check("ultima disponibilità citata", "3,0%" in botf.sent[0]["text"])
check("prodotto non più tracciato", availability_state.load_availability_state()["products"] == {})

print("\n=== 28. Pagina illeggibile -> nessun messaggio, stato intatto ===")
reset_avail()
botg = FakeBot()
asyncio.run(run_avail(botg, [prodotto(30.0)]))     # riepilogo iniziale
botg.sent.clear()
asyncio.run(run_avail(botg, None))                 # fetch fallito
check("nessun messaggio su fetch fallito", botg.sent == [])
check("soglia invariata", availability_state.load_availability_state()["products"]["1104"]["level"] == 30)
asyncio.run(run_avail(botg, [prodotto(None)]))     # barra non parsabile
check("nessun sold out per barra illeggibile", botg.sent == [])
check("nessuna assenza contata", availability_state.load_availability_state()["products"]["1104"]["missing_count"] == 0)

print("\n=== 29. Invio fallito -> soglia non registrata, alert ritentato ===")
reset_avail()
class MuteBot(FakeBot):
    async def send_message(self, *a, **kw):
        raise BadRequest("message thread not found")
asyncio.run(run_avail(MuteBot(), [prodotto(35.0)]))
check("riepilogo iniziale non registrato se non parte", availability_state.load_availability_state()["initialized"] is False)
both = FakeBot()
asyncio.run(run_avail(both, [prodotto(35.0)]))
check("riepilogo ripubblicato al ciclo dopo", len(both.sent) == 1)
asyncio.run(run_avail(MuteBot(), [prodotto(29.0)]))
check("scaglione non registrato dopo invio fallito", availability_state.load_availability_state()["products"]["1104"]["level"] == 35)
both2 = FakeBot()
asyncio.run(run_avail(both2, [prodotto(29.0)]))
check("alert ritentato con successo", len(both2.sent) == 1 and "sotto il 30%" in both2.sent[0]["text"])

print("\n=== 30. Nuovo biglietto 2027 dopo l'avvio -> annunciato ===")
boti = FakeBot()
asyncio.run(run_avail(boti, [prodotto(29.0), prodotto(90.0, pid="1200", name="BRUTAL ASSAULT 2027 VIP lounge")]))
check("nuovo biglietto annunciato", len(boti.sent) == 1 and "Nuovo biglietto" in boti.sent[0]["text"])
check("percentuale del nuovo biglietto", "90,0%" in boti.sent[0]["text"])
check("entrambi tracciati", set(availability_state.load_availability_state()["products"]) == {"1104", "1200"})

print("\n=== 31. Nomi con caratteri speciali -> HTML valido ===")
botj = FakeBot()
reset_avail()
asyncio.run(run_avail(botj, [prodotto(50.0, name='Pass <2027> & "friends" [e-ticket]')], topic=1))
testo = botj.sent[0]["text"]
check("caratteri HTML neutralizzati", "&lt;2027&gt;" in testo and "&amp;" in testo)
check("nessun message_thread_id per General", "message_thread_id" not in botj.sent[0])

print("\n=== 32. Comando /availability -> disponibilità su richiesta ===")


class FakeMessage:
    """Messaggio finto: raccoglie le risposte del comando."""
    def __init__(self):
        self.replies = []

    async def reply_text(self, text, **kw):
        self.replies.append({"text": text, **kw})
        return SimpleNamespace(message_id=1)


def run_cmd(prodotti):
    async def fake_fetch():
        return prodotti
    main.fetch_ticket_availability = fake_fetch
    msg = FakeMessage()
    asyncio.run(main.cmd_availability(SimpleNamespace(message=msg), None))
    return msg.replies


risposte = run_cmd([prodotto(35.012386457473)])
check("risposta inviata dopo l'attesa", len(risposte) == 2)
check("percentuale attuale", "35,0%" in risposte[1]["text"])
check("annuncia la prossima soglia", "Prossimo avviso: sotto il 35%" in risposte[1]["text"])
check("inviato in HTML", risposte[1].get("parse_mode") == "HTML")

risposte = run_cmd([prodotto(3.0)])
check("sotto il 5% il prossimo avviso è il sold out", "Prossimo avviso: il sold out" in risposte[1]["text"])

risposte = run_cmd([prodotto(0.0, sold_out=True)])
check("biglietto esaurito segnalato", "SOLD OUT" in risposte[1]["text"])

risposte = run_cmd([])
check("nessun biglietto in vendita", "non risulta in vendita" in risposte[1]["text"])

risposte = run_cmd(None)
check("errore di lettura segnalato", "❌" in risposte[1]["text"])

risposte = run_cmd([prodotto(None)])
check("barra illeggibile segnalata", "illeggibile" in risposte[1]["text"])

print("\n=== 33. Menù comandi allineato agli handler ===")
# Il menù "/" di Telegram non si aggiorna da solo: se un comando ha l'handler ma
# non e in BOT_COMMANDS, resta invisibile nell'autocompletamento.
import inspect
registrati = set(re.findall(r'CommandHandler\("(\w+)"', inspect.getsource(main.main)))
nel_menu = {c.command for c in main.BOT_COMMANDS}
check("ogni handler e nel menu", registrati <= nel_menu)
check("nessun comando fantasma nel menu", nel_menu <= registrati)
check("availability presente", "availability" in nel_menu and "availability" in registrati)

msg_start = FakeMessage()
asyncio.run(main.cmd_start(SimpleNamespace(message=msg_start), None))
check("/start elenca tutti i comandi", all(f"/{c}" in msg_start.replies[0]["text"] for c in nel_menu))

# ------------------------------------------------------------------- news
from news import news_state

ARTICOLO = {
    "id": "https://brutalassault.cz/en/a/791/lost-and-found",
    "titolo": "Lost & found",
    "url": "https://brutalassault.cz/en/a/791/lost-and-found",
}
# Testo come arriva dal sito tradotto: asterischi, underscore e parentesi quadre
# spaiati sono esattamente ciò che faceva fallire il parsing Markdown.
DETTAGLI = {
    "image_url": "https://content.brutalassault.cz/images/articles/791.jpeg",
    "testo": "Hai perso qualcosa *al festival_? Scrivi a [lost&found <ora>.",
}


def reset_news():
    news_state.NEWS_STATE_FILE.unlink(missing_ok=True)
    news_state.NEWS_FAILURES_FILE.unlink(missing_ok=True)


def run_news(bot, articoli=(ARTICOLO,)):
    """Esegue check_news con fetch_news e lo scraper dell'articolo simulati."""
    async def fake_fetch():
        return list(articoli)
    main.fetch_news = fake_fetch
    notifier.fetch_article = lambda url: asyncio.sleep(0, result=DETTAGLI)
    notifier.translate = lambda testo: testo
    asyncio.run(main.check_news(fake_app(bot), CHAT, 789))


print("\n=== 34. News: testo con caratteri speciali -> HTML con escape ===")
reset_news()
bothtml = FakeBot()
run_news(bothtml)
caption = bothtml.photos[0]["caption"]
check("news pubblicata come foto", len(bothtml.photos) == 1)
check("inviata in HTML", bothtml.photos[0].get("parse_mode") == "HTML")
check("message_thread_id passato", bothtml.photos[0].get("message_thread_id") == 789)
check("titolo in grassetto ed escapato", "<b>Lost &amp; found</b>" in caption)
check("testo escapato", "[lost&amp;found &lt;ora&gt;." in caption)
check("news tracciata", ARTICOLO["id"] in news_state.load_seen())

print("\n=== 35. News: formattazione rifiutata -> re-invio senza formattazione ===")
class NewsFailBot(FakeBot):
    """Telegram rifiuta qualunque formattazione, come per l'articolo 791."""
    ERRORE = BadRequest(
        "Can't parse entities: can't find end of the entity starting at byte offset 277"
    )

    async def send_photo(self, chat_id, photo, **kw):
        if kw.get("parse_mode"):
            raise self.ERRORE
        return await super().send_photo(chat_id, photo, **kw)

    async def send_message(self, chat_id, text, **kw):
        if kw.get("parse_mode"):
            raise self.ERRORE
        return await super().send_message(chat_id, text, **kw)

reset_news()
botnews = NewsFailBot()
run_news(botnews)
check("news pubblicata in chiaro", len(botnews.photos) == 1 and "parse_mode" not in botnews.photos[0])
check("testo integrale nella caption", "[lost&found <ora>." in botnews.photos[0]["caption"])
check("news tracciata (nessun loop di re-invio)", ARTICOLO["id"] in news_state.load_seen())
botnews2 = NewsFailBot()
run_news(botnews2)
check("nessun duplicato al ciclo dopo", botnews2.photos == [] and botnews2.sent == [])

print("\n=== 35b. News senza immagine -> stesso fallback sul messaggio di testo ===")
reset_news()
DETTAGLI_NO_IMG = {"image_url": None, "testo": DETTAGLI["testo"]}
bottxt = NewsFailBot()
async def _fake_fetch_news():
    return [ARTICOLO]
main.fetch_news = _fake_fetch_news
notifier.fetch_article = lambda url: asyncio.sleep(0, result=DETTAGLI_NO_IMG)
notifier.translate = lambda testo: testo
asyncio.run(main.check_news(fake_app(bottxt), CHAT, 789))
check("pubblicata come testo senza formattazione",
      len(bottxt.sent) == 1 and "parse_mode" not in bottxt.sent[0])
check("nessuna foto inviata", bottxt.photos == [])

print("\n=== 36. News impubblicabile -> segnata come vista dopo N tentativi ===")
class NewsDownBot(FakeBot):
    """Nessun invio va a buon fine: errore non recuperabile dal fallback."""
    async def send_photo(self, chat_id, photo, **kw):
        raise NetworkError("Bad Gateway")

    async def send_message(self, chat_id, text, **kw):
        raise NetworkError("Bad Gateway")

reset_news()
botko = NewsDownBot()
for _ in range(main.MAX_NEWS_FAILURES - 1):
    run_news(botko)
check("news non pubblicata resta da ritentare", ARTICOLO["id"] not in news_state.load_seen())
check("tentativi contati", news_state.load_failures().get(ARTICOLO["id"]) == main.MAX_NEWS_FAILURES - 1)
run_news(botko)
check("news abbandonata dopo il limite", ARTICOLO["id"] in news_state.load_seen())
check("contatore ripulito", news_state.load_failures() == {})

print("\n=== 36b. Invio riuscito dopo un errore -> contatore azzerato ===")
reset_news()
run_news(NewsDownBot())
check("primo tentativo fallito registrato", news_state.load_failures().get(ARTICOLO["id"]) == 1)
run_news(FakeBot())
check("news pubblicata al tentativo successivo", ARTICOLO["id"] in news_state.load_seen())
check("contatore azzerato dopo la pubblicazione", news_state.load_failures() == {})

# ------------------------------------------------------------- traduzione
import translator

INGLESE = (
    "As every year, a lot of items have accumulated again this year that you likely "
    "left behind in a metal haze, lost in the mosh pit, or misplaced in any other way. "
    "You can find them at this link: https://drive.google.com/drive/folders/1_rEvNhDxpd "
    "If you spot something in the photos that belongs to you, please contact us."
)
ITALIANO = "Come ogni anno si sono accumulati oggetti smarriti: li trovate a questo link."


def usa_backend(*backend):
    """Sostituisce i servizi di traduzione con quelli finti passati."""
    translator._backends = lambda: tuple(backend)


def ko(errore):
    def _backend(testo):
        raise errore
    return _backend


print("\n=== 37. Traduzione: il testo lungo viene spezzato sotto il limite ===")
lungo = INGLESE * 4
pezzi = translator.split_chunks(lungo, limite=200)
check("nessun blocco oltre il limite", all(len(p) <= 200 for p in pezzi))
check("nessuna parola troncata", all(p == p.strip() for p in pezzi))
check("testo integro una volta ricomposto", " ".join(pezzi).split() == lungo.split())
check("testo corto non spezzato", translator.split_chunks("Poche parole.", 200) == ["Poche parole."])

print("\n=== 38. Traduzione: primo servizio ko -> si passa al secondo ===")
usa_backend(("Google", ko(RuntimeError("No translation was found"))),
            ("MyMemory", lambda testo: ITALIANO))
check("news tradotta dal servizio di riserva", translator.translate(INGLESE) == ITALIANO)

print("\n=== 39. Traduzione: testo restituito uguale all'originale -> non è una traduzione ===")
# translate.google.com/m sotto throttling rimanda indietro il testo di partenza
# invece di fallire: e' cosi' che le news arrivavano nel gruppo in inglese.
usa_backend(("Google", lambda testo: testo), ("MyMemory", lambda testo: ITALIANO))
check("scartata la non-traduzione, usato il secondo servizio", translator.translate(INGLESE) == ITALIANO)
usa_backend(("Google", lambda testo: testo), ("MyMemory", ko(RuntimeError("quota"))))
check("titolo corto identico accettato", translator.translate("Lost and Found") == "Lost and Found")

print("\n=== 40. Traduzione: tutti i servizi ko -> testo originale, news pubblicata ===")
usa_backend(("Google", ko(RuntimeError("429"))), ("MyMemory", ko(RuntimeError("timeout"))))
check("nessuna eccezione propagata", translator.translate(INGLESE) == INGLESE)
check("testo vuoto gestito", translator.translate("") == "")

print("\n=== 41. MyMemory: quota esaurita -> non finisce nel gruppo ===")
class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload

QUOTA_FINITA = {
    "responseStatus": 200,
    "quotaFinished": True,
    "responseData": {"translatedText":
        "MYMEMORY WARNING: YOU USED ALL AVAILABLE FREE TRANSLATIONS FOR TODAY."},
}
translator.requests = SimpleNamespace(get=lambda *a, **kw: FakeResponse(QUOTA_FINITA))
usa_backend(("MyMemory", translator._translate_mymemory))
check("avviso di quota non pubblicato", translator.translate(INGLESE) == INGLESE)

ERRORE_API = {"responseStatus": 403, "responseDetails": "INVALID LANGUAGE PAIR",
              "responseData": {"translatedText": "boh"}}
translator.requests = SimpleNamespace(get=lambda *a, **kw: FakeResponse(ERRORE_API))
check("errore dell'API non pubblicato", translator.translate(INGLESE) == INGLESE)

TRADOTTO = {"responseStatus": "200", "quotaFinished": False,
            "responseData": {"translatedText": ITALIANO}}
translator.requests = SimpleNamespace(get=lambda *a, **kw: FakeResponse(TRADOTTO))
check("risposta valida usata", translator.translate(INGLESE).startswith(ITALIANO))

import requests as _requests
translator.requests = _requests   # ripristina il modulo vero per i test successivi

print("\n=== 21. Scrittura atomica: nessun file temporaneo residuo ===")
leftovers = list(ticket_state.STATE_FILE.parent.glob(".seen_tickets.*.tmp"))
leftovers += list(availability_state.AVAILABILITY_STATE_FILE.parent.glob(".ticket_availability.*.tmp"))
check("nessun .tmp residuo", leftovers == [])

print("\n=== 42. Versioning: numero, changelog e comando /version ===")
import release, version

check("bump patch", release.bump("1.0.0", "patch") == "1.0.1")
check("bump minor azzera la patch", release.bump("1.4.7", "minor") == "1.5.0")
check("bump major azzera minor e patch", release.bump("1.4.7", "major") == "2.0.0")
try:
    release.bump("1.2", "patch")
    check("versione malformata rifiutata", False)
except ValueError:
    check("versione malformata rifiutata", True)

# La versione del file e quella del CHANGELOG devono coincidere: e' l'unico modo
# per sapere quale codice sta girando su Fly.io, dove il repository non c'e'.
check("la versione in esecuzione e annotata nel CHANGELOG", version.release_notes() != [])
check("il numero e semanticamente valido", release.parse_version(version.__version__))

CHANGELOG_FINTO = """# Changelog

## [Non rilasciato]

### Aggiunto

- Comando /version
- Alert sui biglietti, con soglie
  scritte su due righe

## [1.0.0] - 2026-08-28

- Prima versione
"""

nuovo, voci = release.roll_changelog(CHANGELOG_FINTO, "1.1.0", "2026-09-01")
check("le voci passano nella nuova versione", voci == ["Comando /version", "Alert sui biglietti, con soglie"])
check("sezione della nuova versione creata", "## [1.1.0] - 2026-09-01" in nuovo)
check("le voci non restano in Non rilasciato",
      nuovo.index("Comando /version") > nuovo.index("## [1.1.0]"))
check("Non rilasciato resta pronto per il lavoro dopo", "## [Non rilasciato]" in nuovo)
check("le versioni precedenti restano", "## [1.0.0] - 2026-08-28" in nuovo)
check("le voci della nuova versione sono leggibili dal bot",
      version.parse_release_notes(nuovo, "1.1.0")[0] == "Comando /version")
check("le righe spezzate vengono ricomposte",
      version.parse_release_notes(nuovo, "1.1.0")[1].endswith("con soglie scritte su due righe"))
check("nessuna voce residua in Non rilasciato",
      version.parse_release_notes(nuovo, "Non rilasciato") == [])

# Un secondo rilascio sopra il primo non deve inglobare le voci gia' pubblicate
_, voci_vuote = release.roll_changelog(nuovo, "1.1.1", "2026-09-02")
check("rilascio senza novita non ruba le voci precedenti", voci_vuote == [])

try:
    release.roll_changelog("# Changelog\n\n## [1.0.0] - 2026-08-28\n", "1.1.0", "2026-09-01")
    check("CHANGELOG senza Non rilasciato rifiutato", False)
except ValueError:
    check("CHANGELOG senza Non rilasciato rifiutato", True)

sorgente = 'x = 1\n__version__ = "1.0.0"\n__release_date__ = "2026-08-28"\n'
aggiornato = release.rewrite_version_file(sorgente, "1.1.0", "2026-09-01")
check("version.py riscritto con numero e data",
      '__version__ = "1.1.0"' in aggiornato and '__release_date__ = "2026-09-01"' in aggiornato)

msg_version = FakeMessage()
asyncio.run(main.cmd_version(SimpleNamespace(message=msg_version), None))
risposta = msg_version.replies[0]
check("/version mostra il numero", f"v{version.__version__}" in risposta["text"])
check("/version mostra la data di rilascio", version.__release_date__ in risposta["text"])
check("/version elenca le novita", "Novità di questa versione" in risposta["text"])
check("/version inviato in HTML", risposta.get("parse_mode") == "HTML")
check("nessun backtick del changelog nel messaggio", "`" not in risposta["text"])

msg_status = FakeMessage()
asyncio.run(main.cmd_status(SimpleNamespace(message=msg_status), None))
check("/status riporta la versione", version.__version__ in msg_status.replies[0]["text"])

msg_v_start = FakeMessage()
asyncio.run(main.cmd_start(SimpleNamespace(message=msg_v_start), None))
check("/start riporta la versione", f"v{version.__version__}" in msg_v_start.replies[0]["text"])

print(f"\n===== {len(ok)} PASS, {len(fail)} FAIL =====")
for f in fail:
    print("  FAILED:", f)
sys.exit(1 if fail else 0)
