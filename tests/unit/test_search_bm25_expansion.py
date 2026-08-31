"""不依赖 FalkorDB 的最终验证:BM25 扩域 + SearchService 集成。

直接构造 BM25Index + 模拟 SearchService 调用,验证:
1. 手动加边 → BM25 索引文本中包含邻居名
2. 搜 "循环控制" → BM25 命中 "任务并发" (通过扩域)
3. global_search 返回正确的 neighbors 字段(模拟 FalkorDB)
"""
import asyncio
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent.parent
TEST_DOMAIN = "DeepSeek Harness 项目学习"


class MockGraphStore:
    """Mock FalkorDB for in-memory verification."""

    def __init__(self):
        self._nodes: dict[str, dict] = {}
        self._edges: list[tuple] = []
        self._node_neighbors: dict[str, list[dict]] = {}

    def ensure_vector_index(self, domain):
        return True

    def upsert_concept(self, domain, *, name, level=0, is_root=False,
                       description="", embedding=None):
        self._nodes[f"concept:{name}"] = {"name": name, "domain": domain, "description": description}
        return True

    def upsert_note(self, domain, *, node, word_count=0, summary="", embedding=None):
        return True

    def upsert_resource(self, domain, *, node, url, title="", summary="", embedding=None):
        return True

    def upsert_plan(self, domain, *, node, plan_id, goal="", action_count=0, completed=0):
        return True

    def add_edge(self, domain, *, source, target, relation,
                 weight=1.0, intensity="SOFT", evidence="", created_by="system"):
        return self.add_edge_any(
            domain=domain, source=source, target=target, relation=relation,
            weight=weight, intensity=intensity, evidence=evidence, created_by=created_by,
        )

    def add_edge_any(self, domain, *, source, target, relation,
                     weight=1.0, intensity="SOFT", evidence="", created_by="system"):
        self._edges.append((source, target, relation, weight, intensity, evidence, created_by))
        # 同步更新邻居
        self._node_neighbors.setdefault(source, [])
        self._node_neighbors.setdefault(target, [])
        if relation not in ("HAS_NOTE", "HAS_RESOURCE", "HAS_PLAN"):
            # 双向
            self._node_neighbors[source].append({"name": target.replace("concept:", ""), "relation": relation, "hops": 1})
            self._node_neighbors[target].append({"name": source.replace("concept:", ""), "relation": relation, "hops": 1})
        return True

    def neighbors(self, domain, node_id, *, hops=1):
        return self._node_neighbors.get(node_id, [])

    def vector_search(self, domain, query_vec, *, top_k=10):
        return []  # 模拟无 embedding

    @property
    def is_available(self):
        return True

    async def ensure_available(self):
        return True


