<template>
  <aside class="sidebar" :class="{ 'sidebar--hidden': sidebarHidden }" :style="{ width: sidebarWidth }">
  <!-- Floating reveal tab — only visible when sidebar is fully hidden -->
  <button
    v-if="sidebarHidden"
    class="sidebar__reveal-tab"
    title="展开助手"
    @click="cycleWidth"
  >
    <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
      <polyline points="15 18 9 12 15 6" />
    </svg>
    <span>助手</span>
  </button>
    <!-- Header -->
    <header class="sidebar__header">
      <div class="sidebar__title">
        <span class="badge-ai">AI</span>
        <span>知识图谱助手</span>
        <span
          v-if="chatStore.loadedHistorySession"
          class="badge-history"
          :title="`已加载历史会话 ${chatStore.loadedHistorySession.session}（${chatStore.loadedHistorySession.date}）\n后续消息会继续写入该 jsonl 文件。`"
        >
          📜 历史：{{ chatStore.loadedHistorySession.session.slice(0, 8) }}…
        </span>
      </div>
      <div class="sidebar__actions">
        <el-button
          circle
          size="small"
          :title="widthToggleTitle"
          @click="cycleWidth"
        >
          <svg
            viewBox="0 0 24 24"
            width="14"
            height="14"
            fill="none"
            stroke="currentColor"
            stroke-width="2"
            stroke-linecap="round"
            stroke-linejoin="round"
          >
            <polyline points="15 3 21 3 21 9" />
            <polyline points="9 21 3 21 3 15" />
            <line x1="21" y1="3" x2="14" y2="10" />
            <line x1="3" y1="21" x2="10" y2="14" />
          </svg>
        </el-button>
        <el-button
          circle
          size="small"
          title="历史会话查询"
          @click="openMemorySearch"
        >
          <svg
            viewBox="0 0 24 24"
            width="14"
            height="14"
            fill="none"
            stroke="currentColor"
            stroke-width="2"
            stroke-linecap="round"
            stroke-linejoin="round"
          >
            <circle cx="11" cy="11" r="8" />
            <line x1="21" y1="21" x2="16.65" y2="16.65" />
            <line x1="8" y1="11" x2="14" y2="11" />
            <line x1="11" y1="8" x2="11" y2="14" />
          </svg>
        </el-button>
        <el-button
          circle
          size="small"
          title="新会话"
          @click="onNewSession"
        >
          <svg
            viewBox="0 0 24 24"
            width="14"
            height="14"
            fill="none"
            stroke="currentColor"
            stroke-width="2"
            stroke-linecap="round"
            stroke-linejoin="round"
          >
            <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
            <line x1="12" y1="8" x2="12" y2="14" />
            <line x1="9" y1="11" x2="15" y2="11" />
          </svg>
        </el-button>
      </div>
    </header>

    <!-- Collapsible capabilities panel -->
    <div class="capabilities">
      <button class="capabilities__toggle" @click="showCapabilities = !showCapabilities">
        <span class="capabilities__toggle-label">💡 助手能力</span>
        <svg
          class="capabilities__chevron"
          :class="{ 'capabilities__chevron--open': showCapabilities }"
          viewBox="0 0 24 24"
          width="14"
          height="14"
          fill="none"
          stroke="currentColor"
          stroke-width="2.5"
        >
          <polyline points="6 9 12 15 18 9" />
        </svg>
      </button>
      <transition name="cap-slide">
        <div v-if="showCapabilities" class="capabilities__panel">
          <div
            v-for="group in capabilityGroups"
            :key="group.title"
            class="capabilities__group"
          >
            <div class="capabilities__group-title">{{ group.title }}</div>
            <div class="capabilities__items">
              <button
                v-for="item in group.items"
                :key="item.label"
                class="capabilities__item"
                :disabled="(item.needNode && !graphStore.selectedNode) || (item.needDomain && !graphStore.activeDomain)"
                :title="item.needNode && !graphStore.selectedNode ? '请先在图谱中选中一个节点' : (item.needDomain && !graphStore.activeDomain ? '请先在顶部切换到一个知识图谱领域' : item.label)"
                @click="sendQuick(item.prompt)"
              >
                <span class="capabilities__item-emoji">{{ item.emoji }}</span>
                <span>{{ item.label }}</span>
              </button>
            </div>
          </div>
        </div>
      </transition>
    </div>

    <!-- Message list -->
    <div ref="streamRef" class="chat-stream">
      <template v-for="(msg, idx) in chatStore.messages" :key="idx">
      <!-- System banner (used for "loaded historical session" header) —
           rendered as a centered full-width strip, not a chat bubble. -->
      <div
        v-if="msg.role === 'system'"
        class="system-banner"
      >
        <div class="system-banner__text">{{ msg.content }}</div>
      </div>
      <div
        v-else
        class="msg"
        :class="`msg--${msg.role}`"
      >
        <div class="msg__head">
          <span class="msg__avatar">{{ msg.role === 'user' ? 'U' : 'AI' }}</span>
          <span>{{ msg.role === 'user' ? '你' : '助手' }}</span>
        </div>
        <div class="msg__body">
          <!-- Timeline blocks (new format): interleaved text + tools in arrival order -->
          <template v-if="msg.blocks && msg.blocks.length">
            <template v-for="(block, bi) in msg.blocks" :key="bi">
              <!-- Tool card -->
              <div
                v-if="block.kind === 'tool'"
                class="tool-card"
                :class="`tool-card--${block.status}`"
              >
                <button
                  class="tool-card__head"
                  :title="isToolOpen(idx, bi) ? '收起详情' : '展开详情'"
                  @click="toggleTool(idx, bi)"
                >
                  <span class="tool-card__icon">🔧</span>
                  <span class="tool-card__name">{{ toolLabel(block.name) }}</span>
                  <span
                    class="tool-card__status"
                    :class="`tool-card__status--${block.status}`"
                  >
                    {{ statusLabel(block.status) }}
                  </span>
                  <svg
                    class="tool-card__chevron"
                    :class="{ 'tool-card__chevron--open': isToolOpen(idx, bi) }"
                    viewBox="0 0 24 24"
                    width="13"
                    height="13"
                    fill="none"
                    stroke="currentColor"
                    stroke-width="2.5"
                  >
                    <polyline points="6 9 12 15 18 9" />
                  </svg>
                </button>
                <transition name="tool-expand">
                  <div v-if="isToolOpen(idx, bi)" class="tool-card__detail">
                    <div
                      v-if="block.args && Object.keys(block.args).length"
                      class="tool-card__section"
                    >
                      <div class="tool-card__section-title">参数</div>
                      <pre class="tool-card__code">{{ formatArgs(block.args) }}</pre>
                    </div>
                    <div v-if="block.result" class="tool-card__section">
                      <div class="tool-card__section-title">结果</div>
                      <pre class="tool-card__code">{{ block.result }}</pre>
                    </div>
                  </div>
                </transition>
              </div>
              <!-- Text block -->
              <div
                v-else-if="block.kind === 'text' && msg.role !== 'user'"
                class="markdown-body"
                v-html="renderMarkdown(block.text)"
              ></div>
              <div v-else-if="block.kind === 'text'" class="msg__plain">{{ block.text }}</div>
            </template>
          </template>
          <!-- Fallback: old-format messages (toolEvents + content) -->
          <template v-else>
            <!-- Tool cards (collapsible — collapsed by default) -->
            <template v-if="msg.toolEvents && msg.toolEvents.length">
              <div
                v-for="(tool, ti) in groupTools(msg.toolEvents)"
                :key="ti"
                class="tool-card"
                :class="`tool-card--${tool.status}`"
              >
                <button
                  class="tool-card__head"
                  :title="isToolOpen(idx, ti) ? '收起详情' : '展开详情'"
                  @click="toggleTool(idx, ti)"
                >
                  <span class="tool-card__icon">🔧</span>
                  <span class="tool-card__name">{{ toolLabel(tool.name) }}</span>
                  <span
                    class="tool-card__status"
                    :class="`tool-card__status--${tool.status}`"
                  >
                    {{ statusLabel(tool.status) }}
                  </span>
                  <svg
                    class="tool-card__chevron"
                    :class="{ 'tool-card__chevron--open': isToolOpen(idx, ti) }"
                    viewBox="0 0 24 24"
                    width="13"
                    height="13"
                    fill="none"
                    stroke="currentColor"
                    stroke-width="2.5"
                  >
                    <polyline points="6 9 12 15 18 9" />
                  </svg>
                </button>
                <transition name="tool-expand">
                  <div v-if="isToolOpen(idx, ti)" class="tool-card__detail">
                    <div
                      v-if="tool.args && Object.keys(tool.args).length"
                      class="tool-card__section"
                    >
                      <div class="tool-card__section-title">参数</div>
                      <pre class="tool-card__code">{{ formatArgs(tool.args) }}</pre>
                    </div>
                    <div v-if="tool.result" class="tool-card__section">
                      <div class="tool-card__section-title">结果</div>
                      <pre class="tool-card__code">{{ tool.result }}</pre>
                    </div>
                  </div>
                </transition>
              </div>
            </template>
            <!-- Text content -->
            <template v-if="msg.content">
              <div
                v-if="msg.role !== 'user'"
                class="markdown-body"
                v-html="renderMarkdown(msg.content)"
              ></div>
              <div v-else class="msg__plain">{{ msg.content }}</div>
            </template>
          </template>
          <!-- Executing indicator while the agent is still working -->
          <div v-if="msg.pending" class="executing">
            <span class="executing__dots"><span></span><span></span><span></span></span>
            <span class="executing__label">正在执行…</span>
          </div>
        </div>
      </div>
      </template>
    </div>

    <!-- Composer -->
    <footer class="composer">
      <!-- Attachment chips: files the user dropped in this turn.
           They live in ``.agent_memory/tmp/`` until either the
           background cleanup task sweeps them or the user deletes
           them via the × button on the chip. -->
      <div v-if="chatAttachments.length" class="composer__attachments">
        <div
          v-for="att in chatAttachments"
          :key="att.file"
          class="composer__chip"
          :class="{ 'composer__chip--uploading': att.uploading, 'composer__chip--error': att.error }"
          :title="att.path || att.file"
        >
          <el-icon class="composer__chip-icon"><Document /></el-icon>
          <span class="composer__chip-name">{{ att.file }}</span>
          <span v-if="att.uploading" class="composer__chip-status">
            <el-icon class="is-loading"><Loading /></el-icon>
          </span>
          <span v-else-if="att.error" class="composer__chip-status" :title="att.error">⚠</span>
          <button
            class="composer__chip-remove"
            :disabled="att.uploading"
            title="移除"
            @click="removeAttachment(att.file)"
          >×</button>
        </div>
      </div>
      <div class="composer__input-wrapper">
        <textarea
          ref="inputRef"
          v-model="inputText"
          class="composer__input"
          :placeholder="composerPlaceholder"
          rows="2"
          @keydown="onKeydown"
        ></textarea>
        <!-- Floating actions inside the input box (overlay style) -->
        <div class="composer__input-actions">
          <!-- Paperclip: bottom-left -->
          <div class="composer__input-actions-left">
            <input
              ref="fileInputRef"
              type="file"
              multiple
              class="composer__file-input"
              @change="onFilePicked"
            />
            <el-button
              circle
              size="small"
              class="composer__upload-btn"
              title="上传附件（PDF / DOCX / PPTX / XLSX / 图片 / 代码 / 文本，会自动解析给 AI）"
              :disabled="chatStore.isStreaming || isUploadingAttachment"
              @click="triggerFilePicker"
            >
              <el-icon :class="{ 'is-loading': isUploadingAttachment }">
                <component :is="isUploadingAttachment ? Loading : Paperclip" />
              </el-icon>
            </el-button>
          </div>
          <!-- Send / stop: bottom-right -->
          <div class="composer__input-actions-right">
            <el-button
              v-if="chatStore.isStreaming"
              size="small"
              type="danger"
              plain
              @click="chatStore.stopStreaming()"
            >
              中断
            </el-button>
            <el-button
              type="primary"
              size="small"
              :disabled="!canSend"
              @click="onSend"
            >
              发送
            </el-button>
          </div>
        </div>
      </div>
    </footer>

    <!-- Memory search dialog -->
    <MemorySearchDialog ref="memorySearchRef" />
  </aside>
