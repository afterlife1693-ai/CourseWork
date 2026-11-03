"""Flask-сервер: страница и JSON API между браузером и движком ACO.

Запуск из корня проекта:  ``python run.py``  (или ``python -m web.app``).

Состояние (задача и движок) хранится на сервере в словаре по идентификатору
сессии. Идентификатор клиент получает при ``POST /api/instance`` и присылает
дальше в заголовке ``X-Session-Id``. База данных не нужна.
"""

from __future__ import annotations

import secrets
import threading
from collections import OrderedDict

from flask import Flask, jsonify, render_template, request
from werkzeug.exceptions import HTTPException

# Неделя 2: заменить одну строку на   from core.aco import ACO as Engine
# (если конструктор ACO принимает не (cities, params), поправить создание ниже).
from web.mock_aco import MockACO as Engine
from web.validation import ApiError, parse_instance_request, parse_params

SESSION_HEADER = "X-Session-Id"
MAX_SESSIONS = 200  # старые сессии вытесняются, чтобы память не росла бесконечно

HTTP_MESSAGES = {
    404: "Такого адреса не существует.",
    405: "Этот метод для данного адреса не разрешён.",
    413: "Слишком большой запрос.",
}


class Session:
    """Состояние одного пользователя: задача, параметры, движок."""

    def __init__(self) -> None:
        self.lock = threading.Lock()  # Flask по умолчанию многопоточный
        self.cities: list | None = None
        self.params: dict | None = None
        self.engine = None


class SessionStore:
    """Словарь сессий с ограничением размера (самые давние удаляются)."""

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
    """Фабрика приложения (так проще тестировать: у каждого теста своё состояние)."""
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = 1024 * 1024  # 1 МБ — с запасом для 50 городов
    app.config.update(config or {})
    app.json.ensure_ascii = False  # кириллица в ответах читаема
    store = SessionStore()
    app.extensions["aco_sessions"] = store

    # -- вспомогательное ----------------------------------------------------

    def json_body() -> dict:
        """Тело запроса как словарь; пустое тело = пустой словарь."""
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

    # -- обработка ошибок ---------------------------------------------------

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

    # -- страница -----------------------------------------------------------

    @app.get("/")
    def index():
        return render_template("index.html")

    # -- API ----------------------------------------------------------------

    @app.post("/api/instance")
    def api_instance():
        """Создаёт задачу: {"n": 20, "seed": 42} или {"cities": [[x, y], ...]}."""
        cities, seed = parse_instance_request(json_body())
        sid = request.headers.get(SESSION_HEADER)
        session = store.get(sid)
        if session is None:  # первый запрос или устаревший id -> новая сессия
            sid, session = store.create()
        with session.lock:
            session.cities = cities
            session.params = None
            session.engine = None  # новая задача -> старый движок не нужен
        return jsonify(session_id=sid, n=len(cities), cities=cities, seed=seed)

    @app.post("/api/init")
    def api_init():
        """Принимает параметры, создаёт движок, возвращает снапшот итерации 0."""
        session = require_session()
        with session.lock:
            if session.cities is None:
                raise ApiError("Сначала создайте задачу: /api/instance.", code="no_instance")
            params = parse_params(json_body(), len(session.cities))
            if params["seed"] is None:  # фиксируем seed, чтобы запуск был повторяемым
                params["seed"] = secrets.randbelow(2**31)
            session.params = params
            session.engine = Engine(session.cities, params)
            return jsonify(
                params=params,
                n_cities=len(session.cities),
                snapshot=session.engine.initial_snapshot(),
            )

    @app.post("/api/step")
    def api_step():
        """Одна итерация целиком; ответ — снапшот по контракту."""
        session = require_session()
        with session.lock:
            return jsonify(require_engine(session).step())

    @app.post("/api/ant_step")
    def api_ant_step():
        """Один шаг одного муравья (на неделе 1 — на заглушке)."""
        session = require_session()
        with session.lock:
            return jsonify(require_engine(session).ant_step())

    @app.post("/api/reset")
    def api_reset():
        """Сброс: движок удаляется, задача остаётся. Идемпотентно."""
        session = store.get(request.headers.get(SESSION_HEADER))
        if session is not None:
            with session.lock:
                session.engine = None
                session.params = None
        return jsonify(ok=True)

    return app


def main() -> None:
    # debug=True перезапускает сервер при сохранении файла -> словарь сессий
    # обнуляется, и страница покажет «сессия устарела». Это нормально.
    create_app().run(debug=True)


if __name__ == "__main__":
    main()
