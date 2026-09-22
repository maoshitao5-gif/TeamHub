<template>
  <div class="app-container"
  @dragover.prevent
  @dragenter="handleDragEnter"
  @dragleave="handleDragLeave"
  @drop.prevent="handleDrop"
>
    <!-- 初始化向导（首次设置或更换文件库） -->
    <template v-if="!appStore.libraryInitialized || forceSetup">
      <div class="setup-redirect">
        <SetupPage />
      </div>
    </template>

    <!-- 欢迎回来页面（重启后显示） -->
    <template v-else-if="showWelcome">
      <WelcomePage
        @enter="showWelcome = false"
        @change="onChangeLibrary"
      />
    </template>

    <!-- 正常布局 -->
    <template v-else>
      <!-- 顶部导航栏 -->
      <el-header class="app-header">
        <div class="header-content">
          <div class="logo-section" @click="$router.push('/library')" style="cursor: pointer;">
            <el-icon class="logo-icon"><FolderOpened /></el-icon>
            <h1 class="app-title">TeamHub</h1>
          </div>

          <!-- 主导航（文档库、待整理、标签管理 — 同等权重） -->
          <nav class="nav-primary">
            <div
              class="nav-item"
              :class="{ 'is-active': activeMenu === '/library' }"
              @click="handleMenuSelect('/library')"
            >
              <el-icon><Folder /></el-icon>
              <span>文档库</span>
            </div>
            <div
              class="nav-item"
              :class="{ 'is-active': activeMenu === '/pending' }"
              @click="handleMenuSelect('/pending')"
            >
              <el-icon><Download /></el-icon>
              <span>待整理</span>
              <el-badge v-if="appStore.pendingCount > 0" :value="appStore.pendingCount" class="nav-badge" />
            </div>
            <div
              class="nav-item"
              :class="{ 'is-active': activeMenu === '/tags' }"
              @click="handleMenuSelect('/tags')"
            >
              <el-icon><PriceTag /></el-icon>
              <span>标签管理</span>
            </div>
            <div
              class="nav-item"
              :class="{ 'is-active': activeMenu === '/shared' }"
              @click="handleMenuSelect('/shared')"
            >
              <el-icon><Share /></el-icon>
              <span>云仓库</span>
            </div>
          </nav>

          <!-- 次级导航（回收站、设置图标、用户头像） -->
          <div class="nav-secondary">
            <div
              class="nav-item"
              :class="{ 'is-active': activeMenu === '/trash' }"
              @click="handleMenuSelect('/trash')"
            >
              <el-icon><Delete /></el-icon>
              <span>回收站</span>
            </div>
            <div
              class="settings-btn"
              :class="{ 'is-active': activeMenu === '/settings' }"
              @click="$router.push('/settings')"
              title="设置"
            >
              <el-icon><Setting /></el-icon>
            </div>

            <!-- 云服务用户头像 / 登录入口 -->
            <template v-if="authStore.isLoggedIn">
              <el-dropdown trigger="click" placement="bottom-end">
                <div class="settings-btn user-avatar-btn is-logged-in" title="云服务账号">
                  <el-avatar :size="28" :style="{ background: '#4096ff', fontSize: '13px', cursor: 'pointer' }">
                    {{ authStore.user?.display_name?.[0]?.toUpperCase() }}
                  </el-avatar>
                </div>
                <template #dropdown>
                  <el-dropdown-menu>
                    <el-dropdown-item disabled style="font-size:12px;color:#999;">
                      {{ authStore.user?.display_name }} · {{ authStore.user?.email }}
                    </el-dropdown-item>
                    <el-dropdown-item divided @click="$router.push('/team')">
                      <el-icon><Avatar /></el-icon> 团队与工作空间
                    </el-dropdown-item>
                    <el-dropdown-item @click="$router.push('/settings')">
                      <el-icon><Setting /></el-icon> 设置
                    </el-dropdown-item>
                    <el-dropdown-item divided @click="authStore.logout()">
                      <el-icon><SwitchButton /></el-icon> 退出云服务
                    </el-dropdown-item>
                  </el-dropdown-menu>
                </template>
              </el-dropdown>
            </template>
            <el-tooltip v-else content="未登录云服务，点击登录" placement="bottom">
              <div class="settings-btn user-avatar-btn" @click="$router.push('/login')" title="云服务登录">
                <el-icon style="color: rgba(255,255,255,0.4)"><UserFilled /></el-icon>
              </div>
            </el-tooltip>
          </div>
        </div>
      </el-header>

      <!-- 主内容区 -->
      <el-main class="app-main">
        <div class="content-wrapper">
          <router-view />
        </div>
      </el-main>

      <!-- 全局拖拽覆盖层 -->
      <div v-if="isDragging" class="drag-overlay">
        <div class="drag-hint">
          <el-icon :size="48"><Upload /></el-icon>
          <p>松开以添加到待整理</p>
        </div>
      </div>

      <!-- 全局文件处理进度对话框 -->
      <el-dialog v-model="progressDialogVisible" title="正在处理文件"
        :close-on-click-modal="false" :close-on-press-escape="false"
        :show-close="false" width="500px">
        <div class="progress-content">
          <p class="current-file">当前文件：{{ progressInfo.currentFile || '准备中...' }}</p>
          <el-progress :percentage="progressInfo.percentage" :status="progressInfo.status"
            :stroke-width="8" striped striped-flow />
          <p class="progress-stats">
            已处理：{{ progressInfo.processed }} / {{ progressInfo.total }}
            （成功：{{ progressInfo.success }}，失败：{{ progressInfo.failed }}，跳过：{{ progressInfo.duplicates }}）
          </p>
        </div>
        <template #footer>
          <el-button @click="cancelProcessing" :disabled="!processingFiles">取消</el-button>
        </template>
      </el-dialog>

      <!-- P4: 文件夹查重 Loading 遮罩 -->
      <div v-if="folderChecking" class="folder-checking-overlay">
        <div class="folder-checking-hint">
          <el-icon :size="36" class="is-loading"><Loading /></el-icon>
          <p>正在校验文件，请稍候...</p>
        </div>
      </div>

      <!-- P0 / P2: 重复文件详情弹窗 -->
      <el-dialog
        v-model="duplicateDialogVisible"
        :title="duplicateDialogMode === 'folder' ? '文件夹内发现重复文件' : '打散文件夹 — 发现重复文件'"
        width="600px"
        :close-on-click-modal="false"
      >
        <div class="duplicate-dialog-content">
          <p class="duplicate-summary">
            以下 <strong>{{ duplicateDialogData.duplicates.length }}</strong> 个文件在文档库中已有相同内容：
          </p>
          <div v-if="duplicateDialogData.hasPendingHashes" class="pending-hash-warning">
            <el-icon><Warning /></el-icon>
            注意：部分近期整理文件的哈希尚未完成计算，查重结果仅供参考
          </div>
          <el-table :data="duplicateDialogData.duplicates" max-height="260" style="margin-top:12px">
            <el-table-column label="文件名" min-width="160">
              <template #default="{ row }">
                <span class="dup-filename" :title="row.source_filename">{{ row.source_filename }}</span>
              </template>
            </el-table-column>
            <el-table-column label="已有文档" min-width="160">
              <template #default="{ row }">
                <span :title="row.existing_document_name">{{ row.existing_document_name }}</span>
              </template>
            </el-table-column>
            <el-table-column label="操作" width="68" align="center">
              <template #default="{ row }">
                <el-button size="small" text type="primary"
                  @click="navigateToDoc({ id: row.existing_document_id, status: row.existing_document_status })">
                  查看
                </el-button>
              </template>
            </el-table-column>
          </el-table>
        </div>
        <template #footer>
          <!-- 不打散模式：整个文件夹作为一个对象，只需决定是否还要添加 -->
          <template v-if="duplicateDialogMode === 'folder'">
            <el-button @click="resolveDuplicateDialog('cancel')">取消</el-button>
            <el-button type="primary" @click="resolveDuplicateDialog('add')">仍然添加整个文件夹</el-button>
          </template>
          <!-- 打散模式：可以跳过重复文件或全部添加 -->
          <template v-else>
            <el-button @click="resolveDuplicateDialog('cancel')">取消全部</el-button>
            <el-button type="warning" @click="resolveDuplicateDialog('add_all')">全部添加（含重复）</el-button>
            <el-button type="primary" @click="resolveDuplicateDialog('skip')">跳过重复，添加其余</el-button>
          </template>
        </template>
      </el-dialog>
    </template>
  </div>
