# Dual-GCS Flight Training

整理日期為 2026-11-13，Asia/Taipei。適用於 Ubuntu 24.04 教練 GCS 與 Windows 學員 QGC。

這個資料夾包含 RF MAVLink 轉送、Hotspot、教練／學員控制、RTSP 影像 relay 的程式、設定範例、整套與個別元件的操作腳本、驗證工具。

**先開啟 [SOP.html](SOP.html)，或閱讀 [SOP.md](SOP.md)。** HTML 可直接用瀏覽器閱讀，也可列印。原理與 IP／Port 對照見 [ARCHITECTURE.md](ARCHITECTURE.md)。

## Whole-stack commands

先編輯根目錄 [config.json](config.json)。在 GCS 終端切換到專案資料夾，依 SOP 完成網卡與 QGC 前置設定後執行：

```bash
sudo bash ./01_install.sh --user user
sudo bash ./02_start.sh
sudo bash ./03_stop.sh
sudo bash ./04_remove.sh
sudo bash ./05_status.sh
```

安裝會同時部署 MAVLink 與影像服務。開始順序為 MAVLink／Hotspot，再啟動影像；停止及移除先處理影像，再處理 MAVLink／Hotspot。stop 不會取消開機啟用。需要只操作單一元件時，使用 mavlink/ 或 video/ 內的腳本。

**修改根目錄 config.json 後，重新執行 01_install.sh 就會更新已安裝的設定。** 學員 IP、開機模式、RF IP／對端路由、Hotspot IP／SSID、影像服務埠與編碼均從這份設定讀取。Hotspot 密碼沿用原值。video.upstream_url 留空會保留 GCS 上已保存的攝影機網址；填入完整 RTSP 網址則更新上游。

有變更的元件先在 /var/backups/gcs/ 建立私有備份，更新失敗時嘗試回復。兩個元件分開更新，影像失敗時 MAVLink 可能已更新，依錯誤訊息修正後重跑即可。相同設定與程式不會反覆重啟服務。更新保留教練目前的 on/off 模式，boot_default 在下次停止後啟動／開機時生效。

變更 RF／Hotspot 網卡或網段時，從 GCS 本機或獨立管理連線操作；安裝程式會同步更新受管理的網路設定並短暫中斷鏈路。攝影機實體 IP、攝影機專用網卡／路由、學員 Windows IP 與兩台 QGC 的設定仍依 SOP 處理。Hotspot 位址變更後，兩台 QGC 使用安裝最後印出的新 RTSP URL。

## Daily use

```bash
sudo gcs-student-control off     # 學員只監看，遙測與影像繼續
sudo gcs-student-control on      # 恢復學員經 MAVLink relay 的回程
sudo gcs-student-control status
```

教練與學員的 RTSP URL 都是：

```text
rtsp://10.42.0.1:8554/camera
```

教練 QGC 監聽 UDP 14551，學員監聽 UDP 14550。飛控控制模式開機預設為 control，可修改根目錄 config.json 的 boot_default 再執行 install。上述影像網址為預設值。

## Contents

| Path | Purpose |
| --- | --- |
| SOP.html / SOP.md | 新機設定、安裝、日常使用、故障排查、移除及還原 |
| ARCHITECTURE.md | 接線、IP、Port、兩種 relay、服務與資料流 |
| 01_install.sh ... 05_status.sh | 整套操作入口 |
| config.json | 集中的 RF／Hotspot／學員與影像設定；install 安裝或更新時讀取 |
| mavlink/assets/ | MAVProxy、學員 relay、控制 CLI、systemd 範本及管理程式 |
| mavlink/tests/ | MAVLink relay、控制 CLI 與設定測試 |
| network/ | Microhard USB 持久命名及選用的 PCI 忽略範本 |
| video/ | 獨立 RTSP 影像 relay、安裝／開始／停止／移除腳本 |
| tools/ | 設定檢查、Windows MAVLink 與 RTSP 驗證工具 |
| software/README.md | QGC 與執行環境取得方式 |
| verification/ | 本版測試方式與驗證範圍 |
| SHA256SUMS.txt | 檔案雜湊，供解壓後核對 |

這是一份可攜部署資料包，需要網路下載 QGC、Ubuntu 套件及 Python 依賴；不是離線安裝映像。本包不附 QGC 安裝執行檔、SSH 私鑰、sudo 密碼、Hotspot 密碼或現場攝影機的受限設定檔。

video.upstream_url 填入帳密後，config.json 也含登入資訊，請留在 GCS 或使用 `--config /path/to/config.local.json` 指定私有設定檔，不要分享含帳密的版本。程式不會印出攝影機上游網址。

本版採用通用的 gcs 服務名稱，現場 RF 已改走 Microhard USB。既有部署先依現場設定核對；新機依 SOP 安裝。新機的介面名稱、學員實際 IP、攝影機位址及登入路徑需依 SOP 核對。
