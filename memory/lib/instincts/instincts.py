"""Continuous-learning instinct engine.

An *instinct* is a reinforced behavioral memory: a MemoryEpisode with
``category="instinct"`` and a ``confidence`` in [0, 1]. Tool-use observations are
logged cheaply (append-only JSONL), then distilled into instincts when a pattern
recurs. High-confidence instincts can ``evolve`` into emitted skills/commands/
agents and ``promote`` from project to global scope.

Design:
- Observations are append-only JSONL (cheap, high-frequency, rotated on analyze).
  They are NOT episodes.
- Instincts ARE episodes (stored at layer L2 so they inherit the 30-day half-life,
  the A-MEM graph, and reflection — not L0's 1-day decay).
- Reinforcement: a recurring signature bumps confidence (+reinforce_step) and
  access_count and refreshes last_accessed; absence lets normal decay erode it.
- Nothing here computes embeddings — that path is user-triggered (see embeddings.py).
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..store import MemoryEpisode, MemoryStore

INSTINCT_LAYER = 2  # L2 topic-cluster layer: 30-day half-life, cross-session


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "instinct"


class InstinctEngine:
    """Observe tool-use, distill recurring patterns into confidence-scored instincts."""

    def __init__(self, store: MemoryStore, config: Optional[dict] = None):
        self.store = store
        defaults = {
            "enabled": True,
            "min_observations": 20,
            "min_pattern_count": 3,
            "base_confidence": 0.3,
            "reinforce_step": 0.1,
            "decay_step": 0.05,
            "evolve_threshold": 0.8,
            "promote_min_projects": 2,
        }
        self.cfg = {**defaults, **(config or store.config.get("instincts", {}))}
        self.obs_dir = store.base_path / "observations"
        self.evolved_dir = store.base_path / "evolved"
        self.obs_dir.mkdir(parents=True, exist_ok=True)

    # ----------------------------------------------------------------- scope --
    def project_key(self) -> str:
        """Stable key for the current project: git origin if present, else cwd."""
        try:
            out = subprocess.run(
                ["git", "remote", "get-url", "origin"],
                capture_output=True,
                text=True,
                timeout=2,
            )
            if out.returncode == 0 and out.stdout.strip():
                url = out.stdout.strip()
                url = re.sub(r"\.git$", "", url)
                url = re.sub(r"^.*[:/]([^/]+/[^/]+)$", r"\1", url)
                return "p_" + hashlib.sha256(url.encode()).hexdigest()[:10]
        except Exception:
            pass
        return "p_" + hashlib.sha256(str(Path.cwd()).encode()).hexdigest()[:10]

    def _obs_file(self, scope: str) -> Path:
        return self.obs_dir / f"{scope}.jsonl"

    # ------------------------------------------------------------- observe ----
    @staticmethod
    def signature(event: dict) -> str:
        """Stable signature describing a tool action (the unit we cluster on)."""
        tool = event.get("tool_name") or event.get("tool") or "unknown"
        ti = event.get("tool_input") or event.get("input") or {}
        if tool == "Bash":
            cmd = str(ti.get("command", "")).strip()
            verb = cmd.split()[0] if cmd else "bash"
            verb = Path(verb).name  # /usr/bin/git -> git
            return f"Bash:{verb}"
        if tool in ("Edit", "Write", "MultiEdit", "Read", "NotebookEdit"):
            path = ti.get("file_path") or ti.get("path") or ""
            ext = Path(path).suffix or "noext"
            return f"{tool}:{ext}"
        return f"{tool}"

    def observe(self, event: dict) -> dict:
        """Append a compact tool-use observation. Cheap; no episode, no LLM."""
        if not self.cfg.get("enabled", True):
            return {}
        scope = self.project_key()
        record = {
            "ts": _now(),
            "tool": event.get("tool_name") or event.get("tool") or "unknown",
            "signature": self.signature(event),
            "phase": event.get("phase") or event.get("hook_event_name") or "",
            "ok": event.get("ok", True),
            "session": event.get("session_id", ""),
            "cwd": str(Path.cwd()),
        }
        with self._obs_file(scope).open("a") as f:
            f.write(json.dumps(record) + "\n")
        return record

    def _read_observations(self, scope: str) -> List[dict]:
        path = self._obs_file(scope)
        if not path.exists():
            return []
        out = []
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return out

    def _rotate_observations(self, scope: str, consumed: List[dict]) -> None:
        """Archive consumed observations and clear the live buffer."""
        if consumed:
            with (self.obs_dir / f"{scope}.processed.jsonl").open("a") as f:
                for rec in consumed:
                    f.write(json.dumps(rec) + "\n")
        self._obs_file(scope).write_text("")

    # ------------------------------------------------------------- analyze ----
    def _instinct_id(self, scope: str, signature: str) -> str:
        sig_hash = hashlib.sha256(signature.encode()).hexdigest()[:8]
        return f"instinct_{scope}_{sig_hash}"

    @staticmethod
    def _describe(signature: str, count: int) -> str:
        tool, _, detail = signature.partition(":")
        if tool == "Bash":
            return f"Habitually runs `{detail}` (Bash) in this project."
        if tool in ("Edit", "Write", "MultiEdit"):
            return f"Frequently edits {detail} files."
        if tool == "Read":
            return f"Frequently reads {detail} files before acting."
        return f"Frequently uses the {tool} tool."

    def analyze(self, force: bool = False) -> Dict[str, Any]:
        """Distill the observation buffer into new/reinforced instincts.

        No-op unless the buffer has >= min_observations entries (or force=True).
        """
        scope = self.project_key()
        obs = self._read_observations(scope)
        result = {"scope": scope, "observations": len(obs), "created": 0, "reinforced": 0}
        if not obs or (len(obs) < self.cfg["min_observations"] and not force):
            return result

        counts = Counter(o["signature"] for o in obs)
        for signature, count in counts.items():
            if count < self.cfg["min_pattern_count"]:
                continue
            if self._upsert_instinct(scope, signature, count):
                result["created"] += 1
            else:
                result["reinforced"] += 1
        self._rotate_observations(scope, obs)
        return result

    def _upsert_instinct(self, scope: str, signature: str, count: int) -> bool:
        """Create a new instinct or reinforce an existing one. Returns True if created."""
        ep_id = self._instinct_id(scope, signature)
        existing = self.store.get_episode(ep_id)
        if existing:
            existing.confidence = min(1.0, existing.confidence + self.cfg["reinforce_step"])
            existing.access_count += 1
            existing.last_accessed = _now()
            existing.decay_score = 1.0
            ctx = existing.context_snapshot or {}
            ctx["observations"] = int(ctx.get("observations", 0)) + count
            projects = set(ctx.get("projects", []))
            projects.add(scope)
            ctx["projects"] = sorted(projects)
            existing.context_snapshot = ctx
            self.store.update_episode(existing)
            return False

        desc = self._describe(signature, count)
        ep = MemoryEpisode(
            id=ep_id,
            session_id="instinct",
            timestamp=_now(),
            layer=INSTINCT_LAYER,
            title=signature,
            content=desc,
            category="instinct",
            importance=0.6,
            confidence=self.cfg["base_confidence"],
            lesson=desc,
            trigger_type="reaction",
            tags=["instinct", scope, signature.split(":")[0]],
            context_snapshot={"signature": signature, "observations": count, "projects": [scope]},
            access_count=1,
            last_accessed=_now(),
        )
        self.store.save_episode(ep)
        return True

    # ----------------------------------------------------------- reinforce ----
    def adjust(self, instinct_id: str, delta: float) -> Optional[float]:
        ep = self.store.get_episode(instinct_id)
        if not ep or ep.category != "instinct":
            return None
        ep.confidence = max(0.0, min(1.0, ep.confidence + delta))
        ep.last_accessed = _now()
        if delta > 0:
            ep.access_count += 1
            ep.decay_score = 1.0
        self.store.update_episode(ep)
        return ep.confidence

    def reinforce(self, instinct_id: str) -> Optional[float]:
        return self.adjust(instinct_id, self.cfg["reinforce_step"])

    def weaken(self, instinct_id: str) -> Optional[float]:
        return self.adjust(instinct_id, -self.cfg["decay_step"])

    # ---------------------------------------------------------------- list ----
    def list_instincts(
        self, scope: Optional[str] = None, min_confidence: float = 0.0
    ) -> List[MemoryEpisode]:
        items = [
            e
            for e in self.store.list_episodes(category="instinct")
            if e.confidence >= min_confidence
            and (scope is None or scope in e.tags)
        ]
        items.sort(key=lambda e: e.confidence, reverse=True)
        return items

    # -------------------------------------------------------------- evolve ----
    def evolve(
        self,
        instinct_ids: Optional[List[str]] = None,
        kind: str = "skill",
        name: Optional[str] = None,
    ) -> Optional[Path]:
        """Emit a skill/command/agent artifact from high-confidence instincts."""
        if instinct_ids:
            chosen = [e for e in (self.store.get_episode(i) for i in instinct_ids) if e]
        else:
            chosen = self.list_instincts(min_confidence=self.cfg["evolve_threshold"])
        chosen = [e for e in chosen if e and e.category == "instinct"]
        if not chosen:
            return None

        name = name or _slug(chosen[0].title)
        if kind == "skill":
            out_dir = self.evolved_dir / "skills" / name
            out_dir.mkdir(parents=True, exist_ok=True)
            out = out_dir / "SKILL.md"
        else:
            out_dir = self.evolved_dir / f"{kind}s"
            out_dir.mkdir(parents=True, exist_ok=True)
            out = out_dir / f"{name}.md"

        bullets = "\n".join(
            f"- {e.lesson or e.content}  _(confidence {e.confidence:.2f})_" for e in chosen
        )
        desc = f"Learned behaviors distilled from {len(chosen)} reinforced instinct(s)."
        out.write_text(
            f"---\nname: {name}\ndescription: {desc}\norigin: evolved-instinct\n---\n\n"
            f"# {name}\n\n{desc}\n\n## Learned behaviors\n\n{bullets}\n"
        )
        for e in chosen:
            if "evolved" not in e.tags:
                e.tags.append("evolved")
                self.store.update_episode(e)
        return out

    # ------------------------------------------------------------- promote ----
    def promote(self, instinct_id: str) -> bool:
        """Promote an instinct's signature project->global if its behavioral
        signature appears in >= promote_min_projects distinct per-project stores.

        Instincts live in physically-separate per-project stores, so this scans
        every project store under ``projects_base`` (not just the current one),
        then writes/refreshes a permanent global instinct in the global store.
        """
        ep = self.store.get_episode(instinct_id)
        if not ep or ep.category != "instinct":
            return False
        signature = (ep.context_snapshot or {}).get("signature", ep.title)

        # Derive the projects/global roots from this store's own location, rather
        # than constructing a default manager — keeps promote testable with any
        # base and correct regardless of where the store lives.
        #   <global>/projects/<id>  -> projects_base=<global>/projects, global=<global>
        #   <global>                -> projects_base=<global>/projects, global=<global>
        store_base = Path(self.store.base_path)
        if store_base.parent.name == "projects":
            projects_base = store_base.parent
            global_base = projects_base.parent
        else:
            global_base = store_base
            projects_base = store_base / "projects"

        projects_seen: set = set()
        best = ep
        if projects_base.exists():
            for pdir in sorted(projects_base.iterdir()):
                if not pdir.is_dir():
                    continue
                try:
                    pstore = MemoryStore(str(pdir))
                except Exception:
                    continue
                for e in pstore.list_episodes(category="instinct"):
                    if (e.context_snapshot or {}).get("signature") == signature:
                        projects_seen.add(pdir.name)
                        if e.confidence > best.confidence:
                            best = e
                        break
        if not projects_seen:
            # instinct's own store isn't under projects_base (e.g. global cwd)
            projects_seen.add("_self")

        if len(projects_seen) < self.cfg["promote_min_projects"]:
            return False

        gstore = MemoryStore(str(global_base))
        gid = self._instinct_id("global", signature)
        ctx = {"signature": signature, "projects": sorted(projects_seen), "scope": "global"}
        g = gstore.get_episode(gid)
        if g:
            g.confidence = min(1.0, max(g.confidence, best.confidence))
            g.is_permanent = True
            g.last_accessed = _now()
            g.context_snapshot = ctx
            gstore.update_episode(g)
        else:
            g = MemoryEpisode(
                id=gid,
                session_id="instinct",
                timestamp=_now(),
                layer=INSTINCT_LAYER,
                title=signature,
                content=best.content,
                category="instinct",
                importance=0.7,
                confidence=max(self.cfg["base_confidence"], best.confidence),
                lesson=best.lesson,
                tags=["instinct", "global", signature.split(":")[0]],
                context_snapshot=ctx,
                is_permanent=True,
                access_count=1,
                last_accessed=_now(),
            )
            gstore.save_episode(g)
        return True
