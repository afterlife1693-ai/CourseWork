"""
Модуль муравьиного алгоритма для TSP.

Содержит параметры алгоритма, функции построения маршрутов
и основной класс ACO.
"""

from dataclasses import dataclass
import numpy as np
from typing import Optional


@dataclass
class ACOParams:
    """
    Параметры муравьиного алгоритма.

    Attributes:
        n_ants: Число муравьёв в колонии
        alpha: Вес феромона (влияние опыта колонии)
        beta: Вес эвристики (влияние расстояния)
        rho: Коэффициент испарения феромона (0 < rho <= 1)
        q: Константа для расчёта откладываемого феромона
        n_iter: Число итераций алгоритма
        seed: Seed для генератора случайных чисел
        variant: Вариант алгоритма ("AS" или "MMAS")
    """
    n_ants: int
    alpha: float
    beta: float
    rho: float
    q: float
    n_iter: int
    seed: int
    variant: str = "AS"

    def __post_init__(self):
        """Проверяет корректность параметров."""
        if self.n_ants < 1:
            raise ValueError(f"Число муравьёв должно быть >= 1, получено {self.n_ants}")

        if self.alpha < 0:
            raise ValueError(f"alpha должна быть >= 0, получено {self.alpha}")

        if self.beta < 0:
            raise ValueError(f"beta должна быть >= 0, получено {self.beta}")

        if not (0 < self.rho <= 1):
            raise ValueError(f"rho должна быть в диапазоне (0, 1], получено {self.rho}")

        if self.q <= 0:
            raise ValueError(f"q должна быть > 0, получено {self.q}")

        if self.n_iter < 1:
            raise ValueError(f"Число итераций должно быть >= 1, получено {self.n_iter}")

        if self.variant not in ("AS", "MMAS"):
            raise ValueError(f"variant должен быть 'AS' или 'MMAS', получено '{self.variant}'")


def construct_tour(
    choice_info: np.ndarray,
    distances: np.ndarray,
    rng: np.random.Generator,
    start: Optional[int] = None
) -> list[int]:
    """
    Строит маршрут одного муравья с использованием вероятностного выбора.

    Муравей начинает со случайного (или заданного) города и на каждом шаге
    выбирает следующий непосещённый город с вероятностью, пропорциональной
    привлекательности ребра (choice_info).

    Формула вероятности перехода из города i в город j:
        p_ij = choice_info[i, j] / sum(choice_info[i, k]) для всех непосещённых k

    Где choice_info = τ^α · η^β (вычисляется снаружи).

    Args:
        choice_info: Матрица привлекательности рёбер (τ^α · η^β), размер n×n
        distances: Матрица расстояний (для отладки и проверки)
        rng: Генератор случайных чисел (numpy.random.Generator)
        start: Начальный город (если None, выбирается случайно)

    Returns:
        Список индексов городов (маршрут)

    Note:
        Функция не изменяет входные массивы.
    """
    n = choice_info.shape[0]

    # Выбор стартового города
    if start is None:
        current = rng.integers(0, n)
    else:
        current = start

    tour = [current]
    visited = {current}

    # Построение маршрута: выбираем n-1 городов
    for _ in range(n - 1):
        # Получаем веса для всех городов из текущего
        weights = choice_info[current].copy()

        # Зануляем веса для уже посещённых городов
        for city in visited:
            weights[city] = 0.0

        # Сумма весов для непосещённых городов
        total = weights.sum()

        # Если все веса обнулились (может быть при больших alpha/beta)
        # выбираем равновероятно среди непосещённых
        if total == 0 or np.isnan(total):
            unvisited = [i for i in range(n) if i not in visited]
            next_city = rng.choice(unvisited)
        else:
            # Вероятностный выбор ("рулетка")
            probabilities = weights / total
            next_city = int(rng.choice(n, p=probabilities))

        tour.append(next_city)
        visited.add(next_city)
        current = next_city

    return tour


def compute_choice_info(
    tau: np.ndarray,
    eta: np.ndarray,
    alpha: float,
    beta: float
) -> np.ndarray:
    """
    Вычисляет матрицу привлекательности рёбер.

    choice_info[i, j] = τ[i, j]^α · η[i, j]^β

    Args:
        tau: Матрица феромонов n×n
        eta: Матрица эвристики n×n (обычно η = 1/d)
        alpha: Вес феромона
        beta: Вес эвристики

    Returns:
        Матрица привлекательности n×n
    """
    # Избегаем warning при возведении в степень
    with np.errstate(divide='ignore', invalid='ignore'):
        choice_info = (tau ** alpha) * (eta ** beta)
        # Заменяем inf и nan на 0
        choice_info = np.nan_to_num(choice_info, nan=0.0, posinf=0.0, neginf=0.0)

    return choice_info
