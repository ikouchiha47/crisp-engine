"""Generic text file indexer.

Fallback indexer for any readable UTF-8 text file that more specific
indexers don't handle. Provides basic structure detection (paragraphs,
lists, potential code blocks).
"""
from pathlib import Path
from typing import Any, Dict, List, Optional
import re

from .indexer_common import IMediaIndexer, IndexResult, compute_file_hash, is_text_file


class TextIndexer(IMediaIndexer):
    """Fallback indexer for plain text files.
    
    Handles: .txt, .json, .xml, .csv, .log, .conf, .ini, .sh, .bash, .zsh, etc.
    
    Extracts:
    - Line count, size
    - Paragraph structure (blank-line separated)
    - Potential code blocks (indented or fenced)
    - First line as title candidate
    
    Generates:
    - Summary: file size, line count, structure hints
    - Hierarchy: flat list of paragraphs/sections
    - Symbols: none (no semantic structure)
    """
    
    def can_index(self, file_path: Path, content_type: str = None) -> bool:
        # Check if it's readable text
        if file_path.suffix in {'.txt', '.json', '.xml', '.csv', '.log', '.conf', '.ini', 
                               '.sh', '.bash', '.zsh', '.fish', '.ps1', '.bat', '.cmd',
                               '.yaml', '.yml', '.toml', '.cfg', '.config'}:
            return True
        # Fallback: try to read as UTF-8
        return is_text_file(file_path)
    
    def index(self, file_path: Path, context: Optional[Dict[str, Any]] = None) -> IndexResult:
        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")
        
        try:
            content = file_path.read_text(encoding='utf-8')
        except UnicodeDecodeError as e:
            raise ValueError(f"Cannot decode as UTF-8: {e}")
        
        lines = content.split('\n')
        
        # Basic structure detection
        paragraphs = self._split_paragraphs(lines)
        first_line = lines[0] if lines else ""
        
        # Heuristic: is it code-like?
        is_code_like = self._detect_code_like(content, file_path)
        
        # Build hierarchy (flat for text)
        hierarchy = {
            "type": "text",
            "first_line": first_line[:100],
            "paragraph_count": len(paragraphs),
            "is_code_like": is_code_like,
        }
        
        # Generate summary
        summary = self._generate_summary(file_path, len(lines), len(paragraphs), is_code_like)
        
        metadata = {
            "line_count": len(lines),
            "char_count": len(content),
            "paragraph_count": len(paragraphs),
            "is_code_like": is_code_like,
            "encoding": "utf-8",
        }
        
        return IndexResult(
            media_type="text",
            file_path=file_path,
            content_hash=compute_file_hash(file_path),
            summary=summary,
            hierarchy=hierarchy,
            symbols=[],  # no symbols
            metadata=metadata
        )
    
    def _split_paragraphs(self, lines: List[str]) -> List[Dict[str, Any]]:
        """Split text into paragraphs (separated by blank lines)."""
        paragraphs = []
        current = []
        start_line = 1
        
        for i, line in enumerate(lines, 1):
            if line.strip() == "":
                if current:
                    paragraphs.append({
                        "start_line": start_line,
                        "end_line": i - 1,
                        "text": "\n".join(current)[:200],
                    })
                    current = []
                start_line = i + 1
            else:
                current.append(line)
        
        if current:
            paragraphs.append({
                "start_line": start_line,
                "end_line": len(lines),
                "text": "\n".join(current)[:200],
            })
        
        return paragraphs
    
    def _detect_code_like(self, content: str, file_path: Path) -> bool:
        """Heuristic to detect if text file is actually code."""
        # Check extension first
        code_extensions = {'.sh', '.bash', '.zsh', '.fish', '.ps1', '.bat', '.cmd',
                          '.py', '.js', '.ts', '.java', '.go', '.rs', '.c', '.cpp', '.h'}
        if file_path.suffix in code_extensions:
            return True
        
        # Check for common code patterns
        patterns = [
            r'^\s*(def|class|function|var|let|const|if|for|while)\s+',
            r'^\s*#!/bin/(bash|sh|zsh|fish|python)',
            r'^\s*(import|export|from)\s+',
            r'^\s*#include\s+',
            r'^\s*(public|private|static)\s+',
        ]
        
        lines = content.split('\n')[:50]  # check first 50 lines
        code_line_count = 0
        for line in lines:
            for pattern in patterns:
                if re.match(pattern, line):
                    code_line_count += 1
                    break
        
        return code_line_count >= 3  # at least 3 code-like lines
    
    def _generate_summary(self, file_path: Path, line_count: int, paragraph_count: int, is_code_like: bool) -> str:
        """Generate summary for text file."""
        type_desc = "code-like script" if is_code_like else "plain text"
        summary = f"{file_path.name}: {type_desc} with {line_count} lines"
        if paragraph_count > 0:
            summary += f", {paragraph_count} paragraphs"
        return summary + "."
    
    def extract_episodes(self, index_result: IndexResult) -> List[Dict[str, Any]]:
        """Text files don't typically generate episodes unless they contain important info."""
        # Only generate episodes for code-like files or important files
        if index_result.metadata.get("is_code_like"):
            # Treat entire file as one episode with the content
            return [{
                "id": f"txt_{index_result.content_hash[:12]}",
                "layer": 0,
                "timestamp": self._now_iso(),
                "title": f"Script: {index_result.file_path.name}",
                "content": f"# {index_result.file_path.name}\n\nFirst line: {index_result.hierarchy.get('first_line', '')}\n\nFull file content not stored in episode (use cached file).",
                "category": "code" if index_result.metadata.get("is_code_like") else "documentation",
                "tags": ["text", index_result.file_path.suffix.lstrip('.')],
                "importance": 0.4,
                "source_type": "text",
                "source_path": str(index_result.file_path),
                "source_hash": index_result.content_hash,
                "context_snapshot": {
                    "line_count": index_result.metadata.get("line_count", 0),
                    "is_code_like": index_result.metadata.get("is_code_like", False),
                },
            }]
        return []
    
    @staticmethod
    def _now_iso() -> str:
        from lib.time_utils import now_iso
        return now_iso()
