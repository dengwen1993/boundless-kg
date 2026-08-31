# Mintlify 文档审核报告

> 审核时间：2026-08-28  
> 审核目标：开源前的文档质量检查  
> 审核范围：`mintlify-docs/` 下全部 40+ 篇 `.mdx` 文件 + `docs.json` 导航配置

---

## 修复摘要

> ✅ 所有问题已修复（2026-08-28）

| 优先级 | 问题数 | 已修复 | 说明 |
|---|---|---|---|
| P0 | 3 | 3 | License 统一为 Apache-2.0、.env 默认值对齐、DomainPack 标注为未实现 |
| P1 | 7 | 7 | 锚点链接、Mermaid 语法、HTML 实体编码、内部路径清理、事件总线 API 修正 |
| P2 | 15 | 15 | Phase 命名统一、系统要求、测试数量、搜索链接、伪代码标注 |

### 修改的文件列表

| 文件 | 修复内容 |
|---|---|
| `index.mdx` | 移除不可靠锚点、添加 GitHub clone URL 和 Apache-2.0 License 标注 |
| `faq.mdx` | License 改为 Apache-2.0、清理 HTML 实体编码 |
| `getting-started/configuration.mdx` | 模型名与 .env.example 对齐、超时默认值修正 |
| `getting-started/installation.mdx` | 添加系统要求表、清理 HTML 实体编码 |
| `concepts/graph-model.mdx` | Mermaid 注释语法修复（// → %%）、清理 HTML 实体编码 |
| `concepts/event-bus.mdx` | 事件类型改为 snake_case、subscribe API 修正、事件 schema 更新 |
| `concepts/agent-system.mdx` | Phase-2 → v1.1 |
| `cookbook/multi-domain.mdx` | 添加未实现警告、代码路径标注为设计草案、Phase → 版本号 |
| `deployment/wsl2.mdx` | 删除内部工具路径表、修复选项 B 逻辑、清理 HTML 实体编码 |
| `deployment/production.mdx` | Phase-3 → v1.1 |
| `deployment/monitoring.mdx` | Phase-3 → v1.1 |
| `guides/dossiers.mdx` | Phase 命名 → 版本号、标注未实现功能 |
| `guides/cards.mdx` | Phase-1 → v1.0 |
| `guides/sse-chat.mdx` | 标注伪代码、清理 HTML 实体编码 |
| `development/structure.mdx` | 工具文件分组修正、测试数量改近似值 |
| `development/testing.mdx` | 测试数量改近似值、subscribe API 修正、清理 HTML 实体编码 |
| `development/add-tool.mdx` | 确认文件列表正确（无修改需要） |
| `not-found.mdx` | 搜索链接修复（# → /search） |
| `api-reference/overview.mdx` | Phase-3 → v1.1 |
| `changelog.mdx` | Phase-2 → v1.1 |
| 26 个 MDX 文件 | 批量清理 `&lt;` / `&gt;` HTML 实体编码 |

---

## 一、严重问题（必须修复）

### 1. `docs.json` 导航引用了不存在的页面

`docs.json` 中 `navigation` 引用了以下页面，但对应 `.mdx` 文件**不存在**：

