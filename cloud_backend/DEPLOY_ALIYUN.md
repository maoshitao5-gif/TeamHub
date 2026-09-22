# TeamHub 云后端 · 阿里云部署指南

> 适用人群：已有阿里云 ECS + OSS + RDS 资源，直接上手部署
> 目标：将 `cloud_backend/` 部署为生产就绪的 HTTPS API 服务
> 语言：所有命令在 ECS（Ubuntu 22.04）上执行，除非特别注明

---

## 第一章：资源清单与架构确认

### 1.1 前置资源确认

部署前请确认以下资源已就绪：

| 资源 | 推荐规格 | 关键配置检查项 |
|------|---------|--------------|
| ECS | 2核4G，Ubuntu 22.04 LTS | 安全组：仅开 22/80/443，其余关闭 |
| RDS PostgreSQL | 14+，1核2G | 与 ECS **同 VPC 同可用区**，白名单设 ECS 内网 IP |
| OSS Bucket | 标准存储，与 ECS **同 Region** | 权限：私有；使用 RAM 子账号 AK/SK，不用主账号 |

> **为什么同 VPC / 同 Region？**
> ECS ↔ RDS 走内网，不计流量费，延迟更低；ECS ↔ OSS 使用内网 Endpoint 同理。

### 1.2 网络架构图

```
本地桌面端（Electron / TeamHub）
        ↓ HTTPS（443）
   阿里云 ECS 公网 IP / 域名
        ↓
   Nginx（80 → 301 HTTPS，443 → 反代 9000）
        ↓
   Docker 容器（FastAPI + Gunicorn，仅监听 127.0.0.1:9000）
        ├── 内网 VPC → RDS PostgreSQL（teamhub_cloud 库，5432）
        └── 内网 Endpoint → OSS Bucket（teamhub-prod）
```

### 1.3 端口与流量规划

| 端口 | 监听方 | 说明 |
|------|--------|------|
| 22 | ECS 公网 | SSH 运维，建议限制来源 IP |
| 80 | Nginx | HTTP，仅用于 301 跳转 HTTPS |
| 443 | Nginx | HTTPS，对外唯一 API 入口 |
| 9000 | Docker 容器 | **仅监听 127.0.0.1**，不直接对外暴露 |
| 5432 | RDS | 内网，仅允许 ECS 内网 IP 访问 |

> **安全原则**：ECS 安全组出方向默认全放行，入方向仅开 22/80/443，9000 端口绝对不出现在安全组规则中。

---

## 第二章：服务器环境准备

### 2.1 SSH 连接 ECS

```bash
# 密码登录
ssh root@<ECS公网IP>

# 或密钥登录（推荐）
ssh -i ~/.ssh/aliyun_key ubuntu@<ECS公网IP>
```

首次登录后建议立即更新系统：

```bash
sudo apt-get update && sudo apt-get upgrade -y
```

### 2.2 安装 Docker

```bash
# 官方一键安装脚本
curl -fsSL https://get.docker.com | sh

# 将当前用户加入 docker 组（避免每次 sudo）
sudo usermod -aG docker $USER && newgrp docker

# 验证安装
docker --version
# 期望输出：Docker version 24.x.x, build ...

# 设置 Docker 开机自启
sudo systemctl enable docker
```

> **国内网络提速**：如下载缓慢，可配置阿里云镜像加速器（ECS 控制台 → 容器镜像服务 → 镜像加速器，复制配置命令执行即可）。

### 2.3 安装 Nginx

```bash
sudo apt-get install -y nginx
sudo systemctl enable nginx && sudo systemctl start nginx

# 验证
curl http://localhost
# 应返回 Nginx 默认欢迎页 HTML
```

### 2.4 创建应用目录

```bash
sudo mkdir -p /opt/teamhub
sudo chown $USER:$USER /opt/teamhub
cd /opt/teamhub
```

### 2.5 上传代码

**方案 A：Git 克隆（推荐，便于后续更新）**

```bash
# 克隆完整仓库
git clone <你的Git仓库地址> .

# 或仅获取 cloud_backend 目录（节省空间）
git clone --filter=blob:none --sparse <你的Git仓库地址> tmp
cd tmp && git sparse-checkout set cloud_backend
mv cloud_backend /opt/teamhub/
cd /opt/teamhub && rm -rf /opt/teamhub/tmp
```

**方案 B：SCP 直传（适合无 Git 仓库场景）**

```bash
# 在本地机器上执行
scp -r ./cloud_backend root@<ECS公网IP>:/opt/teamhub/
```

上传完成后确认目录结构：

```bash
ls /opt/teamhub/cloud_backend/
# 应包含：main.py  Dockerfile  requirements.txt  app/  alembic/  .env.production  等
```

---

## 第三章：RDS PostgreSQL 配置

### 3.1 RDS 控制台操作

**步骤一：创建数据库账号**

1. 登录阿里云控制台 → RDS → 目标实例
2. 左侧菜单 → **账号管理** → 创建账号
   - 账号名：`teamhub`
   - 账号类型：普通账号（standard）
   - 密码：设置强密码并妥善保存

