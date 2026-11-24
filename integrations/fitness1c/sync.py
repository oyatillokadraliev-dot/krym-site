"""
Синхронизация расписания 1С (метод {classes}, ПОДТВЕРЖДЁН документацией
и реальным ответом боевой базы клуба — см. ниже) с локальной таблицей
`trainings`.

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

СТРУКТУРА ОТВЕТА 1С (ПОДТВЕРЖДЕНО реальным вызовом
FitnessClubService(branch='babajka').get_schedule() на боевой базе,
19.11.2026 — не предположение из документации apiary.io):

    {
      "appointment_id": "ad852230-d390-11f1-8d0d-00505683cb0f",
      "canceled": false,
      "reason_for_cancellation": null,
      "service": {"title": "Грэпплинг", "id": "...", ...},
      "group": {"id": "...", "title": "Единоборства"},
      "room": {"id": "...", "title": "Зал борьбы", ...},
      "employee": {"id": "...", "name": "Абдулкадиров Абдулла", ...},
      "capacity": -1,
      "booked": 0,
      "web_capacity": 0,
      "web_booked": 0,
      "available_slots": 50,
      "start_date": "2026-11-19 19:00",
      "end_date": "2026-11-19 21:00",
      "duration": 120,
      "booking_online": true,
      ...
    }

О ПОЛЯХ ВМЕСТИМОСТИ/СВОБОДНЫХ МЕСТ (capacity, booked, web_capacity,
web_booked, available_slots): подтверждено владельцем клуба, что в
данной базе capacity=-1 означает "вместимость не ограничена". Однако
web_capacity/web_booked/available_slots наблюдались в разных и не
всегда согласованных друг с другом сочетаниях (например
web_capacity=0 при available_slots=50), поэтому эти поля НЕ
используются для показа на сайте количества свободных мест — такое
решение было бы домыслом поверх неоднозначных данных (см. задание,
п.8). Единственное поле, которое показывается на сайте как сигнал
доступности записи — это booking_online (ПОДТВЕРЖДЁННЫЙ явный флаг:
true/false).

О ЧАСОВОМ ПОЯСЕ: start_date/end_date приходят строкой без таймзоны
("2026-11-19 19:00") и ПОДТВЕРЖДЕНО представляют уже местное время
Астрахани (Europe/Astrakhan, UTC+4, без перехода на летнее время).
Поэтому ниже эта строка разбирается как наивная дата/время и
сохраняется как есть (date, start_time, end_time) — никакой
конвертации часового пояса делать не нужно и не следует, иначе
время задвоится со сдвигом. Если в будущем потребуется сравнивать
это время с "текущим временем сервера", такое сравнение должно
явно учитывать зону Europe/Astrakhan (UTC+4) — см. ASTRAKHAN_UTC_OFFSET
в app.py.
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


def _split_date_time(raw_datetime, field_name):
    """
    "2026-11-19 19:00" -> ("2026-11-19", "19:00"). Без таймзоны — она
    уже учтена 1С (местное время Астрахани), см. пояснение в шапке
    файла. Не используем datetime.strptime с расчётом на конкретный
    формат секунд — 1С может как присылать, так и не присылать секунды.
    """
    if not raw_datetime or " " not in raw_datetime:
        raise ScheduleFieldMappingError(
            f"Поле {field_name} отсутствует или имеет неожиданный формат "
            f"(ожидалась строка вида 'YYYY-MM-DD HH:MM'): {raw_datetime!r}."
        )
    date_part, time_part = raw_datetime.split(" ", 1)
    # "19:00:00" -> "19:00" на случай, если 1С добавит секунды.
    time_part = time_part[:5]
    return date_part, time_part


def _map_1c_class_to_training(raw_class, branch):
    """
    Превращает одну запись из реального ответа 1С {classes} в словарь
    полей для локальной таблицы `trainings`.

    Возвращает None, если занятие отменено (canceled=true) — такие
    занятия не должны появляться на сайте (задание, п.5) и поэтому не
    сохраняются и не обновляются в `trainings` этим синком вовсе,
    а не просто скрываются на уровне шаблона.
    """
    try:
        external_id = str(raw_class["appointment_id"])
    except KeyError as exc:
        raise ScheduleFieldMappingError(
            f"В ответе 1С отсутствует ожидаемое поле {exc}."
        ) from exc

    if raw_class.get("canceled"):
        return None

    service = raw_class.get("service") or {}
    employee = raw_class.get("employee") or {}
    room = raw_class.get("room") or {}
    group = raw_class.get("group") or {}

    title = service.get("title") or "Занятие"
    sport = group.get("title") or title

    try:
        date, start_time = _split_date_time(raw_class.get("start_date"), "start_date")
    except ScheduleFieldMappingError as exc:
        raise ScheduleFieldMappingError(f"{exc} (appointment_id={external_id})") from exc

    end_time = None
    if raw_class.get("end_date"):
        try:
            end_date_part, end_time = _split_date_time(raw_class["end_date"], "end_date")
            # Групповые занятия 1С не переходят через полночь — если
            # дата окончания вдруг отличается от даты начала, это
            # неожиданная ситуация, и безопаснее не угадывать: не
            # показываем время окончания, чем показываем его для не
            # того дня.
            if end_date_part != date:
                logger.warning(
                    "[fitness1c.sync] end_date отличается по дате от start_date "
                    "для appointment_id=%s — время окончания не сохранено.",
                    external_id,
                )
                end_time = None
        except ScheduleFieldMappingError:
            end_time = None

    capacity = raw_class.get("capacity")
    trainer_external_id = employee.get("id")
    trainer_name = employee.get("name")
    room_title = room.get("title")
    # booking_online — ПОДТВЕРЖДЁННЫЙ явный флаг (см. шапку файла).
    # Если 1С вдруг его не пришлёт, безопаснее считать запись
    # недоступной, чем ошибочно показать "доступна".
    booking_online = 1 if raw_class.get("booking_online") else 0

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
        "trainer_name": trainer_name,
        "room_title": room_title,
        "booking_online": booking_online,
    }


def _resolve_trainer_id(conn, branch, trainer_external_id):
    """
    Сопоставляет внешний id тренера из 1С с локальной записью в
    integration_trainers. Если тренер с таким external_id ещё не
    заведён на сайте — запись занятия всё равно синхронизируется
    (trainer_id останется NULL, но trainer_name из 1С всё равно
    сохраняется и показывается на сайте), чтобы расписание не
    терялось из-за того, что администратор ещё не завёл личный
    кабинет тренера.
    """
    if not trainer_external_id:
        return None
    row = conn.execute(
        "SELECT id FROM integration_trainers WHERE branch = ? AND external_id = ?",
        (branch, str(trainer_external_id)),
    ).fetchone()
    return row["id"] if row else None


def _mark_canceled_if_exists(conn, branch, external_id, stats):
    """
    Если занятие, пришедшее в этом синке как canceled=true, уже было
    ранее сохранено в `trainings` (например, его отменили уже после
    первой синхронизации) — помечаем его статусом 'cancelled', чтобы
    оно перестало показываться на сайте. Физически не удаляем и не
    трогаем bookings: изменение логики записи/отмены явно запрещено
    заданием (п.7), а история записи нужна для отчётности личного
    кабинета тренера.
    """
    existing = conn.execute(
        "SELECT id FROM trainings WHERE branch = ? AND external_id = ?",
        (branch, external_id),
    ).fetchone()
    if existing:
        conn.execute(
            "UPDATE trainings SET status = 'cancelled', synced_at = datetime('now') WHERE id = ?",
            (existing["id"],),
        )
        stats["canceled"] += 1


def sync_schedule_from_1c(raw_classes, branch="babajka"):
    """
    raw_classes — результат FitnessClubService.get_schedule(...) (список
    занятий из 1С, реальная структура полей — см. докстринг модуля).
    Эта функция НЕ вызывает API сама — получает уже готовый ответ,
    чтобы её можно было протестировать на заранее подготовленных
    данных без сети.

    Логика upsert: занятие сопоставляется по (branch, external_id).
    Если такого ещё нет — создаётся новое с source='1c_sync'. Если
    есть — обновляются изменяемые поля (время могло сдвинуться,
    тренер — смениться) и синхронизируется время обновления.

    Отменённые в 1С занятия (canceled=true) не создаются и не
    обновляются как активные; если такое занятие уже было сохранено
    раньше — помечается статусом 'cancelled' (см.
    _mark_canceled_if_exists), иначе просто пропускается.

    Возвращает статистику (created, updated, canceled, failed) —
    полезно показать администратору/в логах, что реально произошло
    при синке.
    """
    stats = {"created": 0, "updated": 0, "canceled": 0, "failed": 0}
    conn = get_connection()
    try:
        for raw_class in raw_classes:
            raw_external_id = raw_class.get("appointment_id")

            try:
                mapped = _map_1c_class_to_training(raw_class, branch)
            except ScheduleFieldMappingError:
                logger.exception("Не удалось сопоставить занятие 1С с локальной моделью")
                stats["failed"] += 1
                continue

            if mapped is None:
                # canceled=true — не показываем на сайте (задание, п.5).
                if raw_external_id:
                    _mark_canceled_if_exists(conn, branch, str(raw_external_id), stats)
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
                        capacity = ?, trainer_id = ?, trainer_name = ?, room_title = ?,
                        booking_online = ?, status = 'scheduled', synced_at = datetime('now')
                    WHERE id = ?
                    """,
                    (
                        mapped["title"], mapped["sport"], mapped["date"],
                        mapped["start_time"], mapped["end_time"], mapped["capacity"],
                        trainer_id, mapped["trainer_name"], mapped["room_title"],
                        mapped["booking_online"], existing["id"],
                    ),
                )
                stats["updated"] += 1
            else:
                conn.execute(
                    """
                    INSERT INTO trainings
                        (branch, trainer_id, sport, title, date, start_time, end_time,
                         capacity, trainer_name, room_title, booking_online,
                         source, external_id, synced_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '1c_sync', ?, datetime('now'))
                    """,
                    (
                        branch, trainer_id, mapped["sport"], mapped["title"],
                        mapped["date"], mapped["start_time"], mapped["end_time"],
                        mapped["capacity"], mapped["trainer_name"], mapped["room_title"],
                        mapped["booking_online"], mapped["external_id"],
                    ),
                )
                stats["created"] += 1

        conn.commit()
    finally:
        conn.close()

    return stats
