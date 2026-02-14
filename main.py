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
TOKEN = ""

if not TOKEN:
    logger.error("❌ Токен бота не найден!")
    exit(1)

# ==================== СПИСОК АДМИНОВ ===================
ADMIN_IDS = [1593674702]

# ==================== НАСТРОЙКИ ЛИМИТОВ ===================
DAILY_LIMIT = 6

# ==================== ИНИЦИАЛИЗАЦИЯ БОТА ===================
bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# ================== ГЛОБАЛЬНЫЕ ПЕРЕМЕННЫЕ ==================
user_product_positions = {}
admin_pages = {}
moderation_index = {}

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
    waiting_for_user_id_for_ban = State()

class ReviewState(StatesGroup):
    waiting_for_rating = State()
    waiting_for_comment = State()
    waiting_for_evidence = State()

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

        c.execute(f'''CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            last_name TEXT,
            is_banned BOOLEAN DEFAULT 0,
            ban_reason TEXT,
            is_whitelisted BOOLEAN DEFAULT 0,
            daily_limit INTEGER DEFAULT {DAILY_LIMIT},
            registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )''')

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

        c.execute('''CREATE TABLE IF NOT EXISTS reviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            seller_id INTEGER NOT NULL,
            buyer_id INTEGER NOT NULL,
            product_id INTEGER,
            rating INTEGER NOT NULL CHECK(rating >= 1 AND rating <= 5),
            comment TEXT,
            is_moderated BOOLEAN DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (seller_id) REFERENCES users(user_id),
            FOREIGN KEY (buyer_id) REFERENCES users(user_id),
            FOREIGN KEY (product_id) REFERENCES products(id)
        )''')

        conn.commit()
        conn.close()
        logger.info("✅ База данных инициализирована")
        return True
    except Exception as e:
        logger.error(f"❌ Ошибка БД: {e}")
        return False


def get_or_create_user(user_id, username="", first_name="", last_name=""):
    try:
        conn = sqlite3.connect('brainrot_shop.db')
        c = conn.cursor()

        c.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
        user = c.fetchone()

        if not user:
            c.execute(
                """INSERT INTO users (user_id, username, first_name, last_name, daily_limit) 
                   VALUES (?, ?, ?, ?, ?)""",
                (user_id, username, first_name, last_name, DAILY_LIMIT)
            )
            logger.info(f"👤 Создан новый пользователь: {username} (ID: {user_id})")
        else:
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


def can_user_add_product(user_id):
    try:
        conn = sqlite3.connect('brainrot_shop.db')
        c = conn.cursor()
        c.execute("SELECT is_banned, is_whitelisted, daily_limit FROM users WHERE user_id = ?", (user_id,))
        user_info = c.fetchone()
        if not user_info:
            return False, "❌ Ошибка: пользователь не найден в системе."
        is_banned, is_whitelisted, daily_limit = user_info
        if is_banned:
            c.execute("SELECT ban_reason FROM users WHERE user_id = ?", (user_id,))
            ban_reason = c.fetchone()[0]
            return False, f"⛔ Вы забанены! Причина: {ban_reason}"
        if is_whitelisted:
            conn.close()
            return True, "✅ Вы в белом списке! Лимитов нет."
        time_24h_ago = (datetime.now() - timedelta(hours=24)).strftime('%Y-%m-%d %H:%M:%S')
        c.execute("SELECT COUNT(*) FROM products WHERE seller_id = ? AND created_at >= ?", (user_id, time_24h_ago))
        products_last_24h = c.fetchone()[0]
        conn.close()
        if products_last_24h >= daily_limit:
            return False, (f"❌ **Лимит исчерпан!**\n\nВы можете добавить только {daily_limit} товаров в сутки.\nВы уже добавили {products_last_24h} товаров за последние 24 часа.\nПопробуйте позже или свяжитесь с администратором.")
        remaining = daily_limit - products_last_24h
        return True, f"✅ Лимит: {products_last_24h}/{daily_limit} (осталось {remaining})"
    except Exception as e:
        logger.error(f"❌ Ошибка в can_user_add_product: {e}")
        return False, "❌ Произошла ошибка при проверке лимита."


def add_to_whitelist(user_id, admin_id):
    try:
        conn = sqlite3.connect('brainrot_shop.db')
        c = conn.cursor()
        c.execute("UPDATE users SET is_whitelisted = 1 WHERE user_id = ?", (user_id,))
        c.execute("""INSERT INTO admin_actions (admin_id, action_type, target_id, target_type, details) VALUES (?, ?, ?, ?, ?)""",
                  (admin_id, "add_to_whitelist", user_id, "user", f"Добавлен в белый список"))
        conn.commit()
        conn.close()
        return True, "✅ Пользователь добавлен в белый список."
    except Exception as e:
        logger.error(f"❌ Ошибка при добавлении в белый список: {e}")
        return False, f"❌ Ошибка: {e}"


