<template>
  <header class="top-bar">
    <div class="top-bar__left">
      <!-- Logo -->
      <div class="logo">
        <svg
          viewBox="0 0 24 24"
          width="22"
          height="22"
          fill="none"
          stroke="currentColor"
          stroke-width="2"
        >
          <circle cx="6" cy="6" r="2.5" />
          <circle cx="18" cy="6" r="2.5" />
          <circle cx="12" cy="14" r="2.5" />
          <circle cx="6" cy="20" r="2.5" />
          <circle cx="18" cy="20" r="2.5" />
          <line x1="6" y1="6" x2="12" y2="14" />
          <line x1="18" y1="6" x2="12" y2="14" />
          <line x1="12" y1="14" x2="6" y2="20" />
          <line x1="12" y1="14" x2="18" y2="20" />
        </svg>
        <span>BoundlessKG</span>
      </div>

      <!-- Domain switcher — width sized so the longest known domain name fits
       without truncation; mirrors the width used in the dropdown panel. -->
      <div class="domain-switcher">
        <el-select
          v-model="graphStore.activeDomain"
          placeholder="选择领域"
          style="width: 420px"
          @change="onDomainChange"
        >
          <el-option
            v-for="d in graphStore.visibleDomains"
            :key="d.name"
            :label="`${d.name} (${d.node_count ?? 0})`"
            :value="d.name"
          />
        </el-select>
      </div>

      <!-- Breadcrumb — only the drill-down path; the domain itself is
       already shown in the dropdown above, so listing it again would be
       redundant. -->
      <nav v-if="graphStore.drillStack.length" class="breadcrumb">
        <template v-for="(node, idx) in graphStore.drillStack" :key="idx">
          <span class="breadcrumb__sep">/</span>
          <span
            class="breadcrumb__item"
            :class="{
              'breadcrumb__item--current': idx === graphStore.drillStack.length - 1,
            }"
            @click="goToLevel(idx)"
          >
            {{ node }}
          </span>
        </template>
      </nav>

      <!-- Selected-node context pill: 当前选中：节点名 [tier] -->
      <div v-if="graphStore.activeDomain && graphStore.selectedNode" class="context-pill">
        <span class="context-pill__label">当前选中：</span>
        <span class="context-pill__node">{{ graphStore.selectedNode.name }}</span>
        <span
          class="context-pill__tier"
          :class="`context-pill__tier--${tierClass}`"
        >{{ tierLabel }}</span>
      </div>
    </div>

    <div class="top-bar__right">
      <!-- View mode switcher -->
      <div class="view-switcher" role="tablist" aria-label="视图模式">
        <button
          class="view-switcher__btn"
          :class="{ 'view-switcher__btn--active': graphStore.viewMode === 'graph' }"
          title="图谱模式：D3 思维导图"
          role="tab"
          @click="setViewMode('graph')"
        >
          <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">
            <line x1="10" y1="12" x2="14" y2="5.5" />
            <line x1="10" y1="12" x2="14" y2="12" />
            <line x1="10" y1="12" x2="14" y2="18.5" />
            <rect x="3" y="9.5" width="7" height="5" rx="2" fill="currentColor" stroke="none" />
            <rect x="14" y="3" width="7" height="5" rx="2" />
            <rect x="14" y="9.5" width="7" height="5" rx="2" />
            <rect x="14" y="16" width="7" height="5" rx="2" />
          </svg>
          <span>思维导图</span>
        </button>
        <button
          class="view-switcher__btn"
          :class="{ 'view-switcher__btn--active': graphStore.viewMode === 'outline' }"
          title="大纲模式：书籍式层级目录"
          role="tab"
          @click="setViewMode('outline')"
        >
          <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2">
            <line x1="8" y1="6" x2="20" y2="6" />
            <line x1="8" y1="12" x2="20" y2="12" />
            <line x1="8" y1="18" x2="20" y2="18" />
            <circle cx="4" cy="6" r="1" fill="currentColor" />
            <circle cx="4" cy="12" r="1" fill="currentColor" />
            <circle cx="4" cy="18" r="1" fill="currentColor" />
          </svg>
          <span>大纲</span>
        </button>
        <button
          class="view-switcher__btn"
          :class="{ 'view-switcher__btn--active': graphStore.viewMode === 'associations' }"
          title="知识图谱：派生层可视化（点击节点查看主题之间的关系）"
          role="tab"
          @click="setViewMode('associations')"
        >
          <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">
            <circle cx="6" cy="6" r="2" />
            <circle cx="18" cy="6" r="2" />
            <circle cx="6" cy="18" r="2" />
            <circle cx="18" cy="18" r="2" />
            <circle cx="12" cy="12" r="2.5" />
            <line x1="7.5" y1="7.5" x2="10.5" y2="10.5" />
            <line x1="16.5" y1="7.5" x2="13.5" y2="10.5" />
            <line x1="7.5" y1="16.5" x2="10.5" y2="13.5" />
            <line x1="16.5" y1="16.5" x2="13.5" y2="13.5" />
          </svg>
          <span>知识图谱</span>
        </button>
      </div>

      <!-- Action buttons -->
      <el-button
        v-if="graphStore.activeDomain"
        size="small"
        class="top-bar__today-btn"
        @click="timelineVisible = true"
        title="今日活动（跨节点计划/资料/笔记时间线）"
      >
        <span>📅</span>
        <span style="margin-left: 4px">今日活动</span>
      </el-button>
      <el-button :icon="Download" circle size="small" @click="exportGraphZip" title="导出知识图谱 ZIP" />
    </div>
  </header>

  <!-- ═══ Timeline Panel ═══ -->
  <TimelinePanel v-model="timelineVisible" :domain="graphStore.activeDomain || ''" />
