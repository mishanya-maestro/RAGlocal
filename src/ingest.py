
import config
import database
import generate_fulltext as ft_rebuild
from parser import CODE_NAMES, parse_articles


def collect_articles():
    data_dir = config.PROJECT_ROOT / "data"
    all_articles = []
    for name in CODE_NAMES:
        fp = data_dir / f"{name}.txt"
        if not fp.is_file():
            print(f"Пропуск {name}.txt (нет файла)")
            continue
        articles = parse_articles(str(fp))
        print(f"{name}: {len(articles)} статей")
        all_articles.extend(articles)
    return all_articles


def main():
    print("[1/3] Перестроение fulltext.db и FTS5...")
    ft_rebuild.rebuild_fulltext()

    print("[2/3] Сбор статей для Chroma...")
    articles = collect_articles()
    print(f"  Всего статей для индексации: {len(articles)}")

    print("[3/3] Индексация в Chroma (коллекция пересоздаётся)...")
    database.add_articles(articles, reset_collection=True)
    print("Готово.")


if __name__ == "__main__":
    main()