**步骤二：创建数据库**

1. 左侧菜单 → **数据库管理** → 创建数据库
   - 数据库名：`teamhub_cloud`
   - 字符集：`UTF8`
   - 授权账号：选择 `teamhub`，权限：读写（DDL+DML）

**步骤三：配置白名单**

1. 左侧菜单 → **数据安全** → **白名单设置**
2. 添加 ECS **内网 IP**（不是公网 IP）
   - 格式示例：`172.16.12.34`（不需要加 /32，直接填 IP 即可）
   - 获取 ECS 内网 IP：ECS 控制台 → 实例详情 → 网络 → 内网 IP

### 3.2 获取 RDS 连接信息

- RDS 控制台 → 目标实例 → **基本信息** → **连接信息**
- 复制**内网连接地址**（格式：`rm-xxxxxxxx.pg.rds.aliyuncs.com`）
- 端口：`5432`

> **注意**：使用内网地址，不要用外网地址。内网连接不计流量费，且更安全。

### 3.3 在 ECS 上验证连接

```bash
# 安装 PostgreSQL 客户端（仅命令行工具，不安装 Server）
sudo apt-get install -y postgresql-client

# 测试连接
psql -h <RDS内网地址> -U teamhub -d teamhub_cloud -c "SELECT version();"
# 输入密码后，期望输出：
# PostgreSQL 14.x on x86_64-pc-linux-gnu, compiled by gcc ...
```

如果连接失败，请检查：
- 白名单中的 IP 是否与 ECS 内网 IP 一致
- ECS 和 RDS 是否在同一 VPC

---

## 第四章：OSS 配置

### 4.1 确认 Bucket 基本设置

1. OSS 控制台 → 目标 Bucket → **基础设置**
2. 检查以下项目：
   - **读写权限**：必须为**私有**（文件通过预签名 URL 临时授权访问，不能设为公读）
   - **Region**：与 ECS 相同 Region（如杭州、上海等）
   - **存储类型**：标准存储（Standard）

### 4.2 获取内网 Endpoint

内网 Endpoint 格式：`https://oss-cn-<region>-internal.aliyuncs.com`

常用 Region 对应关系：

| Region | 内网 Endpoint |
|--------|--------------|
| 华东1（杭州） | `https://oss-cn-hangzhou-internal.aliyuncs.com` |
| 华东2（上海） | `https://oss-cn-shanghai-internal.aliyuncs.com` |
| 华北2（北京） | `https://oss-cn-beijing-internal.aliyuncs.com` |
| 华南1（深圳） | `https://oss-cn-shenzhen-internal.aliyuncs.com` |

> **关键**：内网 Endpoint 带 `-internal` 后缀，ECS 访问不计流量费，且速度更快。
> 可在 OSS 控制台 → Bucket → **概览** → **访问域名** 中查看"ECS的经典网络访问Endpoint"。

### 4.3 创建 RAM 子账号与权限策略

**步骤一：创建 RAM 用户**

1. 进入**RAM 访问控制**控制台 → **用户** → **创建用户**
2. 登录名称：`teamhub-oss`
3. 访问方式：勾选**OpenAPI调用访问**（即 AccessKey 方式）
4. 创建后立即下载或记录 `AccessKeyId` 和 `AccessKeySecret`（**仅显示一次，务必保存！**）

**步骤二：创建自定义权限策略**

1. RAM 控制台 → **权限策略** → **创建权限策略**
2. 策略名称：`TeamHubOSSPolicy`
3. 配置模式选**脚本编辑**，粘贴以下内容（替换 Bucket 名）：

```json
{
  "Version": "1",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "oss:PutObject",
        "oss:GetObject",
        "oss:DeleteObject",
        "oss:HeadObject",
        "oss:GetBucketInfo",
        "oss:ListObjects"
      ],
      "Resource": [
        "acs:oss:*:*:<你的Bucket名>",
        "acs:oss:*:*:<你的Bucket名>/*"
      ]
    }
  ]
}
```

> 最小权限原则：只授予 TeamHub 实际使用的操作，不授予删除 Bucket、修改权限等危险操作。

**步骤三：授权**

1. RAM 控制台 → **用户** → 找到 `teamhub-oss` → **添加权限**
2. 选择**自定义策略** → 找到 `TeamHubOSSPolicy` → 确认授权

---

## 第五章：环境变量配置

### 5.1 生成 JWT 密钥

```bash
# 在 ECS 上执行，生成 64 字符随机密钥
openssl rand -hex 32
# 示例输出：a3f8b2c1d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1
# 复制这行输出，下一步使用
```

### 5.2 创建 .env 文件

```bash
cd /opt/teamhub/cloud_backend

# 从生产模板复制
cp .env.production .env

# 编辑配置（使用 nano 或 vim）
nano .env
```

**.env 完整配置说明**（逐项填写，不留占位符）：

