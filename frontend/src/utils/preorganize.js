export const PREORGANIZE_STATES = [
  { value: 'waiting', label: '待 AI 预整理', type: 'info' },
  { value: 'processing', label: 'AI 预整理中', type: 'primary' },
  { value: 'ready', label: 'AI 已预整理 · 待确认', type: 'success' },
  { value: 'failed', label: '预整理失败', type: 'danger' },
]

export function preorganizeState(value) {
  return PREORGANIZE_STATES.find(item => item.value === value) || PREORGANIZE_STATES[0]
}

// 仅用于界面提前说明包内内容发送范围，不用于判定实际文件类型或支持能力。
export function isArchiveName(name = '', isFolder = false) {
  return !isFolder && /\.(?:zip|7z|rar|tar|tgz|tbz2?|txz|gz|bz2|xz)$/i.test(name)
}

export function splitOrganizationName(name = '', isFolder = false) {
  if (!isFolder) {
    const compound = /\.tar\.(?:gz|bz2|xz)$/i.exec(name)
    if (compound && compound.index > 0) return { stem: name.slice(0, compound.index), extension: compound[0] }
  }
  const dot = isFolder ? -1 : name.lastIndexOf('.')
  return dot > 0 ? { stem: name.slice(0, dot), extension: name.slice(dot) } : { stem: name, extension: '' }
}

export function organizationName(stem, extension = '') {
  const name = String(stem || '').trim()
  if (!name || ['.', '..'].includes(name) || /[/\\\u0000-\u001f\u007f]/u.test(name)) throw new Error('请填写有效的资料名称，不能包含路径或控制字符')
  return extension && !name.toLowerCase().endsWith(extension.toLowerCase()) ? `${name}${extension}` : name
}

export function createReviewItem(document, detail, requestId = () => crypto.randomUUID()) {
  if (document.id !== detail.document_id) throw new Error('方案与当前资料不一致，请重新打开')
  if (detail.state !== 'ready' || !detail.proposal_id || !detail.proposal) throw new Error(detail.error || '整理方案已失效，请重新预整理')
  const proposal = detail.proposal
  const { extension } = splitOrganizationName(document.name, document.is_folder)
  const suggestedName = proposal.name || document.name
  const name = extension && suggestedName.toLowerCase().endsWith(extension.toLowerCase()) ? suggestedName.slice(0, -extension.length) : suggestedName
  return {
    document_id: document.id, proposal_id: detail.proposal_id, request_id: requestId(),
    original_name: document.name, name, extension, is_folder: Boolean(document.is_folder), object_kind: detail.object_kind || '',
    tags: [...(proposal.tags || [])], target_dir: proposal.target_dir || '',
    summary: proposal.summary || '', related_clues: [...(proposal.related_clues || [])],
    gaps: [...(proposal.gaps || detail.gaps || [])], source_version: detail.source_version,
    sources: structuredClone(detail.sources || []), progress: structuredClone(detail.progress || {}),
    selected: true, error: '', submitted: null,
  }
}

export function buildApplyItems(items) {
  return items.filter(item => item.selected !== false).map(item => {
    const tags = [...new Set(item.tags.map(tag => tag.trim()).filter(Boolean))]
    if (!tags.length) throw new Error(`「${item.original_name || item.name}」请至少添加一个标签`)
    return {
      document_id: item.document_id, proposal_id: item.proposal_id, request_id: item.request_id,
      name: organizationName(item.name, item.extension), tags, target_dir: item.target_dir || '',
    }
  })
}

export function reconcileApplyResults(items, results) {
  const byId = new Map(results.map(item => [item.document_id, item]))
  let succeeded = 0
  const remaining = items.filter(item => {
    if (item.selected === false) return true
    const result = byId.get(item.document_id)
    if (result?.ok === true) { succeeded++; return false }
    item.error = result?.error || '未取得该资料的整理结果，请核对后重试'
    return true
  })
  return { succeeded, remaining }
}

export function mergePendingSelection(previous, items) {
  const selected = new Set(previous.filter(item => item._selected).map(item => item.id))
  return items.map(item => ({ ...item, _selected: selected.has(item.id) }))
}