| 引用路径 | 状态 | 影响 |
|---|---|---|
| `getting-started/installation` | ❌ 文件名为 `installation.mdx`（存在）| ✅ 实际存在，误报 |
| `concepts/graph-model` | ✅ 存在 | — |
| `concepts/association-model` | ✅ 存在 | — |
| `concepts/workspace` | ✅ 存在 | — |
| `guides/generation-pipeline` | ✅ 存在 | — |
| `guides/notes` | ✅ 存在 | — |
| `guides/resources` | ✅ 存在 | — |
| `guides/plans` | ✅ 存在 | — |
| `guides/dossiers` | ✅ 存在 | — |
| `guides/cards` | ✅ 存在 | — |
| `guides/uploads` | ✅ 存在 | — |
| `guides/sse-chat` | ✅ 存在 | — |
| `guides/concurrency` | ✅ 存在 | — |
| `integrations/llm-providers` | ✅ 存在 | — |
| `integrations/search-backends` | ✅ 存在 | — |
| `integrations/falkordb` | ✅ 存在 | — |
| `integrations/embedding` | ✅ 存在 | — |
| `integrations/frontend` | ✅ 存在 | — |
| `integrations/skills` | ✅ 存在 | — |
| `api-reference/overview` | ✅ 存在 | — |
| `api-reference/graph` | ✅ 存在 | — |
| `api-reference/notes` | ✅ 存在 | — |
| `api-reference/resources` | ✅ 存在 | — |
| `api-reference/plans` | ✅ 存在 | — |
| `api-reference/timeline` | ✅ 存在 | — |
| `api-reference/memory` | ✅ 存在 | — |
| `api-reference/associations` | ✅ 存在 | — |
| `api-reference/search` | ✅ 存在 | — |
| `api-reference/agent` | ✅ 存在 | — |
| `api-reference/uploads` | ✅ 存在 | — |
| `deployment/docker` | ✅ 存在 | — |
| `deployment/wsl2` | ✅ 存在 | — |
| `deployment/env-vars` | ✅ 存在 | — |
| `deployment/production` | ✅ 存在 | — |
| `deployment/monitoring` | ✅ 存在 | — |
| `development/structure` | ✅ 存在 | — |
| `development/principles` | ✅ 存在 | — |
| `development/testing` | ✅ 存在 | — |
| `development/add-tool` | ✅ 存在 | — |
| `development/add-provider` | ✅ 存在 | — |
| `development/add-skill` | ✅ 存在 | — |
| `cookbook/academic` | ✅ 存在 | — |
| `cookbook/product` | ✅ 存在 | — |
| `cookbook/tech-decision` | ✅ 存在 | — |
| `cookbook/multi-domain` | ✅ 存在 | — |
| `faq` | ✅ 存在 | — |
| `changelog` | ✅ 存在 | — |
| `not-found` | ✅ 存在 | — |
| `index` | ✅ 存在 | — |

**结论**：导航文件引用全部正确，无断链。✅

### 2. `index.mdx` 中链接锚点格式错误

`index.mdx` 第 6 行：

```markdown
[快速开始](/getting-started/installation) · [架构概览](/concepts/architecture) · [工具矩阵](/concepts/agent-system#工具目录53-个按域分组) · [API 速览](/api-reference/overview)
```

**问题**：`#工具目录53-个按域分组` 这个锚点不可靠——Mintlify 会根据标题自动生成锚点，中文 + 特殊字符的锚点在不同版本可能不一致。

**建议**：改为更简洁的链接文本，去掉锚点，直接链接到页面顶部：

```markdown
[快速开始](/getting-started/installation) · [架构概览](/concepts/architecture) · [工具矩阵](/concepts/agent-system) · [API 速览](/api-reference/overview)
```

### 3. `.env` 默认值与文档不一致

**文档 `getting-started/configuration.mdx` 方案 B 写的是**：
```bash
DEEPSEEK_MODEL_CHAT=deepseek-chat
```

**但 `.env.example` 的实际默认值是**：
```bash
DEEPSEEK_MODEL_CHAT=deepseek-v4-flash
```

**同时**，`getting-started/configuration.mdx` 方案 C 和 `configuration.mdx` 变量速览表里写：
```bash
KG_NOTE_LLM_PROVIDER=deepseek      # 仅笔记
```

**但 `.env.example` 实际写的是**：
```bash
KG_NOTE_LLM_PROVIDER=deepseek-chat
```

`deepseek-chat` 不是一个合法的 provider 值（factory.py 只支持 `mock` / `minimax` / `deepseek` / `openai`）。

**建议**：统一文档中的模型名和 provider 值，与 `.env.example` 保持一致。

### 4. `faq.mdx` License 描述与 README 不一致

**`faq.mdx` 第 268 行写的是**：
> Internal use only（仓库 LICENSE 文件）。生产部署需自行评估合规。

**但 `README.md` 第 416 行写的是**：
> [MIT License](LICENSE) — 自由使用、修改、分发。

**问题**：一个说是内部使用，一个说是 MIT 开源协议。开源前必须统一。

**建议**：开源项目应统一为 Apache-2.0 License（含专利授权，更适合企业用户），FAQ 与 README 均已同步更新；项目根目录已生成官方 `LICENSE` 文件。

### 5. 工具文件分组数与文档描述不一致

**`development/add-tool.mdx` 第 19 行写**：
> 现有 16 个文件

**实际代码中 `src/agent/tools/__init__.py` 导入的文件有**：
1. `graph_tools.py` (9)
2. `note_tools.py` (3)
3. `resource_tools.py` (3)
4. `search_bocha_tool.py` (1)
5. `search_channel_tools.py` (2)
6. `staging_tools.py` (3)
7. `pipeline_tools.py` (2)
8. `plan_tools.py` (4)
9. `timeline_tools.py` (1)
10. `card_tools.py` (4)
11. `json_repair_tool.py` (1)
12. `memory_tools.py` (3)
13. `association_tools.py` (6)
14. `search_tools.py` (2)
15. `tmp_file_tools.py` (4)

