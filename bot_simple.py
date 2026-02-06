import asyncio
import logging
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
import sqlite3
import sys
import os

# ================= КОНФИГУРАЦИЯ =================
TOKEN = "8597607925:AAHTZ9QEtJZUUkkXglxiog_XVssqpQmr01o"  # Замените на ваш токен

# Проверяем, не запущен ли уже бот
if os.path.exists('bot_running.lock'):
    print("❌ ОШИБКА: Бот уже запущен! Закройте предыдущее окно.")
    sys.exit(1)

# Создаем lock-файл
with open('bot_running.lock', 'w') as f:
    f.write('running')

# Удаляем lock при завершении
import atexit


def cleanup():
    if os.path.exists('bot_running.lock'):
        os.remove('bot_running.lock')


atexit.register(cleanup)

# ================= ИНИЦИАЛИЗАЦИЯ =================
bot = Bot(token=TOKEN)
dp = Dispatcher()
logging.basicConfig(level=logging.INFO)
print("🤖 Бот запускается...")


# ================= БАЗА ДАННЫХ =================
def init_db():
    conn = sqlite3.connect('shop.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS offers
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  title TEXT, description TEXT, price TEXT, contact TEXT)''')
    conn.commit()
    conn.close()
    print("📦 База данных готова")


# ================= КОМАНДЫ =================
@dp.message(Command("start"))
async def start(message: types.Message):
    init_db()

    keyboard = types.ReplyKeyboardMarkup(
        keyboard=[
            [types.KeyboardButton(text="🛍️ Покупатель")],
            [types.KeyboardButton(text="💰 Продавец")],
            [types.KeyboardButton(text="ℹ️ О боте")]
        ],
        resize_keyboard=True
    )

    await message.answer(
        "🎮 *Steal A Brainrot Shop*\n\n"
        "Выберите роль:",
        parse_mode="Markdown",
        reply_markup=keyboard
    )


@dp.message(lambda m: m.text == "ℹ️ О боте")
async def about(message: types.Message):
    await message.answer(
        "🤖 *Steal A Brainrot Shop*\n\n"
        "Бот для покупки/продажи предметов из Brainrot (Roblox)\n\n"
        "👨‍💻 Разработчик: @ваш_ник",
        parse_mode="Markdown"
    )


@dp.message(lambda m: m.text == "🛍️ Покупатель")
async def buyer_mode(message: types.Message):
    conn = sqlite3.connect('shop.db')
    c = conn.cursor()
    c.execute("SELECT * FROM offers ORDER BY RANDOM() LIMIT 1")
    offer = c.fetchone()
    conn.close()

    if offer:
        text = f"""
🛒 *Товар*

📌 *Название:* {offer[1]}
📝 *Описание:* {offer[2]}
💰 *Цена:* {offer[3]}
👤 *Контакты:* @{offer[4]}

Листайте дальше командой /next
        """

        keyboard = types.ReplyKeyboardMarkup(
            keyboard=[
                [types.KeyboardButton(text="⏭️ Следующий")],
                [types.KeyboardButton(text="✅ Купить")],
                [types.KeyboardButton(text="🏠 Меню")]
            ],
            resize_keyboard=True
        )

        await message.answer(text, parse_mode="Markdown", reply_markup=keyboard)
    else:
        await message.answer("😔 Товаров пока нет")


@dp.message(lambda m: m.text == "💰 Продавец")
async def seller_mode(message: types.Message):
    keyboard = types.ReplyKeyboardMarkup(
        keyboard=[
            [types.KeyboardButton(text="➕ Добавить товар")],
            [types.KeyboardButton(text="🏠 Меню")]
        ],
        resize_keyboard=True
    )

    await message.answer(
        "💰 *Режим продавца*\n\n"
        "Добавляйте товары на продажу",
        parse_mode="Markdown",
        reply_markup=keyboard
    )


@dp.message(lambda m: m.text == "➕ Добавить товар")
async def add_item(message: types.Message):
    # Простой способ - сразу запросим все данные
    await message.answer(
        "📝 Отправьте данные товара в ОДНОМ сообщении через запятую:\n"
        "Формат: Название, Описание, Цена, Ваш_username\n\n"
        "Пример: Меч Brainrot, Редкий меч из игры, 100 Robux, seller123",
        reply_markup=types.ReplyKeyboardRemove()
    )

    # Ждем ответ
    @dp.message()
    async def process_item_data(msg: types.Message):
        try:
            parts = msg.text.split(',', 3)
            if len(parts) == 4:
                title, desc, price, contact = [p.strip() for p in parts]

                conn = sqlite3.connect('shop.db')
                c = conn.cursor()
                c.execute("INSERT INTO offers (title, description, price, contact) VALUES (?, ?, ?, ?)",
                          (title, desc, price, contact))
                conn.commit()
                conn.close()

                await msg.answer(
                    f"✅ Товар добавлен!\n\n"
                    f"📌 {title}\n"
                    f"💰 {price}\n"
                    f"👤 @{contact}",
                    reply_markup=types.ReplyKeyboardMarkup(
                        keyboard=[[types.KeyboardButton(text="🏠 Меню")]],
                        resize_keyboard=True
                    )
                )
            else:
                await msg.answer("❌ Неправильный формат! Попробуйте снова.")
        except Exception as e:
            await msg.answer(f"❌ Ошибка: {e}")


@dp.message(lambda m: m.text == "⏭️ Следующий")
async def next_item(message: types.Message):
    # Просто вызываем buyer_mode снова
    await buyer_mode(message)


@dp.message(lambda m: m.text == "✅ Купить")
async def buy_item(message: types.Message):
    await message.answer(
        "🎉 Отлично!\n\n"
        "Свяжитесь с продавцом по указанному username для покупки.\n\n"
        "Удачи в игре! 🎮",
        reply_markup=types.ReplyKeyboardMarkup(
            keyboard=[[types.KeyboardButton(text="⏭️ Следующий товар")],
                      [types.KeyboardButton(text="🏠 Меню")]],
            resize_keyboard=True
        )
    )


@dp.message(lambda m: m.text == "🏠 Меню" or m.text == "🏠 Меню")
async def main_menu(message: types.Message):
    keyboard = types.ReplyKeyboardMarkup(
        keyboard=[
            [types.KeyboardButton(text="🛍️ Покупатель")],
            [types.KeyboardButton(text="💰 Продавец")],
            [types.KeyboardButton(text="ℹ️ О боте")]
        ],
        resize_keyboard=True
    )
    await message.answer("🏠 Главное меню", reply_markup=keyboard)


# ================= ЗАПУСК =================
async def main():
    # Удаляем вебхук (если был)
    await bot.delete_webhook(drop_pending_updates=True)

    print("✅ Бот запущен! Откройте Telegram и найдите бота.")
    print("⚠️ Если бот не отвечает, проверьте токен.")

    try:
        await dp.start_polling(bot)
    finally:
        # Удаляем lock-файл при завершении
        if os.path.exists('bot_running.lock'):
            os.remove('bot_running.lock')


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n👋 Бот остановлен")
    except Exception as e:
        print(f"❌ Ошибка: {e}")
        if os.path.exists('bot_running.lock'):
            os.remove('bot_running.lock')