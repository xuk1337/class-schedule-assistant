# 班级课表助手 · 演示部署镜像（ClawCloud Run 等容器平台通用）
# 构建上下文为仓库根目录：docker build -t class-schedule-assistant .
FROM python:3.13-slim

WORKDIR /app

# 先装依赖再拷源码，利用 Docker 层缓存
COPY 班级课表助手_正式系统/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY 班级课表助手_正式系统/ ./

# 会话 Cookie 安全项（平台已终结 TLS）；SECRET_KEY / SYSADMIN_PASSWORD 在平台环境变量中设置
ENV SESSION_COOKIE_SECURE=1

EXPOSE 8000

# 启动前初始化：数据库为空时幂等灌入演示数据（见 scripts/init_deploy.py）
CMD ["sh", "-c", "python scripts/init_deploy.py && gunicorn -w 1 --threads 4 -b 0.0.0.0:${PORT:-8000} 'app:create_app()'"]
