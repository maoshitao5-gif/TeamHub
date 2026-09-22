# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

TeamHub 是一个本地优先的桌面文件管理系统，支持版本追踪、标签分类和云端同步。以 Electron 应用形式运行，内部封装了 Vue 3 前端和 FastAPI/Python 后端，另有独立的云服务后端。代码注释、UI 文本和提交信息均使用中文。

## 开发命令

### 环境安装
```bash
pip install -r requirements.txt
npm install                    # 根目录（Electron 依赖）
cd frontend && npm install     # 前端依赖
cd cloud_backend && pip install -r requirements.txt  # 云服务依赖
```

### 运行（开发模式）
```bash
# 完整 Electron 应用（前端 Vite + Electron 顺序启动，含延迟等待）
npm run electron:dev

# 仅本地后端（Web 模式，端口 8001）
python main.py

# 仅前端（Vite 开发服务器，端口 5173）
cd frontend && npm run dev

# 云服务（独立进程，端口 9000）
cd cloud_backend && python main.py
```

### 构建（生产环境）
```bash
npm run build:all              # 后端 (PyInstaller) + 前端 (Vite) + Electron 打包
npm run backend:build          # PyInstaller → dist/teamhub-backend.exe
npm run frontend:build         # Vite → frontend/dist/（生成主应用 + 悬浮窗两个入口）
npm run electron:build         # electron-builder → dist-electron/
```

### 测试
```bash
pytest backend/tests/ -v               # 运行所有测试
pytest backend/tests/test_foo.py -v   # 运行单个测试文件
pytest backend/tests/ -k "test_name"  # 按名称过滤
```

## 架构

### 整体结构

四个主要层次：

1. **Electron 外壳** (`electron/`) — 主进程管理窗口生命周期，通过 `BackendManager` 将 Python 后端作为子进程启动（开发时 `python main.py`，生产时 `teamhub-backend.exe`）。使用 `contextIsolation` 配合 preload 脚本注入 API 基础地址。后端启动后每 5 秒健康检查一次，失败 3 次后自动重启（最多重启 3 次）。
2. **Vue 3 前端** (`frontend/`) — 基于 Vite 构建的 SPA，Element Plus 组件库、Pinia 状态管理（`app.js` + `auth.js` 两个 store）、Vue Router（11 个路由）。构建产物含两个 HTML 入口：主应用（`index.html`）和悬浮窗（`floating-window.html`）。另有 `frontend/src/admin/` 独立管理子应用（独立路由 `router.js`）。
3. **本地 FastAPI 后端** (`backend/app/`，入口 `main.py`，端口 8001–8010) — 同步 SQLAlchemy ORM + SQLite（WAL 模式）。通过 `httpx` 调用云服务，JWT 对前端透明。
4. **云服务** (`cloud_backend/`，入口 `cloud_backend/main.py`，端口 9000) — 独立 FastAPI 应用，管理用户/团队/工作空间/OSS 文件，生产环境可部署至阿里云。

数据流：Electron → Python 后端（:8001） ← Axios → Vue 前端；云同步时：Python 后端 → httpx → 云服务（:9000） → OSS。

`start-electron-delay.js` 负责在启动 Electron 窗口前轮询等待 Vite 服务就绪（检查 5173-5177 端口，最多 60 次 × 500ms）。

### 核心数据模型

**本地** (`backend/app/models.py`)：
- `Document`：`status`（`pending`/`organized`/`missing`/`trashed`）、`storage_mode`（`move`/`copy`/`index`）、`cloud_doc_id`（推送后回填）、`server_version`（冲突检测）、`workspace_id`
- `Version`：关联 Document，`is_current` 标记最新版本，`sha256_hash` 用于去重
- `Document` ←1:N→ `Version`，`Document` ←M:N→ `Tag`（通过 `document_tag` 关联表）
- `ChangeLog`：`pushed_at` 为 NULL 表示待推送
- 数据库位于 `{library_path}/.teamhub/db.sqlite`

