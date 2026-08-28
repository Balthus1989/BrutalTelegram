import logging
import os
import httpx
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

LATITUDE = 50.3567
LONGITUDE = 15.9183
CITY_NAME = "Jaroměř, CZ"

# Tutto il calcolo delle date usa il fuso del festival: il container su Fly.io
# gira in UTC, quindi date.today() poteva indicare il giorno precedente rispetto
# all'ora locale usata dallo scheduler e dall'API Open-Meteo.
FESTIVAL_TZ = ZoneInfo("Europe/Prague")

# Limite dell'API Open-Meteo: oltre i 16 giorni la richiesta viene rifiutata.
MAX_FORECAST_DAYS = 16

# Timeout della chiamata all'API: il default di httpx e 5s e un rallentamento
# temporaneo faceva saltare il report della giornata.
FORECAST_TIMEOUT = 20


def today() -> date:
    """Data odierna nel fuso del festival (Europe/Prague)."""
    return datetime.now(FESTIVAL_TZ).date()


def _festival_date(env_var: str, default: date) -> date:
    """Data del festival, sovrascrivibile da ambiente (formato YYYY-MM-DD)."""
    raw = os.getenv(env_var)
    if not raw:
        return default
    try:
        return datetime.strptime(raw.strip(), "%Y-%m-%d").date()
    except ValueError:
        logger.warning(f"{env_var}='{raw}' non è una data valida (YYYY-MM-DD): uso {default}.")
        return default


# Aggiornare a ogni edizione (o impostare FESTIVAL_START / FESTIVAL_END nell'ambiente):
# fuori da questo periodo il report meteo automatico resta silente.
FESTIVAL_START = _festival_date("FESTIVAL_START", date(2026, 8, 5))
FESTIVAL_END = _festival_date("FESTIVAL_END", date(2026, 8, 8))

# Giorni prima dell'inizio in cui il report automatico è attivo.
REPORT_WINDOW_DAYS = 15

WMO_CODES = {
    0: "☀️ Sereno", 1: "🌤️ Prevalentemente sereno", 2: "⛅ Parzialmente nuvoloso",
    3: "☁️ Coperto", 45: "🌫️ Nebbia", 48: "🌫️ Nebbia gelata",
    51: "🌦️ Pioggerella leggera", 53: "🌦️ Pioggerella", 55: "🌧️ Pioggerella intensa",
    61: "🌧️ Pioggia leggera", 63: "🌧️ Pioggia", 65: "🌧️ Pioggia intensa",
    71: "🌨️ Neve leggera", 73: "🌨️ Neve", 75: "❄️ Neve intensa",
    80: "🌦️ Rovesci leggeri", 81: "🌧️ Rovesci", 82: "⛈️ Rovesci intensi",
    95: "⛈️ Temporale", 96: "⛈️ Temporale con grandine", 99: "⛈️ Temporale forte",
}

# Nomi dei giorni in italiano: strftime("%A") dipende dal locale del container
# (su Fly.io è C/POSIX e produce "Tuesday" invece di "Martedì").
GIORNI_IT = [
    "Lunedì", "Martedì", "Mercoledì", "Giovedì", "Venerdì", "Sabato", "Domenica",
]

MESI_IT = [
    "gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno",
    "luglio", "agosto", "settembre", "ottobre", "novembre", "dicembre",
]


def format_day(giorno: date) -> str:
    """Es. 'Venerdì 07/08' — indipendente dal locale di sistema."""
    return f"{GIORNI_IT[giorno.weekday()]} {giorno.strftime('%d/%m')}"


def days_until_festival() -> int:
    return (FESTIVAL_START - today()).days


def days_until_festival_end() -> int:
    return (FESTIVAL_END - today()).days


def festival_window_open() -> bool:
    """
    True se oggi il report meteo automatico va pubblicato: nei 15 giorni che
    precedono il festival e per tutta la sua durata.
    """
    return days_until_festival_end() >= 0 and days_until_festival() <= REPORT_WINDOW_DAYS


def festival_dates() -> list[date]:
    delta = (FESTIVAL_END - FESTIVAL_START).days
    return [FESTIVAL_START + timedelta(days=i) for i in range(delta + 1)]


async def _fetch(forecast_days: int) -> dict:
    """Chiamata base all'API Open-Meteo."""
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": LATITUDE,
        "longitude": LONGITUDE,
        "daily": ",".join([
            "weathercode", "temperature_2m_max", "temperature_2m_min",
            "precipitation_sum", "windspeed_10m_max",
        ]),
        "timezone": "Europe/Prague",
        "forecast_days": max(1, min(forecast_days, MAX_FORECAST_DAYS)),
    }
    async with httpx.AsyncClient(timeout=FORECAST_TIMEOUT) as client:
        r = await client.get(url, params=params)
        r.raise_for_status()
        return r.json()


async def fetch_weather_command() -> dict:
    """
    Per il comando /meteo: sempre 7 giorni da oggi, nessuna restrizione.
    """
    return await _fetch(forecast_days=7)


async def fetch_weather_festival() -> dict | None:
    """
    Per il report automatico: solo nei 15 giorni prima del festival e durante.
    Restituisce None se fuori finestra (troppo presto o festival passato).
    """
    giorni = days_until_festival()
    days_to_end = days_until_festival_end()

    if days_to_end < 0:
        logger.info(f"Festival terminato il {FESTIVAL_END}: nessun report meteo.")
        return None  # Festival passato
    if giorni > REPORT_WINDOW_DAYS:
        logger.info(f"Mancano {giorni} giorni al festival: report meteo non ancora attivo.")
        return None  # Troppo presto

    # Open-Meteo copre al massimo 16 giorni da oggi: nei primi giorni della
    # finestra le ultime giornate del festival non sono ancora previste.
    forecast_days = min(days_to_end + 1, MAX_FORECAST_DAYS)
    if days_to_end + 1 > MAX_FORECAST_DAYS:
        logger.info(
            f"Il festival finisce fra {days_to_end} giorni: Open-Meteo ne copre "
            f"{MAX_FORECAST_DAYS}, il report mostrerà solo le giornate disponibili."
        )
    return await _fetch(forecast_days=forecast_days)