def remove_from_whitelist(user_id, admin_id):
    try:
        conn = sqlite3.connect('brainrot_shop.db')
        c = conn.cursor()
        c.execute("UPDATE users SET is_whitelisted = 0 WHERE user_id = ?", (user_id,))
        c.execute("""INSERT INTO admin_actions (admin_id, action_type, target_id, target_type, details) VALUES (?, ?, ?, ?, ?)""",
                  (admin_id, "remove_from_whitelist", user_id, "user", f"Удален из белого списка"))
        conn.commit()
        conn.close()
        return True, "✅ Пользователь удален из белого списка."
    except Exception as e:
        logger.error(f"❌ Ошибка при удалении из белого списка: {e}")
        return False, f"❌ Ошибка: {e}"


def is_user_whitelisted(user_id):
    try:
        conn = sqlite3.connect('brainrot_shop.db')
        c = conn.cursor()
        c.execute("SELECT is_whitelisted FROM users WHERE user_id = ?", (user_id,))
        result = c.fetchone()
        conn.close()
        return result and result[0] == 1
    except Exception as e:
        logger.error(f"❌ Ошибка при проверке белого списка: {e}")
        return False


def get_whitelist():
    try:
        conn = sqlite3.connect('brainrot_shop.db')
        c = conn.cursor()
        c.execute("""SELECT user_id, username, first_name, last_name FROM users WHERE is_whitelisted = 1 ORDER BY user_id""")
        users = c.fetchall()
        conn.close()
        return users
    except Exception as e:
        logger.error(f"❌ Ошибка при получении белого списка: {e}")
        return []


def log_admin_action(admin_id, action_type, target_id=None, target_type=None, reason=None, details=None):
    try:
        conn = sqlite3.connect('brainrot_shop.db')
        c = conn.cursor()
        c.execute("""INSERT INTO admin_actions (admin_id, action_type, target_id, target_type, reason, details) VALUES (?, ?, ?, ?, ?, ?)""",
                  (admin_id, action_type, target_id, target_type, reason, details))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        logger.error(f"❌ Ошибка логирования действия админа: {e}")
        return False


def check_if_user_banned(user_id):
    try:
        conn = sqlite3.connect('brainrot_shop.db')
        c = conn.cursor()
        c.execute("SELECT is_banned, ban_reason FROM users WHERE user_id = ?", (user_id,))
        result = c.fetchone()
        conn.close()
        if result and result[0] == 1:
            return True, result[1]
        return False, None
    except Exception as e:
        logger.error(f"❌ Ошибка при проверке бана: {e}")
        return False, None


def get_all_products():
    try:
        conn = sqlite3.connect('brainrot_shop.db')
        c = conn.cursor()
        c.execute("""
            SELECT p.id, p.title, p.price, p.contact, p.seller_id,
                   (SELECT username FROM users WHERE user_id = p.seller_id LIMIT 1) as username
            FROM products p ORDER BY p.id DESC
        """)
        products = c.fetchall()
        conn.close()
        return products
    except Exception as e:
        logger.error(f"❌ Ошибка в get_all_products: {e}")
        return []


def get_product_by_id(product_id):
    try:
        conn = sqlite3.connect('brainrot_shop.db')
        c = conn.cursor()
        c.execute("SELECT * FROM products WHERE id = ?", (product_id,))
        product = c.fetchone()
        conn.close()
        return product
    except Exception as e:
        logger.error(f"❌ Ошибка в get_product_by_id: {e}")
        return None


def get_all_products_count():
    try:
        conn = sqlite3.connect('brainrot_shop.db')
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM products")
        count = c.fetchone()[0]
        conn.close()
        return count
    except Exception as e:
        logger.error(f"❌ Ошибка в get_all_products_count: {e}")
        return 0


def get_user_by_id_or_username(search_term):
    try:
        conn = sqlite3.connect('brainrot_shop.db')
        c = conn.cursor()
        if search_term.isdigit():
            c.execute("SELECT user_id, username, is_banned, ban_reason FROM users WHERE user_id = ?", (int(search_term),))
        else:
            c.execute("SELECT user_id, username, is_banned, ban_reason FROM users WHERE username = ?", (search_term,))
        user = c.fetchone()
        conn.close()
        return user
    except Exception as e:
        logger.error(f"❌ Ошибка в get_user_by_id_or_username: {e}")
        return None


def ban_user_in_db(user_id, reason, admin_id):
    try:
        conn = sqlite3.connect('brainrot_shop.db')
        c = conn.cursor()
        c.execute("UPDATE users SET is_banned = 1, ban_reason = ? WHERE user_id = ?", (reason, user_id))
        log_admin_action(admin_id=admin_id, action_type="ban_user", target_id=user_id, target_type="user", reason=reason, details=f"Забанен пользователь")
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        logger.error(f"❌ Ошибка при бане пользователя: {e}")
        return False


