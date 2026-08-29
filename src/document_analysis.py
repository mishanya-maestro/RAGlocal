"""Проверка документа на соответствие шаблону + LLM-анализ содержания.

Гибридный подход:
1. Шаблон — надёжная проверка структуры (какие разделы есть / отсутствуют).
2. LLM — проверка содержания найденных разделов на соответствие законам и образцу.
"""

from __future__ import annotations

import json
import traceback
from typing import Any

import config
import database
from document_parser import DocumentSegment, ParsedDocument
from document_templates import TemplateSection, analyze_structure, get_template
from generator import _OUTPUT_RULES, _call_llm, _clean_llm_output


_REQUIRED_ISSUE_FIELDS = {"quote", "issue", "norm", "suggestion", "severity", "confidence"}
_SEVERITY_ORDER = {"критично": 0, "важно": 1, "рекомендация": 2}


def _gather_law_context(queries: list[str], top_k: int = 3) -> str:
    """Собирает контекст из законов для списка поисковых запросов."""
    if not queries:
        return ""

    seen: set[tuple[str, str]] = set()
    parts: list[str] = []
    for query in queries[:6]:  # ограничиваем число запросов
        try:
            results = database.search(query, n_results=top_k)
            docs = (results.get("documents") or [[]])[0]
            metas = (results.get("metadatas") or [[]])[0]
            for doc, m in zip(docs, metas):
                key = (m.get("code", ""), m.get("number", ""))
                if key in seen:
                    continue
                seen.add(key)
                parts.append(f"### {m.get('code', '')}, ст. {m.get('number', '')}\n{doc}")
        except Exception:
            traceback.print_exc()

    return "\n\n".join(parts)


def _build_content_analysis_prompt(
    template_title: str,
    found_sections: list[dict[str, Any]],
    example_text: str,
    law_context: str,
) -> str:
    """Строит один prompt для LLM-проверки содержания найденных разделов."""
    sections_block = []
    for idx, sec in enumerate(found_sections):
        sections_block.append(
            f"""Раздел {idx + 1}: {sec['name']}
Описание проверки: {sec['content_prompt']}
Текст из документа:
{sec['section_text'][:1200]}
---"""
        )

    prompt = f"""Ты — юридический эксперт по законодательству Республики Беларусь.

Проверь содержание найденных разделов документа по типу "{template_title}".
Сверь каждый раздел с образцом и законами. Если раздел оформлен неверно или его содержание не соответствует требованиям, укажи проблему.

Для каждой проблемы верни объект JSON с полями:
- "section_id": id раздела, к которому относится проблема
- "section_name": название раздела
- "quote": цитата из документа, по которой выявлена проблема
- "issue": краткое описание проблемы
- "norm": нарушенная норма в формате "Кодекс, ст. N" (или "не указано")
- "suggestion": конкретная формулировка, что добавить или исправить
- "severity": "критично", "важно" или "рекомендация"
- "confidence": число от 0.0 до 1.0

Если проблем нет, верни пустой массив [].

Образец документа (для сравнения):
{example_text[:2500]}

{law_context}

Найденные разделы из документа пользователя:
{chr(10).join(sections_block)}

{_OUTPUT_RULES}

Верни строго JSON-массив:
[
  {{
    "section_id": "...",
    "section_name": "...",
    "quote": "...",
    "issue": "...",
    "norm": "...",
    "suggestion": "...",
    "severity": "...",
    "confidence": 0.0
  }}
]

JSON:"""
    return prompt


def _analyze_content(
    template_title: str,
    found_sections: list[dict[str, Any]],
    example_text: str,
) -> list[dict[str, Any]]:
    """LLM-проверка содержания найденных разделов."""
    if not found_sections:
        return []

    # Собираем все поисковые запросы из разделов.
    queries: list[str] = []
    for sec in found_sections:
        queries.extend(sec.get("law_queries", []))
    queries = list(dict.fromkeys(queries))[:10]
    law_context = _gather_law_context(queries)

    prompt = _build_content_analysis_prompt(
        template_title=template_title,
        found_sections=found_sections,
        example_text=example_text,
        law_context=law_context,
    )

    try:
        raw = _call_llm(
            system_prompt=(
                "Ты юридический эксперт по законодательству РБ. "
                "Проверяй документы строго по образцу и законам. "
                "Ответ только JSON-массив."
            ),
            user_prompt=prompt,
            model_override="qwen3.5:4b",
            max_tokens=4096,
        )
        raw = _clean_llm_output(raw)
        if "```" in raw:
            raw = raw.split("```")[-2] if raw.count("```") >= 2 else raw.split("```")[-1]
        raw = raw.strip()
        if raw.startswith("json"):
            raw = raw[4:].strip()
        items = _safe_json_loads(raw)
        if not isinstance(items, list):
            return []
        return items
    except Exception:
        traceback.print_exc()
        return []


