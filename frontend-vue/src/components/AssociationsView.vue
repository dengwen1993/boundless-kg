<template>
  <section class="assoc-pane">
    <!-- ========================== 顶部 Hero 概览 ========================== -->
    <div class="assoc-hero">
      <div class="assoc-hero__main">
        <div class="assoc-hero__title">
          <span class="assoc-hero__icon">🕸️</span>
          <h2>知识图谱</h2>
          <span class="assoc-hero__sub">点一下节点，看它和谁相关</span>
        </div>
              </div>
      <div class="assoc-hero__stats">
        <div class="stat">
          <span class="stat__value">{{ store.statistics.concepts }}</span>
          <span class="stat__label">主题</span>
        </div>
        <div class="stat">
          <span class="stat__value">{{ store.statistics.resources }}</span>
          <span class="stat__label">资料</span>
        </div>
        <div class="stat stat--accent">
          <span class="stat__value">{{ store.statistics.associations }}</span>
          <span class="stat__label">关联</span>
        </div>
        <div class="stat">
          <span class="stat__value">{{ store.statistics.derived_events }}</span>
          <span class="stat__label">事件</span>
        </div>
      </div>
    </div>

    <!-- ========================== 工具栏 ========================== -->
    <div class="assoc-toolbar">
      <div class="assoc-toolbar__right">
        <el-button
          size="small"
          :loading="store.saving"
          :disabled="!store.graph"
          @click="onReshuffle"
        >
          🔄 重新梳理
        </el-button>
        <el-button
          size="small"
          :loading="store.saving"
          :disabled="!store.graph"
          @click="onThinkMore"
        >
          ⚡ 找更多关联
        </el-button>
        <el-popconfirm
          title="确认清空当前领域的所有派生关联？此操作不可恢复。"
          @confirm="onClear"
        >
          <template #reference>
            <el-button size="small" :disabled="!store.graph">
              清空
            </el-button>
          </template>
        </el-popconfirm>
      </div>
    </div>

    <!-- ========================== 主体 ========================== -->
    <div class="assoc-body">
      <!-- ====== 左侧：图谱 ====== -->
      <main class="assoc-main">
        <!-- 钻取面包屑（仅在 drill 模式下显示；告诉用户当前是哪个节点的下属视图） -->
        <div v-if="isDrilled" class="drill-bar">
          <button class="drill-bar__home" @click="drillReset" title="回到全局视图">
            🏠 全部节点
          </button>
          <template v-for="(n, idx) in drillBreadcrumb" :key="`${idx}-${n}`">
            <span class="drill-bar__sep">›</span>
            <button
              class="drill-bar__crumb"
              :class="{ 'drill-bar__crumb--current': idx === drillBreadcrumb.length - 1 }"
              :title="idx === drillBreadcrumb.length - 1 ? '当前根节点' : `回到「${n}」这一层`"
              @click="drillToLevel(idx + 1)"
            >
              {{ n }}
            </button>
          </template>
          <span class="drill-bar__meta">
            仅显示与「{{ currentRoot }}」关联的 {{ currentRootEdgeCount }} 条关联
          </span>
        </div>

        <div class="graph-canvas-wrapper">
          <div ref="canvasRef" class="graph-canvas"></div>
          <!-- 浮动缩放控件（必须在 canvasRef 外面，否则 clearCanvas 会清掉） -->
          <div class="assoc-canvas-controls">
            <button class="assoc-ctrl-btn" title="放大" @click="zoomBy(1.3)">+</button>
            <button class="assoc-ctrl-btn" title="缩小" @click="zoomBy(0.77)">−</button>
            <div class="assoc-ctrl-divider"></div>
            <button class="assoc-ctrl-btn" title="适配全部节点" @click="fitToView()">⤢</button>
            <button class="assoc-ctrl-btn" title="回到 100% 缩放" @click="resetView()">⊙</button>
            <template v-if="isDrilled">
              <div class="assoc-ctrl-divider"></div>
              <button class="assoc-ctrl-btn assoc-ctrl-btn--primary" title="返回上一级" @click="drillUp">↑</button>
            </template>
          </div>
          <!-- 当前缩放提示 -->
          <div class="assoc-zoom-indicator" v-if="zoomLevel !== 1">{{ zoomPercent }}%</div>
        </div>
              </main>

      <!-- ====== 右侧：详情面板 ====== -->
      <aside class="assoc-detail">
        <template v-if="store.focusNode">
          <!-- 头部 -->
          <header class="detail__header">
            <div>
              <span
                class="detail__dot"
                :style="{ background: focusDotColor }"
              ></span>
              <h2 class="detail__title">{{ store.focusNode }}</h2>
              <span v-if="focusConcept" class="detail__tier">
                {{ tierLabel(focusConcept.level) }}
              </span>
            </div>
            <el-button text size="small" @click="store.setFocus(null)">关闭</el-button>
          </header>

          <!-- 简介 -->
          <section v-if="focusConcept" class="detail__section">
            <h4>📖 这是什么</h4>
            <p v-if="focusConcept.description" class="detail__text">
              {{ focusConcept.description }}
            </p>
            <p v-else class="detail__text detail__text--muted">
              暂无简介。试试在笔记里写一段，或点「重新梳理」。
            </p>
          </section>

          <!-- 关联主题 -->
          <section class="detail__section">
            <h4>🔗 关联主题</h4>
            <div v-if="relateGroups.length === 0" class="detail__text detail__text--muted">
              目前还没有发现关联。在笔记里用
              <code>@节点名</code>
              引用其他主题，然后点「重新梳理」。
            </div>
            <div v-for="g in relateGroups" :key="g.relation" class="relate-group">
              <div class="relate-group__title">
                <span class="relate-group__glyph">{{ RELATION_GLYPH[g.relation] }}</span>
                {{ relationLabel(g.relation) }}
                <span class="relate-group__count">{{ g.items.length }}</span>
              </div>
              <div class="relate-group__items">
                <button
                  v-for="r in g.items"
                  :key="r.name"
                  class="relate-chip"
                  @click="store.setFocus(r.name)"
                >
                  {{ r.name }}
                </button>
              </div>
            </div>
          </section>

          <!-- 学习建议 -->
          <section v-if="learningTips.length" class="detail__section">
            <h4>💡 学习建议</h4>
            <ul class="tips">
              <li v-for="t in learningTips" :key="t">{{ t }}</li>
            </ul>
          </section>

          <!-- 关联资料 -->
          <section v-if="store.focusNodeAssociations.resources.length" class="detail__section">
            <h4>📎 配套资料 ({{ store.focusNodeAssociations.resources.length }})</h4>
            <ul class="res-list">
              <li
                v-for="r in store.focusNodeAssociations.resources"
                :key="r.id"
                class="res-item"
              >
                <span class="res-item__icon">{{ RESOURCE_ICON[r.type] }}</span>
                <div class="res-item__body">
                  <div class="res-item__name">
                    <a
                      v-if="r.type === 'resource' && r.payload?.url"
                      :href="String(r.payload.url)"
                      target="_blank"
                      rel="noopener"
                    >{{ r.payload?.title ? String(r.payload.title) : String(r.payload.url) }}</a>
                    <span v-else>{{ r.type === 'note' ? '查看笔记' : r.type }}</span>
                  </div>
                  <p v-if="r.summary" class="res-item__summary">
                    {{ truncate(r.summary, 100) }}
                  </p>
                </div>
              </li>
            </ul>
          </section>
        </template>

        <template v-else>
          <div class="detail__empty">
            <div class="detail__empty-icon">👈</div>
            <h3>从这里开始</h3>
            <ol class="detail__steps">
              <li><b>点任意节点</b> — 右侧显示简介、关联、配套资料</li>
              <li><b>拖拽节点</b> — 重新摆位，让图谱更清晰</li>
              <li><b>滚轮缩放</b> — 看全局或细节</li>
              <li><b>想加新关联？</b> — 去「大纲」编辑笔记，用 <code>@节点名</code> 互相引用，再回这里点「重新梳理」</li>
            </ol>
            <el-alert
              v-if="store.statistics.associations === 0 && !store.loading"
              type="info"
              :closable="false"
              show-icon
              class="detail__alert"
            >
              <template #title>
                <span>暂无关联 —— 点上面「🔄 重新梳理」试试</span>
              </template>
            </el-alert>

            <!-- 关系 ↔ 颜色图例：让用户能解读图上每条线的含义 -->
            <div class="detail__legend">
              <h4 class="detail__legend-title">🎨 边的颜色</h4>
              <div
                v-for="group in LEGEND_GROUPS"
                :key="group.title"
                class="legend-group"
              >
                <div class="legend-group__title">{{ group.title }}</div>
                <div class="legend-grid">
                  <div
                    v-for="item in group.items"
                    :key="item.value"
                    class="legend-item"
                  >
                    <span
                      class="legend-item__swatch"
                      :style="{ background: item.color }"
                    ></span>
                    <div class="legend-item__body">
                      <div class="legend-item__label">{{ item.label }}</div>
                      <code class="legend-item__code">{{ item.value }}</code>
                    </div>
                  </div>
                </div>
              </div>
            </div>

            <!-- 节点颜色图例：swatch 直径与图上节点真实半径一致，方便对应 -->
            <div class="detail__legend">
              <h4 class="detail__legend-title">⚪ 点的颜色</h4>
              <div
                v-for="group in NODE_LEGEND_GROUPS"
                :key="group.title"
                class="legend-group"
              >
                <div class="legend-group__title">{{ group.title }}</div>
                <div class="legend-grid legend-grid--nodes">
                  <div
                    v-for="item in group.items"
                    :key="item.label"
                    class="legend-item legend-item--node"
                  >
                    <span
                      class="legend-item__dot"
                      :style="{
                        background: item.color,
                        width: item.size + 'px',
                        height: item.size + 'px',
                      }"
                    ></span>
                    <div class="legend-item__body">
                      <div class="legend-item__label">{{ item.label }}</div>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </template>
      </aside>
    </div>

    <!-- 右键菜单（节点 / 背景共享） -->
    <div
      v-if="contextMenu.visible"
      class="assoc-context-menu"
      :style="{ left: contextMenu.x + 'px', top: contextMenu.y + 'px' }"
      @click.stop
    >
      <button
        v-if="contextMenu.target && !contextMenu.target.resourceType"
        class="assoc-context-menu__item"
        @click="onViewNode"
      >
        <span>👁</span> 查看详情
      </button>
      <button
        v-if="contextMenu.target && !contextMenu.target.resourceType && !contextMenu.target.isDomainRoot"
        class="assoc-context-menu__item"
        @click="onAddAssociation"
      >
        <span>🔗</span> 添加关联
      </button>
      <button
        v-if="contextMenu.target && contextMenu.target.parentName"
        class="assoc-context-menu__item"
        @click="onOpenResourceNote"
      >
        <span>📄</span> 查看笔记
      </button>
      <div
        v-if="contextMenu.target && !contextMenu.target.resourceType"
        class="assoc-context-menu__divider"
      ></div>
      <button
        v-if="contextMenu.target && !contextMenu.target.resourceType"
        class="assoc-context-menu__item assoc-context-menu__item--danger"
        @click="onDeleteNode"
      >
        <span>🗑</span> {{ contextMenu.target.isDomainRoot ? '删除根域' : '删除节点' }}
      </button>
    </div>

    <!-- 手动添加关联对话框 -->
    <AddAssociationDialog
      v-model:visible="addDialogVisible"
      :source-node="addDialogSource"
      @saved="onDialogSaved"
    />
  </section>
