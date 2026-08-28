"""
Test funzionali senza dipendenze esterne: ciclo del Ticket Exchange (notifica,
conferma vendita, eliminazione/fallback dei messaggi) e report meteo giornaliero.

Uso:
    python test_bot.py

Non contatta né Telegram né i siti reali: usa un bot finto e dati simulati.
Lo stato viene scritto in una directory temporanea, mai su /data.
"""
import asyncio, json, os, sys, logging, tempfile
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

logging.basicConfig(level=logging.INFO, format="    | %(levelname)s %(name)s - %(message)s")

# Deve essere impostata prima di importare i moduli: lo stato non deve toccare /data
os.environ["BOT_DATA_DIR"] = tempfile.mkdtemp(prefix="brutalbot-test-")
sys.path.insert(0, str(Path(__file__).parent))

import main, notifier
from telegram.error import BadRequest, NetworkError
from tickets import ticket_state
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

print("\n=== 21. Scrittura atomica: nessun file temporaneo residuo ===")
leftovers = list(ticket_state.STATE_FILE.parent.glob(".seen_tickets.*.tmp"))
check("nessun .tmp residuo", leftovers == [])

print(f"\n===== {len(ok)} PASS, {len(fail)} FAIL =====")
for f in fail:
    print("  FAILED:", f)
sys.exit(1 if fail else 0)
