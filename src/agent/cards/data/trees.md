---
id: trees
title: 批量添加子树（一次性原子插入整棵树）
triggers: ["子树", "批量添加", "kg_add_subtree", "插入子树"]
applies_to_tools: ["kg_add_subtree"]
priority: 20
---

向某个领域批量追加子树时，**统一用 `kg_add_subtree`**——一次调用即可让前端立即看到完整结构，无需后续 `kg_update_node` 回写：

- `nodes` 支持两种形态，**推荐树形**：
  - 树形：`[{name, links?, children?: [{name, links?, children?: ...}]}]`，父→子的边会自动转写到 `parent.links`
  - 平铺（兼容）：`[{name, links?: [...]}, ...]`，传 `parent=` 参数共享根父节点
- 传了 `parent` 但该父节点不存在 → **自动建一个空壳父节点**（`links=[]`）再回写，保证一次落地、避免「kg_add_node 建父 + kg_add_subtree 加子 + kg_update_node 回写」三连
- `links` 里引用到的节点必须**已经存在**（在本批次的新节点中，或在该领域现有图谱中），否则整个调用被拒绝并列出缺失节点——**不会**静默写入破链
- 单条格式错误（`$text` 不是合法 JSON 等）会被跳过并在返回信息中报告，其余节点正常落盘

**单条与批量的取舍**：

- 1 个节点 → `kg_add_node`。
- 2–10 个同主题节点 → `kg_add_subtree` 平铺形态。
- 嵌套多层的整棵子树 → `kg_add_subtree` 树形形态（嵌套深度不限）。
- 超过 10 个**根级**节点 → 仍可分批 `kg_add_subtree`，每批 ≤ 10 个**根级节点**（树形下子节点数不限）。

每个节点名遵循图谱现有命名约定（参考 `kg_view_graph` 返回的命名风格）；`links` 只填**子节点或同层交叉引用名**，不要填父节点或祖先。
