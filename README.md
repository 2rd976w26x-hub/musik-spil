# Musik Spil (v1.4.28)

Denne ZIP er **baseret direkte på `musik-spil-v1.4.7-github-ready.zip`** (den stabile version).

## Hvad er nyt i v1.4.28

- En spiller kan kun tilslutte sig et rum én gang pr. device (samme device kan ikke "dobbelt-joine" i flere faner).
- Lobby-UI rydder korrekt efter man forlader et rum ("Spillere / Gættetid / Runder / Kategori" bliver ikke hængende).
- Ny statistik-side: /admin (og rå JSON på /stats). Statistik er in-memory og nulstilles ved deploy.

- Vælger nu sange *random* inden for den valgte kategori.
- Musik-kategorier bygges udelukkende ud fra antal JSON-filer i mappen (fx `songs.json`, `songs_80s.json` osv.).
- Gør det nemmere at indtaste årstal (iPhone: numerisk tastatur + +/- knapper + hurtigvalg)
- **Løbende stilling** vises altid i topbaren, så man kan se point under hele spillet (lobby, runde og resultat).
- **DJ kan springe en sang over** ("Spring sang over") og få en ny tilfældig sang i samme kategori (ingen point).
- Versionsnummer er opdateret konsekvent (server viser korrekt version i UI).

## Kør lokalt
```bash
python server.py
```
Åbn derefter browseren på den adresse, som serveren skriver i konsollen.
