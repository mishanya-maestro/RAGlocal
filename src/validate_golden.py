from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

_SRC = Path(__file__).resolve().parent
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from parser import CODE_NAMES  # noqa: E402

PROJECT_ROOT = _SRC.parent
DATA_DIR = PROJECT_ROOT / "data"
GOLDEN = DATA_DIR / "golden_eval.json"

ARTICLE_RE = re.compile(r"Стать[ья]\s+(\d+(?:_\d+)?)\.")


def collect_articles_per_code() -> dict[str, set[str]]:
    result: dict[str, set[str]] = {}
    name_to_short = {full: short for short, full in CODE_NAMES.items()}
    for full_name, short in name_to_short.items():
        path = DATA_DIR / f"{short}.txt"
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        nums = set(ARTICLE_RE.findall(text))
        result[full_name] = nums
    return result


def main() -> int:
    with open(GOLDEN, encoding="utf-8") as f:
        data = json.load(f)

    articles = collect_articles_per_code()
    known_codes = set(articles.keys())

    cases = data.get("cases", [])
    errors: list[str] = []
    per_code_counts: Counter = Counter()

    for idx, case in enumerate(cases):
        gold = case.get("gold", [])
        if not gold:
            errors.append(f"case[{idx}]: пустой gold для query={case.get('query')!r}")
            continue
        for g in gold:
            code = g.get("code")
            num = str(g.get("number"))
            if code not in known_codes:
                errors.append(
                    f"case[{idx}]: неизвестный код {code!r} "
                    f"(нет в parser.CODE_NAMES или нет файла data/*.txt)"
                )
                continue
            if num not in articles[code]:
                errors.append(
                    f"case[{idx}]: статья {num!r} не найдена в {code!r}"
                )
                continue
            per_code_counts[code] += 1

    print(f"Всего кейсов: {len(cases)}")
    print("Покрытие по кодексам:")
    for code in sorted(known_codes):
        n = per_code_counts.get(code, 0)
        print(f"  {n:>3}  {code}")

    if errors:
        print(f"\nОшибки ({len(errors)}):")
        for e in errors:
            print(" -", e)
        return 1

    print("\nOK: все ссылки на статьи валидны.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
