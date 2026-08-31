import { ref, type Ref } from 'vue'
import * as d3 from 'd3'
import type { GraphData, GraphNode } from '@/types/graph'
import { useGraphStore } from '@/stores/graph'

interface SelectedNodeInfo {
  name: string
  tier: string
  level: number
  childCount: number
  isDomainRoot: boolean
}

// Callbacks for UI interactions
interface CanvasCallbacks {
  onContextMenu?: (node: SelectedNodeInfo, x: number, y: number) => void
  onEditNode?: (node: SelectedNodeInfo) => void
  onAddChild?: (parentName: string) => void
  onOpenNote?: (nodeName: string) => void
}

const COLOR: Record<string, string> = {
  L0: '#ffb347',
  L1: '#4c7dff',
  L2: '#7c5cff',
  L3: '#f59e0b',
  leaf: '#22d3a5',
}

const RADIUS: Record<string, number> = {
  L0: 11,
  L1: 9,
  L2: 7,
  L3: 6,
  leaf: 5,
}

const NODE_W: Record<string, number> = {
  L0: 200,
  L1: 160,
  L2: 140,
  L3: 120,
  leaf: 110,
}

const NODE_H = 36
const LEVEL_GAP = 70
const SIBLING_GAP = 14

/** Shared singleton state for the canvas — accessible from any component */
const containerRef: Ref<HTMLElement | null> = ref(null)
let svg: d3.Selection<SVGSVGElement, unknown, null, undefined> | null = null
let gRoot: d3.Selection<SVGGElement, unknown, null, undefined> | null = null
let gLinks: d3.Selection<SVGGElement, unknown, null, undefined> | null = null
let gNodes: d3.Selection<SVGGElement, unknown, null, undefined> | null = null
let zoomBehavior: d3.ZoomBehavior<SVGSVGElement, unknown> | null = null
let collapsed = new Set<string>()
let callbacks: CanvasCallbacks = {}

function getNodeWidth(tier: string | undefined): number {
  return NODE_W[tier || 'leaf'] ?? 110
}

/** Build a d3.hierarchy from the current graph, rooted at drill-stack[-1] or domain root */
function buildHierarchy(graph: GraphData | null, drillStack: string[]) {
  if (!graph) return null

  const byName = new Map<string, GraphNode>()
  graph.nodes.forEach((n) => byName.set(n.name, n))

  let rootName: string
  if (drillStack.length > 0) {
    rootName = drillStack[drillStack.length - 1]
  } else {
    const domainRoot = graph.nodes.find((n) => n.isDomainRoot)
    rootName = domainRoot ? domainRoot.name : graph.nodes[0]?.name ?? ''
  }
  const rootNode = byName.get(rootName)
  if (!rootNode) return null

  function buildSubtree(node: GraphNode): any {
    const data: any = {
      name: node.name,
      tier: node.tier || 'leaf',
      level: node.level || 1,
      isDomainRoot: !!node.isDomainRoot,
      childCount: (node.links || []).length,
      children: null as any,
    }

    if (collapsed.has(node.name)) {
      data.children = []
      return data
    }

    const childNodes = (node.links || [])
      .map((c) => byName.get(c))
      .filter(Boolean) as GraphNode[]

    if (childNodes.length > 0) {
      data.children = childNodes.map((c) => buildSubtree(c))
    }
    return data
  }

  const data = buildSubtree(rootNode)
  return d3.hierarchy(data, (d: any) => d.children)
}

