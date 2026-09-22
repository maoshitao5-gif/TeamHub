# TeamHub 云端部署指引

> 适用版本：TeamHub v1.0
> 更新日期：2026-03-16

本文档覆盖两种场景：
- **场景 A**：本机全栈联调（本地后端 + 云后端 + MinIO，无需服务器）
- **场景 B**：生产部署到阿里云 ECS + OSS

---

## 目录

1. [前置准备](#1-前置准备)
2. [场景 A：本机全栈联调](#2-场景-a本机全栈联调)
3. [场景 B：部署到阿里云](#3-场景-b部署到阿里云)
4. [首次初始化（两种场景通用）](#4-首次初始化两种场景通用)
5. [本地桌面端连接云端](#5-本地桌面端连接云端)
6. [功能验证清单](#6-功能验证清单)
7. [常见问题排查](#7-常见问题排查)

---

## 1. 前置准备

### 1.1 本机环境要求

| 工具 | 版本 | 说明 |
|------|------|------|
| Python | 3.11+ | 云后端运行环境 |
| pip | 最新 | Python 包管理 |
| Docker Desktop | 最新 | 场景 A 需要（运行 MinIO） |
| Node.js | 18+ | 前端开发服务器 |

检查已安装版本：
```bash
python --version
docker --version
node --version
```

### 1.2 项目目录说明

```
TeamHub/
├── main.py                  ← 本地后端入口（端口 8001）
├── backend/                 ← 本地后端代码
├── cloud_backend/           ← 云后端代码（本文档的主角）
│   ├── main.py              ← 云后端入口（端口 9000）
│   ├── .env.dev             ← 本地开发配置（场景 A 使用）
│   ├── .env.production      ← 生产配置模板（场景 B 使用）
│   ├── create_superuser.py  ← 创建管理员账号工具
│   ├── docker-compose.yml   ← 本地开发 Docker 环境
│   └── Dockerfile           ← 生产镜像构建文件
├── frontend/                ← Vue 3 前端
│   └── .env.development     ← 前端开发配置（VITE_CLOUD_API_URL）
└── dev-all.js               ← 一键启动所有服务（场景 A）
```

---

## 2. 场景 A：本机全栈联调

本机同时运行：本地后端（:8001）、云后端（:9000）、MinIO（:9001）、前端（:5173）。

### 2.1 安装云后端依赖

```bash
cd cloud_backend
pip install -r requirements.txt
```

> 安装时间较长（含 psycopg2-binary、boto3），请耐心等待。

### 2.2 配置开发环境变量

检查或创建 `cloud_backend/.env.dev`，内容如下：

```ini
# 数据库（开发使用 SQLite，无需安装 PostgreSQL）
DATABASE_URL=sqlite:///./cloud_dev.db

# JWT（开发环境可用此默认值，生产必须替换）
JWT_SECRET_KEY=dev-secret-key-for-local-testing-only-32chars
JWT_ACCESS_TOKEN_EXPIRE_MINUTES=60
JWT_REFRESH_TOKEN_EXPIRE_DAYS=30

# OSS（指向本机 MinIO）
OSS_ENDPOINT=http://localhost:9001
OSS_ACCESS_KEY_ID=minioadmin
OSS_ACCESS_KEY_SECRET=minioadmin
OSS_BUCKET_NAME=teamhub-dev
OSS_PREFIX=teamhub-cloud

# CORS（允许前端 Vite dev server 访问）
ALLOWED_ORIGINS=http://localhost:5173,http://127.0.0.1:5173,http://localhost:5174

# 端口
PORT=9000
```

### 2.3 启动 MinIO（对象存储）

**方式一：用项目内的 Docker Compose 启动 MinIO（推荐）**

```bash
cd cloud_backend
docker-compose up -d minio
```

启动后：
- MinIO S3 API：`http://localhost:9001`
- MinIO Web 控制台：`http://localhost:9002`（用户名/密码：`minioadmin` / `minioadmin`）

**方式二：直接运行 MinIO 可执行文件**

如果已有 MinIO.exe（项目根目录有 `启动MinIO.bat`），直接双击即可。

验证 MinIO 是否启动：
```bash
curl http://localhost:9001/minio/health/live
# 返回 200 即成功
```

### 2.4 启动云后端

```bash
cd cloud_backend
python main.py
```

看到以下输出即成功：
```
TeamHub 云服务启动中...
TeamHub 云服务已启动，监听端口 9000
OSS Bucket 初始化完成
INFO:     Uvicorn running on http://0.0.0.0:9000
```

验证：
```bash
curl http://localhost:9000/api/health
# {"status":"ok","service":"teamhub-cloud"}
```

### 2.5 启动本地后端和前端

**新开一个终端**，启动本地后端：
```bash
cd TeamHub
python main.py
```

**再新开一个终端**，启动前端：
```bash
cd TeamHub/frontend
npm run dev
```

前端启动后访问：`http://localhost:5173`

### 2.6 一键启动（可选）

项目根目录有 `dev-all.js`，可同时启动所有服务：
```bash
cd TeamHub
node dev-all.js
```

---

## 3. 场景 B：部署到阿里云

### 3.1 阿里云资源准备

在阿里云控制台完成以下操作：

#### 3.1.1 创建 ECS 实例
- **推荐配置**：2 核 4G 内存，Ubuntu 22.04 LTS
- **安全组**：开放以下入站规则

| 端口 | 协议 | 用途 |
|------|------|------|
| 22 | TCP | SSH 管理 |
| 80 | TCP | HTTP（Nginx 反代，可选） |
| 443 | TCP | HTTPS（Nginx 反代，可选） |
| 9000 | TCP | 云后端 API（若不用 Nginx 反代则开放） |

#### 3.1.2 创建 OSS Bucket
1. 进入「对象存储 OSS」控制台
2. 创建 Bucket：
   - **名称**：如 `teamhub-prod`（全局唯一）
   - **地域**：与 ECS 同地域（减少延迟）
   - **读写权限**：私有（Private）
3. 在 RAM 控制台创建 AccessKey：
   - 建议创建子账号，仅授予 `AliyunOSSFullAccess`
   - 记录 `AccessKey ID` 和 `AccessKey Secret`

#### 3.1.3 确认 OSS Endpoint

| 地域 | Endpoint（内网，ECS 同地域优先） |
|------|------|
| 华东1（杭州） | `https://oss-cn-hangzhou-internal.aliyuncs.com` |
| 华东2（上海） | `https://oss-cn-shanghai-internal.aliyuncs.com` |
| 华南1（深圳） | `https://oss-cn-shenzhen-internal.aliyuncs.com` |
| 华北2（北京） | `https://oss-cn-beijing-internal.aliyuncs.com` |

> ECS 访问同地域 OSS 内网不收流量费，公网访问收费。

### 3.2 服务器环境配置

SSH 登录 ECS：
```bash
ssh root@<ECS公网IP>
```

安装 Python 3.11：
```bash
apt-get update
apt-get install -y python3.11 python3.11-venv python3-pip libpq-dev
python3.11 --version
```

### 3.3 上传云后端代码

**方式一：git clone（推荐）**
```bash
# 服务器上
git clone <你的仓库地址> /opt/teamhub
cd /opt/teamhub/cloud_backend
```

**方式二：scp 直传**
```bash
# 本机上
scp -r TeamHub/cloud_backend root@<ECS公网IP>:/opt/teamhub/
```

### 3.4 安装生产依赖

```bash
cd /opt/teamhub/cloud_backend
pip3 install -r requirements.txt
```

### 3.5 配置生产环境变量

创建 `/opt/teamhub/cloud_backend/.env.production`：

```ini
# ─── 数据库 ───────────────────────────────────────────
# 选项1：继续使用 SQLite（适合初期小规模，无需安装 PostgreSQL）
DATABASE_URL=sqlite:////opt/teamhub/cloud_backend/data/cloud_prod.db

# 选项2：使用 PostgreSQL（适合多用户高并发，需先安装 PG）
# DATABASE_URL=postgresql://teamhub:强密码@localhost:5432/teamhub_prod

# ─── JWT（必须修改！）─────────────────────────────────
# 生成方法：openssl rand -hex 32
JWT_SECRET_KEY=在此填入64位随机字符串
JWT_ACCESS_TOKEN_EXPIRE_MINUTES=60
JWT_REFRESH_TOKEN_EXPIRE_DAYS=30

# ─── 阿里云 OSS ──────────────────────────────────────
OSS_ENDPOINT=https://oss-cn-shenzhen-internal.aliyuncs.com
OSS_ACCESS_KEY_ID=你的AccessKeyID
OSS_ACCESS_KEY_SECRET=你的AccessKeySecret
OSS_BUCKET_NAME=teamhub-prod
OSS_PREFIX=teamhub-cloud

# ─── CORS（填写实际访问域名或客户端 Origin）───────────
# Electron 打包后的 Origin 是 app://.
# 如果有 Web 管理后台，也加入
ALLOWED_ORIGINS=app://.,https://admin.你的域名.com

# ─── 端口 ─────────────────────────────────────────────
PORT=9000
```

**生成安全的 JWT Secret：**
```bash
openssl rand -hex 32
# 输出示例：a3f5c2e1d8b7a4f6e9c2d5a8b3f7e1c4d6a9f2b5e8c1d4a7b0f3e6c9d2a5b8
```
将输出值填入 `JWT_SECRET_KEY`。

**创建数据目录（使用 SQLite 时）：**
```bash
mkdir -p /opt/teamhub/cloud_backend/data
```

### 3.6 以 systemd 服务方式运行

创建 systemd 服务文件：
```bash
cat > /etc/systemd/system/teamhub-cloud.service << 'EOF'
[Unit]
Description=TeamHub 云服务
After=network.target

[Service]
Type=simple
User=www-data
WorkingDirectory=/opt/teamhub/cloud_backend
ExecStart=/usr/bin/python3 -m gunicorn main:app \
    --worker-class uvicorn.workers.UvicornWorker \
    --workers 2 \
    --bind 0.0.0.0:9000 \
    --timeout 120 \
    --access-logfile /var/log/teamhub-cloud/access.log \
    --error-logfile /var/log/teamhub-cloud/error.log
Restart=always
RestartSec=5
Environment="PATH=/usr/local/bin:/usr/bin"

[Install]
WantedBy=multi-user.target
EOF
```

创建日志目录并启动：
```bash
mkdir -p /var/log/teamhub-cloud
chown www-data:www-data /var/log/teamhub-cloud
chown -R www-data:www-data /opt/teamhub/cloud_backend

systemctl daemon-reload
systemctl enable teamhub-cloud
systemctl start teamhub-cloud
systemctl status teamhub-cloud
```

验证服务正常：
```bash
curl http://localhost:9000/api/health
# {"status":"ok","service":"teamhub-cloud"}
```

### 3.7 配置 Nginx 反向代理（可选但推荐）

安装 Nginx：
```bash
apt-get install -y nginx
```

创建配置文件 `/etc/nginx/sites-available/teamhub`：
```nginx
server {
    listen 80;
    server_name 你的域名.com;   # 或直接填 ECS 公网 IP

    # 将 /api/health 等请求代理到云后端
    location / {
        proxy_pass http://127.0.0.1:9000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_read_timeout 120s;

        # 支持大文件元数据请求
        client_max_body_size 10m;
    }
}
```

启用配置：
```bash
ln -s /etc/nginx/sites-available/teamhub /etc/nginx/sites-enabled/
nginx -t          # 检查配置语法
systemctl restart nginx
```

---

## 4. 首次初始化（两种场景通用）

### 4.1 创建超级管理员账号

云后端启动后，运行以下命令创建管理员：

**场景 A（本机）：**
```bash
cd cloud_backend
python create_superuser.py --email admin@example.com --password 你的强密码 --name 系统管理员
```

**场景 B（服务器）：**
```bash
cd /opt/teamhub/cloud_backend
python3 create_superuser.py --email admin@example.com --password 你的强密码 --name 系统管理员
```

成功输出：
```
[OK] 已创建超级管理员账号：admin@example.com
```

### 4.2 验证管理员登录

```bash
# 场景 A
curl -X POST http://localhost:9000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"admin@example.com","password":"你的强密码"}'

# 场景 B（替换为实际 IP 或域名）
curl -X POST http://<ECS公网IP>:9000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"admin@example.com","password":"你的强密码"}'
```

成功响应示例：
```json
{
  "access_token": "eyJhbGci...",
  "refresh_token": "xxxxxxxx",
  "token_type": "bearer",
  "user": {
    "id": "uuid",
    "email": "admin@example.com",
    "display_name": "系统管理员",
    "is_superuser": true
  }
}
```

### 4.3 访问管理后台

**场景 A：**
打开浏览器访问 `http://localhost:5173/admin.html`（前端 Vite dev server 已启动的情况下）

或直接打开构建产物：`frontend/dist/admin.html`

**场景 B：**
将前端 `admin.html` 及相关资源部署到 Web 服务器，或通过 Nginx 托管 `frontend/dist/` 目录。

使用管理员邮箱/密码登录，可看到：
- **仪表板**：用户数、团队数、工作空间数、设备数
- **用户管理**：激活/禁用用户、设置超管
- **团队管理**：查看团队、调整存储配额

---

## 5. 本地桌面端连接云端

### 5.1 配置云端地址

桌面端（本地后端）需要知道云后端的地址，通过设置页面配置：

1. 打开 TeamHub 桌面应用
2. 进入**设置** → **云端同步**卡片
3. 在「云服务地址」输入框填写：
   - 场景 A（本机联调）：`http://localhost:9000`
   - 场景 B（阿里云）：`http://<ECS公网IP>:9000` 或 `https://你的域名.com`
4. 点击保存

> 该地址保存在 `{library_path}/.teamhub/config.json` 的 `cloud_api_url` 字段中。

### 5.2 注册账号

1. 在桌面端点击右上角**用户图标** → 进入登录页
2. 若无账号，点击「注册」创建新账号
3. 登录后会自动保存 JWT 到 Electron safeStorage（加密存储）

> 场景 A 下也可直接用 create_superuser.py 创建的管理员账号登录。

### 5.3 创建团队和工作空间

1. 登录后进入「团队管理」页（`/team`）
2. 创建一个团队（如：`我的团队`）
3. 在团队内创建工作空间（如：`个人文档库`）
4. 记录工作空间 ID（UUID 格式）

### 5.4 绑定工作空间

1. 进入**设置** → **云端同步**卡片
2. 在「工作空间」下拉框选择刚创建的工作空间
3. 点击「绑定」

绑定后，设置页面同步统计区域会显示：
- 已同步：0
- 待推送：N（本地文档数量）
- 冲突：0
- 游标：0

---

## 6. 功能验证清单

按顺序执行以下测试，确认各功能正常。

### 6.1 基础连通性

```
☐ GET http://localhost:9000/api/health 返回 {"status":"ok"}
☐ POST /api/auth/login 用管理员账号登录成功，返回 access_token
☐ GET /api/users/me 用 Bearer Token 能获取用户信息
```

### 6.2 桌面端登录与绑定

```
☐ 桌面端「设置」→「云端同步」→ 登录成功，显示账号信息
☐ 创建团队后，工作空间下拉框能列出工作空间
☐ 点击「绑定」后，同步统计显示正确数据（待推送 > 0）
```

### 6.3 Push（本地推送到云端）

```
☐ 在桌面端收纳 2-3 个测试文档
☐ 点击「推送」→ 显示「已推送 N 条，冲突 0 条」
☐ 在管理后台（或 API）能查询到这些文档的云端记录
```

验证云端元数据（API 验证）：
```bash
# 获取工作空间文档列表
TOKEN="上一步登录获得的access_token"
WS_ID="工作空间UUID"
curl -H "Authorization: Bearer $TOKEN" \
  http://localhost:9000/api/workspaces/$WS_ID/documents
```

### 6.4 文件上传到 OSS

```
☐ Push 时控制台（云后端日志）无 OSS 相关报错
☐ MinIO 控制台（场景A：http://localhost:9002）或
  阿里云 OSS 控制台（场景B）能看到上传的文件
  路径格式：teamhub-cloud/workspaces/{ws_id}/files/{doc_id}/{ver_id}/{filename}
```

### 6.5 Pull（从云端拉取）

```
☐ 在另一台设备（或同一机器换文件库路径）登录同一账号
☐ 绑定同一工作空间
☐ 点击「拉取」→ 新文档出现在待整理区（inbox）
☐ 文件内容与原始文件一致（校验大小或内容）
```

### 6.6 冲突检测

```
☐ A 设备修改文档 push → B 设备也修改同一文档 push
☐ 第二个 push 返回冲突信息
☐ 设置页面「冲突」数量 > 0，冲突列表显示该文档
☐ 选择「保留本地」/「使用云端」/「两者保留」后，冲突清零
```

### 6.7 管理后台验证

```
☐ 浏览器打开 admin.html，用管理员账号登录
☐ 仪表板显示正确统计数据（用户数 ≥ 1，设备数 ≥ 1）
☐ 用户列表能看到测试账号
☐ 团队列表能看到创建的团队
```

---

## 7. 常见问题排查

### Q1：云后端启动报错 `OSS Bucket 初始化失败`

**场景 A**：MinIO 可能未启动。
```bash
# 检查 MinIO 是否运行
curl http://localhost:9001/minio/health/live

# 若未运行，重新启动
cd cloud_backend
docker-compose up -d minio
```

**场景 B**：检查 `.env.production` 中的 OSS 配置。
```bash
# 测试 OSS 连通性（阿里云）
curl https://teamhub-prod.oss-cn-shenzhen.aliyuncs.com
```
- endpoint 是否填写正确（内网 vs 公网）
- AK/SK 是否有 OSS Full Access 权限
- Bucket 是否已在控制台预先创建

---

### Q2：登录返回 `{"detail":"用户不存在"}` 或 `401`

- 确认已运行 `create_superuser.py` 创建账号
- 确认邮箱/密码正确
- 数据库文件路径是否正确（SQLite 场景下查看 `.env.dev` 的 DATABASE_URL）

---

### Q3：前端无法访问云后端，报 CORS 错误

检查 `.env.dev` 或 `.env.production` 的 `ALLOWED_ORIGINS` 是否包含前端 Origin：
```ini
# 开发环境
ALLOWED_ORIGINS=http://localhost:5173,http://127.0.0.1:5173

# Electron 生产环境（必须包含）
ALLOWED_ORIGINS=app://.,http://localhost:5173
```

修改后重启云后端。

---

### Q4：Push 时报 `云端服务未登录` 或 `401`

桌面端 Token 可能已过期（access_token 有效期 60 分钟）。
1. 在设置页退出云端登录
2. 重新登录
3. 重新绑定工作空间后再 push

---

### Q5：场景 B 服务器上 `systemctl status` 显示失败

查看详细日志：
```bash
journalctl -u teamhub-cloud -n 50 --no-pager
# 或
tail -50 /var/log/teamhub-cloud/error.log
```

常见原因：
- `ModuleNotFoundError`：依赖未安装，重新执行 `pip3 install -r requirements.txt`
- `Permission denied`：`/opt/teamhub/cloud_backend/data/` 目录权限问题，执行 `chown -R www-data /opt/teamhub/cloud_backend/data`
- `.env.production` 不存在：确认文件位于 `/opt/teamhub/cloud_backend/.env.production`

---

### Q6：MinIO 控制台能看到文件，但 Pull 下载失败

预签名 URL 可能因 endpoint 配置问题导致地址不可达。

场景 A 时确认 `OSS_ENDPOINT=http://localhost:9001`（不是容器内网地址）。

可在云后端日志中搜索 `presign-download`，查看生成的 URL 格式是否正确。

---

## 附录：环境变量速查

| 变量名 | 说明 | 示例值 |
|--------|------|--------|
| `DATABASE_URL` | 数据库连接字符串 | `sqlite:///./cloud_dev.db` |
| `JWT_SECRET_KEY` | JWT 签名密钥（≥64字符） | `openssl rand -hex 32` 输出 |
| `JWT_ACCESS_TOKEN_EXPIRE_MINUTES` | access_token 有效期（分钟） | `60` |
| `JWT_REFRESH_TOKEN_EXPIRE_DAYS` | refresh_token 有效期（天） | `30` |
| `OSS_ENDPOINT` | OSS 服务地址 | `http://localhost:9001` |
| `OSS_ACCESS_KEY_ID` | OSS 访问密钥 ID | `minioadmin` |
| `OSS_ACCESS_KEY_SECRET` | OSS 访问密钥 Secret | `minioadmin` |
| `OSS_BUCKET_NAME` | OSS Bucket 名称 | `teamhub-dev` |
| `OSS_PREFIX` | OSS 文件 key 前缀 | `teamhub-cloud` |
| `ALLOWED_ORIGINS` | CORS 允许的 Origin（逗号分隔） | `http://localhost:5173,app://.` |
| `PORT` | 服务监听端口 | `9000` |
