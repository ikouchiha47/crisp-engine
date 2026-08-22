"""Storage: episode identity, dedup, atomic persistence, per-project resolution.

Re-exports everything `lib.store` used to expose as a flat module, so every
existing `from lib.store import X` / `from .store import X` site elsewhere in
the codebase keeps working unchanged — this package split is an internal
reorganization, not a public API change.
"""

from .memory_store import IMemoryStore, MemoryEpisode, MemoryStore, is_code_index_category
from .project_memory import ProjectMemoryManager, get_memory_store
from .sqlite_store import SQLiteVecStore


def get_store(config: dict, base_path: str) -> "IMemoryStore":
    """Factory: return MDFileStore or SQLiteVecStore depending on config.

    config keys:
        store_backend : "md" (default) | "sqlite"
        db_path       : path to SQLite file (default: <base_path>/memory.db)
        embedding_dim : int (default: 384)
    """
    backend = str(config.get("store_backend") or "md").lower()
    if backend == "sqlite":
        db_path = config.get("db_path") or f"{base_path}/memory.db"
        dim = int(config.get("embedding_dim", 1024))
        return SQLiteVecStore(db_path=db_path, embedding_dim=dim)
    return MemoryStore(base_path=base_path)


__all__ = [
    "IMemoryStore",
    "MemoryEpisode",
    "MemoryStore",
    "SQLiteVecStore",
    "is_code_index_category",
    "ProjectMemoryManager",
    "get_memory_store",
    "get_store",
]
