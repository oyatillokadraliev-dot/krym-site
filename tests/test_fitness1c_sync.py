"""
Тесты синхронизации расписания 1С -> локальная таблица `trainings`
(integrations/fitness1c/sync.py) и её использования в app.py.

Используются реальные имена полей ответа 1С, подтверждённые первым
настоящим вызовом FitnessClubService(branch='babajka').get_schedule()
на боевой базе клуба (appointment_id, service, group, room, employee,
start_date/end_date, booking_online, canceled) — НЕ предположения из
документации apiary.io.

Каждый тест создаёт отдельную временную SQLite-базу (через
tempfile + db.DB_PATH monkeypatch), чтобы тесты не зависели друг от
друга и не трогали instance/krym.db разработчика.

Запуск: python3 -m pytest tests/test_fitness1c_sync.py -v
        (или python3 tests/test_fitness1c_sync.py напрямую)
"""

import sys
import os
import tempfile
import shutil
import unittest
from datetime import date, timedelta
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db as db_module


def _make_class(appointment_id, start_date, end_date=None, title="Грэпплинг",
                 group_title="Единоборства", room_title="Зал борьбы",
                 trainer_name="Абдулкадиров Абдулла", trainer_id="e1",
                 capacity=-1, booking_online=True, canceled=False,
                 reason_for_cancellation=None):
    """
    Собирает одну запись в реальной структуре ответа 1С {classes},
    как она наблюдалась на боевой базе клуба (филиал 'babajka').
    """
    return {
        "appointment_id": appointment_id,
        "already_booked": None,
        "canceled": canceled,
        "reason_for_cancellation": reason_for_cancellation,
        "booked": 0,
        "web_booked": 0,
        "web_capacity": 0,
        "service": {"title": title, "id": "svc-1"},
        "group": {"id": "grp-1", "title": group_title},
        "room": {"id": "room-1", "title": room_title},
        "employee": {"id": trainer_id, "name": trainer_name},
        "capacity": capacity,
        "available_slots": 50,
        "start_date": start_date,
        "end_date": end_date,
        "duration": 120,
        "commercial": False,
        "booking_online": booking_online,
    }


class Fitness1CSyncTestCase(unittest.TestCase):
    """Общий setUp/tearDown: изолированная временная БД под каждый тест."""

    def setUp(self):
        self._tmp_dir = tempfile.mkdtemp(prefix="krym_test_db_")
        self._original_db_path = db_module.DB_PATH
        db_module.DB_PATH = os.path.join(self._tmp_dir, "test.db")
        db_module.init_db()

        # Импортируем sync.py после подмены DB_PATH, но он использует
        # get_connection() из db.py динамически при каждом вызове, так
        # что подмена модульной переменной перед каждым тестом работает
        # корректно и без дополнительного переимпорта.
        from integrations.fitness1c import sync as sync_module
        self.sync_module = sync_module

    def tearDown(self):
        db_module.DB_PATH = self._original_db_path
        shutil.rmtree(self._tmp_dir, ignore_errors=True)

    def _fetch_trainings(self, branch="babajka"):
        conn = db_module.get_connection()
        rows = conn.execute(
            "SELECT * FROM trainings WHERE branch = ? ORDER BY date, start_time", (branch,)
        ).fetchall()
        conn.close()
        return rows


class TestBasicMapping(Fitness1CSyncTestCase):
    """Разбор реальной структуры ответа 1С в поля `trainings`."""

    def test_single_class_is_created_with_correct_fields(self):
        raw = [_make_class("appt-1", "2026-12-01 19:00", "2026-12-01 21:00")]
        stats = self.sync_module.sync_schedule_from_1c(raw, branch="babajka")

        self.assertEqual(stats, {"created": 1, "updated": 0, "canceled": 0, "failed": 0})

        rows = self._fetch_trainings()
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["external_id"], "appt-1")
        self.assertEqual(row["title"], "Грэпплинг")
        self.assertEqual(row["sport"], "Единоборства")
        self.assertEqual(row["date"], "2026-12-01")
        self.assertEqual(row["start_time"], "19:00")
        self.assertEqual(row["end_time"], "21:00")
        self.assertEqual(row["room_title"], "Зал борьбы")
        self.assertEqual(row["trainer_name"], "Абдулкадиров Абдулла")
        self.assertEqual(row["booking_online"], 1)
        self.assertEqual(row["source"], "1c_sync")
        self.assertEqual(row["status"], "scheduled")

    def test_booking_online_false_is_stored_as_zero(self):
        raw = [_make_class("appt-2", "2026-12-01 19:00", booking_online=False)]
        self.sync_module.sync_schedule_from_1c(raw, branch="babajka")
        row = self._fetch_trainings()[0]
        self.assertEqual(row["booking_online"], 0)

    def test_capacity_minus_one_is_stored_as_is_not_reinterpreted(self):
        """
        capacity=-1 ("не ограничено") сохраняется как есть — синк не
        должен пытаться домысливать конкретное число свободных мест
        из capacity/available_slots/booked/web_capacity (задание, п.8).
        """
        raw = [_make_class("appt-3", "2026-12-01 19:00", capacity=-1)]
        self.sync_module.sync_schedule_from_1c(raw, branch="babajka")
        row = self._fetch_trainings()[0]
        self.assertEqual(row["capacity"], -1)


