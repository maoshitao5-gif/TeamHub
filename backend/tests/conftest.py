"""
MVP 测试全局隔离夹具

在任何 backend.app 模块导入前,把 settings.library_path 指向临时目录,
防止 database.py 建引擎时在仓库根目录生成 teamhub.db,也绝不触碰真实文件库。
"""
import tempfile

from backend.app.config import settings

# conftest 在测试模块导入前加载,此处先占位到独立临时目录
settings.library_path = tempfile.mkdtemp(prefix="teamhub-mvp-tests-")
