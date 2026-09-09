"""
Scraper della disponibilità di hotel e campeggi in vendita sul sito ufficiale.

Gli alloggi sono una sezione dello stesso shop dei biglietti: la pagina elenco è
un'altra, ma le schede stanno sotto /en/tickets/detail/id/ e usano lo stesso
template, con la stessa barra "Available" e la stessa scritta "Sold out" al
posto della barra quando un alloggio è esaurito.

Per questo il parsing non viene riscritto: si riusa il motore in
tickets/availability_scraper.py, che qui riceve solo un'altra pagina e un altro
filtro. Le tre copie divergenti di is_sold_out() sono già costate un sold out
mai annunciato: questo modulo esiste per configurare quel motore, non per
duplicarlo.
"""

import logging
import os
from typing import Optional

from tickets.availability_scraper import fetch_shop_availability

logger = logging.getLogger(__name__)

ACCOMMODATION_URL = "https://brutalassault.cz/en/accommodation"

# A differenza dei biglietti, qui NON si filtra per anno.
#
# Sui biglietti il filtro serve a escludere i gift voucher, la cui percentuale è
# impostata a mano; nella pagina alloggi non ci sono voucher, e tutto quello che
# il sito elenca è materiale dell'edizione in vendita.
#
# Soprattutto: i nomi degli alloggi non sono affidabili come quelli dei
# biglietti. Al momento della scrittura due prodotti in pagina si chiamano
# "BA 2026" invece di "BA 2027", e uno dei due è una piazzola davvero in vendita
# al 98% — con un filtro "2027" non sarebbe mai stata monitorata, senza che
# niente nei log lo facesse notare.
#
# Un alloggio rimasto da un'edizione passata è comunque esaurito, e un prodotto
# che compare già esaurito non viene né annunciato né tracciato: resta fuori dal
# gruppo da solo, senza bisogno di filtrarlo per nome.
#
# La variabile resta disponibile se una prossima edizione mescolasse in pagina
# alloggi di due anni entrambi acquistabili.
PRODUCT_MATCH = os.getenv("ACCOMMODATION_PRODUCT_MATCH", "")

# Più alto del tetto dei biglietti: la pagina alloggi elenca da sola quasi
# trenta prodotti tra hotel, ready-to-camp e piazzole dei vari campi, e con il
# tetto dei biglietti (25) gli ultimi resterebbero fuori dal monitoraggio.
MAX_PRODUCT_FETCHES = 40


async def fetch_accommodation_availability() -> Optional[list[dict]]:
    """
    Recupera la disponibilità di hotel e campeggi.

    Returns:
        Lista di dict con chiavi: id, name, url, percent, sold_out.
        Lista vuota se non risulta in vendita nessun alloggio.
        None in caso di errore di rete o di pagina non riconoscibile.
    """
    return await fetch_shop_availability(
        ACCOMMODATION_URL,
        match=PRODUCT_MATCH,
        label="alloggi",
        max_fetches=MAX_PRODUCT_FETCHES,
    )