def unban_user_in_db(user_id, admin_id):
    try:
        conn = sqlite3.connect('brainrot_shop.db')
        c = conn.cursor()
        c.execute("UPDATE users SET is_banned = 0, ban_reason = NULL WHERE user_id = ?", (user_id,))
        log_admin_action(admin_id=admin_id, action_type="unban_user", target_id=user_id, target_type="user", reason="Разбан", details=f"Разбанен пользователь")
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        logger.error(f"❌ Ошибка при разбане пользователя: {e}")
        return False


# ================== ФУНКЦИИ ДЛЯ ОТЗЫВОВ ==================
def get_seller_rating(seller_id):
    try:
        conn = sqlite3.connect('brainrot_shop.db')
        c = conn.cursor()
        c.execute("SELECT AVG(rating), COUNT(*) FROM reviews WHERE seller_id = ? AND is_moderated = 1", (seller_id,))
        avg, count = c.fetchone()
        conn.close()
        if avg:
            return round(avg, 1), count
        return None, 0
    except Exception as e:
        logger.error(f"❌ Ошибка в get_seller_rating: {e}")
        return None, 0

def get_seller_reviews(seller_id, page=0, per_page=5):
    try:
        conn = sqlite3.connect('brainrot_shop.db')
        c = conn.cursor()
        offset = page * per_page
        c.execute("""
            SELECT r.rating, r.comment, r.created_at, u.username 
            FROM reviews r
            LEFT JOIN users u ON r.buyer_id = u.user_id
            WHERE r.seller_id = ? AND r.is_moderated = 1
            ORDER BY r.created_at DESC
            LIMIT ? OFFSET ?
        """, (seller_id, per_page, offset))
        reviews = c.fetchall()
        c.execute("SELECT COUNT(*) FROM reviews WHERE seller_id = ? AND is_moderated = 1", (seller_id,))
        total = c.fetchone()[0]
        conn.close()
        return reviews, total
    except Exception as e:
        logger.error(f"❌ Ошибка в get_seller_reviews: {e}")
        return [], 0

def add_review(seller_id, buyer_id, product_id, rating, comment):
    try:
        conn = sqlite3.connect('brainrot_shop.db')
        c = conn.cursor()
        c.execute("""
            INSERT INTO reviews (seller_id, buyer_id, product_id, rating, comment, is_moderated)
            VALUES (?, ?, ?, ?, ?, 0)
        """, (seller_id, buyer_id, product_id, rating, comment))
        review_id = c.lastrowid
        conn.commit()
        conn.close()
        return review_id
    except Exception as e:
        logger.error(f"❌ Ошибка в add_review: {e}")
        return None

def get_review_by_id(review_id):
    try:
        conn = sqlite3.connect('brainrot_shop.db')
        c = conn.cursor()
        c.execute("""
            SELECT r.id, r.rating, r.comment, r.created_at, 
                   u_buyer.user_id, u_buyer.username, 
                   u_seller.user_id, u_seller.username
            FROM reviews r
            LEFT JOIN users u_buyer ON r.buyer_id = u_buyer.user_id
            LEFT JOIN users u_seller ON r.seller_id = u_seller.user_id
            WHERE r.id = ?
        """, (review_id,))
        rev = c.fetchone()
        conn.close()
        return rev
    except Exception as e:
        logger.error(f"❌ Ошибка в get_review_by_id: {e}")
        return None

def approve_review(review_id, admin_id):
    try:
        conn = sqlite3.connect('brainrot_shop.db')
        c = conn.cursor()
        c.execute("UPDATE reviews SET is_moderated = 1 WHERE id = ?", (review_id,))
        c.execute("SELECT seller_id, rating, comment FROM reviews WHERE id = ?", (review_id,))
        seller_id, rating, comment = c.fetchone()
        conn.commit()
        conn.close()
        return seller_id, rating, comment
    except Exception as e:
        logger.error(f"❌ Ошибка в approve_review: {e}")
        return None

def reject_review(review_id, admin_id):
    try:
        conn = sqlite3.connect('brainrot_shop.db')
        c = conn.cursor()
        c.execute("SELECT buyer_id FROM reviews WHERE id = ?", (review_id,))
        buyer_id = c.fetchone()
        if buyer_id:
            buyer_id = buyer_id[0]
        c.execute("DELETE FROM reviews WHERE id = ?", (review_id,))
        conn.commit()
        conn.close()
        return buyer_id
    except Exception as e:
        logger.error(f"❌ Ошибка в reject_review: {e}")
        return None

