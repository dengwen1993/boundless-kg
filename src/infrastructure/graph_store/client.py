"""FalkorDB graph store client — Cypher-based graph operations.

Each domain gets its own graph (``kg_{domain}``). All operations are
async-compatible via ``asyncio.to_thread`` wrapping the synchronous
FalkorDB client.

Graceful degradation: when FalkorDB is unreachable, all operations
log a warning and return empty results — the rest of the system
continues to work with file-based storage.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any

from src.config.settings import get_falkordb_settings

logger = logging.getLogger(__name__)

#: Cap on TCP connect / command wait. Left unset, redis-py inherits the
#: OS SYN-retry limit (~130s), which freezes the whole event loop for
#: every caller that probes availability.
_CONNECT_TIMEOUT_S = 2.0

#: Cooldown before retrying a failed connect, so an unreachable FalkorDB
#: costs one timeout per window rather than one per request.
_RETRY_COOLDOWN_S = 30.0


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _sha8(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()[:8]


def _parse_iso_or_none(s: Any) -> "datetime | None":
    """Best-effort parse ISO-8601 string → datetime (or ``None`` on failure)."""
    from datetime import datetime, timezone

    if not s:
        return None
    if isinstance(s, datetime):
        return s if s.tzinfo else s.replace(tzinfo=timezone.utc)
    try:
        # FalkorDB sometimes returns fractional seconds or a trailing 'Z'.
        text = str(s).strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _strip_concept(node_id: str) -> str:
    """Return the concept name inside ``concept:{name}`` or the original."""
    if isinstance(node_id, str) and node_id.startswith("concept:"):
        return node_id[len("concept:"):]
    return node_id


#: Relation types that have semantic value (stored as edges in FalkorDB).
#: HAS_NOTE / HAS_RESOURCE / HAS_PLAN are NOT here — they're derivable
#: from path conventions.
SEMANTIC_RELATIONS = frozenset({
    "part_of", "prerequisite_of", "enables", "similar_to",
    "contrasts_with", "applies_to", "derived_from", "related_to",
    "references",
})


class GraphStoreClient:
    """FalkorDB client — one graph per domain.

    All Cypher calls are wrapped in ``asyncio.to_thread`` so they
    don't block the event loop.
    """

    def __init__(self) -> None:
        self._settings = get_falkordb_settings()
        self._connection: Any = None
        self._connected = False
        self._has_vector_index: bool = False
        self._next_retry_at: float = 0.0

    def _connect(self) -> Any:
        """Lazily connect to FalkorDB.

        Blocking — async callers must go through :meth:`ensure_available`.
        """
        if self._connection is not None:
            return self._connection
        if time.monotonic() < self._next_retry_at:
            return None
        try:
            from falkordb import FalkorDB
            password = None
            if self._settings.password is not None:
                password = self._settings.password.get_secret_value()
            self._connection = FalkorDB(
                host=self._settings.host,
                port=self._settings.port,
                password=password,
                socket_connect_timeout=_CONNECT_TIMEOUT_S,
                socket_timeout=_CONNECT_TIMEOUT_S,
            )
            self._connected = True
            self._next_retry_at = 0.0
            logger.info(
                "FalkorDB connected (host=%s port=%d)",
                self._settings.host, self._settings.port,
            )
            return self._connection
        except ImportError:
            logger.warning("falkordb package not installed; graph store disabled")
            self._connected = False
            self._next_retry_at = time.monotonic() + _RETRY_COOLDOWN_S
            return None
        except Exception as e:
            logger.warning("FalkorDB connection failed: %s — graph store disabled", e)
            self._connected = False
            self._next_retry_at = time.monotonic() + _RETRY_COOLDOWN_S
            return None

    @property
    def is_available(self) -> bool:
        """Whether FalkorDB is connected and operational."""
        if not self._settings.enabled:
            return False
        return self._connection is not None or self._connect() is not None

    async def ensure_available(self) -> bool:
        """Async-safe :attr:`is_available` — probes off the event loop."""
        if not self._settings.enabled:
            return False
        if self._connection is not None:
            return True
        return await asyncio.to_thread(self._connect) is not None

    def _graph_name(self, domain: str) -> str:
        """Generate a safe graph name for the domain."""
        # Replace spaces/special chars for graph name safety
        safe = domain.replace(" ", "_").replace("/", "_")[:50]
        return f"{self._settings.graph_prefix}{safe}"

    def _get_graph(self, domain: str):
        """Get the FalkorDB Graph object for a domain."""
        conn = self._connect()
        if conn is None:
            return None
        return conn.select_graph(self._graph_name(domain))

    # ---------- Node operations ----------

    def upsert_concept(
        self, domain: str, *,
        name: str, level: int = 0, is_root: bool = False,
        description: str = "", embedding: list[float] | None = None,
    ) -> bool:
        """Insert or update a Concept node."""
        g = self._get_graph(domain)
        if g is None:
            return False
        cid = f"concept:{name}"
        params: dict[str, Any] = {
            "id": cid, "name": name, "domain": domain,
            "level": level, "is_root": is_root,
            "description": description[:500],
            "updated_at": _utcnow_iso(),
        }
        if embedding is not None:
            params["embedding"] = str(embedding)
        try:
            cypher = (
                "MERGE (c:Concept {id: $id}) "
                "SET c.name = $name, c.domain = $domain, "
                "    c.level = $level, c.is_root = $is_root, "
                "    c.description = $description, c.updated_at = $updated_at"
            )
            if embedding is not None:
                cypher += ", c.embedding = $embedding"
            g.query(cypher, params=params)
            return True
        except Exception as e:
            logger.warning("upsert_concept failed: %s", e)
            return False

    def upsert_note(
        self, domain: str, *,
        node: str, word_count: int = 0, summary: str = "",
        embedding: list[float] | None = None,
    ) -> bool:
        """Insert or update a Note node."""
        g = self._get_graph(domain)
        if g is None:
            return False
        nid = f"note:{node}"
        params: dict[str, Any] = {
            "id": nid, "node": node, "domain": domain,
            "word_count": word_count,
            "summary": summary[:500],
            "updated_at": _utcnow_iso(),
        }
        if embedding is not None:
            params["embedding"] = str(embedding)
        try:
            cypher = (
                "MERGE (n:Note {id: $id}) "
                "SET n.node = $node, n.domain = $domain, "
                "    n.word_count = $word_count, n.summary = $summary, "
                "    n.updated_at = $updated_at"
            )
            if embedding is not None:
                cypher += ", n.embedding = $embedding"
            g.query(cypher, params=params)
            return True
        except Exception as e:
            logger.warning("upsert_note failed: %s", e)
            return False

    def upsert_resource(
        self, domain: str, *,
        node: str, url: str, title: str = "", summary: str = "",
        embedding: list[float] | None = None,
    ) -> bool:
        """Insert or update a Resource node."""
        g = self._get_graph(domain)
        if g is None:
            return False
        sha = _sha8(url)
        rid = f"resource:{sha}"
        params: dict[str, Any] = {
            "id": rid, "node": node, "domain": domain,
            "url": url, "title": title,
            "summary": summary[:500],
            "updated_at": _utcnow_iso(),
        }
        if embedding is not None:
            params["embedding"] = str(embedding)
        try:
            cypher = (
                "MERGE (r:Resource {id: $id}) "
                "SET r.node = $node, r.domain = $domain, "
                "    r.url = $url, r.title = $title, "
                "    r.summary = $summary, r.updated_at = $updated_at"
            )
            if embedding is not None:
                cypher += ", r.embedding = $embedding"
            g.query(cypher, params=params)
            return True
        except Exception as e:
            logger.warning("upsert_resource failed: %s", e)
            return False

    def upsert_plan(
        self, domain: str, *,
        node: str, plan_id: str, goal: str = "",
        action_count: int = 0, completed: int = 0,
    ) -> bool:
        """Insert or update a Plan node."""
        g = self._get_graph(domain)
        if g is None:
            return False
        pid = f"plan:{node}:{plan_id}"
        try:
            g.query(
                "MERGE (p:Plan {id: $id}) "
                "SET p.node = $node, p.domain = $domain, "
                "    p.plan_id = $plan_id, p.goal = $goal, "
                "    p.action_count = $action_count, p.completed = $completed, "
                "    p.updated_at = $updated_at",
                params={
                    "id": pid, "node": node, "domain": domain,
                    "plan_id": plan_id, "goal": goal[:200],
                    "action_count": action_count, "completed": completed,
                    "updated_at": _utcnow_iso(),
                },
            )
            return True
        except Exception as e:
            logger.warning("upsert_plan failed: %s", e)
            return False

    def upsert_dossier_entry(
        self, domain: str, *,
        entry_id: str, node: str, entry_type: str,
        title: str, body: str, tags: list[str] | None = None,
        score: float = 0.5, use_count: int = 0,
        created_at: str = "", embedding: list[float] | None = None,
    ) -> bool:
        """Insert or update a DossierEntry node.

        节点的档案条目是派生态,FalkorDB 中存为 (:DossierEntry) 节点,
        方便 Graph-aware prompt 注入时做 BM25 + 向量召回。
        """
        g = self._get_graph(domain)
        if g is None:
            return False
        did = f"dossier_entry:{entry_id}"
        params: dict[str, Any] = {
            "id": did, "entry_id": entry_id, "node": node,
            "domain": domain, "type": entry_type,
            "title": title[:200],
            "body": body[:1000],
            "tags": ",".join(tags or []),
            "score": score, "use_count": use_count,
            "created_at": created_at or _utcnow_iso(),
            "updated_at": _utcnow_iso(),
        }
        if embedding is not None:
            params["embedding"] = str(embedding)
        try:
            cypher = (
                "MERGE (e:DossierEntry {id: $id}) "
                "SET e.entry_id = $entry_id, e.node = $node, e.domain = $domain, "
                "    e.type = $type, e.title = $title, e.body = $body, "
                "    e.tags = $tags, e.score = $score, e.use_count = $use_count, "
                "    e.created_at = $created_at, e.updated_at = $updated_at"
            )
            if embedding is not None:
                cypher += ", e.embedding = $embedding"
            g.query(cypher, params=params)
            return True
        except Exception as e:
            logger.warning("upsert_dossier_entry failed: %s", e)
            return False

    def delete_dossier_entry(self, domain: str, entry_id: str) -> bool:
        """Delete a DossierEntry node by entry_id."""
        return self.delete_node(domain, f"dossier_entry:{entry_id}")

    def add_has_dossier_edge(
        self, domain: str, *,
        node: str, entry_id: str,
    ) -> bool:
        """Concept -[:HAS_DOSSIER]-> DossierEntry 边。"""
        return self.add_edge_any(
            domain,
            source=f"concept:{node}",
            target=f"dossier_entry:{entry_id}",
            relation="has_dossier",
            weight=1.0,
            intensity="STRUCTURAL",
            evidence="Concept ↔ DossierEntry (经验档案)",
            created_by="system",
        )

    # ---------- Edge operations ----------

    def add_edge(
        self, domain: str, *,
        source: str, target: str, relation: str,
        weight: float = 1.0, intensity: str = "SOFT",
        evidence: str = "", created_by: str = "system",
    ) -> bool:
        """Add a semantic edge between two nodes.

        Only semantic relations are stored (see SEMANTIC_RELATIONS).
        HAS_NOTE / HAS_RESOURCE / HAS_PLAN are silently skipped — use
        :meth:`add_edge_any` to force-write structural relations.
        """
        if relation.lower() not in SEMANTIC_RELATIONS:
            return True  # Skip structural-derivable edges

        return self._write_edge(
            domain, source=source, target=target, relation=relation,
            weight=weight, intensity=intensity,
            evidence=evidence, created_by=created_by,
        )

    def add_edge_any(
        self, domain: str, *,
        source: str, target: str, relation: str,
        weight: float = 1.0, intensity: str = "SOFT",
        evidence: str = "", created_by: str = "system",
    ) -> bool:
        """Add an edge of any relation type, bypassing SEMANTIC_RELATIONS.

        Used by structural edge writers (HAS_NOTE / HAS_RESOURCE / HAS_PLAN)
        and by the association-replay path that needs every persisted edge
        mirrored into FalkorDB.
        """
        if relation.lower() not in {
            "part_of", "prerequisite_of", "enables", "similar_to",
            "contrasts_with", "applies_to", "derived_from", "related_to",
            "references", "cites", "has_note", "has_resource", "has_plan",
            "has_dossier",
        }:
            return True
        return self._write_edge(
            domain, source=source, target=target, relation=relation,
            weight=weight, intensity=intensity,
            evidence=evidence, created_by=created_by,
        )

    def _write_edge(
        self, domain: str, *,
        source: str, target: str, relation: str,
        weight: float, intensity: str,
        evidence: str, created_by: str,
    ) -> bool:
        g = self._get_graph(domain)
        if g is None:
            return False
        try:
            rel = relation.upper()  # FalkorDB relationship types are uppercase
            g.query(
                f"MATCH (s {{id: $src}}), (t {{id: $tgt}}) "
                f"MERGE (s)-[r:{rel}]->(t) "
                f"SET r.weight = $weight, r.intensity = $intensity, "
                f"    r.evidence = $evidence, r.created_by = $created_by, "
                f"    r.created_at = $created_at",
                params={
                    "src": source, "tgt": target,
                    "weight": weight, "intensity": intensity,
                    "evidence": evidence[:200],
                    "created_by": created_by,
                    "created_at": _utcnow_iso(),
                },
            )
            return True
        except Exception as e:
            logger.warning("add_edge failed: %s", e)
            return False

    def delete_node(self, domain: str, node_id: str) -> bool:
        """Delete a node and all its edges."""
        g = self._get_graph(domain)
        if g is None:
            return False
        try:
            g.query(
                "MATCH (n {id: $id}) "
                "DETACH DELETE n",
                params={"id": node_id},
            )
            return True
        except Exception as e:
            logger.warning("delete_node failed: %s", e)
            return False

    def delete_concept_by_name(self, domain: str, name: str) -> bool:
        """Delete a Concept node by name and all its edges."""
        return self.delete_node(domain, f"concept:{name}")

    def delete_note_by_node(self, domain: str, node: str) -> bool:
        """Delete a Note node by node name."""
        return self.delete_node(domain, f"note:{node}")

    def delete_edge(
        self, domain: str, *,
        source: str, target: str, relation: str,
    ) -> bool:
        """Delete a single semantic edge between two nodes by relation.

        Used by manual association deletion from the UI — JSON mutation
        stays the source of truth; this just keeps the FalkorDB mirror in
        sync until the next ``sync_full`` rebuild runs.
        """
        g = self._get_graph(domain)
        if g is None:
            return False
        try:
            rel = relation.upper()
            g.query(
                f"MATCH (s {{id: $src}})-[r:{rel}]->(t {{id: $tgt}}) "
                "DELETE r",
                params={"src": source, "tgt": target},
            )
            return True
        except Exception as e:
            logger.warning("delete_edge failed: %s", e)
            return False

    # ---------- Query operations ----------

    def neighbors(
        self, domain: str, node_id: str, *, hops: int = 1,
    ) -> list[dict[str, Any]]:
        """Query N-hop neighbors of a node."""
        g = self._get_graph(domain)
        if g is None:
            return []
        max_hops = min(hops, 5)
        results: list[dict[str, Any]] = []
        try:
            # FalkorDB doesn't support UNWIND on variable-length paths.
            # Query each hop level separately.
            for h in range(1, max_hops + 1):
                result = g.query(
                    f"MATCH (n {{id: $id}})-[r*{h}]-(m) "
                    f"WITH m, r "
                    "RETURN m.id AS neighbor_id, m.name AS name, "
                    "       type(r[-1]) AS relation, $h AS hops",
                    params={"id": node_id, "h": h},
                )
                rows = result.result_set if hasattr(result, "result_set") else []
                for row in rows:
                    if row and row[0]:
                        results.append({
                            "neighbor_id": row[0],
                            "name": row[1],
                            "relation": row[2],
                            "hops": row[3],
                        })
            # Deduplicate by neighbor_id (keep closest hop)
            seen: set[str] = set()
            deduped: list[dict[str, Any]] = []
            for r in results:
                if r["neighbor_id"] not in seen:
                    seen.add(r["neighbor_id"])
                    deduped.append(r)
            return deduped
        except Exception as e:
            logger.warning("neighbors query failed: %s", e)
            # Fallback: simple 1-hop query
            try:
                result = g.query(
                    "MATCH (n {id: $id})-[r]-(m) "
                    "RETURN m.id AS neighbor_id, m.name AS name, "
                    "       type(r) AS relation, 1 AS hops",
                    params={"id": node_id},
                )
                rows = result.result_set if hasattr(result, "result_set") else []
                return [
                    {
                        "neighbor_id": row[0],
                        "name": row[1],
                        "relation": row[2],
                        "hops": row[3],
                    }
                    for row in rows
                    if row and row[0]
                ]
            except Exception as e2:
                logger.warning("neighbors fallback query failed: %s", e2)
                return []

    def ensure_vector_index(self, domain: str) -> bool:
        """Create a vector index on Concept.embedding if supported.

        FalkorDB 4.x+ supports ``db.idx.createVectorIndex``. Older versions
        don't — in that case vector search falls back to local cosine
        similarity (see ``vector_search_local``).
        """
        g = self._get_graph(domain)
        if g is None:
            return False
        try:
            from src.config.settings import get_embedding_settings
            dim = get_embedding_settings().dim
            g.query(
                f"CALL db.idx.createVectorIndex("
                f"'Concept', 'embedding', {dim}, 'COSINE', 'AUTO')"
            )
            logger.info("Vector index created on Concept.embedding (dim=%d)", dim)
            self._has_vector_index = True
            return True
        except Exception as e:
            # Index may already exist or not supported — that's fine
            logger.debug("Vector index creation: %s (may already exist or unsupported)", e)
            self._has_vector_index = False
            return False

    def vector_search(
        self, domain: str, query_vec: list[float], *, top_k: int = 10,
    ) -> list[dict[str, Any]]:
        """Vector similarity search.

        Strategy:
          1. Try FalkorDB native vector search (if index exists).
          2. Fall back to local cosine similarity (fetch all → numpy).
        """
        g = self._get_graph(domain)
        if g is None:
            return []

        # Try native FalkorDB vector search first
        try:
            result = g.query(
                "CALL db.idx.vector.queryNodes("
                "'Concept', 'embedding', $top_k, $vec) "
                "YIELD node, score "
                "RETURN node.id AS id, node.name AS name, "
                "       node.domain AS domain, score",
                params={"top_k": top_k, "vec": query_vec},
            )
            rows = result.result_set if hasattr(result, "result_set") else []
            if rows:
                return [
                    {
                        "id": row[0],
                        "name": row[1],
                        "domain": row[2],
                        "score": row[3],
                    }
                    for row in rows
                    if row and row[0]
                ]
        except Exception:
            pass  # Fall through to local search

        # Fallback: local cosine similarity
        return self._vector_search_local(g, query_vec, top_k)

    def _vector_search_local(
        self, g, query_vec: list[float], top_k: int,
    ) -> list[dict[str, Any]]:
        """Local cosine similarity search.

        Fetches all Concept nodes with embeddings, computes cosine
        similarity in Python. Works when FalkorDB doesn't support
        native vector indices.
        """
        import numpy as np

        try:
            result = g.query(
                "MATCH (c:Concept) "
                "WHERE c.embedding IS NOT NULL "
                "RETURN c.id AS id, c.name AS name, "
                "       c.domain AS domain, c.embedding AS embedding"
            )
            rows = result.result_set if hasattr(result, "result_set") else []
            if not rows:
                return []

            query_arr = np.array(query_vec, dtype=np.float32)
            query_norm = np.linalg.norm(query_arr)
            if query_norm == 0:
                return []

            scored: list[dict[str, Any]] = []
            for row in rows:
                if not row or not row[0] or not row[3]:
                    continue
                try:
                    # Embedding stored as string repr of list
                    emb_str = row[3]
                    if isinstance(emb_str, str):
                        import ast
                        emb = np.array(ast.literal_eval(emb_str), dtype=np.float32)
                    elif isinstance(emb_str, (list, tuple)):
                        emb = np.array(emb_str, dtype=np.float32)
                    else:
                        continue

                    if emb.shape[0] != query_arr.shape[0]:
                        continue

                    emb_norm = np.linalg.norm(emb)
                    if emb_norm == 0:
                        continue

                    score = float(np.dot(query_arr, emb) / (query_norm * emb_norm))
                    scored.append({
                        "id": row[0],
                        "name": row[1],
                        "domain": row[2] if row[2] else "",
                        "score": score,
                    })
                except (ValueError, SyntaxError):
                    continue

            scored.sort(key=lambda x: x["score"], reverse=True)
            return scored[:top_k]
        except Exception as e:
            logger.warning("vector_search_local failed: %s", e)
            return []

    def all_concepts(self, domain: str) -> list[dict[str, Any]]:
        """Return all Concept nodes for a domain."""
        g = self._get_graph(domain)
        if g is None:
            return []
        try:
            result = g.query(
                "MATCH (c:Concept) RETURN c.id AS id, c.name AS name, "
                "c.level AS level, c.description AS description"
            )
            rows = result.result_set if hasattr(result, "result_set") else []
            return [
                {
                    "id": row[0],
                    "name": row[1],
                    "level": row[2],
                    "description": row[3] or "",
                }
                for row in rows
                if row and row[0]
            ]
        except Exception as e:
            logger.warning("all_concepts failed: %s", e)
            return []

    def statistics(self, domain: str) -> dict[str, int]:
        """Return graph statistics."""
        g = self._get_graph(domain)
        if g is None:
            return {"concepts": 0, "notes": 0, "resources": 0, "edges": 0}
        try:
            # Exclude the per-domain ``:GraphMeta`` singleton from
            # per-label counts — it's bookkeeping, not user-visible data.
            result = g.query(
                f"MATCH (n) WHERE NOT n:{self._GRAPH_META_LABEL} "
                "RETURN labels(n)[0] AS type, count(n) AS cnt"
            )
            rows = result.result_set if hasattr(result, "result_set") else []
            stats: dict[str, int] = {
                "concepts": 0, "notes": 0, "resources": 0, "edges": 0
            }
            for row in rows:
                if not row or not row[0]:
                    continue
                label = str(row[0]).lower()
                count = int(row[1]) if row[1] else 0
                if label == "concept":
                    stats["concepts"] = count
                elif label == "note":
                    stats["notes"] = count
                elif label == "resource":
                    stats["resources"] = count

            edge_result = g.query("MATCH ()-[r]->() RETURN count(r) AS cnt")
            edge_rows = edge_result.result_set if hasattr(edge_result, "result_set") else []
            if edge_rows and edge_rows[0]:
                stats["edges"] = int(edge_rows[0][0]) if edge_rows[0][0] else 0
            return stats
        except Exception as e:
            logger.warning("statistics failed: %s", e)
            return {"concepts": 0, "notes": 0, "resources": 0, "edges": 0}

    # ---------- GraphMeta singleton (per-domain metadata) ----------

    #: Cypher id for the per-domain metadata node.  FalkorDB stores
    #: ``last_full_sync``, ``derived_events`` and ``schema_version`` here
    #: so they survive a full ``associations.json`` delete.
    _GRAPH_META_LABEL = "GraphMeta"

    def _graph_meta_id(self, domain: str) -> str:
        return f"meta:{domain}"

    def upsert_graph_meta(
        self,
        domain: str,
        *,
        last_full_sync: str | None,
        derived_events: dict[str, str] | None = None,
        schema_version: str = "2.0-graph",
    ) -> bool:
        """Write the per-domain ``:GraphMeta`` singleton.

        ``derived_events`` is serialised to a JSON string because FalkorDB
        does not expose a native map type for node properties (only
        primitives + lists of primitives).
        """
        g = self._get_graph(domain)
        if g is None:
            return False
        mid = self._graph_meta_id(domain)
        try:
            de_str = json.dumps(derived_events or {}, ensure_ascii=False, default=str)
            g.query(
                f"MERGE (m:{self._GRAPH_META_LABEL} {{id: $id}}) "
                "SET m.domain = $domain, "
                "    m.last_full_sync = $lfs, "
                "    m.derived_events = $de, "
                "    m.schema_version = $sv",
                params={
                    "id": mid, "domain": domain,
                    "lfs": last_full_sync,
                    "de": de_str,
                    "sv": schema_version,
                },
            )
            return True
        except Exception as e:
            logger.warning("upsert_graph_meta failed: %s", e)
            return False

    def get_graph_meta(self, domain: str) -> dict[str, Any] | None:
        """Read the per-domain ``:GraphMeta`` singleton, or ``None``.

        Returns a dict with ``last_full_sync``, ``derived_events`` and
        ``schema_version``. Missing nodes return ``None`` (not an empty
        dict) so callers can distinguish "no sync yet" from "empty sync".
        """
        g = self._get_graph(domain)
        if g is None:
            return None
        try:
            result = g.query(
                f"MATCH (m:{self._GRAPH_META_LABEL} {{id: $id}}) "
                "RETURN m.last_full_sync AS lfs, "
                "       m.derived_events AS de, "
                "       m.schema_version AS sv "
                "LIMIT 1",
                params={"id": self._graph_meta_id(domain)},
            )
            rows = result.result_set if hasattr(result, "result_set") else []
            if not rows or not rows[0]:
                return None
            lfs, de_str, sv = rows[0][0], rows[0][1], rows[0][2]
            de: dict[str, Any] = {}
            if isinstance(de_str, str) and de_str:
                try:
                    de = json.loads(de_str)
                except (ValueError, TypeError):
                    de = {}
            return {
                "last_full_sync": lfs,
                "derived_events": de,
                "schema_version": sv or "2.0-graph",
            }
        except Exception as e:
            logger.warning("get_graph_meta failed: %s", e)
            return None

    # ---------- Whole-graph export (for the associations view) ----------

    #: Edge relations whose source/target should be exported as bare
    #: concept names (i.e. strip the ``concept:`` id prefix). Matches
    #: the on-disk `associations.json` convention used by the legacy
    #: :class:`AssociationRepository` so the two read paths stay
    #: interchangeable.
    _BARE_CONCEPT_RELS: frozenset[str] = frozenset({
        "part_of", "prerequisite_of", "enables", "similar_to",
        "contrasts_with", "applies_to", "derived_from", "related_to",
    })
    #: ``references`` is special: source is ``note:{X}``, target is the
    #: bare referenced concept name (no ``concept:`` prefix). The legacy
    #: JSON stores it exactly so.
    _REFERENCE_RELS: frozenset[str] = frozenset({"references", "cites"})

    def export_graph(self, domain: str) -> dict[str, Any]:
        """Export the whole domain graph as an AssociationGraph-shaped dict.

        The returned shape mirrors :meth:`AssociationRepository.read_raw`
        so the API endpoint can return either source with no client-side
        changes::

            {
              "domain": <str>,
              "concepts": {<name>: ConceptNode...},
              "resources": {<id>: ResourceNode...},
              "associations": [Association...],
              "metadata": {"derived_events": {}, "last_full_sync": ..., "schema_version": "2.0-graph"},
              "generated_at": <iso or None>,
            }

        Side-effects: read-only. Returns the empty shell on connection
        failure (so the route layer can decide whether to fall back to
        the JSON file).
        """
        from datetime import datetime, timezone

        empty = {
            "domain": domain,
            "concepts": {},
            "resources": {},
            "associations": [],
            "metadata": {
                "derived_events": {},
                "last_full_sync": None,
                "schema_version": "2.0-graph",
            },
            "generated_at": None,
        }
        g = self._get_graph(domain)
        if g is None:
            return empty

        # ── Concepts ─────────────────────────────────────────────────
        # Build a name → level map so PART_OF edges can compute
        # in_degree / out_degree / part_of_count without a second pass.
        concepts: dict[str, dict[str, Any]] = {}
        concept_meta: dict[str, dict[str, int]] = {}
        updated_at_max: datetime | None = None

        try:
            result = g.query(
                "MATCH (c:Concept) WHERE c.domain = $domain "
                "RETURN c.id AS id, c.name AS name, c.domain AS domain, "
                "       c.level AS level, c.is_root AS is_root, "
                "       c.description AS description, c.updated_at AS updated_at",
                params={"domain": domain},
            )
            rows = result.result_set if hasattr(result, "result_set") else []
        except Exception as e:
            logger.warning("export_graph: concepts query failed: %s", e)
            rows = []

        for row in rows:
            if not row or not row[1]:
                continue
            name = row[1]
            updated_at = row[6]
            level = int(row[3]) if row[3] is not None else 0
            updated_at_dt = _parse_iso_or_none(updated_at)
            if updated_at_dt and (
                updated_at_max is None or updated_at_dt > updated_at_max
            ):
                updated_at_max = updated_at_dt

            concepts[name] = {
                "id": f"concept:{name}",
                "name": name,
                "domain": row[2] or domain,
                "level": level,
                "is_root": bool(row[4]) or level == 0,
                "description": row[5] or "",
                "in_degree": 0,
                "out_degree": 0,
                "part_of_count": 0,
                "updated_at": (
                    updated_at_dt.isoformat() if updated_at_dt else _utcnow_iso()
                ),
            }
            concept_meta[name] = {
                "in_degree": 0,
                "out_degree": 0,
                "part_of_count": 0,
            }

        # ── Resources (Note / Resource / Plan) ───────────────────────
        resources: dict[str, dict[str, Any]] = {}
        try:
            # Exclude the bookkeeping ``:GraphMeta`` singleton so it
            # never shows up in the frontend resources tab.
            result = g.query(
                f"MATCH (n) WHERE NOT n:Concept AND NOT n:{self._GRAPH_META_LABEL} "
                "AND n.domain = $domain "
                "RETURN labels(n)[0] AS label, n.id AS id, "
                "       n.node AS node, n.url AS url, n.title AS title, "
                "       n.summary AS summary, n.word_count AS word_count, "
                "       n.goal AS goal, n.plan_id AS plan_id, "
                "       n.action_count AS action_count, n.completed AS completed, "
                "       n.updated_at AS updated_at",
                params={"domain": domain},
            )
            rows = result.result_set if hasattr(result, "result_set") else []
        except Exception as e:
            logger.warning("export_graph: resources query failed: %s", e)
            rows = []

        type_for_label = {
            "Note": "note",
            "Resource": "resource",
            "Plan": "plan",
        }
        for row in rows:
            if not row or not row[0] or not row[1]:
                continue
            label = str(row[0])
            rtype = type_for_label.get(label)
            if rtype is None:
                continue
            rid = row[1]
            node = row[2] or ""
            updated_at_dt = _parse_iso_or_none(row[11])
            if updated_at_dt and (
                updated_at_max is None or updated_at_dt > updated_at_max
            ):
                updated_at_max = updated_at_dt

            payload: dict[str, Any] = {}
            if rtype == "resource" and row[3]:
                payload["url"] = row[3]
            if rtype == "plan":
                if row[8]:
                    payload["plan_id"] = row[8]
                payload["action_count"] = (
                    int(row[9]) if row[9] is not None else 0
                )
                payload["completed"] = (
                    int(row[10]) if row[10] is not None else 0
                )
            if rtype == "note":
                payload["word_count"] = (
                    int(row[7]) if row[7] is not None else 0
                )

            resources[rid] = {
                "id": rid,
                "type": rtype,
                "node": node,
                "domain": domain,
                "payload": payload,
                "summary": (row[5] or row[4] or ""),
                "updated_at": (
                    updated_at_dt.isoformat() if updated_at_dt else _utcnow_iso()
                ),
            }

        # ── Edges ────────────────────────────────────────────────────
        associations: list[dict[str, Any]] = []
        try:
            result = g.query(
                "MATCH (s)-[r]->(t) WHERE s.domain = $domain AND t.domain = $domain "
                "RETURN s.id AS src, t.id AS tgt, type(r) AS relation, "
                "       r.weight AS weight, r.intensity AS intensity, "
                "       r.evidence AS evidence, r.created_by AS created_by, "
                "       r.created_at AS created_at",
                params={"domain": domain},
            )
            rows = result.result_set if hasattr(result, "result_set") else []
        except Exception as e:
            logger.warning("export_graph: edges query failed: %s", e)
            rows = []

        for row in rows:
            if not row or not row[0] or not row[1]:
                continue
            relation = str(row[2]).lower() if row[2] else ""
            source, target = self._normalize_edge_endpoints(relation, row[0], row[1])
            created_at_dt = _parse_iso_or_none(row[7])
            if created_at_dt and (
                updated_at_max is None or created_at_dt > updated_at_max
            ):
                updated_at_max = created_at_dt
            associations.append({
                "source": source,
                "target": target,
                "relation": relation,
                "weight": float(row[3]) if row[3] is not None else 1.0,
                "intensity": str(row[4]).upper() if row[4] else "SOFT",
                "evidence": (row[5] or "") if len(row) > 5 else "",
                "created_by": (row[6] or "system") if len(row) > 6 else "system",
                "created_at": (
                    created_at_dt.isoformat() if created_at_dt else _utcnow_iso()
                ),
            })
            # PART_OF counters: increment on source (out_degree), target (in_degree).
            if relation == "part_of":
                src_name = _strip_concept(row[0])
                tgt_name = _strip_concept(row[1])
                if src_name in concept_meta:
                    concept_meta[src_name]["out_degree"] += 1
                if tgt_name in concept_meta:
                    concept_meta[tgt_name]["in_degree"] += 1
                    concept_meta[tgt_name]["part_of_count"] += 1

        # Apply the counters we built while iterating edges.
        for name, meta in concept_meta.items():
            entry = concepts.get(name)
            if entry is None:
                continue
            entry["out_degree"] = meta["out_degree"]
            entry["in_degree"] = meta["in_degree"]
            entry["part_of_count"] = meta["part_of_count"]

        return {
            "domain": domain,
            "concepts": concepts,
            "resources": resources,
            "associations": associations,
            "metadata": self._compose_export_metadata(domain, updated_at_max),
            "generated_at": (
                updated_at_max.isoformat() if updated_at_max else None
            ),
        }

    def _compose_export_metadata(
        self,
        domain: str,
        updated_at_max: datetime | None,
    ) -> dict[str, Any]:
        """Build the ``metadata`` payload for :meth:`export_graph`.

        Prefers the ``:GraphMeta`` singleton when present (authoritative
        ``last_full_sync`` / ``derived_events``); otherwise falls back to
        the max(updated_at) proxy so freshly-imported domains that
        pre-date this commit still return a sensible value.
        """
        meta = self.get_graph_meta(domain) or {}
        proxy = (
            updated_at_max.isoformat() if updated_at_max else None
        )
        return {
            "derived_events": meta.get("derived_events") or {},
            "last_full_sync": meta.get("last_full_sync") or proxy,
            "schema_version": meta.get("schema_version") or "2.0-graph",
        }

    @classmethod
    def _normalize_edge_endpoints(
        cls,
        relation: str,
        source: str,
        target: str,
    ) -> tuple[str, str]:
        """Translate FalkorDB id-style endpoints to the JSON-side convention.

        - ``has_note`` / ``has_resource`` / ``has_plan``: keep
          ``concept:`` on source and the resource-id on target verbatim.
        - ``references`` / ``cites``: source keeps the resource id;
          target is the bare referenced name (we strip any
          ``concept:`` prefix added by the writer).
        - Everything else (``part_of`` and the semantic relations):
          strip ``concept:`` from both endpoints so the JSON shape
          matches the on-disk ``associations.json`` from the legacy repo.
        """
        if relation in ("has_note", "has_resource", "has_plan"):
            return source, target
        if relation in cls._REFERENCE_RELS:
            t = target[len("concept:"):] if target.startswith("concept:") else target
            return source, t
        s = source[len("concept:"):] if source.startswith("concept:") else source
        t = target[len("concept:"):] if target.startswith("concept:") else target
        return s, t

    @classmethod
    def _denormalize_edge_endpoints(
        cls,
        relation: str,
        source: str,
        target: str,
    ) -> tuple[str, str]:
        """Inverse of :meth:`_normalize_edge_endpoints` — JSON → FalkorDB ids.

        ``associations.json`` stores ``part_of`` and semantic edges with bare
        concept names, while the ``has_*`` edges already carry id-style
        endpoints. :meth:`_write_edge` MATCHes on ``id``, so bare names must
        be re-prefixed before writing.
        """
        if relation in ("has_note", "has_resource", "has_plan"):
            return source, target
        if relation in cls._REFERENCE_RELS:
            t = target if target.startswith("concept:") else f"concept:{target}"
            return source, t
        s = source if source.startswith("concept:") else f"concept:{source}"
        t = target if target.startswith("concept:") else f"concept:{target}"
        return s, t

    def import_association_graph(
        self, domain: str, data: dict[str, Any],
    ) -> dict[str, int]:
        """Load an ``associations.json``-shaped dict into FalkorDB.

        Inverse of :meth:`export_graph`. Nodes are written before edges
        because :meth:`_write_edge` MATCHes both endpoints — an edge whose
        endpoints don't exist yet is silently dropped by Cypher.
        """
        if self._get_graph(domain) is None:
            return {"concepts": 0, "resources": 0, "edges": 0}

        concepts = 0
        for name, c in (data.get("concepts") or {}).items():
            if self.upsert_concept(
                domain,
                name=c.get("name") or name,
                level=int(c.get("level") or 0),
                is_root=bool(c.get("is_root")),
                description=c.get("description") or "",
            ):
                concepts += 1

        resources = 0
        for rid, r in (data.get("resources") or {}).items():
            payload = r.get("payload") or {}
            node = r.get("node") or ""
            summary = r.get("summary") or ""
            rtype = r.get("type")
            if rtype == "note":
                ok = self.upsert_note(
                    domain, node=node,
                    word_count=int(payload.get("word_count") or 0),
                    summary=summary,
                )
            elif rtype == "resource":
                ok = self.upsert_resource(
                    domain, node=node,
                    url=payload.get("url") or "",
                    title=payload.get("title") or "",
                    summary=summary,
                )
            elif rtype == "plan":
                ok = self.upsert_plan(
                    domain, node=node,
                    plan_id=payload.get("plan_id") or rid.rsplit(":", 1)[-1],
                    goal=payload.get("goal") or summary,
                    action_count=int(payload.get("action_count") or 0),
                    completed=int(payload.get("completed") or 0),
                )
            else:
                ok = False
            if ok:
                resources += 1

        edges = 0
        for a in (data.get("associations") or []):
            relation = str(a.get("relation") or "").lower()
            src, tgt = self._denormalize_edge_endpoints(
                relation, a.get("source") or "", a.get("target") or "",
            )
            if self.add_edge_any(
                domain, source=src, target=tgt, relation=relation,
                weight=float(a.get("weight") or 1.0),
                intensity=a.get("intensity") or "SOFT",
                evidence=a.get("evidence") or "",
                created_by=a.get("created_by") or "system",
            ):
                edges += 1

        # Mirror the per-domain metadata into a singleton ``:GraphMeta``
        # node so the values survive a later ``associations.json``
        # delete. Without this round-trip the export would have to fall
        # back to the max(updated_at) proxy, which loses the real
        # ``last_full_sync`` timestamp.
        metadata = data.get("metadata") or {}
        self.upsert_graph_meta(
            domain,
            last_full_sync=metadata.get("last_full_sync"),
            derived_events=metadata.get("derived_events") or {},
            schema_version=metadata.get("schema_version") or "2.0-graph",
        )

        return {"concepts": concepts, "resources": resources, "edges": edges}

    def clear_domain(self, domain: str) -> bool:
        """Delete all nodes and edges for a domain graph."""
        g = self._get_graph(domain)
        if g is None:
            return False
        try:
            g.query("MATCH (n) DETACH DELETE n")
            return True
        except Exception as e:
            logger.warning("clear_domain failed: %s", e)
            return False


__all__ = ["GraphStoreClient", "SEMANTIC_RELATIONS"]