</template>

<script setup lang="ts">
import { ref, computed, nextTick, watch, onMounted, reactive } from 'vue'
import { ElMessage } from 'element-plus'
import { Document, Loading, Paperclip } from '@element-plus/icons-vue'
import { Marked } from 'marked'
import { markedHighlight } from 'marked-highlight'
import hljs from 'highlight.js/lib/common'
import DOMPurify from 'dompurify'
import { useChatStore } from '@/stores/chat'
import { useGraphStore } from '@/stores/graph'
import { healthCheck, uploadTmpFile, deleteTmpFile, listTmpFiles } from '@/api'
import type { ToolEvent } from '@/types/graph'
import { isErrorResult } from '@/utils/tools'
import MemorySearchDialog from '@/components/MemorySearchDialog.vue'

const chatStore = useChatStore()
const graphStore = useGraphStore()

const inputText = ref('')
const inputRef = ref<HTMLTextAreaElement | null>(null)
const streamRef = ref<HTMLElement | null>(null)
const showCapabilities = ref(false)

// ── Chat attachments — files dropped in this turn via the paperclip. ──
// Each entry tracks the staged filename + container path so we can
// (a) render a chip in the composer, (b) tell the agent exactly where
// to read from, and (c) clean up if the user removes the chip before
// the message is sent.
interface ChatAttachment {
  /** Server-side filename (after de-collision suffix). */
  file: string
  /** Original name the user picked on disk (for display). */
  original: string
  /** Container path returned by the upload endpoint. */
  path: string
  size: number
  uploading: boolean
  error: string
}
const chatAttachments = ref<ChatAttachment[]>([])
const fileInputRef = ref<HTMLInputElement | null>(null)
const isUploadingAttachment = computed(() =>
  chatAttachments.value.some((a) => a.uploading),
)
// Sending is allowed when the user has typed something OR has at least
// one attachment ready — uploading-only (no text, no finished file)
// still keeps the button disabled.
const canSend = computed(() => {
  if (chatStore.isStreaming) return false
  if (isUploadingAttachment.value) return false
  if (inputText.value.trim()) return true
  return chatAttachments.value.some((a) => !a.error && !a.uploading)
})

