import asyncio
import logging
from datetime import datetime
import os
from telegram import (# type: ignore
    Update,BotCommand) 
from telegram.ext import ( # type: ignore
    ApplicationBuilder, CommandHandler, MessageHandler,
    filters, ConversationHandler, ContextTypes
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler# type: ignore
from datetime import datetime, timedelta
import random
import sys
import nest_asyncio # type: ignore
from pymongo import MongoClient # type: ignore
from flask import Flask # type: ignore
from threading import Thread

#
#   База данных
#
MONGO_URI = os.getenv("MONGO_URI")

client = MongoClient(MONGO_URI)
# Название базы и коллекций
db = client["aria_bot"]  # база
schedule_col = db["schedules"]  # расписания
tasks_col = db["tasks"]  # задания

#
#   Настройка потоков (Чтобы друг друга не били)
#
nest_asyncio.apply()
if sys.platform.startswith("win"):
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
#
#   Функции
#
random_hi = ["Привет! Я Ария.", "Я Ария, рада знакомству!", "Очень приятно! Ария.","Привет-привет! Я Ария",
             "Салют! Ария на связи.","Здравствуй! Меня зовут Ария.","Рада тебя видеть! Я — Ария.","Добро пожаловать! Я Ария, твой спутник.",
             "Йо! Ария к твоим услугам.","О, ты здесь! Я Ария, приятно познакомиться.","Ария здоровается с тобой!","Хэй! Я Ария. Надеюсь, подружимся.",
             "Как здорово, что ты пришёл! Я — Ария."]

#   Перевод строки в время
def time_str_to_datetime(time_str):
    return datetime.strptime(time_str, "%H:%M")

#   Проверка на проходимость занятий по времени
def is_conflict_schedule(user_id, day, new_start_str):
    new_start = time_str_to_datetime(new_start_str)
    new_end = new_start + timedelta(hours=1, minutes=30)

    # Получаем все занятия пользователя в этот день из MongoDB
    entries = schedule_col.find({"user_id": user_id, "day": day})

    for entry in entries:
        exist_start = time_str_to_datetime(entry["time"])
        exist_end = exist_start + timedelta(hours=1, minutes=30)

        if (new_start < exist_end) and (exist_start < new_end):
            return True  # Пересечение найдено

    return False  # Пересечений нет

    # Хранилище в памяти (в реальном боте — база данных)

user_schedules = {}  # user_id -> {weekday: [(time, subject)]}
user_tasks = []      # [{user_id, subject, datetime, description}]

# Логгинг
logging.basicConfig(level=logging.INFO)

# Создаем планировщик
scheduler = AsyncIOScheduler()

# ========== Команды ==========

#   Функция запуска
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    random_hi[random.randint(0,2)]
    await update.message.reply_text(random_hi[random.randint(0,12)])

#   Функция помощи (Список комманд)
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Вот список команд:\n" \
    "============== Базовые ==============\n" \
    "/start — запустить бота\n" \
    "/help — помощь\n" \
    "\n" \
    "============== Работа с расписанием ==============\n" \
    "/add_schedule — добавить предмет в расписание\n" \
    "/add_task — добавить задание в список задач\n" \
    "/view_schedule — посмотреть полное расписание\n" \
    "/view_tasks — посмотреть полный список задач")

# 1. Добавить занятие в расписание
async def add_schedule(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = update.message.from_user.id
        day, time_str, subject = context.args[0], context.args[1], " ".join(context.args[2:])
        day = day.capitalize()


        # Проверка пересечения через MongoDB
        new_start = datetime.strptime(time_str, "%H:%M")
        new_end = new_start + timedelta(hours=1, minutes=30)

        # Загружаем все занятия пользователя в этот день
        existing = schedule_col.find({"user_id": user_id, "day": day})

        for item in existing:
            exist_start = datetime.strptime(item["time"], "%H:%M")
            exist_end = exist_start + timedelta(hours=1, minutes=30)
            if (new_start < exist_end) and (exist_start < new_end):
                await update.message.reply_text("Ошибка: В расписании уже есть занятие на это время или пересекается с существующим.")
                return
        
        # Добавляем новое занятие
        schedule_col.insert_one({
            "user_id": user_id,
            "day": day,
            "time": time_str,
            "subject": subject
        })

        await update.message.reply_text(f"Добавлено: {day}, {time_str} — {subject}")

    except Exception as e:
        await update.message.reply_text("Формат: /add_schedule Понедельник 14:00 Математика")

# 2. Добавить персональное задание
async def add_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = update.message.from_user.id
        subject = context.args[0]
        date_str = context.args[1] + " " + context.args[2]
        description = " ".join(context.args[3:])
        task_time = datetime.strptime(date_str, "%Y-%m-%d %H:%M")

        task = {
            "user_id": user_id,
            "subject": subject,
            "time": task_time,
            "desc": description
        }

        tasks_col.insert_one(task)

        # Планируем напоминание (оставляем как есть)
        scheduler.add_job(send_task_reminder, 'date', run_date=task_time, args=[user_id, subject, description])

        await update.message.reply_text(f"Задача добавлена: {subject} — {description} в {task_time}")

    except Exception as e:
        await update.message.reply_text("Формат: /add_task Математика 2025-06-22 18:00 Сделать дз")

# 3. Просмотр расписания
async def view_schedule(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    cursor = schedule_col.find({"user_id": user_id})
    schedule = {}
    for item in cursor:  # если используешь motor, тут async; иначе обычный for
        # Но с pymongo просто for
        # Меняем на обычный цикл:
        pass

    # Если используешь pymongo (не motor), то так:
    schedule_items = list(schedule_col.find({"user_id": user_id}))

    if not schedule_items:
        await update.message.reply_text("Расписание пусто.")
        return

    schedule = {}
    for item in schedule_items:
        day = item["day"]
        if day not in schedule:
            schedule[day] = []
        schedule[day].append((item["time"], item["subject"]))

    text = "Твоё расписание:\n"
    for day, entries in schedule.items():
        text += f"{day}\n"
        for time, subject in sorted(entries):
            text += f"{time}: {subject}\n"
    await update.message.reply_text(text)

# 4. Просмотр задач
async def view_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    tasks = list(tasks_col.find({"user_id": user_id}))

    if not tasks:
        await update.message.reply_text("Нет задач.")
        return

    text = "Твои задачи:\n"
    for t in tasks:
        text += f"{t['subject']} — {t['desc']} в {t['time'].strftime('%Y-%m-%d %H:%M')}\n"
    await update.message.reply_text(text)

# Функция отправки напоминания
async def send_task_reminder(user_id, subject, desc):
    try:
        await app.bot.send_message(chat_id=user_id, text=f"🔔 Напоминание по предмету {subject}: {desc}")
    except Exception as e:
        print("Ошибка при отправке уведомления:", e)

# Допка к функции отправки напоминания
def schedule_send_task_reminder(user_id, subject, desc):
    asyncio.create_task(send_task_reminder(user_id, subject, desc))
#   
#   Токен и команды
#
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("help", help_command))
app.add_handler(CommandHandler("add_schedule", add_schedule))
app.add_handler(CommandHandler("add_task", add_task))
app.add_handler(CommandHandler("view_schedule", view_schedule))
app.add_handler(CommandHandler("view_tasks", view_tasks))
#
#   Отображение команд в меню бота
#
async def set_commands(app):
    commands = [
        BotCommand("start", "Начать работу с ботом"),
        BotCommand("help", "отобразить все комманды"),
        BotCommand("add_schedule", "Добавить занятие в расписание"),
        BotCommand("add_task", "Добавить персональное задание"),
        BotCommand("view_schedule", "Просмотреть расписание"),
        BotCommand("view_tasks", "Просмотреть задачи"),
    ]
    await app.bot.set_my_commands(commands)

#
#   говорим боту жить 
# 
app_flask = Flask('')

@app_flask.route('/')
def home():
    return "Я жив!"

def run():
    app_flask.run(host='0.0.0.0', port=8080)

def keep_alive():
    t = Thread(target=run)
    t.start()

#
#   Запуск я так понимаю
#
async def main():
    scheduler.start()
    await set_commands(app)
    await app.run_polling()

if __name__ == '__main__':
    nest_asyncio.apply()
    keep_alive()
    asyncio.get_event_loop().run_until_complete(main())
