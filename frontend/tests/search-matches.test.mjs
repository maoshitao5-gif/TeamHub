import test from 'node:test'
import assert from 'node:assert/strict'
import { searchMatchView } from '../src/utils/searchMatch.js'

test('命中优先展示可定位原文，AI概括不会被展示为原文', () => {
  const view = searchMatchView({ passages: [
    { source_kind: 'card', excerpt: '推测用途', location: null },
    { source_kind: 'content', excerpt: '5053压力异常处理记录', location: { page: 80 }, label: '第 80 页' },
  ], coverage: { state: 'partial' } })
  assert.equal(view.source, '原文命中')
  assert.equal(view.location, '第 80 页')
  assert.match(view.excerpt, /5053/)
  assert.match(view.coverage, /尚未/)
})

test('仅AI卡片线索命中时明确标注派生线索和缺口', () => {
  const view = searchMatchView({ passages: [{ source_kind: 'card', excerpt: '可用于检查气室压力', location: null }],
    coverage: { state: 'failed' } })
  assert.equal(view.source, 'AI 资料卡线索')
  assert.equal(view.location, '')
  assert.match(view.coverage, /尚未/)
})

test('不提供命中时隐藏说明，片段为空时不虚构原文', () => {
  assert.equal(searchMatchView(null), null)
  assert.equal(searchMatchView({ passages: [] }), null)
})

test('已提取但尚未完成发布时仍展示缺口，不单凭ready宣称覆盖完整', () => {
  const view = searchMatchView({ passages: [{ source_kind: 'content', excerpt: '原文' }],
    coverage: { state: 'ready', complete: false, gaps: [] } })
  assert.match(view.coverage, /尚未/)
})

test('表格保留工作表及单元格定位；内容只作为文本传给界面', () => {
  const view = searchMatchView({ passages: [{ source_kind: 'content',
    excerpt: '<img src=x onerror=alert(1)>', location: { sheet: '巡检记录', cell: 'F9' } }],
    coverage: { state: 'ready' } })
  assert.match(view.location, /巡检记录.*F9/)
  assert.equal(view.excerpt, '<img src=x onerror=alert(1)>')
  assert.equal(view.coverage, '')
})
