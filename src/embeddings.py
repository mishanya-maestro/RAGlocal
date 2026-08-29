import time

import requests

import config


_ollama_session = requests.Session()


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
            response = _ollama_session.post(
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


def get_embeddings(texts: list[str], batch_size: int = 8, retries: int = 3, delay: float = 2.0):
    """Пакетное получение эмбеддингов через Ollama /api/embed.

    Возвращает список векторов той же длины, что и входной список.
    None для неудавшихся элементов.
    """
    if not texts:
        return []

    if config.MODE not in ("local", "api"):
        # Для OpenRouter и прочих — пока последовательно.
        return [get_embedding(t, retries=retries, delay=delay) for t in texts]

    base = config.OLLAMA_BASE_URL.rstrip("/")
    if base.endswith("/v1"):
        base = base[:-3]
    url = f"{base}/api/embed"
    headers = {"Content-Type": "application/json"}
    results: list = [None] * len(texts)

    for batch_start in range(0, len(texts), batch_size):
        batch_idx = list(range(batch_start, min(batch_start + batch_size, len(texts))))
        batch = [texts[i] for i in batch_idx]
        batch = [t[:7000] if t else "" for t in batch]

        for attempt in range(retries):
            try:
                response = _ollama_session.post(
                    url=url,
                    headers=headers,
                    json={"model": config.EMBEDDING_MODEL, "input": batch},
                    timeout=120,
                )
                if response.status_code == 429:
                    time.sleep(delay * (attempt + 1))
                    continue
                if response.status_code != 200:
                    print(f"  batch embed HTTP {response.status_code}: {response.text[:200]}")
                    time.sleep(delay)
                    continue

                data = response.json()
                embeddings = data.get("embeddings") or []
                if len(embeddings) != len(batch):
                    print(f"  batch size mismatch: {len(embeddings)} vs {len(batch)}")
                    time.sleep(delay)
                    continue
                for i, emb in zip(batch_idx, embeddings):
                    results[i] = emb
                break
            except Exception as e:
                print(f"  batch embed error (попытка {attempt + 1}): {e}")
                time.sleep(delay * (attempt + 1))

    return results
