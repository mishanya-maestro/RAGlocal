"""Автоподбор локальных моделей Ollama по железу ПК.

Embedding и cross-encoder фиксированы (одна векторная БД).
Подбираются LLM, STT, температура и max_tokens.
"""

from __future__ import annotations

import json
import platform
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

import requests

import config

try:
    import psutil
except ImportError:  # pragma: no cover
    psutil = None


# --- Каталог моделей ---

LLM_OPTIONS = [
    {
        "id": "qwen3.5:0.8b",
        "label": "Qwen3.5 0.8B",
        "size_gb": 0.6,
        "min_tier": "minimal",
        "weight": 1,
    },
    {
        "id": "qwen3.5:2b",
        "label": "Qwen3.5 2B",
        "size_gb": 1.5,
        "min_tier": "low",
        "weight": 2,
    },
    {
        "id": "qwen3.5:4b",
        "label": "Qwen3.5 4B",
        "size_gb": 2.5,
        "min_tier": "medium",
        "weight": 3,
    },
    {
        "id": "qwen3.5:9b",
        "label": "Qwen3.5 9B",
        "size_gb": 5.5,
        "min_tier": "high",
        "weight": 4,
    },
    {
        "id": "qwen3.5:27b",
        "label": "Qwen3.5 27B",
        "size_gb": 16.0,
        "min_tier": "ultra",
        "weight": 5,
    },
    {
        "id": "qwen3.5:35b",
        "label": "Qwen3.5 35B",
        "size_gb": 20.0,
        "min_tier": "ultra",
        "weight": 6,
    },
    {
        "id": "qwen3.5:122b",
        "label": "Qwen3.5 122B",
        "size_gb": 70.0,
        "min_tier": "ultra",
        "weight": 7,
    },
]

ASR_OPTIONS = [
    {
        "id": "whisper-base",
        "label": "Whisper Base",
        "size_gb": 0.15,
        "min_tier": "minimal",
        "weight": 1,
    },
    {
        "id": "whisper-small",
        "label": "Whisper Small",
        "size_gb": 0.5,
        "min_tier": "low",
        "weight": 2,
    },
    {
        "id": "whisper-medium",
        "label": "Whisper Medium",
        "size_gb": 1.5,
        "min_tier": "medium",
        "weight": 3,
    },
    {
        "id": "whisper-large-v3",
        "label": "Whisper Large v3",
        "size_gb": 3.0,
        "min_tier": "high",
        "weight": 4,
    },
]

FIXED_MODELS = {
    "embedding": {
        "id": "qwen3-embedding:4b",
        "label": "Qwen3 Embedding 4B",
        "size_gb": 2.5,
        "role": "embedding",
    },
    "reranker": {
        "id": "awenleven/Qwen3-Reranker-4B:Q4_K_M",
        "label": "Qwen3 Reranker 4B",
        "size_gb": 2.5,
        "role": "reranker",
    },
}

TIER_ORDER = ["minimal", "low", "medium", "high", "ultra"]

TIER_CONFIGS = {
    "ultra": {
        "llm": "qwen3.5:27b",
        "asr": "whisper-large-v3",
        "max_tokens": 2500,
        "temperature": 0.1,
    },
    "high": {
        "llm": "qwen3.5:9b",
        "asr": "whisper-large-v3",
        "max_tokens": 2000,
        "temperature": 0.1,
    },
    "medium": {
        "llm": "qwen3.5:4b",
        "asr": "whisper-medium",
        "max_tokens": 1200,
        "temperature": 0.1,
    },
    "low": {
        "llm": "qwen3.5:2b",
        "asr": "whisper-small",
        "max_tokens": 800,
        "temperature": 0.15,
    },
    "minimal": {
        "llm": "qwen3.5:0.8b",
        "asr": "whisper-base",
        "max_tokens": 500,
        "temperature": 0.2,
    },
}

TIER_RAM = {
    "ultra": 36.0,
    "high": 24.0,
    "medium": 15.5,
    "low": 9.5,
    "minimal": 5.0,
}