```ini
# ===== 数据库 =====
# 格式：postgresql://用户名:密码@主机:端口/数据库名
DATABASE_URL=postgresql://teamhub:<RDS密码>@<RDS内网地址>:5432/teamhub_cloud

# ===== JWT 认证 =====
JWT_SECRET_KEY=<openssl rand -hex 32 生成的值>
JWT_ALGORITHM=HS256
JWT_ACCESS_TOKEN_EXPIRE_MINUTES=60
JWT_REFRESH_TOKEN_EXPIRE_DAYS=30

# ===== 阿里云 OSS =====
# 使用内网 Endpoint（带 -internal 后缀）
OSS_ENDPOINT=https://oss-cn-<region>-internal.aliyuncs.com
OSS_ACCESS_KEY_ID=<RAM子账号 AccessKeyId>
OSS_ACCESS_KEY_SECRET=<RAM子账号 AccessKeySecret>
OSS_BUCKET_NAME=<你的Bucket名称>
OSS_PREFIX=teamhub

# ===== CORS 跨域 =====
# 填写实际域名，以及 Electron 必需的 app:// 和 null 来源
ALLOWED_ORIGINS=https://api.<你的域名>.com,app://.,null

# ===== 服务端口 =====
PORT=9000
```

### 5.3 保护配置文件权限

```bash
# 仅 owner 可读写，其他用户无权限
chmod 600 /opt/teamhub/cloud_backend/.env

# 验证
ls -la /opt/teamhub/cloud_backend/.env
# 期望：-rw------- 1 ubuntu ubuntu ... .env
```

> **安全提醒**：`.env` 文件中包含数据库密码和 OSS 密钥，绝对不能提交到 Git 仓库。
> 确认 `cloud_backend/.gitignore` 包含 `.env` 条目（代码仓库中已配置）。

---

## 第六章：Docker 构建与启动

### 6.1 构建 Docker 镜像

```bash
cd /opt/teamhub/cloud_backend

# 构建镜像（首次约 3-5 分钟，视网速而定）
docker build -t teamhub-cloud:latest .

# 验证镜像
docker images | grep teamhub-cloud
# 期望：teamhub-cloud   latest   <IMAGE_ID>   ...   ~300MB
```

> **如果 pip 下载慢**：Dockerfile 已配置阿里云 PyPI 镜像源，国内 ECS 速度应较快。
> 如仍然缓慢，可手动在 Dockerfile 中 `RUN pip install` 命令前添加 `-i https://pypi.aliyun.com/simple`。

### 6.2 启动容器

```bash
docker run -d \
  --name teamhub-cloud \
  --restart unless-stopped \
  --env-file /opt/teamhub/cloud_backend/.env \
  -p 127.0.0.1:9000:9000 \
  teamhub-cloud:latest

# 参数说明：
#   -d                         后台运行
#   --restart unless-stopped   宿主机重启后自动启动容器
#   --env-file .env            从 .env 文件注入环境变量
#   -p 127.0.0.1:9000:9000    只绑定本地 127.0.0.1，不暴露到公网
```

**确认容器状态**：

```bash
# 查看运行中的容器
docker ps
# 期望：STATUS 列显示 Up X seconds/minutes

# 查看启动日志
docker logs teamhub-cloud
# 期望最后几行类似：
# INFO:     Application startup complete.
# INFO:     Uvicorn running on http://0.0.0.0:9000 (Press CTRL+C to quit)
```

如果容器启动失败，查看错误日志：

```bash
docker logs teamhub-cloud 2>&1 | tail -30
```

常见问题：
- `could not connect to server` → 检查 RDS 白名单和 DATABASE_URL
- `NoSuchBucket` → 检查 OSS_BUCKET_NAME 是否正确
- `SignatureDoesNotMatch` → 检查 AK/SK 是否有多余空格

### 6.3 数据库迁移

```bash
# 在容器内执行 Alembic 迁移（创建所有表结构）
docker exec -it teamhub-cloud python -m alembic upgrade head
```

**验证表已创建**：

```bash
docker exec -it teamhub-cloud python -c "
from app.database import engine
from sqlalchemy import inspect
inspector = inspect(engine)
tables = inspector.get_table_names()
print('已创建的表：')
for t in sorted(tables):
    print(f'  - {t}')
"
```

期望输出包含以下所有表：

```
已创建的表：
  - alembic_version
  - cloud_change_logs
  - cloud_documents
  - cloud_tags
  - cloud_versions
  - devices
  - refresh_tokens
  - team_members
  - teams
  - users
  - workspaces
```

### 6.4 创建超级管理员账号

```bash
# 替换邮箱和密码（密码建议 12 位以上，含大小写和数字）
docker exec -it teamhub-cloud python create_superuser.py \
  --email admin@yourdomain.com \
  --password <强密码>

# 期望输出：
# 超级管理员创建成功：admin@yourdomain.com
```

> 此账号用于访问 `/api/admin/*` 管理接口和 Admin Panel 前端页面。

### 6.5 本机健康检查

```bash
# 在 ECS 上直接访问容器（不经过 Nginx）
curl http://127.0.0.1:9000/api/health

# 期望响应：
# {"status":"ok","database":"connected","oss":"connected"}
```

如果 `oss` 显示 `"error"` 而非 `"connected"`，说明 OSS 配置有误，检查 Endpoint / AK / SK / Bucket 名称。

---

