"""SQLiteVecStore — SQLite + sqlite-vec ANN backend for MemoryEpisode.

Implements IMemoryStore using:
  - A plain `episodes` table for all episode fields.
  - A `ep_vecs` vec0 virtual table for ANN cosine search (requires sqlite-vec).
  - A `links` table for A-MEM graph edges.
  - A `file_states` table for change-detection hashes.

Install extras:  pip install 'crisp[semantic]'
                 (sentence-transformers + sqlite-vec)

Falls back gracefully: if sqlite-vec isn't loaded, search_by_embedding returns []
so keyword search still works.
"""

from __future__ import annotations

import json
import math
import sqlite3
import struct
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .memory_store import MemoryEpisode


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _encode_vec(vec: List[float]) -> bytes:
    """Pack a float list into little-endian IEEE 754 binary (sqlite-vec format)."""
    return struct.pack(f"{len(vec)}f", *vec)


def _decode_vec(blob: bytes) -> List[float]:
    n = len(blob) // 4
    return list(struct.unpack(f"{n}f", blob))


class SQLiteVecStore:
    """SQLite + sqlite-vec memory store.

    Parameters
    ----------
    db_path:
        Path to the SQLite database file. Created if it doesn't exist.
    embedding_dim:
        Dimension of embedding vectors. Must match the embedding provider's dim.
        Defaults to 384 (BAAI/bge-small-en-v1.5 / all-MiniLM-L6-v2).
    """

    def __init__(self, db_path: str, embedding_dim: int = 1024):
        self.db_path = Path(db_path).expanduser().resolve()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.embedding_dim = embedding_dim
        self._vec_available = False

        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")

        self._try_load_sqlite_vec()
        self._init_schema()

    # ------------------------------------------------------------------
    # Extension loading
    # ------------------------------------------------------------------

    def _try_load_sqlite_vec(self) -> None:
        try:
            import sqlite_vec  # type: ignore

            self._conn.enable_load_extension(True)
            sqlite_vec.load(self._conn)
            self._conn.enable_load_extension(False)
            self._vec_available = True
        except Exception:
            self._vec_available = False

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    def _init_schema(self) -> None:
        cur = self._conn.cursor()

        cur.executescript("""
            CREATE TABLE IF NOT EXISTS episodes (
                id              TEXT PRIMARY KEY
              , session_id      TEXT NOT NULL DEFAULT ''
              , timestamp       TEXT NOT NULL
              , layer           INTEGER NOT NULL DEFAULT 0
              , title           TEXT NOT NULL DEFAULT ''
              , content         TEXT NOT NULL DEFAULT ''
              , content_hash    TEXT NOT NULL DEFAULT ''
              , source_type     TEXT NOT NULL DEFAULT ''
              , source_path     TEXT NOT NULL DEFAULT ''
              , source_hash     TEXT NOT NULL DEFAULT ''
              , tags            TEXT NOT NULL DEFAULT '[]'
              , category        TEXT NOT NULL DEFAULT ''
              , importance      REAL NOT NULL DEFAULT 0.5
              , frustration_score REAL NOT NULL DEFAULT 0.0
              , correction_applied INTEGER NOT NULL DEFAULT 0
              , correction_delta TEXT NOT NULL DEFAULT ''
              , user_sentiment  TEXT NOT NULL DEFAULT ''
              , retry_count     INTEGER NOT NULL DEFAULT 0
              , explicit_feedback TEXT NOT NULL DEFAULT ''
              , trigger_type    TEXT NOT NULL DEFAULT ''
              , root_cause      TEXT NOT NULL DEFAULT ''
              , impact          TEXT NOT NULL DEFAULT ''
              , lesson          TEXT NOT NULL DEFAULT ''
              , context_snapshot TEXT NOT NULL DEFAULT '{}'
              , access_count    INTEGER NOT NULL DEFAULT 0
              , last_accessed   TEXT NOT NULL DEFAULT ''
              , decay_score     REAL NOT NULL DEFAULT 1.0
              , is_permanent    INTEGER NOT NULL DEFAULT 0
              , parent_id       TEXT NOT NULL DEFAULT ''
              , linked_ids      TEXT NOT NULL DEFAULT '[]'
              , confidence      REAL NOT NULL DEFAULT 0.0
              , embedding_dim   INTEGER NOT NULL DEFAULT 0
            );

            CREATE INDEX IF NOT EXISTS idx_ep_layer    ON episodes(layer);
            CREATE INDEX IF NOT EXISTS idx_ep_category ON episodes(category);
            CREATE INDEX IF NOT EXISTS idx_ep_hash     ON episodes(content_hash);
            CREATE INDEX IF NOT EXISTS idx_ep_source   ON episodes(source_path);

            CREATE TABLE IF NOT EXISTS links (
                source_id   TEXT NOT NULL
              , target_id   TEXT NOT NULL
              , link_type   TEXT NOT NULL
              , strength    REAL NOT NULL DEFAULT 1.0
              , PRIMARY KEY (source_id, target_id, link_type)
            );

            CREATE TABLE IF NOT EXISTS file_states (
                file_path    TEXT PRIMARY KEY
              , content_hash TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS hash_index (
                content_hash TEXT PRIMARY KEY
              , episode_id   TEXT NOT NULL
            );
        """)

        if self._vec_available:
            cur.execute(f"""
                CREATE VIRTUAL TABLE IF NOT EXISTS ep_vecs
                USING vec0(
                    episode_id TEXT PRIMARY KEY
                  , embedding  float[{self.embedding_dim}]
                )
            """)

        self._conn.commit()

    # ------------------------------------------------------------------
    # IMemoryStore implementation
    # ------------------------------------------------------------------

    def save_episode(self, episode: MemoryEpisode) -> bool:
        cur = self._conn.cursor()

        # Dedup by content hash
        if episode.content_hash:
            cur.execute(
                "SELECT id FROM hash_index WHERE content_hash = ?",
                (episode.content_hash,),
            )
            if cur.fetchone():
                return False

        tags_json = json.dumps(episode.tags)
        linked_ids_json = json.dumps(episode.linked_ids)
        ctx_json = json.dumps(episode.context_snapshot)

        cur.execute(
            """INSERT OR REPLACE INTO episodes VALUES (
                :id, :session_id, :timestamp, :layer, :title, :content,
                :content_hash, :source_type, :source_path, :source_hash,
                :tags, :category, :importance, :frustration_score,
                :correction_applied, :correction_delta, :user_sentiment,
                :retry_count, :explicit_feedback, :trigger_type, :root_cause,
                :impact, :lesson, :context_snapshot, :access_count,
                :last_accessed, :decay_score, :is_permanent, :parent_id,
                :linked_ids, :confidence, :embedding_dim
            )""",
            {
                "id": episode.id,
                "session_id": episode.session_id,
                "timestamp": episode.timestamp,
                "layer": episode.layer,
                "title": episode.title,
                "content": episode.content,
                "content_hash": episode.content_hash,
                "source_type": episode.source_type,
                "source_path": episode.source_path,
                "source_hash": episode.source_hash,
                "tags": tags_json,
                "category": episode.category,
                "importance": episode.importance,
                "frustration_score": episode.frustration_score,
                "correction_applied": int(episode.correction_applied),
                "correction_delta": episode.correction_delta,
                "user_sentiment": episode.user_sentiment,
                "retry_count": episode.retry_count,
                "explicit_feedback": episode.explicit_feedback,
                "trigger_type": episode.trigger_type,
                "root_cause": episode.root_cause,
                "impact": episode.impact,
                "lesson": episode.lesson,
                "context_snapshot": ctx_json,
                "access_count": episode.access_count,
                "last_accessed": episode.last_accessed,
                "decay_score": episode.decay_score,
                "is_permanent": int(episode.is_permanent),
                "parent_id": episode.parent_id,
                "linked_ids": linked_ids_json,
                "confidence": episode.confidence,
                "embedding_dim": len(episode.embedding),
            },
        )

        if episode.content_hash:
            cur.execute(
                "INSERT OR REPLACE INTO hash_index VALUES (?, ?)",
                (episode.content_hash, episode.id),
            )

        # Store embedding in vec0 if available and episode has one
        if self._vec_available and episode.embedding:
            vec = episode.embedding
            if len(vec) != self.embedding_dim:
                # Pad or truncate to match table dim — shouldn't happen in practice
                vec = (vec + [0.0] * self.embedding_dim)[: self.embedding_dim]
            cur.execute(
                "INSERT OR REPLACE INTO ep_vecs(episode_id, embedding) VALUES (?, ?)",
                (episode.id, _encode_vec(vec)),
            )

        self._conn.commit()
        return True

    def get_episode(self, episode_id: str) -> Optional[MemoryEpisode]:
        cur = self._conn.cursor()
        cur.execute("SELECT * FROM episodes WHERE id = ?", (episode_id,))
        row = cur.fetchone()
        if row is None:
            return None
        return self._row_to_episode(row)

    def list_episodes(
        self,
        layer: Optional[int] = None,
        category: Optional[str] = None,
        tag: Optional[str] = None,
        include_embedding: bool = False,
    ) -> List[MemoryEpisode]:
        # include_embedding is a no-op here: this backend never carries the
        # vector in the episodes row at all (it lives in the separate
        # ep_vecs sidecar table, queried via search_by_embedding) — accepted
        # only so callers written against the MemoryStore signature (e.g.
        # MemoryReflector._cluster_l1_by_embedding) don't hit a TypeError.
        clauses: List[str] = []
        params: List[Any] = []

        if layer is not None:
            clauses.append("layer = ?")
            params.append(layer)
        if category is not None:
            clauses.append("category = ?")
            params.append(category)

        sql = "SELECT * FROM episodes"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY timestamp DESC"

        cur = self._conn.cursor()
        cur.execute(sql, params)
        rows = cur.fetchall()

        episodes = [self._row_to_episode(r) for r in rows]

        # tag filter is post-process (stored as JSON array)
        if tag is not None:
            episodes = [e for e in episodes if tag in e.tags]

        return episodes

    def delete_episode(self, episode_id: str) -> bool:
        cur = self._conn.cursor()
        cur.execute("SELECT content_hash FROM episodes WHERE id = ?", (episode_id,))
        row = cur.fetchone()
        if row is None:
            return False

        cur.execute("DELETE FROM episodes WHERE id = ?", (episode_id,))
        cur.execute(
            "DELETE FROM hash_index WHERE content_hash = ?", (row["content_hash"],)
        )
        if self._vec_available:
            cur.execute("DELETE FROM ep_vecs WHERE episode_id = ?", (episode_id,))
        cur.execute(
            "DELETE FROM links WHERE source_id = ? OR target_id = ?",
            (episode_id, episode_id),
        )
        self._conn.commit()
        return True

    def get_by_content_hash(self, content_hash: str) -> Optional[str]:
        cur = self._conn.cursor()
        cur.execute(
            "SELECT episode_id FROM hash_index WHERE content_hash = ?",
            (content_hash,),
        )
        row = cur.fetchone()
        return row["episode_id"] if row else None

    def get_file_state(self, file_path: str) -> Optional[str]:
        cur = self._conn.cursor()
        cur.execute(
            "SELECT content_hash FROM file_states WHERE file_path = ?", (file_path,)
        )
        row = cur.fetchone()
        return row["content_hash"] if row else None

    def set_file_state(self, file_path: str, content_hash: str) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO file_states VALUES (?, ?)",
            (file_path, content_hash),
        )
        self._conn.commit()

    def get_links(self, episode_id: str) -> List[Tuple[str, str, float]]:
        cur = self._conn.cursor()
        cur.execute(
            "SELECT target_id, link_type, strength FROM links WHERE source_id = ?",
            (episode_id,),
        )
        return [(r["target_id"], r["link_type"], r["strength"]) for r in cur.fetchall()]

    def add_link(
        self, source_id: str, target_id: str, link_type: str, strength: float
    ) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO links VALUES (?, ?, ?, ?)",
            (source_id, target_id, link_type, strength),
        )
        self._conn.commit()

    def search_by_embedding(
        self, vector: List[float], limit: int
    ) -> List[Tuple[str, float]]:
        """KNN cosine search via sqlite-vec vec0 table."""
        if not self._vec_available or not vector:
            return []

        # Pad/truncate to table dim
        vec = (vector + [0.0] * self.embedding_dim)[: self.embedding_dim]

        cur = self._conn.cursor()
        try:
            cur.execute(
                """
                SELECT episode_id, distance
                FROM ep_vecs
                WHERE embedding MATCH ?
                  AND k = ?
                ORDER BY distance
                """,
                (_encode_vec(vec), limit),
            )
            rows = cur.fetchall()
        except sqlite3.OperationalError:
            return []

        results = []
        for r in rows:
            # sqlite-vec returns L2 distance for cosine-normalised vectors;
            # convert to cosine similarity: sim = 1 - dist^2/2
            dist = r["distance"]
            sim = max(0.0, 1.0 - (dist * dist) / 2.0)
            results.append((r["episode_id"], sim))

        return results

    def search_by_keyword(self, query: str, limit: int) -> List[Tuple[str, float]]:
        """Simple TF-IDF-style keyword search across title + content + tags."""
        terms = [t.lower() for t in query.split() if t]
        if not terms:
            return []

        cur = self._conn.cursor()
        cur.execute(
            "SELECT id, title, content, tags FROM episodes ORDER BY timestamp DESC LIMIT 2000"
        )
        rows = cur.fetchall()

        scored: List[Tuple[str, float]] = []
        for r in rows:
            title_l = (r["title"] or "").lower()
            content_l = (r["content"] or "").lower()
            tags_l = (r["tags"] or "[]").lower()

            score = 0.0
            for t in terms:
                score += title_l.count(t) * 2.0
                score += content_l.count(t) * 1.0
                score += tags_l.count(t) * 0.3

            if score > 0:
                scored.append((r["id"], score))

        scored.sort(key=lambda x: x[1], reverse=True)
        max_score = scored[0][1] if scored else 1.0
        return [(ep_id, s / max_score) for ep_id, s in scored[:limit]]

    def update_access(self, episode_id: str) -> None:
        self._conn.execute(
            """UPDATE episodes
               SET access_count = access_count + 1
                 , last_accessed = ?
                 , decay_score   = MIN(1.0, decay_score + 0.1)
               WHERE id = ?""",
            (_now_iso(), episode_id),
        )
        self._conn.commit()

    def find_similar(
        self, content: str, threshold: float = 0.8
    ) -> List[Tuple[str, float]]:
        """Shingle-based Jaccard similarity (fallback when no embedding)."""
        shingles = _shingle(content)
        if not shingles:
            return []

        cur = self._conn.cursor()
        cur.execute("SELECT id, content FROM episodes")
        results: List[Tuple[str, float]] = []
        for r in cur.fetchall():
            other = _shingle(r["content"] or "")
            if not other:
                continue
            inter = len(shingles & other)
            union = len(shingles | other)
            sim = inter / union if union else 0.0
            if sim >= threshold:
                results.append((r["id"], sim))

        results.sort(key=lambda x: x[1], reverse=True)
        return results

    def get_stats(self) -> Dict[str, Any]:
        cur = self._conn.cursor()
        cur.execute("SELECT layer, COUNT(*) as n FROM episodes GROUP BY layer")
        layer_counts = {f"l{r['layer']}": r["n"] for r in cur.fetchall()}

        cur.execute("SELECT COUNT(*) as n FROM ep_vecs") if self._vec_available else None
        vec_count = cur.fetchone()["n"] if self._vec_available else 0

        cur.execute("SELECT COUNT(*) as n FROM links")
        link_count = cur.fetchone()["n"]

        return {
            "backend": "sqlite_vec",
            "db_path": str(self.db_path),
            "vec_available": self._vec_available,
            "embedding_dim": self.embedding_dim,
            "episodes": layer_counts,
            "vec_indexed": vec_count,
            "links": link_count,
        }

    def prune(self) -> Dict[str, Any]:
        """Stub — real pruning runs via PruningService which calls delete_episode."""
        return {"pruned": 0}

    def consolidate(self, max_l0_per_batch: int = 20) -> Dict[str, Any]:
        """Stub — real consolidation runs via Reflector."""
        return {"consolidated": 0}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _row_to_episode(self, row: sqlite3.Row) -> MemoryEpisode:
        d = dict(row)
        d["tags"] = json.loads(d.get("tags") or "[]")
        d["linked_ids"] = json.loads(d.get("linked_ids") or "[]")
        d["context_snapshot"] = json.loads(d.get("context_snapshot") or "{}")
        d["correction_applied"] = bool(d.get("correction_applied", 0))
        d["is_permanent"] = bool(d.get("is_permanent", 0))
        d.pop("embedding_dim", None)
        d["embedding"] = []
        return MemoryEpisode(**d)

    def close(self) -> None:
        self._conn.close()


def _shingle(text: str, k: int = 3) -> set:
    words = text.lower().split()
    if len(words) < k:
        return set(words)
    return {" ".join(words[i : i + k]) for i in range(len(words) - k + 1)}
