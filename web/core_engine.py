"""Движок на функциях из core, пока в ядре нет класса ACO.

Класса ACO и матрицы феромона (PheromoneMatrix) в core пока нет. Чтобы страница
уже работала с настоящим кодом ядра, здесь собран Ant System из того, что есть:

* TSPInstance хранит города и матрицу расстояний и считает длину замкнутого маршрута;
* compute_choice_info считает привлекательность рёбер τ^α · η^β;
* construct_tour строит маршрут одного муравья;
* compute_nn_tour_length даёт длину маршрута ближайшего соседа, из неё получается
  начальный феромон τ₀ = m / C_nn.

Своё здесь только то, чего в ядре ещё нет: матрица феромона и её обновление
(сначала испарение, потом откладывание Q / L). Когда в core появится ACO, этот файл
можно удалить, а в web/app.py заменить одну строку импорта (подробности в API.md).

Интерфейс движка описан в API.md:

    engine = CoreEngine(instance, params)   # TSPInstance и ACOParams
    engine.initial_snapshot()  # снапшот итерации 0, до первого шага
    engine.step()              # одна итерация целиком, возвращает снапшот
    engine.ant_step()          # один шаг одного муравья, возвращает событие
"""

from __future__ import annotations

import math
import time
from typing import Iterator

import numpy as np

from core.aco import ACOParams, compute_choice_info, construct_tour
from core.heuristics import compute_nn_tour_length
from core.instance import TSPInstance


def round_tau(tau: np.ndarray) -> list:
    """Матрица феромона для JSON с четырьмя значащими цифрами от максимума.

    Обычное округление до 4 знаков после запятой не годится. При τ₀ = m / C_nn
    и добавке 1/L на больших координатах значения порядка 1e-4 превратились бы
    в 0.0001 или в 0, и вся структура карты феромона пропала бы.
    """
    top = float(tau.max())
    if not math.isfinite(top) or top <= 0:
        return tau.tolist()
    decimals = max(4, 3 - math.floor(math.log10(top)))
    return np.round(tau, decimals).tolist()


