"""Core memory store using MD files with YAML frontmatter."""

import hashlib
import json
import os
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol, Tuple

import yaml


def is_code_index_category(category: str) -> bool:
    """True for any structural or semantic code-index episode category.

    Structural (tree-sitter/regex, via IndexerRegistry.extract_episodes)
    tags episodes "code_element"; semantic (huh save-index) and dir
    placeholders use "code_index*". Both mean "this file/symbol is indexed"
    for freshness/staleness/status checks — kept in one place so hooks.py and
    cli.py can't drift into checking only one convention.
    """
    return category == "code_element" or category.startswith("code_index")


@dataclass
class MemoryEpisode:
    """A single memory episode (L0 - raw interaction)."""

    id: str
    session_id: str
    timestamp: str  # ISO format UTC
    layer: int = 0

    # Content
    title: str = ""
    content: str = ""
    content_hash: str = ""

    # Source tracking
    source_type: str = ""  # file, chat, tool, manual
    source_path: str = ""  # file path if applicable
    source_hash: str = ""  # hash of source at time of capture

    # Metadata
    tags: List[str] = field(default_factory=list)
    category: str = ""  # code, doc, decision, bug, correction, preference
    importance: float = 0.5  # 0.0 - 1.0

    # Behavioral signals
    frustration_score: float = 0.0
    correction_applied: bool = False
    correction_delta: str = ""
    user_sentiment: str = ""  # positive, negative, neutral
    retry_count: int = 0
    explicit_feedback: str = ""

    # Causal chain
    trigger_type: str = ""  # user_request, scheduled, reaction, error_recovery
    root_cause: str = ""
    impact: str = ""
    lesson: str = ""

    # Context
    context_snapshot: Dict[str, Any] = field(default_factory=dict)

    # Memory management
    access_count: int = 0
    last_accessed: str = ""
    decay_score: float = 1.0
    is_permanent: bool = False
    parent_id: str = ""  # links to summary (L1+)
    linked_ids: List[str] = field(default_factory=list)  # A-MEM graph links

    # Derived
    embedding: List[float] = field(default_factory=list)

    # Instinct / continuous-learning: confidence in a reinforced behavior (0.0-1.0).
    # Only meaningful for category="instinct" episodes; 0.0 elsewhere (and dropped
    # from frontmatter by the zero-default cleaning below).
    confidence: float = 0.0

    def to_frontmatter(self) -> str:
        """Convert to YAML frontmatter string."""
        data = asdict(self)
        # Remove empty/default fields for cleaner output
        cleaned = {}
        for k, v in data.items():
            if v not in (None, "", [], {}, 0.0, 0, False):
                cleaned[k] = v
            elif k in ("tags", "linked_ids", "embedding") and v:
                cleaned[k] = v
            elif k in ("is_permanent", "correction_applied") and v:
                cleaned[k] = v
        return yaml.dump(cleaned, default_flow_style=False, sort_keys=False)

    @classmethod
    def from_frontmatter(cls, fm_str: str, content: str) -> "MemoryEpisode":
        """Parse from YAML frontmatter + content."""
        data = yaml.safe_load(fm_str)
        data["content"] = content
        # Ensure required fields
        for f in ["id", "session_id", "timestamp"]:
            if f not in data:
                data[f] = (
                    str(uuid.uuid4())
                    if f == "id"
                    else (
                        "session_" + str(uuid.uuid4())[:8]
                        if f == "session_id"
                        else datetime.now(timezone.utc).isoformat()
                    )
                )
        # YAML safe_load may parse ISO timestamps into datetime objects; normalize to str
        if isinstance(data.get("timestamp"), datetime):
            data["timestamp"] = data["timestamp"].isoformat()
        if isinstance(data.get("last_accessed"), datetime):
            data["last_accessed"] = data["last_accessed"].isoformat()
        return cls(**data)


