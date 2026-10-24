<template>
  <el-dialog :model-value="modelValue" @update:model-value="close" title="确认 AI 预整理方案"
    width="780px" top="5vh" class="preorganize-review" data-testid="preorganize-review-dialog"
    :close-on-click-modal="false" :close-on-press-escape="!applying" :show-close="!applying">
    <p class="review-intro">AI 已准备好名称、标签和分类。可在这里调整；点击完成整理后，资料才进入文档库。</p>
    <div v-if="loading" class="review-loading" role="status">正在读取已保存的方案…</div>
    <el-alert v-if="error" :title="error" type="error" :closable="false" class="review-alert" />
    <div v-for="item in items" :key="item.document_id" class="review-item" :data-testid="`preorganize-item-${item.document_id}`">
      <div class="review-item-heading">
        <el-checkbox v-model="item.selected" :disabled="applying || item.uncertain" :aria-label="`采用 ${item.original_name}`" />
        <div><strong>{{ item.original_name }}</strong><span v-if="item.source_version">基于已保存版本 v{{ item.source_version.number }}</span></div>
      </div>
      <p v-if="item.is_folder" class="review-summary">这是整套资料的整理方案。确认后整个文件夹进入文档库，内部文件名和目录结构保持不变。</p>
      <p v-else-if="item.object_kind === 'archive'" class="review-summary">这是整个压缩包的整理方案。确认后压缩包整体进入文档库，保留压缩格式、内部文件名和目录结构。</p>
      <p v-if="item.summary" class="review-summary"><strong>整理说明</strong><br>{{ item.summary }}</p>
      <details class="review-card" @toggle="toggleCard(item.document_id, $event)">
        <summary>查看已保存的 AI 资料卡</summary>
        <DocumentUnderstandingCard v-if="expandedCards.includes(item.document_id)" :visible="modelValue" compact :document="{ id: item.document_id, is_folder: item.is_folder, object_kind: item.object_kind, status: 'pending', current_version: item.source_version }" />
      </details>
      <el-alert v-if="item.error" :title="item.error" type="error" :closable="false" class="review-alert" />
      <el-alert v-if="item.uncertain" title="上次提交结果尚未确认。请点击下方按钮核对原请求，核对前保留已提交内容。" type="warning" :closable="false" class="review-alert" />
      <el-form label-position="top" :disabled="applying || item.uncertain || !item.selected">
        <el-form-item label="资料名称">
          <el-input v-model="item.name" :data-testid="`preorganize-name-${item.document_id}`" :aria-label="`${item.original_name} 的资料名称`">
            <template v-if="item.extension" #append>{{ item.extension }}</template>
          </el-input>
        </el-form-item>
        <el-form-item label="查找标签" required>
          <OrganizeTags v-model="item.tags" :tags="tags" />
        </el-form-item>
        <OrganizeLocation v-model="item.target_dir" />
      </el-form>
      <div v-if="item.related_clues.length" class="review-clues"><span>关联线索</span>{{ item.related_clues.join(' · ') }}</div>
      <div v-if="item.gaps.length" class="review-gaps"><strong>请留意</strong><ul><li v-for="gap in item.gaps" :key="gap">{{ gap }}</li></ul></div>
      <details v-if="item.sources.length" class="review-sources"><summary>查看建议依据（{{ item.sources.length }} 处）</summary>
        <div v-for="(source, index) in item.sources" :key="source.id || index">
          <strong>{{ source.label || source.location_label || source.id || `来源 ${index + 1}` }}</strong>
          <p>{{ source.text || source.excerpt || source.quote || '' }}</p>
        </div>
      </details>
    </div>
    <div v-for="failure in loadFailures" :key="failure.id" class="review-load-failure"><strong>{{ failure.name }}</strong><p>{{ failure.error }}</p></div>
    <template #footer>
      <div class="review-footer">
        <span v-if="items.length">{{ selectedCount }} 项将进入文档库 · 分类位置可留空</span>
        <el-button :disabled="applying" @click="close(false)">稍后处理</el-button>
        <el-button type="primary" :loading="applying" :disabled="loading || selectedCount === 0" data-testid="preorganize-apply" @click="apply">
          {{ hasUncertain ? '核对并完成整理' : `完成 ${selectedCount} 项整理` }}
        </el-button>
      </div>
    </template>
  </el-dialog>
</template>

