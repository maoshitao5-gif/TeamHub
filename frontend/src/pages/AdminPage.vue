<template>
  <div class="admin-page">
    <el-card class="admin-card">
      <template #header>
        <div class="card-header">
          <el-icon class="header-icon"><Setting /></el-icon>
          <span>超级权限管理后台</span>
        </div>
      </template>

      <el-tabs v-model="activeTab" @tab-change="handleTabChange">
        <!-- 文件管理 -->
        <el-tab-pane label="文件管理" name="files">
          <div class="tab-content">
            <div class="toolbar">
              <el-button type="primary" @click="handleSyncStorage" :loading="syncLoading">
                <el-icon><Refresh /></el-icon>
                核对存储
              </el-button>
              <el-button type="danger" :disabled="selectedFiles.length === 0" @click="handleBatchDeleteFiles">
                <el-icon><Delete /></el-icon>
                批量删除 ({{ selectedFiles.length }})
              </el-button>
              <el-pagination
                v-model:current-page="filePage"
                v-model:page-size="filePageSize"
                :total="fileTotal"
                :page-sizes="[20, 50, 100]"
                layout="total, sizes, prev, pager, next"
                @size-change="loadFiles"
                @current-change="loadFiles"
                style="margin-left: auto;"
              />
            </div>
            <el-table
              :data="fileList"
              v-loading="fileLoading"
              @selection-change="handleFileSelectionChange"
              style="margin-top: 16px;"
            >
              <el-table-column type="selection" width="55" />
              <el-table-column prop="id" label="ID" width="80" />
              <el-table-column prop="original_filename" label="文件名" min-width="200" show-overflow-tooltip />
              <el-table-column prop="file_size" label="大小" width="120">
                <template #default="{ row }">
                  {{ formatFileSize(row.file_size) }}
                </template>
              </el-table-column>
              <el-table-column prop="tags" label="标签" width="200">
                <template #default="{ row }">
                  <el-tag
                    v-for="tag in row.tags"
                    :key="tag"
                    size="small"
                    style="margin-right: 4px;"
                  >
                    {{ tag }}
                  </el-tag>
                </template>
              </el-table-column>
              <el-table-column prop="upload_time" label="上传时间" width="180" />
              <el-table-column label="操作" width="120" fixed="right">
                <template #default="{ row }">
                  <el-button
                    type="danger"
                    size="small"
                    @click="handleDeleteFile(row.id)"
                  >
                    删除
                  </el-button>
                </template>
              </el-table-column>
            </el-table>
          </div>
        </el-tab-pane>

        <!-- 标签管理 -->
        <el-tab-pane label="标签管理" name="tags">
          <div class="tab-content">
            <div class="toolbar">
              <el-button type="primary" @click="showCreateTagDialog = true">
                <el-icon><Plus /></el-icon>
                创建标签
              </el-button>
              <el-button type="danger" :disabled="selectedTags.length === 0" @click="handleBatchDeleteTags">
                <el-icon><Delete /></el-icon>
                批量删除 ({{ selectedTags.length }})
              </el-button>
            </div>
            <el-table
              :data="tagList"
              v-loading="tagLoading"
              @selection-change="handleTagSelectionChange"
              style="margin-top: 16px;"
            >
              <el-table-column type="selection" width="55" />
              <el-table-column prop="id" label="ID" width="80" />
              <el-table-column prop="name" label="标签名称" min-width="150">
                <template #default="{ row }">
                  <el-input
                    v-if="row.editing"
                    v-model="row.editName"
                    @blur="handleSaveTag(row)"
                    @keyup.enter="handleSaveTag(row)"
                    size="small"
                  />
                  <span v-else>{{ row.name }}</span>
                </template>
              </el-table-column>
              <el-table-column prop="file_count" label="关联文件数" width="120" />
              <el-table-column label="操作" width="200" fixed="right">
                <template #default="{ row }">
                  <el-button
                    v-if="!row.editing"
                    type="primary"
                    size="small"
                    @click="handleEditTag(row)"
                  >
                    编辑
                  </el-button>
                  <el-button
                    type="danger"
                    size="small"
                    @click="handleDeleteTag(row.id)"
                  >
                    删除
                  </el-button>
                </template>
              </el-table-column>
            </el-table>
          </div>
        </el-tab-pane>

        <!-- 用户管理 -->
        <el-tab-pane label="用户管理" name="users">
          <div class="tab-content">
            <div class="toolbar">
              <el-button type="primary" @click="showCreateUserDialog = true">
                <el-icon><Plus /></el-icon>
                创建用户
              </el-button>
              <el-button type="success" @click="showBatchCreateUserDialog = true">
                <el-icon><Plus /></el-icon>
                批量创建
              </el-button>
            </div>
            <el-table
              :data="userList"
              v-loading="userLoading"
              style="margin-top: 16px;"
            >
              <el-table-column prop="id" label="ID" width="80" />
              <el-table-column prop="username" label="用户名" min-width="150" />
              <el-table-column prop="is_admin" label="管理员" width="100">
                <template #default="{ row }">
                  <el-tag v-if="row.is_admin" type="danger" size="small">是</el-tag>
                  <el-tag v-else type="info" size="small">否</el-tag>
                </template>
              </el-table-column>
              <el-table-column prop="created_at" label="创建时间" width="180" />
              <el-table-column prop="last_login" label="最后登录" width="180" />
              <el-table-column label="操作" width="320" fixed="right">
                <template #default="{ row }">
                  <el-button
                    type="primary"
                    size="small"
                    @click="handleEditUser(row)"
                  >
                    编辑
                  </el-button>
                  <el-button
                    type="warning"
                    size="small"
                    @click="handleResetPassword(row)"
                  >
                    重置密码
                  </el-button>
                  <el-button
                    type="danger"
                    size="small"
                    :disabled="row.is_admin && userList.filter(u => u.is_admin).length === 1"
                    @click="handleDeleteUser(row.id)"
                  >
                    删除
                  </el-button>
                </template>
              </el-table-column>
            </el-table>
          </div>
        </el-tab-pane>
      </el-tabs>
    </el-card>

    <!-- 创建标签对话框 -->
    <el-dialog v-model="showCreateTagDialog" title="创建标签" width="400px">
      <el-form :model="createTagForm" label-width="80px">
        <el-form-item label="标签名称" required>
          <el-input v-model="createTagForm.name" placeholder="请输入标签名称" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showCreateTagDialog = false">取消</el-button>
        <el-button type="primary" @click="handleCreateTag">确定</el-button>
      </template>
    </el-dialog>

    <!-- 创建用户对话框 -->
    <el-dialog v-model="showCreateUserDialog" title="创建用户" width="400px">
      <el-form :model="createUserForm" label-width="80px">
        <el-form-item label="用户名" required>
          <el-input v-model="createUserForm.username" placeholder="请输入用户名" />
        </el-form-item>
        <el-form-item label="密码" required>
          <el-input
            v-model="createUserForm.password"
            type="password"
            placeholder="请输入密码（至少6位）"
            show-password
          />
        </el-form-item>
        <el-form-item label="管理员">
          <el-switch v-model="createUserForm.is_admin" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showCreateUserDialog = false">取消</el-button>
        <el-button type="primary" @click="handleCreateUser">确定</el-button>
      </template>
    </el-dialog>

    <!-- 编辑用户对话框 -->
    <el-dialog v-model="showEditUserDialog" title="编辑用户" width="400px">
      <el-form :model="editUserForm" label-width="80px">
        <el-form-item label="用户名">
          <el-input v-model="editUserForm.username" disabled />
        </el-form-item>
        <el-form-item label="新密码">
          <el-input
            v-model="editUserForm.password"
            type="password"
            placeholder="留空则不修改密码"
            show-password
          />
        </el-form-item>
        <el-form-item label="管理员">
          <el-switch v-model="editUserForm.is_admin" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showEditUserDialog = false">取消</el-button>
        <el-button type="primary" @click="handleUpdateUser">确定</el-button>
      </template>
    </el-dialog>

    <!-- 重置密码对话框 -->
    <el-dialog v-model="showResetPasswordDialog" title="重置密码" width="400px">
      <el-form :model="resetPasswordForm" label-width="80px">
        <el-form-item label="用户名">
          <el-input v-model="resetPasswordForm.username" disabled />
        </el-form-item>
        <el-form-item label="新密码" required>
          <el-input
            v-model="resetPasswordForm.password"
            type="password"
            placeholder="请输入新密码（至少6位）"
            show-password
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showResetPasswordDialog = false">取消</el-button>
        <el-button type="primary" @click="handleResetPasswordConfirm">确定</el-button>
      </template>
    </el-dialog>

    <!-- 批量创建用户对话框 -->
    <el-dialog v-model="showBatchCreateUserDialog" title="批量创建用户" width="600px">
      <el-alert
        type="info"
        :closable="false"
        style="margin-bottom: 16px;"
      >
        <template #default>
          <div>格式：每行一个用户，格式为：用户名,密码,是否管理员(0/1)</div>
          <div style="margin-top: 8px;">示例：</div>
          <div>user1,password123,0</div>
          <div>user2,password456,1</div>
        </template>
      </el-alert>
      <el-form label-width="80px">
        <el-form-item label="用户列表">
          <el-input
            v-model="batchCreateUsersText"
            type="textarea"
            :rows="10"
            placeholder="请输入用户列表，每行一个用户"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showBatchCreateUserDialog = false">取消</el-button>
        <el-button type="primary" @click="handleBatchCreateUsers">确定</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Setting, Delete, Plus, Refresh } from '@element-plus/icons-vue'