class IMemoryStore(Protocol):
    """Storage-agnostic interface for memory operations (D in SOLID).

    Any storage backend (MD files, SQLite, Notion, etc.) can implement this.
    Domain logic depends only on this interface, not concrete implementations.
    """

    def save_episode(self, episode: MemoryEpisode) -> bool:
        """Save an episode. Returns True if written, False if duplicate/skipped."""
        ...

    def get_episode(self, episode_id: str) -> Optional[MemoryEpisode]:
        """Retrieve an episode by ID."""
        ...

    def get_episode_by_id(self, episode_id: str) -> Optional[MemoryEpisode]:
        """Retrieve an episode by stable ID across all layers."""
        ...

    def list_episodes(
        self,
        layer: Optional[int] = None,
        category: Optional[str] = None,
        tag: Optional[str] = None,
    ) -> List[MemoryEpisode]:
        """List episodes with optional filters."""
        ...

    def delete_episode(self, episode_id: str) -> bool:
        """Delete an episode. Returns True if deleted."""
        ...

    def get_by_content_hash(self, content_hash: str) -> Optional[str]:
        """Get episode ID by content hash for deduplication."""
        ...

    def get_file_state(self, file_path: str) -> Optional[str]:
        """Get last content hash for a file (change detection)."""
        ...

    def set_file_state(self, file_path: str, content_hash: str) -> None:
        """Store file's content hash for change detection."""
        ...

    def get_links(self, episode_id: str) -> List[Tuple[str, str, float]]:
        """Get linked episodes: [(target_id, link_type, strength), ...]."""
        ...

    def add_link(
        self, source_id: str, target_id: str, link_type: str, strength: float
    ) -> None:
        """Add a directed link between episodes (A-MEM graph)."""
        ...

    def search_by_embedding(
        self, vector: List[float], limit: int
    ) -> List[Tuple[str, float]]:
        """Search by embedding vector: [(episode_id, score), ...]."""
        ...

    def search_by_keyword(self, query: str, limit: int) -> List[Tuple[str, float]]:
        """Search by keyword: [(episode_id, score), ...]."""
        ...

    def update_access(self, episode_id: str) -> None:
        """Update access count and timestamp for an episode."""
        ...

    def find_similar(
        self, content: str, threshold: float = 0.8
    ) -> List[Tuple[str, float]]:
        """Find similar content using shingle-based Jaccard similarity."""
        ...

    def get_stats(self) -> Dict[str, Any]:
        """Get store statistics."""
        ...

    def prune(self) -> Dict[str, Any]:
        """Run pruning job (decay, archival, conflict resolution)."""
        ...

    def consolidate(self, max_l0_per_batch: int = 20) -> Dict[str, Any]:
        """Run consolidation: L0→L1, L1→L2, L2→L3."""
        ...


class _VecSidecar:
    """sqlite-vec sidecar for MDFileStore.

    Stores embeddings in a separate SQLite DB (cache/vec_sidecar.db) so the
    .md files remain the source of truth and the vec index is fully rebuildable.
    Falls back silently if sqlite-vec extension is not installed.
    """

    def __init__(self, db_path: Path, dim: int):
        import sqlite3, struct as _struct
        self._struct = _struct
        self._dim = dim
        self._ok = False
        try:
            self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
            self._conn.execute("PRAGMA journal_mode=WAL")
            import sqlite_vec  # type: ignore
            self._conn.enable_load_extension(True)
            sqlite_vec.load(self._conn)
            self._conn.enable_load_extension(False)
            self._conn.execute(
                f"CREATE VIRTUAL TABLE IF NOT EXISTS ep_vecs "
                f"USING vec0(episode_id TEXT PRIMARY KEY, embedding float[{dim}])"
            )
            self._conn.commit()
            self._ok = True
        except Exception:
            pass

    def upsert(self, episode_id: str, vec: List[float]) -> None:
        if not self._ok or not vec:
            return
        v = (vec + [0.0] * self._dim)[: self._dim]
        blob = self._struct.pack(f"{self._dim}f", *v)
        try:
            self._conn.execute(
                "INSERT OR REPLACE INTO ep_vecs(episode_id, embedding) VALUES (?,?)",
                (episode_id, blob),
            )
            self._conn.commit()
        except Exception:
            pass

    def search(self, vec: List[float], limit: int) -> List[Tuple[str, float]]:
        if not self._ok or not vec:
            return []
        v = (vec + [0.0] * self._dim)[: self._dim]
        blob = self._struct.pack(f"{self._dim}f", *v)
        try:
            cur = self._conn.execute(
                "SELECT episode_id, distance FROM ep_vecs "
                "WHERE embedding MATCH ? AND k = ? ORDER BY distance",
                (blob, limit),
            )
            rows = cur.fetchall()
        except Exception:
            return []
        out = []
        for ep_id, dist in rows:
            sim = max(0.0, 1.0 - (dist * dist) / 2.0)
            out.append((ep_id, sim))
        return out

    def delete(self, episode_id: str) -> None:
        if not self._ok:
            return
        try:
            self._conn.execute("DELETE FROM ep_vecs WHERE episode_id=?", (episode_id,))
            self._conn.commit()
        except Exception:
            pass

    def count(self) -> int:
        if not self._ok:
            return 0
        try:
            return self._conn.execute("SELECT COUNT(*) FROM ep_vecs").fetchone()[0]
        except Exception:
            return 0


