# TeamHub 架构说明：本地与云端

> 生成日期：2026-03-16
> 覆盖内容：本地/云端隔离关系、连接机制、Push/Pull 数据流、云端管理后台、云端元数据存储

---

## 一、隔离关系：完全独立的两个服务

代码上零耦合，数据库完全分离。

```
TeamHub/
├── backend/          ← 本地后端（FastAPI，端口 8001）
│   └── app/api/        数据库：SQLite  {library}/.teamhub/db.sqlite
│
└── cloud_backend/    ← 云端后端（FastAPI，端口 9000，独立部署阿里云）
    └── app/api/        数据库：PostgreSQL（生产）/ SQLite（开发）
```

| 维度 | 本地端 | 云端 |
|------|--------|------|
| 运行位置 | 用户机器（Electron 子进程） | 阿里云服务器 |
| 数据库 | SQLite（单用户，WAL 模式） | PostgreSQL（多租户） |
| 认证 | 无认证（Device ID 标识设备） | JWT + bcrypt（多用户账号体系） |
| 文件存储 | 用户本地磁盘 | 阿里云 OSS |
| 代码耦合 | 互不 import，零耦合 | 同上 |

---

## 二、连接方式：本地后端代理中转

前端不直接调云端（同步相关），由本地后端代理：

```
前端（Vue 3）
  │
  ├── request.js ──► 本地后端 :8001   文档/版本/标签，全部本地操作
  │
  ├── cloud.js ────► 云端 :9000       登录/注册/团队，JWT 自动续期
  │
  └── sync.js ─────► 本地后端 :8001   同步 push/pull
                          │
                          │ httpx（带 JWT）
                          │ Token 过期时自动刷新，写回 config.json
                          ▼
                        云端 :9000
```

连接核心是 `backend/app/core/cloud_client.py`，封装了带 JWT 的 HTTP 请求，401 时自动用 refresh_token 换新 access_token，**JWT 管理对前端完全透明**。

```python
# cloud_client.py 核心逻辑
def cloud_req(method, url, config, timeout=30, **kwargs) -> httpx.Response:
    headers = auth_headers(config)
    resp = httpx.request(method, url, headers=headers, timeout=timeout, **kwargs)
    if resp.status_code == 401:
        new_config = refresh_token(config)   # 自动换 token
        if new_config:
            config.update(new_config)
            headers = auth_headers(config)
            resp = httpx.request(method, url, headers=headers, timeout=timeout, **kwargs)
    return resp
```

---

## 三、架构总览

```
┌─────────────────────────────────────────────────────────┐
│                  用户机器（Electron）                     │
│                                                         │
│  ┌──────────────────────────────────────────────┐      │
│  │  Vue 3 前端（Chromium 渲染进程）               │      │
│  │  cloud.js  → 直连云端（登录/注册）             │      │
│  │  request.js / sync.js → 本地后端（其余）       │      │
│  └────────────────────┬─────────────────────────┘      │
│                       │ :8001                           │
│  ┌────────────────────▼─────────────────────────┐      │
│  │  本地后端（FastAPI）                           │      │
│  │  文档/版本/标签/设置：纯本地                   │      │
│  │  sync/share：httpx 调用云端（JWT 透传）        │      │
│  └────────────────────┬─────────────────────────┘      │
│      SQLite ◄─────────┘                                 │
│    .teamhub/db.sqlite                                   │
└────────────────────────┬────────────────────────────────┘
                         │ HTTPS REST（JWT）
                         │
          ┌──────────────▼──────────────────────────┐
          │  云端服务器（阿里云）                     │
          │                                          │
          │  FastAPI :9000                           │
          │  ├── /api/auth        用户认证            │
          │  ├── /api/teams       团队管理            │
          │  ├── /api/workspaces  工作空间            │
          │  ├── /api/sync        增量同步            │
          │  ├── /api/storage     预签名 URL          │
          │  └── /api/admin       管理后台（超管）    │
          │                                          │
          │  PostgreSQL                              │
          │  CloudDocument / CloudVersion            │
          │  CloudChangeLog / User / Team / ...      │
          └──────────────────────┬───────────────────┘
                                 │ 预签名 URL（直传）
                          ┌──────▼───────┐
                          │ 阿里云 OSS   │
                          │  文件存储    │
                          └──────────────┘

          ┌──────────────────────────────────────────┐
          │  admin.html（独立 SPA，超管专用）          │
          │  → /api/admin/* → 云端（超管权限）         │
          └──────────────────────────────────────────┘
```

