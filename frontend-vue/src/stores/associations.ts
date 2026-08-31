/**
 * Associations Pinia store — 知识图谱关联层状态管理。
 *
 * 设计要点：
 *  - 一份 `AssociationGraph` 是不可变快照；修改通过 reload 触发
 *  - 选中节点 (`focusNode`) 触发 `loadNeighbors` 拉取邻居
 *  - 派生统计（`derivedEventsCount`）来自 `metadata.derived_events`
 */

import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import * as api from '@/api/associations'
import type {
  Association,
  AssociationGraph,
  NeighborEntry,
  RelationType,
  StatisticsResponse,
} from '@/api/associations'

export const useAssociationsStore = defineStore('associations', () => {
  // ── State ──
  const graph = ref<AssociationGraph | null>(null)
  const loading = ref(false)
  const saving = ref(false)
  const error = ref<string | null>(null)

  /** 当前选中的节点（点击图谱节点时设置）。 */
  const focusNode = ref<string | null>(null)
  /** 邻居查询结果（BFS，最多 3 跳）。 */
  const neighbors = ref<NeighborEntry[]>([])
  const neighborsLoading = ref(false)
  /** 邻居查询的 relation 过滤器（空 = 全部）。 */
  const neighborFilter = ref<RelationType | ''>('')
  /** 邻居查询的跳数（1~3）。 */
  const neighborHops = ref<number>(2)

  /** 边显示过滤（空数组 = 显示所有类型）。 */
  const relationFilter = ref<RelationType[]>([])

  // ── Computed ──
  const conceptList = computed(() =>
    graph.value ? Object.values(graph.value.concepts) : [],
  )
  const resourceList = computed(() =>
    graph.value ? Object.values(graph.value.resources) : [],
  )
  const associationList = computed<Association[]>(
    () => graph.value?.associations ?? [],
  )

  const filteredAssociations = computed<Association[]>(() => {
    const allowed = relationFilter.value
    if (!allowed.length) return associationList.value
    return associationList.value.filter((a) => allowed.includes(a.relation))
  })

  const statistics = computed<StatisticsResponse>(() => ({
    concepts: conceptList.value.length,
    resources: resourceList.value.length,
    associations: associationList.value.length,
    derived_events: Object.keys(graph.value?.metadata?.derived_events ?? {}).length,
  }))

  /** 已选节点的派生信息（按 source/target 找边）。 */
  const focusNodeAssociations = computed<{
    outgoing: Association[]
    incoming: Association[]
    resources: api.ResourceNode[]
  }>(() => {
    if (!graph.value || !focusNode.value) {
      return { outgoing: [], incoming: [], resources: [] }
    }
    const node = focusNode.value
    const all = associationList.value
    return {
      outgoing: all.filter(
        (a) =>
          a.source === node ||
          a.source === `concept:${node}` ||
          a.source === `note:${node}`,
      ),
      incoming: all.filter(
        (a) =>
          a.target === node ||
          a.target === `concept:${node}` ||
          a.target === `note:${node}`,
      ),
      resources: resourceList.value.filter((r) => r.node === node),
    }
  })

  // ── Actions ──

  async function load(domain: string, force = false) {
    if (!force && graph.value?.domain === domain) return
    loading.value = true
    error.value = null
    try {
      graph.value = await api.getAssociations(domain)
    } catch (e: any) {
      error.value = `加载关联图失败: ${e.message}`
      console.error(error.value)
    } finally {
      loading.value = false
    }
  }

  async function reload() {
    if (!graph.value) return
    await load(graph.value.domain, true)
  }

  async function syncFull() {
    if (!graph.value) return
    saving.value = true
    try {
      await api.syncFull(graph.value.domain)
      await reload()
    } catch (e: any) {
      error.value = `全量派生失败: ${e.message}`
    } finally {
      saving.value = false
    }
  }

  async function syncNode(node: string, enqueue_llm = true) {
    if (!graph.value) return
    saving.value = true
    try {
      await api.syncNode(graph.value.domain, node, enqueue_llm)
      await reload()
    } catch (e: any) {
      error.value = `单节点派生失败: ${e.message}`
    } finally {
      saving.value = false
    }
  }

  async function flushLLM() {
    if (!graph.value) return
    saving.value = true
    try {
      const res = await api.flushLLM(graph.value.domain)
      return res
    } finally {
      saving.value = false
    }
  }

  async function clear() {
    if (!graph.value) return
    saving.value = true
    try {
      await api.clearAssociations(graph.value.domain)
      graph.value = null
      focusNode.value = null
      neighbors.value = []
    } catch (e: any) {
      error.value = `清空失败: ${e.message}`
    } finally {
      saving.value = false
    }
  }

  async function setFocus(node: string | null) {
    focusNode.value = node
    neighbors.value = []
    if (node && graph.value) {
      await loadNeighbors(node)
    }
  }

  /** 手动添加一条关联边（右键菜单）。写完后 reload 让视图反映变更。 */
  async function addManualAssociation(payload: {
    source: string
    target: string
    relation: RelationType
    weight?: number
    intensity?: string
    evidence?: string
  }) {
    if (!graph.value) return
    saving.value = true
    try {
      await api.addManualAssociation(graph.value.domain, payload)
      await reload()
    } catch (e: any) {
      error.value = `添加关联失败: ${e.message}`
      throw e
    } finally {
      saving.value = false
    }
  }

  /** 手动删除一条关联边（右键菜单）。 */
  async function deleteManualAssociation(payload: {
    source: string
    target: string
    relation: RelationType
  }) {
    if (!graph.value) return
    saving.value = true
    try {
      await api.deleteManualAssociation(graph.value.domain, payload)
      await reload()
    } catch (e: any) {
      error.value = `删除关联失败: ${e.message}`
      throw e
    } finally {
      saving.value = false
    }
  }

  /** 删除一个概念节点（右键菜单）。
   *  会同步清理关联图 + 主图谱（如果存在）+ FalkorDB。 */
  async function deleteConcept(name: string) {
    if (!graph.value) return
    saving.value = true
    try {
      await api.deleteConcept(graph.value.domain, name)
      // 删除后该节点可能仍是 focus，清掉焦点
      if (focusNode.value === name) focusNode.value = null
      await reload()
    } catch (e: any) {
      error.value = `删除节点失败: ${e.message}`
      throw e
    } finally {
      saving.value = false
    }
  }

  async function loadNeighbors(node: string) {
    if (!graph.value) return
    neighborsLoading.value = true
    try {
      const res = await api.getNeighbors(graph.value.domain, node, {
        hops: neighborHops.value,
        relation: neighborFilter.value || undefined,
      })
      neighbors.value = res.neighbors
    } catch (e: any) {
      error.value = `加载邻居失败: ${e.message}`
      neighbors.value = []
    } finally {
      neighborsLoading.value = false
    }
  }

  function setRelationFilter(rels: RelationType[]) {
    relationFilter.value = rels
  }

  function setNeighborFilter(rel: RelationType | '') {
    neighborFilter.value = rel
    if (focusNode.value) loadNeighbors(focusNode.value)
  }

  function setNeighborHops(hops: number) {
    neighborHops.value = Math.max(1, Math.min(3, hops))
    if (focusNode.value) loadNeighbors(focusNode.value)
  }

  return {
    // state
    graph,
    loading,
    saving,
    error,
    focusNode,
    neighbors,
    neighborsLoading,
    neighborFilter,
    neighborHops,
    relationFilter,
    // computed
    conceptList,
    resourceList,
    associationList,
    filteredAssociations,
    statistics,
    focusNodeAssociations,
    // actions
    load,
    reload,
    syncFull,
    syncNode,
    flushLLM,
    clear,
    setFocus,
    loadNeighbors,
    setRelationFilter,
    setNeighborFilter,
    setNeighborHops,
    addManualAssociation,
    deleteManualAssociation,
    deleteConcept,
  }
})