## 第七章：Nginx 反向代理 + HTTPS

### 7.1 申请 SSL 证书

1. 阿里云控制台 → **数字证书管理服务** → **SSL 证书** → **免费证书**
2. 每个自然年可申请 20 张免费 DV 证书（每张有效期 1 年）
3. 填写域名：`api.<你的域名>.com`
4. 验证方式：DNS 验证（推荐，在阿里云 DNS 中自动添加 TXT 记录）
5. 审核通过后（通常几分钟）→ **下载证书** → 选择 **Nginx** 格式
6. 下载的 zip 包含两个文件：`<证书ID>_api.yourdomain.com.pem` 和 `<证书ID>_api.yourdomain.com.key`

**上传证书到 ECS**：

```bash
# 在本地机器上执行
sudo scp <证书ID>_api.yourdomain.com.pem root@<ECS公网IP>:/etc/nginx/ssl/api.yourdomain.com.pem
sudo scp <证书ID>_api.yourdomain.com.key root@<ECS公网IP>:/etc/nginx/ssl/api.yourdomain.com.key
```

```bash
# 在 ECS 上执行
sudo mkdir -p /etc/nginx/ssl
sudo chmod 700 /etc/nginx/ssl
sudo chmod 600 /etc/nginx/ssl/*
```

### 7.2 配置域名解析

在执行 Nginx 配置前，先完成 DNS 解析，让域名能指向 ECS：

1. 阿里云控制台 → **云解析 DNS** → 目标域名
2. **添加记录**：
   - 记录类型：`A`
   - 主机记录：`api`（即 `api.yourdomain.com`）
   - 记录值：ECS 公网 IP
   - TTL：10 分钟（便于调试）

3. 验证解析生效：

```bash
# 在任意机器上执行
nslookup api.yourdomain.com
# 期望解析到 ECS 公网 IP
```

### 7.3 创建 Nginx 配置

```bash
sudo nano /etc/nginx/sites-available/teamhub-cloud
```

粘贴以下完整配置（替换域名和证书路径）：

```nginx
# HTTP → HTTPS 301 永久重定向
server {
    listen 80;
    listen [::]:80;
    server_name api.<你的域名>.com;

    # 仅允许 Let's Encrypt / 阿里云证书验证的 .well-known 路径通过
    location /.well-known/acme-challenge/ {
        root /var/www/html;
    }

    location / {
        return 301 https://$host$request_uri;
    }
}

# HTTPS 反向代理
server {
    listen 443 ssl http2;
    listen [::]:443 ssl http2;
    server_name api.<你的域名>.com;

    # SSL 证书（阿里云 Nginx 格式）
    ssl_certificate     /etc/nginx/ssl/api.<你的域名>.com.pem;
    ssl_certificate_key /etc/nginx/ssl/api.<你的域名>.com.key;

    # 现代 SSL 配置（TLS 1.2 + 1.3，禁用旧版本）
    ssl_protocols       TLSv1.2 TLSv1.3;
    ssl_ciphers         ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256:ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384:ECDHE-ECDSA-CHACHA20-POLY1305:ECDHE-RSA-CHACHA20-POLY1305:DHE-RSA-AES128-GCM-SHA256;
    ssl_prefer_server_ciphers off;
    ssl_session_cache   shared:SSL:10m;
    ssl_session_timeout 1d;

    # 文件上传大小限制（文档同步，支持大文件）
    client_max_body_size 500m;

    # 安全响应头
    add_header X-Frame-Options DENY always;
    add_header X-Content-Type-Options nosniff always;
    add_header X-XSS-Protection "1; mode=block" always;
    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
    add_header Referrer-Policy "strict-origin-when-cross-origin" always;

    # 反向代理到 Docker 容器
    location / {
        proxy_pass         http://127.0.0.1:9000;
        proxy_http_version 1.1;
        proxy_set_header   Upgrade $http_upgrade;
        proxy_set_header   Connection 'upgrade';
        proxy_set_header   Host $host;
        proxy_set_header   X-Real-IP $remote_addr;
        proxy_set_header   X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;

        # 超时设置（大文件上传需要更长时间）
        proxy_read_timeout  300s;
        proxy_send_timeout  300s;
        proxy_connect_timeout 30s;

        # 禁用缓冲（流式响应友好）
        proxy_buffering off;
    }

    # 静态文件缓存（如有前端静态资源托管在此）
    location ~* \.(js|css|png|jpg|jpeg|gif|ico|svg|woff|woff2)$ {
        proxy_pass http://127.0.0.1:9000;
        proxy_cache_valid 200 1d;
        add_header Cache-Control "public, max-age=86400";
    }
}
```

**启用配置并验证**：

```bash
# 创建软链接启用站点
sudo ln -s /etc/nginx/sites-available/teamhub-cloud /etc/nginx/sites-enabled/

# 删除默认站点（避免冲突）
sudo rm -f /etc/nginx/sites-enabled/default

# 语法检查（必须通过再 reload）
sudo nginx -t
# 期望：nginx: configuration file /etc/nginx/nginx.conf test is successful

# 重新加载 Nginx
sudo systemctl reload nginx
```

