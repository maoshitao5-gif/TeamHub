import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs/promises'

const location = new URL('../src/utils/partnerMarkdown.js', import.meta.url)
let render
try {
  await fs.access(location)
  render = (await import(location.href)).renderPartnerMarkdown
} catch (error) {
  if (error.code !== 'ENOENT') throw error
  // 未接入 Markdown 前，界面的实际基线是安全的纯文本展示。
  render = text => String(text ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
}

test('伙伴回答支持标题、列表、强调和真正的表格结构', () => {
  const html = render('# 核对结果\n\n**结论**\n\n- 第一项\n- 第二项\n\n|设备|状态|\n|---|---|\n|开关|正常|')
  for (const tag of ['h1', 'strong', 'ul', 'li', 'table', 'thead', 'tbody', 'td']) assert.match(html, new RegExp(`<${tag}[ >]`))
  assert.match(html, /核对结果/)
})

test('代码块保留代码并转义标签，不能执行模型输出的HTML', () => {
  const html = render('```html\n<script>alert(1)</script>\n```\n\n<img src=x onerror=alert(1)>\n<svg onload=alert(2)>')
  assert.match(html, /<pre><code/)
  assert.match(html, /&lt;script&gt;/)
  assert.doesNotMatch(html, /<(?:script|svg|img)\b/i)
})

for (const address of ['javascript:alert(1)', 'java&#x73;cript:alert(1)', 'file:///etc/passwd', 'data:text/html,hello', 'vbscript:msgbox(1)']) {
  test(`链接不能导航到危险协议：${address}`, () => {
    const html = render(`[打开](${address})`)
    assert.doesNotMatch(html, /<a\b/)
  })
}

test('外部图片不自动加载；HTTP链接只能经用户点击打开', () => {
  const html = render('![远程图片](https://example.test/private.png)\n\n[参考](https://example.test/a?q=1&x=2)')
  assert.doesNotMatch(html, /<img\b/i)
  assert.match(html, /<a\b[^>]*href="https:\/\/example\.test/)
  assert.match(html, /rel="noopener noreferrer"/)
  assert.match(html, /target="_blank"/)
})

test('未完成流式片段也可以安全渲染，HTML和图片规则不会临时放开', () => {
  const html = render('正在生成 **初步结论\n\n<script src="https://example.test/x.js"')
  assert.match(html, /初步结论/)
  assert.doesNotMatch(html, /<script\b/i)
  assert.equal(render(null), '')
})
