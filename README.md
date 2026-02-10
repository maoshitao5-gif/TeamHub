# 团队文件管理系统

一个基于 FastAPI 的团队文件管理系统，支持标签化文件共享，适用于 300 人规模的团队。

## 功能特性

- ✅ 文件上传与存储
- ✅ SHA-256 哈希值查重（防止重复上传）
- ✅ 标签管理系统
- ✅ 多标签文件关联（多对多关系）
- ✅ 文件搜索（支持关键词和标签搜索）

## 技术栈

- **Web 框架**: FastAPI
- **ORM**: SQLAlchemy
- **数据库**: SQLite（开发环境，可轻松切换至 PostgreSQL/MySQL）
- **Python 版本**: 3.7+

## 项目结构

```
TeamHub/
├── main.py              # FastAPI 应用主入口，包含所有 API 路由
├── models.py            # 数据库模型定义（File, Tag, FileTag）
├── database.py          # 数据库配置和连接管理
├── requirements.txt     # Python 依赖包列表
├── storage/             # 文件存储目录（自动创建）
└── teamhub.db          # SQLite 数据库文件（自动创建）
```

## 快速开始

### 方式一：一键启动（推荐）

**Windows 系统：**
双击运行 `start.bat` 文件

**macOS/Linux 系统：**
```bash
chmod +x start.sh
./start.sh
```

### 方式二：手动启动

#### 1. 安装后端依赖

```bash
pip install -r requirements.txt
```

#### 2. 启动后端服务

```bash
python main.py
```

后端将在 `http://localhost:8080` 启动。

#### 3. 启动前端服务

打开新的终端窗口：

```bash
cd frontend
npm install    # 首次运行需要安装依赖
npm run dev
```

前端将在 `http://localhost:5173` 启动。

#### 4. 访问应用

打开浏览器访问：**http://localhost:5173**

### API 文档

后端启动后，可以访问以下地址查看交互式 API 文档：

- Swagger UI: http://localhost:8080/docs
- ReDoc: http://localhost:8080/redoc

## API 接口说明

### 1. POST /upload - 上传文件

上传文件并关联标签。

**请求参数：**
- `file`: 文件对象（multipart/form-data）
- `tags`: 标签列表（字符串数组）

**示例：**
```bash
curl -X POST "http://localhost:8080/upload" \
  -F "file=@example.pdf" \
  -F "tags=项目文档" \
  -F "tags=重要"
```

**响应：**
```json
{
  "id": 1,
  "original_filename": "example.pdf",
  "file_size": 102400,
  "sha256_hash": "abc123...",
  "upload_time": "2024-01-01T12:00:00",
  "tags": ["项目文档", "重要"]
}
```

**错误响应：**
- `400`: 文件已存在（相同 SHA-256 哈希值）

### 2. GET /tags - 获取所有标签

获取系统中所有已存在的标签列表。

**示例：**
```bash
curl "http://localhost:8080/tags"
```

**响应：**
```json
{
  "tags": [
    {"id": 1, "name": "项目文档"},
    {"id": 2, "name": "重要"}
  ],
  "total": 2
}
```

### 3. POST /search - 搜索文件

根据关键词或标签搜索文件。

**请求体（JSON）：**
```json
{
  "keywords": ["项目"],
  "tags": ["重要"]
}
```

**示例：**
```bash
curl -X POST "http://localhost:8080/search" \
  -H "Content-Type: application/json" \
  -d '{"keywords": ["项目"], "tags": ["重要"]}'
```

**响应：**
```json
{
  "files": [
    {
      "id": 1,
      "original_filename": "example.pdf",
      "file_size": 102400,
      "sha256_hash": "abc123...",
      "upload_time": "2024-01-01T12:00:00",
      "tags": ["项目文档", "重要"]
    }
  ],
  "total": 1
}
```

## 数据模型

### File 表
- `id`: 文件ID（主键）
- `original_filename`: 原始文件名
- `storage_path`: 存储路径
- `sha256_hash`: SHA-256 哈希值（唯一，用于查重）
- `file_size`: 文件大小（字节）
- `upload_time`: 上传时间

### Tag 表
- `id`: 标签ID（主键）
- `name`: 标签名称（唯一）

### FileTag 中间表
- `file_id`: 文件ID（外键）
- `tag_id`: 标签ID（外键）

## 开发说明

### 数据库迁移

当前使用 SQLite，如需切换到其他数据库（如 PostgreSQL），只需修改 `database.py` 中的 `SQLALCHEMY_DATABASE_URL`：

```python
# PostgreSQL 示例
SQLALCHEMY_DATABASE_URL = "postgresql://user:password@localhost/dbname"
```

### 文件存储

- 文件存储在 `storage/` 目录下
- 文件名格式：`{hash前8位}_{原始文件名}`，避免文件名冲突
- 使用 SHA-256 哈希值进行文件查重

### 扩展建议

- 添加用户认证和权限管理
- 添加文件下载接口
- 添加文件删除接口
- 添加文件版本管理
- 添加文件预览功能
- 添加文件访问日志
- 添加文件大小限制和类型限制

## 许可证

MIT License
