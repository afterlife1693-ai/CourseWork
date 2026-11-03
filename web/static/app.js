"use strict";

/**
 * Логика страницы: ввод городов, запросы к серверу и кнопки.
 * Рисованием занимается render.js (Render.render), а здесь только состояние и события.
 */
(() => {
    const MIN_CITIES = 3;
    const MAX_CITIES = 50;
    const MAX_FILE_BYTES = 1024 * 1024;
    const RUN_DELAY_MS = 120;   // пауза между итерациями в режиме «Запуск»
    const MIN_PIXEL_GAP = 14;   // ближе этого расстояния (в пикселях) новый город не ставится
    const SESSION_KEY = "aco_session_id";
    const EMPTY = "–";          // что показывать, когда значения ещё нет

    const $ = (id) => document.getElementById(id);
    const el = {
        canvas: $("canvas"), wrap: $("canvas-wrap"), status: $("status"),
        text: $("cities-text"), randN: $("rand-n"), randSeed: $("rand-seed"), file: $("file-input"),
        btnRandom: $("btn-random"), btnClear: $("btn-clear"), btnApplyText: $("btn-apply-text"),
        btnStep: $("btn-step"), btnRun: $("btn-run"), btnPause: $("btn-pause"), btnReset: $("btn-reset"),
        paramsNote: $("params-note"),
        infoIter: $("info-iter"), infoBest: $("info-best"), infoIterBest: $("info-iter-best"),
        infoTime: $("info-time"), infoCities: $("info-cities"),
    };
    const paramInputs = ["n_ants", "alpha", "beta", "rho", "q", "n_iter"].map((k) => $(`p-${k}`));
    const ctx = el.canvas.getContext("2d");

    const state = {
        cities: [],             // [[x, y], ...] в «мировых» координатах
        world: Render.fitWorld([]),
        view: null,
        dpr: 1,
        snapshot: null,         // последний снапшот с сервера или null
        appliedParams: null,    // параметры, с которыми на сервере создан движок
        sessionId: null,
        synced: false,          // на сервере есть задача и движок, и они соответствуют тому, что на экране
        busy: false,            // сейчас выполняется запрос
        running: false,         // работает цикл «Запуск»
        inflight: null,         // текущий запрос, чтобы дождаться его при сбросе
    };

    // Запросы к серверу

    class ApiError extends Error {
        constructor(message, code, status) {
            super(message);
            this.code = code;
            this.status = status;
        }
    }

    try { state.sessionId = sessionStorage.getItem(SESSION_KEY); } catch (_) { /* хранилище недоступно */ }

    function setSessionId(id) {
        state.sessionId = id;
        try { sessionStorage.setItem(SESSION_KEY, id); } catch (_) { /* не страшно */ }
    }

    /** POST с JSON. fetch не считает ответ 400 ошибкой, поэтому resp.ok проверяем сами. */
    async function api(path, body = {}) {
        const headers = { "Content-Type": "application/json" };
        if (state.sessionId) headers["X-Session-Id"] = state.sessionId;
        let resp;
        try {
            resp = await fetch(path, { method: "POST", headers, body: JSON.stringify(body) });
        } catch (_) {
            throw new ApiError("Нет связи с сервером. Проверьте, что он запущен.", "network", 0);
        }
        let data = null;
        try { data = await resp.json(); } catch (_) { /* ответ не в формате JSON */ }
        if (!resp.ok) {
            throw new ApiError((data && data.error) || `Ошибка сервера (${resp.status}).`,
                data && data.code, resp.status);
        }
        return data;
    }

    // Сообщения, кнопки и блок информации

    function setStatus(text, kind = "") {
        el.status.textContent = text;
        el.status.className = "status" + (kind ? " " + kind : "");
    }

    function fmt(value, digits = 2) {
        return value === null || value === undefined ? EMPTY : Number(value).toFixed(digits);
    }

    function updateInfo() {
        const snap = state.snapshot;
        const nIter = state.appliedParams ? state.appliedParams.n_iter : readNumber("p-n_iter");
        el.infoIter.textContent = snap ? `${snap.iteration} / ${nIter}` : EMPTY;
        el.infoBest.textContent = snap ? fmt(snap.best_length) : EMPTY;
        el.infoIterBest.textContent = snap ? fmt(snap.iter_best_length) : EMPTY;
        el.infoTime.textContent = snap && snap.iteration > 0 ? `${fmt(snap.time_ms, 1)} мс` : EMPTY;
        el.infoCities.textContent = String(state.cities.length);
    }

    function updateButtons() {
        const enough = state.cities.length >= MIN_CITIES;
        const idle = !state.busy && !state.running;
        el.btnStep.disabled = !(idle && enough);
        el.btnRun.disabled = !(idle && enough);
        el.btnPause.disabled = !state.running;
        el.btnReset.disabled = state.busy && !state.running;
        const lockData = state.busy || state.running; // пока идёт работа, города и параметры менять нельзя
        for (const input of paramInputs) input.disabled = lockData;
        for (const b of [el.btnRandom, el.btnClear, el.btnApplyText]) b.disabled = lockData;
        el.randN.disabled = el.randSeed.disabled = el.text.disabled = lockData;
        el.file.disabled = lockData;
    }

    function readNumber(id) {
        return Number($(id).value);
    }

    // Холст

    function rebuildView() {
        const rect = el.wrap.getBoundingClientRect();
        state.view = Render.makeView(state.world, rect.width, rect.height);
    }

    function resizeCanvas() {
        const rect = el.wrap.getBoundingClientRect();
        if (rect.width === 0 || rect.height === 0) return;
        // Размер в пикселях задаём атрибутами width и height. Если оставить только CSS,
        // картинка растянется и станет мутной.
        state.dpr = window.devicePixelRatio || 1;
        el.canvas.width = Math.round(rect.width * state.dpr);
        el.canvas.height = Math.round(rect.height * state.dpr);
        rebuildView();
        draw();
    }

    function draw() {
        if (!state.view) return;
        ctx.setTransform(state.dpr, 0, 0, state.dpr, 0, 0);
        Render.render(ctx, { view: state.view, cities: state.cities, snapshot: state.snapshot });
    }

    // Города: изменение списка

    function formatCoord(v) {
        return String(Number(v.toFixed(4)));
    }

    function syncTextarea() {
        if (document.activeElement === el.text) return; // пока человек печатает, текст не трогаем
        el.text.value = state.cities.map(([x, y]) => `${formatCoord(x)}, ${formatCoord(y)}`).join("\n");
    }

    /** После любого изменения списка городов предыдущий прогон теряет смысл. */
    function invalidateRun() {
        state.synced = false;
        state.snapshot = null;
        state.appliedParams = null;
        el.paramsNote.hidden = true;
    }

    function setCities(cities, { refit }) {
        state.cities = cities;
        if (refit) state.world = Render.fitWorld(cities);
        invalidateRun();
        rebuildView();
        syncTextarea();
        draw();
        updateInfo();
        updateButtons();
    }

    function canEditData() {
        if (state.busy || state.running) {
            setStatus("Поставьте на паузу, чтобы менять города.", "error");
            return false;
        }
        return true;
    }

    /** Ищет города с одинаковыми координатами. Возвращает текст ошибки или null. */
    function findDuplicate(cities) {
        const seen = new Map();
        for (let i = 0; i < cities.length; i++) {
            const key = `${cities[i][0]}|${cities[i][1]}`;
            if (seen.has(key)) return `Города ${seen.get(key)} и ${i} (нумерация с нуля) совпадают по координатам.`;
            seen.set(key, i);
        }
        return null;
    }

    function pointerPosition(event) {
        const rect = el.canvas.getBoundingClientRect();
        return [event.clientX - rect.left, event.clientY - rect.top];
    }

    function nearestCity(px, py, maxDist) {
        let best = -1, bestDist = maxDist;
        state.cities.forEach(([x, y], id) => {
            const [cx, cy] = Render.worldToPx(state.view, x, y);
            const d = Math.hypot(cx - px, cy - py);
            if (d <= bestDist) { best = id; bestDist = d; }
        });
        return best;
    }

    /** Сколько знаков после запятой оставлять у города, поставленного кликом.
     *  Если координаты лежат в диапазоне 0...1, округление до целых всё бы испортило. */
    function clickPrecision() {
        const span = Math.max(state.world.maxX - state.world.minX, state.world.maxY - state.world.minY, 1e-9);
        return Math.max(0, Math.min(6, 3 - Math.floor(Math.log10(span))));
    }

    function onCanvasClick(event) {
        if (!canEditData()) return;
        if (state.cities.length >= MAX_CITIES) {
            setStatus(`Максимум ${MAX_CITIES} городов.`, "error");
            return;
        }
        const [px, py] = pointerPosition(event);
        if (nearestCity(px, py, MIN_PIXEL_GAP) !== -1) {
            setStatus("Здесь уже есть город. Выберите место подальше.", "error");
            return;
        }
        const digits = clickPrecision();
        const [x, y] = Render.pxToWorld(state.view, px, py).map((v) => Number(v.toFixed(digits)));
        setStatus("");
        setCities([...state.cities, [x, y]], { refit: false });
    }

    function onCanvasContextMenu(event) {
        event.preventDefault(); // вместо меню браузера удаляем город под курсором
        if (!canEditData()) return;
        const [px, py] = pointerPosition(event);
        const id = nearestCity(px, py, Render.CITY_RADIUS + 4);
        if (id === -1) return;
        setStatus("");
        setCities(state.cities.filter((_, i) => i !== id), { refit: false });
    }

    // Города: ввод из текста и файлов

    /** Разбивает строку на ячейки. Если в ней есть «;», считаем это CSV из Excel:
     *  разделитель «;», а десятичная запятая. */
    function splitLine(line) {
        if (line.includes(";")) return line.split(";").map((t) => t.trim().replace(",", "."));
        return line.trim().split(/[\s,]+/);
    }

    function toNumber(token) {
        if (token === undefined || token.trim() === "") return NaN; // Number("") равно 0, а нам нужна ошибка
        return Number(token);
    }

    /**
     * Разбирает текст вида «x, y» по строке (для текстового поля и для CSV).
     * Первая строка без цифр считается заголовком. Если в ней есть колонки x и y, берём именно их.
     * Возвращает { cities } или { error }.
     */
    function parseCitiesText(text) {
        const lines = text.replace(/^﻿/, "").split(/\r?\n/);
        let xi = 0, yi = 1, startedData = false;
        const cities = [];
        for (let n = 0; n < lines.length; n++) {
            const line = lines[n];
            if (line.trim() === "") continue;
            const tokens = splitLine(line);
            if (!startedData && !/\d/.test(line)) { // это заголовок
                const names = tokens.map((t) => t.toLowerCase());
                if (names.includes("x") && names.includes("y")) { xi = names.indexOf("x"); yi = names.indexOf("y"); }
                startedData = true;
                continue;
            }
            startedData = true;
            const x = toNumber(tokens[xi]), y = toNumber(tokens[yi]);
            if (!Number.isFinite(x) || !Number.isFinite(y)) {
                return { error: `Строка ${n + 1}: ожидаются два числа «x, y», получено «${line.trim()}».` };
            }
            cities.push([x, y]);
        }
        return validateLoaded(cities);
    }

    function validateLoaded(cities) {
        if (!cities.length) return { error: "Не найдено ни одного города." };
        if (cities.length > MAX_CITIES) {
            return { error: `Слишком много городов: ${cities.length} (максимум ${MAX_CITIES}).` };
        }
        const dup = findDuplicate(cities);
        if (dup) return { error: dup };
        return { cities };
    }

    /** Понимает три вида JSON: {"cities": [[x, y], ...]}, просто [[x, y], ...] и [{"x": ..., "y": ...}, ...]. */
    function parseCitiesJson(text) {
        let data;
        try { data = JSON.parse(text); } catch (_) { return { error: "Файл не является корректным JSON." }; }
        const list = Array.isArray(data) ? data : data && data.cities;
        if (!Array.isArray(list)) return { error: "В JSON ожидается список городов или поле «cities»." };
        const cities = [];
        for (let i = 0; i < list.length; i++) {
            const item = list[i];
            const pair = Array.isArray(item) ? item : item && typeof item === "object" ? [item.x, item.y] : null;
            if (!pair || pair.length !== 2 || !pair.every((v) => typeof v === "number" && Number.isFinite(v))) {
                return { error: `Город ${i}: ожидается пара чисел [x, y].` };
            }
            cities.push([pair[0], pair[1]]);
        }
        return validateLoaded(cities);
    }

    function applyParsed(result, okMessage) {
        if (result.error) { setStatus(result.error, "error"); return; }
        setStatus(okMessage(result.cities.length), "ok");
        setCities(result.cities, { refit: true });
    }

    function onApplyText() {
        if (!canEditData()) return;
        el.text.blur(); // иначе после успешной загрузки syncTextarea не обновит поле
        // Если в тексте ошибка, он остаётся как есть, и его можно исправить.
        applyParsed(parseCitiesText(el.text.value), (n) => `Загружено городов: ${n}.`);
    }

    function onFileChosen() {
        const file = el.file.files[0];
        el.file.value = ""; // чтобы тот же файл можно было выбрать ещё раз
        if (!file || !canEditData()) return;
        if (file.size > MAX_FILE_BYTES) { setStatus("Файл слишком большой (больше 1 МБ).", "error"); return; }
        const reader = new FileReader();
        reader.onerror = () => setStatus("Не удалось прочитать файл.", "error");
        reader.onload = () => {
            const text = String(reader.result);
            const isJson = /\.json$/i.test(file.name) || /^\s*[\[{]/.test(text);
            const result = isJson ? parseCitiesJson(text) : parseCitiesText(text);
            applyParsed(result, (n) => `Из файла «${file.name}» загружено городов: ${n}.`);
        };
        reader.readAsText(file); // содержимому файла не доверяем, всё проверяется выше
    }

    // Кнопки «Случайные города» и «Очистить»

    async function onRandom() {
        if (!canEditData()) return;
        const body = { n: readNumber("rand-n") };
        if (el.randSeed.value.trim() !== "") body.seed = readNumber("rand-seed");
        await guarded(async () => {
            const data = await api("/api/instance", body);
            setSessionId(data.session_id);
            setStatus(`Случайные города: n = ${data.n}, seed = ${data.seed}.`, "ok");
            if (el.randSeed.value.trim() === "") el.randSeed.placeholder = `был ${data.seed}`;
            setCities(data.cities, { refit: true });
        });
    }

    function onClear() {
        if (!canEditData()) return;
        setStatus("");
        setCities([], { refit: true });
    }

    // Параметры и работа алгоритма

    /** Собирает параметры из формы. Допустимые диапазоны окончательно проверяет сервер. */
    function readParams() {
        const params = {};
        for (const key of ["n_ants", "alpha", "beta", "rho", "q", "n_iter"]) {
            const raw = $(`p-${key}`).value.trim();
            if (raw === "") {
                if (key === "n_ants") continue; // пустое поле значит «по умолчанию»: муравьёв столько же, сколько городов
                throw new Error("Заполните все параметры алгоритма.");
            }
            const value = Number(raw);
            if (!Number.isFinite(value)) throw new Error(`Параметр «${key}» должен быть числом.`);
            params[key] = value;
        }
        return params;
    }

    /** Если на сервере ещё нет подходящих задачи и движка, создаёт их. */
    async function ensureReady() {
        if (state.synced) return;
        if (state.cities.length < MIN_CITIES) {
            throw new Error(`Нужно минимум ${MIN_CITIES} города.`);
        }
        const params = readParams();
        const instance = await api("/api/instance", { cities: state.cities });
        setSessionId(instance.session_id);
        const init = await api("/api/init", params);
        state.appliedParams = init.params;
        state.snapshot = init.snapshot;
        state.synced = true;
        el.paramsNote.hidden = true;
        draw();
        updateInfo();
    }

    /** Выполняет одну итерацию. Возвращает true, если можно продолжать. */
    async function stepOnce() {
        await ensureReady();
        const nIter = state.appliedParams.n_iter;
        if (state.snapshot.iteration >= nIter) {
            setStatus(`Выполнено ${nIter} итераций. Нажмите «Сброс», чтобы начать заново.`, "ok");
            return false;
        }
        state.snapshot = await api("/api/step");
        draw();
        updateInfo();
        const more = state.snapshot.iteration < nIter;
        if (more) setStatus("");
        else setStatus(`Готово: ${nIter} итераций, лучший маршрут: ${fmt(state.snapshot.best_length)}.`, "ok");
        return more;
    }

    /** Показывает понятное сообщение вместо трассировки в консоли. */
    function handleError(err) {
        if (err.code === "no_session" || err.code === "no_engine") {
            // Сервер перезапускали, и его состояние потеряно. Задачу создадим заново при следующем действии.
            state.synced = false;
            state.snapshot = null;
            draw();
            updateInfo();
            setStatus("Сессия на сервере устарела (сервер перезапускался). Нажмите «Шаг», и задача создастся заново.", "error");
        } else {
            setStatus(err.message || "Что-то пошло не так.", "error");
        }
    }

    /** Блокирует кнопки на время запроса. Следующий запрос уходит только после ответа на предыдущий. */
    function withBusy(fn) {
        const promise = (async () => {
            state.busy = true;
            updateButtons();
            try { return await fn(); } finally { state.busy = false; updateButtons(); }
        })();
        state.inflight = promise;
        return promise;
    }

    async function guarded(fn) {
        try { return await withBusy(fn); } catch (err) { handleError(err); }
    }

    async function onStep() {
        if (state.busy || state.running) return;
        await guarded(stepOnce);
    }

    async function onRun() {
        if (state.busy || state.running) return;
        state.running = true;
        updateButtons();
        try {
            while (state.running) {
                const more = await withBusy(stepOnce);
                if (!more) break;
                await new Promise((resolve) => setTimeout(resolve, RUN_DELAY_MS));
            }
        } catch (err) {
            handleError(err);
        } finally {
            state.running = false;
            updateButtons();
        }
    }

    function onPause() {
        state.running = false; // цикл остановится сразу после ответа на текущий запрос
        setStatus("Пауза.");
        updateButtons();
    }

    async function onReset() {
        state.running = false;
        if (state.inflight) { try { await state.inflight; } catch (_) { /* ошибку уже показали */ } }
        const hadEngine = state.synced;
        invalidateRun();
        draw();
        updateInfo();
        updateButtons();
        setStatus("Сброшено. Параметры из формы применятся при следующем запуске.");
        if (hadEngine) {
            try { await api("/api/reset"); } catch (_) { /* сервер мог перезапуститься, это не страшно */ }
        }
    }

    function onParamEdited() {
        if (state.synced) el.paramsNote.hidden = false;
        updateInfo();
    }

    // Запуск

    el.canvas.addEventListener("click", onCanvasClick);
    el.canvas.addEventListener("contextmenu", onCanvasContextMenu);
    el.btnRandom.addEventListener("click", onRandom);
    el.btnClear.addEventListener("click", onClear);
    el.btnApplyText.addEventListener("click", onApplyText);
    el.file.addEventListener("change", onFileChosen);
    el.btnStep.addEventListener("click", onStep);
    el.btnRun.addEventListener("click", onRun);
    el.btnPause.addEventListener("click", onPause);
    el.btnReset.addEventListener("click", onReset);
    for (const input of paramInputs) input.addEventListener("input", onParamEdited);

    new ResizeObserver(resizeCanvas).observe(el.wrap);
    resizeCanvas();
    updateInfo();
    updateButtons();
})();
