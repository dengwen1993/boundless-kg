<template>
  <section class="outline-pane">
    <!-- Toolbar -->
    <div class="outline-toolbar">
      <div class="outline-toolbar__left">
        <el-button-group>
          <el-button size="small" @click="expandToLevel(2)">展开至 L2</el-button>
          <el-button size="small" @click="expandToLevel(3)">展开至 L3</el-button>
          <el-button size="small" @click="expandAll">全部展开</el-button>
          <el-button size="small" @click="collapseAll">全部折叠</el-button>
        </el-button-group>
        <el-input
          v-model="filterText"
          size="small"
          clearable
          placeholder="筛选节点…"
          class="outline-filter"
          :prefix-icon="Search"
        />
      </div>
      <div class="outline-toolbar__right">
        <span class="outline-hint">
          🖱 单击选中 · 双击查看笔记 · 右键菜单 CRUD · 点 ▸ 展开/折叠
        </span>
      </div>
    </div>

    <!-- Drill breadcrumb (shown when drilled into a sub-level) -->
    <div v-if="graphStore.drillStack.length > 0" class="drill-breadcrumb">
      <button class="breadcrumb-item breadcrumb-item--home" @click="resetDrill" title="返回全部大纲">
        🏠 全部大纲
      </button>
      <template v-for="(name, idx) in graphStore.drillStack" :key="name">
        <span class="breadcrumb-sep">›</span>
        <button
          class="breadcrumb-item"
          :class="{ 'breadcrumb-item--active': idx === graphStore.drillStack.length - 1 }"
          @click="drillToIndex(idx)"
        >
          {{ name }}
        </button>
      </template>
    </div>

    <!-- Tree body -->
    <div class="outline-body">
      <el-tree
        ref="treeRef"
        :data="treeData"
        :props="treeProps"
        node-key="name"
        :default-expanded-keys="defaultExpandedKeys"
        :filter-node-method="filterNode"
        :expand-on-click-node="false"
        :current-node-key="graphStore.selectedNode?.name ?? ''"
        highlight-current
        :indent="22"
        @node-click="onNodeClick"
      >
        <template #default="{ data }">
          <div
            class="outline-node"
            :class="[
              `outline-node--${data.tier}`,
              { 'outline-node--root': data.isDomainRoot },
            ]"
            @dblclick.stop="onNodeDblClick(data)"
            @contextmenu.prevent.stop="onNodeContextMenu($event, data)"
          >
            <span class="outline-node__dot"></span>
            <span
              class="outline-node__label"
              v-html="highlightLabel(data.name)"
            ></span>
            <span v-if="data.tier" class="outline-node__tier">{{ data.tier }}</span>
            <span
              v-if="data.childCount > 0"
              class="outline-node__count"
              :title="`${data.childCount} 个子节点`"
            >
              {{ data.childCount }}
            </span>
          </div>
        </template>
      </el-tree>

      <!-- Empty state -->
      <div v-if="isEmpty" class="empty-state">
        <div class="empty-state__icon">📚</div>
        <h3>该领域下还没有节点</h3>
        <p>试试右侧助手："帮我展开 X" 或 "加个节点 Y" 或 "打开 节点 Z"</p>
      </div>
    </div>

    <!-- Context menu -->
    <div
      v-if="contextMenu.visible"
      class="context-menu"
      :style="{ left: contextMenu.x + 'px', top: contextMenu.y + 'px' }"
      @click.stop
    >
      <button v-if="contextMenu.childCount > 0" class="context-menu__item" @click="onDrill">
        <span>🔍</span> 展开子层级（下钻）
      </button>
      <button class="context-menu__item" @click="onExpandSubtree">
        <span>📂</span> 展开此分支
      </button>
      <button class="context-menu__item" @click="onCollapseSubtree">
        <span>📁</span> 折叠此分支
      </button>
      <button class="context-menu__item" @click="onOpenNote">
        <span>📄</span> 查看笔记
      </button>
      <button class="context-menu__item" @click="onEdit">
        <span>✏️</span> 编辑节点
      </button>
      <button class="context-menu__item" @click="onAddChild">
        <span>➕</span> 新增子节点
      </button>
      <div class="context-menu__divider"></div>
      <button
        class="context-menu__item context-menu__item--danger"
        @click="onDelete"
      >
        <span>🗑️</span> 删除节点
      </button>
    </div>

    <!-- Node edit/create dialog -->
    <NodeEditDialog
      v-model:visible="editDialogVisible"
      :mode="editMode"
      :node="editTarget"
      :parent-name="editParent"
      @saved="onSaved"
    />
  </section>
