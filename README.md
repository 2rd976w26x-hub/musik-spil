v1.4.1

Musik Spil v1.3.3 (nice iOS UI, behold Spotify-link behavior)

Musik Spil v1.3.0 Clean (historik toggle fold/ud)


## Deploy (GitHub + Render) – HTTPS + PWA
1. Upload this repo to GitHub (root must contain server.py, requirements.txt and web/).
2. On Render: New → Web Service → connect repo.
3. Build Command:
   pip install -r requirements.txt
4. Start Command:
   gunicorn server:app
5. Render gives you an https:// URL. Open it in Safari/Chrome and "Add to Home Screen" to install as PWA.

Note: Rooms are stored in-memory. If the server restarts, rooms disappear (fine for party use).
## Musik-kategorier
Læg flere sanglister i `web/` med navne som:
- `songs_Danske 1960 til 2025.json`
- `songs_90er hits.json`

Serveren loader automatisk alle `web/songs_*.json` og viser dem som kategorier i lobbyen (host kan vælge før spilstart).
