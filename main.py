import os
import asyncio
import logging
import sqlite3
import aiohttp
import requests
import random
from datetime import datetime
from threading import Thread
from flask import Flask, request, jsonify
import time

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

# ==================== FLASK ДЛЯ WEB SERVER ===================
app = Flask('')


@app.route('/')
def home():
    return """
    <html>
        <head>
            <title>Brainrot Shop Bot</title>
            <meta http-equiv="refresh" content="30">
            <meta http-equiv="Cache-Control" content="no-cache, no-store, must-revalidate">
            <meta http-equiv="Pragma" content="no-cache">
            <meta http-equiv="Expires" content="0">
            <script>
                // Авто-обновление страницы каждые 30 секунд
                setInterval(function() {
                    fetch('/ping').then(r => console.log('Ping:', new Date().toLocaleTimeString()));
                }, 30000);
            </script>
            <style>
                body {
                    font-family: Arial, sans-serif;
                    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                    color: white;
                    text-align: center;
                    padding: 50px;
                }
                .container {
                    background: rgba(255, 255, 255, 0.1);
                    backdrop-filter: blur(10px);
                    border-radius: 20px;
                    padding: 40px;
                    max-width: 600px;
                    margin: 0 auto;
                    box-shadow: 0 8px 32px rgba(0, 0, 0, 0.3);
                }
                h1 {
                    font-size: 2.5em;
                    margin-bottom: 20px;
                }
                .status {
                    font-size: 1.5em;
                    color: #4CAF50;
                    font-weight: bold;
                }
                .uptime {
                    font-size: 1.2em;
                    margin: 20px 0;
                    padding: 15px;
                    background: rgba(0, 0, 0, 0.2);
                    border-radius: 10px;
                }
                .links {
                    margin-top: 30px;
                }
                a {
                    color: #FFD700;
                    text-decoration: none;
                    margin: 0 10px;
                    padding: 10px 20px;
                    border: 2px solid #FFD700;
                    border-radius: 10px;
                    transition: all 0.3s;
                }
                a:hover {
                    background: #FFD700;
                    color: #333;
                }
            </style>
        </head>
        <body>
            <div class="container">
                <h1>🤖 Brainrot Shop Bot</h1>
                <p class="status">✅ Online and Running</p>
                <div class="uptime">
                    <p>🕒 Server Time: {}</p>
                    <p>🤖 Bot Status: <span id="botStatus">Checking...</span></p>
                    <p>🔁 Self-ping: Every 20 seconds</p>
                </div>
                <p>Bot is active and ready to receive commands</p>

                <div class="links">
                    <a href="/health">Health Check</a>
                    <a href="/ping">Ping</a>
                    <a href="/bot-status">Bot Status</a>
                </div>

                <div style="margin-top: 40px; font-size: 0.9em; opacity: 0.8;">
                    <p>Powered by Replit + Flask + Aiogram</p>
                    <p>Auto-restart via UptimeRobot</p>
                    <p>Active ping every 20 seconds</p>
                </div>
            </div>
        </body>
    </html>
    """.format(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))


@app.route('/health')
def health_check():
    return jsonify({
        "status": "online",
        "timestamp": datetime.now().isoformat(),
        "bot": "Steal A Brainrot Shop Bot",
        "version": "2.3",
        "service": "Telegram Bot API",
        "server_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "bot_alive": True,
        "endpoints": {
            "home": "/",
            "health": "/health",
            "ping": "/ping",
            "bot_status": "/bot-status"
        }
    })


@app.route('/ping')
def ping():
    return jsonify({
        "status": "pong",
        "timestamp": datetime.now().isoformat(),
        "server_time": datetime.now().strftime("%H:%M:%S"),
        "bot_running": True
    })


@app.route('/bot-status')
def bot_status():
    return jsonify({
        "status": "bot_running",
        "bot_alive": True,
        "last_activity": datetime.now().isoformat(),
        "uptime": "24/7"
    })


@app.route('/keepalive')
def keepalive():
    return "BOT_ALIVE"


def run_flask():
    app.run(host='0.0.0.0', port=8080)


def keep_alive():
    t = Thread(target=run_flask, daemon=True)
    t.start()


# ==================== ПЕРЕМЕННЫЕ ДЛЯ СЛЕДКИ ЗА БОТОМ ===================
bot_activity_counter = 0
bot_last_active = datetime.now()


