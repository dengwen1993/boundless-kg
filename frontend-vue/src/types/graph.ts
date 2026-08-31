/** Graph node types — mirrors knowledge_graph.json structure */

export interface GraphNode {
  name: string
  links: string[]
  /** Decorated fields (added by backend) */
  level?: number
  tier?: 'L0' | 'L1' | 'L2' | 'L3' | 'leaf'
  childCount?: number
  isDomainRoot?: boolean
}

export interface GraphDirection {
  angle?: string
  audience?: string
  depth?: string
  summary?: string
}

export interface GraphData {
  domain: string
  direction: GraphDirection
  nodes: GraphNode[]
  meta?: {
    n_nodes: number
    n_links: number
  }
  domain_match_score?: any
}

export interface DomainInfo {
  name: string
  node_count?: number
}

/** Chat message types */
export interface ChatMessage {
  role: 'user' | 'agent' | 'system'
  content: string
  ts: number
  toolEvents?: ToolEvent[]
  pending?: boolean
  /** Timeline-ordered blocks for interleaved rendering (text + tool cards in arrival order). */
  blocks?: MessageBlock[]
}

export interface ToolEvent {
  type: 'call' | 'result'
  name: string
  args?: Record<string, any>
  result?: string
}

/** A single timeline entry within an agent message — either a text chunk or a tool card. */
export type MessageBlock =
  | { kind: 'text'; text: string }
  | { kind: 'tool'; name: string; args?: Record<string, any>; result?: string; status: 'running' | 'done' | 'error' }

/** SSE event from /api/agent/invoke */
export type AgentEvent =
  | { event: 'text'; data: { delta: string } }
  | { event: 'tool-call'; data: { name: string; args: Record<string, any> } }
  | { event: 'tool-result'; data: { name: string; result: string } }
  | { event: 'error'; data: { message: string; trace?: string } }
  // Backend emits this FIRST on every invoke, with the resolved
  // ``thread_id`` (server-minted when client omitted /api/agent/session).
  | { event: 'session'; data: { session_id: string } }

// ============================================================
// Node note & resources types
// ============================================================

/** web_resources/index.json item */
export interface WebResource {
  title: string
  url: string
  summary: string
  category: string
  added_at: string
}

/** user_uploads/index.json item */
export interface UploadResource {
  file: string
  category: string
  note: string
  moved_at: string
  original_source?: string
  size?: number
}

/** study_materials 目录扫描出来的产物（agent 通过 knowledge-digest 生成）。
 *  字段对齐 UploadResource，让前端可以用同一套 UI 渲染，但 ``source`` 用于区分。
 *  ``file`` 始终是相对 study_materials/ 根的路径：根级文件如 ``cordis_quiz.html``，
 *  嵌套文件如 ``chapters/chapter-01.md``，前端直接用于下载 URL，展示时按 ``/`` 截取末段。 */
export interface StudyMaterialItem {
  file: string
  /** ``'folder'`` 表示这是个目录（UI 可下钻），``'file'`` 或缺省为普通文件。 */
  type?: 'file' | 'folder'
  category: string
  note: string
  moved_at: string
  size?: number
  /** 文件夹条目才有：直接子文件数（不含子目录）。 */
  children_count?: number
  source: 'study_materials'
}

/** Combined resources response */
export interface NodeResources {
  web_resources: WebResource[]
  user_uploads: UploadResource[]
  study_materials: StudyMaterialItem[]
}

// ============================================================
// Node plan (学习计划 / 行动轨迹) types
// ============================================================

/** plan.json 单条行动 */
export interface PlanAction {
  id: string
  content: string
  status: 'pending' | 'done' | 'skipped'
  done_at: string | null
}

/** plan.json 单条记录 —— 一个计划 = 一个目标 + 多条行动 */
export interface PlanItem {
  id: string
  created_at: string
  date: string
  /** 计划目标（一句话） */
  goal: string
  /** 行动列表 */
  actions: PlanAction[]
  /** 计划整体状态（自动汇总：所有行动 done→done，否则 pending） */
  status: 'pending' | 'done' | 'skipped'
  source: 'manual' | 'agent'
  note: string
}

/** GET /api/plans 响应 */
export interface NodePlans {
  items: PlanItem[]
  total: number
}

// ============================================================
// Timeline (活动流聚合) types
// ============================================================

/** 领域活动流单条 —— 由后端实时聚合（观察者模式 → per-day JSONL log） */
export interface Activity {
  node: string
  /** ISO 字符串，精确到秒（展示截到分钟），用于排序 */
  datetime: string
  /** YYYY-MM-DD，用于按日过滤 */
  date: string
  /** 事件类型 — 来源于后端 ActivityKind 枚举 */
  type: ActivityType
  title: string
  /** manual / agent */
  source: string
  /** plan 才有：pending / done / skipped */
  status?: string
  /** 原始定位，如 "plan.json#p2026..." */
  ref: string
  /** 额外结构化负载（plan actions 列表 / node 链接变化等） */
  extra?: Record<string, unknown>
}

/** Timeline event types — mirror of backend ``ActivityKind``. */
export type ActivityType =
  // node CRUD
  | 'node_created'
  | 'node_renamed'
  | 'node_relinked'
  | 'node_deleted'
  // resources / uploads
  | 'web_resource'
  | 'web_resource_added'
  | 'upload'
  | 'upload_added'
  // plans
  | 'plan'
  | 'plan_created'
  | 'plan_action_done'
  | 'plan_action_skipped'
  | 'plan_deleted'
  // notes
  | 'note'
  | 'note_generated'
  | 'note_updated'
  /** 兜底：后端可能返回未在枚举里的类型字符串 */
  | string

/** GET /api/timeline 响应 */
export interface TimelineResponse {
  items: Activity[]
  total: number
}
