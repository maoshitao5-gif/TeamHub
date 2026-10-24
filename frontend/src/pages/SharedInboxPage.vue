<template>
  <div class="shared-page" v-loading="initializing">
    <header>
      <div><h2>共享资料</h2><span>{{ context?.workspace_name || '共享资料库' }}</span></div>
      <SharedFavorites v-if="context" :key="context.workspace_id" :server="context.server_url" :workspace="context.workspace_id" @open="openDetail" />
      <el-button v-if="authStore.isLoggedIn" @click="authStore.refreshWorkspaces()">刷新</el-button>
    </header>
    <el-alert v-if="pageError" :title="pageError" type="error" :closable="false" />
    <template v-if="!authStore.isLoggedIn">
      <el-alert v-if="!authStore.sessionError" title="请先登录云服务" type="info" :closable="false">
        <el-button type="primary" @click="router.push({ path: '/login', query: { redirect: route.fullPath } })">前往登录</el-button>
      </el-alert>
    </template>
    <SharedLibraryEmpty v-else-if="authStore.workspaceLoading || !authStore.currentWorkspace" :libraries="authStore.allWorkspaces" :loading="authStore.workspaceLoading" :error="authStore.workspaceError" @select="selectLibrary" @retry="authStore.refreshWorkspaces()" />
    <template v-if="authStore.isLoggedIn && context && !authStore.workspaceLoading">
      <el-tabs v-model="tab" @tab-change="loadTab">
        <el-tab-pane label="资料查找" name="docs">
          <div class="search-row">
            <el-autocomplete v-model="keyword" :fetch-suggestions="suggestNames" placeholder="按名称查找" clearable @input="scheduleSearch" @select="search" />
            <el-select v-model="selectedTags" multiple filterable remote :remote-method="suggestTags" :loading="suggesting" placeholder="输入标签逐步缩小范围" @change="search" @visible-change="visible => visible && suggestTags('')">
              <el-option v-for="tag in tagOptions" :key="tag.id" :label="tag.name" :value="tag.id" />
            </el-select>
            <el-button @click="clearSearch">清除条件</el-button>
          </div>
          <p v-if="suggestionError" class="muted">联想暂不可用，仍可按名称查找或使用已选标签。</p>
          <div v-if="related.length" class="suggestions">还可按这些标签缩小范围：
            <el-button v-for="tag in related" :key="tag.id" size="small" @click="chooseRelated(tag)">{{ tag.name }}（{{ tag.count }}）</el-button>
          </div>
          <el-alert v-if="searchError" :title="searchError" type="error" :closable="false" />
          <el-button v-if="searchError" text @click="search">重试搜索</el-button>
          <p>共 {{ total }} 份资料</p>
          <div v-loading="searching">
            <el-empty v-if="!searching && !searchError && !docs.length" description="没有匹配的共享资料" />
            <article v-for="doc in docs" :key="doc.id" class="shared-doc">
              <div class="doc-summary" @click="openDetail(doc)">
                <strong>{{ doc.name }}</strong>
                <p><el-tag v-for="tag in doc.tags" :key="tag.id" size="small">{{ tag.name }}</el-tag></p>
                <span class="muted">{{ doc.is_folder ? '文件夹 · ' : '' }}{{ formatSize(doc.total_size) }} · {{ doc.uploader_display_name || '成员贡献' }}</span>
              </div>
              <el-button @click="openDetail(doc)">查看详情</el-button>
              <el-button :data-testid="`preview-current-${doc.id}`" @click="openCurrentPreview(doc)">预览</el-button>
              <el-button type="primary" plain @click="openPull(doc)">取回到个人库</el-button>
              <el-button v-if="context.can_manage" type="danger" text :disabled="busy" @click="manage(doc, 'trash')">移入共享回收站</el-button>
            </article>
          </div>
          <el-pagination v-if="total > 20" v-model:current-page="page" :page-size="20" :total="total" layout="prev, pager, next" @current-change="loadSearch" />
        </el-tab-pane>
        <el-tab-pane v-if="context.can_manage" label="共享回收站" name="trash">
          <el-alert v-if="managementError" :title="managementError" type="error" :closable="false" />
          <div v-loading="managing">
            <el-empty v-if="!managing && !managementError && !trash.length" description="共享回收站为空" />
            <article v-for="doc in trash" :key="doc.id" class="shared-doc">
              <strong class="doc-summary">{{ doc.name }}</strong>
              <el-button :disabled="busy" @click="manage(doc, 'restore')">恢复资料</el-button>
              <el-button type="danger" :disabled="busy" @click="manage(doc, 'purge')">永久删除</el-button>
            </article>
          </div>
          <el-pagination v-if="trashTotal > 20" v-model:current-page="trashPage" :total="trashTotal" :page-size="20" layout="prev, pager, next" @current-change="loadTab" />
        </el-tab-pane>
        <el-tab-pane v-if="context.can_deactivate_users" label="成员账号" name="members">
          <p>停用账号后，该成员不能再登录或访问共享服务；已有资料、版本和标签贡献保留。此操作影响该账号的全部共享资料库。</p>
          <el-alert v-if="managementError" :title="managementError" type="error" :closable="false" />
          <article v-for="member in members" :key="member.id" class="shared-doc">
            <strong class="doc-summary">{{ member.display_name }}</strong>
            <span>{{ member.is_active ? '正常' : '已停用' }}</span>
            <el-button type="danger" :disabled="busy || !member.is_active || member.id === context.user_id" @click="deactivate(member)">停用账号</el-button>
          </article>
        </el-tab-pane>
      </el-tabs>
    </template>

    <el-drawer v-model="detailVisible" :title="detail?.name || '共享资料详情'" size="480px">
      <template v-if="detail">
        <p>{{ detail.description }}</p>
        <el-button :disabled="busy || detailLoading || !!detailError" @click="favoriteDetail">收藏此共享资料</el-button>
        <el-button data-testid="preview-detail-current" :disabled="detailLoading || !currentVersion" @click="openPreview(detail, currentVersion)">预览当前版</el-button>
        <h3>共享标签</h3>
        <el-alert v-if="detailError" :title="detailError" type="error" :closable="false" />
        <div v-loading="detailLoading">
          <div v-for="tag in links" :key="tag.tag_id" class="tag-contribution">
            <el-tag>{{ tag.name }}</el-tag>
            <span class="muted">{{ tag.contributor_name || '历史贡献' }}</span>
            <el-button v-if="tag.can_remove" text type="danger" :disabled="busy" @click="removeTag(tag)">撤销此标注</el-button>
          </div>
          <p v-if="!links.length && !detailLoading && !detailError" class="muted">暂无共享标签</p>
        </div>
        <div v-if="context?.can_tag" class="search-row">
          <el-input v-model="newTag" maxlength="100" placeholder="补充一个方便查找的标签" @keyup.enter="addTag" />
          <el-button type="primary" :loading="busy" :disabled="!newTag.trim() || detailLoading || !!detailError" @click="addTag">补充标签</el-button>
        </div>
        <p class="muted">已有标注不会被整组替换；普通成员只能撤销自己的贡献。</p>
        <h3>版本历史</h3>
        <div v-for="version in versions" :key="version.id" class="version-row">
          <span>v{{ version.version_number }} <el-tag v-if="version.is_current" size="small">当前</el-tag></span>
          <div class="version-actions"><el-button :data-testid="`preview-version-${version.id}`" @click="openPreview(detail, version)">预览</el-button><el-button @click="openPull(detail, version.id)">取回此版本</el-button></div>
        </div>
        <el-button v-if="detailError" @click="openDetail(detail)">重新加载详情</el-button>
      </template>
    </el-drawer>
    <CloudFilePreview v-model="previewVisible" :target="previewTarget" :transport="cloudPreviewTransport" :download-file="downloadPreview" :identity-key="previewIdentity" />
    <el-dialog v-model="pullVisible" title="取回到个人库" width="480px" :close-on-click-modal="false" :before-close="done => { if (!pulling) done() }">
      <p>{{ pullTarget?.name }}。相同内容不重复收录；同名而内容不同的文件另存，不覆盖已有文件。</p>
      <p class="muted">已经关联的资料，后续可从本地详情“检查并更新本地”延续版本。</p>
      <TransferDuplicateNotice v-if="pullDuplicate" :result="pullDuplicate" @open="openExistingLocal" />
      <el-input v-else v-model="pullDirectory" :disabled="pulling" placeholder="相对个人库的目录；留空为库根目录" />
      <template #footer><el-button :disabled="pulling" @click="pullVisible = false">关闭</el-button><el-button v-if="!pullDuplicate" type="primary" :loading="pulling" @click="submitPull">确认取回</el-button></template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, ref, watch, onMounted, onBeforeUnmount } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { searchCloudDocs, listCloudDocs, listCloudDocVersions, pullSharedDoc, cloudPreviewTransport, getCloudPreviewVersions } from '@/api/sync'