export function useGraphCanvas() {
  const graphStore = useGraphStore()

  function init(container: HTMLElement, cb?: CanvasCallbacks) {
    containerRef.value = container
    callbacks = cb || {}

    svg = d3
      .select(container)
      .append('svg')
      .attr('class', 'mindmap-svg')
      .attr('width', '100%')
      .attr('height', '100%')

    // defs
    const defs = svg.append('defs')
    const filter = defs
      .append('filter')
      .attr('id', 'node-shadow')
      .attr('x', '-50%')
      .attr('y', '-50%')
      .attr('width', '200%')
      .attr('height', '200%')
    filter
      .append('feDropShadow')
      .attr('dx', 0)
      .attr('dy', 2)
      .attr('stdDeviation', 3)
      .attr('flood-color', '#000')
      .attr('flood-opacity', 0.35)

    // background click clears selection
    svg.on('click', (e) => {
      if (e.target === svg!.node()) {
        graphStore.selectNode(null)
        applySelectionStyles()
      }
    })

    // Right-click on the SVG background — open the context menu anchored
    // to the currently selected node, if any.  This is a fallback so
    // users who right-click on padding / link space (where no node
    // receives the event) still get a usable menu when they previously
    // selected a node.
    svg.on('contextmenu', (e: any) => {
      // Only fire when the click landed on the SVG root (not on a node —
      // node-level contextmenu fires its own handler and stopPropagation).
      if (e.target !== svg!.node()) return
      e.preventDefault()
      const sel = graphStore.selectedNode
      if (!sel) return
      const info: SelectedNodeInfo = {
        name: sel.name,
        tier: sel.tier || 'leaf',
        level: sel.level || 1,
        childCount: sel.childCount ?? (sel.links || []).length,
        isDomainRoot: !!sel.isDomainRoot,
      }
      if (callbacks.onContextMenu) {
        callbacks.onContextMenu(info, e.clientX, e.clientY)
      }
    })

    gRoot = svg.append('g').attr('class', 'mm-root')
    gLinks = gRoot.append('g').attr('class', 'mm-links')
    gNodes = gRoot.append('g').attr('class', 'mm-nodes')

    zoomBehavior = d3
      .zoom()
      .scaleExtent([0.25, 3])
      .on('zoom', (e) => {
        const t = e.transform
        // Guard against NaN transforms (e.g. when container was hidden)
        if (isNaN(t.x) || isNaN(t.y) || isNaN(t.k)) return
        gRoot!.attr('transform', t)
      })
    svg.call(zoomBehavior)

    // resize observer
    const ro = new ResizeObserver(() => {
      if (graphStore.graph) render()
    })
    ro.observe(container)
  }

  function render() {
    if (!svg || !gRoot || !gNodes || !gLinks || !containerRef.value) return
    if (!graphStore.graph) return
    // Skip if container is hidden (v-show=false) — dimensions are 0
    const rect = containerRef.value.getBoundingClientRect()
    if (rect.width === 0 || rect.height === 0) return

    try {
      _renderImpl()
    } catch (e) {
      console.error('[GraphCanvas.render] crashed:', e)
    }
  }

  function _renderImpl() {
    const root = buildHierarchy(graphStore.graph, graphStore.drillStack)
    if (!root || !containerRef.value || !gNodes || !gLinks || !gRoot) return

    const rect = containerRef.value.getBoundingClientRect()
    const width = rect.width
    const height = rect.height

    const layout = d3
      .tree()
      .nodeSize([NODE_H + SIBLING_GAP, NODE_W['L0'] + LEVEL_GAP])
      .separation((a, b) => (a.parent === b.parent ? 1 : 1.4))

    layout(root as any)

    const nodes = root.descendants()
    const links = root.links()

    // bounds
    let minX = Infinity,
      maxX = -Infinity,
      minY = Infinity,
      maxY = -Infinity
    nodes.forEach((n: any) => {
      const w = getNodeWidth(n.data.tier)
      const h = NODE_H
      if (n.x - h / 2 < minX) minX = n.x - h / 2
      if (n.x + h / 2 > maxX) maxX = n.x + h / 2
      if (n.y - w / 2 < minY) minY = n.y - w / 2
      if (n.y + w / 2 > maxY) maxY = n.y + w / 2
    })
    const treeH = maxY - minY

    const PAD = 60
    const offsetX = PAD - minX
    const offsetY = (height - treeH) / 2 - minY

    svg!.attr('viewBox', `0 0 ${width} ${height}`)

    // ── Nodes ──
    const nodeSel = gNodes
      .selectAll('g.mm-node')
      .data(nodes, (d: any) => d.data.name + ':' + d.depth)

    nodeSel.exit().remove()

    const nodeEnter = nodeSel
      .enter()
      .append('g')
      .attr('class', (d: any) => `mm-node mm-node--${d.data.tier}`)
      .attr('transform', (d: any) => `translate(${d.y + offsetY}, ${d.x + offsetX})`)

    // card body
    nodeEnter
      .append('rect')
      .attr('class', (d: any) => `mm-card ${d.data.isDomainRoot ? 'mm-card--virtual' : ''}`)
      .attr('x', (d: any) => -getNodeWidth(d.data.tier) / 2)
      .attr('y', -NODE_H / 2)
      .attr('width', (d: any) => getNodeWidth(d.data.tier))
      .attr('height', NODE_H)
      .attr('rx', 8)
      .attr('ry', 8)

    // connector dot (left edge)
    nodeEnter
      .append('circle')
      .attr('class', 'mm-connector')
      .attr('cx', (d: any) => -getNodeWidth(d.data.tier) / 2)
      .attr('cy', 0)
      .attr('r', (d: any) => RADIUS[d.data.tier] || 5)

    // label
    nodeEnter
      .append('text')
      .attr('class', 'mm-label')
      .attr('text-anchor', 'middle')
      .attr('dominant-baseline', 'central')
      .attr('dy', '0.35em')
      .attr('y', 0)
      .text((d: any) => d.data.name)

    // collapse triangle for non-leaf nodes
    nodeEnter
      .filter((d: any) => d.data.children && d.data.children.length > 0)
      .append('g')
      .attr('class', 'mm-collapse')
      .attr('transform', (d: any) => `translate(${getNodeWidth(d.data.tier) / 2 - 4}, 0)`)
      .append('polygon')
      .attr('points', '-4,-4 4,0 -4,4')
      .attr('class', 'mm-collapse__icon')

    // "+" add-child button (hidden, shows on hover)
    nodeEnter
      .filter((d: any) => !d.data.isDomainRoot || d.data.children?.length > 0)
      .append('g')
      .attr('class', 'mm-add-btn')
      .attr('transform', `translate(0, ${NODE_H / 2 + 12})`)
      .attr('opacity', 0)
    nodeEnter
      .select('.mm-add-btn')
      .append('circle')
      .attr('r', 9)
      .attr('class', 'mm-add-btn__bg')
    nodeEnter
      .select('.mm-add-btn')
      .append('text')
      .attr('text-anchor', 'middle')
      .attr('dominant-baseline', 'middle')
      .attr('y', 1)
      .attr('class', 'mm-add-btn__label')
      .text('+')

    // merge + position update
    const allNodes = nodeEnter.merge(nodeSel as any)
    allNodes.attr('transform', (d: any) => `translate(${d.y + offsetY}, ${d.x + offsetX})`)

    // update label text (for renamed nodes)
    allNodes.select('text.mm-label').text((d: any) => d.data.name)

    // ── Links ──
    const linkPath = (d: any) => {
      const sy = d.source.y + offsetY + getNodeWidth(d.source.data.tier) / 2
      const ty = d.target.y + offsetY - getNodeWidth(d.target.data.tier) / 2
      const sx = d.source.x + offsetX
      const tx = d.target.x + offsetX
      const mx = (sy + ty) / 2
      return `M${sy},${sx} C${mx},${sx} ${mx},${tx} ${ty},${tx}`
    }

    const linkSel = gLinks
      .selectAll('path.mm-link')
      .data(links, (d: any) => d.source.data.name + '->' + d.target.data.name)

    linkSel.exit().remove()

    const linkEnter = linkSel
      .enter()
      .append('path')
      .attr('class', (d: any) => `mm-link mm-link--${d.target.data.tier}`)
      .attr('d', linkPath)

    linkEnter.merge(linkSel as any).attr('d', linkPath)

    // ── Interactions ──
    allNodes
      .on('click', (e: any, d: any) => {
        e.stopPropagation()
        const info: SelectedNodeInfo = {
          name: d.data.name,
          tier: d.data.tier,
          level: d.data.level,
          childCount: d.data.childCount,
          isDomainRoot: !!d.data.isDomainRoot,
        }
        graphStore.selectNode({
          name: info.name,
          links: [],
          level: info.level,
          tier: info.tier as any,
          childCount: info.childCount,
          isDomainRoot: info.isDomainRoot,
        })
        applySelectionStyles()
      })
      .on('dblclick', (e: any, d: any) => {
        e.stopPropagation()
        // Domain root: pop drill (go back up)
        if (d.data.isDomainRoot && d.depth === 0 && graphStore.drillStack.length > 0) {
          graphStore.popDrill()
          render()
          return
        }
        // All other nodes: open the note panel
        if (callbacks.onOpenNote) {
          callbacks.onOpenNote(d.data.name)
        }
      })
      .on('contextmenu', (e: any, d: any) => {
        e.preventDefault()
        e.stopPropagation()
        const info: SelectedNodeInfo = {
          name: d.data.name,
          tier: d.data.tier,
          level: d.data.level,
          childCount: d.data.childCount,
          isDomainRoot: !!d.data.isDomainRoot,
        }
        graphStore.selectNode({
          name: info.name,
          links: [],
          level: info.level,
          tier: info.tier as any,
          childCount: info.childCount,
          isDomainRoot: info.isDomainRoot,
        })
        applySelectionStyles()
        if (callbacks.onContextMenu) {
          callbacks.onContextMenu(info, e.clientX, e.clientY)
        }
      })
      .on('mouseenter', function () {
        d3.select(this).select('.mm-add-btn').attr('opacity', 1)
      })
      .on('mouseleave', function () {
        d3.select(this).select('.mm-add-btn').attr('opacity', 0)
      })

    // collapse triangle
    allNodes
      .select('.mm-collapse')
      .style('cursor', 'pointer')
      .on('click', (e: any, d: any) => {
        e.stopPropagation()
        toggleCollapse(d.data.name)
      })

    // "+" add child button
    allNodes
      .select('.mm-add-btn')
      .style('cursor', 'pointer')
      .on('click', (e: any, d: any) => {
        e.stopPropagation()
        if (callbacks.onAddChild) {
          callbacks.onAddChild(d.data.name)
        }
      })

    applySelectionStyles()
    requestAnimationFrame(() => fit())
  }

  function applySelectionStyles() {
    if (!gNodes || !gLinks) return
    const sel = graphStore.selectedNode
    gNodes
      .selectAll('.mm-node')
      .classed('mm-node--selected', (d: any) => d.data.name === sel?.name)
    gLinks
      .selectAll('.mm-link')
      .classed('mm-link--active', (d: any) =>
        d.source.data.name === sel?.name || d.target.data.name === sel?.name,
      )
  }

  function toggleCollapse(nodeName: string) {
    if (collapsed.has(nodeName)) collapsed.delete(nodeName)
    else collapsed.add(nodeName)
    render()
  }

  function zoomBy(factor: number) {
    if (svg && zoomBehavior) {
      svg.transition().duration(200).call(zoomBehavior.scaleBy, factor)
    }
  }

  function fit() {
    if (!svg || !gRoot || !zoomBehavior || !containerRef.value) return
    try {
      const rect = containerRef.value.getBoundingClientRect()
      if (rect.width === 0 || rect.height === 0) return // container hidden
      const bbox = (gRoot.node() as SVGGElement).getBBox()
      if (bbox.width === 0 || bbox.height === 0) return
      const PAD = 40
      // For very large trees (e.g. 400+ nodes laid out as a 15000px-tall
      // column) the natural fit scale is tiny (~0.04) and every node
      // becomes 1-2px — effectively invisible. Clamp the lower bound so
      // the user can at least read individual nodes; the overflow is
      // reachable by panning.
      const MIN_FIT_SCALE = 0.4
      const scale = Math.max(
        MIN_FIT_SCALE,
        Math.min(
          (rect.width - PAD * 2) / bbox.width,
          (rect.height - PAD * 2) / bbox.height,
          1.2,
        ),
      )
      if (!isFinite(scale) || scale <= 0) return
      const tx = (rect.width - bbox.width * scale) / 2 - bbox.x * scale
      const ty = (rect.height - bbox.height * scale) / 2 - bbox.y * scale
      if (isNaN(tx) || isNaN(ty)) return
      svg
        .transition()
        .duration(400)
        .call(
          zoomBehavior.transform,
          d3.zoomIdentity.translate(tx, ty).scale(scale),
        )
    } catch {
      /* bbox not yet available */
    }
  }

  /** Snap zoom/pan back to identity and refit. Used by the "reset" button
   * and on domain switches where we want a clean viewport.
   *
   * For very large trees the follow-up ``fit()`` may still produce a
   * tiny scale, so ``fit()`` itself clamps to a minimum readable scale
   * (see ``MIN_FIT_SCALE`` below). */
  function resetView() {
    if (!svg || !zoomBehavior || !containerRef.value) return
    // Skip if container is hidden (e.g. outline mode is active)
    const rect = containerRef.value.getBoundingClientRect()
    if (rect.width === 0 || rect.height === 0) return
    svg.transition().duration(300).call(zoomBehavior.transform, d3.zoomIdentity)
    setTimeout(() => fit(), 320)
  }

  /** Clear the module-level collapsed-node set without touching the
   * viewport. Call this on a domain switch so collapsed branches from
   * the previous graph don't carry over and silently hide nodes in
   * the new graph (which may have name collisions). */
  function clearCollapsed() {
    collapsed.clear()
  }

  function highlight(term: string) {
    if (!gNodes || !gLinks) return
    if (!term || !graphStore.graph) {
      gNodes
        .selectAll('.mm-node')
        .classed('mm-node--selected', (d: any) =>
          d.data.name === graphStore.selectedNode?.name,
        )
      gLinks.selectAll('.mm-link').classed('mm-link--active', false)
      return
    }
    const t = term.toLowerCase()
    const matches = new Set(
      graphStore.graph.nodes
        .filter((n) => n.name.toLowerCase().includes(t))
        .map((n) => n.name),
    )
    gNodes
      .selectAll('.mm-node')
      .classed('mm-node--selected', (d: any) => matches.has(d.data.name))
      .attr('opacity', (d: any) =>
        matches.size === 0 || matches.has(d.data.name) ? 1 : 0.3,
      )
  }

  function getVisibleNodes(): string[] {
    if (!graphStore.graph) return []
    if (graphStore.drillStack.length === 0) {
      return graphStore.graph.nodes
        .filter((n) => n.level === 1)
        .map((n) => n.name)
    }
    const top = graphStore.drillStack[graphStore.drillStack.length - 1]
    const parent = graphStore.nodeMap.get(top)
    if (!parent) return []
    return [top, ...(parent.links || [])]
  }

  return {
    init,
    render,
    zoomBy,
    fit,
    resetView,
    clearCollapsed,
    highlight,
    toggleCollapse,
    getVisibleNodes,
  }
}
