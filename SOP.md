# GCS 設定、使用與移除 SOP

版本日期為 2026-11-13，時區 Asia/Taipei。

## 1. 使用情境與完成標準

教練 GCS 接收 RF 無人機資料並執行 QGC；Windows 學員透過 GCS_Hotspot 接收遙測與影像。教練可以關閉學員的 MAVLink 回程，讓學員繼續監看。

完成標準：兩端 QGC 收到飛控資料、兩端影像持續更新，學員 off 時仍有遙測與影像，on 時可恢復經 relay 的回程。Not Ready 是飛控整備狀態；不能單憑這個字樣判定連線失敗。

本包不會設定飛行任務、解鎖或起飛。安裝及移除請在飛控未解鎖時，從 GCS 本機或獨立管理網路操作；停止 Hotspot 會使透過它登入的 SSH 中斷。

## 2. 先準備什麼

- 教練：Ubuntu 24.04、NetworkManager、Python 3、可用的 RF 網路介面（現場使用 Microhard USB）、攝影機專用網卡，以及支援 AP 的 Wi-Fi 網卡。
- 學員：Windows QGC，Wi-Fi 可連到 GCS_Hotspot。
- 安裝期間可連到 Ubuntu 套件來源與 PyPI。QGC 依 [官方安裝說明](https://docs.qgroundcontrol.com/master/en/qgc-user-guide/getting_started/download_and_install.html) 另行下載與安裝。
- 已取得攝影機實際 IP、可用的完整 RTSP 路徑及登入資訊。攝影機目前輸出 H.265。
- 從 GitHub 下載整份專案，保留所有子目錄。也可用下方 git clone 指令。

從終端進入解壓目錄，例如：

```bash
git clone https://github.com/PeterHung/Dual-GCS_Flight_Training.git
cd Dual-GCS_Flight_Training
python3 ./tools/check_config.py
```

若需要確認檔案完整性：

```bash
sha256sum -c SHA256SUMS.txt
```

### 2.1 現場位址參考

| 角色 | 範例值 | 換機時注意 |
| --- | --- | --- |
| GCS 管理 SSH 範例 | 192.0.2.18，帳號 user | 新機可不同，不是程式綁定條件 |
| GCS RF | Microhard USB gcsrf0，192.168.168.249/24 | 其他 RF 硬體請核對實際介面名稱 |
| 舊 PCI RF | enp0s31f6，unmanaged | 現場已關閉舊 profile 自動連線 |
| RF 端點 | 192.168.168.198:14550 | RF 必須將 MAVLink 送到 GCS .249:14550 |
| Microhard 設備本身 | 192.168.168.199 | 與 GCS 網卡分開，本包不修改它 |
| GCS 攝影機 USB 網卡 | enx001122334455，192.168.168.250/32 | 新 USB 網卡名稱／MAC 可能不同 |
| 攝影機 | 192.168.168.73:554 | 紙本預設 .1.10 與現場不同，使用實測值 |
| Hotspot | wlp2s0，10.42.0.1/24 | 預設值；修改根目錄 config.json 後重跑 install 套用 |
| 學員 Windows | 10.42.0.203 | MAVLink 只轉送給設定的學員 IP |

同網段不能同時有兩台 GCS 使用 .249 或 .250。換新機前先讓原機離開相同實體網路。

## 3. 新 GCS 網卡與接線設定

若在已部署好的現場 GCS 上使用，先執行 05_status.sh 核對狀態，不必重建網路設定或重裝。

新機先執行：

```bash
nmcli device status
ip -br link
ip -br -4 address
nmcli connection show
```

修改網路前，可在 GCS 本機建立只有 root 可讀的備份；此備份可能含網路登入資訊，留在 GCS，不要放進共用資料包：

```bash
BACKUP_DIR="/var/backups/gcs/before_setup_$(date +%Y%m%d_%H%M%S)"
sudo install -d -m 700 "$BACKUP_DIR"
sudo cp -a /etc/netplan "$BACKUP_DIR/"
sudo cp -a /etc/NetworkManager/system-connections "$BACKUP_DIR/"
```

現場 RF 使用 Microhard USB。攝影機接獨立 USB 有線網卡，管理 SSH 使用另一張網卡。在其他 GCS 部署時，先確認實際介面與位址。

### 3.1 RF 網卡與靜態位址

若新機使用相同 Microhard USB 裝置（USB ID 1f94:3002），先在解壓目錄安裝命名規則。重新插接 Microhard USB 後確認介面出現。

```bash
lsusb -d 1f94:3002
sudo install -D -m 0644 ./network/10-gcs-rf-usb.link \
  /etc/systemd/network/10-gcs-rf-usb.link
sudo udevadm control --reload
# 重新插接 Microhard USB 後執行
ip -br link show dev gcsrf0
```

不同 RF 硬體可直接使用它的實際介面名稱。同步修改下方 RF_IF 與根目錄 config.json 的 rf_interface。同名 profile 已存在時，先檢查並沿用或更新該 profile。

從 PCI 移轉時，先確認管理 SSH 走獨立介面，備份舊 RF profile，並停用舊 profile，讓 .249 由 USB 單獨持有。

```bash
RF_IF=gcsrf0
sudo nmcli connection add type ethernet ifname "$RF_IF" con-name gcs-rf-usb \
  connection.autoconnect yes connection.autoconnect-priority 100 \
  ipv4.method manual ipv4.addresses 192.168.168.249/24 \
  ipv4.routes "192.168.168.198/32 0.0.0.0 50" \
  ipv4.never-default yes ipv4.dad-timeout 3000 ipv4.may-fail no ipv6.method disabled
sudo nmcli connection up gcs-rf-usb
ip route get 192.168.168.198
```

現場預期路由顯示 dev gcsrf0 src 192.168.168.249。/32 主機路由將 .198 指定到 RF USB，攝影機 .73 則走攝影機 USB。

#### 忽略已停用的 PCI RF 網卡

只有要停用同一張舊 PCI RF 網卡時才套用這段。先核對 PCI 介面和舊 RF profile，確認管理網路使用其他介面。現場 PCI 介面為 enp0s31f6，其他機器請先調整範例中的介面名稱。

```bash
# 在 NetworkManager 將已核對的舊 PCI RF profile 設為不自動連線後執行
sudo install -D -m 0644 ./network/90-gcs-ignore-rf-pci.conf \
  /etc/NetworkManager/conf.d/90-gcs-ignore-rf-pci.conf
sudo nmcli general reload conf
sudo nmcli device set enp0s31f6 managed no
nmcli device status
```

已確認 RF 只有一個可用出介面時，可用 --configure-rf 讓安裝程式建立靜態位址。需要固定 .198 的 /32 主機路由時，請先依上述方式設定，再執行一般安裝。

### 3.2 攝影機專用網卡

將攝影機接到專用 USB 網卡並供電，新增 profile：

```bash
CAM_IF=enx001122334455
CAM_MAC=$(cat "/sys/class/net/$CAM_IF/address")
sudo nmcli connection add type ethernet ifname "$CAM_IF" con-name gcs-ipcam \
  802-3-ethernet.mac-address "$CAM_MAC" \
  connection.autoconnect yes connection.autoconnect-priority 100 \
  ipv4.method manual ipv4.addresses 192.168.168.250/32 \
  ipv4.routes "192.168.168.73/32" ipv4.route-metric 50 \
  ipv4.never-default yes ipv4.dad-timeout 3000 ipv6.method disabled
sudo nmcli connection up gcs-ipcam
ip route get 192.168.168.73
ping -c 3 -I "$CAM_IF" 192.168.168.73
```

預期攝影機路由使用 CAM_IF，來源 src 192.168.168.250。RF 與攝影機雖同為 192.168.168.x，實際接在不同網卡，以主機路由指定方向，不要把兩張都當成相同用途的 /24 網路。

此 profile 由 NetworkManager 保存，並設定自動連線；不是只在本次開機有效的 ip addr 命令。原理見 [NetworkManager IPv4 設定](https://networkmanager.dev/docs/api/latest/settings-ipv4.html)。新相機 IP 不同時，應同步調整相機路由與 RTSP 上游網址。

## 4. 安裝前設定教練 QGC

### 4.1 QGC 執行檔

先依 software/README.md 取得 QGC。Linux AppImage 範例位置是 ~/Applications/QGroundControl-x86_64.AppImage，以一般桌面使用者執行，不以 sudo 開啟 QGC。

```bash
chmod +x ~/Applications/QGroundControl-x86_64.AppImage
~/Applications/QGroundControl-x86_64.AppImage
```

若桌面為 Wayland 且出現視窗相容性問題，可試：

```bash
QT_QPA_PLATFORM=xcb ~/Applications/QGroundControl-x86_64.AppImage
```

### 4.2 教練 MAVLink

Q → Application Settings → Comm Links，新增或編輯 UDP 連線：

| 欄位 | 教練設定 |
| --- | --- |
| Name | UAV 或 Coach |
| Type | UDP |
| Listening / Local Port | 14551 |
| Server Addresses / Remote Hosts | 留空 |
| Automatically Connect on Start | 開啟 |
| High Latency | 關閉 |
| 內建 UDP Auto-Connect | 關閉，避免另外占用 14550 |
| MAVLink System ID | 255，與學員不同 |

RF 的 14550 由 MAVProxy 使用；教練 QGC 監聽本機 14551。

### 4.3 首次安裝影像服務前的攝影機網址

Q → Application Settings → Video；較舊版本在 General 下的 Video 區塊。

1. Source 選 RTSP Video Stream。
2. RTSP URL 先填入攝影機實體 IP 的完整可用網址，包含廠商所需的路徑與登入參數。
3. 按 Tab 或點其他欄位，確認 QGC 儲存設定；先確認畫面可更新。

本案廠商 URL 形式為：

```text
rtsp://<camera-ip>:554/user=<user>_password=<vendor-token>_channel=1_stream=0.sdp?real_stream
```

不要將 `<vendor-token>` 當成一般明文密碼直接替換。登入欄位使用該相機的實際帳號與裝置支援的方式。現場相機 IP 為 192.168.168.73。

當 config.json 的 video.upstream_url 留空且 GCS 尚未保存攝影機網址時，首次影像安裝會讀取使用者的 ~/.config/QGroundControl/QGroundControl.ini，再將實體攝影機 URL 獨立保存到 /etc/gcs/video-relay.json。**使用這個讀取方式時，首次安裝前需保留教練 QGC 的實體攝影機 URL。** 也可直接在 video.upstream_url 填入完整網址。

## 5. 設定部署參數並安裝整套

先編輯根目錄 config.json，範例值依實際環境調整。整套與個別元件的 install 都預設讀取這一份：

```json
{
  "rf_interface": "gcsrf0",
  "rf_address": "192.168.168.249/24",
  "rf_peer": "192.168.168.198",
  "hotspot_interface": "wlp2s0",
  "hotspot_ssid": "GCS_Hotspot",
  "hotspot_address": "10.42.0.1/24",
  "student_hotspot_ip": "10.42.0.203",
  "boot_default": "control",
  "video": {
    "upstream_url": "",
    "port": 8554,
    "codec": "h265"
  }
}
```

新機至少核對 rf_interface、hotspot_interface、student_hotspot_ip。video.upstream_url 留空時依序沿用 /etc/gcs/video-relay.json 的已保存網址、首次安裝時教練 QGC 的攝影機網址；填入完整 RTSP 網址時優先採用新值。影像監聽 IP 由 hotspot_address 推導，觀看網址為 rtsp://Hotspot-IP:video.port/camera。codec 可選 h265 或 h264，需與攝影機一致。

已安裝後也使用同一操作：修改根目錄 config.json，再重跑 01_install.sh。程式會更新 MAVLink 執行設定與部署紀錄；RF 位址／對端有改動時更新原 RF profile 與對端 /32 路由；Hotspot IP／SSID／介面有改動時更新受管理的 profile，保留原密碼。影像會套用相同 Hotspot IP 與 video 設定。變更網卡時，原 RF profile 必須仍可從舊介面識別，新介面需已存在。

有變更的元件先備份至 /var/backups/gcs/，更新失敗時嘗試回復該元件。MAVLink 與影像分階段更新；影像失敗時 MAVLink 可能已更新。未變更的元件沿用現有服務。更新保留學員 relay 當下的 on/off 模式；boot_default 於下次明確停止後啟動或開機生效。

網路更新需從 GCS 本機或獨立管理介面進行；變更中的網卡若承載目前 SSH 或優先預設路由，安裝會在修改前停止並說明。攝影機硬體 IP、攝影機專用網卡／路由、Windows 的 Wi-Fi IP 及 QGC 欄位由操作者同步調整。

在解壓目錄執行其中一種安裝方式：

```bash
# RF 已依第 3 節設定完成
sudo bash ./01_install.sh --user user

# 新機需要安裝程式建立 RF 靜態 profile
sudo bash ./01_install.sh --user user --configure-rf

```

登入帳號依該機的桌面帳號填寫。已安裝舊版的 GCS，請先完成服務名稱遷移。

首次新裝 MAVLink 會在終端要求輸入 Hotspot 密碼兩次，為 8–63 個可列印 ASCII 字元。密碼交由 NetworkManager 保存在 GCS，不需要寫入本資料包。此步驟需互動式終端，不要把安裝程式放進無人值守的 stdin 管線。

完成後：

```bash
sudo bash ./05_status.sh
```

預期 MAVProxy、學員 relay、影像 relay 為 active/running；Hotspot 是 oneshot，active/exited 是正常完成狀態。預設影像埠為 LISTEN 10.42.0.1:8554；改設定後使用 install 最後印出的新網址，05_status.sh 會讀取已安裝的影像埠。

若 MAVLink 已成功但影像安裝失敗，整套腳本會回報部分完成；先確認教練的實體 RTSP URL，再單獨重試：

```bash
sudo bash ./video/01_install.sh --user user
```

不要為了重試影像而直接移除正在運作的整套服務。

## 6. 教練與學員 QGC 的最終設定

### 6.1 兩台都從影像 relay 收看

教練與學員都選 RTSP Video Stream，把 RTSP URL 整欄替換成：

```text
rtsp://10.42.0.1:8554/camera
```

先完成教練的切換，再讓學員收看。攝影機只由 relay 保留單一上游；不要讓教練、學員或其他播放器持續各自直連實體攝影機。本次實機遇到多路直連停流，共用單一上游後已通過連續播放驗證。

在 Video 頁面，Stop recording when disarmed 保持關閉，其他選項先保留預設。若版本顯示 Disabled When Disarmed，取消勾選以便未解鎖時也收看。返回 Fly View，左下角影像可點擊切換成主畫面；用鏡頭前物體移動確認畫面持續更新。

### 6.2 學員 Windows 遙測

1. Windows 連上 GCS_Hotspot。
2. 執行 ipconfig，核對 Wi-Fi IPv4 是否等於根目錄 config.json 的 student_hotspot_ip。目前預設為 10.42.0.203。
3. 在學員 QGC 的 Comm Links 建立 UDP 14550，Remote Hosts 留空，開機自動連線開啟；關閉額外的內建 UDP Auto-Connect，避免重複連線。
4. 學員 MAVLink System ID 設為 254；教練保留 255。
5. 確認姿態／電池等資料更新，以及影像更新。

Hotspot DHCP 沒有在本包中設定學員 MAC／IP 保留。新機上線或 DHCP 配發變動後都要確認學員實際 IP；只連上 SSID 並不足以保證收到定址的 MAVLink。

### 6.3 開機行為

本包將 Hotspot、MAVProxy、學員 MAVLink relay、影像 relay 設為 systemd 開機啟用。QGC 視窗本身不是這些服務的一部分，使用者登入桌面後需開啟 QGC；本包不建立 QGC 桌面自動啟動設定。

boot_default=control 表示開機後學員可經 relay 回傳。影像服務若等待 Hotspot 位址，日誌會顯示 Waiting for hotspot，待位址就緒後綁定 8554。

## 7. 日常使用與控制開關

### 7.1 開始、停止及狀態

```bash
sudo bash ./02_start.sh
sudo bash ./05_status.sh
sudo bash ./03_stop.sh
```

02_start.sh 依序啟動 MAVLink／Hotspot 與影像。03_stop.sh 先停止影像，再停止 MAVLink／Hotspot。stop 只停止本次程序，開機啟用仍保留。

### 7.2 關閉／恢復學員操控

```bash
sudo gcs-student-control off
sudo gcs-student-control on
sudo gcs-student-control status
```

| 操作 | 學員回程 | 學員遙測 | 學員影像 | 教練 QGC |
| --- | --- | --- | --- | --- |
| off → monitor | relay 丟棄 | 繼續 | 繼續 | 保留原鏈路 |
| on → control | relay 轉給 MAVProxy | 繼續 | 繼續 | 保留原鏈路 |

off 會攔截學員經此 relay 送回的所有 MAVLink 封包，包含參數讀取請求；先前已載入的資訊與新的主動遙測仍可監看。on 會先排掉已排隊的舊回程封包，再放行新的封包。

已到達飛控的指令不會因此撤銷；這不是飛控的 RC 接管或強制停止功能。其他能直接連 RF 的網路路徑不受此 relay 開關限制。

學員 relay 的程序 restart 會保留原模式；整機重開或明確停止後重新開始，套用 boot_default。因此操作 03_stop.sh／02_start.sh 後，請再確認 status。

### 7.3 只操作影像

```bash
sudo bash ./video/03_stop.sh
sudo bash ./video/02_start.sh
sudo systemctl restart gcs-video-relay.service
```

這些操作不切換學員 MAVLink 控制模式。影像服務無額外登入機制，Hotspot 上能連到 8554 的用戶端可收看；MAVLink 的指定學員 IP 不限制影像用戶端。

### 7.4 取消／恢復開機啟用

```bash
# 只取消未來的開機啟用，當前程序繼續運行
sudo systemctl disable gcs-video-relay.service gcs-mavproxy.service \
  gcs-student-relay.service gcs-hotspot.service

# 恢復開機啟用
sudo systemctl enable gcs-video-relay.service gcs-mavproxy.service \
  gcs-student-relay.service gcs-hotspot.service
```

要當下停止使用 03_stop.sh。enable／disable 與 start／stop 是不同操作。

## 8. 驗證與故障排查

### 8.1 影像連通性

Windows PowerShell：

```powershell
Test-NetConnection 10.42.0.1 -Port 8554
python .\tools\probe_rtsp.py --report .\windows_video_check.json
```

預期 TcpTestSucceeded=True，探針結果 ok=true，DESCRIBE／SETUP／PLAY 均為 200，rtp_packets 大於 0。此探針可與 QGC 同時執行，不會占用 MAVLink UDP 14550。

### 8.2 MAVLink 雙向唯讀驗證

先關閉學員 QGC，避免和探針同時占用 UDP 14550。在 Windows 的解壓目錄執行：

```powershell
python .\tools\probe_mavlink.py --report .\windows_mavlink_check.json
```

此工具收心跳並請求讀取 SYS_AUTOSTART，不解鎖、不起飛、不寫入參數。PX4 預期 status=bidirectional_ok；其他韌體需改用該韌體已知的唯讀參數。測試後結束探針再開 QGC。

需要驗證 off 時，先確認可安全進行鏈路測試，教練執行 off 後再跑探針，預期有心跳而參數請求無回覆；結束後依教學需求決定是否恢復 on，不能把 on 當成自動清理步驟。

### 8.3 常見問題

| 現象 | 檢查與處置 |
| --- | --- |
| QGC Disconnected | 先看 05_status.sh；RF 必須送到 .249:14550，教練 QGC 用 14551 |
| UDP 14550 occupied | 關閉占用該埠的舊 QGC／探針；教練不可和 MAVProxy 搶 14550 |
| 學員有遙測但參數請求沒回應 | 看 student-control status 是否為 monitor，核對學員 IP／來源埠 |
| 學員完全無遙測 | 核對 Wi-Fi 實際 IP、指定學員 IP、QGC UDP 14550；查 Windows 防火牆有無既存 QGC 封鎖規則，由操作者只修正對應規則 |
| 等待視訊 | 完整替換成 relay URL，確認 TCP 8554、服務與上游網路；可暫切 Source=Disabled 再切回 RTSP |
| 影像出現後停止 | 關掉其他直連實體攝影機的播放器，教練與學員都從 relay 取流 |
| Camera ping／RTSP 不通 | 檢查供電、USB 網卡、ip route get .73 的介面與 src .250 |
| RF 走錯網卡 | 確認 Microhard USB 枚舉成 gcsrf0，.198/32 走 gcsrf0，來源為 .249 |
| 服務 active 但影像埠未出現 | 查日誌是否等待 Hotspot；先讓 10.42.0.1 出現 |
| 安裝出現 apt／pip 下載錯誤 | 確認 GCS 的管理網路、DNS 與套件來源，依失敗步驟重試 |

GCS 常用唯讀診斷命令：

```bash
sudo bash ./05_status.sh
ip route get 192.168.168.198
ip route get 192.168.168.73
ss -lunp 'sport = :14550'
ss -ltnp 'sport = :8554'
ss -tnp 'dst 192.168.168.73:554'
journalctl -u gcs-video-relay.service -n 50 --no-pager
journalctl -u gcs-student-relay.service -n 50 --no-pager
journalctl -u gcs-mavproxy.service -n 50 --no-pager
```

### 8.4 已安裝後變更學員 IP

1. 先確認新 IP 與 Hotspot 同網段。
2. 修改部署包根目錄 config.json 的 student_hotspot_ip。
3. 執行 sudo bash ./01_install.sh --user user，將 user 換成原部署帳號。程式會先備份，再更新執行設定與部署紀錄。
4. 用 sudo gcs-student-control status 確認模式，再以 ss -uan 核對學員端點與 QGC 遙測。

修改 config.json 後必須重跑 install 才會套用，單純 start 不會讀取部署包的新設定。只想更新 MAVLink 時，可執行 sudo bash ./mavlink/01_install.sh --user user，仍讀取根目錄 config.json。

### 8.5 換攝影機或修改登入資訊

先確認新攝影機專用路由，再修改根目錄 config.json 的 video.upstream_url，填入攝影機完整 RTSP 網址，然後重跑 install。不要把 upstream_url 設成 relay 自己。Hotspot IP 與 video.port 不變時，兩台 QGC 仍使用相同 relay URL。只想更新影像時，可執行 sudo bash ./video/01_install.sh --user user。

設定檔若含相機登入資訊，不要放回共用 ZIP。可使用 sudo bash ./01_install.sh --user user --config /受限路徑/config.json 指定私有設定檔。video.upstream_url 留空表示沿用現有網址，會保留 GCS 上保存的帳密。詳細路徑對照見 ARCHITECTURE.md。

### 8.6 重開機後驗收

```bash
ip -br -4 address show dev gcsrf0
ip route get 192.168.168.198
nmcli device status
sudo bash ./05_status.sh
journalctl -b -u gcs-mavproxy.service --no-pager -n 30
```

現場預期 gcsrf0 為 192.168.168.249/24，對端路由為 dev gcsrf0 src 192.168.168.249。已採用忽略設定的 PCI 埠顯示 unmanaged。四個 gcs 服務應為 active，MAVProxy 日誌應顯示 Detected vehicle。

教練登入桌面後確認 QGC 監聽 UDP 14551。現場已配置桌面登入自啟，新機可依桌面環境設定。Windows 連上 GCS_Hotspot 後，以 QGC 或第 8.1／8.2 節的探針確認遙測和影像。

## 9. 停止、移除與還原

### 9.1 暫時不用

```bash
sudo bash ./03_stop.sh
```

設定與安裝檔仍在，之後可用 02_start.sh；開機自動啟用仍保留。

### 9.2 完整移除本包服務

確認飛控未解鎖，從本機或獨立管理連線執行：

若移除後需要恢復直接連攝影機，先把含 upstream_url 的設定安全備份於 GCS：

```bash
sudo install -d -m 700 /var/backups/gcs/before_remove
sudo cp -p /etc/gcs/video-relay.json /var/backups/gcs/before_remove/
```

只有尚存在的設定檔才需備份；本機副本仍含登入資訊，不要放進共用 ZIP。接著執行整套移除：

```bash
sudo bash ./04_remove.sh
sudo bash ./05_status.sh
```

移除順序為影像服務，再移除 MAVLink／學員 relay／受管理的 Hotspot。會刪除相應服務、控制 CLI、啟動腳本及專用設定。首次新裝且由安裝程式擁有的 MAVProxy venv 會移除；沿用／移轉的 runtime 不會一律刪除。

以下保留：QGC 執行檔與 QGC 使用者設定、RF 靜態網路、攝影機 gcs-ipcam profile、遙測日誌，以及由系統安裝的 GStreamer 套件。05_status.sh 在移除後可能看到 not-found／inactive，這是驗收的一部分。

若某一步失敗，腳本會停止並回報，不會盲目刪除整個 /opt/gcs 或 /etc/gcs。檢查日誌後可重新執行；如 manifest 遺失但仍有服務檔，先人工核對殘留項目。

### 9.3 只移除單一元件

```bash
# 只移除影像；MAVLink 繼續
sudo bash ./video/04_remove.sh

# 只操作 MAVLink 時請先處理影像服務對 Hotspot 的依賴
sudo bash ./video/03_stop.sh
sudo bash ./mavlink/04_remove.sh
```

### 9.4 回復為教練 QGC 直接使用 RF／攝影機

1. 先完成上述服務移除，確認 UDP 14550 不再由 MAVProxy 占用。
2. 教練 QGC 的 Comm Link 改回 UDP 14550，Remote Hosts 留空；RF 仍送到 GCS .249:14550。
3. 若需要直接看攝影機，把 QGC 的 RTSP URL 改為實體攝影機的完整網址，並確認沒有其他程式同時直連。
4. 影像 relay 被移除後，原本的 rtsp://10.42.0.1:8554/camera 不會繼續提供影像。

若不再需要攝影機網路，可另行核對並刪除本 SOP 建立的 gcs-ipcam profile：

```bash
nmcli connection show gcs-ipcam
sudo nmcli connection delete gcs-ipcam
```

這是額外網路還原操作，不包含在 04_remove.sh 中。RF 位址若需還原，也應依當初的 profile 備份處理，不要刪除其他用途的管理介面。


若要恢復舊 PCI RF，先從獨立管理連線停用 USB 的 .249 profile，移除忽略 PCI 的 NetworkManager 設定並重新載入，執行 sudo nmcli device set enp0s31f6 managed yes，再依備份恢復 PCI profile 的自動連線。確認 .249 只配置在要使用的 RF 介面，且 .198/32 走同一張網卡。

## 10. 驗證範圍與紀錄

2026-11-11 現場已完成一次 GCS 重開機驗證。Microhard USB 自動取得 .249、PCI 維持 unmanaged，四個服務均自啟，MAVProxy 偵測到飛控。教練 QGC 由桌面登入自啟，Windows 學員端由使用者確認通過。

[驗證紀錄](verification/README.md) 說明實機與模擬驗證範圍。新機首次安裝仍須依本 SOP 驗收。
