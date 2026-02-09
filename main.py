import asyncio
import logging
import sqlite3
from datetime import datetime, timedelta
import os

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.client.default import DefaultBotProperties

# ==================== НАСТРОЙКА ЛОГИРОВАНИЯ ===================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ==================== ТОКЕН БОТА ===================
TOKEN = os.environ.get("TOKEN", "")

if not TOKEN:
    logger.error("❌ Токен бота не найден в переменной 'TOKEN'!")
    logger.info("ℹ️ Установите переменную окружения TOKEN в настройках Bothost")
    exit(1)

# ==================== СПИСОК АДМИНОВ ===================
# !!! ЗАМЕНИТЕ ЭТИ ЧИСЛА НА ВАШ ID ИЛИ ID ДРУГИХ АДМИНОВ !!!
# Узнать свой ID можно у бота @userinfobot
ADMIN_IDS = [123456789, 987654321]

# ==================== НАСТРОЙКИ ЛИМИТОВ ===================
DAILY_LIMIT = 6  # Лимит товаров в сутки для обычных пользователей

# ==================== ИНИЦИАЛИЗАЦИЯ БОТА ===================
bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

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

class AdminActionForm(StatesGroup):
    waiting_for_user_id = State()
    waiting_for_product_id = State()
    waiting_for_ban_reason = State()
    waiting_for_delete_reason = State()
    waiting_for_whitelist_user = State()
    waiting_for_unwhitelist_user = State()

# ================== БАЗА ДАННЫХ ==================
def init_database():
    """Инициализация базы данных"""
    try:
        conn = sqlite3.connect('brainrot_shop.db')
        c = conn.cursor()
        
        # Таблица товаров
        c.execute('''CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            seller_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            description TEXT NOT NULL,
            price TEXT NOT NULL,
            contact TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )''')
        
        # Таблица пользователей (для админки, банов и белого списка)
        c.execute('''CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            last_name TEXT,
            is_banned BOOLEAN DEFAULT 0,
            ban_reason TEXT,
            is_whitelisted BOOLEAN DEFAULT 0,  -- В белом списке или нет
            daily_limit INTEGER DEFAULT ?,      -- Лимит товаров в день
            registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )''', (DAILY_LIMIT,))
        
        # Таблица действий админов (логирование)
        c.execute('''CREATE TABLE IF NOT EXISTS admin_actions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            admin_id INTEGER NOT NULL,
            action_type TEXT NOT NULL,
            target_id INTEGER,
            target_type TEXT,
            reason TEXT,
            details TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )''')
        
        conn.commit()
        conn.close()
        logger.info("✅ База данных инициализирована")
        return True
    except Exception as e:
        logger.error(f"❌ Ошибка БД: {e}")
        return False

def get_or_create_user(user_id, username="", first_name="", last_name=""):
    """Получаем или создаем запись о пользователе"""
    try:
        conn = sqlite3.connect('brainrot_shop.db')
        c = conn.cursor()
        
        c.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
        user = c.fetchone()
        
        if not user:
            # Создаем нового пользователя с дефолтным лимитом
            c.execute(
                """INSERT INTO users (user_id, username, first_name, last_name, daily_limit) 
                   VALUES (?, ?, ?, ?, ?)""",
                (user_id, username, first_name, last_name, DAILY_LIMIT)
            )
            logger.info(f"👤 Создан новый пользователь: {username} (ID: {user_id})")
        else:
            # Обновляем данные существующего пользователя
            c.execute(
                """UPDATE users SET username = ?, first_name = ?, last_name = ? 
                   WHERE user_id = ?""",
                (username, first_name, last_name, user_id)
            )
        
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        logger.error(f"❌ Ошибка в get_or_create_user: {e}")
        return False

