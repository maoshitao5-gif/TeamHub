"""
兼容版本的main.py
导入原有main.py的所有路由，保持功能不变
这是一个过渡方案，后续会逐步迁移到完全模块化结构
"""
import sys
from pathlib import Path

# 将根目录添加到路径
root_dir = Path(__file__).parent.parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

# 导入原有main.py的app对象
# 这样可以直接使用所有原有路由，保持功能不变
from main import app

# 导出app对象
__all__ = ['app']
