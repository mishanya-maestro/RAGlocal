import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

import config
import database
import fts_index
import reranker
from formalizer import formalize_query

_fts_initialized = False


def ensure_fts_ready():
    global _fts_initialized
    if _fts_initialized:
        return
    try:
        fts_index.ensure_fts_index()
    except Exception as e:
        print("FTS init warning:", e)
    _fts_initialized = True


def _article_key(meta):
    return (str(meta["code"]), str(meta["number"]))


def _rrf_scores(vector_metas, fts_rows, k, vector_weight=1.0, fts_weight=1.0):
    scores: dict[tuple[str, str], float] = defaultdict(float)
    contrib: dict[tuple[str, str], dict[str, Any]] = defaultdict(
        lambda: {"vector_rank": None, "fts_rank": None}
    )
    for rank, meta in enumerate(vector_metas):
        key = _article_key(meta)
        scores[key] += vector_weight / (k + rank + 1)
        if contrib[key]["vector_rank"] is None:
            contrib[key]["vector_rank"] = rank
    for rank, row in enumerate(fts_rows):
        key = (str(row["code"]), str(row["number"]))
        scores[key] += fts_weight / (k + rank + 1)
        if contrib[key]["fts_rank"] is None:
            contrib[key]["fts_rank"] = rank
    return scores, contrib


def _best_vector_chunks(metas, docs, dists):
    out: dict[tuple[str, str], tuple[str, float]] = {}
    for doc, meta, dist in zip(docs, metas, dists):
        key = _article_key(meta)
        if key not in out or dist < out[key][1]:
            out[key] = (doc, dist)
    return out


def _full_text(con, code, number):
    row = con.execute(
        "SELECT text FROM fulltext WHERE code=? AND number=?",
        (code, number),
    ).fetchone()
    return row[0] if row else None


def rrf_ranked_article_keys(query, pool=None):
    """RRF-ранжированные ключи статей без LLM. Используется в eval."""
    if pool is None:
        pool = config.RERANK_POOL_SIZE
    ensure_fts_ready()
    search_query = formalize_query(query) or query
    results = database.search(search_query, config.VECTOR_TOP_K) or {}
    metas = (results.get("metadatas") or [[]])[0]
    fts_rows = fts_index.fts_search(search_query, config.FTS_TOP_K)
    scores, _ = _rrf_scores(
        metas,
        fts_rows,
        config.RRF_K,
        getattr(config, "VECTOR_WEIGHT", 1.0),
        getattr(config, "FTS_WEIGHT", 1.0),
    )
    if not scores:
        return []
    return sorted(scores.keys(), key=lambda x: scores[x], reverse=True)[:pool]


@dataclass
class RetrievalDebug:
    query: str = ""
    search_query: str = ""
    vector_keys: list = field(default_factory=list)
    fts_keys: list = field(default_factory=list)
    rrf_ranked: list = field(default_factory=list)
    rerank_input: list = field(default_factory=list)
    rerank_output: list = field(default_factory=list)
    rerank_status: str = "skipped"
    final_keys: list = field(default_factory=list)


def select_best(results, top_k=None):
    """Старый путь: только Chroma → top-K статей по лучшему чанку. Сохраняем для совместимости."""
    if top_k is None:
        top_k = config.RETRIEVER_TOP_K
    con = sqlite3.connect(str(config.FULLTEXT_DB))
    try:
        docs = (results.get("documents") or [[]])[0]
        metas = (results.get("metadatas") or [[]])[0]
        dists = (results.get("distances") or [[]])[0]
        article_best: dict[tuple[str, str], tuple[str, dict, float]] = {}
        for doc, meta, dist in zip(docs, metas, dists):
            key = _article_key(meta)
            if key not in article_best or dist < article_best[key][2]:
                article_best[key] = (doc, meta, dist)
        sorted_a = sorted(article_best.values(), key=lambda x: x[2])[:top_k]
        rows = []
        sel_meta = []
        for _, meta, _d in sorted_a:
            row = con.execute(
                "SELECT text FROM fulltext WHERE code=? AND number=?",
                (meta["code"], str(meta["number"])),
            ).fetchone()
            if row:
                rows.append(row)
                sel_meta.append({"code": meta["code"], "number": str(meta["number"])})
        return rows, sel_meta
    finally:
        con.close()