// ── Resizable sidebar: cycle assistant/graph width ratio ──
// Each step is the width the ASSISTANT panel takes of the workspace.
// '0%' is the hidden mode — sidebar collapses to zero.
const WIDTH_STEPS = ['30%', '70%', '0%']
const WIDTH_LABELS = ['30%', '70%', '隐藏']
const WIDTH_KEY = 'kg_sidebar_width_v1'
const widthIdx = ref(loadWidthIdx())
const sidebarHidden = computed(() => widthIdx.value === 2)
const sidebarWidth = computed(() => WIDTH_STEPS[widthIdx.value])
const widthToggleTitle = computed(() => {
  const cur = WIDTH_LABELS[widthIdx.value]
  const next = WIDTH_LABELS[(widthIdx.value + 1) % WIDTH_STEPS.length]
  return `调整宽度（${cur} → ${next}）`
})

function loadWidthIdx(): number {
  try {
    const raw = localStorage.getItem(WIDTH_KEY)
    if (raw !== null) {
      const n = parseInt(raw, 10)
      if (n >= 0 && n < WIDTH_STEPS.length) return n
    }
  } catch {
    /* ignore */
  }
  return 0 // default: assistant 30%, graph 70%
}

function cycleWidth() {
  widthIdx.value = (widthIdx.value + 1) % WIDTH_STEPS.length
  try {
    localStorage.setItem(WIDTH_KEY, String(widthIdx.value))
  } catch {
    /* ignore */
  }
}

// ── Markdown rendering (agent replies) ──
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

function renderMarkdown(content: string): string {
  if (!content) return ''
  const raw = md.parse(content) as string
  return DOMPurify.sanitize(raw, {
    ADD_ATTR: ['class', 'target', 'rel'],
    ADD_TAGS: ['span'],
  })
}

// ── Collapsible tool cards ──
type ToolStatus = 'running' | 'done' | 'error'
interface ToolGroup {
  name: string
  args?: Record<string, any>
  result?: string
  status: ToolStatus
}

// Track which tool cards are expanded, keyed by "msgIdx-toolIdx".
const expandedTools = reactive<Record<string, boolean>>({})

function toolKey(msgIdx: number, toolIdx: number): string {
  return `${msgIdx}-${toolIdx}`
}
function isToolOpen(msgIdx: number, toolIdx: number): boolean {
  return !!expandedTools[toolKey(msgIdx, toolIdx)]
}
function toggleTool(msgIdx: number, toolIdx: number) {
  const k = toolKey(msgIdx, toolIdx)
  expandedTools[k] = !expandedTools[k]
}


