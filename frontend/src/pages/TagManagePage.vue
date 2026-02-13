<template>
  <div class="tag-manage-page">
    <!-- 统计概览卡片 -->
    <el-card class="stats-card" shadow="hover">
      <template #header>
        <div class="card-header">
          <el-icon><PriceTag /></el-icon>
          <span>标签统计</span>
        </div>
      </template>

      <!-- 统计概览 -->
      <div class="stats-overview">
        <el-row :gutter="24">
          <el-col :span="8">
            <div class="stat-item">
              <div class="stat-icon stat-icon-primary">
                <el-icon><PriceTag /></el-icon>
              </div>
              <div class="stat-content">
                <div class="stat-value">{{ totalTags }}</div>
                <div class="stat-label">标签总数</div>
              </div>
            </div>
          </el-col>
          <el-col :span="8">
            <div class="stat-item">
              <div class="stat-icon stat-icon-success">
                <el-icon><Document /></el-icon>
              </div>
              <div class="stat-content">
                <div class="stat-value">{{ totalFiles }}</div>
                <div class="stat-label">关联文件总数</div>
              </div>
            </div>
          </el-col>
          <el-col :span="8">
            <div class="stat-item">
              <div class="stat-icon stat-icon-info">
                <el-icon><DataAnalysis /></el-icon>
              </div>
              <div class="stat-content">
                <div class="stat-value">{{ avgFilesPerTag.toFixed(1) }}</div>
                <div class="stat-label">平均每个标签关联文件数</div>
              </div>
            </div>
          </el-col>
        </el-row>
      </div>
    </el-card>

    <!-- 标签列表 -->
    <el-card class="list-card" shadow="hover" v-loading="loading">
      <template #header>
        <div class="card-header">
          <el-icon><PriceTag /></el-icon>
          <span>标签列表</span>
          <el-text type="info" style="margin-left: 12px;">
            共 {{ totalTags }} 个标签
          </el-text>
          <div style="margin-left: auto; display: flex; gap: 8px;">
            <el-button
              type="primary"
              @click="showCreateTagDialog = true"
              size="small"
            >
              <el-icon><Plus /></el-icon>
              创建标签
            </el-button>
            <el-button
              type="danger"
              :disabled="selectedTags.length === 0"
              @click="handleBatchDeleteTags"
              size="small"
            >
              <el-icon><Delete /></el-icon>
              批量删除
              <el-badge v-if="selectedTags.length > 0" :value="selectedTags.length" class="badge-inline" />
            </el-button>
            <el-button
              type="primary"
              link
              @click="loadTags"
              :loading="loading"
              size="small"
            >
              <el-icon><Refresh /></el-icon>
              刷新
            </el-button>
          </div>
        </div>
      </template>

      <el-table
        :data="tagStats"
        stripe
        style="width: 100%"
        :empty-text="loading ? '加载中...' : '暂无标签'"
        :row-class-name="tableRowClassName"
        @selection-change="handleTagSelectionChange"
      >
        <el-table-column type="selection" width="60" align="center" />
        <el-table-column prop="name" label="标签名称" min-width="300">
          <template #default="{ row }">
            <div class="tag-name-cell">
              <el-input
                v-if="row.editing"
                v-model="row.editName"
                size="small"
                @blur="handleSaveTag(row)"
                @keyup.enter="handleSaveTag(row)"
                @keyup.esc="row.editing = false"
                class="tag-edit-input"
                autofocus
              />
              <template v-else>
                <el-icon class="tag-icon"><PriceTag /></el-icon>
                <span class="tag-name">
                  {{ row.name }}
                </span>
              </template>
            </div>
          </template>
        </el-table-column>

        <el-table-column prop="file_count" label="关联文件数" width="160" align="center">
          <template #default="{ row }">
            <div class="file-count-cell">
              <el-icon class="file-count-icon"><Document /></el-icon>
              <span class="file-count-text" :class="getFileCountClass(row.file_count)">
                {{ row.file_count }}
              </span>
            </div>
          </template>
        </el-table-column>

        <el-table-column label="操作" width="280" fixed="right">
          <template #default="{ row }">
            <el-button
              type="primary"
              link
              @click="viewFilesByTag(row.name)"
              :icon="View"
            >
              查看文件
            </el-button>
            <el-button
              v-if="!row.editing"
              type="primary"
              link
              @click="handleEditTag(row)"
              :icon="Edit"
            >
              编辑
            </el-button>
            <el-button
              type="danger"
              link
              @click="handleDeleteTag(row.id)"
              :icon="Delete"
            >
              删除
            </el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <!-- 创建标签对话框 -->
    <el-dialog 
      v-model="showCreateTagDialog" 
      title="创建标签" 
      width="420px"
      :close-on-click-modal="false"
    >
      <el-form :model="createTagForm" label-width="80px" label-position="left">
        <el-form-item label="标签名称" required>
          <el-input 
            v-model="createTagForm.name" 
            placeholder="请输入标签名称"
            maxlength="50"
            show-word-limit
            clearable
            @keyup.enter="handleCreateTag"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <div class="dialog-footer">
          <el-button @click="showCreateTagDialog = false">取消</el-button>
          <el-button type="primary" @click="handleCreateTag">确定</el-button>
        </div>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { PriceTag, Document, Refresh, Plus, Delete, DataAnalysis, View, Edit } from '@element-plus/icons-vue'