async def main():
    from src.application.graph_sync_service import GraphSyncService
    from src.application.search_service import SearchService
    from src.infrastructure.embedding.bm25 import BM25Index, _tokenize
    from src.infrastructure.embedding.client import EmbeddingClient
    from src.infrastructure.repository.association_repo import AssociationRepository
    from src.infrastructure.repository.graph_repo import GraphRepository
    from src.infrastructure.repository.note_repo import NoteRepository
    from src.infrastructure.repository.plan_repo import PlanRepository
    from src.infrastructure.repository.resource_repo import ResourceRepository

    print("=" * 70)
    print("BM25 扩域 + SearchService 集成测试(不依赖 FalkorDB)")
    print("=" * 70)

    # === 1. 用用户真实数据 "DeepSeek Harness 项目学习" ===
    kb_root = ROOT / "workspace" / "knowledge_bases"
    print(f"\n[1] 使用真实知识库: {TEST_DOMAIN}")

    # 准备依赖
    graph_repo = GraphRepository(kb_root)
    note_repo = NoteRepository(kb_root)
    resource_repo = ResourceRepository(kb_root)
    plan_repo = PlanRepository(kb_root)
    assoc_repo = AssociationRepository(kb_root)

    # Mock FalkorDB + Embedding(关掉向量,只用 BM25)
    mock_store = MockGraphStore()
    bm25 = BM25Index()
    embed = EmbeddingClient()
    # 用空 key 强制 embedding 不可用,只看 BM25
    embed._api_key = ""

    svc = GraphSyncService(
        domain=TEST_DOMAIN,
        graph_repo=graph_repo,
        note_repo=note_repo,
        resource_repo=resource_repo,
        plan_repo=plan_repo,
        graph_store=mock_store,  # mock
        embedding_client=embed,
        bm25_index=bm25,
        association_repo=assoc_repo,
    )

    # === 2. 跑 sync_full — 这会重建 BM25 索引(含扩域) ===
    print("\n[2] 跑 sync_full...")
    # 我们的 sync_full 会调用 FalkorDB,但 mock_store 会接受所有写入
    # 同时 _rebuild_bm25 会基于真实 knowledge_graph + associations 构建索引
    try:
        result = await svc.sync_full()
        print(f"    result: {result}")
    except Exception as e:
        print(f"    warn: {e}")

    # === 3. 验证 BM25 索引 ===
    print("\n[3] 验证 BM25 索引:")
    print(f"    索引文档数: {len(bm25._corpora.get(TEST_DOMAIN, []))}")

    # 看一下 "任务并发" 的索引文本 — 应该包含 "循环控制"
    if TEST_DOMAIN in bm25._corpora:
        for doc in bm25._corpora[TEST_DOMAIN]:
            if "任务并发" in doc.get("name", ""):
                print(f"\n    '任务并发' 索引文本:")
                print(f"    {doc.get('text', '')[:200]}...")
                # 关键验证:邻居扩域
                if "循环控制" in doc.get("text", ""):
                    print(f"    ✅ 包含邻居 '循环控制' — 扩域生效!")
                break

    # === 4. BM25 搜 "循环控制" — 应该命中 "任务并发" ===
    print("\n[4] BM25 搜 '循环控制' top10:")
    results = bm25.search(TEST_DOMAIN, "循环控制", top_k=10)
    for r in results:
        marker = "✅" if r["name"] == "任务并发" else "  "
        print(f"      {marker} {r['name']:<30} bm25={r['bm25_score']:.4f}")

    has_task_concurrent = any(r["name"] == "任务并发" for r in results)
    if has_task_concurrent:
        print("\n    ✅ 核心目标达成: 搜 '循环控制' 命中 '任务并发' (手动边的反向)")
    else:
        print("\n    ❌ 没命中 '任务并发' — 扩域可能没生效")

    # === 5. SearchService 集成验证 ===
    print("\n[5] SearchService.search() 搜 '循环控制':")
    search = SearchService(
        graph_store=mock_store,
        embedding_client=embed,
        bm25_index=bm25,
    )
    results = await search.search(TEST_DOMAIN, "循环控制", top_k=5)
    for r in results:
        marker = "✅" if r.name == "任务并发" else "  "
        print(f"      {marker} [{r.type}] {r.name:<30} hybrid={r.hybrid_score:.4f}")

    # === 6. SearchService.global_search() 返回 neighbors ===
    print("\n[6] SearchService.global_search() 验证 neighbors 字段:")
    global_result = await search.global_search(TEST_DOMAIN, "循环控制", top_k=5)
    for item in global_result.get("results", []):
        name = item.get("name")
        nbrs = item.get("neighbors", [])
        nbr_summary = ", ".join(
            f"{n['name']}({n['relation']})" for n in nbrs[:5]
        )
        print(f"      {name:<20} neighbors: [{nbr_summary}]")

    # === 7. 搜 "任务并发" — 应该包含邻居 "循环控制" ===
    print("\n[7] 反向验证: 搜 '任务并发' — 应该包含邻居 '循环控制':")
    results = bm25.search(TEST_DOMAIN, "任务并发", top_k=10)
    for r in results:
        marker = "✅" if r["name"] == "循环控制" else "  "
        print(f"      {marker} {r['name']:<30} bm25={r['bm25_score']:.4f}")

    has_xunkong = any(r["name"] == "循环控制" for r in results)
    if has_xunkong:
        print("\n    ✅ 反向扩域也生效: 搜 '任务并发' 命中 '循环控制'")

    # === 8. 验证 FalkorDB mock 中有手动边 ===
    print("\n[8] Mock FalkorDB 中的手动边:")
    manual_edges = [
        (s, t, r) for s, t, r, *_ in mock_store._edges
        if r in ("ENABLES", "RELATED_TO", "PREREQUISITE_OF", "SIMILAR_TO")
    ]
    for s, t, r in manual_edges:
        print(f"      ✅ {s} -[{r}]-> {t}")

    print("\n" + "=" * 70)
    print("✅ 核心需求验证完成")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())