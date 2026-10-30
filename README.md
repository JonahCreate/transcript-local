# I Hate Transcription

**開發中 · v0.2.0a1 · Apple Silicon Mac**

*Less typing. More living.*

已安裝舊版？請依 [升級教學](UPGRADE.md) 更新一次，保留既有任務與模型。

把錄音留在自己的電腦：選檔、轉錄、回聽校稿、管理詞彙，再匯出繁體逐字稿與字幕。**不需 API key，也沒有雲端轉錄費用。** 首次安裝與模型下載需要網路，下載完成後可離線辨識。辨識速度取決於硬體與模型，不保證即時完成。

> 本版本尚待外部使用者完成安裝到匯出的驗收，不是已驗證的穩定版。沒有說話者標記、AI 自動改寫或雲端轉錄功能。

## 安裝

正式支援 **Apple Silicon（M 系列）Mac**；目前僅在 macOS 26.6.2 驗證，其他 macOS 版本尚待確認。建議至少 8 GB 記憶體並保留 10 GB 以上磁碟空間；長錄音的輸入副本與回聽 WAV 需要額外空間。Intel、Windows、Linux 尚未列入正式支援。

1. 安裝 [Homebrew](https://brew.sh) 與 Git（macOS 顯示提示時安裝 Command Line Tools）。
2. 安裝工具：

   ```sh
   brew install uv ffmpeg
   ```

3. Clone 本專案，進入目錄：

   ```sh
   git clone https://github.com/JonahCreate/transcript-local.git
   cd transcript-local
   ./setup.sh
   ./start.sh
   ```

`setup.sh` 使用 uv 管理 Python 3.12 與鎖定依賴；不需要修改系統 Python，並安裝 `tr`、`iht` 與 `transcript-local` 入口；如有同名檔案會保留並提示。必要時在 `~/.zshrc` 加入 `~/.local/bin` 路徑。`start.sh` 使用已安裝環境，不需要每次上網更新依賴。

在終端機選單選擇「開啟網頁工作台」後，到「環境與模型」確認模型。工具會自動辨識可用的 Hugging Face 模型快取；若模型尚未下載，再選擇並下載模型。Medium 建議一般中文錄音使用；Small 較輕量。大小僅為估計，請以下載實際用量為準。下載來源為 Hugging Face 的 Systran faster-whisper 模型，模型與程式有各自授權。

## 操作

輸入 `tr`，使用上下鍵＋Enter 或數字選擇操作。Esc／Control+C 關閉選單；背景轉錄仍會繼續。重複開啟共用同一個服務。選單提供進度、更新、正常停止；無回應時可用 `iht stop --force`。macOS 預設 zsh 的 `end` 是保留字，因此提供 `iht stop`／`transcript-local stop` 作為停止指令。

1. 到「轉錄工作台」選取多個音訊或影片（也可拖曳）。
2. 選模型、語言；輕度降噪預設關閉。按「加入轉錄佇列」。
3. 一次處理一個任務，其他排隊。停止、程式關閉或意外中斷後，可按「續跑」。
4. 任務完成後點選名稱，點時間戳回聽、修改文字、勾選「已確認」。按「儲存修改」，確認顯示新版本號。編輯停止一秒後會另存草稿；重開時可預覽並恢復。
5. 匯出 MD、TXT、SRT 或含原始辨識與修訂的 JSON。

相同內容和設定會沿用現有任務。「建立新版本」另建任務，保留舊校稿。低信心、重複與分段交界是回聽提示，**不是錯誤判定或正確率保證**。修改字幕文字不會重新計算語音對齊。錄音沒有聲音、無音軌或損壞時，任務會顯示錯誤，不會假裝完成。

### 詞彙表

「我的詞彙」可新增辨識提示（例如「共槽」「packing list」），只影響之後建立的任務。提示不是強制替換。

替換規則每行一筆，例如：

```text
供鈔 → 共槽
```

規則不會自動套用。在校稿畫面按「預覽詞彙替換」，確認語境，勾選要修改的片段後才套用。原始辨識仍保留。修改與預覽使用版本檢查，避免兩個視窗互相覆蓋；版本衝突時請先複製未儲存內容，再重新載入稿件。

### 終端機

在 repo 目錄內執行：

```sh
.venv/bin/transcript-local doctor
.venv/bin/transcript-local download-model small
.venv/bin/transcript-local transcribe "/完整路徑/錄音.m4a" --model small
.venv/bin/transcript-local transcribe "/完整路徑/資料夾" --recursive
.venv/bin/transcript-local transcribe "/完整路徑/錄音.m4a" --enhance --force
```

離線時也可直接使用 `.venv/bin/transcript-local`。資料夾批次支援子資料夾；檔案依序處理，一個失敗不阻止其他檔案。CLI 批次按 Ctrl+C 停止後，完成的片段會保留。

CLI 預設把匯出存到任務的 `exports/`；可用 `--output-dir "/匯出目錄"` 指定位置。每次匯出都有獨立版本目錄，不覆寫先前匯出。CLI 與網頁共用資料；**同一資料目錄同時只能開一個程式**，執行 CLI 批次前先關閉網頁服務。

直接開啟背景工作台：`./start.sh open`。開發與測試可用前景模式 `./start.sh serve --no-browser`，或 `./start.sh serve --port 8765` 指定埠；前景模式與背景模式使用同一個資料鎖。

## 資料與隱私

預設資料目錄：`~/Library/Application Support/transcript-local/`，與 repo 分開。

- `models/`：由本工具明確下載的模型與版本紀錄。既有 Hugging Face 快取可直接使用，不需複製。
- `jobs/`：輸入檔副本、16 kHz 單聲道回聽 WAV、片段進度、錯誤紀錄與校稿 JSON。
- `glossary.json`：個人提示與替換規則。
- `runtime/`：背景管理紀錄、輪替日誌、獨立版本環境及更新前的校稿備份。此目錄含本機驗證資訊，不要整份分享。
- `preferences.json`：上次任務與校稿位置。
- `jobs/*/drafts/`：各視窗獨立草稿，與正式校稿分開。
- `trash/`：「清除」的任務，可手動還原；尚未永久刪除，所以仍占空間。

網頁選檔會把副本串流傳給**同一台電腦**的服務，不送到雲端；單檔上限 20 GB。辨識僅讀本機模型或既有 Hugging Face 快取，不自動下載；續跑時會使用任務原本的模型版本。程式使用 CPU int8，第一版不做 Metal/GPU 加速。

服務只綁定 `127.0.0.1`，啟動連結含一次工作階段憑證，請勿分享。憑證交換成 HttpOnly、SameSite cookie 後，會從網址移除。程式不載入外部字型、分析追蹤或 CDN 資源。模型下載會連線 Hugging Face，但不附帶錄音。

清除資料：先停止程式，再用 Finder 管理資料目錄。要還原 trash 任務，將原任務資料夾移回 `jobs/`，並改回名稱前 32 碼的任務 ID。備份時複製整個資料目錄。新程式不會改動其他轉錄工具的資料與指令。

可用 `TRANSCRIPT_LOCAL_DATA` 或入口前的 `--data-dir PATH` 使用獨立目錄，例如：

```sh
.venv/bin/transcript-local --data-dir "/另一個位置" doctor
```

## 排錯與更新

- 沒有頁面：輸入 `tr` 並選擇開啟工作台；服務健康檢查成功後才會開瀏覽器。
- 工作階段失效：從 `tr` 選單重新開啟，工具會建立目前服務的有效工作階段。
- 缺少 FFmpeg／uv：執行 `brew install ffmpeg uv`，再跑 `./setup.sh`。
- 下載失敗：確認網路與空間，按「重試下載」；完成模型下載前不能轉錄。
- 轉錄失敗：檢查來源有音訊、剩餘磁碟與任務錯誤。可查看該任務的 `worker.log`，分享前遮去私人資訊。
- 小聲／背景吵：可建立開啟降噪的新版本比較，降噪也可能損傷細小人聲。
- 中文辨識不準：優先選中文，加入領域提示並人工回聽。自動語言判斷可能誤判。
- 關閉瀏覽器或終端機選單不會停止背景服務。選擇「停止背景服務」或執行 `iht stop`；只有開發用前景 serve 模式才以 Control+C 停止。
- 更新：網頁開啟時及按「檢查更新」時，先唯讀比對 GitHub main 的版本。只有確認有新版才提供安裝確認；已是最新版不排更新、不鎖校稿、不重啟。離線或版本不明時不宣稱已是最新。從選單或網頁明確觸發安裝。先保存各視窗修改並等待任務／模型下載完成。管理程序在獨立版本目錄安裝及驗證，成功後切換；新版啟動失敗時恢復上一版本。更新失敗可查看 `runtime/update.log`。
- 原始 repo 中未提交的修改不會被發布到更新環境。更新來源限定此專案的 GitHub main；目前尚未引入不相容資料格式遷移。
- 暫時性辨識啟動異常最多自動重試一次。服務意外退出十分鐘內最多自動恢復一次；使用者取消的任務不自動續跑。檔案損壞、模型缺失與空間不足需依提示處理。

## 開發與驗證

```sh
uv sync --frozen --no-editable --extra test
.venv/bin/playwright install chromium --with-deps
.venv/bin/pytest
```

測試使用暫存資料與合成音訊，不使用任何私人錄音。CI 不下載大型模型；真實模型辨識與外部使用者驗收狀態見 [VALIDATION.md](VALIDATION.md)。回報問題請附平台、版本、重現步驟與已去識別的錯誤訊息。

## 授權與來源

自行撰寫的程式採 [MIT](LICENSE)。依賴、模型與 FFmpeg 保留各自授權，詳見 [THIRD_PARTY.md](THIRD_PARTY.md)。本專案沒有搬入 FunASR 或 VoiceStudio 原始碼，也不包含模型權重、錄音、私人逐字稿或金鑰。
