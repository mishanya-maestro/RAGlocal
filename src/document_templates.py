"""Шаблоны документов и движок сравнения с образцом.

Гибридный подход: шаблон проверяет структуру, LLM проверяет содержание.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from document_parser import DocumentSegment, ParsedDocument


@dataclass
class TemplateSection:
    """Раздел шаблона документа."""

    id: str
    name: str
    required: bool
    # Ключевые слова для поиска в документе (заголовки, подписи, абзацы).
    keywords: list[str] = field(default_factory=list)
    # Образец формулировки/содержания раздела.
    example_text: str = ""
    # Промпт для LLM, если раздел найден.
    content_prompt: str = ""
    # Поисковые запросы для retrieval норм, относящихся к разделу.
    law_queries: list[str] = field(default_factory=list)
    # Степень риска, если раздел отсутствует.
    missing_severity: str = "критично"


@dataclass
class DocumentTemplate:
    """Шаблон документа."""

    doc_type: str
    title: str
    example_file: str
    sections: list[TemplateSection] = field(default_factory=list)

    def get_example_text(self) -> str:
        path = Path(__file__).parent / "templates_docs" / self.example_file
        if path.exists():
            return path.read_text(encoding="utf-8")
        return ""


# ---------------------------------------------------------------------------
# Шаблон искового заявления
# ---------------------------------------------------------------------------
ISKOVAYA_TEMPLATE = DocumentTemplate(
    doc_type="claim",
    title="Исковое заявление",
    example_file="iskovoe_zayavlenie.txt",
    sections=[
        TemplateSection(
            id="court",
            name="Наименование суда",
            required=True,
            keywords=["в суд", "суд", "района", "городского суда"],
            example_text="В _______________ суд _______________ района г. _______________",
            content_prompt="Проверь, что указано конкретное наименование суда, в который подаётся исковое заявление.",
            law_queries=["наименование суда исковое заявление", "подсудность дела"],
            missing_severity="критично",
        ),
        TemplateSection(
            id="plaintiff",
            name="Данные истца",
            required=True,
            keywords=["истец", "истца", "адрес места жительства"],
            example_text="Истец: ФИО полностью, адрес места жительства, контактные данные",
            content_prompt="Проверь, что указаны полные данные истца: ФИО, адрес, контактные данные.",
            law_queries=["истец адрес исковое заявление"],
            missing_severity="критично",
        ),
        TemplateSection(
            id="defendant",
            name="Данные ответчика",
            required=True,
            keywords=["ответчик", "ответчика"],
            example_text="Ответчик: наименование организации или ФИО физического лица, адрес",
            content_prompt="Проверь, что указаны полные данные ответчика.",
            law_queries=["ответчик адрес исковое заявление"],
            missing_severity="критично",
        ),
        TemplateSection(
            id="claim_value",
            name="Цена иска",
            required=False,
            keywords=["цена иска", "исковое требование", "сумма иска"],
            example_text="Цена иска: ____________ рублей",
            content_prompt="Проверь, что цена иска указана, если иск имущественный.",
            law_queries=["цена иска государственная пошлина"],
            missing_severity="важно",
        ),
        TemplateSection(
            id="subject",
            name="Предмет иска",
            required=True,
            keywords=["предмет иска", "предметом иска"],
            example_text="Предмет иска: краткое описание, в чём заключается спор",
            content_prompt="Проверь, что предмет иска описан конкретно и понятно.",
            law_queries=["предмет иска"],
            missing_severity="критично",
        ),
        TemplateSection(
            id="grounds",
            name="Основание иска",
            required=True,
            keywords=["основание иска", "основанием иска", "обстоятельства"],
            example_text="Основание иска: ссылка на договор, фактические обстоятельства, нарушение прав истца",
            content_prompt="Проверь, что основание иска содержит фактические обстоятельства и правовые основания.",
            law_queries=["основание иска"],
            missing_severity="критично",
        ),
        TemplateSection(
            id="demands",
            name="Исковые требования",
            required=True,
            keywords=["исковые требования", "требования", "прошу", "прошу взыскать"],
            example_text="Исковые требования: конкретные требования, понятные для исполнения",
            content_prompt="Проверь, что исковые требования конкретны, понятны и подлежат исполнению.",
            law_queries=["исковые требования"],
            missing_severity="критично",
        ),
        TemplateSection(
            id="evidence",
            name="Доказательства",
            required=False,
            keywords=["доказательства", "доказательства подтверждающие"],
            example_text="Доказательства, подтверждающие исковые требования: перечень документов",
            content_prompt="Проверь, что указаны доказательства, подтверждающие исковые требования.",
            law_queries=["доказательства в гражданском процессе"],
            missing_severity="важно",
        ),
        TemplateSection(
            id="attachments",
            name="Приложения",
            required=True,
            keywords=["приложения", "приложению", "прилагаю"],
            example_text="Приложения: копия искового заявления, квитанция о пошлине, копии документов",
            content_prompt="Проверь, что перечислены приложения к исковому заявлению.",
            law_queries=["приложения к исковому заявлению"],
            missing_severity="важно",
        ),
        TemplateSection(
            id="signature",
            name="Подпись истца и дата",
            required=True,
            keywords=["подпись", "истца", "дата подачи", "дата"],
            example_text="Дата подачи заявления, подпись истца / ФИО",
            content_prompt="Проверь, что заявление подписано и содержит дату.",
            law_queries=["подпись истца исковое заявление"],
            missing_severity="критично",
        ),
    ],
)


# ---------------------------------------------------------------------------
# Шаблон договора купли-продажи
# ---------------------------------------------------------------------------
DOGOVOR_TEMPLATE = DocumentTemplate(
    doc_type="contract",
    title="Договор купли-продажи",
    example_file="dogovor_kupli_prodazhi.txt",
    sections=[
        TemplateSection(
            id="parties",
            name="Реквизиты сторон (преамбула)",
            required=True,
            keywords=["продавец", "покупатель", "в лице", "именуемый", "стороны"],
            example_text="Продавец и Покупатель с полными реквизитами, в лице представителей",
            content_prompt="Проверь, что указаны полные реквизиты Продавца и Покупателя, должности и основания представительства.",
            law_queries=["стороны договора реквизиты"],
            missing_severity="критично",
        ),
        TemplateSection(
            id="subject",
            name="Предмет договора",
            required=True,
            keywords=["предмет договора", "товар", "передать в собственность"],
            example_text="Предмет договора: наименование, количество, модель, серийный номер товара",
            content_prompt="Проверь, что предмет договора описан конкретно: наименование, количество, качество.",
            law_queries=["предмет договора купли-продажи"],
            missing_severity="критично",
        ),
        TemplateSection(
            id="price",
            name="Цена и порядок расчётов",
            required=True,
            keywords=["цена договора", "порядок расчётов", "оплаты", "стоимость"],
            example_text="Цена договора и порядок оплаты: сумма, способ и сроки оплаты",
            content_prompt="Проверь, что указана цена договора и порядок оплаты.",
            law_queries=["цена договора порядок оплаты"],
            missing_severity="критично",
        ),
        TemplateSection(
            id="delivery",
            name="Сроки и место передачи товара",
            required=False,
            keywords=["сроки поставки", "передачи товара", "место передачи"],
            example_text="Сроки поставки и место передачи товара",
            content_prompt="Проверь, что указаны сроки и место передачи товара, если это важно для договора.",
            law_queries=["сроки поставки товара"],
            missing_severity="важно",
        ),
        TemplateSection(
            id="obligations",
            name="Права и обязанности сторон",
            required=False,
            keywords=["права и обязанности", "обязанности сторон", "обязуется"],
            example_text="Права и обязанности сторон",
            content_prompt="Проверь, что определены права и обязанности сторон.",
            law_queries=["права и обязанности сторон договора"],
            missing_severity="рекомендация",
        ),
        TemplateSection(
            id="liability",
            name="Ответственность и неустойка",
            required=True,
            keywords=["ответственность", "неустойка", "штраф", "пени"],
            example_text="Ответственность сторон, размер неустойки",
            content_prompt="Проверь, что размер неустойки не заведомо несоразмерен последствиям нарушения.",
            law_queries=["неустойка договор"],
            missing_severity="важно",
        ),
        TemplateSection(
            id="term",
            name="Срок действия и порядок изменения",
            required=False,
            keywords=["срок действия", "действует до", "изменения и дополнения"],
            example_text="Срок действия договора и порядок изменения",
            content_prompt="Проверь, что указан срок действия договора.",
            law_queries=["срок действия договора"],
            missing_severity="важно",
        ),
        TemplateSection(
            id="signature",
            name="Подписи сторон",
            required=True,
            keywords=["подписи сторон", "подпись", "подписал", "м.п."],
            example_text="Подписи сторон с расшифровкой и печатями, если требуется",
            content_prompt="Проверь, что договор подписан обеими сторонами с расшифровкой.",
            law_queries=["подпись договор"],
            missing_severity="критично",
        ),
    ],
)


# ---------------------------------------------------------------------------
# Шаблон трудового договора
# ---------------------------------------------------------------------------
TRUD_TEMPLATE = DocumentTemplate(
    doc_type="labor_contract",
    title="Трудовой договор",
    example_file="trudovoi_dogovor.txt",
    sections=[
        TemplateSection(
            id="parties",
            name="Стороны трудового договора",
            required=True,
            keywords=["работодатель", "работник", "в лице", "именуемый"],
            example_text="Работодатель и Работник с полными реквизитами",
            content_prompt="Проверь, что указаны полные данные работодателя и работника.",
            law_queries=["стороны трудового договора"],
            missing_severity="критично",
        ),
        TemplateSection(
            id="position",
            name="Должность и место работы",
            required=True,
            keywords=["должность", "место работы", "структурное подразделение"],
            example_text="Должность, место работы, структурное подразделение",
            content_prompt="Проверь, что указаны должность и место работы работника.",
            law_queries=["должность трудовой договор"],
            missing_severity="критично",
        ),
        TemplateSection(
            id="start_date",
            name="Дата начала работы",
            required=True,
            keywords=["дата начала работы", "начала работы"],
            example_text="Дата начала работы",
            content_prompt="Проверь, что указана дата начала работы.",
            law_queries=["дата начала работы"],
            missing_severity="критично",
        ),
        TemplateSection(
            id="probation",
            name="Испытательный срок",
            required=False,
            keywords=["испытательный срок", "пробная работа"],
            example_text="Условия об испытательном сроке, не более 3 месяцев (6 для руководителей)",
            content_prompt="Проверь, что испытательный срок не превышает 3 месяцев (6 для руководителей).",
            law_queries=["испытательный срок трудовой договор"],
            missing_severity="важно",
        ),
        TemplateSection(
            id="salary",
            name="Условия оплаты труда",
            required=True,
            keywords=["заработная плата", "оплаты труда", "выплата заработной платы"],
            example_text="Размер заработной платы, сроки выплаты не реже 2 раз в месяц",
            content_prompt="Проверь, что указан размер заработной платы и сроки её выплаты не реже 2 раз в месяц.",
            law_queries=["заработная плата трудовой договор"],
            missing_severity="критично",
        ),
        TemplateSection(
            id="work_hours",
            name="Режим рабочего времени",
            required=True,
            keywords=["режим рабочего времени", "рабочий график", "продолжительность рабочего времени"],
            example_text="Режим рабочего времени, график работы",
            content_prompt="Проверь, что указан режим рабочего времени.",
            law_queries=["режим рабочего времени"],
            missing_severity="важно",
        ),
        TemplateSection(
            id="leave",
            name="Ежегодный отпуск",
            required=False,
            keywords=["отпуск", "ежегодный отпуск", "оплачиваемый отпуск"],
            example_text="Ежегодный оплачиваемый отпуск не менее 24 календарных дней",
            content_prompt="Проверь, что указан ежегодный оплачиваемый отпуск не менее 24 календарных дней.",
            law_queries=["ежегодный оплачиваемый отпуск"],
            missing_severity="рекомендация",
        ),
        TemplateSection(
            id="signature",
            name="Подписи сторон",
            required=True,
            keywords=["подписи сторон", "подпись", "работодатель", "работник"],
            example_text="Подписи работодателя и работника",
            content_prompt="Проверь, что договор подписан обеими сторонами.",
            law_queries=["подпись трудовой договор"],
            missing_severity="критично",
        ),
    ],
)


# ---------------------------------------------------------------------------
# Шаблон приказа о приёме
# ---------------------------------------------------------------------------
PRIKAZ_TEMPLATE = DocumentTemplate(
    doc_type="order",
    title="Приказ о приёме на работу",
    example_file="prikaz_o_prieme.txt",
    sections=[
        TemplateSection(
            id="header",
            name="Шапка приказа (наименование организации, дата, номер)",
            required=True,
            keywords=["приказ", "№", "приказываю", "приёме на работу"],
            example_text="ПРИКАЗ от дата № номер О приёме на работу",
            content_prompt="Проверь, что приказ содержит наименование организации, дату, номер и тему.",
            law_queries=["приказ о приеме на работу"],
            missing_severity="критично",
        ),
        TemplateSection(
            id="basis",
            name="Основание приказа",
            required=True,
            keywords=["основание", "в соответствии с трудовым договором", "трудовой договор от"],
            example_text="Основание: трудовой договор от ... № ...",
            content_prompt="Проверь, что указано основание издания приказа (трудовой договор).",
            law_queries=["основание приказа о приеме"],
            missing_severity="важно",
        ),
        TemplateSection(
            id="employee",
            name="ФИО и должность работника",
            required=True,
            keywords=["принять на работу", "на должность", "фио"],
            example_text="Принять на работу ФИО на должность ...",
            content_prompt="Проверь, что указаны ФИО работника и должность.",
            law_queries=["приказ о приеме на работу должность"],
            missing_severity="критично",
        ),
        TemplateSection(
            id="start_date",
            name="Дата начала работы",
            required=True,
            keywords=["дата начала работы"],
            example_text="Дата начала работы",
            content_prompt="Проверь, что указана дата начала работы.",
            law_queries=["дата начала работы приказ"],
            missing_severity="критично",
        ),
        TemplateSection(
            id="salary",
            name="Размер заработной платы",
            required=True,
            keywords=["заработную плату", "размере"],
            example_text="Установить заработную плату в размере ...",
            content_prompt="Проверь, что указан размер заработной платы.",
            law_queries=["заработная плата приказ о приеме"],
            missing_severity="критично",
        ),
        TemplateSection(
            id="probation",
            name="Испытательный срок",
            required=False,
            keywords=["испытательный срок"],
            example_text="Установить/не устанавливать испытательный срок",
            content_prompt="Проверь, что условие об испытательном сроке не противоречит закону.",
            law_queries=["испытательный срок приказ"],
            missing_severity="важно",
        ),
        TemplateSection(
            id="issuer",
            name="Подпись руководителя",
            required=True,
            keywords=["руководитель организации", "подпись"],
            example_text="Руководитель организации / подпись / ФИО",
            content_prompt="Проверь, что приказ подписан руководителем.",
            law_queries=["подпись приказа"],
            missing_severity="критично",
        ),
        TemplateSection(
            id="acknowledgement",
            name="Ознакомление работника",
            required=True,
            keywords=["ознакомлен", "с приказом", "подпись работника"],
            example_text="С приказом ознакомлен: подпись работника, ФИО, дата",
            content_prompt="Проверь, что работник ознакомлен с приказом под подпись.",
            law_queries=["ознакомление работника с приказом"],
            missing_severity="важно",
        ),
    ],
)


TEMPLATES: dict[str, DocumentTemplate] = {
    "claim": ISKOVAYA_TEMPLATE,
    "contract": DOGOVOR_TEMPLATE,
    "labor_contract": TRUD_TEMPLATE,
    "order": PRIKAZ_TEMPLATE,
}


def get_template(doc_type: str) -> DocumentTemplate | None:
    """Возвращает шаблон для типа документа."""
    return TEMPLATES.get(doc_type)


def _normalize(text: str) -> str:
    return text.lower().replace("ё", "е").strip()


def _find_section_in_document(section: TemplateSection, text: str) -> tuple[bool, str | None]:
    """Ищет раздел шаблона в тексте документа по ключевым словам и fuzzy matching.
    Возвращает (found, matched_text)."""
    norm_text = _normalize(text)

    # 1. Точное вхождение ключевых слов.
    for kw in section.keywords:
        norm_kw = _normalize(kw)
        if norm_kw in norm_text:
            return True, kw

    # 2. Fuzzy matching для заголовков и подписей (по предложениям).
    sentences = [s.strip() for s in text.split("\n") if s.strip()]
    for sentence in sentences:
        norm_sentence = _normalize(sentence)
        for kw in section.keywords:
            norm_kw = _normalize(kw)
            # Если ключевое слово не короче 4 символов — проверяем fuzzy.
            if len(norm_kw) >= 4:
                ratio = difflib.SequenceMatcher(None, norm_kw, norm_sentence).ratio()
                if ratio >= 0.6:
                    return True, sentence

    return False, None


def analyze_structure(parsed: ParsedDocument, template: DocumentTemplate) -> dict[str, Any]:
    """Сравнивает документ с шаблоном. Возвращает структурный отчёт."""
    full_text = _normalize(parsed.full_text)
    segments = parsed.segments

    found_sections: list[dict[str, Any]] = []
    missing_sections: list[dict[str, Any]] = []

    for section in template.sections:
        found, matched = _find_section_in_document(section, parsed.full_text)

        # Определяем текст раздела в документе: ищем ближайший сегмент с keywords.
        section_text = ""
        if found and segments:
            for i, seg in enumerate(segments):
                if any(_normalize(kw) in _normalize(seg.text) for kw in section.keywords):
                    # Берём этот сегмент и следующие 3-4, пока не встретим другой заголовок.
                    parts = [seg.text]
                    for j in range(i + 1, min(i + 5, len(segments))):
                        if segments[j].segment_type == "heading" and any(
                            _normalize(k) in _normalize(segments[j].text) for k in section.keywords
                        ):
                            break
                        parts.append(segments[j].text)
                    section_text = "\n".join(parts).strip()
                    break

        item = {
            "id": section.id,
            "name": section.name,
            "required": section.required,
            "found": found,
            "matched": matched,
            "severity": section.missing_severity,
            "keywords": section.keywords,
            "example_text": section.example_text,
            "content_prompt": section.content_prompt,
            "law_queries": section.law_queries,
            "section_text": section_text,
        }

        if found:
            found_sections.append(item)
        else:
            missing_sections.append(item)

    return {
        "template_title": template.title,
        "template_example_file": template.example_file,
        "template_example_text": template.get_example_text(),
        "total_sections": len(template.sections),
        "found_count": len(found_sections),
        "missing_count": len(missing_sections),
        "required_found": sum(1 for s in found_sections if s["required"]),
        "required_missing": sum(1 for s in missing_sections if s["required"]),
        "found_sections": found_sections,
        "missing_sections": missing_sections,
    }


def get_template_for_unknown(doc_type: str) -> DocumentTemplate | None:
    """Для неизвестного типа возвращает ближайший шаблон или None."""
    return TEMPLATES.get(doc_type)
