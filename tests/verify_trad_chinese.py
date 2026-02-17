
import asyncio
import json
import os
import uuid
import redis.asyncio as aioredis

# Redis Config
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
CHANNEL_TRANSCRIPTIONS = "ch:transcriptions"
CHANNEL_TRANSLATIONS = "ch:translations"

async def verify_translation():
    redis_conn = aioredis.from_url(REDIS_URL, decode_responses=True)
    pubsub = redis_conn.pubsub()
    await pubsub.subscribe(CHANNEL_TRANSLATIONS)
    print(f"Subscribed to {CHANNEL_TRANSLATIONS}")

    session_id = str(uuid.uuid4())
    # 模擬簡體中文輸入
    test_text = "这是一个测试，请把这段文字翻译成英文。"
    # 預期：
    # 1. worker-intelligence 看到中文，翻譯成英文 (因為 Prompt 說中文->英文)
    # 等等，如果輸入是中文，它翻譯成英文。那怎麼測繁體？
    
    # Prompt:
    # 1. 如果輸入是中文（包含繁體和簡體），翻譯成英文。
    # 2. 如果輸入是英文，翻譯成繁體中文。
    
    # Case 1: Input English -> Output Traditional Chinese
    test_text_en = "This is a test, please translate this text to Traditional Chinese."
    
    data = {
        "session_id": session_id,
        "text": test_text_en,
        "language": "en",
        "timestamp": 1234567890
    }

    print(f"Publishing to {CHANNEL_TRANSCRIPTIONS}: {data}")
    await redis_conn.publish(CHANNEL_TRANSCRIPTIONS, json.dumps(data))

    try:
        async for message in pubsub.listen():
            if message["type"] != "message":
                continue
            
            payload = json.loads(message["data"])
            if payload.get("session_id") == session_id:
                print(f"Received translation: {payload}")
                translated = payload.get("translated_text", "")
                print(f"Translated Text: {translated}")
                
                # 簡單檢查：是否包含簡體字 (這很難嚴格檢查，人工看就好)
                # 或者檢查是否包含 "測試" (繁體) vs "测试" (簡體)
                if "測試" in translated or "文字" in translated: 
                     print("✅ Traditional Chinese detected!")
                elif "测试" in translated:
                     print("❌ Simplified Chinese detected!")
                
                break
    except asyncio.TimeoutError:
        print("Timeout waiting for translation.")
    finally:
        await redis_conn.close()

if __name__ == "__main__":
    asyncio.run(verify_translation())
