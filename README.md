# Musik Spil (v1.4.30)

Denne ZIP er **baseret direkte på `musik-spil-v1.4.7-github-ready.zip`** (den stabile version).

## Hvad er nyt i v1.4.30

- **Valgfri Postgres-persistens** for statistik (spillet kører stadig fint uden database).
- **Udbygget admin-dashboard**: grafer for besøg/rum/spil pr. dag + liste over seneste spil.
- **Spilhistorik**: klik ind på et spil i admin og se historik (runder, point, osv.) – hvis DB er slået til.

### Sådan slår du database til

Sæt en environment variable på Render:

- `DATABASE_URL` (fx Render Postgres “Internal Database URL”)

Hvis du vil køre *uden* database, så lad `DATABASE_URL` være tom eller sæt:

- `DISABLE_DB=1`

- En spiller kan kun tilslutte sig et rum én gang pr. device (samme device kan ikke "dobbelt-joine" i flere faner).
- Lobby-UI rydder korrekt efter man forlader et rum ("Spillere / Gættetid / Runder / Kategori" bliver ikke hængende).
- Admin: /admin (og rå JSON på /stats). Hvis `DATABASE_URL` er sat, gemmes statistik + afsluttede spil i Postgres; ellers bruges in-memory.

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

## Postgres (valgfrit)

Spillet virker uden database. Hvis du vil have permanent statistik og historik:

1. Opret en Render Postgres.
2. Sæt environment variable `DATABASE_URL` på webservicen til din Postgres connection-string.
3. Deploy igen (tabeller bliver oprettet automatisk ved første start).

Vil du tvinge in-memory (uanset DB), sæt `DISABLE_DB=1`.