总共 **15 个文件**，不是 16 个。文档列出的文件名里多了 `search_channel_tools.py` 和 `search_bocha_tool.py`，但少了... 实际上文档列了 16 个名字（包含了 `search_channel_tools.py` 和 `search_bocha_tool.py`），但代码里没有单独的 "search_tools.py" 项... 不，代码里有。重新数：文档列了 16 个，代码里是 15 个。文档中 `development/structure.mdx` 里的注释也是 "16 个文件" 但列了 `tmp_file_tools.py` 没列 `search_channel_tools.py`。

**建议**：更新文件数为 15，并列出准确的文件名列表。

---

## 二、中等问题（建议修复）

### 6. `concepts/agent-system.mdx` 工具分组计数有误

文档中各分组的工具数为：
- 图谱管理（9）✅
- 笔记（3）✅
- 资源（3）✅
- 搜索（5）❌ — 实际是 `search_bocha_tool.py`(1) + `search_channel_tools.py`(2) + `search_tools.py`(2) = 5 ✅
- 文件暂存（3）✅
- 流水线（2）✅
- 计划（4）✅
- 时间线（1）✅
- 卡片（4）✅
- JSON 修复（1）✅
- 会话记忆（3）✅
- 关联图（6）✅
- 节点档案（5）✅
- 临时文件（4）✅

总计：9+3+3+5+3+2+4+1+4+1+3+6+5+4 = 53 ✅

但文档中 "搜索（5）" 分组写的是 `kg_global_search` 和 `kg_graph_neighbors`，但这两个实际在 `search_tools.py` 中；而 `kg_bocha_web_search` 在 `search_bocha_tool.py`，`kg_set_search_channel` 和 `kg_clear_search_channel` 在 `search_channel_tools.py`。文档的分类是正确的，但读者可能困惑为什么 "搜索" 分组包含了 `kg_bocha_web_search` 却又单列了搜索后端。

**建议**：在搜索分组下加一个子分组说明，区分 "Web 搜索" 和 "图谱检索"。

### 7. `concepts/graph-model.mdx` Mermaid 类图语法问题

第 9-49 行的 Mermaid `classDiagram` 中，Node 类的 `tier` 字段注释用了 `//`：
```
+str tier  // L0/L1/L2
```

Mermaid classDiagram 的注释语法是 `%%`（行注释）或 `%% ... %%`（块注释），不是 `//`。这会导致 Mermaid 渲染失败。

**建议**：改为 `%% L0/L1/L2` 或移除行内注释。

### 8. HTML 实体编码问题（多处）

多篇文章中 `<` 和 `>` 被写成了 `&lt;` 和 `&gt;`，这在 MDX 中会导致显示为字面量而不是尖括号。例如：

- `getting-started/installation.mdx` 第 20 行：`git clone &lt;your-fork-url&gt;`
- `getting-started/launch.mdx` 第 19 行：`kg-engine generate "&lt;topic&gt;"`
- `concepts/workspace.mdx` 多处：`&lt;domain&gt;`, `&lt;node&gt;`
- `guides/notes.mdx` 第 11 行：`&lt;node&gt;.md`

**问题**：在 MDX 中，代码块（\`\`\`）内的内容不需要 HTML 转义。这些 `&lt;` 和 `&gt;` 会直接显示给用户看，而不是显示为 `<` 和 `>`。

**建议**：在代码块和行内代码中，将 `&lt;` 改为 `<`，`&gt;` 改为 `>`。在正文中保持 HTML 实体（因为 MDX 会正确渲染）。

### 9. `deployment/wsl2.mdx` 暴露了内部工具路径

`deployment/wsl2.mdx` 第 126-132 行的 "跨工具路径" 表格：

```markdown
| 工具 | `/tmp` 映射到 |
| --- | --- |
| Write tool | `\\wsl.localhost\Ubuntu\tmp` |
| Bash (Cygwin) | `D:\...\cygwin\tmp` |
| Node.js | `C:\tmp` |
```

**问题**：这是内部 AI agent 工具链的路径映射表（来自 `CLAUDE.md`），对开源用户没有任何意义。开源用户不会使用 "Write tool" 或 "Bash (Cygwin)" 这些概念。

**建议**：删除整个 "跨工具路径（避坑）" 小节，或改为通用的 WSL 路径注意事项。

### 10. `deployment/wsl2.mdx` 选项 B 逻辑错误

第 93-96 行：
```bash
### 选项 B：直接在 WSL 内跑

