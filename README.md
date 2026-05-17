# 记忆提醒工具

一个可以先本地使用、后续部署到服务器的记忆卡片应用。当前版本包含账号登录、内容存储、标签筛选、今日复习、按反馈自动安排下次提醒、浏览器通知和邮件提醒任务骨架。

## 本地运行

```powershell
python app.py
```

打开：

```text
http://127.0.0.1:8000
```

首次进入可以直接注册账号。默认数据库文件会保存在 `data/memory.sqlite3`。

## 账号密码

应用已经内置账号密码登录：

- 密码使用 PBKDF2-SHA256 加盐哈希保存，不保存明文。
- 注册密码至少 8 位，不能是纯数字或纯字母。
- 上线后建议关闭公开注册，只保留登录。

服务器上创建账号：

```powershell
python scripts/create_user.py --email you@example.com --name 你的昵称
```

如果需要重置密码：

```powershell
python scripts/create_user.py --email you@example.com --reset-password
```

## 复习规则

- 新内容默认安排到明天早上 8 点。
- 点“记住了”：按 3、7、14、30、60、120、180 天逐步拉长。
- 点“模糊”：短间隔复习，通常 1 到 2 天后。
- 点“没记住”：明天早上重新提醒。

## 邮件提醒

复制 `.env.example` 中的 SMTP 配置到运行环境变量里。配置 `SMTP_HOST` 和 `SMTP_FROM` 后，后台任务会每分钟检查一次到期内容，并发送提醒邮件。

常用变量：

```text
APP_HOST=0.0.0.0
APP_PORT=8000
APP_TZ=Asia/Shanghai
SMTP_HOST=smtp.example.com
SMTP_PORT=587
SMTP_USER=your-account
SMTP_PASSWORD=your-password
SMTP_FROM=reminder@example.com
```

## Docker 部署

```powershell
docker compose up -d --build
```

如果使用宝塔或手动 Nginx，可以参考 `deploy/nginx.conf`，把域名反向代理到 `127.0.0.1:8000`。

## 服务器部署建议

1. 服务器安装 Git、Docker、Docker Compose。
2. 克隆仓库：

```bash
git clone https://github.com/Zk-zengke/memory-reminder-tool.git
cd memory-reminder-tool
```

3. 准备生产环境变量：

```bash
cp .env.production.example .env.production
```

4. 先创建你的登录账号：

```bash
python3 scripts/create_user.py --email you@example.com --name 你的昵称
```

5. 确认 `.env.production` 里保持：

```text
ALLOW_REGISTRATION=false
```

6. 启动：

```bash
docker compose -f deploy/docker-compose.prod.yml up -d --build
```

7. 用 Nginx 把域名反向代理到 `127.0.0.1:8000`，并配置 HTTPS。

## 数据库路线

当前版本为了方便本地直接运行，使用 Python 内置 SQLite。接口和表结构已经按服务器版拆好；正式多人长期使用时，可以迁移到 PostgreSQL。PostgreSQL 表结构在 `database/postgresql_schema.sql`。

下一步迁移建议：

1. 把 `cards.tags` 从 SQLite 文本 JSON 换成 PostgreSQL `JSONB`。
2. 新增 PostgreSQL 连接层，保留现有 API 路由。
3. 用定时任务服务或独立 worker 运行邮件、微信、企业微信提醒。
4. 上线后关闭开放注册：`ALLOW_REGISTRATION=false`。
