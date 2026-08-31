<template>
  <el-drawer
    v-model="drawerVisible"
    :title="''"
    direction="rtl"
    size="560px"
    :with-header="false"
    :destroy-on-close="true"
    class="timeline-panel-drawer"
  >
    <div class="timeline-panel">
      <!-- ─── Header ─── -->
      <div class="tp-header">
        <div class="tp-header__left">
          <span class="tp-header__icon">📅</span>
          <div class="tp-header__text">
            <span class="tp-header__title">活动时间线</span>
            <span class="tp-header__domain">{{ domain || '未选领域' }}</span>
          </div>
        </div>
        <el-button size="small" circle :icon="Close" @click="close" />
      </div>

      <!-- ─── Toolbar ─── -->
      <div class="tp-toolbar">
        <el-date-picker
          v-model="dateFilter"
          type="date"
          value-format="YYYY-MM-DD"
          placeholder="选择日期（空=全部）"
          size="small"
          class="tp-date"
          :clearable="true"
          @change="reload"
        />
        <el-button size="small" :icon="Refresh" :loading="loading" @click="reload" />
      </div>

      <!-- ─── Action category chips ─── -->
      <div class="tp-categories">
        <button
          v-for="cat in categories"
          :key="cat.key"
          class="tp-chip"
          :class="{ 'tp-chip--active': categoryFilter === cat.key }"
          :style="`--chip-color: ${cat.color}`"
          @click="categoryFilter = cat.key"
        >
          <span class="tp-chip__icon">{{ cat.icon }}</span>
          <span class="tp-chip__label">{{ cat.label }}</span>
          <span v-if="countOf(cat.key) > 0" class="tp-chip__count">{{ countOf(cat.key) }}</span>
        </button>
      </div>

      <!-- ─── Stats ─── -->
      <div class="tp-stats">
        <span class="tp-stat tp-stat--num">共 {{ filteredActions.length }} 个动作</span>
        <span class="tp-stat tp-stat--when">
          {{ dateFilter ? dateFilter : '全部时间' }}
        </span>
        <span v-if="categoryFilter" class="tp-stat tp-stat--type">
          {{ categoryLabel(categoryFilter) }}
        </span>
      </div>

      <!-- ─── Hint ─── -->
      <div class="tp-hint">
        <span>同类动作在 3 分钟窗口内自动归拢为一条，跨节点/批量的任务合并展示，便于观察并发。点击卡片可展开明细或跳转到对应节点的面板。</span>
      </div>

      <!-- ─── Loading ─── -->
      <div v-if="loading" class="tp-loading">
        <el-icon class="is-loading"><Loading /></el-icon>
        <span>正在聚合活动流…</span>
      </div>

      <!-- ─── Action list ─── -->
      <div v-else-if="filteredActions.length > 0" class="tp-list">
        <div
          v-for="(act, idx) in filteredActions"
          :key="act.id"
          class="tp-item"
          :class="{ 'tp-item--expanded': act.expanded, 'tp-item--batch': act.isBatch }"
          @click="goAction(act)"
        >
          <!-- 左侧时间轴：圆点 + 竖线 -->
          <div class="tp-item__rail">
            <span
              class="tp-item__dot"
              :style="{ background: actionColor(act.kind) }"
            ></span>
            <span v-if="idx < filteredActions.length - 1" class="tp-item__line"></span>
          </div>

          <div class="tp-item__body">
            <div class="tp-item__time">{{ formatTime(act.datetime) }}</div>
            <div class="tp-item__card" :style="{ borderLeftColor: actionColor(act.kind) }">
              <div class="tp-item__title-row">
                <span class="tp-item__action-icon">{{ actionIcon(act.kind) }}</span>
                <span class="tp-item__title">{{ actionTitle(act) }}</span>
                <span
                  v-if="act.items.length > 1"
                  class="tp-item__batch"
                  :title="`本组共 ${act.items.length} 项`"
                >
                  ×{{ act.items.length }}
                </span>
                <span class="tp-item__spacer"></span>
                <span
                  class="tp-item__toggle"
                  @click.stop="toggleAction(act)"
                  :title="act.expanded ? '收起' : '展开'"
                >
                  <el-icon>
                    <component :is="act.expanded ? ArrowUp : ArrowDown" />
                  </el-icon>
                </span>
              </div>
              <div class="tp-item__meta">
                <!-- 单节点：可点击的真实节点名；批量：不展示易误读的伪名，只显示节点数量 -->
                <span
                  v-if="!act.isBatch"
                  class="tp-item__node"
                  @click.stop="goNodeOnly(act)"
                  :title="`跳转到「${act.realNodes[0]}」`"
                >
                  📍 {{ act.realNodes[0] }}
                </span>
                <span v-else class="tp-item__node tp-item__node--batch" :title="`批量涉及 ${act.realNodes.length} 个节点`">
                  📍 涉 {{ act.realNodes.length }} 个节点
                </span>
                <span class="tp-item__sep">·</span>
                <span class="tp-item__source">
                  {{ act.source === 'agent' ? '助手' : '手动' }}
                </span>
                <span v-if="act.items.length > 1" class="tp-item__sep">·</span>
                <span v-if="act.items.length > 1" class="tp-item__range">
                  ⏱ {{ actionTimeRange(act) }}
                </span>
                <span v-if="act.kindsSummary" class="tp-item__sep">·</span>
                <span v-if="act.kindsSummary" class="tp-item__kinds">
                  {{ act.kindsSummary }}
                </span>
              </div>

              <!-- 详情：动作子项 —— 批量动作的核心展示区 -->
              <div v-if="act.expanded" class="tp-item__detail">
                <div
                  v-for="(it, i) in act.items"
                  :key="i"
                  class="tp-subitem"
                  :class="`tp-subitem--${it.type}`"
                  :title="`点击打开「${it.node}」的${subItemIntentLabel(it.type)}`"
                  @click.stop="goSubItem(act, it)"
                >
                  <span class="tp-subitem__type">{{ typeIcon(it.type) }}</span>
                  <span class="tp-subitem__title">{{ it.title }}</span>
                  <span
                    v-if="act.isBatch && it.node"
                    class="tp-subitem__node-tag"
                    :title="`${it.node}`"
                  >
                    {{ it.node }}
                  </span>
                  <span v-if="it.status" class="tp-subitem__status">
                    <el-tag size="small" :type="statusTag(it.status)" effect="light">
                      {{ statusLabel(it.status) }}
                    </el-tag>
                  </span>
                  <span class="tp-subitem__time">{{ formatMin(it.datetime) }}</span>
                  <span class="tp-subitem__chev">›</span>
                </div>

                <!-- 底部操作：单节点 → 跳面板；批量 → 提示用户从明细里选 -->
                <div class="tp-item__detail-actions">
                  <el-button
                    v-if="!act.isBatch"
                    type="primary"
                    size="small"
                    plain
                    @click.stop="goAction(act)"
                  >
                    打开「{{ act.realNodes[0] }}」的面板
                  </el-button>
                  <span
                    v-else
                    class="tp-item__detail-hint"
                  >
                    点击上方任一项，可直达对应节点的面板（共 {{ act.realNodes.length }} 个节点）
                  </span>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- ─── Empty ─── -->
      <div v-else class="tp-empty">
        <span class="tp-empty__icon">🗓️</span>
        <span class="tp-empty__text">暂无活动记录</span>
        <span class="tp-empty__hint">
          {{ dateFilter ? '当天没有相关活动' : '该领域还没有任何动作' }}
        </span>
      </div>
    </div>
  </el-drawer>
