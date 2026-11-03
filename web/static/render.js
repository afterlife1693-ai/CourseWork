"use strict";

/**
 * Отрисовка холста. Здесь нет ни запросов к серверу, ни состояния приложения:
 * render(ctx, state) просто рисует то, что ему передали. Новые слои (феромон,
 * пути муравьёв, подсветка шага) удобно добавлять прямо в render.
 *
 * Координаты бывают двух видов:
 *   «мир» это числа из задачи (x растёт вправо, y вверх, как на карте);
 *   «пиксели» это CSS-пиксели холста (x растёт вправо, y вниз).
 * Переводят одно в другое функции worldToPx и pxToWorld. Всему остальному коду
 * (кликам, подсветке, феромону) стоит пользоваться только ими.
 */
const Render = (() => {
    const PADDING = 40; // отступ от края холста, чтобы города не прилипали к рамке
    const DEFAULT_WORLD = { minX: 0, maxX: 1000, minY: 0, maxY: 700 };
    const CITY_RADIUS = 10;

    const COLORS = {
        city: "#ffffff",
        cityStroke: "#3b4252",
        cityText: "#1f2430",
        start: "#f08a24",
        tour: "#2f6fed",
        hint: "#8a93a3",
    };

    /** Прямоугольник «мира», в который помещаются все города. Для пустого списка берётся стандартный. */
    function fitWorld(cities) {
        if (!cities.length) return { ...DEFAULT_WORLD };
        let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
        for (const [x, y] of cities) {
            minX = Math.min(minX, x); maxX = Math.max(maxX, x);
            minY = Math.min(minY, y); maxY = Math.max(maxY, y);
        }
        if (maxX === minX && maxY === minY) { // город один, рисуем его в центре
            return { minX: minX - 1, maxX: maxX + 1, minY: minY - 1, maxY: maxY + 1 };
        }
        return { minX, maxX, minY, maxY };
    }

    /**
     * Создаёт преобразование «мир <-> пиксели» для холста width × height (в CSS-пикселях).
     * Масштаб одинаковый по обеим осям, чтобы пропорции не искажались, а мир стоит по центру.
     */
    function makeView(world, width, height) {
        const availW = Math.max(width - 2 * PADDING, 1);
        const availH = Math.max(height - 2 * PADDING, 1);
        const spanX = world.maxX - world.minX;
        const spanY = world.maxY - world.minY;
        const sx = spanX > 0 ? availW / spanX : Infinity; // все города могут лежать на одной вертикали
        const sy = spanY > 0 ? availH / spanY : Infinity;
        const scale = Math.min(sx, sy);
        return {
            world, width, height, scale,
            offX: PADDING + (availW - spanX * scale) / 2,
            offY: PADDING + (availH - spanY * scale) / 2,
        };
    }

    /** Из мира в пиксели. */
    function worldToPx(view, x, y) {
        return [
            view.offX + (x - view.world.minX) * view.scale,
            view.height - view.offY - (y - view.world.minY) * view.scale, // ось Y перевёрнута
        ];
    }

    /** Из пикселей в мир. Обратная операция к worldToPx. */
    function pxToWorld(view, px, py) {
        return [
            view.world.minX + (px - view.offX) / view.scale,
            view.world.minY + (view.height - view.offY - py) / view.scale,
        ];
    }

    /** Замкнутая ломаная по номерам городов: после последнего города линия возвращается к первому. */
    function drawTour(ctx, view, cities, tour, style) {
        if (!tour || tour.length < 2) return;
        ctx.save();
        ctx.strokeStyle = style.color;
        ctx.lineWidth = style.width;
        ctx.lineJoin = "round";
        ctx.beginPath();
        tour.forEach((id, i) => {
            const [px, py] = worldToPx(view, cities[id][0], cities[id][1]);
            if (i === 0) ctx.moveTo(px, py); else ctx.lineTo(px, py);
        });
        ctx.closePath();
        ctx.stroke();
        ctx.restore();
    }

    /** Города в виде кружков с номерами (с нуля, как в API). Стартовый город другого цвета. */
    function drawCities(ctx, view, cities, startId) {
        ctx.save();
        ctx.font = "600 11px system-ui, sans-serif";
        ctx.textAlign = "center";
        ctx.textBaseline = "middle";
        cities.forEach(([x, y], id) => {
            const [px, py] = worldToPx(view, x, y);
            const isStart = id === startId;
            ctx.beginPath();
            ctx.arc(px, py, CITY_RADIUS, 0, Math.PI * 2);
            ctx.fillStyle = isStart ? COLORS.start : COLORS.city;
            ctx.fill();
            ctx.lineWidth = 2;
            ctx.strokeStyle = isStart ? COLORS.start : COLORS.cityStroke;
            ctx.stroke();
            ctx.fillStyle = isStart ? "#ffffff" : COLORS.cityText;
            ctx.fillText(String(id), px, py + 0.5);
        });
        ctx.restore();
    }

    /**
     * Рисует всё. state = { view, cities, snapshot }.
     * Слои идут снизу вверх: сначала маршрут, потом города. Перед рисованием холст очищается.
     */
    function render(ctx, state) {
        const { view, cities, snapshot } = state;
        ctx.clearRect(0, 0, view.width, view.height);

        if (!cities.length) {
            ctx.save();
            ctx.fillStyle = COLORS.hint;
            ctx.font = "15px system-ui, sans-serif";
            ctx.textAlign = "center";
            ctx.fillText("Кликните по холсту, чтобы добавить город", view.width / 2, view.height / 2);
            ctx.restore();
            return;
        }

        const bestTour = snapshot && snapshot.best_tour && snapshot.best_tour.length ? snapshot.best_tour : null;
        if (bestTour) drawTour(ctx, view, cities, bestTour, { color: COLORS.tour, width: 2.5 });
        drawCities(ctx, view, cities, bestTour ? bestTour[0] : -1);
    }

    return { fitWorld, makeView, worldToPx, pxToWorld, render, CITY_RADIUS };
})();
