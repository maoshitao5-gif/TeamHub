<template>
  <div>
    <h2 class="page-title">团队管理</h2>

    <el-card shadow="never" style="border-radius:10px">
      <el-table :data="teams" v-loading="loading" stripe size="small">
        <el-table-column prop="name" label="团队名" min-width="160" show-overflow-tooltip />
        <el-table-column prop="slug" label="Slug" width="140" />
        <el-table-column prop="member_count" label="成员数" width="80" align="center" />
        <el-table-column prop="workspace_count" label="工作空间" width="90" align="center" />
        <el-table-column label="存储配额" width="170">
          <template #default="{ row }">
            <span v-if="!row._editing" @dblclick="startEdit(row)" class="quota-text" title="双击编辑">
              {{ fmtSize(row.storage_quota) }}
            </span>
            <el-input
              v-else
              v-model="row._editVal"
              size="small"
              style="width:100px"
              @blur="saveQuota(row)"
              @keyup.enter="saveQuota(row)"
              @keyup.esc="row._editing = false"
              ref="quotaInput"
            >
              <template #suffix>GB</template>
            </el-input>
          </template>
        </el-table-column>
        <el-table-column label="已用存储" width="120">
          <template #default="{ row }">{{ fmtSize(row.storage_used) }}</template>
        </el-table-column>
        <el-table-column label="创建时间" width="170">
          <template #default="{ row }">{{ fmtTime(row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="100" fixed="right">
          <template #default="{ row }">
            <el-button type="danger" size="small" text @click="handleDisband(row)">解散</el-button>
          </template>
        </el-table-column>
      </el-table>

      <!-- 分页 -->
      <div class="pagination-wrap">
        <el-pagination
          v-model:current-page="page"
          v-model:page-size="pageSize"
          :total="total"
          layout="total, prev, pager, next"
          @current-change="load"
        />
      </div>
    </el-card>
  </div>
</template>

<script setup>
import { ref, nextTick, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { getTeams, updateTeam, deleteTeam } from '../api/admin'

const teams = ref([])
const loading = ref(false)
const page = ref(1)
const pageSize = ref(20)
const total = ref(0)
const quotaInput = ref(null)

function fmtTime(iso) {
  if (!iso) return '—'
  return new Date(iso).toLocaleString('zh-CN')
}

function fmtSize(bytes) {
  if (!bytes && bytes !== 0) return '—'
  const gb = bytes / (1024 ** 3)
  return `${gb.toFixed(1)} GB`
}

async function load() {
  loading.value = true
  try {
    const res = await getTeams({ page: page.value, page_size: pageSize.value })
    teams.value = res.data.items.map(t => ({ ...t, _editing: false, _editVal: '' }))
    total.value = res.data.total
  } catch {
    ElMessage.error('加载团队列表失败')
  } finally {
    loading.value = false
  }
}

function startEdit(row) {
  row._editVal = String(Math.round(row.storage_quota / (1024 ** 3)))
  row._editing = true
  nextTick(() => quotaInput.value?.focus())
}

async function saveQuota(row) {
  const gb = parseFloat(row._editVal)
  if (isNaN(gb) || gb <= 0) {
    ElMessage.warning('请输入有效的 GB 数值')
    row._editing = false
    return
  }
  const bytes = Math.round(gb * 1024 ** 3)
  try {
    await updateTeam(row.id, { storage_quota: bytes })
    row.storage_quota = bytes
    ElMessage.success('存储配额已更新')
  } catch {
    ElMessage.error('保存失败')
  } finally {
    row._editing = false
  }
}

async function handleDisband(row) {
  await ElMessageBox.confirm(
    `确认解散团队「${row.name}」？团队内所有工作空间和数据将一并删除，此操作不可恢复。`,
    '解散确认',
    { type: 'warning', confirmButtonText: '解散', confirmButtonClass: 'el-button--danger' }
  )
  try {
    await deleteTeam(row.id)
    ElMessage.success('团队已解散')
    load()
  } catch (err) {
    ElMessage.error(err.response?.data?.detail || '解散失败')
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
.quota-text {
  cursor: pointer;
  border-bottom: 1px dashed #c0c4cc;
}
.pagination-wrap {
  display: flex;
  justify-content: flex-end;
  margin-top: 16px;
}
</style>
