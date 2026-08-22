"""Tree-based hierarchical index for Crisp Engine (PageIndex-style).

Integrates hierarchical tree navigation with layered episodic memory.
Each node can have children, forming a document/memory tree.
"""

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


@dataclass
class IndexNode:
    """A node in the memory tree (PageIndex-inspired).

    Mirrors PageIndex node structure:
    {
      "title": "...",
      "node_id": "...",
      "start_index": 1,
      "end_index": 25,
      "summary": "...",
      "nodes": [...]  # children
    }
    """

    node_id: str
    title: str
    layer: int  # 0=L0, 1=L1, 2=L2, 3=L3

    # Tree structure
    parent_id: Optional[str] = None
    children: List[str] = field(default_factory=list)

    # Position in document/sequence
    start_index: int = 0  # Starting position (e.g., first episode index)
    end_index: int = 0  # Ending position (e.g., last child index)

    # Content
    summary: str = ""
    content: str = ""  # Full text or reference to episodes

    # Metadata
    created_at: str = ""
    updated_at: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    # Link to Crisp Engine episodes
    episode_ids: List[str] = field(default_factory=list)

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat()
        if not self.updated_at:
            self.updated_at = self.created_at


class TreeIndex:
    """Hierarchical tree index for memory (PageIndex-style).

    Organizes episodes into a navigable tree structure:

    Root
    ├── Arc (L3)
    │   ├── Cluster (L2)
    │   │   ├── Summary (L1)
    │   │   │   ├── Episode (L0)
    │   │   │   └── Episode (L0)
    │   │   └── Summary (L1)
    │   └── Cluster (L2)
    └── Arc (L3)

    Enables:
    - Tree traversal (depth-first, breadth-first)
    - Reasoning over hierarchy
    - Context assembly by subtree extraction
    - PageIndex-style start_index/end_index for slicing
    """

    def __init__(self):
        self.nodes: Dict[str, IndexNode] = {}
        self.root_id: Optional[str] = None

    def add_node(self, node: IndexNode) -> None:
        """Add a node to the tree."""
        self.nodes[node.node_id] = node

        # Set as root if first node
        if not self.root_id:
            self.root_id = node.node_id

    def add_child(self, parent_id: str, child_id: str) -> None:
        """Add child node to parent."""
        if parent_id in self.nodes and child_id in self.nodes:
            parent = self.nodes[parent_id]
            child = self.nodes[child_id]
            child.parent_id = parent_id
            if child_id not in parent.children:
                parent.children.append(child_id)
            parent.updated_at = datetime.now(timezone.utc).isoformat()

    def get_subtree(self, node_id: str, max_depth: int = 3) -> Dict:
        """Get subtree rooted at node_id (returns dict tree)."""
        if node_id not in self.nodes:
            return {}

        node = self.nodes[node_id]
        result = {
            "node_id": node.node_id,
            "title": node.title,
            "layer": node.layer,
            "summary": node.summary,
            "episode_count": len(node.episode_ids),
        }

        if max_depth > 0 and node.children:
            result["children"] = [
                self.get_subtree(child_id, max_depth - 1) for child_id in node.children
            ]

        return result

    def get_path_to_root(self, node_id: str) -> List[IndexNode]:
        """Get path from node to root (for reasoning context)."""
        path = []
        current = self.nodes.get(node_id)
        while current:
            path.append(current)
            if current.parent_id:
                current = self.nodes.get(current.parent_id)
            else:
                break
        return path

    def find_by_episode(self, episode_id: str) -> Optional[IndexNode]:
        """Find node containing an episode."""
        for node in self.nodes.values():
            if episode_id in node.episode_ids:
                return node
        return None

    def traverse_dfs(self, node_id: Optional[str] = None) -> List[IndexNode]:
        """Depth-first traversal."""
        if not node_id:
            node_id = self.root_id
        if not node_id or node_id not in self.nodes:
            return []

        result = []
        stack = [node_id]
        while stack:
            nid = stack.pop()
            if nid in self.nodes:
                node = self.nodes[nid]
                result.append(node)
                # Add children in reverse order (so leftmost processed first)
                stack.extend(reversed(node.children))

        return result

    def get_statistics(self) -> Dict[str, Any]:
        """Get tree statistics."""
        stats = {
            "total_nodes": len(self.nodes),
            "layers": {},
            "max_depth": 0,
            "avg_children": 0,
        }

        total_children = 0
        for node in self.nodes.values():
            layer_key = f"l{node.layer}"
            stats["layers"][layer_key] = stats["layers"].get(layer_key, 0) + 1
            total_children += len(node.children)

        if stats["total_nodes"] > 0:
            stats["avg_children"] = total_children / stats["total_nodes"]

        # Compute max depth
        for node_id in self.nodes:
            depth = len(self.get_path_to_root(node_id))
            stats["max_depth"] = max(stats["max_depth"], depth)

        return stats