def _value(daily: dict, key: str, i: int):
    """Valore giornaliero, o None se l'API non lo fornisce per quel giorno."""
    serie = daily.get(key)
    if not isinstance(serie, list) or i >= len(serie):
        return None
    return serie[i]


def _num(value, spec: str, suffix: str = "") -> str:
    """
    Formatta un valore numerico dell'API tollerando i null: Open-Meteo li
    restituisce per le giornate ai limiti dell'orizzonte del modello, e un
    None dentro una f-string faceva fallire l'intero report.
    """
    if value is None:
        return f"N/D{suffix}"
    try:
        return f"{float(value):{spec}}{suffix}"
    except (TypeError, ValueError):
        return f"N/D{suffix}"


def _day_block(daily: dict, i: int, day_name: str, prefix: str = "") -> str:
    """Riga di previsione per un singolo giorno."""
    desc = WMO_CODES.get(_value(daily, "weathercode", i), "❓ N/D")
    t_min = _num(_value(daily, "temperature_2m_min", i), ".0f", "°C")
    t_max = _num(_value(daily, "temperature_2m_max", i), ".0f", "°C")
    rain = _num(_value(daily, "precipitation_sum", i), ".1f", "mm")
    wind = _num(_value(daily, "windspeed_10m_max", i), ".0f", " km/h")
    return (
        f"{prefix}<b>{day_name}</b>\n"
        f"{desc}\n"
        f"🌡️ {t_min} – {t_max}  "
        f"🌧️ {rain}  "
        f"💨 {wind}\n"
    )


def _daily_dates(daily: dict) -> dict[date, int]:
    """Mappa data -> indice nella risposta dell'API."""
    index = {}
    for i, d in enumerate(daily.get("time") or []):
        try:
            index[datetime.strptime(d, "%Y-%m-%d").date()] = i
        except (TypeError, ValueError):
            logger.warning(f"Data non riconosciuta nella risposta Open-Meteo: {d!r}")
    return index


def format_weather_command(data: dict) -> str:
    """Messaggio per /meteo: previsioni generiche 7 giorni."""
    daily = data["daily"]
    giorni = days_until_festival()

    if giorni > 0:
        countdown = f"⏳ Mancano <b>{giorni} giorni</b> al festival!\n"
    elif giorni == 0:
        countdown = "🔥 <b>Il festival inizia oggi!</b>\n"
    else:
        countdown = ""

    lines = [
        f"🤘 <b>Meteo Jaroměř</b>\n📍 {CITY_NAME}\n{countdown}"
    ]

    for giorno, i in sorted(_daily_dates(daily).items()):
        # Evidenzia i giorni del festival
        prefix = "🎸 " if FESTIVAL_START <= giorno <= FESTIVAL_END else ""
        lines.append(_day_block(daily, i, format_day(giorno), prefix))

    lines.append("🔗 <a href='https://open-meteo.com'>Dati: Open-Meteo</a>")
    return "\n".join(lines)


def format_weather_festival(data: dict) -> str:
    """Messaggio per il report automatico: solo giorni del festival."""
    daily = data["daily"]
    date_index = _daily_dates(daily)

    giorni = days_until_festival()
    if giorni > 0:
        header = (
            f"🤘 <b>Meteo Brutal Assault {FESTIVAL_START.year}</b>\n"
            f"📍 {CITY_NAME} — "
            f"{FESTIVAL_START.strftime('%d').lstrip('0')}-"
            f"{FESTIVAL_END.strftime('%d').lstrip('0')} {MESI_IT[FESTIVAL_END.month - 1]}\n"
            f"⏳ Mancano <b>{giorni} giorni</b>!\n"
        )
    else:
        header = (
            f"🤘 <b>Meteo Brutal Assault {FESTIVAL_START.year}</b>\n"
            f"📍 {CITY_NAME}\n"
            f"🔥 <b>È in corso!</b>\n"
        )

    lines = [header]

    giorni_festival = festival_dates()
    coperti = [d for d in giorni_festival if d in date_index]
    for fest_date in coperti:
        lines.append(_day_block(daily, date_index[fest_date], format_day(fest_date)))

    mancanti = len(giorni_festival) - len(coperti)
    if not coperti:
        # Non deve succedere dentro la finestra, ma un report con la sola
        # intestazione sarebbe incomprensibile: meglio dirlo esplicitamente.
        logger.warning(
            "Nessun giorno del festival nella risposta Open-Meteo "
            f"(giorni ricevuti: {sorted(date_index)})."
        )
        lines.append("⏳ Previsioni per i giorni del festival non ancora disponibili.\n")
    elif mancanti:
        lines.append(
            f"⏳ Le ultime {mancanti} giornate sono ancora oltre l'orizzonte "
            f"delle previsioni: arriveranno nei prossimi giorni.\n"
        )

    lines.append("🔗 <a href='https://open-meteo.com'>Dati: Open-Meteo</a>")
    return "\n".join(lines)


if __name__ == "__main__":
    import asyncio

    async def test():
        print("=== TEST /meteo (7 giorni generici) ===\n")
        data = await fetch_weather_command()
        print(format_weather_command(data))

    asyncio.run(test())
