<template>
  <section class="organize-location" aria-label="文件夹归类（可选）">
    <div class="location-header">
      <span class="location-title">归入文件夹 <span class="optional-label">可选</span></span>
      <el-button text type="primary" size="small" :aria-expanded="choosing" @click="choosing = !choosing">
        {{ choosing ? '收起' : modelValue ? '更改文件夹' : '选择文件夹' }}
      </el-button>
    </div>
    <p class="location-help">文件夹用于成套资料或项目交付；零散文件可直接放入资料库。</p>

    <div v-if="modelValue" class="selected-location">
      <el-icon class="folder-icon"><Folder /></el-icon>
      <div class="selected-location-text">
        <span class="selected-label">整理后位置</span>
        <span class="location-path" :title="modelValue">资料库 / {{ modelValue }}</span>
      </div>
      <el-button text size="small" @click="clearLocation">取消归入文件夹</el-button>
    </div>
    <div v-else class="default-location">
      <el-icon><Files /></el-icon>
      <span>直接放入资料库，通过标签查找</span>
    </div>

    <DirectoryTreeSelector
      v-if="choosing"
      ref="picker"
      :model-value="modelValue"
      root-label="直接放入资料库"
      class="location-picker"
      @update:model-value="selectLocation"
    />
  </section>
</template>

<script setup>
import { nextTick, ref, watch } from 'vue'
import { Files, Folder } from '@element-plus/icons-vue'
import DirectoryTreeSelector from '@/components/DirectoryTreeSelector.vue'

const props = defineProps({
  modelValue: { type: String, default: '' },
})

const emit = defineEmits(['update:modelValue'])
const choosing = ref(false)
const picker = ref(null)

watch(choosing, async (open) => {
  if (open) {
    await nextTick()
    picker.value?.$el.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
  }
})

const selectLocation = (path) => {
  emit('update:modelValue', path)
  choosing.value = false
}

const clearLocation = () => selectLocation('')

watch(() => props.modelValue, () => {
  choosing.value = false
})
</script>

<style scoped>
.organize-location {
  min-width: 0;
  margin-top: 24px;
  padding-top: 20px;
  border-top: 1px solid var(--el-border-color-lighter, #ebeef5);
}

.location-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.location-title {
  color: var(--el-text-color-regular, #606266);
  font-size: 14px;
  font-weight: 500;
}

.optional-label {
  margin-left: 6px;
  color: var(--el-text-color-secondary, #909399);
  font-size: 12px;
  font-weight: 400;
}

.location-help {
  margin: 6px 0 12px;
  color: var(--el-text-color-secondary, #909399);
  font-size: 12px;
  line-height: 1.7;
}

.selected-location,
.default-location {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 12px;
  border-radius: 8px;
  background: var(--el-fill-color-light, #f5f7fa);
}

.default-location {
  color: var(--el-text-color-regular, #606266);
  font-size: 13px;
  line-height: 1.6;
}

.default-location .el-icon,
.folder-icon {
  flex-shrink: 0;
  color: var(--el-color-primary, #409eff);
  font-size: 18px;
}

.selected-location-text {
  display: flex;
  flex: 1;
  flex-direction: column;
  min-width: 0;
  gap: 4px;
}

.selected-label {
  color: var(--el-text-color-secondary, #909399);
  font-size: 11px;
  line-height: 1.4;
}

.location-path {
  overflow-wrap: anywhere;
  color: var(--el-text-color-regular, #606266);
  font-size: 13px;
  line-height: 1.5;
}

.selected-location .el-button {
  flex-shrink: 0;
  margin: 0;
}

.location-picker {
  margin-top: 12px;
}

@media (max-width: 480px) {
  .selected-location {
    flex-wrap: wrap;
  }

  .selected-location .el-button {
    margin-left: 28px;
  }
}
</style>