def get_unmoderated_reviews():
    try:
        conn = sqlite3.connect('brainrot_shop.db')
        c = conn.cursor()
        c.execute("SELECT id FROM reviews WHERE is_moderated = 0 ORDER BY created_at ASC")
        ids = [row[0] for row in c.fetchall()]
        conn.close()
        return ids
    except Exception as e:
        logger.error(f"❌ Ошибка в get_unmoderated_reviews: {e}")
        return []


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
            InlineKeyboardButton(text=f"✏️ #{product[0]} - {product[1][:15]}...", callback_data=f"edit_{product[0]}"),
            InlineKeyboardButton(text=f"🗑️ #{product[0]}", callback_data=f"delete_{product[0]}")
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
    keyboard = [
        [KeyboardButton(text="👁 Просмотреть все товары")],
        [KeyboardButton(text="🔍 Найти товары пользователя")],
        [KeyboardButton(text="🗑 Удалить товар (по ID)")],
        [KeyboardButton(text="✏️ Редактировать любой товар")],
        [KeyboardButton(text="⛔ Бан/разбан пользователя")],
        [KeyboardButton(text="⚪ Управление белым списком")],
        [KeyboardButton(text="📝 Модерация отзывов")],
        [KeyboardButton(text="📊 Статистика")],
        [KeyboardButton(text="🏠 Выход из админки")]
    ]
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)

def get_whitelist_keyboard():
    keyboard = [
        [KeyboardButton(text="➕ Добавить в белый список")],
        [KeyboardButton(text="➖ Удалить из белого списка")],
        [KeyboardButton(text="👁 Показать белый список")],
        [KeyboardButton(text="📊 Статистика лимитов")],
        [KeyboardButton(text="◀️ Назад в админку")]
    ]
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)


# ================== ОСНОВНЫЕ КОМАНДЫ ==================
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    get_or_create_user(
        user_id=message.from_user.id,
        username=message.from_user.username,
        first_name=message.from_user.first_name,
        last_name=message.from_user.last_name
    )
    user_product_positions[message.from_user.id] = 0
    await message.answer("🎮 Steal A Brainrot Shop\n\nВыберите свою роль:", reply_markup=get_main_menu_keyboard())

@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    await message.answer(
        "🆘 Помощь\n\nОсновные команды:\n"
        "/start - начать работу\n"
        "/help - эта справка\n"
        "/mylimit - узнать свой лимит\n"
        "/status - состояние бота\n"
        "/ids - список ID товаров (админ)\n"
        "/health - диагностика (админ)\n\n"
        "Используйте кнопки меню для навигации."
    )

@dp.message(Command("mylimit"))
async def cmd_mylimit(message: types.Message):
    user_id = message.from_user.id
    is_banned, ban_reason = check_if_user_banned(user_id)
    if is_banned:
        await message.answer(f"⛔ **Вы забанены!**\n\n📝 Причина: {ban_reason}\n\nВы не можете добавлять товары.\nДля разблока свяжитесь с администратором.", parse_mode="Markdown")
        return
    can_add, limit_message = can_user_add_product(user_id)
    try:
        conn = sqlite3.connect('brainrot_shop.db')
        c = conn.cursor()
        c.execute("SELECT is_whitelisted, daily_limit FROM users WHERE user_id = ?", (user_id,))
        user_info = c.fetchone()
        conn.close()
        if user_info:
            is_whitelisted, daily_limit = user_info
            time_24h_ago = (datetime.now() - timedelta(hours=24)).strftime('%Y-%m-%d %H:%M:%S')
            conn = sqlite3.connect('brainrot_shop.db')
            c = conn.cursor()
            c.execute("SELECT COUNT(*) FROM products WHERE seller_id = ? AND created_at >= ?", (user_id, time_24h_ago))
            products_last_24h = c.fetchone()[0]
            conn.close()
            status = "⚪ **В белом списке**" if is_whitelisted else "🔵 **Обычный пользователь**"
            limit_text = "∞ (без лимитов)" if is_whitelisted else f"{daily_limit} товаров/сутки"
            response = f"📊 **Ваши лимиты**\n\n{status}\n📈 Дневной лимит: {limit_text}\n📦 Добавлено за 24 часа: {products_last_24h}\n\n"
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
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("⛔ У вас нет доступа к этой команде.")
        return
    user_product_positions[message.from_user.id] = 0
    await message.answer("👨‍💻 **Панель администратора**\n\nВыберите действие на клавиатуре ниже:", reply_markup=get_admin_keyboard(), parse_mode="Markdown")

# ================== БЕЛЫЙ СПИСОК (сокращённо) ==================
# (Здесь должны быть все старые обработчики для белого списка, бана и т.д. – они есть в исходном коде, я их не трогаю)

