<template>
  <div class="pac">
    <!-- 日期标记: 🕒 2026-08-02 0/1 完成 -->
    <div v-for="(m, i) in parsed.metaLines" :key="`m-${i}`" class="pac__meta">
      <span class="pac__meta-icon">🕒</span>
      <span class="pac__meta-date">{{ m.date }}</span>
      <span v-if="m.progress" class="pac__meta-progress">{{ m.progress }}</span>
    </div>

    <!-- 开场白: 时段列表之前的自由文本 -->
    <div v-if="parsed.intro" class="pac__intro">{{ parsed.intro }}</div>

    <!-- 找不到结构时, 退化为纯文本 (intro / meta / slots 三段已分别覆盖了常见场景, 这里只在三段全部解析失败时兜底) -->
    <div v-if="!parsed.intro && parsed.slots.length === 0 && parsed.metaLines.length === 0" class="pac__raw">{{ raw }}</div>

    <!-- 时段卡片 -->
    <div v-for="(slot, i) in parsed.slots" :key="`s-${i}`" class="pac__slot">
      <div class="pac__slot-head">
        <span class="pac__time">{{ slot.time }}</span>
        <span class="pac__topic">{{ slot.topic }}</span>
        <span v-for="(tag, ti) in slot.tags" :key="`t-${ti}`" class="pac__tag">
          {{ tag }}
        </span>
      </div>
      <ol v-if="slot.steps.length" class="pac__steps">
        <li v-for="(step, si) in slot.steps" :key="`st-${si}`" class="pac__step">
          <span class="pac__step-num">{{ step.num }}</span>
          <span class="pac__step-text">{{ step.text }}</span>
        </li>
      </ol>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{ content: string }>()

interface ParsedStep { num: string; text: string }
interface ParsedSlot {
  time: string
  topic: string
  tags: string[]
  steps: ParsedStep[]
}
interface ParsedMeta { date: string; progress: string }
interface ParsedContent {
  intro: string
  metaLines: ParsedMeta[]
  slots: ParsedSlot[]
}

const CIRCLED = ['①', '②', '③', '④', '⑤', '⑥', '⑦', '⑧', '⑨', '⑩']
const hasCircled = (s: string) => CIRCLED.some(c => s.includes(c))

function splitSteps(text: string): ParsedStep[] {
  if (!text) return []
  if (hasCircled(text)) {
    const parts = text.split(/([①②③④⑤⑥⑦⑧⑨⑩])/).filter(Boolean)
    const out: ParsedStep[] = []
    for (let i = 0; i < parts.length; i += 2) {
      const num = parts[i]
      const cleaned = (parts[i + 1] || '')
        .replace(/^[;；,，\s]+/, '')
        .replace(/[;；]\s*$/, '')
        .trim()
      if (cleaned) out.push({ num, text: cleaned })
    }
    return out
  }
  const parts = text.split(/(\d+\.\s*)/).filter(Boolean)
  const out: ParsedStep[] = []
  for (let i = 0; i < parts.length; i += 2) {
    const num = parts[i].trim()
    const cleaned = (parts[i + 1] || '').trim()
    if (cleaned) out.push({ num, text: cleaned })
  }
  return out
}

function extractMeta(content: string): { meta: ParsedMeta[]; rest: string } {
  const meta: ParsedMeta[] = []
  const metaRe = /🕒\s*(\d{4}-\d{2}-\d{2})(?:\s+(\d+\/\d+\s*完成|\d+\/\d+))?/g
  const rest = content.replace(metaRe, (_m, date, progress) => {
    meta.push({ date, progress: (progress || '').trim() })
    return ''
  })
  return { meta, rest: rest.trim() }
}

