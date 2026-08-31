<script setup lang="ts">
/**
 * FilePreviewDialog — 通用文件预览抽屉/对话框
 * 根据文件扩展名 / mime 派发到合适的预览组件：
 *   image / audio / video / markdown / html / csv / text / pdf / pptx / unsupported
 *
 * 用法：
 *   <FilePreviewDialog
 *     v-model="visible"
 *     :file-name="fileName"
 *     :download-path="downloadPath"
 *     :file-size="size"
 *   />
 */
import { computed, watch } from 'vue'
import { useFilePreview, type PreviewFile } from './useFilePreview'
import { getPreviewType, getFileIcon, formatSize } from './filePreviewUtils'
import TextPreview from './TextPreview.vue'
import MarkdownPreview from './MarkdownPreview.vue'
import HtmlPreview from './HtmlPreview.vue'
import CsvPreview from './CsvPreview.vue'
import ImagePreview from './ImagePreview.vue'
import AudioPreview from './AudioPreview.vue'
import VideoPreview from './VideoPreview.vue'
import PdfPreview from './PdfPreview.vue'
import PptxPreview from './PptxPreview.vue'
import DocxPreview from './DocxPreview.vue'
import MermaidPreview from './MermaidPreview.vue'
import UnsupportedPreview from './UnsupportedPreview.vue'

const props = defineProps<{
  modelValue: boolean
  fileName: string
  downloadPath: string
  fileType?: string
  fileSize?: number
  /** 'drawer' (右侧抽屉，默认) 或 'dialog' (居中弹窗) */
  variant?: 'drawer' | 'dialog'
  /** 自定义抽屉宽度 */
  width?: string
}>()

const emit = defineEmits<{
  'update:modelValue': [val: boolean]
}>()

const visible = computed({
  get: () => props.modelValue,
  set: (v: boolean) => emit('update:modelValue', v),
})

const previewFile = computed<PreviewFile>(() => ({
  fileName: props.fileName,
  fileSize: props.fileSize,
  fileType: props.fileType ?? '',
  downloadPath: props.downloadPath,
}))

const previewType = computed(() =>
  getPreviewType(props.fileType ?? '', props.fileName),
)

const { loading, error, textContent, imageUrl, mediaUrl, fileBlob, load, reset } = useFilePreview()

watch(
  () => [props.modelValue, props.fileName, props.downloadPath],
  ([open]) => {
    if (open) {
      load(previewFile.value)
    } else {
      reset()
    }
  },
  { immediate: true },
)

function close() {
  visible.value = false
}

const variant = computed(() => props.variant ?? 'drawer')
const drawerWidth = computed(() => props.width ?? '720px')
const fileIcon = computed(() => getFileIcon(props.fileName))
const sizeLabel = computed(() => formatSize(props.fileSize))
</script>

