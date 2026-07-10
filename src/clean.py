import re
import os

FILES = [
    "OGOrb","OMMrb","ONSrb","OOGrb","OOOrb","OPPrb","OVNrb","SMIrb","SNGrb"

]


def clean_headers(text):
    """Удаляет всё от ГЛАВА/РАЗДЕЛ до ближайшей Статьи."""
    text = re.sub(r'(?:ГЛАВА|РАЗДЕЛ).*?(?=Статья)', '', text, flags=re.DOTALL)
    return text


def clean_page_numbers(text):
    """Удаляет колонтитулы и номера страниц из PDF."""
    text = re.sub(
        r'^Национальный правовой Интернет-портал.*$',
        '',
        text,
        flags=re.MULTILINE
    )

    # 2. Удаляем строки, состоящие только из 1-3 цифр (номера страниц вроде "14")
    text = re.sub(r'^\d{1,3}$\n?', '', text, flags=re.MULTILINE)

    return text

def fix_article_numbers(text):
    """Исправляет 'Статья 111' на 'Статья 11.1', если нарушена последовательность."""
    pattern = r'Статья\s+(\d+)\.'
    matches = list(re.finditer(pattern, text))

    if not matches:
        return text

    result = []
    last_pos = 0
    prev_num = None

    for match in matches:
        num = int(match.group(1))
        start, end = match.span()

        if prev_num is not None and num > prev_num * 10:
            prev_str = str(prev_num)
            curr_str = str(num)

            if curr_str.startswith(prev_str):
                decimal_part = curr_str[len(prev_str):]
                new_num = f"{prev_num}_{decimal_part}"

                old = f"Статья {num}."
                new = f"Статья {new_num}."

                chunk = text[last_pos:start] + text[start:end].replace(old, new)
                result.append(chunk)
                last_pos = end
                continue

        result.append(text[last_pos:end])
        last_pos = end
        prev_num = num

    result.append(text[last_pos:])
    return ''.join(result)


def process_file(base_name):
    """Обрабатывает один файл: _raw.txt -> .txt"""
    input_path = f"../data/{base_name}_raw.txt"
    output_path = f"../data/{base_name}.txt"

    if not os.path.exists(input_path):
        print(f"[!] Не найден: {input_path}")
        return

    print(f"[*] Обрабатываю: {base_name}_raw.txt -> {base_name}.txt")

    with open(input_path, "r", encoding="utf-8") as f:
        text = f.read()
    text = clean_headers(text)
    text = clean_page_numbers(text)
    text = fix_article_numbers(text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = text.strip()

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(text)

    print(f"[+] Готово: {output_path}")


# Запуск
if __name__ == "__main__":
    for name in FILES:
        process_file(name)

    print("\nВсе файлы обработаны.")