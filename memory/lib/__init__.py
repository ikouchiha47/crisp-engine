"""Memory system core library."""
__version__ = "1.0.0"

from .store import MemoryEpisode, MemoryStore, ProjectMemoryManager, get_memory_store
from .code_index import CodeAnalyzer, CodeElement
from .consolidate import MemoryReflector, PruningService
from .retrieve import RetrievalOrchestrator
from .hooks import MemoryHookHandler

__all__ = [
    "MemoryEpisode",
    "MemoryStore",
    "CodeAnalyzer",
    "CodeElement",
    "MemoryReflector",
    "RetrievalOrchestrator",
    "PruningService",
    "MemoryHookHandler",
    "ProjectMemoryManager",
    "get_memory_store",
]