</template>

<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import {
  Close,
  Refresh,
  Loading,
  ArrowUp,
  ArrowDown,
} from '@element-plus/icons-vue'
import * as api from '@/api'
import { useGraphStore } from '@/stores/graph'
import type { Activity } from '@/types/graph'

/* ════════════════════════════════════════════════════════════
   Types
   ════════════════════════════════════════════════════════════ */

type ActionKind =
  | 'create_node' // 创建节点（node_created）
  | 'rename_node' // 重命名节点（node_renamed / node_relinked）
  | 'delete_node' // 删除节点（node_deleted）
  | 'search_resource' // 搜索资料（web_resource_added）
  | 'organize_file' // 整理文件（upload_added）
  | 'create_plan' // 生成计划（plan_created）
  | 'process_plan' // 处理计划（plan_action_done / plan_action_skipped）
  | 'write_note' // 撰写笔记（note_generated / note_updated / note_rebuilt）
  | 'manage_domain' // 领域管理（domain_created）
  | 'manage_association' // 关联管理（association_created / association_deleted）
  | 'manage_card' // 提示卡（card_created / card_updated / card_deleted）
  | 'digest_pipeline' // 知识摘要（digest_started / digest_mindmap_generated / digest_slides_generated / digest_quiz_generated / digest_notes_generated / digest_failed）
  | 'skill_output' // 技能导出（pdf_generated / pptx_generated / docx_generated）
  | 'system_op' // 系统操作（graph_exported / graph_synced / fix_links）

interface Action {
  id: string
  /**
   * 纯展示用的聚合节点名：
   *   - 单节点：直接等于真实节点名
   *   - 多节点："<firstNode> 等 N 个节点"
   * ⚠ 这个字段绝对不要再传给 store.openNotePanel() 之类的"逻辑入口"，
   * ⚠ 因为它可能带有"等 N 个节点"这种不是真实图谱节点名的后缀。
   * ✅ 逻辑跳转请使用 realNodes[]。
   */
  node: string
  /** 真实涉及的图谱节点名（去重，保持时间顺序） */
  realNodes: string[]
  /** 是否跨多个节点的批量动作（用于决定点击交互行为） */
  isBatch: boolean
  kind: ActionKind
  datetime: string
  source: 'agent' | 'manual'
  items: Activity[]
  /** 显示态 */
  expanded: boolean
  /** 多 kind 混合时的展示补充 */
  kindsSummary?: string
}

