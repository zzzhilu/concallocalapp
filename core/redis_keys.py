"""
ConCall Local Model — Redis Key & Channel 常數定義

所有服務共用此模組，確保 key 命名一致性。
"""

# =============================================================================
# Redis Lists (Queue)
# =============================================================================
AUDIO_QUEUE = "audio_queue"                     # 音訊 chunk 佇列 (LPUSH / BRPOP)

# =============================================================================
# Redis Keys (Buffer / State)
# =============================================================================
AUDIO_BUFFER_PREFIX = "audio_buffer:"           # + session_id → 完整音訊供 diarization
SESSION_DATA_PREFIX = "session:"                # + session_id → session 元資料
SESSION_TRANSCRIPT_PREFIX = "session_transcript:"  # + session_id → 完整轉寫紀錄
SESSION_LANG_PREFIX = "session:lang:"               # + session_id → 語言偏好 (zh / en-translate)

# =============================================================================
# Redis Pub/Sub Channels
# =============================================================================
CHANNEL_TRANSCRIPTIONS = "ch:transcriptions"    # ASR 輸出：即時轉寫結果
CHANNEL_TRANSLATIONS = "ch:translations"        # LLM 輸出：翻譯結果
CHANNEL_DIARIZATION = "ch:diarization"          # Pyannote 輸出：說話者分離
CHANNEL_SUMMARY = "ch:summary"                  # LLM 輸出：會議摘要
CHANNEL_STATUS = "ch:status"                    # 系統狀態通知

# =============================================================================
# Session Control
# =============================================================================
SESSION_END_SIGNAL = "session:end"              # 會議結束信號 key

# =============================================================================
# Glossary (Custom Vocabulary)
# =============================================================================
GLOSSARY_KEY = "glossary:terms"                  # 自訂詞彙表 JSON (供翻譯 prompt 注入)