# ==================== УЛУЧШЕННЫЙ КЕП-АЛАЙВ ДЛЯ БОТА ===================
async def bot_keep_alive():
    """Постоянная активность для бота, чтобы Replit не останавливал его"""
    global bot_activity_counter, bot_last_active

    while True:
        try:
            bot_activity_counter += 1
            bot_last_active = datetime.now()

            # Создаем активность для бота
            if bot_activity_counter % 30 == 0:  # Каждые 30 циклов (≈10 минут)
                logging.info(f"🤖 Bot keep-alive active. Counter: {bot_activity_counter}")
                logging.info(f"📅 Last bot activity: {bot_last_active.strftime('%H:%M:%S')}")

            # Короткий интервал - 20 секунд
            await asyncio.sleep(20)

        except Exception as e:
            logging.error(f"❌ Bot keep-alive error: {e}")
            await asyncio.sleep(30)


# ==================== СИСТЕМНЫЙ ПИНГ ===================
async def system_ping():
    """Системный пинг для поддержания активности"""
    ping_count = 0

    while True:
        try:
            ping_count += 1

            # Получаем URL нашего приложения
            replit_app_url = os.environ.get("REPLIT_APP_URL", "")

            if not replit_app_url:
                replit_app_url = "https://brainrotbot.gget5897.replit.co"

            # Пингуем разные эндпоинты
            endpoints = ['/', '/ping', '/health', '/bot-status', '/keepalive']
            endpoint = random.choice(endpoints)

            async with aiohttp.ClientSession() as session:
                try:
                    async with session.get(
                            f"{replit_app_url}{endpoint}",
                            timeout=3,
                            headers={'User-Agent': 'BotKeepAlive/1.0'}
                    ) as resp:
                        if resp.status == 200 and ping_count % 50 == 0:
                            logging.info(f"✅ System ping #{ping_count} to {endpoint}")
                except Exception as e:
                    if ping_count % 20 == 0:
                        logging.warning(f"⚠️ System ping error: {e}")

            # Интервал 25 секунд
            await asyncio.sleep(25)

        except Exception as e:
            logging.warning(f"⚠️ System ping loop error: {e}")
            await asyncio.sleep(30)


# ==================== ФОНОВЫЙ КЕП-АЛАЙВ ===================
def background_keep_alive():
    """Фоновая задача для поддержания активности"""
    while True:
        try:
            # Создаем URL
            url = "https://brainrotbot.gget5897.replit.co"

            # Делаем запрос к keepalive эндпоинту
            try:
                response = requests.get(f"{url}/keepalive", timeout=5)
                if response.status_code == 200:
                    current_time = datetime.now().strftime('%H:%M:%S')
                    logging.info(f"🌐 Background keep-alive at {current_time}")
            except Exception as e:
                logging.warning(f"⚠️ Background keep-alive failed: {e}")

            # Интервал 30 секунд
            time.sleep(30)

        except Exception as e:
            logging.error(f"❌ Background keep-alive thread error: {e}")
            time.sleep(30)


# Запускаем фоновый keep-alive в отдельном потоке
background_thread = Thread(target=background_keep_alive, daemon=True)
background_thread.start()

# ==================== НАСТРОЙКА ЛОГИРОВАНИЯ ===================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('bot.log')
    ]
)
logger = logging.getLogger(__name__)

# ==================== ТОКЕН БОТА ===================
TOKEN = os.environ.get("TOKEN") or ""

if TOKEN == "":
    print("❌ ВНИМАНИЕ: Установите токен в Secrets!")
    print("ℹ️ Зайдите в Tools → Secrets и добавьте TOKEN=ваш_токен")