### 7.4 验证 HTTPS 访问

```bash
# 从 ECS 外部或本地机器执行
curl https://api.<你的域名>.com/api/health

# 期望响应：
# {"status":"ok","database":"connected","oss":"connected"}

# 检查 HTTP → HTTPS 重定向
curl -I http://api.<你的域名>.com/api/health
# 期望：HTTP/1.1 301 Moved Permanently
#       Location: https://api.<你的域名>.com/api/health
```

---

## 第八章：前端配置（本地 Electron 连接云端）

### 8.1 更新本地应用的云端 API 地址

**方式一：通过设置页面（推荐，无需改文件）**

1. 打开 TeamHub 客户端
2. 进入**设置**页面 → **云端同步**卡片
3. 找到云服务地址输入框，修改为：`https://api.<你的域名>.com`
4. 点击**保存**

此操作将写入 `config.json` 中的 `cloud_api_url` 字段，应用重启后生效。

**方式二：直接修改配置文件**

找到 TeamHub 的 `config.json` 文件：
- Windows：`%APPDATA%\TeamHub\config.json` 或文件库根目录下的 `.teamhub/config.json`
- 修改 `cloud_api_url` 字段：

```json
{
  "cloud_api_url": "https://api.<你的域名>.com"
}
```

### 8.2 开发模式前端临时指向云端

如需在开发环境中测试云端连接（而非本地 9000 端口），临时修改：

```bash
# frontend/.env.development（临时测试后记得改回）
VITE_CLOUD_API_URL=https://api.<你的域名>.com
```

> 注意：开发模式设置 `VITE_CLOUD_API_URL` 后，该值优先级最高，会覆盖 `config.json` 中的设置。
> 测试完成后改回 `http://localhost:9000`，避免开发时意外操作生产数据。

---

## 第九章：本地 ↔ 云端会话测试流程

### 9.1 基础连通性测试（命令行）

在本地机器（不是 ECS）执行以下测试，验证从外部可以访问云端 API：

```bash
# 测试 1：健康检查
curl https://api.<你的域名>.com/api/health
# 期望：{"status":"ok","database":"connected","oss":"connected"}

# 测试 2：注册测试账号
curl -X POST https://api.<你的域名>.com/api/auth/register \
  -H "Content-Type: application/json" \
  -d '{
    "email": "test@example.com",
    "password": "test123456",
    "display_name": "测试用户"
  }'
# 期望：{"access_token":"eyJ...","token_type":"bearer","refresh_token":"...","user":{...}}

# 测试 3：登录并提取 Token（保存为环境变量，后续测试使用）
TOKEN=$(curl -s -X POST https://api.<你的域名>.com/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"test@example.com","password":"test123456"}' \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")
echo "Token 已保存：${TOKEN:0:20}..."

# 测试 4：获取当前用户信息（验证 JWT 有效）
curl https://api.<你的域名>.com/api/users/me \
  -H "Authorization: Bearer $TOKEN"
# 期望：{"id":"...","email":"test@example.com","display_name":"测试用户","is_superuser":false,...}
```

### 9.2 团队与工作空间测试

```bash
# 创建团队
TEAM_RESP=$(curl -s -X POST https://api.<你的域名>.com/api/teams/ \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name":"测试团队","slug":"test-team"}')
echo "团队创建结果：$TEAM_RESP"
TEAM_ID=$(echo $TEAM_RESP | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")
echo "团队ID：$TEAM_ID"

# 创建工作空间
WS_RESP=$(curl -s -X POST https://api.<你的域名>.com/api/teams/$TEAM_ID/workspaces \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name":"默认工作空间"}')
echo "工作空间创建结果：$WS_RESP"
WS_ID=$(echo $WS_RESP | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")
echo "工作空间ID：$WS_ID"

# 列出工作空间下的文档（初始为空）
curl https://api.<你的域名>.com/api/workspaces/$WS_ID/documents \
  -H "Authorization: Bearer $TOKEN"
# 期望：{"items":[],"total":0,"cursor":null}
```

### 9.3 桌面端 → 云端完整同步测试

**前置条件**：
- TeamHub 客户端已启动
- `config.json` 中 `cloud_api_url` 已设置为 `https://api.<你的域名>.com`

按以下步骤在 TeamHub 客户端中操作：

| 步骤 | 操作位置 | 操作内容 | 期望结果 |
|------|---------|---------|---------|
| 1 | 设置页 → 云端同步卡片 | 输入邮密，点击**登录** | 显示「已登录：测试用户」，账号名出现 |
| 2 | 同一卡片 | 输入或选择上一步的工作空间 ID，点击**绑定工作空间** | 工作空间名称「默认工作空间」显示 |
| 3 | 文档库页面 | 导入 1-2 个本地文档（拖拽或扫描） | 文档出现在列表中，状态为「待整理」或「已整理」 |
| 4 | 文档库页面 | 点击**同步 → 推送**按钮 | 进度条完成，文档同步状态徽章变为绿色「已同步」 |
| 5 | 设置页 → 云端同步卡片 | 查看同步统计 | 「已同步」数量与刚导入的文档数量一致 |
| 6 | 命令行验证 | 执行下方命令 | 能看到刚推送的文档记录 |

