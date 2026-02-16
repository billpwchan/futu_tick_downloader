# Microstructure Event Library（港股 Tick → 結構化事件庫）

> 目的：將逐筆 ticks 轉成 **可行動、低噪音、可回放/可回測** 的「結構化事件（Events）」與「關鍵價格區（Levels）」。
>
> 核心理念：**少而準**。只產出對盤中判斷/盤後復盤有高信息量的事件，並用「去重、冷卻、確認、分級」來控制噪音。

---

## 1. 範圍與非目標

### 1.1 事件庫要解決什麼
- 盤中：在 Telegram/監控面板上顯示「值得看」的時刻（例如吸收、推進、流動性真空、競價異常、結構突破）。
- 盤後：用同一套事件回看當天行情結構，並支援策略驗證/回測（事件 → 進出場規則）。
- 工程：事件是下游（JChart / research / alerts）的統一接口，避免每個下游各算各的。

### 1.2 非目標
- 不做價格預測。
- 不依賴 Level-2（可選），先用 tick trades 做出高價值版本（若未來加 L2 可增強置信度）。

---

## 2. 最小資料假設（Tick 欄位）

> 若實際欄位不同，允許做 mapping，但需保留語義一致。

必需欄位（MVP）：
- `ts_ms`：UTC epoch 毫秒
- `symbol`：如 `HK.00700`
- `price`：成交價
- `size`：成交量（股數）或成交量單位
- `turnover`（可選但建議）：成交額（若無可用 price*size 估算）
- `trade_dir`（可選）：主動買/主動賣；若無則用 tick rule 推斷（見 4.2）

---

## 3. 核心輸出資料結構（3 張表）

### 3.1 `features_1m`：每分鐘特徵（每 symbol 一行）
用途：事件計算、回測、儀表板，避免反覆掃 tick 表。