# ==================== ИНИЦИАЛИЗАЦИЯ БОТА ===================
bot = Bot(token=TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# ================== ГЛОБАЛЬНАЯ ПЕРЕМЕННАЯ ДЛЯ ПОРЯДКА ТОВАРОВ ==================
user_product_positions = {}


# ================== СОСТОЯНИЯ (FSM) ==================
class ProductForm(StatesGroup):
    title = State()
    description = State()
    price = State()
    contact = State()


class EditProductForm(StatesGroup):
    waiting_for_field = State()
    waiting_for_new_value = State()


# ================== БАЗА ДАННЫХ ==================
def init_database():
    try:
        conn = sqlite3.connect('brainrot_shop.db')
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            seller_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            description TEXT NOT NULL,
            price TEXT NOT NULL,
            contact TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )''')
        conn.commit()
        conn.close()
        logger.info("📦 База данных готова")
    except Exception as e:
        logger.error(f"❌ Ошибка БД: {e}")


# ================== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ==================
def get_main_menu_keyboard():
    keyboard = [
        [KeyboardButton(text="🛍️ Покупатель")],
        [KeyboardButton(text="💰 Продавец")],
        [KeyboardButton(text="ℹ️ О боте")]
    ]
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)


def get_buyer_keyboard():
    keyboard = [
        [KeyboardButton(text="⏭️ Следующий товар")],
        [KeyboardButton(text="✅ Купить")],
        [KeyboardButton(text="🏠 Главное меню")]
    ]
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)


def get_seller_keyboard():
    keyboard = [
        [KeyboardButton(text="➕ Добавить товар")],
        [KeyboardButton(text="📋 Мои товары")],
        [KeyboardButton(text="✏️ Управление товарами")],
        [KeyboardButton(text="🏠 Главное меню")]
    ]
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)


def create_products_keyboard(products):
    builder = InlineKeyboardBuilder()
    for product in products:
        builder.row(
            InlineKeyboardButton(
                text=f"✏️ #{product[0]} - {product[1][:15]}...",
                callback_data=f"edit_{product[0]}"
            ),
            InlineKeyboardButton(
                text=f"🗑️ #{product[0]}",
                callback_data=f"delete_{product[0]}"
            )
        )
    builder.row(InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_seller"))
    return builder.as_markup()


def get_edit_options_keyboard():
    keyboard = [
        [KeyboardButton(text="📌 Название")],
        [KeyboardButton(text="📝 Описание")],
        [KeyboardButton(text="💰 Цена")],
        [KeyboardButton(text="👤 Контакты")],
        [KeyboardButton(text="❌ Отмена")]
    ]
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)


# ================== ФУНКЦИЯ ДЛЯ ПОЛУЧЕНИЯ СЛЕДУЮЩЕГО ТОВАРА ==================
async def get_next_product_for_user(user_id):
    try:
        conn = sqlite3.connect('brainrot_shop.db')
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM products")
        total_products = c.fetchone()[0]

        if total_products == 0:
            conn.close()
            return None

        current_position = user_product_positions.get(user_id, 0)
        c.execute("SELECT * FROM products ORDER BY id ASC")
        all_products = c.fetchall()
        product = all_products[current_position]

        next_position = current_position + 1
        if next_position >= total_products:
            next_position = 0

        user_product_positions[user_id] = next_position
        conn.close()
        return product

    except Exception as e:
        logger.error(f"❌ Ошибка при получении товара: {e}")
        return None


async def get_first_product():
    try:
        conn = sqlite3.connect('brainrot_shop.db')
        c = conn.cursor()
        c.execute("SELECT * FROM products ORDER BY id ASC LIMIT 1")
        product = c.fetchone()
        conn.close()
        return product
    except Exception as e:
        logger.error(f"❌ Ошибка при получении первого товара: {e}")
        return None


# ================== ОБРАБОТЧИКИ КОМАНД ==================
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    global bot_activity_counter
    bot_activity_counter += 1
    user_product_positions[message.from_user.id] = 0
    await message.answer(
        "🎮 Steal A Brainrot Shop\n\nВыберите свою роль:",
        reply_markup=get_main_menu_keyboard()
    )


@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    global bot_activity_counter
    bot_activity_counter += 1
    await message.answer(
        "🆘 Помощь\n\nОсновные команды:\n/start - начать работу\n/help - эта справка\n\nИспользуйте кнопки меню для навигации."
    )


@dp.message(Command("status"))
async def cmd_status(message: types.Message):
    global bot_activity_counter
    bot_activity_counter += 1
    await message.answer(
        f"🤖 Статус бота:\n\n"
        f"✅ Онлайн и работает\n"
        f"🕒 Время сервера: {datetime.now().strftime('%H:%M:%S')}\n"
        f"👥 Пользователей в памяти: {len(user_product_positions)}\n"
        f"⚡ Активность счетчик: {bot_activity_counter}\n"
        f"🔁 Self-ping: каждые 20 секунд\n"
        f"📡 UptimeRobot: мониторинг каждые 5 минут"
    )


@dp.message(Command("ping"))
async def cmd_ping(message: types.Message):
    global bot_activity_counter
    bot_activity_counter += 1
    await message.answer(f"🏓 Pong! Bot is alive. Activity: {bot_activity_counter}")


# ================== ПОКУПАТЕЛЬ ==================
@dp.message(F.text == "🛍️ Покупатель")
async def buyer_mode(message: types.Message):
    global bot_activity_counter
    bot_activity_counter += 1
    user_product_positions[message.from_user.id] = 0
    init_database()
    product = await get_first_product()

    if product:
        text = f"""🛒 Товар #{product[0]}

📌 Название: {product[2]}
📝 Описание: {product[3]}
💰 Цена: {product[4]}
👤 Контакты: @{product[5]}"""
        await message.answer(text, reply_markup=get_buyer_keyboard())
    else:
        await message.answer(
            "😔 Товаров пока нет\n\nПопросите друзей добавить товары!",
            reply_markup=get_main_menu_keyboard()
        )


@dp.message(F.text == "⏭️ Следующий товар")
async def next_product(message: types.Message):
    global bot_activity_counter
    bot_activity_counter += 1
    product = await get_next_product_for_user(message.from_user.id)

    if product:
        text = f"""🛒 Товар #{product[0]}

📌 Название: {product[2]}
📝 Описание: {product[3]}
💰 Цена: {product[4]}
👤 Контакты: @{product[5]}"""
        await message.answer(text)
    else:
        await message.answer("😔 Товаров больше нет")


@dp.message(F.text == "✅ Купить")
async def buy_product(message: types.Message):
    global bot_activity_counter
    bot_activity_counter += 1
    await message.answer(
        "🎉 Отличный выбор!\n\n"
        "📞 Свяжитесь с продавцом по указанному username.\n\n"
        "⚠️ Будьте осторожны:\n"
        "• Не переводите деньги заранее\n"
        "• Договоритесь о безопасной сделке\n\n"
        "Удачи в игре! 🎮"
    )


# ================== ПРОДАВЕЦ ==================
@dp.message(F.text == "💰 Продавец")
async def seller_mode(message: types.Message):
    global bot_activity_counter
    bot_activity_counter += 1
    conn = sqlite3.connect('brainrot_shop.db')
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM products WHERE seller_id = ?", (message.from_user.id,))
    count = c.fetchone()[0]
    conn.close()
    await message.answer(
        f"💰 Режим продавца\n\n📊 Ваших товаров: {count}\n\nДоступные действия:",
        reply_markup=get_seller_keyboard()
    )


# ================== ДОБАВЛЕНИЕ ТОВАРА ==================
@dp.message(F.text == "➕ Добавить товар")
async def add_product_start(message: types.Message, state: FSMContext):
    global bot_activity_counter
    bot_activity_counter += 1
    await state.set_state(ProductForm.title)
    await message.answer(
        "📝 Добавление нового товара\n\nВведите название товара:",
        reply_markup=ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="❌ Отмена")]], resize_keyboard=True)
    )


@dp.message(F.text == "❌ Отмена")
async def cancel_operation(message: types.Message, state: FSMContext):
    global bot_activity_counter
    bot_activity_counter += 1
    await state.clear()
    await message.answer("❌ Операция отменена", reply_markup=get_seller_keyboard())


@dp.message(ProductForm.title)
async def process_title(message: types.Message, state: FSMContext):
    global bot_activity_counter
    bot_activity_counter += 1
    if len(message.text) > 100:
        await message.answer("❌ Слишком длинное название! Максимум 100 символов.")
        return
    await state.update_data(title=message.text)
    await state.set_state(ProductForm.description)
    await message.answer("📝 Введите описание товара:")


@dp.message(ProductForm.description)
async def process_description(message: types.Message, state: FSMContext):
    global bot_activity_counter
    bot_activity_counter += 1
    await state.update_data(description=message.text)
    await state.set_state(ProductForm.price)
    await message.answer("💰 Введите цену товара (например: 100 Robux):")


@dp.message(ProductForm.price)
async def process_price(message: types.Message, state: FSMContext):
    global bot_activity_counter
    bot_activity_counter += 1
    await state.update_data(price=message.text)
    await state.set_state(ProductForm.contact)
    await message.answer("👤 Введите ваш username для связи (без @):")


@dp.message(ProductForm.contact)
async def process_contact(message: types.Message, state: FSMContext):
    global bot_activity_counter
    bot_activity_counter += 1
    data = await state.get_data()
    try:
        conn = sqlite3.connect('brainrot_shop.db')
        c = conn.cursor()
        c.execute(
            """INSERT INTO products (seller_id, title, description, price, contact) 
               VALUES (?, ?, ?, ?, ?)""",
            (message.from_user.id, data['title'], data['description'], data['price'], message.text)
        )
        conn.commit()
        conn.close()
        await message.answer(
            f"✅ Товар добавлен!\n\n"
            f"📌 Название: {data['title']}\n"
            f"📝 Описание: {data['description']}\n"
            f"💰 Цена: {data['price']}\n"
            f"👤 Контакты: @{message.text}",
            reply_markup=get_seller_keyboard()
        )
    except Exception as e:
        await message.answer(f"❌ Ошибка при сохранении: {e}")
    await state.clear()


# ================== ПРОСМОТР ТОВАРОВ ==================
@dp.message(F.text == "📋 Мои товары")
async def show_my_products(message: types.Message):
    global bot_activity_counter
    bot_activity_counter += 1
    conn = sqlite3.connect('brainrot_shop.db')
    c = conn.cursor()
    c.execute(
        """SELECT id, title, price, contact FROM products WHERE seller_id = ? ORDER BY id DESC""",
        (message.from_user.id,)
    )
    products = c.fetchall()
    conn.close()

    if not products:
        await message.answer(
            "📭 У вас пока нет товаров.\n\nДобавьте первый товар кнопкой '➕ Добавить товар'",
            reply_markup=get_seller_keyboard()
        )
        return

    text = "📋 Ваши товары:\n\n"
    for idx, product in enumerate(products, 1):
        text += f"{idx}. #{product[0]} - {product[1]}\n   💰 {product[2]} | 👤 @{product[3]}\n\n"
    await message.answer(text, reply_markup=get_seller_keyboard())


# ================== УПРАВЛЕНИЕ ТОВАРАМИ ==================
@dp.message(F.text == "✏️ Управление товарами")
async def manage_products(message: types.Message):
    global bot_activity_counter
    bot_activity_counter += 1
    conn = sqlite3.connect('brainrot_shop.db')
    c = conn.cursor()
    c.execute(
        """SELECT id, title, price FROM products WHERE seller_id = ? ORDER BY id DESC""",
        (message.from_user.id,)
    )
    products = c.fetchall()
    conn.close()

    if not products:
        await message.answer("📭 У вас пока нет товаров для управления.", reply_markup=get_seller_keyboard())
        return

    text = "🛠 Управление товарами\n\n"
    for product in products:
        text += f"#{product[0]} - {product[1]} ({product[2]})\n"
    keyboard = create_products_keyboard(products)
    await message.answer(text, reply_markup=keyboard)


# ================== УДАЛЕНИЕ ТОВАРА ==================
@dp.callback_query(F.data.startswith("delete_"))
async def delete_product_callback(callback: types.CallbackQuery):
    global bot_activity_counter
    bot_activity_counter += 1
    product_id = callback.data.split("_")[1]
    try:
        conn = sqlite3.connect('brainrot_shop.db')
        c = conn.cursor()
        c.execute("SELECT title FROM products WHERE id = ? AND seller_id = ?", (product_id, callback.from_user.id))
        product = c.fetchone()
        if not product:
            await callback.answer("❌ Товар не найден или вы не владелец!")
            return
        c.execute("DELETE FROM products WHERE id = ?", (product_id,))
        conn.commit()
        conn.close()
        await callback.message.edit_text(
            f"✅ Товар удален!\n\n🗑️ Удален товар: {product[0]}\n\nСписок обновлен:")
        await show_updated_products_list(callback.message, callback.from_user.id)
    except Exception as e:
        await callback.answer(f"❌ Ошибка при удалении: {e}")
    await callback.answer()


async def show_updated_products_list(message: types.Message, user_id: int):
    global bot_activity_counter
    bot_activity_counter += 1
    conn = sqlite3.connect('brainrot_shop.db')
    c = conn.cursor()
    c.execute("""SELECT id, title, price FROM products WHERE seller_id = ? ORDER BY id DESC""", (user_id,))
    products = c.fetchall()
    conn.close()

    if not products:
        await message.answer("📭 У вас больше нет товаров.", reply_markup=get_seller_keyboard())
        return

    text = "🛠 Управление товарами\n\n"
    for product in products:
        text += f"#{product[0]} - {product[1]} ({product[2]})\n"
    keyboard = create_products_keyboard(products)
    await message.answer(text, reply_markup=keyboard)


# ================== РЕДАКТИРОВАНИЕ ТОВАРА ==================
@dp.callback_query(F.data.startswith("edit_"))
async def edit_product_callback(callback: types.CallbackQuery, state: FSMContext):
    global bot_activity_counter
    bot_activity_counter += 1
    product_id = callback.data.split("_")[1]
    conn = sqlite3.connect('brainrot_shop.db')
    c = conn.cursor()
    c.execute("""SELECT title, description, price, contact FROM products WHERE id = ? AND seller_id = ?""",
              (product_id, callback.from_user.id))
    product = c.fetchone()
    conn.close()

    if not product:
        await callback.answer("❌ Товар не найден или вы не владелец!")
        return

    await state.update_data(
        edit_product_id=product_id,
        edit_product_title=product[0],
        edit_product_description=product[1],
        edit_product_price=product[2],
        edit_product_contact=product[3]
    )
    await state.set_state(EditProductForm.waiting_for_field)

    text = f"""✏️ Редактирование товара #{product_id}

📌 Название: {product[0]}
📝 Описание: {product[1]}
💰 Цена: {product[2]}
👤 Контакты: @{product[3]}

Выберите что хотите изменить:"""
    await callback.message.answer(text, reply_markup=get_edit_options_keyboard())
    await callback.answer()


@dp.message(EditProductForm.waiting_for_field)
async def process_edit_field(message: types.Message, state: FSMContext):
    global bot_activity_counter
    bot_activity_counter += 1
    if message.text == "❌ Отмена":
        await state.clear()
        await message.answer("❌ Редактирование отменено", reply_markup=get_seller_keyboard())
        return

    field_map = {"📌 Название": "title", "📝 Описание": "description", "💰 Цена": "price", "👤 Контакты": "contact"}
    if message.text not in field_map:
        await message.answer("❌ Пожалуйста, выберите поле из списка")
        return

    field = field_map[message.text]
    data = await state.get_data()
    current_value = data[f"edit_product_{field}"]
    await state.update_data(edit_field=field)
    await state.set_state(EditProductForm.waiting_for_new_value)
    await message.answer(
        f"✏️ Редактирование {message.text.lower()}\n\nТекущее значение: {current_value}\n\nВведите новое значение:",
        reply_markup=ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="❌ Отмена")]], resize_keyboard=True)
    )


@dp.message(EditProductForm.waiting_for_new_value)
async def process_new_value(message: types.Message, state: FSMContext):
    global bot_activity_counter
    bot_activity_counter += 1
    if message.text == "❌ Отмена":
        await state.clear()
        await message.answer("❌ Редактирование отменено", reply_markup=get_seller_keyboard())
        return

    data = await state.get_data()
    product_id = data['edit_product_id']
    field = data['edit_field']
    new_value = message.text

    try:
        conn = sqlite3.connect('brainrot_shop.db')
        c = conn.cursor()
        field_column = {"title": "title", "description": "description", "price": "price", "contact": "contact"}[field]
        c.execute(f"UPDATE products SET {field_column} = ? WHERE id = ?", (new_value, product_id))
        conn.commit()
        conn.close()
        await message.answer(f"✅ {field.capitalize()} успешно обновлено!\n\nНовое значение: {new_value}",
                             reply_markup=get_seller_keyboard())
    except Exception as e:
        await message.answer(f"❌ Ошибка при обновлении: {e}")
    await state.clear()


# ================== ВОЗВРАТ В МЕНЮ ==================
@dp.callback_query(F.data == "back_to_seller")
async def back_to_seller_callback(callback: types.CallbackQuery):
    global bot_activity_counter
    bot_activity_counter += 1
    await callback.message.delete()
    await seller_mode(callback.message)


@dp.message(F.text == "🏠 Главное меню")
async def main_menu(message: types.Message):
    global bot_activity_counter
    bot_activity_counter += 1
    user_product_positions[message.from_user.id] = 0
    await cmd_start(message)


@dp.message(F.text == "ℹ️ О боте")
async def about_bot(message: types.Message):
    global bot_activity_counter
    bot_activity_counter += 1
    await message.answer(
        "🤖 Steal A Brainrot Shop Bot\n\n"
        "📌 Версия: 2.3\n🎮 Игра: Brainrot (Roblox)\n\n"
        "Функции:\n• 🛍️ Просмотр товаров\n• 💰 Продажа предметов\n• ✏️ Редактирование товаров\n• 🗑️ Удаление товаров\n\n"
        "Правила:\n• 🚫 Запрещено мошенничество\n• 💬 Общайтесь вежливо\n• ✅ Проверяйте сделки\n\nУдачи в игре! 🎮"
    )


@dp.message()
async def unknown_command(message: types.Message):
    global bot_activity_counter
    bot_activity_counter += 1
    await message.answer("🤔 Я не понял вашу команду.\n\nИспользуйте кнопки меню или команду /start",
                         reply_markup=get_main_menu_keyboard())


# ================== ЗАПУСК БОТА ==================
async def main():
    try:
        print("=" * 70)
        print("🚀 Запуск Brainrot Shop Bot v2.3...")
        print("=" * 70)

        # Запускаем Flask сервер
        keep_alive()
        print("✅ Веб-сервер запущен на порту 8080")

        # Запускаем улучшенный keep-alive для бота
        asyncio.create_task(bot_keep_alive())
        print("🤖 Bot keep-alive активирован (каждые 20 секунд)")

        # Запускаем системный пинг
        asyncio.create_task(system_ping())
        print("🔁 System ping активирован (каждые 25 секунд)")

        # Запускаем фоновый keep-alive
        print("🌐 Фоновый keep-alive запущен")

        # Инициализируем базу данных
        init_database()

        # Получаем информацию о боте
        bot_info = await bot.get_me()
        print(f"🤖 Бот подключен: @{bot_info.username}")
        print(f"👤 Имя бота: {bot_info.first_name}")
        print(f"🆔 ID бота: {bot_info.id}")

        # Удаляем вебхук
        await bot.delete_webhook(drop_pending_updates=True)

        print("🔄 Запускаю polling...")
        print("=" * 70)
        print("✅ БОТ УСПЕШНО ЗАПУЩЕН!")
        print("")
        print("🛡️  УЛУЧШЕННАЯ ЗАЩИТА ОТ СНА:")
        print("   • Bot keep-alive каждые 20 секунд")
        print("   • System ping каждые 25 секунд")
        print("   • Фоновый keep-alive каждые 30 секунд")
        print("   • UptimeRobot каждые 5 минут")
        print("")
        print("📊 Товары показываются ПО ПОРЯДКУ: 1 → 2 → 3 → 4 → 5 → ...")
        print("")
        print("🔗 Для проверки работы бота:")
        print("   • /ping в Telegram - проверить бота")
        print("   • /status в Telegram - полный статус")
        print("=" * 70)
        print("🕒 Бот будет работать 24/7 без выключений!")
        print("⏸️  Для остановки нажмите Ctrl+C")
        print("=" * 70)

        # Запускаем бота с обработкой ошибок
        restart_count = 0
        max_restarts = 100
        while restart_count < max_restarts:
            try:
                await dp.start_polling(bot, skip_updates=True)
            except Exception as e:
                restart_count += 1
                logger.error(f"❌ Бот упал с ошибкой: {e}")
                logger.info(f"🔄 Перезапуск #{restart_count} через 10 секунд...")
                await asyncio.sleep(10)

        logger.error(f"❌ Достигнут максимум перезапусков ({max_restarts}). Остановка.")

    except KeyboardInterrupt:
        print("\n" + "=" * 50)
        print("👋 Бот остановлен пользователем")
        print("=" * 50)
    except Exception as e:
        logger.error(f"💥 Критическая ошибка: {e}")
        print("🔄 Попробуйте перезапустить проект")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n👋 Завершение работы")
    except Exception as e:
        print(f"💥 Фатальная ошибка: {e}")
