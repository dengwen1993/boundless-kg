<template>
  <el-dialog
    :model-value="visible"
    :title="dialogTitle"
    width="560px"
    @update:model-value="$emit('update:visible', $event)"
    @open="onOpen"
  >
    <!-- 源节点 -->
    <div class="src-row">
      <span class="src-row__label">源节点</span>
      <el-tag
        v-if="sourceNode"
        :type="tierTagType(sourceNode.level)"
        size="large"
        disable-transitions
        effect="dark"
      >
        {{ sourceNode.name }}
      </el-tag>
      <el-tag v-else type="info" size="large" effect="plain">未选择</el-tag>
      <span v-if="sourceNode" class="src-row__tier">{{ tierLabel(sourceNode.level) }}</span>
    </div>

    <!-- 关系类型 -->
    <div class="section">
      <div class="section__title">关系类型</div>
      <el-select
        v-model="relation"
        size="default"
        class="relation-select"
        placeholder="选择一种关系"
      >
        <el-option
          v-for="opt in relationOptions"
          :key="opt.value"
          :value="opt.value"
          :label="opt.label"
        >
          <span class="opt-row">
            <span
              class="opt-swatch"
              :style="{ background: opt.color }"
            ></span>
            <span class="opt-label">{{ opt.label }}</span>
            <code class="opt-code">{{ opt.value }}</code>
          </span>
        </el-option>
      </el-select>
      <p class="hint">
        <el-icon><InfoFilled /></el-icon>
        仅支持概念 ↔ 概念；has_note / has_resource / has_plan 等结构关系由系统派生。
      </p>
    </div>

    <!-- 目标节点 -->
    <div class="section">
      <div class="section__title">
        目标节点
        <span v-if="pendingTarget" class="section__count">{{ pendingTarget }}</span>
      </div>
      <el-input
        v-model="filterText"
        size="small"
        clearable
        placeholder="搜索节点名…"
        class="filter-row__search"
        :prefix-icon="Search"
      />
      <div class="picker">
        <div v-if="filteredCandidates.length === 0" class="empty-hint empty-hint--picker">
          没有可选节点
        </div>
        <div
          v-for="node in filteredCandidates"
          :key="node.name"
          class="picker__item"
          :class="{ 'picker__item--active': pendingTarget === node.name }"
          @click="pendingTarget = node.name"
        >
          <span class="picker__dot" :style="{ background: tierColor(node.level) }"></span>
          <span class="picker__name">{{ node.name }}</span>
          <span class="picker__tier">{{ tierLabel(node.level) }}</span>
        </div>
      </div>
    </div>

    <!-- 备注 -->
    <div class="section">
      <div class="section__title">备注（可选）</div>
      <el-input
        v-model="evidence"
        type="textarea"
        :rows="2"
        placeholder="为什么加这条关联？"
      />
    </div>

    <template #footer>
      <div class="footer-row">
        <span class="footer-row__summary">
          {{ summaryText }}
        </span>
        <div class="footer-row__actions">
          <el-button @click="$emit('update:visible', false)">取消</el-button>
          <el-button
            type="primary"
            :loading="store.saving"
            :disabled="!canSave"
            @click="onSave"
          >
            添加
          </el-button>
        </div>
      </div>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { InfoFilled, Search } from '@element-plus/icons-vue'
import { useAssociationsStore } from '@/stores/associations'
import type { ConceptNode, RelationType } from '@/api/associations'

const props = defineProps<{
  visible: boolean
  sourceNode: ConceptNode | null
}>()

const emit = defineEmits<{
  'update:visible': [val: boolean]
  saved: []
}>()

const store = useAssociationsStore()

// ── 与 AssociationsView 颜色 / 标签对齐 ──
const RELATION_COLOR: Record<RelationType, string> = {
  part_of: '#6b7180',
  prerequisite_of: '#ef4444',
  enables: '#84cc16',
  similar_to: '#7c5cff',
  contrasts_with: '#e879f9',
  applies_to: '#22d3a5',
  derived_from: '#14b8a6',
  related_to: '#9ca3b5',
  has_note: '#4c7dff',
  has_resource: '#06b6d4',
  has_plan: '#f59e0b',
  cites: '#38bdf8',
  references: '#a78bfa',
}

const RELATION_LABEL: Record<RelationType, string> = {
  part_of: '属于',
  prerequisite_of: '先学这个',
  enables: '学完能搞定',
  similar_to: '类似',
  contrasts_with: '对比',
  applies_to: '应用到',
  derived_from: '由此衍生',
  related_to: '相关',
  has_note: '有笔记',
  has_resource: '有资料',
  has_plan: '有计划',
  cites: '引用',
  references: '引用了',
}

// 仅展示用户可手动选择的 8 种概念关系；has_*/cites/references 由系统派生
const USER_PICKABLE: RelationType[] = [
  'part_of',
  'prerequisite_of',
  'enables',
  'similar_to',
  'contrasts_with',
  'applies_to',
  'derived_from',
  'related_to',
]

const relationOptions = computed(() =>
  USER_PICKABLE.map((v) => ({
    value: v,
    label: RELATION_LABEL[v],
    color: RELATION_COLOR[v],
  })),
)

const sourceName = computed(() => props.sourceNode?.name ?? '')
const dialogTitle = computed(() =>
  sourceName.value ? `为「${sourceName.value}」添加关联` : '添加关联',
)

