"""Graph CRUD tools (LangChain @tool wrappers around GraphService).

All tools catch exceptions and return friendly error messages instead
of letting ValueError / KeyError propagate and crash the agent event
stream.

Activity timeline
-----------------

Write tools (kg_add_node / kg_add_subtree / kg_delete_node /
kg_update_node) emit events on the activity bus with
``source="agent"`` so the timeline distinguishes agent-driven changes
from manual UI clicks.
"""

from __future__ import annotations

import json
import re
from typing import Any

from langchain_core.tools import tool

from src.agent.dependencies import get_graph_repo, get_graph_service, get_graph_sync_service
from src.domain.graph.decorator import decorate_graph
from src.observability.activity_bus import ActivityKind, get_activity_bus
from src.observability.logged_tool import logged_tool
from src.utils.json_repair import try_parse_json


@tool
@logged_tool
async def kg_list_domains() -> str:
    """列出所有知识图谱领域；返回 JSON 字符串。"""
    svc = get_graph_service()
    items = await svc.list_domains()
    return json.dumps([i.model_dump() for i in items], ensure_ascii=False)


@tool
@logged_tool
async def kg_view_graph(domain: str) -> str:
    """查看某个领域的完整图谱。"""
    svc = get_graph_service()
    g = await svc.view(domain)
    return g.model_dump_json()


@tool
@logged_tool
async def kg_add_node(domain: str, name: str, parent: str = "", links: Any = None) -> str:
    """往指定领域增加一个节点；可选 parent 让父节点 links 自动追加。

    ``links`` 在类型注解上是 ``Any`` 而不是 ``list[str]``，因为 LangChain 的
    ``@tool`` 会在调用前用生成的 Pydantic schema 严格校验参数——deepagents SDK
    偶发把列表序列化进 ``{"item": [...]}`` 容器，没经过宽松化直接拒。
    在这里再走一遍 ``_coerce_links`` 把所有乱七八糟的形态都归一化成 ``list[str]``。
    """
    links = _coerce_links(links or [])

    # Snapshot whether this is the first write into a brand-new domain.
    # ``add_node`` auto-creates the per-domain JSON file when the path
    # does not yet exist, so we must detect that BEFORE the write — once
    # the file is on disk we can no longer distinguish "create" from
    # "append".  Used to emit a DOMAIN_CREATED event alongside the
    # NODE_CREATED so the activity timeline shows a clean "新建领域"
    # line the first time a domain appears.
    repo = get_graph_repo()
    is_new_domain = not await repo.domain_exists(domain)

    svc = get_graph_service()
    g = await svc.add_node(domain, name, links=links, parent=parent or None)

    bus = get_activity_bus()
    if is_new_domain:
        await bus.emit(
            ActivityKind.DOMAIN_CREATED,
            domain=domain,
            node=name,
            title=f"新建了领域「{domain}」",
            source="agent",
            ref=f"domain:{domain}",
            extra={"first_node": name},
        )

    # Activity timeline — agent-driven node creation.  Emit only
    # after GraphService.add_node has succeeded so a refused add
    # (duplicate, etc.) does not pollute the log.
    await bus.emit(
        ActivityKind.NODE_CREATED,
        domain=domain,
        node=name,
        title=f"新建了节点「{name}」",
        source="agent",
        ref=f"node:{name}",
        extra={"parent": parent} if parent else None,
    )

    return f"✅ 已添加 {name!r}（{domain}, 当前节点数={len(g.nodes)}）"


