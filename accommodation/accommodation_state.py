"""
Stato della disponibilità di hotel e campeggi.

File separato da quello dei biglietti, ma stessa logica: le funzioni sono quelle
di tickets/availability_state.py, che ricevono il percorso su cui lavorare.
"""

from storage import data_file
from tickets.availability_state import load_availability_state, save_availability_state

ACCOMMODATION_STATE_FILE = data_file("accommodation_availability.json")


def load_accommodation_state() -> dict:
    """Stato degli alloggi: soglie già annunciate e sold out già comunicati."""
    return load_availability_state(ACCOMMODATION_STATE_FILE)


def save_accommodation_state(state: dict) -> bool:
    """Salva lo stato degli alloggi. True se il salvataggio è riuscito."""
    return save_availability_state(state, ACCOMMODATION_STATE_FILE)
