"""Crisp Monitor web server.

Serves dashboard.html and a polling /api/events endpoint that tails the
event bus SQLite store. No WebSocket needed — the client polls every 500ms.

Usage:
    crisp monitor [--port 7654] [--host 127.0.0.1]
"""

from __future__ import annotations

import json
import os
from pathlib import Path

_DASHBOARD = Path(__file__).parent / "dashboard.html"


def run(host: str = "127.0.0.1", port: int = 7654) -> None:
    try:
        from fastapi import FastAPI, Query
        from fastapi.responses import HTMLResponse, JSONResponse, Response
        import uvicorn
    except ImportError:
        raise SystemExit(
            "crisp monitor requires: pip install 'crisp[monitor]'\n"
            "  (fastapi and uvicorn are needed)"
        )

    from lib.bus import tail, latest_id

    app = FastAPI(title="Crisp Monitor", docs_url=None, redoc_url=None)

    @app.get("/", response_class=HTMLResponse)
    async def dashboard():
        return HTMLResponse(_DASHBOARD.read_text())

    @app.get("/api/events")
    async def events(
        since: int = Query(0, ge=0),
        limit: int = Query(100, ge=1, le=500),
        session: str = Query(None),
        event: str = Query(None),
    ):
        rows = tail(since_id=since, limit=limit)
        if session:
            rows = [r for r in rows if r.get("session") == session]
        if event:
            rows = [r for r in rows if event in r.get("event", "")]
        return JSONResponse(rows)

    @app.get("/api/latest-id")
    async def get_latest():
        return JSONResponse({"id": latest_id()})

    @app.get("/health")
    async def health():
        return JSONResponse({"ok": True})

    print(f"  Crisp Monitor  ->  http://{host}:{port}")
    print(f"  Press Ctrl+C to stop.\n")
    uvicorn.run(app, host=host, port=port, log_level="warning")