<template>
  <el-drawer
    v-if="variant === 'drawer'"
    v-model="visible"
    direction="rtl"
    :size="drawerWidth"
    :with-header="false"
    :destroy-on-close="false"
    class="fp-drawer"
  >
    <div class="fp-shell">
      <header class="fp-shell__header">
        <div class="fp-shell__title">
          <span class="fp-shell__icon">{{ fileIcon }}</span>
          <span class="fp-shell__name" :title="fileName">{{ fileName }}</span>
        </div>
        <div class="fp-shell__meta">
          <span class="fp-shell__size">{{ sizeLabel }}</span>
          <a
            class="fp-shell__dl"
            :href="downloadPath"
            :download="fileName"
            title="下载到本地"
          >⬇ 下载</a>
          <button class="fp-shell__close" @click="close" aria-label="关闭">✕</button>
        </div>
      </header>

      <div class="fp-shell__body">
        <div v-if="loading" class="fp-shell__loading">
          <div class="fp-shell__spinner" />
          <span>正在加载 {{ fileName }} …</span>
        </div>
        <div v-else-if="error" class="fp-shell__error">
          <span class="fp-shell__error-icon">⚠️</span>
          <div>
            <div class="fp-shell__error-title">加载失败</div>
            <div class="fp-shell__error-msg">{{ error }}</div>
          </div>
        </div>

        <template v-else>
          <ImagePreview v-if="previewType === 'image'" :src="imageUrl" :alt="fileName" />
          <AudioPreview v-else-if="previewType === 'audio'" :src="mediaUrl" />
          <VideoPreview v-else-if="previewType === 'video'" :src="mediaUrl" />
          <MarkdownPreview v-else-if="previewType === 'markdown'" :content="textContent" />
          <HtmlPreview v-else-if="previewType === 'html'" :content="textContent" />
          <CsvPreview v-else-if="previewType === 'csv'" :content="textContent" />
          <TextPreview v-else-if="previewType === 'text'" :content="textContent" :file-name="fileName" />
          <PdfPreview v-else-if="previewType === 'pdf'" :src="mediaUrl" />
          <PptxPreview
            v-else-if="previewType === 'pptx'"
            :src="mediaUrl"
            :file-name="fileName"
            :file-size="fileSize"
            :file-blob="fileBlob"
          />
          <DocxPreview
            v-else-if="previewType === 'docx'"
            :src="mediaUrl"
            :file-name="fileName"
            :file-size="fileSize"
            :file-blob="fileBlob"
          />
          <MermaidPreview
            v-else-if="previewType === 'mermaid'"
            :content="textContent"
            :file-name="fileName"
          />
          <UnsupportedPreview
            v-else
            :file-name="fileName"
            :file-size="fileSize"
            :download-path="downloadPath"
          />
        </template>
      </div>
    </div>
  </el-drawer>

  <el-dialog
    v-else
    v-model="visible"
    width="780px"
    :show-close="false"
    align-center
    class="fp-dialog"
  >
    <div class="fp-shell">
      <header class="fp-shell__header">
        <div class="fp-shell__title">
          <span class="fp-shell__icon">{{ fileIcon }}</span>
          <span class="fp-shell__name" :title="fileName">{{ fileName }}</span>
        </div>
        <div class="fp-shell__meta">
          <span class="fp-shell__size">{{ sizeLabel }}</span>
          <a class="fp-shell__dl" :href="downloadPath" :download="fileName">⬇ 下载</a>
          <button class="fp-shell__close" @click="close" aria-label="关闭">✕</button>
        </div>
      </header>

      <div class="fp-shell__body">
        <div v-if="loading" class="fp-shell__loading">
          <div class="fp-shell__spinner" />
          <span>正在加载 {{ fileName }} …</span>
        </div>
        <div v-else-if="error" class="fp-shell__error">
          <span class="fp-shell__error-icon">⚠️</span>
          <div>
            <div class="fp-shell__error-title">加载失败</div>
            <div class="fp-shell__error-msg">{{ error }}</div>
          </div>
        </div>

        <template v-else>
          <ImagePreview v-if="previewType === 'image'" :src="imageUrl" :alt="fileName" />
          <AudioPreview v-else-if="previewType === 'audio'" :src="mediaUrl" />
          <VideoPreview v-else-if="previewType === 'video'" :src="mediaUrl" />
          <MarkdownPreview v-else-if="previewType === 'markdown'" :content="textContent" />
          <HtmlPreview v-else-if="previewType === 'html'" :content="textContent" />
          <CsvPreview v-else-if="previewType === 'csv'" :content="textContent" />
          <TextPreview v-else-if="previewType === 'text'" :content="textContent" :file-name="fileName" />
          <PdfPreview v-else-if="previewType === 'pdf'" :src="mediaUrl" />
          <PptxPreview
            v-else-if="previewType === 'pptx'"
            :src="mediaUrl"
            :file-name="fileName"
            :file-size="fileSize"
            :file-blob="fileBlob"
          />
          <DocxPreview
            v-else-if="previewType === 'docx'"
            :src="mediaUrl"
            :file-name="fileName"
            :file-size="fileSize"
            :file-blob="fileBlob"
          />
          <MermaidPreview
            v-else-if="previewType === 'mermaid'"
            :content="textContent"
            :file-name="fileName"
          />
          <UnsupportedPreview
            v-else
            :file-name="fileName"
            :file-size="fileSize"
            :download-path="downloadPath"
          />
        </template>
      </div>
    </div>
  </el-dialog>
</template>