def _coerce_node(n: Any, path: str = "nodes") -> dict[str, Any]:
    """Normalize one item of ``nodes`` from ``kg_add_subtree``.

    Some model adapters wrap dict arguments as ``{"$text": "<json string>"}``
    before handing them to the tool.  Unwrap that form so we end up with a
    plain ``{"name": str, "links": list[str]}`` dict.

    Supported shapes (in priority order):
      1. ``{"name": "...", "links": [...]}``  — plain dict
      2. ``{"$text": "<json string>"}`` / ``{"text": ...}`` / ``{"value": ...}``
      3. ``"<json string>"``  — bare JSON string element

    Raises ``KeyError`` with the offending ``path`` (e.g. ``"nodes[3]"``,
    ``"nodes[2].children[1]"``) so the LLM can fix the exact entry.
    """
    # Shape 1: plain dict with name
    if isinstance(n, dict) and "name" in n:
        return n

    # Shape 2: dict wrapping a JSON string under $text / text / value
    if isinstance(n, dict):
        wrapped = n.get("$text") or n.get("text") or n.get("value")
        if isinstance(wrapped, str):
            parsed = try_parse_json(wrapped.strip())
            if parsed is None:
                raise KeyError(
                    f"{path}: $text 不是合法 JSON（即使经过修复），"
                    f"内容前 80 字符：{wrapped.strip()[:80]!r}"
                )
            n = parsed

    # Shape 3: bare JSON string
    if isinstance(n, str):
        parsed = try_parse_json(n.strip())
        if parsed is None:
            raise KeyError(
                f"{path}: 字符串不是合法 JSON（即使经过修复），"
                f"内容前 80 字符：{n.strip()[:80]!r}"
            )
        n = parsed

    # Final validation
    if isinstance(n, dict) and "name" in n:
        return n

    raise KeyError(
        f"{path}: expected {{'name': ..., 'links': [...]}} "
        f"or {{'$text': '<json>'}}, got {type(n).__name__}"
    )


def _walk_tree(node: Any, path: str = "nodes") -> tuple[list[dict[str, Any]], dict[str, list[str]]]:
    """Recursively flatten a (possibly tree-shaped) node spec into a flat list.

    Input ``node`` may be any shape accepted by ``_coerce_node`` — including
    the ``$text``/bare-string wrappers.  Adds support for an optional
    ``children`` field (also accepted as ``child`` / ``kids`` for adapter
    forgiveness) that recursively describes the node's descendants.

    Returns:
        flat: ``[{name, links}, ...]`` one entry per node in pre-order
        parent_links: ``{parent_name: [child_name, ...]}`` tree-wiring map;
            each entry tells ``GraphRepository.add_subtree`` to add the
            children to the parent's ``links`` field after insert.

    Raises ``KeyError`` with the offending path on malformed input so
    the LLM can fix the exact subtree.
    """
    n = _coerce_node(node, path)
    raw_name = n.get("name")
    if not isinstance(raw_name, str) or not raw_name.strip():
        raise KeyError(f"{path}: 节点缺少有效 name 字段（实际: {raw_name!r}）")
    name = raw_name.strip()

    links = _coerce_links(n.get("links"))

    flat: list[dict[str, Any]] = [{"name": name, "links": links}]
    parent_links: dict[str, list[str]] = {}

    children_raw = n.get("children") or n.get("child") or n.get("kids")
    if children_raw is not None:
        if not isinstance(children_raw, list):
            raise KeyError(
                f"{path}.children: 必须是列表，实际类型: {type(children_raw).__name__}"
            )
        child_names: list[str] = []
        for ci, child in enumerate(children_raw):
            sub_flat, sub_wiring = _walk_tree(child, f"{path}.children[{ci}]")
            flat.extend(sub_flat)
            parent_links.update(sub_wiring)
            child_names.append(sub_flat[0]["name"])
        if child_names:
            parent_links[name] = child_names

    return flat, parent_links