import { getCollaborationContext, getSharedTagLinks, changeSharedTags, getSharedSuggestions,
  getSharedTrash, manageSharedDocument, getSharedMembers, deactivateSharedMember } from '@/api/collaboration'
import { formatFileSize } from '@/utils/format'
import SharedFavorites from '@/components/SharedFavorites.vue'
import SharedLibraryEmpty from '@/components/SharedLibraryEmpty.vue'
import { addSharedFavorite } from '@/api/favorite'
import CloudFilePreview from '@/components/CloudFilePreview.vue'
import { savePreviewDownload } from '@/utils/previewDownload'
import { useAuthStore } from '@/stores/auth'
import { useAppStore } from '@/stores/app'
import TransferDuplicateNotice from '@/components/TransferDuplicateNotice.vue'
import { contentDuplicate, existingLocalLocation } from '@/utils/transferDedup'

const context = ref(null), initializing = ref(false), pageError = ref(''), tab = ref('docs')
const keyword = ref(''), selectedTags = ref([]), tagOptions = ref([]), related = ref([]), suggesting = ref(false), suggestionError = ref(false)
const docs = ref([]), total = ref(0), page = ref(1), searching = ref(false), searchError = ref('')
const trash = ref([]), trashTotal = ref(0), trashPage = ref(1), members = ref([]), managing = ref(false), managementError = ref('')
const detailVisible = ref(false), detail = ref(null), links = ref([]), versions = ref([]), detailLoading = ref(false), detailError = ref(''), newTag = ref(''), busy = ref(false)
const pullVisible = ref(false), pullTarget = ref(null), pullVersion = ref(null), pullDirectory = ref(''), pulling = ref(false)
const pullDuplicate = ref(null), route = useRoute(), router = useRouter()
const previewVisible = ref(false), previewTarget = ref(null)
const authStore = useAuthStore(), appStore = useAppStore()
const currentVersion = computed(() => versions.value.find(version => version.is_current))
const previewIdentity = computed(() => `${context.value?.server_id || context.value?.server_url}:${context.value?.user_id}:${context.value?.workspace_id}:${authStore.user?.id}:${appStore.settings.cloud_api_url}:${appStore.libraryPath}:${appStore.syncStatus?.server_id}:${appStore.syncStatus?.workspace_id}`)
let workspace = '', epoch = 0, searchSeq = 0, detailSeq = 0, suggestionSeq = 0, nameSeq = 0, managementSeq = 0, timer
let previewSelectionGeneration = 0, previewSelectionController = null, mounted = false
const message = e => e.response?.data?.error?.message || e.response?.data?.detail || e.message || '请求失败，请重试'
const formatSize = value => formatFileSize(value || 0)

