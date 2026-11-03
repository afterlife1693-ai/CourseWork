"""Тесты веб-части: Flask API и его связка с ядром (core). Запросы идут через app.test_client()."""

import numpy as np
import pytest

from core.aco import ACOParams
from core.heuristics import compute_nn_tour_length
from core.instance import TSPInstance
from web.app import create_app
from web.core_engine import CoreEngine

SNAPSHOT_KEYS = {
    "iteration", "tau", "tours", "lengths",
    "iter_best_length", "best_tour", "best_length", "time_ms",
}


@pytest.fixture
def client():
    return create_app({"TESTING": True}).test_client()


def post(client, path, body=None, sid=None):
    """Отправляет POST с JSON и, если нужно, с идентификатором сессии."""
    headers = {"X-Session-Id": sid} if sid else {}
    return client.post(path, json=body, headers=headers)


def make_session(client, n=8, **params):
    """Создаёт задачу и движок. Возвращает идентификатор сессии и ответ /api/init."""
    sid = post(client, "/api/instance", {"n": n, "seed": 1}).get_json()["session_id"]
    init = post(client, "/api/init", params, sid)
    assert init.status_code == 200, init.get_json()
    return sid, init.get_json()


# /api/instance


def test_instance_returns_requested_number_of_cities(client):
    resp = post(client, "/api/instance", {"n": 20, "seed": 42})
    data = resp.get_json()
    assert resp.status_code == 200
    assert data["n"] == 20 and len(data["cities"]) == 20
    assert data["session_id"]
    # С тем же seed получается та же задача. Это нужно для повторяемых экспериментов.
    again = post(client, "/api/instance", {"n": 20, "seed": 42}).get_json()
    assert again["cities"] == data["cities"]


@pytest.mark.parametrize("bad_n", [2, 51, 0, -5, 3.5, "20", True, None])
def test_instance_rejects_wrong_n_with_400(client, bad_n):
    resp = post(client, "/api/instance", {"n": bad_n})
    assert resp.status_code == 400
    assert resp.get_json()["error"]  # понятный текст, а не трассировка


@pytest.mark.parametrize(
    "cities",
    [
        [[0, 0], [1, 1]],                         # меньше трёх городов
        [[0, 0], [1, 1], [1, 1]],                 # совпадающие города
        [[0, 0], [1, 1], ["a", 2]],               # вместо числа строка
        [[0, 0], [1, 1], [2]],                    # не пара
        [[0, 0], [1, 1], [True, 5]],              # bool не считается числом
        [[i, i] for i in range(51)],              # больше пятидесяти
        "строка вместо списка",
    ],
)
def test_instance_rejects_bad_cities(client, cities):
    resp = post(client, "/api/instance", {"cities": cities})
    assert resp.status_code == 400
    assert resp.get_json()["code"] == "bad_request"


def test_instance_accepts_custom_cities_and_float_coordinates(client):
    cities = [[0, 0], [10.5, 0], [10.5, 7.25]]
    data = post(client, "/api/instance", {"cities": cities}).get_json()
    assert data["cities"] == cities


def test_invalid_json_body_gives_400(client):
    resp = client.post("/api/instance", data="это не json", content_type="application/json")
    assert resp.status_code == 400


# /api/init и /api/step


def test_init_uses_defaults_and_returns_iteration_zero(client):
    sid, init = make_session(client, n=8)
    assert init["params"]["n_ants"] == 8  # по умолчанию муравьёв столько же, сколько городов
    assert init["params"]["variant"] == "AS"
    assert isinstance(init["params"]["seed"], int)
    snap = init["snapshot"]
    assert set(snap) == SNAPSHOT_KEYS
    assert snap["iteration"] == 0 and snap["best_length"] is None and snap["best_tour"] == []