def _coerce_links(raw: Any) -> list[str]:
    """Normalize a node's ``links`` field into ``list[str]``.

    ``Node.links`` is ``list[str]``, but LLM adapters deliver this field
    in several broken shapes.  Each one crashed
    ``Node(links=...)`` with a pydantic ``list_type`` error before this
    helper existed:

      * ``{"item": [...]}``     — XML/JSON-schema round-trip artifact,
        the array got wrapped in a container object (seen in production)
      * ``""`` / ``"   "``      — "no links" expressed as empty string
      * ``'["a", "b"]'``        — the array serialized to a JSON string
      * ``"a, b"``              — a delimiter-joined string
      * ``None``                — explicit null

    Anything unrecognized degrades to ``[]`` rather than raising: a node
    with no cross-links is valid and far better than losing the whole
    batch over a malformed optional field.
    """
    if raw is None:
        return []

    # Unwrap single-key container objects: {"item": [...]}, {"links": [...]}, …
    if isinstance(raw, dict):
        for key in ("item", "items", "link", "links", "$text", "text", "value"):
            if key in raw:
                return _coerce_links(raw[key])
        # Unkeyed fallback — flatten every value we can recognize.
        out: list[str] = []
        for v in raw.values():
            out.extend(_coerce_links(v))
        return out

    if isinstance(raw, str):
        s = raw.strip()
        if not s:
            return []
        # A JSON-serialized array/object that leaked through as a string.
        if s[0] in "[{":
            try:
                return _coerce_links(json.loads(s))
            except json.JSONDecodeError:
                pass
        # Delimiter-joined: "a, b" / "a、b" / "a; b"
        parts = [p.strip() for p in re.split(r"[,;、，；]", s)]
        return [p for p in parts if p]

    if isinstance(raw, (list, tuple, set)):
        out = []
        for item in raw:
            if isinstance(item, str):
                if item.strip():
                    out.append(item.strip())
            elif isinstance(item, dict):
                # [{"name": "x"}] — take the name.
                name = item.get("name") or item.get("$text") or item.get("value")
                if isinstance(name, str) and name.strip():
                    out.append(name.strip())
            elif item is not None:
                out.append(str(item))
        return out

    return []


