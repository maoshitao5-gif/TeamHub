# TeamHub 云服务阿里云部署指引

---

## 一、架构概述

```
用户/桌面端
    ↓ HTTPS
阿里云 SLB（负载均衡）
    ↓
ECS（云服务器）
├── Docker：TeamHub 云服务 API（端口 9000）
└── Nginx：反向代理 + HTTPS 终止（端口 80/443）
    ↓
RDS PostgreSQL（托管数据库）
    ↓
阿里云 OSS（对象存储，服务端持有 AK/SK）
```

---

## 二、前期准备

### 2.1 所需阿里云资源

| 资源 | 规格建议 | 用途 |
|------|---------|------|
| ECS | 2核4G，Ubuntu 22.04 | 运行 Docker 容器 |
| RDS PostgreSQL | PostgreSQL 14，1核2G | 云端数据库 |
| OSS Bucket | 标准存储，同地域 | 文件存储 |
| SLB（可选） | 按量付费 | 生产高可用 |
| 域名 + SSL | 已备案域名 + 免费 DV 证书 | HTTPS 访问 |

### 2.2 网络规划

- ECS 和 RDS **必须在同一 VPC、同一可用区**（内网连接，零费用零延迟）
- OSS Bucket 与 ECS 同 Region（内网访问走内网 endpoint，不产生流量费）
- ECS 安全组开放端口：22（SSH）、80（HTTP）、443（HTTPS）

---

## 三、ECS 服务器初始化

### 3.1 安装 Docker

```bash
# 更新系统
sudo apt-get update && sudo apt-get upgrade -y

# 安装 Docker
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER
newgrp docker

# 验证
docker --version
```

### 3.2 安装 Nginx

```bash
sudo apt-get install -y nginx
sudo systemctl enable nginx
```

### 3.3 上传项目代码

```bash
# 方式一：从 Git 拉取
git clone https://your-git-repo/teamhub.git /opt/teamhub
cd /opt/teamhub/cloud_backend

# 方式二：本地打包上传
# 在本地执行：
scp -r cloud_backend/ user@your-ecs-ip:/opt/teamhub/
```

---

## 四、RDS PostgreSQL 配置

### 4.1 在控制台操作

1. 创建 RDS PostgreSQL 14 实例，选择与 ECS 相同的 VPC
2. 创建数据库：`teamhub_cloud`
3. 创建账号：`teamhub`，赋予 `teamhub_cloud` 数据库的所有权限
4. 白名单：添加 ECS 的**内网 IP**（不要用 0.0.0.0/0）

### 4.2 获取连接信息

在 RDS 控制台 → 数据库连接 → 查看**内网地址**：

```
内网地址：rm-xxxx.pg.rds.aliyuncs.com
端口：5432
数据库：teamhub_cloud
用户名：teamhub
```

---

## 五、阿里云 OSS 配置

### 5.1 创建 Bucket

1. 控制台 → OSS → 创建 Bucket
   - 名称：`teamhub-prod`（全局唯一）
   - 地域：与 ECS 相同（如：华东1-杭州）
   - 存储类型：标准存储
   - 读写权限：**私有**（重要！文件通过预签名 URL 访问）

2. 记录**内网 Endpoint**（ECS 访问走内网，不收流量费）：

   ```
   https://oss-cn-hangzhou-internal.aliyuncs.com
   ```

### 5.2 创建专用 RAM 子账号

> **安全原则：不使用主账号 AK/SK，为云服务创建最小权限子账号**

1. 控制台 → RAM → 用户 → 创建用户
   - 用户名：`teamhub-oss-service`
   - 勾选：**编程访问**（生成 AccessKey）
   - 保存 AccessKeyId 和 AccessKeySecret（**只显示一次**）

2. 创建自定义权限策略：

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
           "oss:GetBucketInfo"
         ],
         "Resource": [
           "acs:oss:*:*:teamhub-prod",
           "acs:oss:*:*:teamhub-prod/*"
         ]
       }
     ]
   }
   ```

3. 将该策略绑定到 `teamhub-oss-service` 账号

---

## 六、配置生产环境变量

在 ECS 上编辑配置文件（**此文件禁止提交 Git**）：

```bash
cd /opt/teamhub/cloud_backend
nano .env
```

填入以下内容：

```ini
# ── 数据库（RDS 内网地址）──
DATABASE_URL=postgresql://teamhub:你的密码@rm-xxxx.pg.rds.aliyuncs.com:5432/teamhub_cloud

# ── JWT（必须替换，生成命令见下方）──
JWT_SECRET_KEY=在此填入随机64位字符串
JWT_ALGORITHM=HS256
JWT_ACCESS_TOKEN_EXPIRE_MINUTES=60
JWT_REFRESH_TOKEN_EXPIRE_DAYS=30

# ── 阿里云 OSS（使用内网 Endpoint，ECS 访问不收费）──
OSS_ENDPOINT=https://oss-cn-hangzhou-internal.aliyuncs.com
OSS_ACCESS_KEY_ID=RAM子账号的AccessKeyId
OSS_ACCESS_KEY_SECRET=RAM子账号的AccessKeySecret
OSS_BUCKET_NAME=teamhub-prod
OSS_PREFIX=teamhub

# ── CORS（填入前端实际域名）──
ALLOWED_ORIGINS=https://your-domain.com,https://www.your-domain.com

# ── 端口 ──
PORT=9000
```

生成随机 JWT 密钥：

```bash
openssl rand -hex 32
```

---

## 七、Docker 构建与启动

```bash
cd /opt/teamhub/cloud_backend

# 构建镜像
docker build -t teamhub-cloud:latest .

# 启动容器
docker run -d \
  --name teamhub-cloud \
  --restart unless-stopped \
  --env-file .env \
  -p 127.0.0.1:9000:9000 \
  teamhub-cloud:latest

