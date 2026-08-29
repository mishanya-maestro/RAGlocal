"""
Запуск:
  run.bat
  .\\.venv\\Scripts\\python.exe src/flask_app.py

http://127.0.0.1:5000
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _reexec_with_project_venv_if_needed() -> None:
    """Если запустили системным Python без faster-whisper — перезапуск через .venv."""
    if os.environ.get("RAG_SKIP_VENV_REEXEC") == "1":
        return

    root = Path(__file__).resolve().parent.parent
    if sys.platform == "win32":
        venv_python = root / ".venv" / "Scripts" / "python.exe"
    else:
        venv_python = root / ".venv" / "bin" / "python"

    if not venv_python.is_file():
        return

    current = Path(sys.executable).resolve()
    target = venv_python.resolve()
    if current == target:
        return

    # Уже в этом venv (иногда executable отличается, смотрим prefix).
    try:
        if Path(sys.prefix).resolve() == (root / ".venv").resolve():
            return
    except OSError:
        pass

    try:
        import faster_whisper  # noqa: F401

        return
    except ImportError:
        pass

    print(
        f"faster-whisper нет в {current}\n"
        f"Перезапуск через venv: {target}"
    )
    os.environ["RAG_SKIP_VENV_REEXEC"] = "1"
    os.execv(str(target), [str(target), *sys.argv])


_reexec_with_project_venv_if_needed()

import re
import sqlite3
import tempfile
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

import autoselect
import config
import corpus_manager
import document_analysis as doc_analysis
import document_parser as doc_parser
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

# При старте: анализ железа + применение моделей, если они уже установлены.
_SETUP_BOOT = autoselect.bootstrap_runtime()
print(
    f"autoselect: tier={_SETUP_BOOT.get('tier')} "
    f"llm={(_SETUP_BOOT.get('models') or {}).get('llm')} "
    f"ready={_SETUP_BOOT.get('ready')} "
    f"missing={_SETUP_BOOT.get('missing')}"
)
print(f"python: {sys.executable}")
try:
    import faster_whisper as _fw  # noqa: F401

    print("faster-whisper: OK")
except ImportError:
    print(
        "faster-whisper: НЕ НАЙДЕН в этом Python. "
        "Запускайте через .venv:\\Scripts\\python.exe src\\flask_app.py "
        "или run.bat"
    )


def _metrics_cache_key(top_k: int, pool: int) -> tuple:
    golden_path = config.PROJECT_ROOT / "data" / "golden_eval.json"
    try:
        mtime = golden_path.stat().st_mtime
    except OSError:
        mtime = 0.0
    return (top_k, pool, mtime)


_WHISPER_CACHE: dict[str, object] = {}


def _voice_stt_ready() -> bool:
    asr = (getattr(config, "ASR_MODEL", "") or "").strip()
    if asr and autoselect._is_asr_model(asr) and autoselect._asr_installed(asr):
        return True
    return bool((config.ASSEMBLYAI_API_KEY or "").strip())


def _guess_audio_suffix(filename: str) -> str:
    lower = (filename or "").lower()
    for ext in (".wav", ".mp3", ".m4a", ".ogg", ".webm", ".mp4", ".mpeg", ".mpga"):
        if lower.endswith(ext):
            return ext
    return ".webm"


def _write_temp_bytes(data: bytes, suffix: str) -> str:
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(data)
        return tmp.name


def _convert_audio_to_wav16k(src_path: str) -> str:
    """Декодирует произвольное аудио в WAV 16 kHz mono. Возвращает путь к wav."""
    import wave

    import av
    import numpy as np

    wav_fd, wav_path = tempfile.mkstemp(suffix=".wav")
    os.close(wav_fd)
    try:
        container = av.open(src_path)
        try:
            stream = next((s for s in container.streams if s.type == "audio"), None)
            if stream is None:
                raise RuntimeError("В записи нет аудиодорожки")
            resampler = av.audio.resampler.AudioResampler(
                format="s16", layout="mono", rate=16000
            )
            chunks: list = []
            for frame in container.decode(stream):
                for out in resampler.resample(frame):
                    arr = out.to_ndarray()
                    if arr.ndim > 1:
                        arr = arr.reshape(-1)
                    chunks.append(np.asarray(arr, dtype=np.int16))
            for out in resampler.resample(None):
                arr = out.to_ndarray()
                if arr.ndim > 1:
                    arr = arr.reshape(-1)
                chunks.append(np.asarray(arr, dtype=np.int16))
        finally:
            container.close()

        if not chunks:
            raise RuntimeError("Пустая аудиодорожка после декодирования")

        pcm = np.concatenate(chunks)
        with wave.open(wav_path, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(16000)
            wf.writeframes(pcm.tobytes())
        return wav_path
    except Exception:
        try:
            os.unlink(wav_path)
        except OSError:
            pass
        raise


def _local_whisper_transcribe_bytes(audio_bytes: bytes, filename: str = "") -> str:
    """Локальный STT через faster-whisper."""
    asr = (getattr(config, "ASR_MODEL", "") or "").strip()
    if not asr or not autoselect._is_asr_model(asr):
        raise RuntimeError("Локальная Whisper-модель не выбрана")
    if not autoselect._asr_installed(asr):
        raise RuntimeError(
            f"Модель {asr} не установлена полностью (часто из‑за нехватки места на диске). "
            "В окне моделей скачайте whisper-base / whisper-small или освободите место "
            "и переустановите выбранную модель."
        )

    fw_name = autoselect._asr_fw_name(asr)
    try:
        from faster_whisper import WhisperModel
    except ImportError as e:
        raise RuntimeError(
            "Не установлен faster-whisper в интерпретаторе "
            f"{sys.executable}. Выполните:\n"
            f'  "{sys.executable}" -m pip install faster-whisper'
        ) from e

    hub_cache = autoselect._asr_download_root_for(asr)
    model = _WHISPER_CACHE.get(fw_name)
    if model is None:
        try:
            kwargs = {
                "device": "cpu",
                "compute_type": "int8",
                "local_files_only": True,
            }
            if hub_cache:
                kwargs["download_root"] = hub_cache
            model = WhisperModel(fw_name, **kwargs)
        except Exception as e:
            raise RuntimeError(
                f"Не удалось загрузить {asr} из локального кэша: {e}. "
                "Скачайте модель заново в окне установки."
            ) from e
        _WHISPER_CACHE[fw_name] = model

    suffix = _guess_audio_suffix(filename)
    src_path = _write_temp_bytes(audio_bytes, suffix)
    wav_path = ""
    audio_path = src_path
    try:
        try:
            wav_path = _convert_audio_to_wav16k(src_path)
            audio_path = wav_path
        except Exception:
            # Whisper/ctranslate2 иногда съедают исходный webm сами.
            audio_path = src_path

        try:
            segments, _info = model.transcribe(
                audio_path, language="ru", vad_filter=True
            )
        except Exception:
            segments, _info = model.transcribe(
                audio_path, language="ru", vad_filter=False
            )
        return " ".join((seg.text or "").strip() for seg in segments).strip()
    finally:
        for p in (src_path, wav_path):
            if p:
                try:
                    os.unlink(p)
                except OSError:
                    pass


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
        voice_stt_ready=_voice_stt_ready(),
        setup_ready=bool(_SETUP_BOOT.get("ready")),
        setup_tier=_SETUP_BOOT.get("tier") or "",
    )


@app.get("/analyze")
def analyze_page():
    return render_template(
        "analyze.html",
        mode=config.MODE,
        available_modes=["local", "api"],
        setup_ready=bool(_SETUP_BOOT.get("ready")),
        setup_tier=_SETUP_BOOT.get("tier") or "",
    )


@app.get("/corpora")
def corpora_page():
    return render_template(
        "corpora.html",
        mode=config.MODE,
        available_modes=["local", "api"],
        setup_ready=bool(_SETUP_BOOT.get("ready")),
        setup_tier=_SETUP_BOOT.get("tier") or "",
    )


@app.get("/api/setup/status")
def api_setup_status():
    try:
        selection = {
            "llm": (request.args.get("llm") or "").strip() or None,
            "asr": (request.args.get("asr") or "").strip() or None,
        }
        selection = {k: v for k, v in selection.items() if v}
        return jsonify(autoselect.get_setup_status(selection=selection or None))
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.post("/api/setup/install")
def api_setup_install():
    data = request.get_json(silent=True) or {}
    models = data.get("models")
    selection = data.get("selection")
    if models is not None and not isinstance(models, list):
        return jsonify({"error": "models должен быть списком"}), 400
    if selection is not None and not isinstance(selection, dict):
        return jsonify({"error": "selection должен быть объектом"}), 400
    try:
        # Одна модель по кнопке «скачать»
        if isinstance(models, list) and len(models) == 1 and not selection:
            result = autoselect.start_install(models=models)
        else:
            result = autoselect.start_install(models=models, selection=selection)
        status_code = 200 if result.get("ok") else 409
        return jsonify(result), status_code
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.get("/api/setup/install/status")
def api_setup_install_status():
    return jsonify(autoselect.install_status())


@app.post("/api/setup/apply")
def api_setup_apply():
    """Применить выбранные/рекомендуемые модели к runtime/.env."""
    data = request.get_json(silent=True) or {}
    selection = data.get("selection")
    if selection is not None and not isinstance(selection, dict):
        return jsonify({"error": "selection должен быть объектом"}), 400
    try:
        status = autoselect.get_setup_status(selection=selection)
        if status["missing"]:
            return jsonify(
                {
                    "error": "Сначала установите недостающие модели",
                    "missing": status["missing"],
                }
            ), 400
        applied = autoselect.apply_models_to_config(status["models"])
        autoselect.write_env(status["models"])
        return jsonify({"ok": True, "applied": applied})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


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
        active_corpus = corpus_manager.get_active_corpus()
        return jsonify(
            {
                "answer": answer,
                "sources": sources,
                "source_meta": source_meta,
                "mode": selected_mode,
                "active_corpus_id": active_corpus.get("id") if active_corpus else None,
                "active_corpus_name": active_corpus.get("name") if active_corpus else None,
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
    asr = (getattr(config, "ASR_MODEL", "") or "").strip()
    use_local = bool(
        asr and autoselect._is_asr_model(asr) and autoselect._asr_installed(asr)
    )
    use_cloud = bool((config.ASSEMBLYAI_API_KEY or "").strip())
    if not use_local and not use_cloud:
        return jsonify(
            {
                "error": (
                    "Установите Whisper в окне моделей или добавьте "
                    "ASSEMBLYAI_API_KEY в .env."
                )
            }
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
        if use_local:
            text = _local_whisper_transcribe_bytes(
                audio_bytes, filename=f.filename or ""
            )
        else:
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


@app.post("/api/analyze-document")
def api_analyze_document():
    """Загрузка PDF/DOCX/TXT и проверка документа на соответствие законам РБ."""
    if "document" not in request.files:
        return jsonify({"error": "Нет файла document"}), 400

    f = request.files["document"]
    if not f or not f.filename:
        return jsonify({"error": "Пустой файл"}), 400

    filename = f.filename or ""
    lower = filename.lower()
    allowed_ext = (".pdf", ".docx", ".txt", ".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tiff", ".tif")
    if not lower.endswith(allowed_ext):
        return jsonify({"error": "Поддерживаются PDF, DOCX, TXT и фото (PNG, JPG, WEBP и др.)"}), 400

    file_bytes = f.read()
    if not file_bytes:
        return jsonify({"error": "Пустой документ"}), 400

    if len(file_bytes) > 10 * 1024 * 1024:
        return jsonify({"error": "Файл больше 10 МБ"}), 400

    try:
        parsed = doc_parser.parse_uploaded_document(filename, file_bytes)
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": f"Не удалось извлечь текст: {e}"}), 500

    if not parsed.full_text.strip():
        return jsonify({"error": "Не удалось извлечь текст из документа"}), 400

    try:
        result = doc_analysis.analyze_document(parsed)
        return jsonify(
            {
                "ok": True,
                "filename": filename,
                "doc_type": parsed.doc_type,
                "doc_type_label": parsed.doc_type_label,
                "doc_type_confidence": parsed.doc_type_confidence,
                "result": result,
            }
        )
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.get("/api/corpora")
def api_list_corpora():
    """Возвращает список корпусов и активный корпус."""
    try:
        corpora = corpus_manager.list_corpora()
        active = corpus_manager.get_active_corpus()
        return jsonify({"corpora": corpora, "active_corpus_id": active.get("id") if active else None})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.post("/api/corpora")
def api_create_corpus():
    """Создаёт новый корпус."""
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    description = (data.get("description") or "").strip()
    if not name:
        return jsonify({"error": "Название корпуса обязательно"}), 400
    try:
        corpus = corpus_manager.create_corpus(name, description)
        return jsonify(corpus), 201
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.delete("/api/corpora/<corpus_id>")
def api_delete_corpus(corpus_id: str):
    """Удаляет корпус и его коллекцию."""
    try:
        if not corpus_manager.delete_corpus(corpus_id):
            return jsonify({"error": "Корпус не найден"}), 404
        return jsonify({"ok": True})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.get("/api/corpora/<corpus_id>/documents")
def api_list_documents(corpus_id: str):
    """Возвращает список документов корпуса."""
    corpus = corpus_manager.get_corpus(corpus_id)
    if not corpus:
        return jsonify({"error": "Корпус не найден"}), 404
    return jsonify({"documents": corpus.get("documents", [])})


@app.post("/api/corpora/<corpus_id>/documents")
def api_upload_document(corpus_id: str):
    """Загружает документы в корпус и индексирует их."""
    if "documents" not in request.files:
        return jsonify({"error": "Нет файлов documents"}), 400

    uploaded = request.files.getlist("documents")
    if not uploaded:
        return jsonify({"error": "Пустой список файлов"}), 400

    allowed_ext = (".pdf", ".docx", ".txt", ".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tiff", ".tif")
    added = []
    errors = []
    for f in uploaded:
        if not f or not f.filename:
            continue
        filename = f.filename
        lower = filename.lower()
        if not lower.endswith(allowed_ext):
            errors.append(f"{filename}: неподдерживаемый формат")
            continue
        file_bytes = f.read()
        if not file_bytes:
            errors.append(f"{filename}: пустой файл")
            continue
        if len(file_bytes) > 10 * 1024 * 1024:
            errors.append(f"{filename}: файл больше 10 МБ")
            continue
        try:
            doc = corpus_manager.store_document(corpus_id, filename, file_bytes)
            added.append({"doc_id": doc["doc_id"], "filename": doc["filename"]})
        except Exception as e:
            traceback.print_exc()
            errors.append(f"{filename}: {e}")

    return jsonify({"ok": True, "added": added, "errors": errors})


@app.delete("/api/corpora/<corpus_id>/documents/<doc_id>")
def api_delete_document(corpus_id: str, doc_id: str):
    """Удаляет документ из корпуса."""
    try:
        if not corpus_manager.delete_document(corpus_id, doc_id):
            return jsonify({"error": "Документ или корпус не найден"}), 404
        return jsonify({"ok": True})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.post("/api/corpora/<corpus_id>/set-active")
def api_set_active_corpus(corpus_id: str):
    """Устанавливает активный корпус для режима вопроса."""
    try:
        if not corpus_manager.set_active_corpus(corpus_id):
            return jsonify({"error": "Корпус не найден"}), 404
        return jsonify({"ok": True, "active_corpus_id": corpus_id})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.post("/api/corpora/clear-active")
def api_clear_active_corpus():
    """Сбрасывает активный корпус (возвращается системная база законов)."""
    try:
        corpus_manager.set_active_corpus(None)
        return jsonify({"ok": True, "active_corpus_id": None})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.get("/api/corpora/active")
def api_get_active_corpus():
    """Возвращает активный корпус."""
    try:
        active = corpus_manager.get_active_corpus()
        return jsonify({"active_corpus_id": active.get("id") if active else None, "corpus": active})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)
