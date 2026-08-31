import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import type { GraphData, GraphNode, DomainInfo } from '@/types/graph'
import * as api from '@/api'
import { setSelectedDomain } from '@/utils/storage'

/** Pending navigation request from the AI assistant (`kg_open_node` tool).
 *
 * The chat store dispatches one of these when the agent asks the UI to
 * jump to a node.  OutlineView watches the value and expands the path
 * + selects the target.  `ts` is bumped on every request so duplicate
 * requests are still applied (e.g. the user asks to open the same node
 * twice in a row). */
export interface NodeNavigationRequest {
  domain: string
  /** Hierarchy chain from the synthetic L0 root down to the target. */
  path: string[]
  /** Target node name (last element of `path` when BFS reaches it). */
  node: string
  ts: number
}

export const useGraphStore = defineStore('graph', () => {
  // ── State ──
  const domains = ref<DomainInfo[]>([])
  const activeDomain = ref<string>('')
  const graph = ref<GraphData | null>(null)
  const loading = ref(false)
  const saving = ref(false)
  const error = ref<string | null>(null)

  // drill stack: list of node names we've drilled into
  const drillStack = ref<string[]>([])
  // currently selected node
  const selectedNode = ref<GraphNode | null>(null)

  // view mode: 'graph' (D3 mind-map) | 'outline' (book-like tree) | 'associations' (derived graph)
  const viewMode = ref<'graph' | 'outline' | 'associations'>('outline')

  // node note panel: when open, shows the note.md + resources for a node
  const notePanelNode = ref<string | null>(null)
  const notePanelVisible = ref(false)
  // optional intent when opening the note panel: 'plan' | 'resource' | 'note'
  // — used to deep-link from the activity timeline into the matching tab
  const notePanelIntent = ref<'plan' | 'resource' | 'note' | null>(null)

  // Pending navigation request from the AI assistant (kg_open_node tool).
  // OutlineView watches this and expands the path + selects the target.
  const pendingNavigation = ref<NodeNavigationRequest | null>(null)

  // ── Computed ──
  const nodeCount = computed(() => graph.value?.meta?.n_nodes ?? 0)
  const linkCount = computed(() => graph.value?.meta?.n_links ?? 0)
  const currentLevel = computed(() => drillStack.value.length + 1)
  const nodeMap = computed(() => {
    const m = new Map<string, GraphNode>()
    graph.value?.nodes.forEach((n) => m.set(n.name, n))
    return m
  })

  /** Domains shown in the TopBar selector.
   *
   * Filters out bookkeeping directories that aren't real domains:
   *   - names starting with `_` or `.` (e.g. `_pipeline`, `.agent_memory`)
   *   - domains with 0 nodes (empty / failed generation runs)
   */
  const visibleDomains = computed(() =>
    domains.value.filter(
      (d) =>
        d.name &&
        !d.name.startsWith('_') &&
        !d.name.startsWith('.') &&
        (d.node_count ?? 0) > 0,
    ),
  )

  /** L1 root nodes (no incoming links) */
  const rootNodes = computed(() => {
    if (!graph.value) return []
    return graph.value.nodes.filter((n) => n.level === 1)
  })

  // ── Actions ──

  async function loadDomains() {
    try {
      domains.value = await api.listDomains()
    } catch (e: any) {
      error.value = `加载领域列表失败: ${e.message}`
      console.error(error.value)
    }
  }

  async function loadGraph(domain: string) {
    loading.value = true
    error.value = null
    try {
      graph.value = await api.getGraph(domain)
      activeDomain.value = domain
      // 持久化用户选择，下次刷新/重开自动恢复
      setSelectedDomain(domain)
      drillStack.value = []
      selectedNode.value = null
    } catch (e: any) {
      error.value = `加载图谱失败: ${e.message}`
      console.error(error.value)
    } finally {
      loading.value = false
    }
  }

  async function refreshGraph() {
    if (!activeDomain.value) return
    const stack = [...drillStack.value]
    const sel = selectedNode.value?.name ?? null
    try {
      graph.value = await api.getGraph(activeDomain.value)
      // Try to preserve drill stack (filter out names that no longer exist)
      drillStack.value = stack.filter((n) => nodeMap.value.has(n))
      // Try to preserve selection
      if (sel && nodeMap.value.has(sel)) {
        selectedNode.value = nodeMap.value.get(sel) ?? null
      } else {
        selectedNode.value = null
      }
    } catch (e: any) {
      error.value = `刷新图谱失败: ${e.message}`
    }
  }

  async function addNode(name: string, parent: string, links: string[] = []) {
    if (!activeDomain.value) return
    saving.value = true
    try {
      const msg = await api.addNode(activeDomain.value, name, parent, links)
      await refreshGraph()
      await loadDomains()
      return msg
    } catch (e: any) {
      error.value = e.message
      throw e
    } finally {
      saving.value = false
    }
  }

  async function updateNode(
    oldName: string,
    opts: {
      newName?: string
      newLinks?: string[]
    },
  ) {
    if (!activeDomain.value) return
    saving.value = true
    try {
      const msg = await api.updateNode(activeDomain.value, oldName, opts)
      await refreshGraph()
      return msg
    } catch (e: any) {
      error.value = e.message
      throw e
    } finally {
      saving.value = false
    }
  }

  async function deleteNode(name: string) {
    if (!activeDomain.value) return
    saving.value = true
    try {
      const msg = await api.deleteNode(activeDomain.value, name)
      await refreshGraph()
      await loadDomains()
      return msg
    } catch (e: any) {
      error.value = e.message
      throw e
    } finally {
      saving.value = false
    }
  }

  function drillTo(nodeName: string) {
    if (nodeMap.value.has(nodeName)) {
      drillStack.value.push(nodeName)
      selectedNode.value = null
    }
  }

  function popDrill() {
    drillStack.value.pop()
    selectedNode.value = null
  }

  function setDrillStack(stack: string[]) {
    drillStack.value = [...stack]
    selectedNode.value = null
  }

  function selectNode(node: GraphNode | null) {
    selectedNode.value = node
  }

  function setViewMode(mode: 'graph' | 'outline' | 'associations') {
    viewMode.value = mode
  }

  function openNotePanel(
    nodeName: string,
    intent: 'plan' | 'resource' | 'note' | null = null,
  ) {
    notePanelNode.value = nodeName
    notePanelIntent.value = intent
    notePanelVisible.value = true
  }

  function clearNotePanelIntent() {
    notePanelIntent.value = null
  }

  function closeNotePanel() {
    notePanelVisible.value = false
    notePanelNode.value = null
    notePanelIntent.value = null
  }

  /** Dispatch a navigation request to OutlineView.
   *
   * The caller is responsible for ensuring ``domain`` is loaded (call
   * :func:`loadGraph` first if it differs from the current domain).
   * The store does NOT auto-load here so callers can chain ``loadGraph``
   * → ``requestNavigation`` in a single async flow. */
  function requestNavigation(req: Omit<NodeNavigationRequest, 'ts'>) {
    pendingNavigation.value = { ...req, ts: Date.now() }
  }

  function clearPendingNavigation() {
    pendingNavigation.value = null
  }

  return {
    // state
    domains,
    activeDomain,
    graph,
    loading,
    saving,
    error,
    drillStack,
    selectedNode,
    viewMode,
    notePanelNode,
    notePanelVisible,
    notePanelIntent,
    pendingNavigation,
    // computed
    nodeCount,
    linkCount,
    currentLevel,
    nodeMap,
    rootNodes,
    visibleDomains,
    // actions
    loadDomains,
    loadGraph,
    refreshGraph,
    addNode,
    updateNode,
    deleteNode,
    drillTo,
    popDrill,
    setDrillStack,
    selectNode,
    setViewMode,
    openNotePanel,
    clearNotePanelIntent,
    closeNotePanel,
    requestNavigation,
    clearPendingNavigation,
  }
})
