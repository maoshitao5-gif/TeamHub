<template>
  <section class="shared-browser">
    <div class="section-heading"><div><p class="eyebrow">ORGANIZATION LIBRARY</p><h1>组织资料</h1><p class="muted">查找团队共享的文件，保持每次交付的版本清楚。</p></div><el-button type="primary" size="large" data-testid="new-publication" @click="openUpload()">上传新资料</el-button></div>
    <form class="search-bar" @submit.prevent="search(1)">
      <el-input v-model="query" clearable placeholder="搜索资料名称" aria-label="搜索资料名称" />
      <el-input v-model="tags" clearable placeholder="标签，以逗号分隔" aria-label="筛选标签" />
      <el-button native-type="submit" :loading="loading">搜索</el-button>
    </form>
    <p v-if="error" role="alert" class="error-text">{{ error }}</p>
    <div class="library-table" v-loading="loading">
      <el-table :data="documents" empty-text="当前范围内没有资料" @row-click="openDocument" row-class-name="document-row">
        <el-table-column label="资料" min-width="260"><template #default="{ row }"><button class="document-link" @click.stop="openDocument(row)">{{ row.name }}</button><div class="muted small">{{ row.description || '暂无说明' }}</div></template></el-table-column>
        <el-table-column label="标签" min-width="180"><template #default="{ row }"><el-tag v-for="tag in row.tags" :key="tag.id" size="small" class="tag">{{ tag.name }}</el-tag></template></el-table-column>
        <el-table-column label="大小" width="120"><template #default="{ row }">{{ size(row.total_size) }}</template></el-table-column>
        <el-table-column label="更新" width="170"><template #default="{ row }">{{ date(row.updated_at) }}</template></el-table-column>
        <el-table-column label="操作" width="110"><template #default="{ row }"><el-button text type="primary" :data-testid="`preview-current-${row.id}`" @click.stop="openCurrentPreview(row)">预览</el-button></template></el-table-column>
      </el-table>
      <div class="table-footer"><span class="muted">{{ total }} 份可访问资料</span><el-pagination :current-page="page" :page-size="20" :total="total" layout="prev, pager, next" @current-change="search" /></div>
    </div>

    <el-drawer v-model="detailOpen" title="资料详情" size="min(660px, 96vw)">
      <div v-if="detail" class="document-detail">
        <p class="eyebrow">{{ detail.visibility === 'restricted' ? '指定成员可见' : '全空间共享' }}</p>
        <h2>{{ detail.name }}</h2><p class="muted">{{ detail.description || '暂无说明' }}</p>
        <div class="detail-actions">
          <el-button :disabled="!currentVersion" data-testid="preview-detail-current" @click="openPreview(detail, currentVersion)">预览当前版</el-button>
          <el-button v-if="detail.capabilities?.contribute && detail.doc_type !== 'folder'" type="primary" @click="openUpload(detail)">提交新版本</el-button>
          <el-button v-if="detail.capabilities?.manage" @click="openAccess">管理访问范围</el-button>
          <el-button v-if="detail.capabilities?.distribute" @click="deliveryOpen = true">投递当前版本</el-button>
        </div>
        <h3>版本记录</h3><p class="muted small">下载所选版本，文件将保存到浏览器的下载位置。</p>
        <article v-for="version in detail.versions" :key="version.id" class="version-row">
          <div><strong>V{{ version.version_number }}</strong><el-tag v-if="version.id === detail.current_version_id" size="small">当前版</el-tag><p>{{ version.note || '未填写版本说明' }}</p><span class="muted small">{{ date(version.created_at) }} · {{ size(version.file_size) }}</span></div>
          <div class="version-actions"><el-button :data-testid="`preview-version-${version.id}`" :aria-label="`预览版本${version.version_number}`" @click="openPreview(detail, version)">预览</el-button><el-button :aria-label="`下载版本${version.version_number}`" @click="download(version)">下载</el-button></div>
        </article>
      </div>
    </el-drawer>

    <CloudFilePreview v-model="previewOpen" :target="previewTarget" :transport="transport" :download-file="downloadPreview" :identity-key="`${webSession.serverId}:${webSession.user?.id}:${workspaceId}`" />

    <DeliveryCompose v-if="detail" v-model="deliveryOpen" :transport="transport" :workspace-id="workspaceId" :source-document-id="detail.id" :source-version-id="detail.current_version_id" :filename="detail.name" :identity-key="`${webSession.serverId}:${webSession.user?.id}`" @sent="deliveryOpen = false" />

    <el-dialog v-model="uploadOpen" :title="uploadTarget ? '提交新版本' : '上传新资料'" width="min(640px, 96vw)" :close-on-click-modal="false" :before-close="closeUpload">
      <el-form label-position="top" @submit.prevent="publish">
        <el-form-item label="文件"><input ref="fileInput" type="file" aria-label="上传文件" :disabled="!!operation || publishing" @change="selectFile" /></el-form-item>
        <template v-if="!uploadTarget">
          <el-form-item label="资料名称"><el-input v-model="form.name" :disabled="!!operation || publishing" maxlength="255" aria-label="资料名称" /></el-form-item>
          <el-form-item label="资料说明"><el-input v-model="form.description" type="textarea" :disabled="!!operation || publishing" aria-label="资料说明" /></el-form-item>
          <el-form-item label="标签"><el-input v-model="form.tags" placeholder="多个标签以逗号分隔" :disabled="!!operation || publishing" /></el-form-item>
          <fieldset :disabled="!!operation || publishing"><legend>访问范围</legend><AccessEditor v-model:visibility="form.visibility" v-model:access="form.access" :members="members" /></fieldset>
        </template>
        <p v-else class="scope-note">向「{{ uploadTarget.name }}」提交新版本。原有历史将保留，访问范围沿用资料设置。</p>
        <el-form-item label="本次版本说明"><el-input v-model="form.note" :disabled="!!operation || publishing" aria-label="本次版本说明" /></el-form-item>
        <el-checkbox v-model="confirmed" :disabled="!!operation || publishing">确认文件内容、目标资料与访问范围</el-checkbox>
        <TransferDuplicateNotice v-if="uploadDuplicate" :result="uploadDuplicate" @open="openExistingCloud" />
        <p v-if="uploadError" role="alert" class="error-text">{{ uploadError }}</p>
        <p v-if="progress" role="status" class="muted">{{ progress }}</p>
        <div class="dialog-footer"><el-button v-if="(operation || uploadDuplicate) && !publishing" @click="resetAttempt">重新填写</el-button><el-button :disabled="publishing" @click="uploadOpen = false">关闭</el-button><el-button v-if="!uploadDuplicate" type="primary" native-type="submit" :loading="publishing" :disabled="!confirmed || !file">{{ operation ? '核对并重试' : uploadTarget ? '确认提交' : '确认上传' }}</el-button></div>
      </el-form>
    </el-dialog>

    <el-dialog v-model="accessOpen" title="管理访问范围" width="min(620px, 96vw)">
      <AccessEditor v-model:visibility="accessForm.visibility" v-model:access="accessForm.access" :members="members" />
      <p class="muted">管理授权可主动移交；必须保留至少一名有效资料管理者。</p>
      <template #footer><el-button @click="accessOpen = false">取消</el-button><el-button type="primary" :loading="savingAccess" @click="saveAccess">保存访问范围</el-button></template>
    </el-dialog>
  </section>