</template>

<script setup lang="ts">
import { nextTick, computed, ref } from 'vue'
import { Download } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { useGraphStore } from '@/stores/graph'
import { useGraphCanvas } from '@/composables/useGraphCanvas'
import TimelinePanel from './TimelinePanel.vue'

const graphStore = useGraphStore()
const canvas = useGraphCanvas()

// ── 今日活动面板 ──
const timelineVisible = ref(false)

// ── Selected-node context (display) ──
const tierLabel = computed(() => {
  const node = graphStore.selectedNode
  if (!node) return ''
  return node.tier || `L${node.level ?? 1}`
})

const tierClass = computed(() => {
  const node = graphStore.selectedNode
  if (!node) return ''
  return node.tier || `L${node.level ?? 1}`
})

function onDomainChange(name: string) {
  graphStore.loadGraph(name)
}

function goToLevel(idx: number) {
  const stack = graphStore.drillStack.slice(0, idx + 1)
  graphStore.setDrillStack(stack)
  canvas.render()
}

function setViewMode(mode: 'graph' | 'outline' | 'associations') {
  graphStore.setViewMode(mode)
  // when switching back to graph mode, re-render the canvas
  if (mode === 'graph') {
    nextTick(() => canvas.render())
  }
}

async function exportGraphZip() {
  if (!graphStore.activeDomain) {
    ElMessage.warning('请先选择一个领域')
    return
  }
  const domain = graphStore.activeDomain
  const filename = `${domain}.zip`
  const url = `/api/graph/${encodeURIComponent(domain)}/export-zip`
  ElMessage.info('正在打包知识图谱…')
  try {
    const res = await fetch(url)
    if (!res.ok) {
      const txt = await res.text().catch(() => '')
      throw new Error(`HTTP ${res.status} ${txt}`)
    }
    const blob = await res.blob()
    const blobUrl = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = blobUrl
    a.download = filename
    a.click()
    URL.revokeObjectURL(blobUrl)
    ElMessage.success(`已导出 ${filename}`)
  } catch (e) {
    console.error('[exportGraphZip] failed:', e)
    ElMessage.error(`导出失败：${(e as Error).message}`)
  }
}
</script>

<style scoped>
.top-bar {
  height: var(--topbar-h);
  background: var(--bg-secondary);
  border-bottom: 1px solid var(--border-color);
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 16px;
  gap: 16px;
  flex-shrink: 0;
}

.top-bar__left {
  display: flex;
  align-items: center;
  gap: 16px;
  flex: 1;
  min-width: 0;
}

.logo {
  display: flex;
  align-items: center;
  gap: 8px;
  font-weight: 700;
  font-size: 15px;
  color: var(--text-primary);
  white-space: nowrap;
}
.logo svg {
  color: var(--accent-blue);
}

.domain-switcher {
  flex-shrink: 0;
}

.top-bar__right {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-shrink: 0;
}

/* ── Selected-node context pill ── */
.context-pill {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 4px 12px;
  background: var(--bg-tertiary);
  border: 1px solid var(--border-color);
  border-radius: 16px;
  font-size: 12px;
  white-space: nowrap;
  flex-shrink: 0;
  max-width: 360px;
  overflow: hidden;
}
.context-pill__label {
  color: var(--text-muted);
  flex-shrink: 0;
}
.context-pill__node {
  color: var(--accent-blue);
  font-weight: 600;
  overflow: hidden;
  text-overflow: ellipsis;
}
.context-pill__tier {
  display: inline-flex;
  align-items: center;
  padding: 1px 6px;
  border-radius: 4px;
  font-size: 10px;
  font-weight: 700;
  color: #fff;
  flex-shrink: 0;
  line-height: 1.4;
}
.context-pill__tier--L0 { background: var(--tier-l0); }
.context-pill__tier--L1 { background: var(--tier-l1); }
.context-pill__tier--L2 { background: var(--tier-l2); }
.context-pill__tier--L3 { background: var(--tier-l3); }
.context-pill__tier--leaf { background: var(--tier-leaf); }

/* View mode switcher (segmented control) */
.view-switcher {
  display: inline-flex;
  align-items: center;
  background: var(--bg-tertiary);
  border: 1px solid var(--border-color);
  border-radius: 8px;
  padding: 2px;
  gap: 2px;
  flex-shrink: 0;
}
.view-switcher__btn {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 4px 10px;
  background: none;
  border: none;
  color: var(--text-secondary);
  font-size: 12.5px;
  font-weight: 500;
  border-radius: 6px;
  cursor: pointer;
  transition: all 0.15s;
  white-space: nowrap;
}
.view-switcher__btn svg {
  flex-shrink: 0;
}
.view-switcher__btn:hover {
  color: var(--text-primary);
  background: var(--bg-hover);
}
.view-switcher__btn--active {
  background: var(--accent-blue);
  color: #fff;
  box-shadow: 0 1px 4px rgba(76, 125, 255, 0.35);
}
.view-switcher__btn--active:hover {
  background: var(--accent-blue);
  color: #fff;
}
</style>