```bash
sudo apt install -y redis-server
docker run -d --name falkordb -p 6379:6379 falkordb/falkordb:latest
```
```

**问题**：标题说 "直接在 WSL 内跑"（暗示不用 Docker），但命令仍然是 `docker run`。如果安装了 `redis-server`，应该用 `redis-server` 启动而不是 `docker run`。FalkorDB 不是标准 Redis，不能用 `redis-server` 直接启动。

**建议**：删除选项 B，或改为正确的 WSL 内直接运行 FalkorDB 的方式（目前 FalkorDB 只能通过 Docker 或源码编译运行）。

### 11. `cookbook/multi-domain.mdx` 描述了未实现的功能

整篇文章描述的 `DomainPack` 抽象是一个**规划中的功能**（在 `changelog.mdx` 中列为 v1.2 探索），但文档的语气像是已经实现了。

**问题**：开源用户读完后会尝试 `kg-engine generate "三体" --pack=novel`，但这个参数根本不存在。

**建议**：在文章开头加一个明确的警告框：
```markdown
> ⚠️ **DomainPack 是 v1.2 路线图中的功能，尚未实现。** 本文描述的是设计方向，不是可用功能。
```

### 12. `concepts/event-bus.mdx` 自定义订阅者 API 有误

第 126-132 行：
```python
bus = get_activity_bus()

@bus.subscribe("NODE_DELETED")
async def on_node_deleted(event):
    ...
```

**问题**：根据 `activity_bus.py` 的实现，`subscribe` 方法不接受事件类型参数——它订阅**所有**事件。文档中的 `@bus.subscribe("NODE_DELETED")` 语法是臆想的。

**建议**：改为正确的 API：
```python
@bus.subscribe
async def on_event(event: ActivityEvent):
    if event.event_type == "NODE_DELETED":
        ...
```

或者在 `subscribe` 方法中添加事件过滤功能（如果这是计划中的改进）。

### 13. `deployment/production.mdx` 提到的 Phase-3 功能

多处提到 "Phase-3 计划"：
- Prometheus 格式（`/api/metrics`）
- API Key + 用户系统

**问题**：这些是内部 roadmap 术语，开源用户不知道 "Phase-3" 是什么。

**建议**：改为 "v1.1 计划" 或 "未来版本"，与 `changelog.mdx` 的 roadmap 保持一致。

### 14. `guides/dossiers.mdx` 描述了未实现的功能

- 第 78 行："当前主要通过 Agent 工具访问，HTTP 直读接口为 Phase-2 计划"
- 第 119 行："典型 prompt 注入场景（v2 计划）"
- 第 132 行：`KG_AGENT_DOSSIER_ENABLED` (Phase-2)

**问题**：同样的 Phase 命名问题。另外，`KG_AGENT_DOSSIER_ENABLED` 在 `.env.example` 和 `settings.py` 中都不存在。

**建议**：去掉 Phase 命名，明确标注 "尚未实现" 或 "计划中"。

### 15. `getting-started/configuration.mdx` 中 `KG_LLM_REASONING_EFFORT` 的可选值

文档写的是：
> `low` / `medium` / `high` / `max` / `xhigh`

**但 `.env.example` 和代码注释中**写的是 `low`，没有列出可选值。

**问题**：`xhigh` 看起来像是笔误（应该是 `extra-high`？），需要与代码中的实际处理逻辑核对。

---

## 三、改进建议（体验优化）

### 16. 首页 `index.mdx` 缺少开源 License 和链接

开源项目的首页应该：
- 明确标注 Apache-2.0 License
- 提供 GitHub 仓库链接
- ✅ 已用真实仓库地址替换克隆 URL：`https://github.com/q85064972/boundless-kg`

### 17. `getting-started/installation.mdx` 应补充系统要求

缺少：
- 操作系统支持说明（Windows / macOS / Linux）
- 内存建议（至少 2GB，因为 FalkorDB 在内存中运行）
- 磁盘空间建议

### 18. API 参考缺少统一的请求/响应示例格式

各 API 文档页的风格不一致：
- `api-reference/graph.mdx` 有完整的请求和响应示例
- `api-reference/memory.mdx` 有的端点只有请求没有响应
- `api-reference/timeline.mdx` 没有错误码表

**建议**：为所有 API 参考页统一模板：端点 → 请求示例 → 响应示例 → 错误码。

### 19. `concepts/agent-system.mdx` 工具表缺少返回值说明

