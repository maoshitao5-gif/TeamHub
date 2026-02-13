<template>
  <div class="batch-operation-bar" v-if="selectedFiles.length > 0">
    <el-card class="batch-card" shadow="hover">
      <div class="batch-content">
        <div class="batch-info">
          <el-icon><Select /></el-icon>
          <span>已选择 <strong>{{ selectedFiles.length }}</strong> 个文件</span>
        </div>
        <div class="batch-actions">
          <el-button
            type="primary"
            @click="handleBatchUpdateTags"
            :icon="PriceTag"
          >
            批量修改标签
          </el-button>
          <el-button
            type="success"
            @click="handleBatchDownload"
            :icon="Download"
            :loading="downloading"
          >
            批量下载
          </el-button>
          <el-button
            type="danger"
            @click="handleBatchDelete"
            :icon="Delete"
          >
            批量删除
          </el-button>
          <el-button @click="handleClearSelection" :icon="Close">取消选择</el-button>
        </div>
      </div>
    </el-card>

    <!-- 批量修改标签对话框 -->
    <el-dialog
      v-model="tagDialogVisible"
      title="批量修改标签"
      width="500px"
    >
      <div class="tag-input-section">
        <div class="tag-input-label">
          <el-icon><PriceTag /></el-icon>
          <span>添加标签到所选文件</span>
        </div>
        <div class="tag-input-wrapper">
          <el-tag
            v-for="tag in selectedTags"
            :key="tag"
            closable
            @close="removeTag(tag)"
            type="primary"
            effect="plain"
            class="tag-item"
          >
            {{ tag }}
          </el-tag>
          <el-autocomplete
            v-model="tagInput"
            :fetch-suggestions="queryTags"
            placeholder="输入标签名称"
            class="tag-input"
            @select="handleTagSelect"
            @keyup.enter="addTag"
            clearable
            :trigger-on-focus="true"
          >
            <template #default="{ item }">
              <div class="tag-suggestion">
                <span class="tag-name">{{ item.name }}</span>
                <span class="tag-count" v-if="item.file_count > 0">
                  ({{ item.file_count }}个文件)
                </span>
              </div>
            </template>
          </el-autocomplete>
        </div>
      </div>
      <template #footer>
        <el-button @click="tagDialogVisible = false">取消</el-button>
        <el-button type="primary" @click="confirmBatchUpdateTags" :loading="updating">
          确定
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Select, PriceTag, Download, Delete, Close } from '@element-plus/icons-vue'
import { batchUpdateTags, batchDownload, getTags } from '@/api/file'
import { batchDeleteFiles } from '@/api/admin'

const props = defineProps({
  selectedFiles: {
    type: Array,
    default: () => []
  }
})

const emit = defineEmits(['clear-selection', 'refresh'])

const tagDialogVisible = ref(false)
const tagInput = ref('')
const selectedTags = ref([])
const allTags = ref([])
const downloading = ref(false)
const updating = ref(false)

// 加载所有标签
const loadAllTags = async () => {
  try {
    const result = await getTags()
    allTags.value = result.tags || []
  } catch (error) {
    console.error('加载标签失败:', error)
  }
}

// 标签自动完成查询
const queryTags = (queryString, cb) => {
  const results = queryString
    ? allTags.value.filter(tag => 
        tag.name.toLowerCase().includes(queryString.toLowerCase()) &&
        !selectedTags.value.includes(tag.name)
      )
    : allTags.value.filter(tag => !selectedTags.value.includes(tag.name))
  
  cb(results.slice(0, 10))
}

// 处理标签选择
const handleTagSelect = (item) => {
  if (item && item.name) {
    const tagName = item.name.trim()
    if (tagName && !selectedTags.value.includes(tagName)) {
      selectedTags.value.push(tagName)
      tagInput.value = ''
    }
  }
}

// 添加标签
const addTag = () => {
  const tag = tagInput.value.trim()
  if (tag && !selectedTags.value.includes(tag)) {
    selectedTags.value.push(tag)
    tagInput.value = ''
  }
}

// 删除标签
const removeTag = (tag) => {
  const index = selectedTags.value.indexOf(tag)
  if (index > -1) {
    selectedTags.value.splice(index, 1)
  }
}

// 批量修改标签
const handleBatchUpdateTags = () => {
  if (props.selectedFiles.length === 0) {
    ElMessage.warning('请先选择文件')
    return
  }
  selectedTags.value = []
  tagInput.value = ''
  loadAllTags()
  tagDialogVisible.value = true
}

// 确认批量更新标签
const confirmBatchUpdateTags = async () => {
  if (selectedTags.value.length === 0) {
    ElMessage.warning('请至少添加一个标签')
    return
  }
  
  updating.value = true
  try {
    const fileIds = props.selectedFiles.map(f => f.id)
    await batchUpdateTags(fileIds, selectedTags.value)
    ElMessage.success('批量更新标签成功')
    tagDialogVisible.value = false
    emit('refresh')
  } catch (error) {
    ElMessage.error('批量更新标签失败')
  } finally {
    updating.value = false
  }
}

