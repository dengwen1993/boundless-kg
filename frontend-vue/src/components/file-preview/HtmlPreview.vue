<script setup lang="ts">
defineProps<{ content: string }>()
</script>

<template>
  <div class="fp-html">
    <iframe
      class="fp-html__frame"
      :srcdoc="content"
      sandbox="allow-scripts allow-popups allow-forms"
    />
  </div>
</template>

<style scoped>
.fp-html {
  width: 100%;
  height: 100%;
  display: flex;
  flex-direction: column;
  align-self: stretch;
  background: #fff;
  border-radius: 8px;
  overflow: hidden;
}

.fp-html__frame {
  width: 100%;
  flex: 1;
  border: 1px solid var(--border-color, #2a3042);
  border-radius: 8px;
  background: #fff;
  min-height: 60vh;
}
</style>