</template>

<script setup>
import { computed, onBeforeUnmount, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import AccessEditor from './AccessEditor.vue'
import DeliveryCompose from '../components/delivery/DeliveryCompose.vue'
import CloudFilePreview from '../components/CloudFilePreview.vue'
import { savePreviewDownload } from '../utils/previewDownload'
import { saveDownload, transport, webSession } from './api'
import TransferDuplicateNotice from '../components/TransferDuplicateNotice.vue'
import { contentDuplicate } from '../utils/transferDedup'

const props = defineProps({ workspaceId: { type: String, required: true } })
const base = computed(() => `/api/workspaces/${encodeURIComponent(props.workspaceId)}`)
const documents = ref([]), query = ref(''), tags = ref(''), total = ref(0), page = ref(1)
const loading = ref(false), error = ref(''), members = ref([])
const detail = ref(null), detailOpen = ref(false)
const previewOpen = ref(false), previewTarget = ref(null)
const currentVersion = computed(() => detail.value?.versions?.find(version => version.id === detail.value.current_version_id))
const deliveryOpen = ref(false)
const uploadOpen = ref(false), uploadTarget = ref(null), file = ref(null), fileInput = ref(null)
const publishing = ref(false), confirmed = ref(false), uploadError = ref(''), progress = ref(''), operation = ref(null)
const uploadDuplicate = ref(null)
let duplicateScope = null
const form = reactive({ name: '', description: '', tags: '', note: '', visibility: 'restricted', access: [] })
const accessOpen = ref(false), savingAccess = ref(false)
const accessForm = reactive({ visibility: 'restricted', access: [], base_access_revision: 0 })
let searchGeneration = 0, detailGeneration = 0, scopeGeneration = 0
let controller = new AbortController(), disposed = false, accessTarget = null
let previewSelectionGeneration = 0, previewSelectionController = null

function cancelPreviewSelection() {
  ++previewSelectionGeneration; previewSelectionController?.abort(); previewSelectionController = null
}
function openPreview(doc, version) {
  cancelPreviewSelection()
  if (!doc || !version?.id) return
  const path = `${base.value}/documents/${encodeURIComponent(doc.id)}/versions/${encodeURIComponent(version.id)}`
  previewTarget.value = {
    documentId: doc.id, versionId: version.id, versionNumber: version.version_number,
    name: doc.name, filename: version.original_filename || doc.name,
    previewPath: `${path}/preview`, downloadPath: `${path}/download`,
  }
  previewOpen.value = true
}
async function openCurrentPreview(row) {
  cancelPreviewSelection(); previewOpen.value = false; previewTarget.value = null
  const scope = currentScope(), run = previewSelectionGeneration, selection = new AbortController()
  previewSelectionController = selection
  try {
    const doc = await transport('GET', `${base.value}/documents/${encodeURIComponent(row.id)}`, undefined, { signal: selection.signal })
    checkScope(scope)
    if (run !== previewSelectionGeneration || selection.signal.aborted) return
    const version = doc.versions?.find(item => item.id === doc.current_version_id)
    if (!version) throw new Error('此资料没有可预览的当前版本')
    openPreview(doc, version)
  } catch (err) { if (!disposed && run === previewSelectionGeneration && !selection.signal.aborted) ElMessage.error(err.message) }
}
const downloadPreview = (target, options) => savePreviewDownload(transport, target, options)

function currentScope() {
  return { generation: scopeGeneration, actorId: webSession.user?.id, serverId: webSession.serverId, workspaceId: props.workspaceId }
}
function checkScope(scope) {
  if (disposed || scope.generation !== scopeGeneration || scope.actorId !== webSession.user?.id || scope.serverId !== webSession.serverId || scope.workspaceId !== props.workspaceId) {
    throw new Error('账号或空间已变化，本次操作已停止，请在当前账号下重新操作')
  }
}
async function scopedRequest(scope, method, path, data, options = {}) {
  checkScope(scope)
  const result = await transport(method, path, data, { ...options, signal: controller.signal })
  checkScope(scope)
  return result
}

const size = value => !value ? '0 B' : value < 1024 ? `${value} B` : value < 1024 ** 2 ? `${(value / 1024).toFixed(1)} KB` : `${(value / 1024 ** 2).toFixed(1)} MB`
const date = value => value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '—'

async function search(nextPage = 1) {
  const generation = ++searchGeneration
  loading.value = true; error.value = ''; page.value = nextPage
  try {
    const result = await transport('GET', `${base.value}/documents/search`, undefined, { params: { q: query.value, tags: tags.value, page: nextPage, page_size: 20 } })
    if (generation !== searchGeneration) return
    documents.value = result.items; total.value = result.total
  } catch (err) { if (generation === searchGeneration) { error.value = err.message; documents.value = []; total.value = 0 } }
  finally { if (generation === searchGeneration) loading.value = false }
}

async function openDocument(row) {
  cancelPreviewSelection(); previewOpen.value = false; previewTarget.value = null
  detailOpen.value = false; detail.value = null
  const scope = currentScope(), generation = ++detailGeneration
  try {
    const result = await scopedRequest(scope, 'GET', `${base.value}/documents/${row.id}`)
    if (generation !== detailGeneration) return
    detail.value = result
    detail.value.versions = [...(result.versions || [])].sort((a, b) => b.version_number - a.version_number)
    detailOpen.value = true
  } catch (err) { if (!disposed && generation === detailGeneration && err.name !== 'AbortError') ElMessage.error(err.message) }
}

async function download(version) {
  try { await saveDownload(`${base.value}/documents/${detail.value.id}/versions/${version.id}/download`, version.original_filename || detail.value.name) }
  catch (err) { ElMessage.error(err.message) }
}

async function loadMembers() {
  const scope = currentScope()
  const result = await scopedRequest(scope, 'GET', `${base.value}/delivery-members`)
  members.value = (Array.isArray(result) ? result : result.items || []).map(member => ({ ...member, user_id: member.user_id || member.id }))
}

async function openUpload(target = null) {
  if (publishing.value) return
  const scope = currentScope()
  try { await loadMembers() } catch (err) { ElMessage.error(err.message); return }
  if (!scopeIsCurrent(scope)) return
  uploadTarget.value = target
  Object.assign(form, { name: '', description: '', tags: '', note: '', visibility: 'restricted', access: [{ user_id: webSession.user.id, role: 'manager' }] })
  file.value = null; confirmed.value = false; operation.value = null; uploadError.value = ''; progress.value = ''; uploadDuplicate.value = null; duplicateScope = null
  uploadOpen.value = true
  if (fileInput.value) fileInput.value.value = ''
}

function selectFile(event) { file.value = event.target.files[0] || null; if (!uploadTarget.value && file.value) form.name = file.value.name }
function closeUpload(done) { if (!publishing.value) done() }
function resetAttempt() { operation.value = null; confirmed.value = false; uploadError.value = ''; progress.value = ''; uploadDuplicate.value = null; duplicateScope = null }
function scopeIsCurrent(scope) { return !disposed && scope.generation === scopeGeneration && scope.actorId === webSession.user?.id && scope.serverId === webSession.serverId && scope.workspaceId === props.workspaceId }
function showDuplicate(value, scope) {
  const duplicate = contentDuplicate(value, 'cloud')
  if (!duplicate || !scopeIsCurrent(scope)) return false
  uploadDuplicate.value = duplicate; duplicateScope = scope
  operation.value = null; confirmed.value = false; uploadError.value = ''; progress.value = ''
  return true
}
async function openExistingCloud() {
  if (!uploadDuplicate.value?.documentId || !duplicateScope || !scopeIsCurrent(duplicateScope)) return
  const id = uploadDuplicate.value.documentId
  uploadOpen.value = false
  await openDocument({ id })
}

async function publish() {
  if (!file.value || !confirmed.value || publishing.value || uploadDuplicate.value) return
  if (!uploadTarget.value && !form.name.trim()) { uploadError.value = '请填写资料名称'; return }
  publishing.value = true; uploadError.value = ''
  const scope = currentScope()
  try {
    if (!operation.value) {
      const target = uploadTarget.value
      operation.value = {
        scope: currentScope(),
        requestId: crypto.randomUUID(), base: base.value, file: file.value,
        path: target ? `${base.value}/documents/${target.id}/versions` : `${base.value}/publications`,
        body: target
          ? { base_version_id: target.current_version_id, note: form.note, confirmed: true }
          : { name: form.name.trim(), description: form.description, tags: form.tags.split(/[,，]/).map(t => t.trim()).filter(Boolean), note: form.note, doc_type: /\.(zip|7z|rar)$/i.test(file.value.name) ? 'archive' : 'file', visibility: form.visibility, access: form.access.map(entry => ({ ...entry })), force_new: false, confirmed: true },
      }
    }
    const op = operation.value
    checkScope(op.scope)
    progress.value = '正在核对发送结果…'
    let existing
    try { existing = await scopedRequest(op.scope, 'GET', `${op.base}/publications/${encodeURIComponent(op.requestId)}`) }
    catch (err) { if (err.status !== 404) throw err }
    checkScope(op.scope)
    if (showDuplicate(existing, op.scope)) return
    if (existing?.status === 'succeeded') { await finishPublication(existing, op.scope); return }
    if (!op.upload) {
      progress.value = '正在上传文件，请保持页面打开…'
      op.upload = await scopedRequest(op.scope, 'POST', `${op.base}/uploads`, op.file, { params: { filename: op.file.name, purpose: 'publication' }, headers: { 'Content-Type': 'application/octet-stream' } })
    }
    progress.value = '文件已上传，正在提交…'
    const result = await scopedRequest(op.scope, 'POST', op.path, { ...op.body, request_id: op.requestId, upload_id: op.upload.upload_id, content_hash: op.upload.sha256, original_filename: op.file.name })
    if (showDuplicate(result, op.scope)) return
    if (result.status !== 'succeeded') throw new Error(result.error?.message || '提交未完成，请核对结果后重试')
    await finishPublication(result, op.scope)
  } catch (err) { if (scopeIsCurrent(scope) && !showDuplicate(err, scope)) { uploadError.value = err.message; progress.value = '原请求已保留，可核对并重试。' } }
  finally { if (scopeIsCurrent(scope)) publishing.value = false }
}

async function finishPublication(result, scope) {
  checkScope(scope)
  uploadOpen.value = false; operation.value = null; progress.value = ''
  if (result.code === 'CONTENT_UNCHANGED') ElMessage.info('内容未变，保留当前版本')
  else ElMessage.success('资料已提交')
  await search(1)
  checkScope(scope)
  await openDocument({ id: result.document_id || result.result_document_id })
}

async function openAccess() {
  const scope = currentScope(), documentId = detail.value.id, generation = detailGeneration
  try {
    await loadMembers()
    checkScope(scope)
    const path = `${base.value}/documents/${documentId}/access`
    const result = await scopedRequest(scope, 'GET', path)
    if (generation !== detailGeneration || detail.value?.id !== documentId) return
    accessTarget = { scope, path, documentId }
    Object.assign(accessForm, { visibility: result.visibility, access: (result.access || []).map(entry => ({ user_id: entry.user_id, role: entry.role })), base_access_revision: result.access_revision })
    accessOpen.value = true
  } catch (err) { ElMessage.error(err.message) }
}

async function saveAccess() {
  const target = accessTarget
  if (!target) return
  savingAccess.value = true
  try {
    await scopedRequest(target.scope, 'PUT', target.path, { ...accessForm })
    accessOpen.value = false; detailOpen.value = false
    ElMessage.success('访问范围已更新'); await search(page.value)
  } catch (err) { ElMessage.error(err.message) }
  finally { savingAccess.value = false }
}

watch(() => [props.workspaceId, webSession.user?.id, webSession.serverId], () => {
  scopeGeneration++; detailGeneration++; controller.abort(); controller = new AbortController()
  accessTarget = null; operation.value = null; uploadDuplicate.value = null; duplicateScope = null; publishing.value = false
  cancelPreviewSelection(); previewOpen.value = false; previewTarget.value = null
  detailOpen.value = false; deliveryOpen.value = false; uploadOpen.value = false; accessOpen.value = false; documents.value = []; search(1)
}, { immediate: true })
onBeforeUnmount(() => { disposed = true; scopeGeneration++; detailGeneration++; searchGeneration++; controller.abort(); cancelPreviewSelection() })
</script>

<style scoped>
.version-actions { display: flex; flex-shrink: 0; gap: 8px; }
.version-actions .el-button + .el-button { margin-left: 0; }
</style>