// Pair each tool 'call' with its matching 'result' into a single card.
function groupTools(events?: ToolEvent[]): ToolGroup[] {
  if (!events || !events.length) return []
  const groups: ToolGroup[] = []
  for (const ev of events) {
    if (ev.type === 'call') {
      groups.push({ name: ev.name, args: ev.args, status: 'running' })
    } else {
      const g = [...groups].reverse().find(
        (x) => x.name === ev.name && x.status === 'running',
      )
      const status: ToolStatus = isErrorResult(ev.result) ? 'error' : 'done'
      if (g) {
        g.result = ev.result
        g.status = status
      } else {
        groups.push({ name: ev.name, result: ev.result, status })
      }
    }
  }
  return groups
}

// Friendly labels for known tool names.
const TOOL_LABELS: Record<string, string> = {
  // ── 内置工具 ──
  mmx_websearch: '联网搜索',
  read_file: '读取文件',
  write_file: '写入文件',
  ls: '列出文件',
  edit_file: '编辑文件',
  write_todos: '待办清单',
  task: '子任务',
  // ── 图谱管理 ──
  kg_run_skill: '创建领域(Skill)',
  kg_list_domains: '列出领域',
  kg_view_graph: '查看图谱',
  kg_add_node: '添加节点',
  kg_add_subtree: '批量子树',
  kg_fix_links: '修复链接',
  kg_open_node: '打开节点',
  // ── 节点笔记 ──
  kg_wiki_lookup: '维基查询',
  kg_generate_note: '生成笔记',
  kg_search_resources: '搜索资料',
  kg_add_learning_resource: '落盘资料',
  kg_add_learning_resources: '批量落盘',
  kg_view_resources: '查看资料',
  kg_bocha_web_search: '博查搜索',
  kg_set_search_channel: '设置搜索渠道',
  kg_clear_search_channel: '清除搜索渠道',
  // ── 资料管理 ──
  kg_upload_resource_file: '上传文件',
  kg_stage_file: '暂存文件',
  kg_classify_pending: 'AI归类',
  kg_create_node_with_resource: '带资料建节点',
  // ── 学习计划 ──
  kg_add_plan: '添加计划',
  kg_view_plans: '查看计划',
  kg_update_plan_status: '更新计划',
  // ── 活动流 ──
  kg_view_timeline: '活动时间线',
  kg_view_today: '今日活动',
  // ── 工具 ──
  kg_repair_json: 'JSON修复',
  // ── 聊天附件 ──
  kg_list_uploaded_files: '列出已上传文件',
  kg_parse_uploaded_file: '解析上传文件',
  kg_delete_uploaded_file: '删除上传文件',
  kg_auto_place_uploaded_file: '自动归类上传',
}
function toolLabel(name: string): string {
  return TOOL_LABELS[name] ? `${TOOL_LABELS[name]}（${name}）` : name
}

const STATUS_LABELS: Record<ToolStatus, string> = {
  running: '执行中',
  done: '已完成',
  error: '失败',
}
function statusLabel(s: ToolStatus): string {
  return STATUS_LABELS[s]
}

function formatArgs(args: Record<string, any>): string {
  try {
    return JSON.stringify(args, null, 2)
  } catch {
    return String(args)
  }
}


// ── Capability groups (mapped to backend agent tools) ──
// 每个按钮对应 ALL_TOOLS 里的一个真实工具；needNode 表示是否需要先选中节点；
// needDomain 表示是否需要先在顶部切换到一个知识图谱领域（activeDomain 非空）。
const capabilityGroups = [
  {
    title: '图谱管理',
    items: [
      { emoji: '📋', label: '列出所有领域', prompt: '列出所有领域', needNode: false },
      { emoji: '🔍', label: '查看当前图谱', prompt: '查看当前领域的知识图谱', needNode: false },
      { emoji: '🎯', label: '打开节点（按名称）', prompt: '帮我用 kg_open_node 打开指定节点', needNode: false },
      { emoji: '➕', label: '添加子节点', prompt: '帮我在当前选中的节点下添加一个新节点', needNode: true },
      { emoji: '🏗️', label: '批量添加子树', prompt: '帮我在当前选中的节点下批量添加多层子树', needNode: true },
      { emoji: '🔧', label: '清理反向链接', prompt: '清理当前图谱中指向祖先的反向链接', needNode: false },
      { emoji: '✏️', label: '重命名节点', prompt: '帮我重命名当前选中的节点', needNode: true },
      { emoji: '🗑️', label: '删除节点', prompt: '删除当前选中的节点', needNode: true },
      { emoji: '📊', label: '检查图谱质量', prompt: '检查当前领域的图谱质量并给出评分', needNode: false },
    ],
  },
  {
    title: '节点笔记',
    items: [
      { emoji: '📝', label: '生成节点笔记', prompt: '帮我生成当前选中节点的笔记', needNode: true },
      { emoji: '🔄', label: '重建笔记', prompt: '帮我强制重新生成当前选中节点的笔记', needNode: true },
      { emoji: '📖', label: '读取已有笔记', prompt: '读取当前选中节点的笔记', needNode: true },
      { emoji: '🌐', label: '联网搜索资料', prompt: '联网搜一下当前选中节点相关的最新资料', needNode: true },
      { emoji: '📎', label: '查看节点资料', prompt: '查看当前选中节点的学习资料', needNode: true },
    ],
  },
  {
    title: '资料与文件',
    items: [
      { emoji: '📎', label: '查看已上传附件', prompt: '用 kg_list_uploaded_files 看看 tmp 目录里有哪些文件', needNode: false },
      { emoji: '📄', label: '解析上传文件', prompt: '用 kg_parse_uploaded_file 解析我刚才上传的文件并总结', needNode: false },
      { emoji: '🤖', label: '自动归类并放入节点', prompt: '调用 kg_auto_place_uploaded_file 自动解析我刚上传到 tmp 的最新文件并放到当前领域最合适的节点下（需要时新建节点）', needNode: false, needDomain: true },
      { emoji: '📤', label: '上传暂存文件', prompt: '把本地文件暂存到 staging 目录', needNode: false },
      { emoji: '🤖', label: 'AI 归类暂存文件', prompt: '扫描 staging 目录，给出文件归位建议', needNode: false },
      { emoji: '📥', label: '上传文件并新建节点', prompt: '基于本地文件新建一个节点并挂资料', needNode: false },
    ],
  },
  {
    title: '学习计划',
    items: [
      { emoji: '➕', label: '添加学习计划', prompt: '为当前选中节点添加学习计划', needNode: true },
      { emoji: '📋', label: '查看学习计划', prompt: '查看当前选中节点的学习计划', needNode: true },
      { emoji: '✅', label: '更新计划状态', prompt: '更新当前选中节点的学习计划状态', needNode: true },
      { emoji: '🗑️', label: '删除计划', prompt: '删除当前选中节点下的某条学习计划', needNode: true },
    ],
  },
  {
    title: '领域生成',
    items: [
      { emoji: '🚀', label: '创建新领域', prompt: '帮我创建一个新的知识图谱领域', needNode: false },
      { emoji: '⏱️', label: '查询生成进度', prompt: '查询最近的图谱生成任务进度', needNode: false },
      { emoji: '📜', label: '查看构建日志', prompt: '查看当前领域的构建日志', needNode: false },
    ],
  },
  {
    title: '活动流',
    items: [
      { emoji: '📅', label: '活动时间线', prompt: '查看当前领域的活动时间线（可按日期/类型/节点过滤）', needNode: false },
    ],
  },
]

