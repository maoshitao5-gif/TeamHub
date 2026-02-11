<template>
  <div class="tag-manage-page">
    <el-card class="stats-card" shadow="hover">
      <template #header>
        <div class="card-header">
          <el-icon><PriceTag /></el-icon>
          <span>标签管理</span>
        </div>
      </template>

      <!-- 统计概览 -->
      <div class="stats-overview">
        <el-row :gutter="20">
          <el-col :span="8">
            <el-statistic title="标签总数" :value="totalTags">
              <template #suffix>
                <el-icon style="vertical-align: -0.125em"><PriceTag /></el-icon>
              </template>
            </el-statistic>
          </el-col>
          <el-col :span="8">
            <el-statistic title="关联文件总数" :value="totalFiles">
              <template #suffix>
                <el-icon style="vertical-align: -0.125em"><Document /></el-icon>
              </template>
            </el-statistic>
          </el-col>
          <el-col :span="8">
            <el-statistic title="平均每个标签" :value="avgFilesPerTag" :precision="1">
              <template #suffix>
                <span style="font-size: 14px">个文件</span>
              </template>
            </el-statistic>
          </el-col>
        </el-row>
      </div>
    </el-card>

    <!-- 标签列表 -->
    <el-card class="tags-list-card" shadow="hover" v-loading="loading">
      <template #header>
        <div class="card-header">
          <el-icon><List /></el-icon>
          <span>标签详情</span>
          <el-button 
            type="primary" 
            link 
            @click="loadTags" 
            :loading="loading"
            style="margin-left: auto;"
          >
            <el-icon><Refresh /></el-icon>
            刷新
          </el-button>
        </div>
      </template>

      <el-table
        :data="tagStats"
        stripe
        style="width: 100%"
        :empty-text="loading ? '加载中...' : '暂无标签'"
      >
        <el-table-column prop="name" label="标签名称" min-width="150">
          <template #default="{ row }">
            <el-tag type="primary" effect="plain" size="large">
              {{ row.name }}
            </el-tag>
          </template>
        </el-table-column>

        <el-table-column prop="file_count" label="关联文件数" width="120" align="center">
          <template #default="{ row }">
            <el-tag :type="getFileCountType(row.file_count)" effect="plain">
              {{ row.file_count }}
            </el-tag>
          </template>
        </el-table-column>

        <el-table-column label="关联文件" min-width="300">
          <template #default="{ row }">
            <div v-if="row.files && row.files.length > 0" class="file-list">
              <el-tag
                v-for="file in row.files.slice(0, 5)"
                :key="file.id"
                size="small"
                class="file-tag"
              >
                {{ file.original_filename }}
              </el-tag>
              <el-text v-if="row.files.length > 5" type="info" size="small">
                等 {{ row.files.length }} 个文件
              </el-text>
            </div>
            <el-text v-else type="info" size="small">暂无关联文件</el-text>
          </template>
        </el-table-column>

        <el-table-column label="操作" width="120" fixed="right">
          <template #default="{ row }">
            <el-button
              type="primary"
              link
              @click="viewFilesByTag(row.name)"
            >
              查看文件
            </el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { PriceTag, Document, List, Refresh } from '@element-plus/icons-vue'
import { getTagsStats } from '@/api/file'

const router = useRouter()

const tagStats = ref([])
const loading = ref(false)
const totalTags = ref(0)
const totalFiles = ref(0)

// 计算平均每个标签关联的文件数
const avgFilesPerTag = computed(() => {
  if (totalTags.value === 0) return 0
  return totalFiles.value / totalTags.value
})

// 根据文件数量返回标签类型
const getFileCountType = (count) => {
  if (count === 0) return 'info'
  if (count < 5) return 'warning'
  if (count < 20) return 'primary'
  return 'success'
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

// 组件挂载时加载数据
onMounted(() => {
  loadTags()
})
</script>

<style scoped>
.tag-manage-page {
  width: 100%;
}

.stats-card {
  margin-bottom: 24px;
  border-radius: 8px;
  border: 1px solid #e4e7ed;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.04);
}

.stats-card :deep(.el-card__header) {
  background: #ffffff;
  border-bottom: 1px solid #e4e7ed;
  padding: 16px 20px;
}

.stats-card :deep(.el-card__body) {
  padding: 20px;
}

.tags-list-card {
  border-radius: 8px;
  border: 1px solid #e4e7ed;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.04);
}

.tags-list-card :deep(.el-card__header) {
  background: #ffffff;
  border-bottom: 1px solid #e4e7ed;
  padding: 16px 20px;
}

.tags-list-card :deep(.el-card__body) {
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

.stats-overview {
  padding: 20px 0;
}

.stats-overview :deep(.el-statistic__head) {
  color: #7f8c8d;
  font-size: 14px;
  font-weight: 500;
  margin-bottom: 8px;
}

.stats-overview :deep(.el-statistic__number) {
  color: #2c3e50;
  font-weight: 600;
}

.stats-overview :deep(.el-statistic__suffix) {
  color: #7f8c8d;
  margin-left: 4px;
}

/* 表格样式优化 */
:deep(.el-table) {
  border: 1px solid #e4e7ed;
  border-radius: 6px;
}

:deep(.el-table th) {
  background: #f8f9fa;
  color: #2c3e50;
  font-weight: 600;
  border-bottom: 2px solid #e4e7ed;
}

:deep(.el-table td) {
  border-bottom: 1px solid #f0f0f0;
}

:deep(.el-table--striped .el-table__body tr.el-table__row--striped td) {
  background: #fafbfc;
}

/* 按钮样式优化 */
:deep(.el-button) {
  padding: 8px 16px;
  font-size: 13px;
  font-weight: 500;
  border-radius: 6px;
  transition: all 0.2s ease;
}

:deep(.el-button--primary) {
  background: #1e88e5;
  border-color: #1e88e5;
}

:deep(.el-button--primary:hover) {
  background: #1565c0;
  border-color: #1565c0;
  transform: translateY(-1px);
  box-shadow: 0 4px 12px rgba(30, 136, 229, 0.2);
}

:deep(.el-button--danger) {
  background: #c62828;
  border-color: #c62828;
}

:deep(.el-button--danger:hover) {
  background: #b71c1c;
  border-color: #b71c1c;
  transform: translateY(-1px);
  box-shadow: 0 4px 12px rgba(198, 40, 40, 0.2);
}

.file-list {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  align-items: center;
}

.file-tag {
  margin: 0;
  max-width: 150px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