import {
  getAdminFiles,
  batchDeleteFiles,
  syncStorage,
  getAdminTags,
  createTag,
  updateTag,
  deleteTag,
  batchDeleteTags,
  getAdminUsers,
  createUser,
  updateUser,
  deleteUser,
  resetUserPassword,
  batchCreateUsers
} from '../api/admin'

// 当前激活的标签页
const activeTab = ref('files')

// ==================== 文件管理 ====================
const fileList = ref([])
const fileLoading = ref(false)
const filePage = ref(1)
const filePageSize = ref(50)
const fileTotal = ref(0)
const selectedFiles = ref([])
const syncLoading = ref(false)

// ==================== 标签管理 ====================
const tagList = ref([])
const tagLoading = ref(false)
const selectedTags = ref([])

// ==================== 用户管理 ====================
const userList = ref([])
const userLoading = ref(false)

// ==================== 对话框 ====================
const showCreateTagDialog = ref(false)
const showCreateUserDialog = ref(false)
const showEditUserDialog = ref(false)
const showResetPasswordDialog = ref(false)
const showBatchCreateUserDialog = ref(false)

const createTagForm = ref({ name: '' })
const createUserForm = ref({
  username: '',
  password: '',
  is_admin: false
})
const editUserForm = ref({
  id: null,
  username: '',
  password: '',
  is_admin: false
})
const resetPasswordForm = ref({
  id: null,
  username: '',
  password: ''
})
const batchCreateUsersText = ref('')

