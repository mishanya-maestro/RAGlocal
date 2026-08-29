"""Инкрементальная индексация новых правовых актов для анализа документов.

Запускаете, если полная индексация (ingest.py) занимает слишком много времени.
Добавляет только GKrb, TKrb, GPKrb, ZPPPT в существующую Chroma-коллекцию.
"""

import config
import database
from parser import CODE_NAMES, parse_articles


NEW_CODES = ["GKrb", "TKrb", "GPKrb", "ZPPPT"]


def main():
    data_dir = config.PROJECT_ROOT / "data"
    articles = []
    for code in NEW_CODES:
        fp = data_dir / f"{code}.txt"
        if not fp.is_file():
            print(f"Пропуск {code}.txt (нет файла)")
            continue
        parsed = parse_articles(str(fp))
        print(f"{code}: {len(parsed)} статей")
        articles.extend(parsed)

    if not articles:
        print("Нет статей для индексации")
        return

    print(f"Добавление {len(articles)} статей в Chroma...")
    database.add_articles(articles, reset_collection=False)
    print("Готово.")


if __name__ == "__main__":
    main()