```bash
# 步骤 6 命令行验证
curl "https://api.<你的域名>.com/api/workspaces/$WS_ID/documents" \
  -H "Authorization: Bearer $TOKEN" \
  | python3 -m json.tool
# 期望：items 数组中包含刚推送的文档，total > 0
```

| 步骤 | 操作位置 | 操作内容 | 期望结果 |
|------|---------|---------|---------|
| 7 | 文档库页面 | 点击**同步 → 拉取**按钮 | 无冲突提示（首次同步，本地与云端一致） |
| 8 | 从另一台设备登录 | 同样配置后点击**拉取** | 能下载到第一台设备推送的文档 |

### 9.4 Admin Panel 测试

Admin Panel 是独立的 HTML 页面，用超管账号登录可查看系统统计。

**开发环境访问**：

```bash
# 启动前端开发服务器
cd frontend && npm run dev
# 访问：http://localhost:5173/admin.html
```

**生产环境（如果 Nginx 托管了前端静态资源）**：

```
https://api.<你的域名>.com/admin.html
```

测试步骤：
1. 访问 admin.html → 自动跳转登录页
2. 使用超管账号（`admin@yourdomain.com`）登录
3. 仪表板应显示：用户数 2（超管 + 测试用户）、团队数 1
4. 用户管理页：能看到 `test@example.com` 的账号信息
5. 点击"设为超管"或"禁用"等操作验证权限控制

### 9.5 OSS 文件上传验证

```bash
# 步骤 1：获取预签名上传 URL
PRESIGN_RESP=$(curl -s -X POST https://api.<你的域名>.com/api/storage/presign-upload \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d "{
    \"workspace_id\": \"$WS_ID\",
    \"doc_id\": \"test-doc-oss-001\",
    \"version_id\": \"v001\",
    \"filename\": \"test.txt\",
    \"content_type\": \"text/plain\"
  }")
echo "预签名响应："
echo $PRESIGN_RESP | python3 -m json.tool

# 提取 upload_url
UPLOAD_URL=$(echo $PRESIGN_RESP | python3 -c "import sys,json; print(json.load(sys.stdin)['upload_url'])")

# 步骤 2：用预签名 URL 直传文件到 OSS（不经过服务器）
curl -X PUT "$UPLOAD_URL" \
  -H "Content-Type: text/plain" \
  --data "Hello TeamHub OSS Test - $(date)"
# 期望：HTTP 200，无响应体（阿里云 OSS PUT 成功无返回内容）
# 或使用 -v 查看状态码：curl -v -X PUT "$UPLOAD_URL" -H "Content-Type: text/plain" --data "test"
echo "上传 HTTP 状态：$?"
```

> OSS 预签名直传成功后，可在 OSS 控制台 → Bucket → 文件列表中看到对应文件，路径格式为：
> `teamhub/workspaces/<ws_id>/files/test-doc-oss-001/v001/test.txt`

### 9.6 Token 刷新测试（Token Rotation 验证）

```bash
# 登录时同时提取 refresh_token
LOGIN_RESP=$(curl -s -X POST https://api.<你的域名>.com/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"test@example.com","password":"test123456"}')

REFRESH_TOKEN=$(echo $LOGIN_RESP | python3 -c "import sys,json; print(json.load(sys.stdin)['refresh_token'])")
echo "Refresh Token: ${REFRESH_TOKEN:0:20}..."

# 用 refresh_token 换取新的 access_token（Token Rotation）
curl -s -X POST https://api.<你的域名>.com/api/auth/refresh \
  -H "Content-Type: application/json" \
  -d "{\"refresh_token\": \"$REFRESH_TOKEN\"}" \
  | python3 -m json.tool
# 期望：返回全新的 access_token 和 refresh_token（旧的 refresh_token 立即失效）

# 验证旧 refresh_token 已失效（Token Rotation 安全特性）
curl -s -X POST https://api.<你的域名>.com/api/auth/refresh \
  -H "Content-Type: application/json" \
  -d "{\"refresh_token\": \"$REFRESH_TOKEN\"}"
# 期望：{"detail":"refresh token 无效或已过期"}（401）
```

### 9.7 权限隔离测试

```bash
# 测试 1：普通账号访问管理接口 → 期望 403
curl -s https://api.<你的域名>.com/api/admin/stats \
  -H "Authorization: Bearer $TOKEN"
# 期望：{"detail":"需要超级管理员权限"}

# 测试 2：超管账号访问管理接口 → 期望 200
ADMIN_TOKEN=$(curl -s -X POST https://api.<你的域名>.com/api/auth/login \
  -H "Content-Type: application/json" \
  -d "{\"email\":\"admin@yourdomain.com\",\"password\":\"<超管密码>\"}" \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

curl -s https://api.<你的域名>.com/api/admin/stats \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  | python3 -m json.tool
# 期望：{"user_count":2,"team_count":1,"workspace_count":1,"document_count":N}

# 测试 3：无 Token 访问受保护接口 → 期望 401
curl -s https://api.<你的域名>.com/api/users/me
# 期望：{"detail":"Not authenticated"}

# 测试 4：跨工作空间访问隔离（创建第二个用户并测试）
# 用 test@example.com 的 Token 访问其他人的工作空间
curl -s "https://api.<你的域名>.com/api/workspaces/<不存在的WS_ID>/documents" \
  -H "Authorization: Bearer $TOKEN"
# 期望：{"detail":"工作空间不存在或无权访问"}（404 或 403）
```