// ==================== 工具函数 ====================
function formatFileSize(bytes) {
  if (bytes === 0) return '0 B'
  const k = 1024
  const sizes = ['B', 'KB', 'MB', 'GB', 'TB']
  const i = Math.floor(Math.log(bytes) / Math.log(k))
  return Math.round(bytes / Math.pow(k, i) * 100) / 100 + ' ' + sizes[i]
}

// ==================== 文件管理 ====================
async function loadFiles() {
  fileLoading.value = true
  try {
    const res = await getAdminFiles(filePage.value, filePageSize.value)
    fileList.value = res.files
    fileTotal.value = res.total
  } catch (error) {
    ElMessage.error('加载文件列表失败：' + (error.message || '未知错误'))
  } finally {
    fileLoading.value = false
  }
}

function handleFileSelectionChange(selection) {
  selectedFiles.value = selection.map(f => f.id)
}

async function handleDeleteFile(fileId) {
  try {
    await ElMessageBox.confirm('确定要删除这个文件吗？', '确认删除', {
      type: 'warning'
    })
    await batchDeleteFiles([fileId])
    ElMessage.success('删除成功')
    loadFiles()
  } catch (error) {
    if (error !== 'cancel') {
      ElMessage.error('删除失败：' + (error.message || '未知错误'))
    }
  }
}