---

## 四、Push / Pull 完整数据流

### 4.1 Push（本地 → 云端）

```
① 用户操作文档
   └─► 本地 Document 表 + ChangeLog 表（pushed_at = NULL）

② 前端点"推送"→ POST /api/sync/push → 本地后端

③ 本地后端聚合待推送 ChangeLog（最多 200 条）

④ 文件上传（仅有新版本时）
   ┌─────────────────────────────────────────────┐
   │  本地后端                                    │
   │  POST /api/storage/presign-upload → 云端    │
   │      ← 返回 presign_url + oss_key           │
   │  httpx.PUT(presign_url, 文件内容)            │
   │      直传 OSS，云后端不经手文件内容           │
   │  POST /api/storage/confirm-upload → 云端    │
   └─────────────────────────────────────────────┘

⑤ POST /api/workspaces/{ws_id}/sync/push → 云端
   Body:
   {
     "device_id": "uuid",
     "changes": [
       { "entity_type": "document", "entity_id": "...",
         "operation": "create|update|trash|restore|delete",
         "payload": { "name": "...", "status": "...", ... },
         "client_version": 0  // 冲突检测游标
       },
       { "entity_type": "version", "entity_id": "...",
         "operation": "create",
         "payload": { "oss_key": "workspaces/...", "sha256_hash": "...", ... }
       },
       { "entity_type": "tag", ... }
     ]
   }
   云端写 CloudDocument / CloudVersion / CloudChangeLog（sequence 自增）
   冲突检测：client_version < server_version → conflict

⑥ 本地后端处理响应
   ChangeLog.pushed_at = now
   Document.sync_status = "synced" | "conflict"
   Document.server_version = 云端版本号
```

### 4.2 Pull（云端 → 本地）

```
① GET /api/workspaces/{ws_id}/sync/pull?since_sequence=100 → 云端
   云端返回 sequence > 100 的所有 CloudChangeLog 条目

② 本地后端逐条处理（过滤本设备产生的变更）
   - document create/update  → 写本地 Document 表
   - version create          → 下载文件到 .teamhub/inbox/
       POST /api/storage/presign-download → 获取限时 URL（1小时有效）
       Electron IPC downloadUrl → Node.js 下载到本地 inbox
   - 冲突判断：本地有修改（sync_status=local）且云端也有变更 → conflict

③ 更新游标：config.json 写入 { cursors: { ws_id: 102 } }

④ 前端刷新 → 待整理页出现新文档，inbox 文件可见
```

### 4.3 数据库变化对照

| 阶段 | 本地 SQLite | 云端 PostgreSQL |
|------|-------------|-----------------|
| 用户操作后 | `ChangeLog(pushed_at=NULL)` | — |
| push 完成 | `ChangeLog.pushed_at=now`<br>`Document.sync_status=synced`<br>`Document.server_version=1` | `CloudDocument(server_version=1)`<br>`CloudVersion(oss_key=...)`<br>`CloudChangeLog(sequence=1)` |
| pull 完成 | 新 `Document(status=pending)`<br>`Version(file_path=inbox/...)` | 无变化 |

---

## 五、云端管理后台

### 5.1 入口与结构

独立于主应用的 Vue SPA，Vite 多入口构建：

```
frontend/
├── index.html     ← 主应用（普通用户）
└── admin.html     ← 管理后台（超级管理员）
    └── src/admin/
        ├── main.js
        ├── router.js       /login | /dashboard | /users | /teams
        ├── api/admin.js    调用云端 /api/admin/* 接口
        └── pages/
            ├── LoginPage.vue       邮箱密码登录
            ├── DashboardPage.vue   系统概况
            ├── UsersPage.vue       用户管理
            └── TeamsPage.vue       团队管理
```