@pytest.mark.parametrize(
    "bad",
    [
        {"rho": 0}, {"rho": 1.5}, {"rho": "0.5"},
        {"alpha": -1}, {"beta": 11}, {"q": 0},
        {"n_ants": 0}, {"n_ants": 2.5}, {"n_iter": 0},
        {"variant": "XYZ"}, {"alpha": float("nan")},
    ],
)
def test_init_rejects_invalid_params(client, bad):
    sid = post(client, "/api/instance", {"n": 5, "seed": 1}).get_json()["session_id"]
    resp = post(client, "/api/init", bad, sid)
    assert resp.status_code == 400
    assert resp.get_json()["error"]


def test_step_returns_valid_snapshot(client):
    sid, _ = make_session(client, n=10, n_ants=6, rho=0.3)
    cities = post(client, "/api/instance", {"n": 10, "seed": 1}, sid).get_json()["cities"]
    post(client, "/api/init", {"n_ants": 6, "rho": 0.3}, sid)
    instance = TSPInstance(cities)

    best_so_far = float("inf")
    for expected_iteration in (1, 2, 3):
        resp = post(client, "/api/step", None, sid)
        snap = resp.get_json()
        assert resp.status_code == 200
        assert set(snap) == SNAPSHOT_KEYS
        assert snap["iteration"] == expected_iteration
        assert len(snap["tours"]) == 6 == len(snap["lengths"])
        for tour, length in zip(snap["tours"], snap["lengths"]):
            assert sorted(tour) == list(range(10))  # в маршруте есть все города, и по одному разу
            assert length == pytest.approx(instance.tour_length(tour), abs=0.01)
        assert sorted(snap["best_tour"]) == list(range(10))
        assert snap["iter_best_length"] == min(snap["lengths"])
        assert snap["best_length"] <= best_so_far  # лучшая длина со временем не растёт
        best_so_far = snap["best_length"]
        # Феромон: матрица n×n, симметричная, без отрицательных значений
        tau = snap["tau"]
        assert len(tau) == 10 and all(len(row) == 10 for row in tau)
        assert all(tau[i][j] == tau[j][i] >= 0 for i in range(10) for j in range(10))


def test_step_before_init_and_without_session(client):
    sid = post(client, "/api/instance", {"n": 5, "seed": 1}).get_json()["session_id"]
    resp = post(client, "/api/step", None, sid)  # /api/init ещё не вызывали
    assert resp.status_code == 400 and resp.get_json()["code"] == "no_engine"

    resp = post(client, "/api/step", None, "несуществующий-id")
    assert resp.status_code == 404 and resp.get_json()["code"] == "no_session"
    assert post(client, "/api/init", {}, None).status_code == 404


def test_reset_drops_engine_but_keeps_instance(client):
    sid, _ = make_session(client, n=5)
    assert post(client, "/api/step", None, sid).status_code == 200
    assert post(client, "/api/reset", None, sid).get_json() == {"ok": True}
    assert post(client, "/api/step", None, sid).get_json()["code"] == "no_engine"
    assert post(client, "/api/init", {}, sid).status_code == 200  # задача никуда не делась
    assert post(client, "/api/reset", None, "чужой-id").status_code == 200  # повторный сброс безопасен


# /api/ant_step


def test_ant_steps_make_full_iteration_and_end_with_update(client):
    n, m = 6, 4
    sid, _ = make_session(client, n=n, n_ants=m)
    events = []
    while True:
        event = post(client, "/api/ant_step", None, sid).get_json()
        events.append(event)
        if event["phase"] == "update":
            break
        assert len(events) < 1000
    phases = [e["phase"] for e in events]
    assert phases.count("move") == m * (n - 1)  # у каждого муравья ровно n - 1 шагов
    assert phases.count("tour_done") == m and phases[-1] == "update"
    assert set(events[-1]["snapshot"]) == SNAPSHOT_KEYS

    move = events[0]
    assert move["current"] == move["partial_tour"][-1]
    assert move["chosen"] in [c["city"] for c in move["candidates"]]
    assert sum(c["p"] for c in move["candidates"]) == pytest.approx(1.0, abs=0.01)
    assert not set(move["partial_tour"]) & {c["city"] for c in move["candidates"]}

    # После update следующий вызов начинает новую итерацию
    assert post(client, "/api/ant_step", None, sid).get_json()["phase"] == "move"


