"""DossierReflector 集成测试。

模拟 LLM 返回 → 归档流程 → 验证:
- dossier.json 写入正确
- ActivityBus 发出 DOSSIER_ENTRY_ADDED 事件
- 老兵加成累加
- 没有挂载节点(空 node)的条目被丢弃
- LLM 返回损坏 JSON 时不抛异常
"""
import asyncio
import json
import shutil
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent.parent
TEST_DOMAIN = "__test_dossier_reflector__"
KB_ROOT = ROOT / "workspace" / "knowledge_bases"
TEST_DIR = KB_ROOT / TEST_DOMAIN


class MockChatModel:
    """伪装 BaseChatModel — 只需要 .invoke(prompt) → str。

    支持两种用法:
    - 单次响应(MockChatModel(text)):每次 invoke 都返回 text
    - 顺序响应(MockChatModel([text1, text2, ...])):第 N 次 invoke 返回 texts[N]
    """

    def __init__(self, response) -> None:
        if isinstance(response, str):
            self._responses = [response]
        else:
            self._responses = list(response)
        self.invoked: list[str] = []

    def invoke(self, prompt: str):
        self.invoked.append(prompt)
        idx = min(len(self.invoked) - 1, len(self._responses) - 1)
        return _MockResult(self._responses[idx])


class _MockResult:
    def __init__(self, content: str) -> None:
        self.content = content


class _Msg:
    def __init__(self, role: str, content: str) -> None:
        self.role = role
        self.content = content


def _setup():
    if TEST_DIR.exists():
        shutil.rmtree(TEST_DIR)
    (TEST_DIR / "notes" / "ChildB").mkdir(parents=True)
    (TEST_DIR / "notes" / "ChildB" / "note.md").write_text("X", encoding="utf-8")
    (TEST_DIR / "knowledge_graph.json").write_text(
        json.dumps({"domain": TEST_DOMAIN, "nodes": [
            {"name": "ChildB", "links": []}
        ]}, ensure_ascii=False),
        encoding="utf-8",
    )


def _cleanup():
    if TEST_DIR.exists():
        shutil.rmtree(TEST_DIR)