</template>

<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import * as d3 from 'd3'
import { useAssociationsStore } from '@/stores/associations'
import { useGraphStore } from '@/stores/graph'
import type { Association, RelationType } from '@/api/associations'
import AddAssociationDialog from './AddAssociationDialog.vue'

const store = useAssociationsStore()
const graphStore = useGraphStore()

const canvasRef = ref<HTMLDivElement | null>(null)

const RESOURCE_ICON: Record<string, string> = {
  note: '📄',
  resource: '🔗',
  plan: '📋',
  quiz: '❓',
  gap: '⚠️',
}

/** 资源节点在画布上显示的中文类型标签（图例 / 节点文本）。
 *  资源节点的 id/label 不再复用父节点名，避免和主概念重复。 */
const RESOURCE_TYPE_LABEL: Record<string, string> = {
  note: '笔记',
  resource: '资料',
  plan: '计划',
}

const VISIBLE_RESOURCE_TYPES = new Set(['note', 'resource', 'plan'])

/** 构造资源节点在 nodes Map 里的 key。
 *  形如 ``📄 note:PR曲线与AP``；不同类型各自独立，因此同一父节点可以同时
 *  挂「笔记」「资料」「计划」三个子节点而互不覆盖。 */
function resourceNodeKey(type: string, parentName: string): string {
  return `${RESOURCE_ICON[type] ?? '📎'} ${type}:${parentName}`
}

// ── 关系类型 ──
const RELATION_GLYPH: Record<RelationType, string> = {
  part_of: '↘',
  prerequisite_of: '🟥',
  enables: '🟧',
  similar_to: '🟪',
  contrasts_with: '🟦',
  applies_to: '🟩',
  derived_from: '🟢',
  related_to: '⚪',
  has_note: '📄',
  has_resource: '🔗',
  has_plan: '📋',
  cites: '➡',
  references: '➡',
}

const RELATION_LABEL: Record<RelationType, string> = {
  part_of: '属于',
  prerequisite_of: '先学这个',
  enables: '学完能搞定',
  similar_to: '类似',
  contrasts_with: '对比',
  applies_to: '应用到',
  derived_from: '由此衍生',
  related_to: '相关',
  has_note: '有笔记',
  has_resource: '有资料',
  has_plan: '有计划',
  cites: '引用',
  references: '引用了',
}

// ── 颜色：硬编码解析后的 CSS 变量值，避免每次渲染都做 getComputedStyle ──
const COLOR = {
  tierL0: '#f59e0b',
  tierL1: '#4c7dff',
  tierL2: '#8b5cf6',
  tierL3: '#ec4899',
  tierLeaf: '#22d3a5',
  textPrimary: '#e6e9ef',
  textMuted: '#6b7180',
  textSecondary: '#9ca3b5',
  bgPrimary: '#0f1117',
  accentBlue: '#4c7dff',
  accentPurple: '#7c5cff',
  accentAmber: '#f59e0b',
  accentGreen: '#22d3a5',
  accentRed: '#ef4444',
  accentCyan: '#06b6d4',
  accentLime: '#84cc16',
  accentFuchsia: '#e879f9',
  accentTeal: '#14b8a6',
  accentSky: '#38bdf8',
  accentViolet: '#a78bfa',
} as const

// 附属边（has_note / has_resource / has_plan）的颜色与对应的详情节点颜色保持一致
const RELATION_COLOR: Record<RelationType, string> = {
  part_of: COLOR.textMuted,
  prerequisite_of: COLOR.accentRed,
  enables: COLOR.accentLime,
  similar_to: COLOR.accentPurple,
  contrasts_with: COLOR.accentFuchsia,
  applies_to: COLOR.accentGreen,
  derived_from: COLOR.accentTeal,
  related_to: COLOR.textSecondary,
  has_note: COLOR.accentBlue,
  has_resource: COLOR.accentCyan,
  has_plan: COLOR.accentAmber,
  cites: COLOR.accentSky,
  references: COLOR.accentViolet,
}

// 图例只列实际出现过的关系，按语义分组；其余 9 种关系的颜色映射保留在 RELATION_COLOR，
// 以后真正使用时会自动按颜色渲染，不会变成无色线。
const LEGEND_GROUPS: { title: string; items: { value: RelationType; label: string; color: string }[] }[] = [
  {
    title: '结构归属',
    items: [
      { value: 'part_of' as RelationType, label: '属于', color: RELATION_COLOR.part_of },
    ],
  },
  {
    title: '内容挂载',
    items: [
      { value: 'has_note' as RelationType, label: '有笔记', color: RELATION_COLOR.has_note },
      { value: 'has_resource' as RelationType, label: '有资料', color: RELATION_COLOR.has_resource },
      { value: 'has_plan' as RelationType, label: '有计划', color: RELATION_COLOR.has_plan },
    ],
  },
]

