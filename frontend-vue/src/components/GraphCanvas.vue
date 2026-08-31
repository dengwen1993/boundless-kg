<template>
  <section class="graph-pane">
    <!-- D3 mounts here -->
    <div ref="canvasRef" class="graph-canvas"></div>

    <!-- Floating controls -->
    <div class="canvas-controls">
      <button class="ctrl-btn" title="放大" @click="canvas.zoomBy(1.3)">+</button>
      <button class="ctrl-btn" title="缩小" @click="canvas.zoomBy(0.77)">−</button>
      <button class="ctrl-btn" title="适配" @click="canvas.fit()">⤢</button>
      <div class="ctrl-divider"></div>
      <button class="ctrl-btn" title="上一层" @click="goUp">↑</button>
      <button class="ctrl-btn" title="下一层" @click="goDown">↓</button>
    </div>

    <!-- Legend -->
    <div class="legend">
      <div class="legend-item"><span class="dot dot--L0"></span>L0 领域根</div>
      <div class="legend-item"><span class="dot dot--L1"></span>L1 一级主题</div>
      <div class="legend-item"><span class="dot dot--L2"></span>L2 主题</div>
      <div class="legend-item"><span class="dot dot--L3"></span>L3 子主题</div>
      <div class="legend-item"><span class="dot dot--leaf"></span>叶子节点</div>
      <div class="legend-divider"></div>
      <div class="legend-item"><span class="rel-line rel-line--depends"></span>依赖</div>
      <div class="legend-item"><span class="rel-line rel-line--related"></span>相关</div>
      <div class="legend-item"><span class="rel-line rel-line--sequence"></span>顺序</div>
      <div class="legend__hint">
        🖱 单击选中 · 双击查看笔记<br />
        ➕ 悬停 <b>+</b> 加子节点<br />
        ▸ 右键：编辑 / 🔗 连接 / 🗑 删除
      </div>
    </div>

    <!-- Empty state -->
    <div v-if="showEmpty" class="empty-state">
      <div class="empty-state__icon">🕸️</div>
      <h3>该领域下还没有节点</h3>
      <p>试试右侧助手："帮我展开 X" 或 "加个节点 Y"</p>
    </div>

    <!-- Context menu -->
    <div
      v-if="contextMenu.visible"
      class="context-menu"
      :style="{ left: contextMenu.x + 'px', top: contextMenu.y + 'px' }"
      @click.stop
    >
      <button class="context-menu__item" @click="onDrill">
        <span>🔍</span> 展开子层级
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
      <button class="context-menu__item context-menu__item--danger" @click="onDelete">
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
import { ref, watch, onMounted, onUnmounted, nextTick } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useGraphStore } from '@/stores/graph'
import { useGraphCanvas } from '@/composables/useGraphCanvas'
import NodeEditDialog from './NodeEditDialog.vue'
import type { GraphNode } from '@/types/graph'

const graphStore = useGraphStore()
const canvas = useGraphCanvas()

const canvasRef = ref<HTMLElement | null>(null)
const showEmpty = ref(false)

// Context menu state
const contextMenu = ref({
  visible: false,
  x: 0,
  y: 0,
})
let contextTarget: any = null

// Edit dialog state
const editDialogVisible = ref(false)
const editMode = ref<'update' | 'create-child'>('update')
const editTarget = ref<GraphNode | null>(null)
const editParent = ref('')

// ── Init canvas on mount ──
onMounted(() => {
  if (canvasRef.value) {
    canvas.init(canvasRef.value, {
      onContextMenu: (node, x, y) => {
        contextTarget = node
        contextMenu.value = { visible: true, x, y }
        // adjust position if overflowing
        nextTick(() => {
          const menuWidth = 200
          const menuHeight = 220
          if (x + menuWidth > window.innerWidth)
            contextMenu.value.x = window.innerWidth - menuWidth - 8
          if (y + menuHeight > window.innerHeight)
            contextMenu.value.y = window.innerHeight - menuHeight - 8
        })
      },
      onEditNode: (node) => {
        openEditDialog(node, 'update')
      },
      onAddChild: (parentName) => {
        openAddChildDialog(parentName)
      },
      onOpenNote: (nodeName) => {
        graphStore.openNotePanel(nodeName)
      },
    })
  }
})

