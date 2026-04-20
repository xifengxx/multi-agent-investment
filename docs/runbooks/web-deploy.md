# 云端部署（Nginx + systemd）MVP Runbook

## 1. 端口与安全组

- 对外：80（HTTP）
- Web API：127.0.0.1:8000（仅本机）
- 建议：只对外暴露 80；不要在安全组暴露 8000

## 2. 目录与权限

建议：

- `DATA_ROOT=/var/lib/multi-agent-investment`
- `SQLITE_PATH=/var/lib/multi-agent-investment/app.sqlite`

目录：

```text
/var/lib/multi-agent-investment/
  inbox/
  reports/
  app.sqlite
```

## 3. Nginx 配置示例

将 `/reports/` 映射到 `${DATA_ROOT}/reports/`，`/api/` 反代到 `127.0.0.1:8000`。

```nginx
server {
  listen 80;
  server_name _;

  location /reports/ {
    alias /var/lib/multi-agent-investment/reports/;
    autoindex on;
  }

  location /api/ {
    proxy_pass http://127.0.0.1:8000;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
  }
}
```

## 4. systemd 单元文件示例

本项目未强制规定 CLI 入口形式，你可以按自己的启动方式把“启动 Web/Worker 的命令”放进 `ExecStart`。

### 4.1 Web API（仅监听 127.0.0.1:8000）

`/etc/systemd/system/multi-agent-web.service`：

```ini
[Unit]
Description=multi-agent web api
After=network.target

[Service]
Type=simple
WorkingDirectory=/opt/multi_agent_investment
Environment=APP_ENV=prod
Environment=WEB_BIND_HOST=127.0.0.1
Environment=WEB_PORT=8000
Environment=DATA_ROOT=/var/lib/multi-agent-investment
Environment=SQLITE_PATH=/var/lib/multi-agent-investment/app.sqlite
Environment=API_TOKEN=REPLACE_ME
Environment=REPORTS_BASE_URL=http://PUBLIC_IP
Environment=TELEGRAM_BOT_TOKEN=REPLACE_ME
Environment=TELEGRAM_CHAT_ID=REPLACE_ME
ExecStart=/opt/multi_agent_investment/.venv/bin/python -m app.main web
Restart=always
RestartSec=2

[Install]
WantedBy=multi-user.target
```

### 4.2 Worker（两个实例：worker@1 + worker@2）

`/etc/systemd/system/multi-agent-worker@.service`：

```ini
[Unit]
Description=multi-agent worker %i
After=network.target

[Service]
Type=simple
WorkingDirectory=/opt/multi_agent_investment
Environment=APP_ENV=prod
Environment=DATA_ROOT=/var/lib/multi-agent-investment
Environment=SQLITE_PATH=/var/lib/multi-agent-investment/app.sqlite
Environment=WORKER_POLL_INTERVAL_SECONDS=2
Environment=WORKER_MAX_ATTEMPTS=3
Environment=REPORTS_BASE_URL=http://PUBLIC_IP
Environment=TELEGRAM_BOT_TOKEN=REPLACE_ME
Environment=TELEGRAM_CHAT_ID=REPLACE_ME
ExecStart=/opt/multi_agent_investment/.venv/bin/python -m app.main worker --worker-id worker-%i
Restart=always
RestartSec=2

[Install]
WantedBy=multi-user.target
```

## 5. 启动顺序

1) 启动 Web：`systemctl enable --now multi-agent-web`
2) 启动 Worker：`systemctl enable --now multi-agent-worker@1 multi-agent-worker@2`
3) 检查 Nginx：`systemctl enable --now nginx`

## 6. 联调

上传：

- URL：`http://PUBLIC_IP/api/upload`
- Header：`Authorization: Bearer <API_TOKEN>`
- multipart 字段：`stock_file`、`etf_file`

查看报告：

- `http://PUBLIC_IP/reports/{snapshot_date}/{run_id}/summary.md`