def can_user_add_product(user_id):
    """
    Проверяет, может ли пользователь добавить товар.
    Возвращает (может_добавить, сообщение_об_ошибке)
    """
    try:
        conn = sqlite3.connect('brainrot_shop.db')
        c = conn.cursor()
        
        # 1. Получаем информацию о пользователе
        c.execute(
            """SELECT is_banned, is_whitelisted, daily_limit FROM users WHERE user_id = ?""",
            (user_id,)
        )
        user_info = c.fetchone()
        
        if not user_info:
            # Пользователя нет в базе (маловероятно, но на всякий случай)
            return False, "❌ Ошибка: пользователь не найден в системе."
        
        is_banned, is_whitelisted, daily_limit = user_info
        
        # 2. Проверяем бан
        if is_banned:
            c.execute("SELECT ban_reason FROM users WHERE user_id = ?", (user_id,))
            ban_reason = c.fetchone()[0]
            return False, f"⛔ Вы забанены! Причина: {ban_reason}"
        
        # 3. Если в белом списке - разрешаем без лимитов
        if is_whitelisted:
            conn.close()
            return True, "✅ Вы в белом списке! Лимитов нет."
        
        # 4. Проверяем лимит для обычных пользователей
        # Считаем, сколько товаров пользователь добавил за последние 24 часа
        time_24h_ago = (datetime.now() - timedelta(hours=24)).strftime('%Y-%m-%d %H:%M:%S')
        
        c.execute(
            """SELECT COUNT(*) FROM products 
               WHERE seller_id = ? AND created_at >= ?""",
            (user_id, time_24h_ago)
        )
        products_last_24h = c.fetchone()[0]
        
        conn.close()
        
        # 5. Проверяем, не превышен ли лимит
        if products_last_24h >= daily_limit:
            remaining_time = "24 часа"  # Можно улучшить: считать точное время до сброса
            return False, (
                f"❌ **Лимит исчерпан!**\n\n"
                f"Вы можете добавить только {daily_limit} товаров в сутки.\n"
                f"Вы уже добавили {products_last_24h} товаров за последние 24 часа.\n"
                f"Попробуйте позже или свяжитесь с администратором."
            )
        
        # 6. Лимит не превышен
        remaining = daily_limit - products_last_24h
        return True, f"✅ Лимит: {products_last_24h}/{daily_limit} (осталось {remaining})"
        
    except Exception as e:
        logger.error(f"❌ Ошибка в can_user_add_product: {e}")
        return False, "❌ Произошла ошибка при проверке лимита."

def add_to_whitelist(user_id, admin_id):
    """Добавляет пользователя в белый список"""
    try:
        conn = sqlite3.connect('brainrot_shop.db')
        c = conn.cursor()
        
        # Обновляем статус пользователя
        c.execute(
            "UPDATE users SET is_whitelisted = 1 WHERE user_id = ?",
            (user_id,)
        )
        
        # Логируем действие
        c.execute(
            """INSERT INTO admin_actions 
               (admin_id, action_type, target_id, target_type, details) 
               VALUES (?, ?, ?, ?, ?)""",
            (admin_id, "add_to_whitelist", user_id, "user", f"Добавлен в белый список")
        )
        
        conn.commit()
        conn.close()
        return True, "✅ Пользователь добавлен в белый список."
    except Exception as e:
        logger.error(f"❌ Ошибка при добавлении в белый список: {e}")
        return False, f"❌ Ошибка: {e}"

def remove_from_whitelist(user_id, admin_id):
    """Удаляет пользователя из белого списка"""
    try:
        conn = sqlite3.connect('brainrot_shop.db')
        c = conn.cursor()
        
        # Обновляем статус пользователя
        c.execute(
            "UPDATE users SET is_whitelisted = 0 WHERE user_id = ?",
            (user_id,)
        )
        
        # Логируем действие
        c.execute(
            """INSERT INTO admin_actions 
               (admin_id, action_type, target_id, target_type, details) 
               VALUES (?, ?, ?, ?, ?)""",
            (admin_id, "remove_from_whitelist", user_id, "user", f"Удален из белого списка")
        )
        
        conn.commit()
        conn.close()
        return True, "✅ Пользователь удален из белого списка."
    except Exception as e:
        logger.error(f"❌ Ошибка при удалении из белого списка: {e}")
        return False, f"❌ Ошибка: {e}"

def is_user_whitelisted(user_id):
    """Проверяет, находится ли пользователь в белом списке"""
    try:
        conn = sqlite3.connect('brainrot_shop.db')
        c = conn.cursor()
        c.execute(
            "SELECT is_whitelisted FROM users WHERE user_id = ?",
            (user_id,)
        )
        result = c.fetchone()
        conn.close()
        
        return result and result[0] == 1
    except Exception as e:
        logger.error(f"❌ Ошибка при проверке белого списка: {e}")
        return False