async def _run():
    from src.application.dossier_service import DossierService
    from src.agent.reflection.dossier_reflector import DossierReflector
    from src.infrastructure.repository.dossier_repo import DossierRepository
    from src.observability.activity_bus import (
        ActivityKind,
        get_activity_bus,
        reset_activity_bus,
    )

    reset_activity_bus()
    bus = get_activity_bus()
    captured: list[dict] = []

    async def capture(event):
        captured.append(event)

    await bus.subscribe(capture)

    print("=" * 70)
    print("DossierReflector 集成测试")
    print("=" * 70)

    repo = DossierRepository(KB_ROOT)
    svc = DossierService(dossier_repo=repo)

    # === Case 1: 正常归档 ===
    print("\n[Case 1] LLM 返回有效 JSON,正常归档")
    good_response = json.dumps([
        {
            "node": "ChildB",
            "type": "pitfall",
            "title": "asyncio cancel 后立即 gather 会丢异常",
            "body": "必须先 await asyncio.sleep(0)",
            "tags": "asyncio,cancel",
            "evidence": "用户原话:下次记住先 sleep(0)",
            "score": 0.85,
        },
        {
            "node": "ChildB",
            "type": "sop",
            "title": "如何启动异步任务",
            "body": "create_task 创建,gather 收集",
            "tags": "asyncio",
            "score": 0.7,
        },
        {
            "node": "",  # 没挂载节点,应被丢弃
            "type": "tip",
            "title": "无挂载节点的会被丢弃",
            "body": "不应该出现在 dossier 里",
            "score": 0.5,
        },
    ], ensure_ascii=False)

    # 两段式:第一次返回 classifier yes,第二次返回 extractor JSON
    llm = MockChatModel([
        '{"should_archive": true, "reason": "踩坑经验"}',
        good_response,
    ])
    reflector = DossierReflector(llm=llm, dossier_service=svc)

    msgs = [
        _Msg("user", "我刚踩了个坑:asyncio cancel 后立即 gather 把异常吞了"),
        _Msg("assistant", "是的,先 await asyncio.sleep(0) 再 gather"),
        _Msg("user", "好的,下次记住这个经验"),
    ]

    written = await reflector.reflect(
        domain=TEST_DOMAIN, messages=msgs, session_id="test-session-1",
    )
    print(f"    written_ids: {written}")
    assert len(written) == 2  # 第 3 条没挂载节点被丢弃
    print(f"    ✅ 归档 2 条(丢弃 1 条空 node)")

    dossier = await svc.view_dossier(TEST_DOMAIN, "ChildB")
    print(f"    ChildB 档案条目: {len(dossier.entries)}")
    for e in dossier.entries:
        print(f"      - [{e.type.value}] {e.title}")
    assert len(dossier.entries) == 2

    # 验证 ActivityBus 事件
    dossier_events = [e for e in captured if e["type"] == ActivityKind.DOSSIER_ENTRY_ADDED]
    print(f"    ActivityBus DOSSIER_ENTRY_ADDED 事件数: {len(dossier_events)}")
    for ev in dossier_events:
        print(f"      - {ev['title']}")
        assert ev["domain"] == TEST_DOMAIN
        assert ev["node"] == "ChildB"
        assert ev["source"] == "agent_reflection"
        assert ev["extra"]["session_id"] == "test-session-1"

    # === Case 2: 重复标题被 dedupe ===
    print("\n[Case 2] 同标题条目被 dedupe 跳过")
    captured.clear()
    written = await reflector.reflect(
        domain=TEST_DOMAIN, messages=msgs, session_id="test-session-2",
    )
    print(f"    重复触发后 written_ids: {written} (期望 [])")
    assert written == []
    dossier_events = [e for e in captured if e["type"] == ActivityKind.DOSSIER_ENTRY_ADDED]
    assert len(dossier_events) == 0
    print(f"    ✅ 重复归档被 dedupe 拦截")

    # === Case 3: LLM 返回损坏 JSON 不抛异常 ===
    print("\n[Case 3] LLM 返回损坏 JSON,reflector 吞掉异常")
    captured.clear()
    bad_llm = MockChatModel([
        '{"should_archive": true}',  # classifier yes
        "这是 markdown ``` not json",  # extractor 返回垃圾
    ])
    bad_reflector = DossierReflector(llm=bad_llm, dossier_service=svc)
    written = await bad_reflector.reflect(
        domain=TEST_DOMAIN, messages=msgs, session_id="test-session-3",
    )
    print(f"    损坏 JSON → written_ids: {written} (期望 [])")
    assert written == []
    dossier_events = [e for e in captured if e["type"] == ActivityKind.DOSSIER_ENTRY_ADDED]
    assert len(dossier_events) == 0
    print(f"    ✅ 损坏 JSON 不抛异常,主流程不受影响")

    # === Case 4: LLM 返回 markdown fence 包裹的 JSON ===
    print("\n[Case 4] LLM 返回 ```json``` 包裹的 JSON")
    fenced = (
        "```json\n" +
        json.dumps([{
            "node": "ChildB",
            "type": "tip",
            "title": "markdown fence 测试",
            "body": "strip 掉 fence 后能解析",
            "tags": "test",
            "score": 0.6,
        }], ensure_ascii=False) +
        "\n```"
    )
    fenced_llm = MockChatModel([
        '{"should_archive": true}',
        fenced,
    ])
    fenced_reflector = DossierReflector(llm=fenced_llm, dossier_service=svc)
    written = await fenced_reflector.reflect(
        domain=TEST_DOMAIN, messages=msgs, session_id="test-session-4",
    )
    print(f"    fenced JSON → written_ids: {written}")
    assert len(written) == 1
    print(f"    ✅ markdown fence 能正确解析")

    # === Case 5: 多次检索累加 use_count ===
    print("\n[Case 5] 检索累加 use_count")
    for i in range(3):
        hits = await svc.search(TEST_DOMAIN, "asyncio", top_k=5)
    dossier = await svc.view_dossier(TEST_DOMAIN, "ChildB")
    use_counts = sorted(e.use_count for e in dossier.entries)
    print(f"    use_counts: {use_counts}")
    assert use_counts[-1] >= 3  # 至少被搜过 3 次
    print(f"    ✅ use_count 累加")

    # === Case 6: 反射器不影响 dossier.json 文件结构 ===
    print("\n[Case 6] dossier.json 结构验证")
    path = KB_ROOT / TEST_DOMAIN / "notes" / "ChildB" / "dossier.json"
    assert path.exists()
    raw = json.loads(path.read_text(encoding="utf-8"))
    print(f"    keys: {sorted(raw.keys())}")
    assert "node" in raw
    assert "domain" in raw
    assert "entries" in raw
    print(f"    entries 数: {len(raw['entries'])}")
    for e in raw["entries"]:
        assert "id" in e
        assert "type" in e
        assert "title" in e
        assert "body" in e
        assert "tags" in e
        assert "score" in e
        assert "use_count" in e
        assert "created_by" in e
        assert "created_at" in e
        assert e["created_by"] == "agent"
    print(f"    ✅ dossier.json 结构完整")

    # === Case 7: 关键词预筛 — 短消息直接跳过,LLM 都不会被调 ===
    print("\n[Case 7] 关键词预筛 — 短问候不触发 LLM")
    short_msgs = [
        _Msg("user", "你好"),
        _Msg("assistant", "你好!有什么可以帮你?"),
        _Msg("user", "hi"),
    ]
    capture_llm = MockChatModel("这条永远不该被调")
    no_call_reflector = DossierReflector(llm=capture_llm, dossier_service=svc)
    written = await no_call_reflector.reflect(
        domain=TEST_DOMAIN, messages=short_msgs, session_id="s7",
    )
    assert written == []
    assert len(capture_llm.invoked) == 0
    print(f"    ✅ LLM 0 次调用,reflect 直接 short-circuit")

    # === Case 8: LLM 分类器返回 false → 跳过完整提取 ===
    print("\n[Case 8] LLM 分类器返回 should_archive=false")
    # 含触发词"复盘",预筛放行;但 LLM 判 false → 不走 extractor
    trivial_msgs = [
        _Msg("user", "今天我想复盘一下,想想最近做的事情"),
        _Msg("assistant", "好呀,你打算怎么总结?"),
        _Msg("user", "嗯,还没想好,先不总结吧"),
    ]
    classifier_no = MockChatModel(
        '{"should_archive": false, "reason": "闲聊,无复用价值"}',
    )
    no_archive_reflector = DossierReflector(
        llm=classifier_no, dossier_service=svc,
    )
    written = await no_archive_reflector.reflect(
        domain=TEST_DOMAIN, messages=trivial_msgs, session_id="s8",
    )
    assert written == []
    assert len(classifier_no.invoked) == 1  # 只调了 classifier,没调 extractor
    print(f"    ✅ LLM 说不要 → 跳过 extractor(只 1 次调用)")

    # === Case 9: LLM 分类器返回 true → 走完整提取 ===
    print("\n[Case 9] LLM 分类器返回 should_archive=true")
    classifier_yes = MockChatModel('{"should_archive": true, "reason": "SOP"}')
    # 第二次 invoke 返回 extract JSON
    class TwoStageLLM:
        def __init__(self):
            self.calls: list[str] = []

        def invoke(self, prompt: str):
            self.calls.append(prompt)
            if len(self.calls) == 1:
                return _MockResult(
                    '{"should_archive": true, "reason": "踩坑经验"}',
                )
            return _MockResult(json.dumps([{
                "node": "ChildB",
                "type": "tip",
                "title": "两段式判定测试",
                "body": "classifier yes → extractor 跑",
                "tags": "test",
                "score": 0.7,
            }], ensure_ascii=False))

    two_stage = TwoStageLLM()
    ts_reflector = DossierReflector(llm=two_stage, dossier_service=svc)
    # 用够长的对话触发(带"踩坑"触发词更稳)
    long_msgs = [
        _Msg("user", "我刚踩了一个坑,asyncio 的 cancel 行为跟我想的不一样"),
        _Msg("assistant", "是的,这个细节很容易踩坑,记下来下次注意"),
        _Msg("user", "好的,以后注意这一点,记到档案里方便以后参考"),
    ]
    written = await ts_reflector.reflect(
        domain=TEST_DOMAIN, messages=long_msgs, session_id="s9",
    )
    assert len(written) == 1
    assert len(two_stage.calls) == 2  # classifier + extractor
    print(f"    ✅ classifier true → extractor 跑(2 次调用)")

    # === Case 10: pre-filter 函数单测 ===
    print("\n[Case 10] _cheap_pre_filter 单测")
    from src.agent.reflection.dossier_reflector import _cheap_pre_filter
    # 短消息
    short = [_Msg("user", "hi")]
    assert _cheap_pre_filter(short) is False
    # 命中触发词(消息足够长 → 触发预筛放行)
    trigger = [_Msg("user",
        "我下次一定记住这个陷阱,以后别再犯同样错误,这种问题不应该再出现第二次,"
        "以前没注意,现在踩过了就要彻底避免再次发生才行",
    )]
    assert _cheap_pre_filter(trigger) is True
    # 长内容但无触发词 → 放给 LLM 判定
    long_no_trigger = [_Msg("user", "x" * 250)]
    assert _cheap_pre_filter(long_no_trigger) is True
    # 中等内容无触发词 → 跳过
    medium_no_trigger = [_Msg("user", "x" * 100)]
    assert _cheap_pre_filter(medium_no_trigger) is False
    print(f"    ✅ 预筛逻辑正确(短/触发词/长内容 三种 case)")

    # === Case 11: classifier JSON 容错 ===
    print("\n[Case 11] _parse_json_bool 容错")
    from src.agent.reflection.dossier_reflector import _parse_json_bool
    assert _parse_json_bool(
        '{"should_archive": true, "reason": "x"}',
    )["should_archive"] is True
    assert _parse_json_bool("garbage")["should_archive"] is False
    assert _parse_json_bool(
        '```json\n{"should_archive": false}\n```',
    )["should_archive"] is False
    print(f"    ✅ classifier JSON 解析容错正确")

    print("\n" + "=" * 70)
    print("✅ 反射器集成测试全部通过(11 个 case)")
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