// 节点颜色图例：size 与图上真实半径一致，让用户能直观对应。
const NODE_LEGEND_GROUPS: { title: string; items: { label: string; color: string; size: number }[] }[] = [
  {
    title: '概念层级',
    items: [
      { label: 'L0 根领域', color: COLOR.tierL0, size: 18 },
      { label: 'L1 分类', color: COLOR.tierL1, size: 14 },
      { label: 'L2 子类', color: COLOR.tierL2, size: 12 },
      { label: 'L3 概念', color: COLOR.tierL3, size: 10 },
      { label: 'L4+ 叶子', color: COLOR.tierLeaf, size: 8 },
    ],
  },
  {
    title: '附属节点',
    items: [
      { label: '笔记', color: COLOR.accentBlue, size: 7 },
      { label: '资料', color: COLOR.accentCyan, size: 7 },
      { label: '计划', color: COLOR.accentAmber, size: 7 },
    ],
  },
]

function relationLabel(r: RelationType): string {
  return RELATION_LABEL[r] || r
}

function truncate(s: string, n: number): string {
  return s.length > n ? s.slice(0, n) + '…' : s
}

function formatSyncTime(iso: string | null | undefined): string {
  if (!iso) return '从未'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return '未知'
  const diff = Date.now() - d.getTime()
  const min = Math.floor(diff / 60000)
  if (min < 1) return '刚刚'
  if (min < 60) return `${min} 分钟前`
  const h = Math.floor(min / 60)
  if (h < 24) return `${h} 小时前`
  const day = Math.floor(h / 24)
  if (day < 30) return `${day} 天前`
  return d.toLocaleDateString('zh-CN')
}

function truncateId(id: string): string {
  const idx = id.indexOf(':')
  return idx >= 0 ? id.slice(idx + 1) : id
}

function tierClass(level: number): string {
  if (level === 0) return 'l0'
  if (level === 1) return 'l1'
  if (level === 2) return 'l2'
  if (level === 3) return 'l3'
  return 'leaf'
}

function tierLabel(level: number): string {
  const t = tierClass(level)
  if (t === 'l0') return 'L0 根'
  if (t === 'l1') return 'L1 主干'
  if (t === 'l2') return 'L2'
  if (t === 'l3') return 'L3'
  return '叶子'
}

function tierColorHex(level: number): string {
  const t = tierClass(level)
  if (t === 'l0') return COLOR.tierL0
  if (t === 'l1') return COLOR.tierL1
  if (t === 'l2') return COLOR.tierL2
  if (t === 'l3') return COLOR.tierL3
  return COLOR.tierLeaf
}

// ── 计算属性 ──

const focusConcept = computed(() =>
  store.focusNode ? store.graph?.concepts[store.focusNode] ?? null : null,
)

const focusDotColor = computed(() =>
  focusConcept.value ? tierColorHex(focusConcept.value.level) : COLOR.accentBlue,
)

const relateGroups = computed(() => {
  if (!store.focusNode) return []
  const node = store.focusNode
  const all = store.associationList

  const out = all.filter((a) => truncateId(a.source) === node)
  const inc = all.filter((a) => truncateId(a.target) === node)

  type Group = { relation: RelationType; items: { name: string; edge: Association }[] }
  const byRel = new Map<RelationType, Group>()

  function add(rel: RelationType, edge: Association, name: string) {
    let g = byRel.get(rel)
    if (!g) {
      g = { relation: rel, items: [] }
      byRel.set(rel, g)
    }
    if (!g.items.find((x) => x.name === name)) g.items.push({ name, edge })
  }

  for (const a of out) add(a.relation, a, truncateId(a.target))
  for (const a of inc) add(a.relation, a, truncateId(a.source))

  const groups = Array.from(byRel.values())
  groups.sort((a, b) => {
    const aS = a.relation.startsWith('has_') ? 1 : 0
    const bS = b.relation.startsWith('has_') ? 1 : 0
    return aS - bS
  })
  return groups
})

const learningTips = computed<string[]>(() => {
  if (!store.focusNode) return []
  const node = store.focusNode
  const tips: string[] = []
  const inc = store.associationList.filter((a) => truncateId(a.target) === node)
  const out = store.associationList.filter((a) => truncateId(a.source) === node)

  const prereq = inc.filter((a) => a.relation === 'prerequisite_of')
  const enables = out.filter((a) => a.relation === 'enables')
  const similar = inc.concat(out).filter((a) => a.relation === 'similar_to')

  if (prereq.length > 0) {
    tips.push(`先了解前置概念：${prereq.map((e) => truncateId(e.source)).join('、')}，会更容易理解。`)
  }
  if (enables.length > 0) {
    tips.push(`学完这个之后，可以去看：${enables.map((e) => truncateId(e.target)).join('、')}。`)
  }
  if (similar.length > 0) {
    const names = similar.map((e) =>
      truncateId(e.source) === node ? truncateId(e.target) : truncateId(e.source),
    )
    tips.push(`类似主题：${names.join('、')} —— 对照看能加深理解。`)
  }
  if (tips.length === 0 && store.focusNodeAssociations.resources.length === 0) {
    tips.push('尚未发现关联 —— 在笔记里写内容，或引用其他主题，再点「重新梳理」。')
  }
  return tips
})

// ── D3 力导向图 ──────────────────────────────────

interface GNode extends d3.SimulationNodeDatum {
  id: string
  label: string
  level: number
  color: string
  radius: number
  /** 资源节点（笔记/资料/计划）所属的概念节点名。概念节点没有此字段。 */
  parentName?: string
  /** 资源节点的类型；概念节点没有此字段。 */
  resourceType?: 'note' | 'resource' | 'plan'
}

interface GLink extends d3.SimulationLinkDatum<GNode> {
  relation: RelationType
  color: string
  weight: number
}

let svgEl: SVGSVGElement | null = null
let gRoot: SVGGElement | null = null
let simulation: d3.Simulation<GNode, GLink> | null = null
let currentNodes: GNode[] = []
let resizeObserver: ResizeObserver | null = null
let rafPending = false
let zoomBehavior: d3.ZoomBehavior<SVGSVGElement, unknown> | null = null
/** 当前缩放比例（用于指示器）。由 zoom 的 on('zoom') 更新。 */
const zoomLevel = ref(1)
/** 缩放百分比显示。 */
const zoomPercent = computed(() => Math.round(zoomLevel.value * 100))
/** 标记是否已经做过一次自动 fit，避免每次 data 变动都重新 fit 把用户手动调整的视图覆盖掉。 */
let hasAutoFit = false

/** 钻取栈：每点击一个节点就 push 一层（最后一个是当前 root）。空数组 = 全局视图。 */
const drillStack = ref<string[]>([])
/** 当前 root（即 drillStack 末尾）。null = 全局视图。 */
const currentRoot = computed(() =>
  drillStack.value.length > 0 ? drillStack.value[drillStack.value.length - 1] : null,
)
/** 是否处于钻取模式。 */
const isDrilled = computed(() => drillStack.value.length > 0)
/** drillStack 的可点击副本（用于面包屑导航）。 */
const drillBreadcrumb = computed(() => drillStack.value.slice())

/** 当前 root 的关联数（含出入双向），用于面包屑右侧的元数据。 */
const currentRootEdgeCount = computed(() => {
  const r = currentRoot.value
  if (!r) return 0
  return store.associationList.filter(
    (a) => truncateId(a.source) === r || truncateId(a.target) === r,
  ).length
})

/** 当前视图是否会有节点被绘制。空数据时不创建 SVG，避免无意义的空容器。 */
const hasVisibleNodes = computed(() => {
  if (!store.graph) return false
  const root = currentRoot.value
  if (root) {
    // drill 模式：root 必然存在；如果至少有一条关联，就有邻居
    return store.associationList.some(
      (a) => truncateId(a.source) === root || truncateId(a.target) === root,
    )
  }
  // 全局：有 concept 就显示（level 过滤在 buildGraphData 里做）
  return store.conceptList.length > 0
})

/** drill 模式下，单个根节点对应的可见节点数上限。
 *  与全局视图保持一致：≤ 该值就继续向更深的层级展开，超过即停。
 */
const DRILL_NODE_BUDGET = 100

