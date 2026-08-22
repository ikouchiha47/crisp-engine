"""Project-aware memory store management.

Handles per-project memory isolation via directory namespaces.
Detects project root automatically (git, package markers, etc.).
"""
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .memory_store import MemoryStore


class ProjectMemoryManager:
    """Manages memory stores per project (directory-based namespace).

    Uses hashed paths for privacy, but isolates episodes by project root.
    Episodes from different projects are stored in separate layer directories.
    """

    def __init__(self, global_base: str = None):
        """
        Args:
            global_base: Base path for global memory (default: ~/.claude/memory)
        """
        self.global_base = Path(
            global_base or Path.home() / ".claude" / "memory"
        ).resolve()
        self.projects_base = self.global_base / "projects"
        self.projects_base.mkdir(parents=True, exist_ok=True)

    def get_store_for_path(self, file_path: str) -> MemoryStore:
        """Get memory store for a given file path (auto-detects project).

        Args:
            file_path: Absolute or relative file path

        Returns:
            MemoryStore scoped to that project's memory directory
        """
        file_path = Path(file_path).resolve()
        project_root = self._detect_project_root(file_path)

        if project_root:
            # Per-project store
            project_id = self._project_id(project_root)
            project_dir = self.projects_base / project_id
            store = MemoryStore(str(project_dir))
            
            # Create project metadata
            self._ensure_project_metadata(project_dir, project_root)
            return store
        else:
            # Fall back to global store
            return MemoryStore(str(self.global_base))

    def get_store_for_project(self, project_root: str) -> MemoryStore:
        """Get memory store for a project root directory."""
        project_root = Path(project_root).resolve()
        project_id = self._project_id(project_root)
        project_dir = self.projects_base / project_id
        store = MemoryStore(str(project_dir))
        self._ensure_project_metadata(project_dir, project_root)
        return store
    
    def get_store_for_project_id(self, project_id: str) -> Optional[MemoryStore]:
        """Get memory store for a project ID (from projects list)."""
        project_dir = self.projects_base / project_id
        if not project_dir.exists():
            return None
        
        meta_file = project_dir / "project.json"
        if meta_file.exists():
            with open(meta_file) as f:
                meta = json.load(f)
            # Return store - it's already at the correct location
            return MemoryStore(str(project_dir))
        
        return None

    def get_global_store(self) -> MemoryStore:
        """Get global memory store (all projects combined)."""
        return MemoryStore(str(self.global_base))

    def list_projects(self) -> list:
        """List all projects with memory stores."""
        projects = []
        if self.projects_base.exists():
            for pdir in self.projects_base.iterdir():
                if pdir.is_dir():
                    # Read project metadata
                    meta_file = pdir / "project.json"
                    if meta_file.exists():
                        import json

                        with open(meta_file) as f:
                            meta = json.load(f)
                        projects.append(
                            {
                                "project_id": pdir.name,
                                "root": meta.get("root", ""),
                                "name": meta.get("name", pdir.name),
                                "created_at": meta.get("created_at", ""),
                                "episode_count": self._count_episodes(pdir),
                            }
                        )
        return projects

    def _detect_project_root(self, path: Path) -> Optional[Path]:
        """Detect project root for a path."""
        # Check current and all parent directories
        for parent in [path] + list(path.parents):
            # Git root
            if (parent / ".git").exists():
                return parent

            # Project markers (in order of preference)
            markers = [
                "package.json",  # Node.js
                "Cargo.toml",  # Rust
                "go.mod",  # Go
                "pyproject.toml",  # Python
                "pom.xml",  # Java Maven
                "build.gradle",  # Java Gradle
                ".claude/",  # Claude Code project
                "Makefile",  # C/C++
                "CMakeLists.txt",  # C/C++
                ".csproj",  # .NET
                ".sln",  # .NET solution
                "Gemfile",  # Ruby
                "composer.json",  # PHP
            ]
            for marker in markers:
                if (parent / marker).exists():
                    return parent

        return None

    def _project_id(self, project_root: Path) -> str:
        """Generate stable, privacy-preserving project ID."""
        # Hash the absolute path to get a stable ID
        path_str = str(project_root.resolve())
        h = hashlib.sha256(path_str.encode()).hexdigest()[:12]
        return f"proj_{h}"

    def _count_episodes(self, project_dir: Path) -> int:
        """Count total episodes across all layers."""
        count = 0
        for layer in range(4):
            layer_dir = project_dir / "layers" / f"l{layer}"
            if layer_dir.exists():
                count += len(list(layer_dir.glob("*.md")))
        return count
    
    def _ensure_project_metadata(self, project_dir: Path, project_root: Path):
        """Create/update project metadata file."""
        project_dir.mkdir(parents=True, exist_ok=True)
        meta_file = project_dir / "project.json"

        is_new = not meta_file.exists()
        if is_new:
            from lib.log import get_logger
            get_logger("store").info(
                "new project registered: %s → %s",
                project_root, project_dir.name,
                extra={"session_id": "-", "project": str(project_root)},
            )
            meta = {}
        else:
            with open(meta_file) as f:
                meta = json.load(f)
        
        meta.update({
            "project_id": project_dir.name,
            "root": str(project_root),
            "name": project_root.name,
            "created_at": meta.get("created_at", datetime.now(timezone.utc).isoformat()),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        })
        
        with open(meta_file, "w") as f:
            json.dump(meta, f, indent=2)


# Convenience function for getting store from current context
def get_memory_store(cwd: str = None) -> MemoryStore:
    """Get MemoryStore for current working directory.
    
    Auto-detects project root and returns appropriate store.
    """
    cwd = Path(cwd or Path.cwd()).resolve()
    manager = ProjectMemoryManager()
    return manager.get_store_for_path(str(cwd))

