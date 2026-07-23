"""Лёгкая переформулировка пользовательского запроса для retrieval.

Без LLM: только шаблоны, юридические синонимы и очистка шума.
Исходный вопрос пользователя для генерации ответа не заменяется.
"""

from __future__ import annotations

import re
from functools import lru_cache


# Разговорные/бытовые → термины, которые реально встречаются в корпусе.
_TERM_REPLACEMENTS: list[tuple[str, str]] = [
    (r"\bмитинг(?:и|а|ов|у|ом)?\b", "массовое мероприятие"),
    (r"\bпикет(?:ы|а|ов|у|ом)?\b", "массовое мероприятие"),
    (r"\bшествие(?:м|й|я)?\b", "массовое мероприятие"),
    (r"\bдемонстраци(?:я|и|ей|ю)\b", "массовое мероприятие"),
    (r"\bжалоб(?:а|ы|у|ой|е)\b", "обращение"),
    (r"\bжаловаться\b", "подать обращение"),
    (r"\bпрописк(?:а|и|у|ой)\b", "место жительства"),
    (r"\bпаспорт(?:а|у|ом|е)?\b", "документ удостоверяющий личность"),
    (r"\bокруга?\b", "избирательный округ"),
    (r"\bучаст(?:ок|ка|ке|ку|ком|ки)\b", "избирательный участок"),
    (r"\bкомисси(?:я|и|ю|ей)\b", "избирательная комиссия"),
    (r"\bбюллетен(?:ь|я|ю|ем|и)\b", "избирательный бюллетень"),
    (r"\bизбирком(?:а|у|ом|е)?\b", "избирательная комиссия"),
]

# Шум, который только мешает FTS/эмбеддингам.
_FILLERS = [
    r"\bну типа\b",
    r"\bкороче говоря\b",
    r"\bкороче\b",
    r"\bтипа\b",
    r"\bвообще\b",
    r"\bпросто\b",
    r"\bслушай\b",
    r"\bсмотри\b",
    r"\bпонимаешь\b",
    r"\bзнаешь\b",
    r"\bладно\b",
    r"\bкстати\b",
    r"\bну\b",
]

# Типовые вопросы → уже хорошие поисковые формулировки.
_TEMPLATES: list[tuple[list[str], str]] = [
    (
        [
            r"право голосовать",
            r"кто может голосовать",
            r"возраст для голосования",
            r"с какого возраста голосуют",
        ],
        "кто имеет право участвовать в голосовании",
    ),
    (
        [
            r"как стать кандидатом",
            r"регистрация кандидата",
            r"выдвижение кандидата",
            r"подача документов кандидата",
        ],
        "порядок выдвижения и регистрации кандидата в депутаты",
    ),
    (
        [
            r"как провести митинг",
            r"согласование митинга",
            r"разрешение на митинг",
            r"порядок проведения митинга",
            r"как организовать митинг",
            r"где нельзя проводить массовые",
            r"в каких местах нельзя",
        ],
        "порядок согласования и проведения массового мероприятия места запрета",
    ),
    (
        [
            r"как написать жалобу",
            r"куда жаловаться",
            r"порядок обращения",
            r"как подать обращение",
        ],
        "порядок рассмотрения обращений граждан и юридических лиц",
    ),
    (
        [
            r"как голосовать",
            r"порядок голосования",
            r"что нужно для голосования",
            r"какие документы для голосования",
        ],
        "порядок голосования на избирательном участке документы для голосования",
    ),
    (
        [
            r"права человека",
            r"основные права",
            r"свободы граждан",
            r"конституционные права",
        ],
        "права и свободы граждан Конституция Республики Беларусь",
    ),
    (
        [
            r"как стать наблюдателем",
            r"права наблюдателя",
            r"кто может быть наблюдателем",
        ],
        "порядок назначения наблюдателей права наблюдателей",
    ),
    (
        [
            r"сроки агитации",
            r"когда начинается агитация",
            r"когда заканчивается агитация",
        ],
        "сроки проведения предвыборной агитации",
    ),
]


def _normalize(text: str) -> str:
    text = (text or "").strip()
    text = re.sub(r"\s+", " ", text)
    return text


def _strip_fillers(text: str) -> str:
    result = text
    for pattern in _FILLERS:
        result = re.sub(pattern, " ", result, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", result).strip()


def _apply_terms(text: str) -> str:
    result = text
    for pattern, replacement in _TERM_REPLACEMENTS:
        result = re.sub(pattern, replacement, result, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", result).strip()


def _match_template(text: str) -> str | None:
    low = text.lower()
    for patterns, formal in _TEMPLATES:
        for pattern in patterns:
            if re.search(pattern, low, flags=re.IGNORECASE):
                return formal
    return None


@lru_cache(maxsize=256)
def formalize_query(question: str) -> str:
    """Вернуть поисковую формулировку. Пустой ввод → пустая строка."""
    original = _normalize(question)
    if not original:
        return ""

    template = _match_template(original)
    if template:
        return template

    cleaned = _strip_fillers(original)
    cleaned = _apply_terms(cleaned)
    cleaned = cleaned.rstrip("?.!;,").strip()
    return cleaned or original


_formalizer = None


def get_formalizer():
    """Совместимость со старым API файла."""
    global _formalizer
    if _formalizer is None:
        _formalizer = QuestionFormalizer()
    return _formalizer


class QuestionFormalizer:
    """Тонкая обёртка: только лёгкая formalize() для retrieval."""

    def formalize(self, question: str) -> str:
        return formalize_query(question)
