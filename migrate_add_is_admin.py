"""
数据库迁移脚本：添加 is_admin 字段到 users 表
"""
import sqlite3
import sys
from pathlib import Path

# 设置Windows控制台编码为UTF-8
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

# 数据库文件路径
DB_PATH = Path("teamhub.db")

def migrate():
    """执行数据库迁移"""
    if not DB_PATH.exists():
        print("数据库文件不存在，无需迁移")
        return
    
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    
    try:
        # 检查 is_admin 列是否已存在
        cursor.execute("PRAGMA table_info(users)")
        columns = [column[1] for column in cursor.fetchall()]
        
        if 'is_admin' in columns:
            print("[OK] is_admin column already exists, no migration needed")
            return
        
        # 添加 is_admin 列
        print("Adding is_admin column...")
        cursor.execute("ALTER TABLE users ADD COLUMN is_admin BOOLEAN DEFAULT 0 NOT NULL")
        
        # 将 admin 用户设置为管理员
        print("Setting admin user as administrator...")
        cursor.execute("UPDATE users SET is_admin = 1 WHERE username = 'admin'")
        
        conn.commit()
        print("[SUCCESS] Migration completed!")
        print("  - Added is_admin column")
        print("  - Set admin user as administrator")
        
    except Exception as e:
        conn.rollback()
        print(f"[ERROR] Migration failed: {e}")
        raise
    finally:
        conn.close()

if __name__ == "__main__":
    migrate()
