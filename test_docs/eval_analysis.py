"""Оценка качества анализа документов на 10 тестовых примерах."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import requests


BASE_URL = "http://127.0.0.1:5000"
API_URL = f"{BASE_URL}/api/analyze-document"

TEST_CASES: list[dict[str, Any]] = [
    {
        "file": "01_contract_missing_price.txt",
        "doc_type": "contract",
        "expected": {"c_price"},
        "description": "Договор без цены",
    },
    {
        "file": "02_contract_multiple_issues.txt",
        "doc_type": "contract",
        "expected": {"c_parties", "c_price", "c_liability", "c_term", "c_form"},
        "description": "Договор с множеством проблем",
    },
    {
        "file": "03_labor_contract_missing_leave.txt",
        "doc_type": "labor_contract",
        "expected": {"lc_leave"},
        "description": "Трудовой договор без отпуска",
    },
    {
        "file": "04_labor_contract_multiple_issues.txt",
        "doc_type": "labor_contract",
        "expected": {"lc_parties", "lc_position", "lc_salary", "lc_probation"},
        "description": "Трудовой договор с множеством проблем",
    },
    {
        "file": "05_claim_good.txt",
        "doc_type": "claim",
        "expected": {"cl_fee"},
        "description": "Исковое заявление без доказательства пошлины",
    },
    {
        "file": "06_claim_multiple_issues.txt",
        "doc_type": "claim",
        "expected": {"cl_court", "cl_parties", "cl_subject", "cl_signature", "cl_fee", "cl_attachments"},
        "description": "Исковое заявление с множеством проблем",
    },
    {
        "file": "07_order_good.txt",
        "doc_type": "order",
        "expected": {"o_acknowledgement"},
        "description": "Приказ без ознакомления работника",
    },
    {
        "file": "08_order_multiple_issues.txt",
        "doc_type": "order",
        "expected": {"o_basis", "o_issuer", "o_date", "o_acknowledgement"},
        "description": "Приказ с множеством проблем",
    },
    {
        "file": "09_consumer_contract_issues.txt",
        "doc_type": "contract",
        "expected": {"c_consumer", "c_form"},
        "description": "Потребительский договор с проблемами",
    },
    {
        "file": "10_rent_contract_issues.txt",
        "doc_type": "contract",
        "expected": {"c_term", "c_form"},
        "description": "Договор аренды с проблемами",
    },
]


# Ключевые слова для сопоставления expected check_id с issues, которые LLM/шаблон возвращают без check_id.
CHECK_KEYWORDS: dict[str, set[str]] = {
    "c_parties": {"сторон", "продавец", "покупатель", "работодатель", "работник", "реквизит", "в лице", "преамбула"},
    "c_subject": {"предмет договора", "предметом договора", "товар", "передать в собственность"},
    "c_price": {"цена", "стоимость", "оплат", "расчёт", "цены договора"},
    "c_term": {"срок действия", "действует до", "пролонгация", "срок действия договора"},
    "c_liability": {"ответственность", "неустойка", "штраф", "пени", "ответственность сторон"},
    "c_form": {"письменная форма", "форма договора", "подпись", "подписи сторон", "заключен в письменной"},
    "c_consumer": {"потребитель", "физическое лицо", "защита прав", "потребителей"},
    "lc_leave": {"отпуск", "ежегодный", "оплачиваемый", "ежегодный отпуск"},
    "lc_parties": {"сторон", "работодатель", "работник", "реквизит", "в лице"},
    "lc_position": {"должность", "место работы", "функция", "должности"},
    "lc_salary": {"оплата", "зарплата", "заработная плата", "тариф", "размер оплаты"},
    "lc_probation": {"испытательный срок", "испытани", "испытательный"},
    "cl_court": {"суд", "наименование суда", "подсудность", "в суд"},
    "cl_parties": {"истец", "ответчик", "сторон", "истца", "ответчика"},
    "cl_subject": {"предмет иска", "исковое требование", "требовани", "предметом иска"},
    "cl_signature": {"подпись", "истца", "подпись истца", "подписано"},
    "cl_fee": {"пошлина", "госпошлина", "судебный сбор", "пошлины"},
    "cl_attachments": {"приложени", "доказательств", "приложения"},
    "o_acknowledgement": {"ознакомлен", "подпись", "распис", "работник ознакомлен"},
    "o_basis": {"основани", "приказ", "прием", "увольнени", "основанием приказа"},
    "o_date": {"дата", "датой приказа"},
    "o_issuer": {"издал", "руководитель", "директор", "приказал", "подпись руководителя"},
}


# Сопоставление id разделов шаблона с check_id (для structural missing_sections).
SECTION_TO_CHECK: dict[str, str] = {
    "parties": "c_parties",
    "subject": "c_subject",
    "price": "c_price",
    "term": "c_term",
    "liability": "c_liability",
    "form": "c_form",
    "consumer": "c_consumer",
    "court": "cl_court",
    "plaintiff": "cl_parties",
    "defendant": "cl_parties",
    "claim_value": "cl_fee",
    "subject_claim": "cl_subject",
    "grounds": "cl_subject",
    "demands": "cl_subject",
    "evidence": "cl_attachments",
    "attachments": "cl_attachments",
    "signature": "cl_signature",
    "employee": "lc_parties",
    "employer": "lc_parties",
    "position": "lc_position",
    "salary": "lc_salary",
    "workplace": "lc_position",
    "probation": "lc_probation",
    "leave": "lc_leave",
    "basis": "o_basis",
    "employee_name": "o_acknowledgement",
    "date": "o_date",
    "issuer": "o_issuer",
    "acknowledgement": "o_acknowledgement",
}


def analyze_file(path: Path) -> dict[str, Any]:
    with open(path, "rb") as f:
        response = requests.post(API_URL, files={"document": (path.name, f, "text/plain")}, timeout=300)
    response.raise_for_status()
    return response.json()


def _issue_matches_check(issue: dict[str, Any], check_id: str) -> bool:
    """Проверяет, относится ли issue к ожидаемому check_id по check_id/section_id/тексту."""
    if issue.get("check_id") == check_id:
        return True

    # Сопоставление по id раздела (шаблон/структура).
    section_id = (issue.get("section_id") or "").lower().strip()
    mapped = SECTION_TO_CHECK.get(section_id)
    if mapped == check_id:
        return True

    keywords = CHECK_KEYWORDS.get(check_id, set())
    text = " ".join(
        str(issue.get(k, "")) for k in ("section_name", "section_id", "issue", "suggestion", "norm")
    ).lower()
    return any(kw.lower() in text for kw in keywords)


def evaluate():
    results: list[dict[str, Any]] = []
    total_tp = 0
    total_fp = 0
    total_fn = 0
    total_expected = 0
    total_actual = 0
    total_time = 0.0
    type_correct = 0
    structural_correct = 0
    structural_total = 0

    for case in TEST_CASES:
        path = Path(__file__).with_name(case["file"])
        print(f"\n[>] {case['file']} - {case['description']}")
        start = time.time()
        try:
            data = analyze_file(path)
        except Exception as e:
            print(f"  ERROR: {e}")
            results.append({"file": case["file"], "error": str(e)})
            continue
        elapsed = round(time.time() - start, 1)
        total_time += elapsed

        result = data.get("result", {})
        issues = result.get("issues", [])
        summary = result.get("summary", {})
        detected_type = result.get("doc_type", "unknown")
        structure = result.get("structure", {})

        expected = set(case["expected"])

        # Определяем, какие check_id из expected покрыты фактическими issues.
        matched: set[str] = set()
        for check_id in expected:
            if any(_issue_matches_check(issue, check_id) for issue in issues):
                matched.add(check_id)

        # Фактические check_id, которые удалось сопоставить (для FP).
        actual_matched: set[str] = set()
        for issue in issues:
            for check_id in expected | set(CHECK_KEYWORDS.keys()):
                if _issue_matches_check(issue, check_id):
                    actual_matched.add(check_id)
                    break

        tp = len(matched)
        fp = len(actual_matched - expected)
        fn = len(expected - matched)

        total_tp += tp
        total_fp += fp
        total_fn += fn
        total_expected += len(expected)
        total_actual += len(actual_matched)

        precision = tp / (tp + fp) if (tp + fp) else 0.0
        recall = tp / (tp + fn) if (tp + fn) else 0.0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0

        # Structural completeness: required sections found / total required.
        all_sections = structure.get("found_sections", []) + structure.get("missing_sections", [])
        required_sections = [s for s in all_sections if s.get("required")]
        total_required = len(required_sections)
        found_required = sum(1 for s in required_sections if s.get("found"))
        structural_correct += found_required
        structural_total += total_required
        structural_rate = found_required / total_required if total_required else 0.0

        if detected_type == case["doc_type"]:
            type_correct += 1

        row = {
            "file": case["file"],
            "expected_type": case["doc_type"],
            "detected_type": detected_type,
            "type_correct": detected_type == case["doc_type"],
            "expected_count": len(expected),
            "actual_count": len(actual_matched),
            "tp": tp,
            "fp": fp,
            "fn": fn,
            "precision": round(precision, 2),
            "recall": round(recall, 2),
            "f1": round(f1, 2),
            "structural_rate": round(structural_rate, 2),
            "found_required": found_required,
            "total_required": total_required,
            "time": elapsed,
            "summary": summary,
            "matched_checks": sorted(matched),
            "expected_checks": sorted(expected),
            "missed_checks": sorted(expected - matched),
            "extra_checks": sorted(actual_matched - expected),
        }
        results.append(row)

        print(f"  type: {detected_type} | time: {elapsed}s | issues: {len(issues)}")
        print(f"  precision: {precision:.2f} | recall: {recall:.2f} | f1: {f1:.2f}")
        print(f"  structure: {found_required}/{total_required} required sections found")
        if row["missed_checks"]:
            print(f"  missed: {row['missed_checks']}")
        if row["extra_checks"]:
            print(f"  extra: {row['extra_checks']}")

    overall_precision = total_tp / (total_tp + total_fp) if (total_tp + total_fp) else 0.0
    overall_recall = total_tp / (total_tp + total_fn) if (total_tp + total_fn) else 0.0
    overall_f1 = (
        2 * overall_precision * overall_recall / (overall_precision + overall_recall)
        if (overall_precision + overall_recall)
        else 0.0
    )
    type_accuracy = type_correct / len(TEST_CASES) if TEST_CASES else 0.0
    structural_accuracy = structural_correct / structural_total if structural_total else 0.0
    avg_time = total_time / len(TEST_CASES) if TEST_CASES else 0.0

    report = {
        "overall": {
            "total_expected": total_expected,
            "total_actual": total_actual,
            "true_positives": total_tp,
            "false_positives": total_fp,
            "false_negatives": total_fn,
            "precision": round(overall_precision, 2),
            "recall": round(overall_recall, 2),
            "f1": round(overall_f1, 2),
            "type_accuracy": round(type_accuracy, 2),
            "structural_accuracy": round(structural_accuracy, 2),
            "avg_time_sec": round(avg_time, 1),
        },
        "cases": results,
    }

    out_path = Path(__file__).with_name("eval_report.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print("\n" + "=" * 60)
    print("ИТОГОВЫЕ МЕТРИКИ АНАЛИЗА ДОКУМЕНТОВ")
    print("=" * 60)
    print(f"Кейсов: {len(TEST_CASES)}")
    print(f"Точность определения типа документа: {type_accuracy:.2%}")
    print(f"Структурная полнота (required sections): {structural_accuracy:.2%}")
    print(f"Всего ожидалось проблем: {total_expected}")
    print(f"True positives: {total_tp}")
    print(f"False positives: {total_fp}")
    print(f"False negatives: {total_fn}")
    print(f"Precision: {overall_precision:.2f}")
    print(f"Recall: {overall_recall:.2f}")
    print(f"F1-score: {overall_f1:.2f}")
    print(f"Среднее время анализа: {avg_time:.1f}s")
    print(f"\nОтчёт сохранён: {out_path}")


if __name__ == "__main__":
    evaluate()
