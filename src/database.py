

import re
import time

import chromadb
from chromadb.config import Settings

import config
import embeddings

client = chromadb.PersistentClient(
    path=str(config.CHROMA_PERSIST_DIR),
    settings=Settings(anonymized_telemetry=False),
)

collection = client.get_or_create_collection(name=config.COLLECTION_NAME)


def _split_paragraphs(text: str) -> list[str]:
    parts = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    if len(parts) > 1:
        return parts
    parts = [p.strip() for p in text.split("\n") if p.strip()]
    if len(parts) > 1:
        return parts
    return re.split(r"(?<=[\.\?!])\s+", text)


def chunk_text(text, chunk_size=None, overlap=None):
    if chunk_size is None:
        chunk_size = config.CHUNK_SIZE
    if overlap is None:
        overlap = config.CHUNK_OVERLAP

    if len(text) <= chunk_size + 200:
        return [text]

    paragraphs = _split_paragraphs(text)

    chunks: list[str] = []
    current = ""
    for p in paragraphs:
        if not current:
            current = p
            continue
        if len(current) + 1 + len(p) <= chunk_size:
            current = f"{current}\n{p}"
        else:
            chunks.append(current)
            if overlap > 0 and len(current) > overlap:
                tail = current[-overlap:]
                current = f"{tail}\n{p}"
            else:
                current = p
    if current:
        chunks.append(current)

    # Если какой-то параграф сам по себе огромный, добиваем его линейным fallback.
    out: list[str] = []
    for ch in chunks:
        if len(ch) <= chunk_size + 200:
            out.append(ch)
            continue
        start = 0
        step = max(1, chunk_size - overlap)
        while start < len(ch):
            out.append(ch[start : start + chunk_size])
            start += step
    return out


def _embed_text(code, number, chunk_idx, total_chunks, body):
    return (
        f"Закон: {code}\n"
        f"Статья: {number}\n"
        f"Часть: {chunk_idx + 1}/{total_chunks}\n"
        f"Текст: {body}"
    )


def add_articles(articles, reset_collection: bool = False):
    """индексирует статьи с пакетными эмбеддингами."""
    global collection
    if reset_collection:
        try:
            client.delete_collection(config.COLLECTION_NAME)
        except Exception as e:
            print(f"  delete_collection warning: {e}")
        collection = client.get_or_create_collection(name=config.COLLECTION_NAME)

    print(f"Начинаем индексацию {len(articles)} статей...")
    skipped = 0
    for i, article in enumerate(articles):
        chunks = chunk_text(article["text"])
        total = len(chunks)
        short_code = article.get("short_code", "")

        embed_texts = [
            _embed_text(article["code"], article["number"], chunk_idx, total, body)
            for chunk_idx, body in enumerate(chunks)
        ]
        vectors = embeddings.get_embeddings(embed_texts, batch_size=16)

        for chunk_idx, (body, vector, embed_text) in enumerate(zip(chunks, vectors, embed_texts)):
            if vector is None:
                skipped += 1
                continue

            unique_id = f"{article['code']}_st{article['number']}_chunk{chunk_idx}"
            collection.upsert(
                ids=[unique_id],
                embeddings=[vector],
                documents=[embed_text],
                metadatas=[
                    {
                        "number": str(article["number"]),
                        "code": article["code"],
                        "short_code": short_code,
                        "chunk_index": chunk_idx,
                        "total_chunks": total,
                    }
                ],
            )

        if (i + 1) % 50 == 0:
            print(f"  Загружено: {i + 1}/{len(articles)} (пропущено чанков: {skipped})")

    print(f"Готово! Пропущено чанков: {skipped}")


def search(query, n_results=None, filter_codes: list[str] | None = None):
    """возвращает chroma-results"""
    if n_results is None:
        n_results = config.VECTOR_TOP_K
    query_vector = embeddings.get_embedding(query)
    if query_vector is None:
        print("search: пустой embedding запроса, возвращаю пусто")
        return {
            "documents": [[]],
            "metadatas": [[]],
            "distances": [[]],
            "ids": [[]],
        }
    kwargs = {"query_embeddings": [query_vector], "n_results": n_results}
    if filter_codes:
        kwargs["where"] = {"short_code": {"$in": filter_codes}}
    results = collection.query(**kwargs)
    return results
