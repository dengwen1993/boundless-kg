/**
 * Associations API — 封装后端 `/api/associations/*` 端点。
 *
 * 后端实现见 `src/api/routes/associations.py`；派生态存储于
 * `<kb_root>/<domain>/associations.json`，由 GraphSyncOrchestrator 自动维护。
 */

import axios from 'axios'

const http = axios.create({
  baseURL: '/api',
  timeout: 30000,
})

// ============================================================
// 类型
// ============================================================

export type RelationType =
  | 'part_of'
  | 'prerequisite_of'
  | 'enables'
  | 'similar_to'
  | 'contrasts_with'
  | 'applies_to'
  | 'derived_from'
  | 'related_to'
  | 'has_note'
  | 'has_resource'
  | 'has_plan'
  | 'cites'
  | 'references'

export type EdgeIntensity = 'HARD' | 'SOFT' | 'STRUCTURAL'
export type ResourceType = 'note' | 'resource' | 'plan' | 'quiz' | 'gap'

export interface ConceptNode {
  id: string
  name: string
  domain: string
  level: number
  is_root: boolean
  description: string
  in_degree: number
  out_degree: number
  part_of_count: number
  updated_at: string
}

export interface ResourceNode {
  id: string
  type: ResourceType
  node: string
  domain: string
  payload: Record<string, unknown>
  summary: string
  updated_at: string
}

export interface Association {
  source: string
  target: string
  relation: RelationType
  weight: number
  intensity: EdgeIntensity
  evidence: string
  created_by: 'llm' | 'system' | 'vector'
  created_at: string
}

export interface AssociationGraph {
  domain: string
  concepts: Record<string, ConceptNode>
  resources: Record<string, ResourceNode>
  associations: Association[]
  metadata: {
    derived_events: Record<string, string>
    last_full_sync: string | null
    schema_version: string
  }
  generated_at: string | null
}

export interface NeighborEntry {
  name: string
  relation: RelationType
  intensity: EdgeIntensity
  weight: number
  hops: number
}

export interface NeighborsResponse {
  domain: string
  node: string
  hops: number
  neighbors: NeighborEntry[]
}

export interface StatisticsResponse {
  concepts: number
  resources: number
  associations: number
  derived_events: number
}

export interface SyncResponse {
  concepts: number
  resources: number
  associations: number
}

export interface FlushLLMResponse {
  flushed: number
  added?: number
  message?: string
  errors?: number
  event_ids?: string[]
}

// ============================================================
// API
// ============================================================

/** 完整 associations.json 原始内容（前端可视化首选）。 */
export async function getAssociations(domain: string): Promise<AssociationGraph> {
  const { data } = await http.get(`/associations/${encodeURIComponent(domain)}`)
  return data
}

export async function getConcepts(domain: string): Promise<{
  domain: string
  concepts: Record<string, ConceptNode>
}> {
  const { data } = await http.get(
    `/associations/${encodeURIComponent(domain)}/concepts`,
  )
  return data
}

export async function getResources(domain: string): Promise<{
  domain: string
  resources: Record<string, ResourceNode>
}> {
  const { data } = await http.get(
    `/associations/${encodeURIComponent(domain)}/resources`,
  )
  return data
}

export async function getEdges(domain: string): Promise<{
  domain: string
  associations: Association[]
  total: number
}> {
  const { data } = await http.get(
    `/associations/${encodeURIComponent(domain)}/edges`,
  )
  return data
}

export async function getNeighbors(
  domain: string,
  node: string,
  opts?: { hops?: number; relation?: RelationType; direction?: 'any' | 'out' | 'in' },
): Promise<NeighborsResponse> {
  const params: Record<string, string | number> = { node }
  if (opts?.hops !== undefined) params.hops = opts.hops
  if (opts?.relation) params.relation = opts.relation
  if (opts?.direction) params.direction = opts.direction
  const { data } = await http.get(
    `/associations/${encodeURIComponent(domain)}/neighbors`,
    { params },
  )
  return data
}

export async function getStatistics(domain: string): Promise<StatisticsResponse> {
  const { data } = await http.get(
    `/associations/${encodeURIComponent(domain)}/statistics`,
  )
  return data
}

export async function syncFull(domain: string): Promise<SyncResponse> {
  const { data } = await http.post(
    `/associations/${encodeURIComponent(domain)}/sync`,
  )
  return data
}

export async function syncNode(
  domain: string,
  node: string,
  enqueue_llm = true,
): Promise<SyncResponse> {
  const { data } = await http.post(
    `/associations/${encodeURIComponent(domain)}/sync-node`,
    { node, enqueue_llm },
  )
  return data
}

export async function flushLLM(domain: string): Promise<FlushLLMResponse> {
  const { data } = await http.post(
    `/associations/${encodeURIComponent(domain)}/flush-llm`,
  )
  return data
}

export async function clearAssociations(domain: string): Promise<{ ok: boolean; message: string }> {
  const { data } = await http.delete(
    `/associations/${encodeURIComponent(domain)}`,
  )
  return data
}

/** Manually add one association edge between two concept nodes (UI 右键). */
export async function addManualAssociation(
  domain: string,
  payload: {
    source: string
    target: string
    relation: RelationType
    weight?: number
    intensity?: string
    evidence?: string
  },
): Promise<{ ok: boolean; message: string; total: number; falkordb_synced?: boolean | null }> {
  const { data } = await http.post(
    `/associations/${encodeURIComponent(domain)}/manual`,
    payload,
  )
  return data
}

/** Manually delete one association edge (UI 右键). */
export async function deleteManualAssociation(
  domain: string,
  payload: { source: string; target: string; relation: RelationType },
): Promise<{ ok: boolean; message: string; total: number; falkordb_synced?: boolean | null }> {
  const { data } = await http.delete(
    `/associations/${encodeURIComponent(domain)}/manual`,
    { params: payload },
  )
  return data
}

/** 删除一个概念节点（关联图 + 主图谱）。处理「幽灵」概念（仅存在于关联图的节点）。 */
export async function deleteConcept(
  domain: string,
  name: string,
): Promise<{ ok: boolean; message: string; main_deleted: boolean; falkordb_synced?: boolean | null }> {
  const { data } = await http.delete(
    `/associations/${encodeURIComponent(domain)}/concept`,
    { params: { name } },
  )
  return data
}