def get_whitelist():
    """Возвращает список пользователей в белом списке"""
    try:
        conn = sqlite3.connect('brainrot_shop.db')
        c = conn.cursor()
        c.execute(
            """SELECT user_id, username, first_name, last_name 
               FROM users WHERE is_whitelisted = 1 ORDER BY user_id"""
        )
        users = c.fetchall()
        conn.close()
        return users
    except Exception as e:
        logger.error(f"❌ Ошибка при получении белого списка: {e}")
        return []

def log_admin_action(admin_id, action_type, target_id=None, target_type=None, reason=None, details=None):
    """Логирование действий админа"""
    try:
        conn = sqlite3.connect('brainrot_shop.db')
        c = conn.cursor()
        c.execute(
            """INSERT INTO admin_actions 
               (admin_id, action_type, target_id, target_type, reason, details) 
               VALUES (?, ?, ?, ?, ?, ?)""",
            (admin_id, action_type, target_id, target_type, reason, details)
        )
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        logger.error(f"❌ Ошибка логирования действия админа: {e}")
        return False

def check_if_user_banned(user_id):
    """Проверка, забанен ли пользователь"""
    try:
        conn = sqlite3.connect('brainrot_shop.db')
        c = conn.cursor()
        c.execute("SELECT is_banned, ban_reason FROM users WHERE user_id = ?", (user_id,))
        result = c.fetchone()
        conn.close()
        
        if result and result[0] == 1:
            return True, result[1]  # Забанен и причина
        return False, None  # Не забанен
    except Exception as e:
        logger.error(f"❌ Ошибка при проверке бана: {e}")
        return False, None

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

def get_admin_keyboard():
    """Клавиатура для админ-панели"""
    keyboard = [
        [KeyboardButton(text="👁 Просмотреть все товары")],
        [KeyboardButton(text="🔍 Найти товары пользователя")],
        [KeyboardButton(text="🗑 Удалить товар (по ID)")],
        [KeyboardButton(text="✏️ Редактировать любой товар")],
        [KeyboardButton(text="⛔ Бан/разбан пользователя")],
        [KeyboardButton(text="⚪ Управление белым списком")],
        [KeyboardButton(text="📊 Статистика")],
        [KeyboardButton(text="🏠 Выход из админки")]
    ]
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)

def get_whitelist_keyboard():
    """Клавиатура для управления белым списком"""
    keyboard = [
        [KeyboardButton(text="➕ Добавить в белый список")],
        [KeyboardButton(text="➖ Удалить из белого списка")],
        [KeyboardButton(text="👁 Показать белый список")],
        [KeyboardButton(text="📊 Статистика лимитов")],
        [KeyboardButton(text="◀️ Назад в админку")]
    ]
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)

# ================== ОСНОВНЫЕ КОМАНДЫ БОТА ==================
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    """Обработчик команды /start"""
    get_or_create_user(
        user_id=message.from_user.id,
        username=message.from_user.username,
        first_name=message.from_user.first_name,
        last_name=message.from_user.last_name
    )
    
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
        "/help - эта справка\n"
        "/mylimit - узнать свой лимит\n\n"
        "Используйте кнопки меню для навигации."
    )

@dp.message(Command("mylimit"))
async def cmd_mylimit(message: types.Message):
    """Показывает текущий лимит пользователя"""
    user_id = message.from_user.id
    
    # Проверяем, забанен ли пользователь
    is_banned, ban_reason = check_if_user_banned(user_id)
    if is_banned:
        await message.answer(
            f"⛔ **Вы забанены!**\n\n"
            f"📝 Причина: {ban_reason}\n\n"
            f"Вы не можете добавлять товары.\n"
            f"Для разблока свяжитесь с администратором.",
            parse_mode="Markdown"
        )
        return
    
    # Проверяем лимит
    can_add, limit_message = can_user_add_product(user_id)
    
    # Получаем дополнительную информацию
    try:
        conn = sqlite3.connect('brainrot_shop.db')
        c = conn.cursor()
        c.execute(
            """SELECT is_whitelisted, daily_limit FROM users WHERE user_id = ?""",
            (user_id,)
        )
        user_info = c.fetchone()
        conn.close()
        
        if user_info:
            is_whitelisted, daily_limit = user_info
            
            # Считаем товары за последние 24 часа
            time_24h_ago = (datetime.now() - timedelta(hours=24)).strftime('%Y-%m-%d %H:%M:%S')
            conn = sqlite3.connect('brainrot_shop.db')
            c = conn.cursor()
            c.execute(
                """SELECT COUNT(*) FROM products 
                   WHERE seller_id = ? AND created_at >= ?""",
                (user_id, time_24h_ago)
            )
            products_last_24h = c.fetchone()[0]
            conn.close()
            
            # Формируем ответ
            status = "⚪ **В белом списке**" if is_whitelisted else "🔵 **Обычный пользователь**"
            limit_text = "∞ (без лимитов)" if is_whitelisted else f"{daily_limit} товаров/сутки"
            
            response = (
                f"📊 **Ваши лимиты**\n\n"
                f"{status}\n"
                f"📈 Дневной лимит: {limit_text}\n"
                f"📦 Добавлено за 24 часа: {products_last_24h}\n\n"
            )
            
            if not is_whitelisted:
                remaining = daily_limit - products_last_24h
                response += f"✅ Осталось сегодня: {remaining} товаров\n\n"
            
            response += limit_message
            
            await message.answer(response, parse_mode="Markdown")
        else:
            await message.answer("❌ Не удалось получить информацию о лимитах.")
            
    except Exception as e:
        logger.error(f"❌ Ошибка в cmd_mylimit: {e}")
        await message.answer("❌ Произошла ошибка при получении информации.")

