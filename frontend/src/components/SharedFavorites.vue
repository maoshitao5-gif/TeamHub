<template>
  <el-button @click="open">共享收藏</el-button>
  <el-dialog v-model="visible" title="本空间的共享收藏" width="600px" @closed="openSelected">
    <el-alert v-if="error" :title="error" type="error" :closable="false" />
    <div v-loading="loading">
      <div v-for="item in items" :key="item.id" class="shared-favorite-row">
        <span>{{ item.document?.name || '暂不可访问的收藏' }}</span>
        <p v-if="item.error">{{ item.error }}；可稍后刷新重试。</p>
        <el-button :disabled="!item.document || busy" @click="choose(item)">打开资料</el-button>
        <el-button :disabled="busy" @click="remove(item)">取消收藏</el-button>
      </div>
      <el-empty v-if="!loading && !error && !items.length" description="暂无共享收藏" />
    </div>
    <template #footer><el-button :disabled="loading || busy" @click="open">刷新收藏</el-button></template>
  </el-dialog>
</template>

<script setup>
import { ref, onBeforeUnmount } from 'vue'
import { getSharedFavorites, resolveSharedFavorite, removeFavorite } from '@/api/favorite'
const props = defineProps({ server: String, workspace: String })
const emit = defineEmits(['open'])
const visible = ref(false), loading = ref(false), busy = ref(false), error = ref(''), items = ref([])
let sequence = 0
let selectedDocument = null
const message = e => e.response?.data?.error?.message || e.response?.data?.detail || e.message || '请求失败'
async function open() {
  visible.value = true; loading.value = true; error.value = ''
  const run = ++sequence
  try {
    const favorites = await getSharedFavorites()
    const scoped = favorites.filter(item => item.server_url === props.server && item.workspace_id === props.workspace)
    const resolved = []
    for (const item of scoped) {
      if (run !== sequence) return
      try { resolved.push({ ...item, document: await resolveSharedFavorite(item.id) }) }
      catch (e) { resolved.push({ ...item, error: message(e) }) }
    }
    if (run === sequence) items.value = resolved
  } catch (e) { if (run === sequence) error.value = message(e) }
  finally { if (run === sequence) loading.value = false }
}
function choose(item) { selectedDocument = item.document; visible.value = false }
function openSelected() {
  if (selectedDocument) emit('open', selectedDocument)
  selectedDocument = null
}
async function remove(item) {
  busy.value = true
  try { await removeFavorite(item.id); items.value = items.value.filter(row => row.id !== item.id) }
  catch (e) { error.value = message(e) }
  finally { busy.value = false }
}
onBeforeUnmount(() => { sequence++ })
</script>

<style scoped>
.shared-favorite-row { padding: 12px 0; border-bottom: 1px solid #eee; }
.shared-favorite-row span { display: block; margin-bottom: 8px; }
.shared-favorite-row p { color: #777; }
</style>