function parseContent(raw: string): ParsedContent {
  const { meta, rest } = extractMeta(raw)
  if (!rest) return { intro: '', metaLines: meta, slots: [] }

  // 匹配 "🕐 HH:MM--HH:MM |" 或  "HH:MM--HH:MM |"
  const slotRe = /(🕐\s*)?(\d{1,2}:\d{2}\s*--\s*\d{1,2}:\d{2})\s*\|/g
  const matches: { idx: number; len: number; time: string }[] = []
  let m: RegExpExecArray | null
  while ((m = slotRe.exec(rest)) !== null) {
    matches.push({
      idx: m.index,
      len: m[0].length,
      time: m[2].replace(/\s+/g, ''),
    })
  }

  if (matches.length === 0) {
    return { intro: rest, metaLines: meta, slots: [] }
  }

  const intro = rest.slice(0, matches[0].idx).trim()
  const slots: ParsedSlot[] = []
  for (let i = 0; i < matches.length; i++) {
    const start = matches[i].idx + matches[i].len
    const end = i + 1 < matches.length ? matches[i + 1].idx : rest.length
    const body = rest.slice(start, end).trim()

    // 第一个 ":" 是 topic 与 steps 的分界 (后续 ":" 保留在 steps 中)
    const colonIdx = body.indexOf(':')
    let head = body
    let stepText = ''
    if (colonIdx >= 0) {
      head = body.slice(0, colonIdx).trim()
      stepText = body.slice(colonIdx + 1).trim()
    }

    const tags: string[] = []
    const tagRe = /【([^】]+)】/g
    let tm: RegExpExecArray | null
    while ((tm = tagRe.exec(head)) !== null) tags.push(tm[1].trim())
    const topic = head.split('【')[0].trim()

    slots.push({
      time: matches[i].time,
      topic,
      tags,
      steps: splitSteps(stepText),
    })
  }
  return { intro, metaLines: meta, slots }
}

const raw = computed(() => props.content || '')
const parsed = computed(() => parseContent(raw.value))
</script>

<style scoped>
.pac {
  display: flex;
  flex-direction: column;
  gap: 8px;
  font-size: 12.5px;
  line-height: 1.55;
  color: var(--text-secondary);
  width: 100%;
  min-width: 0;
}

/* 日期行 */
.pac__meta {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 11.5px;
  color: var(--text-muted);
  padding-bottom: 4px;
  border-bottom: 1px dashed var(--border-color);
}
.pac__meta-icon { font-size: 12px; }
.pac__meta-date { color: var(--text-secondary); font-weight: 500; }
.pac__meta-progress {
  margin-left: auto;
  font-size: 11px;
  padding: 1px 6px;
  border-radius: 4px;
  background: var(--bg-hover);
  color: var(--text-muted);
}

/* 开场白 */
.pac__intro {
  font-size: 12px;
  color: var(--text-muted);
  font-style: italic;
  padding: 2px 0;
}

/* 找不到结构时 */
.pac__raw {
  font-size: 12.5px;
  color: var(--text-secondary);
  white-space: pre-wrap;
  word-break: break-word;
}

/* 时段块 */
.pac__slot {
  background: rgba(76, 125, 255, 0.05);
  border: 1px solid var(--border-color);
  border-left: 2px solid var(--accent-blue);
  border-radius: 6px;
  padding: 6px 10px 8px;
}
.pac__slot-head {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px;
  margin-bottom: 4px;
}
.pac__time {
  font-size: 11.5px;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
  color: var(--accent-blue);
  background: rgba(76, 125, 255, 0.12);
  padding: 1px 6px;
  border-radius: 4px;
  white-space: nowrap;
}
.pac__topic {
  font-size: 12.5px;
  font-weight: 600;
  color: var(--text-primary);
}
.pac__tag {
  font-size: 11px;
  color: var(--text-secondary);
  background: var(--bg-hover);
  padding: 1px 6px;
  border-radius: 3px;
  border: 1px solid var(--border-color);
  white-space: nowrap;
}

/* 步骤列表 */
.pac__steps {
  list-style: none;
  margin: 4px 0 0;
  padding: 0 0 0 4px;
  display: flex;
  flex-direction: column;
  gap: 3px;
}
.pac__step {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  font-size: 12px;
  line-height: 1.6;
  color: var(--text-secondary);
}
.pac__step-num {
  flex-shrink: 0;
  min-width: 18px;
  text-align: center;
  font-size: 11.5px;
  color: var(--accent-blue);
  font-weight: 600;
  font-variant-numeric: tabular-nums;
  padding-top: 1px;
}
.pac__step-text {
  flex: 1;
  min-width: 0;
  word-break: break-word;
}
</style>
