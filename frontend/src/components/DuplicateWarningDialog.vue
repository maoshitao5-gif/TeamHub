<template>
  <el-dialog
    v-model="visible"
    title="发现重复文件"
    width="480px"
    :close-on-click-modal="false"
    class="dup-dialog"
    @closed="onClosed"
  >
    <div class="dup-body">
      <el-icon class="dup-icon" :size="40" color="#e6a23c"><WarningFilled /></el-icon>
      <div class="dup-text">
        <p class="dup-hint">文件内容与文档库中已有文档完全相同：</p>
        <div class="dup-doc-card">
          <el-icon style="color:#4096ff; flex-shrink:0;"><Document /></el-icon>
          <div class="dup-doc-info">
            <span class="dup-doc-name">{{ existingDoc.name }}</span>
            <span v-if="existingDoc.storage_path" class="dup-doc-path">
              {{ existingDoc.storage_path }}
            </span>
          </div>
          <el-tag
            :type="existingDoc.status === 'pending' ? 'warning' : 'success'"
            size="small"
            style="flex-shrink:0;"
          >{{ existingDoc.status === 'pending' ? '待整理' : '已整理' }}</el-tag>
        </div>
      </div>
    </div>

    <template #footer>
      <div class="dup-footer">
        <el-button @click="onCancel">取消</el-button>
        <el-button type="primary" plain @click="onNavigate">
          <el-icon><View /></el-icon>
          跳转查看
        </el-button>
        <el-button type="warning" @click="onConfirm">仍然继续</el-button>
      </div>
    </template>
  </el-dialog>
</template>

<script setup>
import { ref, watch } from 'vue'
import { WarningFilled, Document, View } from '@element-plus/icons-vue'

const props = defineProps({
  modelValue: { type: Boolean, default: false },
  // 已存在的重复文档对象 { id, name, storage_path, status }
  existingDoc: { type: Object, default: () => ({}) },
})

const emit = defineEmits(['update:modelValue', 'confirm', 'cancel', 'navigate'])

const visible = ref(props.modelValue)
watch(() => props.modelValue, v => { visible.value = v })
watch(visible, v => emit('update:modelValue', v))

const onConfirm = () => {
  visible.value = false
  emit('confirm')
}

const onCancel = () => {
  visible.value = false
  emit('cancel')
}

const onNavigate = () => {
  visible.value = false
  emit('navigate', props.existingDoc)
}

const onClosed = () => {
  // 关闭时若未触发 confirm/cancel，视为取消
}
</script>

<style scoped>
.dup-body {
  display: flex;
  align-items: flex-start;
  gap: 16px;
  padding: 4px 0 8px;
}

.dup-icon {
  flex-shrink: 0;
  margin-top: 2px;
}

.dup-text {
  flex: 1;
  min-width: 0;
}

.dup-hint {
  margin: 0 0 12px;
  font-size: 14px;
  color: #606266;
}

.dup-doc-card {
  display: flex;
  align-items: center;
  gap: 10px;
  background: #f5f7fa;
  border: 1px solid #ebeef5;
  border-radius: 8px;
  padding: 10px 14px;
}

.dup-doc-info {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 3px;
}

.dup-doc-name {
  font-size: 14px;
  font-weight: 500;
  color: #303133;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.dup-doc-path {
  font-size: 12px;
  color: #909399;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.dup-footer {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
}

:deep(.dup-dialog) {
  .el-dialog__header {
    padding: 18px 24px 14px;
    border-bottom: 1px solid #f0f0f0;
  }
  .el-dialog__title {
    font-size: 17px;
    font-weight: 600;
    color: #e6a23c;
  }
  .el-dialog__body {
    padding: 20px 24px 8px;
  }
  .el-dialog__footer {
    padding: 12px 24px 18px;
    border-top: 1px solid #f0f0f0;
  }
}
</style>
