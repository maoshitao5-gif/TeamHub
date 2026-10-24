// 使用桌面自带的 Node 运行；实际 GUI 进程必须清除 Node 模式。
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const http = require('node:http');
const { execFileSync, spawnSync } = require('node:child_process');

function readState(file) {
  try { return JSON.parse(fs.readFileSync(file, 'utf8')); }
  catch { return null; }
}

function processMatches(pid, binary, parentPid) {
  if (!Number.isInteger(pid) || pid <= 1) return false;
  try {
    const options = { encoding: 'utf8', env: { ...process.env, LC_ALL: 'en_US.UTF-8' } };
    const command = execFileSync('/bin/ps', ['-p', String(pid), '-o', 'command='], options).trim();
    if (command !== binary && !command.startsWith(binary + ' ')) return false;
    if (parentPid !== undefined) {
      const parent = execFileSync('/bin/ps', ['-p', String(pid), '-o', 'ppid='], options).trim();
      if (Number(parent) !== parentPid) return false;
    }
    return true;
  } catch { return false; }
}

function stateMatches(state, appPath, userData) {
  return state && state.executable === path.join(appPath, 'Contents/MacOS/TeamHub') &&
    state.userData === userData && Number.isInteger(state.port) && state.port >= 8001 && state.port <= 8010 &&
    typeof state.instanceId === 'string' && state.instanceId.length > 0 &&
    processMatches(state.pid, state.executable) &&
    processMatches(state.backendPid, path.join(appPath, 'Contents/Resources/backend/teamhub-backend'), state.pid);
}

function healthy(state) {
  return new Promise(resolve => {
    let request, response, settled = false;
    const finish = result => {
      if (settled) return;
      settled = true;
      clearTimeout(deadline);
      resolve(result);
    };
    const deadline = setTimeout(() => {
      request?.destroy();
      response?.destroy();
      finish(false);
    }, 1000);
    request = http.get({ hostname: '127.0.0.1', port: state.port, path: '/api/health', timeout: 1000 }, incoming => {
      response = incoming;
      let body = '';
      response.on('data', chunk => {
        body += chunk;
        if (body.length > 65536) { response.destroy(); finish(false); }
      });
      response.on('end', () => {
        try { finish(response.statusCode === 200 && JSON.parse(body).instance_id === state.instanceId); }
        catch { finish(false); }
      });
      response.on('error', () => finish(false));
      response.on('aborted', () => finish(false));
    });
    request.on('timeout', () => request.destroy());
    request.on('error', () => finish(false));
  });
}

async function launch(appPath, logPath, timeoutMs = 40000) {
  appPath = path.resolve(appPath);
  logPath = path.resolve(logPath);
  const userData = process.env.TEAMHUB_USER_DATA_DIR
    ? path.resolve(process.env.TEAMHUB_USER_DATA_DIR)
    : path.join(os.homedir(), 'Library/Application Support/teamhub-desktop');
  const stateFile = path.join(userData, 'desktop-state.json');
  const binary = path.join(appPath, 'Contents/MacOS/TeamHub');
  fs.accessSync(binary, fs.constants.X_OK);
  fs.mkdirSync(path.dirname(logPath), { recursive: true });
  const env = { ...process.env };
  delete env.ELECTRON_RUN_AS_NODE;
  const previous = readState(stateFile);
  // LaunchServices otherwise reuses any running instance of the same bundle,
  // including an instance belonging to a different personal profile.
  const sameProfileRunning = previous?.executable === binary && previous.userData === userData &&
    processMatches(previous.pid, binary);
  const args = ['--stdout', logPath, '--stderr', logPath];
  if (!sameProfileRunning) args.push('-n');
  args.push(appPath);
  const opened = spawnSync('/usr/bin/open', args, { env, encoding: 'utf8' });
  if (opened.error) throw opened.error;
  if (opened.status !== 0) throw new Error(opened.stderr.trim() || 'macOS 未能打开 TeamHub');
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const state = readState(stateFile);
    if (state?.executable === binary && state.userData === userData &&
        state.status === 'error' && processMatches(state.pid, binary)) {
      throw new Error(state.error || 'TeamHub 启动失败');
    }
    if (state?.status === 'ready' && stateMatches(state, appPath, userData) && await healthy(state)) {
      return state;
    }
    await new Promise(resolve => setTimeout(resolve, 200));
  }
  throw new Error(`TeamHub 未在 ${timeoutMs / 1000} 秒内完成启动。请查看 ${logPath} 和 ${userData}/logs/backend.log`);
}

if (require.main === module) {
  launch(process.argv[2], process.argv[3]).then(state => {
    console.log(`TeamHub 桌面与个人服务已就绪：http://127.0.0.1:${state.port}`);
  }).catch(error => { console.error(error.message); process.exitCode = 1; });
}

module.exports = { readState, processMatches, stateMatches, healthy, launch };
