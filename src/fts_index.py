

import re
import sqlite3

import config

_FTS_TABLE = "articles_fts"

# Базовый стоп-словарь русского для FTS-запросов. Цель — снизить шум по местоимениям
# и общим словам, оставив юридически значимые токены.
RU_STOPWORDS = {
    "а", "ах", "без", "более", "больше", "будет", "будем", "будут", "будь", "будьте",
    "был", "была", "были", "было", "быть",
    "в", "вам", "вами", "вас", "ваш", "ваша", "ваше", "ваши", "ведь", "везде",
    "весь", "вкруг", "вместо", "внутри", "во", "вокруг", "вон", "вот", "впрочем",
    "все", "всё", "всего", "всем", "всеми", "всех", "всею", "всю", "вся",
    "вы", "где", "да", "давай", "давать", "даже", "далее", "далеко", "дальше",
    "для", "до", "его", "ей", "ему", "если", "есть", "ещё", "еще", "же", "за",
    "зачем", "здесь", "и", "из", "из-за", "из-под", "или", "им", "ими",
    "иногда", "их", "к", "кажется", "как", "какая", "какие", "каких", "какое",
    "какой", "какому", "когда", "кого", "коли", "конечно",
    "которая", "которого", "которое", "которой", "котором", "которому", "которые",
    "который", "которым", "которых", "кому", "кто", "куда", "ли", "либо", "лишь",
    "люди", "мало", "между", "меня", "менее", "мне", "много", "мной", "мною", "мог",
    "могу", "может", "можно", "можешь", "мой", "моя", "моё", "мои",
    "мы", "на", "над", "надо", "нам", "нами",
    "нас", "наш", "наша", "наше", "наши", "не", "него", "нее", "неё", "ней", "нельзя",
    "немного", "нет", "нечего", "ним", "ними", "них", "ничего", "ничто",
    "но", "ну", "о", "об", "оба", "обе", "обо", "оно", "она", "они",
    "от", "очень", "перед", "по", "под", "поэтому", "при",
    "про", "просто", "против", "путём", "путем", "разве", "ранее", "раньше",
    "с", "сам", "сама", "сами", "само", "сейчас", "сих", "сколько", "снова", "со",
    "собой", "собою", "сюда", "так", "также", "такие", "такое", "такой",
    "там", "те", "тебе", "тебя", "тем", "теми", "тех", "то", "тобой", "тобою", "того",
    "тогда", "тоже", "только", "том", "тому", "тот", "тою", "ту", "тут", "ты",
    "у", "уже", "хоть", "хотя", "что", "чтоб", "чтобы", "чуть", "эта", "эти", "этим",
    "этими", "этих", "это", "этого", "этой", "этом", "этому", "этот", "эту",
    "я",
}


def _connect():
    return sqlite3.connect(str(config.FULLTEXT_DB))


def _create_fts(cur):
    cur.execute(
        f"""
        CREATE VIRTUAL TABLE {_FTS_TABLE} USING fts5(
            code UNINDEXED,
            number UNINDEXED,
            text,
            tokenize = 'unicode61 remove_diacritics 2'
        )
        """
    )


def ensure_fts_index(force_rebuild: bool = False):
    """Создаёт или перестраивает FTS-таблицу по `fulltext` (полное соответствие)."""
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (_FTS_TABLE,),
        )
        exists = cur.fetchone() is not None

        try:
            cur.execute("SELECT COUNT(*) FROM fulltext")
            full_count = cur.fetchone()[0] or 0
        except sqlite3.OperationalError:
            full_count = 0

        if not exists:
            _create_fts(cur)
            if full_count:
                cur.execute(
                    f"INSERT INTO {_FTS_TABLE}(code, number, text) "
                    f"SELECT code, number, text FROM fulltext"
                )
            conn.commit()
            return

        cur.execute(f"SELECT COUNT(*) FROM {_FTS_TABLE}")
        fts_count = cur.fetchone()[0] or 0

        if force_rebuild or (full_count and fts_count != full_count):
            cur.execute(f"DROP TABLE {_FTS_TABLE}")
            _create_fts(cur)
            if full_count:
                cur.execute(
                    f"INSERT INTO {_FTS_TABLE}(code, number, text) "
                    f"SELECT code, number, text FROM fulltext"
                )
            conn.commit()
    finally:
        conn.close()


def _escape_fts_token(t: str) -> str:
    return t.replace('"', '""')


def _tokenize(query: str) -> list[str]:
    parts = re.findall(r"[\w\-]+", query, flags=re.UNICODE)
    return [p.lower() for p in parts if p]


def _build_match_query(user_query: str, max_tokens: int = 16) -> str | None:
    tokens = _tokenize(user_query)
    meaningful = [t for t in tokens if len(t) >= 3 and t not in RU_STOPWORDS]
    if not meaningful:
        meaningful = [t for t in tokens if len(t) >= 2]
    meaningful = meaningful[:max_tokens]
    if not meaningful:
        return None
    return " OR ".join(f'"{_escape_fts_token(t)}"' for t in meaningful)


def fts_search(query: str, limit: int) -> list[dict]:
    """Возвращает [{code, number, bm25, snippet}] от лучшего совпадения к худшему."""
    match_q = _build_match_query(query)
    if not match_q:
        return []

    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (_FTS_TABLE,),
        )
        if cur.fetchone() is None:
            return []

        try:
            cur.execute(
                f"""
                SELECT code, number, bm25({_FTS_TABLE}) AS r,
                       snippet({_FTS_TABLE}, 2, '«', '»', '…', 18) AS snip
                FROM {_FTS_TABLE}
                WHERE {_FTS_TABLE} MATCH ?
                ORDER BY r
                LIMIT ?
                """,
                (match_q, limit),
            )
        except sqlite3.OperationalError as e:
            print(f"fts_search MATCH error: {e}; q={match_q!r}")
            return []

        rows = []
        for code, number, bm, snip in cur.fetchall():
            rows.append(
                {
                    "code": code,
                    "number": str(number),
                    "bm25": bm,
                    "snippet": snip or "",
                }
            )
        return rows
    finally:
        conn.close()
