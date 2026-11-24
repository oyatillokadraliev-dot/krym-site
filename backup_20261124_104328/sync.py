"""
Синхронизация расписания 1С (метод {classes}, ПОДТВЕРЖДЁН документацией)
с локальной таблицей `trainings`.

Зачем нужен отдельный слой синхронизации, а не прямой вызов API на
каждой загрузке страницы расписания:
  - 1С может быть временно недоступна — сайт не должен в этот момент
    показывать пустое расписание; он показывает последнюю сохранённую
    копию из `trainings` и явно (не навязчиво) отмечает, что данные
    могли устареть.
  - Бронирования в нашей БД (`bookings`) ссылаются на training_id —
    нужен стабильный локальный id, который не исчезает между запросами
    к 1С.
  - Один и тот же вызов `get_schedule()` используется и для обновления
    витрины, и для сверки количества участников — кэшировать его в
    БД дешевле, чем дёргать 1С на каждый клик.

ВАЖНО о структуре ответа 1С: открытая документация подтверждает сам
факт существования метода {classes} и общий смысл параметров (диапазон
дат), но НЕ публикует построчно точные имена полей ответа (id занятия,
название, время начала/окончания, тренер, вместимость). Поэтому ниже
используются разумные по смыслу названия полей (id, title, date,
start_time, end_time, trainer_id, capacity) с одним местом, где их
нужно поправить под реальный ответ 1С — см. _map_1c_class_to_training.
Это нужно уточнить и поправить по факту первого реального ответа API
на тестовой базе, не раньше.
"""

import logging

from db import get_connection

logger = logging.getLogger("fitness1c.sync")


class ScheduleFieldMappingError(Exception):
    """
    Поднимается, если ответ 1С не содержит ожидаемых полей — то есть
    _map_1c_class_to_training() нужно поправить под реальную структуру
    ответа. Это осознанно НЕ подавляется тихим пропуском записи, чтобы
    расхождение сразу было заметно при первом реальном запуске синка,
    а не терялось молча.
    """


def _map_1c_class_to_training(raw_class, branch):
    """
    Превращает одну запись из ответа 1С {classes} в кортеж полей для
    локальной таблицы `trainings`. Содержит предположения об именах
    полей ответа, которые нужно сверить на реальном ответе 1С.
    """
    try:
        external_id = str(raw_class["id"])
        title = raw_class.get("title") or raw_class.get("name") or "Занятие"
        sport = raw_class.get("sport") or raw_class.get("service_name") or title
        date = raw_class["date"]
        start_time = raw_class["start_time"]
        end_time = raw_class.get("end_time")
        capacity = raw_class.get("capacity")
        trainer_external_id = raw_class.get("trainer_id")
    except KeyError as exc:
        raise ScheduleFieldMappingError(
            f"В ответе 1С отсутствует ожидаемое поле {exc}. Структуру "
            f"ответа нужно сверить с реальным API и поправить "
            f"_map_1c_class_to_training в integrations/fitness1c/sync.py."
        ) from exc

    return {
        "branch": branch,
        "external_id": external_id,
        "title": title,
        "sport": sport,
        "date": date,
        "start_time": start_time,
        "end_time": end_time,
        "capacity": capacity,
        "trainer_external_id": trainer_external_id,
    }


def _resolve_trainer_id(conn, branch, trainer_external_id):
    """
    Сопоставляет внешний id тренера из 1С с локальной записью в
    integration_trainers. Если тренер с таким external_id ещё не
    заведён на сайте — запись занятия всё равно синхронизируется
    (trainer_id останется NULL), чтобы расписание не терялось из-за
    того, что администратор ещё не завёл личный кабинет тренера.
    """
    if not trainer_external_id:
        return None
    row = conn.execute(
        "SELECT id FROM integration_trainers WHERE branch = ? AND external_id = ?",
        (branch, str(trainer_external_id)),
    ).fetchone()
    return row["id"] if row else None


def sync_schedule_from_1c(raw_classes, branch="babajka"):
    """
    raw_classes — результат FitnessClubService.get_schedule(...) (список
    занятий из 1С). Эта функция НЕ вызывает API сама — получает уже
    готовый ответ, чтобы её можно было протестировать на заранее
    подготовленных данных без сети.

    Логика upsert: занятие сопоставляется по (branch, external_id).
    Если такого ещё нет — создаётся новое с source='1c_sync'. Если
    есть — обновляются изменяемые поля (время могло сдвинуться,
    тренер — смениться) и синхронизируется время обновления.

    Возвращает статистику (created, updated, failed) — полезно показать
    администратору/в логах, что реально произошло при синке.
    """
    stats = {"created": 0, "updated": 0, "failed": 0}
    conn = get_connection()
    try:
        for raw_class in raw_classes:
            try:
                mapped = _map_1c_class_to_training(raw_class, branch)
            except ScheduleFieldMappingError:
                logger.exception("Не удалось сопоставить занятие 1С с локальной моделью")
                stats["failed"] += 1
                continue

            trainer_id = _resolve_trainer_id(conn, branch, mapped["trainer_external_id"])

            existing = conn.execute(
                "SELECT id FROM trainings WHERE branch = ? AND external_id = ?",
                (branch, mapped["external_id"]),
            ).fetchone()

            if existing:
                conn.execute(
                    """
                    UPDATE trainings
                    SET title = ?, sport = ?, date = ?, start_time = ?, end_time = ?,
                        capacity = ?, trainer_id = ?, synced_at = datetime('now')
                    WHERE id = ?
                    """,
                    (
                        mapped["title"], mapped["sport"], mapped["date"],
                        mapped["start_time"], mapped["end_time"], mapped["capacity"],
                        trainer_id, existing["id"],
                    ),
                )
                stats["updated"] += 1
            else:
                conn.execute(
                    """
                    INSERT INTO trainings
                        (branch, trainer_id, sport, title, date, start_time, end_time,
                         capacity, source, external_id, synced_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, '1c_sync', ?, datetime('now'))
                    """,
                    (
                        branch, trainer_id, mapped["sport"], mapped["title"],
                        mapped["date"], mapped["start_time"], mapped["end_time"],
                        mapped["capacity"], mapped["external_id"],
                    ),
                )
                stats["created"] += 1

        conn.commit()
    finally:
        conn.close()

    return stats
