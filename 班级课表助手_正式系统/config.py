"""应用配置。

密钥、数据库路径等一律从环境变量读取；缺省值仅供本地开发使用，
生产环境必须通过环境变量覆盖（见 .env.example 与 README.md）。
"""
import os

BASE_DIR = os.path.abspath(os.path.dirname(__file__))

# 仅供本地开发的兜底密钥；APP_ENV=production 时若仍是该值，create_app 会拒绝启动
DEV_SECRET_KEY = 'dev-only-secret-key-do-not-use-in-production'


class Config:
    # Flask session 签名密钥：生产环境必须设置 SECRET_KEY 环境变量
    SECRET_KEY = os.environ.get('SECRET_KEY') or DEV_SECRET_KEY

    # SQLite 数据库文件路径，可用 DATABASE 环境变量覆盖（测试可注入内存/临时库）
    DATABASE = os.environ.get('DATABASE') or os.path.join(BASE_DIR, 'instance', 'classschedule.db')

    # 会话 Cookie 安全项（PRD §9：HttpOnly、SameSite=Lax，生产启用 Secure）
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'
    SESSION_COOKIE_SECURE = os.environ.get('SESSION_COOKIE_SECURE', '0') == '1'

    # 双 CSV 导入：单文件 ≤ 2MB，两份文件 + 表单开销，整体上限 6MB
    MAX_CONTENT_LENGTH = 6 * 1024 * 1024