class CoreEngine:
    """Ant System на функциях core. Поддерживается только вариант "AS"."""

    def __init__(self, instance: TSPInstance, params: ACOParams):
        if params.variant != "AS":
            raise NotImplementedError(
                f"Вариант «{params.variant}» пока не реализован: в core ещё нет класса ACO. "
                "Сейчас доступен только «AS»."
            )
        self.instance = instance
        self.params = params
        self.n = instance.n
        self.m = params.n_ants
        self.rng = np.random.default_rng(params.seed)

        # η = 1/d. На диагонали d = 0, поэтому ставим там 0, чтобы не получить inf и nan.
        dist = instance.distances
        with np.errstate(divide="ignore"):
            self.eta = np.where(dist > 0, 1.0 / dist, 0.0)

        # Начальный феромон в Ant System равен m / C_nn. Он не зависит от масштаба координат.
        c_nn = compute_nn_tour_length(dist)
        tau0 = self.m / c_nn if c_nn > 0 else 1.0
        self.tau = np.full((self.n, self.n), tau0)

        self.iteration = 0
        self.best_tour: list[int] = []
        self.best_length: float | None = None
        self._gen: Iterator[dict] | None = None  # генератор текущей итерации
        self._snapshot = self.initial_snapshot()

    # Публичный интерфейс

    def initial_snapshot(self) -> dict:
        """Снапшот итерации 0: феромон распределён поровну, маршрутов ещё нет."""
        return self._make_snapshot([], [], None, 0.0)

    def step(self) -> dict:
        """Выполняет одну итерацию целиком.

        Если пользователь уже сделал несколько шагов муравья, доигрывается текущая
        итерация, а новая не начинается.
        """
        if self._gen is None:
            self._gen = self._iteration()
        for _ in self._gen:  # дочитываем генератор до события update
            pass
        self._gen = None
        return self._snapshot

    def ant_step(self) -> dict:
        """Возвращает следующее событие итерации: move, tour_done или update."""
        if self._gen is None:
            self._gen = self._iteration()
        event = next(self._gen)
        if event["phase"] == "update":
            self._gen = None  # итерация закончилась, следующий вызов начнёт новую
        return event

    # Внутренняя часть

    def _iteration(self) -> Iterator[dict]:
        """Одна итерация в виде генератора событий.

        Каждый муравей делает n - 1 шагов (событие move) и завершает маршрут
        (событие tour_done). Когда прошли все муравьи, обновляется феромон
        и приходит событие update со снапшотом.
        """
        t0 = time.perf_counter()
        # 1. choice_info считается один раз: внутри итерации феромон не меняется.
        choice_info = compute_choice_info(self.tau, self.eta, self.params.alpha, self.params.beta)
        # 2. Каждый муравей строит маршрут функцией ядра. Первый город она может вернуть
        #    как numpy.int64, а JSON такое число не принимает, поэтому приводим всё к int.
        tours = [
            [int(c) for c in construct_tour(choice_info, self.instance.distances, self.rng)]
            for _ in range(self.m)
        ]
        # 3. Длины замкнутых маршрутов. Заодно tour_length проверяет, что маршрут корректен.
        lengths = [float(self.instance.tour_length(t)) for t in tours]
        build_time = time.perf_counter() - t0

        for k, tour in enumerate(tours):
            for j in range(self.n - 1):
                current = tour[j]
                rest = np.array(tour[j + 1:])  # города, в которых муравей ещё не был
                # Вероятность перехода считается по той же формуле, по которой construct_tour
                # выбирал город: p_ij = choice_info[i, j] / сумма choice_info[i, k] по k из rest.
                w = choice_info[current, rest]
                total = w.sum()
                if total > 0 and np.isfinite(total):
                    p = w / total
                else:  # все веса обнулились, construct_tour в этом случае выбирает наугад
                    p = np.full(len(rest), 1.0 / len(rest))
                yield {
                    "phase": "move",
                    "ant": k,
                    "current": current,
                    "partial_tour": tour[: j + 1],
                    "candidates": [
                        {"city": int(c), "p": round(float(pi), 4)} for c, pi in zip(rest, p)
                    ],
                    "chosen": tour[j + 1],
                }
            yield {"phase": "tour_done", "ant": k, "tour": tour, "length": round(lengths[k], 2)}

        t1 = time.perf_counter()
        # 4. Сначала испарение, потом 5. откладывание Q / L на рёбра каждого маршрута.
        #    Ребро откладывается в обе стороны, чтобы матрица осталась симметричной.
        self.tau *= 1.0 - self.params.rho
        for tour, length in zip(tours, lengths):
            amount = self.params.q / length
            a = np.array(tour)
            b = np.roll(a, -1)  # следующий город, а у последнего это первый (маршрут замкнут)
            np.add.at(self.tau, (a, b), amount)
            np.add.at(self.tau, (b, a), amount)

        # 6. Лучший маршрут сохраняем копией списка, а не ссылкой.
        best_i = int(np.argmin(lengths))
        if self.best_length is None or lengths[best_i] < self.best_length:
            self.best_length = lengths[best_i]
            self.best_tour = list(tours[best_i])
        self.iteration += 1

        elapsed_ms = (build_time + time.perf_counter() - t1) * 1000
        self._snapshot = self._make_snapshot(tours, lengths, lengths[best_i], elapsed_ms)
        yield {"phase": "update", "snapshot": self._snapshot}

    def _make_snapshot(
        self, tours: list, lengths: list, iter_best: float | None, time_ms: float
    ) -> dict:
        """Собирает снапшот по контракту. Все значения обычные: int, float и list, без numpy."""
        return {
            "iteration": self.iteration,
            "tau": round_tau(self.tau),
            "tours": [list(t) for t in tours],
            "lengths": [round(x, 2) for x in lengths],
            "iter_best_length": None if iter_best is None else round(iter_best, 2),
            "best_tour": list(self.best_tour),
            "best_length": None if self.best_length is None else round(self.best_length, 2),
            "time_ms": round(time_ms, 2),
        }
