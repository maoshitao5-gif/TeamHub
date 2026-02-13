# 数据库迁移说明

本项目使用 Alembic 管理数据库迁移。

## 初始化数据库

首次运行项目时，数据库表会自动创建（通过 `init_db()`）。

## 使用 Alembic 进行迁移

### 创建新的迁移

当你修改了数据模型（`backend/app/models.py`）后，需要创建迁移：

```bash
cd backend
alembic revision --autogenerate -m "描述你的修改"
```

### 应用迁移

```bash
cd backend
alembic upgrade head
```

### 回滚迁移

回滚到上一个版本：

```bash
cd backend
alembic downgrade -1
```

回滚到特定版本：

```bash
cd backend
alembic downgrade <revision_id>
```

### 查看迁移历史

```bash
cd backend
alembic history
```

### 查看当前版本

```bash
cd backend
alembic current
```

## 注意事项

1. **总是先备份数据库**再进行迁移操作
2. 迁移文件会保存在 `backend/alembic/versions/` 目录
3. 生产环境部署前，务必在测试环境验证迁移
4. SQLite 不支持某些 ALTER 操作（如删除列），可能需要手动编辑迁移文件

## 常见问题

### Q: 如何重置数据库？

```bash
# 删除数据库文件
rm teamhub.db

# 运行应用，会自动创建新数据库
python main.py
```

### Q: 迁移失败怎么办？

1. 检查迁移文件中的 SQL 语句
2. 手动编辑迁移文件修复问题
3. 或者删除迁移文件重新生成