TIER_META = {
    "ultra": {
        "title": "ULTRA",
        "subtitle": "Топовое железо — можно брать крупные модели",
        "tone": "ultra",
    },
    "high": {
        "title": "HIGH",
        "subtitle": "Мощный ПК — комфортно для больших локальных моделей",
        "tone": "high",
    },
    "medium": {
        "title": "MEDIUM",
        "subtitle": "Сбалансированный уровень — лучший компромисс скорость/качество",
        "tone": "medium",
    },
    "low": {
        "title": "LOW",
        "subtitle": "Скромные ресурсы — лучше лёгкие модели",
        "tone": "low",
    },
    "minimal": {
        "title": "MINIMAL",
        "subtitle": "Очень слабое железо — только самые лёгкие модели",
        "tone": "minimal",
    },
}

_INSTALL_LOCK = threading.Lock()
_INSTALL_STATE: dict[str, Any] = {
    "running": False,
    "models": [],
    "current": None,
    "done": [],
    "failed": [],
    "log": [],
    "progress": {},
    "error": None,
    "finished": False,
    "selection": None,
}


def _ollama_base() -> str:
    base = (config.OLLAMA_BASE_URL or "http://localhost:11434/v1").rstrip("/")
    if base.endswith("/v1"):
        base = base[:-3]
    return base


def get_hardware() -> dict[str, Any]:
    """Собирает информацию о железе и считает баллы."""
    info: dict[str, Any] = {
        "cpu_cores": 4,
        "cpu_freq_mhz": 0.0,
        "ram_gb": 8.0,
        "vram_gb": 0.0,
        "gpu": None,
        "os": platform.system(),
        "score": 0.0,
    }

    if psutil is not None:
        try:
            info["cpu_cores"] = psutil.cpu_count(logical=True) or 4
        except Exception:
            pass
        try:
            freq = psutil.cpu_freq()
            if freq:
                info["cpu_freq_mhz"] = float(freq.max or freq.current or 0)
        except Exception:
            pass
        try:
            ram = psutil.virtual_memory()
            info["ram_gb"] = round(ram.total / (1024**3), 1)
        except Exception:
            pass
    else:
        try:
            import os

            info["cpu_cores"] = os.cpu_count() or 4
        except Exception:
            pass

    try:
        out = subprocess.check_output(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.total",
                "--format=csv,noheader,nounits",
            ],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
        gpus = []
        vram_total = 0.0
        for line in out.strip().split("\n"):
            if "," not in line:
                continue
            parts = line.split(",")
            name = parts[0].strip()
            mem_gb = float(parts[1].strip()) / 1024.0
            gpus.append(f"{name} ({mem_gb:.1f} GB)")
            vram_total += mem_gb
        info["gpu"] = ", ".join(gpus) if gpus else None
        info["vram_gb"] = round(vram_total, 1)
    except Exception:
        info["gpu"] = None
        info["vram_gb"] = 0.0

    info["score"] = round(
        info["ram_gb"] * 15
        + info["vram_gb"] * 50
        + info["cpu_cores"] * 10
        + info["cpu_freq_mhz"] * 0.02,
        1,
    )
    return info


def get_tier(score: float) -> str:
    if score >= 1200:
        return "ultra"
    if score >= 700:
        return "high"
    if score >= 400:
        return "medium"
    if score >= 200:
        return "low"
    return "minimal"


def select_models(tier: str | None = None) -> dict[str, Any]:
    """Полный набор: фиксированные embedding/reranker + LLM/STT/температура по tier."""
    if tier is None:
        tier = get_tier(get_hardware()["score"])
    cfg = TIER_CONFIGS.get(tier, TIER_CONFIGS["medium"]).copy()
    cfg["embedding"] = FIXED_MODELS["embedding"]["id"]
    cfg["reranker"] = FIXED_MODELS["reranker"]["id"]
    cfg["tier"] = tier
    return cfg


def _find_option(options: list[dict[str, Any]], model_id: str) -> dict[str, Any] | None:
    target = (model_id or "").strip().lower()
    for item in options:
        if item["id"].lower() == target:
            return item
    return None


def _tier_index(tier: str) -> int:
    try:
        return TIER_ORDER.index(tier)
    except ValueError:
        return 2