class TestNoDuplicatesOnReSync(Fitness1CSyncTestCase):
    """Задание, п.2: не создавать дубликаты, appointment_id — идентификатор."""

    def test_second_sync_of_same_class_updates_not_duplicates(self):
        raw = [_make_class("appt-1", "2026-12-01 19:00")]
        stats1 = self.sync_module.sync_schedule_from_1c(raw, branch="babajka")
        stats2 = self.sync_module.sync_schedule_from_1c(raw, branch="babajka")

        self.assertEqual(stats1["created"], 1)
        self.assertEqual(stats2["created"], 0)
        self.assertEqual(stats2["updated"], 1)
        self.assertEqual(len(self._fetch_trainings()), 1)

    def test_changed_time_is_reflected_on_resync(self):
        raw_v1 = [_make_class("appt-1", "2026-12-01 19:00", "2026-12-01 20:00")]
        raw_v2 = [_make_class("appt-1", "2026-12-01 20:00", "2026-12-01 21:00")]
        self.sync_module.sync_schedule_from_1c(raw_v1, branch="babajka")
        self.sync_module.sync_schedule_from_1c(raw_v2, branch="babajka")

        rows = self._fetch_trainings()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["start_time"], "20:00")
        self.assertEqual(rows[0]["end_time"], "21:00")


class TestCanceledClassesAreHidden(Fitness1CSyncTestCase):
    """Задание, п.5: отменённые занятия не должны отображаться на сайте."""

    def test_canceled_class_not_seen_for_first_time_is_not_created(self):
        raw = [_make_class("appt-cancel-1", "2026-12-01 19:00", canceled=True,
                            reason_for_cancellation="Тренер заболел")]
        stats = self.sync_module.sync_schedule_from_1c(raw, branch="babajka")

        self.assertEqual(stats, {"created": 0, "updated": 0, "canceled": 0, "failed": 0})
        self.assertEqual(len(self._fetch_trainings()), 0)

    def test_previously_synced_class_that_becomes_canceled_is_marked_cancelled(self):
        raw_active = [_make_class("appt-1", "2026-12-01 19:00")]
        self.sync_module.sync_schedule_from_1c(raw_active, branch="babajka")

        raw_canceled = [_make_class("appt-1", "2026-12-01 19:00", canceled=True,
                                     reason_for_cancellation="Отменено клубом")]
        stats = self.sync_module.sync_schedule_from_1c(raw_canceled, branch="babajka")

        self.assertEqual(stats["canceled"], 1)
        rows = self._fetch_trainings()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "cancelled")

    def test_mixed_batch_with_active_and_canceled_classes_same_day(self):
        """Несколько занятий в один день, часть из них отменена."""
        raw = [
            _make_class("appt-a", "2026-12-01 18:00", title="Бокс"),
            _make_class("appt-b", "2026-12-01 19:00", title="Йога", canceled=True),
            _make_class("appt-c", "2026-12-01 20:00", title="Плавание"),
        ]
        stats = self.sync_module.sync_schedule_from_1c(raw, branch="babajka")

        self.assertEqual(stats["created"], 2)
        titles = {row["title"] for row in self._fetch_trainings()}
        self.assertEqual(titles, {"Бокс", "Плавание"})
        self.assertNotIn("Йога", titles)


class TestEmptyAndMalformedData(Fitness1CSyncTestCase):
    """Пустое расписание и некорректные/неполные данные от 1С."""

    def test_empty_schedule_does_not_raise(self):
        stats = self.sync_module.sync_schedule_from_1c([], branch="babajka")
        self.assertEqual(stats, {"created": 0, "updated": 0, "canceled": 0, "failed": 0})
        self.assertEqual(self._fetch_trainings(), [])

    def test_missing_appointment_id_is_counted_as_failed_not_raised(self):
        raw = [{"canceled": False, "service": {"title": "Без id"}, "start_date": "2026-12-01 10:00"}]
        stats = self.sync_module.sync_schedule_from_1c(raw, branch="babajka")
        self.assertEqual(stats["failed"], 1)
        self.assertEqual(stats["created"], 0)

    def test_missing_start_date_is_counted_as_failed_not_raised(self):
        raw = [{"appointment_id": "appt-x", "canceled": False, "service": {"title": "Без даты"}}]
        stats = self.sync_module.sync_schedule_from_1c(raw, branch="babajka")
        self.assertEqual(stats["failed"], 1)

    def test_one_malformed_class_does_not_block_others_in_same_batch(self):
        raw = [
            _make_class("good-1", "2026-12-01 18:00", title="Хорошее занятие 1"),
            {"appointment_id": "bad-1", "canceled": False, "service": {"title": "Сломанное"}},
            _make_class("good-2", "2026-12-01 19:00", title="Хорошее занятие 2"),
        ]
        stats = self.sync_module.sync_schedule_from_1c(raw, branch="babajka")
        self.assertEqual(stats, {"created": 2, "updated": 0, "canceled": 0, "failed": 1})

    def test_end_date_on_different_day_is_not_trusted(self):
        """
        Если end_date вдруг указывает на другую календарную дату, чем
        start_date, безопаснее не показывать время окончания, чем
        молча показать его для не того дня.
        """
        raw = [_make_class("appt-1", "2026-12-01 23:30", end_date="2026-12-02 00:30")]
        self.sync_module.sync_schedule_from_1c(raw, branch="babajka")
        row = self._fetch_trainings()[0]
        self.assertIsNone(row["end_time"])
        self.assertEqual(row["date"], "2026-12-01")