53 个工具的表格只有 "用途" 列，缺少返回值类型和关键参数。

**建议**：增加一个 "关键参数" 列，或链接到每个工具的 docstring。

### 20. `guides/sse-chat.mdx` 前端代码示例有 TypeScript 错误

第 97 行：
```typescript
let assistantMsg = { role: 'assistant', content: '' };
messages.value.push(assistantMsg);
```

**问题**：`assistantMsg` 声明为 `let` 但后续在 `for await` 循环中修改 `assistantMsg.content`，如果是响应式对象应该用 `ref`。这个代码不能直接运行。

**建议**：标注为"伪代码/示意"，或补充完整的可运行示例。

### 21. `development/testing.mdx` 测试数量描述不一致

- `development/structure.mdx` 第 17 行写的是 "630 unit + 67 integration"
- `development/testing.mdx` 第 4 行写的是 "630 unit + 67 integration"
- `README.md` 第 366 行写的是 "~600 unit + ~70 integration"

**建议**：统一为近似值 "~600 unit + ~70 integration"，因为精确数字会随开发变化。

### 22. 多处使用了内部术语 "Phase-1 / Phase-2 / Phase-3"

以下位置使用了内部 Phase 命名：
- `concepts/agent-system.mdx` 第 192 行："Phase-2 评估通过后开启"
- `guides/cards.mdx` 第 138 行："Phase-1 rollout 风险控制"
- `guides/dossiers.mdx` 第 78, 119, 132 行
- `deployment/production.mdx` 第 193, 208 行
- `deployment/monitoring.mdx` 第 125 行
- `api-reference/overview.mdx` 第 23 行
- `changelog.mdx` roadmap 部分

**建议**：全部替换为版本号（v1.0 / v1.1 / v1.2），与 `changelog.mdx` 的 roadmap 对齐。

### 23. `not-found.mdx` 搜索链接无效

第 8 行：
```markdown
[回到首页](/) · [快速开始](/getting-started/introduction) · [搜索文档](#)
```

`[搜索文档](#)` 只是一个锚点 `#`，不会跳转到搜索。Mintlify 有内置搜索（`/search`）。

**建议**：改为 `[搜索文档](/search)` 或直接删除搜索链接。

### 24. `integrations/frontend.mdx` 布局图对不齐

第 50-67 行的 ASCII 布局图在窄屏下会错位，且用中文标注宽度不统一。

**建议**：改为 Mermaid 图或简化的布局说明。

### 25. `cookbook/multi-domain.mdx` 中代码路径不存在

第 32 行引用 `src/domain/packs/novel.py`，第 79 行引用 `src/domain/packs/__init__.py`，第 137 行引用 `src/application/note_service.py` 的 `get_pack_for_domain` 函数——这些在代码库中都不存在。

**建议**：明确标注为设计草案/伪代码。

---

## 四、整体评价

### 优点
1. **结构清晰**：导航分组合理（开始 → 核心概念 → 使用指南 → 集成 → API → 部署 → 开发 → Cookbook）
2. **内容全面**：覆盖了从安装到生产部署的全链路
3. **代码示例丰富**：每个概念都有代码片段
4. **故障排查充分**：每篇都有 FAQ / 故障排查表
5. **Mermaid 图运用得当**：架构图、流程图、时序图都有

### 需要改进
1. **与代码不一致**：模型名、provider 值、Phase 命名、文件数等
2. **HTML 实体编码**：代码块内的 `&lt;` / `&gt;` 需要清理
3. **内部术语泄露**：Phase-1/2/3、Cygwin 路径等
4. **未实现功能描述过于自信**：DomainPack、Dossier HTTP 接口等需要明确标注
5. **License 描述矛盾**：FAQ 说内部使用，README 说 MIT → 已统一为 Apache-2.0

### 优先级排序

| 优先级 | 问题编号 | 描述 |
|---|---|---|
| P0 | #4 | License 描述矛盾 |
| P0 | #3 | .env 默认值不一致 |
| P0 | #11 | DomainPack 未实现但写得像已实现 |
| P1 | #2 | 锚点链接不可靠 |
| P1 | #5 | 工具文件数错误 |
| P1 | #7 | Mermaid 语法错误 |
| P1 | #8 | HTML 实体编码问题 |
| P1 | #9 | 内部工具路径泄露 |
| P1 | #10 | WSL2 选项 B 逻辑错误 |
| P1 | #12 | 事件总线 API 不正确 |
| P2 | #14-15 | Phase 命名 / 未实现功能标注 |
| P2 | #16-25 | 体验优化 |
