<script setup lang="ts">
import { getFileIcon, formatSize } from './filePreviewUtils'

defineProps<{ fileName: string; fileSize?: number; downloadPath: string }>()
const emit = defineEmits<{ (e: 'download'): void }>()
</script>

<template>
  <div class="fp-unsupported">
    <div class="fp-unsupported__icon">{{ getFileIcon(fileName) }}</div>
    <div class="fp-unsupported__name">{{ fileName }}</div>
    <div class="fp-unsupported__size">{{ formatSize(fileSize) }}</div>
    <div class="fp-unsupported__hint">暂不支持此格式预览</div>
    <a
      class="fp-unsupported__dl"
      :href="downloadPath"
      :download="fileName"
      @click="emit('download')"
    >⬇ 下载文件</a>
  </div>
</template>

<style scoped>
.fp-unsupported {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 10px;
  padding: 48px 24px;
  text-align: center;
  color: var(--text-primary, #e6e9ef);
}

.fp-unsupported__icon {
  font-size: 56px;
  line-height: 1;
  opacity: 0.8;
}

.fp-unsupported__name {
  font-size: 15px;
  font-weight: 600;
  word-break: break-all;
}

.fp-unsupported__size {
  font-size: 12px;
  color: var(--text-muted, #6b7180);
  font-family: 'JetBrains Mono', monospace;
}

.fp-unsupported__hint {
  font-size: 12.5px;
  color: var(--text-secondary, #9ca3b5);
}

.fp-unsupported__dl {
  margin-top: 6px;
  padding: 6px 18px;
  font-size: 13px;
  border-radius: 6px;
  border: 1px solid var(--accent-blue, #4c7dff);
  background: var(--accent-blue-soft, rgba(76, 125, 255, 0.15));
  color: var(--accent-blue, #4c7dff);
  text-decoration: none;
  cursor: pointer;
  transition: background 0.15s, color 0.15s;
}

.fp-unsupported__dl:hover {
  background: var(--accent-blue, #4c7dff);
  color: #fff;
}
</style>