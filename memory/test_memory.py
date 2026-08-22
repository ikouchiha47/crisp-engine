#!/usr/bin/env python3
"""Test script for Crisp Engine memory system."""
import sys
import tempfile
import shutil
from pathlib import Path

# Add lib to path
sys.path.insert(0, str(Path(__file__).parent))

from lib.store import MemoryEpisode, MemoryStore
from lib.code_index import CodeAnalyzer
from lib.consolidate import MemoryReflector
from lib.retrieve import RetrievalOrchestrator
from lib.consolidate import PruningService


def test_episode_creation():
    """Test MemoryEpisode creation and serialization."""
    print("Test 1: Episode creation...")
    episode = MemoryEpisode(
        id="test_001",
        session_id="sess_test",
        timestamp="2026-05-01T15:30:00Z",
        title="Test episode",
        content="This is a test memory",
        category="test",
        importance=0.8,
        tags=["test", "memory"],
    )
    
    # Test serialization
    fm = episode.to_frontmatter()
    assert "test_001" in fm
    assert "Test episode" in fm
    print("  ✓ Episode serialization works")
    
    # Test deserialization
    episode2 = MemoryEpisode.from_frontmatter(fm, "This is a test memory")
    assert episode2.id == "test_001"
    assert episode2.title == "Test episode"
    print("  ✓ Episode deserialization works")
    return True


def test_store_operations():
    """Test store CRUD operations."""
    print("\nTest 2: Store operations...")
    
    # Create temp directory
    tmpdir = tempfile.mkdtemp()
    try:
        store = MemoryStore(tmpdir)
        
        # Create episode
        episode = MemoryEpisode(
            id="test_001",
            session_id="sess_test",
            timestamp="2026-05-01T15:30:00Z",
            title="Test episode",
            content="This is a test memory",
            category="test",
            importance=0.8,
            tags=["test"],
        )
        
        # Save
        result = store.save_episode(episode)
        assert result == True
        print("  ✓ Episode saved")
        
        # Read
        retrieved = store.get_episode("test_001")
        assert retrieved is not None
        assert retrieved.title == "Test episode"
        print("  ✓ Episode retrieved")
        
        # List
        episodes = store.list_episodes()
        assert len(episodes) == 1
        print("  ✓ Episode listed")
        
        # Update access
        store.update_access("test_001")
        retrieved = store.get_episode("test_001")
        assert retrieved.access_count == 1
        print("  ✓ Access updated")
        
        # Duplicate detection
        episode2 = MemoryEpisode(
            id="test_002",
            session_id="sess_test",
            timestamp="2026-05-01T15:31:00Z",
            title="Duplicate",
            content="This is a test memory",  # Same content!
            category="test",
        )
        result = store.save_episode(episode2)
        assert result == False  # Should be duplicate
        print("  ✓ Duplicate detection works")
        
        # Delete
        result = store.delete_episode("test_001")
        assert result == True
        retrieved = store.get_episode("test_001")
        assert retrieved is None
        print("  ✓ Episode deleted")
        
        # Stats
        stats = store.get_stats()
        assert "total" in stats
        print("  ✓ Stats retrieved")
        
    finally:
        shutil.rmtree(tmpdir)
    
    return True


def test_file_change_detection():
    """Test file state tracking."""
    print("\nTest 3: File change detection...")
    
    tmpdir = tempfile.mkdtemp()
    try:
        store = MemoryStore(tmpdir)
        
        # Create test file
        test_file = Path(tmpdir) / "test.py"
        test_file.write_text("print('hello')")
        
        # First hash
        hash1 = store.compute_hash("print('hello')")
        store.set_file_state(str(test_file), hash1)
        
        # Check state
        retrieved = store.get_file_state(str(test_file))
        assert retrieved == hash1
        print("  ✓ File state stored")
        
        # Change file
        test_file.write_text("print('world')")
        hash2 = store.compute_hash("print('world')")
        
        # Check different
        retrieved = store.get_file_state(str(test_file))
        assert retrieved == hash1  # Still old hash
        print("  ✓ Change detected")
        
        # Update state
        store.set_file_state(str(test_file), hash2)
        retrieved = store.get_file_state(str(test_file))
        assert retrieved == hash2
        print("  ✓ File state updated")
        
    finally:
        shutil.rmtree(tmpdir)
    
    return True


def test_code_analysis():
    """Test code element extraction."""
    print("\nTest 4: Code analysis...")
    
    tmpdir = tempfile.mkdtemp()
    try:
        # Create test Python file
        test_file = Path(tmpdir) / "test.py"
        test_file.write_text("""
def hello(name):
    \"\"\"Say hello.\"\"\"
    return f"Hello {name}"

class Greeter:
    def __init__(self):
        self.name = "World"
    
    def greet(self):
        return hello(self.name)
""")
        
        analyzer = CodeAnalyzer()
        elements = analyzer.analyze_file(str(test_file))
        
        assert len(elements) >= 2  # At least function and class
        
        func = [e for e in elements if e.type == "function"][0]
        assert func.name == "hello"
        print(f"  ✓ Function extracted: {func.name}")
        
        cls = [e for e in elements if e.type == "class"][0]
        assert cls.name == "Greeter"
        print(f"  ✓ Class extracted: {cls.name}")
        
        # Check hashes
        for elem in elements:
            h = elem.compute_hash()
            assert h is not None and len(h) > 0
        print("  ✓ Hashes computed")
        
    finally:
        shutil.rmtree(tmpdir)
    
    return True


