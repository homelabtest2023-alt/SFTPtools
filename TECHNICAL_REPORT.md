# SFTPtools 技術文檔報告

- 文檔日期: 2026-03-19
- 項目名稱: SFTPtools (EasySFTPServer)
- 主要代碼文件: `EasySFTPServer.py`
- 適用場景: Waterfall SFTP / SFTP-NG 客戶端與 Windows 環境文件傳輸

## 1. 文檔目的

本報告用於說明 SFTPtools 的技術架構、運行流程、關鍵配置、日誌與故障定位方法，以及本次針對「可連線但無法獲取/寫入文件」問題的修補內容。

## 2. 軟件總覽

SFTPtools 是一個基於 `asyncssh` + `tkinter` 的圖形化 SFTP 服務端工具，主要能力如下:

- 提供 SFTP 服務監聽（默認 2222 端口）
- 提供簡單賬密認證
- 支持自定義映射根目錄（chroot）
- 支持多連線展示與基礎操作日誌

## 3. 代碼結構與職責

### 3.1 `log_event(ip, message)`

- 職責: 寫入按 IP 分檔的操作日誌
- 目錄: 程序基準路徑下 `logs/`
- 文件命名: `x_x_x_x.log`

### 3.2 `LoggingSFTPServer(asyncssh.SFTPServer)`

- 職責: SFTP 協議操作入口，接收並處理客戶端文件操作請求
- 核心能力:
  - 路徑正規化與兼容處理
  - 對關鍵 SFTP 操作記錄請求/失敗日誌
  - 調用 `asyncssh` 默認能力實際執行文件操作

### 3.3 `SFTPServerAuth(asyncssh.SSHServer)`

- 職責: SSH 層連線與賬密驗證
- 行為:
  - `begin_auth()` 固定要求密碼認證
  - `validate_password()` 驗證用戶名和密碼

### 3.4 `SFTPServerApp`

- 職責: GUI 控制與服務生命週期管理
- 功能:
  - 目錄、端口、賬密輸入
  - 啟停服務
  - 連線設備列表展示
  - 主線程和 asyncio 後台線程協調

## 4. 協議處理流程（高層）

1. 客戶端建立 SSH 連線
2. 服務端做密碼認證
3. 客戶端開啟 SFTP 子系統
4. 客戶端發送 `realpath/stat/scandir/open/rename...` 等請求
5. `LoggingSFTPServer` 做路徑兼容處理後調用父類執行
6. 結果返回客戶端，並按操作寫入日誌

## 5. 本次修補內容

### 5.1 問題背景

現象為:

- 客戶端能連接與認證成功
- Waterfall SFTP-NG 在獲取目錄或寫入文件時失敗
- Windows OpenSSH 同場景正常

這通常表示「SFTP 連線層正常，但部分 SFTP 操作語義/路徑格式兼容不足」。

### 5.2 修補摘要

已在 [EasySFTPServer.py](D:/GitHub/SFTPtools/EasySFTPServer.py) 完成以下修補:

1. 路徑正規化重構為雙分支:
- `_normalize_path_str()`
- `_normalize_path_bytes()`

2. 修正 bytes 路徑兼容策略:
- 不再走 decode->encode 破壞原始字節路徑
- 保留輸入類型（`str`/`bytes`）一致性

3. 去除會改變語義的 `.strip()`:
- 避免把前後空白當成可清洗字符，減少文件名行為回歸

4. 補齊 `posix_rename()`:
- 覆蓋客戶端可能使用的 POSIX rename 擴展路徑
- 避免僅覆蓋 `rename()` 導致的寫入後重命名失敗

5. 新增 `scandir()` 兼容入口:
- 額外覆蓋目錄枚舉路徑
- 並帶 `list_folder()` 回退處理與錯誤日誌

### 5.3 兼容性收益

- 提高 Waterfall SFTP-NG 對 Windows 路徑風格請求的適配能力
- 降低「上傳成功但落盤/改名失敗」概率
- 提高目錄枚舉操作可觀測性，方便快速定位後續問題

## 6. 配置與運行說明

### 6.1 啟動配置

- 根目錄: GUI `Step 1`
- 端口: GUI `Port`
- 賬密: GUI `Username` / `Password`

### 6.2 關鍵資產

- Host Key: `sftp_host_key`
- 日誌目錄: `logs/`

## 7. 日誌與排障

### 7.1 觀察重點

請優先查看以下模式:

- `Request realpath:` / `realpath failed:`
- `Request scandir:` / `scandir failed:`
- `Request list_folder:` / `list_folder failed:`
- `Request Upload:` / `Upload failed:`
- `Request rename:` / `rename failed:`
- `Request posix_rename:` / `posix_rename failed:`

### 7.2 快速定位建議

1. 先確認是否有 `Device connected` 與正常關閉記錄
2. 若連線正常但讀寫失敗，對照第一條 `failed` 操作定位
3. 若失敗集中在路徑相關操作，先對照客戶端實際上送路徑格式
4. 若失敗集中在寫入類操作，重點檢查 `posix_rename` / `rename` / 權限

## 8. 驗證計劃（建議）

### 8.1 功能驗證清單

- 使用 Waterfall SFTP-NG:
  - 登錄
  - 列根目錄
  - 上傳新文件
  - 覆蓋上傳同名文件
  - 下載文件
  - 重命名文件
  - 刪除文件與目錄

- 使用 Windows OpenSSH 客戶端:
  - 執行相同操作，作對照測試

### 8.2 判定標準

- 不出現無法列目錄/無法寫入的阻斷性錯誤
- 日誌能完整覆蓋對應操作請求與失敗信息

## 9. 風險與後續改進

### 9.1 當前風險

- 本次環境無可用 Python 解析器，未在本機執行自動化回歸
- GUI 文本存在編碼顯示差異（不影響 SFTP 核心功能）

### 9.2 後續建議

1. 增加最小化集成測試（本地 SFTP 客戶端腳本）
2. 對常見客戶端（Waterfall、WinSCP、OpenSSH、MobaXterm）做兼容矩陣
3. 在日誌中增加 request id，便於跨操作串聯追蹤
4. 在 GUI 中新增「打開日誌目錄」與「連線詳細診斷」按鈕

## 10. 本次代碼變更摘要

- 修改文件: [EasySFTPServer.py](D:/GitHub/SFTPtools/EasySFTPServer.py)
- 新增方法:
  - `_normalize_path_str`
  - `_normalize_path_bytes`
  - `scandir`
  - `posix_rename`
- 調整方法:
  - `_normalize_path`
  - `_get_decoded_path`

