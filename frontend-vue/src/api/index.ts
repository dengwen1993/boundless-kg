import axios from 'axios'
import type {
  GraphData,
  DomainInfo,
  AgentEvent,
  NodeResources,
  WebResource,
  UploadResource,
  StudyMaterialItem,
  PlanItem,
  NodePlans,
  Activity,
  TimelineResponse,
  PlanAction,
} from '@/types/graph'

const http = axios.create({
  baseURL: '/api',
  timeout: 30000,
})

// ============================================================
// Memory / session search types
// ============================================================

export interface SessionInfo {
  session: string
  date: string
  size: number
  mtime: string
}

export interface MemoryMatch {
  line: number
  match: string
  context: string
  session: string
  date: string
}

export interface MemorySearchResult {
  query: string
  searched_days: number
  total_matches: number
  matches: MemoryMatch[]
}

export interface MemoryRecallResult {
  session: string
  date: string
  // Records (JSONL lines) — not raw text lines.  Older payloads
  // exposed ``total_lines`` / ``shown_lines``; the backend now reports
  // records, so the newer fields are the source of truth.
  total_lines?: number
  shown_lines?: number
  total_records?: number
  shown_records?: number
  content: string
}

/**
 * Single record parsed out of a session's JSONL file.
 * ``type`` is one of: ``user`` / ``agent`` / ``tool_call`` /
 * ``tool_result`` / ``error``.  Other fields are type-specific
 * (``content`` for user/agent, ``name``+``args`` for tool_call,
 * ``name``+``result`` for tool_result, ``message`` for error).
 */
export interface MemoryRecord {
  type: string
  ts?: string
  session_id?: string
  content?: string
  name?: string
  args?: Record<string, any>
  result?: string
  message?: string
}

export interface MemorySessionDetail {
  ok: boolean
  session: string
  date?: string
  total_records?: number
  records: MemoryRecord[]
  error?: string
  hint?: string
}

// ============================================================
// Memory API
// ============================================================

/** List available session files */
export async function listMemorySessions(
  days: number = 14,
): Promise<{ sessions: SessionInfo[]; total: number }> {
  const { data } = await http.get('/memory/sessions', { params: { days } })
  return data
}

/** Search conversation history for a keyword */
export async function searchMemory(
  query: string,
  days: number = 7,
  maxResults: number = 10,
): Promise<MemorySearchResult> {
  const { data } = await http.get('/memory/search', {
    params: { query, days, max_results: maxResults },
  })
  return data
}

/** Recall recent conversation tail */
export async function recallMemory(
  lines: number = 60,
): Promise<MemoryRecallResult> {
  const { data } = await http.get('/memory/recall', { params: { lines } })
  return data
}

/**
 * Load a specific historical session by id — returns the parsed
 * JSONL records (user/agent/tool_call/tool_result/error) so the
 * frontend can render the original conversation into the chat panel.
 *
 * When ``date`` (``YYYY-MM-DD``) is supplied the backend goes straight
 * to ``conversations/{date}/{session_id}.jsonl`` and skips the
 * cross-date scan; without it, the backend walks date folders
 * newest-first.
 *
 * Returns ``{ ok: false, error }`` on missing id, etc. — caller
 * should branch on ``ok`` rather than try/catch.
 */
export async function getMemorySession(
  sessionId: string,
  date?: string,
): Promise<MemorySessionDetail> {
  const { data } = await http.get(
    `/memory/session/${encodeURIComponent(sessionId)}`,
    { params: date ? { date } : undefined },
  )
  return data
}

/** List all domains */
export async function listDomains(): Promise<DomainInfo[]> {
  const { data } = await http.get('/domains')
  return data.details || data.domains.map((d: string) => ({ name: d }))
}

/** Get full graph for a domain (decorated with levels/tiers) */
export async function getGraph(domain: string): Promise<GraphData> {
  const { data } = await http.get(`/graph/${encodeURIComponent(domain)}`)
  return data
}

/** Add a node (optionally under a parent) */
export async function addNode(
  domain: string,
  name: string,
  parent = '',
  links: string[] = [],
): Promise<string> {
  const { data } = await http.post('/nodes', {
    domain,
    name,
    parent,
    links,
  })
  return data.message
}

