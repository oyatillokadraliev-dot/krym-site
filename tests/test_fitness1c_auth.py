"""
Тесты HTTP Basic Auth в integrations/fitness1c/client.py.

Никакие реальные учётные данные или адреса 1С здесь не используются —
только фиктивные тестовые значения ("test-login", "test-password",
"https://test.fitness1c.example/api3") и замоканные сетевые запросы
(unittest.mock.patch на уровне requests.request). Это проверяет
КОРРЕКТНОСТЬ НАШЕГО КОДА (что он формирует правильный запрос и
правильно обрабатывает разные коды ответа), а не то, что настоящая 1С
действительно отвечает так — это по-прежнему требует проверки на
реальной тестовой базе, которую этот файл не заменяет.

Запуск: python3 -m pytest tests/test_fitness1c_auth.py -v
        (или python3 tests/test_fitness1c_auth.py напрямую — ниже есть
        ручной раннер на случай, если pytest не установлен в окружении)
"""

import sys
import os
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests
from integrations.fitness1c.client import (
    Fitness1CClient,
    Fitness1CConfigError,
    Fitness1CAuthError,
    Fitness1CRequestError,
)


FAKE_BASE_URL = "https://test.fitness1c.example/api3"
FAKE_LOGIN = "test-login"
FAKE_PASSWORD = "test-password-not-real"


class TestConfigValidation(unittest.TestCase):
    """Клиент должен отказывать сразу и понятно, если параметры не заданы."""

    def test_missing_base_url_raises_config_error(self):
        with self.assertRaises(Fitness1CConfigError):
            Fitness1CClient(base_url="", login=FAKE_LOGIN, password=FAKE_PASSWORD, branch="babajka")

    def test_missing_login_raises_config_error(self):
        with self.assertRaises(Fitness1CConfigError):
            Fitness1CClient(base_url=FAKE_BASE_URL, login="", password=FAKE_PASSWORD, branch="babajka")

    def test_missing_password_raises_config_error(self):
        with self.assertRaises(Fitness1CConfigError):
            Fitness1CClient(base_url=FAKE_BASE_URL, login=FAKE_LOGIN, password="", branch="babajka")


class TestBasicAuthIsUsed(unittest.TestCase):
    """
    Проверяем, что клиент реально передаёт auth=(login, password) в
    requests.request — это и есть HTTP Basic Auth, подтверждённый
    поддержкой 1С. Раньше (до исправления) здесь передавались
    кастомные заголовки apikey/usertoken — тест фиксирует, что это
    больше не так.
    """

    def setUp(self):
        self.client = Fitness1CClient(
            base_url=FAKE_BASE_URL, login=FAKE_LOGIN, password=FAKE_PASSWORD, branch="babajka"
        )

    @patch("integrations.fitness1c.client.requests.request")
    def test_auth_tuple_passed_to_requests(self, mock_request):
        mock_response = MagicMock(status_code=200)
        mock_response.json.return_value = {"ok": True}
        mock_request.return_value = mock_response

        self.client.request("classes", "GET", "/classes", params={"date_from": "2026-01-01"})

        call_kwargs = mock_request.call_args.kwargs
        self.assertIn("auth", call_kwargs)
        self.assertEqual(call_kwargs["auth"], (FAKE_LOGIN, FAKE_PASSWORD))

    @patch("integrations.fitness1c.client.requests.request")
    def test_no_custom_auth_headers_sent(self, mock_request):
        """
        Заголовки запроса не должны содержать старые apikey/usertoken —
        они были ошибочной реконструкцией и теперь убраны полностью.
        """
        mock_response = MagicMock(status_code=200)
        mock_response.json.return_value = {"ok": True}
        mock_request.return_value = mock_response

        self.client.request("classes", "GET", "/classes")

        headers = mock_request.call_args.kwargs.get("headers", {})
        self.assertNotIn("apikey", headers)
        self.assertNotIn("usertoken", headers)


