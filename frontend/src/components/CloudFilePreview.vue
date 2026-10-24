<template>
  <el-dialog :model-value="modelValue" width="min(1100px, 95vw)" top="5vh" class="cloud-file-preview" data-testid="cloud-file-preview" destroy-on-close :close-on-click-modal="false" @update:model-value="close">
    <template #header>
      <div class="preview-heading">
        <h2 data-testid="cloud-preview-title">{{ target?.name || '资料预览' }}<span v-if="versionNumber"> · V{{ versionNumber }}</span></h2>
        <p>{{ metadata?.filename || target?.filename || '正在读取所选版本…' }}</p>
      </div>
    </template>
    <div v-if="modelValue" v-loading="loading" class="preview-body" aria-live="polite">
      <el-alert v-if="notice && !error" :title="notice" :type="metadata?.truncated ? 'warning' : 'info'" :closable="false" show-icon class="preview-notice" />
      <div v-if="error" class="preview-state" role="alert">
        <h3>暂时无法预览</h3><p>{{ error }}</p>
        <el-button data-testid="preview-retry" @click="loadPreview">重试</el-button>
      </div>
      <template v-else-if="metadata">
        <PdfPreview v-if="metadata.kind === 'pdf' && pdfData" :data="pdfData" @error="showPdfError" />
        <div v-else-if="metadata.kind === 'image' && imageUrl" class="image-preview">
          <div class="image-toolbar"><el-button size="small" aria-label="缩小图片" :disabled="imageZoom <= 0.5" @click="imageZoom = Math.max(0.5, imageZoom - 0.25)">−</el-button><span>{{ Math.round(imageZoom * 100) }}%</span><el-button size="small" aria-label="放大图片" :disabled="imageZoom >= 2" @click="imageZoom = Math.min(2, imageZoom + 0.25)">＋</el-button></div>
          <div class="image-viewport"><img data-testid="preview-image" :src="imageUrl" :alt="metadata.filename" :style="{ width: `${imageZoom * 100}%`, height: `${imageZoom * 100}%` }" @error="showImageError" /></div>
        </div>
        <pre v-else-if="metadata.kind === 'text'" data-testid="preview-text" class="text-preview">{{ metadata.text }}</pre>
        <div v-else-if="metadata.kind === 'document'" data-testid="preview-document" class="document-preview">
          <template v-for="(block, index) in metadata.blocks || []" :key="index">
            <p v-if="block.type === 'paragraph'">{{ block.text }}</p>
            <div v-else-if="block.type === 'table'" class="table-scroll"><table><tbody><tr v-for="(row, rowIndex) in block.rows" :key="rowIndex"><td v-for="(cell, cellIndex) in row" :key="cellIndex">{{ cell }}</td></tr></tbody></table></div>
          </template>
          <p v-if="!metadata.blocks?.length" class="preview-muted">文档没有可预览的文字内容，请下载查看图片或其他内容。</p>
        </div>
        <div v-else-if="metadata.kind === 'spreadsheet'" data-testid="preview-spreadsheet" class="spreadsheet-preview">
          <label class="sheet-selector">工作表 <select v-model.number="sheetIndex" data-testid="preview-sheet-select" aria-label="选择工作表"><option v-for="(sheet, index) in metadata.sheets || []" :key="index" :value="index">{{ sheet.name }}</option></select></label>
          <p v-if="selectedSheet?.truncated" class="sheet-notice">当前工作表内容较多，仅显示部分行列。请下载原文件查看完整内容。</p>
          <div v-if="selectedSheet?.rows?.length" class="table-scroll sheet-table"><table><thead><tr><th aria-label="行号"></th><th v-for="index in sheetColumns" :key="index">{{ columnName(index - 1) }}</th></tr></thead><tbody><tr v-for="(row, rowIndex) in selectedSheet.rows" :key="rowIndex"><th>{{ rowIndex + 1 }}</th><td v-for="index in sheetColumns" :key="index">{{ row[index - 1] ?? '' }}</td></tr></tbody></table></div>
          <p v-else class="preview-muted">当前工作表没有可预览的单元格内容。</p>
        </div>
        <div v-else-if="metadata.kind === 'unsupported'" class="preview-state"><h3>此格式暂不支持在线预览</h3><p>{{ metadata.message || '可以下载原文件，在本机应用中查看。' }}</p></div>
      </template>
    </div>
    <template #footer><div class="preview-footer"><span class="preview-muted">预览所选版本，文件不会收录到个人库。</span><div><el-button data-testid="preview-close" @click="close">关闭</el-button><el-button data-testid="preview-download" type="primary" :disabled="!target || downloading" :loading="downloading" @click="download">下载原文件</el-button></div></div></template>
  </el-dialog>
