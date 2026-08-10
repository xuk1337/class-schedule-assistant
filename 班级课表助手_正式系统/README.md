# 班级课表助手 · 正式系统

Python 3 + Flask 3.1 + SQLite + 原生 HTML/CSS/JS 单页前端。需求基线见
[《PRD产品需求文档.md》](../项目文档/01_需求与设计/PRD产品需求文档.md)
v2.0（§4 功能清单、§6.8 API 契约、§6.9 数据模型）。

## 项目结构

```
班级课表助手_正式系统/
├── app.py                      # Flask 应用工厂：CSRF 中间件、请求 ID、安全响应头、静态托管、蓝图注册
├── config.py                   # 配置（密钥/路径从环境变量读取，见 .env.example）
├── db.py                       # SQLite 连接（Flask g 缓存、外键开启）、幂等初始化
├── utils.py                    # 统一 JSON 响应、require_roles 鉴权、审计、邀请码生成与摘要
├── database/
│   └── schema.sql              # 9 张表 + 约束 + 唯一/部分唯一索引（幂等，可重复执行）
├── routes_auth.py              # 认证 API（/api/auth）
├── routes_system.py            # 系统管理 API（/api/system）
├── routes_business.py          # 业务 API（/api/schedule、/api/courses、/api/course-sessions、
│                               #   /api/homework、/api/teachers、/api/classes/me、/api/import）
├── scripts/
│   ├── create_system_admin.py  # 受控创建系统管理员（部署/运维修用）
│   └── seed_dev.py             # 开发种子数据（先清库重灌，幂等）
├── static/                     # 前端：index.html（课表 SPA）、admin.html（系统管理后台）、js/css
├── instance/                   # 运行时生成的 SQLite 数据库（勿提交版本库）
├── requirements.txt
├── requirements-dev.txt        # 开发与自动化测试依赖
└── .env.example
```

> `routes_*.py` 与 `static/` 由并行工作包交付；缺省时 `create_app()` 会以
> ImportError 明确暴露缺失模块。

## 快速开始（开发环境）

Windows PowerShell：

```powershell
cd 班级课表助手_正式系统

# 1. 创建虚拟环境并安装开发依赖
py -3 -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements-dev.txt

# 2. 灌入开发种子数据（每次先清库重灌，并打印演示账号与邀请码）
.\.venv\Scripts\python scripts\seed_dev.py

# 3. 启动服务
.\.venv\Scripts\python app.py
```

macOS 或 Linux：

```bash
cd 班级课表助手_正式系统
python3 -m venv .venv
./.venv/bin/python -m pip install -r requirements-dev.txt
./.venv/bin/python scripts/seed_dev.py
./.venv/bin/python app.py
```

访问课表主页 `http://127.0.0.1:5000/`，系统管理后台为
`http://127.0.0.1:5000/admin`。

首次启动会自动按 `database/schema.sql` 幂等建表；测试或脚本可用
`create_app({'DATABASE': '...'})` 覆盖数据库路径。

## 自动化测试

测试使用临时数据库，不会修改 `instance/classschedule.db`：

```powershell
# Windows PowerShell
.\.venv\Scripts\python -m pytest -q
```

```bash
# macOS 或 Linux
./.venv/bin/python -m pytest -q
```

## 演示账号（仅供开发环境，禁止用于生产）

由 `scripts/seed_dev.py` 生成，密码与邀请码以脚本当次输出为准：

| 角色 | 账号 | 密码 | 说明 |
| --- | --- | --- | --- |
| 系统管理员 | `sysadmin` | `sysadmin123` | 进入 `/admin` 系统管理后台 |
| 班级管理员 | `admin2301` | `admin1234` | 软件技术2301班 |
| 班级管理员 | `admin2302` | `admin1234` | 软件技术2302班 |
| 学生（已激活） | `2023301001`–`2023301005` | `student123` | 软件技术2301班 |
| 学生（已激活） | `2023302001`–`2023302005` | `student123` | 软件技术2302班 |
| 学生（待激活） | `2023301006`–`2023301008`、`2023302006`–`2023302008` | 激活时自设 | 需凭当次种子输出的班级邀请码激活 |

班级演示邀请码在每次运行种子脚本时随机生成并打印一次，数据库只保存 sha256 摘要。

## CSV 模板格式（双 CSV 整体导入，PRD §6.6）

