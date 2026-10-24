/** 将检索来源与覆盖情况转为用户可判断的说明；内容始终按纯文本渲染。 */
export function searchMatchView(match) {
  const passages = match?.passages
  if (!Array.isArray(passages) || !passages.length) return null
  const passage = passages.find(item => item?.source_kind === 'content') || passages[0]
  if (!passage) return null
  const place = passage.location || {}
  const location = passage.label || [
    place.member_path,
    place.sheet && `${place.sheet}${place.cell ? ` · ${place.cell}` : ''}`,
    place.page && `第 ${place.page} 页`,
    place.paragraph && `第 ${place.paragraph} 段`,
    place.line && `第 ${place.line} 行`,
  ].filter(Boolean).join(' · ')
  const source = ({ content: '原文命中', card: 'AI 资料卡线索', note: '人工补充线索',
    ai: 'AI 检索线索', metadata: '名称或标签命中', name: '名称命中', tag: '标签命中',
    description: '资料说明命中' })[passage.source_kind] || '检索线索'
  const state = match.coverage?.state
  const incomplete = (state && state !== 'ready') || match.coverage?.complete === false || match.coverage?.gaps?.length > 0
  const coverage = incomplete
    ? (['queued', 'processing'].includes(state)
      ? '内容索引正在建立，尚未覆盖的内容可能遗漏。'
      : '部分内容尚未建立索引，未命中不代表资料中没有相关内容。') : ''
  return { source, location: String(location).slice(0, 240),
    excerpt: String(passage.excerpt || '').slice(0, 360), coverage }
}