</template>

<script setup lang="ts">
import { ref, computed, watch, onMounted, onUnmounted, nextTick } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Search } from '@element-plus/icons-vue'
import { useGraphStore } from '@/stores/graph'
import NodeEditDialog from './NodeEditDialog.vue'
import type { GraphNode } from '@/types/graph'

interface OutlineNode {
  name: string
  tier: string
  level: number
  isDomainRoot: boolean
  childCount: number
  children: OutlineNode[]
}

const graphStore = useGraphStore()
const treeRef = ref<any>(null)
const filterText = ref('')

const treeProps = {
  label: 'name',
  children: 'children',
}

// ── Build tree data from graph (rooted at drill-stack top or domain root) ──
const treeData = computed<OutlineNode[]>(() => {
  const g = graphStore.graph
  if (!g || g.nodes.length === 0) return []

  const byName = new Map<string, GraphNode>()
  g.nodes.forEach((n) => byName.set(n.name, n))

  let rootName: string
  if (graphStore.drillStack.length > 0) {
    rootName = graphStore.drillStack[graphStore.drillStack.length - 1]
  } else {
    const domainRoot = g.nodes.find((n) => n.isDomainRoot)
    rootName = domainRoot ? domainRoot.name : g.nodes[0]?.name ?? ''
  }
  const rootNode = byName.get(rootName)
  if (!rootNode) return []

  const visited = new Set<string>()
  visited.add(rootNode.name)

  function buildSubtree(node: GraphNode): OutlineNode {
    const childNodes = ((node.links || [])
      .map((c) => byName.get(c))
      .filter(Boolean) as GraphNode[]).filter((c) => {
      if (visited.has(c.name)) return false
      visited.add(c.name)
      return true
    })
    return {
      name: node.name,
      tier: node.tier || 'leaf',
      level: node.level || 1,
      isDomainRoot: !!node.isDomainRoot,
      childCount: (node.links || []).length,
      children: childNodes.map((c) => buildSubtree(c)),
    }
  }

  return [buildSubtree(rootNode)]
})

// ── Default expanded keys: L0 + L1 (so L1 & L2 visible initially) ──
const defaultExpandedKeys = computed<string[]>(() => {
  const keys: string[] = []
  function walk(nodes: OutlineNode[]) {
    nodes.forEach((n) => {
      // expand domain root, L0, L1, or any node with level <= 1
      if (n.isDomainRoot || n.tier === 'L0' || n.tier === 'L1' || (n.level ?? 1) <= 1) {
        keys.push(n.name)
      }
      if (n.children?.length) walk(n.children)
    })
  }
  walk(treeData.value)
  return keys
})

const isEmpty = computed(() => treeData.value.length === 0)

// ── Filter ──
watch(filterText, (val) => {
  treeRef.value?.filter(val)
})

function filterNode(value: string, data: OutlineNode) {
  if (!value) return true
  return data.name.toLowerCase().includes(value.toLowerCase())
}

// Apply default expansion whenever tree data rebuilds (new domain / drill change)
watch(
  treeData,
  () => {
    nextTick(() => {
      if (!treeRef.value) return
      defaultExpandedKeys.value.forEach((k) => {
        const n = treeRef.value.store?.getNode(k)
        if (n) n.expanded = true
      })
    })
  },
  { immediate: true },
)

// ── AI assistant navigation: kg_open_node ──
// When the chat store dispatches a navigation request, expand the path
// from L0 to the target and set it as the current (highlighted) node.
// Runs after the tree data is rendered (nextTick) so el-tree's internal
// store has registered the new node-keys.
watch(
  () => graphStore.pendingNavigation,
  (req) => {
    if (!req || req.domain !== graphStore.activeDomain) return
    nextTick(() => {
      applyNavigation(req.path, req.node).catch((e: any) => {
        console.warn('[kg_open_node][outline] applyNavigation failed', e)
      })
    })
  },
  { deep: true },
)