# ================== ПОКУПАТЕЛЬ ==================
@dp.message(F.text == "🛍️ Покупатель")
async def buyer_mode(message: types.Message):
    user_product_positions[message.from_user.id] = 0
    product = await get_first_product()
    if product:
        await show_product_with_review_button(message, product)
    else:
        await message.answer("😔 Товаров пока нет\n\nПопросите друзей добавить товары!", reply_markup=get_main_menu_keyboard())

@dp.message(F.text == "⏭️ Следующий товар")
async def next_product(message: types.Message):
    product = await get_next_product_for_user(message.from_user.id)
    if product:
        await show_product_with_review_button(message, product)
    else:
        await message.answer("😔 Товаров больше нет")

async def show_product_with_review_button(message: types.Message, product):
    product_id, seller_id, title, description, price, contact, _ = product
    text = (
        f"🛒 Товар #{product_id}\n\n"
        f"📌 Название: {title}\n"
        f"📝 Описание: {description}\n"
        f"💰 Цена: {price}\n"
        f"👤 Контакты: @{contact}"
    )
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Купить", callback_data=f"buy_{product_id}")
    builder.button(text="⭐ Отзывы о продавце", callback_data=f"reviews:{seller_id}:{product_id}")
    builder.button(text="🏠 Главное меню", callback_data="back_to_main")
    builder.adjust(2)
    await message.answer(text, reply_markup=builder.as_markup())

@dp.callback_query(F.data.startswith("buy_"))
async def buy_callback(callback: types.CallbackQuery):
    await callback.message.answer(
        "🎉 Отличный выбор!\n\n"
        "📞 Свяжитесь с продавцом по указанному username.\n\n"
        "⚠️ Будьте осторожны:\n"
        "• Не переводите деньги заранее\n"
        "• Договоритесь о безопасной сделке\n\n"
        "Удачи в игре! 🎮"
    )
    await callback.answer()

@dp.callback_query(F.data == "back_to_main")
async def back_to_main_callback(callback: types.CallbackQuery):
    await callback.message.delete()
    await cmd_start(callback.message)

# ================== ОТЗЫВЫ (ИСПРАВЛЕНЫ) ==================
@dp.callback_query(F.data.startswith("reviews:"))
async def show_seller_reviews(callback: types.CallbackQuery):
    # callback.data = "reviews:seller_id:product_id"
    _, seller_id, product_id = callback.data.split(":")
    seller_id = int(seller_id)

    avg_rating, total = get_seller_rating(seller_id)

    conn = sqlite3.connect('brainrot_shop.db')
    c = conn.cursor()
    c.execute("SELECT username FROM users WHERE user_id = ?", (seller_id,))
    res = c.fetchone()
    seller_username = res[0] if res else str(seller_id)
    conn.close()

    await callback.message.edit_text(
        f"👤 Продавец: @{seller_username}\n"
        f"⭐ Рейтинг: {avg_rating if avg_rating else 'нет'} (на основе {total} отзывов)\n\n"
        f"📝 Загружаю отзывы...",
        reply_markup=InlineKeyboardBuilder().button(text="🔄 Загрузить", callback_data=f"rev_load:{seller_id}:0").as_markup()
    )
    await callback.answer()

@dp.callback_query(F.data.startswith("rev_load:"))
async def load_reviews_page(callback: types.CallbackQuery):
    # callback.data = "rev_load:seller_id:page"
    _, seller_id, page_str = callback.data.split(":")
    seller_id = int(seller_id)
    page = int(page_str)

    reviews, total = get_seller_reviews(seller_id, page)
    total_pages = (total + 4) // 5 if total else 1

    conn = sqlite3.connect('brainrot_shop.db')
    c = conn.cursor()
    c.execute("SELECT username FROM users WHERE user_id = ?", (seller_id,))
    res = c.fetchone()
    seller_username = res[0] if res else str(seller_id)
    conn.close()

    avg, total_rating = get_seller_rating(seller_id)
    rating_text = f"{avg}/5" if avg else "нет"

    text = f"👤 Продавец: @{seller_username}\n⭐ Рейтинг: {rating_text} (на основе {total_rating} отзывов)\n\n"
    text += "📝 **Отзывы:**\n\n"
    if not reviews:
        text += "Пока нет отзывов.\n"
    else:
        for r in reviews:
            rating, comment, created_at, username = r
            date = datetime.strptime(created_at, "%Y-%m-%d %H:%M:%S").strftime("%d.%m.%Y")
            stars = "⭐" * rating
            text += f"{stars} {rating}/5 — {comment if comment else 'без комментария'}\n"
            text += f"👤 @{username or 'Аноним'} | 📅 {date}\n\n"

    text += f"\nСтраница {page+1} из {total_pages}"

    builder = InlineKeyboardBuilder()
    if page > 0:
        builder.button(text="⬅️ Назад", callback_data=f"rev_load:{seller_id}:{page-1}")
    if page < total_pages - 1:
        builder.button(text="➡️ Вперёд", callback_data=f"rev_load:{seller_id}:{page+1}")
    builder.button(text="✍️ Оставить отзыв", callback_data=f"leave_review:{seller_id}")
    builder.button(text="🔙 Назад к товару", callback_data="back_to_product")
    builder.adjust(2)

    await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=builder.as_markup())
    await callback.answer()

