"""Проверка входных данных API и генерация случайных задач.

Сервер сам проверяет всё, что приходит от клиента: проверка в браузере ничего
не гарантирует, запрос можно отправить в обход страницы. Любая ошибка ввода
превращается в ``ApiError`` -> ответ 400 с понятным текстом (без трассировки).
"""

from __future__ import annotations

import math
import secrets
from typing import Any

import numpy as np

MIN_CITIES = 3
MAX_CITIES = 50
COORD_LIMIT = 1e9  # защита от абсурдных координат (переполнение, потеря точности)

VARIANTS = ("AS", "MMAS")


class ApiError(Exception):
    """Ошибка, которую нужно показать пользователю как JSON {"error", "code"}."""

    def __init__(self, message: str, status: int = 400, code: str = "bad_request"):
        super().__init__(message)
        self.message = message
        self.status = status
        self.code = code


# ---------------------------------------------------------------------------
# Низкоуровневые проверки чисел
# ---------------------------------------------------------------------------


def _is_number(value: Any) -> bool:
    """Число JSON: int или float, конечное, не bool (True == 1 в Python!)."""
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def _get_int(data: dict, key: str, label: str, lo: int, hi: int, default: Any) -> Any:
    """Целое из ``data[key]`` в диапазоне [lo, hi]; нет значения -> ``default``."""
    value = data.get(key)
    if value is None:
        return default
    if isinstance(value, float) and value.is_integer():
        value = int(value)  # 20.0 из JavaScript — это тоже 20
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
    """Число из ``data[key]`` в диапазоне [lo, hi] (или (lo, hi], если lo_open)."""
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


# ---------------------------------------------------------------------------
# Города
# ---------------------------------------------------------------------------


def parse_cities(raw: Any) -> list[list[float]]:
    """Проверяет список городов ``[[x, y], ...]`` и возвращает его копию.

    Города нумеруются с нуля (как в ядре и в событиях «шаг муравья»).
    Совпадающие города отвергаем: расстояние 0 ломает эвристику 1/d.
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


def random_cities(n: int, seed: int, width: int = 1000, height: int = 700) -> list[list[int]]:
    """Случайные города с целыми координатами; один seed -> одна и та же задача.

    Координаты уникальны, поэтому дубликатов (и расстояний 0) не бывает.
    """
    rng = np.random.default_rng(seed)
    seen: set[tuple[int, int]] = set()
    cities: list[list[int]] = []
    while len(cities) < n:
        point = (int(rng.integers(0, width + 1)), int(rng.integers(0, height + 1)))
        if point not in seen:
            seen.add(point)
            cities.append([point[0], point[1]])
    return cities


def parse_instance_request(data: dict) -> tuple[list[list[float]], int | None]:
    """Разбирает тело ``POST /api/instance``.

    Либо ``{"cities": [[x, y], ...]}``, либо ``{"n": 20, "seed": 42}``.
    Возвращает (города, seed); для явно заданных городов seed равен None.
    """
    if data.get("cities") is not None:
        return parse_cities(data["cities"]), None

    n = _get_int(data, "n", "n", MIN_CITIES, MAX_CITIES, None)
    if n is None:
        raise ApiError("Укажите либо «cities», либо «n» (число городов).")
    seed = _get_int(data, "seed", "seed", 0, 2**32 - 1, None)
    if seed is None:
        seed = secrets.randbelow(10**6)  # сервер выбрал сам -> вернём клиенту
    return random_cities(n, seed), seed


# ---------------------------------------------------------------------------
# Параметры алгоритма
# ---------------------------------------------------------------------------


def parse_params(data: dict, n_cities: int) -> dict:
    """Проверяет параметры ACO из ``POST /api/init`` и подставляет значения по умолчанию.

    Имена параметров — по договорённости из API.md:
    ``n_ants, alpha, beta, rho, q, n_iter, seed, variant``.
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
