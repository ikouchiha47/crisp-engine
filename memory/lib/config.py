"""Global Crisp configuration loader.

Precedence (highest → lowest):
  1. CLI flags passed in via override dict
  2. Environment variables  (CRISP_EMBEDDING_PROVIDER, CRISP_EMBEDDING_MODEL, …)
  3. ~/.config/crisp/config.json  (user-level, shared across all projects)
  4. <project>/.crisp.json        (project-local, optional)
  5. Project MemoryStore config   (config/config.json inside the store dir)

The result is a flat dict suitable for passing to get_provider() and SQLiteVecStore.
Call load() once at CLI startup, then pass the merged config where needed.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

# ── Canonical env-var names ──────────────────────────────────────────────────
_ENV_MAP = {
    "CRISP_EMBEDDING_PROVIDER": "embedding_provider",
    "CRISP_EMBEDDING_MODEL":    "embedding_model",
    "CRISP_EMBEDDING_URL":      "embedding_api_url",
    "CRISP_EMBEDDING_DIM":      "embedding_dim",
    "CRISP_STORE_BACKEND":      "store_backend",
    "CRISP_DB_PATH":            "db_path",
    "CRISP_CONSOLIDATE_L2L3":  "consolidation_l2l3_auto",
    "CRISP_GENERATE_PROVIDER":    "generate_provider",
    "CRISP_GENERATE_MODEL":       "generate_model",
    "CRISP_GENERATE_API_URL":     "generate_api_url",
    "CRISP_GENERATE_THINK":       "generate_think",
    "CRISP_GENERATE_TEMPERATURE": "generate_temperature",
    "CRISP_GENERATE_TOP_P":       "generate_top_p",
    "CRISP_GENERATE_TOP_K":       "generate_top_k",
    "CRISP_GENERATE_TIMEOUT":     "generate_timeout",
}

# ── Default global config path ───────────────────────────────────────────────
GLOBAL_CONFIG_PATH = Path.home() / ".config" / "crisp" / "config.json"
PROJECT_CONFIG_NAME = ".crisp.json"


def _load_json(path: Path) -> Dict[str, Any]:
    if path.exists():
        try:
            return json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _load_dotenv(path: Path) -> Dict[str, str]:
    """Minimal .env parser — KEY=value, ignores comments and blank lines."""
    result: Dict[str, str] = {}
    if not path.exists():
        return result
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        result[key.strip()] = val.strip().strip('"').strip("'")
    return result


def load(
    cwd: Optional[str] = None,
    overrides: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Return merged config dict.  overrides = CLI flags (highest precedence)."""
    merged: Dict[str, Any] = {}

    # 1. Global config file
    merged.update(_load_json(GLOBAL_CONFIG_PATH))

    # 2. Project-local .crisp.json (walk up from cwd)
    if cwd:
        here = Path(cwd).resolve()
        for parent in [here, *here.parents]:
            candidate = parent / PROJECT_CONFIG_NAME
            if candidate.exists():
                merged.update(_load_json(candidate))
                break
            if (parent / ".git").exists():
                # Reached repo root without finding .crisp.json — stop
                break

    # 3. .env file in cwd (if present) — only CRISP_* vars
    if cwd:
        env_vars = _load_dotenv(Path(cwd) / ".env")
        for env_key, cfg_key in _ENV_MAP.items():
            if env_key in env_vars:
                merged[cfg_key] = env_vars[env_key]

    # 4. Shell environment variables
    for env_key, cfg_key in _ENV_MAP.items():
        val = os.environ.get(env_key)
        if val:
            merged[cfg_key] = val

    # 5. CLI overrides (highest precedence)
    if overrides:
        merged.update({k: v for k, v in overrides.items() if v is not None})

    # Coerce embedding_dim to int if present
    if "embedding_dim" in merged:
        try:
            merged["embedding_dim"] = int(merged["embedding_dim"])
        except (ValueError, TypeError):
            pass

    # Coerce generate_* numeric/bool config values — these arrive as strings
    # from env vars / .crisp.json and must reach lib/generate.py typed
    # correctly (a truthy string "false" must not coerce to Python True).
    if "generate_think" in merged:
        merged["generate_think"] = _coerce_bool(merged["generate_think"])
    for key in ("generate_temperature", "generate_top_p", "generate_timeout"):
        if key in merged:
            try:
                merged[key] = float(merged[key])
            except (ValueError, TypeError):
                pass
    if "generate_top_k" in merged:
        try:
            merged["generate_top_k"] = int(merged["generate_top_k"])
        except (ValueError, TypeError):
            pass

    return merged


def _coerce_bool(val: Any) -> bool:
    if isinstance(val, bool):
        return val
    return str(val).strip().lower() in ("1", "true", "yes", "on")


def write_global(updates: Dict[str, Any]) -> None:
    """Persist updates into ~/.config/crisp/config.json."""
    GLOBAL_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    existing = _load_json(GLOBAL_CONFIG_PATH)
    existing.update(updates)
    GLOBAL_CONFIG_PATH.write_text(json.dumps(existing, indent=2))


def write_project(cwd: str, updates: Dict[str, Any]) -> None:
    """Persist updates into <cwd>/.crisp.json."""
    path = Path(cwd) / PROJECT_CONFIG_NAME
    existing = _load_json(path)
    existing.update(updates)
    path.write_text(json.dumps(existing, indent=2))