def _safe_json_loads(raw: str) -> Any:
    """Пытается распарсить JSON, если нужно — дополняет обрезанный массив."""
    candidates = [raw, raw + "]", raw + "}]", raw + '"}]', raw + '"]"}]', raw + "}"]
    for candidate in candidates:
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue
    raise json.JSONDecodeError("Unable to parse LLM JSON", raw, 0)


def _validate_content_issue(item: dict[str, Any], found_sections: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None

    for field in _REQUIRED_ISSUE_FIELDS:
        item.setdefault(field, "")

    sev = (item.get("severity") or "").lower().strip()
    if sev not in _SEVERITY_ORDER:
        item["severity"] = "важно"
    else:
        item["severity"] = sev

    try:
        item["confidence"] = max(0.0, min(1.0, float(item.get("confidence") or 0.5)))
    except (ValueError, TypeError):
        item["confidence"] = 0.5

    section_id = item.get("section_id") or ""
    section_name = item.get("section_name") or ""
    if not section_name:
        sec = next((s for s in found_sections if s.get("id") == section_id), None)
        if sec:
            item["section_name"] = sec.get("name", "")

    return item


def analyze_document(parsed: ParsedDocument) -> dict[str, Any]:
    """Главная точка входа: шаблонная структура + LLM содержание."""
    if not parsed or not parsed.full_text.strip():
        return {
            "chunks": 0,
            "issues": [],
            "summary": {"critical": 0, "important": 0, "recommendation": 0, "total": 0},
        }

    doc_type = parsed.doc_type if parsed.doc_type != "unknown" else "contract"
    template = get_template(doc_type)

    if not template:
        return {
            "chunks": 0,
            "issues": [],
            "summary": {"critical": 0, "important": 0, "recommendation": 0, "total": 0},
            "error": f"Нет шаблона для типа документа: {doc_type}",
        }

    # 1. Структурный анализ.
    structure = analyze_structure(parsed, template)

    # 2. Автоматические замечания по отсутствующим разделам.
    issues: list[dict[str, Any]] = []
    for missing in structure["missing_sections"]:
        if not missing["required"]:
            continue
        issues.append(
            {
                "type": "missing_section",
                "section_id": missing["id"],
                "section_name": missing["name"],
                "quote": "",
                "issue": f"Отсутствует обязательный раздел: {missing['name']}. В документе не найдены ключевые слова: {', '.join(missing['keywords'][:5])}.",
                "norm": "не указано",
                "suggestion": f"Добавьте раздел «{missing['name']}». Пример: {missing['example_text']}",
                "severity": missing["severity"],
                "confidence": 0.95,
            }
        )

    # 3. LLM-анализ содержания найденных разделов.
    found_sections = structure.get("found_sections", [])
    content_issues = _analyze_content(
        template_title=template.title,
        found_sections=found_sections,
        example_text=template.get_example_text(),
    )
    for item in content_issues:
        validated = _validate_content_issue(item, found_sections)
        if validated:
            validated["type"] = "content_issue"
            issues.append(validated)

    # 4. Сортировка и summary.
    issues.sort(key=lambda x: (_SEVERITY_ORDER.get(x.get("severity", ""), 3), -x.get("confidence", 0)))

    summary = {
        "critical": sum(1 for i in issues if i.get("severity") == "критично"),
        "important": sum(1 for i in issues if i.get("severity") == "важно"),
        "recommendation": sum(1 for i in issues if i.get("severity") == "рекомендация"),
        "total": len(issues),
    }

    return {
        "chunks": 1,
        "doc_type": doc_type,
        "doc_type_label": _get_label(doc_type),
        "doc_type_confidence": parsed.doc_type_confidence,
        "metadata": parsed.metadata,
        "template": {
            "title": template.title,
            "example_file": template.example_file,
            "example_text": template.get_example_text(),
        },
        "structure": structure,
        "issues": issues,
        "summary": summary,
    }


def _get_label(doc_type: str) -> str:
    from document_parser import _TYPE_LABELS

    return _TYPE_LABELS.get(doc_type, "Прочий документ")