async function applyNavigation(path: string[], target: string) {
  console.log('[kg_open_node][outline] applyNavigation START', { path, target })
  const tree: any = treeRef.value
  if (!tree) {
    console.warn('[kg_open_node][outline] treeRef is null, bail')
    return
  }
  // Expand every node along the path so the target is rendered.
  for (const k of path) {
    const n = tree.store?.getNode?.(k)
    if (n) {
      n.expanded = true
      console.log('[kg_open_node][outline] expanded', k)
    } else {
      console.warn('[kg_open_node][outline] node NOT in tree store', k)
    }
  }
  // Wait for Vue to re-render so el-tree registers the children of
  // each newly-expanded node into its internal store.  Without this
  // tick, `setCurrentKey` silently ignores keys that aren't yet
  // registered in the store — the target key won't highlight, the
  // scroll position looks stale, and the selection appears broken.
  await nextTick()
  // Drive the :current-node-key prop via the store so the highlight is
  // declarative.  For real nodes we have full metadata; for the synthetic
  // L0 root (which lives only in the decorated payload, not in nodeMap)
  // we fall back to a minimal record so the highlight still works.
  const existing = graphStore.nodeMap.get(target)
  console.log('[kg_open_node][outline] nodeMap lookup', {
    target,
    found: !!existing,
    existing,
    nodeMapSize: graphStore.nodeMap.size,
  })
  if (existing) {
    graphStore.selectNode(existing)
  } else {
    graphStore.selectNode({
      name: target,
      links: [],
      level: 0,
      tier: 'L0',
      isDomainRoot: true,
    })
  }
  console.log('[kg_open_node][outline] selectNode done', {
    selectedNode: graphStore.selectedNode?.name,
  })
  // Imperative call too — the prop binding can lag a tick on the very
  // first request, and setCurrentKey flips the .is-current class
  // synchronously.
  const setKeyResult = tree.setCurrentKey?.(target)
  console.log('[kg_open_node][outline] setCurrentKey called', {
    target,
    returned: setKeyResult,
    storeCurrentNodeKey: tree.store?.currentNodeKey,
    isCurrentInDom: !!document.querySelector('.el-tree-node.is-current'),
  })
  // Scroll the highlighted row into view.  Two ticks: one to let
  // setCurrentKey paint the .is-current class, another so the
  // browser has a layout to scroll within.
  nextTick(() => {
    const el = document.querySelector('.el-tree-node.is-current')
    console.log('[kg_open_node][outline] scrollIntoView check', {
      found: !!el,
      label: el?.textContent?.trim().slice(0, 50),
    })
    if (el instanceof HTMLElement) {
      el.scrollIntoView({ behavior: 'smooth', block: 'center' })
    }
  })
}

// ── Highlight search term in label ──
function escapeHtml(s: string): string {
  return s
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
}

function highlightLabel(name: string): string {
  const term = filterText.value.trim()
  if (!term) return escapeHtml(name)
  const lower = name.toLowerCase()
  const t = term.toLowerCase()
  let result = ''
  let i = 0
  while (i < name.length) {
    const idx = lower.indexOf(t, i)
    if (idx === -1) {
      result += escapeHtml(name.slice(i))
      break
    }
    result += escapeHtml(name.slice(i, idx))
    result += `<mark class="outline-mark">${escapeHtml(name.slice(idx, idx + term.length))}</mark>`
    i = idx + term.length
  }
  return result || escapeHtml(name)
}

// ── Node interactions ──
function onNodeClick(data: OutlineNode) {
  graphStore.selectNode({
    name: data.name,
    links: [],
    level: data.level,
    tier: data.tier as any,
    childCount: data.childCount,
    isDomainRoot: data.isDomainRoot,
  })
}

function onNodeDblClick(data: OutlineNode) {
  if (data.isDomainRoot) {
    ElMessage.info('领域根节点没有独立笔记')
    return
  }
  graphStore.openNotePanel(data.name)
}

// ── Context menu ──
const contextMenu = ref({ visible: false, x: 0, y: 0, childCount: 0 })
let contextTarget: OutlineNode | null = null

function onNodeContextMenu(e: MouseEvent, data: OutlineNode) {
  contextTarget = data
  contextMenu.value = { visible: true, x: e.clientX, y: e.clientY, childCount: data.childCount }
  graphStore.selectNode({
    name: data.name,
    links: [],
    level: data.level,
    tier: data.tier as any,
    childCount: data.childCount,
    isDomainRoot: data.isDomainRoot,
  })
  nextTick(() => {
    const menuWidth = 220
    const menuHeight = 260
    if (contextMenu.value.x + menuWidth > window.innerWidth)
      contextMenu.value.x = window.innerWidth - menuWidth - 8
    if (contextMenu.value.y + menuHeight > window.innerHeight)
      contextMenu.value.y = window.innerHeight - menuHeight - 8
  })
}

