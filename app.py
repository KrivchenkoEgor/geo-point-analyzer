"""
app.py — главный файл сервиса GeoPoint Analyzer.

Собирает вместе:
  - FastAPI (веб-сервер) — как «каркас» здания
  - Gradio (веб-интерфейс) — как «витрина» для пользователя
  - analyzer (логика) — «движок», который делает анализ

Запуск одной командой:
    python app.py

После запуска открой в браузере: http://127.0.0.1:7860
"""

import logging
import os

import folium
import gradio as gr
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

# Грузим переменные окружения из .env (если есть)
load_dotenv()

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("logs/app.log"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)

# Импортируем наш «движок» анализа
from analyzer import analyze_area, haversine_distance  # noqa: E402


# Диапазоны для валидации входных данных.
# Это «оградки» — не даём пользователю ввести невозможное.
LAT_MIN, LAT_MAX = -90.0, 90.0       # широта: от -90 (Южный полюс) до 90 (Северный)
LON_MIN, LON_MAX = -180.0, 180.0     # долгота: от -180 до 180
RADIUS_MIN, RADIUS_MAX = 50, 5000    # радиус: 50 м (маленький двор) — 5 км (крупный район)

# Координаты по умолчанию (Новосибирск) — показываются при первом открытии
DEFAULT_LAT, DEFAULT_LON = 54.640406, 83.303459

# «Столик у окна» для выбранной точки: JS-код карты кладёт сюда координаты клика,
# а кнопка «Взять точку с карты» их забирает. Для локального сервиса на одного
# пользователя хватает простого словаря.
selected_point = {"lat": None, "lon": None}


def validate_inputs(lat: float, lon: float, radius: int) -> str | None:
    """
    Проверяет входные данные перед анализом.

    Возвращает текст ошибки, если что-то не так, или None если всё ок.
    Это как охранник на входе: проверяет билеты перед тем, как пустить.
    """
    if not (LAT_MIN <= lat <= LAT_MAX):
        return f"Широта должна быть от {LAT_MIN} до {LAT_MAX}. Вы ввели: {lat}"
    if not (LON_MIN <= lon <= LON_MAX):
        return f"Долгота должна быть от {LON_MIN} до {LON_MAX}. Вы ввели: {lon}"
    if not (RADIUS_MIN <= radius <= RADIUS_MAX):
        return f"Радиус должен быть от {RADIUS_MIN} до {RADIUS_MAX} м. Вы ввели: {radius}"
    return None


# Цвета зданий по категории этажности — «светофор»:
# зелёный = малоэтажка, жёлтый = среднеэтажка, красный = высотка,
# серый = этажность в OSM не указана.
BUILDING_COLORS = {
    "1-3 этажа": "#22c55e",
    "4-9 этажа": "#f59e0b",
    "10+ этажа": "#ef4444",
    "Этажность неизвестна": "#9ca3af",
}


