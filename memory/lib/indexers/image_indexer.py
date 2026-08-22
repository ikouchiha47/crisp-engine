"""Image file indexer.

Extracts basic metadata from image files.
Future: Could integrate vision model (LLaVA, CLIP) for content description.
"""
from pathlib import Path
from typing import Any, Dict, List, Optional

from .indexer_common import IMediaIndexer, IndexResult, compute_file_hash


class ImageIndexer(IMediaIndexer):
    """Indexer for image files (PNG, JPG, JPEG, GIF, WebP).
    
    Extracts:
    - Dimensions (width, height)
    - Format (PNG, JPEG, etc.)
    - Color mode (RGB, RGBA, grayscale)
    - File size
    
    Does NOT yet use vision models (future enhancement).
    """
    
    SUPPORTED_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp', '.tiff', '.ico'}
    
    def can_index(self, file_path: Path, content_type: str = None) -> bool:
        ext = file_path.suffix.lower()
        if ext in self.SUPPORTED_EXTENSIONS:
            return True
        # Also check MIME type if provided
        if content_type and content_type.startswith('image/'):
            return True
        return False
    
    def index(self, file_path: Path, context: Optional[Dict[str, Any]] = None) -> IndexResult:
        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")
        
        try:
            from PIL import Image
        except ImportError:
            # Fallback: just file stats
            return self._index_fallback(file_path)
        
        try:
            with Image.open(file_path) as img:
                width, height = img.size
                mode = img.mode
                format_name = img.format or file_path.suffix.lstrip('.').upper()
        except Exception as e:
            # If PIL can't read, use fallback
            return self._index_fallback(file_path, error=str(e))
        
        file_size = file_path.stat().st_size
        
        # Build hierarchy (single node for now)
        hierarchy = {
            "image": file_path.name,
            "dimensions": {"width": width, "height": height},
            "format": format_name,
            "mode": mode,
        }
        
        summary = f"Image: {file_path.name}, {width}×{height}, {format_name}, {mode}"
        
        metadata = {
            "width": width,
            "height": height,
            "format": format_name,
            "mode": mode,
            "size_bytes": file_size,
        }
        
        return IndexResult(
            media_type="image",
            file_path=file_path,
            content_hash=compute_file_hash(file_path),
            summary=summary,
            hierarchy=hierarchy,
            symbols=[],  # images have no symbols
            metadata=metadata
        )
    
    def _index_fallback(self, file_path: Path, error: str = None) -> IndexResult:
        """Fallback when PIL not available or image corrupt."""
        file_size = file_path.stat().st_size
        summary = f"Image: {file_path.name} (size: {file_size} bytes)"
        if error:
            summary += f" - unreadable: {error}"
        
        return IndexResult(
            media_type="image",
            file_path=file_path,
            content_hash=compute_file_hash(file_path),
            summary=summary,
            hierarchy={"image": file_path.name},
            symbols=[],
            metadata={"size_bytes": file_size, "error": error}
        )
    
    def extract_episodes(self, index_result: IndexResult) -> List[Dict[str, Any]]:
        """Images generate minimal episodes (just the file reference)."""
        # Only create episode if image is reasonably large (>10KB)
        if index_result.metadata.get("size_bytes", 0) < 10_000:
            return []
        
        return [{
            "id": f"img_{index_result.content_hash[:12]}",
            "layer": 0,
            "timestamp": self._now_iso(),
            "title": f"Image: {index_result.file_path.name}",
            "content": index_result.summary,
            "category": "media",
            "tags": ["image", index_result.metadata.get("format", "").lower()],
            "importance": 0.3,
            "source_type": "image",
            "source_path": str(index_result.file_path),
            "source_hash": index_result.content_hash,
            "context_snapshot": index_result.metadata,
        }]
    
    @staticmethod
    def _now_iso() -> str:
        from lib.time_utils import now_iso
        return now_iso()