---

## 第十章：运维操作参考

### 10.1 日志查看

```bash
# 实时跟踪日志（Ctrl+C 退出）
docker logs -f teamhub-cloud

# 查看最近 100 行日志
docker logs --tail 100 teamhub-cloud

# 查看特定时间段的日志
docker logs --since "2024-01-01T10:00:00" --until "2024-01-01T11:00:00" teamhub-cloud

# 将日志保存到文件
docker logs teamhub-cloud > /tmp/teamhub-$(date +%Y%m%d).log 2>&1
```

### 10.2 更新部署

```bash
cd /opt/teamhub

# 1. 拉取最新代码
git pull

# 2. 重新构建镜像（--no-cache 强制重新下载依赖，通常不需要）
cd cloud_backend
docker build -t teamhub-cloud:latest .

# 3. 停止并删除旧容器
docker stop teamhub-cloud && docker rm teamhub-cloud

# 4. 启动新容器
docker run -d \
  --name teamhub-cloud \
  --restart unless-stopped \
  --env-file /opt/teamhub/cloud_backend/.env \
  -p 127.0.0.1:9000:9000 \
  teamhub-cloud:latest

# 5. 执行数据库迁移（有新迁移时）
docker exec -it teamhub-cloud python -m alembic upgrade head

# 6. 验证健康状态
curl http://127.0.0.1:9000/api/health
```

> **零停机部署（进阶）**：可将步骤 3-4 合并为 `docker run --name teamhub-cloud-new ...` 启动新容器，验证正常后 `docker stop teamhub-cloud && docker rename teamhub-cloud-new teamhub-cloud`，但本项目单实例场景通常不需要。

### 10.3 健康监控（Crontab 自动重启）

```bash
# 编辑 crontab
crontab -e

# 添加以下行：每 5 分钟检查，失败则重启容器并记录日志
*/5 * * * * curl -sf http://127.0.0.1:9000/api/health || (docker restart teamhub-cloud; echo "$(date): TeamHub 容器已重启" >> /var/log/teamhub-restart.log)
```

```bash
# 查看重启日志
cat /var/log/teamhub-restart.log
```

### 10.4 数据库备份

```bash
# RDS 生产环境：直接使用 RDS 控制台的自动备份功能（默认每天备份，保留 7 天）
# 如需手动备份，在 ECS 上执行：

# 安装 pg_dump（如未安装）
sudo apt-get install -y postgresql-client

# 导出数据库（在 ECS 上执行）
pg_dump -h <RDS内网地址> -U teamhub -d teamhub_cloud \
  -f /opt/teamhub/backup/teamhub_cloud_$(date +%Y%m%d_%H%M%S).sql
# 输入 RDS 密码

# 压缩备份文件
gzip /opt/teamhub/backup/teamhub_cloud_*.sql

# 定期清理 30 天前的备份
find /opt/teamhub/backup -name "*.sql.gz" -mtime +30 -delete
```

### 10.5 容器资源限制（可选）

```bash
# 为容器添加内存和 CPU 限制（防止单容器耗尽资源）
docker update --memory 1g --cpus 1.5 teamhub-cloud

# 查看容器资源使用情况
docker stats teamhub-cloud
```

### 10.6 SSL 证书续期

阿里云免费证书有效期 1 年，到期前 30 天会收到邮件提醒：

1. 重新申请证书（同第七章 7.1 步骤）
2. 上传新证书文件，覆盖旧文件：

```bash
sudo cp 新证书.pem /etc/nginx/ssl/api.yourdomain.com.pem
sudo cp 新证书.key /etc/nginx/ssl/api.yourdomain.com.key
sudo nginx -t && sudo systemctl reload nginx
```

---

## 第十一章：上线安全检查清单

部署完成后，逐项确认以下安全配置：

### 配置安全

```
[ ] .env 文件已从 Git 仓库排除（git ls-files .env 无输出）
[ ] .env 文件权限为 600（ls -la .env 显示 -rw-------）
[ ] JWT_SECRET_KEY 为 openssl rand -hex 32 生成的随机值（非示例值）
[ ] DATABASE_URL 密码非弱密码（非 admin/123456 等）
[ ] OSS 使用 RAM 子账号 AK/SK，不使用主账号
```

### 网络安全

```
[ ] ECS 安全组入方向仅开 22/80/443，无 9000/5432 等内部端口
[ ] 容器仅监听 127.0.0.1:9000（docker ps 确认 PORTS 列显示 127.0.0.1:9000->9000/tcp）
[ ] RDS 白名单仅允许 ECS 内网 IP（不含 0.0.0.0/0）
[ ] OSS Bucket 权限为「私有」（OSS 控制台确认）
[ ] OSS 使用内网 Endpoint（-internal 后缀，不计流量费）
```

