<template>
  <aside v-if="view" class="search-match-excerpt" aria-label="检索命中说明" data-testid="search-match-excerpt">
    <p class="match-location"><strong>{{ view.source }}</strong><span v-if="view.location"> · {{ view.location }}</span></p>
    <p v-if="view.excerpt" class="match-excerpt">{{ view.excerpt }}</p>
    <p v-if="view.coverage" class="match-coverage">{{ view.coverage }}</p>
  </aside>
</template>

<script setup>
import { computed } from 'vue'
import { searchMatchView } from '@/utils/searchMatch'
const props = defineProps({ match: Object })
const view = computed(() => searchMatchView(props.match))
</script>

<style scoped>
.search-match-excerpt { width: 100%; box-sizing: border-box; padding: 10px 12px; border-left: 3px solid #8bbbf5; background: #f4f8fd; border-radius: 6px; color: #52647b; }
.search-match-excerpt p { margin: 0; overflow-wrap: anywhere; }
.match-location { font-size: 12px; line-height: 1.6; }
.match-location strong { color: #41658c; font-weight: 500; }
.match-excerpt { margin-top: 4px !important; font-size: 13px; line-height: 1.65; white-space: pre-wrap; display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical; overflow: hidden; }
.match-coverage { margin-top: 6px !important; color: #9a742e; font-size: 12px; line-height: 1.5; }
</style>
