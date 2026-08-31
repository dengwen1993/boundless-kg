<script setup lang="ts">
/**
 * DocxPreview — .docx / .doc 浏览器内联预览
 *
 * 基于 @vue-office/docx（与 @vue-office/pptx 同源，零后端依赖）：
 *   - 支持段落、表格、列表、页眉页脚等常见结构
 *   - 支持文字可选、缩放
 *   - 限制：不渲染嵌入字体 / 复杂宏 / SmartArt
 *
 * ⚠️ @vue-office/docx 内部只处理 string / ArrayBuffer，
 *    没有 Blob 分支——直接传 Blob 会被静默吞错导致黑屏。
 *    因此我们必须提前把 Blob 转成 ArrayBuffer 再传。
 */
import { ref, watch } from 'vue'
import VueOfficeDocx from '@vue-office/docx'

const props = defineProps<{
  /** Blob URL（仅在父组件没有传 fileBlob 时使用） */
  src?: string
  fileName: string
  fileSize?: number
  /** useFilePreview 中的原始 Blob（首选；优先用这个） */
  fileBlob?: Blob | null
}>()

/**
 * @vue-office/docx 只吃 string / ArrayBuffer；
 * 我们的策略：优先从 Blob 读取 ArrayBuffer，回退到 string (Blob URL)。
 */
const docxSrc = ref<string | ArrayBuffer | undefined>(undefined)
const renderError = ref('')
const isRendering = ref(true)

async function resolveSrc() {
  isRendering.value = true
  renderError.value = ''
  docxSrc.value = undefined
  try {
    if (props.fileBlob) {
      docxSrc.value = await props.fileBlob.arrayBuffer()
    } else if (props.src) {
      // 让组件内部自己去 fetch 字符串 URL
      docxSrc.value = props.src
    } else {
      renderError.value = '无法加载 DOCX 文件内容'
    }
  } catch (e) {
    renderError.value = e instanceof Error ? e.message : '读取文件失败'
  } finally {
    isRendering.value = false
  }
}

watch(
  () => [props.fileBlob, props.src] as const,
  () => resolveSrc(),
  { immediate: true },
)
</script>

<template>
  <div class="fp-docx">
    <div v-if="isRendering" class="fp-docx__status">
      <div class="fp-docx__spinner" />
      <span>正在加载 {{ fileName }} …</span>
    </div>
    <div v-else-if="renderError" class="fp-docx__error">
      <div class="fp-docx__error-icon">⚠️</div>
      <div class="fp-docx__error-title">加载失败</div>
      <div class="fp-docx__error-msg">{{ renderError }}</div>
    </div>
    <div v-else-if="docxSrc" class="fp-docx__viewer">
      <VueOfficeDocx
        :src="docxSrc"
        :style="{ width: '100%', height: '100%' }"
      />
    </div>
  </div>
</template>

<style scoped>
.fp-docx {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
  background: var(--bg-primary, #0f1117);
}

/* ── Loading ── */
.fp-docx__status {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 12px;
  height: 100%;
  color: var(--text-secondary, #9ca3b5);
  font-size: 13px;
}

.fp-docx__spinner {
  width: 28px;
  height: 28px;
  border-radius: 50%;
  border: 3px solid var(--border-color, #2a3042);
  border-top-color: var(--accent-blue, #4c7dff);
  animation: fp-docx-spin 0.8s linear infinite;
}

@keyframes fp-docx-spin {
  to { transform: rotate(360deg); }
}

/* ── Error ── */
.fp-docx__error {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  height: 100%;
  padding: 24px;
  text-align: center;
  color: var(--text-secondary, #9ca3b5);
}

.fp-docx__error-icon {
  font-size: 32px;
}

.fp-docx__error-title {
  font-size: 14px;
  font-weight: 700;
  color: var(--accent-red-text, #ff8b8b);
}

.fp-docx__error-msg {
  font-size: 12.5px;
  word-break: break-all;
  max-width: 480px;
}

/* ── Viewer ── */
.fp-docx__viewer {
  flex: 1;
  min-height: 0;
  border: 1px solid var(--border-color, #2a3042);
  border-radius: 10px;
  overflow: hidden;
  background: #fff;
}

/* 让 @vue-office/docx 内部容器自适应撑满 */
.fp-docx__viewer :deep(.vue-office-docx) {
  width: 100%;
  height: 100%;
  overflow: auto;
  background: #fff;
}
</style>
