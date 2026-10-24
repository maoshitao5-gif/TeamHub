import test from 'node:test'
import assert from 'node:assert/strict'

const utils = () => import('../src/utils/preorganize.js')

test('预整理名称表单保留文件扩展名，文件夹和点文件不误拆分', async () => {
  const { splitOrganizationName, organizationName } = await utils()
  assert.deepEqual(splitOrganizationName('设备报告.v2.pdf', false), { stem: '设备报告.v2', extension: '.pdf' })
  assert.deepEqual(splitOrganizationName('项目.v2', true), { stem: '项目.v2', extension: '' })
  assert.deepEqual(splitOrganizationName('.env', false), { stem: '.env', extension: '' })
  assert.equal(organizationName('设备报告', '.pdf'), '设备报告.pdf')
  assert.equal(organizationName('设备报告.pdf', '.pdf'), '设备报告.pdf')
  assert.throws(() => organizationName('../设备', '.pdf'), /名称/)
  assert.throws(() => organizationName('', '.pdf'), /名称/)
})

test('压缩资料改名完整保留复合扩展名，类型以后台识别结果为准', async () => {
  const { splitOrganizationName, createReviewItem, buildApplyItems } = await utils()
  for (const extension of ['.tar.gz', '.tar.bz2', '.tar.xz', '.TAR.GZ']) {
    assert.deepEqual(splitOrganizationName(`工程资料${extension}`), { stem: '工程资料', extension })
    const item = createReviewItem({ id: 'a1', name: `旧资料${extension}` }, {
      document_id: 'a1', object_kind: 'archive', state: 'ready', proposal_id: 'p1',
      proposal: { name: `项目验收${extension}`, tags: ['验收'] },
    }, () => 'request-a')
    assert.equal(item.object_kind, 'archive')
    assert.equal(item.name, '项目验收')
    assert.equal(buildApplyItems([item])[0].name, `项目验收${extension}`)
  }
  const ordinary = createReviewItem({ id: 't1', name: '名字.zip', object_kind: 'archive' }, {
    document_id: 't1', object_kind: 'text', state: 'ready', proposal_id: 'p1', proposal: { tags: ['说明'] },
  })
  assert.equal(ordinary.object_kind, 'text')
})

test('压缩包名称仅为授权范围提示提供线索，文件夹与普通文件不误判', async () => {
  const { isArchiveName } = await utils()
  for (const extension of ['.zip', '.7z', '.rar', '.tar', '.tgz', '.tbz', '.tbz2', '.txz', '.gz', '.bz2', '.xz', '.TAR.GZ']) {
    assert.equal(isArchiveName(`资料${extension}`), true)
  }
  assert.equal(isArchiveName('打包说明.txt'), false)
  assert.equal(isArchiveName('资料.zip', true), false)
  assert.equal(isArchiveName(), false)
})

test('采用方案使用独立快照，规范化标签，不会修改模型建议或资料对象', async () => {
  const { createReviewItem, buildApplyItems } = await utils()
  const doc = { id: 'd1', name: '原名.txt', tags: [{ name: '原标签' }] }
  const detail = { document_id: 'd1', state: 'ready', proposal_id: 'p1', proposal: { name: '建议.txt', tags: [' 标签 ', '标签'], target_dir: '', summary: '说明', gaps: [] } }
  const item = createReviewItem(doc, detail, () => 'request-1')
  item.name = '人工修正'
  item.tags.push('项目')
  assert.deepEqual(detail.proposal.tags, [' 标签 ', '标签'])
  assert.equal(doc.name, '原名.txt')
  assert.deepEqual(buildApplyItems([item]), [{ document_id: 'd1', proposal_id: 'p1', request_id: 'request-1', name: '人工修正.txt', tags: ['标签', '项目'], target_dir: '' }])
  item.tags = []
  assert.throws(() => buildApplyItems([item]), /至少添加一个标签/)
  assert.throws(() => createReviewItem(doc, { ...detail, document_id: 'other' }), /不一致/)
  assert.throws(() => createReviewItem(doc, { ...detail, state: 'waiting' }), /失效/)
})

test('批次部分成功只移除成功项，失败项保留人工输入；缺失结果不伪报成功', async () => {
  const { reconcileApplyResults } = await utils()
  const items = [{ document_id: 'd1', name: '已编辑一' }, { document_id: 'd2', name: '已编辑二' }, { document_id: 'd3', name: '已编辑三' }]
  const result = reconcileApplyResults(items, [{ document_id: 'd1', ok: true }, { document_id: 'd2', ok: false, error: '版本已更新' }])
  assert.equal(result.succeeded, 1)
  assert.equal(result.remaining.length, 2)
  assert.equal(result.remaining[0].name, '已编辑二')
  assert.equal(result.remaining[0].error, '版本已更新')
  assert.match(result.remaining[1].error, /未取得/)
})

test('轮询列表保留仍存在的选择，不携带旧内容与旧方案', async () => {
  const { mergePendingSelection } = await utils()
  const before = [{ id: 'd1', name: '旧名', _selected: true }, { id: 'd2', _selected: true }]
  const next = mergePendingSelection(before, [{ id: 'd1', name: '新名' }, { id: 'd3' }])
  assert.deepEqual(next, [{ id: 'd1', name: '新名', _selected: true }, { id: 'd3', _selected: false }])
})
