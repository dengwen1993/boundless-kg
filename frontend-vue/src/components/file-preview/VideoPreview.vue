<script setup lang="ts">
defineProps<{ src: string }>()
</script>

<template>
  <div class="fp-video">
    <video :src="src" controls class="fp-video__player" />
  </div>
</template>

<style scoped>
.fp-video {
  width: 100%;
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 16px;
  box-sizing: border-box;
  background: #000;
  border-radius: 8px;
}

.fp-video__player {
  width: 100%;
  max-height: 70vh;
  object-fit: contain;
  border-radius: 6px;
}
</style>