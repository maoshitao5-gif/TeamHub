/**
 * TeamHub 一键启动脚本（开发模式）
 * 启动顺序：清理端口 → 本地后端 → MinIO → 云服务 → 前端 → 等 Vite 就绪 → Electron
 *
 * node dev-all.js                启动全部服务（含 MinIO + Electron 窗口）
 * node dev-all.js --no-minio     跳过 MinIO
 * node dev-all.js --no-cloud     跳过云服务
 * node dev-all.js --no-electron  不打开 Electron 窗口（浏览器调试）
 * node dev-all.js --no-front     跳过前端和 Electron（纯 API 调试）
 */

const { spawn, execSync } = require('child_process')
const http = require('http')
const path = require('path')
const fs   = require('fs')

const ROOT       = __dirname
const ARGV       = process.argv.slice(2)
const noCloud    = ARGV.includes('--no-cloud')
const noFront    = ARGV.includes('--no-front')
const noElectron = ARGV.includes('--no-electron') || noFront
const noMinio    = ARGV.includes('--no-minio')

const MINIO_EXE  = path.join(ROOT, 'tools', 'minio.exe')
const minioReady = !noMinio && fs.existsSync(MINIO_EXE)

// ── ANSI 颜色 ─────────────────────────────────────────────
const C = {
  reset:   '\x1b[0m',
  bold:    '\x1b[1m',
  cyan:    '\x1b[36m',
  yellow:  '\x1b[33m',
  green:   '\x1b[32m',
  magenta: '\x1b[35m',
  red:     '\x1b[31m',
  gray:    '\x1b[90m',
  blue:    '\x1b[34m',
}

// ── 服务定义 ──────────────────────────────────────────────
const ALL_SERVICES = [
  {
    name:       '本地后端',
    port:       8001,
    color:      C.cyan,
    cmd:        'python',
    args:       ['main.py'],
    cwd:        ROOT,
  },
  {
    name:       'MinIO  ',
    port:       9001,
    cleanPorts: [9001, 9002],           // API:9001  控制台:9002
    color:      C.magenta,
    cmd:        MINIO_EXE,
    args:       ['server', path.join(ROOT, 'minio-data'),
                 '--address', ':9001', '--console-address', ':9002'],
    cwd:        ROOT,
    env:        { MINIO_ROOT_USER: 'minioadmin', MINIO_ROOT_PASSWORD: 'minioadmin' },
    skip:       !minioReady,
  },
  {
    name:       '云  服务',
    port:       9000,
    color:      C.yellow,
    cmd:        'python',
    args:       ['main.py'],
    cwd:        path.join(ROOT, 'cloud_backend'),
    skip:       noCloud,
  },
  {
    name:       '前    端',
    port:       5173,
    color:      C.green,
    cmd:        'npm',
    args:       ['run', 'dev'],
    cwd:        path.join(ROOT, 'frontend'),
    skip:       noFront,
  },
]

const services = ALL_SERVICES.filter(s => !s.skip)

// ── 日志工具 ──────────────────────────────────────────────
function ts() {
  return `${C.gray}${new Date().toTimeString().slice(0, 8)}${C.reset}`
}
function log(label, color, msg) {
  process.stdout.write(`${ts()} ${color}[${label}]${C.reset} ${msg}\n`)
}
function logSys(msg) {
  process.stdout.write(`${ts()} ${C.blue}[启动器]${C.reset} ${msg}\n`)
}

// ── 端口清理 ──────────────────────────────────────────────
function getPidsOnPort(port) {
  try {
    const out = execSync(`netstat -ano | findstr :${port}`, {
      encoding: 'utf8',
      stdio: ['pipe', 'pipe', 'ignore'],
    })
    const pids = new Set()
    out.split('\n').forEach(line => {
      const m = line.match(/\s+(\d+)\s*$/)
      if (m) {
        const pid = parseInt(m[1], 10)
        if (pid > 0) pids.add(pid)
      }
    })
    return [...pids]
  } catch (_) {
    return []
  }
}

function killPid(pid) {
  try {
    execSync(`taskkill /PID ${pid} /F /T`, { stdio: 'ignore' })
    return true
  } catch (_) {
    return false
  }
}

