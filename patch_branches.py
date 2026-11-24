# -*- coding: utf-8 -*-
import sys

OLD_BLOCK = '''        {% set babajka_days_order = ['Пн, ср, пт', 'Вт, чт, сб', 'Пн, вт, чт, сб'] %}
        {% for days_group in babajka_days_order %}
          {% set group_items = data.babajka.schedule | selectattr('days', 'equalto', days_group) | list %}
          {% if group_items %}
          <h3 class="price-group-title" style="margin-bottom: 16px;">
            {{ {'Пн, ср, пт': 'Понедельник, среда, пятница', 'Вт, чт, сб': 'Вторник, четверг, суббота', 'Пн, вт, чт, сб': 'Понедельник, вторник, четверг, суббота'}[days_group] }}
          </h3>
          <div class="schedule-table" style="margin-bottom: 36px;">
            <div class="schedule-row is-head">
              <div>Время</div>
              <div>Занятие</div>
            </div>
            {% for item in group_items %}
            <div class="schedule-row">
              <div class="schedule-time">{{ item.time_range }}</div>
              <div><div class="schedule-class">{{ item.class_name }}</div></div>
            </div>
            {% endfor %}
          </div>
          {% endif %}
        {% endfor %}'''

NEW_BLOCK = '''        {% set ru_weekdays = {0: 'Понедельник', 1: 'Вторник', 2: 'Среда', 3: 'Четверг', 4: 'Пятница', 5: 'Суббота', 6: 'Воскресенье'} %}
        {% set ru_months = {1: 'января', 2: 'февраля', 3: 'марта', 4: 'апреля', 5: 'мая', 6: 'июня', 7: 'июля', 8: 'августа', 9: 'сентября', 10: 'октября', 11: 'ноября', 12: 'декабря'} %}

        {% if not data.babajka.schedule_1c.configured %}
          <p class="schedule-sub">Расписание групповых занятий временно недоступно для отображения на сайте.</p>
        {% elif not data.babajka.schedule_1c.days %}
          <p class="schedule-sub">На ближайшие дни групповых занятий не запланировано. Актуальное расписание можно уточнить у администратора клуба.</p>
        {% else %}
          {% if not data.babajka.schedule_1c.is_fresh %}
          <p class="schedule-sub" style="margin-bottom: 16px;">Не удалось обновить расписание из системы клуба прямо сейчас — показана последняя сохранённая версия, она может быть немного неактуальной.</p>
          {% endif %}
          {% for day in data.babajka.schedule_1c.days %}
            {% set day_date = day.date | string %}
            {% set parsed_date = day_date.split('-') %}
            <h3 class="price-group-title" style="margin-bottom: 16px;">
              {{ ru_weekdays[dateformat_weekday(day_date)] }}, {{ parsed_date[2] | int }} {{ ru_months[parsed_date[1] | int] }}
            </h3>
            <div class="schedule-table" style="margin-bottom: 36px;">
              <div class="schedule-row is-head">
                <div>Время</div>
                <div>Занятие</div>
                <div>Зал</div>
                <div>Запись</div>
              </div>
              {% for item in day['items'] %}
              <div class="schedule-row">
                <div class="schedule-time">{{ item.start_time }}{% if item.end_time %}–{{ item.end_time }}{% endif %}</div>
                <div>
                  <div class="schedule-class">{{ item.title }}</div>
                  {% if item.trainer_name %}<div class="schedule-trainer">{{ item.trainer_name }}</div>{% endif %}
                </div>
                <div class="schedule-trainer">{{ item.room_title or '—' }}</div>
                <div class="schedule-branch">
                  {% if item.booking_online %}Доступна{% else %}Недоступна{% endif %}
                </div>
              </div>
              {% endfor %}
            </div>
          {% endfor %}
        {% endif %}'''

path = "templates/branches.html"
with open(path, "r", encoding="utf-8") as f:
    content = f.read()

count = content.count(OLD_BLOCK)
if count == 0:
    print("ОШИБКА: не найден ожидаемый старый блок в файле.")
    print("Возможно, файл уже был изменён или отличается от ожидаемого — ничего не менял.")
    sys.exit(1)
if count > 1:
    print(f"ОШИБКА: старый блок встретился {count} раз(а) — ожидался ровно один. Ничего не менял.")
    sys.exit(1)

new_content = content.replace(OLD_BLOCK, NEW_BLOCK)
with open(path, "w", encoding="utf-8") as f:
    f.write(new_content)

print("УСПЕХ: templates/branches.html обновлён (заменён ровно 1 блок).")
