import asyncio
import logging
import sqlite3
from datetime import datetime
import os

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.client.default import DefaultBotProperties  # Импортируем для новой версии aiogram

# ==================== НАСТРОЙКА ЛОГИРОВАНИЯ ===================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ==================== ТОКЕН БОТА ===================
# Получаем токен из переменных окружения
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")

if not TOKEN:
    logger.error("❌ Токен бота не найден!")
    logger.info("ℹ️ Установите переменную окружения TELEGRAM_BOT_TOKEN в настройках Bothost")
    exit(1)

# ==================== ИНИЦИАЛИЗАЦИЯ БОТА ===================
try:
    # НОВЫЙ синтаксис для aiogram 3.7.0+
    bot = Bot(
        token=TOKEN, 
        default=DefaultBotProperties(parse_mode="HTML")  # Используем DefaultBotProperties
    )
    storage = MemoryStorage()
    dp = Dispatcher(storage=storage)
    logger.info("✅ Бот инициализирован успешно")
except Exception as e:
    logger.error(f"❌ Ошибка инициализации бота: {e}")
    exit(1)

# ================== ГЛОБАЛЬНЫЕ ПЕРЕМЕННЫЕ ==================
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
    """Инициализация базы данных"""
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
        logger.info("✅ База данных инициализирована")
        return True
    except Exception as e:
        logger.error(f"❌ Ошибка БД: {e}")
        return False

# ================== КЛАВИАТУРЫ ==================
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

# ================== ФУНКЦИИ ДЛЯ РАБОТЫ С ТОВАРАМИ ==================
async def get_next_product_for_user(user_id):
    """Получение следующего товара для пользователя"""
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
    """Получение первого товара"""
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
    """Обработчик команды /start"""
    user_product_positions[message.from_user.id] = 0
    await message.answer(
        "🎮 Steal A Brainrot Shop\n\nВыберите свою роль:",
        reply_markup=get_main_menu_keyboard()
    )

@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    """Обработчик команды /help"""
    await message.answer(
        "🆘 Помощь\n\nОсновные команды:\n"
        "/start - начать работу\n"
        "/help - эта справка\n\n"
        "Используйте кнопки меню для навигации."
    )

@dp.message(Command("status"))
async def cmd_status(message: types.Message):
    """Обработчик команды /status"""
    conn = sqlite3.connect('brainrot_shop.db')
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM products")
    total_products = c.fetchone()[0]
    conn.close()
    
    await message.answer(
        f"🤖 Статус бота:\n\n"
        f"✅ Онлайн и работает\n"
        f"🕒 Время сервера: {datetime.now().strftime('%H:%M:%S')}\n"
        f"📊 Товаров в базе: {total_products}\n"
        f"👥 Пользователей в памяти: {len(user_product_positions)}"
    )

# ================== ПОКУПАТЕЛЬ ==================
@dp.message(F.text == "🛍️ Покупатель")
async def buyer_mode(message: types.Message):
    """Режим покупателя"""
    user_product_positions[message.from_user.id] = 0
    product = await get_first_product()

    if product:
        text = (
            f"🛒 Товар #{product[0]}\n\n"
            f"📌 Название: {product[2]}\n"
            f"📝 Описание: {product[3]}\n"
            f"💰 Цена: {product[4]}\n"
            f"👤 Контакты: @{product[5]}"
        )
        await message.answer(text, reply_markup=get_buyer_keyboard())
    else:
        await message.answer(
            "😔 Товаров пока нет\n\nПопросите друзей добавить товары!",
            reply_markup=get_main_menu_keyboard()
        )

@dp.message(F.text == "⏭️ Следующий товар")
async def next_product(message: types.Message):
    """Следующий товар"""
    product = await get_next_product_for_user(message.from_user.id)

    if product:
        text = (
            f"🛒 Товар #{product[0]}\n\n"
            f"📌 Название: {product[2]}\n"
            f"📝 Описание: {product[3]}\n"
            f"💰 Цена: {product[4]}\n"
            f"👤 Контакты: @{product[5]}"
        )
        await message.answer(text)
    else:
        await message.answer("😔 Товаров больше нет")

