# CLAUDE.md

本文件为 Claude Code (claude.ai/code) 在此仓库中工作时提供指导。

## 项目概述

TeamHub 是一个本地优先的桌面文件管理系统，支持版本追踪和标签分类。以 Electron 应用形式运行，内部封装了 Vue 3 前端和 FastAPI/Python 后端。代码注释、UI 文本和提交信息均使用中文。

## 架构

三层桌面应用：

- **Electron 外壳** (`electron/`) — 主进程管理窗口生命周期，通过 `BackendManager` 将 Python 后端作为子进程启动。使用 `contextIsolation` 配合 preload 脚本注入 API 基础地址。
- **Vue 3 前端** (`frontend/`) — 基于 Vite 构建的 SPA，使用 Element Plus 组件库、Pinia 状态管理和 Vue Router。通过 Axios REST 调用与后端通信。
- **FastAPI 后端** (`backend/app/`，入口 `main.py`) — 同步 SQLAlchemy ORM + SQLite（WAL 模式）。路由拆分为 `api/documents.py`、`api/versions.py`、`api/tags.py`、`api/settings_api.py`。

数据流：Electron 在端口 (8001-8010) 上启动 Python 后端 → 前端在 Chromium 渲染进程中加载 → Axios 调用 `http://127.0.0.1:{port}/api/*`。

### 核心数据模型关系

`Document` ←1:N→ `Version`（文档版本历史），`Document` ←M:N→ `Tag`（通过 `document_tag` 关联表）。文档有 `status` 字段（`pending`/`organized`/`missing`/`trashed`）和 `storage_mode`（`move`/`copy`/`index`）。数据库位于 `{library_path}/.teamhub/db.sqlite`。

## 开发命令

### 环境安装
```bash
pip install -r requirements.txt
npm install                    # 根目录（Electron 依赖）
cd frontend && npm install     # 前端依赖
```

### 运行（开发模式）
```bash
# 完整 Electron 应用（前端 + Electron + 后端自动启动）
npm run electron:dev

# 仅后端（Web 模式，端口 8080）
python main.py

# 仅前端（Vite 开发服务器，端口 5173）
cd frontend && npm run dev
```

### 构建（生产环境）
```bash
npm run build:all              # 后端 (PyInstaller) + 前端 (Vite) + Electron 打包
npm run backend:build          # PyInstaller → dist/teamhub-backend.exe
npm run frontend:build         # Vite → frontend/dist/
npm run electron:build         # electron-builder → dist-electron/
```

### 测试
```bash
pytest backend/tests/ -v
```

## 重要文件位置

| 用途 | 路径 |
|------|------|
| 后端入口 / FastAPI 应用 | `main.py` |
| 后端配置（Pydantic Settings） | `backend/app/config.py` |
| SQLAlchemy 数据模型 | `backend/app/models.py` |
| 数据库初始化与会话管理 | `backend/app/database.py` |
| API 路由模块 | `backend/app/api/*.py` |
| Pydantic 请求/响应模式 | `backend/app/api/schemas.py` |
| 前端入口 | `frontend/src/main.js` |
| Vue Router（5 个页面 + 初始化页） | `frontend/src/router/index.js` |
| Axios 配置与拦截器 | `frontend/src/api/request.js` |
| Pinia 状态管理 | `frontend/src/stores/` |
| Electron 主进程 | `electron/main.js` |
| 后端进程管理器 | `electron/backend-manager.js` |
| PyInstaller 打包配置 | `teamhub-backend.spec` |

## 开发约定

- **语言**：代码注释、UI 文本和日志信息使用中文，修改代码时请保持此惯例。
- **ID 生成**：使用 UUID v7（Python < 3.13.1 时回退到 UUID v4），存储为 `String(36)`。
- **时间戳**：统一使用 UTC 时区（`datetime.now(timezone.utc)`）。
- **数据库访问**：同步 SQLAlchemy 会话通过 FastAPI `Depends(get_db)` 注入。路由声明为 `async def` 但数据库调用是同步的——这是针对单用户本地场景的有意设计。
- **API 前缀**：所有接口在 `/api/` 路径下（健康检查：`/api/health`）。
- **UTF-8 中间件**：自定义 `UTF8EncodingMiddleware` 处理 Windows 编码问题——请勿移除。
- **软删除**：文档使用 `trashed_at` 时间戳标记删除，不做硬删除。
- **配置持久化**：运行时设置保存到 `config.json`（路径取决于 Electron 或独立模式，详见 `config.py`）。

