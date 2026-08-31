<script setup lang="ts">
defineProps<{ src: string }>()
</script>

<template>
  <div class="fp-audio">
    <audio :src="src" controls class="fp-audio__player" />
  </div>
</template>

<style scoped>
.fp-audio {
  width: 100%;
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 32px;
  box-sizing: border-box;
}

.fp-audio__player {
  width: 100%;
  max-width: 520px;
}
</style>