import { getTagsStats, createTag, updateTag, deleteTag, batchDeleteTags } from '@/api/file'

const router = useRouter()

const tagStats = ref([])
const loading = ref(false)
const totalTags = ref(0)
const totalFiles = ref(0)
const selectedTags = ref([])
const showCreateTagDialog = ref(false)
const createTagForm = ref({ name: '' })

// 计算平均每个标签关联的文件数
const avgFilesPerTag = computed(() => {
  if (totalTags.value === 0) return 0
  return totalFiles.value / totalTags.value
})

// 根据文件数量返回标签类型（保留用于其他用途）
const getFileCountType = (count) => {
  if (count === 0) return 'info'
  if (count < 5) return 'warning'
  if (count < 20) return 'primary'
  return 'success'
}

// 根据文件数量返回CSS类名
const getFileCountClass = (count) => {
  if (count === 0) return 'file-count-zero'
  if (count < 5) return 'file-count-low'
  if (count < 20) return 'file-count-medium'
  return 'file-count-high'
}

// 表格行类名
const tableRowClassName = ({ row, rowIndex }) => {
  return 'tag-table-row'
}

// 加载标签统计信息
const loadTags = async () => {
  loading.value = true
  try {
    const result = await getTagsStats()
    tagStats.value = result.tags || []
    totalTags.value = result.total || 0
    totalFiles.value = result.total_files || 0
  } catch (error) {
    console.error('加载标签统计失败:', error)
    ElMessage.error('加载标签统计失败')
  } finally {
    loading.value = false
  }
}

// 查看该标签关联的文件
const viewFilesByTag = (tagName) => {
  router.push({
    path: '/files',
    query: { tag: tagName }
  })
}

// 处理标签选择变化
const handleTagSelectionChange = (selection) => {
  selectedTags.value = selection.map(tag => tag.id)
}

// 编辑标签
const handleEditTag = (row) => {
  row.editing = true
  row.editName = row.name
}

// 保存标签
const handleSaveTag = async (row) => {
  if (!row.editName || row.editName.trim() === '') {
    ElMessage.warning('标签名称不能为空')
    row.editing = false
    return
  }
  
  try {
    await updateTag(row.id, row.editName.trim())
    ElMessage.success('标签更新成功')
    row.name = row.editName.trim()
    row.editing = false
    loadTags()
  } catch (error) {
    console.error('更新标签失败:', error)
    row.editing = false
  }
}

// 删除标签
const handleDeleteTag = async (tagId) => {
  try {
    await ElMessageBox.confirm(
      '确定要删除该标签吗？删除后该标签将从所有文件中移除。',
      '确认删除',
      {
        confirmButtonText: '确定',
        cancelButtonText: '取消',
        type: 'warning',
      }
    )
    
    await deleteTag(tagId)
    ElMessage.success('标签删除成功')
    loadTags()
  } catch (error) {
    if (error !== 'cancel') {
      console.error('删除标签失败:', error)
    }
  }
}

// 批量删除标签
const handleBatchDeleteTags = async () => {
  if (selectedTags.value.length === 0) {
    ElMessage.warning('请选择要删除的标签')
    return
  }
  
  try {
    await ElMessageBox.confirm(
      `确定要删除选中的 ${selectedTags.value.length} 个标签吗？删除后这些标签将从所有文件中移除。`,
      '确认批量删除',
      {
        confirmButtonText: '确定',
        cancelButtonText: '取消',
        type: 'warning',
      }
    )
    
    await batchDeleteTags(selectedTags.value)
    ElMessage.success('批量删除成功')
    selectedTags.value = []
    loadTags()
  } catch (error) {
    if (error !== 'cancel') {
      console.error('批量删除标签失败:', error)
    }
  }
}

// 创建标签
const handleCreateTag = async () => {
  if (!createTagForm.value.name || createTagForm.value.name.trim() === '') {
    ElMessage.warning('请输入标签名称')
    return
  }
  
  try {
    await createTag(createTagForm.value.name.trim())
    ElMessage.success('标签创建成功')
    showCreateTagDialog.value = false
    createTagForm.value.name = ''
    loadTags()
  } catch (error) {
    console.error('创建标签失败:', error)
  }
}

// 组件挂载时加载数据
onMounted(() => {
  loadTags()
})
</script>

<style scoped>
.tag-manage-page {
  width: 100%;
}

/* 卡片样式 - 与文件列表保持一致 */
.stats-card {
  margin-bottom: 24px;
  border-radius: 8px;
  border: 1px solid #e4e7ed;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.04);
  transition: all 0.3s ease;
}

.stats-card:hover {
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.08);
}

.stats-card :deep(.el-card__header) {
  background: #ffffff;
  border-bottom: 1px solid #e4e7ed;
  padding: 16px 20px;
}