// ── Composer placeholder adapts to selection ──
const composerPlaceholder = computed(() => {
  const node = graphStore.selectedNode
  if (node) {
    return `针对「${node.name}」提问…（Shift+Enter 换行）`
  }
  return '问我任何关于知识图谱的问题…（Shift+Enter 换行）'
})

// ── Auto-grow composer: keep textarea a hair taller than its content so
// long drafts stay fully visible above the floating upload/send buttons,
// instead of scrolling INSIDE the textarea and looking like the buttons
// are covering the last line. Cap at ``MAX_INPUT_H`` so a pasted essay
// doesn't push the conversation list off-screen — beyond the cap we
// fall back to an in-textarea scrollbar. ──
const MAX_INPUT_H = 220
function autoGrow() {
  const el = inputRef.value
  if (!el) return
  el.style.height = 'auto' // reset so scrollHeight reflects content, not current height
  const desired = Math.min(el.scrollHeight, MAX_INPUT_H)
  el.style.height = `${desired}px`
  el.style.overflowY = el.scrollHeight > MAX_INPUT_H ? 'auto' : 'hidden'
}

// ── Build context string for the agent ──
function buildContext(): string {
  const domain = graphStore.activeDomain
  if (!domain) return ''

  const parts: string[] = [`用户当前正在浏览知识图谱「${domain}」`]
  const node = graphStore.selectedNode

  if (node) {
    // Build hierarchy path from drill stack + node name
    const pathParts = [domain, ...graphStore.drillStack, node.name]
    // Deduplicate (domain might appear in drillStack)
    const dedupedPath: string[] = []
    for (const p of pathParts) {
      if (dedupedPath.length === 0 || dedupedPath[dedupedPath.length - 1] !== p) {
        dedupedPath.push(p)
      }
    }
    const hierarchyPath = dedupedPath.join(' > ')

    parts.push(
      `已选中节点「${node.name}」（层级：${node.tier || `L${node.level ?? 1}`}，子节点数：${node.childCount ?? 0}）`,
    )
    parts.push(`节点层级路径：${hierarchyPath}`)
    parts.push('请在回答时考虑用户当前选中的节点和关注点，优先针对该节点给出建议。')
  } else {
    parts.push('当前未选中任何节点，请根据整体图谱回答。')
  }

  return parts.join('；')
}

// Check agent health on mount
onMounted(async () => {
  try {
    const h = await healthCheck()
    chatStore.agentAvailable = h.agent_available
    if (!h.agent_available) {
      ElMessage.warning(
        'DeepAgent 后端未就绪（聊天功能不可用，但图谱 CRUD 正常）',
      )
    }
  } catch {
    chatStore.agentAvailable = false
  }
})

// Auto-scroll to bottom when messages change
watch(
  () => chatStore.messages,
  () => {
    nextTick(() => {
      if (streamRef.value) {
        streamRef.value.scrollTop = streamRef.value.scrollHeight
      }
    })
  },
  { deep: true },
)

// Re-size the composer textarea to fit its current content. ``flush: 'post'``
// runs AFTER the DOM has applied the new ``inputText`` value, so scrollHeight
// already reflects the change — no ``nextTick`` dance needed inside.
watch(
  inputText,
  () => {
    autoGrow()
  },
  { immediate: true, flush: 'post' },
)

function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault()
    onSend()
  }
}

// ── Chat attachment upload (paperclip) ──
function triggerFilePicker() {
  fileInputRef.value?.click()
}

async function onFilePicked(e: Event) {
  const target = e.target as HTMLInputElement
  const files = target.files ? Array.from(target.files) : []
  // Reset the input so picking the SAME file twice still fires change.
  target.value = ''
  for (const file of files) {
    await uploadOneAttachment(file)
  }
}