@tool
@logged_tool
async def kg_add_subtree(
    domain: str,
    nodes: Any = None,
    parent: str = "",
) -> str:
    """一次性插入整棵子树。一次调用 → 前端可立即展开父节点。

    **两种输入形态（推荐树形）**：

      1. 树形（推荐）：``[{name, links?, children?: [{name, links?, children?: ...}]}]``
         树的父子关系会被**自动转写**为 ``parent.links → children`` 的边，
         整棵子树一次原子写入，不需要事后 ``kg_update_node`` 回写 links。
      2. 平铺（兼容老用法）：``[{name, links?: [...]}, ...]``，所有新节点
         共享 ``parent``（如有），行为与历史版本一致。

    **失败前置校验**（提交前发现，不写半套数据）：

      - ``links`` 字段里引用到的节点名：必须出现在本批次新增节点中，
        或本来就在该领域的图谱里。否则直接返回错误，列出所有缺失节点，
        让 LLM 先补建再重试（不再静默塞破链）。
      - 同批次内**节点名重复**：返回错误而不是默默覆盖。
      - 单条格式错误被跳过并在返回信息中报告（部分成功策略）。

    **自动建父**：当 ``parent`` 指定且该父节点不存在时，自动建一个
    空壳父节点（``links=[]``），保证一次调用就能让前端展开看到一个
    完整子树（之前必须先用 ``kg_add_node`` 建父 + ``kg_add_subtree``
    加子 + ``kg_update_node`` 回写 links — 共三次工具调用）。

    兼容模型把结构化参数序列化成 ``{"$text": "<json>"}`` / 裸 JSON 字符串
    的形态（含顶层和每条节点各自的形态）。
    """
    from src.domain.graph.models import Node

    # ── Top-level deserialization ──
    # LLM may wrap the entire ``nodes`` argument as
    # ``{"$text": "<json string>"}`` or pass a bare JSON string.
    # Also: some SDK adapters serialize a single-node spec as a bare
    # ``{name, links}`` dict instead of a one-element list. Auto-wrap
    # those so the call still succeeds instead of triggering a hard error.
    # ``nodes`` is ``Any`` in the signature (instead of ``list[dict]``)
    # because LangChain's ``@tool`` validates the annotation strictly
    # before we get to run — a dict-wrapped payload would be rejected at
    # the framework layer instead of being normalised here.
    if nodes is None:
        raise KeyError(
            "nodes 参数不能为空，必须传至少一个节点数组 "
            "（形如 [{\"name\": \"...\", \"links\": [], \"children\": [...]}, ...]）"
        )
    if isinstance(nodes, dict):
        wrapped = nodes.get("$text") or nodes.get("text") or nodes.get("value")
        if isinstance(wrapped, str):
            nodes = try_parse_json(wrapped)
        elif any(key in nodes for key in ("nodes", "item", "items")):
            for key in ("nodes", "item", "items"):
                if key in nodes:
                    nodes = nodes[key]
                    break
        elif "name" in nodes:
            # Single-node dict delivered instead of a list — auto-wrap.
            nodes = [nodes]
    if isinstance(nodes, str):
        nodes = try_parse_json(nodes)
    if not isinstance(nodes, list):
        raise KeyError(
            f"nodes 参数必须是数组（形如 [{{\"name\": \"...\", \"links\": [], "
            f"\"children\": [...]}}, ...]），实际类型: {type(nodes).__name__}"
        )

    # ── Flatten tree / flat-list into (flat_nodes, parent_links map) ──
    flat_nodes: list[dict[str, Any]] = []
    wiring: dict[str, list[str]] = {}
    skipped: list[str] = []
    seen_names: set[str] = set()
    duplicates: list[str] = []

    for i, n in enumerate(nodes):
        try:
            sub_flat, sub_wiring = _walk_tree(n, f"nodes[{i}]")
        except KeyError as e:
            # Partial success beats total failure: one malformed entry
            # out of N should not throw away the other N-1 nodes the
            # model got right.
            skipped.append(str(e.args[0] if e.args else e))
            continue
        for entry in sub_flat:
            name = entry["name"]
            if name in seen_names:
                duplicates.append(name)
            seen_names.add(name)
        flat_nodes.extend(sub_flat)
        for p, kids in sub_wiring.items():
            existing_kids = wiring.get(p, [])
            for k in kids:
                if k not in existing_kids:
                    existing_kids.append(k)
            wiring[p] = existing_kids

    if duplicates:
        return (
            f"❌ kg_add_subtree 失败：同批次/与图谱现有节点重的节点名：{sorted(set(duplicates))}。"
            "这些节点在图谱中已存在或本批次内重复，必须先去重。"
        )

    if not flat_nodes:
        detail = "；".join(skipped) if skipped else "nodes 为空"
        raise KeyError(f"没有可用节点，全部条目均无效：{detail}")

    parsed: list[Node] = [
        Node(name=n["name"], links=n["links"]) for n in flat_nodes
    ]

    svc = get_graph_service()
    repo = get_graph_repo()

    # Snapshot pre-state so we can compute truthful counts after write.
    is_new_domain = not await repo.domain_exists(domain)
    pre_existing_names: set[str] = set()
    if not is_new_domain:
        pre_graph = await repo.read_graph(domain)
        pre_existing_names = {n.name for n in pre_graph.nodes}

    # ── Pre-validate dangling link targets (no silent broken refs) ──
    known_after = pre_existing_names | {n["name"] for n in flat_nodes}
    dangling: list[tuple[str, str]] = []
    for n in flat_nodes:
        for t in n["links"]:
            if t == n["name"]:
                continue  # self-ref is allowed (rare but valid)
            if t not in known_after:
                dangling.append((n["name"], t))
    if dangling:
        missing_names = sorted({t for _, t in dangling})
        sample = "\n".join(
            f"  · 节点「{node}」的 links 引用了不存在的「{target}」"
            for node, target in dangling[:20]
        )
        more = ""
        if len(dangling) > 20:
            more = f"\n  · …还有 {len(dangling) - 20} 条"
        return (
            "❌ kg_add_subtree 失败：以下 link 目标节点不存在（既不在本批次的新节点中，"
            "也不在该领域现有图谱中）。请先用 kg_add_subtree 创建它们，或从 links 中"
            "去掉这些引用后重试。\n"
            f"缺失节点：{missing_names}\n"
            f"{sample}{more}"
        )

    # ── Atomic single write ──
    g, added_names = await svc.add_subtree(
        domain,
        parsed,
        parent=parent or None,
        extra_parent_links=wiring if wiring else None,
        auto_create_parents=bool(parent),  # only auto-create the user-given parent
    )

    # ── Activity timeline ──
    bus = get_activity_bus()

    if is_new_domain:
        await bus.emit(
            ActivityKind.DOMAIN_CREATED,
            domain=domain,
            node=added_names[0] if added_names else "",
            title=f"新建了领域「{domain}」",
            source="agent",
            ref=f"domain:{domain}",
            extra={
                "first_node": added_names[0] if added_names else "",
                "node_count": len(added_names),
            },
        )

    # Only emit NODE_CREATED for nodes that actually landed in the graph;
    # pre-existing names are skipped so the timeline doesn't lie about them.
    for n in parsed:
        if n.name not in added_names:
            continue
        await bus.emit(
            ActivityKind.NODE_CREATED,
            domain=domain,
            node=n.name,
            title=f"新建了节点「{n.name}」",
            source="agent",
            ref=f"node:{n.name}",
            extra={"parent": parent} if parent else None,
        )

    # ── Truthful return message ──
    parent_auto_created = bool(
        parent
        and parent not in pre_existing_names
        and parent in {n.name for n in g.nodes}
    )
    parent_msg = (
        f"，已回写父节点「{parent}」"
        f"{'（自动新建）' if parent_auto_created else ''}的 links"
        if parent
        else ""
    )
    wiring_count = len(wiring)
    wiring_msg = (
        f"，已为 {wiring_count} 个内部父节点写回子树 links"
        if wiring_count
        else ""
    )
    skipped_msg = ""
    if skipped:
        skipped_msg = (
            f"\n⚠️  跳过 {len(skipped)} 个格式错误的条目，请用正确格式重试这些节点："
            + "；".join(skipped)
        )

    duplicates_in_existing = sorted(
        {n["name"] for n in flat_nodes} - set(added_names) - set(duplicates)
    )
    if duplicates_in_existing:
        names_label = "、".join(duplicates_in_existing)
        if added_names:
            added_label = "、".join(added_names)
            dedup_msg = (
                f"\nℹ️  已插入 {added_label}；"
                f"{len(duplicates_in_existing)} 个与图谱现有节点同名已跳过：{names_label}"
            )
        else:
            dedup_msg = (
                f"\nℹ️  全部 {len(duplicates_in_existing)} 个名称与图谱现有节点同名，已跳过："
                f"{names_label}"
            )
    else:
        dedup_msg = ""

    added_label = "、".join(added_names) if added_names else "(无)"
    msg = (
        f"✅ 已插入 {len(added_names)} 个节点: {added_label}"
        f"（{domain}, 当前节点数={len(g.nodes)}{parent_msg}{wiring_msg}）"
        f"{dedup_msg}{skipped_msg}"
    )
    return msg