# 验证启动
docker logs -f teamhub-cloud
curl http://127.0.0.1:9000/api/health
```

> `-p 127.0.0.1:9000:9000` 只监听本机回环，由 Nginx 代理对外，**不直接暴露 9000 端口到公网**

---

## 八、Nginx 反向代理 + HTTPS

### 8.1 申请 SSL 证书

阿里云控制台 → SSL 证书 → 免费证书（DV，20 张/年，有效期 3 个月，支持自动续期）

下载证书，上传到 ECS：

```bash
sudo mkdir -p /etc/nginx/ssl
sudo cp your-domain.pem /etc/nginx/ssl/
sudo cp your-domain.key /etc/nginx/ssl/
```

### 8.2 Nginx 配置

```bash
sudo nano /etc/nginx/sites-available/teamhub-cloud
```

```nginx
# HTTP → HTTPS 重定向
server {
    listen 80;
    server_name api.your-domain.com;
    return 301 https://$host$request_uri;
}

# HTTPS 反向代理
server {
    listen 443 ssl http2;
    server_name api.your-domain.com;

    ssl_certificate     /etc/nginx/ssl/your-domain.pem;
    ssl_certificate_key /etc/nginx/ssl/your-domain.key;
    ssl_protocols       TLSv1.2 TLSv1.3;
    ssl_ciphers         HIGH:!aNULL:!MD5;

    # 安全响应头
    add_header X-Frame-Options DENY;
    add_header X-Content-Type-Options nosniff;
    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;

    # 文件上传大小限制（文档文件）
    client_max_body_size 500m;

    location / {
        proxy_pass         http://127.0.0.1:9000;
        proxy_set_header   Host $host;
        proxy_set_header   X-Real-IP $remote_addr;
        proxy_set_header   X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;
        proxy_read_timeout 120s;
    }
}
```

```bash
# 启用配置
sudo ln -s /etc/nginx/sites-available/teamhub-cloud /etc/nginx/sites-enabled/
sudo nginx -t          # 检查配置语法
sudo systemctl reload nginx
```

### 8.3 DNS 配置

在域名服务商控制台添加 A 记录：

| 主机记录 | 记录类型 | 记录值 | TTL |
|---------|---------|-------|-----|
| api | A | ECS 公网 IP | 600 |

---

## 九、数据库初始化

容器首次启动时会自动调用 `init_db()` 创建所有表。手动验证：

```bash
docker exec -it teamhub-cloud python -c "
from app.database import init_db, engine
from sqlalchemy import inspect
init_db()
tables = inspect(engine).get_table_names()
print('已创建表：', tables)
"
```

正常输出应包含：
```
已创建表：['users', 'refresh_tokens', 'teams', 'team_members', 'workspaces',
           'cloud_documents', 'cloud_versions', 'cloud_tags', 'cloud_document_tags',
           'cloud_change_logs', 'devices']
```

---

## 十、更新前端配置

将 `frontend/.env.production` 中的云服务地址改为生产域名：

```ini
# frontend/.env.production
VITE_CLOUD_API_URL=https://api.your-domain.com
```

重新构建前端并打包 Electron 应用：

```bash
cd frontend && npm run build
npm run electron:build
```

---

## 十一、运维操作

### 查看日志

```bash
docker logs -f teamhub-cloud           # 实时日志
docker logs --tail 100 teamhub-cloud   # 最近 100 行
docker logs --since 1h teamhub-cloud   # 最近 1 小时
```

### 更新部署

```bash
cd /opt/teamhub/cloud_backend
git pull
docker build -t teamhub-cloud:latest .
docker stop teamhub-cloud && docker rm teamhub-cloud
docker run -d \
  --name teamhub-cloud \
  --restart unless-stopped \
  --env-file .env \
  -p 127.0.0.1:9000:9000 \
  teamhub-cloud:latest
docker logs -f teamhub-cloud
```

### 健康监控（Crontab）

```bash
crontab -e

# 每 5 分钟检查一次，失败则自动重启容器
*/5 * * * * curl -sf http://127.0.0.1:9000/api/health || docker restart teamhub-cloud >> /var/log/teamhub-health.log 2>&1
```

### 清理旧镜像

```bash
docker image prune -f
```

---

## 十二、安全上线清单

上线前逐项打勾确认：

- [ ] `.env` 文件不在 Git 仓库中（`.gitignore` 已包含）
- [ ] `JWT_SECRET_KEY` 已替换为 `openssl rand -hex 32` 生成的随机字符串
- [ ] OSS 使用 RAM 子账号，权限仅限 `teamhub-prod` Bucket 的读写删
- [ ] ECS 安全组未开放 9000 端口（只开 22 / 80 / 443）
- [ ] RDS 白名单只允许 ECS 内网 IP，未配置 0.0.0.0/0
- [ ] Nginx 已配置 HTTPS，HTTP 已 301 重定向到 HTTPS
- [ ] OSS Bucket 读写权限为**私有**（文件通过预签名 URL 访问）
- [ ] Docker 容器以非 root 用户（appuser）运行
- [ ] 生产启动命令不含 `--reload` 参数
- [ ] `curl https://api.your-domain.com/api/health` 返回 `{"status":"ok"}`

---

## 附录：常用命令速查

```bash
# 进入容器 shell
docker exec -it teamhub-cloud bash

# 查看容器资源占用
docker stats teamhub-cloud

# 重启容器
docker restart teamhub-cloud

# 查看 Nginx 错误日志
sudo tail -f /var/log/nginx/error.log

# 测试 API
curl https://api.your-domain.com/api/health
curl -X POST https://api.your-domain.com/api/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email":"admin@example.com","password":"Admin123456","display_name":"管理员"}'
```