async function handleBatchDeleteFiles() {
  if (selectedFiles.value.length === 0) return
  
  try {
    await ElMessageBox.confirm(
      `确定要删除选中的 ${selectedFiles.value.length} 个文件吗？`,
      '确认批量删除',
      { type: 'warning' }
    )
    await batchDeleteFiles(selectedFiles.value)
    ElMessage.success('批量删除成功')
    selectedFiles.value = []
    loadFiles()
  } catch (error) {
    if (error !== 'cancel') {
      ElMessage.error('批量删除失败：' + (error.message || '未知错误'))
    }
  }
}

async function handleSyncStorage() {
  try {
    await ElMessageBox.confirm(
      '确定要核对存储吗？系统将扫描 storage 目录，同步数据库记录。\n\n' +
      '- 如果数据库有记录但文件不存在，将删除数据库记录\n' +
      '- 如果文件存在但数据库没有记录，将创建数据库记录',
      '确认核对存储',
      { type: 'info' }
    )
    
    syncLoading.value = true
    const res = await syncStorage()
    
    let message = `核对完成！\n`
    message += `- 删除了 ${res.deleted_count} 条孤立记录\n`
    message += `- 添加了 ${res.added_count} 条新记录`
    
    if (res.deleted_count > 0 || res.added_count > 0) {
      ElMessage.success(message)
      // 刷新文件列表
      loadFiles()
    } else {
      ElMessage.info('存储已同步，无需更新')
    }
  } catch (error) {
    if (error !== 'cancel') {
      ElMessage.error('核对存储失败：' + (error.message || '未知错误'))
    }
  } finally {
    syncLoading.value = false
  }
}

// ==================== 标签管理 ====================
async function loadTags() {
  tagLoading.value = true
  try {
    const res = await getAdminTags()
    tagList.value = res.tags.map(tag => ({
      ...tag,
      editing: false,
      editName: tag.name
    }))
  } catch (error) {
    ElMessage.error('加载标签列表失败：' + (error.message || '未知错误'))
  } finally {
    tagLoading.value = false
  }
}

function handleTagSelectionChange(selection) {
  selectedTags.value = selection.map(t => t.id)
}

function handleEditTag(row) {
  row.editing = true
  row.editName = row.name
}

async function handleSaveTag(row) {
  if (!row.editName || row.editName.trim() === '') {
    ElMessage.warning('标签名称不能为空')
    row.editing = false
    row.editName = row.name
    return
  }
  
  if (row.editName === row.name) {
    row.editing = false
    return
  }
  
  try {
    await updateTag(row.id, row.editName.trim())
    ElMessage.success('标签更新成功')
    row.name = row.editName.trim()
    row.editing = false
  } catch (error) {
    ElMessage.error('更新失败：' + (error.message || '未知错误'))
    row.editing = false
    row.editName = row.name
  }
}

async function handleDeleteTag(tagId) {
  try {
    await ElMessageBox.confirm('确定要删除这个标签吗？删除后会解除所有文件的关联。', '确认删除', {
      type: 'warning'
    })
    await deleteTag(tagId)
    ElMessage.success('删除成功')
    loadTags()
  } catch (error) {
    if (error !== 'cancel') {
      ElMessage.error('删除失败：' + (error.message || '未知错误'))
    }
  }
}

async function handleBatchDeleteTags() {
  if (selectedTags.value.length === 0) return
  
  try {
    await ElMessageBox.confirm(
      `确定要删除选中的 ${selectedTags.value.length} 个标签吗？`,
      '确认批量删除',
      { type: 'warning' }
    )
    await batchDeleteTags(selectedTags.value)
    ElMessage.success('批量删除成功')
    selectedTags.value = []
    loadTags()
  } catch (error) {
    if (error !== 'cancel') {
      ElMessage.error('批量删除失败：' + (error.message || '未知错误'))
    }
  }
}

async function handleCreateTag() {
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
    ElMessage.error('创建失败：' + (error.message || '未知错误'))
  }
}