def fit_badge(option: dict[str, Any], pc_tier: str, recommended_id: str) -> dict[str, str]:
    """Бейдж соответствия модели мощности ПК."""
    if option["id"] == recommended_id:
        return {
            "code": "recommended",
            "label": "Рекомендуемо",
            "tone": "good",
        }

    opt_tier_idx = _tier_index(option.get("min_tier", "medium"))
    pc_idx = _tier_index(pc_tier)
    rec = _find_option(
        LLM_OPTIONS if option.get("role") == "llm" else ASR_OPTIONS,
        recommended_id,
    )
    rec_weight = rec["weight"] if rec else opt_tier_idx
    opt_weight = option.get("weight", opt_tier_idx)

    if opt_weight > rec_weight or opt_tier_idx > pc_idx:
        return {
            "code": "too_heavy",
            "label": "Слишком мощная для ПК",
            "tone": "bad",
        }
    if opt_weight < rec_weight:
        return {
            "code": "weaker",
            "label": "Слабее рекомендации",
            "tone": "warn",
        }
    return {
        "code": "ok",
        "label": "Подходит",
        "tone": "neutral",
    }


def list_installed_models() -> list[str]:
    """Список локальных моделей Ollama."""
    try:
        resp = requests.get(f"{_ollama_base()}/api/tags", timeout=5)
        if not resp.ok:
            return []
        data = resp.json() or {}
        names = []
        for item in data.get("models") or []:
            name = (item.get("name") or item.get("model") or "").strip()
            if name:
                names.append(name)
        return names
    except Exception:
        return []


def _model_installed(name: str, installed: list[str] | None = None) -> bool:
    installed = installed if installed is not None else list_installed_models()
    target = name.strip().lower()
    if not target:
        return False
    for item in installed:
        low = item.lower()
        if low == target or low.startswith(target + ":") or target.startswith(low + ":"):
            return True
        if ":" in target:
            base, tag = target.split(":", 1)
            if low.startswith(base + ":" + tag):
                return True
    return False


def _ollama_online() -> bool:
    try:
        resp = requests.get(f"{_ollama_base()}/api/tags", timeout=3)
        return resp.ok
    except Exception:
        return False


def _build_slots(tier: str, installed: list[str], selection: dict[str, str] | None = None) -> list[dict[str, Any]]:
    recommended = select_models(tier)
    selection = selection or {}
    chosen_llm = selection.get("llm") or recommended["llm"]
    chosen_asr = selection.get("asr") or recommended["asr"]
    chosen_embedding = selection.get("embedding") or recommended["embedding"]
    chosen_reranker = selection.get("reranker") or recommended["reranker"]

    llm_options = []
    for opt in LLM_OPTIONS:
        item = dict(opt)
        item["role"] = "llm"
        item["badge"] = fit_badge(item, tier, recommended["llm"])
        item["selected"] = item["id"] == chosen_llm
        item["installed"] = _model_installed(item["id"], installed)
        llm_options.append(item)

    asr_options = []
    for opt in ASR_OPTIONS:
        item = dict(opt)
        item["role"] = "asr"
        item["badge"] = fit_badge(item, tier, recommended["asr"])
        item["selected"] = item["id"] == chosen_asr
        item["installed"] = _model_installed(item["id"], installed)
        asr_options.append(item)

    slots = [
        {
            "role": "llm",
            "title": "LLM",
            "selectable": True,
            "recommended_id": recommended["llm"],
            "selected_id": chosen_llm,
            "options": llm_options,
            "model": next((o for o in llm_options if o["id"] == chosen_llm), llm_options[0]),
        },
        {
            "role": "embedding",
            "title": "Embedding",
            "selectable": False,
            "recommended_id": FIXED_MODELS["embedding"]["id"],
            "selected_id": chosen_embedding,
            "options": [],
            "model": {
                **FIXED_MODELS["embedding"],
                "selected": True,
                "installed": _model_installed(chosen_embedding, installed),
                "badge": {
                    "code": "fixed",
                    "label": "Фиксировано",
                    "tone": "neutral",
                },
            },
        },
        {
            "role": "reranker",
            "title": "Cross encoder",
            "selectable": False,
            "recommended_id": FIXED_MODELS["reranker"]["id"],
            "selected_id": chosen_reranker,
            "options": [],
            "model": {
                **FIXED_MODELS["reranker"],
                "selected": True,
                "installed": _model_installed(chosen_reranker, installed),
                "badge": {
                    "code": "fixed",
                    "label": "Фиксировано",
                    "tone": "neutral",
                },
            },
        },
        {
            "role": "asr",
            "title": "Speech to text",
            "selectable": True,
            "recommended_id": recommended["asr"],
            "selected_id": chosen_asr,
            "options": asr_options,
            "model": next((o for o in asr_options if o["id"] == chosen_asr), asr_options[0]),
        },
    ]
    return slots


