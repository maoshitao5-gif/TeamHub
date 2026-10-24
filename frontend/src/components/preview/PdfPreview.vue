<template>
  <div class="pdf-preview" data-testid="pdf-preview">
    <div class="pdf-toolbar">
      <div class="toolbar-group">
        <el-button size="small" data-testid="pdf-prev" :disabled="!pdf || pageNumber <= 1" @click="pageNumber--">上一页</el-button>
        <span data-testid="pdf-page-status">第 {{ pageNumber }} / {{ pageCount || '—' }} 页</span>
        <el-button size="small" data-testid="pdf-next" :disabled="!pdf || pageNumber >= pageCount" @click="pageNumber++">下一页</el-button>
      </div>
      <div class="toolbar-group">
        <el-button size="small" data-testid="pdf-zoom-out" aria-label="缩小 PDF" :disabled="!pdf || zoom <= 0.5" @click="changeZoom(-0.25)">−</el-button>
        <span data-testid="pdf-zoom-status">{{ Math.round(zoom * 100) }}%</span>
        <el-button size="small" data-testid="pdf-zoom-in" aria-label="放大 PDF" :disabled="!pdf || zoom >= 2" @click="changeZoom(0.25)">＋</el-button>
      </div>
    </div>
    <div ref="viewport" v-loading="loading" class="pdf-viewport" aria-label="PDF 页面">
      <canvas ref="canvas" data-testid="pdf-preview-canvas" :aria-label="`PDF 第 ${pageNumber} 页`" />
    </div>
    <p v-if="error" role="alert" class="pdf-error">{{ error }}</p>
  </div>
</template>

<script setup>
import { nextTick, onBeforeUnmount, onMounted, ref, shallowRef, watch } from 'vue'
import { AnnotationMode, GlobalWorkerOptions, getDocument } from 'pdfjs-dist/build/pdf.mjs'
import workerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?url'

const props = defineProps({ data: { type: Uint8Array, required: true } })
const emit = defineEmits(['error'])
const pdf = shallowRef(null), canvas = ref(null), viewport = ref(null)
const pageNumber = ref(1), pageCount = ref(0), zoom = ref(1), loading = ref(true), error = ref('')
let loadingTask = null, renderTask = null, renderGeneration = 0, disposed = false

GlobalWorkerOptions.workerSrc = new URL(workerUrl, document.baseURI).href

function changeZoom(delta) { zoom.value = Math.max(0.5, Math.min(2, zoom.value + delta)) }

async function renderPage() {
  const generation = ++renderGeneration
  renderTask?.cancel()
  renderTask = null
  if (!pdf.value || !canvas.value || disposed) return
  loading.value = true
  try {
    const page = await pdf.value.getPage(pageNumber.value)
    if (disposed || generation !== renderGeneration) return
    const natural = page.getViewport({ scale: 1 })
    const fitWidth = Math.max(240, (viewport.value?.clientWidth || 900) - 48)
    const pixelRatio = Math.min(window.devicePixelRatio || 1, 2)
    const scale = Math.min(
      Math.min(fitWidth / natural.width, 1.5) * zoom.value,
      8192 / (natural.width * pixelRatio), 8192 / (natural.height * pixelRatio),
      Math.sqrt(16 * 1024 * 1024 / (natural.width * natural.height)) / pixelRatio,
    )
    const pageViewport = page.getViewport({ scale })
    canvas.value.width = Math.ceil(pageViewport.width * pixelRatio)
    canvas.value.height = Math.ceil(pageViewport.height * pixelRatio)
    canvas.value.style.width = `${Math.ceil(pageViewport.width)}px`
    canvas.value.style.height = `${Math.ceil(pageViewport.height)}px`
    renderTask = page.render({
      canvas: canvas.value, viewport: pageViewport,
      transform: pixelRatio === 1 ? undefined : [pixelRatio, 0, 0, pixelRatio, 0, 0],
      annotationMode: AnnotationMode.DISABLE,
    })
    await renderTask.promise
  } catch (err) {
    if (disposed || generation !== renderGeneration || err.name === 'RenderingCancelledException') return
    error.value = 'PDF 页面无法显示，请重试或下载原文件查看。'
    emit('error', error.value, props.data)
  } finally { if (!disposed && generation === renderGeneration) loading.value = false }
}

onMounted(async () => {
  try {
    loadingTask = getDocument({
      data: props.data, isEvalSupported: false, enableXfa: false,
      cMapUrl: new URL('assets/pdfjs/cmaps/', document.baseURI).href, cMapPacked: true,
      standardFontDataUrl: new URL('assets/pdfjs/standard_fonts/', document.baseURI).href,
      useWasm: false, useSystemFonts: true, maxImageSize: 32 * 1024 * 1024,
      canvasMaxAreaInBytes: 64 * 1024 * 1024,
    })
    const result = await loadingTask.promise
    if (disposed) { await result.destroy(); return }
    pdf.value = result; pageCount.value = result.numPages
    await nextTick()
    await renderPage()
  } catch (err) {
    if (disposed) return
    error.value = err.name === 'PasswordException'
      ? '此 PDF 需要密码，请下载原文件查看。'
      : 'PDF 无法显示，请重试或下载原文件查看。'
    loading.value = false
    emit('error', error.value, props.data)
  }
})
watch([pageNumber, zoom], renderPage)
onBeforeUnmount(() => {
  disposed = true; ++renderGeneration
  renderTask?.cancel(); renderTask = null
  loadingTask?.destroy().catch(() => {})
  pdf.value = null
  if (canvas.value) { canvas.value.width = 0; canvas.value.height = 0 }
})
</script>

<style scoped>
.pdf-preview { display: flex; flex-direction: column; min-height: 0; height: 100%; }
.pdf-toolbar, .toolbar-group { display: flex; align-items: center; gap: 12px; }
.pdf-toolbar { justify-content: space-between; padding: 0 0 12px; flex-wrap: wrap; color: #536174; }
.toolbar-group span { min-width: 56px; text-align: center; font-size: 13px; }
.pdf-viewport { flex: 1; min-height: 260px; overflow: auto; padding: 24px; background: #edf0f5; text-align: center; border-radius: 8px; }
canvas { display: inline-block; background: white; box-shadow: 0 2px 10px #18263a18; }
.pdf-error { color: #b44242; }
</style>
