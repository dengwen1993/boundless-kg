<template>
  <el-dialog
    v-model="visible"
    title="历史会话查询"
    width="720px"
    :close-on-click-modal="false"
    destroy-on-close
    class="memory-dialog"
  >
    <!-- Search bar -->
    <div class="memory-search-bar">
      <el-input
        v-model="query"
        placeholder="输入关键词搜索历史对话…"
        clearable
        @keyup.enter="onSearch"
        @clear="onClear"
      >
        <template #prefix>
          <span style="font-size:14px">🔍</span>
        </template>
        <template #append>
          <el-button :loading="searching" @click="onSearch">搜索</el-button>
        </template>
      </el-input>
      <div class="memory-search-opts">
        <span class="opt-label">搜索范围</span>
        <el-radio-group v-model="days" size="small" @change="onSearch">
          <el-radio-button :value="1">今天</el-radio-button>
          <el-radio-button :value="3">3天</el-radio-button>
          <el-radio-button :value="7">7天</el-radio-button>
          <el-radio-button :value="30">30天</el-radio-button>
        </el-radio-group>
      </div>
    </div>

    <!-- Load by Session ID — paste an id (e.g. d7e4cfebba8f4fc8)
         and click "载入" to bring that conversation back into the
         chat panel + hand it to the agent via kg_recall_session. -->
    <div class="memory-load-by-id">
      <el-input
        v-model="loadById"
        placeholder="按 Session ID 直接载入历史会话（如 d7e4cfebba8f4fc8）"
        clearable
        size="small"
        @keyup.enter="onLoadById"
        @clear="loadByIdError = ''"
      >
        <template #prefix>
          <span style="font-size:13px">🆔</span>
        </template>
        <template #append>
          <el-button :loading="loadingById" @click="onLoadById">载入</el-button>
        </template>
      </el-input>
      <div v-if="loadByIdError" class="memory-load-error">{{ loadByIdError }}</div>
    </div>

    <!-- Quick recall -->
    <div class="memory-quick">
      <el-button text size="small" :loading="recalling" @click="onRecall">
        📋 查看最近对话摘要
      </el-button>
    </div>

    <!-- Status -->
    <div v-if="errorMsg" class="memory-error">{{ errorMsg }}</div>

    <!-- Recall result -->
    <div v-if="recallResult" class="memory-recall">
      <div class="memory-recall__head">
        <span><strong>{{ recallResult.date }}</strong> · {{ recallResult.session }} · 共 {{ recallResult.total_records ?? recallResult.total_lines }} 条记录（显示最后 {{ recallResult.shown_records ?? recallResult.shown_lines }} 条）</span>
        <el-button text size="small" @click="recallResult = null">收起</el-button>
      </div>
      <pre class="memory-recall__content">{{ recallResult.content }}</pre>
    </div>

    <!-- Search results -->
    <div v-if="searchResult" class="memory-results">
      <div class="memory-results__summary">
        关于「<strong>{{ searchResult.query }}</strong>」找到 <strong>{{ searchResult.total_matches }}</strong> 条匹配
      </div>
      <div
        v-for="(m, i) in searchResult.matches"
        :key="i"
        class="memory-match"
      >
        <div class="memory-match__head">
          <span class="memory-match__loc">{{ m.date }} · {{ m.session }} · L{{ m.line }}</span>
          <el-button
            link
            size="small"
            class="memory-match__load"
            :loading="loadingSessionId === m.session"
            @click="onLoadSession(m.session, m.date)"
          >
            📥 载入此会话
          </el-button>
        </div>
        <pre class="memory-match__context">{{ m.context }}</pre>
      </div>
      <div v-if="searchResult.total_matches === 0" class="memory-empty">
        没有找到匹配结果
      </div>
    </div>

    <!-- Sessions list (when no search yet) -->
    <div v-if="!searchResult && !recallResult && sessions.length" class="memory-sessions">
      <div class="memory-sessions__title">最近会话</div>
      <div
        v-for="s in sessions"
        :key="`${s.date}-${s.session}`"
        class="memory-session-item"
        :class="{ 'memory-session-item--loading': loadingSessionId === s.session }"
        :title="`点击载入会话 ${s.session}`"
        @click="onLoadSession(s.session, s.date)"
      >
        <span class="memory-session-item__date">{{ s.date }}</span>
        <span class="memory-session-item__id">{{ s.session }}</span>
        <span class="memory-session-item__size">{{ formatSize(s.size) }}</span>
        <span class="memory-session-item__time">{{ s.mtime }}</span>
        <span class="memory-session-item__action">
          {{ loadingSessionId === s.session ? '载入中…' : '载入 ▶' }}
        </span>
      </div>
    </div>

    <template #footer>
      <el-button @click="visible = false">关闭</el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import {
  listMemorySessions,
  searchMemory,
  recallMemory,
  type MemorySearchResult,
  type MemoryRecallResult,
  type SessionInfo,
} from '@/api'
import { ElMessage } from 'element-plus'
import { useChatStore } from '@/stores/chat'

