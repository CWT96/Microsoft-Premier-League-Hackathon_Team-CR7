# frontend/fan-app

Phone fan app for the demo: landing → pick Northbridge or Calder → live PASS / SHOOT / DRIBBLE calls with live points.

- Plain HTML, CSS and vanilla JS. No build step, no framework.
- Served by the backend at `/` (static mount at the end of `backend/app/main.py`), so the page and `/demo/ws` share one origin.
- Talks only to WebSocket `/demo/ws` (protocol in `backend/demo/router.py` and `backend/demo/session.py`). The server drives all timing; the page only reacts to messages.
- Design source: `origin/frontend/demo-ux:frontend/demo-ux/screens/` (Main, Pick, Live, HUD). Crests in `assets/` are copied from there.

Run locally:

```bash
.venv/bin/uvicorn backend.app.main:app --port 8000   # then open http://localhost:8000
```
