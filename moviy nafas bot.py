# bot.py
import sqlite3
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters

DB_PATH = 'db1.sql'

# Подключение к БД и регистрация нового пользователя
def register_user(telegram_id, name):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    try:
        cursor.execute(
            "INSERT OR IGNORE INTO users (telegram_id, name, role) VALUES (?, ?, ?)",
            (telegram_id, name, 'volunteer')
        )
        conn.commit()
    finally:
        conn.close()

# Команда /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    register_user(str(user.id), user.first_name)
    await update.message.reply_text(
        f"Привет, {user.first_name}! Добро пожаловать в Moviy Nafas bot. Ты успешно зарегистрирован как волонтёр 🌱"
    )

# Запуск бота
if __name__ == '__main__':
    app = ApplicationBuilder().token("8301193074:AAGUfZ8UlWiIgB2EFSU_kUIMMdMTDHXpRts").build()
    app.add_handler(CommandHandler("start", start))
    app.run_polling()