仅班级管理员可用；两份 CSV 均不含班级列，数据自动归属当前管理员绑定班级与当前学期；
统一校验失败整体回滚，确认后在同一事务中覆盖本班当前学期全部课程与作业。
编码 UTF-8（支持 BOM），标准 CSV 引号转义；单文件 ≤ 2MB；课程 ≤ 100 行、作业 ≤ 300 行。

**课程 CSV**（表头固定，同一课程编号可多行，每行一条上课安排；同编号的课程名/教师/考试日期/备注必须一致）：

```csv
课程编号,课程名,教师,教室,星期,开始节次,结束节次,起始周,结束周,考试日期,备注
MATH-01,高等数学,张思源,教学楼301,1,1,2,1,20,2026-12-20,考试时携带计算器
MATH-01,高等数学,张思源,教学楼305,3,3,4,1,20,2026-12-20,考试时携带计算器
```

- 课程编号：仅字母、数字、`_`、`-`；星期 1–7；节次 1–12；周次不超过当前学期周数
- 考试日期可空，非空必须为 `YYYY-MM-DD`

**作业 CSV**（表头固定，`课程编号` 必须引用同批课程 CSV 中存在的课程）：

```csv
课程编号,作业内容,截止日期
MATH-01,习题集第 5 章第 1-20 题,2026-09-10
```

- 截止日期必须为 `YYYY-MM-DD`；同课程同内容同截止日期的作业不得重复

学生名单 CSV（系统管理后台导入）表头须包含「姓名」「学号」两列（顺序不限，兼容 name/student_no 英文表头）；
导入后学生为待激活状态，不设置密码。任一行校验失败整批回滚并逐行报错。

## 生产部署要点

1. **环境变量**：复制 `.env.example`，必须设置独立的 `SECRET_KEY`
   （`python -c "import secrets; print(secrets.token_hex(32))"`），
   `APP_ENV=production`、`SESSION_COOKIE_SECURE=1`（需 HTTPS）；
   `DATABASE` 指向受控目录。`APP_ENV=production` 下缺省密钥会拒绝启动，
   `seed_dev.py` 拒绝执行。
2. **初始化**：
   - 数据库：应用启动时自动幂等建表（或提前执行一次任意启动命令）。
   - 首个系统管理员：`python scripts/create_system_admin.py --login-id <账号> --name <姓名>`
     （密码交互输入，至少 8 位；该命令是唯一的系统管理员创建入口）。
3. **WSGI 启动**（开发服务器勿用于生产）：
   - gunicorn：`gunicorn -w 2 -b 127.0.0.1:8000 'app:create_app()'`
   - waitress：`waitress-serve --listen=127.0.0.1:8000 app:create_app`
   - 前置 Nginx/Caddy 终结 TLS 并反代。
4. **备份恢复**：SQLite 单文件，停机或使用 `sqlite3 <db> ".backup '<备份路径>'"`
   在线备份；恢复时替换数据库文件并重启，恢复后执行
   `PRAGMA foreign_key_check;` 校验外键并核对关键记录数量。建议纳入定时任务并定期演练恢复。
5. **安全基线**：生产页面、源码与日志中不得出现演示账号、固定密码或邀请码明文；
   会话 Cookie 已启用 HttpOnly + SameSite=Lax，所有 `/api/` 写请求强制 CSRF 头校验。

## 在线演示部署（Render）

仓库根目录的 `render.yaml` 是 Render Blueprint 配置：在
[Render](https://render.com) 用 GitHub 登录 → New → Blueprint → 选择本仓库即可，
部署时按提示设置 `SYSADMIN_PASSWORD`（系统管理员初始密码）。

- 启动命令先执行 `scripts/init_deploy.py`：数据库为空时自动灌入演示数据
  （幂等，非空跳过），系统管理员密码取 `SYSADMIN_PASSWORD`，未设置则随机生成并
  仅在启动日志打印一次。
- 免费方案限制：15 分钟无访问会休眠（首次打开约 30 秒冷启动）；磁盘为临时磁盘，
  重启后演示数据重置。请勿在该标记下存放真实数据。
- 该演示部署不设置 `APP_ENV=production`（否则初始化脚本按安全约定拒绝灌入演示数据）；
  独立 `SECRET_KEY`、`SESSION_COOKIE_SECURE` 等安全项均已显式开启。
