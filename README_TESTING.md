# 测试说明

本项目使用 pytest 进行测试。

## 安装测试依赖

```bash
pip install -r requirements.txt
```

## 运行测试

### 运行所有测试

```bash
pytest
```

### 运行特定测试文件

```bash
pytest tests/test_api/test_health.py
```

### 运行特定测试函数

```bash
pytest tests/test_api/test_health.py::test_read_root
```

### 按标记运行测试

```bash
# 只运行单元测试
pytest -m unit

# 只运行集成测试
pytest -m integration

# 排除慢速测试
pytest -m "not slow"
```

### 查看测试覆盖率

```bash
# 安装 pytest-cov
pip install pytest-cov

# 运行测试并生成覆盖率报告
pytest --cov=backend/app --cov-report=html

# 查看 HTML 报告
# 打开 htmlcov/index.html
```

## 测试结构

```
tests/
├── __init__.py
├── conftest.py              # pytest 配置和共享 fixtures
├── test_api/               # API 测试
│   ├── __init__.py
│   ├── test_health.py      # 健康检查
│   └── test_auth.py        # 认证测试
└── test_core/              # 核心功能测试
    ├── __init__.py
    ├── test_validators.py  # 验证器测试
    └── test_security.py    # 安全功能测试
```

## 编写测试

### 单元测试示例

```python
import pytest

@pytest.mark.unit
def test_example():
    assert 1 + 1 == 2
```

### 集成测试示例

```python
import pytest

@pytest.mark.integration
def test_api_endpoint(client):
    response = client.get("/")
    assert response.status_code == 200
```

### 使用 Fixtures

```python
def test_with_db(db, test_user):
    # db 和 test_user 是在 conftest.py 中定义的 fixtures
    from backend.app.models import User
    user = User(**test_user)
    db.add(user)
    db.commit()
```

## 测试最佳实践

1. **独立性**：每个测试应该独立运行，不依赖其他测试
2. **清晰性**：测试名称应该清楚地描述测试内容
3. **简洁性**：一个测试只测试一个功能点
4. **覆盖率**：目标是达到 80% 以上的代码覆盖率
5. **快速性**：测试应该快速运行（单元测试 < 100ms）

## 持续集成

在 CI/CD 流程中，可以这样运行测试：

```bash
# 运行测试并生成 JUnit XML 报告
pytest --junitxml=test-results.xml

# 运行测试并生成覆盖率报告
pytest --cov=backend/app --cov-report=xml
```

## 常见问题

### Q: 测试数据库文件在哪里？

测试使用独立的 SQLite 数据库（`test.db`），每次测试后会自动清理。

### Q: 如何调试失败的测试？

```bash
# 显示详细输出
pytest -vv

# 在失败时进入调试器
pytest --pdb

# 显示 print 输出
pytest -s
```

### Q: 如何跳过某个测试？

```python
@pytest.mark.skip(reason="暂时跳过")
def test_something():
    pass
```