/** Update (rename / relink) a node */
export async function updateNode(
  domain: string,
  oldName: string,
  opts: {
    newName?: string
    newLinks?: string[]
  },
): Promise<string> {
  const { data } = await http.patch(
    `/nodes/${encodeURIComponent(domain)}`,
    opts,
    { params: { name: oldName } },
  )
  return data.message
}

/** Delete a node */
export async function deleteNode(domain: string, name: string): Promise<string> {
  const { data } = await http.delete(
    `/nodes/${encodeURIComponent(domain)}`,
    { params: { name } },
  )
  return data.message
}

// ============================================================
// Node note & resources
// ============================================================

/** Get node note.md content; if missing returns needs_generation=true (no longer auto-generates to avoid blocking) */
export async function getNodeNote(
  domain: string,
  nodeName: string,
): Promise<{ content: string; created: boolean; needs_generation?: boolean }> {
  const { data } = await http.get(
    `/notes/${encodeURIComponent(domain)}`,
    { params: { node: nodeName } },
  )
  return data
}

/** Generate node note.md via LLM (manual trigger when needs_generation) */
export async function generateNodeNote(
  domain: string,
  nodeName: string,
): Promise<{ content: string; created: boolean; source?: string }> {
  const { data } = await http.post(
    `/notes/${encodeURIComponent(domain)}/generate`,
    null,
    // 生成需要调 Wikipedia + LLM，可能 30~120s（reasoning 模型冷启动更慢），
    // 给 5 分钟兜底，避免网络抖动 / 大节点上下文时误判超时
    { params: { node: nodeName }, timeout: 300000 },
  )
  return data
}

/** Save node note.md content */
export async function saveNodeNote(
  domain: string,
  nodeName: string,
  content: string,
): Promise<string> {
  const { data } = await http.put(
    `/notes/${encodeURIComponent(domain)}`,
    { content },
    { params: { node: nodeName } },
  )
  return data.message
}

/** Single note index entry returned by getNotesIndex */
export interface NoteIndexEntry {
  name: string
  tier: string
  has_note: boolean
  summary: string
  words: number
  mtime: string | null
  source: string | null
}

/** Notes index response — list of (self + children) note summaries for a node */
export interface NotesIndexResponse {
  node: string
  tier: string
  is_leaf: boolean
  children: NoteIndexEntry[]
  self: NoteIndexEntry | null
}

/** Get note index for a node (no LLM generation; just enumerates child note states).
 *  Used by the note panel for non-leaf nodes to show a list view first. */
export async function getNotesIndex(
  domain: string,
  nodeName: string,
): Promise<NotesIndexResponse> {
  const { data } = await http.get(
    `/notes-index/${encodeURIComponent(domain)}`,
    { params: { node: nodeName } },
  )
  return data
}

/** Get all resources for a node */
export async function getNodeResources(
  domain: string,
  nodeName: string,
): Promise<NodeResources> {
  const { data } = await http.get(
    `/resources/${encodeURIComponent(domain)}`,
    { params: { node: nodeName } },
  )
  // study_materials 可能不存在（节点尚未生成消化产物），兜底为 []
  return {
    web_resources: data.web_resources ?? [],
    user_uploads: data.user_uploads ?? [],
    study_materials: data.study_materials ?? [],
  }
}

/** Add a web resource (URL) */
export async function addWebResource(
  domain: string,
  nodeName: string,
  item: { title: string; url: string; summary: string; category: string },
): Promise<{ ok: boolean; item: WebResource; total: number }> {
  const { data } = await http.post(
    `/resources/${encodeURIComponent(domain)}/web`,
    item,
    { params: { node: nodeName } },
  )
  return data
}

/** Delete a web resource by URL */
export async function deleteWebResource(
  domain: string,
  nodeName: string,
  url: string,
): Promise<{ ok: boolean; total: number }> {
  const { data } = await http.delete(
    `/resources/${encodeURIComponent(domain)}/web`,
    { params: { node: nodeName, url } },
  )
  return data
}

/** Edit a web resource (by original_url) */
export async function editWebResource(
  domain: string,
  nodeName: string,
  originalUrl: string,
  item: { title: string; url: string; summary: string; category: string },
): Promise<{ ok: boolean; total: number }> {
  const { data } = await http.put(
    `/resources/${encodeURIComponent(domain)}/web`,
    item,
    { params: { node: nodeName, original_url: originalUrl } },
  )
  return data
}