@dp.message(Command("status"))
async def cmd_status(message: types.Message):
    """Обработчик команды /status"""
    conn = sqlite3.connect('brainrot_shop.db')
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM products")
    total_products = c.fetchone()[0]
    
    c.execute("SELECT COUNT(*) FROM users WHERE is_whitelisted = 1")
    whitelisted_users = c.fetchone()[0]
    
    conn.close()
    
    await message.answer(
        f"🤖 Статус бота:\n\n"
        f"✅ Онлайн и работает\n"
        f"🕒 Время сервера: {datetime.now().strftime('%H:%M:%S')}\n"
        f"📊 Товаров в базе: {total_products}\n"
        f"⚪ Пользователей в белом списке: {whitelisted_users}\n"
        f"👥 Пользователей в памяти: {len(user_product_positions)}"
    )

# ================== АДМИН КОМАНДЫ ==================
@dp.message(Command("admin"))
async def cmd_admin(message: types.Message):
    """Проверяем, является ли пользователь админом, и показываем панель"""
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("⛔ У вас нет доступа к этой команде.")
        return
    
    user_product_positions[message.from_user.id] = 0
    await message.answer(
        "👨‍💻 **Панель администратора**\n\n"
        "Выберите действие на клавиатуре ниже:",
        reply_markup=get_admin_keyboard(),
        parse_mode="Markdown"
    )

# ================== ФУНКЦИИ БЕЛОГО СПИСКА ==================
@dp.message(F.text == "⚪ Управление белым списком")
async def admin_whitelist_menu(message: types.Message):
    """Меню управления белым списком"""
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("⛔ У вас нет доступа.")
        return
    
    await message.answer(
        "⚪ **Управление белым списком**\n\n"
        "Пользователи в белом списке:\n"
        "• Не имеют лимитов на добавление товаров\n"
        "• Могут добавлять неограниченное количество товаров\n\n"
        "Выберите действие:",
        parse_mode="Markdown",
        reply_markup=get_whitelist_keyboard()
    )

@dp.message(F.text == "➕ Добавить в белый список")
async def admin_add_to_whitelist_start(message: types.Message, state: FSMContext):
    """Начало добавления пользователя в белый список"""
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("⛔ У вас нет доступа.")
        return
    
    await state.set_state(AdminActionForm.waiting_for_whitelist_user)
    await message.answer(
        "➕ **Добавление в белый список**\n\n"
        "Введите ID пользователя или его username (без @):\n\n"
        "Примеры:\n"
        "• `123456789` (ID пользователя)\n"
        "• `username` (юзернейм без @)",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="❌ Отмена")]],
            resize_keyboard=True
        )
    )