function cancelPreviewSelection() { ++previewSelectionGeneration; previewSelectionController?.abort(); previewSelectionController = null }
function resetPreview() { cancelPreviewSelection(); previewVisible.value = false; previewTarget.value = null }
function openPreview(doc, version) {
  cancelPreviewSelection()
  if (!doc || !version?.id || !workspace) return
  const path = `/api/sharing/cloud-docs/${encodeURIComponent(doc.id)}/versions/${encodeURIComponent(version.id)}`
  const query = `?workspace_id=${encodeURIComponent(workspace)}`
  previewTarget.value = {
    documentId: doc.id, versionId: version.id, versionNumber: version.version_number,
    name: doc.name, filename: version.original_filename || doc.name,
    previewPath: `${path}/preview${query}`, contentPath: `${path}/preview/content${query}`, downloadPath: `${path}/download${query}`,
  }
  previewVisible.value = true
}
async function openCurrentPreview(doc) {
  resetPreview()
  const run = previewSelectionGeneration, ws = workspace, scope = epoch, selection = new AbortController()
  previewSelectionController = selection
  try {
    const history = await getCloudPreviewVersions(doc.id, ws, { signal: selection.signal })
    if (run !== previewSelectionGeneration || scope !== epoch || ws !== workspace || selection.signal.aborted) return
    const version = history.find(item => item.is_current)
    if (!version) throw new Error('此资料没有可预览的当前版本')
    openPreview(doc, version)
  } catch (e) { if (run === previewSelectionGeneration && scope === epoch && !selection.signal.aborted) ElMessage.error(message(e)) }
}
const downloadPreview = (target, options) => savePreviewDownload(cloudPreviewTransport, target, options)
function selectLibrary(id) { const library = authStore.allWorkspaces.find(item => item.id === id); if (library) authStore.switchWorkspace(library) }

