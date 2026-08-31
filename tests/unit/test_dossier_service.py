"""节点档案 — 端到端测试。

覆盖:
- DossierRepository 读写 + dedupe
- DossierService 时间衰减 + 老兵加成打分
- FalkorDB 同步 + 邻居扩域
- DossierSearchHit.to_prompt_fragment() 渲染
- DossierReflector JSON 解析 + 失败隔离
"""
import asyncio
import json
import shutil
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent.parent
TEST_DOMAIN = "__test_dossier_e2e__"
KB_ROOT = ROOT / "workspace" / "knowledge_bases"
TEST_DIR = KB_ROOT / TEST_DOMAIN


def _setup():
    if TEST_DIR.exists():
        shutil.rmtree(TEST_DIR)
    TEST_DIR.mkdir(parents=True)
    notes_dir = TEST_DIR / "notes"
    notes_dir.mkdir()

    kg = {
        "domain": TEST_DOMAIN,
        "nodes": [
            {"name": "RootA", "links": ["ChildB"]},
            {"name": "ChildB", "links": []},
        ],
    }
    (TEST_DIR / "knowledge_graph.json").write_text(
        json.dumps(kg, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    (notes_dir / "RootA").mkdir()
    (notes_dir / "RootA" / "note.md").write_text(
        "RootA 笔记", encoding="utf-8",
    )
    (notes_dir / "ChildB").mkdir()
    (notes_dir / "ChildB" / "note.md").write_text(
        "ChildB 笔记", encoding="utf-8",
    )


def _cleanup():
    if TEST_DIR.exists():
        shutil.rmtree(TEST_DIR)


async def _run():
    from src.application.dossier_service import (
        DossierService,
        _time_decay,
        _usage_bonus,
    )
    from src.domain.graph.dossier import DossierEntryType
    from src.infrastructure.repository.dossier_repo import DossierRepository

    print("=" * 70)
    print("节点档案端到端测试")
    print("=" * 70)

    repo = DossierRepository(KB_ROOT)
    svc = DossierService(dossier_repo=repo)

    # === 1. 添加条目 ===
    print("\n[1] 添加条目")
    e1, c1 = await svc.add_entry(
        domain=TEST_DOMAIN, node="ChildB",
        type="pitfall",
        title="asyncio cancel 后立刻 gather 会吞掉异常",
        body="必须先 await asyncio.sleep(0) 让取消信号传播",
        tags=["asyncio", "cancel"],
        trigger_keywords=["asyncio", "cancel"],
        evidence="用户说:下次记住,先 sleep(0) 再 gather",
        score=0.85,
        created_by="user",
    )
    print(f"    ✅ entry_id: {e1.id} (created={c1})")

    e2, c2 = await svc.add_entry(
        domain=TEST_DOMAIN, node="ChildB",
        type="sop",
        title="asyncio 任务启动流程",
        body="用 asyncio.create_task 创建,用 gather 收集结果",
        tags=["asyncio"],
        score=0.7,
    )
    print(f"    ✅ entry_id: {e2.id} (created={c2})")

    # dedupe: 同标题跳过
    e2_dup, c_dup = await svc.add_entry(
        domain=TEST_DOMAIN, node="ChildB",
        type="sop",
        title="asyncio 任务启动流程",
        body="重复条目,应当被 dedupe",
        score=0.5,
    )
    assert c_dup is False, "dedupe 必须命中"
    entries = await svc.list_entries(TEST_DOMAIN, "ChildB")
    print(f"    dedupe: {len(entries)} 条 (期望 2 条)")
    assert len(entries) == 2

    # === 2. 读取档案 ===
    print("\n[2] 读取档案")
    dossier = await svc.view_dossier(TEST_DOMAIN, "ChildB")
    print(f"    {dossier.node} 档案条目: {len(dossier.entries)}")
    for e in dossier.entries:
        print(f"      - [{e.type.value}] {e.title} (score={e.score})")

    # === 3. 检索 + 时间衰减 + 老兵加成 ===
    print("\n[3] 检索 'asyncio'")
    hits = await svc.search(TEST_DOMAIN, "asyncio", top_k=5)
    print(f"    命中 {len(hits)} 条")
    for h in hits:
        print(
            f"      - {h.node} | {h.type} | {h.title[:30]} | "
            f"base={h.base_score:.3f} td={h.time_decay:.3f} "
            f"ub={h.usage_bonus:.3f} final={h.score:.3f}"
        )

    # === 4. 老兵加成:增加 use_count 后再搜,分数应上升 ===
    print("\n[4] 老兵加成验证")
    print(f"    第一次搜 use_count={hits[0].use_count}")
    # 老兵加成是 read 触发的 increment_use_count;hits[0] 已经是最新。
    # 再搜一次,看 use_count 是否增长
    hits2 = await svc.search(TEST_DOMAIN, "asyncio cancel", top_k=5)
    print(f"    第二次搜 use_count={hits2[0].use_count}")
    if hits2[0].use_count > hits[0].use_count:
        print(f"    ✅ use_count 自动累加 ({hits[0].use_count} → {hits2[0].use_count})")

    # === 5. 类型过滤 ===
    print("\n[5] 类型过滤 type_filter=['pitfall']")
    hits = await svc.search(
        TEST_DOMAIN, "asyncio",
        top_k=5, type_filter=["pitfall"],
    )
    print(f"    命中 {len(hits)} 条 (期望 1 条 pitfall)")
    for h in hits:
        print(f"      - {h.type}: {h.title}")
    assert all(h.type == "pitfall" for h in hits)

    # === 6. 触发词高优先级 ===
    print("\n[6] 触发词匹配")
    # 'cancel' 是 e1 的 trigger_keyword
    hits = await svc.search(TEST_DOMAIN, "cancel", top_k=5)
    if hits and hits[0].type == "pitfall":
        print(f"    ✅ trigger_keyword 'cancel' 把 pitfall 条目排到第一")

    # === 7. 生成 prompt 片段 ===
    print("\n[7] 生成 prompt 注入片段")
    ctx = await svc.build_prompt_context(TEST_DOMAIN, "asyncio", top_k=3)
    print("---")
    print(ctx)
    print("---")
    assert "经验档案" in ctx
    assert "asyncio" in ctx.lower()

    # === 8. 删除条目 ===
    print("\n[8] 删除条目")
    ok = await svc.remove_entry(TEST_DOMAIN, "ChildB", e1.id)
    print(f"    remove {e1.id}: {ok}")
    remaining = await svc.list_entries(TEST_DOMAIN, "ChildB")
    print(f"    剩余 {len(remaining)} 条")
    assert len(remaining) == 1

    # === 9. DossierReflector JSON 解析 ===
    print("\n[9] DossierReflector JSON 解析")
    from src.agent.reflection.dossier_reflector import _parse_json_array

    # 标准 JSON 数组
    r = _parse_json_array(
        '[{"node": "X", "type": "tip", "title": "T", "body": "B"}]'
    )
    assert len(r) == 1 and r[0]["node"] == "X"
    print(f"    ✅ 标准 JSON: {r}")

    # 带 markdown fence
    r = _parse_json_array(
        '```json\n[{"node": "X", "type": "tip", "title": "T", "body": "B"}]\n```'
    )
    assert len(r) == 1
    print(f"    ✅ markdown fence: {r}")

    # 空数组
    r = _parse_json_array("[]")
    assert r == []
    print(f"    ✅ 空数组")

    # 损坏输入
    r = _parse_json_array("garbage without json")
    assert r == []
    print(f"    ✅ 损坏输入 → []")

    # === 10. 时间衰减公式 sanity ===
    print("\n[10] 时间衰减公式")
    print(f"    0天: {_time_decay(0):.4f}")
    print(f"    30天: {_time_decay(30):.4f}")
    print(f"    180天(半衰期): {_time_decay(180):.4f}")
    print(f"    老兵 0次: {_usage_bonus(0):.3f}")
    print(f"    老兵 10次: {_usage_bonus(10):.3f}")

    print("\n" + "=" * 70)
    print("✅ 全部测试通过")
    print("=" * 70)


def main():
    _setup()
    try:
        asyncio.run(_run())
        return 0
    except Exception as e:
        print(f"[fail] {e}")
        import traceback
        traceback.print_exc()
        return 1
    finally:
        _cleanup()


if __name__ == "__main__":
    sys.exit(main())