/** Upload a file to user_uploads */
export async function uploadFile(
  domain: string,
  nodeName: string,
  file: File,
  category: string,
  note: string,
): Promise<{ ok: boolean; item: UploadResource; total: number }> {
  const form = new FormData()
  form.append('file', file)
  form.append('category', category)
  form.append('note', note)
  const { data } = await http.post(
    `/resources/${encodeURIComponent(domain)}/upload`,
    form,
    {
      params: { node: nodeName },
      headers: { 'Content-Type': 'multipart/form-data' },
      timeout: 120000,
    },
  )
  return data
}

/** Delete an uploaded file */
export async function deleteUpload(
  domain: string,
  nodeName: string,
  filename: string,
): Promise<{ ok: boolean; total: number }> {
  const { data } = await http.delete(
    `/resources/${encodeURIComponent(domain)}/upload/${encodeURIComponent(filename)}`,
    { params: { node: nodeName } },
  )
  return data
}

/** Edit upload metadata (category, note) */
export async function editUpload(
  domain: string,
  nodeName: string,
  filename: string,
  item: { category: string; note: string },
): Promise<{ ok: boolean; total: number }> {
  const { data } = await http.put(
    `/resources/${encodeURIComponent(domain)}/upload/${encodeURIComponent(filename)}`,
    item,
    { params: { node: nodeName } },
  )
  return data
}

/** Build download URL for an uploaded file */
export function getDownloadUrl(
  domain: string,
  nodeName: string,
  filename: string,
): string {
  return `/api/resources/${encodeURIComponent(domain)}/download/${encodeURIComponent(filename)}?node=${encodeURIComponent(nodeName)}`
}

/** Build download URL for a study-material file (knowledge-digest output) */
export function getStudyMaterialUrl(
  domain: string,
  nodeName: string,
  filename: string,
): string {
  return `/api/resources/${encodeURIComponent(domain)}/study-materials/${encodeURIComponent(filename)}?node=${encodeURIComponent(nodeName)}`
}

/** List study materials for a node.
 *
 *  Pass ``path`` (relative to study_materials/ root) to drill into a
 *  sub-directory.  Returned items mix files and folder entries; folder
 *  entries have ``type='folder'`` and ``children_count`` so the UI can
 *  render a clickable navigation. */
export async function listStudyMaterials(
  domain: string,
  nodeName: string,
  path: string = '',
): Promise<StudyMaterialItem[]> {
  const { data } = await http.get(
    `/resources/${encodeURIComponent(domain)}/study-materials`,
    { params: { node: nodeName, path: path || undefined } },
  )
  return data.items ?? []
}

// ============================================================
// Node plan (学习计划 / 行动轨迹)
// ============================================================

/** Get all plan items for a node (timeline desc) */
export async function getNodePlans(
  domain: string,
  nodeName: string,
): Promise<NodePlans> {
  const { data } = await http.get(
    `/plans/${encodeURIComponent(domain)}`,
    { params: { node: nodeName } },
  )
  return data
}

/** Add a plan item */
export async function addPlan(
  domain: string,
  nodeName: string,
  item: { goal: string; actions: string[]; date?: string; note?: string; source?: 'manual' | 'agent' },
): Promise<{ ok: boolean; item: PlanItem; total: number }> {
  const { data } = await http.post(
    `/plans/${encodeURIComponent(domain)}`,
    item,
    { params: { node: nodeName } },
  )
  return data
}

/** Update a plan's goal/note/date (by id) */
export async function updatePlan(
  domain: string,
  nodeName: string,
  id: string,
  patch: { date?: string; goal?: string; note?: string },
): Promise<{ ok: boolean; item: PlanItem; total: number }> {
  const { data } = await http.put(
    `/plans/${encodeURIComponent(domain)}/plan/${encodeURIComponent(id)}`,
    patch,
    { params: { node: nodeName } },
  )
  return data
}

