/** Preview downloads retain the selected immutable version and the dialog's cancellation scope. */
export async function savePreviewDownload(transport, target, options = {}) {
  const blob = await transport('GET', target.downloadPath, undefined, { ...options, responseType: 'blob' })
  if (options.signal?.aborted) return
  const url = URL.createObjectURL(blob)
  try {
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = target.filename || target.name || 'TeamHub文件'
    document.body.append(anchor)
    anchor.click()
    anchor.remove()
  } finally { setTimeout(() => URL.revokeObjectURL(url), 1000) }
}
