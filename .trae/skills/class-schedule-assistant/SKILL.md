---
name: "class-schedule-assistant"
description: "Guides development, maintenance, and testing of the Flask class schedule assistant. Invoke for code, API, database, UI, security, documentation, or test changes in this project."
---

# Class Schedule Assistant

## Purpose

Use this skill when working on the “班级课表助手” project. It guides AI-assisted analysis, implementation, testing, review, and documentation while preserving the formal system’s permission, data-isolation, transaction, and security rules.

This skill applies when the user asks to:

- Add, modify, debug, or review a project feature.
- Change a Flask API, SQLite schema, frontend page, CSV import, or PDF behavior.
- Work on authentication, class isolation, role permissions, invite codes, or account lifecycle.
- Write or update tests, README, PRD, architecture, task, deployment, or user documentation.
- Investigate a mismatch between the implementation and the product documents.

## Project Root

Treat the folder containing this `.trae` directory as the workspace root.

Important paths:

```text
README.md
项目文档/
  项目介绍文档.md
  01_需求与设计/
    项目需求搜集清单.md
    PRD产品需求文档.md
    技术架构说明书.md
    tasks.md
    架构图.html
  02_使用与交付/
    使用手册.md
    班级课表助手_交接文档.md
    📋 提交说明.md
  03_测试验收/
    测试报告.md
    审查报告.md
  04_答辩材料/
    班级课表助手_答辩PPT.pptx
    答辩素材/
班级课表助手_正式系统/
  app.py
  config.py
  db.py
  utils.py
  database/schema.sql
  routes_auth.py
  routes_business.py
  routes_system.py
  scripts/
  static/
  tests/
```

The current implementation is the runtime source of truth. The PRD defines intended behavior. When they disagree:

1. Identify the mismatch explicitly.
2. Determine whether the code or document is stale.
3. Preserve confirmed business rules.
4. Update implementation, tests, and related documents together.
5. Never claim completion without verification evidence.

## Technology Baseline

- Python 3.9+
- Flask 3.1
- SQLite with foreign keys enabled
- Werkzeug password hashing
- Server-side Flask Session
- Native HTML, CSS, and JavaScript
- Pytest for automated tests

Do not introduce a new framework or database unless the user explicitly requests a migration and the change is justified.

## Mandatory Business Rules

### Roles and account status

The formal system has three roles:

```text
student
admin
system_admin
```

Supported account statuses:

```text
pending
active
disabled
```

Rules:

- Students cannot freely register.
- A system administrator imports a class roster first.
- A pending student activates with matching name, student number, class invite code, and a new password.
- Student numbers are globally unique.
- Class administrators are appointed externally and created or handed over by a system administrator.
- Administrators cannot self-register or upgrade through an invite code.
- Each class has at most one active class administrator.
- Handover disables the old administrator’s class authority without changing class data.

### Class and semester isolation

- Courses, sessions, and homework belong to a class and semester.
- Students can only read data for their own class.
- Class administrators can only manage their own class.
- The server derives class and semester scope from Session and database state.
- Never trust a client-supplied `class_id`, `semester_id`, role, or status to expand access.
- Cross-class resources should return the project’s established 403 or 404 behavior without leaking data.

### Courses and sessions

- `courses` stores course-level information.
- `course_sessions` stores one or more teaching arrangements for a course.
- Do not duplicate a course only because it meets more than once per week.
- A conflict exists when class, semester, weekday, section range, and week range overlap.
- Conflict detection runs on the server and returns HTTP 409 with useful details.
- Update checks must exclude the record being updated.

### Homework

- Homework is shared by the whole class.
- Only the class administrator can create, update, or delete homework.
- Students are read-only.
- Homework belongs to a course, not to an individual course session.

### Dual CSV import

The import requires two files in one operation:

```text
课程.csv
作业.csv
```

Course headers:

```csv
课程编号,课程名,教师,教室,星期,开始节次,结束节次,起始周,结束周,考试日期,备注
```

Homework headers:

```csv
课程编号,作业内容,截止日期
```

Rules:

- Parse CSV with a real CSV parser; do not split rows manually.
- Support UTF-8 BOM and quoted fields.
- Validate both files completely before replacing data.
- Homework course codes must reference the same imported course batch.
- Repeated course codes represent multiple sessions; course-level fields must remain consistent.
- Replace only the current administrator’s class and current semester.
- Perform deletion and insertion in one SQLite transaction.
- Any validation or persistence failure rolls back everything.
- Return row-level validation details without exposing secrets.

### Invite codes

- Invite codes belong to classes, not individual administrators.
- Generate them using a cryptographically secure random source.
- Store only a digest in the database.
- Return plaintext only when creating or regenerating a code.
- Regeneration immediately invalidates the old code.
- Never write invite-code plaintext into logs or audit details.

## Security Rules