</template>

<script setup>
import { computed, defineAsyncComponent, onBeforeUnmount, onMounted, ref, shallowRef, watch } from 'vue'
import { ElMessage } from 'element-plus'

const PdfPreview = defineAsyncComponent(() => import('./preview/PdfPreview.vue'))
const props = defineProps({
  modelValue: Boolean,
  target: { type: Object, default: null },
  identityKey: { type: String, required: true },
  transport: { type: Function, required: true },
  downloadFile: { type: Function, required: true },
})
const emit = defineEmits(['update:modelValue'])
const loading = ref(false), error = ref(''), metadata = ref(null), imageUrl = ref(''), pdfData = shallowRef(null)
const imageZoom = ref(1), sheetIndex = ref(0), downloading = ref(false)
let controller = null, generation = 0, disposed = false, checkingAccess = false, identityCancelled = false
const versionNumber = computed(() => metadata.value?.version_number || props.target?.versionNumber)
const selectedSheet = computed(() => metadata.value?.sheets?.[sheetIndex.value] || null)
const sheetColumns = computed(() => Math.max(0, ...(selectedSheet.value?.rows || []).map(row => row.length)))
const notice = computed(() => {
  const value = metadata.value
  if (!value || value.kind === 'unsupported') return ''
  if (value.message) return value.message
  if (value.kind === 'document') return '文字与表格预览，排版、图片等请下载原文件查看。'
  if (value.kind === 'spreadsheet') return '工作表数据预览，图表、公式及完整排版请下载原文件查看。'
  return value.truncated ? '内容较多，仅显示部分内容，请下载原文件查看完整内容。' : ''
})

function columnName(index) {
  let name = '', value = index + 1
  while (value > 0) { value--; name = String.fromCharCode(65 + value % 26) + name; value = Math.floor(value / 26) }
  return name
}
function clearPreview() {
  ++generation; controller?.abort(); controller = null
  if (imageUrl.value) URL.revokeObjectURL(imageUrl.value)
  imageUrl.value = ''; pdfData.value = null; metadata.value = null
  loading.value = false; error.value = ''; imageZoom.value = 1; sheetIndex.value = 0; downloading.value = false
  checkingAccess = false
}
function close() { clearPreview(); emit('update:modelValue', false) }
function isCurrent(run) { return !disposed && props.modelValue && run === generation }
function showPdfError(message, data) { if (props.modelValue && data === pdfData.value) error.value = message }
function showImageError(event) { if (props.modelValue && event.target.src === imageUrl.value) error.value = '图片无法显示，请下载原文件查看。' }

async function loadPreview() {
  clearPreview()
  const target = props.target
  if (!props.modelValue || identityCancelled || !target?.previewPath || !target?.versionId || disposed) return
  const run = generation, requestController = new AbortController()
  controller = requestController; loading.value = true
  try {
    const result = await props.transport('GET', target.previewPath, undefined, { signal: requestController.signal })
    if (!isCurrent(run)) return
    if (result.version_id !== target.versionId) throw new Error('预览版本不匹配，请关闭后重新打开。')
    if (!['pdf', 'image', 'text', 'document', 'spreadsheet', 'unsupported'].includes(result.kind)) throw new Error('服务返回的预览格式无法识别，请下载原文件查看。')
    metadata.value = result
    if (['pdf', 'image'].includes(result.kind)) {
      const blob = await props.transport('GET', target.contentPath || `${target.previewPath}/content`, undefined, { signal: requestController.signal, responseType: 'blob' })
      if (!isCurrent(run)) return
      if (result.kind === 'image') imageUrl.value = URL.createObjectURL(blob)
      else {
        const bytes = await blob.arrayBuffer()
        if (isCurrent(run)) pdfData.value = new Uint8Array(bytes)
      }
    }
  } catch (err) {
    if (!isCurrent(run) || requestController.signal.aborted || err.name === 'AbortError') return
    error.value = err.response?.data?.error?.message || err.response?.data?.detail?.message || (typeof err.response?.data?.detail === 'string' ? err.response.data.detail : err.message) || '读取预览失败，请重试。'
  } finally { if (isCurrent(run)) loading.value = false }
}

