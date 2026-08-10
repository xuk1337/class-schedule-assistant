#!/usr/bin/env python3
"""部署初始化：数据库为空时灌入演示数据并创建系统管理员。

用于 Render 等无交互 shell 的平台，随启动命令执行（见 render.yaml）。
幂等：users 表已有数据时直接跳过，不会覆盖线上数据。

环境变量：
    SYSADMIN_PASSWORD  系统管理员初始密码（至少 8 位）；未设置时随机生成，
                       明文仅在启动日志中打印一次，请立即保存。
"""
import os
import secrets
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import create_app
from db import get_db


def main():
    app = create_app()
    with app.app_context():
        db = get_db()
        if db.execute('SELECT 1 FROM users LIMIT 1').fetchone():
            print('数据库非空，跳过演示数据初始化')
            return 0

    # 复用开发种子脚本灌入演示数据；系统管理员密码改由环境变量/随机值控制，
    # 避免公开部署使用 README 中的固定弱口令
    import seed_dev
    password = os.environ.get('SYSADMIN_PASSWORD') or secrets.token_hex(8)
    if len(password) < 8:
        print('错误：SYSADMIN_PASSWORD 长度至少 8 位', file=sys.stderr)
        return 1
    seed_dev.SYSADMIN_PASSWORD = password
    rc = seed_dev.main()
    if rc == 0:
        print(f'系统管理员初始密码：{password}（仅此一次在日志显示，请立即保存并登录修改）')
    return rc


if __name__ == '__main__':
    sys.exit(main())