class TestSecretsNeverLeaked(unittest.TestCase):
    """
    Пароль не должен попадать: (а) в текст, который пишется в лог
    (integration_log через log_fn), (б) в текст исключения, которое
    видит вызывающий код/пользователь сайта.
    """

    def setUp(self):
        self.logged_calls = []

        def fake_log(**kwargs):
            self.logged_calls.append(kwargs)

        self.client = Fitness1CClient(
            base_url=FAKE_BASE_URL,
            login=FAKE_LOGIN,
            password=FAKE_PASSWORD,
            branch="babajka",
            log_fn=fake_log,
        )

    @patch("integrations.fitness1c.client.requests.request")
    def test_password_not_in_log_on_success(self, mock_request):
        mock_response = MagicMock(status_code=200)
        mock_response.json.return_value = {"ok": True}
        mock_request.return_value = mock_response

        self.client.request("classes", "GET", "/classes")

        self.assertEqual(len(self.logged_calls), 1)
        log_text = str(self.logged_calls[0])
        self.assertNotIn(FAKE_PASSWORD, log_text)
        self.assertNotIn(FAKE_LOGIN, log_text)

    @patch("integrations.fitness1c.client.requests.request")
    def test_password_not_in_log_on_auth_error(self, mock_request):
        mock_response = MagicMock(status_code=401, text="Unauthorized")
        mock_request.return_value = mock_response

        with self.assertRaises(Fitness1CAuthError):
            self.client.request("classes", "GET", "/classes")

        self.assertEqual(len(self.logged_calls), 1)
        log_text = str(self.logged_calls[0])
        self.assertNotIn(FAKE_PASSWORD, log_text)
        self.assertNotIn(FAKE_LOGIN, log_text)

    @patch("integrations.fitness1c.client.requests.request")
    def test_password_not_in_exception_message(self, mock_request):
        mock_response = MagicMock(status_code=403, text="Forbidden: role missing")
        mock_request.return_value = mock_response

        with self.assertRaises(Fitness1CAuthError) as ctx:
            self.client.request("client_to_class", "POST", "/classes/1/book")

        self.assertNotIn(FAKE_PASSWORD, str(ctx.exception))
        self.assertNotIn(FAKE_LOGIN, str(ctx.exception))


class TestAuthErrorHandling(unittest.TestCase):
    """401 и 403 — отдельный класс ошибки, не повторяются автоматически."""

    def setUp(self):
        self.client = Fitness1CClient(
            base_url=FAKE_BASE_URL, login=FAKE_LOGIN, password=FAKE_PASSWORD, branch="babajka"
        )

    @patch("integrations.fitness1c.client.requests.request")
    def test_401_raises_auth_error_not_request_error(self, mock_request):
        mock_request.return_value = MagicMock(status_code=401, text="Unauthorized")

        with self.assertRaises(Fitness1CAuthError) as ctx:
            self.client.request("classes", "GET", "/classes")

        self.assertEqual(ctx.exception.http_status, 401)
        self.assertEqual(mock_request.call_count, 1, "401 не должен повторяться автоматически")

    @patch("integrations.fitness1c.client.requests.request")
    def test_403_raises_auth_error_with_rights_hint(self, mock_request):
        mock_request.return_value = MagicMock(status_code=403, text="Forbidden")

        with self.assertRaises(Fitness1CAuthError) as ctx:
            self.client.request("client_to_class", "POST", "/classes/1/book", idempotent=False)

        self.assertEqual(ctx.exception.http_status, 403)
        self.assertIn("прав", str(ctx.exception).lower())
        self.assertEqual(mock_request.call_count, 1, "403 не должен повторяться автоматически")

    @patch("integrations.fitness1c.client.requests.request")
    def test_401_is_not_confused_with_retryable_500(self, mock_request):
        """
        Регрессионный тест: 401 не должен попасть в ветку "временная
        ошибка, повторить" — она предназначена для 429/5xx, а не для
        ошибок авторизации.
        """
        mock_request.return_value = MagicMock(status_code=401, text="Unauthorized")

        with self.assertRaises(Fitness1CAuthError):
            self.client.request("classes", "GET", "/classes")

        self.assertEqual(mock_request.call_count, 1)


class TestNonAuthErrorsStillWork(unittest.TestCase):
    """
    Регрессия: поведение для НЕ-авторизационных ошибок (временные 5xx,
    сетевые сбои, обычные 4xx вроде 404) не должно было измениться
    при добавлении обработки 401/403 — список и логика бронирования/
    расписания/филиалов не менялись (задание прямо это требует).
    """

    def setUp(self):
        self.client = Fitness1CClient(
            base_url=FAKE_BASE_URL, login=FAKE_LOGIN, password=FAKE_PASSWORD, branch="babajka"
        )

    @patch("integrations.fitness1c.client.time.sleep")
    @patch("integrations.fitness1c.client.requests.request")
    def test_503_still_retries_and_succeeds(self, mock_request, mock_sleep):
        resp_fail = MagicMock(status_code=503, text="Service Unavailable")
        resp_ok = MagicMock(status_code=200)
        resp_ok.json.return_value = {"ok": True}
        mock_request.side_effect = [resp_fail, resp_ok]

        result = self.client.request("classes", "GET", "/classes")
        self.assertEqual(result, {"ok": True})
        self.assertEqual(mock_request.call_count, 2)

    @patch("integrations.fitness1c.client.requests.request")
    def test_404_raises_request_error_not_auth_error(self, mock_request):
        mock_request.return_value = MagicMock(status_code=404, text="Not Found")

        with self.assertRaises(Fitness1CRequestError):
            self.client.request("classes", "GET", "/classes/does-not-exist")

    @patch("integrations.fitness1c.client.requests.request")
    def test_connection_error_on_non_idempotent_does_not_retry(self, mock_request):
        mock_request.side_effect = requests.ConnectionError("refused")

        with self.assertRaises(Fitness1CRequestError):
            self.client.request("client_to_class", "POST", "/classes/1/book", idempotent=False)

        self.assertEqual(mock_request.call_count, 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