@tool
@logged_tool
async def kg_fix_links(domain: str) -> str:
    """规范化图谱为前向树：移除指向自身祖先的 link 条目。"""
    svc = get_graph_service()
    removed, scanned = await svc.fix_links(domain)

    # Activity timeline — agent-driven fix-links.
    await get_activity_bus().emit(
        ActivityKind.FIX_LINKS,
        domain=domain,
        node="",
        title=f"修复了孤链（扫描 {scanned} 节点，清理 {removed} 条）",
        source="agent",
        ref=f"domain:{domain}",
        extra={"removed": removed, "scanned": scanned},
    )
    return f"✅ 扫描 {scanned} 个节点，清理 {removed} 条指向祖先的 link"


@tool
@logged_tool
async def kg_delete_node(domain: str, name: str) -> str:
    """从图谱中删除指定节点，并清理所有引用该节点的 link 和笔记目录。"""
    svc = get_graph_service()
    g = await svc.delete_node(domain, name)

    # Clean up on-disk assets (notes, resources, plans, uploads).
    cleaned = await svc.delete_node_assets(domain, name)

    # Activity timeline — agent-driven delete.
    await get_activity_bus().emit(
        ActivityKind.NODE_DELETED,
        domain=domain,
        node=name,
        title=f"删除了节点「{name}」",
        source="agent",
        ref=f"node:{name}",
    )

    cleanup_msg = "，已清理笔记目录" if cleaned else ""
    return f"✅ 已删除节点 {name!r}（{domain}, 剩余节点数={len(g.nodes)}{cleanup_msg}）"