</template>

<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAppStore } from '@/stores/app'
import { useAuthStore } from '@/stores/auth'
import { FolderOpened, Folder, Download, PriceTag, Delete, Setting, Upload, Loading, Warning, Share, UserFilled, Avatar, SwitchButton } from '@element-plus/icons-vue'
import { h } from 'vue'
import { ElMessage, ElMessageBox, ElNotification, ElButton } from 'element-plus'
import { quickAddToPending, checkFileDuplicate, checkFolderDuplicates, undoPending } from '@/api/document'
import SetupPage from '@/pages/SetupPage.vue'
import WelcomePage from '@/pages/WelcomePage.vue'

const route = useRoute()
const router = useRouter()
const appStore = useAppStore()
const authStore = useAuthStore()

const showWelcome = ref(false)   // 是否显示欢迎回来页
const forceSetup = ref(false)    // 强制显示设置页（更换文件库时）

const activeMenu = computed(() => route.path)

const handleMenuSelect = (index) => {
  router.push(index)
}

const onChangeLibrary = () => {
  showWelcome.value = false
  forceSetup.value = true
}

// 当文件库路径变化时（SetupPage 完成初始化），退出强制设置模式
watch(() => appStore.libraryPath, (newPath) => {
  if (newPath && forceSetup.value) {
    forceSetup.value = false
  }
})

