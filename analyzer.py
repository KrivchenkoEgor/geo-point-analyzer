"""analyzer.py — модуль анализа данных от Overpass API.

Работа: берёт «сырые» данные от OpenStreetMap (список зданий и магазинов)
и превращает их в удобную группировку — дома по этажам, супермаркеты списком.

Аналогия: курьер принёс с склада большую коробку с данными.
Этот модуль — сортировщик: раскладывает по полочкам:
  - дома 1-3 этажа — на первую полку
  - дома 4-9 этажей — на вторую
  - дома 10+ этажей — на третью
  - супермаркеты — отдельная витрина с названиями и расстояниями
"""

import logging
import math
from typing import Any

logger = logging.getLogger(__name__)


def parse_building_levels(levels_str: str) -> int | None:
    """Парсит строку с количеством этажей из OSM-тега building:levels."""
    try:
        levels = int(float(levels_str))
        return levels
    except (ValueError, TypeError):
        return None


# Категории этажности — единый источник правды: и для текстового отчёта,
# и для цвета здания на карте (та же классификация, одни и те же границы).
FLOOR_GROUPS = ["1-3 этажа", "4-9 этажа", "10+ этажа", "Этажность неизвестна"]


def classify_building_levels(levels_str: str | None) -> str:
    """Определяет категорию этажности здания по тегу building:levels."""
    levels = parse_building_levels(levels_str) if levels_str else None
    if levels is None:
        return "Этажность неизвестна"
    if levels <= 3:
        return "1-3 этажа"
    if levels <= 9:
        return "4-9 этажа"
    return "10+ этажа"


def haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Вычисляет расстояние между двумя точками по формуле Хаверсинус."""
    R = 6371000
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)
    a = math.sin(delta_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c


def group_buildings_by_floors(elements: list[dict]) -> dict[str, int]:
    """Группирует жилые здания по количеству этажей."""
    groups = {group: 0 for group in FLOOR_GROUPS}
    for element in elements:
        levels_raw = element.get("tags", {}).get("building:levels")
        groups[classify_building_levels(levels_raw)] += 1
    return groups


def extract_buildings(elements: list[dict]) -> list[dict]:
    """
    Извлекает жилые здания с контурами и категорией этажности.

    Каждое здание — словарь:
      - group: категория этажности (ключ из FLOOR_GROUPS) — для цвета на карте
      - polygon: список точек контура [[lat, lon], ...] (для отрисовки)
      - lat/lon: центр здания (запасной вариант, если контура нет)

    Контур нужен, чтобы на карте подсветить здание цветом по этажности.
    """
    buildings = []
    for element in elements:
        tags = element.get("tags", {})
        levels_raw = tags.get("building:levels")
        geometry = element.get("geometry")
        polygon = None
        if isinstance(geometry, list) and geometry:
            polygon = [[pt["lat"], pt["lon"]] for pt in geometry]
        building = {
            "group": classify_building_levels(levels_raw),
            "polygon": polygon,
            "lat": element.get("lat"),
            "lon": element.get("lon"),
        }
        # Для way/relation контура нет только в исключительных случаях;
        # тогда центр можно взять как среднее по контуру или он придёт от API
        if building["lat"] is None and polygon:
            building["lat"] = sum(p[0] for p in polygon) / len(polygon)
            building["lon"] = sum(p[1] for p in polygon) / len(polygon)
        buildings.append(building)
    return buildings


def extract_supermarkets(elements: list[dict]) -> list[dict]:
    """Извлекает информацию о супермаркетах из ответа Overpass."""
    supermarkets = []
    for element in elements:
        tags = element.get("tags", {})
        name = tags.get("name", "Без названия")
        street = tags.get("addr:street", "")
        housenumber = tags.get("addr:housenumber", "")
        address = f"{street}, {housenumber}".strip(", ") if street or housenumber else "Адрес не указан"
        lat = element.get("lat") or element.get("center", {}).get("lat")
        lon = element.get("lon") or element.get("center", {}).get("lon")
        supermarkets.append({
            "name": name,
            "address": address,
            "lat": lat,
            "lon": lon,
        })
    supermarkets.sort(key=lambda s: s["name"])
    return supermarkets


def split_elements_by_type(elements: list[dict]) -> tuple[list[dict], list[dict]]:
    """Разделяет элементы на здания и супермаркеты по тегам."""
    buildings = []
    supermarkets = []
    for element in elements:
        tags = element.get("tags", {})
        if tags.get("building") in ("residential", "apartments"):
            buildings.append(element)
        elif tags.get("shop") == "supermarket":
            supermarkets.append(element)
    return buildings, supermarkets


def format_results_text(
    floor_groups: dict[str, int],
    supermarkets: list[dict],
    total_buildings: int,
    center_lat: float = None,
    center_lon: float = None,
) -> str:
    """Форматирует результаты анализа в текст для отображения в веб-интерфейсе."""
    import math
    lines = []
    lines.append("## 📊 Результаты анализа территории")
    lines.append("")
    lines.append(f"**Всего жилых зданий найдено:** {total_buildings}")
    lines.append("")
    lines.append("### 🏠 Живые дома по этажности")
    lines.append("")
    for group_name, count in floor_groups.items():
        lines.append(f"  {group_name}: **{count}**")
    lines.append("")
    lines.append(f"### 🛒 Супермаркеты: **{len(supermarkets)}**")
    lines.append("")
    if supermarkets:
        for i, sm in enumerate(supermarkets, 1):
            sm_lat = sm.get("lat")
            sm_lon = sm.get("lon")
            dist_text = "— м"
            if center_lat is not None and center_lon is not None and sm_lat is not None and sm_lon is not None:
                R = 6371000
                phi1 = math.radians(center_lat)
                phi2 = math.radians(sm_lat)
                delta_phi = math.radians(sm_lat - center_lat)
                delta_lambda = math.radians(sm_lon - center_lon)
                a = math.sin(delta_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2
                c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
                dist_m = R * c
                dist_text = f"{round(dist_m)} м"
            addr_part = f" — {sm['address']}" if sm['address'] != "Адрес не указан" else ""
            lines.append(f"  {i}. **{sm['name']}**{addr_part} — {dist_text}")
    else:
        lines.append("  Супермаркеты не найдены в заданном радиусе.")
    return "\n".join(lines)


def analyze_area(
    lat: float,
    lon: float,
    radius: int,
    center_lat: float = None,
    center_lon: float = None,
) -> dict[str, Any]:
    """Главная функция анализа территории."""
    if center_lat is None:
        center_lat = lat
    if center_lon is None:
        center_lon = lon
    import logging
    logger.info(f"Начинаю анализ территории: ({lat}, {lon}), радиус {radius}м")
    from overpass_client import fetch_buildings_and_supermarkets
    raw_data = fetch_buildings_and_supermarkets(lat, lon, radius)
    all_elements = raw_data.get("elements", [])
    building_elements, supermarket_elements = split_elements_by_type(all_elements)
    floor_groups = group_buildings_by_floors(building_elements)
    buildings = extract_buildings(building_elements)
    for sm in supermarket_elements:
        if sm.get("lat") is None:
            sm["lat"] = sm.get("center", {}).get("lat")
        if sm.get("lon") is None:
            sm["lon"] = sm.get("center", {}).get("lon")
    supermarkets = extract_supermarkets(supermarket_elements)
    text = format_results_text(floor_groups, supermarkets, len(building_elements),
                               center_lat=center_lat, center_lon=center_lon)
    logger.info(f"Анализ завершён: {len(building_elements)} зданий, {len(supermarkets)} супермаркетов")
    return {
        "floor_groups": floor_groups,
        "buildings": buildings,
        "supermarkets": supermarkets,
        "total_buildings": len(building_elements),
        "text": text,
    }