function waitPortFree(port, maxMs = 3000) {
  const deadline = Date.now() + maxMs
  while (Date.now() < deadline) {
    if (getPidsOnPort(port).length === 0) return true
    Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, 200)
  }
  return getPidsOnPort(port).length === 0
}

async function clearAllPorts() {
  logSys(`${C.bold}检查端口占用...${C.reset}`)
  let anyOccupied = false

  // 收集所有需要清理的端口（含 MinIO 控制台 9002）
  const ports = [...new Set(services.flatMap(s => s.cleanPorts || [s.port]))]

  for (const port of ports) {
    const pids = getPidsOnPort(port)
    if (pids.length === 0) {
      logSys(`端口 ${C.green}${port}${C.reset} 空闲 ✓`)
      continue
    }
    anyOccupied = true
    logSys(`端口 ${C.yellow}${port}${C.reset} 被占用（PID: ${pids.join(', ')}），正在清理...`)
    const killed = pids.filter(pid => killPid(pid))
    if (killed.length > 0) {
      logSys(`端口 ${port} 已释放（终止了 PID: ${killed.join(', ')}）`)
    } else {
      logSys(`${C.red}端口 ${port} 清理失败，启动后可能报端口冲突${C.reset}`)
    }
  }

  if (anyOccupied) {
    logSys('等待系统释放端口...')
    await new Promise(r => setTimeout(r, 800))
    let allFree = true
    for (const port of ports) {
      if (!waitPortFree(port, 2000)) {
        logSys(`${C.red}警告：端口 ${port} 仍被占用${C.reset}`)
        allFree = false
      }
    }
    if (allFree) logSys(`${C.green}所有端口已就绪 ✓${C.reset}`)
  } else {
    logSys(`${C.green}所有端口均空闲 ✓${C.reset}`)
  }
  console.log()
}

// ── 等待 Vite 前端就绪 ────────────────────────────────────
function checkHttp(port) {
  return new Promise(resolve => {
    const req = http.get(`http://localhost:${port}`, { timeout: 1000 }, res => {
      resolve(res.statusCode < 500)
      res.destroy()
    })
    req.on('error',   () => resolve(false))
    req.on('timeout', () => { req.destroy(); resolve(false) })
  })
}

async function waitForVite(maxWaitMs = 60000) {
  const ports    = [5173, 5174, 5175, 5176, 5177]
  const deadline = Date.now() + maxWaitMs
  logSys('等待前端 Vite 服务就绪...')

  while (Date.now() < deadline) {
    for (const port of ports) {
      if (await checkHttp(port)) {
        logSys(`${C.green}前端已就绪 → http://localhost:${port} ✓${C.reset}`)
        return port
      }
    }
    await new Promise(r => setTimeout(r, 500))
  }

  logSys(`${C.yellow}Vite 等待超时，仍尝试启动 Electron...${C.reset}`)
  return 5173
}

// ── 启动后台服务进程 ──────────────────────────────────────
function startService(service) {
  const fullCmd = [service.cmd, ...service.args].join(' ')
  const proc = spawn(fullCmd, {
    cwd:   service.cwd,
    shell: true,
    stdio: 'pipe',
    windowsHide: true,
    env: { ...process.env, PYTHONUNBUFFERED: '1', FORCE_COLOR: '1', ...(service.env || {}) },
  })

  let outBuf = '', errBuf = ''

  proc.stdout.on('data', chunk => {
    outBuf += chunk.toString()
    const lines = outBuf.split('\n'); outBuf = lines.pop()
    lines.forEach(l => { if (l.trim()) log(service.name, service.color, l) })
  })

  proc.stderr.on('data', chunk => {
    errBuf += chunk.toString()
    const lines = errBuf.split('\n'); errBuf = lines.pop()
    lines.forEach(l => { if (l.trim()) log(service.name, service.color, l) })
  })

  proc.on('close', code => {
    const msg = code === 0
      ? `${C.gray}进程已退出${C.reset}`
      : `${C.red}进程异常退出（退出码: ${code}）${C.reset}`
    log(service.name, service.color, msg)
  })

  proc.on('error', err => {
    log(service.name, service.color, `${C.red}启动失败：${err.message}${C.reset}`)
  })

  log(service.name, service.color, `${C.gray}启动中... → http://localhost:${service.port}${C.reset}`)
  return proc
}