/** 把一张图对应的 node/link 数组构建出来。
 *  - drill 模式：BFS 从 root 逐层展开邻居，每加完一层检查总量；> 100 停止
 *  - 全局模式：若节点 > 100，按层级收敛（L2→L1→L0）保证 ≤ 100 个
 */
function buildGraphData(): { nodes: GNode[]; links: GLink[] } {
  if (!store.graph) return { nodes: [], links: [] }
  const allConcepts = store.conceptList
  const allResources = store.resourceList
  const allAssociations = store.associationList
  const root = currentRoot.value

  // 建立 id → parent name 的查找表。资源 ID 三种格式，truncateId 只能处理前两种：
  //   note:ParentName           → truncateId → "ParentName"
  //   plan:ParentName:PLANID    → truncateId → "ParentName:PLANID"（需再 split）
  //   resource:HASH             → truncateId → "HASH"（不在概念集合里，查不出父节点）
  // 用资源条目自带的 r.node 字段统一映射，避免硬编码 ID 格式。
  const idToParent = new Map<string, string>()
  for (const r of allResources) {
    if (r.id) idToParent.set(r.id, r.node)
  }

  // ── 计算当前视图要展示哪些节点 ──
  let visibleNames: Set<string>

  if (root) {
    // drill：先把邻接表建出来，BFS 逐层加入；某层加完总量 > 100 时停止
    const adj = new Map<string, Set<string>>()
    const addEdge = (a: string, b: string) => {
      if (!adj.has(a)) adj.set(a, new Set())
      adj.get(a)!.add(b)
    }
    for (const a of allAssociations) {
      const from = idToParent.get(a.source) ?? truncateId(a.source)
      const to = idToParent.get(a.target) ?? truncateId(a.target)
      if (from === to) continue
      addEdge(from, to)
      addEdge(to, from)
    }

    visibleNames = new Set([root])
    let frontier: Set<string> = new Set([root])

    while (frontier.size > 0) {
      const next = new Set<string>()
      for (const node of frontier) {
        for (const neighbor of adj.get(node) ?? []) {
          if (!visibleNames.has(neighbor)) next.add(neighbor)
        }
      }
      if (next.size === 0) break
      // 整层加进去：若会越过预算就停在当前层；否则全收
      if (visibleNames.size + next.size > DRILL_NODE_BUDGET) break
      for (const n of next) visibleNames.add(n)
      frontier = next
    }
  } else {
    // 全局：L0 (domain root) 永远展示，预算 100 只算非 L0 节点
    const l0Names = allConcepts.filter((c) => c.level === 0).map((c) => c.name)
    const nonRoot = allConcepts.filter((c) => c.level > 0)

    if (nonRoot.length <= 100) {
      // 非 root ≤ 100：全展示（含 L0）
      visibleNames = new Set(allConcepts.map((c) => c.name))
    } else {
      // 从 L2 起往上抬，找到首个 ≤ 100 的层级上限；L0 始终并入结果
      let chosen: Set<string> | null = null
      for (const maxLevel of [2, 1]) {
        const atLevel = nonRoot.filter((c) => c.level <= maxLevel)
        if (atLevel.length <= 100) {
          chosen = new Set([...l0Names, ...atLevel.map((c) => c.name)])
          break
        }
      }
      // 兜底：连 L1 都 > 100，只显示 L0（用 drill-down 看细节）
      visibleNames = chosen ?? new Set(l0Names)
    }
  }

  // ── 构建节点 ──
  const nodes = new Map<string, GNode>()
  for (const c of allConcepts) {
    if (!visibleNames.has(c.name)) continue
    nodes.set(c.name, {
      id: c.name,
      label: c.name,
      level: c.level,
      color: tierColorHex(c.level),
      radius: (c.is_root || c.level === 0) ? 18 : 10 + Math.min(c.level, 4) * 2,
    })
  }
  for (const r of allResources) {
    if (!VISIBLE_RESOURCE_TYPES.has(r.type)) continue
    if (!visibleNames.has(r.node)) continue
    const typeLabel = RESOURCE_TYPE_LABEL[r.type] ?? r.type
    const icon = RESOURCE_ICON[r.type] ?? '📎'
    const key = resourceNodeKey(r.type, r.node)
    if (!nodes.has(key)) {
      const color =
        r.type === 'note'
          ? COLOR.accentBlue
          : r.type === 'resource'
          ? COLOR.accentCyan
          : COLOR.accentAmber
      nodes.set(key, {
        id: key,
        label: `${icon} ${typeLabel}`,
        level: 99,
        color,
        radius: 7,
        parentName: r.node,
        resourceType: r.type as 'note' | 'resource' | 'plan',
      })
    }
  }

  // ── 构建边：两端都在可见集里的才保留 ──
  const links: GLink[] = []
  /** 解析 association 端点：先查 idToParent 表，匹配不上再回退 truncateId。 */
  const endpointName = (id: string): string | null => {
    const parent = idToParent.get(id)
    if (parent) return parent
    return truncateId(id) || null
  }
  for (const a of allAssociations) {
    const from = endpointName(a.source)
    const to = endpointName(a.target)
    if (!from || !to) continue
    if (!visibleNames.has(from) || !visibleNames.has(to)) continue
    let sn = nodes.get(from)
    let tn = nodes.get(to)
    if (a.relation === 'has_note') tn = nodes.get(resourceNodeKey('note', to))
    if (a.relation === 'has_resource') tn = nodes.get(resourceNodeKey('resource', to))
    if (a.relation === 'has_plan') tn = nodes.get(resourceNodeKey('plan', to))
    if (a.relation === 'references') sn = nodes.get(resourceNodeKey('note', from))
    if (sn && tn && sn !== tn) {
      links.push({
        source: sn,
        target: tn,
        relation: a.relation,
        color: RELATION_COLOR[a.relation] || COLOR.textSecondary,
        weight: a.weight,
      })
    }
  }
  return { nodes: Array.from(nodes.values()), links }
}

/** 销毁旧的 SVG / simulation，准备全新渲染。只删 SVG 节点，不动兄弟节点（浮动控件在外面）。 */
function clearCanvas() {
  if (simulation) {
    simulation.stop()
    simulation.on('tick', null)
    simulation.on('end', null)
    simulation = null
  }
  if (canvasRef.value) {
    const oldSvg = canvasRef.value.querySelector('svg.assoc-graph-svg')
    if (oldSvg) oldSvg.remove()
  }
  svgEl = null
  gRoot = null
  zoomBehavior = null
  currentNodes = []
  hasAutoFit = false
}

