"""SQLite 连接管理：Flask g 缓存、外键约束、幂等建表。"""
import os
import sqlite3

from flask import current_app, g

SCHEMA_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'database', 'schema.sql')


def get_db():
    """返回当前请求/上下文的 sqlite3.Connection（row_factory=sqlite3.Row）。

    每个连接开启外键约束，并设置 busy timeout 降低写锁冲突概率。
    事务由调用方控制：写操作后自行 commit()，不 commit 的连接关闭时自动回滚。
    """
    if 'db' not in g:
        conn = sqlite3.connect(current_app.config['DATABASE'])
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA foreign_keys = ON')
        conn.execute('PRAGMA busy_timeout = 5000')
        g.db = conn
    return g.db


def close_db(e=None):
    """关闭当前上下文的连接（未提交的事务随连接关闭回滚）。"""
    db = g.pop('db', None)
    if db is not None:
        db.close()


def init_db(app):
    """按 database/schema.sql 幂等建表建索引，可重复执行。"""
    with app.app_context():
        db = get_db()
        with open(SCHEMA_PATH, 'r', encoding='utf-8') as f:
            db.executescript(f.read())
        db.commit()