@dp.message(AdminActionForm.waiting_for_whitelist_user)
async def process_add_to_whitelist(message: types.Message, state: FSMContext):
    """Обработка добавления в белый список"""
    if message.text == "❌ Отмена":
        await state.clear()
        await message.answer("❌ Добавление отменено.", reply_markup=get_whitelist_keyboard())
        return
    
    search_term = message.text.strip()
    admin_id = message.from_user.id
    
    try:
        conn = sqlite3.connect('brainrot_shop.db')
        c = conn.cursor()
        
        # Пытаемся найти пользователя
        if search_term.isdigit():
            user_id = int(search_term)
            c.execute("SELECT username FROM users WHERE user_id = ?", (user_id,))
            user = c.fetchone()
            if user:
                username = user[0]
            else:
                # Пользователя нет в базе, но мы можем его добавить
                username = None
        else:
            # Ищем по username
            c.execute("SELECT user_id FROM users WHERE username = ?", (search_term,))
            user = c.fetchone()
            if user:
                user_id = user[0]
                username = search_term
            else:
                await message.answer("❌ Пользователь не найден в базе.")
                await state.clear()
                return
        
        # Проверяем, не в белом списке ли уже
        c.execute("SELECT is_whitelisted FROM users WHERE user_id = ?", (user_id,))
        current_status = c.fetchone()
        
        if current_status and current_status[0] == 1:
            user_info = f"@{username}" if username else f"ID: {user_id}"
            await message.answer(
                f"ℹ️ Пользователь {user_info} уже в белом списке.",
                reply_markup=get_whitelist_keyboard()
            )
            await state.clear()
            return
        
        # Добавляем/обновляем пользователя в базе
        if not username:
            # Если пользователя нет в базе, создаем запись
            c.execute(
                """INSERT INTO users (user_id, is_whitelisted) 
                   VALUES (?, 1)""",
                (user_id,)
            )
        else:
            # Обновляем существующего пользователя
            c.execute(
                "UPDATE users SET is_whitelisted = 1 WHERE user_id = ?",
                (user_id,)
            )
        
        # Логируем действие
        c.execute(
            """INSERT INTO admin_actions 
               (admin_id, action_type, target_id, target_type, details) 
               VALUES (?, ?, ?, ?, ?)""",
            (admin_id, "add_to_whitelist", user_id, "user", 
             f"Добавлен в белый список. Username: {username or 'неизвестен'}")
        )
        
        conn.commit()
        conn.close()
        
        await state.clear()
        
        user_info = f"@{username}" if username else f"ID: {user_id}"
        await message.answer(
            f"✅ Пользователь {user_info} добавлен в белый список!\n\n"
            f"Теперь он может добавлять неограниченное количество товаров.",
            reply_markup=get_whitelist_keyboard()
        )
        
        # Отправляем уведомление пользователю (если он в боте)
        try:
            await bot.send_message(
                user_id,
                "🎉 **Вас добавили в белый список!**\n\n"
                "Теперь вы можете добавлять неограниченное количество товаров "
                "без каких-либо лимитов.\n\n"
                "Спасибо за вашу активность! 🚀",
                parse_mode="Markdown"
            )
        except Exception as e:
            logger.warning(f"Не удалось отправить уведомление пользователю {user_id}: {e}")
        
    except Exception as e:
        logger.error(f"❌ Ошибка в process_add_to_whitelist: {e}")
        await message.answer("❌ Произошла ошибка при добавлении в белый список.")
        await state.clear()

@dp.message(F.text == "➖ Удалить из белого списка")
async def admin_remove_from_whitelist_start(message: types.Message, state: FSMContext):
    """Начало удаления пользователя из белого списка"""
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("⛔ У вас нет доступа.")
        return
    
    await state.set_state(AdminActionForm.waiting_for_unwhitelist_user)
    await message.answer(
        "➖ **Удаление из белого списка**\n\n"
        "Введите ID пользователя или его username (без @):\n\n"
        "Примеры:\n"
        "• `123456789` (ID пользователя)\n"
        "• `username` (юзернейм без @)",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="❌ Отмена")]],
            resize_keyboard=True
        )
    )

