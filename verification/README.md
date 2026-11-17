# Verification

- [package_checks.json](package_checks.json) 記錄語法、設定、單元測試與 7 個模擬的腳本流程。
- 新機首次安裝依 [SOP](../SOP.md) 驗收。

root-config-update-1 新增根目錄 config.json 與重跑 install 更新功能。本次僅在 WSL 使用暫存檔、模擬服務／網路與 loopback socket 驗證，涵蓋更新學員 IP、RF／Hotspot 位址與路由、保留密碼／攝影機網址、不變設定重跑、元件啟動失敗回復。此版本的 config 更新尚未在實體 GCS 驗收，先前的 Microhard USB、MAVLink 和 RTSP 架構已在既有 GCS 驗證。