// ── Edit dialog ──
const editDialogVisible = ref(false)
const editMode = ref<'update' | 'create-child'>('update')
const editTarget = ref<GraphNode | null>(null)
const editParent = ref('')

function openEditDialog(node: any, mode: 'update' | 'create-child') {
  editMode.value = mode
  editTarget.value = node
  editParent.value = ''
  editDialogVisible.value = true
}

function openAddChildDialog(parentName: string) {
  editMode.value = 'create-child'
  editTarget.value = null
  editParent.value = parentName
  editDialogVisible.value = true
}

// ── Context menu actions ──
function onDrill() {
  contextMenu.value.visible = false
  if (!contextTarget) return
  if (contextTarget.childCount > 0) {
    graphStore.drillTo(contextTarget.name)
  } else {
    ElMessage.info(`「${contextTarget.name}」已是叶子节点`)
  }
}

// ── Drill navigation (go back to full outline) ──
function resetDrill() {
  graphStore.setDrillStack([])
}

function drillToIndex(idx: number) {
  const newStack = graphStore.drillStack.slice(0, idx + 1)
  graphStore.setDrillStack(newStack)
}

function onEdit() {
  contextMenu.value.visible = false
  if (!contextTarget) return
  if (contextTarget.isDomainRoot) {
    ElMessage.info('领域根节点名称与领域名一致，不能重命名')
    return
  }
  const node = graphStore.nodeMap.get(contextTarget.name)
  if (node) openEditDialog(node, 'update')
}

function onOpenNote() {
  contextMenu.value.visible = false
  if (!contextTarget) return
  if (contextTarget.isDomainRoot) {
    ElMessage.info('领域根节点没有独立笔记')
    return
  }
  graphStore.openNotePanel(contextTarget.name)
}

function onAddChild() {
  contextMenu.value.visible = false
  if (!contextTarget) return
  openAddChildDialog(contextTarget.name)
}

function onExpandSubtree() {
  contextMenu.value.visible = false
  if (!contextTarget || !treeRef.value) return
  const keys: string[] = []
  function collect(nodes: OutlineNode[]) {
    nodes.forEach((n) => {
      keys.push(n.name)
      if (n.children?.length) collect(n.children)
    })
  }
  collect([contextTarget])
  keys.forEach((k) => {
    const n = treeRef.value.store?.getNode(k)
    if (n) n.expanded = true
  })
}

function onCollapseSubtree() {
  contextMenu.value.visible = false
  if (!contextTarget || !treeRef.value) return
  const keys: string[] = []
  function collect(nodes: OutlineNode[]) {
    nodes.forEach((n) => {
      if (n.children?.length) {
        keys.push(n.name)
        collect(n.children)
      }
    })
  }
  collect([contextTarget])
  keys.forEach((k) => {
    const n = treeRef.value.store?.getNode(k)
    if (n && k !== contextTarget!.name) n.expanded = false
  })
}

async function onDelete() {
  contextMenu.value.visible = false
  if (!contextTarget) return
  if (contextTarget.isDomainRoot) {
    ElMessage.info('不能删除领域根节点')
    return
  }
  const targetName = contextTarget.name
  try {
    await ElMessageBox.confirm(
      `此操作会同时移除其他节点中指向它的链接，且不可撤销。`,
      `删除节点「${targetName}」？`,
      {
        confirmButtonText: '确定删除',
        cancelButtonText: '取消',
        type: 'warning',
      },
    )
    await graphStore.deleteNode(targetName)
    ElMessage.success(`已删除「${targetName}」`)
  } catch (e: any) {
    if (e !== 'cancel' && e?.message) ElMessage.error(e.message)
  }
}

function onSaved() {
  // graph refresh handled by store
}

// ── Expand / collapse controls ──
function collectAllKeys(): string[] {
  const keys: string[] = []
  function walk(nodes: OutlineNode[]) {
    nodes.forEach((n) => {
      keys.push(n.name)
      if (n.children?.length) walk(n.children)
    })
  }
  walk(treeData.value)
  return keys
}

function expandAll() {
  if (!treeRef.value) return
  collectAllKeys().forEach((k) => {
    const n = treeRef.value.store?.getNode(k)
    if (n) n.expanded = true
  })
}