class TestTrainerResolution(Fitness1CSyncTestCase):
    """trainer_name из 1С сохраняется и показывается, даже без локального кабинета тренера."""

    def test_trainer_without_local_account_still_synced_with_name(self):
        raw = [_make_class("appt-1", "2026-12-01 19:00", trainer_name="Новый Тренер", trainer_id="unknown-ext-id")]
        self.sync_module.sync_schedule_from_1c(raw, branch="babajka")
        row = self._fetch_trainings()[0]
        self.assertIsNone(row["trainer_id"])
        self.assertEqual(row["trainer_name"], "Новый Тренер")

    def test_trainer_with_matching_local_account_resolves_trainer_id(self):
        conn = db_module.get_connection()
        conn.execute(
            "INSERT INTO integration_trainers (branch, name, password_hash, external_id) "
            "VALUES ('babajka', 'Абдулкадиров Абдулла', 'x', 'e1')"
        )
        conn.commit()
        trainer_local_id = conn.execute(
            "SELECT id FROM integration_trainers WHERE external_id = 'e1'"
        ).fetchone()["id"]
        conn.close()

        raw = [_make_class("appt-1", "2026-12-01 19:00", trainer_id="e1")]
        self.sync_module.sync_schedule_from_1c(raw, branch="babajka")
        row = self._fetch_trainings()[0]
        self.assertEqual(row["trainer_id"], trainer_local_id)


class TestAppIntegrationWithFallback(Fitness1CSyncTestCase):
    """
    Поведение app.fetch_babajka_schedule() на уровне сайта: доступность
    при ошибке 1С (задание, п.9) и сохранение последней копии.
    """

    def setUp(self):
        super().setUp()
        os.environ["FITNESS1C_ENABLED"] = "0"

    def test_no_config_and_no_cached_data_reports_not_configured(self):
        import importlib
        import app as app_module
        importlib.reload(app_module)  # подхватить DB_PATH, подменённый в setUp
        result = app_module.fetch_babajka_schedule()
        self.assertFalse(result["configured"])
        self.assertEqual(result["days"], [])

    def test_api_error_falls_back_to_cached_data_without_raising(self):
        tomorrow = (date.today() + timedelta(days=1)).isoformat() + " "
        raw = [_make_class("appt-1", tomorrow + "19:00", tomorrow + "21:00")]
        self.sync_module.sync_schedule_from_1c(raw, branch="babajka")

        os.environ["FITNESS1C_ENABLED"] = "1"
        os.environ["FITNESS1C_BASE_URL"] = "https://test.invalid/api3"
        os.environ["FITNESS1C_LOGIN"] = "login"
        os.environ["FITNESS1C_PASSWORD"] = "pass"
        os.environ["FITNESS1C_APIKEY"] = "key"
        os.environ["FITNESS1C_CLUB_ID"] = "club1"

        import importlib
        import app as app_module
        importlib.reload(app_module)

        from integrations.fitness1c.client import Fitness1CRequestError

        with patch("app.FitnessClubService") as MockService:
            MockService.return_value.get_schedule.side_effect = Fitness1CRequestError("таймаут")
            result = app_module.fetch_babajka_schedule()

        self.assertFalse(result["is_fresh"])
        self.assertTrue(result["configured"])
        self.assertEqual(len(result["days"]), 1)
        self.assertEqual(result["days"][0]["items"][0]["title"], "Грэпплинг")

        for key in ("FITNESS1C_ENABLED", "FITNESS1C_BASE_URL", "FITNESS1C_LOGIN",
                    "FITNESS1C_PASSWORD", "FITNESS1C_APIKEY", "FITNESS1C_CLUB_ID"):
            os.environ.pop(key, None)


if __name__ == "__main__":
    unittest.main(verbosity=2)
