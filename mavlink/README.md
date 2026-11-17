# MAVLink component

此目錄保留已部署的 MAVLink 元件及單獨操作入口。完整操作順序請讀上一層的 [SOP](../SOP.md)。

- [../config.json](../config.json)：根目錄統一設定檔。首次安裝與重跑 install 更新時都讀取；核對 RF／Hotspot 介面與學員 IP。
- assets/manage.py：安裝、開始、停止、移除與設定檢查。
- assets/student-relay.py：單一執行緒的 MAVLink 學員 relay。
- assets/gcs-student-control：教練 root CLI。
- tests/：既有 relay、控制 CLI 與管理測試。

```bash
python3 ./assets/manage.py check
sudo bash ./01_install.sh --user user
sudo bash ./02_start.sh
sudo bash ./03_stop.sh
sudo bash ./04_remove.sh
```

這些命令不安裝或移除影像 relay。一般使用優先採用上層整套腳本，以正確順序處理影像與 Hotspot。