<style scoped>
.fp-shell {
  display: flex;
  flex-direction: column;
  height: 100%;
  background: var(--bg-primary, #0f1117);
  color: var(--text-primary, #e6e9ef);
}

/* ── Header ── */
.fp-shell__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 14px 20px;
  border-bottom: 1px solid var(--border-color, #2a3042);
  background: var(--bg-secondary, #161922);
  gap: 12px;
  flex-shrink: 0;
}

.fp-shell__title {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
  flex: 1;
}

.fp-shell__icon {
  font-size: 22px;
  line-height: 1;
}

.fp-shell__name {
  font-size: 14.5px;
  font-weight: 600;
  color: var(--text-primary, #e6e9ef);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  word-break: break-all;
}

.fp-shell__meta {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
}

.fp-shell__size {
  font-size: 12px;
  color: var(--text-muted, #6b7180);
  font-family: 'JetBrains Mono', monospace;
}

.fp-shell__dl {
  padding: 4px 12px;
  font-size: 12.5px;
  border-radius: 6px;
  border: 1px solid var(--accent-blue, #4c7dff);
  background: var(--accent-blue-soft, rgba(76, 125, 255, 0.15));
  color: var(--accent-blue, #4c7dff);
  text-decoration: none;
  transition: background 0.15s, color 0.15s;
}

.fp-shell__dl:hover {
  background: var(--accent-blue, #4c7dff);
  color: #fff;
}

.fp-shell__close {
  width: 28px;
  height: 28px;
  border: 1px solid var(--border-color, #2a3042);
  background: var(--bg-tertiary, #1c2030);
  color: var(--text-secondary, #9ca3b5);
  border-radius: 6px;
  cursor: pointer;
  font-size: 14px;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: background 0.15s, color 0.15s;
  /* 防止 PPTX / 大型预览组件溢出遮挡 header 交互 */
  position: relative;
  z-index: 10;
  pointer-events: auto;
}

.fp-shell__header {
  /* 让 header 永远在最上层，挡住任何溢出内容 */
  position: relative;
  z-index: 5;
}

.fp-shell__close:hover {
  background: var(--bg-hover, #242938);
  color: var(--text-primary, #e6e9ef);
}

/* ── Body ── */
.fp-shell__body {
  flex: 1;
  overflow: auto;
  padding: 16px;
  display: flex;
  flex-direction: column;
  align-items: stretch;
  min-height: 0;
}

.fp-shell__loading {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 12px;
  height: 100%;
  color: var(--text-secondary, #9ca3b5);
  font-size: 13px;
}

.fp-shell__spinner {
  width: 28px;
  height: 28px;
  border-radius: 50%;
  border: 3px solid var(--border-color, #2a3042);
  border-top-color: var(--accent-blue, #4c7dff);
  animation: fp-spin 0.8s linear infinite;
}

@keyframes fp-spin {
  to { transform: rotate(360deg); }
}

.fp-shell__error {
  display: flex;
  gap: 12px;
  padding: 24px;
  margin: auto;
  border: 1px solid var(--accent-red-edge, rgba(239, 68, 68, 0.45));
  background: var(--accent-red-soft, rgba(239, 68, 68, 0.15));
  border-radius: 8px;
  color: var(--accent-red-text, #ff8b8b);
  max-width: 480px;
}

.fp-shell__error-icon {
  font-size: 22px;
  line-height: 1.2;
}

.fp-shell__error-title {
  font-size: 14px;
  font-weight: 700;
  margin-bottom: 2px;
}

.fp-shell__error-msg {
  font-size: 12.5px;
  color: var(--text-secondary, #9ca3b5);
  word-break: break-all;
}

/* ── Drawer / Dialog overrides ── */
.fp-drawer :deep(.el-drawer__header) {
  display: none;
}

.fp-drawer :deep(.el-drawer__body) {
  padding: 0;
  background: var(--bg-primary, #0f1117);
}

.fp-dialog :deep(.el-dialog__header) {
  display: none;
}

.fp-dialog :deep(.el-dialog__body) {
  padding: 0;
  background: var(--bg-primary, #0f1117);
  border-radius: 12px;
  overflow: hidden;
}
</style>