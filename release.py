"""
Rilascio: avanza la versione, aggiorna il CHANGELOG, crea commit e tag.

Uso:
    python release.py patch            # 1.0.0 -> 1.0.1
    python release.py minor            # 1.0.1 -> 1.1.0
    python release.py major            # 1.1.0 -> 2.0.0
    python release.py --set 2.3.0      # numero deciso a mano
    python release.py minor --dry-run  # mostra cosa farebbe, senza toccare nulla

Cosa fa, in ordine:
    1. controlla che l'albero di lavoro sia pulito (un rilascio deve
       corrispondere a un commit preciso, non a modifiche non salvate);
    2. calcola il numero nuovo e lo scrive in version.py;
    3. sposta le voci di "[Non rilasciato]" del CHANGELOG in una sezione
       "[X.Y.Z] - data", lasciando "[Non rilasciato]" vuota per il lavoro dopo;
    4. crea il commit "Rilascio vX.Y.Z" e il tag annotato "vX.Y.Z".

Push e deploy restano manuali: lo script stampa i comandi da eseguire. Il
numero avanza qui, ma nessuna versione esce di casa senza che qualcuno la mandi.
"""

import argparse
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).parent
VERSION_FILE = ROOT / "version.py"
CHANGELOG_FILE = ROOT / "CHANGELOG.md"

UNRELEASED = "Non rilasciato"
VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


# ------------------------------------------------------------ file I/O

def read_file(path: Path) -> tuple:
    """
    Contenuto del file (righe normalizzate a "\\n") e terminatore in uso.

    Il repository ha file sia CRLF sia LF: riscrivendoli sempre con il
    terminatore di sistema il commit di rilascio diventerebbe un diff di
    centinaia di righe invece delle due che cambiano davvero.
    """
    grezzo = path.read_bytes().decode("utf-8")
    fine_riga = "\r\n" if "\r\n" in grezzo else "\n"
    return grezzo.replace("\r\n", "\n"), fine_riga


def write_file(path: Path, testo: str, fine_riga: str) -> None:
    path.write_bytes(testo.replace("\n", fine_riga).encode("utf-8"))


# --------------------------------------------------------------- numero

def parse_version(versione: str) -> tuple:
    match = VERSION_RE.match(versione.strip())
    if not match:
        raise ValueError(f"Versione non valida: '{versione}' (atteso MAJOR.MINOR.PATCH)")
    return tuple(int(p) for p in match.groups())


def bump(versione: str, parte: str) -> str:
    """Nuovo numero secondo il versionamento semantico."""
    major, minor, patch = parse_version(versione)
    if parte == "major":
        return f"{major + 1}.0.0"
    if parte == "minor":
        return f"{major}.{minor + 1}.0"
    if parte == "patch":
        return f"{major}.{minor}.{patch + 1}"
    raise ValueError(f"Parte sconosciuta: '{parte}' (major, minor o patch)")


def current_version(testo: str) -> str:
    match = re.search(r'^__version__\s*=\s*"([^"]+)"', testo, re.MULTILINE)
    if not match:
        raise ValueError("__version__ non trovato in version.py")
    return match.group(1)


def rewrite_version_file(testo: str, versione: str, giorno: str) -> str:
    """version.py con numero e data del nuovo rilascio."""
    testo, trovato = re.subn(
        r'^__version__\s*=\s*"[^"]*"',
        f'__version__ = "{versione}"',
        testo,
        count=1,
        flags=re.MULTILINE,
    )
    if not trovato:
        raise ValueError("__version__ non trovato in version.py")

    testo, trovato = re.subn(
        r'^__release_date__\s*=\s*"[^"]*"',
        f'__release_date__ = "{giorno}"',
        testo,
        count=1,
        flags=re.MULTILINE,
    )
    if not trovato:
        raise ValueError("__release_date__ non trovato in version.py")
    return testo


# ------------------------------------------------------------ changelog

def roll_changelog(testo: str, versione: str, giorno: str) -> tuple:
    """
    Sposta le voci di "[Non rilasciato]" sotto la nuova versione.

    Restituisce (nuovo_testo, voci_spostate): le voci finiscono anche nel
    messaggio del tag, così `git show vX.Y.Z` dice cosa conteneva il rilascio
    senza dover aprire il CHANGELOG.
    """
    righe = testo.splitlines()
    inizio = next(
        (i for i, r in enumerate(righe)
         if re.match(rf"^##\s+\[{re.escape(UNRELEASED)}\]", r)),
        None,
    )
    if inizio is None:
        raise ValueError(
            f"Sezione '## [{UNRELEASED}]' assente dal CHANGELOG: aggiungila prima di rilasciare."
        )

    # La sezione finisce dove comincia la versione precedente (o il file)
    fine = next(
        (i for i in range(inizio + 1, len(righe)) if re.match(r"^##\s+\[", righe[i])),
        len(righe),
    )
    corpo = list(righe[inizio + 1:fine])
    while corpo and not corpo[0].strip():
        corpo.pop(0)
    while corpo and not corpo[-1].strip():
        corpo.pop()

    voci = [r.strip()[2:].strip() for r in corpo if r.strip().startswith(("- ", "* "))]

    aggiornate = (
        righe[:inizio]
        + [f"## [{UNRELEASED}]", ""]
        + [f"## [{versione}] - {giorno}", ""]
        + corpo
        + [""]
        + righe[fine:]
    )
    return "\n".join(aggiornate).rstrip() + "\n", voci


