"""
Модуль для работы с задачей коммивояжёра (TSP).

Содержит класс TSPInstance, который представляет задачу TSP:
города, матрицу расстояний и операции над маршрутами.
"""

import json
import csv
import numpy as np
from typing import Optional


class TSPInstance:
    """
    Представление задачи коммивояжёра.

    Хранит координаты городов и матрицу расстояний между ними.
    Поддерживает создание из списка координат, случайную генерацию
    и загрузку из файлов.

    Attributes:
        n: Число городов
        cities: Координаты городов (n×2)
        distances: Матрица расстояний между городами (n×n)
    """

    def __init__(self, cities: list[tuple[float, float]], round_distances: bool = False):
        """
        Создаёт задачу TSP из списка координат городов.

        Args:
            cities: Список координат [(x1, y1), (x2, y2), ...]
            round_distances: Округлять ли расстояния до целых (для TSPLIB)

        Raises:
            ValueError: Если городов меньше 3 или есть дубликаты координат
        """
        if len(cities) < 3:
            raise ValueError(f"Требуется минимум 3 города, получено {len(cities)}")

        self.cities = np.array(cities, dtype=np.float64)
        self.n = len(self.cities)
        self.round_distances = round_distances

        # Проверка на дубликаты координат
        for i in range(self.n):
            for j in range(i + 1, self.n):
                if np.allclose(self.cities[i], self.cities[j]):
                    raise ValueError(
                        f"Города {i} и {j} имеют одинаковые координаты: {self.cities[i]}"
                    )

        # Вычисление матрицы расстояний
        self.distances = self._compute_distances()

    def _compute_distances(self) -> np.ndarray:
        """
        Вычисляет матрицу евклидовых расстояний между всеми парами городов.

        Returns:
            Симметричная матрица расстояний n×n
        """
        # Векторизованное вычисление: разность координат для всех пар
        diff = self.cities[:, np.newaxis, :] - self.cities[np.newaxis, :, :]
        distances = np.sqrt(np.sum(diff**2, axis=2))

        if self.round_distances:
            distances = np.round(distances).astype(np.float64)

        return distances

    @staticmethod
    def random(
        n: int,
        seed: int,
        width: float = 100.0,
        height: float = 100.0
    ) -> 'TSPInstance':
        """
        Создаёт случайную задачу TSP.

        Города размещаются случайно в прямоугольнике [0, width] × [0, height].
        Одинаковый seed гарантирует одинаковую задачу.

        Args:
            n: Число городов
            seed: Seed для генератора случайных чисел
            width: Ширина области
            height: Высота области

        Returns:
            Новый экземпляр TSPInstance

        Raises:
            ValueError: Если n < 3
        """
        if n < 3:
            raise ValueError(f"Требуется минимум 3 города, получено {n}")

        rng = np.random.default_rng(seed)
        cities = rng.random((n, 2)) * [width, height]

        return TSPInstance(cities.tolist())

    @staticmethod
    def from_json(filepath: str) -> 'TSPInstance':
        """
        Загружает задачу из JSON файла.

        Формат: {"cities": [[x1, y1], [x2, y2], ...]}

        Args:
            filepath: Путь к JSON файлу

        Returns:
            Новый экземпляр TSPInstance
        """
        with open(filepath, 'r', encoding='utf-8') as f:
            data = json.load(f)

        if 'cities' not in data:
            raise ValueError("JSON файл должен содержать поле 'cities'")

        return TSPInstance(data['cities'])

    @staticmethod
    def from_csv(filepath: str) -> 'TSPInstance':
        """
        Загружает задачу из CSV файла.

        Формат: две колонки (x, y), по одному городу на строку.
        Первая строка может быть заголовком.

        Args:
            filepath: Путь к CSV файлу

        Returns:
            Новый экземпляр TSPInstance
        """
        cities = []
        with open(filepath, 'r', encoding='utf-8') as f:
            reader = csv.reader(f)
            for i, row in enumerate(reader):
                if len(row) < 2:
                    continue

                try:
                    x, y = float(row[0]), float(row[1])
                    cities.append((x, y))
                except ValueError:
                    # Пропускаем строку (вероятно, заголовок)
                    if i == 0:
                        continue
                    raise ValueError(f"Неверный формат в строке {i + 1}: {row}")

        return TSPInstance(cities)

    def tour_length(self, tour: list[int]) -> float:
        """
        Вычисляет длину замкнутого маршрута.

        Маршрут замкнутый: после последнего города идёт возврат к первому.

        Args:
            tour: Список индексов городов (перестановка от 0 до n-1)

        Returns:
            Общая длина маршрута

        Raises:
            ValueError: Если маршрут некорректен
        """
        if not self.is_valid_tour(tour):
            raise ValueError("Некорректный маршрут")

        length = 0.0
        for i in range(len(tour)):
            city_from = tour[i]
            city_to = tour[(i + 1) % len(tour)]  # Замыкание на первый город
            length += self.distances[city_from, city_to]

        return length

    def is_valid_tour(self, tour: list[int]) -> bool:
        """
        Проверяет корректность маршрута.

        Корректный маршрут содержит каждый город ровно один раз
        (перестановка чисел от 0 до n-1).

        Args:
            tour: Список индексов городов

        Returns:
            True, если маршрут корректен
        """
        if len(tour) != self.n:
            return False

        # Проверка, что все города от 0 до n-1 присутствуют ровно один раз
        return set(tour) == set(range(self.n))

    def to_dict(self) -> dict:
        """
        Преобразует задачу в JSON-совместимый словарь.

        Returns:
            Словарь с координатами городов (обычные float, не numpy)
        """
        return {
            'cities': [[float(x), float(y)] for x, y in self.cities]
        }

    def save_json(self, filepath: str) -> None:
        """
        Сохраняет задачу в JSON файл.

        Args:
            filepath: Путь к выходному файлу
        """
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(self.to_dict(), f, indent=2, ensure_ascii=False)

    def __repr__(self) -> str:
        return f"TSPInstance(n={self.n})"