@tool
@logged_tool
async def kg_update_node(
    domain: str,
    name: str,
    new_name: str = "",
    new_links: Any = None,
) -> str:
    """重命名节点和/或更新其 links。new_name 为空则不改名；new_links 为 None 则不改 links。

    ``new_links`` 类型注解是 ``Any`` 而不是 ``list[str] | None``，原因同
    ``kg_add_node``——LangChain ``@tool`` 会用 Pydantic schema 严格校验参数，
    SDK 把数组序列化成 ``{"item": [...]}`` 时会直接拒。在函数体内用
    ``_coerce_links`` 归一化所有形态（``{"item": [...]}``、JSON 字符串、分隔
    字符串、``None`` 等）。

    重命名时会自动迁移笔记、资料、计划等节点级资产。

    **同步保证**：调用返回前会**同步等待派生图同步完成**
    （FalkorDB Concept 节点 + PART_OF 边 + BM25 索引），确保
    紧随其后的 ``kg_query_neighbors`` / ``kg_view_associations``
    都能立刻看到新结构。
    """
    # Normalise ``new_links`` BEFORE handing off to the repo so the
    # service layer receives an unambiguous ``list[str]`` (or None when
    # the caller wants to leave links unchanged).  ``None`` (no change)
    # is preserved as-is — only explicit payloads get normalised.
    if new_links is not None:
        new_links = _coerce_links(new_links)

    svc = get_graph_service()
    g = await svc.update_node(domain, name, new_name=new_name, new_links=new_links)
    label = new_name or name

    # ── Migrate on-disk assets when renaming ──
    migration_msg = ""
    if new_name and new_name != name:
        report = await svc.migrate_node_assets(domain, name, label)
        migrated_parts = []
        if report["note_migrated"]:
            migrated_parts.append("笔记")
        if report["resources_migrated"]:
            migrated_parts.append("资料")
        if report["plans_migrated"]:
            migrated_parts.append("计划")
        if report["uploads_migrated"]:
            migrated_parts.append("文件")
        if migrated_parts:
            migration_msg = f"，已迁移：{'+'.join(migrated_parts)}"
        if report["errors"]:
            migration_msg += f"，⚠️{len(report['errors'])}个迁移错误"

    # Activity timeline — distinguish rename vs relink, same as the
    # API route layer.
    bus = get_activity_bus()
    if new_name and new_name != name:
        await bus.emit(
            ActivityKind.NODE_RENAMED,
            domain=domain,
            node=label,
            title=f"重命名节点「{name}」→「{label}」",
            source="agent",
            ref=f"node:{label}",
            extra={"old_name": name, "new_name": label},
        )
    if new_links is not None:
        await bus.emit(
            ActivityKind.NODE_RELINKED,
            domain=domain,
            node=label,
            title=f"更新了节点「{label}」的链接",
            source="agent",
            ref=f"node:{label}",
            extra={"new_links": new_links},
        )

    # ── Synchronously sync the derived graph (FalkorDB + BM25) ──
    # The DerivationSubscriber listens for NODE_RENAMED / NODE_RELINKED
    # events and triggers sync_for_node as a fire-and-forget task.
    # But the LLM often calls ``kg_query_neighbors`` immediately after,
    # which would race against the background task and may miss the new
    # structure.  Block here so the tool returns only after the derived
    # graph is consistent.
    #
    # Sync failure is logged but never breaks the write: the
    # DerivationSubscriber is still subscribed and will retry on the
    # next event, so a transient FalkorDB / embedding outage does not
    # cost the user their update.
    sync_msg = ""
    try:
        sync_svc = get_graph_sync_service(domain)
        await sync_svc.sync_for_node(label)
    except Exception as exc:  # noqa: BLE001 — best-effort
        sync_msg = f"，⚠️ 派生图同步失败（已记日志，DerivationSubscriber 会重试）：{type(exc).__name__}"
        logger = __import__("logging").getLogger(__name__)
        logger.warning(
            "kg_update_node: derived graph sync failed for domain=%s node=%s: %s",
            domain, label, exc, exc_info=True,
        )

    return f"✅ 已更新节点 {name!r} → {label!r}（{domain}, 当前节点数={len(g.nodes)}{migration_msg}{sync_msg}）"


