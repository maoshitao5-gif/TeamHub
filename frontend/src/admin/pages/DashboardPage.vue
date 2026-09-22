<template>
  <div>
    <h2 class="page-title">系统概况</h2>

    <!-- 统计卡片 -->
    <el-row :gutter="16" class="stats-row">
      <el-col :span="6" v-for="card in statCards" :key="card.label">
        <el-card class="stat-card" shadow="hover">
          <div class="stat-value">{{ stats[card.key] ?? '—' }}</div>
          <div class="stat-label">{{ card.label }}</div>
        </el-card>
      </el-col>
    </el-row>

    <!-- 最近注册用户 -->
    <el-card class="recent-card" shadow="never">
      <template #header>
        <span class="section-title">最近注册用户</span>
      </template>
      <el-table :data="stats.recent_users || []" stripe size="small">
        <el-table-column prop="email" label="邮箱" min-width="200" />
        <el-table-column prop="display_name" label="昵称" width="140" />
        <el-table-column label="状态" width="90">
          <template #default="{ row }">
            <el-tag :type="row.is_active ? 'success' : 'danger'" size="small">
              {{ row.is_active ? '正常' : '已禁用' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="超管" width="80">
          <template #default="{ row }">
            <el-tag v-if="row.is_superuser" type="warning" size="small">超管</el-tag>
            <span v-else style="color:#c0c4cc">—</span>
          </template>
        </el-table-column>
        <el-table-column label="注册时间" width="180">
          <template #default="{ row }">{{ fmtTime(row.created_at) }}</template>
        </el-table-column>
      </el-table>
    </el-card>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { getStats } from '../api/admin'

const stats = ref({})

const statCards = [
  { key: 'user_count', label: '总用户数' },
  { key: 'team_count', label: '总团队数' },
  { key: 'workspace_count', label: '工作空间数' },
  { key: 'device_count', label: '设备数' },
]

function fmtTime(iso) {
  if (!iso) return '—'
  return new Date(iso).toLocaleString('zh-CN')
}

async function load() {
  try {
    const res = await getStats()
    stats.value = res.data
  } catch {
    ElMessage.error('获取统计数据失败')
  }
}

onMounted(load)
</script>

<style scoped>
.page-title {
  font-size: 20px;
  font-weight: 600;
  color: #303133;
  margin-bottom: 20px;
}
.stats-row {
  margin-bottom: 20px;
}
.stat-card {
  text-align: center;
  border-radius: 10px;
}
.stat-value {
  font-size: 36px;
  font-weight: 700;
  color: #4096ff;
  line-height: 1.2;
}
.stat-label {
  font-size: 13px;
  color: #909399;
  margin-top: 6px;
}
.recent-card {
  border-radius: 10px;
}
.section-title {
  font-size: 15px;
  font-weight: 600;
  color: #303133;
}
</style>
