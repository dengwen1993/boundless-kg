<script setup lang="ts">
defineProps<{ src: string }>()
</script>

<template>
  <div class="fp-pdf">
    <iframe class="fp-pdf__frame" :src="src" />
  </div>
</template>

<style scoped>
.fp-pdf {
  width: 100%;
  height: 100%;
  display: flex;
  flex-direction: column;
  align-self: stretch;
}

.fp-pdf__frame {
  width: 100%;
  flex: 1;
  border: 1px solid var(--border-color, #2a3042);
  border-radius: 8px;
  background: #fff;
  min-height: 65vh;
}
</style>