"""Проверка входных данных API.

Сервер сам проверяет всё, что приходит от клиента. Проверка в браузере ничего
не гарантирует, потому что запрос можно отправить в обход страницы. Любая
ошибка ввода превращается в ApiError, а тот в ответ 400 с понятным текстом.
"""

from __future__ import annotations

import math
import secrets
from typing import Any

from core.instance import TSPInstance

MIN_CITIES = 3
MAX_CITIES = 50
COORD_LIMIT = 1e9  # слишком большие координаты портят точность вычислений

VARIANTS = ("AS", "MMAS")


class ApiError(Exception):
    """Ошибка, которую нужно показать пользователю. Клиент получает её как JSON {"error", "code"}."""

    def __init__(self, message: str, status: int = 400, code: str = "bad_request"):
        super().__init__(message)
        self.message = message
        self.status = status
        self.code = code


# Проверки отдельных чисел


def _is_number(value: Any) -> bool:
    """Число из JSON: int или float, конечное. Значение bool не подходит, хотя в Python True == 1."""
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def _get_int(data: dict, key: str, label: str, lo: int, hi: int, default: Any) -> Any:
    """Целое из data[key] в диапазоне [lo, hi]. Если значения нет, возвращает default."""
    value = data.get(key)
    if value is None:
        return default
    if isinstance(value, float) and value.is_integer():
        value = int(value)  # JavaScript присылает 20.0, и это тоже целое число
    if isinstance(value, bool) or not isinstance(value, int):
        raise ApiError(f"Параметр «{label}» должен быть целым числом.")
    if not lo <= value <= hi:
        raise ApiError(f"Параметр «{label}» должен быть в диапазоне от {lo} до {hi}.")
    return value


def _get_float(
    data: dict,
    key: str,
    label: str,
    lo: float,
    hi: float,
    default: float,
    lo_open: bool = False,
) -> float:
    """Число из data[key] в диапазоне [lo, hi], а при lo_open в диапазоне (lo, hi]."""
    value = data.get(key)
    if value is None:
        return default
    if not _is_number(value):
        raise ApiError(f"Параметр «{label}» должен быть числом.")
    too_small = value <= lo if lo_open else value < lo
    if too_small or value > hi:
        left = "(" if lo_open else "["
        raise ApiError(f"Параметр «{label}» должен быть в диапазоне {left}{lo:g}; {hi:g}].")
    return float(value)


# Города


def parse_cities(raw: Any) -> list[list[float]]:
    """Проверяет список городов [[x, y], ...] и возвращает его копию.

    Города нумеруются с нуля, как в ядре и в событиях шага муравья.
    Совпадающие города отклоняются: расстояние между ними равно 0,
    а эвристика 1/d делит на это расстояние.
    """
    if not isinstance(raw, list):
        raise ApiError("Поле «cities» должно быть списком пар [x, y].")
    if len(raw) < MIN_CITIES:
        raise ApiError(f"Нужно минимум {MIN_CITIES} города, получено {len(raw)}.")
    if len(raw) > MAX_CITIES:
        raise ApiError(f"Слишком много городов: максимум {MAX_CITIES}, получено {len(raw)}.")

    cities: list[list[float]] = []
    for i, item in enumerate(raw):
        ok = (
            isinstance(item, (list, tuple))
            and len(item) == 2
            and all(_is_number(v) and abs(v) <= COORD_LIMIT for v in item)
        )
        if not ok:
            raise ApiError(f"Город {i}: ожидается пара чисел [x, y] (по модулю до {COORD_LIMIT:g}).")
        cities.append([item[0], item[1]])

    first_seen: dict[tuple, int] = {}
    for i, (x, y) in enumerate(cities):
        if (x, y) in first_seen:
            raise ApiError(
                f"Города {first_seen[(x, y)]} и {i} (нумерация с нуля) "
                "имеют одинаковые координаты."
            )
        first_seen[(x, y)] = i
    return cities


def parse_instance_request(data: dict) -> tuple[TSPInstance, int | None]:
    """Разбирает тело POST /api/instance и возвращает задачу ядра TSPInstance.

    В запросе либо {"cities": [[x, y], ...]}, либо {"n": 20, "seed": 42}.
    Возвращает пару (задача, seed). Для явно заданных городов seed равен None.

    Случайные города строит TSPInstance.random, ту же функцию используют ядро
    и эксперименты. Поэтому одинаковые n и seed дают одну и ту же задачу везде.
    """
    if data.get("cities") is not None:
        cities = parse_cities(data["cities"])  # здесь наши понятные сообщения об ошибках
        try:
            return TSPInstance(cities), None
        except ValueError as err:  # запасная проверка: то, что не пропустило само ядро
            raise ApiError(str(err)) from err

    n = _get_int(data, "n", "n", MIN_CITIES, MAX_CITIES, None)
    if n is None:
        raise ApiError("Укажите либо «cities», либо «n» (число городов).")
    seed = _get_int(data, "seed", "seed", 0, 2**32 - 1, None)
    if seed is None:
        seed = secrets.randbelow(10**6)  # seed выбрал сервер, поэтому вернём его клиенту
    return TSPInstance.random(n, seed), seed


# Параметры алгоритма


def parse_params(data: dict, n_cities: int) -> dict:
    """Проверяет параметры из POST /api/init и подставляет значения по умолчанию.

    Имена параметров зафиксированы в API.md:
    n_ants, alpha, beta, rho, q, n_iter, seed, variant.
    """
    variant = data.get("variant")
    if variant is None:
        variant = "AS"
    if variant not in VARIANTS:
        raise ApiError("Параметр «variant» должен быть \"AS\" или \"MMAS\".")

    return {
        "n_ants": _get_int(data, "n_ants", "число муравьёв (n_ants)", 1, 500, n_cities),
        "alpha": _get_float(data, "alpha", "α (alpha)", 0.0, 10.0, 1.0),
        "beta": _get_float(data, "beta", "β (beta)", 0.0, 10.0, 3.0),
        "rho": _get_float(data, "rho", "ρ (rho)", 0.0, 1.0, 0.5, lo_open=True),
        "q": _get_float(data, "q", "Q (q)", 0.0, 1e6, 1.0, lo_open=True),
        "n_iter": _get_int(data, "n_iter", "число итераций (n_iter)", 1, 10_000, 200),
        "seed": _get_int(data, "seed", "seed", 0, 2**32 - 1, None),
        "variant": variant,
    }