function collapseAll() {
  if (!treeRef.value) return
  collectAllKeys().forEach((k) => {
    const n = treeRef.value.store?.getNode(k)
    if (n) n.expanded = false
  })
}

function expandToLevel(targetLevel: number) {
  if (!treeRef.value) return
  function walk(nodes: OutlineNode[]) {
    nodes.forEach((n) => {
      const node = treeRef.value.store?.getNode(n.name)
      if (node) {
        // expand nodes whose level < targetLevel so that level==targetLevel nodes are visible
        node.expanded = (n.level ?? 1) < targetLevel
      }
      if (n.children?.length) walk(n.children)
    })
  }
  walk(treeData.value)
}

// ── Close context menu on outside click ──
function onDocumentClick() {
  contextMenu.value.visible = false
}
onMounted(() => document.addEventListener('click', onDocumentClick))
onUnmounted(() => document.removeEventListener('click', onDocumentClick))

// ── Expose quick add child for external use ──
defineExpose({
  quickAddChild: (parentName: string) => openAddChildDialog(parentName),
})
</script>

<style scoped>
.outline-pane {
  flex: 1;
  position: relative;
  overflow: hidden;
  display: flex;
  flex-direction: column;
  background: var(--bg-primary);
}

/* Toolbar */
.outline-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 10px 16px;
  background: var(--bg-secondary);
  border-bottom: 1px solid var(--border-color);
  flex-shrink: 0;
}
.outline-toolbar__left {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}
.outline-toolbar__right {
  flex-shrink: 0;
}
.outline-filter {
  width: 200px;
}
.outline-hint {
  font-size: 12px;
  color: var(--text-muted);
}

/* Drill breadcrumb */
.drill-breadcrumb {
  display: flex;
  align-items: center;
  gap: 2px;
  padding: 6px 16px;
  background: var(--bg-tertiary);
  border-bottom: 1px solid var(--border-color);
  font-size: 13px;
  flex-shrink: 0;
  overflow-x: auto;
  white-space: nowrap;
}
.breadcrumb-item {
  background: none;
  border: none;
  color: var(--text-secondary);
  cursor: pointer;
  padding: 3px 8px;
  border-radius: 4px;
  font-size: 13px;
  transition: all 0.12s;
}
.breadcrumb-item:hover {
  background: var(--bg-hover);
  color: var(--text-primary);
}
.breadcrumb-item--home {
  font-weight: 600;
  color: var(--accent-blue);
}
.breadcrumb-item--home:hover {
  color: var(--accent-blue);
}
.breadcrumb-item--active {
  color: var(--text-primary);
  font-weight: 600;
  cursor: default;
  background: var(--bg-hover);
}
.breadcrumb-item--active:hover {
  background: var(--bg-hover);
}
.breadcrumb-sep {
  color: var(--text-muted);
  font-size: 14px;
  user-select: none;
}

/* Body */
.outline-body {
  flex: 1;
  overflow: auto;
  padding: 12px 8px 24px 8px;
  position: relative;
}

/* Tree customization */
.outline-pane :deep(.el-tree) {
  background: transparent;
  color: var(--text-primary);
  font-size: 13.5px;
  --el-tree-node-hover-bg-color: var(--bg-hover);
}
.outline-pane :deep(.el-tree-node__content) {
  height: 36px;
  border-radius: var(--radius-sm);
  padding-right: 8px;
  transition: background 0.12s;
}
.outline-pane :deep(.el-tree-node__content:hover) {
  background: var(--bg-hover);
}
.outline-pane :deep(.el-tree-node__expand-icon) {
  color: var(--text-muted);
  font-size: 14px;
}
.outline-pane :deep(.el-tree-node__expand-icon.is-leaf) {
  visibility: hidden;
}
.outline-pane :deep(.el-tree-node__expand-icon:hover) {
  color: var(--accent-blue);
}

/* ── Node row ──────────────────────────────────────────────────
   Every tier-specific colour is funnelled through --tier-color,
   set once per row in the depth ramp below.  The dot, tier badge,
   count chip and selection tint all read the generic --tier-*
   tints derived from it, so a new tier costs one line rather than
   one rule per element. */
