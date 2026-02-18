const { app } = require('electron');
const path = require('path');
const fs = require('fs');

class PathHelper {
  constructor() {
    this.userDataPath = null;
  }

  /**
   * 获取用户数据目录路径
   * Windows: C:\Users\{用户名}\AppData\Roaming\TeamHub\
   * macOS:   ~/Library/Application Support/TeamHub/
   * Linux:   ~/.config/TeamHub/
   */
  getUserDataPath() {
    if (!this.userDataPath) {
      this.userDataPath = app.getPath('userData');
    }
    return this.userDataPath;
  }

  /**
   * 获取数据库目录路径
   */
  getDatabasePath() {
    return path.join(this.getUserDataPath(), 'database');
  }

  /**
   * 获取存储目录路径
   */
  getStoragePath() {
    return path.join(this.getUserDataPath(), 'storage');
  }

  /**
   * 获取日志目录路径
   */
  getLogsPath() {
    return path.join(this.getUserDataPath(), 'logs');
  }

  /**
   * 获取配置目录路径
   */
  getConfigPath() {
    return path.join(this.getUserDataPath(), 'config');
  }

  /**
   * 创建所有必要的目录
   */
  ensureDirectories() {
    const directories = [
      this.getUserDataPath(),
      this.getDatabasePath(),
      this.getStoragePath(),
      this.getLogsPath(),
      this.getConfigPath()
    ];

    directories.forEach(dir => {
      if (!fs.existsSync(dir)) {
        fs.mkdirSync(dir, { recursive: true });
        console.log(`[PathHelper] Created directory: ${dir}`);
      }
    });
  }

  /**
   * 获取后端可执行文件路径
   */
  getBackendExecutablePath() {
    const isDev = !app.isPackaged;

    if (isDev) {
      // 开发模式：使用 Python 直接运行
      return process.platform === 'win32' ? 'python' : 'python3';
    } else {
      // 生产模式：使用打包的可执行文件
      const exeName = process.platform === 'win32' ? 'teamhub-backend.exe' : 'teamhub-backend';
      return path.join(process.resourcesPath, 'backend', exeName);
    }
  }

  /**
   * 获取后端主脚本路径（开发模式使用）
   */
  getBackendScriptPath() {
    return path.join(__dirname, '..', '..', 'main.py');
  }

  /**
   * 检查是否为开发模式
   */
  isDevelopment() {
    return !app.isPackaged;
  }
}

module.exports = PathHelper;
