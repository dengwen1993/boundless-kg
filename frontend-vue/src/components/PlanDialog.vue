<template>
  <el-dialog
    v-model="dialogVisible"
    :title="''"
    width="680px"
    :destroy-on-close="true"
    class="plan-dialog"
    @close="onClose"
  >
    <div class="plan-panel">
      <!-- ═══ Header ═══ -->
      <div class="pp-header">
        <div class="pp-header__left">
          <span class="pp-header__icon">🎯</span>
          <div class="pp-header__text">
            <span class="pp-header__title">学习计划</span>
            <span class="pp-header__node">{{ nodeName }} · {{ domain }}</span>
          </div>
        </div>
        <div class="pp-header__right">
          <el-button size="small" type="primary" :icon="Plus" @click="startAdd">新建计划</el-button>
          <el-button size="small" :icon="Refresh" :loading="loading" @click="reload" />
        </div>
      </div>

      <!-- ═══ Stats ═══ -->
      <div class="pp-stats">
        <span class="pp-stat">📋 {{ plans.length }} 个计划</span>
        <span class="pp-stat pp-stat--done">✅ {{ totalDoneActions }}/{{ totalActions }} 行动完成</span>
      </div>

      <!-- ═══ Add form ═══ -->
      <div v-if="showAdd" class="pp-add-form">
        <el-input v-model="newGoal" placeholder="计划目标（一句话）" size="small" class="pp-add-goal" />
        <div class="pp-add-actions">
          <div v-for="(a, i) in newActions" :key="i" class="pp-add-action-row">
            <el-input v-model="newActions[i]" :placeholder="`行动 ${i + 1}`" size="small" class="pp-add-action-input" />
            <el-button size="small" circle :icon="Close" @click="newActions.splice(i, 1)" v-if="newActions.length > 1" />
          </div>
        </div>
        <div class="pp-add-btns">
          <el-button size="small" @click="newActions.push('')">+ 加一条行动</el-button>
          <el-button size="small" type="primary" @click="submitAdd" :loading="adding">创建</el-button>
          <el-button size="small" @click="showAdd = false">取消</el-button>
        </div>
      </div>

      <!-- ═══ Plan cards ═══ -->
      <div v-loading="loading" class="pp-list">
        <div
          v-for="plan in plans"
          :key="plan.id"
          class="pp-card"
          :class="{
            'pp-card--done': plan.status === 'done',
            'pp-card--editing': editingPlanId === plan.id,
          }"
        >
          <!-- ── Card header ── -->
          <div class="pp-card__header">
            <template v-if="editingPlanId === plan.id">
              <span class="pp-card__status">✏️</span>
              <el-input
                v-model="editDraft.goal"
                placeholder="计划目标"
                size="small"
                class="pp-card__goal-input"
                maxlength="120"
                show-word-limit
              />
              <el-button size="small" type="primary" :loading="savingEdit" @click="saveEdit(plan)">保存</el-button>
              <el-button size="small" :disabled="savingEdit" @click="cancelEdit">取消</el-button>
            </template>
            <template v-else>
              <span class="pp-card__status" :class="`pp-card__status--${plan.status}`">
                {{ statusIcon(plan.status) }}
              </span>
              <span class="pp-card__goal">{{ plan.goal }}</span>
              <el-tag size="small" :type="plan.source === 'agent' ? 'primary' : 'info'" effect="plain">
                {{ plan.source === 'agent' ? '助手' : '手动' }}
              </el-tag>
              <el-dropdown trigger="click" @command="(cmd: string) => onPlanMenu(cmd, plan)">
                <el-button size="small" circle :icon="MoreFilled" />
                <template #dropdown>
                  <el-dropdown-menu>
                    <el-dropdown-item command="edit" :icon="Edit">编辑计划</el-dropdown-item>
                    <el-dropdown-item command="add_action">+ 加行动</el-dropdown-item>
                    <el-dropdown-item command="delete" divided>删除计划</el-dropdown-item>
                  </el-dropdown-menu>
                </template>
              </el-dropdown>
            </template>
          </div>

          <!-- ── Card meta (display) / date input (edit) ── -->
          <div v-if="editingPlanId === plan.id" class="pp-card__edit-meta">
            <label class="pp-card__edit-meta-label">📅 日期</label>
            <el-input v-model="editDraft.date" placeholder="YYYY-MM-DD" size="small" class="pp-card__date-input" />
            <label class="pp-card__edit-meta-label">📝 备注</label>
            <el-input
              v-model="editDraft.note"
              type="textarea"
              :autosize="{ minRows: 1, maxRows: 3 }"
              placeholder="备注（可选）"
              size="small"
              class="pp-card__note-input"
            />
          </div>
          <div v-else class="pp-card__meta">
            <span>📅 {{ plan.date }}</span>
            <span>📊 {{ doneCount(plan) }}/{{ plan.actions.length }} 完成</span>
          </div>

          <!-- ── Actions list ── -->
          <div class="pp-card__actions">
            <!-- Edit mode: all actions as textareas -->
            <template v-if="editingPlanId === plan.id">
              <div
                v-for="(act, i) in editDraft.actions"
                :key="act.id || `new-${i}`"
                class="pp-action pp-action--edit"
              >
                <span class="pp-action__num">{{ i + 1 }}</span>
                <el-input
                  v-model="editDraft.actions[i].content"
                  type="textarea"
                  :autosize="{ minRows: 1, maxRows: 8 }"
                  placeholder="行动内容"
                  size="small"
                  class="pp-action__input"
                />
                <el-button
                  size="small"
                  link
                  :icon="Close"
                  class="pp-action__del pp-action__del--always"
                  @click="removeEditAction(i)"
                />
              </div>
              <el-button size="small" plain :icon="Plus" @click="addEditAction" class="pp-action__add">
                + 加一条行动
              </el-button>
            </template>

            <!-- Display mode -->
            <template v-else>
              <div
                v-for="act in plan.actions"
                :key="act.id"
                class="pp-action"
                :class="{ 'pp-action--done': act.status === 'done' }"
              >
                <el-checkbox
                  :model-value="act.status === 'done'"
                  @change="(val: any) => toggleAction(plan, act, val)"
                  size="small"
                  class="pp-action__check"
                />
                <div class="pp-action__content">
                  <PlanActionContent :content="act.content" />
                </div>
                <el-button size="small" link :icon="Close" @click="removeAction(plan, act)" class="pp-action__del" />
              </div>
            </template>
          </div>

          <!-- Add action inline (display mode only) -->
          <div v-if="addingTo === plan.id && editingPlanId !== plan.id" class="pp-card__add-action">
            <el-input v-model="newActionContent" placeholder="新行动内容" size="small" @keyup.enter="submitAddAction(plan)" />
            <el-button size="small" type="primary" @click="submitAddAction(plan)">加</el-button>
            <el-button size="small" @click="addingTo = ''">取消</el-button>
          </div>

          <!-- Note (display mode, when present) -->
          <div v-else-if="plan.note && editingPlanId !== plan.id" class="pp-card__note">📝 {{ plan.note }}</div>
        </div>
      </div>

      <!-- ═══ Empty ═══ -->
      <div v-if="!loading && plans.length === 0 && !showAdd" class="pp-empty">
        <span class="pp-empty__icon">🎯</span>
        <span class="pp-empty__text">还没有学习计划</span>
        <el-button size="small" type="primary" @click="startAdd">新建第一个计划</el-button>
      </div>
    </div>
  </el-dialog>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue'
