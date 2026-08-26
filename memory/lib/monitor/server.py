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
    from lib.store import MemoryStore
    from lib import config as _cfg
    from lib.store.project_memory import ProjectMemoryManager

    app = FastAPI(title="Crisp Monitor", docs_url=None, redoc_url=None)

    def _all_stores() -> list[tuple[str, MemoryStore]]:
        """Return (proj_dir_name, store) for every project store that has episodes."""
        base = Path.home() / ".claude" / "memory" / "projects"
        stores = []
        if not base.exists():
            return stores
        for proj_dir in sorted(base.iterdir()):
            if not proj_dir.is_dir():
                continue
            store = MemoryStore(str(proj_dir))
            eps = store.list_episodes()
            if eps:
                # infer project name from source_path of first episode with one
                name = proj_dir.name
                for ep in eps:
                    if ep.source_path and "/dev/" in ep.source_path:
                        parts = ep.source_path.split("/")
                        # take the part after /dev/ or last meaningful segment
                        try:
                            idx = parts.index("dev")
                            name = "/".join(parts[idx+1:idx+3])
                        except ValueError:
                            name = parts[-2] if len(parts) > 1 else proj_dir.name
                        break
                stores.append((name, proj_dir.name, store))
        return stores

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

    @app.get("/api/projects")
    async def projects():
        result = []
        for name, proj_id, store in _all_stores():
            eps = store.list_episodes()
            from collections import Counter
            layers = Counter(ep.layer for ep in eps)
            cats = Counter(ep.category for ep in eps)
            result.append({
                "name": name, "proj_id": proj_id,
                "total": len(eps),
                "layers": {f"l{k}": v for k, v in sorted(layers.items())},
                "categories": dict(cats.most_common(8)),
            })
        return JSONResponse(result)

    @app.get("/api/episodes/{proj_id}")
    async def episodes(
        proj_id: str,
        layer: int = Query(None),
        category: str = Query(None),
        limit: int = Query(200, ge=1, le=2000),
    ):
        base = Path.home() / ".claude" / "memory" / "projects" / proj_id
        if not base.exists():
            return JSONResponse({"error": "project not found"}, status_code=404)
        store = MemoryStore(str(base))
        eps = store.list_episodes()
        if layer is not None:
            eps = [e for e in eps if e.layer == layer]
        if category:
            eps = [e for e in eps if e.category == category]
        eps = eps[-limit:]
        return JSONResponse([{
            "id": ep.id,
            "layer": ep.layer,
            "category": ep.category,
            "importance": round(ep.importance, 3),
            "embedded": bool(ep.embedding),
            "session_id": ep.session_id,
            "source_path": ep.source_path or "",
            "created_at": ep.created_at or "",
            "tags": ep.tags or [],
            "content_preview": (ep.content or "")[:300],
        } for ep in eps])

    @app.get("/api/episode/{proj_id}/{ep_id}")
    async def episode_detail(proj_id: str, ep_id: str):
        base = Path.home() / ".claude" / "memory" / "projects" / proj_id
        if not base.exists():
            return JSONResponse({"error": "project not found"}, status_code=404)
        store = MemoryStore(str(base))
        ep = store.get_episode(ep_id)
        if not ep:
            return JSONResponse({"error": "episode not found"}, status_code=404)
        return JSONResponse({
            "id": ep.id, "layer": ep.layer, "category": ep.category,
            "importance": round(ep.importance, 3), "embedded": bool(ep.embedding),
            "session_id": ep.session_id, "source_path": ep.source_path or "",
            "created_at": ep.created_at or "", "tags": ep.tags or [],
            "content": ep.content or "",
            "context_snapshot": ep.context_snapshot or {},
        })

    @app.get("/health")
    async def health():
        return JSONResponse({"ok": True})

    print(f"  Crisp Monitor  ->  http://{host}:{port}")
    print(f"  Press Ctrl+C to stop.\n")
    uvicorn.run(app, host=host, port=port, log_level="warning")