// ==================== 用户管理 ====================
async function loadUsers() {
  userLoading.value = true
  try {
    const res = await getAdminUsers()
    userList.value = res.users
  } catch (error) {
    ElMessage.error('加载用户列表失败：' + (error.message || '未知错误'))
  } finally {
    userLoading.value = false
  }
}

async function handleCreateUser() {
  if (!createUserForm.value.username || createUserForm.value.username.trim() === '') {
    ElMessage.warning('请输入用户名')
    return
  }
  
  if (!createUserForm.value.password || createUserForm.value.password.length < 6) {
    ElMessage.warning('密码长度至少6位')
    return
  }
  
  try {
    await createUser(
      createUserForm.value.username.trim(),
      createUserForm.value.password,
      createUserForm.value.is_admin
    )
    ElMessage.success('用户创建成功')
    showCreateUserDialog.value = false
    createUserForm.value = {
      username: '',
      password: '',
      is_admin: false
    }
    loadUsers()
  } catch (error) {
    ElMessage.error('创建失败：' + (error.message || '未知错误'))
  }
}

function handleEditUser(row) {
  editUserForm.value = {
    id: row.id,
    username: row.username,
    password: '',
    is_admin: row.is_admin
  }
  showEditUserDialog.value = true
}

async function handleUpdateUser() {
  const updateData = {}
  
  if (editUserForm.value.password) {
    if (editUserForm.value.password.length < 6) {
      ElMessage.warning('密码长度至少6位')
      return
    }
    updateData.password = editUserForm.value.password
  }
  
  updateData.is_admin = editUserForm.value.is_admin
  
  try {
    await updateUser(editUserForm.value.id, updateData)
    ElMessage.success('用户更新成功')
    showEditUserDialog.value = false
    loadUsers()
  } catch (error) {
    ElMessage.error('更新失败：' + (error.message || '未知错误'))
  }
}

async function handleDeleteUser(userId) {
  try {
    await ElMessageBox.confirm('确定要删除这个用户吗？', '确认删除', {
      type: 'warning'
    })
    await deleteUser(userId)
    ElMessage.success('删除成功')
    loadUsers()
  } catch (error) {
    if (error !== 'cancel') {
      ElMessage.error('删除失败：' + (error.message || '未知错误'))
    }
  }
}

function handleResetPassword(row) {
  resetPasswordForm.value = {
    id: row.id,
    username: row.username,
    password: ''
  }
  showResetPasswordDialog.value = true
}

async function handleResetPasswordConfirm() {
  if (!resetPasswordForm.value.password || resetPasswordForm.value.password.length < 6) {
    ElMessage.warning('密码长度至少6位')
    return
  }
  
  try {
    await resetUserPassword(resetPasswordForm.value.id, resetPasswordForm.value.password)
    ElMessage.success('密码重置成功')
    showResetPasswordDialog.value = false
    resetPasswordForm.value = {
      id: null,
      username: '',
      password: ''
    }
  } catch (error) {
    ElMessage.error('重置失败：' + (error.message || '未知错误'))
  }
}

async function handleBatchCreateUsers() {
  if (!batchCreateUsersText.value || !batchCreateUsersText.value.trim()) {
    ElMessage.warning('请输入用户列表')
    return
  }
  
  try {
    // 解析用户列表
    const lines = batchCreateUsersText.value.trim().split('\n')
    const users = []
    
    for (let i = 0; i < lines.length; i++) {
      const line = lines[i].trim()
      if (!line) continue
      
      const parts = line.split(',')
      if (parts.length < 2) {
        ElMessage.warning(`第 ${i + 1} 行格式错误：${line}`)
        return
      }
      
      const username = parts[0].trim()
      const password = parts[1].trim()
      const isAdmin = parts.length >= 3 && parts[2].trim() === '1'
      
      if (!username || !password) {
        ElMessage.warning(`第 ${i + 1} 行用户名或密码不能为空`)
        return
      }
      
      if (password.length < 6) {
        ElMessage.warning(`第 ${i + 1} 行密码长度至少6位`)
        return
      }
      
      users.push({
        username,
        password,
        is_admin: isAdmin
      })
    }
    
    if (users.length === 0) {
      ElMessage.warning('没有有效的用户数据')
      return
    }
    
    const result = await batchCreateUsers(users)
    ElMessage.success(result.message || `成功创建 ${result.created_count} 个用户`)
    
    if (result.failed_count > 0 && result.failed_users) {
      const failedInfo = result.failed_users.map(u => `${u.username}: ${u.error}`).join('\n')
      ElMessageBox.alert(failedInfo, '部分用户创建失败', {
        type: 'warning'
      })
    }
    
    showBatchCreateUserDialog.value = false
    batchCreateUsersText.value = ''
    loadUsers()
  } catch (error) {
    ElMessage.error('批量创建失败：' + (error.message || '未知错误'))
  }
}

