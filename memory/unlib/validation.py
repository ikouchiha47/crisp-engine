"""Validation utilities for Crisp Engine.

Ensures data integrity, validates inputs, and provides
defensive programming helpers.
"""
import re
import os
from pathlib import Path
from typing import Any, Dict, Optional, List, Tuple
from datetime import datetime, timezone


class ValidationError(Exception):
    """Raised when validation fails."""
    pass


class Validator:
    """Static validation helpers."""
    
    @staticmethod
    def validate_episode_data(data: Dict[str, Any]) -> bool:
        """Validate episode data before storage."""
        required = ["id", "session_id", "timestamp", "content"]
        for field in required:
            if field not in data:
                raise ValidationError(f"Missing required field: {field}")
        
        # Validate ID format
        if not re.match(r'^[a-zA-Z0-9_-]+$', data['id']):
            raise ValidationError(f"Invalid episode ID: {data['id']}")
        
        # Validate timestamp
        try:
            datetime.fromisoformat(data['timestamp'].replace('Z', '+00:00'))
        except (ValueError, AttributeError):
            raise ValidationError(f"Invalid timestamp: {data['timestamp']}")
        
        # Validate importance range
        if 'importance' in data:
            imp = data['importance']
            if not (0.0 <= imp <= 1.0):
                raise ValidationError(f"Importance must be 0-1, got {imp}")
        
        # Validate layer
        if 'layer' in data:
            layer = data['layer']
            if layer not in [0, 1, 2, 3]:
                raise ValidationError(f"Layer must be 0-3, got {layer}")
        
        return True
    
    @staticmethod
    def validate_file_path(file_path: str) -> bool:
        """Validate file path for security (no traversal attacks)."""
        path = Path(file_path).resolve()
        
        # Check for suspicious patterns
        suspicious = ['..', '~', '$', '|', ';', '`']
        for pattern in suspicious:
            if pattern in file_path:
                raise ValidationError(f"Suspicious path pattern: {pattern}")
        
        # Ensure path doesn't escape typical sandbox (if needed)
        # For now, just ensure it's absolute and exists or parent exists
        if not path.is_absolute():
            raise ValidationError(f"Path must be absolute: {file_path}")
        
        return True
    
    @staticmethod
    def validate_content_hash(hash_str: str) -> bool:
        """Validate SHA256 hash format."""
        if not re.match(r'^[a-f0-9]{64}$', hash_str):
            raise ValidationError(f"Invalid SHA256 hash: {hash_str}")
        return True
    
    @staticmethod
    def validate_tags(tags: List[str]) -> bool:
        """Validate tag list."""
        for tag in tags:
            if len(tag) > 50:
                raise ValidationError(f"Tag too long (max 50): {tag}")
            if not re.match(r'^[a-zA-Z0-9_-]+$', tag):
                raise ValidationError(f"Invalid tag format: {tag}")
        return True


class IntegrityChecker:
    """Check integrity of memory store."""
    
    def __init__(self, store):
        self.store = store
        self.errors = []
        self.warnings = []
    
    def run_all_checks(self) -> Dict[str, Any]:
        """Run all integrity checks."""
        self.errors = []
        self.warnings = []
        
        self._check_episode_consistency()
        self._check_hash_cache()
        self._check_file_states()
        self._check_links()
        self._check_parent_links()
        self._check_layer_progression()
        self._check_duplicate_content()
        
        return {
            "valid": len(self.errors) == 0,
            "errors": self.errors,
            "warnings": self.warnings,
            "total_errors": len(self.errors),
            "total_warnings": len(self.warnings),
        }
    
    def _check_episode_consistency(self):
        """Check all episodes can be parsed."""
        for layer in range(4):
            layer_dir = self.store.layers_path / f"l{layer}"
            if not layer_dir.exists():
                continue
            for fp in layer_dir.glob("*.md"):
                ep = self.store._parse_file(fp)
                if ep is None:
                    self.errors.append(f"Cannot parse episode: {fp.name}")
                elif ep.id != fp.stem:
                    self.warnings.append(f"ID mismatch: {fp.stem} vs {ep.id}")
    
    def _check_hash_cache(self):
        """Verify hash_cache entries are valid."""
        for content_hash, ep_id in self.store.hash_cache.items():
            if not Validator.validate_content_hash(content_hash):
                self.errors.append(f"Invalid hash in cache: {content_hash}")
            
            # Verify episode exists
            ep = self.store.get_episode(ep_id)
            if ep is None:
                self.errors.append(f"Hash cache points to missing episode: {ep_id}")
    
    def _check_file_states(self):
        """Verify file state tracking is consistent."""
        for path_hash, content_hash in self.store.file_states.items():
            if not re.match(r'^[a-f0-9]{16}$', path_hash):
                self.warnings.append(f"Suspicious path hash: {path_hash}")
            
            if not Validator.validate_content_hash(content_hash):
                self.errors.append(f"Invalid content hash for {path_hash}: {content_hash}")
    
    def _check_links(self):
        """Verify A-MEM graph links are valid."""
        for ep_id, links in self.store.links.items():
            # Verify source exists
            source = self.store.get_episode(ep_id)
            if source is None:
                self.errors.append(f"Link from missing episode: {ep_id}")
                continue
            
            for link in links:
                target_id = link.get("target_id") if isinstance(link, dict) else link[0]
                target = self.store.get_episode(target_id)
                if target is None:
                    self.errors.append(f"Link to missing episode: {target_id}")
    
    def _check_parent_links(self):
        """Verify parent_id references are valid."""
        for layer in range(4):
            for ep in self.store.list_episodes(layer=layer):
                if ep.parent_id:
                    parent = self.store.get_episode(ep.parent_id)
                    if parent is None:
                        self.errors.append(f"Orphan parent link: {ep.id} → {ep.parent_id}")
    
    def _check_layer_progression(self):
        """Check that layer order makes sense."""
        # L3 should be permanent
        for ep in self.store.list_episodes(layer=3):
            if not ep.is_permanent:
                self.warnings.append(f"L3 episode not marked permanent: {ep.id}")
        
        # L0 should have no children
        for ep in self.store.list_episodes(layer=0):
            if ep.parent_id:
                # OK - L0 can have L1 parent
                parent = self.store.get_episode(ep.parent_id)
                if parent and parent.layer != 1:
                    self.errors.append(f"L0 episode parent is not L1: {ep.id} → {ep.parent_id} (L{parent.layer})")
    
    def _check_duplicate_content(self):
        """Find episodes with identical content (should be deduped)."""
        content_map = {}
        for layer in range(4):
            for ep in self.store.list_episodes(layer=layer):
                if ep.content:
                    if ep.content in content_map:
                        self.errors.append(
                            f"Duplicate content detected: {ep.id} and {content_map[ep.content]['id']}"
                        )
                    else:
                        content_map[ep.content] = {"id": ep.id, "layer": ep.layer}


def run_integrity_check(base_path: str = None) -> Dict[str, Any]:
    """Run integrity check on memory store.
    
    Args:
        base_path: Path to memory store (default: ~/.claude/memory)
    
    Returns:
        Dict with check results
    """
    from .store import MemoryStore
    
    if base_path is None:
        base_path = str(Path.home() / ".claude" / "memory")
    
    store = MemoryStore(base_path)
    checker = IntegrityChecker(store)
    return checker.run_all_checks()
