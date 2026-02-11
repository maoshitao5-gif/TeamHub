<template>
  <el-dialog
    v-model="visible"
    :title="filename"
    width="80%"
    :before-close="handleClose"
    class="file-preview-dialog"
  >
    <div class="preview-content" v-loading="loading">
      <!-- 图片预览 -->
      <img
        v-if="isImage"
        :src="previewUrl"
        :alt="filename"
        class="preview-image"
        @error="handleImageError"
      />
      
      <!-- PDF预览 -->
      <iframe
        v-else-if="isPdf"
        :src="previewUrl"
        class="preview-pdf"
        frameborder="0"
      />
      
      <!-- 不支持预览的文件类型 -->
      <div v-else class="preview-unsupported">
        <el-icon class="unsupported-icon"><Document /></el-icon>
        <p>该文件类型不支持预览</p>
        <el-button type="primary" @click="handleDownload">下载文件</el-button>
      </div>
    </div>
    
    <template #footer>
      <el-button @click="handleClose">关闭</el-button>
      <el-button type="primary" @click="handleDownload">下载</el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { ref, computed, watch } from 'vue'
import { Document } from '@element-plus/icons-vue'
import { getPreviewUrl, downloadFile } from '@/api/file'

const props = defineProps({
  modelValue: {
    type: Boolean,
    default: false
  },
  file: {
    type: Object,
    default: null
  }
})

const emit = defineEmits(['update:modelValue', 'download'])

const visible = computed({
  get: () => props.modelValue,
  set: (val) => emit('update:modelValue', val)
})

const loading = ref(false)
const imageError = ref(false)

// 预览URL
const previewUrl = computed(() => {
  if (!props.file) return ''
  return getPreviewUrl(props.file.id)
})

// 文件名
const filename = computed(() => {
  return props.file?.original_filename || ''
})

// 判断文件类型
const fileExt = computed(() => {
  if (!filename.value) return ''
  return filename.value.split('.').pop()?.toLowerCase() || ''
})

const isImage = computed(() => {
  return ['jpg', 'jpeg', 'png', 'gif'].includes(fileExt.value)
})

const isPdf = computed(() => {
  return fileExt.value === 'pdf'
})

// 处理图片加载错误
const handleImageError = () => {
  imageError.value = true
  loading.value = false
}

// 关闭对话框
const handleClose = () => {
  visible.value = false
  imageError.value = false
}

// 下载文件
const handleDownload = () => {
  if (props.file) {
    emit('download', props.file)
  }
  handleClose()
}

// 监听文件变化，重置状态
watch(() => props.file, () => {
  imageError.value = false
  loading.value = true
  setTimeout(() => {
    loading.value = false
  }, 500)
})
</script>

<style scoped>
.file-preview-dialog :deep(.el-dialog) {
  border-radius: 8px;
}

.file-preview-dialog :deep(.el-dialog__header) {
  padding: 20px 20px 16px;
  border-bottom: 1px solid #e4e7ed;
}

.file-preview-dialog :deep(.el-dialog__title) {
  color: #2c3e50;
  font-weight: 600;
  font-size: 16px;
}

.file-preview-dialog :deep(.el-dialog__body) {
  padding: 20px;
  max-height: 70vh;
  overflow: auto;
  background: #fafbfc;
}

.file-preview-dialog :deep(.el-dialog__footer) {
  padding: 16px 20px;
  border-top: 1px solid #e4e7ed;
}

.file-preview-dialog :deep(.el-dialog__footer .el-button) {
  padding: 8px 20px;
  font-size: 14px;
  font-weight: 500;
  border-radius: 6px;
}

.file-preview-dialog :deep(.el-dialog__footer .el-button--primary) {
  background: #2c3e50;
  border-color: #2c3e50;
}

.file-preview-dialog :deep(.el-dialog__footer .el-button--primary:hover) {
  background: #34495e;
  border-color: #34495e;
}

.preview-content {
  min-height: 400px;
  display: flex;
  justify-content: center;
  align-items: center;
}

.preview-image {
  max-width: 100%;
  max-height: 70vh;
  object-fit: contain;
  border-radius: 6px;
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.1);
  border: 1px solid #e4e7ed;
}

.preview-pdf {
  width: 100%;
  height: 70vh;
  border: 1px solid #e4e7ed;
  border-radius: 6px;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.04);
}

.preview-unsupported {
  text-align: center;
  padding: 60px 20px;
}

.unsupported-icon {
  font-size: 64px;
  color: #95a5a6;
  margin-bottom: 24px;
}

.preview-unsupported p {
  color: #34495e;
  font-size: 16px;
  font-weight: 500;
  margin-bottom: 24px;
}

.preview-unsupported :deep(.el-button) {
  padding: 10px 24px;
  font-size: 14px;
  font-weight: 500;
  border-radius: 6px;
  background: #2c3e50;
  border-color: #2c3e50;
}

.preview-unsupported :deep(.el-button:hover) {
  background: #34495e;
  border-color: #34495e;
  transform: translateY(-1px);
  box-shadow: 0 4px 12px rgba(44, 62, 80, 0.2);
}
</style>