def test_step_after_ant_steps_finishes_current_iteration(client):
    sid, _ = make_session(client, n=6, n_ants=4)
    for _ in range(7):  # сначала несколько шагов муравья
        post(client, "/api/ant_step", None, sid)
    snap = post(client, "/api/step", None, sid).get_json()
    assert snap["iteration"] == 1 and len(snap["tours"]) == 4  # текущая итерация доиграна, новая не началась
    assert post(client, "/api/step", None, sid).get_json()["iteration"] == 2


# Связка с ядром (core)


def make_engine(n=12, seed=3, **overrides):
    values = dict(n_ants=n, alpha=1.0, beta=3.0, rho=0.5, q=1.0, n_iter=50, seed=9)
    values.update(overrides)
    return CoreEngine(TSPInstance.random(n, seed), ACOParams(**values))


def test_random_instance_is_the_same_as_in_core(client):
    """При одинаковых n и seed веб даёт ту же задачу, что и TSPInstance.random в ядре."""
    data = post(client, "/api/instance", {"n": 15, "seed": 5}).get_json()
    assert data["cities"] == TSPInstance.random(15, 5).to_dict()["cities"]


def test_json_saved_by_core_opens_in_web(client, tmp_path):
    """Файл, сохранённый методом save_json из ядра, API принимает без доработок."""
    path = tmp_path / "instance.json"
    TSPInstance.random(20, 11).save_json(str(path))
    cities = TSPInstance.from_json(str(path)).to_dict()["cities"]
    data = post(client, "/api/instance", {"cities": cities}).get_json()
    assert data["n"] == 20 and data["cities"] == cities


def test_engine_snapshots_are_reproducible_for_same_seed():
    a, b = make_engine(), make_engine()
    for _ in range(3):
        sa, sb = a.step(), b.step()
        sa.pop("time_ms"), sb.pop("time_ms")  # время замера каждый раз немного разное
        assert sa == sb


def test_engine_tours_come_from_core_and_are_plain_ints(client):
    """construct_tour может вернуть numpy.int64, а до JSON должны дойти обычные int."""
    sid, _ = make_session(client, n=9, n_ants=30)  # муравьёв много, поэтому стартовые города разные
    snap = post(client, "/api/step", None, sid).get_json()  # если бы тип был неправильный, пришло бы 500
    assert all(type(c) is int for tour in snap["tours"] for c in tour)


def test_ants_beat_random_search_and_get_close_to_nearest_neighbor():
    """Проверяет, что маршруты строят именно муравьи, а не случайный выбор.

    За 50 итераций результат должен быть не хуже ближайшего соседа больше чем на 10%
    и как минимум вдвое лучше среднего случайного маршрута.
    """
    engine = make_engine(n=15, seed=4)
    first = engine.step()
    for _ in range(49):
        last = engine.step()
    nn = compute_nn_tour_length(engine.instance.distances)
    assert last["best_length"] <= nn * 1.10
    assert last["best_length"] <= first["best_length"]

    rng = np.random.default_rng(0)
    random_tours = [rng.permutation(15).tolist() for _ in range(200)]
    mean_random = sum(engine.instance.tour_length(t) for t in random_tours) / 200
    assert last["best_length"] < 0.5 * mean_random


def test_pheromone_matrix_stays_symmetric_and_positive():
    engine = make_engine(n=10)
    for _ in range(5):
        tau = engine.step()["tau"]
        assert all(tau[i][j] == tau[j][i] > 0 for i in range(10) for j in range(10))


def test_mmas_is_rejected_until_core_has_aco(client):
    sid = post(client, "/api/instance", {"n": 6, "seed": 1}).get_json()["session_id"]
    resp = post(client, "/api/init", {"variant": "MMAS"}, sid)
    assert resp.status_code == 400 and resp.get_json()["code"] == "not_supported"


def test_index_page_has_canvas(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert b"<canvas" in resp.data
