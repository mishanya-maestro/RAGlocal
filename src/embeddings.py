import time

import requests

import config


def get_embedding(text, retries=3, delay=2):
    """превращает текст в вектор"""

    if not text:
        return None

    if len(text) > 7000:
        text = text[:7000]

    if config.MODE in ("local", "api"):
        url = f"{config.OLLAMA_BASE_URL}/embeddings"
        headers = {"Content-Type": "application/json"}
        payload = {"model": config.EMBEDDING_MODEL, "input": text}
    else:
        url = "https://openrouter.ai/api/v1/embeddings"
        headers = config.HEADERS
        payload = {"model": config.EMBEDDING_MODEL, "input": text}

    for attempt in range(retries):
        try:
            response = requests.post(
                url=url, headers=headers, json=payload, timeout=30
            )

            if response.status_code == 429:
                print(f"  Rate limit, ждем {delay * (attempt + 1)}с...")
                time.sleep(delay * (attempt + 1))
                continue

            if response.status_code != 200:
                print(f"  Ошибка HTTP {response.status_code}: {response.text[:200]}")
                time.sleep(delay)
                continue

            data = response.json()

            if "data" in data and data["data"]:
                return data["data"][0]["embedding"]
            if "embeddings" in data and data["embeddings"]:
                return data["embeddings"][0]
            if "embedding" in data and data["embedding"]:
                return data["embedding"]

            print(f"  Нет поля embedding в ответе: {str(data)[:200]}")
            time.sleep(delay)
            continue

        except Exception as e:
            print(f"  Ошибка запроса (попытка {attempt + 1}): {e}")
            time.sleep(delay * (attempt + 1))

    print("  Не удалось получить эмбеддинг после всех попыток")
    return None