@tool
@logged_tool
async def kg_validate_graph(domain: str) -> str:
    """验证图谱质量并返回问题列表 + 质量评分（6 维度）。"""
    svc = get_graph_service()
    issues = await svc.validate(domain)
    score = await svc.score(domain)
    out = {
        "domain": domain,
        "issues": issues,
        "score": score.model_dump(),
        "level": score.level.value,
        "total": score.total,
    }
    return json.dumps(out, ensure_ascii=False)


def _find_hierarchy_path(nodes: list[dict], target: str) -> list[str]:
    """BFS from the synthetic L0 root down to ``target`` following ``links``.

    ``decorated_graph`` puts a synthetic L0 node (``isDomainRoot=True``,
    ``level=0``) at the top whose ``links`` are the L1 roots; from there
    each ``links`` edge goes to a child.  The first path we discover is
    returned — the graph is a DAG so there may be more than one, but any
    path is enough for the UI to expand and highlight the target.
    """
    by_name = {n["name"]: n for n in nodes}
    if target not in by_name:
        return []
    target_node = by_name[target]
    if target_node.get("isDomainRoot") or target_node.get("level") == 0:
        return [target]
    start = next(
        (n["name"] for n in nodes if n.get("isDomainRoot")),
        nodes[0]["name"] if nodes else target,
    )
    if start == target:
        return [target]
    parents: dict[str, str | None] = {start: None}
    queue: list[str] = [start]
    while queue:
        cur = queue.pop(0)
        cur_node = by_name.get(cur)
        if not cur_node:
            continue
        for child in cur_node.get("links", []) or []:
            if child in by_name and child not in parents:
                parents[child] = cur
                if child == target:
                    path: list[str] = []
                    n: str | None = target
                    while n is not None:
                        path.append(n)
                        n = parents.get(n)
                    return list(reversed(path))
                queue.append(child)
    # Not reachable from L0 — the target is an orphan root (forest-style
    # graph with no incoming edge).  Frontend's OutlineView expands
    # ``path`` via el-tree's ``store.getNode(k).expanded = true``; if the
    # synthetic L0 root is NOT in the path, el-tree never registers the
    # orphan's children, and ``setCurrentKey(target)`` silently fails
    # because the key isn't in the internal store yet.
    #
    # Prepend the synthetic L0 root so the frontend expands it first
    # and the orphan target becomes selectable.
    return [start, target]