# ------------------------------------------------------------------ git

def git(*args, check=True) -> str:
    risultato = subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, text=True, encoding="utf-8"
    )
    if check and risultato.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {risultato.stderr.strip()}")
    return (risultato.stdout or "").strip()


def working_tree_dirty() -> str:
    return git("status", "--porcelain")


def tag_exists(tag: str) -> bool:
    return bool(git("tag", "--list", tag))


# ------------------------------------------------------------------ CLI

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Avanza la versione e prepara il rilascio.")
    parser.add_argument(
        "parte",
        nargs="?",
        choices=["major", "minor", "patch"],
        help="quale parte del numero avanzare",
    )
    parser.add_argument("--set", dest="esplicita",
                        help="imposta un numero preciso (MAJOR.MINOR.PATCH)")
    parser.add_argument("--dry-run", action="store_true",
                        help="mostra le modifiche senza scrivere né committare")
    parser.add_argument("--allow-empty", action="store_true",
                        help=f"rilascia anche senza voci in '[{UNRELEASED}]'")
    parser.add_argument("--no-commit", action="store_true",
                        help="scrive i file ma non crea commit e tag")
    args = parser.parse_args(argv)

    if bool(args.parte) == bool(args.esplicita):
        parser.error("indica la parte da avanzare (major/minor/patch) oppure --set X.Y.Z")

    testo_version, fine_version = read_file(VERSION_FILE)
    attuale = current_version(testo_version)
    try:
        nuova = args.esplicita.strip() if args.esplicita else bump(attuale, args.parte)
        if parse_version(nuova) <= parse_version(attuale):
            print(f"❌ La versione {nuova} non è successiva a quella attuale {attuale}.")
            return 1
    except ValueError as e:
        print(f"❌ {e}")
        return 1

    tag = f"v{nuova}"
    if tag_exists(tag):
        print(f"❌ Il tag {tag} esiste già.")
        return 1

    sporco = working_tree_dirty()
    if sporco and not args.dry_run:
        print("❌ Modifiche non committate: un rilascio deve corrispondere a un commit preciso.")
        print(sporco)
        return 1

    giorno = date.today().isoformat()
    try:
        testo_changelog, fine_changelog = read_file(CHANGELOG_FILE)
        nuovo_changelog, voci = roll_changelog(testo_changelog, nuova, giorno)
    except (OSError, ValueError) as e:
        print(f"❌ {e}")
        return 1

    if not voci and not args.allow_empty:
        print(
            f"❌ Nessuna voce sotto '## [{UNRELEASED}]' nel CHANGELOG: annota cosa "
            f"cambia in {nuova}, oppure usa --allow-empty."
        )
        return 1

    print(f"Versione: {attuale} → {nuova}   ({giorno})")
    for voce in voci:
        print(f"  • {voce}")
    if not voci:
        print("  (nessuna voce nel changelog)")

    if args.dry_run:
        print("\n--dry-run: nessun file modificato.")
        return 0

    write_file(VERSION_FILE, rewrite_version_file(testo_version, nuova, giorno), fine_version)
    write_file(CHANGELOG_FILE, nuovo_changelog, fine_changelog)
    print(f"\n✅ Aggiornati {VERSION_FILE.name} e {CHANGELOG_FILE.name}.")

    if args.no_commit:
        print("--no-commit: commit e tag non creati.")
        return 0

    git("add", VERSION_FILE.name, CHANGELOG_FILE.name)
    git("commit", "-m", f"Rilascio v{nuova}")
    messaggio = (
        "\n".join([f"Versione {nuova}", ""] + [f"- {v}" for v in voci])
        if voci else f"Versione {nuova}"
    )
    git("tag", "-a", tag, "-m", messaggio)
    print(f"✅ Commit e tag {tag} creati.")
    print("\nPer pubblicare il rilascio:")
    print("  git push --follow-tags")
    print("  fly deploy")
    return 0


if __name__ == "__main__":
    sys.exit(main())