@dp.message(F.text == "✅ Купить")
async def buy_product(message: types.Message):
    """Покупка товара"""
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
    """Режим продавца"""
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
    """Начало добавления товара"""
    await state.set_state(ProductForm.title)
    await message.answer(
        "📝 Добавление нового товара\n\nВведите название товара:",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="❌ Отмена")]], 
            resize_keyboard=True
        )
    )

@dp.message(F.text == "❌ Отмена")
async def cancel_operation(message: types.Message, state: FSMContext):
    """Отмена операции"""
    await state.clear()
    await message.answer("❌ Операция отменена", reply_markup=get_seller_keyboard())

@dp.message(ProductForm.title)
async def process_title(message: types.Message, state: FSMContext):
    """Обработка названия товара"""
    if len(message.text) > 100:
        await message.answer("❌ Слишком длинное название! Максимум 100 символов.")
        return
    await state.update_data(title=message.text)
    await state.set_state(ProductForm.description)
    await message.answer("📝 Введите описание товара:")

@dp.message(ProductForm.description)
async def process_description(message: types.Message, state: FSMContext):
    """Обработка описания товара"""
    await state.update_data(description=message.text)
    await state.set_state(ProductForm.price)
    await message.answer("💰 Введите цену товара (например: 100 Robux):")

@dp.message(ProductForm.price)
async def process_price(message: types.Message, state: FSMContext):
    """Обработка цены товара"""
    await state.update_data(price=message.text)
    await state.set_state(ProductForm.contact)
    await message.answer("👤 Введите ваш username для связи (без @):")

@dp.message(ProductForm.contact)
async def process_contact(message: types.Message, state: FSMContext):
    """Обработка контактов"""
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
    finally:
        await state.clear()

# ================== ПРОСМОТР ТОВАРОВ ==================
@dp.message(F.text == "📋 Мои товары")
async def show_my_products(message: types.Message):
    """Показать товары продавца"""
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
    """Управление товарами"""
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
    """Удаление товара"""
    product_id = callback.data.split("_")[1]
    try:
        conn = sqlite3.connect('brainrot_shop.db')
        c = conn.cursor()
        c.execute("SELECT title FROM products WHERE id = ? AND seller_id = ?", 
                 (product_id, callback.from_user.id))
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
    """Показать обновленный список товаров"""
    conn = sqlite3.connect('brainrot_shop.db')
    c = conn.cursor()
    c.execute("""SELECT id, title, price FROM products WHERE seller_id = ? ORDER BY id DESC""", 
             (user_id,))
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
    """Редактирование товара"""
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

    text = (
        f"✏️ Редактирование товара #{product_id}\n\n"
        f"📌 Название: {product[0]}\n"
        f"📝 Описание: {product[1]}\n"
        f"💰 Цена: {product[2]}\n"
        f"👤 Контакты: @{product[3]}\n\n"
        f"Выберите что хотите изменить:"
    )
    await callback.message.answer(text, reply_markup=get_edit_options_keyboard())
    await callback.answer()

@dp.message(EditProductForm.waiting_for_field)
async def process_edit_field(message: types.Message, state: FSMContext):
    """Обработка выбора поля для редактирования"""
    if message.text == "❌ Отмена":
        await state.clear()
        await message.answer("❌ Редактирование отменено", reply_markup=get_seller_keyboard())
        return

    field_map = {
        "📌 Название": "title",
        "📝 Описание": "description",
        "💰 Цена": "price",
        "👤 Контакты": "contact"
    }
    
    if message.text not in field_map:
        await message.answer("❌ Пожалуйста, выберите поле из списка")
        return

    field = field_map[message.text]
    data = await state.get_data()
    current_value = data[f"edit_product_{field}"]
    
    await state.update_data(edit_field=field)
    await state.set_state(EditProductForm.waiting_for_new_value)
    
    await message.answer(
        f"✏️ Редактирование {message.text.lower()}\n\n"
        f"Текущее значение: {current_value}\n\n"
        f"Введите новое значение:",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="❌ Отмена")]], 
            resize_keyboard=True
        )
    )

