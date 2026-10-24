export function normalizeSuggestedTags(values = []) {
  return [...new Set(values.filter(value => typeof value === 'string').map(value => value.trim()).filter(Boolean))]
}

export function appendSelectedTags(existing, candidates, selected) {
  const allowed = new Set(normalizeSuggestedTags(candidates))
  return normalizeSuggestedTags([...existing, ...selected.filter(value => allowed.has(value))])
}

export function buildAISettingsUpdate(form, saved, clearKey = false) {
  const base_url = form.base_url.trim(), model = form.model.trim()
  if (!base_url || !model) throw new Error('请填写 AI 服务地址和模型名称')
  if (!clearKey && saved.has_api_key && base_url !== saved.base_url && !form.api_key.trim()) {
    throw new Error('更换 AI 服务地址时，请重新输入该服务的密钥')
  }
  const payload = { base_url, model }
  if (clearKey) payload.clear_api_key = true
  else if (form.api_key.trim()) payload.api_key = form.api_key.trim()
  return payload
}

// 不包含资料内容或凭据，只用于拒绝已关闭或已切换上下文的异步结果。
export function createAIRequestScope() {
  let generation = 0
  return {
    begin(documentId, libraryPath) { return { generation: ++generation, documentId, libraryPath } },
    invalidate() { generation++ },
    isCurrent(token, documentId, libraryPath) {
      return token?.generation === generation && token.documentId === documentId && token.libraryPath === libraryPath
    },
  }
}