def resolve_selection(selection: dict[str, str] | None = None, tier: str | None = None) -> dict[str, Any]:
    tier = tier or get_tier(get_hardware()["score"])
    base = select_models(tier)
    selection = selection or {}
    llm = selection.get("llm") or base["llm"]
    asr = selection.get("asr") or base["asr"]
    embedding = selection.get("embedding") or base["embedding"]
    reranker = selection.get("reranker") or base["reranker"]

    llm_opt = _find_option(LLM_OPTIONS, llm) or _find_option(LLM_OPTIONS, base["llm"])
    # Температура/токены берём от tier ПК, а не от выбранной модели.
    return {
        "llm": llm_opt["id"] if llm_opt else base["llm"],
        "asr": asr if _find_option(ASR_OPTIONS, asr) else base["asr"],
        "embedding": embedding,
        "reranker": reranker,
        "temperature": base["temperature"],
        "max_tokens": base["max_tokens"],
        "tier": tier,
    }


def required_models(models: dict[str, Any] | None = None) -> list[str]:
    models = models or select_models()
    out: list[str] = []
    for key in ("llm", "embedding", "reranker", "asr"):
        name = (models.get(key) or "").strip()
        if name and name not in out:
            out.append(name)
    return out


def get_setup_status(selection: dict[str, str] | None = None) -> dict[str, Any]:
    hw = get_hardware()
    tier = get_tier(hw["score"])
    recommended = select_models(tier)
    models = resolve_selection(selection, tier)
    installed = list_installed_models()
    slots = _build_slots(tier, installed, models)
    required = required_models(models)
    missing = [m for m in required if not _model_installed(m, installed)]
    return {
        "hardware": hw,
        "tier": tier,
        "tier_meta": TIER_META.get(tier, TIER_META["medium"]),
        "models": models,
        "recommended": recommended,
        "slots": slots,
        "ram_need_gb": TIER_RAM.get(tier, 0.0),
        "installed": installed,
        "required": required,
        "missing": missing,
        "ready": len(missing) == 0,
        "ollama_online": bool(installed) or _ollama_online(),
        "install": dict(_INSTALL_STATE),
        "active": {
            "llm": config.OLLAMA_LLM_MODEL,
            "embedding": getattr(config, "EMBEDDING_MODEL", ""),
            "reranker": getattr(config, "RERANKER_MODEL", ""),
            "asr": getattr(config, "ASR_MODEL", os_environ_asr()),
            "temperature": getattr(config, "GENERATOR_TEMP", 0.1),
            "max_tokens": getattr(config, "GENERATOR_MAX_TOKENS", 1800),
        },
    }


def os_environ_asr() -> str:
    import os

    return os.environ.get("ASR_MODEL", "")


def apply_models_to_config(models: dict[str, Any] | None = None) -> dict[str, Any]:
    """Применяет выбранный набор к runtime-config (без chunk sizes)."""
    models = models or select_models()
    llm = models["llm"]
    embedding = models["embedding"]
    reranker = models["reranker"]
    asr = models.get("asr") or ""
    temperature = float(models["temperature"])
    max_tokens = int(models["max_tokens"])

    config.OLLAMA_LLM_MODEL = llm
    config.RERANKER_MODEL = reranker
    config.GENERATOR_TEMP = temperature
    config.GENERATOR_MAX_TOKENS = max_tokens
    config.ASR_MODEL = asr

    if config.MODE == "local":
        config.LLM_MODEL = llm
        config.EMBEDDING_MODEL = embedding
    elif config.MODE == "api":
        config.EMBEDDING_MODEL = embedding
    else:
        config.EMBEDDING_MODEL = embedding

    return {
        "llm": llm,
        "embedding": embedding,
        "reranker": reranker,
        "asr": asr,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "tier": models.get("tier"),
    }


