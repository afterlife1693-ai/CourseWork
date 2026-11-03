"""Заглушка движка ``MockACO``: тот же интерфейс, что у настоящего ``ACO``.

Нужна, чтобы веб-часть (B) не ждала ядро (A). Внутри нет муравьиного алгоритма:
маршруты — случайные перестановки, но всё остальное честное:

* длины считаются по настоящей формуле (маршрут замкнутый);
* феромон обновляется по настоящему правилу «испарение -> откладывание»;
* вероятности кандидатов в событиях «шаг муравья» считаются по формуле
  p_ij = τ^α · η^β / Σ(...) (но город муравей выбирает случайно — это заглушка).

На второй неделе в ``web/app.py`` меняется одна строка импорта
(``MockACO`` -> ``ACO``), и всё остальное продолжает работать.

Интерфейс (контракт — в API.md):

    engine = MockACO(cities, params)
    engine.initial_snapshot()  # снапшот итерации 0 (до первого шага)
    engine.step()              # одна итерация целиком -> снапшот
    engine.ant_step()          # один шаг одного муравья -> событие
"""

from __future__ import annotations

import time
from typing import Iterator

import numpy as np


def distance_matrix(cities: list[list[float]]) -> np.ndarray:
    """Матрица евклидовых расстояний n×n."""
    pts = np.asarray(cities, dtype=float)
    diff = pts[:, None, :] - pts[None, :, :]
    return np.sqrt((diff**2).sum(axis=2))


def tour_length(dist: np.ndarray, tour) -> float:
    """Длина замкнутого маршрута: в сумму входит ребро «последний -> первый»."""
    idx = np.asarray(tour)
    return float(dist[idx, np.roll(idx, -1)].sum())


class MockACO:
    """Движок-заглушка. Параметры — словарь после ``parse_params``."""

    def __init__(self, cities: list[list[float]], params: dict):
        self.cities = cities
        self.params = dict(params)
        self.n = len(cities)
        self.m = int(params["n_ants"])
        self.dist = distance_matrix(cities)
        self.rng = np.random.default_rng(params.get("seed"))

        # η = 1/d; на диагонали (d = 0) ставим 0, чтобы не получить inf и nan
        with np.errstate(divide="ignore"):
            self.eta = np.where(self.dist > 0, 1.0 / self.dist, 0.0)

        # Феромон: плотная симметричная матрица n×n, диагональ не используется
        self.tau = np.ones((self.n, self.n))
        np.fill_diagonal(self.tau, 0.0)
        # Масштаб добавки: Q·L_ref/L, где L_ref — длина тождественного маршрута.
        # Без него 1/L ~ 1e-4 исчезало бы при округлении матрицы до 4 знаков.
        self._ref_length = tour_length(self.dist, np.arange(self.n))

        self.iteration = 0
        self.best_tour: list[int] = []
        self.best_length: float | None = None
        self._gen: Iterator[dict] | None = None  # генератор текущей итерации
        self._snapshot = self.initial_snapshot()

    # -- публичный интерфейс -------------------------------------------------

    def initial_snapshot(self) -> dict:
        """Снапшот итерации 0: феромон ровный, маршрутов ещё нет."""
        return self._make_snapshot([], [], None, 0.0)

    def step(self) -> dict:
        """Одна итерация целиком. Если пользователь уже сделал несколько шагов
        муравья, доигрываем текущую итерацию, а не начинаем новую."""
        if self._gen is None:
            self._gen = self._iteration()
        for _ in self._gen:  # дочитываем генератор до события update
            pass
        self._gen = None
        return self._snapshot

    def ant_step(self) -> dict:
        """Следующее событие итерации: ``move``, ``tour_done`` или ``update``."""
        if self._gen is None:
            self._gen = self._iteration()
        event = next(self._gen)
        if event["phase"] == "update":
            self._gen = None  # итерация закончена, следующий вызов начнёт новую
        return event

    # -- внутреннее ----------------------------------------------------------

    def _weights(self) -> np.ndarray:
        """choice_info = τ^α · η^β. Считается один раз за итерацию (τ внутри неё не меняется)."""
        return self.tau ** self.params["alpha"] * self.eta ** self.params["beta"]

    def _iteration(self) -> Iterator[dict]:
        """Одна итерация как генератор событий.

        Порядок: каждый муравей идёт n-1 шагов (``move``), затем ``tour_done``;
        когда закончили все — обновляем феромон и выдаём ``update`` со снапшотом.
        """
        t0 = time.perf_counter()
        tours = [self.rng.permutation(self.n) for _ in range(self.m)]
        lengths = [tour_length(self.dist, t) for t in tours]
        weights = self._weights()
        build_time = time.perf_counter() - t0

        for k, tour in enumerate(tours):
            for j in range(self.n - 1):
                current = int(tour[j])
                rest = tour[j + 1:]  # ещё не посещённые города
                w = weights[current, rest]
                total = w.sum()
                if total > 0 and np.isfinite(total):
                    p = w / total
                else:  # все веса обнулились: запасной вариант — равные вероятности
                    p = np.full(len(rest), 1.0 / len(rest))
                yield {
                    "phase": "move",
                    "ant": k,
                    "current": current,
                    "partial_tour": [int(c) for c in tour[: j + 1]],
                    "candidates": [
                        {"city": int(c), "p": round(float(pi), 4)} for c, pi in zip(rest, p)
                    ],
                    "chosen": int(tour[j + 1]),
                }
            yield {
                "phase": "tour_done",
                "ant": k,
                "tour": [int(c) for c in tour],
                "length": round(lengths[k], 2),
            }

        t1 = time.perf_counter()
        # Испарение, затем откладывание (порядок стандартный для Ant System)
        self.tau *= 1.0 - self.params["rho"]
        for tour, length in zip(tours, lengths):
            amount = self.params["q"] * self._ref_length / length
            a, b = tour, np.roll(tour, -1)
            np.add.at(self.tau, (a, b), amount)  # симметрия: ребро в обе стороны
            np.add.at(self.tau, (b, a), amount)

        best_i = int(np.argmin(lengths))
        if self.best_length is None or lengths[best_i] < self.best_length:
            self.best_length = lengths[best_i]
            self.best_tour = [int(c) for c in tours[best_i]]  # копия, не ссылка
        self.iteration += 1

        elapsed_ms = (build_time + time.perf_counter() - t1) * 1000
        self._snapshot = self._make_snapshot(
            [[int(c) for c in t] for t in tours], lengths, lengths[best_i], elapsed_ms
        )
        yield {"phase": "update", "snapshot": self._snapshot}

    def _make_snapshot(
        self, tours: list, lengths: list, iter_best: float | None, time_ms: float
    ) -> dict:
        """Снапшот по контракту (все значения — обычные int/float/list, не numpy)."""
        return {
            "iteration": self.iteration,
            "tau": np.round(self.tau, 4).tolist(),
            "tours": tours,
            "lengths": [round(float(x), 2) for x in lengths],
            "iter_best_length": None if iter_best is None else round(float(iter_best), 2),
            "best_tour": list(self.best_tour),
            "best_length": None if self.best_length is None else round(self.best_length, 2),
            "time_ms": round(time_ms, 2),
        }
