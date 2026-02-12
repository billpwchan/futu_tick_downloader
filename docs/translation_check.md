# 文件翻譯自檢

本文件說明如何檢查文件是否仍殘留大段英文敘述，並確保翻譯不破壞可執行內容。

## 目的

- 找出「應翻譯但未翻譯」的英文段落。
- 保留合法英文內容：程式碼、指令、路徑、設定鍵名、環境變數、日誌原文。

## 前置條件

- 在 repo 根目錄執行。
- 需有 `bash`、`awk`、`rg`、`git`。

## 步驟

1. 執行文件英文段落檢查：

```bash
bash scripts/check_doc_translation.sh
```

2. 若出現清單，逐項人工確認：

- 是否為漏翻的英文敘述。
- 是否屬於允許保留英文（例如 inline code、路徑、ENV key、產品名）。

3. 修正後重新執行，直到只剩可接受項目。

4. 執行本地 Markdown 相對連結檢查：

```bash
bash -lc '
set -euo pipefail
broken=0
while IFS= read -r f; do
  dir=$(dirname "$f")
  while IFS= read -r link; do
    target=${link#*](}
    target=${target%)}
    [[ -z "$target" || "$target" =~ ^(http://|https://|mailto:|#) ]] && continue
    path=${target%%#*}
    [[ "$path" == /* ]] && ref=".${path}" || ref="$dir/$path"
    [[ -e "$ref" ]] || { echo "$f -> $target"; broken=1; }
  done < <(rg -o '\''\[[^]]+\]\([^)]*\)'\'' "$f")
done < <(git ls-files '\''*.md'\'')
[[ $broken -eq 0 ]]
'
```

## 如何驗證

- 掃描結果應以「可接受的必要英文」為主。
- 不應出現連續英文敘述段落（例如完整英文說明段）。
- Markdown 相對連結檢查應無輸出（且回傳碼為 0）。

## 排除規則（腳本內建）

- 排除 Markdown fenced code block 內容。
- 排除 inline code。
- 排除 URL、常見路徑樣式。
- 排除全大寫環境變數型態 token（例如 `LOG_LEVEL`）。

## 常見問題

### 為什麼仍有少量英文被列出？

偵測是啟發式規則，會刻意保守，寧可多報也不漏報。請以人工判讀為準。

### 是否要求 100% 無英文？

否。只要英文出現在允許保留的技術區域，即符合規範。
