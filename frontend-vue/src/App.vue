<template>
  <div class="app-layout">
    <!-- Top bar -->
    <TopBar />

    <!-- Main workspace -->
    <main class="workspace">
      <!-- Left: graph / outline / associations (hidden while note is open so the note owns the middle) -->
      <GraphCanvas
        v-show="graphStore.viewMode === 'graph' && !graphStore.notePanelVisible"
        class="workspace__pane"
      />
      <OutlineView
        v-show="graphStore.viewMode === 'outline' && !graphStore.notePanelVisible"
        class="workspace__pane"
      />
      <AssociationsView
        v-show="graphStore.viewMode === 'associations' && !graphStore.notePanelVisible"
        class="workspace__pane"
      />

      <!-- Middle: inline note panel (renders only when a node is opened) -->
      <NodeNotePanel v-show="graphStore.notePanelVisible" class="workspace__pane" />

      <!-- Right: AI assistant sidebar — always visible so user can Q&A while reading -->
      <ChatPanel />
    </main>

    <!-- Fullscreen Mermaid diagram viewer (teleported to body) -->
    <MermaidViewer />
  </div>
</template>

<script setup lang="ts">
import { onMounted } from 'vue'
import TopBar from '@/components/TopBar.vue'
import GraphCanvas from '@/components/GraphCanvas.vue'
import OutlineView from '@/components/OutlineView.vue'
import AssociationsView from '@/components/AssociationsView.vue'
import ChatPanel from '@/components/ChatPanel.vue'
import NodeNotePanel from '@/components/NodeNotePanel.vue'
import MermaidViewer from '@/components/MermaidViewer.vue'
import { useGraphStore } from '@/stores/graph'
import { installMermaidObserver } from '@/composables/useMermaid'
import { getSelectedDomain } from '@/utils/storage'

const graphStore = useGraphStore()

onMounted(async () => {
  await graphStore.loadDomains()
  // 优先恢复用户上次选择的领域；若该领域已不存在，则回退到第一个
  const remembered = getSelectedDomain()
  const target = graphStore.visibleDomains.find((d) => d.name === remembered)
    ?? graphStore.visibleDomains[0]
  if (target) {
    await graphStore.loadGraph(target.name)
  }
  // Auto-detect and render Mermaid diagrams anywhere in the app.
  installMermaidObserver()
})
</script>

<style scoped>
.app-layout {
  display: flex;
  flex-direction: column;
  height: 100vh;
  width: 100vw;
  overflow: hidden;
}

.workspace {
  flex: 1;
  display: flex;
  overflow: hidden;
  min-width: 0;
}

.workspace__pane {
  flex: 1 1 auto;
  min-width: 0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
</style>
