<template>
  <div class="organize-tags">
    <el-select
      v-model="selectedTags"
      multiple
      filterable
      allow-create
      default-first-option
      :reserve-keyword="false"
      placeholder="例如设备、项目或业务"
      aria-label="查找标签"
      class="tag-select"
    >
      <el-option
        v-for="tag in availableTags"
        :key="tag.name"
        :label="tag.name"
        :value="tag.name"
      />
    </el-select>
    <p class="tag-help">至少一个标签即可完成整理；可搜索已有标签，或输入新标签后按回车添加。</p>
  </div>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  modelValue: { type: Array, default: () => [] },
  tags: { type: Array, default: () => [] },
})

const emit = defineEmits(['update:modelValue'])

const normalizeNames = (names) => [...new Set(
  names.filter((name) => typeof name === 'string').map((name) => name.trim()).filter(Boolean),
)]

const selectedTags = computed({
  get: () => props.modelValue,
  set: (names) => emit('update:modelValue', normalizeNames(names)),
})

const availableTags = computed(() => normalizeNames(props.tags.map((tag) => tag.name))
  .map((name) => ({ name })))
</script>

<style scoped>
.organize-tags {
  width: 100%;
  min-width: 0;
}

.tag-select {
  width: 100%;
}

.tag-select :deep(.el-select__wrapper) {
  min-height: 42px;
  padding: 7px 12px;
}

.tag-help {
  margin: 8px 0 0;
  color: var(--el-text-color-secondary, #909399);
  font-size: 12px;
  line-height: 1.7;
}
</style>
