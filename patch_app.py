# -*- coding: utf-8 -*-
import sys

REPLACEMENTS = [
    # Блок 1: импорты
    (
'''import os
import functools

from flask import Flask, render_template, request, redirect, url_for, session, flash
from werkzeug.security import check_password_hash, generate_password_hash

from db import get_connection, init_db

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-in-production")

# Ключ JS API Яндекс.Карт — получить бесплатно на developer.tech.yandex.ru
# и указать в переменной окружения YANDEX_MAPS_API_KEY на сервере.
# Без ключа карта на странице просто не подключится (шаблон это обработает).
YANDEX_MAPS_API_KEY = os.environ.get("YANDEX_MAPS_API_KEY", "")

init_db()''',
'''import os
import functools
import logging
from datetime import date, timedelta

from flask import Flask, render_template, request, redirect, url_for, session, flash
from werkzeug.security import check_password_hash, generate_password_hash

from db import get_connection, init_db, log_integration_call
from integrations.fitness1c import config as fitness1c_config
from integrations.fitness1c.service import FitnessClubService
from integrations.fitness1c.client import (
    Fitness1CConfigError,
    Fitness1CAuthError,
    Fitness1CRequestError,
)
from integrations.fitness1c.sync import sync_schedule_from_1c

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-in-production")

logger = logging.getLogger("app")

# Сколько дней вперёд показываем расписание Бабайки на сайте (считая
# сегодняшний день) — обычный диапазон "на эту и следующую неделю".
# Глубину истории назад не запрашиваем: прошедшие занятия на публичной
# витрине не нужны.
BABAJKA_SCHEDULE_DAYS_AHEAD = 14

# Ключ JS API Яндекс.Карт — получить бесплатно на developer.tech.yandex.ru
# и указать в переменной окружения YANDEX_MAPS_API_KEY на сервере.
# Без ключа карта на странице просто не подключится (шаблон это обработает).
YANDEX_MAPS_API_KEY = os.environ.get("YANDEX_MAPS_API_KEY", "")

init_db()'''
    ),
    # Блок 2: dateformat_weekday
    (
'''@app.context_processor
def inject_maps_key():
    return {"yandex_maps_api_key": YANDEX_MAPS_API_KEY}


def normalize_phone(raw):''',
'''@app.context_processor
def inject_maps_key():
    return {"yandex_maps_api_key": YANDEX_MAPS_API_KEY}


@app.template_global("dateformat_weekday")
def dateformat_weekday(date_str):
    """
    'YYYY-MM-DD' -> номер дня недели (0=понедельник, ... 6=воскресенье),
    для шаблона branches.html (расписание Бабайки из 1С). Дата уже
    хранится в локальном времени Астрахани (см. integrations/fitness1c/sync.py),
    поэтому здесь не требуется никакой работы с часовым поясом —
    достаточно разобрать календарную дату.
    """
    return date.fromisoformat(date_str).weekday()


def normalize_phone(raw):'''
    ),
    # Блок 3: новые функции расписания Бабайки
    (
'''def fetch_schedule(branch):
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM schedule_items WHERE branch = ? ORDER BY sort_order", (branch,)
    ).fetchall()
    conn.close()
    return rows


def fetch_prices_by_category(branch):''',
'''def fetch_schedule(branch):
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM schedule_items WHERE branch = ? ORDER BY sort_order", (branch,)
    ).fetchall()
    conn.close()
    return rows


# --------------------------------------------------------------------------
# Расписание филиала "Бабайка" — из 1С (trainings, source='1c_sync').
#
# Филиал Началовское шоссе этот код не использует вообще: он по-прежнему
# работает через fetch_schedule()/schedule_items выше, без изменений.
# --------------------------------------------------------------------------

def _try_sync_babajka_schedule_from_1c():
    """
    Пытается обновить локальную копию расписания Бабайки свежими данными
    из 1С. НИКОГДА не поднимает исключение наружу — любая ошибка (1С не
    настроена, недоступна, вернула неожиданный ответ) логируется, и
    страница расписания просто продолжает показывать последнюю
    сохранённую в БД копию (см. sync.py о том, почему это обоснованно).

    Возвращает (ok: bool, message: str) для необязательного показа
    статуса администратору — на публичной витрине эта информация не
    выводится напрямую, только сам факт "данные могут быть неактуальны".
    """
    ok, message = fitness1c_config.get_config_status()
    if not ok:
        # Интеграция не включена/не настроена — это ожидаемое состояние
        # при разработке и не должно шуметь в логах на уровне warning.
        logger.info("[fitness1c] Синхронизация расписания Бабайки пропущена: %s", message)
        return False, message

    try:
        service = FitnessClubService(branch="babajka", log_fn=log_integration_call)
        date_from = date.today().isoformat()
        date_to = (date.today() + timedelta(days=BABAJKA_SCHEDULE_DAYS_AHEAD)).isoformat()
        raw_classes = service.get_schedule(date_from, date_to)
    except Fitness1CConfigError as exc:
        logger.warning("[fitness1c] Расписание Бабайки: ошибка конфигурации: %s", exc)
        return False, str(exc)
    except Fitness1CAuthError as exc:
        logger.error("[fitness1c] Расписание Бабайки: ошибка авторизации (%s): %s", exc.http_status, exc)
        return False, str(exc)
    except Fitness1CRequestError as exc:
        logger.warning("[fitness1c] Расписание Бабайки: 1С недоступна или вернула ошибку: %s", exc)
        return False, str(exc)
    except Exception:
        # Подстраховка: даже непредвиденная ошибка не должна ронять
        # страницу расписания — показываем то, что уже есть в БД.
        logger.exception("[fitness1c] Расписание Бабайки: непредвиденная ошибка синхронизации")
        return False, "Непредвиденная ошибка синхронизации с 1С."

    try:
        stats = sync_schedule_from_1c(raw_classes, branch="babajka")
    except Exception:
        logger.exception("[fitness1c] Расписание Бабайки: ошибка при сохранении полученных данных")
        return False, "Ошибка при сохранении полученного расписания."

    logger.info(
        "[fitness1c] Синхронизация расписания Бабайки: создано=%s, обновлено=%s, отменено=%s, ошибок=%s",
        stats["created"], stats["updated"], stats["canceled"], stats["failed"],
    )
    return True, "ok"


def fetch_babajka_schedule():
    """
    Возвращает расписание Бабайки для публичной витрины: список строк из
    `trainings` (branch='babajka', source='1c_sync', status='scheduled',
    дата от сегодня на BABAJKA_SCHEDULE_DAYS_AHEAD дней вперёд),
    сгруппированный по дате.

    Сначала пытается обновить данные из 1С (см.
    _try_sync_babajka_schedule_from_1c) — но при любой ошибке продолжает
    работать с тем, что уже есть в локальной БД, и сообщает об этом
    через schedule_is_fresh=False, чтобы шаблон мог (не навязчиво)
    отметить, что расписание может быть устаревшим.

    Отменённые занятия (status='cancelled') не возвращаются — они не
    должны быть видны на сайте (задание, п.5).
    """
    schedule_is_fresh, _sync_message = _try_sync_babajka_schedule_from_1c()

    date_from = date.today().isoformat()
    date_to = (date.today() + timedelta(days=BABAJKA_SCHEDULE_DAYS_AHEAD)).isoformat()

    conn = get_connection()
    rows = conn.execute(
        """
        SELECT * FROM trainings
        WHERE branch = 'babajka' AND source = '1c_sync' AND status = 'scheduled'
          AND date >= ? AND date <= ?
        ORDER BY date, start_time
        """,
        (date_from, date_to),
    ).fetchall()
    conn.close()

    days = []
    current_date = None
    current_group = None
    for row in rows:
        if row["date"] != current_date:
            current_date = row["date"]
            current_group = {"date": current_date, "items": []}
            days.append(current_group)
        current_group["items"].append(row)

    # "configured" здесь означает "есть ли вообще что показать на
    # витрине", а не "включена ли сейчас переменная FITNESS1C_ENABLED":
    # если интеграция настроена и синк прошёл успешно, либо в БД уже
    # есть ранее сохранённая копия расписания (даже если именно сейчас
    # 1С недоступна или интеграция временно выключена) — страница
    # показывает то, что есть, с пометкой свежести. Сообщение
    # "недоступно" нужно только когда данных нет вовсе ни в 1С, ни в
    # локальном кэше.
    return {
        "days": days,
        "is_fresh": schedule_is_fresh,
        "configured": schedule_is_fresh or bool(days) or fitness1c_config.is_enabled(),
    }


def fetch_prices_by_category(branch):'''
    ),
    # Блок 4: подключение в /branches
    (
'''        "babajka": {
            "trainers": fetch_trainers("babajka"),
            "directions": fetch_directions("babajka"),
            "schedule": fetch_schedule("babajka"),
        },''',
'''        "babajka": {
            "trainers": fetch_trainers("babajka"),
            "directions": fetch_directions("babajka"),
            "schedule_1c": fetch_babajka_schedule(),
        },'''
    ),
]


def main():
    path = "app.py"
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()

    for i, (old, new) in enumerate(REPLACEMENTS, start=1):
        count = content.count(old)
        if count == 0:
            print(f"ОШИБКА в блоке {i}: не найден ожидаемый старый текст. Ничего не менял.")
            sys.exit(1)
        if count > 1:
            print(f"ОШИБКА в блоке {i}: найдено {count} совпадений, ожидалось 1. Ничего не менял.")
            sys.exit(1)

    for old, new in REPLACEMENTS:
        content = content.replace(old, new, 1)

    with open(path, "w", encoding="utf-8") as f:
        f.write(content)

    print(f"УСПЕХ: app.py обновлён ({len(REPLACEMENTS)} блока применены).")


if __name__ == "__main__":
    main()
