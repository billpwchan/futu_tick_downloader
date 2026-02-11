# ops/ Legacy Compatibility Layer

`ops/` 下文件保留用于兼容既有运维入口，新的主入口已迁移：

- 安装：`scripts/install_systemd.sh`
- 日常健康检查：`scripts/healthcheck.sh`
- DB 验证：`scripts/verify_db.sh`
- 日志跟踪：`scripts/tail_logs.sh`
- 推荐 systemd 模板：`deploy/systemd/hk-tick-collector.service`

说明：

- `ops/install_collector.sh` 已改为调用 `scripts/install_systemd.sh` 的兼容包装器。
- `ops/hk-tick-collector.service` 作为历史路径保留，模板源以 `deploy/systemd` 为准。
