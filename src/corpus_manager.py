"""Управление пользовательскими корпусами документов для RAG."""

from __future__ import annotations

import json
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any

import config
import database
import document_parser

CORPORA_FILE = Path(getattr(config, "CORPORA_FILE", "corpora.json"))

_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE)


def _is_valid_uuid(value: str) -> bool:
    return bool(value and _UUID_RE.match(value))


def _load_state() -> dict[str, Any]:
    if not CORPORA_FILE.exists():
        return {"corpora": [], "active_corpus_id": None}
    try:
        with open(CORPORA_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"corpora": [], "active_corpus_id": None}


def _save_state(state: dict[str, Any]) -> None:
    with open(CORPORA_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def _collection_name_for(corpus_id: str) -> str:
    return database._sanitize_collection_name(corpus_id)


def list_corpora() -> list[dict[str, Any]]:
    """Возвращает список корпусов с метаданными."""
    return _load_state().get("corpora", [])


def get_corpus(corpus_id: str) -> dict[str, Any] | None:
    for c in list_corpora():
        if c["id"] == corpus_id:
            return c
    return None


def create_corpus(name: str, description: str = "") -> dict[str, Any]:
    """Создаёт новый пустой корпус."""
    if not name or not name.strip():
        raise ValueError("Название корпуса не может быть пустым")

    corpus_id = str(uuid.uuid4())
    corpus = {
        "id": corpus_id,
        "name": name.strip(),
        "description": description.strip(),
        "collection_name": _collection_name_for(corpus_id),
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "documents": [],
        "indexed_count": 0,
    }
    state = _load_state()
    state["corpora"].append(corpus)
    _save_state(state)
    return corpus


def delete_corpus(corpus_id: str) -> bool:
    """Удаляет корпус и его коллекцию."""
    state = _load_state()
    corpus = next((c for c in state["corpora"] if c["id"] == corpus_id), None)
    if not corpus:
        return False

    state["corpora"] = [c for c in state["corpora"] if c["id"] != corpus_id]
    if state.get("active_corpus_id") == corpus_id:
        state["active_corpus_id"] = None
    _save_state(state)

    try:
        database.delete_collection(corpus["collection_name"])
    except Exception:
        pass

    # Удаляем хранилище файлов корпуса.
    try:
        import shutil

        storage_dir = _corpus_storage_dir(corpus_id)
        shutil.rmtree(storage_dir, ignore_errors=True)
    except Exception:
        pass
    return True


def set_active_corpus(corpus_id: str | None) -> bool:
    """Устанавливает активный корпус для режима вопроса."""
    state = _load_state()
    if corpus_id is not None and not get_corpus(corpus_id):
        return False
    state["active_corpus_id"] = corpus_id
    _save_state(state)
    return True


def get_active_corpus_id() -> str | None:
    return _load_state().get("active_corpus_id")


def get_active_corpus() -> dict[str, Any] | None:
    cid = get_active_corpus_id()
    if not cid:
        return None
    return get_corpus(cid)


def add_document(corpus_id: str, filename: str, file_bytes: bytes) -> dict[str, Any]:
    """Добавляет документ в корпус и возвращает метаданные документа."""
    corpus = get_corpus(corpus_id)
    if not corpus:
        raise ValueError(f"Корпус {corpus_id} не найден")

    segments = document_parser.extract_text(filename, file_bytes)
    full_text = "\n\n".join(s.text for s in segments)
    if not full_text.strip():
        raise ValueError("Не удалось извлечь текст из документа")

    doc_id = str(uuid.uuid4())
    doc = {
        "doc_id": doc_id,
        "filename": filename,
        "added_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "char_count": len(full_text),
        "chunk_count": 0,
    }

    state = _load_state()
    for c in state["corpora"]:
        if c["id"] == corpus_id:
            c["documents"].append(doc)
            break
    _save_state(state)

    # Переиндексируем весь корпус, чтобы чанки были консистентны.
    reindex_corpus(corpus_id)
    return doc


def delete_document(corpus_id: str, doc_id: str) -> bool:
    """Удаляет документ из корпуса, файлы на диске и пересоздаёт индекс."""
    if not _is_valid_uuid(doc_id):
        return False
    corpus = get_corpus(corpus_id)
    if not corpus:
        return False

    state = _load_state()
    for c in state["corpora"]:
        if c["id"] == corpus_id:
            before = len(c["documents"])
            c["documents"] = [d for d in c["documents"] if d["doc_id"] != doc_id]
            if len(c["documents"]) == before:
                return False
            break
    _save_state(state)

    # Удаляем файлы документа.
    storage_dir = _corpus_storage_dir(corpus_id)
    doc_path = storage_dir / doc_id
    for ext in ("", ".txt"):
        try:
            (doc_path.with_suffix(ext) if ext else doc_path).unlink(missing_ok=True)
        except OSError:
            pass

    reindex_corpus(corpus_id)
    return True


def reindex_corpus(corpus_id: str) -> dict[str, Any]:
    """Пересоздаёт индекс корпуса на основе сохранённых документов."""
    corpus = get_corpus(corpus_id)
    if not corpus:
        raise ValueError(f"Корпус {corpus_id} не найден")

    # Собираем текст из файлов на диске (используем .txt, а не исходный бинарник).
    docs_for_index: list[dict[str, Any]] = []
    storage_dir = _corpus_storage_dir(corpus_id)
    for doc in corpus.get("documents", []):
        text_path = (storage_dir / doc["doc_id"]).with_suffix(".txt")
        if not text_path.exists():
            continue
        try:
            text = text_path.read_text(encoding="utf-8")
            if text.strip():
                docs_for_index.append(
                    {
                        "doc_id": doc["doc_id"],
                        "filename": doc["filename"],
                        "text": text,
                    }
                )
        except Exception:
            continue

    # Пересоздаём коллекцию и индексируем.
    col_name = corpus["collection_name"]
    try:
        database.delete_collection(col_name)
    except Exception:
        pass

    if docs_for_index:
        database.add_corpus_chunks(corpus_id, docs_for_index, name=col_name)
    else:
        database.get_collection(col_name)

    # Обновляем счётчики.
    chunk_count = database.collection_count(col_name)
    state = _load_state()
    for c in state["corpora"]:
        if c["id"] == corpus_id:
            for doc in c["documents"]:
                doc["chunk_count"] = 0
            c["indexed_count"] = chunk_count
            break
    _save_state(state)

    return {"corpus_id": corpus_id, "indexed_count": chunk_count}


def _corpus_storage_dir(corpus_id: str) -> Path:
    """Возвращает папку для хранения оригиналов документов корпуса."""
    base = Path(getattr(config, "CORPORA_STORAGE_DIR", "corpora_storage"))
    d = base / corpus_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def store_document(corpus_id: str, filename: str, file_bytes: bytes) -> dict[str, Any]:
    """Сохраняет файл на диск, добавляет метаданные и переиндексирует корпус."""
    corpus = get_corpus(corpus_id)
    if not corpus:
        raise ValueError(f"Корпус {corpus_id} не найден")

    segments = document_parser.extract_text(filename, file_bytes)
    full_text = "\n\n".join(s.text for s in segments)
    if not full_text.strip():
        raise ValueError("Не удалось извлечь текст из документа")

    # Безопасное имя файла: только имя, без путей.
    safe_filename = os.path.basename(filename).strip() or "document"

    doc_id = str(uuid.uuid4())
    doc_path = _corpus_storage_dir(corpus_id) / doc_id
    doc_path.write_bytes(file_bytes)
    text_path = doc_path.with_suffix(".txt")
    text_path.write_text(full_text, encoding="utf-8")

    doc = {
        "doc_id": doc_id,
        "filename": safe_filename,
        "added_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "char_count": len(full_text),
        "chunk_count": 0,
    }

    state = _load_state()
    for c in state["corpora"]:
        if c["id"] == corpus_id:
            c["documents"].append(doc)
            break
    _save_state(state)

    reindex_corpus(corpus_id)
    return doc
