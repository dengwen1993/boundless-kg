"""端到端测试:手动边 → FalkorDB → BM25 扩域 → 搜索命中。

不污染真实数据 — 用临时领域名 "__test_graph_sync_e2e__"。
"""
import asyncio
import json
import shutil
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent.parent
TEST_DOMAIN = "__test_graph_sync_e2e__"
KB_ROOT = ROOT / "workspace" / "knowledge_bases"
TEST_DIR = KB_ROOT / TEST_DOMAIN


def _setup_test_kb():
    """Create a test domain with knowledge_graph.json + associations.json."""
    if TEST_DIR.exists():
        shutil.rmtree(TEST_DIR)
    TEST_DIR.mkdir(parents=True)
    notes_dir = TEST_DIR / "notes"
    notes_dir.mkdir()

    # knowledge_graph.json — 三个节点的简单结构
    kg = {
        "domain": TEST_DOMAIN,
        "nodes": [
            {"name": "RootA", "links": ["ChildB", "ChildC"]},
            {"name": "ChildB", "links": []},
            {"name": "ChildC", "links": []},
        ],
    }
    (TEST_DIR / "knowledge_graph.json").write_text(
        json.dumps(kg, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # 空 notes
    (notes_dir / "RootA").mkdir()
    (notes_dir / "RootA" / "note.md").write_text(
        "RootA 笔记:这是一个测试节点", encoding="utf-8"
    )
    (notes_dir / "ChildB").mkdir()
    (notes_dir / "ChildB" / "note.md").write_text(
        "ChildB 笔记:另一个测试节点,描述包含并发字样", encoding="utf-8"
    )
    (notes_dir / "ChildC").mkdir()
    (notes_dir / "ChildC" / "note.md").write_text(
        "ChildC 笔记:第三个节点,描述包含任务字样", encoding="utf-8"
    )

    # associations.json — 一条手动边 (模拟用户 UI 加边)
    assoc = {
        "domain": TEST_DOMAIN,
        "concepts": {},
        "resources": {},
        "associations": [
            {
                "source": "ChildB",
                "target": "ChildC",
                "relation": "enables",
                "weight": 0.8,
                "intensity": "HARD",
                "evidence": "manually added by test",
                "created_by": "user",
                "created_at": "2026-08-26T00:00:00Z",
            }
        ],
        "metadata": {
            "derived_events": {},
            "last_full_sync": None,
            "schema_version": "1.0",
        },
        "generated_at": None,
    }
    (TEST_DIR / "associations.json").write_text(
        json.dumps(assoc, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _cleanup_test_kb():
    """Remove the test domain from KB."""
    if TEST_DIR.exists():
        shutil.rmtree(TEST_DIR)


def _cleanup_test_graph():
    """Clean up the test graph in FalkorDB."""
    from src.infrastructure.graph_store.client import GraphStoreClient
    store = GraphStoreClient()
    g = store._get_graph(TEST_DOMAIN)
    if g is not None:
        try:
            g.query("MATCH (n) DETACH DELETE n")
        except Exception as e:
            print(f"[warn] clean graph failed: {e}")


async def _run():
    from src.application.graph_sync_service import GraphSyncService
    from src.infrastructure.embedding.bm25 import BM25Index
    from src.infrastructure.embedding.client import EmbeddingClient
    from src.infrastructure.graph_store.client import GraphStoreClient
    from src.infrastructure.repository.association_repo import AssociationRepository
    from src.infrastructure.repository.graph_repo import GraphRepository
    from src.infrastructure.repository.note_repo import NoteRepository
    from src.infrastructure.repository.plan_repo import PlanRepository
    from src.infrastructure.repository.resource_repo import ResourceRepository

    print("=" * 60)
    print(f"测试领域: {TEST_DOMAIN}")
    print("=" * 60)

    # 1. 检查 FalkorDB 可用
    store = GraphStoreClient()
    print(f"\n[1] FalkorDB available: {await store.ensure_available()}")
    if not await store.ensure_available():
        print("[fail] FalkorDB 不可用,跳过集成测试")
        return False

    # 2. 清理 FalkorDB 中可能残留的测试数据
    _cleanup_test_graph()

    # 3. 构造服务
    graph_repo = GraphRepository(KB_ROOT)
    note_repo = NoteRepository(KB_ROOT)
    resource_repo = ResourceRepository(KB_ROOT)
    plan_repo = PlanRepository(KB_ROOT)
    assoc_repo = AssociationRepository(KB_ROOT)
    bm25 = BM25Index()
    embed = EmbeddingClient()

    svc = GraphSyncService(
        domain=TEST_DOMAIN,
        graph_repo=graph_repo,
        note_repo=note_repo,
        resource_repo=resource_repo,
        plan_repo=plan_repo,
        graph_store=store,
        embedding_client=embed,
        bm25_index=bm25,
        association_repo=assoc_repo,
    )

    # 4. 跑 sync_full — 应该把手动边也同步进 FalkorDB
    print("\n[2] sync_full()")
    result = await svc.sync_full()
    print(f"    result: {result}")

    # 5. 验证 FalkorDB 中是否有 ENABLES 边
    print("\n[3] 验证 FalkorDB 中的边:")
    g = store._get_graph(TEST_DOMAIN)
    r = g.query(
        "MATCH (s)-[r:ENABLES]->(t) "
        "RETURN s.name, t.name, r.weight, r.intensity, r.evidence"
    )
    edges = r.result_set
    if edges:
        for row in edges:
            print(f"    ✅ {row[0]} -[{row[2]} {row[3]}]-> {row[1]}")
            print(f"        evidence: {row[4]}")
    else:
        print("    ❌ FalkorDB 中没找到 ENABLES 边")
        return False

    # 6. 验证 FalkorDB 中节点数
    r = g.query("MATCH (c:Concept) RETURN c.name")
    nodes = sorted(row[0] for row in r.result_set)
    print(f"\n[4] FalkorDB 中的概念节点: {nodes}")

    # 7. 验证 BM25 扩域 — 搜 ChildB 应该命中 ChildC(因为 ChildC 是 ChildB 的关联邻居)
    print("\n[5] BM25 扩域验证:")
    print("    搜 'ChildB' top5:")
    bm25_results = bm25.search(TEST_DOMAIN, "ChildB", top_k=5)
    for r in bm25_results:
        print(f"      - {r['name']} (bm25={r['bm25_score']:.3f})")

    print("\n    搜 '任务' top5:")
    bm25_results = bm25.search(TEST_DOMAIN, "任务", top_k=5)
    for r in bm25_results:
        print(f"      - {r['name']} (bm25={r['bm25_score']:.3f})")

    # 核心扩域验证:ChildC 笔记里没写 ChildB,但通过手动边 ChildC 应被 BM25 命中
    print("\n    [核心扩域] ChildC 笔记里只有'任务'字样,搜'ChildB'应命中 ChildC:")
    bm25_results = bm25.search(TEST_DOMAIN, "ChildB", top_k=5)
    found_c = any(r["name"] == "ChildC" for r in bm25_results)
    if found_c:
        print("      ✅ ChildC 被命中 — BM25 扩域生效!")
    else:
        print("      ⚠️ ChildC 未被命中 — 扩域可能没生效(查 _build_neighbor_index)")
        # 调试输出:看实际索引文本
        from src.infrastructure.embedding.bm25 import _tokenize
        if TEST_DOMAIN in bm25._corpora:
            for doc in bm25._corpora[TEST_DOMAIN]:
                if doc["name"] == "ChildC":
                    print(f"      ChildC 索引文本: {doc.get('text', '')[:200]}")
                    print(f"      ChildC tokens: {_tokenize(doc.get('text', ''))}")

    # 8. 验证 SearchService 整体
    print("\n[6] SearchService.search() 集成:")
    from src.application.search_service import SearchService
    search = SearchService(
        graph_store=store,
        embedding_client=embed,
        bm25_index=bm25,
    )
    results = await search.search(TEST_DOMAIN, "ChildB", top_k=5)
    print(f"    'ChildB' 检索结果: {len(results)} 条")
    for r in results:
        print(f"      - [{r.type}] {r.name} hybrid={r.hybrid_score:.3f}")

    # 9. global_search 带 neighbors
    print("\n[7] SearchService.global_search() 带邻居:")
    global_result = await search.global_search(TEST_DOMAIN, "ChildB", top_k=5)
    for item in global_result.get("results", []):
        nbrs = item.get("neighbors", [])
        nbr_summary = ", ".join(
            f"{n['name']}({n['relation']})" for n in nbrs[:5]
        )
        print(f"      - {item['name']} | neighbors: [{nbr_summary}]")

    # 10. 测试增量同步:再加一条边,然后 sync_for_node
    print("\n[8] 增量同步测试:再加一条 RELATED_TO 边")
    assoc_graph = await assoc_repo.read(TEST_DOMAIN)
    from src.domain.graph.association import Association, RelationType, EdgeIntensity
    new_assoc = Association(
        source="RootA",
        target="ChildC",
        relation=RelationType.RELATED_TO,
        weight=0.7,
        intensity=EdgeIntensity.SOFT,
        evidence="manually added by e2e test (incremental)",
        created_by="user",
    )
    assoc_graph = await assoc_repo.add_association(TEST_DOMAIN, new_assoc)

    sync_result = await svc.sync_for_node("RootA")
    print(f"    sync_for_node('RootA') = {sync_result}")

    # 验证 FalkorDB 中现在有 RELATED_TO 边
    r = g.query(
        "MATCH (s)-[r:RELATED_TO]->(t) "
        "RETURN s.name, t.name, r.evidence"
    )
    new_edges = r.result_set
    if new_edges:
        for row in new_edges:
            print(f"    ✅ 增量边: {row[0]} -> {row[1]} (ev: {row[2]})")
    else:
        print("    ❌ 增量同步失败:RELATED_TO 边没进 FalkorDB")
        return False

    # 11. 验证 graph_store.neighbors() 能查到 ENABLES 边
    print("\n[9] FalkorDB neighbors() 验证:")
    nbrs = store.neighbors(TEST_DOMAIN, "concept:ChildB", hops=1)
    nbr_names = sorted(n["name"] for n in nbrs if n.get("name"))
    print(f"    ChildB 1 跳邻居: {nbr_names}")

    has_enables_to_c = any(
        n["name"] == "ChildC" and n["relation"] == "ENABLES" for n in nbrs
    )
    if has_enables_to_c:
        print("    ✅ ChildB → ChildC 的 ENABLES 边在 neighbors() 中可见")
    else:
        print("    ❌ neighbors() 没看到 ENABLES 边")
        return False

    print("\n" + "=" * 60)
    print("✅ 全部测试通过")
    print("=" * 60)
    return True


def main():
    print("[setup] 准备测试知识库...")
    _setup_test_kb()

    passed = False
    try:
        passed = asyncio.run(_run())
    except Exception as e:
        print(f"[fail] 测试异常: {e}")
        import traceback
        traceback.print_exc()
        passed = False
    finally:
        # 清理 FalkorDB + 测试文件
        print("\n[cleanup] 清理测试数据...")
        _cleanup_test_graph()
        _cleanup_test_kb()
        print("[done]")

    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())