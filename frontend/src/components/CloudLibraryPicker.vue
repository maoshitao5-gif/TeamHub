<template>
  <div class="library-picker" data-testid="cloud-library-picker">
    <span class="library-picker-label">共享资料库</span>
    <el-select v-if="libraries.length > 1" :model-value="modelValue || undefined" placeholder="选择资料库" aria-label="共享资料库" data-testid="shared-library-selector" :disabled="disabled || loading" :loading="loading" @change="id => emit('change', id)">
      <el-option v-for="library in libraries" :key="library.id" :label="libraryLabel(library)" :value="library.id" :data-testid="`shared-library-option-${library.id}`" />
    </el-select>
    <span v-else-if="libraries.length === 1" class="library-picker-current">{{ libraryLabel(libraries[0]) }}</span>
    <span v-else class="library-picker-current muted">{{ loading ? '正在加载资料库…' : '暂无可访问的资料库' }}</span>
    <span v-if="loading && modelValue" class="muted" role="status">正在切换…</span>
  </div>
</template>
<script setup>
import { libraryLabel } from '@/utils/sharedLibraries'
defineProps({ libraries: { type: Array, default: () => [] }, modelValue: { type: String, default: '' }, loading: Boolean, disabled: Boolean })
const emit = defineEmits(['change'])
</script>
<style scoped>
.library-picker { display: flex; align-items: center; gap: 14px; flex-wrap: wrap; }
.library-picker-label { color: #6b7b8c; font-size: 13px; }
.library-picker-current { color: #263b50; font-weight: 600; overflow-wrap: anywhere; }
.library-picker .el-select { width: min(420px, 100%); }
.muted { font-size: 13px; color: #8492a1; }
</style>