class TreeBuilder:
    """Build hierarchical tree from episodes (PageIndex-style builder).

    Converts flat episode list into hierarchical tree:
    - All L3 arcs become root children
    - Each L3 contains L2 clusters as children
    - Each L2 contains L1 summaries
    - Each L1 contains L0 episodes

    Also supports markdown heading-based tree building.
    """

    def __init__(self, store):
        self.store = store

    def build_from_layers(self) -> TreeIndex:
        """Construct tree from layered episodes.

        Retrieves all episodes from store and organizes them
        into a hierarchical tree based on parent_id and linked_ids.
        """
        tree = TreeIndex()

        # Get all episodes
        all_episodes = []
        for layer in range(4):
            all_episodes.extend(self.store.list_episodes(layer=layer))

        # Create nodes for each episode
        for ep in all_episodes:
            node = IndexNode(
                node_id=ep.id,
                title=ep.title or f"Episode {ep.id[:8]}",
                layer=ep.layer,
                summary=ep.content[:200] if ep.content else "",
                content=ep.content,
                episode_ids=[ep.id],
                created_at=ep.timestamp,
                metadata={
                    "category": ep.category,
                    "tags": ep.tags,
                    "importance": ep.importance,
                    "decay_score": ep.decay_score,
                },
            )
            tree.add_node(node)

        # Build parent-child relationships
        for ep in all_episodes:
            if ep.parent_id:
                tree.add_child(ep.parent_id, ep.id)
            elif ep.linked_ids:
                # For episodes without explicit parent, use first linked ID as parent
                tree.add_child(ep.linked_ids[0], ep.id)

        # Ensure arcs (L3) are at root level
        l3_episodes = [ep for ep in all_episodes if ep.layer == 3]
        for l3 in l3_episodes:
            if l3.id in tree.nodes:
                node = tree.nodes[l3.id]
                if not node.parent_id:  # Already root-level
                    pass
                else:
                    # Detach from parent, make root-level
                    parent = tree.nodes.get(node.parent_id)
                    if parent and l3.id in parent.children:
                        parent.children.remove(l3.id)
                    node.parent_id = None

        return tree

    def build_from_markdown_headings(
        self, markdown: str, base_episode: "MemoryEpisode"
    ) -> TreeIndex:
        """Build tree from markdown headings (like PageIndex).

        Each heading (#, ##, ###) becomes a node.
        Content under heading becomes node's content.
        """
        tree = TreeIndex()

        lines = markdown.split("\n")
        current_path = []  # Stack of (level, node_id)

        for i, line in enumerate(lines):
            if line.startswith("#"):
                # Count heading level
                level = 0
                while line.startswith("#"):
                    level += 1
                    line = line[1:]

                title = line.strip()

                # Create node
                node_id = f"md_{uuid.uuid4().hex[:8]}"
                node = IndexNode(
                    node_id=node_id,
                    title=title,
                    layer=min(level - 1, 3),  # H1->L0, H2->L1, etc.
                    content=title,
                    start_index=i,
                    end_index=i,
                )
                tree.add_node(node)

                # Link to parent based on heading level
                while current_path and current_path[-1][0] >= level:
                    current_path.pop()

                if current_path:
                    parent_id = current_path[-1][1]
                    tree.add_child(parent_id, node_id)

                current_path.append((level, node_id))

        return tree

    def merge_trees(self, tree1: TreeIndex, tree2: TreeIndex) -> TreeIndex:
        """Merge two trees, combining overlapping nodes."""
        merged = TreeIndex()

        # Add all nodes from both trees
        for node in tree1.nodes.values():
            merged.add_node(node)
        for node in tree2.nodes.values():
            if node.node_id not in merged.nodes:
                merged.add_node(node)

        # Merge children relationships
        for tree in [tree1, tree2]:
            for node in tree.nodes.values():
                for child_id in node.children:
                    if child_id in merged.nodes:
                        merged.add_child(node.node_id, child_id)

        return merged


