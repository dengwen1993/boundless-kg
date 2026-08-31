<template>
  <section v-if="nodeName" class="note-pane">
    <div class="note-panel">
      <!-- ─── Header: title + tier + actions ─── -->
      <div class="note-panel__header">
        <div class="note-panel__title-row">
          <div class="note-panel__icon-wrap">
            <span class="note-panel__icon">📄</span>
          </div>
          <div class="note-panel__title-content">
            <input
              ref="nameInputRef"
              v-model="editingName"
              class="note-panel__name-input"
              placeholder="节点名称"
              @keyup.enter="onNameBlur"
              @blur="onNameBlur"
            />
            <div class="note-panel__meta">
              <el-tag v-if="nodeInfo" size="small" class="note-panel__tier">
                {{ tierLabel(nodeInfo.tier) }}
              </el-tag>
              <span v-if="nodeInfo?.domain" class="note-panel__breadcrumb-text">
                {{ nodeInfo.domain }}
              </span>
              <el-tag
                v-if="hasChildren"
                size="small"
                type="info"
                effect="plain"
                class="note-panel__view-tag"
              >
                含 {{ notesIndex!.children.length }} 个子笔记
              </el-tag>
            </div>
          </div>
        </div>
        <div class="note-panel__actions">
          <el-button
            size="small"
            :class="{ 'is-active': isEditing }"
            @click="toggleEditMode"
          >
            <el-icon v-if="!isEditing"><Edit /></el-icon>
            <el-icon v-else><View /></el-icon>
            <span style="margin-left: 4px">{{ isEditing ? '预览' : '编辑' }}</span>
          </el-button>
          <el-button
            v-if="isEditing"
            size="small"
            type="primary"
            :loading="savingNote"
            @click="saveNote"
          >
            <el-icon><Document /></el-icon>
            <span style="margin-left: 4px">保存</span>
          </el-button>
          <el-button size="small" circle :icon="Close" @click="close" />
        </div>
      </div>

      <!-- Loading -->
      <div v-if="loading || notesIndexLoading" class="note-panel__loading">
        <el-icon class="is-loading"><Loading /></el-icon>
        <span v-if="notesIndexLoading">正在加载笔记索引…</span>
        <span v-else>正在调用大模型生成笔记…</span>
      </div>

      <!-- ─── Detail body (always shows parent's own note.md) ─── -->
      <div v-else class="note-panel__body">
        <!-- ═══ Resources button bar ═══ -->
        <section class="resources-bar-section">
          <el-button class="resources-bar" @click="resourceDialogVisible = true">
            <span class="resources-bar__icon">📚</span>
            <span class="resources-bar__label">学习资料</span>
            <span class="resources-bar__count">
              {{
                resources.web_resources.length +
                resources.user_uploads.length +
                (resources.study_materials?.length ?? 0)
              }}
            </span>
            <el-icon class="resources-bar__arrow"><ArrowRight /></el-icon>
          </el-button>
        </section>

        <!-- ═══ Plan button bar ═══ -->
        <section class="resources-bar-section">
          <el-button class="resources-bar plan-bar" @click="planDialogVisible = true">
            <span class="resources-bar__icon">🎯</span>
            <span class="resources-bar__label">学习计划</span>
            <span class="resources-bar__count" :class="{ 'plan-bar__count--zero': plans.length === 0 }">
              {{ plans.length }}
            </span>
            <el-icon class="resources-bar__arrow"><ArrowRight /></el-icon>
          </el-button>
        </section>

        <!-- ═══ Note content (parent's own note.md) ═══ -->
        <section class="note-section">
          <div class="section-header">
            <h3 class="section-title">
              <span class="section-icon">📝</span>
              {{ notesIndex && notesIndex.children.length > 0 ? '父节点笔记' : '笔记内容' }}
              <el-tag v-if="noteCreated" size="small" type="warning" class="section-tag-warning">
                新建
              </el-tag>
              <span v-else-if="noteContent" class="section-count">
                {{ wordCount }} 字
              </span>
            </h3>
          </div>

          <!-- 笔记不存在 / 需生成：显示生成按钮 -->
          <div v-if="needsGeneration || !noteContent" class="note-empty-gen">
            <el-button type="primary" :loading="generating" @click="generateNote">
              <span style="margin-left: 4px">{{ generating ? '生成中…' : '生成笔记' }}</span>
            </el-button>
          </div>

          <!-- View mode -->
          <div v-else-if="!isEditing" ref="markdownRef" class="markdown-body" v-html="renderedMarkdown"></div>

          <!-- Edit mode -->
          <div v-else class="note-edit-area">
            <textarea
              v-model="noteContent"
              class="note-textarea"
              :placeholder="editPlaceholder"
              spellcheck="false"
            ></textarea>
            <div class="note-edit-hint">
              <span>支持 Markdown · 标题 · 代码块（自动高亮）· 表格 · 引用</span>
            </div>
          </div>
        </section>

        <!-- ═══ Children notes index (only for non-leaf nodes with children) ═══ -->
        <section v-if="hasChildren" class="note-list-section">
          <div class="section-header">
            <h3 class="section-title">
              <span class="section-icon">📚</span>
              子节点笔记（共 {{ notesIndex!.children.length }} 篇）
              <span class="section-count">
                {{ notesIndex!.children.filter((c) => c.has_note).length }} 已生成
              </span>
            </h3>
          </div>

          <div class="note-list-grid">
            <article
              v-for="child in notesIndex!.children"
              :key="child.name"
              class="note-card"
              :class="{ 'note-card--empty': !child.has_note }"
              @click="openChildNote(child)"
            >
              <header class="note-card__header">
                <span class="note-card__icon">{{ child.has_note ? '📝' : '🪧' }}</span>
                <h4 class="note-card__title">{{ child.name }}</h4>
                <span class="note-card__chev">›</span>
              </header>
              <p v-if="child.summary" class="note-card__summary">{{ child.summary }}</p>
              <p v-else-if="!child.has_note" class="note-card__summary note-card__summary--empty">
                （尚未生成笔记 · 点击进入查看 / 触发生成）
              </p>
              <p v-else class="note-card__summary note-card__summary--empty">（无摘要）</p>

              <footer class="note-card__meta">
                <span class="note-card__tier">{{ tierLabel(child.tier) }}</span>
                <span v-if="child.has_note" class="note-card__words">
                  {{ child.words }} 字
                </span>
                <span v-else class="note-card__words note-card__words--empty">未生成</span>
                <span v-if="child.mtime" class="note-card__time">
                  {{ formatCardTime(child.mtime) }}
                </span>
              </footer>
            </article>
          </div>
        </section>
      </div>
    </div>

    <!-- ═══ Resource Dialog ═══ -->
    <ResourceDialog
      v-model="resourceDialogVisible"
      :domain="graphStore.activeDomain || ''"
      :node-name="nodeName"
      :resources="resources"
      @update:resources="onResourcesUpdate"
    />

    <!-- ═══ Plan Dialog ═══ -->
    <PlanDialog
      v-model="planDialogVisible"
      :domain="graphStore.activeDomain || ''"
      :node-name="nodeName"
      :plans="plans"
      @update:plans="onPlansUpdate"
    />
  </section>
</template>

<script setup lang="ts">
import { ref, computed, watch, nextTick, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import {
  Close,
  Loading,
  Edit,
  View,
  Document,
  ArrowRight,
} from '@element-plus/icons-vue'
import { Marked } from 'marked'
import { markedHighlight } from 'marked-highlight'
import hljs from 'highlight.js/lib/common'
import DOMPurify from 'dompurify'
import { useGraphStore } from '@/stores/graph'
import * as api from '@/api'
import type { NodeResources, PlanItem } from '@/types/graph'
import type { NotesIndexResponse, NoteIndexEntry } from '@/api'
import ResourceDialog from './ResourceDialog.vue'
import PlanDialog from './PlanDialog.vue'

const graphStore = useGraphStore()

// ── Drawer visibility ──
const drawerVisible = computed({
  get: () => graphStore.notePanelVisible,
  set: (val) => {
    if (!val) graphStore.closeNotePanel()
  },
})

const nodeName = computed(() => graphStore.notePanelNode || '')

// ── State ──
const loading = ref(false)
const savingNote = ref(false)
const isEditing = ref(false)
const noteContent = ref('')
const noteCreated = ref(false)
const needsGeneration = ref(false)
const generating = ref(false)
const editingName = ref('')
const nameInputRef = ref<HTMLInputElement | null>(null)
const markdownRef = ref<HTMLElement | null>(null)
const nodeInfo = ref<any>(null)

const resources = ref<NodeResources>({ web_resources: [], user_uploads: [], study_materials: [] })
const resourceDialogVisible = ref(false)

const plans = ref<PlanItem[]>([])
const planDialogVisible = ref(false)

// ── Children notes index (non-leaf nodes show this BELOW their own note) ──
const notesIndex = ref<NotesIndexResponse | null>(null)
const notesIndexLoading = ref(false)
/** 是否有可展示的子节点（非叶 + 索引加载成功 + children 非空） */
const hasChildren = computed(
  () => !!notesIndex.value && !notesIndex.value.is_leaf && notesIndex.value.children.length > 0,
)

// ── Markdown rendering setup ──
const editPlaceholder =
  '在此编辑 note.md 内容…\n\n支持 Markdown 语法：\n## 标题、**粗体**、*斜体*、行内代码、\n代码块（自动高亮）、- 列表、> 引用、| 表格 |'
const md = new Marked(
  markedHighlight({
    langPrefix: 'hljs language-',
    highlight(code: string, lang: string) {
      const language = lang && hljs.getLanguage(lang) ? lang : 'plaintext'
      try {
        return hljs.highlight(code, { language, ignoreIllegals: true }).value
      } catch {
        return code
      }
    },
  }),
)
md.setOptions({ breaks: true, gfm: true })

const renderedMarkdown = computed(() => {
  if (!noteContent.value) {
    return '<p class="md-empty">（暂无内容，点击「编辑」开始写作）</p>'
  }
  const raw = md.parse(noteContent.value) as string
  return DOMPurify.sanitize(raw, {
    ADD_ATTR: ['class'],
    ADD_TAGS: ['span'],
  })
})

const wordCount = computed(() => {
  if (!noteContent.value) return 0
  const stripped = noteContent.value
    .replace(/```[\s\S]*?```/g, '')
    .replace(/`[^`]*`/g, '')
    .replace(/[#*_>\-\[\]]/g, '')
  return stripped.replace(/\s+/g, '').length
})

// ── Post-render: add language label + copy button to code blocks ──
async function enhanceCodeBlocks() {
  await nextTick()
  if (!markdownRef.value) return
  const pres = markdownRef.value.querySelectorAll('pre')
  pres.forEach((pre) => {
    if (pre.querySelector('.code-block-header')) return
    const codeEl = pre.querySelector('code')
    if (!codeEl) return

    const className = codeEl.className || ''
    const langMatch = className.match(/language-([\w+-]+)/)
    const lang = langMatch ? langMatch[1] : 'text'

    const header = document.createElement('div')
    header.className = 'code-block-header'

    const langLabel = document.createElement('span')
    langLabel.className = 'code-block-lang'
    langLabel.textContent = lang
    header.appendChild(langLabel)

    const copyBtn = document.createElement('button')
    copyBtn.className = 'code-block-copy'
    copyBtn.type = 'button'
    copyBtn.textContent = '复制'
    copyBtn.addEventListener('click', async (e) => {
      e.preventDefault()
      e.stopPropagation()
      const text = codeEl.textContent || ''
      try {
        await navigator.clipboard.writeText(text)
        copyBtn.textContent = '✓ 已复制'
        copyBtn.classList.add('copied')
        setTimeout(() => {
          copyBtn.textContent = '复制'
          copyBtn.classList.remove('copied')
        }, 1600)
      } catch {
        copyBtn.textContent = '失败'
        setTimeout(() => (copyBtn.textContent = '复制'), 1600)
      }
    })
    header.appendChild(copyBtn)

    pre.insertBefore(header, pre.firstChild)
  })
}

watch(renderedMarkdown, enhanceCodeBlocks)
onMounted(enhanceCodeBlocks)

// ── Load data when panel opens ──
watch(
  () => [graphStore.notePanelVisible, graphStore.notePanelNode],
  async ([visible, name]) => {
    if (visible && name) {
      editingName.value = name as string
      // 并行拉：笔记索引（决定要不要在父节点笔记下方追加子节点网格）+ 父节点笔记详情
      await Promise.all([loadIndex(), loadDetail()])
      const node = graphStore.nodeMap.get(name as string)
      nodeInfo.value = node || null
      // consume intent: deep-link into matching tab (consumed once, then cleared)
      const intent = graphStore.notePanelIntent
      if (intent === 'plan') {
        await nextTick()
        planDialogVisible.value = true
      } else if (intent === 'resource') {
        await nextTick()
        resourceDialogVisible.value = true
      } else if (intent === 'note') {
        await nextTick()
        isEditing.value = false
      }
      graphStore.clearNotePanelIntent()
    }
  },
  { immediate: true },
)

/** 加载子节点笔记索引（不触发 LLM）。失败时静默 —— 父节点的笔记详情照常显示。 */
async function loadIndex() {
  if (!nodeName.value || !graphStore.activeDomain) return
  notesIndexLoading.value = true
  try {
    notesIndex.value = await api.getNotesIndex(
      graphStore.activeDomain,
      nodeName.value,
    )
  } catch (e: any) {
    // 索引拉不到也不影响父节点笔记展示；只是看不到子节点网格
    console.warn('[NodeNotePanel] notes-index failed:', e)
    notesIndex.value = null
  } finally {
    notesIndexLoading.value = false
  }
}

/** 详情视图：加载 note.md / resources / plans。 */
async function loadDetail() {
  if (!nodeName.value || !graphStore.activeDomain) return
  loading.value = true
  needsGeneration.value = false
  // 用 allSettled 容错：note 加载失败不阻塞 resources/plans
  const [noteP, resP, planP] = await Promise.allSettled([
    api.getNodeNote(graphStore.activeDomain, nodeName.value),
    api.getNodeResources(graphStore.activeDomain, nodeName.value),
    api.getNodePlans(graphStore.activeDomain, nodeName.value),
  ])
  // note
  if (noteP.status === 'fulfilled') {
    noteContent.value = noteP.value.content || ''
    noteCreated.value = noteP.value.created || false
    needsGeneration.value = !!noteP.value.needs_generation
  } else {
    noteContent.value = ''
    needsGeneration.value = true
  }
  // resources
  resources.value = resP.status === 'fulfilled'
    ? resP.value
    : { web_resources: [], user_uploads: [], study_materials: [] }
  // plans
  plans.value = planP.status === 'fulfilled' ? (planP.value.items || []) : []
  if (noteP.status === 'rejected') {
    ElMessage.warning('笔记加载失败，可点击「生成笔记」重试')
  }
  loading.value = false
}

/** 手动生成 note.md（note 不存在或加载失败时点击） */
async function generateNote() {
  if (!nodeName.value || !graphStore.activeDomain) return
  generating.value = true
  try {
    ElMessage.info('正在调用大模型生成笔记，请稍候…')
    const res = await api.generateNodeNote(graphStore.activeDomain, nodeName.value)
    noteContent.value = res.content
    noteCreated.value = true
    needsGeneration.value = false
    ElMessage.success('笔记已生成')
  } catch (e: any) {
    ElMessage.error(`生成失败: ${e.message}`)
  } finally {
    generating.value = false
  }
}

/** 子节点卡片点击：进入该子节点的笔记面板（仍然走完整的 list+detail 流程） */
function openChildNote(child: NoteIndexEntry) {
  if (!graphStore.activeDomain) return
  graphStore.openNotePanel(child.name, null)
}

// ── Resource dialog update handler ──
function onResourcesUpdate(newRes: NodeResources) {
  resources.value = newRes
}

// ── Plan dialog update handler ──
function onPlansUpdate(newPlans: PlanItem[]) {
  plans.value = newPlans
}

// ── Node name editing ──
async function onNameBlur() {
  const newName = editingName.value.trim()
  if (!newName) {
    ElMessage.error('节点名不能为空')
    editingName.value = nodeName.value
    return
  }
  if (newName === nodeName.value) return

  try {
    await graphStore.updateNode(nodeName.value, { newName })
    ElMessage.success(`已重命名 [${nodeName.value}] -> [${newName}]`)
    graphStore.notePanelNode = newName
    editingName.value = newName
    // 重命名后子节点关系可能变了，重新拉两份
    await Promise.all([loadIndex(), loadDetail()])
  } catch (e: any) {
    ElMessage.error(e.message || '重命名失败')
    editingName.value = nodeName.value
  }
}

// ── Note editing ──
function toggleEditMode() {
  isEditing.value = !isEditing.value
}

async function saveNote() {
  if (!graphStore.activeDomain || !nodeName.value) return
  savingNote.value = true
  try {
    await api.saveNodeNote(graphStore.activeDomain, nodeName.value, noteContent.value)
    ElMessage.success('笔记已保存')
    isEditing.value = false
    noteCreated.value = false
  } catch (e: any) {
    ElMessage.error(`保存失败: ${e.message}`)
  } finally {
    savingNote.value = false
  }
}

// ── Helpers ──
function tierLabel(tier: string): string {
  const map: Record<string, string> = {
    L0: 'L0 · 领域',
    L1: 'L1 · 主题',
    L2: 'L2 · 子主题',
    L3: 'L3 · 概念',
    leaf: 'Leaf · 节点',
  }
  return map[tier] || tier || 'Leaf'
}

/** 卡片底部时间戳：「07-22 22:50」 */
function formatCardTime(iso: string): string {
  if (!iso || iso.length < 16) return ''
  return `${iso.slice(5, 10)} ${iso.slice(11, 16)}`
}

function close() {
  graphStore.closeNotePanel()
}

function onClose() {
  isEditing.value = false
  resourceDialogVisible.value = false
  planDialogVisible.value = false
  notesIndex.value = null
}
</script>

<style scoped>
/* ════════════════════════════════════════════════════════════
   Note Panel - Scientific Aesthetic
   ════════════════════════════════════════════════════════════ */

.note-pane {
  flex: 1 1 auto;
  min-width: 0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  background: var(--bg-secondary);
  border-right: 1px solid var(--border-color);
}

.note-panel {
  display: flex;
  flex-direction: column;
  height: 100%;
  width: 100%;
  overflow: hidden;
  background: var(--bg-secondary);
}

/* ── Header ── */
.note-panel__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 18px 24px;
  background: linear-gradient(180deg, var(--bg-tertiary) 0%, var(--bg-secondary) 100%);
  border-bottom: 1px solid var(--border-color);
  flex-shrink: 0;
  gap: 16px;
  position: relative;
}

.note-panel__header::after {
  content: '';
  position: absolute;
  left: 0;
  right: 0;
  bottom: -1px;
  height: 1px;
  background: linear-gradient(
    90deg,
    transparent 0%,
    rgba(76, 125, 255, 0.4) 30%,
    rgba(124, 92, 255, 0.4) 70%,
    transparent 100%
  );
}

.note-panel__title-row {
  display: flex;
  align-items: center;
  gap: 12px;
  flex: 1;
  min-width: 0;
}

.note-panel__icon-wrap {
  width: 38px;
  height: 38px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(135deg, rgba(76, 125, 255, 0.15), rgba(124, 92, 255, 0.15));
  border: 1px solid rgba(76, 125, 255, 0.25);
  border-radius: 10px;
  flex-shrink: 0;
}

.note-panel__icon {
  font-size: 18px;
  filter: grayscale(0.2);
}

.note-panel__title-content {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.note-panel__name-input {
  width: 100%;
  background: none;
  border: 1px solid transparent;
  border-radius: 6px;
  padding: 3px 8px;
  font-size: 19px;
  font-weight: 700;
  color: var(--text-primary);
  outline: none;
  letter-spacing: -0.005em;
  transition: border-color 0.15s, background 0.15s;
  margin-left: -8px;
}

.note-panel__name-input:hover {
  border-color: var(--border-color);
  background: var(--bg-tertiary);
}

.note-panel__name-input:focus {
  border-color: var(--accent-blue);
  background: var(--bg-tertiary);
}

.note-panel__meta {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  padding-left: 8px;
}

.note-panel__tier {
  font-family: 'JetBrains Mono', monospace;
  font-size: 10.5px !important;
  height: 20px !important;
  padding: 0 6px !important;
  letter-spacing: 0.04em;
}

.note-panel__view-tag {
  font-size: 10.5px !important;
  height: 20px !important;
  padding: 0 6px !important;
}

.note-panel__breadcrumb-text {
  color: var(--text-muted);
  font-size: 12px;
}

.note-panel__actions {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-shrink: 0;
}

.note-panel__actions .el-button.is-active {
  background: rgba(76, 125, 255, 0.15);
  border-color: var(--accent-blue);
  color: var(--accent-blue);
}

/* ── Loading ── */
.note-panel__loading {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 60px 0;
  color: var(--text-muted);
  font-size: 14px;
}

/* ── Body ── */
.note-panel__body {
  flex: 1;
  overflow-y: auto;
  padding: 24px 28px 48px;
}

/* ── Resources bar ── */
.resources-bar-section {
  margin-bottom: 28px;
}

.resources-bar {
  width: 100%;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 14px 18px !important;
  background: var(--bg-tertiary) !important;
  border: 1px solid var(--border-color) !important;
  border-radius: 12px !important;
  transition: border-color 0.15s, transform 0.15s, box-shadow 0.15s !important;
  height: auto !important;
}

.resources-bar:hover {
  border-color: var(--accent-blue) !important;
  transform: translateY(-1px);
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.2);
}

.resources-bar__icon {
  font-size: 18px;
}

.resources-bar__label {
  flex: 1;
  text-align: left;
  font-size: 14px;
  font-weight: 600;
  color: var(--text-primary);
}

.resources-bar__count {
  font-size: 13px;
  font-weight: 700;
  color: var(--accent-blue);
  background: rgba(76, 125, 255, 0.12);
  border: 1px solid rgba(76, 125, 255, 0.25);
  padding: 2px 10px;
  border-radius: 10px;
  font-family: 'JetBrains Mono', monospace;
}

.resources-bar__arrow {
  color: var(--text-muted);
  font-size: 14px;
}

/* ── Plan bar（学习计划栏，紫色系，区别于学习资料的蓝色） ── */
.plan-bar {
  border-left: 3px solid var(--accent-purple) !important;
}

.plan-bar:hover {
  border-color: var(--accent-purple) !important;
}

.plan-bar .resources-bar__icon {
  filter: hue-rotate(40deg);
}

.plan-bar .resources-bar__count {
  color: var(--accent-purple);
  background: rgba(124, 92, 255, 0.12);
  border-color: rgba(124, 92, 255, 0.25);
}

.plan-bar__count--zero {
  color: var(--text-muted) !important;
  background: var(--bg-tertiary) !important;
  border-color: var(--border-color) !important;
}

/* ── Section header ── */
.section-header {
  margin-bottom: 14px;
}

.section-title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 15px;
  font-weight: 700;
  color: var(--text-primary);
  margin: 0;
  letter-spacing: -0.005em;
}

.section-icon {
  font-size: 16px;
}

.section-count {
  font-size: 11.5px;
  font-weight: 500;
  color: var(--text-muted);
  background: var(--bg-tertiary);
  border: 1px solid var(--border-color);
  padding: 1px 8px;
  border-radius: 10px;
  margin-left: 4px;
  font-family: 'JetBrains Mono', monospace;
  letter-spacing: 0.04em;
}

.section-tag-warning {
  margin-left: 4px !important;
}

/* ════════════════════════════════════════════════════════════
   Note Section (markdown content)
   ════════════════════════════════════════════════════════════ */

.note-empty-gen {
  display: flex;
  justify-content: center;
  align-items: center;
  gap: 12px;
  padding: 32px 16px;
  color: var(--text-muted);
}

.note-section {
  margin-bottom: 24px;
}

/* ── Markdown body (rendered) ── */
.markdown-body {
  font-family: 'Inter', 'PingFang SC', 'Microsoft YaHei', sans-serif;
  font-size: 15px;
  line-height: 1.85;
  color: var(--text-primary);
  word-break: break-word;
  letter-spacing: 0.01em;
}

/* ── Headings ── */
.markdown-body :deep(h1) {
  font-size: 1.85em;
  font-weight: 800;
  margin: 1.6em 0 0.7em;
  padding: 0 0 0.5em;
  border-bottom: 1px solid var(--border-color);
  color: var(--text-primary);
  letter-spacing: -0.015em;
  position: relative;
}

.markdown-body :deep(h1)::after {
  content: '';
  position: absolute;
  bottom: -1px;
  left: 0;
  width: 64px;
  height: 2px;
  background: linear-gradient(90deg, var(--accent-blue), var(--accent-purple));
}

.markdown-body :deep(h2) {
  font-size: 1.4em;
  font-weight: 700;
  margin: 1.6em 0 0.6em;
  padding: 0.1em 0 0.1em 0.85em;
  border-left: 3px solid var(--accent-purple);
  color: var(--text-primary);
  letter-spacing: -0.008em;
  line-height: 1.35;
}

.markdown-body :deep(h3) {
  font-size: 1.15em;
  font-weight: 700;
  margin: 1.3em 0 0.5em;
  color: var(--accent-cyan);
  position: relative;
  padding-left: 0.95em;
  letter-spacing: -0.005em;
}

.markdown-body :deep(h3)::before {
  content: '#';
  position: absolute;
  left: 0;
  color: var(--accent-cyan);
  opacity: 0.55;
  font-weight: 800;
}

.markdown-body :deep(h4) {
  font-size: 1.02em;
  font-weight: 700;
  margin: 1.1em 0 0.4em;
  color: var(--accent-amber);
}

.markdown-body :deep(h5),
.markdown-body :deep(h6) {
  font-size: 0.95em;
  font-weight: 700;
  margin: 1em 0 0.3em;
  color: var(--text-secondary);
}

/* ── Paragraphs ── */
.markdown-body :deep(p) {
  margin: 0.85em 0;
}

/* ── Lists ── */
.markdown-body :deep(ul),
.markdown-body :deep(ol) {
  margin: 0.8em 0;
  padding-left: 1.6em;
}

.markdown-body :deep(ul) {
  list-style: none;
}

.markdown-body :deep(ul > li) {
  position: relative;
  padding-left: 0.25em;
  margin: 0.45em 0;
}

.markdown-body :deep(ul > li::before) {
  content: '';
  position: absolute;
  left: -1.05em;
  top: 0.7em;
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: var(--accent-blue);
  opacity: 0.7;
}

.markdown-body :deep(ul > li > ul > li::before) {
  background: var(--accent-purple);
  opacity: 0.6;
  left: -1.15em;
}

.markdown-body :deep(ul > li > ul > li > ul > li::before) {
  background: var(--accent-cyan);
  opacity: 0.55;
}

.markdown-body :deep(ol > li) {
  margin: 0.45em 0;
  padding-left: 0.25em;
}

.markdown-body :deep(ol > li::marker) {
  color: var(--accent-amber);
  font-weight: 700;
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.9em;
}

/* ── Blockquote ── */
.markdown-body :deep(blockquote) {
  margin: 1.2em 0;
  padding: 0.95em 1.15em;
  background: linear-gradient(
    135deg,
    rgba(76, 125, 255, 0.07),
    rgba(124, 92, 255, 0.04)
  );
  border-left: 3px solid var(--accent-blue);
  border-radius: 0 10px 10px 0;
  color: var(--text-secondary);
  font-size: 0.94em;
  line-height: 1.75;
  position: relative;
}

.markdown-body :deep(blockquote)::before {
  content: '"';
  position: absolute;
  top: -8px;
  left: 8px;
  font-size: 32px;
  color: var(--accent-blue);
  opacity: 0.25;
  font-family: Georgia, serif;
  line-height: 1;
}

.markdown-body :deep(blockquote p) {
  margin: 0.3em 0;
}

.markdown-body :deep(blockquote p:first-child) {
  margin-top: 0;
}

.markdown-body :deep(blockquote p:last-child) {
  margin-bottom: 0;
}

/* ── Inline code ── */
.markdown-body :deep(code) {
  font-family: 'JetBrains Mono', 'Fira Code', 'Consolas', monospace;
  font-size: 0.88em;
  padding: 0.15em 0.45em;
  background: var(--bg-tertiary);
  border: 1px solid var(--border-color);
  border-radius: 4px;
  color: var(--accent-amber);
}

/* ── Code blocks ── */
.markdown-body :deep(pre) {
  margin: 1.2em 0;
  padding: 0;
  background: var(--bg-primary);
  border: 1px solid var(--border-color);
  border-radius: 10px;
  overflow: hidden;
  position: relative;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.2);
}

.markdown-body :deep(.code-block-header) {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0.55em 1em;
  background: rgba(0, 0, 0, 0.3);
  border-bottom: 1px solid var(--border-color);
  font-family: 'JetBrains Mono', monospace;
  font-size: 11px;
  color: var(--text-muted);
}

.markdown-body :deep(.code-block-lang) {
  text-transform: uppercase;
  letter-spacing: 0.08em;
  font-weight: 700;
  color: var(--accent-cyan);
}

.markdown-body :deep(.code-block-copy) {
  background: none;
  border: 1px solid var(--border-color);
  color: var(--text-secondary);
  font-size: 11px;
  padding: 0.2em 0.7em;
  border-radius: 4px;
  cursor: pointer;
  transition: all 0.15s;
  font-family: inherit;
  letter-spacing: 0.04em;
}

.markdown-body :deep(.code-block-copy:hover) {
  background: var(--bg-hover);
  color: var(--text-primary);
  border-color: var(--border-light);
}

.markdown-body :deep(.code-block-copy.copied) {
  background: var(--accent-green);
  color: white;
  border-color: var(--accent-green);
}

.markdown-body :deep(pre code) {
  display: block;
  padding: 1em 1.2em;
  background: transparent;
  border: none;
  color: var(--text-primary);
  font-size: 13px;
  line-height: 1.7;
  overflow-x: auto;
  font-family: 'JetBrains Mono', 'Fira Code', 'Consolas', monospace;
}

/* ── Links ── */
.markdown-body :deep(a) {
  color: var(--accent-blue);
  text-decoration: none;
  border-bottom: 1px solid rgba(76, 125, 255, 0.4);
  transition: color 0.15s, border-color 0.15s;
  padding-bottom: 1px;
}

.markdown-body :deep(a:hover) {
  color: var(--accent-cyan);
  border-bottom-color: var(--accent-cyan);
}

/* ── Tables ── */
.markdown-body :deep(table) {
  width: 100%;
  margin: 1.2em 0;
  border-collapse: separate;
  border-spacing: 0;
  font-size: 0.92em;
  border: 1px solid var(--border-color);
  border-radius: 10px;
  overflow: hidden;
}

.markdown-body :deep(th),
.markdown-body :deep(td) {
  padding: 0.7em 1em;
  text-align: left;
  border-bottom: 1px solid var(--border-color);
}

.markdown-body :deep(tr:last-child td) {
  border-bottom: none;
}

.markdown-body :deep(th) {
  background: var(--bg-tertiary);
  font-weight: 700;
  color: var(--text-primary);
  letter-spacing: 0.02em;
  font-size: 0.9em;
}

.markdown-body :deep(tbody tr:hover) {
  background: rgba(76, 125, 255, 0.04);
}

/* ── Horizontal rule ── */
.markdown-body :deep(hr) {
  border: none;
  height: 1px;
  background: linear-gradient(
    90deg,
    transparent,
    var(--border-light) 30%,
    var(--border-light) 70%,
    transparent
  );
  margin: 2em 0;
}

/* ── Strong / em ── */
.markdown-body :deep(strong) {
  font-weight: 700;
  color: var(--text-primary);
  background: linear-gradient(
    180deg,
    transparent 60%,
    rgba(245, 158, 11, 0.2) 60%
  );
  padding: 0 2px;
}

.markdown-body :deep(em) {
  font-style: italic;
  color: var(--text-secondary);
}

/* ── Images ── */
.markdown-body :deep(img) {
  max-width: 100%;
  border-radius: 8px;
  border: 1px solid var(--border-color);
}

/* ── Empty state ── */
.markdown-body :deep(.md-empty) {
  color: var(--text-muted);
  font-style: italic;
  text-align: center;
  padding: 3em 0;
  font-size: 0.95em;
}

/* ── Edit area ── */
.note-edit-area {
  position: relative;
}

.note-textarea {
  width: 100%;
  min-height: 520px;
  padding: 16px 18px;
  background: var(--bg-primary);
  border: 1px solid var(--border-color);
  border-radius: 10px;
  color: var(--text-primary);
  font-family: 'JetBrains Mono', 'Fira Code', 'Consolas', monospace;
  font-size: 13.5px;
  line-height: 1.75;
  resize: vertical;
  outline: none;
  transition: border-color 0.15s, box-shadow 0.15s;
}

.note-textarea:focus {
  border-color: var(--accent-blue);
  box-shadow: 0 0 0 3px rgba(76, 125, 255, 0.12);
}

.note-edit-hint {
  margin-top: 8px;
  font-size: 12px;
  color: var(--text-muted);
  text-align: right;
}

/* ════════════════════════════════════════════════════════════
   Note list view (non-leaf nodes) — child notes as cards
   ════════════════════════════════════════════════════════════ */
.note-list-section {
  margin-bottom: 8px;
}

.note-list-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 12px;
}

.note-card {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 14px 16px;
  background: var(--bg-tertiary);
  border: 1px solid var(--border-color);
  border-left: 3px solid var(--accent-blue);
  border-radius: 10px;
  cursor: pointer;
  transition: border-color 0.15s, transform 0.15s, box-shadow 0.15s, background 0.15s;
  min-height: 110px;
}

.note-card:hover {
  border-color: var(--accent-blue);
  background: var(--bg-quaternary, var(--bg-tertiary));
  transform: translateY(-1px);
  box-shadow: 0 4px 14px rgba(0, 0, 0, 0.25);
}

.note-card--empty {
  border-left-color: var(--border-color);
  opacity: 0.85;
}
.note-card--empty:hover {
  border-left-color: var(--accent-amber);
}

.note-card__header {
  display: flex;
  align-items: center;
  gap: 8px;
}
.note-card__icon { font-size: 15px; flex-shrink: 0; }
.note-card__title {
  flex: 1;
  min-width: 0;
  margin: 0;
  font-size: 14px;
  font-weight: 700;
  color: var(--text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.note-card__chev {
  font-size: 18px;
  color: var(--text-muted);
  font-weight: 700;
  line-height: 1;
  flex-shrink: 0;
  transition: transform 0.15s, color 0.15s;
}
.note-card:hover .note-card__chev {
  color: var(--accent-blue);
  transform: translateX(2px);
}

.note-card__summary {
  flex: 1;
  margin: 0;
  font-size: 12.5px;
  color: var(--text-secondary);
  line-height: 1.55;
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
  overflow: hidden;
  text-overflow: ellipsis;
}
.note-card__summary--empty {
  color: var(--text-muted);
  font-style: italic;
}

.note-card__meta {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 11px;
  color: var(--text-muted);
  font-family: 'JetBrains Mono', monospace;
  flex-wrap: wrap;
}
.note-card__tier {
  background: rgba(76, 125, 255, 0.1);
  border: 1px solid rgba(76, 125, 255, 0.2);
  color: var(--accent-blue);
  padding: 1px 6px;
  border-radius: 8px;
  font-size: 10.5px;
}
.note-card__words {
  font-weight: 600;
  color: var(--text-secondary);
}
.note-card__words--empty { color: var(--accent-amber); font-weight: 500; }
.note-card__time {
  margin-left: auto;
  font-size: 10.5px;
}

.note-list-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 6px;
  padding: 60px 20px;
  color: var(--text-muted);
  font-size: 13px;
}
.note-list-empty__icon {
  font-size: 36px;
  opacity: 0.5;
}

/* ── Drawer overrides (legacy, kept for reference — drawer removed) ── */
</style>
