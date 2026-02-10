"""
数据库迁移脚本：添加 relative_path 列到 files 表
"""
import sqlite3
import os

def migrate_database():
    """添加 relative_path 列到 files 表"""
    db_path = "teamhub.db"
    
    if not os.path.exists(db_path):
        print(f"数据库文件 {db_path} 不存在，将在首次启动时自动创建")
        return
    
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # 检查列是否已存在
        cursor.execute("PRAGMA table_info(files)")
        columns = [column[1] for column in cursor.fetchall()]
        
        if 'relative_path' in columns:
            print("[信息] 列 'relative_path' 已存在，无需迁移")
            conn.close()
            return
        
        # 添加 relative_path 列
        print("[执行] 正在添加 relative_path 列...")
        try:
            cursor.execute("""
                ALTER TABLE files 
                ADD COLUMN relative_path VARCHAR(500) NULL
            """)
            conn.commit()
            print("[成功] 已添加 relative_path 列")
        except sqlite3.OperationalError as e:
            if "duplicate column" in str(e).lower() or "already exists" in str(e).lower():
                print("[信息] 列已存在（可能是并发操作）")
            else:
                raise
        
        conn.close()
        
    except sqlite3.Error as e:
        print(f"[失败] 迁移失败: {e}")
        raise

if __name__ == "__main__":
    migrate_database()
