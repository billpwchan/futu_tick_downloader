# 常見問題（FAQ）

## 只支援港股 symbol 嗎？

主要目標是港股 tick 採集，但 symbol 處理遵循 Futu code 格式，可按需求擴充。

## 為什麼採「每個交易日一個 SQLite DB」？

維運更簡單：保留、備份與檔案大小管理都更直觀。

## 會出現重複資料嗎？

匯入層面會（push + poll 重疊屬正常），最終表會透過唯一索引與 `INSERT OR IGNORE` 去重。

## 可以關掉 poll 備援嗎？

可以（`FUTU_POLL_ENABLED=0`），但若 push 串流停滯，生產可靠性會下降。

## 專案是否允許再散布市場資料？

不允許。本專案僅在你自身 Futu/OpenD 授權範圍內採集與儲存資料。

## 什麼時候該從 SQLite 升級？

當你需要多主機寫入、大規模分析、或多租戶集中存取時，可考慮 Postgres 或 columnar lakehouse 架構。
