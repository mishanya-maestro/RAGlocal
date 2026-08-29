"""Чек-листы compliance-проверок и генерация целевых поисковых запросов."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class CheckItem:
    """Пункт чек-листа для проверки документа."""

    id: str
    name: str
    description: str
    # Поисковые запросы, по которым ищем релевантные статьи.
    search_queries: list[str] = field(default_factory=list)
    # Обязательные поля, которые должны присутствовать в документе.
    required_clauses: list[str] = field(default_factory=list)
    # Кодексы/законы, в которых искать нормы.
    relevant_codes: list[str] = field(default_factory=list)
    # Вес риска по умолчанию, если LLM не определил severity.
    default_severity: str = "важно"


# short codes кодексов, используемых в проекте.
CODES_CONTRACT = ["GKrb", "ZPPPT"]
CODES_CLAIM = ["GPKrb"]
CODES_ORDER = ["TKrb"]
CODES_LABOR_CONTRACT = ["TKrb"]


CHECKLISTS: dict[str, list[CheckItem]] = {
    "contract": [
        CheckItem(
            id="c_parties",
            name="Стороны договора",
            description="Проверить, что указаны полные реквизиты сторон: наименование, ИНН/УНП, адрес, должность подписанта.",
            search_queries=["реквизиты сторон договора"],
            required_clauses=["наименование", "адрес", "УНП", "ИНН", "в лице"],
            relevant_codes=CODES_CONTRACT,
            default_severity="критично",
        ),
        CheckItem(
            id="c_subject",
            name="Предмет договора",
            description="Проверить чёткость и непротиворечивость предмета договора.",
            search_queries=["существенные условия договора"],
            required_clauses=["предмет договора"],
            relevant_codes=CODES_CONTRACT,
            default_severity="критично",
        ),
        CheckItem(
            id="c_price",
            name="Цена и расчёты",
            description="Проверить наличие цены, порядка и сроков оплаты, валюты.",
            search_queries=["цена договора и порядок оплаты"],
            required_clauses=["цена", "стоимость", "оплата"],
            relevant_codes=CODES_CONTRACT,
            default_severity="критично",
        ),
        CheckItem(
            id="c_term",
            name="Срок действия",
            description="Проверить наличие срока действия договора и порядок пролонгации/расторжения.",
            search_queries=["срок действия и расторжение договора"],
            required_clauses=["срок действия", "расторжение"],
            relevant_codes=CODES_CONTRACT,
            default_severity="важно",
        ),
        CheckItem(
            id="c_liability",
            name="Ответственность и неустойка",
            description="Проверить размер неустойки, ответственность сторон, Ограничение неустойки разумностью.",
            search_queries=["неустойка и ответственность сторон"],
            required_clauses=["неустойка", "ответственность", "штраф", "пени"],
            relevant_codes=CODES_CONTRACT,
            default_severity="важно",
        ),
        CheckItem(
            id="c_form",
            name="Форма договора",
            description="Проверить, соблюдена ли письменная форма для договоров, где она обязательна.",
            search_queries=["письменная форма договора"],
            required_clauses=["договор", "подписали"],
            relevant_codes=CODES_CONTRACT,
            default_severity="важно",
        ),
        CheckItem(
            id="c_consumer",
            name="Защита прав потребителей",
            description="Если договор с физлицом-потребителем — проверить информацию о товаре, гарантийный срок, недействительные условия.",
            search_queries=["права потребителя информация о товаре"],
            required_clauses=[],
            relevant_codes=CODES_CONTRACT + ["ZPPPT"],
            default_severity="важно",
        ),
    ],
    "claim": [
        CheckItem(
            id="cl_court",
            name="Наименование суда",
            description="Проверить указание наименования суда, в который подано заявление.",
            search_queries=["наименование суда и подсудность искового заявления"],
            required_clauses=["в суд", "наименование суда"],
            relevant_codes=CODES_CLAIM,
            default_severity="критично",
        ),
        CheckItem(
            id="cl_parties",
            name="Стороны процесса",
            description="Проверить указание истца, ответчика, их места жительства/нахождения.",
            search_queries=["истец и ответчик в исковом заявлении"],
            required_clauses=["истец", "ответчик"],
            relevant_codes=CODES_CLAIM,
            default_severity="критично",
        ),
        CheckItem(
            id="cl_subject",
            name="Предмет и основание иска",
            description="Проверить чёткость предмета иска и ссылку на правовые основания.",
            search_queries=["предмет и основание иска"],
            required_clauses=["предмет иска", "основание", "требования"],
            relevant_codes=CODES_CLAIM,
            default_severity="критично",
        ),
        CheckItem(
            id="cl_signature",
            name="Подпись и доверенность",
            description="Проверить подпись истца/представителя и приложение доверенности.",
            search_queries=["подпись истца и доверенность в иске"],
            required_clauses=["подпись", "подписал", "доверенность"],
            relevant_codes=CODES_CLAIM,
            default_severity="критично",
        ),
        CheckItem(
            id="cl_fee",
            name="Государственная пошлина",
            description="Проверить уплату государственной пошлины или основание освобождения.",
            search_queries=["государственная пошлина при подаче иска"],
            required_clauses=["государственная пошлина", "пошлина"],
            relevant_codes=CODES_CLAIM,
            default_severity="важно",
        ),
        CheckItem(
            id="cl_attachments",
            name="Приложения",
            description="Проверить перечень документов, прилагаемых к исковому заявлению.",
            search_queries=["приложения к исковому заявлению"],
            required_clauses=["приложение", "прилагаю"],
            relevant_codes=CODES_CLAIM,
            default_severity="важно",
        ),
        CheckItem(
            id="cl_limitation",
            name="Исковая давность",
            description="Проверить, не пропущен ли срок исковой давности.",
            search_queries=["исковая давность"],
            required_clauses=[],
            relevant_codes=CODES_CLAIM,
            default_severity="важно",
        ),
    ],
    "order": [
        CheckItem(
            id="o_basis",
            name="Основание приказа",
            description="Проверить указание правового и фактического основания издания приказа.",
            search_queries=["основание издания приказа"],
            required_clauses=["основании", "в соответствии", "руководствуясь"],
            relevant_codes=CODES_ORDER,
            default_severity="важно",
        ),
        CheckItem(
            id="o_issuer",
            name="Подпись и должность",
            description="Проверить должность и подпись лица, издавшего приказ.",
            search_queries=["подпись и должность в приказе"],
            required_clauses=["приказываю", "подпись", "руководитель"],
            relevant_codes=CODES_ORDER,
            default_severity="критично",
        ),
        CheckItem(
            id="o_date",
            name="Дата и номер",
            description="Проверить наличие даты и номера приказа.",
            search_queries=["дата и номер приказа"],
            required_clauses=["приказ", "от", "№", "номер"],
            relevant_codes=CODES_ORDER,
            default_severity="критично",
        ),
        CheckItem(
            id="o_acknowledgement",
            name="Ознакомление работника",
            description="Для кадровых приказов — проверить факт ознакомления работника под подпись.",
            search_queries=["ознакомление работника с приказом"],
            required_clauses=["ознакомлен", "подпись", "с приказом"],
            relevant_codes=CODES_ORDER,
            default_severity="важно",
        ),
    ],
    "labor_contract": [
        CheckItem(
            id="lc_parties",
            name="Стороны трудового договора",
            description="Проверить указание работодателя и работника, их реквизитов.",
            search_queries=["стороны трудового договора"],
            required_clauses=["работодатель", "работник"],
            relevant_codes=CODES_LABOR_CONTRACT,
            default_severity="критично",
        ),
        CheckItem(
            id="lc_position",
            name="Должность и место работы",
            description="Проверить указание должности, места работы, структурного подразделения.",
            search_queries=["должность и место работы в трудовом договоре"],
            required_clauses=["должность", "место работы"],
            relevant_codes=CODES_LABOR_CONTRACT,
            default_severity="критично",
        ),
        CheckItem(
            id="lc_salary",
            name="Заработная плата",
            description="Проверить размер заработной платы, даты выплаты, не ниже минимума.",
            search_queries=["заработная плата в трудовом договоре"],
            required_clauses=["заработная плата", "оплата труда"],
            relevant_codes=CODES_LABOR_CONTRACT,
            default_severity="критично",
        ),
        CheckItem(
            id="lc_probation",
            name="Испытательный срок",
            description="Проверить, не превышает ли испытательный срок максимум (3 месяца, 6 для руководителей).",
            search_queries=["испытательный срок в трудовом договоре"],
            required_clauses=["испытательный срок"],
            relevant_codes=CODES_LABOR_CONTRACT,
            default_severity="важно",
        ),
        CheckItem(
            id="lc_work_hours",
            name="Режим рабочего времени",
            description="Проверить указание режима рабочего времени и времени отдыха.",
            search_queries=["режим рабочего времени в трудовом договоре"],
            required_clauses=["режим рабочего времени", "рабочее время"],
            relevant_codes=CODES_LABOR_CONTRACT,
            default_severity="важно",
        ),
        CheckItem(
            id="lc_leave",
            name="Отпуск",
            description="Проверить указание ежегодного оплачиваемого отпуска.",
            search_queries=["ежегодный оплачиваемый отпуск"],
            required_clauses=["отпуск"],
            relevant_codes=CODES_LABOR_CONTRACT,
            default_severity="рекомендация",
        ),
    ],
}


def get_checklist(doc_type: str) -> list[CheckItem]:
    """Возвращает чек-лист для типа документа."""
    return CHECKLISTS.get(doc_type, [])


def get_relevant_codes(doc_type: str) -> list[str]:
    """Возвращает список short codes кодексов, релевантных для типа документа."""
    codes: set[str] = set()
    for item in CHECKLISTS.get(doc_type, []):
        codes.update(item.relevant_codes)
    return sorted(codes)


def generate_search_queries(doc_type: str) -> list[str]:
    """Все поисковые запросы для данного типа документа."""
    queries: list[str] = []
    for item in CHECKLISTS.get(doc_type, []):
        queries.extend(item.search_queries)
    return queries


def get_document_type_label(doc_type: str) -> str:
    """Человекочитаемая метка типа документа."""
    from document_parser import _TYPE_LABELS

    return _TYPE_LABELS.get(doc_type, "Прочий документ")