async function favoriteDetail() {
  busy.value = true
  try {
    await addSharedFavorite(context.value.server_url, workspace, detail.value.id)
    ElMessage.success('已收藏，可从共享收藏再次打开')
  } catch (e) { ElMessage.error(message(e)) }
  finally { busy.value = false }
}

async function initialize() {
  resetPreview()
  const run = ++epoch
  clearTimeout(timer)
  ++searchSeq; ++detailSeq; ++suggestionSeq; ++nameSeq; ++managementSeq
  context.value = null; workspace = ''; docs.value = []; total.value = 0
  tagOptions.value = []; related.value = []; selectedTags.value = []; keyword.value = ''
  trash.value = []; members.value = []; tab.value = 'docs'; detailVisible.value = false; pullVisible.value = false; pullDuplicate.value = null; pulling.value = false
  pageError.value = ''
  if (!authStore.isLoggedIn || !authStore.currentWorkspace?.id || authStore.workspaceLoading) { initializing.value = false; return }
  const selectedWorkspace = authStore.currentWorkspace.id
  initializing.value = true
  try {
    const permission = await getCollaborationContext(selectedWorkspace)
    if (run !== epoch || authStore.workspaceLoading || authStore.currentWorkspace?.id !== selectedWorkspace) return
    context.value = permission; workspace = selectedWorkspace
    await search()
    if (run === epoch) await openHighlightedDocument()
  } catch (e) { if (run === epoch) pageError.value = message(e) }
  finally { if (run === epoch) initializing.value = false }
}
function scheduleSearch() { clearTimeout(timer); timer = setTimeout(search, 300) }
async function search() { page.value = 1; await loadSearch() }
async function loadSearch() {
  if (!workspace) return
  const run = ++searchSeq, ws = workspace
  searching.value = true; searchError.value = ''
  try {
    const result = await searchCloudDocs({ workspace_id: ws, q: keyword.value, tag_ids: selectedTags.value.join(','), page: page.value, page_size: 20 })
    if (run !== searchSeq || ws !== workspace) return
    docs.value = result.items; total.value = result.total
    related.value = []
    if (selectedTags.value.length) {
      try {
        const hints = await getSharedSuggestions(ws, 'cooccurrence', keyword.value, selectedTags.value)
        if (run === searchSeq && ws === workspace) related.value = hints
      } catch { if (run === searchSeq) suggestionError.value = true }
    }
  } catch (e) {
    if (run === searchSeq) { docs.value = []; total.value = 0; searchError.value = message(e) }
  } finally { if (run === searchSeq) searching.value = false }
}
function clearSearch() { keyword.value = ''; selectedTags.value = []; related.value = []; search() }
async function suggestNames(q, callback) {
  const run = ++nameSeq, ws = workspace
  if (!ws) { callback([]); return }
  try {
    const result = await getSharedSuggestions(ws, 'name', q)
    if (run === nameSeq && ws === workspace) callback(result.map(item => ({ value: item.name })))
  } catch { if (run === nameSeq) { callback([]); suggestionError.value = true } }
}
async function suggestTags(q) {
  const run = ++suggestionSeq, ws = workspace
  suggesting.value = true; suggestionError.value = false
  try {
    const hints = await getSharedSuggestions(ws, 'tag', q)
    if (run !== suggestionSeq || ws !== workspace) return
    tagOptions.value = [...new Map([...tagOptions.value.filter(t => selectedTags.value.includes(t.id)), ...hints].map(t => [t.id, t])).values()]
  } catch { if (run === suggestionSeq) suggestionError.value = true }
  finally { if (run === suggestionSeq) suggesting.value = false }
}
function chooseRelated(tag) {
  if (!tagOptions.value.some(t => t.id === tag.id)) tagOptions.value.push(tag)
  if (!selectedTags.value.includes(tag.id)) selectedTags.value.push(tag.id)
  search()
}
function clearDetail() {
  ++detailSeq; resetPreview()
  detailVisible.value = false; detail.value = null; links.value = []; versions.value = []
  newTag.value = ''; detailError.value = ''; detailLoading.value = false
}
async function openDetail(doc) {
  resetPreview()
  const run = ++detailSeq, ws = workspace
  detail.value = doc; detailVisible.value = true; detailLoading.value = true
  detailError.value = ''; links.value = []; versions.value = []; newTag.value = ''
  try {
    const [tags, history] = await Promise.all([getSharedTagLinks(doc.id, ws), listCloudDocVersions(doc.id, ws)])
    if (run !== detailSeq || ws !== workspace) return
    links.value = tags; versions.value = history
  } catch (e) {
    if (run !== detailSeq || ws !== workspace) return
    const status = e.response?.status || e.status
    if ([401, 403, 404, 410].includes(status)) { clearDetail(); pageError.value = message(e) }
    else detailError.value = message(e)
  }
  finally { if (run === detailSeq) detailLoading.value = false }
}
async function applyTags(changes) {
  if (busy.value || !detail.value) return
  const id = detail.value.id, ws = workspace, run = epoch
  busy.value = true
  try {
    const updated = await changeSharedTags(id, ws, changes)
    if (run !== epoch) return
    if (detail.value?.id === id) { links.value = updated; newTag.value = '' }
    ElMessage.success('共享标签已更新'); await loadSearch()
  } catch (e) { ElMessage.error(message(e)) }
  finally { busy.value = false }
}
function addTag() { if (newTag.value.trim()) applyTags({ add: [newTag.value.trim()] }) }
function removeTag(tag) { applyTags({ remove: [tag.tag_id] }) }
async function loadTab() {
  if (tab.value === 'docs') { await loadSearch(); return }
  const ws = workspace, run = epoch, currentTab = tab.value, sequence = ++managementSeq
  managing.value = true; managementError.value = ''
  try {
    if (currentTab === 'trash') {
      const result = await getSharedTrash(ws, trashPage.value)
      if (run === epoch && sequence === managementSeq) { trash.value = result.items; trashTotal.value = result.total }
    } else if (currentTab === 'members') {
      const result = await getSharedMembers(ws)
      if (run === epoch && sequence === managementSeq) members.value = result
    }
  } catch (e) { if (run === epoch && sequence === managementSeq) managementError.value = message(e) }
  finally { if (run === epoch && sequence === managementSeq) managing.value = false }
}
async function manage(doc, action) {
  if (busy.value) return
  const ws = workspace, run = epoch
  busy.value = true
  try {
    if (action !== 'restore') await ElMessageBox.confirm(action === 'purge' ? `永久删除「${doc.name}」及其历史记录？此操作不能撤销。` : `将「${doc.name}」移入共享回收站？成员将无法查找和取回。`, '确认组织资料操作', { type: 'warning' })
    if (run !== epoch) return
    const result = await manageSharedDocument(doc.id, ws, action)
    if (run !== epoch) return
    ElMessage.success(result.message)
    await loadTab()
  } catch (e) { if (e !== 'cancel' && e !== 'close') ElMessage.error(message(e)) }
  finally { busy.value = false }
}
async function deactivate(member) {
  if (busy.value) return
  const run = epoch, ws = workspace
  busy.value = true
  try {
    await ElMessageBox.confirm(`停用「${member.display_name}」的账号？全部共享资料库访问将停止，资料和贡献保留。`, '确认停用账号', { type: 'warning' })
    if (run !== epoch) return
    await deactivateSharedMember(member.id, ws)
    if (run === epoch) { ElMessage.success('账号已停用'); await loadTab() }
  } catch (e) { if (e !== 'cancel' && e !== 'close') ElMessage.error(message(e)) }
  finally { busy.value = false }
}
async function openHighlightedDocument() {
  clearDetail()
  const id = route.query.highlight, requestedWorkspace = route.query.workspace
  if (!id || requestedWorkspace !== workspace) return
  const run = epoch, ws = workspace, sequence = detailSeq
  try {
    const result = await listCloudDocs(ws)
    if (run !== epoch || sequence !== detailSeq || ws !== workspace || route.query.highlight !== id || route.query.workspace !== ws) return
    const doc = result.documents.find(item => item.id === id)
    if (!doc) throw new Error('资料已不存在或当前账号无权查看')
    await openDetail(doc)
  } catch (e) { if (run === epoch && sequence === detailSeq && route.query.highlight === id && route.query.workspace === ws) ElMessage.error(message(e)) }
}
function openPull(doc, version = null) { if (pulling.value) return; pullTarget.value = doc; pullVersion.value = version; pullDirectory.value = ''; pullDuplicate.value = null; pullVisible.value = true }
function openExistingLocal() {
  const location = existingLocalLocation(pullDuplicate.value)
  if (location) { pullVisible.value = false; router.push(location) }
}
async function submitPull() {
  if (pulling.value || !pullTarget.value || pullDuplicate.value) return
  const run = epoch, ws = workspace, identity = previewIdentity.value
  const targetId = pullTarget.value.id, versionId = pullVersion.value, targetDir = pullDirectory.value || null
  pulling.value = true
  try {
    const result = await pullSharedDoc(targetId, { workspaceId: ws, versionId, targetDir })
    if (run !== epoch || identity !== previewIdentity.value) return
    const duplicate = contentDuplicate(result, 'local')
    if (duplicate) { pullDuplicate.value = duplicate; return }
    ElMessage.success('已取回到个人库'); pullVisible.value = false
  } catch (e) { if (run === epoch && identity === previewIdentity.value) ElMessage.error(message(e)) }
  finally { if (run === epoch) pulling.value = false }
}
watch(() => [route.query.highlight, route.query.workspace], () => { if (mounted && workspace) openHighlightedDocument() })
watch(() => [authStore.user?.id, authStore.serverId, appStore.settings.cloud_api_url, authStore.currentWorkspace?.id, authStore.workspaceLoading, appStore.libraryPath], () => {
  resetPreview()
  if (mounted) initialize()
})
onMounted(() => { mounted = true; initialize() })
onBeforeUnmount(() => { mounted = false; resetPreview(); ++epoch; ++searchSeq; ++detailSeq; ++suggestionSeq; ++nameSeq; ++managementSeq; clearTimeout(timer) })
</script>

<style scoped>
.shared-page { padding: 24px; max-width: 1280px; margin: 0 auto; }
header, .shared-doc, .version-row, .tag-contribution, .search-row { display: flex; align-items: center; gap: 12px; }
header { justify-content: space-between; margin-bottom: 24px; }
h2 { margin: 0 0 6px; }
.search-row { margin: 16px 0; }
.search-row .el-select { min-width: 260px; flex: 1; }
.shared-doc { padding: 18px; background: white; border: 1px solid #e4e7ed; border-radius: 8px; margin: 12px 0; }
.doc-summary { flex: 1; cursor: pointer; min-width: 0; overflow-wrap: anywhere; }
.el-tag { margin-right: 6px; }
.muted { color: #687583; font-size: 13px; }
.suggestions { margin: 12px 0; }
.tag-contribution, .version-row { padding: 12px 0; border-bottom: 1px solid #eee; justify-content: space-between; }
.version-actions { display: flex; flex-shrink: 0; gap: 8px; }
.version-actions .el-button + .el-button { margin-left: 0; }
</style>