// ── 启动 Electron ─────────────────────────────────────────
function startElectron() {
  log('Electron', C.magenta, `${C.gray}正在打开桌面窗口...${C.reset}`)

  const proc = spawn('electron .', {
    cwd:   ROOT,
    shell: true,
    stdio: 'pipe',
    windowsHide: false,
    env: { ...process.env, FORCE_COLOR: '1' },
  })

  let outBuf = '', errBuf = ''

  proc.stdout.on('data', chunk => {
    outBuf += chunk.toString()
    const lines = outBuf.split('\n'); outBuf = lines.pop()
    lines.forEach(l => { if (l.trim()) log('Electron', C.magenta, l) })
  })

  proc.stderr.on('data', chunk => {
    errBuf += chunk.toString()
    const lines = errBuf.split('\n'); errBuf = lines.pop()
    lines.forEach(l => {
      if (l.trim() && !l.includes('DevTools') && !l.includes('Autofill')) {
        log('Electron', C.magenta, l)
      }
    })
  })

  proc.on('close', code => {
    log('Electron', C.magenta, `${C.gray}窗口已关闭（退出码: ${code}）${C.reset}`)
    logSys('Electron 已关闭，正在停止所有服务...')
    process.exit(0)
  })

  proc.on('error', err => {
    log('Electron', C.magenta, `${C.red}启动失败：${err.message}${C.reset}`)
    log('Electron', C.magenta, `${C.red}请确认已安装：npm install${C.reset}`)
  })

  return proc
}

// ── 主流程 ────────────────────────────────────────────────
async function main() {
  // 构建横幅条目
  const bannerRows = []
  for (const s of services) {
    bannerRows.push({ color: s.color, label: s.name, addr: `http://localhost:${s.port}` })
    // MinIO 额外显示控制台地址
    if (s.cleanPorts && s.cleanPorts.length > 1) {
      bannerRows.push({ color: C.gray, label: '  └控制台', addr: `http://localhost:${s.cleanPorts[1]}` })
    }
  }
  if (!noElectron) {
    bannerRows.push({ color: C.magenta, label: 'Electron ', addr: '桌面窗口' })
  }
  if (!minioReady && !noMinio) {
    bannerRows.push({ color: C.yellow, label: '⚠ MinIO  ', addr: 'tools/minio.exe 不存在，已跳过' })
  }

  console.log()
  console.log('  ╔══════════════════════════════════════════════════════╗')
  console.log(`  ║  ${C.bold}TeamHub 开发环境${C.reset}  按 Ctrl+C 停止所有服务          ║`)
  console.log('  ╠══════════════════════════════════════════════════════╣')
  bannerRows.forEach(r => {
    const line = `${r.color}${r.label}${C.reset}  →  ${r.addr}`
    // 去掉 ANSI 后算实际字符长度
    const plain = `${r.label}  →  ${r.addr}`
    const pad   = Math.max(0, 48 - plain.length)
    console.log(`  ║  ${line}${' '.repeat(pad)}║`)
  })
  console.log('  ╚══════════════════════════════════════════════════════╝')
  console.log()

  // 第一步：清理端口
  await clearAllPorts()

  // 第二步：启动后台服务（并行）
  logSys(`${C.bold}启动服务进程...${C.reset}`)
  console.log()
  const procs = services.map(s => ({ proc: startService(s), service: s }))

  // 第三步：等待 Vite 就绪后启动 Electron
  if (!noElectron) {
    waitForVite(60000).then(() => {
      const ep = startElectron()
      procs.push({ proc: ep, service: { name: 'Electron', color: C.magenta } })
    })
  }

  // Ctrl+C：停止所有进程
  function shutdown() {
    console.log('\n')
    logSys('收到停止信号，正在关闭所有服务...')
    procs.forEach(({ proc, service }) => {
      if (!proc || proc.killed) return
      try {
        spawn(`taskkill /PID ${proc.pid} /F /T`, { shell: true, stdio: 'ignore' })
        log(service.name, service.color || C.gray, `${C.gray}已停止${C.reset}`)
      } catch (_) {}
    })
    setTimeout(() => process.exit(0), 600)
  }

  process.on('SIGINT',  shutdown)
  process.on('SIGTERM', shutdown)
}

main()
