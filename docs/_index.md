# 文件入口（Docs Index）

這份文件是 `HK Tick Collector` 的單一入口，避免資訊散落。

## 依角色閱讀

- 交易研究員：先看 [`/docs/00-總覽.md`](00-%E7%B8%BD%E8%A6%BD.md) + [`/docs/06-資料格式與查詢.md`](06-%E8%B3%87%E6%96%99%E6%A0%BC%E5%BC%8F%E8%88%87%E6%9F%A5%E8%A9%A2.md)
- 工程師：先看 [`/docs/01-快速開始（本機）.md`](01-%E5%BF%AB%E9%80%9F%E9%96%8B%E5%A7%8B%EF%BC%88%E6%9C%AC%E6%A9%9F%EF%BC%89.md) + [`/docs/07-貢獻指南（開發者）.md`](07-%E8%B2%A2%E7%8D%BB%E6%8C%87%E5%8D%97%EF%BC%88%E9%96%8B%E7%99%BC%E8%80%85%EF%BC%89.md)
- SRE/運維：先看 [`/docs/02-部署到 AWS Lightsail（Ubuntu）.md`](02-%E9%83%A8%E7%BD%B2%E5%88%B0%20AWS%20Lightsail%EF%BC%88Ubuntu%EF%BC%89.md) + [`/docs/04-運維 Runbook.md`](04-%E9%81%8B%E7%B6%AD%20Runbook.md)
  - 含 OpenD 登錄短信驗證、行情權限優先（`auto_hold_quote_right`）、常見故障對照表
- 收盤自動化：看 [`/docs/09-收盤後自動化（歸檔與本地拉取）.md`](09-%E6%94%B6%E7%9B%A4%E5%BE%8C%E8%87%AA%E5%8B%95%E5%8C%96%EF%BC%88%E6%AD%B8%E6%AA%94%E8%88%87%E6%9C%AC%E5%9C%B0%E6%8B%89%E5%8F%96%EF%BC%89.md)
- 交易資料品質：看 [`/docs/quality.md`](quality.md) + [`/docs/hk-tickctl.md`](hk-tickctl.md)
- Telegram 值班：先看 [`/docs/telegram.md`](telegram.md) + [`/docs/runbook/telegram-actions.md`](runbook/telegram-actions.md)

## 目錄

- [`/docs/00-總覽.md`](00-%E7%B8%BD%E8%A6%BD.md)
- [`/docs/01-快速開始（本機）.md`](01-%E5%BF%AB%E9%80%9F%E9%96%8B%E5%A7%8B%EF%BC%88%E6%9C%AC%E6%A9%9F%EF%BC%89.md)
- [`/docs/02-部署到 AWS Lightsail（Ubuntu）.md`](02-%E9%83%A8%E7%BD%B2%E5%88%B0%20AWS%20Lightsail%EF%BC%88Ubuntu%EF%BC%89.md)
- [`/docs/03-配置說明（.env）.md`](03-%E9%85%8D%E7%BD%AE%E8%AA%AA%E6%98%8E%EF%BC%88.env%EF%BC%89.md)
- [`/docs/04-運維 Runbook.md`](04-%E9%81%8B%E7%B6%AD%20Runbook.md)
- [`/docs/05-Telegram 通知（產品化）.md`](05-Telegram%20%E9%80%9A%E7%9F%A5%EF%BC%88%E7%94%A2%E5%93%81%E5%8C%96%EF%BC%89.md)
- [`/docs/06-資料格式與查詢.md`](06-%E8%B3%87%E6%96%99%E6%A0%BC%E5%BC%8F%E8%88%87%E6%9F%A5%E8%A9%A2.md)
- [`/docs/07-貢獻指南（開發者）.md`](07-%E8%B2%A2%E7%8D%BB%E6%8C%87%E5%8D%97%EF%BC%88%E9%96%8B%E7%99%BC%E8%80%85%EF%BC%89.md)
- [`/docs/08-發版流程.md`](08-%E7%99%BC%E7%89%88%E6%B5%81%E7%A8%8B.md)
- [`/docs/09-收盤後自動化（歸檔與本地拉取）.md`](09-%E6%94%B6%E7%9B%A4%E5%BE%8C%E8%87%AA%E5%8B%95%E5%8C%96%EF%BC%88%E6%AD%B8%E6%AA%94%E8%88%87%E6%9C%AC%E5%9C%B0%E6%8B%89%E5%8F%96%EF%BC%89.md)
- [`/docs/quality.md`](quality.md)
- [`/docs/archive.md`](archive.md)
- [`/docs/hk-tickctl.md`](hk-tickctl.md)
- [`/docs/runbook.md`](runbook.md)
- [`/docs/telegram.md`](telegram.md)
- [`/docs/runbook/telegram-actions.md`](runbook/telegram-actions.md)
- [`/docs/engineering/telegram-interactive-flow.md`](engineering/telegram-interactive-flow.md)
- [`/docs/user-guide/telegram-actions.md`](user-guide/telegram-actions.md)
