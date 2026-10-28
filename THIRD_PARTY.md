# 第三方來源

本專案只發布自行撰寫的整合程式。以下套件透過安裝程序取得，模型需由使用者明確下載；請以對應鎖定版本及上游 LICENSE 為準。

| 元件 | 用途 | 上游 |
|---|---|---|
| faster-whisper（MIT） | 本機語音辨識 | https://github.com/SYSTRAN/faster-whisper |
| CTranslate2（MIT） | 推論執行 | https://github.com/OpenNMT/CTranslate2 |
| Whisper（MIT） | 模型來源 | https://github.com/openai/whisper |
| Systran faster-whisper models | 轉換後模型，權重不包含於 repo | https://huggingface.co/Systran/faster-whisper-medium 、https://huggingface.co/Systran/faster-whisper-small |
| FastAPI（MIT）／Starlette（BSD-3-Clause） | 本機服務 | https://github.com/fastapi/fastapi 、https://github.com/Kludex/starlette |
| Uvicorn（BSD-3-Clause） | HTTP 服務 | https://github.com/Kludex/uvicorn |
| OpenCC Python reimplementation（Apache-2.0） | 繁體轉換 | https://github.com/yichen0831/opencc-python |
| Hugging Face Hub（Apache-2.0） | 模型下載 | https://github.com/huggingface/huggingface_hub |
| FFmpeg | 音訊轉換，使用使用者安裝的版本 | https://ffmpeg.org/legal.html |
| uv（MIT / Apache-2.0） | Python 與依賴管理 | https://github.com/astral-sh/uv |

轉換模型沿用上游模型的條件；發布其他模型前須另確認其來源與授權。本專案不提供 FunASR、CAM++、SenseVoice、pyannote 或 VoiceStudio 的程式與權重。
