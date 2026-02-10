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
import { batchUpdateTags, batchDownload, deleteFile, getTags } from '@/api/file'

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
    
    // 逐个删除文件
    const deletePromises = props.selectedFiles.map(file => deleteFile(file.id))
    await Promise.all(deletePromises)
    
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
  margin-bottom: 16px;
}

.batch-card {
  border-radius: 8px;
  background: linear-gradient(135deg, #ecf5ff 0%, #d9ecff 100%);
  border: 1px solid #b3d8ff;
}

.batch-content {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 8px 0;
}

.batch-info {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 14px;
  color: #409EFF;
  font-weight: 500;
}

.batch-actions {
  display: flex;
  gap: 8px;
}

.tag-input-section {
  padding: 16px;
  background: #f5f7fa;
  border-radius: 6px;
}

.tag-input-label {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 12px;
  font-size: 14px;
  font-weight: 500;
  color: #606266;
}

.tag-input-wrapper {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
}

.tag-item {
  margin: 0;
}

.tag-input {
  width: 200px;
}

.tag-suggestion {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.tag-name {
  font-weight: 500;
}

.tag-count {
  color: #909399;
  font-size: 12px;
}
</style>
