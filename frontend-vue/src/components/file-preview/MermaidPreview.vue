<script setup lang="ts">
/**
 * MermaidPreview — .mmd / .mermaid 文件预览组件
 *
 * 复用 useMermaid composable 的渲染能力：
 *   - 使用与聊天面板/笔记面板相同的 mermaid dark 主题
 *   - 渲染失败时回退为原始源码展示
 *   - 顶部 toolbar 提供"放大查看"按钮（复用 MermaidViewer 全屏查看器）
 *   - 右上角可切换"源码" / "图形"视图，便于用户对照修改
 */
import { onMounted, ref, watch, nextTick } from 'vue'
import {
  renderMermaidBlocks,
  installMermaidObserver,
} from '@/composables/useMermaid'

const props = defineProps<{
  /** .mmd 文件原始文本内容 */
  content: string
  /** 文件名（用于放大查看器标题） */
  fileName?: string
}>()

type ViewMode = 'rendered' | 'source'
const viewMode = ref<ViewMode>('rendered')

const renderRef = ref<HTMLElement | null>(null)
const renderError = ref('')
const isRendering = ref(false)

/**
 * 把 content 注入到 renderRef 内的一个 <pre><code class="language-mermaid">
 * 占位节点里，然后调用 renderMermaidBlocks 让 composable 完成替换。
 * 这样能复用聊天面板里写好的渲染管线（含 dark 主题、错误回退、
 * expand 按钮注入、bindFunctions 调用等），零重复代码。
 */
async function renderTo(target: HTMLElement, source: string) {
  // 清空旧的容器并塞入一个待渲染节点
  target.innerHTML = ''
  const pre = document.createElement('pre')
  pre.className = 'mermaid-render-slot'
  const code = document.createElement('code')
  code.className = 'language-mermaid'
  code.textContent = source
  pre.appendChild(code)
  target.appendChild(pre)

  isRendering.value = true
  renderError.value = ''
  try {
    await renderMermaidBlocks(target)
    // 检查是否成功替换（pre 被替换为 .mermaid-diagram）
    const stillRaw = target.querySelector('pre.mermaid-render-slot')
    if (stillRaw) {
      renderError.value = 'Mermaid 源码解析失败，请检查语法后重试。'
    }
  } catch (e) {
    renderError.value = e instanceof Error ? e.message : '渲染失败'
  } finally {
    isRendering.value = false
  }
}

watch(
  () => [props.content, viewMode.value] as const,
  async ([source, mode]) => {
    if (mode !== 'rendered') return
    await nextTick()
    if (renderRef.value) await renderTo(renderRef.value, source || '')
  },
  { immediate: true },
)

onMounted(() => {
  // 确保全局 observer 已安装，这样即使 dialog 卸载/重挂也安全
  installMermaidObserver()
})
</script>

<template>
  <div class="fp-mermaid">
    <!-- Toolbar -->
    <div class="fp-mermaid__toolbar">
      <div class="fp-mermaid__tabs">
        <button
          class="fp-mermaid__tab"
          :class="{ 'is-active': viewMode === 'rendered' }"
          @click="viewMode = 'rendered'"
          type="button"
        >
          🧠 图形
        </button>
        <button
          class="fp-mermaid__tab"
          :class="{ 'is-active': viewMode === 'source' }"
          @click="viewMode = 'source'"
          type="button"
        >
          &lt;/&gt; 源码
        </button>
      </div>
    </div>

    <!-- Body -->
    <div v-if="viewMode === 'rendered'" class="fp-mermaid__body">
      <div v-if="isRendering" class="fp-mermaid__status">
        <div class="fp-mermaid__spinner" />
        <span>正在渲染图表…</span>
      </div>
      <div v-else-if="renderError" class="fp-mermaid__error">
        <div class="fp-mermaid__error-title">⚠️ {{ renderError }}</div>
        <div class="fp-mermaid__error-hint">已切换到「源码」视图以便查看。</div>
      </div>
      <div ref="renderRef" class="fp-mermaid__render-host" />
    </div>

    <pre v-else class="fp-mermaid__source"><code>{{ content }}</code></pre>
  </div>
</template>

<style scoped>
.fp-mermaid {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
}

/* ── Toolbar ── */
.fp-mermaid__toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 0 0 12px;
  flex-shrink: 0;
}

.fp-mermaid__tabs {
  display: inline-flex;
  background: var(--bg-tertiary, #1c2030);
  border: 1px solid var(--border-color, #2a3042);
  border-radius: 8px;
  padding: 2px;
}

.fp-mermaid__tab {
  padding: 5px 14px;
  font-size: 12.5px;
  border: none;
  background: transparent;
  color: var(--text-secondary, #9ca3b5);
  border-radius: 6px;
  cursor: pointer;
  font-weight: 500;
  transition: all 0.15s;
}

.fp-mermaid__tab:hover {
  color: var(--text-primary, #e6e9ef);
}

.fp-mermaid__tab.is-active {
  background: var(--accent-blue-soft, rgba(76, 125, 255, 0.18));
  color: var(--accent-blue, #4c7dff);
}

/* ── Body (rendered) ── */
.fp-mermaid__body {
  flex: 1;
  min-height: 0;
  overflow: auto;
  background: var(--bg-secondary, #0d1117);
  border: 1px solid var(--border-color, #2a3042);
  border-radius: 10px;
  padding: 20px;
  display: flex;
  align-items: center;
  justify-content: center;
}

.fp-mermaid__render-host {
  width: 100%;
  display: flex;
  justify-content: center;
}

/* 让生成的 mermaid-diagram 撑满容器宽度 */
.fp-mermaid__render-host :deep(.mermaid-diagram) {
  width: 100%;
  display: flex;
  justify-content: center;
}

.fp-mermaid__render-host :deep(.mermaid-svg-holder) {
  width: 100%;
  display: flex;
  justify-content: center;
}

.fp-mermaid__render-host :deep(.mermaid-svg-holder svg) {
  max-width: 100%;
  height: auto;
}

.fp-mermaid__status {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 10px;
  color: var(--text-secondary, #9ca3b5);
  font-size: 13px;
}

.fp-mermaid__spinner {
  width: 26px;
  height: 26px;
  border-radius: 50%;
  border: 3px solid var(--border-color, #2a3042);
  border-top-color: var(--accent-blue, #4c7dff);
  animation: fp-mermaid-spin 0.8s linear infinite;
}

@keyframes fp-mermaid-spin {
  to { transform: rotate(360deg); }
}

.fp-mermaid__error {
  text-align: center;
  color: var(--accent-red-text, #ff8b8b);
  font-size: 13px;
}

.fp-mermaid__error-title {
  font-weight: 600;
  margin-bottom: 4px;
}

.fp-mermaid__error-hint {
  font-size: 12px;
  color: var(--text-muted, #6b7180);
}

/* ── Body (source) ── */
.fp-mermaid__source {
  flex: 1;
  min-height: 0;
  margin: 0;
  overflow: auto;
  background: var(--bg-secondary, #0d1117);
  border: 1px solid var(--border-color, #2a3042);
  border-radius: 10px;
  padding: 16px;
  font-family: 'JetBrains Mono', 'Consolas', monospace;
  font-size: 12.5px;
  color: var(--text-primary, #e6e9ef);
  line-height: 1.55;
  white-space: pre;
  word-break: normal;
}

.fp-mermaid__source code {
  font-family: inherit;
  background: transparent;
  color: inherit;
  padding: 0;
}
</style>