"""Markdown file indexer.

Extracts heading hierarchy, code blocks, links, and section structure.
"""
from pathlib import Path
from typing import Any, Dict, List, Optional
import re

from .indexer_common import IMediaIndexer, IndexResult, compute_file_hash


class MarkdownIndexer(IMediaIndexer):
    """Indexer for Markdown (.md) files.
    
    Extracts:
    - Heading hierarchy (h1-h6)
    - Code blocks with language detection
    - Links and references
    - List structures
    
    Generates:
    - Summary: heading count, main topics
    - Hierarchy: nested heading tree
    - Symbols: each heading as a "symbol" with line numbers
    """
    
    SUPPORTED_EXTENSIONS = {'.md', '.markdown'}
    
    def can_index(self, file_path: Path, content_type: str = None) -> bool:
        return file_path.suffix.lower() in self.SUPPORTED_EXTENSIONS
    
    def index(self, file_path: Path, context: Optional[Dict[str, Any]] = None) -> IndexResult:
        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")
        
        content = file_path.read_text(encoding='utf-8')
        lines = content.split('\n')
        
        # Parse headings and build tree
        hierarchy = self._parse_headings(lines)
        
        # Extract code blocks
        code_blocks = self._extract_code_blocks(content)
        
        # Extract links
        links = self._extract_links(content)
        
        # Build symbols list: treat each heading as a symbol
        symbols = self._build_heading_symbols(hierarchy, lines)
        
        # Summary
        summary = self._generate_summary(hierarchy, code_blocks, links, file_path)
        
        metadata = {
            "format": "markdown",
            "line_count": len(lines),
            "heading_count": len(symbols),
            "code_block_count": len(code_blocks),
            "link_count": len(links),
        }
        
        return IndexResult(
            media_type="markdown",
            file_path=file_path,
            content_hash=compute_file_hash(file_path),
            summary=summary,
            hierarchy=hierarchy,
            symbols=symbols,
            metadata=metadata
        )
    
    def _parse_headings(self, lines: List[str]) -> List[Dict[str, Any]]:
        """Parse markdown headings into hierarchical tree."""
        root = []
        stack = []  # (level, node)
        
        for i, line in enumerate(lines, 1):
            match = re.match(r'^(#{1,6})\s+(.+)$', line)
            if not match:
                continue
            
            level = len(match.group(1))
            title = match.group(2).strip()
            
            node = {
                "title": title,
                "line_start": i,
                "line_end": i,  # will extend to content end
                "level": level,
                "children": [],
                "content_lines": [],
            }
            
            # Pop stack to find parent level
            while stack and stack[-1][0] >= level:
                stack.pop()
            
            if stack:
                parent = stack[-1][1]
                parent["children"].append(node)
            else:
                root.append(node)
            
            stack.append((level, node))
        
        # Set line_end for each node to include content until next heading or EOF
        all_headings = self._flatten_headings(root)
        for idx, node in enumerate(all_headings):
            if idx + 1 < len(all_headings):
                node["line_end"] = all_headings[idx+1]["line_start"] - 1
            else:
                node["line_end"] = len(lines)
        
        return root
    
    def _flatten_headings(self, tree: List[Dict]) -> List[Dict]:
        """Flatten heading tree into ordered list."""
        result = []
        for node in tree:
            result.append(node)
            if node["children"]:
                result.extend(self._flatten_headings(node["children"]))
        return result
    
    def _extract_code_blocks(self, content: str) -> List[Dict[str, Any]]:
        """Extract fenced code blocks."""
        blocks = []
        pattern = r'```(\w*)\n(.*?)```'
        for match in re.finditer(pattern, content, re.DOTALL):
            lang = match.group(1) or ""
            code = match.group(2)
            blocks.append({
                "language": lang,
                "code": code[:200],  # preview
                "full_length": len(code),
            })
        return blocks
    
    def _extract_links(self, content: str) -> List[Dict[str, str]]:
        """Extract markdown links [text](url)."""
        links = []
        pattern = r'\[([^\]]+)\]\(([^)]+)\)'
        for match in re.finditer(pattern, content):
            text, url = match.groups()
            links.append({"text": text, "url": url})
        return links
    
    def _build_heading_symbols(self, hierarchy: List[Dict], lines: List[str]) -> List[Dict]:
        """Convert headings to symbol list."""
        symbols = []
        for node in self._flatten_headings(hierarchy):
            # Extract content snippet
            start, end = node["line_start"], node["line_end"]
            content = "\n".join(lines[start-1:end])[:200]
            
            symbols.append({
                "id": f"h{node['level']}_{node['line_start']}",
                "name": node["title"],
                "type": f"h{node['level']}",
                "line_start": node["line_start"],
                "line_end": node["line_end"],
                "signature": "",  # headings don't have signatures
                "docstring": content,
            })
        return symbols
    
    def _generate_summary(self, hierarchy: List[Dict], code_blocks: List, links: List, file_path: Path) -> str:
        """Generate summary of markdown document."""
        headings = self._flatten_headings(hierarchy)
        
        # Count by level
        level_counts = {}
        for h in headings:
            lvl = h["level"]
            level_counts[lvl] = level_counts.get(lvl, 0) + 1
        
        parts = [f"{file_path.name} is a Markdown document with:"]
        if level_counts.get(1, 0) > 0:
            parts.append(f"{level_counts[1]} top-level heading(s)")
        if len(hierarchy) > 1:
            parts.append(f"{len(hierarchy)} top-level sections")
        if code_blocks:
            parts.append(f"{len(code_blocks)} code block(s)")
        if links:
            parts.append(f"{len(links)} link(s)")
        
        if len(parts) > 1:
            summary = " ".join(parts[1:]) + "."
        else:
            summary = f"{file_path.name} appears empty or contains no recognizable content."
        
        return summary
    
    def extract_episodes(self, index_result: IndexResult) -> List[Dict[str, Any]]:
        """Convert markdown sections into episodes."""
        episodes = []
        timestamp = self._now_iso()
        file_str = str(index_result.file_path)
        
        for symbol in index_result.symbols:
            if symbol["type"].startswith('h') and int(symbol["type"][1:]) <= 3:  # h1-h3 only
                importance = 0.6 if symbol["type"] in ("h1", "h2") else 0.4
                
                episode = {
                    "id": f"md_{index_result.content_hash[:8]}_{symbol['line_start']}",
                    "layer": 0,
                    "timestamp": timestamp,
                    "title": symbol["name"],
                    "content": symbol.get("docstring", ""),
                    "category": "documentation",
                    "tags": ["markdown", symbol["type"]],
                    "importance": importance,
                    "source_type": "markdown",
                    "source_path": file_str,
                    "source_hash": index_result.content_hash,
                    "context_snapshot": {
                        "line_start": symbol["line_start"],
                        "line_end": symbol["line_end"],
                        "heading_level": symbol["type"],
                    },
                }
                episodes.append(episode)
        
        return episodes
    
    @staticmethod
    def _now_iso() -> str:
        from lib.time_utils import now_iso
        return now_iso()