.stats-card :deep(.el-card__body) {
  padding: 24px;
}

.list-card {
  margin-bottom: 24px;
  border-radius: 8px;
  border: 1px solid #e4e7ed;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.04);
}

.list-card :deep(.el-card__header) {
  background: #ffffff;
  border-bottom: 1px solid #e4e7ed;
  padding: 16px 20px;
}

.list-card :deep(.el-card__body) {
  padding: 20px;
}

.card-header {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 16px;
  font-weight: 600;
  color: #2c3e50;
}

.card-header .el-icon {
  color: #2c3e50;
  font-size: 18px;
}

.badge-inline {
  margin-left: 4px;
}

/* 统计卡片样式 */
.stats-overview {
  padding: 4px 0;
}

.stat-item {
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 20px;
  background: #fafbfc;
  border-radius: 8px;
  border: 1px solid #e4e7ed;
  transition: all 0.2s ease;
}

.stat-item:hover {
  background: #f5f7fa;
  border-color: #d0d7de;
  transform: translateY(-2px);
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.06);
}

.stat-icon {
  width: 48px;
  height: 48px;
  border-radius: 8px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 24px;
  flex-shrink: 0;
}

.stat-icon-primary {
  background: linear-gradient(135deg, #1e88e5 0%, #1565c0 100%);
  color: #ffffff;
}

.stat-icon-success {
  background: linear-gradient(135deg, #2e7d32 0%, #1b5e20 100%);
  color: #ffffff;
}

.stat-icon-info {
  background: linear-gradient(135deg, #0288d1 0%, #01579b 100%);
  color: #ffffff;
}

.stat-content {
  flex: 1;
}

.stat-value {
  font-size: 28px;
  font-weight: 700;
  color: #2c3e50;
  line-height: 1.2;
  margin-bottom: 4px;
}

.stat-label {
  font-size: 14px;
  color: #7f8c8d;
  font-weight: 500;
}

/* 表格样式优化 - 与文件列表保持一致 */
:deep(.el-table) {
  border: 1px solid #e4e7ed;
  border-radius: 6px;
}

:deep(.el-table th) {
  background: #f8f9fa;
  color: #2c3e50;
  font-weight: 600;
  border-bottom: 2px solid #e4e7ed;
  padding: 12px 16px;
}

:deep(.el-table td) {
  border-bottom: 1px solid #f0f0f0;
  padding: 12px 16px;
}

:deep(.el-table--striped .el-table__body tr.el-table__row--striped td) {
  background: #fafbfc;
}

:deep(.el-table tr:hover > td) {
  background: #f5f7fa !important;
}

:deep(.tag-table-row) {
  transition: background-color 0.2s ease;
}

:deep(.tag-table-row:hover) {
  background-color: #f5f7fa;
}

/* 标签名称单元格 - 参照文件名样式 */
.tag-name-cell {
  display: flex;
  align-items: center;
  gap: 10px;
}

.tag-icon {
  color: #7f8c8d;
  font-size: 18px;
}

.tag-name {
  font-weight: 500;
  color: #2c3e50;
  font-size: 14px;
  transition: color 0.2s ease;
}

.tag-edit-input {
  width: 200px;
}

.tag-edit-input :deep(.el-input__wrapper) {
  box-shadow: 0 0 0 1px #1e88e5 inset;
}

/* 关联文件数显示样式 */
.file-count-cell {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
}

.file-count-icon {
  color: #7f8c8d;
  font-size: 16px;
}

.file-count-text {
  font-weight: 600;
  font-size: 14px;
  transition: color 0.2s ease;
}

.file-count-zero {
  color: #95a5a6;
}

.file-count-low {
  color: #f57c00;
}

.file-count-medium {
  color: #1e88e5;
}

.file-count-high {
  color: #2e7d32;
}


/* 操作按钮样式 - 与文件列表保持一致 */
:deep(.el-table .el-button--link) {
  padding: 4px 8px;
  font-size: 13px;
  font-weight: 500;
  margin-right: 8px;
}

:deep(.el-table .el-button--link:last-child) {
  margin-right: 0;
}

:deep(.el-table .el-button--link.is-link) {
  color: #1e88e5;
}

:deep(.el-table .el-button--link.is-link:hover) {
  color: #1565c0;
}

:deep(.el-table .el-button--link.el-button--danger) {
  color: #c62828;
}

:deep(.el-table .el-button--link.el-button--danger:hover) {
  color: #b71c1c;
}

/* 对话框样式 */
.dialog-footer {
  display: flex;
  justify-content: flex-end;
  gap: 12px;
}

:deep(.el-dialog__header) {
  padding: 20px 20px 16px;
  border-bottom: 1px solid #e4e7ed;
}

:deep(.el-dialog__body) {
  padding: 24px 20px;
}

:deep(.el-dialog__footer) {
  padding: 16px 20px 20px;
  border-top: 1px solid #e4e7ed;
}

/* 响应式设计 */
@media (max-width: 768px) {
  .stats-overview .el-col {
    margin-bottom: 16px;
  }
}
</style>
