"""Тесты веб-части (Flask API и заглушка движка) через ``app.test_client()``."""

import pytest

from web.app import create_app
from web.mock_aco import MockACO, distance_matrix, tour_length
from web.validation import random_cities

SNAPSHOT_KEYS = {
    "iteration", "tau", "tours", "lengths",
    "iter_best_length", "best_tour", "best_length", "time_ms",
}


@pytest.fixture
def client():
    return create_app({"TESTING": True}).test_client()


def post(client, path, body=None, sid=None):
    """POST с JSON и (необязательно) идентификатором сессии."""
    headers = {"X-Session-Id": sid} if sid else {}
    return client.post(path, json=body, headers=headers)


def make_session(client, n=8, **params):
    """Создаёт задачу и движок; возвращает (session_id, ответ /api/init)."""
    sid = post(client, "/api/instance", {"n": n, "seed": 1}).get_json()["session_id"]
    init = post(client, "/api/init", params, sid)
    assert init.status_code == 200, init.get_json()
    return sid, init.get_json()


# -- /api/instance ------------------------------------------------------------


def test_instance_returns_requested_number_of_cities(client):
    resp = post(client, "/api/instance", {"n": 20, "seed": 42})
    data = resp.get_json()
    assert resp.status_code == 200
    assert data["n"] == 20 and len(data["cities"]) == 20
    assert data["session_id"]
    # одинаковый seed -> одинаковая задача (нужно для повторяемых экспериментов)
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
        [[0, 0], [1, 1]],                         # меньше 3 городов
        [[0, 0], [1, 1], [1, 1]],                 # совпадающие города
        [[0, 0], [1, 1], ["a", 2]],               # не числа
        [[0, 0], [1, 1], [2]],                    # не пара
        [[0, 0], [1, 1], [True, 5]],              # bool не число
        [[i, i] for i in range(51)],              # больше 50
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


# -- /api/init, /api/step -----------------------------------------------------


def test_init_uses_defaults_and_returns_iteration_zero(client):
    sid, init = make_session(client, n=8)
    assert init["params"]["n_ants"] == 8  # по умолчанию m = n
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
    dist = distance_matrix(cities)

    best_so_far = float("inf")
    for expected_iteration in (1, 2, 3):
        resp = post(client, "/api/step", None, sid)
        snap = resp.get_json()
        assert resp.status_code == 200
        assert set(snap) == SNAPSHOT_KEYS
        assert snap["iteration"] == expected_iteration
        assert len(snap["tours"]) == 6 == len(snap["lengths"])
        for tour, length in zip(snap["tours"], snap["lengths"]):
            assert sorted(tour) == list(range(10))  # перестановка всех городов
            assert length == pytest.approx(tour_length(dist, tour), abs=0.01)
        assert sorted(snap["best_tour"]) == list(range(10))
        assert snap["iter_best_length"] == min(snap["lengths"])
        assert snap["best_length"] <= best_so_far  # лучшая длина не растёт
        best_so_far = snap["best_length"]
        # феромон: матрица n×n, симметричная, без отрицательных значений
        tau = snap["tau"]
        assert len(tau) == 10 and all(len(row) == 10 for row in tau)
        assert all(tau[i][j] == tau[j][i] >= 0 for i in range(10) for j in range(10))


def test_step_before_init_and_without_session(client):
    sid = post(client, "/api/instance", {"n": 5, "seed": 1}).get_json()["session_id"]
    resp = post(client, "/api/step", None, sid)  # init ещё не вызывали
    assert resp.status_code == 400 and resp.get_json()["code"] == "no_engine"

    resp = post(client, "/api/step", None, "несуществующий-id")
    assert resp.status_code == 404 and resp.get_json()["code"] == "no_session"
    assert post(client, "/api/init", {}, None).status_code == 404


def test_reset_drops_engine_but_keeps_instance(client):
    sid, _ = make_session(client, n=5)
    assert post(client, "/api/step", None, sid).status_code == 200
    assert post(client, "/api/reset", None, sid).get_json() == {"ok": True}
    assert post(client, "/api/step", None, sid).get_json()["code"] == "no_engine"
    assert post(client, "/api/init", {}, sid).status_code == 200  # задача сохранилась
    assert post(client, "/api/reset", None, "чужой-id").status_code == 200  # идемпотентно


# -- /api/ant_step -------------------------------------------------------------


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
    assert phases.count("move") == m * (n - 1)  # ровно m·(n−1) шагов
    assert phases.count("tour_done") == m and phases[-1] == "update"
    assert set(events[-1]["snapshot"]) == SNAPSHOT_KEYS

    move = events[0]
    assert move["current"] == move["partial_tour"][-1]
    assert move["chosen"] in [c["city"] for c in move["candidates"]]
    assert sum(c["p"] for c in move["candidates"]) == pytest.approx(1.0, abs=0.01)
    assert not set(move["partial_tour"]) & {c["city"] for c in move["candidates"]}

    # после update следующий вызов начинает новую итерацию
    assert post(client, "/api/ant_step", None, sid).get_json()["phase"] == "move"


def test_step_after_ant_steps_finishes_current_iteration(client):
    sid, _ = make_session(client, n=6, n_ants=4)
    for _ in range(7):  # несколько шагов муравья
        post(client, "/api/ant_step", None, sid)
    snap = post(client, "/api/step", None, sid).get_json()
    assert snap["iteration"] == 1 and len(snap["tours"]) == 4  # доиграли, а не начали новую
    assert post(client, "/api/step", None, sid).get_json()["iteration"] == 2


# -- заглушка и страница ---------------------------------------------------------


def test_mock_is_reproducible_for_same_seed():
    cities = random_cities(7, seed=3)
    params = {"n_ants": 5, "alpha": 1.0, "beta": 3.0, "rho": 0.5, "q": 1.0, "seed": 9}
    a, b = MockACO(cities, params), MockACO(cities, params)
    for _ in range(3):
        sa, sb = a.step(), b.step()
        sa.pop("time_ms"), sb.pop("time_ms")  # время замера, естественно, разное
        assert sa == sb


def test_tour_length_closes_the_tour():
    square = distance_matrix([[0, 0], [1, 0], [1, 1], [0, 1]])
    assert tour_length(square, [0, 1, 2, 3]) == pytest.approx(4.0)  # с рёбром 3 -> 0


def test_index_page_has_canvas(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert b"<canvas" in resp.data
