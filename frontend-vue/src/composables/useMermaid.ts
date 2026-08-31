/**
 * useMermaid — lazy-loaded Mermaid diagram renderer with fullscreen viewer.
 *
 * Uses a **global MutationObserver** to auto-detect and render every
 * `<pre><code class="language-mermaid">…</code></pre>` anywhere in the app.
 * This means NO component-level integration is needed — just call
 * `installMermaidObserver()` once (e.g. in App.vue onMounted) and mount
 * `<MermaidViewer />` somewhere in the tree.
 *
 * Key design decisions:
 *  - **Lazy import**: Mermaid (~2 MB) is only loaded when a diagram is
 *    actually encountered, keeping the initial bundle small.
 *  - **Dark theme**: matches the app's dark design system.
 *  - **securityLevel 'loose'**: allows rich HTML labels (`<br/>`, `<i>`, …).
 *  - **Graceful failure**: if `mermaid.render` throws (e.g. the source is
 *    still streaming in or has a syntax error) the original `<pre>` is left
 *    intact so the raw source stays readable, and the block is retried on
 *    the next call.
 *  - **Fullscreen viewer**: each diagram gets an "expand" button; clicking
 *    it opens the SVG in a zoomable / pannable fullscreen overlay.
 *  - **Streaming-safe**: the observer is debounced so during rapid DOM
 *    updates (e.g. SSE streaming) we only attempt rendering after the DOM
 *    settles. An `isConnected` check prevents replacing detached nodes.
 */

import { ref } from 'vue'

/** Minimal subset of the Mermaid API we depend on. */
interface MermaidAPI {
  initialize: (config: Record<string, unknown>) => void
  render: (
    id: string,
    text: string,
  ) => Promise<{ svg: string; bindFunctions?: (el: Element) => void }>
}

let mermaidLib: MermaidAPI | null = null
let initPromise: Promise<void> | null = null
let renderSeq = 0

// ── Fullscreen viewer shared state (module-level singleton) ──
export const viewerVisible = ref(false)
export const viewerSvg = ref('')
export const viewerTitle = ref('')

/** Open the fullscreen diagram viewer with the given SVG markup. */
export function openMermaidViewer(svgHtml: string, title = '流程图') {
  viewerSvg.value = svgHtml
  viewerTitle.value = title
  viewerVisible.value = true
}

/** Lazily import + initialise Mermaid exactly once. */
function ensureInit(): Promise<void> {
  if (initPromise) return initPromise
  initPromise = (async () => {
    const mod = await import('mermaid')
    const m = (mod.default ?? mod) as MermaidAPI
    m.initialize({
      startOnLoad: false,
      theme: 'dark',
      securityLevel: 'loose',
      fontFamily: 'Inter, "PingFang SC", "Microsoft YaHei", sans-serif',
      flowchart: { useMaxWidth: true, htmlLabels: true, curve: 'basis' },
      sequence: { useMaxWidth: true },
      gantt: { useMaxWidth: true },
    })
    mermaidLib = m
  })()
  return initPromise
}

/**
 * Render every Mermaid code block inside `root` into an SVG diagram.
 *
 * - On success the `<pre>` is swapped for `<div class="mermaid-diagram">`.
 * - On failure the `<pre>` is left untouched (raw source stays visible) and
 *   the processing flag is cleared so the block can be retried later.
 * - Already-processing blocks are skipped to avoid duplicate concurrent
 *   renders of the same element.
 * - Each rendered diagram gets an "expand" button for fullscreen viewing.
 */
export async function renderMermaidBlocks(root: HTMLElement | null): Promise<void> {
  if (!root) return
  await ensureInit()
  if (!mermaidLib) return

  // Collect every <pre> whose <code> is tagged language-mermaid and not busy.
  const targets: HTMLPreElement[] = []
  root.querySelectorAll<HTMLPreElement>('pre').forEach((pre) => {
    if (pre.dataset.mermaidProcessing === '1') return
    const code = pre.querySelector<HTMLElement>('code')
    if (!code) return
    if (/\blanguage-mermaid\b/.test(code.className || '')) {
      targets.push(pre)
    }
  })
  if (!targets.length) return

  // Mark as busy *before* awaiting so concurrent calls skip them.
  targets.forEach((pre) => (pre.dataset.mermaidProcessing = '1'))

  await Promise.all(
    targets.map(async (pre) => {
      const code = pre.querySelector('code')
      const source = code?.textContent || ''
      if (!source.trim()) {
        delete pre.dataset.mermaidProcessing
        return
      }
      try {
        const id = `mmd-${++renderSeq}`
        const { svg, bindFunctions } = await mermaidLib!.render(id, source)

        // The <pre> may have been removed from the DOM by a v-html re-render
        // during streaming. Skip replacement if it's no longer connected.
        if (!pre.isConnected) return

        // Build wrapper with toolbar
        const wrap = document.createElement('div')
        wrap.className = 'mermaid-diagram'

        const toolbar = document.createElement('div')
        toolbar.className = 'mermaid-toolbar'

        const expandBtn = document.createElement('button')
        expandBtn.className = 'mermaid-expand-btn'
        expandBtn.type = 'button'
        expandBtn.title = '放大查看'
        expandBtn.innerHTML =
          '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="15 3 21 3 21 9"/><polyline points="9 21 3 21 3 15"/><line x1="21" y1="3" x2="14" y2="10"/><line x1="3" y1="21" x2="10" y2="14"/></svg>'
        expandBtn.addEventListener('click', (e) => {
          e.stopPropagation()
          e.preventDefault()
          openMermaidViewer(svg, '流程图')
        })
        toolbar.appendChild(expandBtn)

        const svgHolder = document.createElement('div')
        svgHolder.className = 'mermaid-svg-holder'
        svgHolder.innerHTML = svg

        wrap.appendChild(toolbar)
        wrap.appendChild(svgHolder)
        pre.replaceWith(wrap)
        if (bindFunctions) bindFunctions(svgHolder)
      } catch {
        // Leave the <pre> as a readable code-block fallback and allow retry.
        delete pre.dataset.mermaidProcessing
      }
    }),
  )
}

// ── Global MutationObserver: auto-detect & render mermaid blocks ──

let observer: MutationObserver | null = null
let debounceTimer: ReturnType<typeof setTimeout> | undefined

/**
 * Install a global MutationObserver that watches the entire document for
 * mermaid code blocks and renders them automatically.
 *
 * Call this once in App.vue onMounted. The observer is debounced (350ms)
 * so that during rapid DOM updates (e.g. SSE streaming) we only attempt
 * rendering after the DOM settles.
 */
export function installMermaidObserver() {
  if (observer) return // already installed

  // Initial scan for any blocks already in the DOM.
  scheduleScan()

  observer = new MutationObserver(() => {
    scheduleScan()
  })

  observer.observe(document.body, {
    childList: true,
    subtree: true,
  })
}

function scheduleScan() {
  clearTimeout(debounceTimer)
  debounceTimer = setTimeout(() => {
    renderMermaidBlocks(document.body)
  }, 350)
}
