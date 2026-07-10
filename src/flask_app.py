"""
py -3 src/flask_app.py
http://127.0.0.1:5000
"""

from __future__ import annotations

import re
import sqlite3
import sys
import time
import traceback

import requests
from flask import Flask, abort, jsonify, render_template, request

# На Windows stdout по умолчанию cp1252, и диагностические print() с кириллицей
# (например, в database.py) роняют запросы UnicodeEncodeError. Переключаем
# stdout/stderr в UTF-8 максимально совместимым способом.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import config
import eval_retrieval as metrics_mod
from generator import generate_answer, generate_direct_answer

app = Flask(
    __name__,
    template_folder="templates",
    static_folder="static",
)
app.config["MAX_CONTENT_LENGTH"] = 25 * 1024 * 1024

ASSEMBLYAI_BASE = "https://api.assemblyai.com"
ASSEMBLYAI_POLL_SEC = 3.0
ASSEMBLYAI_MAX_WAIT_SEC = 180.0

_METRICS_CACHE: dict = {}
_METRICS_CACHE_TTL_SEC = 600.0


def _metrics_cache_key(top_k: int, pool: int) -> tuple:
    golden_path = config.PROJECT_ROOT / "data" / "golden_eval.json"
    try:
        mtime = golden_path.stat().st_mtime
    except OSError:
        mtime = 0.0
    return (top_k, pool, mtime)


def _assemblyai_transcribe_bytes(audio_bytes: bytes) -> str:
    """Загружает аудио в AssemblyAI и возвращает текст транскрипта."""
    api_key = (config.ASSEMBLYAI_API_KEY or "").strip()
    if not api_key:
        raise RuntimeError("Не задан ASSEMBLYAI_API_KEY в окружении (.env)")

    headers = {"authorization": api_key}
    upload_resp = requests.post(
        f"{ASSEMBLYAI_BASE}/v2/upload",
        headers=headers,
        data=audio_bytes,
        timeout=120,
    )
    if not upload_resp.ok:
        raise RuntimeError(
            f"AssemblyAI upload: HTTP {upload_resp.status_code}: {upload_resp.text[:500]}"
        )
    upload_url = upload_resp.json().get("upload_url")
    if not upload_url:
        raise RuntimeError("AssemblyAI: нет upload_url в ответе")

    payload = {
        "audio_url": upload_url,
        "language_detection": True,
        "speech_models": ["universal-3-pro", "universal-2"],
    }
    tr_resp = requests.post(
        f"{ASSEMBLYAI_BASE}/v2/transcript",
        json=payload,
        headers=headers,
        timeout=60,
    )
    if not tr_resp.ok:
        raise RuntimeError(
            f"AssemblyAI transcript create: HTTP {tr_resp.status_code}: {tr_resp.text[:500]}"
        )
    transcript_id = tr_resp.json().get("id")
    if not transcript_id:
        raise RuntimeError("AssemblyAI: нет id транскрипта")

    poll_url = f"{ASSEMBLYAI_BASE}/v2/transcript/{transcript_id}"
    deadline = time.monotonic() + ASSEMBLYAI_MAX_WAIT_SEC
    while time.monotonic() < deadline:
        poll = requests.get(poll_url, headers=headers, timeout=30)
        if not poll.ok:
            raise RuntimeError(
                f"AssemblyAI poll: HTTP {poll.status_code}: {poll.text[:500]}"
            )
        body = poll.json()
        status = body.get("status")
        if status == "completed":
            return (body.get("text") or "").strip()
        if status == "error":
            err = body.get("error") or body
            raise RuntimeError(f"Транскрипция не удалась: {err}")
        time.sleep(ASSEMBLYAI_POLL_SEC)

    raise RuntimeError(
        "Превышено время ожидания транскрипции (попробуйте более короткую запись)"
    )


_SOURCE_RE = re.compile(r"^(?P<code>.+?),\s*ст\.\s*(?P<number>[\d_]+)$")


def _normalize_mode(value: str | None) -> str:
    mode = (value or config.MODE).strip().lower()
    if mode == "ollama":
        return "local"
    if mode in ("split", "openrouter"):
        return "api"
    if mode in ("local", "api"):
        return mode
    return config.MODE


def _parse_legacy_sources(sources: list[str]) -> list[dict]:
    out = []
    for source in sources:
        match = _SOURCE_RE.match(str(source).strip())
        if not match:
            out.append({"label": str(source), "code": "", "number": ""})
            continue
        out.append(
            {
                "label": str(source),
                "code": match.group("code"),
                "number": match.group("number"),
            }
        )
    return out


@app.get("/")
def index():
    return render_template(
        "index.html",
        mode=config.MODE,
        model=config.LLM_MODEL,
        available_modes=["local", "api"],
        voice_stt_ready=bool((config.ASSEMBLYAI_API_KEY or "").strip()),
    )


