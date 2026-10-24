const { spawn, execFile } = require('child_process');
const { randomUUID, randomBytes } = require('crypto');
const axios = require('axios');
const path = require('path');
const fs = require('fs');
const localAxios = axios.create({ proxy: false });
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));

function groupExists(pid) {
  try { process.kill(-pid, 0); return true; }
  catch (error) { if (error.code === 'ESRCH') return false; throw error; }
}

// POSIX 每次启动建立专属进程组，覆盖冻结程序的引导进程与实际服务子进程。
// Windows 只按仍由本管理器持有的根 PID 结束进程树，不查询或终止端口占用者。
async function signalOwnedBackend(owned, signal) {
  if (!owned.child.pid) return;
  if (owned.processGroup) {
    try { process.kill(-owned.child.pid, signal); }
    catch (error) { if (error.code !== 'ESRCH') throw error; }
    return;
  }
  if (owned.child.exitCode !== null || owned.child.signalCode !== null) return;
  await new Promise((resolve, reject) => {
    const command = path.join(process.env.SystemRoot || 'C:\\Windows', 'System32', 'taskkill.exe');
    execFile(command, ['/PID', String(owned.child.pid), '/T', '/F'], { windowsHide: true, timeout: 3000 }, error => {
      if (error && owned.child.exitCode === null && owned.child.signalCode === null) reject(error);
      else resolve();
    });
  });
}

// 仅管理自身创建的子进程；启动、重启、停止串行，不根据占用端口杀进程。
class BackendManager {
  constructor(port, userDataPath, pathHelper) {
    // 桌面会话内保持稳定，后端自动重启沿用；不同桌面实例互不通用。
    this.aiCapability = randomBytes(32).toString('hex');
    Object.assign(this, { port, userDataPath, pathHelper, backendProcess: null, ownedBackend: null,
      healthCheckInterval: null, restartTimer: null, restartCount: 0, maxRestarts: 3,
      healthCheckIntervalMs: 5000, healthCheckFailures: 0, maxHealthCheckFailures: 3,
      isShuttingDown: false, starting: null, restarting: null, stopping: null });
  }

  start() {
    if (this.isShuttingDown) return Promise.reject(new Error('Backend manager is shutting down'));
    if (this.starting) return this.starting;
    this.starting = (async () => {
      if (this.stopping) await this.stopping;
      if (this.isShuttingDown) throw new Error('Backend startup cancelled');
      if (this.backendProcess) {
        if (this.backendProcess.exitCode === null && this.backendProcess.signalCode === null) return;
        await this.stop(false);
      }
      await this._start();
    })().finally(() => { this.starting = null; });
    return this.starting;
  }

  async _start() {
    const isDev = this.pathHelper.isDevelopment();
    const command = this.pathHelper.getBackendExecutablePath();
    const args = isDev ? [this.pathHelper.getBackendScriptPath()] : [];
    fs.mkdirSync(path.join(this.userDataPath, 'logs'), { recursive: true });
    const log = fs.createWriteStream(path.join(this.userDataPath, 'logs', 'backend.log'), { flags: 'a' });
    log.on('error', error => console.error('[BackendManager] Log error:', error.message));
    const instance = randomUUID();
    console.log(`[BackendManager] Starting ${command} on ${this.port}`);
    const child = spawn(command, args, {
      env: { ...process.env, ELECTRON_APP: 'true', USER_DATA_DIR: this.userDataPath,
        PORT: String(this.port), TEAMHUB_INSTANCE_ID: instance, PYTHONIOENCODING: 'utf-8',
        TEAMHUB_AI_CAPABILITY: this.aiCapability },
      cwd: isDev ? path.join(__dirname, '..') : undefined,
      detached: process.platform !== 'win32', windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'],
    });
    this.backendProcess = child;
    const owned = { child, processGroup: process.platform !== 'win32', closed: false };
    this.ownedBackend = owned;
    this.instance = instance;
    let failure = null, ready = false;
    for (const stream of [child.stdout, child.stderr]) stream.on('data', data => { if (!log.writableEnded) log.write(data); });
    child.once('error', error => { failure = error; });
    child.once('exit', (code, signal) => {
      failure = new Error(`Backend exited (${code}, ${signal})`);
      // exit 仅说明引导进程结束；实际服务可能仍占用端口，交由 stop 清理完整进程组。
      if (ready && !this.isShuttingDown && !this.stopping) this.scheduleRestart();
    });
    child.once('close', () => { owned.closed = true; log.end(); });
    try {
      const deadline = Date.now() + 30000;
      while (Date.now() < deadline) {
        if (failure) throw failure;
        if (this.isShuttingDown) throw new Error('Backend startup cancelled');
        try {
          const response = await localAxios.get(this.getBackendURL() + '/api/health', { timeout: 1000 });
          if (response.status === 200 && response.data.instance_id === instance) {
            ready = true;
            this.startHealthCheck();
            if (typeof this.onReady === 'function') this.onReady();
            console.log('[BackendManager] Backend ready');
            return;
          }
        } catch { /* 启动期间允许健康端点尚不可达。 */ }
        await pause(200);
      }
      throw new Error('Backend startup timed out or health endpoint belongs to another process');
    } catch (error) {
      await this.stop(false);
      throw error;
    }
  }

