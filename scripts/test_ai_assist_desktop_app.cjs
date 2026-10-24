// 仅由 test_ai_assist_desktop.cjs 启动，使用临时 profile 和生产 file:// 页面。
const { app, BrowserWindow, ipcMain } = require('electron')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')
assert(process.env.TEAMHUB_AI_FIXTURE, '请使用隔离的 AI 验收入口')
const fixture = JSON.parse(fs.readFileSync(process.env.TEAMHUB_AI_FIXTURE, 'utf8'))
assert(fixture.temp.startsWith(path.join(os.tmpdir(), 'teamhub-ai-assist-')), '只接受隔离测试目录')
app.setPath('userData', path.join(fixture.temp, 'electron-profile'))
app.whenReady().then(async () => {
  const openedLinks = []
  ipcMain.handle('fixture-open-reference', (event, address) => {
    assert(['http:', 'https:'].includes(new URL(address).protocol))
    openedLinks.push(address)
    fs.writeFileSync(path.join(fixture.temp, 'opened-reference-links.json'), JSON.stringify(openedLinks))
  })
  const preload = path.join(fixture.temp, 'ai-preload.cjs')
  fs.writeFileSync(preload, `require('electron').contextBridge.exposeInMainWorld('electron',{apiBaseURL:${JSON.stringify(fixture.api)},aiCapability:${JSON.stringify(fixture.capability)},openExternalLink:url=>require('electron').ipcRenderer.invoke('fixture-open-reference',url)})`)
  const window = new BrowserWindow({ show: false, width: 1440, height: 1080, webPreferences: { preload, contextIsolation: true, nodeIntegration: false } })
  const source = fs.readFileSync(path.join(fixture.repo, 'electron/main.js'), 'utf8')
  const csp = source.match(/const csp = isDev[\s\S]*?: "([^"]+)";/)?.[1]
  assert(csp, '未找到生产 CSP')
  window.webContents.session.webRequest.onHeadersReceived((details, callback) => callback({ responseHeaders: { ...details.responseHeaders, 'Content-Security-Policy': [csp] } }))
  await window.loadURL('about:blank')
})
app.on('window-all-closed', () => app.quit())