def build_map(
    lat: float,
    lon: float,
    radius: int,
    supermarkets: list[dict],
    buildings: list[dict] | None = None,
) -> str:
    """
    Строит интерактивную карту с маркерами.

    На карте:
      - синий круг = радиус поиска
      - красный маркер = центральная точка
      - цветные контуры = жилые здания (цвет по этажности, см. BUILDING_COLORS)
      - иконки корзины = супермаркеты (с названием во всплывашке)
      - легенда цветов в левом нижнем углу

    Возвращает HTML-код карты для вставки в Gradio через gr.HTML.

    Аналогия: рисуем план района с флажками — где ищем и что нашли.
    """
    # Создаём карту с центром в заданной точке
    m = folium.Map(location=[lat, lon], zoom_start=15)

    # Красный маркер — центральная точка запроса
    folium.Marker(
        location=[lat, lon],
        popup="📍 Точка поиска",
        tooltip="Точка поиска",
        icon=folium.Icon(color="red", icon="star"),
    ).add_to(m)

    # Синий круг — граница радиуса поиска
    folium.Circle(
        radius=radius,
        location=[lat, lon],
        popup=f"Радиус поиска: {radius} м",
        color="#3b82f6",
        fill=True,
        fill_opacity=0.1,
    ).add_to(m)

    # Жилые здания — цветные контуры по этажности
    for b in buildings or []:
        group = b.get("group", "Этажность неизвестна")
        color = BUILDING_COLORS.get(group, "#9ca3af")
        if b.get("polygon"):
            folium.Polygon(
                locations=b["polygon"],
                color=color,
                weight=2,
                fill=True,
                fill_color=color,
                fill_opacity=0.45,
                popup=group,
                tooltip=group,
            ).add_to(m)
        elif b.get("lat") is not None and b.get("lon") is not None:
            # Запасной вариант: здание без контура — цветная точка
            folium.CircleMarker(
                location=[b["lat"], b["lon"]],
                radius=6,
                color=color,
                fill=True,
                fill_color=color,
                fill_opacity=0.8,
                popup=group,
                tooltip=group,
            ).add_to(m)

    # Зелёные маркеры с иконкой корзины — супермаркеты
    for sm in supermarkets:
        sm_lat = sm.get("lat")
        sm_lon = sm.get("lon")
        # Пропускаем магазины без координат (бывает в OSM)
        if sm_lat is None or sm_lon is None:
            continue
        popup_text = f"<b>{sm['name']}</b><br>{sm['address']}"
        folium.Marker(
            location=[sm_lat, sm_lon],
            popup=folium.Popup(popup_text, max_width=250),
            tooltip=sm["name"],
            icon=folium.Icon(color="green", icon="shopping-cart", prefix="fa"),
        ).add_to(m)

    # Легенда — цветные плашки с подписями (левый нижний угол карты)
    legend_items = "".join(
        f'<div style="display:flex;align-items:center;gap:6px">'
        f'<span style="width:14px;height:14px;background:{color};'
        f'border-radius:3px;display:inline-block"></span> {name}</div>'
        for name, color in BUILDING_COLORS.items()
    )
    legend_html = (
        '<div style="position:fixed;bottom:25px;left:25px;z-index:9999;'
        'background:white;padding:8px 12px;border-radius:8px;font-size:12px;'
        'box-shadow:0 1px 4px rgba(0,0,0,.3)">'
        f"<b>Этажность</b>{legend_items}</div>"
    )
    m.get_root().html.add_child(folium.Element(legend_html))

    # Возвращаем HTML карты — Gradio покажет его через gr.HTML
    return m._repr_html_()


def build_picker_map(lat: float, lon: float) -> str:
    """
    Карта-«выборщик»: клик по карте ставит маркер и запоминает точку.

    Как это работает:
      1. Пользователь кликает по карте — плагин ClickForMarker ставит
         зелёный маркер (визуальная обратная связь: «сюда кликнули»).
      2. Одновременно наш JS-обработчик отправляет координаты клика
         на сервер (POST /set_point) — сервер кладёт их в selected_point.
      3. Кнопка «Взять точку с карты» забирает координаты из selected_point
         и подставляет их в поля широты/долготы.

    Возвращает HTML-код карты для gr.HTML.
    """
    m = folium.Map(location=[lat, lon], zoom_start=15)

    # Красный маркер — текущая точка (то, что сейчас в полях)
    folium.Marker(
        location=[lat, lon],
        popup="Текущая точка",
        tooltip="Текущая точка",
        icon=folium.Icon(color="red", icon="star"),
    ).add_to(m)

    # Плагин: клик по карте ставит зелёный маркер
    m.add_child(folium.ClickForMarker())

    # Наш JS: при клике отправляем координаты на сервер.
    # get_name() возвращает имя переменной карты в folium-странице
    # (например, map_5f2a...) — через неё вешаем обработчик клика.
    # ВАЖНО: folium вставляет наш скрипт ДО создания карты, поэтому
    # ждём появления переменной карты (проверка каждые 300 мс).
    map_var = m.get_name()
    click_js = f"""
    (function () {{
        function init_click() {{
            if (typeof {map_var} !== 'undefined') {{
                {map_var}.on('click', function(e) {{
                    fetch('/set_point', {{
                        method: 'POST',
                        headers: {{'Content-Type': 'application/json'}},
                        body: JSON.stringify({{lat: e.latlng.lat, lon: e.latlng.lng}})
                    }}).catch(function() {{}});
                }});
            }} else {{
                setTimeout(init_click, 300);
            }}
        }}
        init_click();
    }})();
    """
    m.get_root().script.add_child(folium.Element(click_js))

    return m._repr_html_()