// 拖拽状态（全局）
const isDragging = ref(false)
let dragCounter = 0
const processingFiles = ref(false)
const progressDialogVisible = ref(false)
const progressInfo = ref({
  currentFile: '',
  total: 0,
  processed: 0,
  success: 0,
  failed: 0,
  duplicates: 0,
  percentage: 0,
  status: 'success'
})
let processingCancelled = false

// P4: 文件夹查重 Loading 状态
const folderChecking = ref(false)

// P0 / P2: 重复文件详情弹窗
const duplicateDialogVisible = ref(false)
const duplicateDialogMode = ref('folder')  // 'folder'（不打散）或 'scatter'（打散）
const duplicateDialogData = ref({ duplicates: [], hasPendingHashes: false })
let duplicateDialogResolve = null

const showDuplicateDialog = (mode, duplicates, hasPendingHashes = false) => {
  return new Promise((resolve) => {
    duplicateDialogMode.value = mode
    duplicateDialogData.value = { duplicates, hasPendingHashes }
    duplicateDialogVisible.value = true
    duplicateDialogResolve = resolve
  })
}

const resolveDuplicateDialog = (action) => {
  duplicateDialogVisible.value = false
  if (duplicateDialogResolve) {
    duplicateDialogResolve(action)
    duplicateDialogResolve = null
  }
}

const handleDragEnter = (e) => {
  if (!appStore.libraryInitialized) return
  dragCounter++
  isDragging.value = true
}

const handleDragLeave = (e) => {
  dragCounter--
  if (dragCounter <= 0) {
    dragCounter = 0
    isDragging.value = false
  }
}