def write_env(models: dict[str, Any] | None = None, path: Path | None = None) -> Path:
    """Пишет/обновляет .env выбранными моделями (чанки не трогаем)."""
    models = models or select_models()
    path = path or (config.PROJECT_ROOT / ".env")

    existing: dict[str, str] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            raw = line.strip()
            if not raw or raw.startswith("#") or "=" not in raw:
                continue
            key, value = raw.split("=", 1)
            existing[key.strip()] = value.strip()

    existing["RAG_MODE"] = existing.get("RAG_MODE") or "local"
    existing["OLLAMA_LLM_MODEL"] = models["llm"]
    existing["EMBEDDING_MODEL"] = models["embedding"]
    existing["RERANKER_MODEL"] = models["reranker"]
    existing["ASR_MODEL"] = models.get("asr") or existing.get("ASR_MODEL", "")
    existing["USE_RERANK"] = existing.get("USE_RERANK") or "1"
    existing["GENERATOR_TEMP"] = str(models["temperature"])
    existing["GENERATOR_MAX_TOKENS"] = str(models["max_tokens"])

    order = [
        "RAG_MODE",
        "OLLAMA_LLM_MODEL",
        "EMBEDDING_MODEL",
        "RERANKER_MODEL",
        "ASR_MODEL",
        "USE_RERANK",
        "GENERATOR_TEMP",
        "GENERATOR_MAX_TOKENS",
        "OPENROUTER_API_KEY",
        "ASSEMBLYAI_API_KEY",
        "OPENROUTER_LLM_MODEL",
    ]
    lines = ["# Автоматически обновлено autoselect"]
    seen = set()
    for key in order:
        if key in existing:
            lines.append(f"{key}={existing[key]}")
            seen.add(key)
    for key, value in existing.items():
        if key not in seen:
            lines.append(f"{key}={value}")

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _append_log(message: str) -> None:
    _INSTALL_STATE["log"].append(
        {"ts": time.time(), "message": message, "current": _INSTALL_STATE.get("current")}
    )
    if len(_INSTALL_STATE["log"]) > 200:
        _INSTALL_STATE["log"] = _INSTALL_STATE["log"][-200:]


def _set_progress(name: str, **fields: Any) -> None:
    progress = _INSTALL_STATE.setdefault("progress", {})
    item = progress.setdefault(
        name,
        {"pct": 0, "status": "", "done": False, "error": None, "total": 0, "completed": 0},
    )
    item.update(fields)


def _pull_model(name: str) -> None:
    _INSTALL_STATE["current"] = name
    _set_progress(name, pct=0, status="starting", done=False, error=None)
    _append_log(f"Скачивание {name}...")
    with requests.post(
        f"{_ollama_base()}/api/pull",
        json={"name": name, "stream": True},
        stream=True,
        timeout=None,
    ) as resp:
        if not resp.ok:
            raise RuntimeError(f"Ollama pull HTTP {resp.status_code}: {resp.text[:300]}")
        for line in resp.iter_lines(decode_unicode=True):
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            status = event.get("status") or ""
            completed = event.get("completed")
            total = event.get("total")
            pct = 0
            if total and completed is not None and total > 0:
                pct = int(completed * 100 / total)
                _set_progress(
                    name,
                    pct=pct,
                    status=status or "downloading",
                    completed=completed,
                    total=total,
                )
                _append_log(f"{name}: {status} ({pct}%)")
            elif status:
                _set_progress(name, status=status)
                _append_log(f"{name}: {status}")
            if event.get("error"):
                raise RuntimeError(str(event["error"]))
    _INSTALL_STATE["done"].append(name)
    _set_progress(name, pct=100, status="success", done=True)
    _append_log(f"Готово: {name}")