import { Plus, Refresh, Close, MoreFilled, Edit } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import * as api from '@/api'
import { useGraphStore } from '@/stores/graph'
import type { PlanItem } from '@/types/graph'
import PlanActionContent from './PlanActionContent.vue'

const props = defineProps<{
  modelValue: boolean
  domain: string
  nodeName: string
}>()
const emit = defineEmits<{ 'update:modelValue': [val: boolean] }>()

const graphStore = useGraphStore()
const dialogVisible = computed({
  get: () => props.modelValue,
  set: (v) => emit('update:modelValue', v),
})

const plans = ref<PlanItem[]>([])
const loading = ref(false)
const showAdd = ref(false)
const newGoal = ref('')
const newActions = ref<string[]>([''])
const adding = ref(false)
const addingTo = ref('')
const newActionContent = ref('')

// ── Edit mode ──
const editingPlanId = ref('')
const savingEdit = ref(false)
const editDraft = ref<{
  goal: string
  date: string
  note: string
  actions: { id: string; content: string }[]
}>({ goal: '', date: '', note: '', actions: [] })

const totalActions = computed(() => plans.value.reduce((s, p) => s + p.actions.length, 0))
const totalDoneActions = computed(() => plans.value.reduce((s, p) => s + doneCount(p), 0))

