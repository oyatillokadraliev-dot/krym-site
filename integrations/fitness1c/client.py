"""
Низкоуровневый HTTP-клиент для API 1С:Фитнес клуб v3.

Источник сведений об авторизации:
  - HTTP Basic Auth (логин и пароль пользователя 1С с правами на HTTP-сервис)
    подтверждён официальным ответом поддержки 1С:Фитнес клуб и документацией
    https://apiary.io.
  - Заголовок `apikey` (ключ авторизации внешнего приложения) подтверждён
    практической проверкой на боевом/тестовом сервере клуба: без него
    сервер 1С возвращает бизнес-ошибку 1062 ("Не указан ключ авторизации
    внешнего приложения").
  - Коды ответа 401 (не авторизован) и 403 (доступ запрещён) обрабатываются
    явно как ошибки авторизации/прав — не как временные сбои, поэтому
    запрос с такими кодами НЕ повторяется автоматически.

Это единственный файл проекта, который реально обращается по сети к 1С.
Все остальные части интеграции работают через FitnessClubService
(service.py), а не напрямую через этот клиент.
"""

import time
import json
import logging

import requests

logger = logging.getLogger("fitness1c")


class Fitness1CConfigError(Exception):
    """Не заданы обязательные параметры подключения (адрес API, логин, пароль, ключ)."""


class Fitness1CAuthError(Exception):
    """
    Сервер 1С ответил 401 или 403 — неверный логин/пароль, либо у
    пользователя 1С нет прав на запрошенный метод. Отдельный класс
    ошибки (не Fitness1CRequestError), чтобы вызывающий код мог
    показать отдельное, более точное сообщение администратору, а не
    общую "ошибку связи с 1С".
    """

    def __init__(self, message, http_status):
        super().__init__(message)
        self.http_status = http_status


class Fitness1CRequestError(Exception):
    """
    Запрос к 1С завершился ошибкой после всех повторных попыток (не
    связанной с авторизацией — см. Fitness1CAuthError для 401/403).
    Хранит исходный HTTP-статус и текст ошибки (без секретов) для
    того, чтобы вызывающий код мог показать клиенту понятное сообщение,
    не подтверждая бронирование вслепую.
    """

    def __init__(self, message, http_status=None):
        super().__init__(message)
        self.http_status = http_status


DEFAULT_TIMEOUT_SECONDS = 10
DEFAULT_MAX_RETRIES = 2
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
AUTH_ERROR_STATUS_CODES = {401, 403}

# Сколько символов текста ответа сервера сохранять в сообщении об
# ошибке/логе.
MAX_ERROR_TEXT_LENGTH = 300


def _safe_error_text(response):
    """
    Возвращает текст ответа сервера, урезанный до безопасной длины.
    Логин, пароль и apikey передаются только в заголовках запроса и
    никогда не включаются в логируемые структуры.
    """
    try:
        text = response.text or ""
    except Exception:
        return "(не удалось прочитать тело ответа)"
    return text[:MAX_ERROR_TEXT_LENGTH]


