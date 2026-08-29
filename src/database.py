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


def _get_default_collection_name() -> str:
    return getattr(config, "COLLECTION_NAME", "belarus_laws")


# Default collection for backward compatibility with the original legal corpus.
_collection_name = _get_default_collection_name()
collection = client.get_or_create_collection(name=_collection_name)


def _sanitize_collection_name(name: str) -> str:
    """Приводит имя коллекции к допустимому формату ChromaDB."""
    name = name.lower().strip()
    name = re.sub(r"[^a-z0-9_\-]+", "_", name)
    name = re.sub(r"_+", "_", name).strip("_")
    if not name or name[0].isdigit():
        name = "corpus_" + name
    if len(name) > 60:
        name = name[:60]
    return name


def get_collection(name: str | None = None):
    """Возвращает коллекцию ChromaDB по имени. None — коллекция по умолчанию."""
    name = name or _get_default_collection_name()
    return client.get_or_create_collection(name=name)


def list_collections() -> list[str]:
    """Возвращает список имён коллекций в ChromaDB."""
    try:
        return [c.name for c in client.list_collections()]
    except Exception:
        return []


def delete_collection(name: str) -> None:
    """Удаляет коллекцию ChromaDB."""
    try:
        client.delete_collection(name=name)
    except Exception:
        pass


def collection_count(name: str | None = None) -> int:
    """Возвращает количество записей в коллекции."""
    try:
        return get_collection(name).count()
    except Exception:
        return 0


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


def _add_article_chunks(col, article, skipped: list[int]) -> None:
    """Добавляет чанки одной статьи в коллекцию."""
    chunks = chunk_text(article["text"])
    total = len(chunks)
    short_code = article.get("short_code", "")

    embed_texts = [
        _embed_text(article["code"], article["number"], chunk_idx, total, body)
        for chunk_idx, body in enumerate(chunks)
    ]
    vectors = embeddings.get_embeddings(embed_texts, batch_size=64)

    for chunk_idx, (body, vector, embed_text) in enumerate(zip(chunks, vectors, embed_texts)):
        if vector is None:
            skipped[0] += 1
            continue

        unique_id = f"{article['code']}_st{article['number']}_chunk{chunk_idx}"
        col.upsert(
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


def add_articles(articles, reset_collection: bool = False):
    """Индексирует статьи в коллекцию по умолчанию (backward compatibility)."""
    global collection
    name = _get_default_collection_name()
    if reset_collection:
        try:
            client.delete_collection(name)
        except Exception as e:
            print(f"  delete_collection warning: {e}")
        collection = client.get_or_create_collection(name=name)

    add_articles_to_collection(articles, name=name)


def add_articles_to_collection(articles, name: str, reset_collection: bool = False):
    """Индексирует статьи в указанную коллекцию."""
    col = get_collection(name)
    if reset_collection:
        try:
            client.delete_collection(name)
        except Exception as e:
            print(f"  delete_collection warning: {e}")
        col = client.get_or_create_collection(name=name)

    print(f"Начинаем индексацию {len(articles)} статей в коллекцию '{name}'...")
    skipped = [0]
    for i, article in enumerate(articles):
        _add_article_chunks(col, article, skipped)
        if (i + 1) % 50 == 0:
            print(f"  Загружено: {i + 1}/{len(articles)} (пропущено чанков: {skipped[0]})")

    print(f"Готово! Пропущено чанков: {skipped[0]}")


def add_corpus_chunks(corpus_id: str, docs: list[dict], name: str | None = None):
    """Индексирует документы пользовательского корпуса в коллекцию.

    docs: список словарей с ключами 'doc_id', 'filename', 'text'.
    """
    col_name = name or _sanitize_collection_name(corpus_id)
    col = get_collection(col_name)

    print(f"Начинаем индексацию {len(docs)} документов в корпус '{col_name}'...")
    skipped = [0]
    for doc in docs:
        chunks = chunk_text(doc["text"])
        total = len(chunks)
        doc_id = doc.get("doc_id", "")
        filename = doc.get("filename", "")

        embed_texts = [
            f"Документ: {filename}\nЧасть: {chunk_idx + 1}/{total}\nТекст: {body}"
            for chunk_idx, body in enumerate(chunks)
        ]
        vectors = embeddings.get_embeddings(embed_texts, batch_size=64)

        for chunk_idx, (body, vector, embed_text) in enumerate(zip(chunks, vectors, embed_texts)):
            if vector is None:
                skipped[0] += 1
                continue
            unique_id = f"corpus_{corpus_id}_doc_{doc_id}_chunk_{chunk_idx}"
            col.upsert(
                ids=[unique_id],
                embeddings=[vector],
                documents=[embed_text],
                metadatas=[
                    {
                        "doc_id": doc_id,
                        "filename": filename,
                        "corpus_id": corpus_id,
                        "chunk_index": chunk_idx,
                        "total_chunks": total,
                        "source_type": "corpus",
                    }
                ],
            )

    print(f"Готово! Пропущено чанков: {skipped[0]}")
    return col_name


def search(query, n_results=None, filter_codes: list[str] | None = None):
    """Поиск в коллекции по умолчанию (backward compatibility)."""
    return search_collection(query, _get_default_collection_name(), n_results, filter_codes)


def search_collection(
    query,
    name: str | None = None,
    n_results=None,
    filter_codes: list[str] | None = None,
):
    """Поиск в указанной коллекции."""
    col = get_collection(name)
    if n_results is None:
        n_results = config.VECTOR_TOP_K
    query_vector = embeddings.get_embedding(query)
    if query_vector is None:
        print("search_collection: пустой embedding запроса, возвращаю пусто")
        return {
            "documents": [[]],
            "metadatas": [[]],
            "distances": [[]],
            "ids": [[]],
        }
    kwargs = {"query_embeddings": [query_vector], "n_results": n_results}
    if filter_codes:
        kwargs["where"] = {"short_code": {"$in": filter_codes}}
    results = col.query(**kwargs)
    return results
