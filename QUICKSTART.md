# 🚀 快速开始指南

## 📦 安装依赖

### 1. 后端依赖

```bash
pip install -r requirements.txt
```

### 2. 前端依赖

```bash
cd frontend
npm install
```

---

## ⚙️ 配置

### 1. 环境变量配置

复制环境变量模板：

```bash
# 已经创建了 .env 文件，可以直接使用
# 如果需要修改，编辑 .env 文件

# Windows
notepad .env

# macOS/Linux
nano .env
```

**重要：** 生产环境请修改 `SECRET_KEY`！

```env
SECRET_KEY=dev-secret-key-for-local-testing-only-change-in-production
```

生成安全的密钥：

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

---

## 🗄️ 数据库初始化

应用会自动创建数据库，无需手动操作。

**可选：使用 Alembic 管理迁移**

```bash
cd backend
alembic upgrade head
```

查看迁移文档：`README_DATABASE.md`

---

## ▶️ 启动应用

### 方法一：一键启动（推荐）

**Windows：**
```bash
start.bat
```

**macOS/Linux：**
```bash
chmod +x start.sh
./start.sh
```

### 方法二：手动启动

**1. 启动后端：**

```bash
# Windows
python main.py

# macOS/Linux
python3 main.py
```

后端运行在：http://localhost:8080

**2. 启动前端（新终端）：**

```bash
cd frontend
npm run dev
```

前端运行在：http://localhost:5173

---

## 🧪 运行测试

```bash
# 运行所有测试
pytest

# 只运行单元测试
pytest -m unit

# 查看测试覆盖率
pytest --cov=backend/app --cov-report=html
```

查看测试文档：`README_TESTING.md`

---

## 📖 API 文档

启动后端后，访问：

- **Swagger UI：** http://localhost:8080/docs
- **ReDoc：** http://localhost:8080/redoc

---

## 🔑 默认管理员账号

```
用户名：admin
密码：admin123
```

**⚠️ 生产环境请立即修改密码！**

---

## 📁 项目结构

```
TeamHub/
├── backend/              # 后端代码
│   ├── app/
│   │   ├── api/         # API 路由
│   │   ├── core/        # 核心功能
│   │   ├── middleware/  # 中间件
│   │   ├── models.py    # 数据模型
│   │   ├── database.py  # 数据库配置
│   │   └── config.py    # 应用配置
│   └── alembic/         # 数据库迁移
├── frontend/            # 前端代码
│   └── src/
├── tests/               # 测试代码
├── logs/                # 日志文件
├── storage/             # 文件存储
├── .env                 # 环境变量
└── requirements.txt     # Python 依赖

```

---

## 🐛 故障排除

### 问题 1：找不到模块

```bash
# 重新安装依赖
pip install -r requirements.txt
```

### 问题 2：数据库错误

```bash
# 删除数据库重新开始
rm teamhub.db
python main.py
```

### 问题 3：端口被占用

修改端口：

```python
# main.py 最后一行
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8081)  # 修改端口
```

### 问题 4：前端无法连接后端

检查 CORS 配置：

```python
# backend/app/config.py
allowed_origins = [
    "http://localhost:5173",  # 确保包含前端地址
    ...
]
```

---

## 📚 更多文档

- `OPTIMIZATION_SUMMARY.md` - 优化总结
- `README_DATABASE.md` - 数据库迁移指南
- `README_TESTING.md` - 测试指南
- `README.md` - 项目说明

---

## ✨ 新特性

### ✅ 配置管理

- 使用环境变量管理配置
- 支持 .env 文件
- 类型安全的配置

### ✅ 日志系统

- 统一的日志格式
- 文件和控制台双输出
- 日志轮转

### ✅ 数据验证

- 文件大小限制（默认 1GB）
- 文件类型验证
- 标签验证

### ✅ 数据库迁移

- Alembic 版本控制
- 自动检测模型变更
- 安全的数据库升级

### ✅ 测试框架

- pytest 测试框架
- 单元测试和集成测试
- 测试覆盖率报告

---

## 🔒 安全提示

1. **修改默认密码**
   - 管理员密码
   - SECRET_KEY

2. **环境变量**
   - 不要提交 .env 到 Git
   - 生产环境使用强密钥

3. **文件上传**
   - 已设置大小限制
   - 可配置允许的文件类型

4. **日志**
   - 定期清理日志文件
   - 注意日志中的敏感信息

---

## 💡 开发建议

### 代码格式化

```bash
# 安装工具
pip install black isort flake8

# 格式化代码
black backend/
isort backend/

# 代码检查
flake8 backend/
```

### 提交前检查

```bash
# 运行测试
pytest

# 代码格式化
black backend/

# 启动应用确认无错误
python main.py
```

---

## 📞 获取帮助

- 查看日志：`logs/teamhub.log`
- 运行测试：`pytest -v`
- 查看 API 文档：http://localhost:8080/docs

---

**祝你使用愉快！** 🎉
