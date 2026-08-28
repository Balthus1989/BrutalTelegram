"""
Traduzione EN -> IT dei testi presi dal sito ufficiale.

Google non espone un endpoint gratuito stabile: translate.google.com/m (quello
usato da deep-translator) risponde 200 con dentro una pagina "Error 500" appena
un IP fa qualche richiesta di fila, e translate_a/single risponde 429. Con il
vecchio `except: return testo` le news finivano nel gruppo in inglese e nei log
restava solo una print: qui si prova un secondo servizio (MyMemory) e ogni
fallimento viene loggato.
"""

import logging

import requests
from deep_translator import GoogleTranslator

logger = logging.getLogger(__name__)

# Limite di caratteri accettato da GoogleTranslator per singola richiesta.
MAX_CHARS = 5000

MYMEMORY_URL = "https://api.mymemory.translated.net/get"
# L'API di MyMemory rifiuta le query oltre i 500 caratteri: si spezza prima.
MYMEMORY_MAX_CHARS = 480

TIMEOUT = 15

# Sotto questa lunghezza un testo che torna identico all'originale non dice
# nulla ("Brutal Assault", "Lost and Found" possono restare tali).
MIN_CHARS_CONFRONTO = 40


def _translate_google(testo: str) -> str | None:
    return GoogleTranslator(source="en", target="it").translate(testo)


def split_chunks(testo: str, limite: int = MYMEMORY_MAX_CHARS) -> list[str]:
    """
    Spezza il testo in blocchi sotto il limite, tagliando a fine frase (o, in
    mancanza, sull'ultimo spazio) per non troncare le parole a metà.
    """
    chunks: list[str] = []
    resto = testo.strip()
    while len(resto) > limite:
        finestra = resto[:limite]
        taglio = max(finestra.rfind(". "), finestra.rfind("! "), finestra.rfind("? "))
        if taglio == -1:
            taglio = finestra.rfind(" ")
        if taglio == -1:
            taglio = limite - 1
        chunks.append(resto[: taglio + 1].strip())
        resto = resto[taglio + 1 :].strip()
    if resto:
        chunks.append(resto)
    return chunks


def _translate_mymemory(testo: str) -> str:
    parti = []
    for chunk in split_chunks(testo):
        risposta = requests.get(
            MYMEMORY_URL,
            params={"q": chunk, "langpair": "en|it"},
            timeout=TIMEOUT,
        )
        risposta.raise_for_status()
        dati = risposta.json()

        if str(dati.get("responseStatus")) != "200" or dati.get("quotaFinished"):
            raise RuntimeError(
                dati.get("responseDetails") or f"responseStatus {dati.get('responseStatus')}"
            )

        tradotto = (dati.get("responseData") or {}).get("translatedText") or ""
        # A quota esaurita MyMemory risponde 200 mettendo un avviso al posto
        # della traduzione: pubblicato così finirebbe dritto nel gruppo.
        if not tradotto.strip() or "MYMEMORY WARNING" in tradotto.upper():
            raise RuntimeError(tradotto.strip() or "risposta senza traduzione")

        parti.append(tradotto.strip())

    return " ".join(parti)


def traduzione_valida(originale: str, tradotto: str | None) -> bool:
    """
    Scarta le risposte che non sono una traduzione: sotto throttling
    translate.google.com/m rimanda indietro la stringa di partenza invece di
    fallire, ed è così che le news arrivavano nel gruppo ancora in inglese.
    """
    if not tradotto or not tradotto.strip():
        return False
    if len(originale.strip()) < MIN_CHARS_CONFRONTO:
        return True
    return tradotto.strip() != originale.strip()


# I servizi vengono provati in quest'ordine: Google non ha quota giornaliera ma
# è spesso sotto throttling, MyMemory è stabile ma limitato a 5000 caratteri al
# giorno per IP (le news del sito stanno sotto il migliaio di caratteri l'una).
def _backends():
    return (
        ("Google", _translate_google),
        ("MyMemory", _translate_mymemory),
    )


def translate(testo: str) -> str:
    if not testo:
        return ""

    testo = testo[:MAX_CHARS]

    for nome, backend in _backends():
        try:
            tradotto = backend(testo)
        except Exception as e:
            logger.warning(f"Traduzione con {nome} fallita: {str(e)[:200]}")
            continue

        if not traduzione_valida(testo, tradotto):
            logger.warning(f"{nome} non ha tradotto il testo: provo il servizio successivo.")
            continue

        logger.info(f"Testo tradotto con {nome} ({len(testo)} caratteri).")
        return tradotto

    # fallback: testo originale, in inglese ma pubblicato
    logger.error(
        "Nessun servizio di traduzione disponibile: la news viene pubblicata in inglese."
    )
    return testo


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    testo = "Plunge into the depths where crushing weight meets chilling fragility. The latest Brutal Assault update expands the line-up with a total of 12 bands. From the nautical funerals of Ahab and the sludge anthems of the legendary Crowbar, to the post-metal filth of local icons LVMEN and the total sonic terror of Violent Magic Orchestra. Each of these acts defines a distinct shade of darkness. At the same time, we regret to announce that the American outfit Fallujah will not be performing at this year's Brutal. Taking their slot on the line-up are their British peers, Cryptic Shift."
    print(translate(testo))