const relation = ref<RelationType>('related_to')
const pendingTarget = ref<string>('')
const evidence = ref<string>('')
const filterText = ref<string>('')

function onOpen() {
  relation.value = 'related_to'
  pendingTarget.value = ''
  evidence.value = ''
  filterText.value = ''
}

watch(
  () => [props.visible, props.sourceNode?.name],
  ([vis]) => { if (vis) onOpen() },
)

// ── 候选目标：所有概念节点 - 源节点自身 ──
const allConcepts = computed<ConceptNode[]>(() => store.conceptList)
const filteredCandidates = computed<ConceptNode[]>(() => {
  const term = filterText.value.trim().toLowerCase()
  return allConcepts.value
    .filter((n) => n.name !== sourceName.value)
    .filter((n) => !term || n.name.toLowerCase().includes(term))
    .slice()
    .sort((a, b) => a.level - b.level || a.name.localeCompare(b.name, 'zh-Hans-CN'))
})

const canSave = computed(() => !!sourceName.value && !!pendingTarget.value)
const summaryText = computed(() => {
  if (!sourceName.value) return '请先选择源节点'
  if (!pendingTarget.value) return '请选择目标节点'
  const r = RELATION_LABEL[relation.value]
  return `${sourceName.value} --${r}--> ${pendingTarget.value}`
})

// ── 层级标签 / 颜色 ──
const TIER_TAG: Record<string, 'warning' | 'primary' | 'info' | 'success'> = {
  L0: 'warning',
  L1: 'primary',
  L2: 'info',
  L3: 'success',
  leaf: 'success',
}
function tierTagType(level: number) {
  if (level === 0) return 'warning'
  if (level === 1) return 'primary'
  if (level === 2) return 'info'
  return 'success'
}
function tierLabel(level: number): string {
  if (level === 0) return 'L0 根'
  if (level === 1) return 'L1 主干'
  if (level === 2) return 'L2'
  return 'L3+ 叶子'
}
function tierColor(level: number): string {
  if (level === 0) return '#f59e0b'
  if (level === 1) return '#4c7dff'
  if (level === 2) return '#8b5cf6'
  if (level === 3) return '#ec4899'
  return '#22d3a5'
}

async function onSave() {
  if (!canSave.value) return
  try {
    await store.addManualAssociation({
      source: sourceName.value,
      target: pendingTarget.value,
      relation: relation.value,
      evidence: evidence.value || '',
    })
    ElMessage.success(
      `已添加：${sourceName.value} --${RELATION_LABEL[relation.value]}--> ${pendingTarget.value}`,
    )
    emit('update:visible', false)
    emit('saved')
  } catch (e: any) {
    ElMessage.error(e?.message || '保存失败')
  }
}
</script>

<style scoped>
.src-row {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 4px 0 14px;
  border-bottom: 1px dashed var(--border-color);
  margin-bottom: 14px;
}
.src-row__label {
  font-size: 12px;
  color: var(--text-muted);
}
.src-row__tier {
  font-size: 11px;
  color: var(--text-muted);
  font-family: ui-monospace, SFMono-Regular, monospace;
}

.section {
  margin-bottom: 16px;
}
.section__title {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  font-weight: 600;
  color: var(--text-secondary);
  margin-bottom: 8px;
}
.section__count {
  font-size: 11px;
  color: var(--text-muted);
  background: var(--bg-secondary);
  border-radius: 9px;
  padding: 1px 7px;
}

.relation-select {
  width: 100%;
}

.opt-row {
  display: flex;
  align-items: center;
  gap: 8px;
}
.opt-swatch {
  width: 14px;
  height: 4px;
  border-radius: 2px;
  flex-shrink: 0;
}
.opt-label {
  font-weight: 600;
}
.opt-code {
  margin-left: auto;
  font-size: 11px;
  color: var(--text-muted);
  font-family: monospace;
}

.hint {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 11.5px;
  color: var(--text-muted);
  margin: 6px 0 0;
}

.filter-row__search {
  margin-bottom: 8px;
}

.picker {
  max-height: 240px;
  overflow-y: auto;
  border: 1px solid var(--border-color);
  border-radius: 6px;
  background: var(--bg-secondary);
}
.empty-hint {
  font-size: 12px;
  color: var(--text-muted);
  font-style: italic;
}
.empty-hint--picker {
  padding: 24px;
  text-align: center;
}

.picker__item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 7px 10px;
  border-bottom: 1px solid var(--border-light);
  font-size: 13px;
  cursor: pointer;
  transition: background 0.12s ease;
}
.picker__item:last-child { border-bottom: none; }
.picker__item:hover {
  background: var(--bg-hover);
}
.picker__item--active {
  background: rgba(76, 125, 255, 0.15);
}
.picker__dot {
  display: inline-block;
  width: 10px;
  height: 10px;
  border-radius: 50%;
  flex-shrink: 0;
}
.picker__name {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.picker__tier {
  font-size: 10px;
  color: var(--text-muted);
  font-family: ui-monospace, SFMono-Regular, monospace;
  padding: 1px 5px;
  background: var(--bg-tertiary);
  border-radius: 3px;
}

.footer-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  width: 100%;
}
.footer-row__summary {
  font-size: 12px;
  color: var(--text-muted);
}
.footer-row__actions {
  display: flex;
  gap: 8px;
}
</style>