class MemoryStore:
    """File-based memory store using MD files with YAML frontmatter.

    Implements IMemoryStore interface. Storage is a pluggable detail -
    domain logic depends only on the IMemoryStore interface.
    """

    def __init__(self, base_path: str):
        self.base_path = Path(base_path).expanduser().resolve()
        self.layers_path = self.base_path / "layers"
        self.cache_path = self.base_path / "cache"
        self.config_path = self.base_path / "config"
        self.links_path = self.base_path / "cache" / "links.json"

        # Create directories
        for p in [self.layers_path / f"l{i}" for i in range(4)]:
            p.mkdir(parents=True, exist_ok=True)
        self.cache_path.mkdir(parents=True, exist_ok=True)
        self.config_path.mkdir(parents=True, exist_ok=True)

        # Config
        self.config_file = self.config_path / "config.json"
        self.config = self._load_config()

        # Hash cache for change detection (content_hash -> episode_id)
        self.hash_cache_file = self.cache_path / "hashes.json"
        self.hash_cache = self._load_hash_cache()

        # File state tracking for change detection (path_hash -> content_hash)
        self.file_states_file = self.cache_path / "file_states.json"
        self.file_states = self._load_file_states()

        # Path hash mapping for privacy (path_hash -> original_path metadata)
        self.path_index_file = self.cache_path / "path_index.json"
        self.path_index = self._load_path_index()

        # Links (A-MEM graph)
        self.links = self._load_links()

        # Vec sidecar — initialised after config is loaded
        self._vec: Optional[_VecSidecar] = None

    def _get_vec(self) -> "_VecSidecar":
        """Lazy-init the vec sidecar so startup never blocks on sqlite-vec."""
        if self._vec is None:
            dim = int(self.config.get("embedding_dim", 1024))
            self._vec = _VecSidecar(self.cache_path / "vec_sidecar.db", dim)
        return self._vec

    def _load_config(self) -> Dict[str, Any]:
        """Load or create default config."""
        default = {
            "decay_half_life_days": {"l0": 1, "l1": 7, "l2": 30, "l3": 36500},
            "importance_threshold": 0.3,
            "similarity_threshold": 0.92,
            "access_boost_days": 7,
            "archive_threshold_days": 90,
            "delete_threshold_days": 365,
            "reflection_interval": 20,
            # Embeddings (used only by the USER-TRIGGERED `search --semantic` path).
            # Default: huggingface (BAAI/bge-small-en-v1.5, local, ~130 MB on first run).
            # Fallback chain on failure: hf → word2vec (gensim, ~66 MB) → ImportError.
            "embedding_provider": "ollama",  # ollama | huggingface | dspy | word2vec
            "embedding_model": "qllama/bge-large-en-v1.5:latest",
            "embedding_api_url": "http://localhost:11434/api/embeddings",
            "embedding_dim": 1024,
            "use_local_embeddings": True,
            "pii_scrubbing": True,
            # Continuous-learning instinct engine (lib/instincts.py).
            "instincts": {
                "enabled": True,
                "min_observations": 20,   # buffer size before analyze() distills
                "min_pattern_count": 3,   # times a signature must recur to instinct
                "base_confidence": 0.3,   # confidence of a freshly distilled instinct
                "reinforce_step": 0.1,    # +confidence when a pattern recurs
                "decay_step": 0.05,       # -confidence on contradiction
                "evolve_threshold": 0.8,  # min confidence to evolve into an artifact
                "promote_min_projects": 2,  # distinct projects before project->global
            },
        }
        if self.config_file.exists():
            with open(self.config_file) as f:
                return {**default, **json.load(f)}
        return default

    def _save_config(self):
        """Save config to disk."""
        with open(self.config_file, "w") as f:
            json.dump(self.config, f, indent=2)

    @staticmethod
    def _read_json_tolerant(path: Path, default):
        """Load a JSON state file, surviving corruption instead of crashing.

        A direct open()+json.dump() write (what this used to be) is not
        atomic: two processes writing the same file around the same time —
        e.g. two hook invocations, or a crash mid-write — can interleave and
        leave a file with a second JSON object's tail fused onto the first's
        closing brace. That happened for real to this project's own
        hashes.json during development. On corruption: try to salvage the
        first complete JSON value (raw_decode), else fall back to `default`
        rather than taking the whole store down on the next hook call.
        """
        if not path.exists():
            return default
        try:
            return json.loads(path.read_text())
        except json.JSONDecodeError:
            try:
                obj, _ = json.JSONDecoder().raw_decode(path.read_text())
                return obj
            except (json.JSONDecodeError, ValueError):
                return default

    @staticmethod
    def _write_json_atomic(path: Path, data) -> None:
        """Write JSON via temp-file + rename so a crash or a concurrent
        writer can never observe (or produce) a half-written file — the
        rename is atomic at the filesystem level, unlike open(path, "w").
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + f".tmp{os.getpid()}")
        tmp.write_text(json.dumps(data, indent=2))
        os.replace(tmp, path)

    def _load_hash_cache(self) -> Dict[str, str]:
        """Load hash cache for deduplication."""
        return self._read_json_tolerant(self.hash_cache_file, {})

    def _save_hash_cache(self):
        """Save hash cache."""
        self._write_json_atomic(self.hash_cache_file, self.hash_cache)

    def _load_file_states(self) -> Dict[str, str]:
        """Load file state tracking for change detection (path_hash -> content_hash)."""
        return self._read_json_tolerant(self.file_states_file, {})

    def _save_file_states(self):
        """Save file state tracking."""
        self._write_json_atomic(self.file_states_file, self.file_states)

    def _load_path_index(self) -> Dict[str, Dict[str, str]]:
        """Load path index (path_hash -> {project, rel_path, original})."""
        return self._read_json_tolerant(self.path_index_file, {})

    def _save_path_index(self):
        """Save path index."""
        self._write_json_atomic(self.path_index_file, self.path_index)

    def _load_links(self) -> Dict[str, List[Dict[str, Any]]]:
        """Load A-MEM graph links."""
        return self._read_json_tolerant(self.links_path, {})

    def _save_links(self):
        """Save A-MEM graph links."""
        self._write_json_atomic(self.links_path, self.links)

    @staticmethod
    def compute_hash(content: str) -> str:
        """Compute SHA256 hash of content."""
        return hashlib.sha256(content.encode()).hexdigest()

    @staticmethod
    def compute_path_hash(file_path: str) -> str:
        """Compute privacy-preserving hash of file path.

        Hashes absolute path to avoid storing PII in plaintext.
        Returns first 16 chars of SHA256(path)."""
        return hashlib.sha256(file_path.encode()).hexdigest()[:16]

    def _get_project_root(self, file_path: str) -> Optional[str]:
        """Detect project root for a file (git root or workspace marker)."""
        path = Path(file_path).resolve()

        # Look for git root
        for parent in [path] + list(path.parents):
            if (parent / ".git").exists():
                return str(parent)

        # Look for project markers
        for marker in [
            "package.json",
            "Cargo.toml",
            "go.mod",
            "pyproject.toml",
            ".claude/",
        ]:
            for parent in [path] + list(path.parents):
                if (parent / marker).exists():
                    return str(parent)

        return None

    def _get_relative_path(self, file_path: str) -> str:
        """Get path relative to project root, or absolute if no root found."""
        project_root = self._get_project_root(file_path)
        if project_root:
            try:
                return str(Path(file_path).relative_to(project_root))
            except ValueError:
                pass
        return str(Path(file_path).resolve())

    def _filepath(self, episode_id: str, layer: int) -> Path:
        """Get file path for an episode."""
        return self.layers_path / f"l{layer}" / f"{episode_id}.md"

    def save_episode(self, episode: MemoryEpisode) -> bool:
        """Write an episode to storage with deduplication.

        Returns True if written, False if duplicate/skipped.

        Deduplication flow:
        1. Check content hash - if exists, update access count only
        2. Check file state - if unchanged, link to existing episode
        3. Actually persist
        """
        # Deduplication: content hash check
        if episode.content:
            content_hash = self.compute_hash(episode.content)
            episode.content_hash = content_hash

            existing_id = self.hash_cache.get(content_hash)
            if existing_id:
                # Duplicate! Update access count only
                existing = self.get_episode(existing_id)
                if existing:
                    existing.access_count += 1
                    existing.last_accessed = datetime.now(timezone.utc).isoformat()
                    self._write_raw(existing)
                return False

        # File change check (if source_type=file) — file_states is keyed by
        # hashed path everywhere else (get_file_state/set_file_state/
        # _get_episode_id_for_file); use the same key here so this lookup
        # actually finds what those other methods wrote.
        if episode.source_type == "file" and episode.source_path:
            last_hash = self.get_file_state(episode.source_path)
            if last_hash == episode.source_hash:
                # File unchanged - link to existing episode
                existing_id = self._get_episode_id_for_file(episode.source_path)
                if existing_id:
                    existing = self.get_episode(existing_id)
                    if existing:
                        existing.access_count += 1
                        existing.last_accessed = datetime.now(timezone.utc).isoformat()
                        self._write_raw(existing)
                    return False
            # File changed - store new hash
            self.set_file_state(episode.source_path, episode.source_hash)

        # Actually persist
        self._write_raw(episode)

        # Update hash cache
        if episode.content:
            self.hash_cache[episode.content_hash] = episode.id
            self._save_hash_cache()

        # Index embedding into vec sidecar if present
        if episode.embedding:
            self._get_vec().upsert(episode.id, episode.embedding)

        return True

    def update_episode(self, episode: MemoryEpisode) -> None:
        """Persist in-place changes to an existing episode (no dedup check).

        Used when mutating metadata such as instinct confidence/access without
        creating a new episode. Refreshes the content-hash cache if content moved.
        """
        if episode.content:
            new_hash = self.compute_hash(episode.content)
            if new_hash != episode.content_hash:
                # content changed: drop the stale hash mapping, register the new one
                self.hash_cache = {
                    k: v for k, v in self.hash_cache.items() if v != episode.id
                }
                episode.content_hash = new_hash
            self.hash_cache[episode.content_hash] = episode.id
            self._save_hash_cache()
        self._write_raw(episode)

    def _get_episode_id_for_file(self, file_path: str) -> Optional[str]:
        """Find episode ID for a given file path (uses hashed lookup)."""
        path_hash = self.compute_path_hash(file_path)
        content_hash = self.file_states.get(path_hash)
        if not content_hash:
            return None

        # Find episode with matching content_hash and source_path
        for layer in range(4):
            layer_dir = self.layers_path / f"l{layer}"
            if not layer_dir.exists():
                continue
            for fp in layer_dir.glob("*.md"):
                ep = self._parse_file(fp)
                if ep and ep.source_path == file_path:
                    return ep.id
        return None

    def _write_raw(self, episode: MemoryEpisode):
        """Write episode to file (internal)."""
        filepath = self._filepath(episode.id, episode.layer)
        fm = episode.to_frontmatter()
        content = f"---\n{fm}---\n\n{episode.content}"
        filepath.write_text(content)

    def read_episode(self, episode_id: str) -> Optional[MemoryEpisode]:
        """Read an episode by ID."""
        for layer in range(4):
            filepath = self._filepath(episode_id, layer)
            if filepath.exists():
                ep = self._parse_file(filepath)
                if ep:
                    return ep
        return None

    def get_episode(self, episode_id: str) -> Optional[MemoryEpisode]:
        """Read an episode by ID. Alias for read_episode."""
        return self.read_episode(episode_id)

    def get_episode_by_id(self, episode_id: str) -> Optional[MemoryEpisode]:
        """Read an episode by stable ID across all layers."""
        return self.read_episode(episode_id)

    def _parse_file(self, filepath: Path) -> Optional[MemoryEpisode]:
        """Parse MD file with YAML frontmatter."""
        content = filepath.read_text()
        # Extract frontmatter
        match = re.match(r"^---\n(.*?)\n---\n\n?(.*)$", content, re.DOTALL)
        if not match:
            return None
        fm_str, body = match.groups()
        episode = MemoryEpisode.from_frontmatter(fm_str, body.strip())
        return episode

    def list_episodes(
        self,
        layer: Optional[int] = None,
        category: Optional[str] = None,
        tag: Optional[str] = None,
    ) -> List[MemoryEpisode]:
        """List episodes with optional filters."""
        layers = [layer] if layer is not None else range(4)
        episodes = []
        for l in layers:
            layer_dir = self.layers_path / f"l{l}"
            if not layer_dir.exists():
                continue
            for fp in sorted(layer_dir.glob("*.md")):
                ep = self._parse_file(fp)
                if ep:
                    if category and ep.category != category:
                        continue
                    if tag and tag not in ep.tags:
                        continue
                    episodes.append(ep)
        # Sort by timestamp descending
        episodes.sort(key=lambda e: e.timestamp, reverse=True)
        return episodes

    def update_access(self, episode_id: str) -> None:
        """Update access count and timestamp for an episode."""
        episode = self.get_episode(episode_id)
        if episode:
            episode.access_count += 1
            episode.last_accessed = datetime.now(timezone.utc).isoformat()
            self._write_raw(episode)

    def delete_episode(self, episode_id: str) -> bool:
        """Delete an episode from all layers."""
        deleted = False
        for layer in range(4):
            filepath = self._filepath(episode_id, layer)
            if filepath.exists():
                filepath.unlink()
                deleted = True
        # Remove from hash cache — hash_cache is {content_hash: episode_id},
        # so a stale entry is one whose VALUE is this episode_id, not whose
        # key happens to equal it (keys are content hashes, never episode ids).
        self.hash_cache = {k: v for k, v in self.hash_cache.items() if v != episode_id}
        self._save_hash_cache()
        # Remove from file states if this was the only episode for that file
        # (simplified: keep file state, it will be reused)
        return deleted

    def get_by_content_hash(self, content_hash: str) -> Optional[str]:
        """Get episode ID by content hash for deduplication."""
        return self.hash_cache.get(content_hash)

    def get_file_state(self, file_path: str) -> Optional[str]:
        """Get last content hash for a file (uses hashed path for privacy)."""
        path_hash = self.compute_path_hash(file_path)
        return self.file_states.get(path_hash)

    def set_file_state(self, file_path: str, content_hash: str) -> None:
        """Store file's content hash (uses hashed path)."""
        path_hash = self.compute_path_hash(file_path)
        self.file_states[path_hash] = content_hash

        # Also store path metadata for reverse lookup
        rel_path = self._get_relative_path(file_path)
        project_root = self._get_project_root(file_path) or ""
        self.path_index[path_hash] = {
            "original": str(Path(file_path).resolve()),
            "relative": rel_path,
            "project": project_root,
        }
        self._save_file_states()
        self._save_path_index()

    def get_links(self, episode_id: str) -> List[Tuple[str, str, float]]:
        """Get linked episodes: [(target_id, link_type, strength), ...]."""
        link_dicts = self.links.get(episode_id, [])
        result = []
        for ld in link_dicts:
            if isinstance(ld, dict):
                result.append((ld["target_id"], ld["link_type"], ld["strength"]))
            else:
                result.append(ld)
        return result

    def add_link(
        self, source_id: str, target_id: str, link_type: str, strength: float
    ) -> None:
        """Add a directed link between episodes (A-MEM graph)."""
        if source_id not in self.links:
            self.links[source_id] = []
        # Avoid duplicates
        existing = [
            (t, lt, s)
            for t, lt, s in self.links[source_id]
            if t == target_id and lt == link_type
        ]
        if not existing:
            self.links[source_id].append(
                {"target_id": target_id, "link_type": link_type, "strength": strength}
            )
            self._save_links()

    def search_by_embedding(
        self, vector: List[float], limit: int
    ) -> List[Tuple[str, float]]:
        """ANN search via sqlite-vec sidecar. Returns [] if sidecar unavailable."""
        return self._get_vec().search(vector, limit)

    def search_by_keyword(self, query: str, limit: int) -> List[Tuple[str, float]]:
        """Search by keyword using TF-IDF on cached content."""
        results = []
        query_lower = query.lower()
        query_words = set(query_lower.split())

        for layer in range(4):
            layer_dir = self.layers_path / f"l{layer}"
            if not layer_dir.exists():
                continue
            for fp in layer_dir.glob("*.md"):
                ep = self._parse_file(fp)
                if not ep or not ep.content:
                    continue

                # Simple keyword matching
                content_lower = ep.content.lower()
                title_lower = ep.title.lower()

                # Score based on matches
                score = 0.0
                if query_lower in title_lower:
                    score += 2.0
                if query_lower in content_lower:
                    score += 1.0

                # Word-level matching
                content_words = set(content_lower.split())
                word_matches = len(query_words & content_words)
                if word_matches > 0:
                    score += word_matches * 0.5

                # Tag matching
                tag_matches = len(query_words & set([t.lower() for t in ep.tags]))
                if tag_matches > 0:
                    score += tag_matches * 0.3

                if score > 0:
                    # Boost by importance and recency
                    if ep.importance > 0.5:
                        score *= 1.2
                    results.append((ep.id, score))

        results.sort(key=lambda x: x[1], reverse=True)
        return results[:limit]

    def find_similar(
        self, content: str, threshold: float = 0.8
    ) -> List[Tuple[str, float]]:
        """Find similar content using shingle-based Jaccard similarity."""

        def get_shingles(text: str, k: int = 5) -> set:
            words = text.lower().split()
            return set([" ".join(words[i : i + k]) for i in range(len(words) - k + 1)])

        target_shingles = get_shingles(content)
        if not target_shingles:
            return []

        results = []
        for layer in range(4):
            layer_dir = self.layers_path / f"l{layer}"
            if not layer_dir.exists():
                continue
            for fp in layer_dir.glob("*.md"):
                ep = self._parse_file(fp)
                if ep and ep.content:
                    ep_shingles = get_shingles(ep.content)
                    if not ep_shingles:
                        continue
                    intersection = len(target_shingles & ep_shingles)
                    union = len(target_shingles | ep_shingles)
                    similarity = intersection / union if union > 0 else 0
                    if similarity >= threshold:
                        results.append((ep.id, similarity))

        results.sort(key=lambda x: x[1], reverse=True)
        return results

    def get_stats(self) -> Dict[str, Any]:
        """Get store statistics."""
        stats = {"layers": {}, "total": 0, "links": 0}
        for layer in range(4):
            layer_dir = self.layers_path / f"l{layer}"
            count = len(list(layer_dir.glob("*.md"))) if layer_dir.exists() else 0
            stats["layers"][f"l{layer}"] = count
            stats["total"] += count
        stats["hash_cache_size"] = len(self.hash_cache)
        stats["file_states_size"] = len(self.file_states)
        stats["links"] = sum(len(v) for v in self.links.values())
        return stats