const handleDrop = async (e) => {
  dragCounter = 0
  isDragging.value = false

  if (!appStore.libraryInitialized) return

  const files = e.dataTransfer?.files
  if (!files || files.length === 0) return

  // 检查是否在 Electron 环境
  if (!files[0].path) {
    ElMessage.warning('拖拽上传仅在桌面应用中可用')
    return
  }

  // 收集拖入的文件和文件夹信息
  const droppedItems = []
  for (const file of files) {
    if (window.electron?.checkIsDirectory) {
      const isDir = await window.electron.checkIsDirectory(file.path)
      const name = file.path.split(/[/\\]/).pop()
      droppedItems.push({ path: file.path, name: name, isDir: isDir })
    } else {
      const name = file.path.split(/[/\\]/).pop()
      droppedItems.push({ path: file.path, name: name, isDir: false })
    }
  }

  if (droppedItems.length === 0) return

  // 直接处理，无需确认（拖入后可通过通知栏撤销）
  for (const item of droppedItems) {
    if (item.isDir) {
      await handleFolderDrop(item.path)
    } else {
      await handleFileDrop(item.path)
    }
  }
}

// 执行撤销（支持批量）
const handleUndo = async (docIds) => {
  let succeeded = 0
  let failed = 0
  for (const id of docIds) {
    try {
      await undoPending(id)
      succeeded++
    } catch (e) {
      console.error('[撤销] 失败:', id, e)
      failed++
    }
  }
  if (succeeded > 0) {
    ElMessage.success(docIds.length === 1 ? '已撤销，文件已移回原始位置' : `已撤销 ${succeeded} 个文件`)
  }
  if (failed > 0) {
    ElMessage.error(`${failed} 个文件撤销失败（原始位置可能已有同名文件）`)
  }
  appStore.fetchPendingCount()
}

// 显示带撤销按钮的通知
const showUndoNotification = (name, docIds) => {
  let notif = null
  notif = ElNotification({
    title: '已添加到待整理',
    message: h('div', { style: 'display:flex; align-items:center; gap:8px; margin-top:4px;' }, [
      h('span', { style: 'flex:1; font-size:13px; color:#606266; word-break:break-all;' }, name),
      h(ElButton, {
        size: 'small',
        style: 'flex-shrink:0;',
        onClick: () => {
          if (notif) notif.close()
          router.push('/pending')
        }
      }, { default: () => '查看' }),
      h(ElButton, {
        size: 'small',
        style: 'flex-shrink:0;',
        onClick: async () => {
          if (notif) notif.close()
          await handleUndo(docIds)
        }
      }, { default: () => '撤销' }),
    ]),
    duration: 8000,
    type: 'success',
    position: 'bottom-right',
  })
}

// 导航到已存在文档（从查重跳转）
const navigateToDoc = (doc) => {
  const path = doc.status === 'organized' ? '/library' : '/pending'
  router.push({ path, query: { highlight: doc.id } })
}

// 处理单个文件
const handleFileDrop = async (filePath) => {
  // 查重检测（优先检查已整理文档库，再检查待整理区）
  try {
    const checkResult = await checkFileDuplicate(filePath)
    if (checkResult.check_succeeded && checkResult.is_duplicate) {
      if (checkResult.duplicate_in_pending && checkResult.pending_document) {
        // P1: 文件已在待整理区中
        const pendingDoc = checkResult.pending_document
        try {
          await ElMessageBox.confirm(
            `该文件已在待整理中（"${pendingDoc.name}"），是否仍然添加？`,
            '发现重复文件',
            {
              confirmButtonText: '仍然添加',
              cancelButtonText: '前往查看',
              type: 'warning',
              distinguishCancelAndClose: true,
            }
          )
          // 用户选择"仍然添加"，继续执行
        } catch (err) {
          if (err === 'cancel') navigateToDoc(pendingDoc)
          return  // cancel 或 close 均终止添加
        }
      } else if (checkResult.existing_document) {
        // 文件已在已整理文档库中
        const existingDoc = checkResult.existing_document
        try {
          await ElMessageBox.confirm(
            `文件"${existingDoc.name}"已存在于文档库中，是否仍然添加？`,
            '发现重复文件',
            {
              confirmButtonText: '仍然添加',
              cancelButtonText: '前往查看',
              type: 'warning',
              distinguishCancelAndClose: true,
            }
          )
          // 用户选择"仍然添加"，继续执行
        } catch (err) {
          if (err === 'cancel') navigateToDoc(existingDoc)
          return  // cancel 或 close 均终止添加
        }
      }
    }
  } catch (e) {
    console.warn('[查重] 请求失败，继续添加:', e)
  }

  // 正常添加
  try {
    const doc = await quickAddToPending({ source_path: filePath, is_folder: false })
    appStore.fetchPendingCount()
    showUndoNotification(doc.name, [doc.id])
  } catch (err) {
    console.error('快速添加失败:', filePath, err)
    ElMessage.error(`添加文件失败: ${err.message || '未知错误'}`)
  }
}

