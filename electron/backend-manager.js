const { spawn } = require('child_process');
const axios = require('axios');
const path = require('path');
const fs = require('fs');

// 本地请求不走系统代理
const localAxios = axios.create({ proxy: false });

class BackendManager {
  constructor(port, userDataPath, pathHelper) {
    this.port = port;
    this.userDataPath = userDataPath;
    this.pathHelper = pathHelper;
    this.backendProcess = null;
    this.healthCheckInterval = null;
    this.restartCount = 0;
    this.maxRestarts = 3;
    this.healthCheckIntervalMs = 5000; // 5 秒
    this.healthCheckFailures = 0;
    this.maxHealthCheckFailures = 3;
    this.isShuttingDown = false;
    this.logStream = null;
  }

  /**
   * 启动后端进程
   */
  async start() {
    try {
      console.log('[BackendManager] Starting backend process...');

      // 创建日志文件流
      const logFile = path.join(this.userDataPath, 'logs', 'backend.log');
      this.logStream = fs.createWriteStream(logFile, { flags: 'a' });

      // 确定要执行的命令和参数
      const isDev = this.pathHelper.isDevelopment();
      let command, args;

      if (isDev) {
        // 开发模式：使用 Python 直接运行
        command = this.pathHelper.getBackendExecutablePath();
        args = [this.pathHelper.getBackendScriptPath()];
        console.log('[BackendManager] Running in development mode');
      } else {
        // 生产模式：使用打包的可执行文件
        command = this.pathHelper.getBackendExecutablePath();
        args = [];
        console.log('[BackendManager] Running in production mode');
      }

      // 设置环境变量
      const env = {
        ...process.env,
        ELECTRON_APP: 'true',
        USER_DATA_DIR: this.userDataPath,
        PORT: this.port.toString(),
        PYTHONIOENCODING: 'utf-8',
        PYTHONLEGACYWINDOWSSTDIO: '0'
      };

      console.log(`[BackendManager] Command: ${command} ${args.join(' ')}`);
      console.log(`[BackendManager] Port: ${this.port}`);
      console.log(`[BackendManager] User data dir: ${this.userDataPath}`);

      // 启动进程
      this.backendProcess = spawn(command, args, {
        env,
        cwd: isDev ? path.join(__dirname, '..') : undefined,
        windowsHide: true, // Windows 下隐藏控制台窗口
        stdio: ['ignore', 'pipe', 'pipe']
      });

      // 设置输出流编码为 UTF-8
      this.backendProcess.stdout.setEncoding('utf8');
      this.backendProcess.stderr.setEncoding('utf8');

      // 监听标准输出
      this.backendProcess.stdout.on('data', (data) => {
        const message = data.toString();
        console.log(`[Backend] ${message.trim()}`);
        if (this.logStream) {
          this.logStream.write(`[${new Date().toISOString()}] ${message}`);
        }
      });

      // 监听标准错误
      this.backendProcess.stderr.on('data', (data) => {
        const message = data.toString();
        console.error(`[Backend Error] ${message.trim()}`);
        if (this.logStream) {
          this.logStream.write(`[${new Date().toISOString()}] ERROR: ${message}`);
        }
      });

      // 监听进程退出
      this.backendProcess.on('exit', (code, signal) => {
        console.log(`[BackendManager] Backend process exited with code ${code}, signal ${signal}`);

        if (!this.isShuttingDown && this.restartCount < this.maxRestarts) {
          console.log(`[BackendManager] Attempting to restart backend (attempt ${this.restartCount + 1}/${this.maxRestarts})`);
          this.restartCount++;
          setTimeout(() => this.start(), 2000); // 2 秒后重启
        } else if (this.restartCount >= this.maxRestarts) {
          console.error('[BackendManager] Max restart attempts reached. Backend will not restart.');
        }
      });

      // 监听进程错误
      this.backendProcess.on('error', (err) => {
        console.error('[BackendManager] Failed to start backend process:', err);
      });

      // 等待后端启动
      await this.waitForBackend();

      // 启动健康检查
      this.startHealthCheck();

      console.log('[BackendManager] Backend started successfully');
    } catch (error) {
      console.error('[BackendManager] Error starting backend:', error);
      throw error;
    }
  }

  /**
   * 等待后端服务启动
   */
  async waitForBackend(timeout = 30000) {
    const startTime = Date.now();
    const checkInterval = 500;

    while (Date.now() - startTime < timeout) {
      try {
        const response = await localAxios.get(`http://127.0.0.1:${this.port}/api/health`, {
          timeout: 1000
        });

        if (response.status === 200) {
          console.log('[BackendManager] Backend is ready');
          return true;
        }
      } catch (error) {
        // 继续等待
      }

      await new Promise(resolve => setTimeout(resolve, checkInterval));
    }

    throw new Error('Backend failed to start within timeout period');
  }

  /**
   * 启动健康检查
   */
  startHealthCheck() {
    if (this.healthCheckInterval) {
      clearInterval(this.healthCheckInterval);
    }

    this.healthCheckInterval = setInterval(async () => {
      try {
        const response = await localAxios.get(`http://127.0.0.1:${this.port}/api/health`, {
          timeout: 3000
        });

        if (response.status === 200) {
          // 健康检查成功，重置失败计数
          this.healthCheckFailures = 0;
        } else {
          this.handleHealthCheckFailure();
        }
      } catch (error) {
        this.handleHealthCheckFailure();
      }
    }, this.healthCheckIntervalMs);
  }

  /**
   * 处理健康检查失败
   */
  handleHealthCheckFailure() {
    this.healthCheckFailures++;
    console.warn(`[BackendManager] Health check failed (${this.healthCheckFailures}/${this.maxHealthCheckFailures})`);

    if (this.healthCheckFailures >= this.maxHealthCheckFailures) {
      console.error('[BackendManager] Backend appears to be unresponsive. Attempting restart...');
      this.restart();
    }
  }

  /**
   * 重启后端
   */
  async restart() {
    console.log('[BackendManager] Restarting backend...');
    await this.stop(false);
    this.healthCheckFailures = 0;
    await this.start();
  }

  /**
   * 停止后端进程
   */
  async stop(isShutdown = true) {
    this.isShuttingDown = isShutdown;

    console.log('[BackendManager] Stopping backend...');

    // 停止健康检查
    if (this.healthCheckInterval) {
      clearInterval(this.healthCheckInterval);
      this.healthCheckInterval = null;
    }

    // 关闭日志流
    if (this.logStream) {
      this.logStream.end();
      this.logStream = null;
    }

    // 停止后端进程
    if (this.backendProcess) {
      return new Promise((resolve) => {
        const killTimeout = setTimeout(() => {
          console.warn('[BackendManager] Backend did not exit gracefully, forcing kill...');
          if (this.backendProcess) {
            this.backendProcess.kill('SIGKILL');
          }
          resolve();
        }, 5000); // 5 秒超时

        this.backendProcess.once('exit', () => {
          clearTimeout(killTimeout);
          console.log('[BackendManager] Backend stopped');
          this.backendProcess = null;
          resolve();
        });

        // 发送终止信号
        this.backendProcess.kill('SIGTERM');
      });
    }
  }

  /**
   * 获取后端 URL
   */
  getBackendURL() {
    return `http://127.0.0.1:${this.port}`;
  }
}

module.exports = BackendManager;