@dp.message(AdminActionForm.waiting_for_unwhitelist_user)
async def process_remove_from_whitelist(message: types.Message, state: FSMContext):
    """Обработка удаления из белого списка"""
    if message.text == "❌ Отмена":
        await state.clear()
        await message.answer("❌ Удаление отменено.", reply_markup=get_whitelist_keyboard())
        return
    
    search_term = message.text.strip()
    admin_id = message.from_user.id
    
    try:
        conn = sqlite3.connect('brainrot_shop.db')
        c = conn.cursor()
        
        # Пытаемся найти пользователя
        if search_term.isdigit():
            user_id = int(search_term)
            c.execute("SELECT username, is_whitelisted FROM users WHERE user_id = ?", (user_id,))
            result = c.fetchone()
        else:
            # Ищем по username
            c.execute("SELECT user_id, username, is_whitelisted FROM users WHERE username = ?", (search_term,))
            result = c.fetchone()
        
        if not result:
            await message.answer("❌ Пользователь не найден в базе.")
            await state.clear()
            return
        
        if len(result) == 3:  # Поиск по username
            user_id, username, is_whitelisted = result
        else:  # Поиск по ID
            user_id, is_whitelisted = result[0], result[1]
            username = search_term if not search_term.isdigit() else None
        
        # Проверяем, в белом списке ли
        if not is_whitelisted:
            user_info = f"@{username}" if username else f"ID: {user_id}"
            await message.answer(
                f"ℹ️ Пользователь {user_info} не в белом списке.",
                reply_markup=get_whitelist_keyboard()
            )
            await state.clear()
            return
        
        # Удаляем из белого списка
        c.execute(
            "UPDATE users SET is_whitelisted = 0 WHERE user_id = ?",
            (user_id,)
        )
        
        # Логируем действие
        c.execute(
            """INSERT INTO admin_actions 
               (admin_id, action_type, target_id, target_type, details) 
               VALUES (?, ?, ?, ?, ?)""",
            (admin_id, "remove_from_whitelist", user_id, "user", 
             f"Удален из белого списка. Username: {username or 'неизвестен'}")
        )
        
        conn.commit()
        conn.close()
        
        await state.clear()
        
        user_info = f"@{username}" if username else f"ID: {user_id}"
        await message.answer(
            f"✅ Пользователь {user_info} удален из белого списка.\n\n"
            f"Теперь на него будут распространяться обычные лимиты ({DAILY_LIMIT} товаров/сутки).",
            reply_markup=get_whitelist_keyboard()
        )
        
        # Отправляем уведомление пользователю (если он в боте)
        try:
            await bot.send_message(
                user_id,
                f"⚠️ **Вас удалили из белого списка**\n\n"
                f"Теперь на вас распространяются обычные лимиты:\n"
                f"• {DAILY_LIMIT} товаров в сутки\n\n"
                f"Если вы считаете, что это ошибка, свяжитесь с администратором.",
                parse_mode="Markdown"
            )
        except Exception as e:
            logger.warning(f"Не удалось отправить уведомление пользователю {user_id}: {e}")
        
    except Exception as e:
        logger.error(f"❌ Ошибка в process_remove_from_whitelist: {e}")
        await message.answer("❌ Произошла ошибка при удалении из белого списка.")
        await state.clear()

@dp.message(F.text == "👁 Показать белый список")
async def admin_show_whitelist(message: types.Message):
    """Показывает список пользователей в белом списке"""
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("⛔ У вас нет доступа.")
        return
    
    users = get_whitelist()
    
    if not users:
        await message.answer(
            "📭 Белый список пуст.",
            reply_markup=get_whitelist_keyboard()
        )
        return
    
    text = "⚪ **Пользователи в белом списке:**\n\n"
    
    for user in users:
        user_id, username, first_name, last_name = user
        name_parts = []
        if first_name:
            name_parts.append(first_name)
        if last_name:
            name_parts.append(last_name)
        
        full_name = " ".join(name_parts) if name_parts else "Без имени"
        user_ident = f"@{username}" if username else f"ID: {user_id}"
        
        text += f"• {full_name} ({user_ident})\n"
    
    text += f"\nВсего: **{len(users)}** пользователей"
    
    await message.answer(text, parse_mode="Markdown", reply_markup=get_whitelist_keyboard())

