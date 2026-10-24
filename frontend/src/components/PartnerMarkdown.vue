<template>
  <div class="partner-markdown-container"><div class="partner-markdown" v-html="rendered" @click="openReference" /><p v-if="linkError" class="reference-error" role="alert">{{ linkError }}</p></div>
</template>

<script setup>
import { computed, ref } from 'vue'
import { renderPartnerMarkdown } from '@/utils/partnerMarkdown'
const props = defineProps({ content: { type: String, default: '' } })
const rendered = computed(() => renderPartnerMarkdown(props.content))
const linkError = ref('')
async function openReference(event) {
  const anchor = event.target?.closest?.('a[href]')
  if (!anchor || !event.currentTarget.contains(anchor) || !window.electron?.openExternalLink) return
  event.preventDefault()
  linkError.value = ''
  try { await window.electron.openExternalLink(anchor.href) }
  catch { linkError.value = '无法打开参考链接，请复制链接后在浏览器中查看。' }
}
</script>

<style scoped>
.partner-markdown { color:#35455a; font-size:14px; line-height:1.85; overflow-wrap:anywhere; }
.partner-markdown :deep(> :first-child) { margin-top:0; }
.partner-markdown :deep(> :last-child) { margin-bottom:0; }
.partner-markdown :deep(p) { margin:0 0 12px; }
.partner-markdown :deep(h1),.partner-markdown :deep(h2),.partner-markdown :deep(h3),.partner-markdown :deep(h4) { margin:22px 0 10px; line-height:1.5; color:#2e4664; }
.partner-markdown :deep(h1) { font-size:22px; }.partner-markdown :deep(h2) { font-size:19px; }.partner-markdown :deep(h3) { font-size:16px; }.partner-markdown :deep(h4) { font-size:14px; }
.partner-markdown :deep(ul),.partner-markdown :deep(ol) { padding-left:24px; margin:10px 0 14px; }
.partner-markdown :deep(li) { margin:4px 0; }
.partner-markdown :deep(blockquote) { border-left:3px solid #c7d9f0; padding:8px 15px; margin:14px 0; color:#667b95; background:#f7faff; }
.partner-markdown :deep(table) { display:block; max-width:100%; overflow:auto; border-collapse:collapse; margin:16px 0; font-size:13px; }
.partner-markdown :deep(th),.partner-markdown :deep(td) { border:1px solid #dee7f2; padding:8px 12px; min-width:70px; text-align:left; }
.partner-markdown :deep(th) { background:#f1f6fc; font-weight:600; }
.partner-markdown :deep(code) { font-family:ui-monospace,SFMono-Regular,Consolas,monospace; padding:2px 5px; font-size:.9em; background:#f0f4f9; border-radius:4px; }
.partner-markdown :deep(pre) { max-width:100%; overflow:auto; padding:16px; border:1px solid #e3eaf3; border-radius:9px; background:#f6f8fc; line-height:1.7; }
.partner-markdown :deep(pre code) { padding:0; background:none; border-radius:0; }
.partner-markdown :deep(a) { color:#2878d4; text-decoration:underline; text-underline-offset:3px; }
.partner-markdown :deep(hr) { margin:20px 0; border:0; border-top:1px solid #e5edf5; }
.reference-error { color:#b75d51; font-size:12px; margin:8px 0; }
</style>
