"""Flask-сервер: страница и JSON API между браузером и движком муравьиного алгоритма.

Запуск из корня проекта: python run.py (или python -m web.app).

Состояние (задача и движок) лежит на сервере, в словаре сессий. Идентификатор
сессии клиент получает от /api/instance и дальше присылает в заголовке
X-Session-Id. База данных для этого не нужна.
"""

from __future__ import annotations

import secrets
import threading
from collections import OrderedDict

from flask import Flask, jsonify, render_template, request
from werkzeug.exceptions import HTTPException

from core.aco import ACOParams
from core.instance import TSPInstance

# В core пока нет класса ACO, поэтому движком служит переходник на функциях ядра.
# Когда ACO появится, достаточно заменить эту строку на
#     from core.aco import ACO as Engine
# и удалить web/core_engine.py. Конструктор у обоих одинаковый: Engine(instance, params).
from web.core_engine import CoreEngine as Engine
from web.validation import ApiError, parse_instance_request, parse_params

SESSION_HEADER = "X-Session-Id"
MAX_SESSIONS = 200  # старые сессии вытесняются, чтобы память не росла бесконечно

HTTP_MESSAGES = {
    404: "Такого адреса не существует.",
    405: "Этот метод для данного адреса не разрешён.",
    413: "Слишком большой запрос.",
}


class Session:
    """Состояние одного пользователя: задача, параметры и движок."""

    def __init__(self) -> None:
        self.lock = threading.Lock()  # сервер Flask обрабатывает запросы в нескольких потоках
        self.instance: TSPInstance | None = None  # задача из core
        self.params: dict | None = None
        self.engine = None


class SessionStore:
    """Словарь сессий с ограничением размера. Когда место кончается, удаляются самые давние."""

    def __init__(self, max_sessions: int = MAX_SESSIONS) -> None:
        self._items: OrderedDict[str, Session] = OrderedDict()
        self._lock = threading.Lock()
        self._max = max_sessions

    def create(self) -> tuple[str, Session]:
        sid = secrets.token_urlsafe(16)
        session = Session()
        with self._lock:
            self._items[sid] = session
            while len(self._items) > self._max:
                self._items.popitem(last=False)
        return sid, session

    def get(self, sid: str | None) -> Session | None:
        if not sid:
            return None
        with self._lock:
            session = self._items.get(sid)
            if session is not None:
                self._items.move_to_end(sid)
            return session


def create_app(config: dict | None = None) -> Flask:
    """Собирает приложение. Так проще тестировать: у каждого теста своё состояние."""
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = 1024 * 1024  # 1 МБ хватает с большим запасом
    app.config.update(config or {})
    app.json.ensure_ascii = False  # чтобы кириллица в ответах читалась как есть
    store = SessionStore()
    app.extensions["aco_sessions"] = store

    # Вспомогательные функции

    def json_body() -> dict:
        """Тело запроса как словарь. Пустое тело считается пустым словарём."""
        if not request.get_data():
            return {}
        data = request.get_json(force=True, silent=True)
        if not isinstance(data, dict):
            raise ApiError("Тело запроса должно быть JSON-объектом.")
        return data

    def require_session() -> Session:
        session = store.get(request.headers.get(SESSION_HEADER))
        if session is None:
            raise ApiError(
                "Сессия не найдена (возможно, сервер перезапускался). "
                "Создайте задачу заново.",
                status=404,
                code="no_session",
            )
        return session

    def require_engine(session: Session):
        if session.engine is None:
            raise ApiError(
                "Движок не создан: сначала вызовите /api/init.", code="no_engine"
            )
        return session.engine

    # Обработка ошибок: клиент всегда получает JSON, а не страницу с трассировкой

    @app.errorhandler(ApiError)
    def handle_api_error(err: ApiError):
        return jsonify(error=err.message, code=err.code), err.status

    @app.errorhandler(HTTPException)
    def handle_http_error(err: HTTPException):
        if request.path.startswith("/api/"):
            message = HTTP_MESSAGES.get(err.code, err.description)
            return jsonify(error=message, code="http_error"), err.code
        return err

    @app.errorhandler(Exception)
    def handle_unexpected(err: Exception):
        if not request.path.startswith("/api/"):
            raise err
        app.logger.exception("Необработанная ошибка в %s", request.path)
        return jsonify(error="Внутренняя ошибка сервера.", code="internal"), 500

    # Страница

    @app.get("/")
    def index():
        return render_template("index.html")

    # API

    @app.post("/api/instance")
    def api_instance():
        """Создаёт задачу: {"n": 20, "seed": 42} или {"cities": [[x, y], ...]}."""
        instance, seed = parse_instance_request(json_body())
        sid = request.headers.get(SESSION_HEADER)
        session = store.get(sid)
        if session is None:  # первый запрос или устаревший id, начинаем новую сессию
            sid, session = store.create()
        with session.lock:
            session.instance = instance
            session.params = None
            session.engine = None  # задача новая, поэтому старый движок не нужен
        return jsonify(
            session_id=sid, n=instance.n, cities=instance.to_dict()["cities"], seed=seed
        )

    @app.post("/api/init")
    def api_init():
        """Принимает параметры, создаёт движок и возвращает снапшот итерации 0."""
        session = require_session()
        with session.lock:
            if session.instance is None:
                raise ApiError("Сначала создайте задачу: /api/instance.", code="no_instance")
            params = parse_params(json_body(), session.instance.n)
            if params["seed"] is None:  # фиксируем seed, чтобы запуск можно было повторить
                params["seed"] = secrets.randbelow(2**31)
            try:
                core_params = ACOParams(**params)  # запасная проверка на стороне ядра
                engine = Engine(session.instance, core_params)
            except NotImplementedError as err:  # например, MMAS, которого пока нет в ядре
                raise ApiError(str(err), code="not_supported") from err
            except ValueError as err:
                raise ApiError(str(err)) from err
            session.params = params
            session.engine = engine
            return jsonify(
                params=params,
                n_cities=session.instance.n,
                snapshot=engine.initial_snapshot(),
            )

    @app.post("/api/step")
    def api_step():
        """Выполняет одну итерацию целиком и возвращает снапшот."""
        session = require_session()
        with session.lock:
            return jsonify(require_engine(session).step())

    @app.post("/api/ant_step")
    def api_ant_step():
        """Делает один шаг одного муравья и возвращает событие."""
        session = require_session()
        with session.lock:
            return jsonify(require_engine(session).ant_step())

    @app.post("/api/reset")
    def api_reset():
        """Удаляет движок, а задачу оставляет. Повторный вызов ничего не ломает."""
        session = store.get(request.headers.get(SESSION_HEADER))
        if session is not None:
            with session.lock:
                session.engine = None
                session.params = None
        return jsonify(ok=True)

    return app


def main() -> None:
    # При debug=True сервер перезапускается при каждом сохранении файла, и словарь
    # сессий обнуляется. Страница в этом случае пишет, что сессия устарела. Так и задумано.
    create_app().run(debug=True)


if __name__ == "__main__":
    main()