async function recheckAccess() {
  if (!props.modelValue || !metadata.value || loading.value || checkingAccess || !controller) return
  const run = generation, target = props.target, activeController = controller
  checkingAccess = true
  try {
    const result = await props.transport('GET', target.previewPath, undefined, { signal: activeController.signal })
    if (isCurrent(run) && result.version_id !== target.versionId) {
      clearPreview(); error.value = '所选版本已不可用，请关闭后重新打开。'
    }
  } catch (err) {
    if (!isCurrent(run) || activeController.signal.aborted) return
    const status = err.status || err.response?.status
    if ([401, 403, 404, 410].includes(status)) {
      clearPreview(); error.value = '当前账号已无法访问此资料或版本，请关闭预览或重新核对。'
    } else ElMessage.warning('暂时无法核对资料访问权限，请稍后重试。')
  } finally { if (isCurrent(run)) checkingAccess = false }
}

async function download() {
  if (!props.target || downloading.value) return
  const run = generation, target = { ...props.target }
  downloading.value = true
  try { await props.downloadFile(target, { signal: controller?.signal }) }
  catch (err) { if (isCurrent(run) && err.name !== 'AbortError') ElMessage.error(err.message || '下载失败，请重试。') }
  finally { if (isCurrent(run)) downloading.value = false }
}

watch(() => props.identityKey, () => {
  if (props.modelValue) { identityCancelled = true; close() }
  else clearPreview()
}, { flush: 'sync' })
watch(() => [props.modelValue, props.target?.previewPath, props.target?.versionId], () => {
  clearPreview()
  if (!props.modelValue) identityCancelled = false
  else if (!identityCancelled) loadPreview()
}, { immediate: true, flush: 'sync' })
onMounted(() => window.addEventListener('focus', recheckAccess))
onBeforeUnmount(() => { disposed = true; clearPreview(); window.removeEventListener('focus', recheckAccess) })
</script>

<style scoped>
.preview-heading h2 { font-size: 19px; line-height: 1.5; margin: 0 36px 4px 0; overflow-wrap: anywhere; }
.preview-heading h2 span { color: #687583; font-weight: 500; }
.preview-heading p { color: #798596; margin: 0; font-size: 13px; overflow-wrap: anywhere; }
.preview-body { height: min(66vh, 740px); min-height: 280px; display: flex; flex-direction: column; gap: 12px; }
.preview-notice { flex-shrink: 0; }
.preview-state { flex: 1; text-align: center; align-content: center; padding: 28px; color: #657287; }
.preview-state h3 { color: #354459; font-weight: 500; }
.preview-footer, .image-toolbar { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.preview-muted, .sheet-notice { color: #798596; font-size: 13px; }
.preview-footer > div { display: flex; flex-shrink: 0; gap: 10px; }
.preview-footer .el-button + .el-button { margin-left: 0; }
.image-preview { height: 100%; min-height: 0; display: flex; flex-direction: column; gap: 12px; }
.image-toolbar { justify-content: center; color: #657287; }
.image-viewport { background: #f2f4f8; flex: 1; overflow: auto; display: flex; align-items: flex-start; justify-content: center; padding: 20px; border-radius: 8px; }
.image-viewport img { max-width: none; object-fit: contain; flex-shrink: 0; }
.text-preview { overflow: auto; white-space: pre-wrap; overflow-wrap: anywhere; line-height: 1.8; background: #f7f8fa; border-radius: 8px; padding: 24px; margin: 0; flex: 1; }
.document-preview { flex: 1; overflow: auto; border: 1px solid #e8ecf2; border-radius: 8px; padding: 24px 32px; line-height: 1.9; }
.document-preview p { white-space: pre-wrap; overflow-wrap: anywhere; margin: 0 0 16px; }
.table-scroll { overflow: auto; }
table { border-collapse: collapse; font-size: 13px; }
td, th { padding: 8px 12px; border: 1px solid #e0e5ec; text-align: left; min-width: 100px; white-space: pre-wrap; overflow-wrap: anywhere; max-width: 480px; }
.document-preview table { width: 100%; margin-bottom: 16px; }
.spreadsheet-preview { flex: 1; min-height: 0; display: flex; flex-direction: column; gap: 12px; }
.sheet-selector { display: flex; gap: 12px; align-items: center; color: #536174; font-size: 14px; }
.sheet-selector select { border: 1px solid #d8dfe9; border-radius: 6px; background: white; padding: 8px 12px; min-width: 160px; color: #354459; }
.sheet-table { flex: 1; }
.sheet-table th { color: #687583; background: #f3f5f8; font-weight: 500; min-width: 40px; position: sticky; top: 0; }
.sheet-table tbody th { position: sticky; left: 0; }
.sheet-notice { margin: 0; }
@media (max-width: 640px) { .preview-footer { align-items: flex-start; flex-direction: column; } .preview-body { height: 60vh; } .document-preview { padding: 16px; } }
</style>
