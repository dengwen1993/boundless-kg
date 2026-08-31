<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{ content: string }>()

const rows = computed(() => {
  const lines = props.content.split(/\r?\n/).filter((l) => l.trim() !== '')
  return lines.map((line) => {
    const cells: string[] = []
    let cur = ''
    let inQuote = false
    for (let i = 0; i < line.length; i++) {
      const ch = line[i]
      if (ch === '"') {
        if (inQuote && line[i + 1] === '"') { cur += '"'; i++ }
        else inQuote = !inQuote
      } else if (ch === ',' && !inQuote) {
        cells.push(cur); cur = ''
      } else {
        cur += ch
      }
    }
    cells.push(cur)
    return cells
  })
})

const headers = computed(() => rows.value[0] ?? [])
const body = computed(() => rows.value.slice(1))
</script>

<template>
  <div class="fp-csv">
    <table class="fp-csv__table">
      <thead>
        <tr>
          <th v-for="(h, i) in headers" :key="i">{{ h }}</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="(row, ri) in body" :key="ri">
          <td v-for="(cell, ci) in body[0]?.length ? row : row" :key="ci">{{ cell }}</td>
        </tr>
      </tbody>
    </table>
  </div>
</template>

<style scoped>
.fp-csv {
  width: 100%;
  height: 100%;
  overflow: auto;
  padding: 16px;
  box-sizing: border-box;
  background: var(--bg-secondary, #161922);
  border-radius: 8px;
}

.fp-csv__table {
  border-collapse: collapse;
  width: 100%;
  font-size: 13px;
  color: var(--text-primary, #e6e9ef);
}

.fp-csv__table th,
.fp-csv__table td {
  border: 1px solid var(--border-color, #2a3042);
  padding: 6px 12px;
  white-space: nowrap;
  text-align: left;
}

.fp-csv__table thead th {
  background: var(--bg-tertiary, #1c2030);
  font-weight: 600;
  color: var(--text-primary, #e6e9ef);
  position: sticky;
  top: 0;
  z-index: 1;
}

.fp-csv__table tbody tr:nth-child(even) {
  background: var(--bg-tertiary, #1c2030);
}

.fp-csv__table tbody tr:hover {
  background: var(--bg-hover, #242938);
}
</style>