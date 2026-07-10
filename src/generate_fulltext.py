"""Полный rebuild fulltext.db и FTS5 из data/*.txt.

Безопасно перезапускаемый: пересоздаёт таблицу, чтобы избежать дубликатов.
Парсер используется общий — из parser.py.
"""

import sqlite3

import config
import fts_index
from parser import CODE_NAMES, parse_articles


def rebuild_fulltext():
    con = sqlite3.connect(str(config.FULLTEXT_DB))
    cur = con.cursor()
    cur.execute("DROP TABLE IF EXISTS fulltext")
    cur.execute(
        "CREATE TABLE fulltext("
        "id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "code TEXT NOT NULL,"
        "number TEXT NOT NULL,"
        "text TEXT NOT NULL,"
        "UNIQUE(code, number)"
        ")"
    )
    con.commit()

    data_dir = config.PROJECT_ROOT / "data"
    total = 0
    for name in CODE_NAMES:
        fp = data_dir / f"{name}.txt"
        if not fp.is_file():
            print(f"Пропуск {name}.txt (нет файла)")
            continue
        articles = parse_articles(str(fp))
        for art in articles:
            cur.execute(
                "INSERT OR REPLACE INTO fulltext(code, number, text) VALUES (?, ?, ?)",
                (art["code"], art["number"], art["text"]),
            )
        con.commit()
        print(f"{name}: {len(articles)} статей")
        total += len(articles)
    con.close()

    fts_index.ensure_fts_index(force_rebuild=True)
    print(f"Готово. fulltext + FTS индекс обновлены. Всего статей: {total}")


if __name__ == "__main__":
    rebuild_fulltext()