def run_analysis(lat: float, lon: float, radius: int):
    """
    Главная функция интерфейса: вызывается по кнопке «Анализировать».

    Берёт координаты и радиус, проверяет их, запускает анализ,
    и возвращает три вещи для интерфейса:
      1. текстовый отчёт (Markdown) — с расстояниями до супермаркетов
      2. карта (HTML)
      3. сообщение о статусе (для лога интерфейса)

    Если что-то пошло не так — возвращаем понятное сообщение, не падаем.
    """
    logger.info(f"UI запрос: lat={lat}, lon={lon}, radius={radius}")

    # 1. Валидация входных данных
    error = validate_inputs(lat, lon, radius)
    if error:
        logger.warning(f"Неверный ввод: {error}")
        return f"❌ **Ошибка ввода:** {error}", "", "Проверьте введённые значения."

    try:
        # 2. Запускаем анализ — движок делает запрос к OpenStreetMap
        # Передаем координаты центральной точки для расчета расстояний
        result = analyze_area(lat, lon, radius, center_lat=lat, center_lon=lon)

        # 3. Текстовый отчёт уже содержит расстояния (вычислены в analyzer.py)
        map_html = build_map(
            lat,
            lon,
            radius,
            result["supermarkets"],
            buildings=result["buildings"],
        )

        # 4. Сообщение о статусе
        status = (
            f"✅ Готово! Найдено: {result['total_buildings']} зданий, "
            f"{len(result['supermarkets'])} супермаркетов."
        )
        return result["text"], map_html, status

    except RuntimeError as e:
        # Ошибка от Overpass API — показываем дружелюбное сообщение
        logger.error(f"Ошибка анализа: {e}")
        return f"⚠️ **Не удалось получить данные:** {str(e)}", "", "Ошибка запроса."
    except Exception as e:
        # Любая непредвиденная ошибка — не падаем, показываем пользователю
        logger.exception("Непредвиденная ошибка")
        return f"❌ **Внутренняя ошибка:** {str(e)}", "", "Обратитесь к разработчику."


def take_point_from_map():
    """
    Забирает точку, выбранную кликом по карте, и подставляет её в поля.

    Если пользователь ещё не кликал по карте — вежливо подсказываем.
    Возвращает: широту, долготу, статус, обновлённую карту-выборщик.
    """
    if selected_point["lat"] is None:
        return (
            gr.update(),
            gr.update(),
            "👆 Сначала кликните по карте, чтобы выбрать точку",
            gr.update(),
        )

    lat, lon = selected_point["lat"], selected_point["lon"]
    logger.info(f"Точка взята с карты: {lat}, {lon}")
    return (
        lat,
        lon,
        f"✅ Точка взята с карты: {lat:.6f}, {lon:.6f}",
        build_picker_map(lat, lon),
    )


