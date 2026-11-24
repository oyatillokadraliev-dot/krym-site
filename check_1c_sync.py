"""
Безопасная проверка реального синка расписания Бабайки с 1С.

ЧТО ДЕЛАЕТ:
  - Только ЧИТАЕТ расписание из 1С (FitnessClubService.get_schedule).
  - НЕ создаёт, НЕ отменяет записи клиентов, НЕ трогает посещаемость,
    НЕ делает вообще никаких изменяющих вызовов к 1С — метод get_schedule
    в client.py/service.py использует только GET-запрос.
  - Запускает sync_schedule_from_1c(...) на реальном ответе — это
    пишет ТОЛЬКО в локальную таблицу trainings (в instance/krym.db
    вашего сайта), в саму 1С ничего не отправляется.
  - Печатает сравнение: что пришло из 1С vs что оказалось в БД.

КАК ЗАПУСТИТЬ:
  1. Убедитесь, что вы в корне проекта (там, где лежит app.py, .env).
  2. Убедитесь, что в .env уже стоит FITNESS1C_ENABLED=1 и реальные
     FITNESS1C_BASE_URL / FITNESS1C_LOGIN / FITNESS1C_PASSWORD /
     FITNESS1C_APIKEY / FITNESS1C_CLUB_ID (как у вас уже и есть).
  3. Запустите:
       python3 check_1c_sync.py
     (либо через venv проекта: ./.venv/bin/python3 check_1c_sync.py)

  Скрипт НИЧЕГО не печатает из значений FITNESS1C_LOGIN/PASSWORD/APIKEY —
  только статус "настроено/нет" по каждой переменной.

ПОСЛЕ ЗАПУСКА пришлите мне консольный вывод целиком — я сверю его с
ожидаемым поведением и подготовлю финальный отчёт.
"""

import os
import sys
from datetime import date, timedelta

# Подхватываем .env, если установлен python-dotenv (он есть в requirements.txt проекта)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    print("[!] python-dotenv не установлен — переменные окружения должны быть заданы иначе (например, через export).")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

SECRET_ENV_VARS = ["FITNESS1C_LOGIN", "FITNESS1C_PASSWORD", "FITNESS1C_APIKEY"]


def print_section(title):
    print()
    print("=" * 70)
    print(title)
    print("=" * 70)