function doneCount(p: PlanItem) {
  return p.actions.filter(a => a.status === 'done').length
}
function statusIcon(s: string) {
  return { pending: '⏳', done: '✅', skipped: '⏭️' }[s] || '•'
}

async function reload() {
  if (!props.domain || !props.nodeName) return
  loading.value = true
  try {
    const res = await api.getNodePlans(props.domain, props.nodeName)
    plans.value = res.items || []
  } catch {
    plans.value = []
  } finally {
    loading.value = false
  }
}

watch(
  () => [props.modelValue, props.domain, props.nodeName],
  async ([visible]) => {
    if (visible && props.domain && props.nodeName) {
      await reload()
    }
  },
  { immediate: true },
)

// ── Add plan ──
function startAdd() {
  showAdd.value = true
  newGoal.value = ''
  newActions.value = ['']
}

async function submitAdd() {
  const goal = newGoal.value.trim()
  const actions = newActions.value.map(a => a.trim()).filter(Boolean)
  if (!goal) { ElMessage.warning('请填写计划目标'); return }
  if (actions.length === 0) { ElMessage.warning('至少需要1条行动'); return }
  adding.value = true
  try {
    await api.addPlan(props.domain, props.nodeName, { goal, actions })
    ElMessage.success('计划已创建')
    showAdd.value = false
    await reload()
  } catch (e: any) {
    ElMessage.error(`创建失败: ${e.message}`)
  } finally {
    adding.value = false
  }
}

// ── Toggle action status ──
async function toggleAction(plan: PlanItem, act: { id: string; status: string }, val: boolean) {
  const status = val ? 'done' : 'pending'
  try {
    await api.updateAction(props.domain, props.nodeName, plan.id, act.id, { status })
    act.status = status
    plan.status = plan.actions.every(a => a.status === 'done') ? 'done' : 'pending'
  } catch (e: any) {
    ElMessage.error(`更新失败: ${e.message}`)
  }
}

// ── Add action to plan ──
function onPlanMenu(cmd: string, plan: PlanItem) {
  if (cmd === 'edit') {
    startEdit(plan)
  } else if (cmd === 'add_action') {
    addingTo.value = plan.id
    newActionContent.value = ''
  } else if (cmd === 'delete') {
    deletePlan(plan)
  }
}

// ── Edit plan ──
function startEdit(plan: PlanItem) {
  editingPlanId.value = plan.id
  editDraft.value = {
    goal: plan.goal,
    date: plan.date,
    note: plan.note || '',
    actions: plan.actions.map(a => ({ id: a.id, content: a.content })),
  }
  addingTo.value = ''
}

function cancelEdit() {
  editingPlanId.value = ''
}

function addEditAction() {
  editDraft.value.actions.push({ id: '', content: '' })
}

function removeEditAction(i: number) {
  editDraft.value.actions.splice(i, 1)
}