## 云端同步扩展性评估

### 已具备的同步友好基础

| 维度 | 评分 | 现状 |
|------|------|------|
| UUID 标识体系 | 8/10 | UUID v7 时间可排序，v4 回退保证唯一，三张核心表主键均为 `String(36)` UUID，直接适配多设备同步 |
| UTC 时间戳 | 7/10 | `created_at`/`updated_at`/`trashed_at` 统一 UTC，支持增量同步判断；缺 `last_synced_at` |
| SHA-256 文件哈希 | 8/10 | Version 表 `sha256_hash` 字段 + `file_utils.py` 4KB 分块计算，可用于去重秒传和完整性校验；首次创建时为 NULL（后台异步） |
| 数据模型关系 | 7/10 | Document→Version(1:N)、Document↔Tag(M:N)，UUID 外键关联 + CASCADE 级联删除，每个实体可独立同步 |
| .teamhub 目录结构 | 7/10 | 隐藏目录隔离（类 `.git`），含 db.sqlite/config.json/trash/index-versions，可扩展新增 sync/ 目录 |
| 软删除机制 | 8/10 | `status: "trashed"` + `trashed_at` + 物理文件移入 `.teamhub/trash/`，云端同步删除时本地可保留恢复能力 |

### 关键缺口（必须补齐才能实现云端同步）

| 缺口 | 评分 | 影响 |
|------|------|------|
| **变更日志（ChangeLog）** | 0/10 | 无法增量推送/拉取，只能全量对比。需新增 `change_log` 表记录每次 CRUD 操作 |
| **远程同步元数据** | 0/10 | Document/Version 缺少 `remote_id`、`sync_status`、`last_synced_at` 字段，无法追踪已同步数据 |
| **冲突检测机制** | 0/10 | 假设单设备操作，无向量时钟或乐观锁，多设备并行修改会互相覆盖 |
| **设备标识** | 0/10 | 无 `device_id` 概念，同步需知道变更来源设备 |
| **并发写入控制** | 3/10 | SQLite WAL 仅提供并发读，无乐观锁字段，同步拉取可能覆盖本地未保存数据 |

### 版本管理与 Git 模式对比

| 特性 | TeamHub 当前实现 | Git 模式 |
|------|-----------------|---------|
| 存储方式 | 完整快照（每版本一个完整文件副本） | 对象存储 + pack 压缩 |
| 版本号 | 线性自增整数（v1, v2, v3） | SHA-1 哈希（内容寻址） |
| 分支 | 不支持 | 原生支持 |
| 合并 | 不支持 | 三路合并 |
| 差异计算 | 无 | diff + patch |
| 文件夹版本 | 各文件独立版本，无文件夹级快照 | tree 对象记录完整目录快照 |

### 整体结论

> 数据层基础扎实（UUID + UTC + SHA-256 + 清晰关系模型），但同步层完全空白。改造思路是在现有模型上**叠加同步层**（ChangeLog + SyncMeta + ConflictResolver），而非重构底层。

### 改造路线图

| 阶段 | 内容 | 预估工作量 |
|------|------|-----------|
| 第一步 | ChangeLog 表 + 同步元数据字段 + 设备标识 | 2-3 周 |
| 第二步 | 增量同步 API（`/api/sync/changes`、push、pull） | 2-3 周 |
| 第三步 | 冲突检测 + 冲突解决界面 | 2-3 周 |
| 第四步 | 云端服务（自建或第三方对象存储） | 3-4 周 |
| 可选 | Delta 差异存储 + 传输压缩优化 | 2-4 周 |