def test_reflection():
    """Test L0 → L1 consolidation."""
    print("\nTest 5: Reflection/consolidation...")
    
    tmpdir = tempfile.mkdtemp()
    try:
        store = MemoryStore(tmpdir)
        
        # Create 20 L0 episodes (same session)
        session_id = "sess_test"
        for i in range(20):
            episode = MemoryEpisode(
                id=f"l0_{i:03d}",
                session_id=session_id,
                timestamp=f"2026-05-01T15:{i:02d}:00Z",
                title=f"Episode {i}",
                content=f"Content for episode {i}",
                category="test",
                importance=0.5,
            )
            store.save_episode(episode)
        
        # Run consolidation
        reflector = MemoryReflector(store)
        result = reflector.consolidate(max_l0_per_batch=20)
        
        assert result["l1_created"] > 0
        print(f"  ✓ Created {result['l1_created']} L1 summaries")
        
        # Check L1 episodes exist
        l1_episodes = store.list_episodes(layer=1)
        assert len(l1_episodes) > 0
        print(f"  ✓ Found {len(l1_episodes)} L1 episodes")
        
    finally:
        shutil.rmtree(tmpdir)
    
    return True


def test_retrieval():
    """Test search and retrieval."""
    print("\nTest 6: Retrieval...")
    
    tmpdir = tempfile.mkdtemp()
    try:
        store = MemoryStore(tmpdir)
        
        # Create episodes with different content
        episodes = [
            ("ep1", "bug in authentication", "bug"),
            ("ep2", "fix for login issue", "fix"),
            ("ep3", "database bug connection", "bug"),
        ]
        
        for eid, content, category in episodes:
            episode = MemoryEpisode(
                id=eid,
                session_id="sess_test",
                timestamp="2026-05-01T15:30:00Z",
                title=content,
                content=content + " details here",
                category=category,
                importance=0.7,
            )
            store.save_episode(episode)
        
        # Search
        orchestrator = RetrievalOrchestrator(store)
        results = orchestrator.search("bug", limit=10)
        
        assert len(results) >= 2  # Should find both bug-related episodes
        print(f"  ✓ Found {len(results)} results for 'bug'")
        
        # Check scores
        for episode, score in results:
            assert score > 0
        print("  ✓ All results have positive scores")
        
    finally:
        shutil.rmtree(tmpdir)
    
    return True


def test_pruning():
    """Test pruning service."""
    print("\nTest 7: Pruning...")
    
    tmpdir = tempfile.mkdtemp()
    try:
        store = MemoryStore(tmpdir)
        
        # Create old episode
        episode = MemoryEpisode(
            id="old_ep",
            session_id="sess_old",
            timestamp="2020-01-01T00:00:00Z",  # Very old
            title="Old episode",
            content="Old content",
            category="test",
            importance=0.1,
            decay_score=0.01,  # Very low decay
        )
        store.save_episode(episode)
        
        # Run pruning
        pruner = PruningService(store)
        result = pruner.prune()
        
        assert "updated_decay" in result
        print(f"  ✓ Pruning completed: {result}")
        
    finally:
        shutil.rmtree(tmpdir)
    
    return True


def test_links():
    """Test A-MEM graph links."""
    print("\nTest 8: Graph links...")
    
    tmpdir = tempfile.mkdtemp()
    try:
        store = MemoryStore(tmpdir)
        
        # Create two episodes
        ep1 = MemoryEpisode(
            id="ep1",
            session_id="sess_test",
            timestamp="2026-05-01T15:30:00Z",
            title="Episode 1",
            content="First",
            category="test",
        )
        ep2 = MemoryEpisode(
            id="ep2",
            session_id="sess_test",
            timestamp="2026-05-01T15:31:00Z",
            title="Episode 2",
            content="Second",
            category="test",
        )
        store.save_episode(ep1)
        store.save_episode(ep2)
        
        # Add link
        store.add_link("ep1", "ep2", "similar", 0.8)
        
        # Retrieve links
        links = store.get_links("ep1")
        assert len(links) == 1
        assert links[0][0] == "ep2"
        assert links[0][1] == "similar"
        print("  ✓ Link created and retrieved")
        
    finally:
        shutil.rmtree(tmpdir)
    
    return True


def main():
    """Run all tests."""
    print("="*60)
    print("Crisp Engine Memory System - Test Suite")
    print("="*60)
    
    tests = [
        test_episode_creation,
        test_store_operations,
        test_file_change_detection,
        test_code_analysis,
        test_reflection,
        test_retrieval,
        test_pruning,
        test_links,
    ]
    
    passed = 0
    failed = 0
    
    for test in tests:
        try:
            if test():
                passed += 1
        except Exception as e:
            print(f"  ✗ FAILED: {e}")
            import traceback
            traceback.print_exc()
            failed += 1
    
    print("\n" + "="*60)
    print(f"Results: {passed} passed, {failed} failed")
    print("="*60)
    
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
