import re


def fix_article_numbers(text):
    """Исправляет 'Статья 111' на 'Статья 11.1', если это нарушает логику последовательности."""

    # Находим все вхождения "Статья [число]."
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



with open("../data/KONrb1.txt", "r", encoding="utf-8") as f:
    text = f.read()

fixed = fix_article_numbers(text)

with open("../data/KONrb_raw.txt", "w", encoding="utf-8") as f:
    f.write(fixed)

print("Готово. Проверь файл KONrb_fixed.txt")