@dp.message(EditProductForm.waiting_for_new_value)
async def process_new_value(message: types.Message, state: FSMContext):
    """Обработка нового значения поля"""
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
        
        field_column = {
            "title": "title",
            "description": "description", 
            "price": "price",
            "contact": "contact"
        }[field]
        
        c.execute(f"UPDATE products SET {field_column} = ? WHERE id = ?", 
                 (new_value, product_id))
        conn.commit()
        conn.close()
        
        await message.answer(
            f"✅ {field.capitalize()} успешно обновлено!\n\n"
            f"Новое значение: {new_value}",
            reply_markup=get_seller_keyboard()
        )
    except Exception as e:
        await message.answer(f"❌ Ошибка при обновлении: {e}")
    finally:
        await state.clear()

# ================== ВОЗВРАТ В МЕНЮ ==================
@dp.callback_query(F.data == "back_to_seller")
async def back_to_seller_callback(callback: types.CallbackQuery):
    """Возврат в меню продавца"""
    await callback.message.delete()
    await seller_mode(callback.message)

@dp.message(F.text == "🏠 Главное меню")
async def main_menu(message: types.Message):
    """Возврат в главное меню"""
    user_product_positions[message.from_user.id] = 0
    await cmd_start(message)

@dp.message(F.text == "ℹ️ О боте")
async def about_bot(message: types.Message):
    """Информация о боте"""
    await message.answer(
        "🤖 Steal A Brainrot Shop Bot\n\n"
        "📌 Версия: 2.3\n🎮 Игра: Brainrot (Roblox)\n\n"
        "Функции:\n"
        "• 🛍️ Просмотр товаров\n"
        "• 💰 Продажа предметов\n"
        "• ✏️ Редактирование товаров\n"
        "• 🗑️ Удаление товаров\n\n"
        "Правила:\n"
        "• 🚫 Запрещено мошенничество\n"
        "• 💬 Общайтесь вежливо\n"
        "• ✅ Проверяйте сделки\n\n"
        "Удачи в игре! 🎮"
    )

@dp.message()
async def unknown_command(message: types.Message):
    """Обработчик неизвестных команд"""
    await message.answer(
        "🤔 Я не понял вашу команду.\n\n"
        "Используйте кнопки меню или команду /start",
        reply_markup=get_main_menu_keyboard()
    )

# ================== ЗАПУСК БОТА ==================
async def main():
    """Основная функция запуска бота"""
    try:
        logger.info("=" * 70)
        logger.info("🚀 Запуск Brainrot Shop Bot...")
        logger.info("=" * 70)

        # Инициализация базы данных
        if not init_database():
            logger.error("❌ Не удалось инициализировать базу данных")
            return

        # Получаем информацию о боте
        try:
            bot_info = await bot.get_me()
            logger.info(f"✅ Бот подключен: @{bot_info.username}")
            logger.info(f"👤 Имя бота: {bot_info.first_name}")
            logger.info(f"🆔 ID бота: {bot_info.id}")
        except Exception as e:
            logger.error(f"❌ Не удалось подключиться к боту: {e}")
            logger.error("ℹ️ Проверьте токен в BotFather")
            return

        # Удаляем вебхук
        await bot.delete_webhook(drop_pending_updates=True)

        logger.info("🔄 Запускаю polling...")
        logger.info("✅ БОТ УСПЕШНО ЗАПУЩЕН!")
        logger.info("📊 Товары показываются ПО ПОРЯДКУ: 1 → 2 → 3 → 4 → 5 → ...")
        logger.info("=" * 70)

        # Запускаем бота
        await dp.start_polling(bot, skip_updates=True)

    except KeyboardInterrupt:
        logger.info("\n👋 Бот остановлен пользователем")
    except Exception as e:
        logger.error(f"💥 Критическая ошибка: {e}")

if __name__ == "__main__":
    asyncio.run(main())

