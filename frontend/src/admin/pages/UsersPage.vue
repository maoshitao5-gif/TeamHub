<template>
  <div>
    <h2 class="page-title">用户管理</h2>

    <!-- 搜索栏 -->
    <el-card class="search-card" shadow="never">
      <el-input
        v-model="keyword"
        placeholder="搜索邮箱 / 昵称"
        clearable
        style="width:280px"
        @input="handleSearch"
      >
        <template #prefix><el-icon><Search /></el-icon></template>
      </el-input>
    </el-card>

    <!-- 用户表格 -->
    <el-card shadow="never" style="border-radius:10px">
      <el-table :data="users" v-loading="loading" stripe size="small">
        <el-table-column prop="email" label="邮箱" min-width="200" show-overflow-tooltip />
        <el-table-column prop="display_name" label="昵称" width="140" />
        <el-table-column label="注册时间" width="170">
          <template #default="{ row }">{{ fmtTime(row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <el-switch
              v-model="row.is_active"
              active-text="正常"
              inactive-text="禁用"
              :loading="row._saving"
              @change="(val) => toggleActive(row, val)"
            />
          </template>
        </el-table-column>
        <el-table-column label="超管" width="80">
          <template #default="{ row }">
            <el-checkbox
              v-model="row.is_superuser"
              :loading="row._saving"
              @change="(val) => toggleSuperuser(row, val)"
            />
          </template>
        </el-table-column>
        <el-table-column label="操作" width="100" fixed="right">
          <template #default="{ row }">
            <el-button type="danger" size="small" text @click="handleDelete(row)">删除</el-button>
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
import { ref, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Search } from '@element-plus/icons-vue'
import { getUsers, updateUser, deleteUser } from '../api/admin'

const users = ref([])
const loading = ref(false)
const keyword = ref('')
const page = ref(1)
const pageSize = ref(20)
const total = ref(0)

let searchTimer = null
function handleSearch() {
  clearTimeout(searchTimer)
  searchTimer = setTimeout(() => { page.value = 1; load() }, 400)
}

function fmtTime(iso) {
  if (!iso) return '—'
  return new Date(iso).toLocaleString('zh-CN')
}

async function load() {
  loading.value = true
  try {
    const res = await getUsers({ page: page.value, page_size: pageSize.value, keyword: keyword.value || undefined })
    users.value = res.data.items
    total.value = res.data.total
  } catch {
    ElMessage.error('加载用户列表失败')
  } finally {
    loading.value = false
  }
}

async function toggleActive(row, val) {
  row._saving = true
  try {
    await updateUser(row.id, { is_active: val })
    ElMessage.success(val ? '已启用' : '已禁用')
  } catch {
    row.is_active = !val
    ElMessage.error('操作失败')
  } finally {
    row._saving = false
  }
}

async function toggleSuperuser(row, val) {
  row._saving = true
  try {
    await updateUser(row.id, { is_superuser: val })
    ElMessage.success(val ? '已设为超管' : '已取消超管')
  } catch {
    row.is_superuser = !val
    ElMessage.error('操作失败')
  } finally {
    row._saving = false
  }
}

async function handleDelete(row) {
  await ElMessageBox.confirm(`确认删除用户 ${row.email}？此操作不可恢复。`, '删除确认', {
    type: 'warning',
    confirmButtonText: '删除',
    confirmButtonClass: 'el-button--danger',
  })
  try {
    await deleteUser(row.id)
    ElMessage.success('已删除')
    load()
  } catch (err) {
    ElMessage.error(err.response?.data?.detail || '删除失败')
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
.search-card {
  border-radius: 10px;
  margin-bottom: 16px;
}
.pagination-wrap {
  display: flex;
  justify-content: flex-end;
  margin-top: 16px;
}
</style>