@tool
@logged_tool
async def kg_open_node(domain: str, node_name: str) -> str:
    """在前端图谱中"打开"指定节点 —— 展开其祖先路径并定位选中它。

    当用户说"帮我打开 / 跳转到 / 定位到 X 节点"时调用。**仅查找和
    计算路径，不修改图谱数据**，因此可以放心在用户未要求修改时调用。
    返回 JSON 字符串，前端 ChatPanel 会监听此工具的 result 并触发
    OutlineView 展开路径 + 滚动到目标节点 + 高亮选中。

    返回格式::

        {
          "ok": true,
          "domain": "...",
          "node": "目标节点名",
          "path": ["L0", "祖先A", "祖先B", "目标节点"],   # 展开路径
          "tier": "L2",
          "level": 2,
          "is_domain_root": false
        }

    节点不存在时返回 ``ok: false``，并附带 ``message`` + 候选节点列表
    （最多 30 条），方便 LLM 修正后重试。同时会扫描 ``notes/`` 目录：
    若目标节点在磁盘上存在 note/plan 资产但未挂入图谱，会在 ``message``
    中追加一条提示，建议先调用 ``kg_add_node`` 补登。
    """
    svc = get_graph_service()
    g = await svc.view(domain)
    decorated = decorate_graph(g.model_dump())

    nodes = decorated.get("nodes", [])
    target = next((n for n in nodes if n.get("name") == node_name), None)
    if not target:
        available = [n.get("name", "") for n in nodes if n.get("name")]
        # Self-correction hints for the LLM:
        #   1. Substring matches first (cheap, exact).
        #   2. Case-insensitive matches next.
        #   3. Otherwise fall back to the first 10 names so the model
        #      at least sees the top-level structure of the domain.
        needle = (node_name or "").strip()
        substr_hits = [n for n in available if needle and needle in n][:10]
        ci_hits: list[str] = []
        if needle:
            low = needle.lower()
            ci_hits = [n for n in available if n not in substr_hits and low in n.lower()][:10]
        suggestions = substr_hits or ci_hits or available[:10]

        # BUG-004 follow-up: detect the "notes/ exists but graph doesn't"
        # case.  When ``kg_add_node`` was skipped after writing the note,
        # the node has on-disk assets (note.md / plan.json / resources/)
        # but no entry in ``knowledge_graph.json`` — so even
        # ``kg_read_note`` succeeds yet ``kg_open_node`` returns ok=false.
        # Surface this so the LLM (or operator) can call ``kg_add_node``
        # to graft the orphan onto the graph instead of looping.
        #
        # Probe the repo's own ``kb_root`` (NOT a hardcoded
        # ``Path("knowledge_bases")``): the latter would silently miss
        # in tests (where ``tmp_kb_root`` lives under ``tmp_path``) and
        # also drift whenever the agent is launched from a different
        # working directory.
        orphan_hint = ""
        try:
            kb_root = getattr(svc, "_repo", None)
            kb_root_path = getattr(kb_root, "kb_root", None)
            if kb_root_path is not None:
                notes_dir = kb_root_path / domain / "notes" / needle
                if notes_dir.is_dir():
                    orphan_hint = (
                        f"；检测到节点「{needle}」在 notes/ 目录下有资产但"
                        f"未挂入图谱，建议先用 kg_add_node(name={needle!r})"
                        f"（必要时指定 parent）补登，再重新调用 kg_open_node。"
                    )
        except Exception:
            # Path probe is best-effort; never fail the tool just because
            # we couldn't stat the filesystem.
            orphan_hint = ""

        msg = (
            f"领域「{domain}」中找不到节点「{node_name}」"
            + (
                f"，可选类似节点：{', '.join(suggestions)}"
                if suggestions
                else ""
            )
            + orphan_hint
        )
        return json.dumps(
            {
                "ok": False,
                "domain": domain,
                "node": node_name,
                "message": msg,
                "available_sample": available[:30],
                "suggestions": suggestions,
            },
            ensure_ascii=False,
        )

    path = _find_hierarchy_path(nodes, node_name)
    return json.dumps(
        {
            "ok": True,
            "domain": domain,
            "node": node_name,
            "path": path,
            "tier": target.get("tier", "leaf"),
            "level": target.get("level", 1),
            "is_domain_root": bool(target.get("isDomainRoot", False)),
        },
        ensure_ascii=False,
    )


__all__ = [
    "kg_list_domains",
    "kg_view_graph",
    "kg_add_node",
    "kg_add_subtree",
    "kg_fix_links",
    "kg_delete_node",
    "kg_update_node",
    "kg_validate_graph",
    "kg_open_node",
]