// 处理文件夹
const handleFolderDrop = async (folderPath) => {
  try {
    const folderName = folderPath.split(/[/\\]/).pop()

    let result
    try {
      result = await ElMessageBox.confirm(
        `检测到文件夹"${folderName}"，是否要打散文件夹内的文件进行整理？\n\n选择"打散"：将文件夹内的每个文件单独添加到待整理\n选择"不打散"：将整个文件夹作为一个整理对象`,
        '文件夹整理方式',
        {
          confirmButtonText: '打散',
          cancelButtonText: '不打散',
          type: 'info',
          distinguishCancelAndClose: true
        }
      )
      result = 'confirm'
    } catch (err) {
      if (err === 'cancel') {
        result = 'cancel'
      } else {
        result = 'close'
      }
    }

    if (result === 'confirm') {
      await processFolderRecursive(folderPath, folderName)
    } else if (result === 'cancel') {
      // 用户选择"不打散"，先对文件夹内文件做批量查重
      // P4: 显示 Loading 遮罩
      folderChecking.value = true
      let folderCheck = null
      try {
        folderCheck = await checkFolderDuplicates(folderPath)
      } catch (e) {
        console.warn('[文件夹查重] 查重失败，继续添加:', e)
      } finally {
        folderChecking.value = false
      }

      // P0: 如果有重复，展示包含具体文件清单的弹窗
      if (folderCheck && folderCheck.check_succeeded && folderCheck.duplicate_count > 0) {
        const action = await showDuplicateDialog('folder', folderCheck.duplicates, folderCheck.has_pending_hashes)
        if (action !== 'add') return  // 用户取消，终止操作
      }

      // 添加整个文件夹
      try {
        const doc = await quickAddToPending({
          source_path: folderPath,
          is_folder: true,
        })
        appStore.fetchPendingCount()
        showUndoNotification(doc.name, [doc.id])
      } catch (err) {
        throw err
      }
    }
    // 如果 result === 'close'，用户关闭了对话框，不执行任何操作
  } catch (err) {
    if (err !== 'cancel' && err !== 'close') {
      console.error('处理文件夹失败:', folderPath, err)
      ElMessage.error(`处理文件夹失败: ${err.message || '未知错误'}`)
    }
  }
}