@dp.message(F.text == "📊 Статистика лимитов")
async def admin_limits_stats(message: types.Message):
    """Статистика по лимитам"""
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("⛔ У вас нет доступа.")
        return
    
    try:
        conn = sqlite3.connect('brainrot_shop.db')
        c = conn.cursor()
        
        # Общая статистика
        c.execute("SELECT COUNT(*) FROM users")
        total_users = c.fetchone()[0]
        
        c.execute("SELECT COUNT(*) FROM users WHERE is_whitelisted = 1")
        whitelisted = c.fetchone()[0]
        
        c.execute("SELECT COUNT(*) FROM users WHERE is_banned = 1")
        banned = c.fetchone()[0]
        
        # Статистика по активности за последние 24 часа
        time_24h_ago = (datetime.now() - timedelta(hours=24)).strftime('%Y-%m-%d %H:%M:%S')
        
        # Пользователи, достигшие лимита
        c.execute("""
            SELECT u.user_id, u.username, COUNT(p.id) as product_count
            FROM users u
            LEFT JOIN products p ON u.user_id = p.seller_id 
                AND p.created_at >= ?
            WHERE u.is_whitelisted = 0 AND u.is_banned = 0
            GROUP BY u.user_id
            HAVING product_count >= ?
            ORDER BY product_count DESC
        """, (time_24h_ago, DAILY_LIMIT))
        users_at_limit = c.fetchall()
        
        # Самые активные пользователи
        c.execute("""
            SELECT u.user_id, u.username, COUNT(p.id) as product_count
            FROM users u
            LEFT JOIN products p ON u.user_id = p.seller_id 
                AND p.created_at >= ?
            WHERE u.is_banned = 0
            GROUP BY u.user_id
            ORDER BY product_count DESC
            LIMIT 10
        """, (time_24h_ago,))
        top_active = c.fetchall()
        
        conn.close()
        
        # Формируем ответ
        text = (
            f"📊 **Статистика лимитов**\n\n"
            f"👥 Всего пользователей: {total_users}\n"
            f"⚪ В белом списке: {whitelisted}\n"
            f"⛔ Забанено: {banned}\n"
            f"🔵 Обычных пользователей: {total_users - whitelisted - banned}\n"
            f"📈 Дневной лимит: {DAILY_LIMIT} товаров\n\n"
        )
        
        if users_at_limit:
            text += f"**⚠️ Достигли лимита ({DAILY_LIMIT}+):**\n"
            for user in users_at_limit[:5]:  # Показываем первых 5
                user_id, username, count = user
                user_ident = f"@{username}" if username else f"ID: {user_id}"
                text += f"• {user_ident}: {count} товаров\n"
            
            if len(users_at_limit) > 5:
                text += f"• ...и еще {len(users_at_limit) - 5} пользователей\n"
            text += "\n"
        
        if top_active:
            text += "**🏆 Самые активные (за 24ч):**\n"
            for i, user in enumerate(top_active, 1):
                user_id, username, count = user
                user_ident = f"@{username}" if username else f"ID: {user_id}"
                status = "⚪" if is_user_whitelisted(user_id) else "🔵"
                text += f"{i}. {status} {user_ident}: {count} товаров\n"
        
        await message.answer(text, parse_mode="Markdown", reply_markup=get_whitelist_keyboard())
        
    except Exception as e:
        logger.error(f"❌ Ошибка в admin_limits_stats: {e}")
        await message.answer("❌ Произошла ошибка при загрузке статистики.")

@dp.message(F.text == "◀️ Назад в админку")
async def back_to_admin(message: types.Message):
    """Возврат из управления белым списком в админ-панель"""
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("⛔ У вас нет доступа.")
        return
    
    await message.answer(
        "Возврат в панель администратора.",
        reply_markup=get_admin_keyboard()
    )

# ================== СУЩЕСТВУЮЩИЕ АДМИН-ФУНКЦИИ ==================
# (Все остальные функции из предыдущего кода остаются без изменений)
# Я сохранил их структуру, но из-за ограничения длины не дублирую здесь
# Просто добавьте эти новые функции к вашему существующему коду

@dp.message(F.text == "👁 Просмотреть все товары")
async def admin_show_all_products(message: types.Message):
    # ... ваш существующий код ...
    pass

@dp.message(F.text == "🔍 Найти товары пользователя")
async def admin_find_user_products(message: types.Message, state: FSMContext):
    # ... ваш существующий код ...
    pass

@dp.message(F.text == "🗑 Удалить товар (по ID)")
async def admin_delete_product_start(message: types.Message, state: FSMContext):
    # ... ваш существующий код ...
    pass

@dp.message(F.text == "⛔ Бан/разбан пользователя")
async def admin_ban_user_start(message: types.Message, state: FSMContext):
    # ... ваш существующий код ...
    pass

@dp.message(F.text == "📊 Статистика")
async def admin_stats(message: types.Message):
    # ... ваш существующий код ...
    pass

