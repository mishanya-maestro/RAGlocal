"""Проверка загруженного документа на соответствие законодательству РБ.

Pipeline:
1. Классификация и парсинг документа (document_parser).
2. Чек-лист compliance-проверок по типу документа (compliance_checklists).
3. Целевой retrieval по чек-листу в релевантных кодексах.
4. Один LLM-вызов на чанк для проверки всех пунктов чек-листа.
5. Валидация и агрегация отчёта.
"""

from __future__ import annotations

import json
import re
import sqlite3
import traceback
from typing import Any

import config
import database
from compliance_checklists import CheckItem, get_checklist
from document_parser import DocumentSegment, ParsedDocument, chunk_segments
from generator import _OUTPUT_RULES, _call_llm, _clean_llm_output
from retrieval import retrieve_context


_REQUIRED_ISSUE_FIELDS = {"quote", "issue", "norm", "norm_quote", "suggestion", "severity", "confidence"}
_SEVERITY_ORDER = {"критично": 0, "важно": 1, "рекомендация": 2}


def _resolve_filter_codes(check_items: list[CheckItem]) -> list[str] | None:
    codes: set[str] = set()
    for item in check_items:
        codes.update(item.relevant_codes)
    return sorted(codes) if codes else None


def _gather_context_for_checklist(
    checklist: list[CheckItem], filter_codes: list[str] | None, top_k: int = 3
) -> tuple[list[tuple], list[dict[str, Any]]]:
    import sqlite3

    all_rows: list[tuple] = []
    all_meta: list[dict] = []
    seen: set[tuple[str, str]] = set()

    queries: list[str] = []
    for item in checklist:
        queries.extend(item.search_queries)
    queries = list(dict.fromkeys(queries))[:10]  

    con = sqlite3.connect(str(config.FULLTEXT_DB))
    try:
        for query in queries:
            try:
                results = database.search(query, n_results=top_k, filter_codes=filter_codes)
                docs = (results.get("documents") or [[]])[0]
                metas = (results.get("metadatas") or [[]])[0]
                for doc, m in zip(docs, metas):
                    key = (m["code"], m["number"])
                    if key in seen:
                        continue
                    seen.add(key)

                    row = con.execute(
                        "SELECT text FROM fulltext WHERE code=? AND number=?",
                        (m["code"], m["number"]),
                    ).fetchone()
                    if row:
                        all_rows.append(row)
                        all_meta.append(m)
            except Exception:
                traceback.print_exc()
    finally:
        con.close()

    return all_rows, all_meta


def _build_context(rows: list[tuple], meta: list[dict], max_chars: int = 6000) -> str:
    parts = []
    used = 0
    for row, m in zip(rows, meta):
        if not row or not row[0]:
            continue
        text = row[0]
        if used + len(text) > max_chars:
            text = text[: max_chars - used]
        parts.append(f"### {m['code']}, ст. {m['number']}\n{text}")
        used += len(text)
        if used >= max_chars:
            break
    return "\n\n".join(parts)


def _build_checklist_prompt(checklist: list[CheckItem]) -> str:
    lines = []
    for item in checklist:
        lines.append(f"- {item.id}: {item.name}. {item.description}")
        lines.append(f"  Обязательные положения: {', '.join(item.required_clauses) or 'нет'}.")
    return "\n".join(lines)


def _check_chunk(
    chunk: DocumentSegment,
    checklist: list[CheckItem],
    context_rows: list[tuple],
    context_meta: list[dict],
) -> list[dict[str, Any]]:
    """Один LLM-вызов на чанк для проверки всех пунктов чек-листа."""
    context = _build_context(context_rows, context_meta)
    if not context.strip():
        return []

    prompt = f"""Ты — юридический эксперт по законодательству Республики Беларусь.

Проверь фрагмент документа по следующим пунктам чек-листа. Для каждой выявленной проблемы верни объект JSON в массиве.

Пункты чек-листа:
{_build_checklist_prompt(checklist)}

Требования к каждому объекту:
- "quote": цитата из фрагмента документа (точно как в тексте).
- "issue": описание проблемы (кратко, по существу).
- "norm": нарушенная/неучтённая статья в формате "Кодекс, ст. N".
- "norm_quote": цитата из текста статьи, на которую ссылаешься.
- "suggestion": конкретная формулировка правки или рекомендация.
- "severity": одно из "критично", "важно", "рекомендация".
- "confidence": число 0.0–1.0.
- "check_id": id пункта чек-листа (например "c_subject").

Если для пункта проблем нет, не включай объект. Если проблем нет вообще, верни пустой массив [].

Отсутствие обязательного положения в документе — это тоже проблема. Severity по умолчанию: критично для отсутствия существенных условий, важно для ответственности/формы, рекомендация для прочих.

Контекст из законодательства РБ:
{context}

Фрагмент документа (страница {chunk.page or 'неизвестна'}):
{chunk.text}

{_OUTPUT_RULES}

Верни строго JSON-массив:
[
  {{
    "quote": "...",
    "issue": "...",
    "norm": "...",
    "norm_quote": "...",
    "suggestion": "...",
    "severity": "...",
    "confidence": 0.0,
    "check_id": "..."
  }}
]

JSON:"""
    try:
        raw = _call_llm(
            system_prompt=(
                "Ты юридический эксперт по законодательству РБ. "
                "Анализируй документы строго по предоставленным статьям. "
                "Ответ только в JSON-массиве без пояснений."
            ),
            user_prompt=prompt,
            mode_override=None,
            model_override="qwen3.5:4b",
        )
        raw = _clean_llm_output(raw)
        if "```" in raw:
            raw = raw.split("```")[-2] if raw.count("```") >= 2 else raw.split("```")[-1]
        raw = raw.strip()
        if raw.startswith("json"):
            raw = raw[4:].strip()
        items = json.loads(raw)
        if not isinstance(items, list):
            return []
        return items
    except Exception:
        traceback.print_exc()
        return []