const props = defineProps<{
  modelValue: boolean
  domain: string
}>()

const emit = defineEmits<{
  'update:modelValue': [val: boolean]
}>()

const graphStore = useGraphStore()

const drawerVisible = computed({
  get: () => props.modelValue,
  set: (v) => emit('update:modelValue', v),
})

const dateFilter = ref(todayStr())
const categoryFilter = ref<'all' | ActionKind>('all')
const rawActivities = ref<Activity[]>([])
const actionExpanded = ref<Record<string, boolean>>({})
const loading = ref(false)

/* ════════════════════════════════════════════════════════════
   Constants
   ════════════════════════════════════════════════════════════ */

const WINDOW_SECONDS = 3 * 60 // 3 分钟内同一动作类型自动归拢为一条（批量任务通常短时间内密集触发）

const categories: Array<{
  key: 'all' | ActionKind
  label: string
  icon: string
  color: string
}> = [
  { key: 'all', label: '全部', icon: '🗂️', color: '#94a3b8' },
  { key: 'create_node', label: '创建节点', icon: '🌱', color: '#22c55e' },
  { key: 'rename_node', label: '重命名节点', icon: '✏️', color: '#a855f7' },
  { key: 'delete_node', label: '删除节点', icon: '🗑️', color: '#ef4444' },
  { key: 'search_resource', label: '搜索资料', icon: '🔍', color: '#06b6d4' },
  { key: 'organize_file', label: '整理文件', icon: '📂', color: '#f59e0b' },
  { key: 'create_plan', label: '生成计划', icon: '🎯', color: '#7c5cff' },
  { key: 'process_plan', label: '处理计划', icon: '✅', color: '#10b981' },
  { key: 'write_note', label: '撰写笔记', icon: '📝', color: '#3b82f6' },
  { key: 'manage_domain', label: '领域管理', icon: '🌐', color: '#0ea5e9' },
  { key: 'manage_association', label: '关联管理', icon: '🔗', color: '#14b8a6' },
  { key: 'manage_card', label: '提示卡', icon: '🃏', color: '#eab308' },
  { key: 'digest_pipeline', label: '知识摘要', icon: '🧠', color: '#d946ef' },
  { key: 'skill_output', label: '导出文件', icon: '📄', color: '#64748b' },
  { key: 'system_op', label: '系统操作', icon: '⚙️', color: '#475569' },
]

/* ════════════════════════════════════════════════════════════
   Date helpers
   ════════════════════════════════════════════════════════════ */

