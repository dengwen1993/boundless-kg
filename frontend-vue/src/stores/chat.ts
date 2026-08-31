import { defineStore } from 'pinia'
import { ref } from 'vue'
import type { ChatMessage, AgentEvent, ToolEvent } from '@/types/graph'
import { streamAgent, createAgentSession, getMemorySession } from '@/api'
import type { MemoryRecord } from '@/api'
import { useGraphStore } from './graph'
import { isErrorResult } from '@/utils/tools'

const STORAGE_KEY = 'kg_chat_history_v1'
const SESSION_KEY = 'kg_chat_session_id_v1'

const GREETING =
  '你好！我是知识图谱助手 🤖\n\n能力面板里列了常用操作（点击即可触发），也可以直接告诉我你想做什么：\n• 创建新领域："帮我建一个 Python 入门知识图谱"\n• 打开节点："帮我打开 认知能力 节点"（自动展开路径 + 高亮）\n• 加节点："在 机器学习 下加一个节点：Transformer"\n• 重命名/删除节点："重命名 当前节点 为 …" / "删除 涂鸦美术"\n• 生成/读取笔记："帮我生成/读取 节点 笔记"\n• 联网搜资料："查一下 RAG 最新进展"\n• 学习计划："为 当前节点 添加学习计划"\n• 活动时间线："查看活动流"'