// ==================== 标签页切换 ====================
function handleTabChange(tabName) {
  if (tabName === 'files') {
    loadFiles()
  } else if (tabName === 'tags') {
    loadTags()
  } else if (tabName === 'users') {
    loadUsers()
  }
}

// ==================== 初始化 ====================
onMounted(() => {
  loadFiles()
})
</script>

<style scoped>
.admin-page {
  padding: 0;
}

.admin-card {
  background: #ffffff;
  border-radius: 8px;
  border: 1px solid #e4e7ed;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.04);
}

.admin-card :deep(.el-card__header) {
  background: #ffffff;
  border-bottom: 1px solid #e4e7ed;
  padding: 16px 20px;
}

.admin-card :deep(.el-card__body) {
  padding: 20px;
}

.card-header {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 18px;
  font-weight: 600;
  color: #2c3e50;
}

.header-icon {
  font-size: 20px;
  color: #2c3e50;
}

.tab-content {
  padding: 0;
}

.toolbar {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 20px;
  flex-wrap: wrap;
}

.toolbar :deep(.el-button) {
  padding: 8px 16px;
  font-size: 13px;
  font-weight: 500;
  border-radius: 6px;
  transition: all 0.2s ease;
}

.toolbar :deep(.el-button--primary) {
  background: #1e88e5;
  border-color: #1e88e5;
}

.toolbar :deep(.el-button--primary:hover) {
  background: #1565c0;
  border-color: #1565c0;
  transform: translateY(-1px);
  box-shadow: 0 4px 12px rgba(30, 136, 229, 0.2);
}

.toolbar :deep(.el-button--danger) {
  background: #c62828;
  border-color: #c62828;
}

.toolbar :deep(.el-button--danger:hover) {
  background: #b71c1c;
  border-color: #b71c1c;
  transform: translateY(-1px);
  box-shadow: 0 4px 12px rgba(198, 40, 40, 0.2);
}

.toolbar :deep(.el-button:disabled) {
  opacity: 0.5;
  cursor: not-allowed;
}

.toolbar :deep(.el-button:disabled:hover) {
  transform: none;
  box-shadow: none;
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

:deep(.el-table .el-button) {
  padding: 4px 8px;
  font-size: 13px;
  font-weight: 500;
}

:deep(.el-table .el-button--danger) {
  color: #c62828;
}

:deep(.el-table .el-button--danger:hover) {
  color: #b71c1c;
}

/* 标签页样式优化 */
:deep(.el-tabs__header) {
  margin-bottom: 20px;
  border-bottom: 1px solid #e4e7ed;
}

:deep(.el-tabs__item) {
  color: #7f8c8d;
  font-weight: 500;
  padding: 0 24px;
  transition: color 0.2s ease;
}

:deep(.el-tabs__item:hover) {
  color: #2c3e50;
}

:deep(.el-tabs__item.is-active) {
  color: #1e88e5;
  font-weight: 600;
}

:deep(.el-tabs__active-bar) {
  background-color: #1e88e5;
}

/* 分页样式优化 */
:deep(.el-pagination) {
  margin-top: 20px;
}

:deep(.el-pagination .el-pager li) {
  color: #34495e;
  font-weight: 500;
}

:deep(.el-pagination .el-pager li.is-active) {
  background: #1e88e5;
  color: #ffffff;
}

:deep(.el-pagination .btn-prev),
:deep(.el-pagination .btn-next) {
  color: #34495e;
}

:deep(.el-pagination .btn-prev:hover),
:deep(.el-pagination .btn-next:hover) {
  color: #1e88e5;
}
</style>
