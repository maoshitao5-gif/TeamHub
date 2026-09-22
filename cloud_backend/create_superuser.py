"""
创建超级管理员 CLI 工具

用法：
    python create_superuser.py --email admin@example.com --password secret

若用户已存在则将其设为超级管理员；若不存在则创建新用户。
"""
import argparse
import sys
import os

# 确保能导入 app 模块
sys.path.insert(0, os.path.dirname(__file__))

from app.database import SessionLocal, engine, Base
from app.models import User
from app.core.security import hash_password

Base.metadata.create_all(bind=engine)


def create_or_promote(email: str, password: str, display_name: str = "超级管理员"):
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == email).first()
        if user:
            user.is_superuser = True
            user.is_active = True
            if password:
                user.hashed_password = hash_password(password)
            db.commit()
            print(f"[OK] 已将现有用户 {email} 设为超级管理员")
        else:
            user = User(
                email=email,
                hashed_password=hash_password(password),
                display_name=display_name,
                is_active=True,
                is_superuser=True,
            )
            db.add(user)
            db.commit()
            print(f"[OK] 已创建超级管理员账号：{email}")
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="创建或提升超级管理员")
    parser.add_argument("--email", required=True, help="用户邮箱")
    parser.add_argument("--password", required=True, help="登录密码")
    parser.add_argument("--name", default="超级管理员", help="显示名称（新建时使用）")
    args = parser.parse_args()

    create_or_promote(args.email, args.password, args.name)