function renderGraph() {
  const el = canvasRef.value
  if (!el) return
  // 容器必须真的有尺寸，否则等下一次 ResizeObserver 触发
  const rect = el.getBoundingClientRect()
  const width = rect.width
  const height = rect.height
  if (width < 10 || height < 10) return

  clearCanvas()

  const { nodes, links } = buildGraphData()

  // SVG 用 viewBox + 100% 尺寸，自身永远贴合容器；避免再手算 width/height
  const svg = d3
    .select(el)
    .append('svg')
    .attr('class', 'assoc-graph-svg')
    .attr('width', '100%')
    .attr('height', '100%')
    .attr('viewBox', `0 0 ${width} ${height}`)
  svgEl = svg.node() as SVGSVGElement | null
  const g = svg.append('g').attr('class', 'assoc-graph-root')
  gRoot = g.node() as SVGGElement | null

  // zoom/persist：缩放和平移都直接走 d3.zoom，scaleExtent 给得宽一些保证能缩到全局适配
  zoomBehavior = d3
    .zoom<SVGSVGElement, unknown>()
    .scaleExtent([0.05, 5])
    .on('zoom', (e) => {
      if (!gRoot) return
      const t = e.transform
      if (Number.isNaN(t.x) || Number.isNaN(t.y) || Number.isNaN(t.k)) return
      g.attr('transform', t.toString())
      zoomLevel.value = t.k
    })
  svg.call(zoomBehavior as any)
  // 重置缩放比例记录
  zoomLevel.value = 1

  // SVG 背景右键 fallback 改到 onMounted 里挂在 canvasRef 上（见下方）。

  if (!nodes.length) {
    svg
      .append('text')
      .attr('x', width / 2)
      .attr('y', height / 2)
      .attr('text-anchor', 'middle')
      .attr('fill', COLOR.textMuted)
      .attr('font-size', 14)
      .text('尚无数据 —— 先添加节点，再点「重新梳理」')
    return
  }

  currentNodes = nodes

  simulation = d3
    .forceSimulation<GNode, GLink>(nodes)
    .force(
      'link',
      d3
        .forceLink<GNode, GLink>(links)
        .id((d) => d.id)
        .distance(80)
        .strength(0.6),
    )
    .force('charge', d3.forceManyBody<GNode>().strength(-220))
    .force('center', d3.forceCenter(width / 2, height / 2))
    .force('collision', d3.forceCollide<GNode>().radius((d) => d.radius + 6))
    .alphaDecay(0.05)
    .velocityDecay(0.4)

  // 所有可视内容必须挂到 gRoot（不是 svg），否则 getBBox 永远是 0，
  // fitToView 不生效；并且 zoom transform 应用到 gRoot，挂在它外面的内容不会跟着缩放。
  const link = g
    .append('g')
    .attr('class', 'assoc-links')
    .selectAll<SVGLineElement, GLink>('line')
    .data(links)
    .enter()
    .append('line')
    .attr('stroke', (d) => d.color)
    .attr('stroke-width', (d) => Math.max(1, d.weight * 2.5))
    .attr('opacity', 0.6)

  const linkLabel = g
    .append('g')
    .attr('class', 'assoc-link-labels')
    .selectAll<SVGTextElement, GLink>('text')
    .data(links)
    .enter()
    .append('text')
    .attr('font-size', 10)
    .attr('fill', (d) => d.color)
    .attr('text-anchor', 'middle')
    .attr('opacity', 0.75)
    .text((d) => RELATION_GLYPH[d.relation as RelationType] || '·')

  const node = g
    .append('g')
    .attr('class', 'assoc-nodes')
    .selectAll<SVGCircleElement, GNode>('circle')
    .data(nodes)
    .enter()
    .append('circle')
    .attr('r', (d) => d.radius)
    .attr('fill', (d) => d.color)
    .attr('stroke-width', 1.5)
    .attr('stroke', COLOR.bgPrimary)
    .style('cursor', 'pointer')
    .on('click', (_e, d) => {
      // 资源节点钻取到所属概念（按 label 钻取只对概念节点有意义）；
      // 资源节点的 label 是「📄 笔记」这种类型字符串，不能直接 drillTo
      drillTo(d.parentName ?? d.label)
    })
    .on('dblclick', (_e, d) => {
      if (d.resourceType === 'note' && d.parentName) {
        graphStore.openNotePanel(d.parentName, 'note')
      } else if (d.resourceType === 'resource' && d.parentName) {
        graphStore.openNotePanel(d.parentName, 'resource')
      } else if (d.resourceType === 'plan' && d.parentName) {
        graphStore.openNotePanel(d.parentName, 'plan')
      }
    })
    .on('contextmenu', (e: MouseEvent) => {
      // 用原生 listener 在 svgEl 上统一处理；这里只需阻止浏览器默认菜单
      e.preventDefault()
    })
    .call(
      d3
        .drag<SVGCircleElement, GNode>()
        .on('start', (e, d) => {
          if (!e.active && simulation) simulation.alphaTarget(0.3).restart()
          d.fx = d.x
          d.fy = d.y
        })
        .on('drag', (e, d) => {
          d.fx = e.x
          d.fy = e.y
        })
        .on('end', (e, d) => {
          if (!e.active && simulation) simulation.alphaTarget(0)
          d.fx = null
          d.fy = null
        }),
    )

  const label = g
    .append('g')
    .attr('class', 'assoc-labels')
    .selectAll<SVGTextElement, GNode>('text')
    .data(nodes)
    .enter()
    .append('text')
    .attr('text-anchor', 'middle')
    .attr('dy', (d) => -d.radius - 4)
    .attr('font-size', (d) => (d.radius > 16 ? 13 : 11))
    .attr('font-weight', (d) => (d.radius > 16 ? 'bold' : 'normal'))
    .attr('fill', COLOR.textPrimary)
    .text((d) => d.label)

  // 描边高亮（focus node）单独维护，避免每帧改 stroke
  applyFocusHighlight()

  // tick 用 rAF 合批，减少每帧的 attribute 写入
  simulation.on('tick', () => {
    if (rafPending) return
    rafPending = true
    requestAnimationFrame(() => {
      rafPending = false
      link
        .attr('x1', (d) => (d.source as GNode).x ?? 0)
        .attr('y1', (d) => (d.source as GNode).y ?? 0)
        .attr('x2', (d) => (d.target as GNode).x ?? 0)
        .attr('y2', (d) => (d.target as GNode).y ?? 0)
      linkLabel
        .attr('x', (d) => (((d.source as GNode).x ?? 0) + ((d.target as GNode).x ?? 0)) / 2)
        .attr('y', (d) => (((d.source as GNode).y ?? 0) + ((d.target as GNode).y ?? 0)) / 2)
      node
        .attr('cx', (d) => d.x ?? 0)
        .attr('cy', (d) => d.y ?? 0)
      label
        .attr('x', (d) => d.x ?? 0)
        .attr('y', (d) => d.y ?? 0)
    })
  })

  // 模拟稳定后自动适配（仅首次渲染 / 数据结构变了才做，后续用户操作不要覆盖）
  simulation.on('end', () => {
    if (!hasAutoFit) {
      hasAutoFit = true
      requestAnimationFrame(() => fitToView())
    }
  })
}

function applyFocusHighlight() {
  if (!svgEl) return
  const focus = store.focusNode
  const svg = d3.select(svgEl)
  svg
    .select<SVGCircleElement>('g.assoc-nodes')
    .selectAll<SVGCircleElement, GNode>('circle')
    .attr('stroke', (d) =>
      d.label === focus ? COLOR.accentBlue : COLOR.bgPrimary,
    )
    .attr('stroke-width', (d) => (d.label === focus ? 3 : 1.5))
}

function updateViewBox() {
  if (!svgEl) return
  const el = canvasRef.value
  if (!el) return
  const rect = el.getBoundingClientRect()
  const width = rect.width
  const height = rect.height
  if (width < 10 || height < 10) return
  d3.select(svgEl).attr('viewBox', `0 0 ${width} ${height}`)
  // 容器尺寸变化只改 viewBox，不重启 simulation、不重置用户的缩放
}

/** 计算所有节点的 bbox 并应用 zoom transform，让所有节点完整可见（不漏节点）。 */
function fitToView(padding = 60, duration = 400) {
  if (!svgEl || !gRoot || !zoomBehavior) return
  const el = canvasRef.value
  if (!el) return
  const rect = el.getBoundingClientRect()
  if (rect.width === 0 || rect.height === 0) return

  try {
    // 用 linkLabel 节点集合也行，但 gRoot 包含全部节点 + 边标签，最准确
    const bbox = (gRoot as SVGGElement).getBBox()
    if (!Number.isFinite(bbox.width) || !Number.isFinite(bbox.height)) return
    if (bbox.width === 0 || bbox.height === 0) return

    // 同时考虑节点的半径（bbox 不包含 stroke / radius），加一圈 padding
    const paddedW = bbox.width + padding * 2
    const paddedH = bbox.height + padding * 2
    const scale = Math.min(
      rect.width / paddedW,
      rect.height / paddedH,
      5,
    )
    if (!Number.isFinite(scale) || scale <= 0) return

    // 让 bbox 在容器内居中
    const tx = (rect.width - bbox.width * scale) / 2 - bbox.x * scale
    const ty = (rect.height - bbox.height * scale) / 2 - bbox.y * scale
    if (Number.isNaN(tx) || Number.isNaN(ty)) return

    const sel = d3.select(svgEl)
    sel
      .transition()
      .duration(duration)
      .call(zoomBehavior.transform, d3.zoomIdentity.translate(tx, ty).scale(scale))
  } catch {
    /* bbox not yet available */
  }
}

/** 缩放级别调整（按钮触发）。 */
function zoomBy(factor: number) {
  if (!svgEl || !zoomBehavior) return
  d3.select(svgEl)
    .transition()
    .duration(180)
    .call(zoomBehavior.scaleBy, factor)
}