**云端** (`cloud_backend/app/models.py`)：User、RefreshToken、Team、TeamMember、Workspace、CloudDocument、CloudVersion、CloudTag、CloudChangeLog、Device

### 云同步流程

- **推送**：`POST /api/sync/push-doc` → 计算 SHA-256 → 向云后端申请预签名 URL → PUT 直传 OSS → 云后端归档版本，更新 `ChangeLog.pushed_at`
- **拉取**：`POST /api/sync/pull-doc` → 下载文件到 inbox → `_place_file()` 移入目标目录 → 更新本地 DB
- **检查差异**：`POST /api/sync/check-doc` → 重新计算本地文件 SHA-256 与云端对比（始终从磁盘读取，不使用缓存 hash）
- **云端文档列表**：`GET /api/sync/cloud-docs`，可按 doc_id 获取版本和下载链接
- 每次推送使用新 UUID 作为 `version_id`，确保 OSS key 唯一，历史版本独立存储

### .teamhub/ 目录结构

```
{library_path}/.teamhub/
├── db.sqlite           # SQLite 数据库（WAL 模式）
├── config.json         # 文件库级配置 + JWT token 持久化
├── index-versions/     # 索引模式版本存储
├── pointers/           # .ptr 指针文件（SHA-256 OID，参考 Git LFS）
├── inbox/              # 云端拉取文件的临时暂存目录
└── trash/              # 回收站目录
```

### Electron IPC 通信

`electron/preload.js` 通过 `contextBridge` 暴露 `window.electron`，主要方法：
- 文件系统：`openPath`、`showItemInFolder`、`selectDirectory`、`selectFile`、`checkIsDirectory`、`listDirectoryContents`
- 云下载：`downloadUrl(url, localPath)`（Node.js https 下载预签名 URL）
- Token 安全存储：`storeToken`、`loadToken`、`clearToken`（safeStorage）
- 悬浮窗：`toggleFloatingWindow`

### 前端 API 层

| 文件 | 用途 |
|------|------|
| `api/request.js` | 本地后端 Axios 实例（`:8001`），含请求/响应拦截器 |
| `api/cloud.js` | 云服务 Axios 实例（`:9000`），JWT 自动附加 + 401 静默续期，`setCloudApiUrl()` 支持运行时切换地址 |
| `api/document.js` | 本地文档 CRUD |
| `api/tag.js` | 本地标签 CRUD（含统计） |
| `api/sync.js` | 本地后端同步端点（push-doc/pull-doc/check-doc/云端文档列表/设备/登录/工作空间） |
| `api/oss.js` | 共享操作（toggleDocSync / quickShare） |
| `api/cloudOss.js` | 通过云服务下载共享文件 |
| `api/settings.js` | 本地配置读写 |

Token 存储优先级：`localStorage`（Web / Electron 渲染进程）；`safeStorage` IPC 用于持久化跨重启。

## 重要文件位置

