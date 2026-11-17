# Software prerequisites

這個目錄列出軟體取得方式。本包包含本案部署程式原始碼與腳本，不捆綁大型第三方執行檔或離線套件庫。

## QGroundControl

使用 [官方下載及安裝頁](https://docs.qgroundcontrol.com/master/en/qgc-user-guide/getting_started/download_and_install.html)，選適合硬體架構的 Linux AppImage 或 Windows 安裝程式，並依該版本說明安裝相依套件。

現場觀察版本為 Linux QGC 5.1.3、Windows QGC 5.0.8；這是此次驗證快照，不表示它們是未來的最新版。不同版本設定頁位置可能不同，Video 可能在獨立頁面或 General 內。

Linux 範例將 AppImage 放在 ~/Applications/，以一般桌面帳號執行。此資料包不變更或自動安裝 QGC 本體、不建立桌面自動啟動項目。

## Installed by scripts

- MAVLink 新裝：python3-venv，並在專用 venv 安裝 MAVProxy==1.8.74 與 future。依賴由 pip 解析，不是完整離線鎖定的 wheel 集合。
- 影像：Python GI、GStreamer RTSP Server 與 H.265 所需 good／bad 插件；缺少時由 apt-get 安裝。
- 系統前置：Ubuntu 24.04、NetworkManager、支援 AP 的 Wi-Fi 與實際可用的網卡。

Windows 的 tools/probe_mavlink.py 與 probe_rtsp.py 使用 Python 3 標準函式庫。若 Windows 未安裝 Python，可依 [Python 官方 Windows 說明](https://docs.python.org/3/using/windows.html) 安裝；QGC 本身不依賴這兩個驗證腳本。

如需離線部署，應另備對應架構的 QGC、Ubuntu deb 相依與 Python wheels，再於乾淨目標機驗證。本包不宣稱可在無網路環境完成首次安裝。