@app.get("/source")
def source_page():
    code = (request.args.get("code") or "").strip()
    number = (request.args.get("number") or "").strip()
    if not code or not number:
        abort(400, "Не указан кодекс или статья")

    con = sqlite3.connect(str(config.FULLTEXT_DB))
    try:
        row = con.execute(
            "SELECT text FROM fulltext WHERE code=? AND number=?",
            (code, number),
        ).fetchone()
    finally:
        con.close()

    if not row:
        abort(404, "Источник не найден")

    return render_template(
        "source.html",
        code=code,
        number=number,
        article_text=row[0],
    )


@app.post("/api/ask")
def api_ask():
    data = request.get_json(silent=True) or {}
    query = (data.get("query") or "").strip()
    selected_mode = _normalize_mode(data.get("mode"))
    if not query:
        return jsonify({"error": "Пустой запрос"}), 400
    try:
        answer, sources, source_meta = generate_answer(
            query,
            mode_override=selected_mode,
            include_source_meta=True,
        )
        if not source_meta:
            source_meta = _parse_legacy_sources(sources)
        return jsonify(
            {
                "answer": answer,
                "sources": sources,
                "source_meta": source_meta,
                "mode": selected_mode,
            }
        )
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.get("/api/metrics")
def api_metrics():
    """Топ-5 retrieval-метрик качества RAG по data/golden_eval.json.

    Query params:
    - detail=1   — включить per_case breakdown в ответ (по умолчанию выключено,
                   чтобы JSON был компактным).
    - refresh=1  — игнорировать кэш и пересчитать.

    Метрики (все без вызовов LLM):
    - recall_at_k:    gold ∈ финальный top-K
    - mrr_at_k:       средний обратный ранг первой gold-статьи
    - precision_at_k: |gold ∩ top-K| / K
    - ndcg_at_k:      нормированный DCG@K
    - recall_at_pool: gold ∈ pool до реранкера (диагностический потолок)
    """
    detail = request.args.get("detail") in ("1", "true", "True", "yes")
    refresh = request.args.get("refresh") in ("1", "true", "True", "yes")
    top_k = config.RETRIEVER_TOP_K
    pool = config.RERANK_POOL_SIZE

    cache_key = _metrics_cache_key(top_k, pool)
    now = time.monotonic()
    cached = _METRICS_CACHE.get(cache_key)
    if cached and not refresh and (now - cached["ts"]) < _METRICS_CACHE_TTL_SEC:
        result = cached["result"]
        elapsed_ms = cached["elapsed_ms"]
        from_cache = True
    else:
        started = time.monotonic()
        try:
            result = metrics_mod.compute_metrics(top_k=top_k, pool=pool)
        except Exception as e:
            traceback.print_exc()
            return jsonify({"error": str(e)}), 500
        elapsed_ms = int((time.monotonic() - started) * 1000)
        _METRICS_CACHE[cache_key] = {
            "result": result,
            "elapsed_ms": elapsed_ms,
            "ts": now,
        }
        from_cache = False

    payload = {
        "n": result.get("n", 0),
        "k": result.get("k", top_k),
        "pool": result.get("pool", pool),
        "metrics": result.get("metrics", {}),
        "config": result.get("config", {}),
        "elapsed_ms": elapsed_ms,
        "from_cache": from_cache,
    }
    if "error" in result:
        payload["error"] = result["error"]
    if detail:
        payload["per_case"] = result.get("per_case", [])
    return jsonify(payload)


@app.post("/api/transcribe")
def api_transcribe():
    """Speech-to-text: multipart поле `audio` (webm, mp3, m4a и т.д.)."""
    if not (config.ASSEMBLYAI_API_KEY or "").strip():
        return jsonify(
            {"error": "Добавьте ASSEMBLYAI_API_KEY в .env для распознавания речи."}
        ), 503

    if "audio" not in request.files:
        return jsonify({"error": "Нет файла audio"}), 400

    f = request.files["audio"]
    if not f or not f.filename:
        return jsonify({"error": "Пустой файл"}), 400

    audio_bytes = f.read()
    if not audio_bytes:
        return jsonify({"error": "Пустое аудио"}), 400

    try:
        text = _assemblyai_transcribe_bytes(audio_bytes)
        return jsonify({"text": text})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.post("/api/compare")
def api_compare():
    data = request.get_json(silent=True) or {}
    query = (data.get("query") or "").strip()
    selected_mode = _normalize_mode(data.get("mode"))
    if not query:
        return jsonify({"error": "Пустой запрос"}), 400

    rag_answer = ""
    sources = []
    direct_answer = ""
    rag_error = None
    direct_error = None

    try:
        rag_answer, sources, source_meta = generate_answer(
            query,
            mode_override=selected_mode,
            include_source_meta=True,
        )
        if not source_meta:
            source_meta = _parse_legacy_sources(sources)
    except Exception as e:
        rag_error = str(e)
        source_meta = []

    try:
        direct_answer = generate_direct_answer(query, mode_override=selected_mode)
    except Exception as e:
        direct_error = str(e)

    return jsonify(
        {
            "rag_answer": rag_answer,
            "sources": sources,
            "source_meta": source_meta,
            "direct_answer": direct_answer,
            "rag_error": rag_error,
            "direct_error": direct_error,
            "mode": selected_mode,
        }
    )


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)