  scheduleRestart() {
    if (this.isShuttingDown || this.restartTimer || this.restarting) return;
    if (this.restartCount >= this.maxRestarts) {
      clearInterval(this.healthCheckInterval);
      this.healthCheckInterval = null;
      console.error('[BackendManager] Restart limit reached; restart the application after checking logs');
      return;
    }
    this.restartTimer = setTimeout(() => {
      this.restartTimer = null;
      this.restart().catch(error => {
        console.error('[BackendManager] Restart failed:', error.message);
        this.scheduleRestart();
      });
    }, 1000);
  }

  startHealthCheck() {
    clearInterval(this.healthCheckInterval);
    this.healthCheckFailures = 0;
    let checking = false;
    this.healthCheckInterval = setInterval(async () => {
      if (checking || this.isShuttingDown || this.restarting) return;
      checking = true;
      try {
        const response = await localAxios.get(this.getBackendURL() + '/api/health', { timeout: 1500 });
        if (response.data.instance_id !== this.instance) throw new Error('Backend identity mismatch');
        this.healthCheckFailures = 0;
      } catch {
        if (++this.healthCheckFailures >= this.maxHealthCheckFailures) this.scheduleRestart();
      } finally { checking = false; }
    }, this.healthCheckIntervalMs);
  }

  restart() {
    if (this.restarting) return this.restarting;
    if (this.isShuttingDown) return Promise.resolve();
    this.restarting = (async () => {
      this.restartCount++;
      await this.stop(false);
      if (!this.isShuttingDown) await this.start();
    })().finally(() => { this.restarting = null; });
    return this.restarting;
  }

  stop(isShutdown = true) {
    if (isShutdown) this.isShuttingDown = true;
    clearTimeout(this.restartTimer); this.restartTimer = null;
    clearInterval(this.healthCheckInterval); this.healthCheckInterval = null;
    if (this.stopping) return this.stopping;
    const owned = this.ownedBackend;
    if (!owned) return Promise.resolve();
    const alive = () => owned.processGroup && owned.child.pid
      ? groupExists(owned.child.pid) : !owned.closed;
    const waitForExit = async duration => {
      const deadline = Date.now() + duration;
      while (alive()) {
        if (Date.now() >= deadline) return false;
        await pause(40);
      }
      return true;
    };
    this.stopping = (async () => {
      await signalOwnedBackend(owned, 'SIGTERM');
      if (!await waitForExit(5000)) {
        await signalOwnedBackend(owned, 'SIGKILL');
        if (!await waitForExit(3000)) throw new Error('Owned backend process tree did not exit after SIGKILL');
      }
      if (this.ownedBackend === owned) {
        this.ownedBackend = null;
        this.backendProcess = null;
      }
    })().finally(() => { this.stopping = null; });
    return this.stopping;
  }

  getBackendURL() { return `http://127.0.0.1:${this.port}`; }
}
module.exports = BackendManager;