### HTTPS 安全

```
[ ] Nginx 已配置 HTTPS，HTTP 301 重定向已验证
[ ] SSL 证书有效（curl -I https://api.yourdomain.com 无证书错误）
[ ] HSTS 头已配置（curl -I https://api.yourdomain.com 包含 Strict-Transport-Security）
[ ] X-Frame-Options DENY 头已配置（防点击劫持）
```

### 运行时安全

```
[ ] Docker 容器以非 root 用户（appuser）运行（docker exec teamhub-cloud whoami 输出 appuser）
[ ] 生产启动命令使用 gunicorn 管理，无 --reload 参数
[ ] 超管账号已创建，邮箱非 admin@example.com 等常见值
```

### 功能验证

```
[ ] GET /api/health 返回 {"status":"ok","database":"connected","oss":"connected"}
[ ] POST /api/auth/register 能成功注册
[ ] POST /api/auth/login 能成功登录并获取 Token
[ ] 普通账号访问 GET /api/admin/stats 返回 403
[ ] 超管账号访问 GET /api/admin/stats 返回正常统计数据
[ ] OSS 预签名上传流程测试通过（第九章 9.5）
[ ] Token Rotation 测试通过（旧 refresh_token 使用后失效）
```

---

## 附录 A：常见错误排查

### A.1 容器启动失败

```bash
# 查看详细错误
docker logs teamhub-cloud 2>&1

# 常见错误及解决方法：
# "could not translate host name ... to address"
#   → 检查 DATABASE_URL 中的 RDS 地址是否正确
# "password authentication failed for user"
#   → 检查 DATABASE_URL 中的密码是否正确
# "endpoint not found" 或 "InvalidAccessKeyId"
#   → 检查 OSS_ACCESS_KEY_ID 和 OSS_ENDPOINT
# "port is already allocated"
#   → 端口 9000 已被占用：lsof -i :9000，或旧容器未删除
```

### A.2 Nginx 502 Bad Gateway

```bash
# 检查 Docker 容器是否在运行
docker ps | grep teamhub-cloud

# 检查端口是否监听
ss -tlnp | grep 9000

# 检查 Nginx 错误日志
sudo tail -f /var/log/nginx/error.log
```

### A.3 OSS 上传失败

```bash
# 在容器内测试 OSS 连通性
docker exec -it teamhub-cloud python -c "
import boto3, os
s3 = boto3.client('s3',
    endpoint_url=os.environ['OSS_ENDPOINT'],
    aws_access_key_id=os.environ['OSS_ACCESS_KEY_ID'],
    aws_secret_access_key=os.environ['OSS_ACCESS_KEY_SECRET'],
)
resp = s3.list_objects_v2(Bucket=os.environ['OSS_BUCKET_NAME'], MaxKeys=1)
print('OSS 连接正常，Bucket 内对象数（最多1）:', len(resp.get('Contents', [])))
"
```

### A.4 CORS 错误（浏览器/Electron 报错）

```bash
# 检查 .env 中 ALLOWED_ORIGINS 是否包含实际来源
# Electron 应用来源：app://.
# 浏览器本地开发：http://localhost:5173

# 临时调试：用 curl 模拟 preflight 请求
curl -v -X OPTIONS https://api.<你的域名>.com/api/health \
  -H "Origin: app://." \
  -H "Access-Control-Request-Method: POST"
# 期望响应包含：Access-Control-Allow-Origin: app://.
```

---

## 附录 B：关键文件速查

| 文件 | 用途 |
|------|------|
| `cloud_backend/DEPLOY_ALIYUN.md` | 本文档 |
| `cloud_backend/.env.production` | 生产环境变量模板（需复制为 .env 填写） |
| `cloud_backend/.env.dev` | 本地开发环境变量（SQLite + MinIO） |
| `cloud_backend/Dockerfile` | Docker 镜像定义（gunicorn + uvicorn workers，非 root 运行） |
| `cloud_backend/docker-compose.yml` | 本地开发一键启动（API + MinIO，不含 PostgreSQL） |
| `cloud_backend/requirements.txt` | Python 依赖（含 gunicorn + psycopg2-binary） |
| `cloud_backend/alembic/versions/` | 数据库迁移脚本目录 |
| `cloud_backend/create_superuser.py` | 超管账号初始化脚本 |
| `cloud_backend/app/config.py` | Pydantic Settings（读取 .env 环境变量） |
| `cloud_backend/app/database.py` | DB 连接（SQLite 开发 / PostgreSQL 生产双支持） |
| `cloud_backend/app/core/deps.py` | 通用依赖（权限检查、工作空间访问控制） |
| `cloud_backend/app/api/auth.py` | 认证路由（register / login / refresh / logout） |
| `cloud_backend/app/api/sync.py` | 增量同步路由（push / pull / 冲突解决） |
| `cloud_backend/app/api/storage.py` | OSS 预签名 URL 路由 |

---

*文档版本：2026-03-10 | TeamHub v1.0*