export const useChatStore = defineStore('chat', () => {
  const messages = ref<ChatMessage[]>(loadHistory())
  const sessionId = ref<string>(loadSessionId())
  const isStreaming = ref(false)
  const agentAvailable = ref<boolean | null>(null) // null = unknown
  // When the chat panel was populated from a historical session via
  // ``loadHistoricalSession``, this holds the source session id + date
  // so the header can show "已加载历史会话 …" banner.  ``null`` means
  // the chat is in its normal (live) mode.
  const loadedHistorySession = ref<{ session: string; date: string } | null>(null)
  let abortController: AbortController | null = null

  function loadHistory(): ChatMessage[] {
    try {
      const raw = localStorage.getItem(STORAGE_KEY)
      if (raw) return JSON.parse(raw)
    } catch {
      /* ignore */
    }
    return []
  }

  function saveHistory() {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(messages.value.slice(-50)))
    } catch {
      /* ignore */
    }
  }

  function loadSessionId(): string {
    try {
      const raw = localStorage.getItem(SESSION_KEY)
      if (raw && /^[a-f0-9]{16}$/i.test(raw)) return raw
    } catch {
      /* ignore */
    }
    return ''
  }

  function saveSessionId(id: string) {
    try {
      localStorage.setItem(SESSION_KEY, id)
    } catch {
      /* ignore */
    }
  }

  function pushGreeting() {
    messages.value.push({
      role: 'agent',
      content: GREETING,
      ts: Date.now(),
    })
    saveHistory()
  }

  /** Start a fresh, empty conversation.
   *
   *  - Mints a new 16-char session id (from the backend, falling back
   *    to a client-side uuid4 if the backend is unreachable).
   *  - Persists it so reload keeps the same session.
   *  - Clears the in-memory + localStorage chat history and re-pushes
   *    the greeting.
   *
   *  Returned Promise resolves once the new id is in hand, so the
   *  caller can immediately use it as ``thread_id`` on the next send.
   */
  async function startNewSession(): Promise<string> {
    // If a stream is in flight, abort it first so its writes don't land
    // in the new session.
    if (abortController) {
      abortController.abort()
      abortController = null
    }
    isStreaming.value = false

    let id = ''
    try {
      const r = await createAgentSession()
      id = r.session_id
    } catch (e: any) {
      console.warn('[chat] createAgentSession failed, falling back to local uuid:', e)
    }
    if (!id) {
      // Client-side fallback — match the backend format (uuid4.hex[:16])
      id = (crypto.randomUUID
        ? crypto.randomUUID().replace(/-/g, '').slice(0, 16)
        : Math.random().toString(16).slice(2, 18).padEnd(16, '0'))
    }

    sessionId.value = id
    saveSessionId(id)
    loadedHistorySession.value = null

    messages.value = []
    localStorage.removeItem(STORAGE_KEY)
    pushGreeting()
    return id
  }

  /** Legacy alias — kept so older callers / tests still compile.
   *  Prefer ``startNewSession()`` for the "新会话" UX. */
  function clearHistory() {
    void startNewSession()
  }

  /** Convert a single JSONL record into the chat-panel message form.
   *
   *  Records arrive in chronological order, but tool_call + tool_result
   *  come as siblings.  The chat-panel renderer expects tool events to
   *  be embedded inside the *following* agent message (``toolEvents``)
   *  so the tool card is anchored to the assistant turn that triggered
   *  it.  So we buffer ``tool_call`` events until we either see a
   *  matching ``tool_result`` or move past — and flush them onto the
   *  next agent / user message we emit.
   *
   *  ``session_start`` / ``session_end`` rows are filtered out at the
   *  HTTP layer (``getMemorySession``) so this loop never sees them.
   */
  function _recordsToMessages(records: MemoryRecord[]): ChatMessage[] {
    const out: ChatMessage[] = []
    // Tool calls seen but not yet matched to a result.  We append them
    // to the next agent message we emit; if a matching result never
    // arrives (truncated log) we still flush on the next user msg so
    // the user can see "tool ran but its result is missing".
    const pendingToolEvents: ToolEvent[] = []
    const flushTo = (m: ChatMessage) => {
      if (!pendingToolEvents.length) return
      m.toolEvents = [...pendingToolEvents, ...(m.toolEvents ?? [])]
      const blocks = m.blocks ?? []
      for (const te of pendingToolEvents) {
        if (te.type === 'call') {
          blocks.push({
            kind: 'tool',
            name: te.name,
            args: te.args,
            status: te.result !== undefined ? (isErrorResult(te.result) ? 'error' : 'done') : 'running',
            result: te.result,
          })
        }
      }
      m.blocks = blocks
      pendingToolEvents.length = 0
    }

    for (const rec of records) {
      const rtype = rec.type
      if (rtype === 'user') {
        const m: ChatMessage = {
          role: 'user',
          content: rec.content ?? '',
          ts: 0,
        }
        flushTo(m)
        out.push(m)
      } else if (rtype === 'agent') {
        const m: ChatMessage = {
          role: 'agent',
          content: rec.content ?? '',
          ts: 0,
          toolEvents: [],
          blocks: [{ kind: 'text', text: rec.content ?? '' }],
        }
        flushTo(m)
        out.push(m)
      } else if (rtype === 'tool_call') {
        pendingToolEvents.push({
          type: 'call',
          name: rec.name ?? '?',
          args: rec.args ?? {},
        })
      } else if (rtype === 'tool_result') {
        const name = rec.name ?? ''
        const resultStr = rec.result ?? ''
        // Walk backwards through pendingToolEvents to find the most
        // recent matching call to pair this result with.  We use a
        // plain for-loop (not ``find``) so TS can narrow the element
        // type and we can read ``name`` / ``args`` off the slot.
        let matchedIdx = -1
        for (let i = pendingToolEvents.length - 1; i >= 0; i--) {
          const ev = pendingToolEvents[i]
          if (ev.type === 'call' && ev.name === name) {
            matchedIdx = i
            break
          }
        }
        if (matchedIdx >= 0) {
          const callEv = pendingToolEvents[matchedIdx]
          // In-place upgrade: keep ``type: 'call'`` (so the renderer
          // path matches it) but bolt the result string onto the slot
          // so flushTo can mark the card as done.
          pendingToolEvents[matchedIdx] = {
            type: 'call',
            name: callEv.name,
            args: callEv.args,
            result: resultStr,
          }
        } else {
          // Orphan result (no preceding call) — append as a standalone
          // tool-result event; the next flush will pick it up.
          pendingToolEvents.push({ type: 'result', name, result: resultStr })
        }
      } else if (rtype === 'error') {
        const m: ChatMessage = {
          role: 'agent',
          content: `[${rec.ts ?? ''}] error: ${rec.message ?? ''}`,
          ts: 0,
          toolEvents: [],
          blocks: [{ kind: 'text', text: `❌ ${rec.message ?? ''}` }],
        }
        flushTo(m)
        out.push(m)
      }
    }
    // Flush trailing tool events as a synthetic agent message so they
    // don't get dropped on the floor.
    if (pendingToolEvents.length) {
      const m: ChatMessage = {
        role: 'agent',
        content: '',
        ts: 0,
        toolEvents: [],
        blocks: [],
      }
      flushTo(m)
      out.push(m)
    }
    return out
  }

  /** Load a historical session into the chat panel.
   *
   *  1. Abort any in-flight request (its writes would otherwise land in
   *     a thread that no longer matches the visible conversation).
   *  2. Pull the parsed JSONL records for ``sessionId`` via
   *     ``GET /api/memory/session/{id}``.
   *  3. Replace ``messages`` with the reconstructed conversation.
   *  4. Set ``sessionId`` so the *next* ``sendMessage`` call lands in
   *     the same JSONL file (server writes ``session_resume`` header
   *     on existing files), so subsequent turns continue the old log.
   *  5. Fire a "silent" user message instructing the agent to call
   *     ``kg_recall_session`` — this is how we hand the historical
   *     transcript to the LLM, because LangGraph's in-process
   *     MemorySaver drops the thread on backend restart.
   *
   *  Returns ``true`` on success, ``false`` if the session id could
   *  not be resolved (the dialog then keeps its current view).
   */
  async function loadHistoricalSession(
    targetSessionId: string,
    targetDate?: string,
  ): Promise<boolean> {
    const id = targetSessionId.trim()
    if (!id) return false
    if (abortController) {
      abortController.abort()
      abortController = null
    }
    isStreaming.value = false

    let detail
    try {
      detail = await getMemorySession(id, targetDate)
    } catch (e: any) {
      console.warn('[chat] getMemorySession failed', e)
      return false
    }
    if (!detail?.ok) {
      console.warn('[chat] getMemorySession not ok', detail)
      return false
    }

    const resolvedDate = detail.date ?? targetDate ?? ''
    const rebuilt = _recordsToMessages(detail.records)
    // Header banner so the user knows the chat is replayed history
    // and any new turns they send will be appended to that file.
    const banner: ChatMessage = {
      role: 'system',
      content:
        `📜 已加载历史会话 ${id}（${resolvedDate}，${detail.total_records ?? rebuilt.length} 条记录）\n` +
        '后续消息会继续写入该会话文件。如需新会话请点击「新会话」。',
      ts: Date.now(),
    }
    messages.value = [banner, ...rebuilt]
    saveHistory()

    // Switch to the loaded thread so subsequent sends resume the file.
    sessionId.value = id
    saveSessionId(id)
    loadedHistorySession.value = { session: id, date: resolvedDate }

    // Hand the transcript to the LLM via the dedicated tool.
    //   ``kg_recall_session`` is the canonical tool — the agent can pull
    //   more than the trimmed ``content`` field if it needs detail.
    //   Passing ``date`` lets the tool hit conversations/{date}/{id}.jsonl
    //   directly (no cross-date scan), which matters when sessions grow
    //   into the hundreds.
    const dateArg = resolvedDate ? `, "${resolvedDate}"` : ''
    const turnPrompt =
      `我已切换到历史会话 ${id}（${resolvedDate}）。\n` +
      `请调用 kg_recall_session("${id}"${dateArg}) 加载该会话的完整上下文，\n` +
      `确认你已经掌握之前讨论的内容后，回复一句简短的"已就绪"即可，不要重复历史。`
    try {
      await sendMessage(turnPrompt, undefined, undefined, undefined)
    } catch (e) {
      console.warn('[chat] historical-resume sendMessage failed', e)
    }
    return true
  }

  async function sendMessage(
    text: string,
    threadId?: string,
    context?: string,
    attachments?: string[],
  ) {
    if (!text.trim() || isStreaming.value) return

    // Ensure we always have a session id — the backend will mint one
    // if we still don't, but having it client-side lets the SSE
    // connection log into the right JSONL file from the very first
    // event.
    let tid = threadId || sessionId.value
    if (!tid) {
      tid = await startNewSession()
    }
    sessionId.value = tid
    saveSessionId(tid)

    // push user message
    messages.value.push({
      role: 'user',
      content: text,
      ts: Date.now(),
    })
    saveHistory()

    // push pending agent message
    messages.value.push({
      role: 'agent',
      content: '',
      ts: Date.now(),
      toolEvents: [],
      blocks: [],
      pending: true,
    })
    // Grab the REACTIVE proxy back from the array. Mutating the raw object
    // we just pushed would bypass Vue's proxy setter and never re-render.
    const agentMsg = messages.value[messages.value.length - 1] as ChatMessage

    isStreaming.value = true
    abortController = new AbortController()

    try {
      await streamAgent(
        text,
        tid,
        ((ev: any) => {
          console.debug('[chat] onEvent', ev.event, ev.data)
          // The backend emits a `session` event first with the
          // resolved session id (in case the server minted one
          // because we omitted /api/agent/session earlier).  Sync
          // it so reloads + the MemorySearch dialog both see the
          // right id.
          if (ev.event === 'session') {
            const sid = ev.data?.session_id
            if (typeof sid === 'string' && sid && sid !== sessionId.value) {
              sessionId.value = sid
              saveSessionId(sid)
            }
            return
          }
          if (ev.event === 'attachment-warning') {
            // Surface the backend's "this image was too big / wrong
            // format" warning as a small red text block above the
            // agent's reply so the user sees why an image didn't go
            // through.
            const reason = (ev.data as any)?.reason || '附件处理失败'
            const fname = (ev.data as any)?.file || '附件'
            const warnText = `\n\n⚠️ ${fname}：${reason}`
            agentMsg.content += warnText
            agentMsg.blocks!.push({ kind: 'text', text: warnText })
            return
          }
          if (ev.event === 'text') {
            agentMsg.content += ev.data.delta
            // Append to the last text block if it exists, otherwise start a new one
            const blocks = agentMsg.blocks!
            const last = blocks[blocks.length - 1]
            if (last && last.kind === 'text') {
              last.text += ev.data.delta
            } else {
              blocks.push({ kind: 'text', text: ev.data.delta })
            }
          } else if (ev.event === 'tool-call') {
            agentMsg.toolEvents!.push({
              type: 'call',
              name: (ev.data as any).name,
              args: (ev.data as any).args,
            })
            agentMsg.blocks!.push({
              kind: 'tool',
              name: (ev.data as any).name,
              args: (ev.data as any).args,
              status: 'running',
            })
          } else if (ev.event === 'tool-result') {
            // append to last tool call event
            const lastCall = [...agentMsg.toolEvents!]
              .reverse()
              .find((t) => t.type === 'call' && t.name === (ev.data as any).name)
            if (lastCall) {
              agentMsg.toolEvents!.push({
                type: 'result',
                name: (ev.data as any).name,
                result: (ev.data as any).result,
              })
            }
            // Update the matching running tool block
            const blocks = agentMsg.blocks!
            for (let i = blocks.length - 1; i >= 0; i--) {
              const b = blocks[i]
              if (
                b.kind === 'tool' &&
                b.name === (ev.data as any).name &&
                b.status === 'running'
              ) {
                b.result = (ev.data as any).result
                b.status = isErrorResult((ev.data as any).result) ? 'error' : 'done'
                break
              }
            }
            // Side-effect: kg_open_node asks the UI to navigate.  Handle it
            // here (not in a tool wrapper) so the chat store stays the single
            // funnel for "agent wants to do something client-side".
            if ((ev.data as any).name === 'kg_open_node') {
              handleOpenNodeResult((ev.data as any).result)
            }
          } else if (ev.event === 'error') {
            const errText = `\n\n❌ Error: ${(ev.data as any).message}`
            agentMsg.content += errText
            agentMsg.blocks!.push({ kind: 'text', text: errText })
          } else if (ev.event === 'dossier-archived') {
            // 后台归档完成 — 在当前 agent 消息下面追加一个"档案"块,
            // 弹一个轻提示,用户能立刻看到"🤖 学到了"。
            const d = (ev.data as any) || {}
            const node = d.node || ''
            const title = d.title || ''
            const note = `\n\n🤖 **学到了** — ${node ? `「${node}」` : ''} ${title}`
            agentMsg.content += note
            agentMsg.blocks!.push({ kind: 'text', text: note })
          }
        }) as any,
        abortController.signal,
        context,
        attachments,
      )
    } catch (e: any) {
      if (e.name === 'AbortError') {
        agentMsg.content += '\n\n（已中断）'
        agentMsg.blocks!.push({ kind: 'text', text: '\n\n（已中断）' })
      } else {
        agentMsg.content += `\n\n❌ ${e.message}`
        agentMsg.blocks!.push({ kind: 'text', text: `\n\n❌ ${e.message}` })
        agentAvailable.value = false
      }
    } finally {
      agentMsg.pending = false
      isStreaming.value = false
      abortController = null
      console.debug('[chat] done — final content length:', agentMsg.content.length)
      saveHistory()
    }
  }

  function stopStreaming() {
    if (abortController) {
      abortController.abort()
    }
  }

  /** Parse ``kg_open_node`` tool result and trigger OutlineView navigation.
   *
   * Result shape (set by the backend tool)::
   *     {"ok": true, "domain": "...", "node": "...",
   *      "path": ["L0", "...", "target"], "tier": "...", "level": N}
   *     {"ok": false, "domain": "...", "node": "...", "message": "..."}
   *
   * If the target domain is not the active one, we load it first so the
   * OutlineView is rendering the right graph when the navigation lands.
   * After navigation, the target node is also marked as the current
   * selection so chat-panel buttons gated on ``needNode`` enable.
   */
  async function handleOpenNodeResult(resultStr: string | undefined) {
    console.log('[kg_open_node][chat] handleOpenNodeResult START', { resultStr })
    if (!resultStr) {
      console.warn('[kg_open_node][chat] empty result, bail')
      return
    }
    let payload: any
    try {
      payload = JSON.parse(resultStr)
    } catch (e) {
      console.warn('[kg_open_node][chat] JSON.parse failed', e)
      return
    }
    console.log('[kg_open_node][chat] parsed payload', payload)
    if (!payload || payload.ok !== true) {
      console.warn('[kg_open_node][chat] payload.ok !== true, bail')
      return
    }
    if (!payload.domain || !payload.node || !Array.isArray(payload.path)) {
      console.warn('[kg_open_node][chat] payload missing fields', payload)
      return
    }

    const graphStore = useGraphStore()
    try {
      console.log('[kg_open_node][chat] before loadGraph', {
        activeDomain: graphStore.activeDomain,
        targetDomain: payload.domain,
        sameDomain: graphStore.activeDomain === payload.domain,
      })
      if (graphStore.activeDomain !== payload.domain) {
        await graphStore.loadGraph(payload.domain)
        console.log('[kg_open_node][chat] loadGraph done', {
          nodes: graphStore.graph?.nodes?.length,
        })
      }
      graphStore.setViewMode('outline')
      graphStore.requestNavigation({
        domain: payload.domain,
        path: payload.path,
        node: payload.node,
      })
      console.log('[kg_open_node][chat] requestNavigation fired', {
        pendingNavigation: graphStore.pendingNavigation,
      })
      const target = graphStore.nodeMap.get(payload.node)
      console.log('[kg_open_node][chat] nodeMap lookup', {
        key: payload.node,
        found: !!target,
        target,
        nodeMapSize: graphStore.nodeMap.size,
      })
      if (target) {
        graphStore.selectNode(target)
        console.log('[kg_open_node][chat] selectNode called', {
          selectedNode: graphStore.selectedNode?.name,
        })
      } else {
        console.warn('[kg_open_node][chat] node NOT in nodeMap', {
          key: payload.node,
          available: [...graphStore.nodeMap.keys()].filter((k) => k.includes(payload.node)),
        })
      }
    } catch (e: any) {
      console.warn('[kg_open_node][chat] handler failed', e?.message ?? e, e)
    }
  }

  // initialize greeting if empty
  if (messages.value.length === 0) {
    pushGreeting()
  }

  return {
    messages,
    sessionId,
    isStreaming,
    agentAvailable,
    loadedHistorySession,
    sendMessage,
    stopStreaming,
    startNewSession,
    clearHistory,
    loadHistoricalSession,
  }
})