def main():
    print_section("1. Проверка конфигурации (без вывода самих секретов)")
    for var in ["FITNESS1C_ENABLED", "FITNESS1C_BASE_URL", "FITNESS1C_CLUB_ID"]:
        print(f"  {var} = {os.environ.get(var) or '(не задано)'}")
    for var in SECRET_ENV_VARS:
        status = "задано" if os.environ.get(var) else "НЕ ЗАДАНО"
        print(f"  {var} = <скрыто> ({status})")

    from integrations.fitness1c import config as fitness1c_config
    ok, message = fitness1c_config.get_config_status()
    print(f"\n  get_config_status() -> ok={ok}, message={message!r}")
    if not ok:
        print("\n[СТОП] Интеграция не настроена согласно config.py. Дальше идти бессмысленно.")
        return

    print_section("2. Реальный вызов 1С: get_schedule (ТОЛЬКО ЧТЕНИЕ)")
    from integrations.fitness1c.service import FitnessClubService
    from integrations.fitness1c.client import (
        Fitness1CConfigError, Fitness1CAuthError, Fitness1CRequestError,
    )

    date_from = date.today().isoformat()
    date_to = (date.today() + timedelta(days=14)).isoformat()
    print(f"  Запрашиваемый период: {date_from} .. {date_to} (14 дней вперёд)")
    print(f"  Филиал: babajka")

    try:
        service = FitnessClubService(branch="babajka")
        raw_classes = service.get_schedule(date_from, date_to)
    except Fitness1CConfigError as exc:
        print(f"\n[ОШИБКА КОНФИГУРАЦИИ] {exc}")
        return
    except Fitness1CAuthError as exc:
        print(f"\n[ОШИБКА АВТОРИЗАЦИИ] HTTP {exc.http_status}: {exc}")
        print("  Проверьте FITNESS1C_LOGIN / FITNESS1C_PASSWORD / FITNESS1C_APIKEY в .env")
        return
    except Fitness1CRequestError as exc:
        print(f"\n[ОШИБКА ЗАПРОСА] {exc}")
        print("  1С недоступна, либо неверный FITNESS1C_BASE_URL / FITNESS1C_CLUB_ID, либо сетевая проблема.")
        return

    print(f"\n  Получено занятий от 1С: {len(raw_classes)}")

    active = [c for c in raw_classes if not c.get("canceled")]
    canceled = [c for c in raw_classes if c.get("canceled")]
    print(f"  Из них активных (canceled=false): {len(active)}")
    print(f"  Из них отменённых (canceled=true): {len(canceled)}")

    print_section("3. Сырые данные 1С (первые 5 занятий, для ручной сверки)")
    for c in raw_classes[:5]:
        print(f"""  appointment_id: {c.get('appointment_id')}
    service.title:     {(c.get('service') or {}).get('title')}
    group.title:        {(c.get('group') or {}).get('title')}
    room.title:         {(c.get('room') or {}).get('title')}
    employee.name:      {(c.get('employee') or {}).get('name')}
    start_date:         {c.get('start_date')}
    end_date:            {c.get('end_date')}
    capacity:           {c.get('capacity')}
    available_slots:    {c.get('available_slots')}
    booked:             {c.get('booked')}
    web_capacity:       {c.get('web_capacity')}
    booking_online:     {c.get('booking_online')}
    canceled:            {c.get('canceled')}
    reason_for_cancellation: {c.get('reason_for_cancellation')}
""")
    if len(raw_classes) > 5:
        print(f"  ... и ещё {len(raw_classes) - 5} занятий (не выведены, чтобы не засорять консоль)")

    print_section("4. Запуск sync_schedule_from_1c() — запись ТОЛЬКО в локальную БД сайта")
    # init_db() создаёт instance/krym.db и папку instance/, если их ещё
    # нет (как это уже происходит при обычном старте app.py). Без этого
    # шага get_connection() падает с "unable to open database file" —
    # это не проблема самого синка, а особенность того, что этот
    # диагностический скрипт запускается отдельно от app.py.
    from db import init_db
    init_db()
    from integrations.fitness1c.sync import sync_schedule_from_1c
    stats = sync_schedule_from_1c(raw_classes, branch="babajka")
    print(f"  Результат синка: {stats}")

    print_section("5. Сверка: что оказалось в локальной БД (trainings) после синка")
    from db import get_connection
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT external_id, title, sport, date, start_time, end_time,
               room_title, trainer_name, booking_online, capacity, status
        FROM trainings
        WHERE branch = 'babajka' AND source = '1c_sync'
          AND date >= ? AND date <= ?
        ORDER BY date, start_time
        """,
        (date_from, date_to),
    ).fetchall()
    conn.close()

    print(f"  Строк в БД (scheduled + cancelled, в рамках периода): {len(rows)}")
    scheduled_count = sum(1 for r in rows if r["status"] == "scheduled")
    cancelled_count = sum(1 for r in rows if r["status"] == "cancelled")
    print(f"  Из них status='scheduled' (будут показаны на сайте): {scheduled_count}")
    print(f"  Из них status='cancelled' (НЕ будут показаны на сайте): {cancelled_count}")

    print("\n  Детали (первые 10 строк):")
    for r in rows[:10]:
        print(f"    [{r['status']:9s}] {r['date']} {r['start_time']}-{r['end_time'] or '?':5s} "
              f"{r['title']:20s} | {r['trainer_name'] or '-':25s} | {r['room_title'] or '-':15s} | "
              f"запись: {'да' if r['booking_online'] else 'нет':3s} | capacity={r['capacity']}")

    print_section("6. Контрольные проверки соответствия 1С <-> БД")
    active_ids = {str(c["appointment_id"]) for c in active}
    db_scheduled_ids = {r["external_id"] for r in rows if r["status"] == "scheduled"}
    canceled_ids = {str(c["appointment_id"]) for c in canceled}
    db_cancelled_ids = {r["external_id"] for r in rows if r["status"] == "cancelled"}

    missing_in_db = active_ids - db_scheduled_ids
    extra_in_db = db_scheduled_ids - active_ids

    print(f"  Активных в 1С (в рамках периода): {len(active_ids)}")
    print(f"  Активных (scheduled) в БД:         {len(db_scheduled_ids)}")
    if missing_in_db:
        print(f"  [!] Есть в 1С как активные, но НЕ попали в БД как scheduled: {missing_in_db}")
        print("      (возможная причина: ошибка маппинга полей — см. вывод sync выше, failed > 0?)")
    else:
        print("  OK: все активные занятия из 1С присутствуют в БД.")

    if extra_in_db:
        print(f"  [i] Есть в БД как scheduled, но НЕ пришли в этом ответе 1С как активные: {extra_in_db}")
        print("      Это НЕ обязательно ошибка — см. п.4 нашего предыдущего аудита:")
        print("      занятие могло быть за пределами запрошенного периода в прошлый раз,")
        print("      либо отменено без явного canceled=true. Требует вашего ручного взгляда.")
    else:
        print("  OK: лишних активных записей в БД не найдено.")

    print(f"\n  Отменённых в 1С (в рамках периода): {len(canceled_ids)}")
    print(f"  Из них учтено как cancelled в БД:    {len(canceled_ids & db_cancelled_ids)}")
    not_cancelled_in_db = canceled_ids - db_cancelled_ids
    if not_cancelled_in_db:
        print(f"  [i] Отменены в 1С, но в БД либо их не было вовсе, либо они не 'cancelled': {not_cancelled_in_db}")
        print("      Это нормально, если это занятие раньше никогда не попадало в БД")
        print("      (отменённые занятия, которых ещё не было в кэше, просто не создаются — см. п.5 задания).")

    print_section("Готово")
    print("Скопируйте весь вывод выше и пришлите его целиком.")


if __name__ == "__main__":
    main()