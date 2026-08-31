<script setup lang="ts">
import { computed } from 'vue'
import { Marked } from 'marked'
import { markedHighlight } from 'marked-highlight'
import hljs from 'highlight.js/lib/common'
import DOMPurify from 'dompurify'

const props = defineProps<{ content: string }>()

const marked = new Marked(
  markedHighlight({
    langPrefix: 'hljs language-',
    highlight(code: string, lang: string) {
      const language = hljs.getLanguage(lang) ? lang : 'plaintext'
      return hljs.highlight(code, { language, ignoreIllegals: true }).value
    },
  }),
)

const html = computed(() => {
  const raw = marked.parse(props.content || '', { async: false }) as string
  return DOMPurify.sanitize(raw, { ADD_ATTR: ['target', 'rel'] })
})
</script>

<template>
  <div class="fp-md" v-html="html" />
</template>

<style scoped>
.fp-md {
  width: 100%;
  align-self: flex-start;
  padding: 8px 4px 24px;
  color: var(--text-primary, #e6e9ef);
  font-size: 14px;
  line-height: 1.7;
}

/* Markdown 渲染内容的样式（深色主题，与项目 UI 对齐） */
.fp-md :deep(h1),
.fp-md :deep(h2),
.fp-md :deep(h3),
.fp-md :deep(h4) {
  color: var(--text-primary, #e6e9ef);
  margin: 16px 0 8px;
  font-weight: 700;
  line-height: 1.4;
}
.fp-md :deep(h1) { font-size: 22px; border-bottom: 1px solid var(--border-color, #2a3042); padding-bottom: 6px; }
.fp-md :deep(h2) { font-size: 18px; border-bottom: 1px solid var(--border-color, #2a3042); padding-bottom: 4px; }
.fp-md :deep(h3) { font-size: 16px; }
.fp-md :deep(h4) { font-size: 14px; }

.fp-md :deep(p) { margin: 8px 0; }
.fp-md :deep(ul),
.fp-md :deep(ol) { padding-left: 22px; margin: 8px 0; }
.fp-md :deep(li) { margin: 3px 0; }
.fp-md :deep(a) {
  color: var(--accent-blue, #4c7dff);
  text-decoration: none;
  border-bottom: 1px solid transparent;
}
.fp-md :deep(a:hover) { border-bottom-color: var(--accent-blue, #4c7dff); }

.fp-md :deep(blockquote) {
  margin: 10px 0;
  padding: 8px 14px;
  border-left: 3px solid var(--accent-blue, #4c7dff);
  background: var(--bg-tertiary, #1c2030);
  color: var(--text-secondary, #9ca3b5);
  border-radius: 0 6px 6px 0;
}

.fp-md :deep(code) {
  font-family: 'JetBrains Mono', 'Consolas', monospace;
  font-size: 12.5px;
  padding: 1px 6px;
  background: var(--bg-tertiary, #1c2030);
  border-radius: 4px;
  color: var(--accent-cyan, #06b6d4);
}

.fp-md :deep(pre) {
  margin: 12px 0;
  padding: 14px;
  background: var(--bg-secondary, #0d1117);
  border-radius: 8px;
  overflow-x: auto;
  border: 1px solid var(--border-color, #2a3042);
}
.fp-md :deep(pre code) {
  padding: 0;
  background: transparent;
  color: var(--text-primary, #e6e9ef);
  font-size: 12.5px;
}

.fp-md :deep(table) {
  border-collapse: collapse;
  margin: 12px 0;
  width: 100%;
  font-size: 13px;
}
.fp-md :deep(th),
.fp-md :deep(td) {
  border: 1px solid var(--border-color, #2a3042);
  padding: 6px 10px;
  text-align: left;
}
.fp-md :deep(th) {
  background: var(--bg-tertiary, #1c2030);
  color: var(--text-primary, #e6e9ef);
}

.fp-md :deep(hr) {
  border: none;
  border-top: 1px solid var(--border-color, #2a3042);
  margin: 16px 0;
}

.fp-md :deep(img) {
  max-width: 100%;
  border-radius: 6px;
  display: block;
  margin: 8px auto;
}
</style>