async function uploadOneAttachment(file: File) {
  // Optimistic chip with `uploading: true` so the user sees immediate
  // feedback even on slow links.  The chip is removed if the upload
  // fails outright, otherwise it stays put until the user sends the
  // message (and then optionally removes via ×).
  const placeholder: ChatAttachment = reactive({
    file: file.name,
    original: file.name,
    path: '',
    size: file.size,
    uploading: true,
    error: '',
  })
  chatAttachments.value.push(placeholder)
  try {
    const res = await uploadTmpFile(file)
    placeholder.file = res.item.file
    placeholder.path = res.item.path
    placeholder.size = res.item.size
  } catch (err: any) {
    placeholder.uploading = false
    placeholder.error = err?.message || '上传失败'
    ElMessage.error(`上传失败：${file.name}`)
  } finally {
    placeholder.uploading = false
  }
}

async function removeAttachment(file: string) {
  const idx = chatAttachments.value.findIndex((a) => a.file === file)
  if (idx === -1) return
  const att = chatAttachments.value[idx]
  chatAttachments.value.splice(idx, 1)
  // Best-effort cleanup on the server — don't block the UI if the
  // server rejects the delete (e.g. already cleaned up by the
  // periodic sweep).
  if (att.path && !att.error) {
    try {
      await deleteTmpFile(att.file)
    } catch {
      /* ignore — server-side cleanup will catch it later */
    }
  }
}

/** Image MIME types MiniMax-M3 accepts — mirrors the backend whitelist. */
const IMAGE_EXT_RE = /\.(png|jpe?g|gif|webp)$/i

/** Drain non-image attachments into the outgoing message.  Successful
 *  uploads become a brief machine-readable appendix at the top of the
 *  user's message so the LLM knows the files exist and where to read
 *  them.  Image attachments are NOT listed here — the backend pipes
 *  them straight to MiniMax-M3 as multimodal content blocks, so the
 *  model can see them without going through `kg_parse_uploaded_file`. */
function buildAttachmentPrompt(ready: ChatAttachment[]): string {
  if (ready.length === 0) return ''
  const nonImage = ready.filter((a) => !IMAGE_EXT_RE.test(a.file))
  if (nonImage.length === 0) return ''
  const lines = nonImage.map(
    (a) =>
      `- ${a.file}（${(a.size / 1024).toFixed(1)} KB，容器路径：\`${a.path}\`）`,
  )
  return (
    '\n\n[用户附件 — 已上传到 tmp 目录，可直接读取]\n' +
    lines.join('\n') +
    '\n\n请用 `kg_parse_uploaded_file(filename)` 读取内容再回答用户问题。'
  )
}

// Pull any leftover attachments from a previous session / accidental
// refresh — they're transient state but cheap to clean up so the chip
// list doesn't accumulate cruft.
async function pruneStaleAttachments() {
  try {
    const { items } = await listTmpFiles()
    const known = new Set(chatAttachments.value.map((a) => a.file))
    // Don't actually delete anything — listing is read-only. We just
    // want to surface "you have N files from earlier" to the user.
    if (items.length > 0 && chatAttachments.value.length === 0) {
      // (No UI for now; future enhancement: 「载入上次会话的附件」 button.)
      void known
    }
  } catch {
    /* ignore — best-effort */
  }
}

async function onSend() {
  const text = inputText.value.trim()
  const readyAttachments = chatAttachments.value.filter(
    (a) => !a.error && a.path && !a.uploading,
  )
  // The backend turns images into Anthropic-format image content
  // blocks (so MiniMax-M3 actually sees the pixels); non-image
  // attachments still get a text reference so the agent can call
  // ``kg_parse_uploaded_file`` on them.  We DON'T bake the filename
  // list into the user-visible message any more — that would just
  // duplicate info the backend already injects.
  const attachmentNote = buildAttachmentPrompt(readyAttachments)
  const fullText = text + attachmentNote
  const attachmentNames = readyAttachments.map((a) => a.file)
  if ((!text && attachmentNames.length === 0) || chatStore.isStreaming) return
  if (isUploadingAttachment.value) return
  inputText.value = ''
  const ctx = buildContext()
  // Pass no threadId — the chat store will use its current
  // ``sessionId`` (minting one on first use), keeping every "新会话"
  // click fully isolated.
  await chatStore.sendMessage(
    fullText,
    undefined,
    ctx,
    attachmentNames.length ? attachmentNames : undefined,
  )
  // Successfully sent — chips are now "consumed" from the user's
  // POV. We keep them in the list so the user can still remove them
  // (which now triggers a server-side delete); they get cleared on
  // the next session / when the user clicks ×.
  // After agent responds, refresh graph in case the agent modified it
  await graphStore.refreshGraph()
}

function sendQuick(prompt: string) {
  inputText.value = prompt
  onSend()
}

/** "新会话" button handler — like most chat UIs, this both clears
 *  the visible chat and rotates to a fresh session id so the agent
 *  loses the previous LangGraph thread + conversation context. */
async function onNewSession() {
  if (chatStore.isStreaming) {
    ElMessage.info('请先等待当前回复结束')
    return
  }
  await chatStore.startNewSession()
  ElMessage.success('已开启新会话')
}

const memorySearchRef = ref<InstanceType<typeof MemorySearchDialog> | null>(null)

function openMemorySearch() {
  memorySearchRef.value?.open()
}
</script>

<style scoped>
.sidebar {
  width: var(--sidebar-w);
  min-width: 320px;
  background: var(--bg-secondary);
  border-left: 1px solid var(--border-color);
  display: flex;
  flex-direction: column;
  flex-shrink: 0;
  transition: width 0.25s ease;
  overflow: hidden;
}

.sidebar--hidden {
  min-width: 0;
  border-left: none;
  overflow: visible;
}

.sidebar__reveal-tab {
  position: fixed;
  right: 0;
  top: 50%;
  transform: translateY(-50%);
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 4px;
  padding: 10px 6px;
  background: var(--bg-secondary);
  border: 1px solid var(--border-color);
  border-right: none;
  border-radius: 8px 0 0 8px;
  cursor: pointer;
  color: var(--text-secondary);
  font-size: 11px;
  font-weight: 500;
  writing-mode: horizontal-tb;
  z-index: 100;
  box-shadow: -2px 0 8px rgba(0, 0, 0, 0.15);
  transition: background 0.15s, color 0.15s;
}