const chatStore = useChatStore()

const visible = ref(false)
const query = ref('')
const days = ref(7)
const searching = ref(false)
const recalling = ref(false)
const errorMsg = ref('')
const searchResult = ref<MemorySearchResult | null>(null)
const recallResult = ref<MemoryRecallResult | null>(null)
const sessions = ref<SessionInfo[]>([])

// ── Load-by-id state ──
// ``loadById`` is the standalone input field where the user pastes a
// raw session id (e.g. d7e4cfebba8f4fc8) and clicks "载入".  ``loadingSessionId``
// tracks whichever session is being loaded via either path (id field
// OR row click) so the right row can show its own spinner.
const loadById = ref('')
const loadingById = ref(false)
const loadByIdError = ref('')
const loadingSessionId = ref('')

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

function open() {
  visible.value = true
  searchResult.value = null
  recallResult.value = null
  errorMsg.value = ''
  loadByIdError.value = ''
  loadSessions()
}

async function loadSessions() {
  try {
    const r = await listMemorySessions(14)
    sessions.value = r.sessions.slice(0, 20)
  } catch {
    /* sessions list is optional */
  }
}

async function onSearch() {
  if (!query.value.trim()) return
  searching.value = true
  errorMsg.value = ''
  searchResult.value = null
  recallResult.value = null
  try {
    searchResult.value = await searchMemory(query.value.trim(), days.value, 15)
  } catch (e: any) {
    errorMsg.value = e?.message || '搜索失败'
  } finally {
    searching.value = false
  }
}

function onClear() {
  searchResult.value = null
  errorMsg.value = ''
}

async function onRecall() {
  recalling.value = true
  errorMsg.value = ''
  searchResult.value = null
  recallResult.value = null
  try {
    recallResult.value = await recallMemory(80)
  } catch (e: any) {
    errorMsg.value = e?.message || '回顾失败'
  } finally {
    recalling.value = false
  }
}

/** Validate a session id (16-char hex, per backend `_sanitize_session_id`). */
function validSessionId(s: string): boolean {
  return /^[a-f0-9]{16}$/i.test(s)
}

/** Load a historical session by raw id (from the "load-by-id" input). */
async function onLoadById() {
  const id = loadById.value.trim()
  if (!id) return
  loadByIdError.value = ''
  if (!validSessionId(id)) {
    loadByIdError.value = `无效的 Session ID（应是 16 位 hex，实际 "${id}"）`
    return
  }
  await _loadHistorical(id)
}

/** Load a historical session by clicking a row or match button. */
async function onLoadSession(sessionId: string, date?: string) {
  loadByIdError.value = ''
  await _loadHistorical(sessionId, date)
}

async function _loadHistorical(sessionId: string, date?: string) {
  loadingById.value = true
  loadingSessionId.value = sessionId
  try {
    const ok = await chatStore.loadHistoricalSession(sessionId, date)
    if (ok) {
      ElMessage.success(`已载入会话 ${sessionId}`)
      visible.value = false
    } else {
      loadByIdError.value = `无法加载 ${sessionId}（可能文件不存在）`
    }
  } catch (e: any) {
    loadByIdError.value = e?.message || '载入失败'
  } finally {
    loadingById.value = false
    loadingSessionId.value = ''
  }
}

