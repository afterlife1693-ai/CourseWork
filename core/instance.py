"""
Эвристические алгоритмы для TSP.

Содержит простые алгоритмы построения маршрутов:
- Nearest Neighbor (ближайший сосед)
- 2-opt (локальное улучшение, опционально)
"""

import numpy as np
from typing import Optional


def nearest_neighbor(
    distances: np.ndarray,
    start: Optional[int] = None,
    seed: Optional[int] = None
) -> list[int]:
    """
    Жадный алгоритм ближайшего соседа.

    На каждом шаге выбирается ближайший непосещённый город.
    Простой и быстрый алгоритм, даёт приемлемое решение за O(n²).

    Используется для:
    - Базового сравнения с ACO
    - Вычисления начального значения феромона (τ₀ = m / C_nn)
    - Эталона "разумного" решения

    Args:
        distances: Матрица расстояний n×n
        start: Начальный город (если None, выбирается случайно или 0)
        seed: Seed для случайного выбора начального города

    Returns:
        Маршрут (список индексов городов)
    """
    n = distances.shape[0]

    # Выбор стартового города
    if start is None:
        if seed is not None:
            rng = np.random.default_rng(seed)
            current = rng.integers(0, n)
        else:
            current = 0
    else:
        current = start

    tour = [current]
    visited = {current}

    # Строим маршрут: на каждом шаге выбираем ближайший непосещённый город
    for _ in range(n - 1):
        nearest = None
        min_dist = float('inf')

        # Ищем ближайший непосещённый город
        for city in range(n):
            if city not in visited:
                dist = distances[current, city]
                if dist < min_dist:
                    min_dist = dist
                    nearest = city

        if nearest is None:
            # Это не должно происходить, но на всякий случай
            unvisited = [i for i in range(n) if i not in visited]
            nearest = unvisited[0] if unvisited else 0

        tour.append(nearest)
        visited.add(nearest)
        current = nearest

    return tour


def compute_nn_tour_length(distances: np.ndarray, start: Optional[int] = None) -> float:
    """
    Вычисляет длину маршрута, построенного алгоритмом ближайшего соседа.

    Удобная функция для быстрого получения C_nn без построения самого маршрута.

    Args:
        distances: Матрица расстояний n×n
        start: Начальный город

    Returns:
        Длина маршрута ближайшего соседа
    """
    tour = nearest_neighbor(distances, start)

    # Вычисляем длину (с замыканием)
    length = 0.0
    n = len(tour)
    for i in range(n):
        city_from = tour[i]
        city_to = tour[(i + 1) % n]
        length += distances[city_from, city_to]

    return length
