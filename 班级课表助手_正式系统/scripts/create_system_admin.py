#!/usr/bin/env python3
"""受控创建系统管理员账号（仅部署/运维使用，无公开注册入口）。

用法：
    python scripts/create_system_admin.py --login-id sysadmin --name 张三
    python scripts/create_system_admin.py --login-id sysadmin --name 张三 --password 'xxxxx'

缺省参数进入交互式输入（密码不回显）。密码至少 8 位。
注意：--password 会留在 shell 历史中，仅在自动化场景使用。
"""
import argparse
import getpass
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from werkzeug.security import generate_password_hash

from app import create_app
from db import get_db

# 本机运行环境（LibreSSL）的 hashlib 无 scrypt，Werkzeug 默认算法会报错，统一显式指定 pbkdf2
HASH_METHOD = 'pbkdf2:sha256'


def main():
    parser = argparse.ArgumentParser(description='创建系统管理员账号（仅部署/运维使用）')
    parser.add_argument('--login-id', help='登录账号')
    parser.add_argument('--name', help='姓名')
    parser.add_argument('--password', help='初始密码（至少 8 位；不传则交互输入）')
    args = parser.parse_args()

    login_id = (args.login_id or input('登录账号: ')).strip()
    name = (args.name or input('姓名: ')).strip()
    if not login_id or not name:
        print('错误：登录账号和姓名不能为空', file=sys.stderr)
        return 1

    password = args.password
    if password is None:
        password = getpass.getpass('初始密码（至少 8 位）: ')
        if getpass.getpass('再次输入密码: ') != password:
            print('错误：两次输入的密码不一致', file=sys.stderr)
            return 1
    if len(password) < 8:
        print('错误：密码长度至少 8 位', file=sys.stderr)
        return 1

    app = create_app()
    with app.app_context():
        db = get_db()
        if db.execute('SELECT 1 FROM users WHERE login_id = ?', (login_id,)).fetchone():
            print(f'错误：登录账号 {login_id!r} 已存在', file=sys.stderr)
            return 1
        cur = db.execute(
            "INSERT INTO users (login_id, name, password_hash, role, status, class_id)"
            " VALUES (?, ?, ?, 'system_admin', 'active', NULL)",
            (login_id, name, generate_password_hash(password, method=HASH_METHOD)),
        )
        db.execute(
            "INSERT INTO audit_logs (actor_user_id, action, target_type, target_id, result, detail_json)"
            " VALUES (NULL, 'system_admin.create', 'user', ?, 'success', ?)",
            (cur.lastrowid, json.dumps({'login_id': login_id}, ensure_ascii=False)),
        )
        db.commit()

    print(f'系统管理员已创建：login_id={login_id}，name={name}')
    print('请通过安全渠道线下交付凭证；密码不会再次显示。')
    return 0


if __name__ == '__main__':
    sys.exit(main())