class Fitness1CClient:
    """
    Тонкая обёртка над requests для вызовов API 1С:Фитнес клуб.

    Параметры подключения намеренно не хранятся в коде — только в
    переменных окружения (см. config.py).
    """

    def __init__(self, base_url, login, password, branch,
                 apikey=None,
                 timeout=DEFAULT_TIMEOUT_SECONDS,
                 max_retries=DEFAULT_MAX_RETRIES, log_fn=None):
        if not base_url:
            raise Fitness1CConfigError(
                "Не задан адрес API 1С (FITNESS1C_BASE_URL). Его нужно "
                "получить у поддержки 1С:Фитнес клуб вместе с логином и "
                "паролем пользователя 1С для Basic Auth."
            )
        if not login:
            raise Fitness1CConfigError(
                "Не задан FITNESS1C_LOGIN. Без него запросы к API "
                "выполняться не будут."
            )
        if not password:
            raise Fitness1CConfigError(
                "Не задан FITNESS1C_PASSWORD. Без него запросы к API "
                "выполняться не будут."
            )
        self.base_url = base_url.rstrip("/")
        self._login = login
        self._password = password
        self._apikey = apikey
        self.branch = branch
        self.timeout = timeout
        self.max_retries = max_retries
        # log_fn(branch, method, request_summary, http_status, success, error_message)
        # — функция записи в integration_log.
        self._log_fn = log_fn

    def _auth(self):
        return (self._login, self._password)

    def _headers(self):
        headers = {"Accept": "application/json"}
        if self._apikey:
            headers["apikey"] = self._apikey
        return headers

    def _log(self, method, request_summary, http_status, success, error_message=None):
        if self._log_fn is None:
            return
        try:
            self._log_fn(
                branch=self.branch,
                method=method,
                request_summary=request_summary,
                http_status=http_status,
                success=success,
                error_message=error_message,
            )
        except Exception:
            # Журналирование не должно ронять основной запрос.
            logger.exception("Не удалось записать integration_log")

    def request(self, method_name, http_method, path, params=None, json_body=None,
                idempotent=True):
        """
        Выполняет запрос к 1С с повторными попытками при временных ошибках
        (сетевые сбои, 429/5xx) и логированием БЕЗ секретов.

        Логин, пароль и apikey передаются ТОЛЬКО через auth= и headers=
        запроса requests — они никогда не попадают в params, json_body,
        request_summary или текст лога.
        """
        url = f"{self.base_url}{path}"

        # В лог пишем ТОЛЬКО метод, путь и урезанные параметры запроса —
        # логин, пароль и apikey сюда никогда не попадают.
        request_summary = json.dumps(
            {"path": path, "params": params, "has_body": json_body is not None},
            ensure_ascii=False,
        )[:500]

        attempt = 0
        last_error = None

        while attempt <= self.max_retries:
            attempt += 1
            try:
                response = requests.request(
                    http_method,
                    url,
                    auth=self._auth(),
                    headers=self._headers(),
                    params=params,
                    json=json_body,
                    timeout=self.timeout,
                )
            except requests.RequestException as exc:
                last_error = str(exc)
                logger.warning(
                    "[fitness1c] Сетевая ошибка при вызове %s (попытка %s/%s): %s",
                    method_name, attempt, self.max_retries + 1, exc,
                )
                if not idempotent:
                    self._log(method_name, request_summary, None, False, last_error)
                    raise Fitness1CRequestError(
                        f"Не удалось связаться с 1С для метода {method_name}, "
                        f"и запрос нельзя безопасно повторить автоматически: {last_error}"
                    )
                time.sleep(min(2 ** attempt, 8))
                continue

            if response.status_code in AUTH_ERROR_STATUS_CODES:
                error_text = _safe_error_text(response)
                self._log(method_name, request_summary, response.status_code, False, error_text)
                if response.status_code == 401:
                    message = (
                        f"1С отклонила авторизацию (401) для метода {method_name} — "
                        f"проверьте FITNESS1C_LOGIN и FITNESS1C_PASSWORD."
                    )
                else:
                    message = (
                        f"1С запретила доступ (403) для метода {method_name} — "
                        f"у пользователя 1С, вероятно, нет прав на этот метод. "
                        f"Нужно обратиться к администратору 1С за расширением прав."
                    )
                raise Fitness1CAuthError(message, http_status=response.status_code)

            if response.status_code in RETRYABLE_STATUS_CODES and attempt <= self.max_retries:
                logger.warning(
                    "[fitness1c] Временная ошибка %s при вызове %s (попытка %s/%s)",
                    response.status_code, method_name, attempt, self.max_retries + 1,
                )
                time.sleep(min(2 ** attempt, 8))
                continue

            success = 200 <= response.status_code < 300
            error_message = None if success else _safe_error_text(response)
            self._log(method_name, request_summary, response.status_code, success, error_message)

            if not success:
                raise Fitness1CRequestError(
                    f"1С вернула ошибку {response.status_code} для метода {method_name}: "
                    f"{error_message}",
                    http_status=response.status_code,
                )

            try:
                return response.json()
            except ValueError:
                return None

        self._log(method_name, request_summary, None, False, last_error)
        raise Fitness1CRequestError(
            f"Не удалось связаться с 1С для метода {method_name} после "
            f"{self.max_retries + 1} попыток: {last_error}"
        )