### 5.2 管理后台 API（均需 `is_superuser = True`）

| 端点 | 功能 |
|------|------|
| `GET  /api/admin/stats` | 系统概况：用户数、团队数、工作空间数、设备数 |
| `GET  /api/admin/users` | 用户列表（分页 + 关键词搜索） |
| `PUT  /api/admin/users/{id}` | 修改用户（激活/禁用/设为超管） |
| `DELETE /api/admin/users/{id}` | 删除用户（自动撤销 refresh_token） |
| `GET  /api/admin/teams` | 团队列表（含配额/用量） |
| `PUT  /api/admin/teams/{id}` | 调整存储配额 |
| `DELETE /api/admin/teams/{id}` | 解散团队（cascade delete） |

### 5.3 认证方式

```
登录    → POST /api/auth/login → { access_token, refresh_token }
存储    → localStorage: admin_token, admin_user
请求    → Authorization: Bearer {admin_token}
401时   → 清除 token，跳转 /login（无自动续期，需手动重新登录）
```

### 5.4 主应用 vs 管理后台对比

| 维度 | 主应用（index.html） | 管理后台（admin.html） |
|------|------------------|-------------------|
| 目标用户 | 普通用户 | 超级管理员 |
| API 目标 | 本地后端 :8001 | 云端 :9000 |
| 路由 | /library, /settings, ... | /dashboard, /users, /teams |
| Token 存储 | Electron safeStorage | localStorage |
| 权限模型 | 多租户（工作空间隔离） | 全局超管 |
| Token 续期 | 自动静默续期 | 无，手动重新登录 |

---

## 六、云端元数据存储

### 6.1 核心原则

**文件内容存 OSS，元数据存数据库，两者通过 `oss_key` 关联。**

```
文件本体   → 阿里云 OSS
             key：workspaces/{ws_id}/files/{doc_id}/{ver_id}/{filename}

文件元数据 → 云端 PostgreSQL
             CloudVersion.oss_key 指向 OSS 中的文件
```

### 6.2 各表存储的元数据

#### `cloud_documents`（文档级）

| 字段 | 说明 |
|------|------|
| `id` | 与本地 `Document.id` **完全相同的 UUID**，无需映射表 |
| `workspace_id` | 所属工作空间 |
| `name` | 文档名称 |
| `description` | 描述/备注 |
| `is_folder` | 是否为文件夹文档 |
| `status` | pending / organized / missing / trashed |
| `file_count` | 文件数量 |
| `total_size` | 总大小（字节） |
| `server_version` | 单调递增，用于冲突检测 |
| `created_at` / `updated_at` / `trashed_at` | 时间戳 |

#### `cloud_versions`（版本级，最关键）

| 字段 | 说明 |
|------|------|
| `id` | 与本地 `Version.id` 相同的 UUID |
| `document_id` | 所属文档 |
| `version_number` | 版本号（1, 2, 3...） |
| `sha256_hash` | 文件内容哈希，用于完整性校验 |
| **`oss_key`** | **文件在 OSS 的完整路径**（下载时使用） |
| `file_size` | 文件大小（字节） |
| `original_filename` | 原始文件名 |
| `relative_path` | 文件夹文档内部路径（如 `合同/采购合同.pdf`） |
| `note` | 版本备注 |
| `is_current` | 是否为当前版本 |
| `created_at` | 版本创建时间 |

#### `cloud_change_logs`（增量同步的核心）

| 字段 | 说明 |
|------|------|
| `sequence_number` | 工作空间内单调递增序号（pull 游标） |
| `entity_type` | document / version / tag |
| `entity_id` | 对应实体的 UUID |
| `operation` | create / update / delete / trash / restore |
| **`payload`** | **JSON 快照**，包含变更后的完整关键字段 |
| `device_id` | 发起此变更的设备 ID |
| `created_at` | 变更时间 |

