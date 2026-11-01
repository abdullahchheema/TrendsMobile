"""Device identifier validation, independent of Frappe for focused tests."""


def valid_imei(value):
    if not isinstance(value, str) or len(value) != 15 or not value.isascii() or not value.isdigit():
        return False
    if len(set(value)) == 1:
        return False
    digits = [int(d) for d in value]
    return sum((d * 2 - 9 if d * 2 > 9 else d * 2) if i % 2 else d
               for i, d in enumerate(digits)) % 10 == 0
