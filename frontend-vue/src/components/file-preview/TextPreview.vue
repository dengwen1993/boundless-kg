<script setup lang="ts">
import { computed } from 'vue'
import hljs from 'highlight.js/lib/core'
import 'highlight.js/styles/github-dark.css'
// 按需注册常用语言
import javascript from 'highlight.js/lib/languages/javascript'
import typescript from 'highlight.js/lib/languages/typescript'
import python from 'highlight.js/lib/languages/python'
import java from 'highlight.js/lib/languages/java'
import kotlin from 'highlight.js/lib/languages/kotlin'
import scala from 'highlight.js/lib/languages/scala'
import go from 'highlight.js/lib/languages/go'
import rust from 'highlight.js/lib/languages/rust'
import c from 'highlight.js/lib/languages/c'
import cpp from 'highlight.js/lib/languages/cpp'
import csharp from 'highlight.js/lib/languages/csharp'
import php from 'highlight.js/lib/languages/php'
import ruby from 'highlight.js/lib/languages/ruby'
import swift from 'highlight.js/lib/languages/swift'
import sql from 'highlight.js/lib/languages/sql'
import graphql from 'highlight.js/lib/languages/graphql'
import css from 'highlight.js/lib/languages/css'
import scss from 'highlight.js/lib/languages/scss'
import less from 'highlight.js/lib/languages/less'
import bash from 'highlight.js/lib/languages/bash'
import json from 'highlight.js/lib/languages/json'
import yaml from 'highlight.js/lib/languages/yaml'
import xml from 'highlight.js/lib/languages/xml'
import ini from 'highlight.js/lib/languages/ini'
import plaintext from 'highlight.js/lib/languages/plaintext'
import { getCodeLanguage } from './filePreviewUtils'

hljs.registerLanguage('javascript', javascript)
hljs.registerLanguage('typescript', typescript)
hljs.registerLanguage('python', python)
hljs.registerLanguage('java', java)
hljs.registerLanguage('kotlin', kotlin)
hljs.registerLanguage('scala', scala)
hljs.registerLanguage('go', go)
hljs.registerLanguage('rust', rust)
hljs.registerLanguage('c', c)
hljs.registerLanguage('cpp', cpp)
hljs.registerLanguage('csharp', csharp)
hljs.registerLanguage('php', php)
hljs.registerLanguage('ruby', ruby)
hljs.registerLanguage('swift', swift)
hljs.registerLanguage('sql', sql)
hljs.registerLanguage('graphql', graphql)
hljs.registerLanguage('css', css)
hljs.registerLanguage('scss', scss)
hljs.registerLanguage('less', less)
hljs.registerLanguage('bash', bash)
hljs.registerLanguage('json', json)
hljs.registerLanguage('yaml', yaml)
hljs.registerLanguage('xml', xml)
hljs.registerLanguage('ini', ini)
hljs.registerLanguage('plaintext', plaintext)

const props = defineProps<{ content: string; fileName?: string }>()

const highlightedHtml = computed(() => {
  const lang = props.fileName ? getCodeLanguage(props.fileName) : 'plaintext'
  try {
    const result = hljs.highlight(props.content, { language: lang, ignoreIllegals: true })
    return result.value
  } catch {
    return hljs.highlight(props.content, { language: 'plaintext', ignoreIllegals: true }).value
  }
})
</script>

<template>
  <div class="fp-text">
    <pre class="fp-text__pre"><code class="hljs" v-html="highlightedHtml" /></pre>
  </div>
</template>

<style scoped>
.fp-text {
  width: 100%;
  align-self: flex-start;
  background: var(--bg-secondary, #0d1117);
  border-radius: 8px;
}

.fp-text__pre {
  margin: 0;
  font-family: 'JetBrains Mono', 'Consolas', 'Monaco', monospace;
  font-size: 12.5px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-all;
  background: var(--bg-secondary, #0d1117);
  border-radius: 8px;
  padding: 16px;
  overflow-x: auto;
}

.fp-text__pre code {
  font-family: inherit;
  font-size: inherit;
  background: transparent;
  padding: 0;
}
</style>