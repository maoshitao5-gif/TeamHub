// 由 test_desktop_session_refresh.cjs 调用，只加载生产页面，不连接任何日常资料库。
const { app, BrowserWindow } = require('electron')
const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')
const assert = require('node:assert/strict')
assert(process.env.TEAMHUB_REFRESH_FIXTURE, '请使用隔离的 session refresh 主脚本')
const fixture = JSON.parse(fs.readFileSync(process.env.TEAMHUB_REFRESH_FIXTURE, 'utf8'))
assert(fixture.temp.startsWith(path.join(os.tmpdir(), 'teamhub-session-refresh-')), '只接受隔离测试目录')
app.setPath('userData', path.join(fixture.temp, 'electron-profile'))
app.whenReady().then(async () => {
  const preload = path.join(fixture.temp, 'refresh-preload.cjs')
  fs.writeFileSync(preload, `require('electron').contextBridge.exposeInMainWorld('electron', {apiBaseURL:${JSON.stringify(fixture.api)},aiCapability:${JSON.stringify(fixture.capability)}})`)
  const window = new BrowserWindow({ show: false, width: 1440, height: 1000, webPreferences: { preload, contextIsolation: true, nodeIntegration: false } })
  const source = fs.readFileSync(path.join(fixture.repo, 'electron/main.js'), 'utf8')
  const csp = source.match(/const csp = isDev[\s\S]*?: "([^"]+)";/)?.[1]
  assert(csp, '未找到生产 CSP')
  window.webContents.session.webRequest.onHeadersReceived((details, callback) => callback({ responseHeaders: { ...details.responseHeaders, 'Content-Security-Policy': [csp] } }))
  // 先保留空白页，让父进程装好观测和网络范围约束，再加载生产页面。
  await window.loadURL('about:blank')
})
app.on('window-all-closed', () => app.quit())
