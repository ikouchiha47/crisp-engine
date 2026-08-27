"""Single persistence path for every hook collaborator: embed then save.

Extracted from what used to be MemoryHookHandler._save/_embed, duplicated in
spirit across every handler — now every collaborator shares one instance.
"""
from lib.bus import emit as _bus_emit, EmbedResult, EpisodeSaved
from lib.log import get_logger as _get_logger
from lib.store import MemoryEpisode, MemoryStore

_log = _get_logger("hooks")


class EpisodeWriter:
    """Embeds and saves episodes; tracks project_root for bus event context."""

    def __init__(self, store: MemoryStore):
        self.store = store
        self._embed_provider = None  # lazy: built on first episode save
        self.project_root = ""  # set by collaborators once resolved

    def set_project_root(self, root: str) -> None:
        if root:
            self.project_root = root

    def save(self, episode: MemoryEpisode) -> bool:
        """Embed then save — single call site so embed is never forgotten."""
        self.embed(episode)
        ok = self.store.save_episode(episode)
        _log.info(
            "saved episode %s layer=%d cat=%s importance=%.2f embedded=%s",
            episode.id, episode.layer, episode.category, episode.importance,
            bool(episode.embedding),
            extra={"session_id": episode.session_id, "project": "-"},
        )
        try:
            _bus_emit(EpisodeSaved(
                session_id=episode.session_id,
                project=self.project_root or "-",
                id=episode.id,
                layer=episode.layer,
                category=episode.category,
                importance=round(episode.importance, 3),
                embedded=bool(episode.embedding),
            ))
        except Exception:
            pass
        return ok

    def embed(self, episode: MemoryEpisode) -> None:
        """Attach embedding to episode in-place before saving.

        Uses the store's configured embedding_provider. Skips silently on any
        error so a missing Ollama / uninstalled package never breaks a hook.
        """
        if episode.embedding:
            return  # already embedded
        try:
            if self._embed_provider is None:
                from lib.embeddings import get_provider
                from lib import config as _cfg
                merged = _cfg.load()
                merged.update(self.store.config)
                self._embed_provider = get_provider(merged)
                _log.info(
                    "embedding provider initialised: %s",
                    merged.get("embedding_provider", "?"),
                    extra={"session_id": episode.session_id, "project": "-"},
                )
            text = f"{episode.title}\n{episode.content}".strip()
            if text:
                try:
                    episode.embedding = self._embed_provider.embed(text)
                    try:
                        _bus_emit(EmbedResult(
                            session_id=episode.session_id,
                            project=self.project_root or "-",
                            episode_id=episode.id,
                            provider=type(self._embed_provider).__name__,
                            success=True, fallback_used=False,
                        ))
                    except Exception:
                        pass
                except Exception as per_call_exc:
                    # Per-call failure (e.g. Ollama 500): fall back to HF or word2vec.
                    _log.warning(
                        "embed per-call failure for %s (%s), trying fallback",
                        episode.id, per_call_exc,
                        extra={"session_id": episode.session_id, "project": "-"},
                    )
                    from lib.embeddings import _hf_then_w2v
                    from lib import config as _cfg
                    merged = _cfg.load()
                    merged.update(self.store.config)
                    try:
                        fallback = _hf_then_w2v(merged)
                        episode.embedding = fallback.embed(text)
                        self._embed_provider = fallback  # promote so next call uses it
                        try:
                            _bus_emit(EmbedResult(
                                session_id=episode.session_id,
                                project=self.project_root or "-",
                                episode_id=episode.id,
                                provider=type(fallback).__name__,
                                success=True, fallback_used=True,
                            ))
                        except Exception:
                            pass
                    except Exception as fb_exc:
                        _log.warning(
                            "embed fallback also failed for %s: %s", episode.id, fb_exc,
                            extra={"session_id": episode.session_id, "project": "-"},
                        )
        except Exception as exc:
            _log.warning(
                "embed failed for %s: %s", episode.id, exc,
                extra={"session_id": episode.session_id, "project": "-"},
            )