#### 其余元数据表

| 表 | 存储内容 |
|---|------|
| `cloud_tags` | 标签名称、颜色，工作空间内唯一 |
| `cloud_document_tags` | 文档-标签多对多关联 |
| `devices` | 设备 ID、名称、平台、每个工作空间的 sync 游标 |
| `users` | 用户邮箱、密码哈希、激活状态、超管标记 |
| `teams` | 团队名、slug、存储配额/用量 |
| `workspaces` | 工作空间名称、所属团队 |
| `refresh_tokens` | Token 哈希、过期时间、吊销时间 |

### 6.3 `payload` 快照设计

`cloud_change_logs.payload` 存储变更发生时的**完整字段 JSON 快照**，而非 diff：

```json
{
  "name": "2026年采购合同",
  "status": "organized",
  "is_folder": false,
  "file_count": 1,
  "total_size": 245760,
  "sha256_hash": "a3f5c2...",
  "oss_key": "workspaces/ws-abc/files/doc-xyz/ver-001/合同.pdf",
  "version_number": 2,
  "original_filename": "合同.pdf"
}
```

**好处**：pull 时无需再查主表，直接从 change_log 重建本地状态。

### 6.4 元数据与 OSS 的关联链

```
① 上传时（push）
   本地 Version.file_path（磁盘文件）
       ↓ presign-upload 获取限时上传 URL
   直传 OSS: workspaces/ws-id/files/doc-id/ver-id/file.pdf
       ↓ confirm-upload 写回
   CloudVersion.oss_key = "workspaces/.../file.pdf"
   CloudVersion.sha256_hash = "abc123..."     ← 完整性凭证

② 下载时（pull）
   CloudVersion.oss_key
       ↓ presign-download（云端持有 OSS 凭证，生成限时 URL）
   presign_url（有效期 1 小时）
       ↓ Electron Node.js 下载
   本地 .teamhub/inbox/ver-id/file.pdf

③ 增量同步（游标机制）
   设备记录：{ ws_id: last_sequence = 100 }
       ↓ GET /sync/pull?since_sequence=100
   CloudChangeLog WHERE sequence_number > 100
   payload 包含完整快照，无需再查主表
```

### 6.5 云端存什么 vs 不存什么

| 数据类型 | 云端是否存储 | 存储位置 |
|---------|------------|---------|
| 文档元数据（名称/状态/大小） | ✅ 存储 | `cloud_documents` |
| 版本元数据（版本号/哈希/文件名） | ✅ 存储 | `cloud_versions` |
| 标签数据 | ✅ 存储 | `cloud_tags` |
| 变更历史（完整快照） | ✅ 存储 | `cloud_change_logs` |
| 文件内容本体 | ❌ 不存 | OSS（`oss_key` 指向） |
| 本地存储路径（storage_path） | ❌ 不存 | 仅本地 SQLite |
| 本地收纳方式（move/copy/index） | ❌ 不存 | 仅本地 SQLite |
| 本地原始路径（original_path） | ❌ 不存 | 仅本地 SQLite |

---

## 七、关键文件索引

| 功能 | 文件路径 |
|------|---------|
| 本地-云端 HTTP 工具 | `backend/app/core/cloud_client.py` |
| 本地同步 API | `backend/app/api/sync.py` |
| 本地共享 API | `backend/app/api/share.py` |
| 云端同步核心 | `cloud_backend/app/api/sync.py` |
| 云端 OSS 预签名 | `cloud_backend/app/api/storage.py` |
| 云端管理后台 API | `cloud_backend/app/api/admin.py` |
| 云端数据模型 | `cloud_backend/app/models.py` |
| 前端同步 API 封装 | `frontend/src/api/sync.js` |
| 前端云服务 API 封装 | `frontend/src/api/cloud.js` |
| 前端认证状态管理 | `frontend/src/stores/auth.js` |
| 管理后台前端入口 | `frontend/admin.html` + `frontend/src/admin/` |
