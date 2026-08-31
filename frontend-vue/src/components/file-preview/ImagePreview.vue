<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{ src: string; alt: string }>()

const imgStyle = computed(() => ({
  maxWidth: '100%',
  maxHeight: '70vh',
  display: 'block',
  margin: '0 auto',
  borderRadius: '8px',
  background: 'transparent',
}))

// 抑制 unused warning
void props
</script>

<template>
  <div class="fp-image">
    <img :src="src" :alt="alt" class="fp-image__img" :style="imgStyle" />
  </div>
</template>

<style scoped>
.fp-image {
  width: 100%;
  height: 100%;
  overflow: auto;
  padding: 16px;
  box-sizing: border-box;
  text-align: center;
}

.fp-image__img {
  border-radius: 8px;
  object-fit: contain;
  background: transparent;
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.25);
}
</style>