class ReasoningRetriever:
    """PageIndex-inspired LLM reasoning retrieval.

    Instead of pure keyword search, uses LLM to reason about
    which memory nodes are most relevant to query.
    """

    def __init__(self, tree: TreeIndex, store, llm_client=None):
        self.tree = tree
        self.store = store
        self.llm = llm_client

    def retrieve(
        self, query: str, max_nodes: int = 10, use_reasoning: bool = True
    ) -> List[IndexNode]:
        """
        Retrieve relevant nodes via reasoning:

        1. Fast pre-filter: keyword search on node summaries
        2. Candidate selection: top-k nodes
        3. LLM reasoning: "Which nodes actually contain answer?"
        4. Return reasoned selection
        """
        # Step 1: Keyword pre-filter
        candidates = self._keyword_filter(query, k=max_nodes * 2)

        if not use_reasoning or not self.llm:
            return candidates[:max_nodes]

        # Step 2: LLM reasoning
        reasoning_prompt = self._build_reasoning_prompt(query, candidates)
        selected_ids = self._reason_with_llm(reasoning_prompt)

        # Step 3: Map IDs back to nodes
        results = [
            self.tree.nodes[nid] for nid in selected_ids if nid in self.tree.nodes
        ]

        return results

    def _keyword_filter(self, query: str, k: int = 20) -> List[IndexNode]:
        """Simple keyword matching to get candidate nodes."""
        query_lower = query.lower()
        scored = []

        for node in self.tree.nodes.values():
            score = 0
            if query_lower in node.title.lower():
                score += 2.0
            if query_lower in node.summary.lower():
                score += 1.0
            if score > 0:
                scored.append((node, score))

        scored.sort(key=lambda x: x[1], reverse=True)
        return [node for node, _ in scored[:k]]

    def _build_reasoning_prompt(self, query: str, candidates: List[IndexNode]) -> str:
        """Build prompt for LLM reasoning."""
        lines = [
            "Given the user query, identify which memory nodes contain relevant information.",
            f"Query: {query}",
            "",
            "Memory nodes:",
        ]

        for i, node in enumerate(candidates):
            lines.append(f"[{i}] {node.title} (L{node.layer})")
            lines.append(f"    Summary: {node.summary}")
            lines.append(f"    Episode IDs: {', '.join(node.episode_ids[:3])}")
            lines.append("")

        lines.append(
            "Return a list of node numbers (e.g., '0, 2, 4') that are most relevant."
        )
        return "\n".join(lines)

    def _reason_with_llm(self, prompt: str) -> List[str]:
        """Call LLM to reason over candidates."""
        # Placeholder - would call Claude/OpenAI/etc
        # For now, return all candidates (no-op)
        return []

    def explain_selection(self, query: str, node: IndexNode) -> str:
        """Generate explanation for why this node was selected."""
        # Could use LLM to generate explanation
        return f"Selected '{node.title}' because it contains information about {query}"