def _validate_issue(
    issue: dict[str, Any],
    chunk_text: str,
    context_rows: list[tuple],
    checklist: list[CheckItem],
) -> dict[str, Any] | None:
    if not isinstance(issue, dict):
        return None

    for field in _REQUIRED_ISSUE_FIELDS:
        issue.setdefault(field, "")

    sev = (issue.get("severity") or "").lower().strip()
    if sev not in _SEVERITY_ORDER:
        issue["severity"] = "важно"
    else:
        issue["severity"] = sev

    try:
        issue["confidence"] = max(0.0, min(1.0, float(issue.get("confidence") or 0.5)))
    except (ValueError, TypeError):
        issue["confidence"] = 0.5

    quote = (issue.get("quote") or "").strip()
    if quote and quote not in chunk_text:
        issue["confidence"] = max(0.1, issue["confidence"] - 0.2)
        issue["validation_note"] = "цитата не найдена дословно в фрагменте"

    norm = (issue.get("norm") or "").strip()
    if norm:
        match = re.search(r"([^,]+),\s*ст\.?\s*(\S+)", norm, re.IGNORECASE)
        if match:
            code_name = match.group(1).strip()
            number = match.group(2).strip()
            con = sqlite3.connect(str(config.FULLTEXT_DB))
            try:
                row = con.execute(
                    "SELECT 1 FROM fulltext WHERE code=? AND number=?",
                    (code_name, number),
                ).fetchone()
                if not row:
                    issue["confidence"] = max(0.1, issue["confidence"] - 0.3)
                    issue["validation_note"] = "норма не найдена в индексе"
            finally:
                con.close()

    norm_quote = (issue.get("norm_quote") or "").strip()
    if norm_quote:
        context_text = "\n".join(r[0] for r in context_rows if r and r[0])
        if norm_quote not in context_text:
            issue["confidence"] = max(0.1, issue["confidence"] - 0.2)
            issue["validation_note"] = "цитата нормы не найдена в контексте"

    check_id = issue.get("check_id") or ""
    check_name = next((c.name for c in checklist if c.id == check_id), "")
    issue["check_name"] = check_name

    return issue


def analyze_document(parsed: ParsedDocument) -> dict[str, Any]:
    """Главная точка входа: анализирует ParsedDocument и возвращает отчёт."""
    if not parsed or not parsed.full_text.strip():
        return {
            "chunks": 0,
            "issues": [],
            "summary": {"critical": 0, "important": 0, "recommendation": 0, "total": 0},
        }

    doc_type = parsed.doc_type if parsed.doc_type != "unknown" else "contract"
    checklist = get_checklist(doc_type) or get_checklist("contract")
    filter_codes = _resolve_filter_codes(checklist)

    chunks = chunk_segments(parsed.segments, max_chunk_size=1500, overlap=200)
    if not chunks:
        chunks = [DocumentSegment(text=parsed.full_text[:5000], index=0)]

    context_rows, context_meta = _gather_context_for_checklist(checklist, filter_codes)

    all_issues: list[dict[str, Any]] = []
    errors: list[str] = []

    for chunk in chunks:
        try:
            raw_issues = _check_chunk(chunk, checklist, context_rows, context_meta)
            for issue in raw_issues:
                validated = _validate_issue(issue, chunk.text, context_rows, checklist)
                if validated:
                    all_issues.append(validated)
        except Exception as e:
            traceback.print_exc()
            errors.append(str(e))

    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for it in all_issues:
        key = (it.get("quote") or "") + "|" + (it.get("issue") or "") + "|" + (it.get("norm") or "")
        if key not in seen:
            seen.add(key)
            unique.append(it)

    unique.sort(key=lambda x: (_SEVERITY_ORDER.get(x.get("severity", ""), 3), -x.get("confidence", 0)))

    summary = {
        "critical": sum(1 for i in unique if i.get("severity") == "критично"),
        "important": sum(1 for i in unique if i.get("severity") == "важно"),
        "recommendation": sum(1 for i in unique if i.get("severity") == "рекомендация"),
        "total": len(unique),
    }

    return {
        "chunks": len(chunks),
        "doc_type": doc_type,
        "doc_type_label": _get_label(doc_type),
        "doc_type_confidence": parsed.doc_type_confidence,
        "metadata": parsed.metadata,
        "issues": unique,
        "summary": summary,
        "errors": errors,
    }


def _get_label(doc_type: str) -> str:
    from document_parser import _TYPE_LABELS

    return _TYPE_LABELS.get(doc_type, "Прочий документ")