@dp.message(F.text == "🏠 Выход из админки")
async def admin_exit(message: types.Message):
    """Выход из админ-панели в главное меню"""
    await message.answer(
        "Выход из панели администратора.",
        reply_markup=get_main_menu_keyboard()
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
    # Проверяем, не забанен ли пользователь
    is_banned, ban_reason = check_if_user_banned(message.from_user.id)
    if is_banned:
        await message.answer(
            f"⛔ **Вы забанены в этом боте!**\n\n"
            f"📝 Причина: {ban_reason}\n\n"
            f"Вы не можете добавлять новые товары.\n"
            f"Для разблока свяжитесь с администратором.",
            parse_mode="Markdown",
            reply_markup=get_main_menu_keyboard()
        )
        return
    
    # Проверяем лимиты (но показываем режим продавца даже если лимит исчерпан)
    can_add, limit_message = can_user_add_product(message.from_user.id)
    
    conn = sqlite3.connect('brainrot_shop.db')
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM products WHERE seller_id = ?", (message.from_user.id,))
    count = c.fetchone()[0]
    conn.close()
    
    response = f"💰 Режим продавца\n\n📊 Ваших товаров: {count}\n\n"
    
    if not can_add and "Лимит исчерпан" in limit_message:
        response += f"⚠️ {limit_message}\n\n"
    
    response += "Доступные действия:"
    
    await message.answer(response, reply_markup=get_seller_keyboard())

# ================== ДОБАВЛЕНИЕ ТОВАРА ==================
@dp.message(F.text == "➕ Добавить товар")
async def add_product_start(message: types.Message, state: FSMContext):
    """Начало добавления товара"""
    # Проверяем, не забанен ли пользователь
    is_banned, ban_reason = check_if_user_banned(message.from_user.id)
    if is_banned:
        await message.answer(
            f"⛔ **Вы забанены и не можете добавлять товары!**\n\n"
            f"📝 Причина: {ban_reason}\n\n"
            f"Для разблока свяжитесь с администратором.",
            parse_mode="Markdown",
            reply_markup=get_seller_keyboard()
        )
        return
    
    # Проверяем лимиты
    can_add, limit_message = can_user_add_product(message.from_user.id)
    
    if not can_add:
        await message.answer(
            limit_message,
            parse_mode="Markdown",
            reply_markup=get_seller_keyboard()
        )
        return
    
    await state.set_state(ProductForm.title)
    await message.answer(
        f"📝 Добавление нового товара\n\n{limit_message}\n\nВведите название товара:",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="❌ Отмена")]], 
            resize_keyboard=True
        )
    )

# ... остальной ваш существующий код (обработчики отмены, process_title, process_description и т.д.)

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
        
        # Получаем обновленную информацию о лимитах
        can_add, limit_message = can_user_add_product(message.from_user.id)
        
        await message.answer(
            f"✅ Товар добавлен!\n\n"
            f"📌 Название: {data['title']}\n"
            f"📝 Описание: {data['description']}\n"
            f"💰 Цена: {data['price']}\n"
            f"👤 Контакты: @{message.text}\n\n"
            f"{limit_message}",
            reply_markup=get_seller_keyboard()
        )
    except Exception as e:
        await message.answer(f"❌ Ошибка при сохранении: {e}")
    finally:
        await state.clear()

# ================== ОСТАЛЬНЫЕ ФУНКЦИИ ==================
# (Все остальные функции из вашего предыдущего кода)

# ================== ЗАПУСК БОТА ==================
async def main():
    """Основная функция запуска бота"""
    try:
        logger.info("=" * 70)
        logger.info("🚀 Запуск Brainrot Shop Bot v2.4 (с лимитами и белым списком)...")
        logger.info("=" * 70)
        logger.info(f"📊 Настройки: Лимит {DAILY_LIMIT} товаров/сутки для обычных пользователей")

        # Инициализация базы данных
        init_database()

        # Получаем информацию о боте
        bot_info = await bot.get_me()
        logger.info(f"✅ Бот подключен: @{bot_info.username}")
        logger.info(f"👤 Имя бота: {bot_info.first_name}")
        logger.info(f"🆔 ID бота: {bot_info.id}")

        # Удаляем вебхук
        await bot.delete_webhook(drop_pending_updates=True)

        logger.info("🔄 Запускаю polling...")
        logger.info("✅ БОТ УСПЕШНО ЗАПУЩЕН!")
        logger.info(f"📊 Лимит: {DAILY_LIMIT} товаров/сутки для обычных пользователей")
        logger.info("⚪ Белый список: безлимитный доступ")
        logger.info("=" * 70)

        # Запускаем бота
        await dp.start_polling(bot, skip_updates=True)

    except KeyboardInterrupt:
        logger.info("\n👋 Бот остановлен пользователем")
    except Exception as e:
        logger.error(f"💥 Критическая ошибка: {e}")

if __name__ == "__main__":
    asyncio.run(main())