def retrieve_context(
    query,
    top_k=None,
    pool=None,
    use_rerank=None,
    return_debug: bool = False,
    filter_codes: list[str] | None = None,
):
    """Полный pipeline. Возвращает (context_rows, metadata) или (..., RetrievalDebug)."""
    if top_k is None:
        top_k = config.RETRIEVER_TOP_K
    if pool is None:
        pool = config.RERANK_POOL_SIZE
    if use_rerank is None:
        use_rerank = getattr(config, "USE_RERANK", True)

    ensure_fts_ready()

    # Переформулировка только для поиска; исходный query остаётся для LLM выше по стеку.
    search_query = formalize_query(query) or query

    results = database.search(search_query, config.VECTOR_TOP_K, filter_codes=filter_codes) or {}
    docs = (results.get("documents") or [[]])[0]
    metas = (results.get("metadatas") or [[]])[0]
    dists = (results.get("distances") or [[]])[0]

    fts_rows = fts_index.fts_search(search_query, config.FTS_TOP_K, filter_codes=filter_codes)

    scores, _ = _rrf_scores(
        metas,
        fts_rows,
        config.RRF_K,
        getattr(config, "VECTOR_WEIGHT", 1.0),
        getattr(config, "FTS_WEIGHT", 1.0),
    )

    snippets = {(str(r["code"]), str(r["number"])): r.get("snippet") or "" for r in fts_rows}
    best_chunks = _best_vector_chunks(metas, docs, dists)

    rrf_ranked = sorted(scores.keys(), key=lambda x: scores[x], reverse=True)[:pool]

    debug = RetrievalDebug(
        query=query,
        search_query=search_query,
        vector_keys=[_article_key(m) for m in metas][:20],
        fts_keys=[(str(r["code"]), str(r["number"])) for r in fts_rows][:20],
        rrf_ranked=list(rrf_ranked),
    )

    if not rrf_ranked:
        # Холодный кейс: ни вектор, ни FTS ничего не дали. Без LLM-реранка возвращаем пусто.
        if return_debug:
            return [], [], debug
        return [], []

    con = sqlite3.connect(str(config.FULLTEXT_DB))
    try:
        previews: list[str] = []
        cand_metas: list[dict] = []
        for key in rrf_ranked:
            cand_metas.append({"code": key[0], "number": key[1]})
            parts: list[str] = []
            if key in best_chunks:
                parts.append(best_chunks[key][0])
            if key in snippets and snippets[key]:
                parts.append(f"FTS: {snippets[key]}")
            if not parts:
                full = _full_text(con, key[0], key[1])
                if full:
                    parts.append(full[:1200])
            preview = " | ".join(parts).replace("\n", " ")[:1500]
            if not preview.strip():
                preview = f"{key[0]}, ст. {key[1]}"
            previews.append(preview)

        debug.rerank_input = list(rrf_ranked)

        if use_rerank:
            _, sel_meta_re, status = reranker.rerank(
                query, previews, cand_metas, top_k=config.RERANKER_TOP_K
            )
            debug.rerank_status = status
            if status != "ok" or not sel_meta_re:
                sel_meta = cand_metas[:top_k]
            else:
                sel_meta = sel_meta_re[:top_k]
        else:
            sel_meta = cand_metas[:top_k]
            debug.rerank_status = "disabled"

        debug.rerank_output = [(m["code"], m["number"]) for m in sel_meta]

        context_rows: list = []
        final_meta: list[dict] = []
        for m in sel_meta:
            row = con.execute(
                "SELECT text FROM fulltext WHERE code=? AND number=?",
                (m["code"], m["number"]),
            ).fetchone()
            if row:
                context_rows.append(row)
                final_meta.append(m)
            else:
                print(
                    f"retrieval: статья {m['code']} ст. {m['number']} не найдена в fulltext.db"
                )
        debug.final_keys = [(m["code"], m["number"]) for m in final_meta]

        if return_debug:
            return context_rows, final_meta, debug
        return context_rows, final_meta
    finally:
        con.close()