async function saveEdit(plan: PlanItem) {
  const goal = editDraft.value.goal.trim()
  if (!goal) { ElMessage.warning('请填写计划目标'); return }
  if (editDraft.value.actions.length === 0) {
    ElMessage.warning('至少需要 1 条行动'); return
  }
  // 过滤空白行动（新增且空内容的视为无效，跳过；现有的允许保留空白吗？这里统一 trim 过滤）
  const cleanedActions = editDraft.value.actions
    .map(a => ({ id: a.id, content: a.content.trim() }))
    .filter(a => a.content)
  if (cleanedActions.length === 0) {
    ElMessage.warning('至少需要 1 条非空行动'); return
  }
  editDraft.value.actions = cleanedActions

  savingEdit.value = true
  try {
    // 1) 计划级字段
    const planPatch: { goal?: string; date?: string; note?: string } = {}
    if (goal !== plan.goal) planPatch.goal = goal
    if (editDraft.value.date.trim() && editDraft.value.date !== plan.date) {
      planPatch.date = editDraft.value.date.trim()
    }
    const noteVal = editDraft.value.note.trim()
    if (noteVal !== (plan.note || '')) planPatch.note = noteVal
    if (Object.keys(planPatch).length > 0) {
      await api.updatePlan(props.domain, props.nodeName, plan.id, planPatch)
    }

    // 2) 增量更新 actions: 删 / 改 / 增
    const original = plan.actions
    const draftIds = new Set(cleanedActions.filter(a => a.id).map(a => a.id))

    for (const orig of original) {
      if (!draftIds.has(orig.id)) {
        await api.deleteAction(props.domain, props.nodeName, plan.id, orig.id)
      }
    }
    for (const a of cleanedActions) {
      if (!a.id) continue
      const orig = original.find(o => o.id === a.id)
      if (orig && orig.content !== a.content) {
        await api.updateAction(props.domain, props.nodeName, plan.id, a.id, {
          status: orig.status as 'pending' | 'done' | 'skipped',
          content: a.content,
        })
      }
    }
    for (const a of cleanedActions) {
      if (a.id) continue
      await api.addAction(props.domain, props.nodeName, plan.id, a.content)
    }

    ElMessage.success('计划已保存')
    editingPlanId.value = ''
    await reload()
  } catch (e: any) {
    ElMessage.error(`保存失败: ${e.message || e}`)
  } finally {
    savingEdit.value = false
  }
}

async function submitAddAction(plan: PlanItem) {
  const content = newActionContent.value.trim()
  if (!content) return
  try {
    await api.addAction(props.domain, props.nodeName, plan.id, content)
    ElMessage.success('行动已追加')
    addingTo.value = ''
    await reload()
  } catch (e: any) {
    ElMessage.error(`追加失败: ${e.message}`)
  }
}

// ── Delete action ──
async function removeAction(plan: PlanItem, act: { id: string }) {
  try {
    await api.deleteAction(props.domain, props.nodeName, plan.id, act.id)
    plan.actions = plan.actions.filter(a => a.id !== act.id)
    plan.status = plan.actions.length > 0 && plan.actions.every(a => a.status === 'done') ? 'done' : 'pending'
    ElMessage.success('已删除')
  } catch (e: any) {
    ElMessage.error(`删除失败: ${e.message}`)
  }
}

// ── Delete plan ──
async function deletePlan(plan: PlanItem) {
  try {
    await api.deletePlan(props.domain, props.nodeName, plan.id)
    plans.value = plans.value.filter(p => p.id !== plan.id)
    ElMessage.success('计划已删除')
  } catch (e: any) {
    ElMessage.error(`删除失败: ${e.message}`)
  }
}

function onClose() {
  showAdd.value = false
  addingTo.value = ''
  editingPlanId.value = ''
}
</script>

<script lang="ts">
import { watch } from 'vue'
</script>

<style scoped>
.plan-panel { display: flex; flex-direction: column; gap: 12px; }

