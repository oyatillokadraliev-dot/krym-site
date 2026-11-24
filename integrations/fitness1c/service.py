"""
Сервис интеграции с 1С:Фитнес клуб — единственная точка входа, через
которую остальной код сайта (app.py) обращается к 1С. Ничего в app.py
не должно формировать HTTP-запросы к 1С напрямую.

Статус каждого метода указан явно в его docstring:
  ПОДТВЕРЖДЕНО    — метод есть в открытой документации
                     https://apiary.io, реализован и
                     проверен на реальном сервере 1С.
  НЕ ПОДТВЕРЖДЕНО — метод в открытой документации не найден. Функция
                     существует (чтобы остальной код мог её вызывать и
                     единообразно обработать результат), но поднимает
                     NotImplementedInAPIError с понятным объяснением,
                     а не выдумывает ответ и не возвращает тестовые данные.
"""

from . import config
from .client import (
    Fitness1CClient,
    Fitness1CRequestError,
    Fitness1CConfigError,
    Fitness1CAuthError,
)


class NotImplementedInAPIError(Exception):
    """
    Поднимается методами, для которых в открытой документации 1С
    (fitness1cv3.docs.apiary.io) не найдено подтверждённого способа
    выполнить операцию.
    """

    def __init__(self, message, suggested_alternatives=None):
        super().__init__(message)
        self.suggested_alternatives = suggested_alternatives or []


class FitnessClubService:
    """
    branch здесь всегда 'babajka' — филиал на 1С. Сервис не рассчитан
    на вызов для 'nachalovskoe' (там нет и не будет интеграции).
    """

    def __init__(self, branch="babajka", log_fn=None):
        if branch != "babajka":
            raise ValueError(
                "FitnessClubService поддерживает только филиал 'babajka' — "
                "для 'nachalovskoe' используется локальная система сайта, "
                "без обращений к 1С."
            )
        self.branch = branch
        self._client = None
        self._log_fn = log_fn

    def _get_client(self):
        if self._client is None:
            ok, message = config.get_config_status()
            if not ok:
                raise Fitness1CConfigError(message)
            self._client = Fitness1CClient(
                base_url=config.get_base_url(),
                login=config.get_login(),
                password=config.get_password(),
                branch=self.branch,
                apikey=config.get_apikey(),
                log_fn=self._log_fn,
            )
        return self._client

    # ------------------------------------------------------------------
    # ПОДТВЕРЖДЕНО: получение расписания занятий за период.
    # ------------------------------------------------------------------
    def get_schedule(self, date_from, date_to):
        """
        date_from, date_to — строки 'YYYY-MM-DD'.
        Возвращает сырой список занятий из 1С (из поля 'data' ответа /classes).
        """
        client = self._get_client()
        response = client.request(
            method_name="classes",
            http_method="GET",
            path="/classes",
            params={
                "club_id": config.get_club_id(),
                "start_date": date_from,
                "end_date": date_to,
            },
            idempotent=True,
        )
        if isinstance(response, dict):
            return response.get("data") or []
        return response or []

    # ------------------------------------------------------------------
    # ПОДТВЕРЖДЕНО: подробности занятия.
    # ------------------------------------------------------------------
    def get_class_description(self, class_id):
        client = self._get_client()
        return client.request(
            method_name="class_descriptions",
            http_method="GET",
            path=f"/classes/{class_id}",
            idempotent=True,
        )

    # ------------------------------------------------------------------
    # ПОДТВЕРЖДЕНО: количество свободных мест на занятии.
    # ------------------------------------------------------------------
    def get_available_slots(self, class_id):
        client = self._get_client()
        return client.request(
            method_name="available_slots",
            http_method="GET",
            path=f"/classes/{class_id}/available_slots",
            idempotent=True,
        )

    # ------------------------------------------------------------------
    # ПОДТВЕРЖДЕНО: запись клиента на групповое занятие.
    # ------------------------------------------------------------------
    def book_client_to_class(self, class_id, client_phone):
        if not client_phone:
            raise ValueError(
                "book_client_to_class требует идентификатор клиента "
                "(ожидается телефон клиента в 1С) — без него неясно, "
                "кого записывать на занятие."
            )
        client = self._get_client()
        return client.request(
            method_name="client_to_class",
            http_method="POST",
            path=f"/classes/{class_id}/book",
            json_body={"client_phone": client_phone},
            idempotent=False,
        )

    # ------------------------------------------------------------------
    # ПОДТВЕРЖДЕНО: отмена записи клиента на групповое занятие.
    # ------------------------------------------------------------------
    def cancel_client_from_class(self, class_id, client_phone):
        if not client_phone:
            raise ValueError("cancel_client_from_class требует идентификатор клиента.")
        client = self._get_client()
        return client.request(
            method_name="client_from_class",
            http_method="DELETE",
            path=f"/classes/{class_id}/book",
            json_body={"client_phone": client_phone},
            idempotent=False,
        )

    # ------------------------------------------------------------------
    # НЕ ПОДТВЕРЖДЕНО (заглушки по заданию заказчика).
    # ------------------------------------------------------------------
    def get_client_usertoken(self, client_phone):
        raise NotImplementedInAPIError(
            "Отдельный usertoken клиента не используется: интеграция работает "
            "через Basic Auth пользователя 1С и заголовок apikey."
        )

    def get_class_participants(self, class_id):
        raise NotImplementedInAPIError(
            "Метод получения полного списка участников занятия (включая "
            "записи, созданные администратором прямо в 1С) не найден в "
            "открытой документации api3."
        )

    def push_attendance(self, class_id, client_external_id, attended):
        raise NotImplementedInAPIError(
            "Метод передачи отметки посещаемости в 1С не подтверждён "
            "документацией api3 однозначно."
        )