// 递归处理文件夹
const processFolderRecursive = async (folderPath, folderName) => {
  if (!window.electron?.listDirectoryContents) {
    ElMessage.error('无法遍历文件夹，请确保在 Electron 环境中运行')
    return
  }

  const filesToProcess = []
  const collectFiles = async (dirPath, dirName) => {
    const contents = await window.electron.listDirectoryContents(dirPath)

    for (const file of contents.files) {
      filesToProcess.push({
        path: file.path,
        name: file.name,
        relativePath: file.path.replace(folderPath, '').replace(/^[/\\]/, '')
      })
    }

    for (const subFolder of contents.folders) {
      try {
        const result = await ElMessageBox.confirm(
          `检测到子文件夹"${subFolder.name}"，是否要打散该子文件夹？\n\n选择"打散"：将子文件夹内的每个文件单独添加到待整理\n选择"不打散"：将整个子文件夹作为一个整理对象`,
          '子文件夹整理方式',
          {
            confirmButtonText: '打散',
            cancelButtonText: '不打散',
            type: 'info',
            distinguishCancelAndClose: true
          }
        )

        if (result === 'confirm') {
          await collectFiles(subFolder.path, subFolder.name)
        } else {
          filesToProcess.push({
            path: subFolder.path,
            name: subFolder.name,
            relativePath: subFolder.path.replace(folderPath, '').replace(/^[/\\]/, ''),
            isFolder: true
          })
        }
      } catch (err) {
        if (err !== 'cancel') {
          console.error('处理子文件夹失败:', subFolder.path, err)
        }
      }
    }
  }

  await collectFiles(folderPath, folderName)

  if (filesToProcess.length === 0) {
    ElMessage.info('文件夹内没有可处理的文件')
    return
  }

  // P2: 预扫描阶段——批量查重，汇总结果后一次性展示（替代原来的逐文件弹框）
  const nonFolderFiles = filesToProcess.filter(f => !f.isFolder)
  let skipDuplicates = false
  const duplicatePathSet = new Set()

  if (nonFolderFiles.length > 0) {
    folderChecking.value = true
    const preScanDuplicates = []
    for (const item of nonFolderFiles) {
      try {
        const checkResult = await checkFileDuplicate(item.path)
        if (checkResult.check_succeeded && checkResult.is_duplicate) {
          const docRef = checkResult.existing_document || checkResult.pending_document
          if (docRef) {
            preScanDuplicates.push({
              source_filename: item.relativePath || item.name,
              existing_document_id: docRef.id,
              existing_document_name: docRef.name,
              existing_document_status: docRef.status,
            })
            duplicatePathSet.add(item.path)
          }
        }
      } catch (e) {
        // 单文件查重失败不影响整体
      }
    }
    folderChecking.value = false

    if (preScanDuplicates.length > 0) {
      const action = await showDuplicateDialog('scatter', preScanDuplicates, false)
      if (action === 'cancel') return
      if (action === 'skip') skipDuplicates = true
      // 'add_all'：不跳过，全部添加
    }
  }

  const createdDocIds = []

  processingFiles.value = true
  processingCancelled = false
  progressDialogVisible.value = true
  progressInfo.value = {
    currentFile: '',
    total: filesToProcess.length,
    processed: 0,
    success: 0,
    failed: 0,
    duplicates: 0,
    percentage: 0,
    status: 'success'
  }

  for (let i = 0; i < filesToProcess.length; i++) {
    if (processingCancelled) break

    const item = filesToProcess[i]
    progressInfo.value.currentFile = item.relativePath || item.name

    // P2: 根据预扫描结果决定是否跳过（不再逐一调用查重接口）
    if (!item.isFolder && skipDuplicates && duplicatePathSet.has(item.path)) {
      progressInfo.value.duplicates++
      progressInfo.value.processed = i + 1
      progressInfo.value.percentage = Math.round(((i + 1) / filesToProcess.length) * 100)
      continue
    }

    try {
      const doc = await quickAddToPending({
        source_path: item.path,
        is_folder: item.isFolder || false,
      })
      createdDocIds.push(doc.id)
      progressInfo.value.success++
    } catch (err) {
      console.error('添加文件失败:', item.path, err)
      progressInfo.value.failed++
    }

    progressInfo.value.processed = i + 1
    progressInfo.value.percentage = Math.round(((i + 1) / filesToProcess.length) * 100)
  }

  if (processingCancelled) {
    progressInfo.value.status = 'warning'
    if (createdDocIds.length > 0) {
      showUndoNotification(`已添加 ${createdDocIds.length} 个（处理被取消）`, createdDocIds)
    } else {
      ElMessage.warning('处理已取消')
    }
  } else {
    progressInfo.value.status = progressInfo.value.failed > 0 ? 'exception' : 'success'
    const parts = []
    if (progressInfo.value.success > 0) parts.push(`成功添加 ${progressInfo.value.success} 个`)
    if (progressInfo.value.duplicates > 0) parts.push(`跳过 ${progressInfo.value.duplicates} 个重复`)
    if (progressInfo.value.failed > 0) parts.push(`${progressInfo.value.failed} 个失败`)

    if (progressInfo.value.success === 0 && progressInfo.value.duplicates === 0) {
      ElMessage.error('所有文件添加失败')
    } else if (createdDocIds.length > 0) {
      showUndoNotification(parts.join('，'), createdDocIds)
    } else {
      ElMessage.success(parts.join('，'))
    }
  }

  setTimeout(() => {
    progressDialogVisible.value = false
    processingFiles.value = false
    appStore.fetchPendingCount()
  }, 1500)
}