# ---------------------------------------------------------------------------
# Сборка интерфейса Gradio
# ---------------------------------------------------------------------------
# gr.Blocks — это «конструктор» интерфейса: раскладываем элементы по местам.
# В Gradio 6.0 тему (theme) передаём в launch(), а не в Blocks().
with gr.Blocks(title="GeoPoint Analyzer") as demo:
    gr.Markdown("# 🗺️ GeoPoint Analyzer")
    gr.Markdown(
        "Анализ территории по координатам: **жилые дома по этажности** "
        "и **супермаркеты** в заданном радиусе. "
        "Данные — [OpenStreetMap](https://www.openstreetmap.org)."
    )

    with gr.Row():
        # Левая колонка — ввод данных
        with gr.Column(scale=1):
            lat_input = gr.Number(
                value=DEFAULT_LAT,
                label="Широта (lat)",
                info="От -90 до 90. Например: 54.640406",
            )
            lon_input = gr.Number(
                value=DEFAULT_LON,
                label="Долгота (lon)",
                info="От -180 до 180. Например: 83.303459",
            )
            radius_input = gr.Number(
                value=1000,
                label="Радиус поиска (метры)",
                info="От 50 до 5000 м",
            )
            analyze_btn = gr.Button("🔍 Анализировать", variant="primary")
            status_box = gr.Textbox(label="Статус", interactive=False)

            # --- Выбор точки на карте ---
            gr.Markdown("### 🗺️ Или выберите точку на карте")
            gr.Markdown(
                "*Кликните по карте — появится маркер, "
                "затем нажмите «Взять точку с карты».*"
            )
            # gr.HTML не умеет определять высоту по содержимому (iframe),
            # поэтому задаём её явно — иначе карта невидима и клик не работает
            picker_map = gr.HTML(
                value=build_picker_map(DEFAULT_LAT, DEFAULT_LON),
                height=420,
            )
            pick_btn = gr.Button("📍 Взять точку с карты")

        # Правая колонка — результаты
        with gr.Column(scale=2):
            result_text = gr.Markdown(label="Результаты анализа")
            map_html = gr.HTML(label="Карта")

    # Кнопка запускает анализ, результаты идут в три компонента
    analyze_btn.click(
        fn=run_analysis,
        inputs=[lat_input, lon_input, radius_input],
        outputs=[result_text, map_html, status_box],
    )

    # Кнопка «Взять точку с карты» подставляет кликнутую точку в поля
    pick_btn.click(
        fn=take_point_from_map,
        outputs=[lat_input, lon_input, status_box, picker_map],
    )

    # Подсказка внизу
    gr.Markdown(
        "---\n"
        "*Совет: координаты можно скопировать из URL на "
        "[openstreetmap.org](https://www.openstreetmap.org) "
        "(формат `?lat=..&lon=..`).*"
    )


# ---------------------------------------------------------------------------
# FastAPI — оборачиваем Gradio в полноценный веб-сервер
# ---------------------------------------------------------------------------
# gr.mount_gradio_app прикрепляет интерфейс Gradio к FastAPI-приложению
# по пути "/" — это позволяет добавить отдельные API-эндпоинты.
app = FastAPI(title="GeoPoint Analyzer API")

# CORS: разрешаем запросы из карты (она живёт в отдельном iframe, и браузер
# считает их «чужими»). Для локального сервиса — открываем все источники.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.post("/set_point")
async def set_point(request: Request) -> dict:
    """
    Принимает координаты клика по карте (вызов из JS в браузере)
    и сохраняет их для кнопки «Взять точку с карты».
    """
    data = await request.json()
    lat = float(data.get("lat"))
    lon = float(data.get("lon"))
    if not (LAT_MIN <= lat <= LAT_MAX) or not (LON_MIN <= lon <= LON_MAX):
        logger.warning(f"Отклонены координаты вне диапазона: {lat}, {lon}")
        return {"ok": False, "error": "Координаты вне диапазона"}
    selected_point["lat"] = lat
    selected_point["lon"] = lon
    logger.info(f"Точка выбрана на карте: {lat:.6f}, {lon:.6f}")
    return {"ok": True}


# Монтируем интерфейс Gradio на корень "/" и передаём тему оформления
app = gr.mount_gradio_app(
    app,
    demo,
    path="/",
    theme=gr.themes.Soft(),
    show_error=True,
)


if __name__ == "__main__":
    # Запуск одной командой: python app.py
    # Используем uvicorn напрямую — так FastAPI-эндпоинты (в т.ч. /set_point)
    # работают вместе с интерфейсом Gradio.
    import uvicorn

    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "7860"))

    logger.info(f"Запуск сервиса на http://{host}:{port}")
    uvicorn.run(app, host=host, port=port, log_config=None)