/** 回到初始视图：缩放 100%，平移归零（保留 simulation 中已稳定的节点位置）。 */
function resetView() {
  if (!svgEl || !zoomBehavior) return
  d3.select(svgEl)
    .transition()
    .duration(300)
    .call(zoomBehavior.transform, d3.zoomIdentity)
}

/** 钻取到指定节点：把节点推入栈底，作为新的 parent/root。如果已经是当前 root，不重复 push。 */
function drillTo(label: string) {
  if (currentRoot.value === label) return
  drillStack.value = [...drillStack.value, label]
  store.setFocus(label)
}

/** 回到上一级：弹出栈顶。若已无栈，回到全局视图（focusNode 清空）。 */
function drillUp() {
  if (drillStack.value.length === 0) return
  const next = drillStack.value.slice(0, -1)
  drillStack.value = next
  const newRoot = next.length > 0 ? next[next.length - 1] : null
  store.setFocus(newRoot)
}

/** 直接跳到栈中指定层级（用于面包屑点击）。0 = 全局，1 = drillStack[0]，… */
function drillToLevel(level: number) {
  if (level <= 0) {
    drillReset()
    return
  }
  const target = drillStack.value[level - 1]
  if (!target) return
  drillStack.value = drillStack.value.slice(0, level)
  store.setFocus(target)
}

/** 一键回到全局视图（清空栈 + 取消 focus）。 */
function drillReset() {
  drillStack.value = []
  store.setFocus(null)
}

// ── Watchers ──────────────────────────────────────
// 只监听真正影响图谱结构的数据；focusNode 只改描边；drillStack 切换 view
watch(
  () => [
    store.graph?.domain,
    store.associationList.length,
    store.conceptList.length,
    drillStack.value.length,
    drillStack.value[drillStack.value.length - 1],
  ] as const,
  () => {
    nextTick(() => {
      // 主动清旧画布并尝试渲染。若容器当前隐藏（v-show=false，width=0），
      // renderGraph 会提前 return，但 svgEl 已经被置 null；下次视图变为可见时，
      // ResizeObserver 会触发 renderGraph 兜底分支用新数据重建图。
      // 否则旧 svg 还在，下次视图恢复时 ResizeObserver 走 updateViewBox 分支，
      // 不会重建图，导致切换领域后图谱停留在旧领域的数据。
      clearCanvas()
      renderGraph()
    })
  },
)

watch(
  () => store.focusNode,
  () => applyFocusHighlight(),
)

watch(() => graphStore.activeDomain, async (d) => {
  if (d) {
    // Drop the previous domain's drill path so a name collision doesn't
    // keep the stale view alive after switching. Without this, drilling
    // into "系统设计与架构" under "AI 应用开发" then switching to "DeepSeek
    // Harness" leaves the breadcrumb reading
    //   � 全部节点 › 系统设计与架构
    // even though the new domain has nothing to do with that node.
    drillReset()
    await store.load(d, true)
  }
})

onMounted(async () => {
  if (graphStore.activeDomain) await store.load(graphStore.activeDomain, true)
  // 用 ResizeObserver 替代 window resize：容器尺寸真变化时要么重算 viewBox，要么在 svg 还没建出来时补一次 renderGraph。
  // 初次挂载 v-show=false 导致 width=0，renderGraph 提前 return；v-show 切到 true 后回调会被再次触发，此时 svgEl 还是 null，需要在这里兜底。
  if (canvasRef.value) {
    nextTick(() => {
      renderGraph()
      resizeObserver = new ResizeObserver(() => {
        if (!svgEl) renderGraph()
        else updateViewBox()
      })
      resizeObserver.observe(canvasRef.value!)
    })
  }

  // 右键菜单：挂在持久容器 div 上（不被 d3 重渲染影响）+ capture phase
  // 命中节点：取 SVGElement.__data__（d3 把 datum 存在这里）；命中背景：基于 focusNode
  canvasRef.value?.addEventListener(
    'contextmenu',
    (e: MouseEvent) => {
      e.preventDefault()
      // console.debug('[AssocView] captured contextmenu, target=', e.target)
      const t = e.target as Element | null
      // 节点圆 / 节点 label 都在 g.assoc-nodes 下
      const assocGroup = t?.closest('g.assoc-nodes')
      if (assocGroup) {
        const datumEl = (t as any).__data__ as GNode | undefined
        if (datumEl) {
          const target: ContextTarget = {
            id: datumEl.id,
            label: datumEl.label,
            level: datumEl.level,
            parentName: datumEl.parentName,
            resourceType: datumEl.resourceType,
            // 用 concept.is_root + name 双重判断，避免孤儿概念(level=0)被误当成领域根
            isDomainRoot: isDomainRootNode(datumEl.label),
          }
          openContextMenu(target, e.clientX, e.clientY)
          return
        }
      }
      // 命中背景：基于当前 focusNode 弹菜单
      const focus = store.focusNode
      if (!focus) return
      const concept = store.conceptList.find((c) => c.name === focus)
      if (!concept) return
      const target: ContextTarget = {
        id: `concept:${focus}`,
        label: focus,
        level: concept.level,
        isDomainRoot: isDomainRootNode(focus),
      }
      openContextMenu(target, e.clientX, e.clientY)
    },
    { capture: true },
  )
})

onUnmounted(() => {
  if (simulation) simulation.stop()
  if (resizeObserver) {
    resizeObserver.disconnect()
    resizeObserver = null
  }
  // 容器 div 上的 contextmenu 是 capture-phase 永久 listener，组件销毁时一并移除
  // （Vue 卸载时会自动移除通过模板 ref 绑定的 element 上的 addEventListener 注册的 listener吗？不会；
  // 需要手动清理，否则下一次挂载会出现重复 handler。）
  // 这里无 ref 持有，给个 best-effort：把 listener 替换成 noop 不太可行；简单做法是依赖 GC 回收 DOM。
  clearCanvas()
})

// ── 用户操作 ──────────────────────────────────────

async function onReshuffle() {
  await store.syncFull()
  ElMessage.success('梳理完成')
}

async function onThinkMore() {
  const res = await store.flushLLM()
  if (res) {
    ElMessage.success(`已新增 ${res.added ?? 0} 条关联`)
    await store.reload()
  }
}

async function onClear() {
  await store.clear()
  ElMessage.success('已清空')
}

// ── 右键菜单 / 手动操作 ──────────────────────────────

/** 当前右键菜单的目标节点（来自 d3 force 布局的 GNode）。
 *  资源节点有 resourceType；根节点有 isDomainRoot；普通概念节点没有这俩字段。 */
interface ContextTarget {
  id: string
  label: string
  level: number
  isDomainRoot?: boolean
  parentName?: string
  resourceType?: 'note' | 'resource' | 'plan'
}

// 整张菜单用单一 ref，target 也要响应式（不然 v-if 看不到）
const contextMenu = ref<{
  visible: boolean
  x: number
  y: number
  target: ContextTarget | null
}>({ visible: false, x: 0, y: 0, target: null })

const addDialogVisible = ref(false)
const addDialogSource = ref<ConceptNode | null>(null)

/** 判断节点是否是真正的领域根节点（不可删除、不可作为关联的源）。
 *  只用「名字 === 领域名」最严格：ConceptNode.is_root 会被 Pydantic 自动同步为
 *  level==0，而孤儿概念也是 level==0，会被误判；只有与领域同名的才是真正根。 */
function isDomainRootNode(name: string): boolean {
  return !!graphStore.activeDomain && name === graphStore.activeDomain
}

function openContextMenu(d: ContextTarget, x: number, y: number) {
  contextMenu.value = { visible: true, x, y, target: d }
  nextTick(() => {
    const menuWidth = 200
    const menuHeight = 220
    if (x + menuWidth > window.innerWidth)
      contextMenu.value.x = window.innerWidth - menuWidth - 8
    if (y + menuHeight > window.innerHeight)
      contextMenu.value.y = window.innerHeight - menuHeight - 8
  })
}

function hideContextMenu() {
  contextMenu.value.visible = false
  contextMenu.value.target = null
}

function onDocumentClick() {
  if (contextMenu.value.visible) hideContextMenu()
}
onMounted(() => document.addEventListener('click', onDocumentClick))
onUnmounted(() => document.removeEventListener('click', onDocumentClick))

