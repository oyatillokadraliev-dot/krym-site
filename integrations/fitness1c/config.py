import os


def is_enabled():
    return os.environ.get("FITNESS1C_ENABLED", "") == "1"


def get_base_url():
    return os.environ.get("FITNESS1C_BASE_URL", "")


def get_login():
    return os.environ.get("FITNESS1C_LOGIN", "")


def get_password():
    return os.environ.get("FITNESS1C_PASSWORD", "")


def get_apikey():
    return os.environ.get("FITNESS1C_APIKEY", "")


def get_club_id():
    return os.environ.get("FITNESS1C_CLUB_ID", "")


def get_config_status():
    """
    Возвращает (ok: bool, message: str) — используется роутами сайта,
    чтобы показать администратору/разработчику, чего не хватает,
    не роняя сайт. Сообщение никогда не включает сами секреты —
    только то, что переменная не задана.
    """
    if not is_enabled():
        return False, "Интеграция с 1С выключена (FITNESS1C_ENABLED не установлена в '1')."
    if not get_base_url():
        return False, "Не задан FITNESS1C_BASE_URL."
    if not get_login():
        return False, "Не задан FITNESS1C_LOGIN."
    if not get_password():
        return False, "Не задан FITNESS1C_PASSWORD."
    if not get_apikey():
        return False, "Не задан FITNESS1C_APIKEY."
    if not get_club_id():
        return False, "Не задан FITNESS1C_CLUB_ID."
    return True, "ok"