建議 schema（SQLite）：
```sql
CREATE TABLE IF NOT EXISTS features_1m (
  ts_min_ms      INTEGER NOT NULL, -- minute bucket start, UTC ms
  symbol         TEXT    NOT NULL,

  -- activity
  ticks          INTEGER NOT NULL,
  volume         REAL    NOT NULL,
  turnover       REAL    NOT NULL,

  -- OHLC（以成交價聚合）
  open           REAL    NOT NULL,
  high           REAL    NOT NULL,
  low            REAL    NOT NULL,
  close          REAL    NOT NULL,

  -- orderflow（若 trade_dir 缺失，使用 tick rule）
  buy_volume     REAL    NOT NULL,
  sell_volume    REAL    NOT NULL,
  imbalance      REAL    NOT NULL, -- (buy-sell)/(buy+sell+eps)

  -- impact / liquidity proxy
  abs_return     REAL    NOT NULL, -- |close-open|/open
  price_change   REAL    NOT NULL, -- close-open
  impact         REAL    NOT NULL, -- |price_change| / (volume+eps)
  jump_count     INTEGER NOT NULL, -- number of multi-tick jumps within minute (optional)

  -- profile position（相對 levels_daily）
  dist_to_poc    REAL,             -- close - POC
  in_value_area  INTEGER,          -- 1/0
  above_vah      INTEGER,
  below_val      INTEGER,

  -- compression/release
  compression_score REAL,          -- optional
  release_score     REAL,          -- optional

  PRIMARY KEY (ts_min_ms, symbol)
);
CREATE INDEX IF NOT EXISTS idx_features_1m_symbol_ts ON features_1m(symbol, ts_min_ms);
3.2 levels_daily：每日結構 Levels（每 symbol 一行）
用途：事件解釋（POC/VA/HVN/LVN）、JChart 畫結構、盤後摘要。

建議 schema：

CREATE TABLE IF NOT EXISTS levels_daily (
  trading_day    TEXT    NOT NULL, -- YYYY-MM-DD (HK trading day)
  symbol         TEXT    NOT NULL,

  -- profile core
  poc            REAL    NOT NULL,
  vah            REAL    NOT NULL,
  val            REAL    NOT NULL,

  -- nodes（可用 JSON 存）
  hvn_json       TEXT,             -- [{"price":..., "vol":...}, ...]
  lvn_json       TEXT,             -- [{"price":..., "vol":...}, ...]

  -- auction metrics（港股特色）
  open_auc_range REAL,             -- optional
  close_auc_range REAL,            -- optional
  close_strength_score REAL,        -- optional

  -- meta
  total_volume   REAL    NOT NULL,
  total_turnover REAL    NOT NULL,
  calc_version   TEXT    NOT NULL,

  PRIMARY KEY (trading_day, symbol)
);
CREATE INDEX IF NOT EXISTS idx_levels_daily_symbol_day ON levels_daily(symbol, trading_day);
3.3 events：事件庫（核心）
用途：Telegram 推送、回放/回測、運維與市場事件共用統一事件模型。

建議 schema：

CREATE TABLE IF NOT EXISTS events (
  ts_ms          INTEGER NOT NULL,
  symbol         TEXT    NOT NULL,
  event_type     TEXT    NOT NULL, -- e.g., ABSORPTION_BUY
  severity       TEXT    NOT NULL, -- INFO/WARN/ALERT
  score          REAL    NOT NULL, -- 0~100, 置信度/重要性
  dedup_key      TEXT    NOT NULL, -- 用於去重
  cooldown_sec   INTEGER NOT NULL, -- 事件冷卻時間（寫入時已決定）
  payload_json   TEXT    NOT NULL, -- JSON with evidence
  created_at_ms  INTEGER NOT NULL,

  PRIMARY KEY (ts_ms, symbol, event_type, dedup_key)
);
CREATE INDEX IF NOT EXISTS idx_events_symbol_ts ON events(symbol, ts_ms);
CREATE INDEX IF NOT EXISTS idx_events_type_ts ON events(event_type, ts_ms);
4. 計算口徑（MVP 需要一致）
4.1 分桶
features_1m.ts_min_ms = floor(ts_ms / 60000) * 60000

日維度 trading_day：以香港交易日（HKT）定義；但 ts_ms 一律 UTC 存。

4.2 主動買賣方向（無 trade_dir 時）
Tick rule（簡化版）：

若 price > prev_price → buy-initiated

若 price < prev_price → sell-initiated

若 price == prev_price → 沿用上一筆方向（或記為 neutral，MVP 可沿用）

5. 事件設計原則（降噪）
確認（Confirm）：多數事件需連續 K 個窗口滿足才觸發（避免單點噪音）。

去重（Dedup）：同一事件在同一「結構單元」只保留一筆（例如同一 LVN 突破不連發）。

冷卻（Cooldown）：觸發後 N 秒內同 dedup_key 不再推送（但可寫入 suppressed_count 到 payload）。

分級（Severity）：INFO 僅寫庫不推；WARN 可能推；ALERT 必推（可依群組策略設定）。

可解釋（Evidence）：payload 必含「數據證據」：窗口、量、價、impact、關鍵 level。

6. Event Types（MVP 高價值清單）
分 5 類：Profile/Levels、Orderflow、Liquidity、Auction、Structure（壓縮釋放/背離）

6.1 Profile / Levels 類
(E1) VALUE_ACCEPTANCE
含義：價格進入 Value Area 後被「接受」（停留+成交），常見於回歸均衡。

觸發（1m）：

先前 above_vah=1 或 below_val=1

本分鐘 in_value_area=1

且連續 K=2 分鐘仍在 VA 內

severity：INFO（可不推）

dedup_key：symbol|day|VA_ACCEPT

cooldown：1800s

payload：{ "from":"above_vah/below_val", "poc":..., "vah":..., "val":..., "minutes_in_va":K }

(E2) VALUE_REJECTION
含義：觸及 VA 邊界後快速拒絕（假突破/回撤）

觸發（1m）：

本分鐘觸及 VAH/VAL（high>=vah 或 low<=val）

下一分鐘 close 回到 VA 外，且 abs_return 高於近期分位（例如 > p70）

severity：WARN（視策略推送）

dedup_key：symbol|day|VA_REJECT|edge(VAL/VAH)

cooldown：900s

payload：含邊界、觸及價、拒絕方向、1m/2m 變化

(E3) LVN_BREAKOUT (UP/DOWN)
含義：穿越低成交量節點（真空區），通常伴隨滑點/快速移動

觸發（1m）：

close 穿越某個 LVN 價位（由 levels_daily.lvn_json 給定）

且 impact > 最近 60m 中位數 * x（例如 x=2）

severity：ALERT（值得看）

dedup_key：symbol|day|LVN|price=<lvn>|dir=<up/down>

cooldown：1200s

payload：{ "lvn":..., "impact":..., "impact_vs_med":..., "window":"60m" }

6.2 Orderflow（吸收/推進）
(E4) ABSORPTION_BUY / ABSORPTION_SELL
含義：成交量大但價格推不動（有被動大單吸收）

觸發（1m）：

imbalance 明顯偏向一側（|imbalance| >= 0.35）

但 abs_return 低（<= 近 60m p30）

且 volume 高（>= 近 60m p70）

可選：靠近關鍵 level（距離 POC/VAH/VAL <= N ticks）加分

severity：WARN（高分可推）

score：基於 volume 分位、abs_return 反向、level proximity

dedup_key：symbol|day|ABSORB|side=<buy/sell>|level=<nearest_level_bucket>

cooldown：900s

payload：{ "imbalance":..., "volume_pctl":..., "ret_pctl":..., "nearest_level":"POC/VAH/VAL/LVN/HVN", "dist":... }

(E5) INITIATION_UP / INITIATION_DOWN
含義：方向性推進（成交密度+impact+位移同步）

觸發（1m）：

abs_return >= 近 60m p80

且 impact >= 近 60m p80

且 imbalance 與方向一致（上漲時 imbalance>0）

若同時穿越 LVN 或離開 VA（above_vah/below_val）則加分→更可能推送

severity：ALERT

dedup_key：symbol|day|INIT|dir=<up/down>|band=<va/outer>

cooldown：600s

payload：包含 return/impact 分位、是否穿越 LVN、相對 VA 狀態

6.3 Liquidity（真空/滑點風險）
(E6) LIQUIDITY_VACUUM_UP / DOWN
含義：單位成交量造成的價格位移異常（滑點風險）

觸發（1m）：

impact >= 近 120m p90

且 jump_count（若有）>= 閾值 或 close-open 跨多個 tick

且 volume 不一定高（反而常見中低 volume）

severity：ALERT（偏風控）

dedup_key：symbol|day|VACUUM|dir=<up/down>|impact_band=<p90+>

cooldown：900s

payload：{ "impact":..., "impact_pctl":..., "volume":..., "jump_count":... }

6.4 Auction（港股特色：開盤/收盤競價）
需要定義時段（HKT）：

開盤集合：09:00–09:20

收盤競價：16:00–16:10（以實際市場規則為準，可配置）

(E7) OPEN_IMBALANCE
含義：開盤集合方向性強（容易形成當日基調）

觸發：

09:00–09:20 期間的成交集中度（例如 top-3 價位成交占比）>= 閾值

且開盤後 5 分鐘的方向與集合方向一致（確認）

severity：WARN（高分可推）

dedup_key：symbol|day|OPEN_IMB

cooldown：全日一次

payload：集合 range、集中度、確認結果

(E8) CLOSE_AUCTION_DOMINANCE
含義：收盤競價主導當日收盤定價（常見被“釘價”）

觸發：

16:00–16:10 成交量占全天比例 >= 閾值（如 >= 12%）

且收盤價偏離 POC/VA（例如 |close-POC| / 日波幅 >= 閾值）

severity：ALERT（盤後/隔日重要）

dedup_key：symbol|day|CLOSE_DOM

cooldown：全日一次（盤後發）

payload：比例、偏離、當日 VA/POC、close strength

6.5 Structure（壓縮→釋放）
(E9) RANGE_COMPRESSION
含義：盤中結構壓縮（能量累積）

觸發（rolling 15m）：

15m 真實波幅（high-low）<= 近 120m p20

且 ticks/min <= 近 120m p30（可選）

severity：INFO（通常不推）

dedup_key：symbol|day|COMP|15m_bucket=<start>

cooldown：1800s

payload：分位、窗口、range、ticks/min

(E10) BREAKOUT_RELEASE_UP / DOWN
含義：壓縮後釋放（更可交易）

觸發：

先出現 RANGE_COMPRESSION（最近 60m 內）

隨後 1m 出現 INITIATION（E5）或 LVN_BREAKOUT（E3）

severity：ALERT

dedup_key：symbol|day|RELEASE|dir=<up/down>|from_comp=<comp_bucket>

cooldown：1200s

payload：連結 comp 的 evidence + 釋放證據

7. Telegram 推送策略（低噪音）
7.1 推送白名單（建議 MVP）
必推（ALERT）：

LVN_BREAKOUT

INITIATION

LIQUIDITY_VACUUM

BREAKOUT_RELEASE

CLOSE_AUCTION_DOMINANCE（盤後）

可推（WARN，高 score 才推）：

ABSORPTION（score >= 75）

VALUE_REJECTION（score >= 70）

OPEN_IMBALANCE（開盤後確認才推）

不推（INFO，只寫庫）：

VALUE_ACCEPTANCE

RANGE_COMPRESSION

7.2 訊息卡片模板（事件版）
[Microstructure] ⚠️/🚨 <event_title>
- Symbol: HK.00700
- When: 2026-02-16 10:32 HKT
- What: <一句話結論（非技術）>
- Evidence: <3 個核心數字：impact/imbalance/level>
- Context: POC/VAH/VAL / 距離
- Note: <風險或觀察建議（不給投資建議）>
8. 參數化（建議 env）
先給保守預設，後續以分位數自適應為主，減少手調。

MS_EVENT_LOOKBACK_MIN=120

MS_PCTL_WINDOW_MIN=120

MS_ABSORB_IMB_THRESHOLD=0.35

MS_ABSORB_VOL_PCTL=0.70

MS_ABSORB_RET_PCTL=0.30

MS_INIT_RET_PCTL=0.80

MS_INIT_IMPACT_PCTL=0.80

MS_VACUUM_IMPACT_PCTL=0.90

MS_EVENT_COOLDOWN_SEC_DEFAULT=900

MS_WARN_SCORE_PUSH=75

9. 驗證 SQL（驗收用）
9.1 看今天某票事件
SELECT
  datetime(ts_ms/1000,'unixepoch') AS utc,
  symbol, event_type, severity, score, payload_json
FROM events
WHERE symbol='HK.00700'
ORDER BY ts_ms DESC
LIMIT 50;
9.2 看某類事件今天出現多少次（控噪）
SELECT event_type, severity, COUNT(*) AS n
FROM events
WHERE ts_ms >= (strftime('%s','now')-86400)*1000
GROUP BY event_type, severity
ORDER BY n DESC;
9.3 抽查 features_1m 的分位數假設（debug）
SELECT
  datetime(ts_min_ms/1000,'unixepoch') AS utc_min,
  volume, imbalance, impact, abs_return, in_value_area, dist_to_poc
FROM features_1m
WHERE symbol='HK.00700'
ORDER BY ts_min_ms DESC
LIMIT 120;
10. 實作建議（不綁語言，但利於落地）
streaming aggregator：

ingest tick → 更新當前 minute bucket 的累積器

minute rollover → flush to features_1m（單 writer）

levels_daily：

盤後（或每 N 分鐘增量）計算 profile（POC/VA/HVN/LVN）

events：

以 features_1m 為主觸發（避免掃 tick）

寫入前做 dedup + cooldown 檢查（可在記憶體，也可查 events 表近 N 秒）

11. Roadmap（後續增強）
若接入 L2（買賣盤、broker queue）：吸收/冰山置信度可大幅提升

事件與 JChart 整合：events 作為「畫線/標記」來源，減少主觀線

回放：事件可在 replay 模式中復現（同規則、同輸出）