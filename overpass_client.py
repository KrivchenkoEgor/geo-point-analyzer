"""
overpass_client.py — модуль для запросов к Overpass API (OpenStreetMap).

Работа: отправляет запрос к бесплатному API OpenStreetMap
и возвращает сырые данные о зданиях и магазинах вокруг заданной точки.

Аналогия: это курьер, который ходит на склад карт OpenStreetMap
и приносит нам список домов и супермаркетов в заданном районе.
"""

import logging
import time
import requests

# Настройка логирования — записываем события в файл logs/app.log
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("logs/app.log"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)

# Список зеркал Overpass API — это как несколько складов с одинаковыми данными.
# Если основной сервер перегружен (504) или просит подождать (429),
# переключаемся на следующий. Публичные серверы бесплатные, но часто лимитируют.
OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.openstreetmap.fr/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
]

# Таймаут запроса в секундах — максимальное время ожидания ответа от ОДНОГО зеркала
REQUEST_TIMEOUT = 60

# Сколько раз повторять запрос при ошибке 429 (Too Many Requests)
# или сетевых сбоях. Публичный Overpass API часто просит подождать.
MAX_RETRIES = 3

# Сколько секунд ждать между попытками по умолчанию
# (если сервер не указал Retry-After)
RETRY_WAIT = 5

# User-Agent — «пропуск» на сервер Overpass.
# Сервер отклоняет запросы без него (ошибка 406) — защита от анонимных ботов.
# Правило вежливости: указываем, кто мы и для чего обращаемся.
REQUEST_HEADERS = {
    "User-Agent": "GeoPointAnalyzer/1.0 (educational geo-analysis project)",
    "Accept": "application/json",
}


def query_overpass(query_text: str) -> dict:
    """
    Отправляет запрос к Overpass API и возвращает JSON-ответ.

    Это как отправить курьера на склад: даём ему список (запрос),
    он идёт, приносит JSON с результатами.

    Стратегия надёжности: перебираем зеркала (OVERPASS_ENDPOINTS) и
    повторяем попытки (MAX_RETRIES на каждое зеркало). Это нужно потому,
    что публичные серверы Overpass часто перегружены: то 429 (слишком много
    запросов), то 504 (таймаут шлюза). Несколько зеркал = больше шансов
    получить данные.

    Args:
        query_text: текст запроса на языке Overpass QL

    Returns:
        dict: распарсенный JSON-ответ от сервера

    Raises:
        RuntimeError: если ни одно зеркало не ответило успешно
    """
    # Собираем «перечень ошибок» — для понятного финального сообщения
    errors = []

    for endpoint in OVERPASS_ENDPOINTS:
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                logger.info(
                    f"Запрос к {endpoint} (попытка {attempt}/{MAX_RETRIES})..."
                )
                response = requests.post(
                    endpoint,
                    data={"data": query_text},
                    headers=REQUEST_HEADERS,
                    timeout=REQUEST_TIMEOUT,
                )

                # 429 — слишком много запросов: ждём и повторяем.
                # Сколько ждать — подсказывает сервер в заголовке Retry-After.
                if response.status_code == 429:
                    wait = int(response.headers.get("Retry-After", RETRY_WAIT))
                    logger.warning(f"429 Too Many Requests. Жду {wait}с и повторяю...")
                    time.sleep(wait)
                    continue

                # Любая другая ошибка статуса (5xx и т.п.) — пробуем другое зеркало
                response.raise_for_status()

                result = response.json()
                logger.info(
                    f"Успех: {len(result.get('elements', []))} элементов от {endpoint}"
                )
                return result

            except requests.exceptions.Timeout:
                msg = f"{endpoint}: таймаут"
                logger.warning(msg)
                errors.append(msg)
                break  # таймаут = сервер «тормозит», лучше сменить зеркало
            except requests.exceptions.ConnectionError:
                msg = f"{endpoint}: нет подключения"
                logger.warning(msg)
                errors.append(msg)
                break  # соединение не получилось — сразу следующее зеркало
            except requests.exceptions.HTTPError as e:
                status = e.response.status_code
                msg = f"{endpoint}: HTTP {status}"
                logger.warning(msg)
                errors.append(msg)
                # 5xx (504 и т.п.) — пробуем другое зеркало сразу
                if 500 <= status < 600:
                    break
                # 400 (Bad Request) — координаты вне допустимого диапазона.
                # Retry бессловленен: одни и те же координаты выдадут 400 на всех зеркалах.
                # तुरंत прекращаем попытки и поднимаем ошибку с понятным mensaje.
                if 400 <= status < 500:
                    raise RuntimeError(
                        f"Координаты вне допустимого диапазона: "
                        f"широта от -90 до 90, долгота от -180 до 180. "
                        f"Получен статус {status}."
                    )
                # 4xx (кроме 400, 429 выше) — повторяем попытку
                if attempt < MAX_RETRIES:
                    time.sleep(RETRY_WAIT)
                    continue
            except ValueError:
                msg = f"{endpoint}: не-JSON ответ"
                logger.warning(msg)
                errors.append(msg)
                break
            except requests.exceptions.RequestException as e:
                msg = f"{endpoint}: {e}"
                logger.warning(msg)
                errors.append(msg)
                if attempt < MAX_RETRIES:
                    time.sleep(RETRY_WAIT)
                    continue

    # Ни одно зеркало не сработало
    logger.error(f"Все зеркала Overpass недоступны. Ошибки: {errors}")
    raise RuntimeError(
        "Не удалось получить данные от серверов OpenStreetMap "
        "(все зеркала перегружены или недоступны). "
        "Подождите пару минут и попробуйте снова."
    )


def fetch_buildings_and_supermarkets(lat: float, lon: float, radius: int) -> dict:
    """
    Ищет жилые здания И супермаркеты одним объединённым запросом.

    Объединение двух запросов в один — ключевой приём для работы с
    публичным Overpass API: меньше запросов = меньше шансов получить
    429 (Too Many Requests) и быстрее результат для пользователя.

    В ответе элементы различаем по тегам:
      - building=residential или building=apartments → жилой дом
      - shop=supermarket → супермаркет
    Разделение по тегам делается в analyzer.py.

    Аналогия: отправляем курьера ОДИН раз с двумя списками покупок
    вместо двух рейсов.

    Args:
        lat: широта (например, 54.64)
        lon: долгота (например, 83.30)
        radius: радиус поиска в метрах (например, 1000)

    Returns:
        dict: JSON-ответ от Overpass со всеми найденными элементами
    """
    # Один запрос через union (...) — собираем здания и супермаркеты вместе.
    # out geom; — просим ПОЛНУЮ ГЕОМЕТРИЮ (контуры зданий), а не только центр.
    # Это нужно, чтобы на карте можно было подсветить каждое здание цветом
    # по этажности. За каждый way/relation придёт поле geometry (список точек).
    query = f"""
    [out:json][timeout:30];
    (
      way["building"="residential"](around:{radius},{lat},{lon});
      way["building"="apartments"](around:{radius},{lat},{lon});
      relation["building"="residential"](around:{radius},{lat},{lon});
      relation["building"="apartments"](around:{radius},{lat},{lon});
      node["shop"="supermarket"](around:{radius},{lat},{lon});
      way["shop"="supermarket"](around:{radius},{lat},{lon});
    );
    out geom tags;
    """
    logger.info(
        f"Поиск зданий и супермаркетов: lat={lat}, lon={lon}, radius={radius}м"
    )
    return query_overpass(query.strip())