// Track previous domain so we can distinguish a real domain switch
// (which should clear the canvas's collapsed state) from a normal
// drill-in/drill-out (which should preserve everything). Both events
// fire the [graph, drillStack] watcher, so we compare `activeDomain`
// against the last value seen by this watcher.
let prevDomain = ''

// ── Re-render when graph or drill stack changes ──
watch(
  () => [graphStore.graph, graphStore.drillStack],
  () => {
    const currentDomain = graphStore.activeDomain
    const isDomainSwitch = currentDomain !== prevDomain
    prevDomain = currentDomain

    nextTick(() => {
      if (isDomainSwitch) {
        // Drop the previous graph's collapsed state so name collisions
        // don't silently hide branches in the new graph. Do NOT touch
        // the zoom/pan here — render() already schedules a fit() that
        // animates from the current viewport, and resetting the zoom
        // on every switch makes very large trees collapse to a single
        // unreadable column. Let the user keep their preferred zoom.
        canvas.clearCollapsed()
      }
      canvas.render()
      showEmpty.value = (graphStore.graph?.nodes.length ?? 0) <= 1
    })
  },
  { deep: true },
)

// ── Close context menu on outside click ──
function onDocumentClick() {
  contextMenu.value.visible = false
}
onMounted(() => document.addEventListener('click', onDocumentClick))
onUnmounted(() => document.removeEventListener('click', onDocumentClick))

// ── Navigation ──
function goUp() {
  graphStore.popDrill()
  canvas.render()
}

function goDown() {
  const sel = graphStore.selectedNode
  if (sel && (sel.childCount ?? 0) > 0) {
    graphStore.drillTo(sel.name)
    canvas.render()
  } else if (sel) {
    ElMessage.info(`「${sel.name}」已是叶子节点`)
  } else {
    ElMessage.info('请先选中一个节点')
  }
}

// ── Context menu actions ──
function onDrill() {
  contextMenu.value.visible = false
  if (contextTarget.childCount > 0) {
    graphStore.drillTo(contextTarget.name)
    canvas.render()
  } else {
    ElMessage.info(`「${contextTarget.name}」已是叶子节点`)
  }
}

function onEdit() {
  contextMenu.value.visible = false
  if (contextTarget.isDomainRoot) {
    ElMessage.info('领域根节点名称与领域名一致，不能重命名')
    return
  }
  openEditDialog(contextTarget, 'update')
}

function onOpenNote() {
  contextMenu.value.visible = false
  if (contextTarget.isDomainRoot) {
    ElMessage.info('领域根节点没有独立笔记')
    return
  }
  graphStore.openNotePanel(contextTarget.name)
}

function onAddChild() {
  contextMenu.value.visible = false
  openAddChildDialog(contextTarget.name)
}

async function onDelete() {
  contextMenu.value.visible = false
  if (contextTarget.isDomainRoot) {
    ElMessage.info('不能删除领域根节点')
    return
  }
  try {
    await ElMessageBox.confirm(
      `此操作会同时移除其他节点中指向它的链接，且不可撤销。`,
      `删除节点「${contextTarget.name}」？`,
      {
        confirmButtonText: '确定删除',
        cancelButtonText: '取消',
        type: 'warning',
      },
    )
    await graphStore.deleteNode(contextTarget.name)
    ElMessage.success(`已删除「${contextTarget.name}」`)
    canvas.render()
  } catch (e: any) {
    if (e !== 'cancel' && e?.message) {
      ElMessage.error(e.message)
    }
  }
}

// ── Edit dialog helpers ──
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

function onSaved() {
  canvas.render()
}

// ── Quick add child (called from external if needed) ──
defineExpose({
  quickAddChild: (parentName: string) => openAddChildDialog(parentName),
})
</script>

<style scoped>
.graph-pane {
  flex: 1;
  position: relative;
  overflow: hidden;
  background: var(--bg-primary);
}

.graph-canvas {
  width: 100%;
  height: 100%;
}
</style>