/** Delete a plan (by id) */
export async function deletePlan(
  domain: string,
  nodeName: string,
  id: string,
): Promise<{ ok: boolean; total: number }> {
  const { data } = await http.delete(
    `/plans/${encodeURIComponent(domain)}/plan/${encodeURIComponent(id)}`,
    { params: { node: nodeName } },
  )
  return data
}

/** Add an action to an existing plan */
export async function addAction(
  domain: string,
  nodeName: string,
  planId: string,
  content: string,
): Promise<{ ok: boolean; item: PlanItem; total: number }> {
  const { data } = await http.post(
    `/plans/${encodeURIComponent(domain)}/plan/${encodeURIComponent(planId)}/actions`,
    { content },
    { params: { node: nodeName } },
  )
  return data
}

/** Update an action's status/content (by plan_id + action_id) */
export async function updateAction(
  domain: string,
  nodeName: string,
  planId: string,
  actionId: string,
  patch: { status: 'pending' | 'done' | 'skipped'; content?: string },
): Promise<{ ok: boolean; item: PlanItem }> {
  const { data } = await http.put(
    `/plans/${encodeURIComponent(domain)}/plan/${encodeURIComponent(planId)}/actions/${encodeURIComponent(actionId)}`,
    patch,
    { params: { node: nodeName } },
  )
  return data
}

/** Delete an action (by plan_id + action_id) */
export async function deleteAction(
  domain: string,
  nodeName: string,
  planId: string,
  actionId: string,
): Promise<{ ok: boolean; item: PlanItem }> {
  const { data } = await http.delete(
    `/plans/${encodeURIComponent(domain)}/plan/${encodeURIComponent(planId)}/actions/${encodeURIComponent(actionId)}`,
    { params: { node: nodeName } },
  )
  return data
}

// ============================================================
// Timeline (活动流聚合) —— 串联节点 + 计划 + 资料 + 日期
// ============================================================

/** Get aggregated activity timeline for a domain (cross-source, desc by datetime) */
export async function getTimeline(
  domain: string,
  opts?: { date?: string; node?: string; type?: string },
): Promise<TimelineResponse> {
  const params: Record<string, string> = {}
  if (opts?.date) params.date = opts.date
  if (opts?.node) params.node = opts.node
  if (opts?.type) params.type = opts.type
  const { data } = await http.get(
    `/timeline/${encodeURIComponent(domain)}`,
    { params },
  )
  return data
}

/** Health check */
export async function healthCheck(): Promise<{
  ok: boolean
  agent_available: boolean
  agent_error: string | null
}> {
  const { data } = await http.get('/health')
  return data
}

/**
 * Mint a new agent session id.
 *
 * Backend returns ``{session_id, date}``.  The frontend stores this in
 * ``chat.sessionId`` (per-session) and uses it as ``thread_id`` for
 * every subsequent ``/api/agent/invoke`` call so the conversation
 * lands in its own JSONL file and LangGraph thread.
 */
export async function createAgentSession(): Promise<{
  session_id: string
  date: string
}> {
  const { data } = await http.post('/agent/session', {})
  return data
}

// ============================================================
// Transient file uploads (chat attachments → .agent_memory/tmp/)
// ============================================================

export interface TmpUploadItem {
  file: string
  size: number
  mtime: number
  path: string
}

export interface TmpParseResult {
  text: string
  truncated: boolean
  format: string
  size: number
  chars: number
}

/** Upload a file to the agent-memory tmp directory. */
export async function uploadTmpFile(file: File): Promise<{
  ok: boolean
  item: { file: string; size: number; path: string }
}> {
  const form = new FormData()
  form.append('file', file)
  const { data } = await http.post('/tmp/upload', form, {
    headers: { 'Content-Type': 'multipart/form-data' },
    timeout: 120000,
  })
  return data
}

/** List files currently sitting in the tmp directory. */
export async function listTmpFiles(): Promise<{
  items: TmpUploadItem[]
  total: number
}> {
  const { data } = await http.get('/tmp/list')
  return data
}

/** Delete a file from the tmp directory by filename. */
export async function deleteTmpFile(filename: string): Promise<{ ok: boolean }> {
  const { data } = await http.delete(`/tmp/${encodeURIComponent(filename)}`)
  return data
}

/** Build the URL the browser can hit to download / preview a tmp file. */
export function getTmpFileUrl(filename: string): string {
  return `/api/tmp/download/${encodeURIComponent(filename)}`
}