function onViewNode() {
  const t = contextMenu.value.target
  hideContextMenu()
  if (!t) return
  if (t.resourceType) return
  store.setFocus(t.label)
  drillTo(t.label)
}

function onAddAssociation() {
  const t = contextMenu.value.target
  hideContextMenu()
  if (!t || t.resourceType || t.isDomainRoot) return
  const concept = store.conceptList.find((c) => c.name === t.label)
  if (!concept) {
    ElMessage.error('找不到该节点的概念信息')
    return
  }
  addDialogSource.value = concept
  addDialogVisible.value = true
}

function onOpenResourceNote() {
  const t = contextMenu.value.target
  hideContextMenu()
  if (!t || !t.parentName) return
  graphStore.openNotePanel(t.parentName, t.resourceType === 'resource' ? 'resource' : t.resourceType === 'plan' ? 'plan' : 'note')
}

async function onDeleteNode() {
  const t = contextMenu.value.target
  hideContextMenu()
  if (!t || t.resourceType) return
  const name = t.label
  const isRoot = t.isDomainRoot
  try {
    await ElMessageBox.confirm(
      isRoot
        ? '这是领域根节点 —— 删除后知识图谱将没有根，但领域本身仍然存在。此操作不可撤销。'
        : '此操作会同时清理该节点的所有关联边，且不可撤销。',
      `删除${isRoot ? '根域' : '节点'}「${name}」？`,
      {
        confirmButtonText: '确定删除',
        cancelButtonText: '取消',
        type: 'warning',
      },
    )
    await store.deleteConcept(name)
    ElMessage.success(`已删除「${name}」`)
    if (store.focusNode === name) store.setFocus(null)
    if (isRoot) {
      // 根域删了，领域本身还在；reload 让视图刷新
      await store.reload()
    }
  } catch (e: any) {
    if (e !== 'cancel' && e?.message) {
      ElMessage.error(e.message)
    }
  }
}

function onDialogSaved() {
  // store 已经在 action 内 reload；这里只是占位以便外部埋点
}
</script>

<style scoped>
/* ============================================================
   Pane
   ============================================================ */
.assoc-pane {
  flex: 1;
  display: flex;
  flex-direction: column;
  background: var(--bg-primary);
  overflow: hidden;
}

/* ============================================================
   Hero / Stats
   ============================================================ */
.assoc-hero {
  display: flex;
  align-items: stretch;
  gap: 0;
  padding: 0;
  background: var(--bg-secondary);
  border-bottom: 1px solid var(--border-color);
  flex-shrink: 0;
}
.assoc-hero__main {
  flex: 1;
  padding: 10px 18px 10px 18px;
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
  border-right: 1px solid var(--border-color);
}
.assoc-hero__title {
  display: flex;
  align-items: baseline;
  gap: 8px;
  flex-wrap: wrap;
}
.assoc-hero__icon {
  font-size: 18px;
  line-height: 1;
}
.assoc-hero__title h2 {
  margin: 0;
  font-size: 16px;
  font-weight: 700;
  color: var(--text-primary);
  letter-spacing: 0.2px;
}
.assoc-hero__sub {
  font-size: 12px;
  color: var(--text-muted);
  font-weight: 400;
}

.assoc-hero__stats {
  display: flex;
  align-items: stretch;
  flex-shrink: 0;
}
.assoc-hero__stats .stat {
  min-width: 84px;
  padding: 10px 16px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 2px;
  border-right: 1px solid var(--border-color);
  background: var(--bg-secondary);
  transition: background 0.15s ease;
}
.assoc-hero__stats .stat:last-child {
  border-right: none;
}
.assoc-hero__stats .stat:hover {
  background: var(--bg-tertiary);
}
.stat__value {
  font-size: 20px;
  font-weight: 700;
  color: var(--text-primary);
  font-feature-settings: 'tnum';
  line-height: 1.1;
}
.stat__label {
  font-size: 11px;
  color: var(--text-muted);
  letter-spacing: 0.5px;
}
.stat--accent .stat__value {
  color: var(--accent-blue);
}
.stat--accent .stat__label {
  color: var(--accent-blue);
  opacity: 0.7;
}

/* ============================================================
   Toolbar
   ============================================================ */
.assoc-toolbar {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 12px;
  padding: 8px 16px;
  background: var(--bg-secondary);
  border-bottom: 1px solid var(--border-color);
  flex-shrink: 0;
}
.assoc-toolbar__left {
  display: flex;
  align-items: center;
  gap: 10px;
}
.assoc-toolbar__title {
  font-weight: 600;
  font-size: 14px;
  color: var(--text-primary);
}
.assoc-toolbar__right {
  display: flex;
  gap: 6px;
}

/* ============================================================
   Body layout
   ============================================================ */
.assoc-body {
  flex: 1;
  display: flex;
  min-height: 0;
  overflow: hidden;
}

.assoc-main {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-width: 0;
  background: var(--bg-primary);
  padding: 10px;
  gap: 6px;
}

/* ============================================================
   Graph
   ============================================================ */
.graph-canvas-wrapper {
  flex: 1;
  min-height: 0;
  position: relative;
  display: flex;
}
.graph-canvas {
  flex: 1;
  min-height: 0;
  width: 100%;
  background: var(--bg-secondary);
  border: 1px solid var(--border-color);
  border-radius: var(--radius-md);
  overflow: hidden;
  position: relative;
}
.graph-canvas :deep(.assoc-graph-svg) {
  display: block;
  cursor: grab;
  overflow: hidden;
}
.graph-canvas :deep(.assoc-graph-svg):active {
  cursor: grabbing;
}

/* 浮动缩放控件（右上角，绝对定位到 wrapper 上；用唯一前缀避免和 global.css 里的同名类冲突） */
.assoc-canvas-controls {
  position: absolute;
  top: 10px;
  right: 10px;
  display: flex;
  flex-direction: column;
  gap: 2px;
  background: var(--bg-tertiary);
  border: 1px solid var(--border-color);
  border-radius: var(--radius-sm);
  padding: 3px;
  z-index: 5;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.25);
}
.assoc-ctrl-btn {
  width: 22px;
  height: 22px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: transparent;
  border: none;
  border-radius: 3px;
  color: var(--text-secondary);
  cursor: pointer;
  font-size: 13px;
  line-height: 1;
  transition: background 0.12s ease, color 0.12s ease;
}
.assoc-ctrl-btn:hover {
  background: var(--bg-hover);
  color: var(--text-primary);
}
.assoc-ctrl-btn:active {
  background: var(--accent-blue-soft);
}
.assoc-ctrl-btn--primary {
  background: var(--accent-blue-soft);
  color: var(--accent-blue);
}
.assoc-ctrl-btn--primary:hover {
  background: var(--accent-blue-tint);
}
.assoc-ctrl-divider {
  height: 1px;
  background: var(--border-color);
  margin: 2px 3px;
}

/* 钻取面包屑（画布上方，告诉用户当前钻到了哪一层） */
.drill-bar {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 4px;
  padding: 6px 10px;
  background: var(--bg-secondary);
  border: 1px solid var(--border-color);
  border-radius: var(--radius-md);
  font-size: 12px;
  color: var(--text-secondary);
  flex-shrink: 0;
}
.drill-bar__home {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 3px 10px;
  background: var(--bg-tertiary);
  border: 1px solid var(--border-color);
  border-radius: var(--radius-pill);
  color: var(--text-primary);
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.15s ease;
}
.drill-bar__home:hover {
  background: var(--accent-blue-soft);
  border-color: var(--accent-blue-edge);
  color: var(--accent-blue);
}
.drill-bar__sep {
  color: var(--text-muted);
  font-size: 14px;
  padding: 0 2px;
  user-select: none;
}
.drill-bar__crumb {
  background: transparent;
  border: 1px solid transparent;
  padding: 3px 8px;
  border-radius: var(--radius-sm);
  font-size: 12px;
  color: var(--text-secondary);
  cursor: pointer;
  font-family: inherit;
  max-width: 200px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  transition: all 0.15s ease;
}
.drill-bar__crumb:hover {
  background: var(--bg-hover);
  color: var(--text-primary);
}
.drill-bar__crumb--current {
  background: var(--accent-blue-soft);
  border-color: var(--accent-blue-edge);
  color: var(--accent-blue);
  font-weight: 600;
  cursor: default;
}
.drill-bar__crumb--current:hover {
  background: var(--accent-blue-soft);
  color: var(--accent-blue);
}
.drill-bar__meta {
  margin-left: auto;
  font-size: 11px;
  color: var(--text-muted);
  padding-left: 12px;
}