@dp.callback_query(F.data.startswith("leave_review:"))
async def leave_review_start(callback: types.CallbackQuery, state: FSMContext):
    # callback.data = "leave_review:seller_id"
    _, seller_id = callback.data.split(":")
    seller_id = int(seller_id)
    await state.update_data(seller_id=seller_id)
    await state.set_state(ReviewState.waiting_for_rating)
    await callback.message.edit_text(
        "⭐ Оцените продавца от 1 до 5 (напишите число):\n\n"
        "1 — ужасно\n2 — плохо\n3 — нормально\n4 — хорошо\n5 — отлично",
        reply_markup=InlineKeyboardBuilder().button(text="❌ Отмена", callback_data="cancel_review").as_markup()
    )
    await callback.answer()

@dp.message(ReviewState.waiting_for_rating)
async def process_review_rating(message: types.Message, state: FSMContext):
    if not message.text.isdigit() or int(message.text) not in range(1,6):
        await message.answer("❌ Пожалуйста, введите число от 1 до 5.")
        return
    rating = int(message.text)
    await state.update_data(rating=rating)
    await state.set_state(ReviewState.waiting_for_comment)
    await message.answer(
        f"⭐ Вы поставили оценку: {'⭐'*rating}\n\n"
        "📝 Напишите текстовый отзыв (или отправьте 'пропустить'):",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="⏩ Пропустить"), KeyboardButton(text="❌ Отмена")]],
            resize_keyboard=True
        )
    )