/* Header */
.pp-header { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.pp-header__left { display: flex; align-items: center; gap: 10px; }
.pp-header__icon { font-size: 22px; }
.pp-header__text { display: flex; flex-direction: column; gap: 2px; }
.pp-header__title { font-size: 17px; font-weight: 700; color: var(--text-primary); }
.pp-header__node { font-size: 12px; color: var(--text-muted); }
.pp-header__right { display: flex; gap: 8px; }

/* Stats */
.pp-stats { display: flex; gap: 16px; font-size: 13px; color: var(--text-muted); padding: 0 4px; }
.pp-stat--done { color: var(--accent-green, #10b981); font-weight: 600; }

/* Add form */
.pp-add-form {
  background: var(--bg-tertiary);
  border: 1px solid var(--border-color);
  border-radius: 10px;
  padding: 14px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.pp-add-goal { font-size: 14px; }
.pp-add-actions { display: flex; flex-direction: column; gap: 6px; }
.pp-add-action-row { display: flex; gap: 8px; align-items: center; }
.pp-add-action-input { flex: 1; }
.pp-add-btns { display: flex; gap: 8px; }

/* Plan cards */
.pp-list { display: flex; flex-direction: column; gap: 12px; max-height: 520px; overflow-y: auto; }
.pp-card {
  background: var(--bg-tertiary);
  border: 1px solid var(--border-color);
  border-left: 3px solid var(--accent-blue, #4c7dff);
  border-radius: 10px;
  padding: 12px 16px;
  transition: border-color 0.15s;
}
.pp-card--done { border-left-color: var(--accent-green, #10b981); opacity: 0.75; }
.pp-card__header { display: flex; align-items: center; gap: 8px; }
.pp-card__status { font-size: 18px; flex-shrink: 0; }
.pp-card__goal { font-size: 15px; font-weight: 600; color: var(--text-primary); flex: 1; }
.pp-card__meta { display: flex; gap: 16px; font-size: 12px; color: var(--text-muted); margin: 6px 0 8px 26px; }
.pp-card__actions { display: flex; flex-direction: column; gap: 8px; margin-left: 26px; }
.pp-action { display: flex; align-items: flex-start; gap: 8px; padding: 4px 0; }
.pp-action__check { padding-top: 2px; flex-shrink: 0; }
.pp-action__content { flex: 1; min-width: 0; }
.pp-action--done { opacity: 0.55; }
.pp-action__del { margin-left: auto; opacity: 0; transition: opacity 0.15s; flex-shrink: 0; }
.pp-action:hover .pp-action__del { opacity: 1; }
.pp-card__add-action { display: flex; gap: 8px; margin: 8px 0 0 26px; }
.pp-card__note { font-size: 12px; color: var(--text-muted); margin-top: 8px; margin-left: 26px; }

/* Edit mode */
.pp-card--editing {
  border-color: var(--accent-blue, #4c7dff);
  border-left-color: var(--accent-amber, #f59e0b);
  box-shadow: 0 0 0 1px rgba(76, 125, 255, 0.2) inset;
  opacity: 1;
}
.pp-card__goal-input { flex: 1; }
.pp-card__edit-meta {
  display: grid;
  grid-template-columns: auto 160px;
  align-items: center;
  gap: 8px 10px;
  margin: 10px 0 10px 26px;
  font-size: 12px;
  color: var(--text-muted);
}
.pp-card__edit-meta-label { text-align: right; }
.pp-card__date-input { width: 100%; }
.pp-card__note-input { grid-column: 1 / -1; }
.pp-action--edit { gap: 8px; }
.pp-action__num {
  flex-shrink: 0;
  min-width: 22px;
  height: 22px;
  line-height: 20px;
  text-align: center;
  border-radius: 4px;
  background: var(--bg-hover);
  color: var(--text-muted);
  font-size: 11px;
  font-weight: 600;
  margin-top: 4px;
}
.pp-action__input { flex: 1; min-width: 0; }
.pp-action__del--always { opacity: 1; color: var(--text-muted); }
.pp-action__del--always:hover { color: var(--accent-red, #ef4444); }
.pp-action__add { align-self: flex-start; margin-top: 4px; }

/* Empty */
.pp-empty { display: flex; flex-direction: column; align-items: center; gap: 12px; padding: 50px 20px; }
.pp-empty__icon { font-size: 40px; opacity: 0.5; }
.pp-empty__text { font-size: 15px; color: var(--text-muted); }
</style>