- Hash passwords with Werkzeug; never store or compare plaintext passwords.
- Enforce authentication, role, status, class, and semester authorization on the server.
- Require CSRF protection for state-changing `/api/` requests, except explicitly designed authentication entry points.
- Keep Session cookies `HttpOnly` and `SameSite=Lax`; enable `Secure` in HTTPS production.
- Apply login rate limiting using the existing project behavior.
- Escape untrusted text and avoid assigning raw user data to `innerHTML`.
- Do not log passwords, password hashes, invite-code plaintext, CSRF tokens, or secret keys.
- Production must use an independent `SECRET_KEY`.
- Do not add fixed production accounts, passwords, invite codes, or business data.
- Do not use `localStorage` as the authoritative business data source.

## API Conventions

Follow the project’s existing routes and response helpers. Preserve the standard error shape:

```json
{
  "code": "ERROR_CODE",
  "message": "Readable message",
  "details": {}
}
```

When changing an API:

1. Read the route, utility functions, frontend caller, and related tests.
2. Preserve HTTP semantics: 400 validation, 401 unauthenticated, 403 unauthorized, 404 hidden or missing resource, 409 conflict.
3. Keep writes transactional where multiple records change.
4. Add or update audit logging for security-sensitive and management operations.
5. Update the frontend and documents if fields or behavior change.

## Database Rules

The formal model contains:

```text
semesters
classes
users
teachers
time_slots
courses
course_sessions
homework
audit_logs
```

Before changing `database/schema.sql`:

- Read existing foreign keys, checks, cascades, and partial unique indexes.
- Preserve idempotent empty-database initialization.
- Enable and verify `PRAGMA foreign_keys=ON`.
- Prefer database constraints for invariants that must survive concurrent requests.
- Add migration handling when a change must support an existing database.
- Test empty initialization, repeat initialization, constraints, and cascade behavior.
- Never silently delete or rewrite a user’s existing database.

## Frontend Rules

- Use `static/js/api.js` for HTTP and Session/CSRF behavior.
- Keep student, class administrator, and system administrator views consistent with server permissions.
- Button visibility is only a usability feature; it is not authorization.
- Preserve complete information display. Avoid truncating names, courses, homework, errors, or audit content without a way to view the full value.
- Maintain desktop and mobile layouts.
- Preserve keyboard focus, labels, dialog behavior, loading states, errors, empty states, and confirmation for destructive actions.
- PDF export must output the complete current class/current week schedule in landscape format.

## Work Process

Follow this sequence for project changes:

1. Read task-relevant project memory and current documents.
2. Inspect the actual implementation before proposing a design.
3. Check the worktree and do not overwrite unrelated user changes.
4. Identify affected roles, tables, APIs, pages, tests, and documents.
5. Make the smallest coherent change that satisfies the requirement.
6. Add tests proportional to risk and permission impact.
7. Run focused tests, then the complete test suite when feasible.
8. Run browser regression for user-facing workflows.
9. Update PRD, task status, architecture, README, user manual, or submission notes when behavior or completion status changes.
10. Report what changed, verification performed, and any remaining risk.

## Verification Commands

From `班级课表助手_正式系统` on Windows:

```powershell
py -3 -m pip install -r requirements.txt
py -3 -m pip install -r requirements-dev.txt
py -3 -m pytest -q
py -3 app.py
```

On macOS or Linux:

```bash
python3 -m pip install -r requirements.txt
python3 -m pip install -r requirements-dev.txt
python3 -m pytest -q
python3 app.py
```

If a dependency or environment prevents a test from running, report that fact. Do not convert an unverified claim into “passed”.

Minimum browser regression:

1. Pending student activation and subsequent login.
2. Active student read-only schedule, homework, search, week/day view, and PDF.
3. Class administrator course/session CRUD, conflict rejection, homework CRUD, dual CSV rollback, and invite-code regeneration.
4. System administrator semester, class, roster, account lifecycle, administrator handover, and audit log.
5. Cross-class denial, disabled accounts, CSRF failures, mobile layout, and console errors.

## Documentation Rules

- Use the actual project paths and platform-neutral commands.
- Separate planned, implemented, tested, deployed, and accepted states.
- Keep task and acceptance statuses synchronized with evidence.
- Do not retain statements such as “backend pending” after verified implementation.
- Do not claim CloudStudio deployment without a working external URL.
- Do not claim tests passed only because test files exist.
- Keep demo credentials in explicitly marked development-only material.

## Completion Checklist

Before marking a task complete, verify:

- Confirmed business rules remain intact.
- All permissions are enforced server-side.
- Class and semester isolation is covered.
- Database operations are atomic where required.
- No secret or fixed production credential was introduced.
- Focused and regression tests pass, or blockers are clearly reported.
- Browser workflows have no relevant console or network errors.
- Documentation and task status match the delivered implementation.
