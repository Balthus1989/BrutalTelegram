"""
Versione del bot — unica fonte di verità.

Il numero segue il versionamento semantico MAJOR.MINOR.PATCH e viene riscritto
da `release.py` a ogni rilascio: non modificarlo a mano, altrimenti il numero
mostrato dal bot, la voce nel CHANGELOG e il tag git smettono di coincidere.

Il container non contiene il repository git (`.git/` è in `.dockerignore`),
quindi in produzione la versione in esecuzione è leggibile solo da qui: è
questo file — non il tag — a dire quale codice sta girando su Fly.io.
"""

import re
from pathlib import Path

__version__ = "1.0.0"
__release_date__ = "2026-08-28"

CHANGELOG_FILE = Path(__file__).parent / "CHANGELOG.md"

# "## [1.2.3] - 2026-08-28": il separatore può essere trattino o em dash, e la
# sezione delle modifiche non ancora rilasciate si chiama "[Non rilasciato]".
SECTION_RE = re.compile(r"^##\s+\[(?P<version>[^\]]+)\]")


def parse_release_notes(changelog_text: str, version: str) -> list[str]:
    """
    Voci elencate nel CHANGELOG sotto una specifica versione.

    Le voci scritte su più righe vengono ricomposte in una riga sola: nel
    CHANGELOG sono spezzate per leggibilità, ma in chat andrebbero a capo a metà
    frase.
    """
    voci = []
    dentro = False
    for riga in changelog_text.splitlines():
        intestazione = SECTION_RE.match(riga)
        if intestazione:
            if dentro:
                break  # sezione successiva: le voci di questa versione sono finite
            dentro = intestazione.group("version") == version
            continue
        if not dentro:
            continue
        pulita = riga.strip()
        if pulita.startswith(("- ", "* ")):
            voci.append(pulita[2:].strip())
        elif voci and pulita and riga[:1].isspace():
            # riga indentata sotto un elenco: è la continuazione della voce sopra
            voci[-1] += " " + pulita
    return voci


def release_notes(version: str = None, changelog: Path = None) -> list[str]:
    """
    Novità della versione indicata (di default quella in esecuzione).

    Restituisce una lista vuota se il CHANGELOG manca o non ha ancora una voce
    per quella versione: `/version` deve rispondere comunque, il changelog è un
    di più e non una dipendenza del bot.
    """
    percorso = changelog or CHANGELOG_FILE
    try:
        testo = percorso.read_text(encoding="utf-8")
    except OSError:
        return []
    return parse_release_notes(testo, version or __version__)
