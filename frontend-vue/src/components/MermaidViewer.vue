<!--
  MermaidViewer — fullscreen zoomable / pannable diagram overlay.

  Driven by the module-level reactive state in useMermaid.ts so that any
  component that calls renderMermaidBlocks gets the viewer for free — just
  include <MermaidViewer /> once anywhere in the tree.

  Interaction model:
   - Mouse wheel / trackpad scroll → zoom in / out (0.3× – 5×)
   - Click-and-drag → pan
   - Toolbar buttons: zoom in, zoom out, reset, toggle fit-to-width, close
   - Click backdrop or press Esc → close
-->
<template>
  <Teleport to="body">
    <Transition name="mermaid-viewer-fade">
      <div
        v-if="viewerVisible"
        class="mermaid-viewer"
        @click.self="close"
        @wheel.prevent="onWheel"
      >
        <!-- Toolbar -->
        <div class="mermaid-viewer__toolbar">
          <span class="mermaid-viewer__title">{{ viewerTitle }}</span>
          <div class="mermaid-viewer__tools">
            <button
              class="mermaid-viewer__btn"
              title="缩小"
              @click="zoomBy(0.8)"
            >
              <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="11" cy="11" r="8"/><line x1="8" y1="11" x2="14" y2="11"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
            </button>
            <span class="mermaid-viewer__zoom-label">{{ Math.round(scale * 100) }}%</span>
            <button
              class="mermaid-viewer__btn"
              title="放大"
              @click="zoomBy(1.25)"
            >
              <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="11" cy="11" r="8"/><line x1="11" y1="8" x2="11" y2="14"/><line x1="8" y1="11" x2="14" y2="11"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
            </button>
            <button
              class="mermaid-viewer__btn"
              title="重置"
              @click="reset"
            >
              <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="1 4 1 10 7 10"/><path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10"/></svg>
            </button>
            <button
              class="mermaid-viewer__btn mermaid-viewer__btn--close"
              title="关闭"
              @click="close"
            >
              <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
            </button>
          </div>
        </div>

        <!-- Canvas -->
        <div
          ref="canvasRef"
          class="mermaid-viewer__canvas"
          :class="{ 'is-dragging': isDragging }"
          @mousedown="onDragStart"
          @mousemove="onDragMove"
          @mouseup="onDragEnd"
          @mouseleave="onDragEnd"
        >
          <div
            class="mermaid-viewer__svg-wrap"
            :style="{ transform: `translate(${offsetX}px, ${offsetY}px) scale(${scale})` }"
            v-html="viewerSvg"
          ></div>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<script setup lang="ts">
import { ref, watch, onMounted, onUnmounted } from 'vue'
import { viewerVisible, viewerSvg, viewerTitle } from '@/composables/useMermaid'

// ── Zoom & pan state ──
const scale = ref(1)
const offsetX = ref(0)
const offsetY = ref(0)
const isDragging = ref(false)
const canvasRef = ref<HTMLElement | null>(null)

// Drag tracking (screen-space delta → offset delta)
let dragStartX = 0
let dragStartY = 0
let dragOriginX = 0
let dragOriginY = 0

const MIN_SCALE = 0.3
const MAX_SCALE = 5

function clamp(v: number, min: number, max: number) {
  return Math.max(min, Math.min(max, v))
}

function zoomBy(factor: number) {
  scale.value = clamp(scale.value * factor, MIN_SCALE, MAX_SCALE)
}

function reset() {
  scale.value = 1
  offsetX.value = 0
  offsetY.value = 0
}

function onWheel(e: WheelEvent) {
  // Smooth zoom: scroll up = zoom in, scroll down = zoom out
  const factor = e.deltaY < 0 ? 1.1 : 0.9
  zoomBy(factor)
}

function onDragStart(e: MouseEvent) {
  // Only start dragging on left-click, not on the toolbar
  if (e.button !== 0) return
  isDragging.value = true
  dragStartX = e.clientX
  dragStartY = e.clientY
  dragOriginX = offsetX.value
  dragOriginY = offsetY.value
}

function onDragMove(e: MouseEvent) {
  if (!isDragging.value) return
  offsetX.value = dragOriginX + (e.clientX - dragStartX)
  offsetY.value = dragOriginY + (e.clientY - dragStartY)
}

function onDragEnd() {
  isDragging.value = false
}

function close() {
  viewerVisible.value = false
}

// Reset transform each time the viewer opens so the user starts fresh.
watch(viewerVisible, (v) => {
  if (v) reset()
})

// Esc to close
function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Escape' && viewerVisible.value) close()
}
onMounted(() => window.addEventListener('keydown', onKeydown))
onUnmounted(() => window.removeEventListener('keydown', onKeydown))
</script>

<style scoped>
.mermaid-viewer {
  position: fixed;
  inset: 0;
  z-index: 3000;
  background: rgba(0, 0, 0, 0.85);
  display: flex;
  flex-direction: column;
  backdrop-filter: blur(4px);
}

.mermaid-viewer__toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 20px;
  background: rgba(22, 25, 34, 0.95);
  border-bottom: 1px solid var(--border-color);
  flex-shrink: 0;
}

.mermaid-viewer__title {
  font-size: 14px;
  font-weight: 600;
  color: var(--text-primary);
  display: flex;
  align-items: center;
  gap: 6px;
}

.mermaid-viewer__title::before {
  content: '📊';
  font-size: 16px;
}

.mermaid-viewer__tools {
  display: flex;
  align-items: center;
  gap: 6px;
}

.mermaid-viewer__btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 34px;
  height: 34px;
  border: 1px solid var(--border-color);
  border-radius: 7px;
  background: var(--bg-tertiary);
  color: var(--text-secondary);
  cursor: pointer;
  transition: all 0.15s;
}

.mermaid-viewer__btn:hover {
  background: var(--bg-hover);
  color: var(--text-primary);
  border-color: var(--accent-blue);
}

.mermaid-viewer__btn--close:hover {
  border-color: var(--accent-red);
  color: var(--accent-red);
}

.mermaid-viewer__zoom-label {
  font-size: 12px;
  font-weight: 600;
  color: var(--text-secondary);
  min-width: 48px;
  text-align: center;
  user-select: none;
}

.mermaid-viewer__canvas {
  flex: 1;
  overflow: hidden;
  display: flex;
  align-items: center;
  justify-content: center;
  position: relative;
  cursor: grab;
}

.mermaid-viewer__canvas.is-dragging {
  cursor: grabbing;
}

.mermaid-viewer__svg-wrap {
  transform-origin: center center;
  transition: transform 0.08s ease-out;
  /* Give the SVG a width context so its width="100%" resolves correctly */
  width: 90vw;
  display: flex;
  align-items: center;
  justify-content: center;
}

.mermaid-viewer__canvas.is-dragging .mermaid-viewer__svg-wrap {
  transition: none;
}

/* Let mermaid's natural width="100%" + height attributes work;
   only constrain with max-* to prevent overflow. */
.mermaid-viewer__svg-wrap :deep(svg) {
  max-width: 100%;
  max-height: 85vh;
}

/* Fade transition */
.mermaid-viewer-fade-enter-active,
.mermaid-viewer-fade-leave-active {
  transition: opacity 0.2s ease;
}

.mermaid-viewer-fade-enter-from,
.mermaid-viewer-fade-leave-to {
  opacity: 0;
}
</style>