def _install_worker(models: list[str], selection: dict[str, Any] | None) -> None:
    try:
        for name in models:
            if not _INSTALL_STATE["running"]:
                break
            if _model_installed(name):
                _INSTALL_STATE["done"].append(name)
                _set_progress(name, pct=100, status="already installed", done=True)
                _append_log(f"Уже установлена: {name}")
                continue
            try:
                _pull_model(name)
            except Exception as e:
                _INSTALL_STATE["failed"].append({"model": name, "error": str(e)})
                _set_progress(name, status="error", error=str(e), done=False)
                _append_log(f"Ошибка {name}: {e}")
        if not _INSTALL_STATE["failed"]:
            selected = selection or resolve_selection()
            still_missing = [
                m for m in required_models(selected) if not _model_installed(m)
            ]
            if not still_missing:
                applied = apply_models_to_config(selected)
                write_env(selected)
                _append_log(
                    "Конфиг применён: "
                    f"LLM={applied['llm']}, ASR={applied['asr']}, "
                    f"temp={applied['temperature']}, max_tokens={applied['max_tokens']}"
                )
            else:
                _append_log(
                    "Часть моделей скачана. Ещё нужно: " + ", ".join(still_missing)
                )
    except Exception as e:
        _INSTALL_STATE["error"] = str(e)
        _append_log(f"Критическая ошибка: {e}")
    finally:
        _INSTALL_STATE["running"] = False
        _INSTALL_STATE["current"] = None
        _INSTALL_STATE["finished"] = True


def start_install(
    models: list[str] | None = None,
    selection: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Запускает фоновую установку выбранных/рекомендуемых моделей."""
    with _INSTALL_LOCK:
        if _INSTALL_STATE["running"]:
            return {
                "ok": False,
                "error": "Установка уже выполняется",
                "install": dict(_INSTALL_STATE),
            }

        if not _ollama_online():
            return {
                "ok": False,
                "error": "Ollama недоступна. Запустите Ollama и повторите.",
                "install": dict(_INSTALL_STATE),
            }

        resolved = resolve_selection(selection)
        to_install = models or [
            m for m in required_models(resolved) if not _model_installed(m)
        ]
        if not to_install:
            applied = apply_models_to_config(resolved)
            write_env(resolved)
            return {
                "ok": True,
                "message": "Все выбранные модели уже установлены",
                "applied": applied,
                "install": dict(_INSTALL_STATE),
            }

        progress = {
            name: {
                "pct": 0,
                "status": "queued",
                "done": False,
                "error": None,
                "total": 0,
                "completed": 0,
            }
            for name in to_install
        }
        _INSTALL_STATE.update(
            {
                "running": True,
                "models": list(to_install),
                "current": None,
                "done": [],
                "failed": [],
                "log": [],
                "progress": progress,
                "error": None,
                "finished": False,
                "selection": resolved,
            }
        )
        thread = threading.Thread(
            target=_install_worker,
            args=(list(to_install), resolved),
            daemon=True,
        )
        thread.start()
        return {"ok": True, "message": "Установка запущена", "install": dict(_INSTALL_STATE)}


def install_status() -> dict[str, Any]:
    return dict(_INSTALL_STATE)


def bootstrap_runtime() -> dict[str, Any]:
    """При старте приложения подбирает модели и применяет, если они уже есть."""
    status = get_setup_status()
    models = status["models"]
    if status["ready"]:
        apply_models_to_config(models)
    return status


if __name__ == "__main__":
    status = get_setup_status()
    hw = status["hardware"]
    models = status["models"]
    print("=" * 50)
    print("АНАЛИЗ ОБОРУДОВАНИЯ")
    print("=" * 50)
    print(f"CPU:  {hw['cpu_cores']} ядер @ {hw['cpu_freq_mhz']:.0f} MHz")
    print(f"RAM:  {hw['ram_gb']} GB")
    print(f"GPU:  {hw['gpu'] or 'не обнаружен'}")
    print(f"VRAM: {hw['vram_gb']} GB")
    print(f"Баллы: {hw['score']}")
    print(f"Tier: {status['tier'].upper()}")
    print("=" * 50)
    print("МОДЕЛИ:")
    print(f"  LLM:       {models['llm']}")
    print(f"  Embedding: {models['embedding']}  (фикс.)")
    print(f"  Reranker:  {models['reranker']}  (фикс.)")
    print(f"  ASR:       {models['asr']}")
    print(f"  Temp:      {models['temperature']}")
    print(f"  Max tokens:{models['max_tokens']}")
    print("=" * 50)
    print(f"RAM нужно: {status['ram_need_gb']:.1f} GB / {hw['ram_gb']} GB")
    print(f"Не хватает: {status['missing'] or '—'}")
    print("=" * 50)
    for m in status["required"]:
        mark = "OK" if m not in status["missing"] else "NEED"
        print(f"  [{mark}] ollama pull {m}")