/** Ask the backend to parse a tmp file into LLM-readable text. */
export async function parseTmpFile(
  filename: string,
  maxChars = 20000,
): Promise<TmpParseResult> {
  const { data } = await http.get(
    `/tmp/parse/${encodeURIComponent(filename)}`,
    { params: { max_chars: maxChars } },
  )
  return data
}

export interface TmpAutoPlaceResult {
  ok: boolean
  filename: string
  format?: string
  size?: number
  node: string
  path: string
  rationale: string
  new_node_created: boolean
  decision: {
    node: string
    new_node_name: string
    rationale: string
    is_new: boolean
  }
  bytes_copied?: number
  skipped?: string
}

/**
 * One-shot auto-placement: parse the tmp file, ask the LLM which
 * node it belongs to, then copy it into that node's ``user_uploads/``.
 *
 * Use this when the user drops a file in chat and wants the system to
 * pick the right node automatically instead of having to drill into
 * the resource dialog first.
 */
export async function autoPlaceTmpFile(
  filename: string,
  domain: string,
  opts: { create_new_node?: boolean; max_chars?: number } = {},
): Promise<TmpAutoPlaceResult> {
  const { data } = await http.post('/tmp/auto-place', {
    filename,
    domain,
    create_new_node: opts.create_new_node ?? true,
    max_chars: opts.max_chars ?? 8000,
  })
  return data
}

/**
 * Stream agent events via SSE (fetch + ReadableStream).
 * Calls onEvent for each parsed SSE frame.
 * Returns when the stream ends.
 *
 * ``attachments`` is the list of filenames currently sitting in
 * ``.agent_memory/tmp/`` (uploaded via ``POST /api/tmp/upload``). Image
 * attachments are upgraded server-side to multimodal Anthropic content
 * blocks so MiniMax-M3 can see them directly; non-image attachments
 * surface as text references so the agent can call
 * ``kg_parse_uploaded_file`` on them.
 */
export async function streamAgent(
  message: string,
  threadId: string,
  onEvent: (ev: AgentEvent) => void,
  signal?: AbortSignal,
  context?: string,
  attachments?: string[],
): Promise<void> {
  const body: Record<string, unknown> = {
    message,
    thread_id: threadId,
    context: context || '',
  }
  if (attachments && attachments.length) {
    body.attachments = attachments
  }

  const res = await fetch('/api/agent/invoke', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    signal,
  })

  if (!res.ok || !res.body) {
    const txt = await res.text().catch(() => '')
    throw new Error(`Agent invoke failed: HTTP ${res.status} ${txt}`)
  }

  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let frameCount = 0
  let emittedCount = 0

  console.debug('[streamAgent] stream opened, HTTP', res.status)

  const parseFrame = (frame: string) => {
    const ev: Partial<AgentEvent> = {}
    let dataObj: any = {}
    // Lines may end with \r (SSE spec / sse_starlette uses \r\n)
    for (const line of frame.split(/\r?\n/)) {
      if (line.startsWith('event:')) {
        ;(ev as any).event = line.slice(6).trim()
      } else if (line.startsWith('data:')) {
        try {
          dataObj = JSON.parse(line.slice(5).trim())
        } catch {
          dataObj = { raw: line.slice(5).trim() }
        }
      }
      // lines starting with ':' are keepalive comments — ignored
    }
    if ((ev as any).event) {
      ;(ev as any).data = dataObj
      emittedCount++
      console.debug('[streamAgent] event', (ev as any).event, dataObj)
      onEvent(ev as AgentEvent)
    }
  }

  while (true) {
    const { value, done } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })

    // SSE frames are separated by a blank line — tolerate \r\n\r\n and \n\n
    const frames = buffer.split(/\r?\n\r?\n/)
    buffer = frames.pop() ?? ''

    for (const frame of frames) {
      frameCount++
      parseFrame(frame)
    }
  }

  // Flush any trailing frame left in the buffer after the stream ends
  if (buffer.trim()) {
    frameCount++
    parseFrame(buffer)
  }

  console.debug(
    `[streamAgent] stream closed — frames=${frameCount}, events emitted=${emittedCount}`,
  )
}