@dp.message(ReviewState.waiting_for_comment)
async def process_review_comment(message: types.Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await state.clear()
        await message.answer("❌ Отзыв отменён.", reply_markup=get_main_menu_keyboard())
        return
    comment = None if message.text == "⏩ Пропустить" else message.text
    data = await state.get_data()
    seller_id = data['seller_id']
    rating = data['rating']
    buyer_id = message.from_user.id
    review_id = add_review(seller_id, buyer_id, None, rating, comment)
    if review_id:
        await message.answer(
            "✅ Ваш отзыв отправлен на модерацию. После проверки он появится в профиле продавца.",
            reply_markup=get_main_menu_keyboard()
        )
        for admin_id in ADMIN_IDS:
            try:
                await bot.send_message(
                    admin_id,
                    f"🆕 Новый отзыв на модерации!\n"
                    f"От: @{message.from_user.username or message.from_user.first_name}\n"
                    f"Оценка: {rating}⭐\n"
                    f"Комментарий: {comment if comment else 'нет'}\n"
                    f"ID отзыва: {review_id}"
                )
            except:
                pass
    else:
        await message.answer("❌ Ошибка при сохранении отзыва. Попробуйте позже.", reply_markup=get_main_menu_keyboard())
    await state.clear()

@dp.callback_query(F.data == "cancel_review")
async def cancel_review(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("❌ Отзыв отменён.")
    await callback.answer()

@dp.callback_query(F.data == "back_to_product")
async def back_to_product(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    product = await get_next_product_for_user(user_id)
    if product:
        await show_product_with_review_button(callback.message, product)
    else:
        await callback.message.answer("😔 Товаров нет", reply_markup=get_main_menu_keyboard())
    await callback.answer()

# ================== МОДЕРАЦИЯ ОТЗЫВОВ (АДМИНКА) ==================
@dp.message(F.text == "📝 Модерация отзывов")
async def moderation_start(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("⛔ У вас нет доступа.")
        return
    review_ids = get_unmoderated_reviews()
    if not review_ids:
        await message.answer("📭 Нет отзывов на модерации.")
        return
    moderation_index[message.from_user.id] = {
        'review_ids': review_ids,
        'current': 0
    }
    await show_moderation_review(message, review_ids[0])

async def show_moderation_review(target, review_id):
    review = get_review_by_id(review_id)
    if not review:
        if isinstance(target, types.Message):
            await target.answer("❌ Отзыв не найден.")
        else:
            await target.message.edit_text("❌ Отзыв не найден.")
        return
    r_id, rating, comment, created_at, buyer_id, buyer_username, seller_id, seller_username = review
    date = datetime.strptime(created_at, "%Y-%m-%d %H:%M:%S").strftime("%d.%m.%Y %H:%M")
    text = (
        f"📝 **Отзыв #{r_id}**\n\n"
        f"👤 **Покупатель:** @{buyer_username or buyer_id}\n"
        f"👤 **Продавец:** @{seller_username or seller_id}\n"
        f"⭐ **Оценка:** {rating}/5\n"
        f"💬 **Комментарий:** {comment if comment else '—'}\n"
        f"📅 **Дата:** {date}\n"
    )
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Одобрить", callback_data=f"mod_approve:{r_id}")
    builder.button(text="❌ Отклонить", callback_data=f"mod_reject:{r_id}")
    builder.button(text="🔍 Запросить док-ва", callback_data=f"mod_evidence:{r_id}")
    user_id = target.from_user.id if isinstance(target, types.CallbackQuery) else target.chat.id
    data = moderation_index.get(user_id)
    if data:
        current_idx = data['current']
        total = len(data['review_ids'])
        if current_idx > 0:
            prev_id = data['review_ids'][current_idx-1]
            builder.button(text="⬅️ Предыдущий", callback_data=f"mod_show:{prev_id}")
        if current_idx < total - 1:
            next_id = data['review_ids'][current_idx+1]
            builder.button(text="➡️ Следующий", callback_data=f"mod_show:{next_id}")
    builder.button(text="🔄 Обновить", callback_data=f"mod_refresh:{r_id}")
    builder.adjust(2,2,2,1)
    if isinstance(target, types.Message):
        await target.answer(text, parse_mode="Markdown", reply_markup=builder.as_markup())
    else:
        await target.message.edit_text(text, parse_mode="Markdown", reply_markup=builder.as_markup())

@dp.callback_query(F.data.startswith("mod_show:"))
async def mod_show_callback(callback: types.CallbackQuery):
    review_id = int(callback.data.split(":")[1])
    user_id = callback.from_user.id
    data = moderation_index.get(user_id)
    if data:
        try:
            idx = data['review_ids'].index(review_id)
            data['current'] = idx
        except ValueError:
            pass
    await show_moderation_review(callback, review_id)
    await callback.answer()

@dp.callback_query(F.data.startswith("mod_approve:"))
async def mod_approve_callback(callback: types.CallbackQuery):
    review_id = int(callback.data.split(":")[1])
    admin_id = callback.from_user.id
    result = approve_review(review_id, admin_id)
    if result:
        seller_id, rating, comment = result
        await callback.answer("✅ Отзыв одобрен!")
        try:
            await bot.send_message(
                seller_id,
                f"📢 Вам оставили новый отзыв!\n"
                f"⭐ Оценка: {rating}/5\n"
                f"💬 Комментарий: {comment if comment else '—'}"
            )
        except:
            pass
    else:
        await callback.answer("❌ Ошибка при одобрении.")
    user_id = callback.from_user.id
    data = moderation_index.get(user_id)
    if data and data['review_ids']:
        try:
            idx = data['review_ids'].index(review_id)
            data['review_ids'].pop(idx)
            if data['review_ids']:
                new_idx = min(idx, len(data['review_ids'])-1)
                data['current'] = new_idx
                await show_moderation_review(callback, data['review_ids'][new_idx])
            else:
                await callback.message.edit_text("✅ Все отзывы обработаны!")
                moderation_index.pop(user_id, None)
        except ValueError:
            pass
    else:
        await callback.message.edit_text("✅ Отзыв одобрен. Больше отзывов нет.")

@dp.callback_query(F.data.startswith("mod_reject:"))
async def mod_reject_callback(callback: types.CallbackQuery):
    review_id = int(callback.data.split(":")[1])
    admin_id = callback.from_user.id
    buyer_id = reject_review(review_id, admin_id)
    if buyer_id:
        await callback.answer("❌ Отзыв отклонён!")
        try:
            await bot.send_message(
                buyer_id,
                "❌ Ваш отзыв не прошёл модерацию. Свяжитесь с администратором для уточнения причин."
            )
        except:
            pass
    else:
        await callback.answer("❌ Ошибка при отклонении.")
    user_id = callback.from_user.id
    data = moderation_index.get(user_id)
    if data and data['review_ids']:
        try:
            idx = data['review_ids'].index(review_id)
            data['review_ids'].pop(idx)
            if data['review_ids']:
                new_idx = min(idx, len(data['review_ids'])-1)
                data['current'] = new_idx
                await show_moderation_review(callback, data['review_ids'][new_idx])
            else:
                await callback.message.edit_text("✅ Все отзывы обработаны!")
                moderation_index.pop(user_id, None)
        except ValueError:
            pass
    else:
        await callback.message.edit_text("✅ Отзыв отклонён. Больше отзывов нет.")

@dp.callback_query(F.data.startswith("mod_evidence:"))
async def mod_evidence_callback(callback: types.CallbackQuery, state: FSMContext):
    review_id = int(callback.data.split(":")[1])
    review = get_review_by_id(review_id)
    if not review:
        await callback.answer("❌ Отзыв не найден.")
        return
    buyer_id = review[4]
    await state.update_data(evidence_review_id=review_id, evidence_buyer_id=buyer_id)
    await state.set_state(ReviewState.waiting_for_evidence)
    await callback.message.edit_text(
        "📝 Введите текст запроса для покупателя (например, попросите прислать скриншоты):",
        reply_markup=InlineKeyboardBuilder().button(text="❌ Отмена", callback_data="cancel_evidence").as_markup()
    )
    await callback.answer()

@dp.message(ReviewState.waiting_for_evidence)
async def process_evidence_request(message: types.Message, state: FSMContext):
    data = await state.get_data()
    buyer_id = data['evidence_buyer_id']
    review_id = data['evidence_review_id']
    request_text = message.text
    try:
        await bot.send_message(
            buyer_id,
            f"🔍 Администратор запросил подтверждение по вашему отзыву #{review_id}:\n\n{request_text}\n\n"
            f"Пожалуйста, отправьте доказательства (скриншоты) в ответном сообщении."
        )
        await message.answer("✅ Запрос отправлен покупателю.")
    except Exception as e:
        await message.answer(f"❌ Не удалось отправить сообщение покупателю: {e}")
    await state.clear()
    await moderation_start(message)

@dp.callback_query(F.data == "cancel_evidence")
async def cancel_evidence(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("❌ Запрос доказательств отменён.")
    await callback.answer()
    await moderation_start(callback.message)

@dp.callback_query(F.data.startswith("mod_refresh:"))
async def mod_refresh_callback(callback: types.CallbackQuery):
    review_id = int(callback.data.split(":")[1])
    await show_moderation_review(callback, review_id)
    await callback.answer()

# ================== ПРОДАВЕЦ (ваши старые обработчики) ==================
# (Все старые обработчики для продавца: добавление, удаление, редактирование товаров должны быть здесь. Я их не трогал, они остаются без изменений. Если их нет в вашем файле – добавьте.)

# ================== О БОТЕ ==================
@dp.message(F.text == "ℹ️ О боте")
async def about_bot(message: types.Message):
    text = (
        "🤖 <b>Steal A Brainrot Shop Bot</b>\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "📌 <b>О проекте:</b>\n"
        "Этот проект полностью готов заменить все чаты по <b>Steal A Brainrot</b>.\n"
        "Удобная, быстрая и безопасная платформа для торговли.\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "🎮 <b>Игра:</b> Brainrot (Roblox)\n"
        "📦 <b>Товары:</b> виртуальные предметы, аккаунты, услуги\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "⚙️ <b>Функции:</b>\n"
        "• 🛍️ Просмотр товаров в ленте\n"
        "• 💰 Продажа своих предметов\n"
        "• ✏️ Редактирование объявлений\n"
        "• 🗑️ Удаление товаров\n"
        "• ⭐ Система лимитов и белый список\n"
        "• 📝 Отзывы и рейтинг продавцов\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "👤 <b>Контакты администратора:</b>\n"
        "📨 <b>@AbelTesayfe</b>\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "🛡️ <b>Правила:</b>\n"
        "• 🚫 Запрещено мошенничество\n"
        "• 💬 Общайтесь уважительно\n"
        "• ✅ Проверяйте сделки перед покупкой\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "✨ <b>Удачи в игре и выгодных сделок!</b>"
    )
    await message.answer(text, parse_mode="HTML", reply_markup=get_main_menu_keyboard())

@dp.message()
async def unknown_command(message: types.Message):
    await message.answer(
        "🤔 Я не понял вашу команду.\n\nИспользуйте кнопки меню или команду /start",
        reply_markup=get_main_menu_keyboard()
    )

# ================== ЗАПУСК БОТА ==================
async def main():
    try:
        logger.info("=" * 70)
        logger.info("🚀 Запуск Brainrot Shop Bot v3.3 (с отзывами и исправленной пагинацией)")
        logger.info("=" * 70)
        logger.info(f"📊 Настройки: Лимит {DAILY_LIMIT} товаров/сутки для обычных пользователей")

        # Создаём таблицы при запуске
        init_database()

        bot_info = await bot.get_me()
        logger.info(f"✅ Бот подключен: @{bot_info.username}")
        logger.info(f"👤 Имя бота: {bot_info.first_name}")
        logger.info(f"🆔 ID бота: {bot_info.id}")

        await bot.delete_webhook(drop_pending_updates=True)

        logger.info("🔄 Запускаю polling...")
        logger.info("✅ БОТ УСПЕШНО ЗАПУЩЕН!")
        logger.info("=" * 70)

        await dp.start_polling(bot, skip_updates=True)

    except KeyboardInterrupt:
        logger.info("\n👋 Бот остановлен пользователем")
    except Exception as e:
        logger.error(f"💥 Критическая ошибка: {e}")

if __name__ == "__main__":
    asyncio.run(main())