.sidebar__reveal-tab:hover {
  background: var(--bg-hover, var(--bg-primary));
  color: var(--text-primary);
}

.sidebar__actions {
  display: flex;
  align-items: center;
  gap: 6px;
}

.sidebar__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 16px;
  border-bottom: 1px solid var(--border-color);
  flex-shrink: 0;
}

.sidebar__title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-weight: 600;
  font-size: 14px;
}

.badge-ai {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 2px 8px;
  background: linear-gradient(135deg, var(--accent-purple), var(--accent-blue));
  border-radius: 6px;
  font-size: 10px;
  font-weight: 700;
  color: white;
}

.badge-history {
  display: inline-flex;
  align-items: center;
  padding: 2px 8px;
  background: var(--bg-tertiary);
  border: 1px solid var(--border-color);
  border-radius: 6px;
  font-size: 10.5px;
  font-weight: 500;
  color: var(--accent-cyan);
  font-family: 'JetBrains Mono', monospace;
  cursor: help;
}

/* ── Collapsible capabilities ── */
.capabilities {
  border-bottom: 1px solid var(--border-color);
  flex-shrink: 0;
}

.capabilities__toggle {
  display: flex;
  align-items: center;
  justify-content: space-between;
  width: 100%;
  padding: 8px 16px;
  background: none;
  border: none;
  color: var(--text-secondary);
  font-size: 12.5px;
  font-weight: 500;
  cursor: pointer;
  transition: background 0.15s, color 0.15s;
}
.capabilities__toggle:hover {
  background: var(--bg-hover);
  color: var(--text-primary);
}
.capabilities__toggle-label {
  display: flex;
  align-items: center;
  gap: 4px;
}
.capabilities__chevron {
  transition: transform 0.2s ease;
  flex-shrink: 0;
}
.capabilities__chevron--open {
  transform: rotate(180deg);
}

.capabilities__panel {
  padding: 4px 12px 10px;
}

.capabilities__group {
  margin-bottom: 8px;
}
.capabilities__group:last-child {
  margin-bottom: 0;
}

.capabilities__group-title {
  font-size: 10.5px;
  font-weight: 600;
  color: var(--text-muted);
  text-transform: uppercase;
  letter-spacing: 0.5px;
  padding: 4px 4px 2px;
}

.capabilities__items {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}

.capabilities__item {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 4px 8px;
  background: var(--bg-tertiary);
  border: 1px solid var(--border-color);
  border-radius: 6px;
  color: var(--text-secondary);
  font-size: 11.5px;
  cursor: pointer;
  transition: all 0.15s;
  white-space: nowrap;
}
.capabilities__item:hover:not(:disabled) {
  background: var(--accent-blue);
  border-color: var(--accent-blue);
  color: #fff;
}
.capabilities__item:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}
.capabilities__item-emoji {
  font-size: 12px;
}

/* ── Slide transition ── */
.cap-slide-enter-active,
.cap-slide-leave-active {
  transition: all 0.2s ease;
  overflow: hidden;
}
.cap-slide-enter-from,
.cap-slide-leave-to {
  opacity: 0;
  max-height: 0;
  padding-top: 0;
  padding-bottom: 0;
}
.cap-slide-enter-to,
.cap-slide-leave-from {
  opacity: 1;
  max-height: 600px;
}

/* ── Composer ── */
.composer {
  border-top: 1px solid var(--border-color);
  padding: 12px 16px;
  flex-shrink: 0;
  background: var(--bg-secondary);
}

.composer__input-wrapper {
  position: relative;
}

.composer__input {
  width: 100%;
  background: var(--bg-tertiary);
  border: 1px solid var(--border-color);
  border-radius: 8px;
  padding: 10px 12px 56px; /* extra bottom padding so text doesn't slip under the floating buttons — generous gap to keep text visually clear of the upload/send row */
  color: var(--text-primary);
  font-size: 13.5px;
  font-family: inherit;
  resize: none;
  outline: none;
  transition: border-color 0.15s;
  box-sizing: border-box;
}

.composer__input:focus {
  border-color: var(--accent-blue);
}

.composer__input::placeholder {
  color: var(--text-muted);
}

.composer__input-actions {
  position: absolute;
  left: 8px;
  right: 8px;
  bottom: 8px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  pointer-events: none; /* let clicks pass through to textarea */
}

.composer__input-actions-left,
.composer__input-actions-right {
  display: flex;
  align-items: center;
  gap: 8px;
  pointer-events: auto; /* but the actual buttons stay clickable */
}

.composer__upload-btn {
  flex-shrink: 0;
}

.composer__row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: 8px;
}

.composer__left {
  display: flex;
  align-items: center;
  gap: 8px;
}

.composer__hint {
  font-size: 11px;
  color: var(--text-muted);
}

.composer__actions {
  display: flex;
  gap: 8px;
}

/* ── Composer attachment chips (paperclip uploads) ── */
.composer__file-input {
  display: none;
}

.composer__attachments {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-bottom: 8px;
}

.composer__chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 3px 6px 3px 8px;
  background: var(--bg-tertiary);
  border: 1px solid var(--border-color);
  border-radius: 6px;
  font-size: 11.5px;
  color: var(--text-secondary);
  max-width: 220px;
  transition: opacity 0.15s, border-color 0.15s;
}

.composer__chip--uploading {
  border-color: var(--accent-blue);
  opacity: 0.85;
}

.composer__chip--error {
  border-color: #d33;
  color: #d33;
}

.composer__chip-icon {
  flex-shrink: 0;
  font-size: 13px;
  color: var(--accent-blue);
}

