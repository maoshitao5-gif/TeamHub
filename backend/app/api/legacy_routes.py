"""
遗留路由兼容模块
临时方案：导入原有main.py中的所有路由并重新注册
后续会逐步迁移到各个模块中
"""
import sys
from pathlib import Path
from fastapi import APIRouter

# 将根目录添加到路径
root_dir = Path(__file__).parent.parent.parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

# 创建路由器
legacy_router = APIRouter()

# 注意：由于原有main.py中的路由是直接注册到app上的
# 我们需要采用不同的策略来迁移
# 方案1：直接导入原有main.py的app对象（但会导致循环导入）
# 方案2：逐步将路由函数迁移到各个模块（推荐）

# 这里我们暂时不导入，而是逐步迁移
# 所有路由会逐步迁移到 app.api.v1 下的各个模块中

# TODO: 将所有路由迁移到对应的模块
# - 文件路由 -> app.api.v1.files
# - 标签路由 -> app.api.v1.tags
# - 搜索路由 -> app.api.v1.search
# - 管理路由 -> app.api.v1.admin
