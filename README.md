# Musik Spil (v1.4.19)

Denne ZIP er **baseret direkte på `musik-spil-v1.4.7-github-ready.zip`** (den stabile version).

## Hvad er nyt i v1.4.19

- Robust /api JSON parsing (fixer 400-fejl hvor version vises som "?" og "Opret rum" ikke reagerer, hvis Content-Type header mangler/ændres).

- Mere stilren Spotify/DJ-visning (kort-layout + optional preview embed).

## Hvad er nyt i v1.4.16

- Vælger nu sange *random* inden for den valgte kategori.
  - Hvis der kun findes én sangliste (Standard), auto-genererer spillet årti-kategorier (fx 1990, 2000) ud fra sangenes årstal.
- Fix: Knapperne til hele årtier (fx 1990, 2000 osv.) virker igen.
- Gør det nemmere at indtaste årstal (iPhone: numerisk tastatur + +/- knapper + hurtigvalg)
- **Løbende stilling** vises altid i topbaren, så man kan se point under hele spillet (lobby, runde og resultat).
- **DJ kan springe en sang over** ("Spring sang over") og få en ny tilfældig sang i samme kategori (ingen point).
- Versionsnummer er opdateret konsekvent (server viser korrekt version i UI).

## Kør lokalt
```bash
python server.py
```
Åbn derefter browseren på den adresse, som serveren skriver i konsollen.