defineExpose({ open })
</script>

<style scoped>
.memory-dialog :deep(.el-dialog__body) {
  max-height: 60vh;
  overflow-y: auto;
  padding-top: 8px;
}

.memory-search-bar {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.memory-search-opts {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 12px;
}

.opt-label {
  color: var(--text-muted, #909399);
}

.memory-quick {
  margin: 10px 0 4px;
}

/* ── Load by Session ID ── */
.memory-load-by-id {
  margin: 12px 0 4px;
  padding: 8px 10px;
  background: var(--bg-tertiary, rgba(76, 125, 255, 0.04));
  border: 1px dashed var(--border-color, #dcdfe6);
  border-radius: 6px;
}
.memory-load-error {
  margin-top: 4px;
  font-size: 12px;
  color: #f56c6c;
}

.memory-error {
  color: #f56c6c;
  font-size: 13px;
  padding: 8px 0;
}

/* ── Recall ── */
.memory-recall__head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 12.5px;
  color: var(--text-secondary);
  margin-bottom: 8px;
  padding: 6px 12px;
  background: var(--bg-tertiary, #f0f2f5);
  border-radius: 6px;
}

.memory-recall__content {
  background: var(--bg-primary, #1e1e2e);
  border: 1px solid var(--border-color, #e4e7ed);
  border-radius: 8px;
  padding: 12px 16px;
  font-size: 12px;
  line-height: 1.6;
  max-height: 40vh;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-word;
  color: var(--text-primary, #e0e0e0);
}

/* ── Search results ── */
.memory-results__summary {
  font-size: 13px;
  padding: 8px 0 4px;
  color: var(--text-secondary);
}

.memory-match {
  margin: 8px 0;
  border: 1px solid var(--border-color, #e4e7ed);
  border-radius: 8px;
  overflow: hidden;
}

.memory-match__head {
  padding: 4px 12px;
  font-size: 11.5px;
  color: var(--text-muted);
  background: var(--bg-tertiary, #f0f2f5);
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.memory-match__load {
  font-size: 11.5px !important;
  padding: 0 6px !important;
}

.memory-match__loc {
  font-family: 'JetBrains Mono', monospace;
}

.memory-match__context {
  padding: 8px 12px;
  font-size: 12px;
  line-height: 1.5;
  background: var(--bg-primary, #1e1e2e);
  color: var(--text-primary, #e0e0e0);
  margin: 0;
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 200px;
  overflow: auto;
}

.memory-empty {
  text-align: center;
  color: var(--text-muted);
  padding: 24px 0;
}

/* ── Sessions list ── */
.memory-sessions {
  margin-top: 12px;
}

.memory-sessions__title {
  font-size: 13px;
  font-weight: 600;
  margin-bottom: 6px;
}

.memory-session-item {
  display: flex;
  gap: 16px;
  padding: 6px 8px;
  font-size: 12.5px;
  border-bottom: 1px solid var(--border-color, rgba(0,0,0,0.06));
  font-family: 'JetBrains Mono', monospace;
  cursor: pointer;
  transition: background 0.12s;
}

.memory-session-item:hover {
  background: var(--bg-hover, rgba(76, 125, 255, 0.06));
}

.memory-session-item--loading {
  opacity: 0.6;
  cursor: progress;
}

.memory-session-item__action {
  color: var(--accent-blue, #409eff);
  min-width: 60px;
  text-align: right;
  font-size: 11.5px;
}

.memory-session-item__date {
  color: var(--accent-blue, #409eff);
  min-width: 90px;
}

.memory-session-item__id {
  flex: 1;
  color: var(--text-primary);
}

.memory-session-item__size {
  color: var(--text-muted);
  min-width: 60px;
  text-align: right;
}

.memory-session-item__time {
  color: var(--text-muted);
  min-width: 120px;
  text-align: right;
}
</style>