| 用途 | 路径 |
|------|------|
| 本地后端入口 | `main.py` |
| 云服务入口 | `cloud_backend/main.py` |
| 后端配置（Pydantic Settings） | `backend/app/config.py` |
| SQLAlchemy 数据模型（本地） | `backend/app/models.py` |
| SQLAlchemy 数据模型（云端） | `cloud_backend/app/models.py` |
| 数据库初始化与会话管理 | `backend/app/database.py` |
| 本地 API 路由模块 | `backend/app/api/`（`documents.py`、`versions.py`、`tags.py`、`settings_api.py`、`sync.py`、`share.py`） |
| 云后端 API 路由模块 | `cloud_backend/app/api/`（auth、users、teams、workspaces、documents、storage、sync、devices、sharing、backup、admin） |
| FastAPI 依赖注入（DB Session） | `backend/app/api/deps.py` |
| Pydantic 请求/响应模式 | `backend/app/api/schemas.py` |
| 云 HTTP 客户端（含 Token 刷新） | `backend/app/core/cloud_client.py` |
| 指针文件生成（SHA-256 OID） | `backend/app/core/pointer.py` |
| 文件操作工具 | `backend/app/core/file_utils.py` |
| 日志工具 | `backend/app/core/logger.py`（`get_logger(name)` 获取命名 logger） |
| Windows UTF-8 初始化 | `backend/app/core/encoding.py`（`setup_utf8_encoding()`，在 main.py 最早调用） |
| 后端其他核心工具 | `backend/app/core/`（trash_cleaner、version_cleaner、name_analyzer、validators、device、changelog、oss_client） |
| 后端中间件 | `backend/app/middleware/`（`encoding.py` UTF8EncodingMiddleware、`exception.py` 统一异常处理器） |
| 前端入口 | `frontend/src/main.js` |
| Vue Router（11 个路由） | `frontend/src/router/index.js` |
| 管理子应用 | `frontend/src/admin/`（独立 Vue 应用，含自己的 `router.js` 和 `App.vue`） |
| Pinia 全局状态（文件库） | `frontend/src/stores/app.js` |
| Pinia 认证状态 | `frontend/src/stores/auth.js` |
| 版本一致性感知 composable | `frontend/src/composables/useVersionAwareness.js`（窗口激活时触发扫描，5 分钟防抖） |
| Vite 构建配置（双入口） | `frontend/vite.config.js` |
| Electron 主进程 | `electron/main.js` |
| 后端进程管理器 | `electron/backend-manager.js` |
| Electron IPC 暴露层 | `electron/preload.js` |
| 工具函数 | `frontend/src/utils/format.js`（formatFileSize、formatDateTime）、`frontend/src/utils/fileIcons.js` |

## 开发约定

- **语言**：代码注释、UI 文本和日志信息使用中文，修改代码时请保持此惯例。
- **ID 生成**：使用 UUID v7（Python < 3.13.1 时回退到 UUID v4），存储为 `String(36)`。
- **时间戳**：统一使用 UTC 时区（`datetime.now(timezone.utc)`）。
- **数据库访问**：同步 SQLAlchemy 会话通过 FastAPI `Depends(get_db)` 注入（`deps.py`）。路由声明为 `async def` 但数据库调用是同步的——这是针对单用户本地场景的有意设计。
- **API 前缀**：本地后端所有接口在 `/api/` 路径下（健康检查：`/api/health`）；云服务同样使用 `/api/` 前缀。
- **UTF-8 中间件**：`backend/app/core/encoding.py` 的 `setup_utf8_encoding()` 在 `main.py` 最先调用；`UTF8EncodingMiddleware` 处理响应编码——请勿移除两者。
- **软删除**：文档使用 `trashed_at` 时间戳标记删除，不做物理删除；删除时同步清理所有关联 Version 文件。
- **配置持久化**：运行时设置（含 JWT token、云服务地址）保存到 `{library_path}/.teamhub/config.json`；`cloud_api_url` 支持运行时切换（前端 `setCloudApiUrl()`）。优先级：`VITE_CLOUD_API_URL` > `config.json cloud_api_url` > `http://localhost:9000`。
- **路由守卫**：`meta: { requiresCloudAuth: true }` 的路由在未登录时重定向到 `/login`。
- **路由别名**：`frontend/vite.config.js` 中 `@` 映射到 `frontend/src/`。
- **SmartSearchBar 跨页搜索**：`docSource`/`tagSource` props 为空时调本地 API；传入外部数组时（如云仓库页）从数组中过滤，不走 API。
- **httpx 代理**：`cloud_client.py` 中 `_http` 对 localhost/127.0.0.1 绕过系统代理，OSS 上传仍走系统代理。
- **OSS 安全隔离**：OSS AK/SK 仅云后端持有，本地后端通过云后端预签名 URL 直传，不接触凭证。