// 批量下载
const handleBatchDownload = async () => {
  if (props.selectedFiles.length === 0) {
    ElMessage.warning('请先选择文件')
    return
  }
  
  downloading.value = true
  try {
    const fileIds = props.selectedFiles.map(f => f.id)
    await batchDownload(fileIds)
    ElMessage.success('批量下载开始')
  } catch (error) {
    ElMessage.error('批量下载失败')
  } finally {
    downloading.value = false
  }
}

// 批量删除
const handleBatchDelete = async () => {
  if (props.selectedFiles.length === 0) {
    ElMessage.warning('请先选择文件')
    return
  }
  
  try {
    await ElMessageBox.confirm(
      `确定要删除选中的 ${props.selectedFiles.length} 个文件吗？此操作不可恢复！`,
      '确认批量删除',
      {
        confirmButtonText: '确定删除',
        cancelButtonText: '取消',
        type: 'warning',
        dangerouslyUseHTMLString: false
      }
    )
    
    // 批量删除文件
    const fileIds = props.selectedFiles.map(f => f.id)
    await batchDeleteFiles(fileIds)
    
    ElMessage.success(`成功删除 ${props.selectedFiles.length} 个文件`)
    emit('refresh')
    emit('clear-selection')
  } catch (error) {
    if (error !== 'cancel') {
      ElMessage.error('批量删除失败')
    }
  }
}

// 清除选择
const handleClearSelection = () => {
  emit('clear-selection')
}
</script>

<style scoped>
.batch-operation-bar {
  margin-bottom: 20px;
}

.batch-card {
  border-radius: 8px;
  background: #ffffff;
  border: 1px solid #e4e7ed;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.04);
}

.batch-card :deep(.el-card__body) {
  padding: 16px 20px;
}

.batch-content {
  display: flex;
  justify-content: space-between;
  align-items: center;
  flex-wrap: wrap;
  gap: 16px;
}

.batch-info {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 14px;
  color: #2c3e50;
  font-weight: 500;
}

.batch-info .el-icon {
  color: #1e88e5;
  font-size: 18px;
}

.batch-info strong {
  color: #1e88e5;
  font-weight: 600;
}

.batch-actions {
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
}

.batch-actions :deep(.el-button) {
  padding: 8px 16px;
  font-size: 13px;
  font-weight: 500;
  border-radius: 6px;
  transition: all 0.2s ease;
}

.batch-actions :deep(.el-button--primary) {
  background: #1e88e5;
  border-color: #1e88e5;
}

.batch-actions :deep(.el-button--primary:hover) {
  background: #1565c0;
  border-color: #1565c0;
  transform: translateY(-1px);
  box-shadow: 0 4px 12px rgba(30, 136, 229, 0.2);
}

.batch-actions :deep(.el-button--success) {
  background: #2e7d32;
  border-color: #2e7d32;
}

.batch-actions :deep(.el-button--success:hover) {
  background: #1b5e20;
  border-color: #1b5e20;
  transform: translateY(-1px);
  box-shadow: 0 4px 12px rgba(46, 125, 50, 0.2);
}

.batch-actions :deep(.el-button--danger) {
  background: #c62828;
  border-color: #c62828;
}

.batch-actions :deep(.el-button--danger:hover) {
  background: #b71c1c;
  border-color: #b71c1c;
  transform: translateY(-1px);
  box-shadow: 0 4px 12px rgba(198, 40, 40, 0.2);
}

.batch-actions :deep(.el-button:not(.el-button--primary):not(.el-button--success):not(.el-button--danger)) {
  background: #f8f9fa;
  border-color: #e4e7ed;
  color: #34495e;
}

.batch-actions :deep(.el-button:not(.el-button--primary):not(.el-button--success):not(.el-button--danger):hover) {
  background: #e9ecef;
  border-color: #d0d7de;
  color: #2c3e50;
}

.tag-input-section {
  padding: 20px;
  background: #f8f9fa;
  border: 1px solid #e4e7ed;
  border-radius: 6px;
}

.tag-input-label {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 14px;
  font-size: 14px;
  font-weight: 500;
  color: #34495e;
}

.tag-input-label .el-icon {
  color: #2c3e50;
}

.tag-input-wrapper {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  align-items: center;
}

.tag-item {
  margin: 0;
  font-size: 13px;
  padding: 6px 12px;
  border-radius: 4px;
}

.tag-input {
  width: 200px;
}

.tag-input :deep(.el-input__wrapper) {
  box-shadow: 0 0 0 1px #e4e7ed inset;
  border-radius: 6px;
}

.tag-input :deep(.el-input__wrapper:hover) {
  box-shadow: 0 0 0 1px #c0c4cc inset;
}

.tag-input :deep(.el-input.is-focus .el-input__wrapper) {
  box-shadow: 0 0 0 1px #1e88e5 inset;
}

.tag-suggestion {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.tag-name {
  font-weight: 500;
  color: #2c3e50;
}

.tag-count {
  color: #7f8c8d;
  font-size: 12px;
}
</style>