.outline-node {
  --tier-color: var(--tier-leaf);
  --tier-fill: color-mix(in srgb, var(--tier-color) 12%, transparent);
  --tier-chip: color-mix(in srgb, var(--tier-color) 20%, transparent);
  --tier-edge: color-mix(in srgb, var(--tier-color) 35%, transparent);
  --tier-text: color-mix(in srgb, var(--tier-color) 55%, #ffffff);
  --dot-size: 9px;
  --label-size: 12.5px;
  --label-weight: 400;

  display: flex;
  align-items: center;
  gap: 8px;
  flex: 1;
  min-width: 0;
  padding: 4px 8px;
  border-radius: var(--radius-md);
  cursor: pointer;
  user-select: none;
}

/* Depth ramp — hue, dot size and type weight all step down together,
   so nesting depth is legible even without the tier badge. */
.outline-node--L0 {
  --tier-color: var(--tier-l0);
  --dot-size: 13px;
  --label-size: 14px;
  --label-weight: 600;
}
.outline-node--L1 {
  --tier-color: var(--tier-l1);
  --dot-size: 11px;
  --label-size: 13.5px;
  --label-weight: 600;
}
.outline-node--L2 {
  --tier-color: var(--tier-l2);
  --dot-size: 11px;
  --label-size: 13px;
  --label-weight: 500;
}
.outline-node--L3 {
  --tier-color: var(--tier-l3);
}
.outline-node--leaf {
  --tier-color: var(--tier-leaf);
}
.outline-node--root {
  --dot-size: 13px;
  --label-size: 14.5px;
  --label-weight: 700;
  background: linear-gradient(90deg, var(--tier-fill), transparent);
  box-shadow: inset 2px 0 0 var(--tier-color);
}

.outline-node__dot {
  width: var(--dot-size);
  height: var(--dot-size);
  border-radius: var(--radius-pill);
  flex-shrink: 0;
  background: var(--tier-color);
}

.outline-node__label {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: var(--label-size);
  font-weight: var(--label-weight);
  color: var(--text-primary);
}
/* Leaf-ish rows recede so the branch structure stays dominant. */
.outline-node--L3 .outline-node__label,
.outline-node--leaf .outline-node__label {
  color: var(--text-secondary);
}

/* Tinted fill + soft edge + light text, rather than a saturated ring
   on a dark chip — keeps the badge readable at 10px. */
.outline-node__tier {
  font-size: 10px;
  font-weight: 600;
  line-height: 1.4;
  padding: 1px 6px;
  border-radius: var(--radius-pill);
  background: var(--tier-chip);
  color: var(--tier-text);
  border: 1px solid var(--tier-edge);
  flex-shrink: 0;
}

.outline-node__count {
  font-size: 11px;
  font-weight: 600;
  min-width: 18px;
  height: 18px;
  padding: 0 5px;
  border-radius: var(--radius-pill);
  background: var(--bg-tertiary);
  color: var(--text-secondary);
  border: 1px solid var(--border-color);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

/* Selection picks up the row's own tier colour instead of a fixed blue,
   so the highlight agrees with the dot and badge beside it.

   Two things to keep in mind here:
   1. Element Plus paints .is-current with --el-color-primary-light-9,
      a near-white blue that glares on this dark theme — neutralise it.
   2. .el-tree-node elements NEST, so `.is-current .outline-node` would
      also match every row in the selected node's subtree.  Always go
      through `> .el-tree-node__content` to stay on the current row. */
.outline-pane :deep(.el-tree-node.is-current > .el-tree-node__content) {
  background: transparent;
}
.outline-pane :deep(.el-tree-node.is-current > .el-tree-node__content .outline-node) {
  /* Mixed against a dark base rather than transparent, so the row reads
     as a tinted surface and can never wash out towards white. */
  background: color-mix(in srgb, var(--tier-color) 16%, var(--bg-secondary));
  box-shadow: inset 2px 0 0 var(--tier-color);
}
.outline-pane :deep(.el-tree-node.is-current > .el-tree-node__content .outline-node__dot) {
  box-shadow: 0 0 0 3px color-mix(in srgb, var(--tier-color) 22%, transparent);
}
/* Lift the label to full contrast rather than a bright tier tint — the
   tinted surface and accent bar already carry the selected state. */
.outline-pane :deep(.el-tree-node.is-current > .el-tree-node__content .outline-node__label) {
  color: var(--text-primary);
}
.outline-pane :deep(.el-tree-node.is-current > .el-tree-node__content .outline-node__count) {
  background: var(--tier-chip);
  border-color: var(--tier-edge);
  color: var(--tier-text);
}

.outline-mark {
  background: var(--accent-amber-soft);
  color: var(--accent-amber);
  border-radius: var(--radius-xs);
  padding: 0 1px;
}
</style>