// 取消处理
const cancelProcessing = () => {
  processingCancelled = true
  processingFiles.value = false
}

// 处理悬浮窗拖拽的文件（升级：复用完整处理逻辑，跳过初始确认）
if (window.electron && window.electron.on) {
  window.electron.on('floating-window-files-dropped', async (filePaths) => {
    if (!appStore.libraryInitialized) return
    for (const filePath of filePaths) {
      const isFolder = window.electron?.checkIsDirectory
        ? await window.electron.checkIsDirectory(filePath)
        : false
      if (isFolder) {
        await handleFolderDrop(filePath)
      } else {
        await handleFileDrop(filePath)
      }
    }
  })
}

onMounted(async () => {
  // 优先加载设置（确保 cloud_api_url 在任何云请求前就绪）
  await appStore.fetchSettings()

  // 设置加载完成后再恢复云服务登录态，保证使用正确的云端地址
  authStore.restoreSession().catch(() => {})

  await appStore.fetchLibraryInfo()
  if (appStore.libraryInitialized) {
    showWelcome.value = true
    appStore.fetchPendingCount()

    // 静默加载同步状态（供导航栏和 LibraryPage 使用）
    appStore.fetchSyncStatus().catch(() => {})
  }
  
  // 根据悬浮窗设置控制 Electron 悬浮窗（不依赖文件库初始化）
  if (window.electron?.toggleFloatingWindow) {
    const enabled = appStore.settings.enable_floating_window !== false // 默认true
    window.electron.toggleFloatingWindow(enabled)
  }
})
</script>

<style>
* {
  margin: 0;
  padding: 0;
  box-sizing: border-box;
}

body {
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial,
    'Noto Sans', sans-serif;
  background: #f5f7fa;
  min-height: 100vh;
  color: #2c3e50;
}

#app {
  min-height: 100vh;
}
</style>

<style scoped>
.app-container {
  min-height: 100vh;
  display: flex;
  flex-direction: column;
  background: #f5f7fa;
}

