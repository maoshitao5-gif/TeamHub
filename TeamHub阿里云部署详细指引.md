# TeamHub 云后端 · 阿里云部署详细指引

> 适用版本：TeamHub v1.0
> 前提：已购买阿里云 ECS、OSS、RDS 服务

---

## 目录

1. [整体架构说明](#1-整体架构说明)
2. [第一步：收集阿里云资源信息](#2-第一步收集阿里云资源信息)
3. [第二步：SSH 登录 ECS](#3-第二步ssh-登录-ecs)
4. [第三步：服务器环境配置](#4-第三步服务器环境配置)
5. [第四步：上传云后端代码](#5-第四步上传云后端代码)
6. [第五步：配置生产环境变量](#6-第五步配置生产环境变量)
7. [第六步：创建 systemd 服务](#7-第六步创建-systemd-服务)
8. [第七步：配置 Nginx 反向代理](#8-第七步配置-nginx-反向代理)
9. [第八步：首次初始化](#9-第八步首次初始化)
10. [第九步：桌面端连接云端](#10-第九步桌面端连接云端)
11. [验证清单](#11-验证清单)
12. [常见问题排查](#12-常见问题排查)

---

## 1. 整体架构说明

```
你的电脑（TeamHub 桌面端）
        │
        │  HTTP（公网）
        ▼
阿里云 ECS
  └── Nginx（端口 80）→ 云后端 gunicorn（端口 9000）
        │                       │
        │ 内网                  │ 内网
        ▼                       ▼
阿里云 OSS（存储文件）    阿里云 RDS（PostgreSQL，存储元数据）
```

- **ECS**：运行 Python 云后端（FastAPI + gunicorn）
- **OSS**：存储同步的文件内容（实际文件二进制）
- **RDS**：PostgreSQL 数据库，存储用户、团队、文档元数据
- **Nginx**：反向代理，80 端口对外，转发到内部 9000 端口

---

## 2. 第一步：收集阿里云资源信息

在开始之前，先从阿里云控制台收集好以下信息，后面配置时会用到。

### 2.1 ECS 信息

进入「云服务器 ECS」→ 实例列表，记录：

- **公网 IP**：8.156.80.189
- **操作系统**：Alibaba Cloud Linux 3.2104 LTS 64位（本指引基于此系统）

同时确认安全组已开放以下端口（安全组 → 入方向规则）：

| 端口 | 用途 |
|------|------|
| 22 | SSH 登录 |
| 80 | HTTP（Nginx 反代） |
| 9000 | 云后端调试用（配好 Nginx 后可关闭） |

### 2.2 OSS 信息

进入「对象存储 OSS」→ Bucket 列表 → 点击你的 Bucket → 「概览」，记录：

- **Bucket 名称**：例如 `file-manager-kmj`
- **外网访问 Endpoint**：例如 `oss-cn-chengdu.aliyuncs.com`

> **必须使用外网 Endpoint（不要用含 `-internal` 的内网地址）**
>
> 原因：云后端会生成「预签名下载 URL」给桌面客户端用。URL 里嵌入了 Endpoint 域名，
> 如果是内网地址，桌面端在公网上根本访问不到，导致文件下载失败。

然后创建 RAM 子账号获取 AccessKey（如果还没有）：

1. 搜索「RAM 访问控制」→ 用户 → 创建用户，勾选「OpenAPI 调用访问」
2. **立即复制保存 AccessKey ID 和 AccessKey Secret**（关闭后无法再次查看）
3. 为该用户添加权限：`AliyunOSSFullAccess`

### 2.3 RDS 信息

进入「云数据库 RDS」→ 实例列表 → 点击你的实例，记录：

- **内网连接地址**：例如 `pgm-2vc4bqbp999y18o4.pgsql.cn-chengdu.rds.aliyuncs.com`（PostgreSQL 实例类似）
- **端口**：PostgreSQL 默认 `5432`
- **数据库名**：需要在 RDS 控制台「数据库管理」里创建一个，例如 `teamhub_prod`
- **用户名 / 密码**：在「账号管理」里创建的数据库账号

**在 RDS 控制台完成以下操作（如未操作过）：**

1. 「数据库管理」→ 创建数据库，数据库名 `teamhub_prod`，字符集 `UTF8`
2. 「账号管理」→ 创建账号，例如用户名 `teamhub`，设置强密码，权限选「读写」，授权到 `teamhub_prod` 数据库
3. 「白名单设置」→ 添加 ECS 实例的**内网 IP**（在 ECS 实例详情页「内网 IP」字段），否则 ECS 无法连接 RDS

---

## 3. 第二步：SSH 登录 ECS

在**你的电脑**上打开 PowerShell 或终端：

```powershell
ssh root@123.45.67.89
```

首次连接输入 `yes` 确认，再输入购买 ECS 时设置的密码。

登录成功后会看到：
```
root@iZxxxxxxx:~#
```

---

## 4. 第三步：服务器环境配置

以下命令全部在**服务器上**（SSH 登录后）执行。

### 4.1 安装系统依赖

```bash
dnf update -y
dnf install -y python3.11 python3.11-devel libpq-devel gcc nginx
```

验证：
```bash
python3.11 --version
# Python 3.11.x
```

> 如果提示 `No match for argument: python3.11`，说明需要先启用 App Stream 模块：
> ```bash
> dnf module enable python3.11 -y
> dnf install -y python3.11 python3.11-devel libpq-devel gcc nginx
> ```

### 4.2 创建目录

```bash
mkdir -p /opt/teamhub/cloud_backend
mkdir -p /var/log/teamhub-cloud
```

---

## 5. 第四步：上传云后端代码

在**你的电脑**上（不是服务器），打开 PowerShell：

```powershell
# 替换 123.45.67.89 为你的 ECS 公网 IP
scp -r C:\Users\Administrator\TeamHub\cloud_backend\* root@8.156.80.189:/opt/teamhub/cloud_backend/
```

上传完成后，在服务器上验证：
```bash
ls /opt/teamhub/cloud_backend/
# 应该看到：app/ main.py requirements.txt create_superuser.py 等
```

然后创建 Python 虚拟环境并安装依赖：

```bash
cd /opt/teamhub/cloud_backend
python3.11 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# 验证
python -c "import fastapi, boto3, sqlalchemy, psycopg2; print('依赖安装成功')"
```

---

## 6. 第五步：配置生产环境变量

### 6.1 生成 JWT 密钥

```bash
openssl rand -hex 32
# 输出示例：a3f5c2e1d8b7a4f6e9c2d5a8b3f7e1c4...
# 复制这串字符，下面会用到
```
588569b29034588ee10f84839af1a896f006fcee95d0c59434c7b68aab4253b4
### 6.2 创建配置文件

```bash
vi /opt/teamhub/cloud_backend/.env.production
```

填入以下内容，**将尖括号内的内容替换为你的实际值**：

```ini
# ─── 数据库（阿里云 RDS PostgreSQL）──────────────────
# 格式：postgresql://用户名:密码@内网连接地址:端口/数据库名
DATABASE_URL=postgresql://maoshitao:Mst52133@<pgm-2vc4bqbp999y18o4.pgsql.cn-chengdu.rds.aliyuncs.com>:5432/teamhub_prod

# ─── JWT 认证（必须修改！）────────────────────────────
JWT_SECRET_KEY=<粘贴上面 openssl 生成的字符串>
JWT_ACCESS_TOKEN_EXPIRE_MINUTES=60
JWT_REFRESH_TOKEN_EXPIRE_DAYS=30

# ─── 阿里云 OSS ───────────────────────────────────────
# 注意：用外网 Endpoint，不要用含 -internal 的内网地址
OSS_ENDPOINT=https://<你的OSS外网Endpoint>
OSS_ACCESS_KEY_ID=<RAM子账号的AccessKey ID>
OSS_ACCESS_KEY_SECRET=<RAM子账号的AccessKey Secret>
OSS_BUCKET_NAME=<你的Bucket名称>
OSS_PREFIX=teamhub-cloud

# ─── CORS（Electron 桌面端的 Origin 是 app://.）──────
ALLOWED_ORIGINS=app://.,http://localhost:5173

# ─── 服务端口 ─────────────────────────────────────────
PORT=9000
```

保存：`Ctrl+X` → `Y` → 回车。

### 6.3 验证数据库连接

```bash
cd /opt/teamhub/cloud_backend
source venv/bin/activate

python -c "
from app.config import settings
from sqlalchemy import create_engine, text
engine = create_engine(settings.database_url)
with engine.connect() as conn:
    conn.execute(text('SELECT 1'))
print('数据库连接成功')
"
```

如果报错，常见原因：
- RDS 白名单未添加 ECS 内网 IP
- 数据库名 / 用户名 / 密码有误
- RDS 实例未处于「运行中」状态

---

## 7. 第六步：创建 systemd 服务

### 7.1 设置目录权限

```bash
chown -R nginx:nginx /opt/teamhub/cloud_backend
chown -R nginx:nginx /var/log/teamhub-cloud
```

### 7.2 创建服务文件

```bash
cat > /etc/systemd/system/teamhub-cloud.service << 'EOF'
[Unit]
Description=TeamHub 云服务
After=network.target

[Service]
Type=simple
User=nginx
WorkingDirectory=/opt/teamhub/cloud_backend
ExecStart=/opt/teamhub/cloud_backend/venv/bin/gunicorn main:app \
    --worker-class uvicorn.workers.UvicornWorker \
    --workers 2 \
    --bind 0.0.0.0:9000 \
    --timeout 120 \
    --access-logfile /var/log/teamhub-cloud/access.log \
    --error-logfile /var/log/teamhub-cloud/error.log
Restart=always
RestartSec=5
Environment="PATH=/opt/teamhub/cloud_backend/venv/bin:/usr/local/bin:/usr/bin"

[Install]
WantedBy=multi-user.target
EOF
```

### 7.3 启动服务

```bash
systemctl daemon-reload
systemctl enable teamhub-cloud
systemctl start teamhub-cloud
systemctl status teamhub-cloud
```

启动成功标志：
```
Active: active (running) since ...
```

本机验证：
```bash
curl http://localhost:9000/api/health
# {"status":"ok","service":"teamhub-cloud"}
```

若失败，查看日志：
```bash
journalctl -u teamhub-cloud -n 50 --no-pager
```

---

## 8. 第七步：配置 Nginx 反向代理

Alibaba Cloud Linux 3 的 Nginx 使用 `/etc/nginx/conf.d/` 目录管理配置（不用 `sites-available`）：

```bash
cat > /etc/nginx/conf.d/teamhub.conf << 'EOF'
server {
    listen 80;
    server_name 8.156.80.189;   # 替换为你的 ECS 公网 IP（有域名填域名）

    proxy_read_timeout 120s;
    proxy_connect_timeout 10s;
    client_max_body_size 10m;

    location / {
        proxy_pass http://127.0.0.1:9000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }
}
EOF
```

检查语法并启动：
```bash
nginx -t          # 检查语法，输出 ok 才继续
systemctl restart nginx
systemctl enable nginx
```

验证（在**你的电脑**上执行）：
```powershell
curl http://8.156.80.189/api/health
# {"status":"ok","service":"teamhub-cloud"}
```

---

## 9. 第八步：首次初始化

### 9.1 创建超级管理员账号

在**服务器上**执行：

```bash
cd /opt/teamhub/cloud_backend
source venv/bin/activate

python create_superuser.py \
  --email maoshitao@hotmail.com \
  --password Mst52133 \
  --name 系统管理员
```

成功输出：
```
[OK] 已创建超级管理员账号：admin@yourcompany.com
```

### 9.2 验证登录（在你的电脑上）

```powershell
curl -X POST http://8.156.80.189/api/auth/login `
  -H "Content-Type: application/json" `
  -d "{\"email\":\"maoshitao@hotmail.com\",\"password\":\"Mst52133\"}"
```

看到 `access_token` 字段说明一切正常。

---

## 10. 第九步：桌面端连接云端

1. 打开 TeamHub 桌面应用 → 设置 → 「云端同步」卡片
2. 「云服务地址」填写：`http://123.45.67.89`（替换为你的 ECS 公网 IP）
3. 点击保存
4. 点击右上角用户图标登录，使用刚创建的管理员账号
5. 进入「团队管理」页，创建团队和工作空间
6. 回到「设置」→「云端同步」，绑定工作空间
7. 点击「推送」测试同步

---

## 11. 验证清单

```
服务器：
☐ systemctl status teamhub-cloud → active (running)
☐ curl http://localhost:9000/api/health → {"status":"ok"}
☐ curl http://localhost/api/health → {"status":"ok"}（经 Nginx）

外网：
☐ 从本地 curl http://<ECS公网IP>/api/health → {"status":"ok"}
☐ 管理员账号登录，返回 access_token

OSS：
☐ 服务启动日志含「OSS Bucket 初始化完成」
☐ /var/log/teamhub-cloud/error.log 无 OSS 报错

桌面端：
☐ 登录成功，右上角显示用户头像
☐ 绑定工作空间后「推送」正常
☐ OSS 控制台能看到上传的文件（路径：teamhub-cloud/workspaces/...）
```

---

## 12. 常见问题排查

### Q1：curl 云后端超时或 Connection refused

1. 检查安全组：ECS 控制台 → 安全组 → 入方向，确认 80 和 9000 端口已开放，源地址 `0.0.0.0/0`
2. 检查服务状态：`systemctl status teamhub-cloud`

---

### Q2：启动报错 ModuleNotFoundError

服务文件中的 `ExecStart` 路径必须用虚拟环境的 gunicorn：
```
/opt/teamhub/cloud_backend/venv/bin/gunicorn   ← 正确
/usr/bin/gunicorn                              ← 错误
```
修改后执行 `systemctl daemon-reload && systemctl restart teamhub-cloud`。

---

### Q3：数据库连接失败（could not connect to server）

- RDS 白名单未添加 ECS 的内网 IP → 去 RDS 控制台「白名单设置」添加
- `.env.production` 中 DATABASE_URL 的地址/端口/密码有误
- RDS 实例未运行 → 控制台确认实例状态为「运行中」

---

### Q4：推送成功但下载文件失败

`.env.production` 中的 `OSS_ENDPOINT` 用了内网地址：
```ini
# 错误（含 -internal，客户端无法访问）
OSS_ENDPOINT=https://oss-cn-shenzhen-internal.aliyuncs.com

# 正确（外网地址）
OSS_ENDPOINT=https://oss-cn-shenzhen.aliyuncs.com
```
修改后 `systemctl restart teamhub-cloud`。

---

### Q5：OSS Bucket 初始化失败

- Bucket 名称与控制台不一致（区分大小写）
- AccessKey 权限不足 → RAM 控制台确认有 `AliyunOSSFullAccess`
- Endpoint 域名格式错误（必须带 `https://`）

---

### Q6：忘记管理员密码

```bash
cd /opt/teamhub/cloud_backend && source venv/bin/activate
python create_superuser.py --email admin@yourcompany.com --password 新密码
```

---

### Q7：代码更新后如何重部署

在你的电脑上：
```powershell
scp -r C:\Users\Administrator\TeamHub\cloud_backend\* root@123.45.67.89:/opt/teamhub/cloud_backend/
```

服务器上重启：
```bash
systemctl restart teamhub-cloud
```

---

## 附录：常用运维命令

```bash
# 查看服务状态
systemctl status teamhub-cloud

# 实时查看日志
journalctl -u teamhub-cloud -f

# 查看最近错误
tail -100 /var/log/teamhub-cloud/error.log

# 重启服务
systemctl restart teamhub-cloud

# 重启 Nginx
systemctl restart nginx
```

## 附录：关键文件路径

| 用途 | 路径 |
|------|------|
| 生产配置 | `/opt/teamhub/cloud_backend/.env.production` |
| 访问日志 | `/var/log/teamhub-cloud/access.log` |
| 错误日志 | `/var/log/teamhub-cloud/error.log` |
| systemd 服务 | `/etc/systemd/system/teamhub-cloud.service` |
| Nginx 配置 | `/etc/nginx/sites-available/teamhub` |
| Python 虚拟环境 | `/opt/teamhub/cloud_backend/venv/` |
