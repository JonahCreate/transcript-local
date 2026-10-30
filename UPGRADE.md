# 從舊版升級到 I Hate Transcription

這份教學適用於已安裝 Transcript Local 的 Apple Silicon Mac 使用者。

1. 先把網頁上需要保留的修改儲存；舊版曾有連續儲存問題，建議把最後修改的段落另外複製備份。
2. 等轉錄完成。在原本啟動工具的終端機按 Control+C 停止舊服務。若原視窗已關閉而服務仍在，請先確認沒有未儲存修改，再尋求協助停止；不要隨便刪除鎖檔或終止所有 Python 程序。
3. 在終端機貼上以下整行，並選擇原本 clone 的 `transcript-local` 資料夾：

```sh
repo=$(osascript -e 'POSIX path of (choose folder with prompt "請選擇 transcript-local 專案資料夾")') && cd "$repo" && git remote get-url origin | grep -Eq '(^|[:/])JonahCreate/transcript-local(\.git)?$' && git pull --ff-only && ./setup.sh
```

4. 完成後重新開啟終端機，輸入 `tr`。用上下鍵、Enter 或數字選擇「開啟網頁工作台」。
5. 檢查原任務與校稿。原本的資料及模型目錄保持相容，無須重新上傳或下載完整模型。

## 日常操作

- `tr`：開啟 IHT 選單。Esc、選單中的離開、Control+C 都只關閉選單。
- `iht open`／`transcript-local open`：直接開啟工作台。
- `iht stop`／`transcript-local stop`：正常停止背景服務，完成片段保留。
- `iht stop --force`：正常停止等候十秒仍無回應時才使用；進行中的片段下次重跑。
- macOS 預設 zsh 的 `end` 是保留字，不能直接使用 `end tr`。在沒有名稱衝突的其他 shell 才安裝 `end tr [--force]`。
- 帶參數的 `tr` 保留原系統文字轉換功能，例如 `printf abc | tr a-z A-Z`。

關閉瀏覽器或終端機後，背景轉錄仍會繼續。要停止，請使用選單或停止指令。背景服務只在使用時啟動，不設定登入自動啟動。

## 儲存與恢復

修改停止一秒後，工具嘗試把草稿備份到本機。正式稿需按「儲存修改」，看到版號才表示成功。立即關機或連線中斷前尚未送出的內容仍可能遺失。

找到草稿時，按「預覽恢復」，比對並勾選要帶回的內容，確認後再儲存。兩個視窗有版本衝突時，工具保留草稿，不會直接覆蓋別人的修改。

## 更新

從選單或網頁按「更新程式」，先儲存各視窗的修改，並等待轉錄及模型下載完成。新版在獨立環境安裝、驗證後才切換；失敗會保留原服務或恢復上一版本。更新需要網路，完成後會開啟新網頁。

若出現無法確認目前安裝版本的訊息，先儲存校稿並使用 `iht stop` 停止服務，再到專案資料夾執行 `./setup.sh`，接著重新輸入 `tr`。若程式檔案有自行修改，請先保留修改並確認版本；不要直接刪除檔案或強制覆蓋。

資料仍在 `~/Library/Application Support/transcript-local/`。`runtime/` 保存服務紀錄、日誌、版本環境與更新前的設定／校稿備份。分享日誌前檢查私人資訊，勿分享 `service.json` 或 `supervisor.json`，其中含本機驗證資訊。

這是開發中試用版，外部使用者完成驗收前，不宣稱為穩定版。
