# EasySFTPServer

一個基於 `asyncssh` 和 `tkinter` 的簡易並發 SFTP 服務器。
它解決了 Windows 內置 OpenSSH 配置繁瑣、權限難調的痛點，能夠快速搭建一個高性能的、支持自定義用戶名和密碼的並發 SFTP 服務器。

## 依賴安裝
請確保你的環境是 Python 3.8+，然後執行：
```bash
pip install -r requirements.txt
```

## 使用方法
直接執行以下腳本，將彈出圖形界面：
```bash
python EasySFTPServer.py
```
- 可以選擇映射的根目錄
- 自定義監聽端口號
- 輕鬆設置客戶端連接用的用戶名和密碼