.app-header {
  background: linear-gradient(160deg, #1e2d3d 0%, #2c3e50 100%);
  color: #ffffff;
  padding: 0;
  box-shadow: 0 2px 12px rgba(0, 0, 0, 0.15);
  position: sticky;
  top: 0;
  z-index: 1000;
}

.header-content {
  max-width: 1400px;
  margin: 0 auto;
  padding: 0 32px;
  height: 64px;
  display: flex;
  align-items: center;
  gap: 0;
}

.logo-section {
  display: flex;
  align-items: center;
  gap: 12px;
  transition: opacity 0.2s;
  margin-right: 24px;
  flex-shrink: 0;
}

.logo-section:hover {
  opacity: 0.9;
}

.logo-icon {
  font-size: 28px;
  color: #ecf0f1;
}

.app-title {
  font-size: 20px;
  font-weight: 600;
  color: #ffffff;
  margin: 0;
}

.nav-primary {
  flex: 1;
  display: flex;
  flex-direction: row;
  flex-wrap: nowrap;
  align-items: stretch;
  min-width: 0;
  overflow: hidden;
}

.nav-item {
  display: flex;
  flex-shrink: 0;
  align-items: center;
  gap: 6px;
  height: 64px;
  padding: 0 20px;
  color: rgba(255, 255, 255, 0.85);
  cursor: pointer;
  border-bottom: 2px solid transparent;
  font-size: 14px;
  font-weight: 500;
  white-space: nowrap;
  transition: all 0.2s ease;
  user-select: none;
}

.nav-item:hover {
  background: rgba(255, 255, 255, 0.08);
  color: #ffffff;
}

.nav-item.is-active {
  color: #ffffff;
  border-bottom-color: #60a5fa;
  background: rgba(96, 165, 250, 0.15);
  border-radius: 4px 4px 0 0;
}

.nav-secondary {
  flex-shrink: 0;
  display: flex;
  align-items: center;
}

.settings-btn {
  width: 40px;
  height: 40px;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  border-radius: 8px;
  color: rgba(255, 255, 255, 0.7);
  transition: var(--transition-base, all 0.22s ease);
  margin-left: 4px;
  font-size: 18px;
}

.settings-btn:hover {
  background: rgba(255, 255, 255, 0.1);
  color: #ffffff;
}

.settings-btn.is-active {
  background: rgba(96, 165, 250, 0.2);
  color: #60a5fa;
}

.nav-badge {
  margin-left: 6px;
}

.nav-badge :deep(.el-badge__content) {
  font-size: 11px;
}

.app-main {
  flex: 1;
  padding: 32px;
  background: #f5f7fa;
}

.content-wrapper {
  max-width: 1400px;
  margin: 0 auto;
}

.setup-redirect {
  min-height: 100vh;
}

/* P4: 文件夹查重 Loading 遮罩 */
.folder-checking-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.35);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 9998;
  backdrop-filter: blur(2px);
}
.folder-checking-hint {
  background: #fff;
  border-radius: 12px;
  padding: 32px 48px;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 16px;
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.18);
}
.folder-checking-hint p {
  margin: 0;
  font-size: 15px;
  color: #606266;
}

/* P0 / P2: 重复文件详情弹窗 */
.duplicate-dialog-content .duplicate-summary {
  margin: 0 0 4px;
  font-size: 14px;
  color: #303133;
}
.duplicate-dialog-content .pending-hash-warning {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: 10px;
  padding: 8px 12px;
  background: #fdf6ec;
  border: 1px solid #f5dab1;
  border-radius: 6px;
  font-size: 13px;
  color: #e6a23c;
}
.dup-filename {
  display: block;
  max-width: 200px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 13px;
}

@media (max-width: 768px) {
  .app-main {
    padding: 20px;
  }
  .app-title {
    font-size: 18px;
  }
  .header-content {
    padding: 0 16px;
  }
  .nav-item {
    padding: 0 10px;
    font-size: 13px;
  }
}

.drag-overlay {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background: rgba(52, 152, 219, 0.12);
  z-index: 9999;
  display: flex;
  align-items: center;
  justify-content: center;
  pointer-events: none;
}

.drag-hint {
  text-align: center;
  padding: 40px 60px;
  border: 3px dashed #3498db;
  border-radius: 16px;
  background: rgba(255, 255, 255, 0.95);
  color: #3498db;
}

.drag-hint p {
  margin-top: 12px;
  font-size: 18px;
  font-weight: 500;
}

.progress-content {
  padding: 20px 0;
}

.current-file {
  margin-bottom: 16px;
  font-size: 14px;
  color: #606266;
  word-break: break-all;
}

.progress-stats {
  margin-top: 16px;
  font-size: 13px;
  color: #909399;
  text-align: center;
}
</style>