function todayStr(): string {
  const d = new Date()
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

function toUnix(dt: string): number {
  // 兼容 "2026-07-22T22:24:00" 与 "2026-07-22T22:24:00.123456"
  if (!dt) return 0
  const t = Date.parse(dt.replace(' ', 'T'))
  return Number.isNaN(t) ? 0 : Math.floor(t / 1000)
}

/* ════════════════════════════════════════════════════════════
   Action classification
   ════════════════════════════════════════════════════════════ */

function classify(item: Activity): ActionKind {
  const t = item.type
  // ---- Node CRUD --------------------------------------------------
  if (t === 'node_created') return 'create_node'
  if (t === 'node_renamed' || t === 'node_relinked') return 'rename_node'
  if (t === 'node_deleted') return 'delete_node'
  // ---- Domain -----------------------------------------------------
  if (t === 'domain_created') return 'manage_domain'
  // ---- Resources / uploads ----------------------------------------
  if (t === 'web_resource' || t === 'web_resource_added') return 'search_resource'
  if (t === 'upload' || t === 'upload_added') return 'organize_file'
  // ---- Plans ------------------------------------------------------
  if (t === 'plan_created' || t === 'plan') {
    // Legacy "plan" entries had a ``status`` field on the activity;
    // keep the rule so old data still classifies correctly.  Newly
    // emitted "plan_created" events always carry status=pending.
    return item.status === 'done' || item.status === 'skipped'
      ? 'process_plan'
      : 'create_plan'
  }
  if (
    t === 'plan_action_done' ||
    t === 'plan_action_skipped' ||
    t === 'plan_deleted'
  ) {
    return 'process_plan'
  }
  // ---- Notes ------------------------------------------------------
  if (t === 'note' || t === 'note_generated' || t === 'note_updated' || t === 'note_rebuilt') {
    return 'write_note'
  }
  // ---- Associations (manual) --------------------------------------
  if (t === 'association_created' || t === 'association_deleted') {
    return 'manage_association'
  }
  // ---- Prompt cards ----------------------------------------------
  if (t === 'card_created' || t === 'card_updated' || t === 'card_deleted') {
    return 'manage_card'
  }
  // ---- Digest pipeline --------------------------------------------
  if (
    t === 'digest_started' ||
    t === 'digest_mindmap_generated' ||
    t === 'digest_slides_generated' ||
    t === 'digest_quiz_generated' ||
    t === 'digest_notes_generated' ||
    t === 'digest_failed'
  ) {
    return 'digest_pipeline'
  }
  // ---- Skill outputs ----------------------------------------------
  if (t === 'pdf_generated' || t === 'pptx_generated' || t === 'docx_generated') {
    return 'skill_output'
  }
  // ---- System ops -------------------------------------------------
  if (t === 'graph_exported' || t === 'graph_synced' || t === 'fix_links') {
    return 'system_op'
  }
  // 兜底：未知类型按资料归档，避免被静默丢失
  return 'search_resource'
}

function isProcessStatus(s?: string): boolean {
  return s === 'done' || s === 'skipped'
}

/**
 * 把原始 Activity 列表聚合成「动作」。
 * 新规则（按类别自动归拢）：
 *   1. 同 kind + 时间窗内连续 ⇒ 合并为一个动作（忽略节点差异，便于观察批量/并发）
 *   2. 跨时间窗的同 kind 仍归为一类，但显示成独立的动作卡（带序号）
 *   3. 聚合粒度：默认 3 分钟。批量任务通常短时间内密集触发，3 分钟足够把它们归拢；
 *      不同时间段则自然分开，便于观察并发举动。
 */
function buildActions(items: Activity[]): Action[] {
  const sorted = [...items].sort(
    (a, b) => toUnix(a.datetime) - toUnix(b.datetime),
  )

  const out: Action[] = []
  let i = 0
  while (i < sorted.length) {
    const head = sorted[i]
    const kind = classify(head)
    const group: Activity[] = [head]
    let j = i + 1
    while (j < sorted.length) {
      const next = sorted[j]
      if (classify(next) !== kind) break
      if (
        toUnix(next.datetime) - toUnix(group[group.length - 1].datetime) >
        WINDOW_SECONDS
      )
        break
      group.push(next)
      j++
    }
    out.push(makeAction(kind, group))
    i = j
  }

  // 最新动作在前
  out.sort((a, b) => toUnix(b.datetime) - toUnix(a.datetime))
  return out
}

function makeAction(kind: ActionKind, items: Activity[]): Action {
  // 按时间排序，取最早作为动作发生时间
  const itemsSorted = [...items].sort((a, b) => toUnix(a.datetime) - toUnix(b.datetime))
  const datetime = itemsSorted[0]?.datetime || ''
  // 真实节点列表（去重，按出现顺序）
  const seen = new Set<string>()
  const realNodes: string[] = []
  for (const it of itemsSorted) {
    if (!seen.has(it.node)) {
      seen.add(it.node)
      realNodes.push(it.node)
    }
  }
  // 展示用聚合名（节点数 ≥ 2 时带"等 N 个节点"）
  const node =
    realNodes.length === 1
      ? realNodes[0]
      : `${realNodes[0]} 等 ${realNodes.length} 个节点`
  const isBatch = realNodes.length > 1
  // 优先展示 agent 触发，否则 manual
  const source: 'agent' | 'manual' = itemsSorted.some((i) => i.source === 'agent')
    ? 'agent'
    : 'manual'

  const distinctKinds = Array.from(new Set(itemsSorted.map((i) => classify(i))))
  const kindsSummary =
    distinctKinds.length > 1
      ? distinctKinds.map((k) => actionLabel(k)).join(' + ')
      : undefined

  const id = `${kind}#${datetime}#${itemsSorted.length}`
  return {
    id,
    node,
    realNodes,
    isBatch,
    kind,
    datetime,
    source,
    items: itemsSorted,
    expanded: !!actionExpanded.value[id],
    kindsSummary,
  }
}

/* ════════════════════════════════════════════════════════════
   Computed: filtered + count
   ════════════════════════════════════════════════════════════ */

const allActions = computed<Action[]>(() => buildActions(rawActivities.value))

const filteredActions = computed<Action[]>(() => {
  if (categoryFilter.value === 'all') return allActions.value
  return allActions.value.filter((a) => a.kind === categoryFilter.value)
})

function countOf(kind: 'all' | ActionKind): number {
  if (kind === 'all') return allActions.value.length
  return allActions.value.filter((a) => a.kind === kind).length
}

/* ════════════════════════════════════════════════════════════
   Data loading
   ════════════════════════════════════════════════════════════ */

async function reload() {
  if (!props.domain) {
    rawActivities.value = []
    return
  }
  loading.value = true
  try {
    const res = await api.getTimeline(props.domain, {
      date: dateFilter.value || undefined,
    })
    rawActivities.value = res.items || []
  } catch {
    rawActivities.value = []
  } finally {
    loading.value = false
  }
}

watch(
  () => [props.modelValue, props.domain],
  async ([visible, domain]) => {
    if (visible && domain) {
      if (!dateFilter.value) dateFilter.value = todayStr()
      await reload()
    }
  },
  { immediate: true },
)

/* ════════════════════════════════════════════════════════════
   Presentation helpers
   ════════════════════════════════════════════════════════════ */

function actionIcon(k: ActionKind): string {
  return categories.find((c) => c.key === k)?.icon || '•'
}
function actionColor(k: ActionKind): string {
  return categories.find((c) => c.key === k)?.color || '#94a3b8'
}
function actionLabel(k: ActionKind): string {
  return categories.find((c) => c.key === k)?.label || k
}
function categoryLabel(k: 'all' | ActionKind): string {
  return categories.find((c) => c.key === k)?.label || String(k)
}

function actionTitle(act: Action): string {
  const n = act.items.length
  switch (act.kind) {
    case 'create_node': {
      const nodeCount = new Set(act.items.map((i) => i.node)).size
      return nodeCount > 1
        ? `批量新建了 ${n} 个节点（涉及 ${nodeCount} 个节点）`
        : `新建了节点 [${act.node}]`
    }
    case 'rename_node': {
      // The backend title already reads 「重命名 A → B」; if multiple
      // renames collapsed into one card, surface a count.
      return n > 1
        ? `重命名了 ${n} 个节点`
        : act.items[0]?.title || `重命名了节点 [${act.node}]`
    }
    case 'delete_node': {
      const nodeCount = new Set(act.items.map((i) => i.node)).size
      return nodeCount > 1
        ? `批量删除了 ${n} 个节点（涉及 ${nodeCount} 个节点）`
        : act.items[0]?.title || `删除了节点 [${act.node}]`
    }
    case 'search_resource':
      return n > 1 ? `搜索了 ${n} 份资料` : `搜索了 1 份资料`
    case 'organize_file':
      return n > 1 ? `整理了 ${n} 个文件` : `整理了 1 个文件`
    case 'create_plan':
      return n > 1 ? `生成了 ${n} 条计划` : `新增了 1 条计划`
    case 'process_plan': {
      const done = act.items.filter((i) => i.status === 'done').length
      const skip = act.items.filter((i) => i.status === 'skipped').length
      const parts: string[] = []
      if (done) parts.push(`完成 ${done}`)
      if (skip) parts.push(`跳过 ${skip}`)
      return `处理计划：${parts.join('，') || '更新'}`
    }
    case 'write_note': {
      const nodeCount = new Set(act.items.map((i) => i.node)).size
      return nodeCount > 1
        ? `批量撰写了 ${n} 篇笔记（涉及 ${nodeCount} 个节点）`
        : `撰写了笔记`
    }
  }
}

/** 聚合动作的时间区间文本（用于明细头部） */
function actionTimeRange(act: Action): string {
  if (act.items.length < 2) return formatMin(act.datetime)
  const first = act.items[0].datetime
  const last = act.items[act.items.length - 1].datetime
  const a = formatMin(first)
  const b = formatMin(last)
  return a === b ? a : `${a} → ${b}`
}

function formatTime(dt: string): string {
  if (!dt) return ''
  try {
    const datePart = dt.slice(0, 10)
    const timePart = dt.length >= 16 ? dt.slice(11, 16) : ''
    return timePart ? `${datePart.slice(5)} ${timePart}` : datePart.slice(5)
  } catch {
    return dt
  }
}

function formatMin(dt: string): string {
  return dt.length >= 16 ? dt.slice(11, 16) : dt
}

function typeIcon(t: string): string {
  return (
    {
      // legacy / generic shapes (still used by historical data)
      plan: '🎯',
      web_resource: '🌐',
      upload: '📂',
      note: '📝',
      // new bus-emitted kinds
      node_created: '🌱',
      node_renamed: '✏️',
      node_relinked: '🔗',
      node_deleted: '🗑️',
      web_resource_added: '🌐',
      upload_added: '📂',
      plan_created: '🎯',
      plan_action_done: '✅',
      plan_action_skipped: '⏭️',
      plan_deleted: '🗑️',
      note_generated: '📝',
      note_rebuilt: '📝',
      note_updated: '📝',
      // domain / association / card / digest / skill / system
      domain_created: '🌐',
      association_created: '🔗',
      association_deleted: '🔗',
      card_created: '🃏',
      card_updated: '🃏',
      card_deleted: '🃏',
      digest_started: '🧠',
      digest_mindmap_generated: '🧠',
      digest_slides_generated: '🧠',
      digest_quiz_generated: '🧠',
      digest_notes_generated: '🧠',
      digest_failed: '⚠️',
      pdf_generated: '📄',
      pptx_generated: '📄',
      docx_generated: '📄',
      graph_exported: '📦',
      graph_synced: '🔄',
      fix_links: '🔧',
    } as Record<string, string>
  )[t] || '•'
}

function subItemIntentLabel(t: string): string {
  return (
    {
      note: '笔记',
      plan: '计划',
      web_resource: '学习资料',
      upload: '上传文件',
      // bus kinds
      note_generated: '笔记',
      note_rebuilt: '笔记',
      note_updated: '笔记',
      plan_created: '计划',
      plan_action_done: '计划（完成）',
      plan_action_skipped: '计划（跳过）',
      plan_deleted: '计划',
      node_created: '节点',
      node_renamed: '节点（重命名）',
      node_relinked: '节点（更新链接）',
      node_deleted: '节点（删除）',
      web_resource_added: '学习资料',
      upload_added: '上传文件',
      // new domains
      domain_created: '领域',
      association_created: '关联（新增）',
      association_deleted: '关联（删除）',
      card_created: '提示卡',
      card_updated: '提示卡',
      card_deleted: '提示卡',
      digest_started: '知识摘要',
      digest_mindmap_generated: '知识摘要（脑图）',
      digest_slides_generated: '知识摘要（幻灯片）',
      digest_quiz_generated: '知识摘要（测验）',
      digest_notes_generated: '知识摘要（笔记）',
      digest_failed: '知识摘要（失败）',
      pdf_generated: 'PDF',
      pptx_generated: 'PPTX',
      docx_generated: 'DOCX',
      graph_exported: '导出快照',
      graph_synced: '派生同步',
      fix_links: '修复孤链',
    } as Record<string, string>
  )[t] || '详情'
}

function statusLabel(s: string): string {
  return ({ pending: '待办', done: '完成', skipped: '跳过' } as Record<string, string>)[s] || s
}

function statusTag(s: string): '' | 'success' | 'info' | 'warning' {
  if (s === 'done') return 'success'
  if (s === 'skipped') return 'info'
  return 'warning'
}

/* ════════════════════════════════════════════════════════════
   Interaction
   ════════════════════════════════════════════════════════════ */

function intentForKind(k: ActionKind): 'plan' | 'resource' | 'note' | null {
  if (k === 'create_plan' || k === 'process_plan') return 'plan'
  if (k === 'search_resource' || k === 'organize_file') return 'resource'
  if (k === 'write_note') return 'note'
  // Node-related actions: open the panel with no specific intent; the
  // panel default view is the node's note / resources / plan overview.
  if (k === 'create_node' || k === 'rename_node' || k === 'delete_node') return null
  // Association / card / digest / skill / system / domain — no panel
  // target.  The TimelinePanel renders its own expansion with details.
  return null
}

/**
 * 入口分流：
 *   - 单节点（distinctNodes.length === 1）：直接打开对应面板。
 *   - 批量（distinctNodes.length > 1）：什么都不打开，而是强制展开明细，
 *     避免把伪节点名（如 "认知能力 等 3 个节点"）传给 store，进而让后端
 *     给一个不存在的"节点"建目录 + 调 LLM 生成 node.md。
 */
function goAction(act: Action) {
  if (act.isBatch) {
    // 强制展开，让用户点击具体子项去对应节点
    actionExpanded.value[act.id] = true
    act.expanded = true
    return
  }
  const realNode = act.realNodes[0]
  openPanelForNode(realNode, intentForKind(act.kind))
  drawerVisible.value = false
}

function goNodeOnly(act: Action) {
  const realNode = act.realNodes[0]
  const node = graphStore.nodeMap?.get(realNode)
  if (node && typeof (graphStore as any).selectNode === 'function') {
    ;(graphStore as any).selectNode(node)
  }
  drawerVisible.value = false
}

// Activity → intent (used only by goSubItem).
// Keep this in sync with the kinds emitted by src/agent/tools/plan_tools.py
// and src/api/routes/plans.py — missing a key here is exactly the
// "clicked plan → opened 学习资料" bug. Legacy "plan" / "note" are
// kept for backwards-compat with old activity rows.
const ACTIVITY_INTENT: Record<
  string,
  'plan' | 'resource' | 'note'
> = {
  // Notes
  note: 'note',
  note_generated: 'note',
  note_rebuilt: 'note',
  note_updated: 'note',
  // Plans (current bus kinds + legacy)
  plan: 'plan',
  plan_created: 'plan',
  plan_action_done: 'plan',
  plan_action_skipped: 'plan',
  plan_deleted: 'plan',
  // Resources (default for search/upload activities)
  web_resource: 'resource',
  web_resource_added: 'resource',
  upload: 'resource',
  upload_added: 'resource',
}

/** 点击明细中的子项，按活动类型跳到对应节点的对应 tab */
function goSubItem(act: Action, it: Activity) {
  const intent = ACTIVITY_INTENT[it.type] ?? 'resource'
  openPanelForNode(it.node, intent)
  drawerVisible.value = false
}

/** 真正的"打开面板"入口——只接受图谱里真实存在的节点名 */
function openPanelForNode(nodeName: string, intent: 'plan' | 'resource' | 'note' | null) {
  // 防御 1：伪节点名（带"等 N 个节点"）一律拒收
  if (!nodeName || /等\s*\d+\s*个节点/.test(nodeName)) {
    console.warn('[TimelinePanel] refuse to open panel for fake node name:', nodeName)
    return
  }
  // 防御 2：节点不在图谱里也拒收，避免后端 _ensure_node_dir 凭空建目录
  const node = graphStore.nodeMap?.get(nodeName)
  if (!node) {
    console.warn('[TimelinePanel] node not in graph, skip:', nodeName)
    return
  }
  if (typeof (graphStore as any).openNotePanel === 'function') {
    ;(graphStore as any).openNotePanel(nodeName, intent)
  } else {
    ;(graphStore as any).selectNode?.(node)
  }
}

function toggleAction(act: Action) {
  actionExpanded.value[act.id] = !actionExpanded.value[act.id]
  act.expanded = actionExpanded.value[act.id]
}

function close() {
  drawerVisible.value = false
}
</script>

<style scoped>
/* ════════════════════════════════════════════════════════════
   Timeline Panel — 活动时间线看板（按动作汇总）
   ════════════════════════════════════════════════════════════ */
.timeline-panel {
  display: flex;
  flex-direction: column;
  height: 100%;
  overflow: hidden;
  background: var(--bg-secondary);
}

/* ── Header ── */
.tp-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 16px 20px 12px;
  background: linear-gradient(180deg, var(--bg-tertiary) 0%, var(--bg-secondary) 100%);
  border-bottom: 1px solid var(--border-color);
  flex-shrink: 0;
  gap: 12px;
}
.tp-header__left {
  display: flex;
  align-items: center;
  gap: 10px;
  flex: 1;
  min-width: 0;
}
.tp-header__icon { font-size: 20px; }
.tp-header__text {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}
.tp-header__title {
  font-size: 16px;
  font-weight: 700;
  color: var(--text-primary);
}
.tp-header__domain {
  font-size: 12px;
  color: var(--text-muted);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* ── Toolbar ── */
.tp-toolbar {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 12px 20px 0;
  flex-shrink: 0;
}
.tp-date { flex: 1; min-width: 0; }

/* ── Action category chips ── */
.tp-categories {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  padding: 10px 20px 0;
  flex-shrink: 0;
}
.tp-chip {
  --chip-color: #94a3b8;
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 4px 10px;
  border: 1px solid var(--border-color);
  background: var(--bg-tertiary);
  color: var(--text-secondary);
  border-radius: 14px;
  font-size: 12px;
  cursor: pointer;
  transition: border-color 0.15s, background 0.15s, color 0.15s;
}
.tp-chip:hover {
  border-color: var(--chip-color);
  color: var(--text-primary);
}
.tp-chip--active {
  background: var(--chip-color);
  border-color: var(--chip-color);
  color: #fff;
  font-weight: 600;
}
.tp-chip__count {
  background: rgba(0, 0, 0, 0.18);
  color: inherit;
  padding: 0 6px;
  border-radius: 8px;
  font-size: 11px;
  font-family: 'JetBrains Mono', monospace;
}
.tp-chip--active .tp-chip__count { background: rgba(255, 255, 255, 0.25); }

/* ── Stats ── */
.tp-stats {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 20px 0;
  font-size: 12px;
  flex-shrink: 0;
  flex-wrap: wrap;
}
.tp-stat { color: var(--text-muted); }
.tp-stat--num {
  font-weight: 700;
  color: var(--accent-blue);
  font-family: 'JetBrains Mono', monospace;
}
.tp-stat--when,
.tp-stat--type {
  background: var(--bg-tertiary);
  border: 1px solid var(--border-color);
  padding: 1px 8px;
  border-radius: 10px;
  font-family: 'JetBrains Mono', monospace;
}

/* ── Hint ── */
.tp-hint {
  padding: 8px 20px 4px;
  font-size: 11.5px;
  color: var(--text-muted);
  line-height: 1.5;
  flex-shrink: 0;
}

/* ── Loading ── */
.tp-loading {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 60px 0;
  color: var(--text-muted);
  font-size: 14px;
}

/* ── Action list ── */
.tp-list {
  flex: 1;
  overflow-y: auto;
  padding: 12px 20px 40px;
}

/* ── Item ── */
.tp-item {
  display: flex;
  gap: 12px;
  min-height: 56px;
  cursor: pointer;
}

/* 左侧时间轴 */
.tp-item__rail {
  display: flex;
  flex-direction: column;
  align-items: center;
  flex-shrink: 0;
  width: 14px;
  padding-top: 4px;
}
.tp-item__dot {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  flex-shrink: 0;
  border: 2px solid var(--bg-secondary);
  box-shadow: 0 0 0 1px var(--border-color);
  z-index: 1;
}
.tp-item__line {
  width: 2px;
  flex: 1;
  background: var(--border-color);
  margin-top: 2px;
  min-height: 20px;
}

.tp-item__body {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding-bottom: 14px;
}

.tp-item__time {
  font-size: 11.5px;
  font-weight: 600;
  color: var(--text-muted);
  font-family: 'JetBrains Mono', monospace;
  letter-spacing: 0.02em;
}

.tp-item__card {
  background: var(--bg-tertiary);
  border: 1px solid var(--border-color);
  border-left-width: 3px;
  border-radius: 8px;
  padding: 8px 12px;
  transition: border-color 0.15s, transform 0.15s, background 0.15s;
}
.tp-item__card:hover {
  border-color: var(--border-light);
  background: var(--bg-quaternary, var(--bg-tertiary));
  transform: translateX(2px);
}

.tp-item__title-row {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 4px;
}
.tp-item__action-icon { font-size: 14px; flex-shrink: 0; }
.tp-item__title {
  font-size: 13.5px;
  color: var(--text-primary);
  line-height: 1.4;
  word-break: break-word;
  font-weight: 500;
}
.tp-item__batch {
  font-size: 11px;
  font-weight: 700;
  color: var(--accent-blue);
  background: rgba(59, 130, 246, 0.12);
  padding: 1px 7px;
  border-radius: 9px;
  font-family: 'JetBrains Mono', monospace;
  flex-shrink: 0;
  line-height: 1.4;
}
.tp-item__range {
  font-family: 'JetBrains Mono', monospace;
  font-size: 11px;
  color: var(--text-secondary);
}
.tp-item__spacer { flex: 1; }
.tp-item__toggle {
  font-size: 14px;
  color: var(--text-muted);
  padding: 2px 4px;
  border-radius: 4px;
}
.tp-item__toggle:hover { background: var(--bg-secondary); color: var(--text-primary); }

.tp-item__meta {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 11.5px;
  color: var(--text-muted);
  flex-wrap: wrap;
}
.tp-item__node {
  color: var(--accent-blue);
  font-weight: 600;
  cursor: pointer;
  border-bottom: 1px dashed transparent;
}
.tp-item__node:hover { border-bottom-color: var(--accent-blue); }
.tp-item__sep { opacity: 0.5; }
.tp-item__source { font-size: 11px; }
.tp-item__kinds { font-size: 11px; opacity: 0.8; }

/* 批量卡片：轻微虚化边框提示用户"这里有多节点" */
.tp-item--batch > .tp-item__body > .tp-item__card {
  border-left-width: 3px;
  border-left-style: double;
}
.tp-item--batch > .tp-item__body > .tp-item__card:hover {
  border-left-style: solid;
}

/* ── Detail (expanded) ── */
.tp-item__detail {
  margin-top: 8px;
  padding-top: 8px;
  border-top: 1px dashed var(--border-color);
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.tp-subitem {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 4px 6px;
  border-radius: 4px;
  font-size: 12px;
  color: var(--text-secondary);
  background: var(--bg-secondary);
  cursor: pointer;
  transition: background 0.12s, transform 0.12s, border-color 0.12s;
  border: 1px solid transparent;
}
.tp-subitem:hover {
  background: var(--bg-tertiary);
  border-color: var(--accent-blue);
  transform: translateX(2px);
}
.tp-subitem__type { font-size: 12px; flex-shrink: 0; }
.tp-subitem__title {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.tp-subitem__status .el-tag { font-size: 10px !important; height: 18px !important; padding: 0 6px !important; }
.tp-subitem__time {
  font-size: 10.5px;
  color: var(--text-muted);
  font-family: 'JetBrains Mono', monospace;
  flex-shrink: 0;
}
.tp-subitem__node-tag {
  font-size: 10.5px;
  font-family: 'JetBrains Mono', monospace;
  color: var(--accent-blue);
  background: rgba(76, 125, 255, 0.1);
  border: 1px solid rgba(76, 125, 255, 0.2);
  padding: 0 6px;
  border-radius: 8px;
  flex-shrink: 0;
  max-width: 160px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.tp-subitem__chev {
  color: var(--text-muted);
  font-weight: 700;
  font-size: 14px;
  line-height: 1;
  flex-shrink: 0;
  transition: transform 0.12s, color 0.12s;
}
.tp-subitem:hover .tp-subitem__chev {
  color: var(--accent-blue);
  transform: translateX(2px);
}
.tp-item__detail-actions {
  margin-top: 8px;
  display: flex;
  justify-content: flex-end;
  align-items: center;
}
.tp-item__detail-hint {
  font-size: 11.5px;
  color: var(--text-muted);
  font-style: italic;
}
.tp-item__node--batch {
  cursor: default;
  border-bottom-style: dotted !important;
}

/* ── Empty ── */
.tp-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 6px;
  padding: 80px 20px;
  text-align: center;
}
.tp-empty__icon { font-size: 40px; opacity: 0.5; }
.tp-empty__text { font-size: 15px; font-weight: 600; color: var(--text-secondary); }
.tp-empty__hint { font-size: 12px; color: var(--text-muted); }

/* ── Drawer overrides ── */
.timeline-panel-drawer :deep(.el-drawer__body) {
  padding: 0;
  background: var(--bg-secondary);
}
</style>
