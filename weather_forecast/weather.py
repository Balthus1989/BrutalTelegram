import logging
import os
import httpx
from datetime import datetime, date, timedelta

logger = logging.getLogger(__name__)

LATITUDE = 50.3567
LONGITUDE = 15.9183
CITY_NAME = "Jaroměř, CZ"

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
    return (FESTIVAL_START - date.today()).days

def festival_window_open() -> bool:
    """
    True se oggi il report meteo automatico va pubblicato: nei 15 giorni che
    precedono il festival e per tutta la sua durata.
    """
    days_to_end = (FESTIVAL_END - date.today()).days
    return days_to_end >= 0 and days_until_festival() <= 15


def festival_dates() -> list[date]:
    delta = (FESTIVAL_END - FESTIVAL_START).days
    return [FESTIVAL_START + timedelta(days=i) for i in range(delta + 1)]

async def _fetch(forecast_days: int) -> dict:
    """Chiamata base all'API Open-Meteo."""
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": LATITUDE,
        "longitude": LONGITUDE,
        "daily": [
            "weathercode", "temperature_2m_max", "temperature_2m_min",
            "precipitation_sum", "windspeed_10m_max"
        ],
        "timezone": "Europe/Prague",
        "forecast_days": forecast_days,
    }
    async with httpx.AsyncClient() as client:
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
    today = date.today()
    giorni = days_until_festival()
    days_to_end = (FESTIVAL_END - today).days

    if days_to_end < 0:
        logger.info(f"Festival terminato il {FESTIVAL_END}: nessun report meteo.")
        return None  # Festival passato
    if giorni > 15:
        logger.info(f"Mancano {giorni} giorni al festival: report meteo non ancora attivo.")
        return None  # Troppo presto

    # Open-Meteo accetta forecast_days tra 1 e 16
    forecast_days = max(1, min(days_to_end + 1, 16))
    return await _fetch(forecast_days=forecast_days)

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

    for i, date_str in enumerate(daily["time"]):
        fest_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        day_name = format_day(fest_date)

        # Evidenzia i giorni del festival
        is_festival = FESTIVAL_START <= fest_date <= FESTIVAL_END
        prefix = "🎸 " if is_festival else ""

        code = daily["weathercode"][i]
        desc = WMO_CODES.get(code, "❓ N/D")
        t_max = daily["temperature_2m_max"][i]
        t_min = daily["temperature_2m_min"][i]
        rain = daily["precipitation_sum"][i]
        wind = daily["windspeed_10m_max"][i]

        lines.append(
            f"{prefix}<b>{day_name}</b>\n"
            f"{desc}\n"
            f"🌡️ {t_min:.0f}°C – {t_max:.0f}°C  "
            f"🌧️ {rain:.1f}mm  "
            f"💨 {wind:.0f} km/h\n"
        )

    lines.append("🔗 <a href='https://open-meteo.com'>Dati: Open-Meteo</a>")
    return "\n".join(lines)

def format_weather_festival(data: dict) -> str:
    """Messaggio per il report automatico: solo giorni del festival."""
    daily = data["daily"]
    date_index = {
        datetime.strptime(d, "%Y-%m-%d").date(): i
        for i, d in enumerate(daily["time"])
    }

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

    for fest_date in festival_dates():
        if fest_date not in date_index:
            continue
        i = date_index[fest_date]
        day_name = format_day(fest_date)
        code = daily["weathercode"][i]
        desc = WMO_CODES.get(code, "❓ N/D")
        t_max = daily["temperature_2m_max"][i]
        t_min = daily["temperature_2m_min"][i]
        rain = daily["precipitation_sum"][i]
        wind = daily["windspeed_10m_max"][i]

        lines.append(
            f"<b>{day_name}</b>\n"
            f"{desc}\n"
            f"🌡️ {t_min:.0f}°C – {t_max:.0f}°C  "
            f"🌧️ {rain:.1f}mm  "
            f"💨 {wind:.0f} km/h\n"
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