.composer__chip-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 160px;
}

.composer__chip-status {
  display: inline-flex;
  align-items: center;
  flex-shrink: 0;
}

.composer__chip-remove {
  flex-shrink: 0;
  background: none;
  border: none;
  color: var(--text-muted);
  font-size: 14px;
  line-height: 1;
  cursor: pointer;
  padding: 0 4px;
  border-radius: 3px;
}
.composer__chip-remove:hover:not(:disabled) {
  background: var(--bg-hover);
  color: var(--text-primary);
}
.composer__chip-remove:disabled {
  cursor: not-allowed;
  opacity: 0.5;
}

/* ── Plain (user) message text ── */
.msg__plain {
  white-space: pre-wrap;
  word-break: break-word;
}

/* ── System banner (e.g. "loaded historical session") ── */
.system-banner {
  display: flex;
  justify-content: center;
  margin: 12px 0;
}
.system-banner__text {
  max-width: 92%;
  padding: 8px 14px;
  background: linear-gradient(
    135deg,
    rgba(76, 125, 255, 0.08),
    rgba(166, 120, 255, 0.08)
  );
  border: 1px dashed var(--accent-blue);
  border-radius: 8px;
  font-size: 11.5px;
  line-height: 1.55;
  color: var(--text-secondary);
  text-align: center;
  white-space: pre-wrap;
  word-break: break-word;
}

/* ── Markdown body (rendered agent replies) ── */
.markdown-body {
  font-size: 13.5px;
  line-height: 1.7;
  color: var(--text-primary);
  word-break: break-word;
}

.markdown-body :deep(> *:first-child) {
  margin-top: 0;
}
.markdown-body :deep(> *:last-child) {
  margin-bottom: 0;
}

.markdown-body :deep(h1),
.markdown-body :deep(h2),
.markdown-body :deep(h3),
.markdown-body :deep(h4) {
  font-weight: 700;
  line-height: 1.35;
  color: var(--text-primary);
}
.markdown-body :deep(h1) {
  font-size: 1.35em;
  margin: 1.1em 0 0.55em;
  padding-bottom: 0.35em;
  border-bottom: 1px solid var(--border-color);
}
.markdown-body :deep(h2) {
  font-size: 1.2em;
  margin: 1.1em 0 0.5em;
  padding-left: 0.6em;
  border-left: 3px solid var(--accent-purple);
}
.markdown-body :deep(h3) {
  font-size: 1.08em;
  margin: 1em 0 0.45em;
  color: var(--accent-cyan);
}
.markdown-body :deep(h4) {
  font-size: 1em;
  margin: 0.9em 0 0.4em;
  color: var(--accent-amber);
}

.markdown-body :deep(p) {
  margin: 0.6em 0;
}

.markdown-body :deep(ul),
.markdown-body :deep(ol) {
  margin: 0.6em 0;
  padding-left: 1.5em;
}
.markdown-body :deep(li) {
  margin: 0.3em 0;
}
.markdown-body :deep(ol > li::marker) {
  color: var(--accent-amber);
  font-weight: 700;
}
.markdown-body :deep(ul > li::marker) {
  color: var(--accent-blue);
}

.markdown-body :deep(blockquote) {
  margin: 0.8em 0;
  padding: 0.55em 0.9em;
  background: rgba(76, 125, 255, 0.06);
  border-left: 3px solid var(--accent-blue);
  border-radius: 0 8px 8px 0;
  color: var(--text-secondary);
}
.markdown-body :deep(blockquote p) {
  margin: 0.25em 0;
}

.markdown-body :deep(code) {
  font-family: 'JetBrains Mono', 'Fira Code', 'Consolas', monospace;
  font-size: 0.86em;
  padding: 0.12em 0.4em;
  background: var(--bg-tertiary);
  border: 1px solid var(--border-color);
  border-radius: 4px;
  color: var(--accent-amber);
}

.markdown-body :deep(pre) {
  margin: 0.8em 0;
  padding: 0;
  background: var(--bg-primary);
  border: 1px solid var(--border-color);
  border-radius: 8px;
  overflow: hidden;
}
.markdown-body :deep(pre code) {
  display: block;
  padding: 0.85em 1em;
  background: transparent;
  border: none;
  color: var(--text-primary);
  font-size: 12.5px;
  line-height: 1.6;
  overflow-x: auto;
}

.markdown-body :deep(a) {
  color: var(--accent-blue);
  text-decoration: none;
  border-bottom: 1px solid rgba(76, 125, 255, 0.4);
}
.markdown-body :deep(a:hover) {
  color: var(--accent-cyan);
  border-bottom-color: var(--accent-cyan);
}

.markdown-body :deep(table) {
  width: 100%;
  margin: 0.8em 0;
  border-collapse: separate;
  border-spacing: 0;
  font-size: 0.92em;
  border: 1px solid var(--border-color);
  border-radius: 8px;
  overflow: hidden;
}
.markdown-body :deep(th),
.markdown-body :deep(td) {
  padding: 0.5em 0.75em;
  text-align: left;
  border-bottom: 1px solid var(--border-color);
}
.markdown-body :deep(tr:last-child td) {
  border-bottom: none;
}
.markdown-body :deep(th) {
  background: var(--bg-tertiary);
  font-weight: 700;
}

.markdown-body :deep(hr) {
  border: none;
  height: 1px;
  background: var(--border-color);
  margin: 1.2em 0;
}

.markdown-body :deep(strong) {
  font-weight: 700;
  color: var(--text-primary);
}
.markdown-body :deep(em) {
  font-style: italic;
  color: var(--text-secondary);
}

.markdown-body :deep(img) {
  max-width: 100%;
  border-radius: 6px;
  border: 1px solid var(--border-color);
}
</style>
