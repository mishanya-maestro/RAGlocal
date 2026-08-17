"""Парсер юридических текстов на статьи.

Единый источник правды для построения fulltext.db и Chroma.
Поддерживает номера статей с подчёркиванием (`11_1`, `42_1`, ...).
"""

import os
import re

CODE_NAMES = {
    "IZKrb": "Избирательный кодекс РБ",
    "KONrb": "Конституция РБ",
    "OGOrb": "Закон об основах гражданского общества",
    "OMMrb": "Закон о массовых мероприятиях",
    "ONSrb": "Закон о Национальном собрании РБ",
    "OOGrb": "Закон об обращениях граждан и юридических лиц",
    "OOOrb": "Закон об общественных объединениях",
    "OPPrb": "Закон о политических партиях",
    "OVNrb": "Закон о Всебелорусском народном собрании",
    "SMIrb": "Закон о средствах массовой информации",
    "SNGrb": "Закон о присоединении РБ к Конвенции СНГ о демократических выборах",
    # Правовые акты для анализа документов (placeholder — заменить на официальные тексты)
    "GPKrb": "Гражданский процессуальный кодекс РБ",
    "TKrb": "Трудовой кодекс РБ",
    "GKrb": "Гражданский кодекс РБ",
    "ZPPPT": "Закон о защите прав потребителей",
}

_ARTICLE_NUMBER = r"\d+(?:_\d+)?"
ARTICLE_PATTERN = re.compile(
    rf"Статья\s+({_ARTICLE_NUMBER})\.\s*(.*?)(?=Статья\s+{_ARTICLE_NUMBER}\.|$)",
    re.DOTALL,
)


def _normalize(content: str) -> str:
    content = content.strip()
    # схлопываем 3+ пустых строк, но сохраняем абзацы (двойной \n).
    content = re.sub(r"\n{3,}", "\n\n", content)
    # уплотняем повторяющиеся пробелы в пределах строки.
    content = re.sub(r"[ \t]+", " ", content)
    return content


def parse_articles(filepath):
    filename = os.path.basename(filepath).replace(".txt", "")
    code_name = CODE_NAMES.get(filename, "Неизвестный кодекс")

    with open(filepath, "r", encoding="utf-8") as f:
        text = f.read()

    articles = []
    for number, content in ARTICLE_PATTERN.findall(text):
        clean = _normalize(content)
        articles.append(
            {
                "number": number,
                "code": code_name,
                "short_code": filename,
                "text": f"Статья {number}. {clean}",
            }
        )
    return articles


def list_corpus_files(data_dir):
    out = []
    for code_short in CODE_NAMES:
        fp = os.path.join(str(data_dir), f"{code_short}.txt")
        if os.path.isfile(fp):
            out.append((code_short, fp))
    return out
