"""Cross-encoder reranker через локальную Ollama-модель Qwen3-Reranker."""

import re

import requests

import config


_THINK_RE = re.compile(r"<think>.*?</think>", re.IGNORECASE | re.DOTALL)
_SCORE_RE = re.compile(r"[-+]?\d+(?:[\.,]\d+)?")


def _ollama_generate_url() -> str:
    base_url = config.OLLAMA_BASE_URL.rstrip("/")
    if base_url.endswith("/v1"):
        base_url = base_url[:-3]
    return f"{base_url}/api/generate"


def _clean_output(text: str) -> str:
    return _THINK_RE.sub("", text or "").strip()


def _parse_score(content: str) -> float | None:
    text = _clean_output(content).lower()
    if not text:
        return None

    match = _SCORE_RE.search(text)
    if match:
        try:
            raw = float(match.group(0).replace(",", "."))
        except ValueError:
            raw = 0.0
        return max(0.0, min(100.0, raw))

    if "yes" in text or "relevant" in text or "релевант" in text:
        return 100.0
    if "no" in text or "irrelevant" in text or "нерелевант" in text:
        return 0.0
    return None


def _build_score_prompt(query: str, document: str) -> str:
    max_chars = getattr(config, "RERANK_DOC_MAX_CHARS", 1800)
    doc = (document or "").replace("\n", " ").strip()[:max_chars]
    return (
        "You are a cross-encoder relevance scoring model.\n"
        "Compare the query and the document. "
        "Return only one integer from 0 to 100, where 0 means unrelated "
        "and 100 means the document directly answers the query. "
        "Do not output words, JSON, markdown, or explanations.\n\n"
        f"Query: {query}\n"
        f"Document: {doc}\n"
        "Score:"
    )


def _score_document(query: str, document: str) -> float | None:
    payload = {
        "model": config.RERANKER_MODEL,
        "prompt": _build_score_prompt(query, document),
        "stream": False,
        "think": False,
        "options": {
            "temperature": 0.0,
            "num_predict": getattr(config, "RERANK_NUM_PREDICT", 16),
        },
    }
    headers = {"Content-Type": "application/json"}
    timeout = getattr(config, "RERANK_TIMEOUT_SEC", 60)

    response = requests.post(
        _ollama_generate_url(),
        headers=headers,
        json=payload,
        timeout=timeout,
    )
    if response.status_code != 200 and "think" in payload:
        fallback_payload = dict(payload)
        fallback_payload.pop("think", None)
        response = requests.post(
            _ollama_generate_url(),
            headers=headers,
            json=fallback_payload,
            timeout=timeout,
        )
    if response.status_code != 200:
        print(f"rerank HTTP {response.status_code}: {response.text[:400]}")
        return None

    try:
        data = response.json()
    except ValueError as e:
        print(f"rerank JSON error: {e}; body[:500]={response.text[:500]!r}")
        return None

    content = data.get("response") or ""
    score = _parse_score(content)
    if score is None:
        print(f"rerank invalid score; content[:200]={content[:200]!r}")
    return score


def rerank(query, documents, metadatas, top_k=5):
    n = len(documents)
    if n == 0:
        return [], [], "empty_input"
    k = min(top_k, n)

    scored: list[tuple[float, int, str, dict]] = []
    try:
        for idx, (doc, meta) in enumerate(zip(documents, metadatas)):
            score = _score_document(query, doc)
            if score is None:
                continue
            scored.append((score, idx, doc, meta))
    except Exception as e:
        print(f"rerank request error: {e}")
        return documents[:k], metadatas[:k], "fallback_request_error"

    if not scored:
        return documents[:k], metadatas[:k], "fallback_no_scores"

    # При равных score сохраняем порядок RRF-кандидатов через idx.
    scored.sort(key=lambda item: (-item[0], item[1]))
    selected = scored[:k]
    selected_docs = [doc for _score, _idx, doc, _meta in selected]
    selected_meta = [meta for _score, _idx, _doc, meta in selected]
    return selected_docs, selected_meta, "ok"