/* 缩放百分比提示（仅当用户主动缩放过才显示） */
.assoc-zoom-indicator {
  position: absolute;
  bottom: 8px;
  right: 8px;
  padding: 2px 7px;
  background: var(--bg-tertiary);
  border: 1px solid var(--border-color);
  border-radius: var(--radius-pill);
  font-size: 10.5px;
  font-weight: 600;
  color: var(--text-secondary);
  z-index: 5;
  font-feature-settings: 'tnum';
  pointer-events: none;
}

/* ============================================================
   Detail panel
   ============================================================ */
.assoc-detail {
  width: 380px;
  border-left: 1px solid var(--border-color);
  background: var(--bg-secondary);
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  flex-shrink: 0;
}

.detail__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 14px 18px;
  background: var(--bg-tertiary);
  border-bottom: 1px solid var(--border-color);
  position: sticky;
  top: 0;
  z-index: 2;
}
.detail__header > div {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}
.detail__dot {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  flex-shrink: 0;
}
.detail__title {
  margin: 0;
  font-size: 16px;
  font-weight: 700;
  color: var(--text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.detail__tier {
  font-size: 10px;
  font-weight: 600;
  padding: 2px 7px;
  border-radius: 10px;
  background: var(--bg-hover);
  color: var(--text-secondary);
  letter-spacing: 0.3px;
}

.detail__section {
  padding: 14px 18px;
  border-bottom: 1px solid var(--border-color);
}
.detail__section h4 {
  margin: 0 0 10px 0;
  font-size: 12px;
  color: var(--text-muted);
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.5px;
}
.detail__text {
  margin: 0;
  font-size: 13px;
  color: var(--text-secondary);
  line-height: 1.7;
}
.detail__text code {
  background: var(--bg-tertiary);
  padding: 1px 6px;
  border-radius: var(--radius-xs);
  font-family: monospace;
  font-size: 12px;
  color: var(--accent-blue);
  border: 1px solid var(--border-color);
}
.detail__text--muted { color: var(--text-muted); }

/* Relation groups */
.relate-group {
  margin-bottom: 12px;
}
.relate-group__title {
  font-size: 11.5px;
  color: var(--text-secondary);
  font-weight: 600;
  margin-bottom: 6px;
  display: flex;
  align-items: center;
  gap: 6px;
}
.relate-group__glyph {
  font-size: 14px;
}
.relate-group__count {
  color: var(--text-muted);
  font-weight: normal;
  margin-left: auto;
  font-size: 10.5px;
}
.relate-group__items {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.relate-chip {
  padding: 4px 10px;
  background: var(--bg-tertiary);
  border: 1px solid var(--border-color);
  border-radius: var(--radius-pill);
  font-size: 12px;
  color: var(--text-secondary);
  cursor: pointer;
  transition: all 0.15s ease;
  font-family: inherit;
}
.relate-chip:hover {
  background: var(--accent-blue-soft);
  border-color: var(--accent-blue-edge);
  color: var(--accent-blue);
}

/* Tips */
.tips {
  margin: 0;
  padding-left: 18px;
  font-size: 12.5px;
  color: var(--text-secondary);
  line-height: 1.7;
}
.tips li { margin-bottom: 6px; }

/* Resource list in detail */
.res-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.res-item {
  display: flex;
  gap: 10px;
  padding: 8px 10px;
  background: var(--bg-tertiary);
  border-radius: var(--radius-sm);
}
.res-item__icon {
  font-size: 16px;
  flex-shrink: 0;
}
.res-item__body { flex: 1; min-width: 0; }
.res-item__name {
  font-size: 12.5px;
  font-weight: 600;
  color: var(--text-primary);
  margin-bottom: 2px;
}
.res-item__name a {
  color: var(--accent-blue);
  text-decoration: none;
}
.res-item__name a:hover { text-decoration: underline; }
.res-item__summary {
  margin: 0;
  font-size: 11.5px;
  color: var(--text-secondary);
  line-height: 1.5;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

/* Empty state in detail */
.detail__empty {
  padding: 40px 24px;
  text-align: center;
  color: var(--text-secondary);
}
.detail__empty-icon {
  font-size: 48px;
  margin-bottom: 12px;
  opacity: 0.6;
}
.detail__empty h3 {
  margin: 0 0 16px 0;
  font-size: 16px;
  color: var(--text-primary);
}
.detail__steps {
  text-align: left;
  padding-left: 20px;
  font-size: 12.5px;
  line-height: 1.7;
  color: var(--text-secondary);
}
.detail__steps li { margin-bottom: 10px; }
.detail__steps b { color: var(--text-primary); }
.detail__steps code {
  background: var(--bg-tertiary);
  padding: 1px 6px;
  border-radius: var(--radius-xs);
  font-family: monospace;
  font-size: 11.5px;
  color: var(--accent-blue);
}
.detail__alert { margin-top: 18px; text-align: left; }

/* 关系 ↔ 颜色图例（在「从这里开始」空状态下展示） */
.detail__legend {
  margin-top: 24px;
  padding: 14px 14px 10px;
  background: var(--bg-tertiary);
  border: 1px solid var(--border-color);
  border-radius: var(--radius-md);
  text-align: left;
}
.detail__legend-title {
  margin: 0 0 10px;
  font-size: 11.5px;
  font-weight: 600;
  color: var(--text-muted);
  letter-spacing: 0.5px;
  text-transform: uppercase;
}
.legend-group + .legend-group {
  margin-top: 10px;
}
.legend-group__title {
  font-size: 11px;
  color: var(--text-secondary);
  margin-bottom: 6px;
  letter-spacing: 0.3px;
}
.legend-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px 10px;
}
.legend-item {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}
.legend-item__swatch {
  flex-shrink: 0;
  width: 16px;
  height: 4px;
  border-radius: 2px;
  box-shadow: 0 0 0 1px rgba(255, 255, 255, 0.06);
}
.legend-grid--nodes {
  grid-template-columns: repeat(2, minmax(0, 1fr));
}
.legend-item--node {
  align-items: center;
}
.legend-item__dot {
  flex-shrink: 0;
  border-radius: 50%;
  box-shadow: 0 0 0 1px rgba(255, 255, 255, 0.1);
  align-self: center;
}
.legend-item__body {
  display: flex;
  flex-direction: column;
  min-width: 0;
  line-height: 1.3;
}
.legend-item__label {
  font-size: 12px;
  font-weight: 600;
  color: var(--text-primary);
}
.legend-item__code {
  font-family: monospace;
  font-size: 10.5px;
  color: var(--text-muted);
  background: transparent;
  padding: 0;
  border: none;
}

/* (旧主题地图相关样式已移除) */

/* ============================================================
   右键菜单（知识图谱视图专用）
   ============================================================ */
.assoc-context-menu {
  position: fixed;
  z-index: 1000;
  min-width: 180px;
  background: var(--bg-tertiary);
  border: 1px solid var(--border-color);
  border-radius: var(--radius-md);
  box-shadow: 0 6px 24px rgba(0, 0, 0, 0.35);
  padding: 4px;
  font-size: 13px;
  display: flex;
  flex-direction: column;
  gap: 1px;
}
.assoc-context-menu__item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 12px;
  background: transparent;
  border: none;
  border-radius: var(--radius-sm);
  color: var(--text-primary);
  font-family: inherit;
  font-size: 13px;
  text-align: left;
  cursor: pointer;
  transition: background 0.12s ease;
}
.assoc-context-menu__item:hover {
  background: var(--bg-hover);
}
.assoc-context-menu__item--danger {
  color: var(--accent-red, #ef4444);
}
.assoc-context-menu__item--danger:hover {
  background: rgba(239, 68, 68, 0.12);
}
.assoc-context-menu__item span {
  font-size: 15px;
  width: 18px;
  text-align: center;
  flex-shrink: 0;
}
.assoc-context-menu__divider {
  height: 1px;
  background: var(--border-color);
  margin: 4px 6px;
}
</style>