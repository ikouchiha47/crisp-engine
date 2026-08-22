"""Audio file indexer.

Extracts basic metadata from audio files.
Future: Could integrate whisper.cpp or similar for transcription.
"""
from pathlib import Path
from typing import Any, Dict, List, Optional

from .indexer_common import IMediaIndexer, IndexResult, compute_file_hash


class AudioIndexer(IMediaIndexer):
    """Indexer for audio files (MP3, WAV, M4A, OGG).
    
    Extracts:
    - Duration (if available)
    - Format/container
    - Sample rate, channels (if available)
    - File size
    
    Does NOT yet transcribe (future enhancement).
    """
    
    SUPPORTED_EXTENSIONS = {'.mp3', '.wav', '.m4a', '.ogg', '.flac', '.aac'}
    
    def can_index(self, file_path: Path, content_type: str = None) -> bool:
        ext = file_path.suffix.lower()
        if ext in self.SUPPORTED_EXTENSIONS:
            return True
        if content_type and content_type.startswith('audio/'):
            return True
        return False
    
    def index(self, file_path: Path, context: Optional[Dict[str, Any]] = None) -> IndexResult:
        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")
        
        file_size = file_path.stat().st_size
        format_name = file_path.suffix.lstrip('.').upper()
        
        # Try to get duration using mutagen if available
        duration = None
        try:
            from mutagen import File as MutagenFile
            audio = MutagenFile(str(file_path))
            if audio and hasattr(audio, 'info') and hasattr(audio.info, 'length'):
                duration = audio.info.length
        except ImportError:
            pass
        except Exception:
            pass
        
        hierarchy = {
            "audio": file_path.name,
            "format": format_name,
            "size_bytes": file_size,
        }
        
        if duration is not None:
            hierarchy["duration_seconds"] = round(duration, 2)
            summary = f"Audio: {file_path.name}, {format_name}, duration {duration:.1f}s"
        else:
            summary = f"Audio: {file_path.name}, {format_name} (duration unknown)"
        
        metadata = {
            "format": format_name,
            "size_bytes": file_size,
            "duration_seconds": duration,
        }
        
        return IndexResult(
            media_type="audio",
            file_path=file_path,
            content_hash=compute_file_hash(file_path),
            summary=summary,
            hierarchy=hierarchy,
            symbols=[],
            metadata={k: v for k, v in metadata.items() if v is not None}
        )
    
    def extract_episodes(self, index_result: IndexResult) -> List[Dict[str, Any]]:
        """Audio files don't produce episodic content yet (no transcription)."""
        # Only create episode if we have duration (indicates it's a proper audio file)
        if index_result.metadata.get("duration_seconds", 0) < 5:
            return []  # Skip very short audio
        
        return [{
            "id": f"aud_{index_result.content_hash[:12]}",
            "layer": 0,
            "timestamp": self._now_iso(),
            "title": f"Audio: {index_result.file_path.name}",
            "content": index_result.summary,
            "category": "media",
            "tags": ["audio", index_result.metadata.get("format", "").lower()],
            "importance": 0.2,
            "source_type": "audio",
            "source_path": str(index_result.file_path),
            "source_hash": index_result.content_hash,
            "context_snapshot": index_result.metadata,
        }]
    
    @staticmethod
    def _now_iso() -> str:
        from lib.time_utils import now_iso
        return now_iso()