<script setup>
import { computed, onUnmounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { useAppStore } from '@/stores/app'
import { getPreorganizeDocument, applyPreorganize } from '@/api/preorganize'
import { createReviewItem, buildApplyItems, reconcileApplyResults } from '@/utils/preorganize'
import OrganizeTags from '@/components/OrganizeTags.vue'
import OrganizeLocation from '@/components/OrganizeLocation.vue'
import DocumentUnderstandingCard from '@/components/DocumentUnderstandingCard.vue'

const props = defineProps({
  modelValue: Boolean, documents: { type: Array, default: () => [] },
  libraryToken: { type: String, default: '' }, tags: { type: Array, default: () => [] },
})
const emit = defineEmits(['update:modelValue', 'applied'])
const appStore = useAppStore()
const items = ref([]), loadFailures = ref([]), error = ref(''), loading = ref(false), applying = ref(false)
const expandedCards = ref([])
function toggleCard(id, event) { expandedCards.value = event.target.open ? [...new Set([...expandedCards.value, id])] : expandedCards.value.filter(value => value !== id) }
const selectedCount = computed(() => items.value.filter(item => item.selected).length)
const hasUncertain = computed(() => items.value.some(item => item.uncertain))
let generation = 0, controller, sessionToken = ''
function invalidate() {
  generation++; controller?.abort(); controller = null
  items.value = []; loadFailures.value = []; error.value = ''; loading.value = false; applying.value = false; sessionToken = ''
  expandedCards.value = []
}
function close(value) { if (applying.value) return; if (!value) { invalidate(); emit('update:modelValue', false) } }
async function load() {
  invalidate()
  if (!props.modelValue || !props.libraryToken) return
  const current = generation
  sessionToken = props.libraryToken
  const documents = props.documents.map(doc => ({ id: doc.id, name: doc.name, is_folder: doc.is_folder }))
  controller = new AbortController()
  loading.value = true
  const outcomes = await Promise.allSettled(documents.map(async doc => createReviewItem(doc, await getPreorganizeDocument(doc.id, controller.signal))))
  if (current !== generation) return
  items.value = outcomes.filter(item => item.status === 'fulfilled').map(item => item.value)
  loadFailures.value = outcomes.flatMap((item, index) => item.status === 'rejected' ? [{ ...documents[index], error: item.reason?.message || '无法读取方案，请稍后重试' }] : [])
  loading.value = false
}
async function apply() {
  if (applying.value || loading.value || !selectedCount.value || !sessionToken) return
  const current = generation
  let payload
  try {
    payload = buildApplyItems(items.value).map(item => {
      const existing = items.value.find(row => row.document_id === item.document_id)
      return existing.uncertain ? { ...existing.submitted, tags: [...existing.submitted.tags] } : item
    })
  } catch (e) { error.value = e.message; return }
  const selected = new Set(payload.map(item => item.document_id))
  for (const item of items.value) if (selected.has(item.document_id)) {
    const submitted = payload.find(value => value.document_id === item.document_id)
    item.submitted = { ...submitted, tags: [...submitted.tags] }
  }
  applying.value = true; error.value = ''
  try {
    const result = await applyPreorganize({ library_token: sessionToken, items: payload }, controller.signal)
    if (current !== generation) return
    const { succeeded, remaining } = reconcileApplyResults(items.value, result.items || [])
    const outcomes = new Map((result.items || []).map(item => [item.document_id, item]))
    for (const item of remaining) if (selected.has(item.document_id)) {
      item.uncertain = !outcomes.has(item.document_id)
      if (!item.uncertain) { item.submitted = null; item.request_id = crypto.randomUUID() }
    }
    items.value = remaining
    if (succeeded) { ElMessage.success(`已完成 ${succeeded} 项整理`); emit('applied', succeeded) }
    if (!items.value.length && !loadFailures.value.length) emit('update:modelValue', false)
  } catch (e) {
    if (current !== generation) return
    error.value = e.message || '整理请求未完成，请核对后重试'
    const definitive = e.response?.status >= 400 && e.response?.status < 500
    for (const item of items.value) if (selected.has(item.document_id)) item.uncertain = !definitive
  } finally { if (current === generation) applying.value = false }
}
watch(() => props.modelValue, visible => { if (visible) load(); else invalidate() }, { immediate: true })
watch(() => [appStore.libraryPath, props.libraryToken], () => { invalidate(); emit('update:modelValue', false) }, { flush: 'sync' })
onUnmounted(invalidate)
</script>

<style scoped>
.review-intro { margin: 0 0 18px; color: #64748b; font-size: 13px; line-height: 1.8; }
.review-loading { padding: 32px; text-align: center; color: #64748b; }
.review-item { padding: 20px; border: 1px solid #e3e9f0; border-radius: 12px; background: #fff; margin-bottom: 16px; }
.review-item-heading { display: flex; gap: 10px; align-items: flex-start; }
.review-item-heading strong { display: block; color: #263c52; overflow-wrap: anywhere; line-height: 1.6; }
.review-item-heading span { display: block; margin-top: 4px; font-size: 12px; color: #7c8b9b; }
.review-summary { color: #536477; font-size: 13px; line-height: 1.8; margin: 12px 0 18px; white-space: pre-wrap; overflow-wrap: anywhere; }
.review-card { margin-bottom: 16px; font-size: 12px; color: #2d6073; }.review-card > summary { cursor: pointer; }
.review-alert { margin-bottom: 14px; }
.review-clues, .review-gaps, .review-sources { margin-top: 14px; padding-top: 12px; border-top: 1px solid #edf1f5; font-size: 12px; line-height: 1.8; overflow-wrap: anywhere; }
.review-clues { color: #64748b; }.review-clues span { margin-right: 10px; font-weight: 600; }
.review-gaps { color: #94651f; }.review-gaps ul { padding-left: 20px; margin: 4px 0 0; }
.review-sources { color: #63758a; }.review-sources summary { cursor: pointer; }.review-sources p { white-space: pre-wrap; }
.review-load-failure { padding: 16px; background: #fff5f4; border-radius: 8px; color: #a84c43; margin-bottom: 12px; }
.review-footer { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }.review-footer span { margin-right: auto; color: #7b8798; font-size: 12px; }
.review-footer :deep(.el-button + .el-button) { margin-left: 0; }
:global(.preorganize-review) { max-width: calc(100vw - 32px); max-height: 90vh; display: flex; flex-direction: column; }
:global(.preorganize-review .el-dialog__body) { overflow-y: auto; min-height: 0; }
</style>
