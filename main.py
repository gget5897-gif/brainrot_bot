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
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            expires_at TIMESTAMP,                -- дата истечения (created + 3 дня)
            last_extended_at TIMESTAMP,           -- дата последнего продления
            last_checked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP  -- дата последней проверки актуальности
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
        # Показываем только товары, срок которых ещё не истёк
        c.execute("SELECT * FROM products WHERE expires_at > ? ORDER BY id ASC", (datetime.now(),))
        all_products = c.fetchall()

        if not all_products:
            conn.close()
            return None

        current_position = user_product_positions.get(user_id, 0)
        if current_position >= len(all_products):
            current_position = 0

        product = all_products[current_position]

        next_position = current_position + 1
        if next_position >= len(all_products):
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
        c.execute("SELECT * FROM products WHERE expires_at > ? ORDER BY id ASC LIMIT 1", (datetime.now(),))
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
                   (SELECT username FROM users WHERE user_id = p.seller_id LIMIT 1) as username,
                   p.expires_at
            FROM products p 
            ORDER BY p.id DESC
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
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    get_or_create_user(
        user_id=message.from_user.id,
        username=message.from_user.username,
        first_name=message.from_user.first_name,
        last_name=message.from_user.last_name
    )
    user_product_positions[message.from_user.id] = 0
    await message.answer("🎮 Steal A Brainrot Shop\n\nВыберите свою роль:", reply_markup=get_main_menu_keyboard())

@dp.message(Command("help"))
async def cmd_help(message: types.Message, state: FSMContext):
    await state.clear()
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
async def cmd_mylimit(message: types.Message, state: FSMContext):
    await state.clear()
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
async def cmd_status(message: types.Message, state: FSMContext):
    await state.clear()
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
async def cmd_admin(message: types.Message, state: FSMContext):
    await state.clear()
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("⛔ У вас нет доступа к этой команде.")
        return
    user_product_positions[message.from_user.id] = 0
    await message.answer("👨‍💻 **Панель администратора**\n\nВыберите действие на клавиатуре ниже:", reply_markup=get_admin_keyboard(), parse_mode="Markdown")

@dp.message(F.text == "👁 Просмотреть все товары")
async def admin_show_all_products(message: types.Message, state: FSMContext):
    await state.clear()
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("⛔ У вас нет доступа.")
        return
    try:
        conn = sqlite3.connect('brainrot_shop.db')
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM products")
        total_count = c.fetchone()[0]

        if total_count == 0:
            await message.answer("📭 В базе данных пока нет товаров.")
            conn.close()
            return

        c.execute("""
            SELECT 
                p.id, 
                p.title, 
                p.price, 
                p.contact, 
                p.seller_id,
                (SELECT username FROM users WHERE user_id = p.seller_id LIMIT 1) as username,
                p.expires_at
            FROM products p 
            ORDER BY p.id DESC
        """)
        all_products = c.fetchall()
        conn.close()

        admin_pages[message.from_user.id] = {
            'products': all_products,
            'page': 0,
            'total': total_count
        }

        await send_products_page(message.from_user.id, message)

    except Exception as e:
        logger.error(f"❌ Ошибка в admin_show_all_products: {e}", exc_info=True)
        await message.answer("❌ Произошла ошибка при загрузке товаров.")

async def send_products_page(user_id, target_message_or_callback):
    data = admin_pages.get(user_id)
    if not data:
        return
    products = data['products']
    page = data['page']
    total = data['total']
    per_page = 10
    start = page * per_page
    end = start + per_page
    page_products = products[start:end]
    total_pages = (total + per_page - 1) // per_page

    text = f"📋 <b>Все товары в базе (всего: {total})</b>\n"
    text += f"📄 Страница {page + 1} из {total_pages}\n\n"

    for product in page_products:
        product_id, title, price, contact, seller_id, username, expires_at = product
        safe_title = title[:35] + "..." if len(title) > 35 else title
        seller_info = f"@{username}" if username else f"ID: {seller_id}"
        expires_str = datetime.strptime(expires_at, '%Y-%m-%d %H:%M:%S').strftime('%d.%m.%Y %H:%M') if expires_at else 'не указано'
        text += (
            f"<b>🔢 ID: {product_id}</b>\n"
            f"📌 {safe_title}\n"
            f"💰 {price} | 👤 {seller_info}\n"
            f"📞 @{contact}\n"
            f"⏳ Истекает: {expires_str}\n"
            f"────────────────────\n"
        )

    builder = InlineKeyboardBuilder()
    if page > 0:
        builder.button(text="⬅️ Назад", callback_data="admin_page_prev")
    if end < total:
        builder.button(text="➡️ Вперёд", callback_data="admin_page_next")
    builder.button(text="🔄 Обновить", callback_data="admin_page_refresh")
    builder.adjust(2)

    if isinstance(target_message_or_callback, types.CallbackQuery):
        await target_message_or_callback.message.edit_text(text, parse_mode="HTML", reply_markup=builder.as_markup())
        await target_message_or_callback.answer()
    else:
        await target_message_or_callback.answer(text, parse_mode="HTML", reply_markup=builder.as_markup())

@dp.callback_query(F.data.startswith("admin_page_"))
async def admin_page_callback(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    user_id = callback.from_user.id
    data = admin_pages.get(user_id)
    if not data:
        await callback.answer("❌ Сессия истекла, начните заново.")
        return
    action = callback.data.split("_")[2]
    if action == "prev":
        data['page'] -= 1
    elif action == "next":
        data['page'] += 1
    elif action == "refresh":
        pass
    await send_products_page(user_id, callback)

@dp.message(Command("ids"))
async def cmd_ids(message: types.Message, state: FSMContext):
    await state.clear()
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("⛔ У вас нет доступа.")
        return
    try:
        conn = sqlite3.connect('brainrot_shop.db')
        c = conn.cursor()
        c.execute("SELECT id, title FROM products ORDER BY id DESC")
        products = c.fetchall()
        conn.close()
        if not products:
            await message.answer("📭 Товаров нет в базе.")
            return
        text = "🆔 <b>СПИСОК ID ТОВАРОВ:</b>\n\n"
        for pid, title in products[:50]:
            short_title = title[:25] + "..." if len(title) > 25 else title
            text += f"<b>ID: {pid}</b> - {short_title}\n"
        if len(products) > 50:
            text += f"\n... и ещё {len(products)-50} товаров. Используйте /admin для полного просмотра."
        text += f"\n\n📊 Всего товаров: {len(products)}"
        await message.answer(text, parse_mode="HTML")
    except Exception as e:
        logger.error(f"❌ Ошибка в cmd_ids: {e}")
        await message.answer("❌ Ошибка при получении ID товаров.")

@dp.message(Command("health"))
async def cmd_health(message: types.Message, state: FSMContext):
    await state.clear()
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("⛔ У вас нет доступа.")
        return
    try:
        import os
        import sqlite3
        from datetime import datetime
        memory_mb = 0
        try:
            with open('/proc/self/status') as f:
                for line in f:
                    if line.startswith('VmRSS:'):
                        memory_mb = int(line.split()[1]) / 1024
                        break
        except:
            memory_mb = 0
        db_size = 0
        if os.path.exists('brainrot_shop.db'):
            db_size = os.path.getsize('brainrot_shop.db') / (1024 * 1024)
        conn = sqlite3.connect('brainrot_shop.db')
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM users")
        total_users = c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM products")
        total_products = c.fetchone()[0]
        conn.close()
        text = (
            f"🏥 <b>Диагностика бота</b>\n\n"
            f"<b>Пользователи в базе:</b> {total_users}\n"
            f"<b>Товаров в базе:</b> {total_products}\n"
            f"<b>Размер базы данных:</b> {db_size:.2f} MB\n\n"
            f"<b>Память бота (приблизительно):</b> {memory_mb:.1f} MB\n"
            f"<b>Время:</b> {datetime.now().strftime('%H:%M:%S')}"
        )
        await message.answer(text, parse_mode="HTML")
    except Exception as e:
        logger.error(f"❌ Ошибка в health: {e}")
        await message.answer("❌ Не удалось получить информацию.")

@dp.message(F.text == "🔍 Найти товары пользователя")
async def admin_find_user_products(message: types.Message, state: FSMContext):
    await state.clear()
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("⛔ У вас нет доступа.")
        return
    await state.set_state(AdminActionForm.waiting_for_user_id)
    await message.answer(
        "🔍 **Поиск товаров пользователя**\n\n"
        "Введите ID пользователя или его username (без @):\n\n"
        "Примеры:\n"
        "• `123456789` (ID пользователя)\n"
        "• `username` (юзернейм без @)",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="❌ Отмена поиска")]],
            resize_keyboard=True
        )
    )

@dp.message(AdminActionForm.waiting_for_user_id)
async def process_user_id_for_search(message: types.Message, state: FSMContext):
    if message.text == "❌ Отмена поиска":
        await state.clear()
        await message.answer("❌ Поиск отменен.", reply_markup=get_admin_keyboard())
        return
    search_term = message.text.strip()
    try:
        conn = sqlite3.connect('brainrot_shop.db')
        c = conn.cursor()
        if search_term.isdigit():
            user_id = int(search_term)
            c.execute("SELECT username FROM users WHERE user_id = ?", (user_id,))
            user = c.fetchone()
            username = user[0] if user else None
        else:
            c.execute("SELECT user_id FROM users WHERE username = ?", (search_term,))
            user = c.fetchone()
            if user:
                user_id = user[0]
                username = search_term
            else:
                try:
                    user_id = int(search_term)
                    username = None
                except:
                    await message.answer("❌ Пользователь не найден. Проверьте ID или username.")
                    await state.clear()
                    return
        c.execute("""
            SELECT id, title, price, contact, created_at 
            FROM products 
            WHERE seller_id = ? 
            ORDER BY id DESC
        """, (user_id,))
        products = c.fetchall()
        conn.close()
        await state.clear()
        if not products:
            user_info = f"@{username}" if username else f"ID: {user_id}"
            await message.answer(
                f"📭 У пользователя {user_info} нет товаров.",
                reply_markup=get_admin_keyboard()
            )
            return
        text = f"📋 <b>Товары пользователя</b> "
        if username:
            text += f"<b>@{username}</b> "
        text += f"(ID: <code>{user_id}</code>)\n\n"
        for product in products[:10]:
            created_date = datetime.strptime(product[4], "%Y-%m-%d %H:%M:%S").strftime("%d.%m.%Y")
            text += (
                f"<b>🔢 ID: {product[0]}</b>\n"
                f"📌 Название: {product[1]}\n"
                f"💰 Цена: {product[2]}\n"
                f"📞 Контакт: @{product[3]}\n"
                f"📅 Добавлен: {created_date}\n"
                f"────────────────────\n"
            )
        if len(products) > 10:
            text += f"\n... и ещё {len(products)-10} товаров.\n"
        text += f"\n<b>Всего товаров:</b> {len(products)}"
        await message.answer(text, parse_mode="HTML", reply_markup=get_admin_keyboard())
    except Exception as e:
        logger.error(f"❌ Ошибка в process_user_id_for_search: {e}")
        await message.answer("❌ Произошла ошибка при поиске.")
        await state.clear()

@dp.message(F.text == "🗑 Удалить товар (по ID)")
async def admin_delete_product_start(message: types.Message, state: FSMContext):
    await state.clear()
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("⛔ У вас нет доступа.")
        return
    await state.set_state(AdminActionForm.waiting_for_product_id)
    await message.answer(
        "🗑 <b>Удаление товара</b>\n\n"
        "Введите ID товара, который хотите удалить:\n\n"
        "ID товара можно узнать из списка всех товаров.\n\n"
        "💡 <b>Пример:</b> <code>2</code>",
        parse_mode="HTML",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="❌ Отмена удаления")]],
            resize_keyboard=True
        )
    )

@dp.message(AdminActionForm.waiting_for_product_id)
async def process_product_id_for_delete(message: types.Message, state: FSMContext):
    if message.text == "❌ Отмена удаления":
        await state.clear()
        await message.answer("❌ Удаление отменено.", reply_markup=get_admin_keyboard())
        return
    if not message.text.isdigit():
        await message.answer("❌ ID товара должен быть числом. Попробуйте снова:")
        return
    product_id = int(message.text)
    try:
        product = get_product_by_id(product_id)
        if not product:
            await message.answer("❌ Товар с таким ID не найден.")
            return
        await state.update_data(
            delete_product_id=product_id,
            delete_product_title=product[2],
            delete_seller_id=product[1]
        )
        await state.set_state(AdminActionForm.waiting_for_delete_reason)
        await message.answer(
            f"✅ Товар найден: <b>ID: {product[0]} - {product[2]}</b>\n\n"
            f"Теперь укажите <b>причину удаления</b>:\n\n"
            f"Примеры:\n"
            f"• Нарушение правил магазина\n"
            f"• Мошенничество\n"
            f"• Несоответствие описания",
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"❌ Ошибка в process_product_id_for_delete: {e}")
        await message.answer("❌ Произошла ошибка при поиске товара.")
        await state.clear()

@dp.message(AdminActionForm.waiting_for_delete_reason)
async def process_delete_reason(message: types.Message, state: FSMContext):
    reason = message.text.strip()
    data = await state.get_data()
    product_id = data['delete_product_id']
    product_title = data['delete_product_title']
    seller_id = data['delete_seller_id']
    try:
        conn = sqlite3.connect('brainrot_shop.db')
        c = conn.cursor()
        c.execute("DELETE FROM products WHERE id = ?", (product_id,))
        log_admin_action(
            admin_id=message.from_user.id,
            action_type="delete_product",
            target_id=product_id,
            target_type="product",
            reason=reason,
            details=f"Удален товар: {product_title}"
        )
        conn.commit()
        conn.close()
        await state.clear()
        await message.answer(
            f"✅ Товар <b>ID: {product_id} - {product_title}</b> успешно удален.\n"
            f"📝 Причина: {reason}",
            parse_mode="HTML",
            reply_markup=get_admin_keyboard()
        )
        try:
            asyncio.create_task(
                bot.send_message(
                    seller_id,
                    f"⚠️ <b>Ваш товар был удален администратором</b>\n\n"
                    f"📌 Товар: <b>{product_title}</b> (ID: #{product_id})\n"
                    f"📝 Причина: {reason}\n\n"
                    f"Если вы не согласны с решением, свяжитесь с администрацией.",
                    parse_mode="HTML"
                )
            )
        except Exception as e:
            logger.error(f"❌ Ошибка при отправке уведомления продавцу: {e}")
    except Exception as e:
        logger.error(f"❌ Ошибка при удалении товара: {e}")
        await message.answer("❌ Произошла ошибка при удалении товара.")
        await state.clear()

@dp.message(F.text == "✏️ Редактировать любой товар")
async def admin_edit_product(message: types.Message, state: FSMContext):
    await state.clear()
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("⛔ У вас нет доступа.")
        return
    await message.answer(
        "✏️ <b>Редактирование любого товара</b>\n\n"
        "Для редактирования товара введите его ID.\n"
        "Вы можете получить ID товара из списка всех товаров.\n\n"
        "Введите ID товара для редактирования:",
        parse_mode="HTML",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="❌ Отмена")]],
            resize_keyboard=True
        )
    )
    await state.set_state(AdminActionForm.waiting_for_product_id)
    await state.update_data(action="edit_product")

@dp.message(F.text == "⛔ Бан/разбан пользователя")
async def admin_ban_user_start(message: types.Message, state: FSMContext):
    await state.clear()
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("⛔ У вас нет доступа.")
        return
    await state.set_state(AdminActionForm.waiting_for_user_id_for_ban)
    await message.answer(
        "⛔ <b>Бан/разбан пользователя</b>\n\n"
        "Введите ID пользователя или его username (без @):\n\n"
        "Примеры:\n"
        "• 123456789 (ID пользователя)\n"
        "• username (юзернейм без @)\n\n"
        "После ввода вы сможете забанить или разбанить пользователя.",
        parse_mode="HTML",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="❌ Отмена")]],
            resize_keyboard=True
        )
    )

@dp.message(AdminActionForm.waiting_for_user_id_for_ban)
async def process_ban_user_id(message: types.Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await state.clear()
        await message.answer("❌ Операция отменена.", reply_markup=get_admin_keyboard())
        return
    search_term = message.text.strip()
    admin_id = message.from_user.id
    user = get_user_by_id_or_username(search_term)
    if not user:
        await message.answer("❌ Пользователь не найден. Проверьте ID или username и попробуйте снова:")
        return
    user_id, username, is_banned, ban_reason = user
    await state.update_data(
        ban_user_id=user_id,
        ban_username=username,
        is_banned_current=is_banned
    )
    user_info = f"@{username}" if username else f"ID: {user_id}"
    if is_banned:
        await state.set_state(AdminActionForm.waiting_for_ban_reason)
        await message.answer(
            f"ℹ️ <b>Пользователь {user_info} уже забанен.</b>\n\n"
            f"📝 Причина бана: {ban_reason}\n\n"
            f"Хотите разбанить этого пользователя?\n"
            f"Введите 'ДА' для разбана или 'НЕТ' для отмены:",
            parse_mode="HTML",
            reply_markup=ReplyKeyboardMarkup(
                keyboard=[
                    [KeyboardButton(text="ДА"), KeyboardButton(text="НЕТ")],
                    [KeyboardButton(text="❌ Отмена")]
                ],
                resize_keyboard=True
            )
        )
    else:
        await state.set_state(AdminActionForm.waiting_for_ban_reason)
        await message.answer(
            f"✅ <b>Пользователь найден: {user_info}</b>\n\n"
            f"Статус: <b>Не забанен</b>\n\n"
            f"Введите причину для бана этого пользователя:",
            parse_mode="HTML",
            reply_markup=ReplyKeyboardMarkup(
                keyboard=[[KeyboardButton(text="❌ Отмена")]],
                resize_keyboard=True
            )
        )

@dp.message(AdminActionForm.waiting_for_ban_reason)
async def process_ban_reason(message: types.Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await state.clear()
        await message.answer("❌ Операция отменена.", reply_markup=get_admin_keyboard())
        return
    data = await state.get_data()
    user_id = data.get('ban_user_id')
    username = data.get('ban_username')
    is_banned_current = data.get('is_banned_current')
    admin_id = message.from_user.id
    user_info = f"@{username}" if username else f"ID: {user_id}"
    if is_banned_current:
        if message.text.upper() == "ДА":
            if unban_user_in_db(user_id, admin_id):
                await state.clear()
                await message.answer(
                    f"✅ <b>Пользователь {user_info} успешно разбанен!</b>\n\n"
                    f"Теперь он снова может добавлять товары.",
                    parse_mode="HTML",
                    reply_markup=get_admin_keyboard()
                )
                try:
                    await bot.send_message(
                        user_id,
                        "🎉 <b>Вас разбанили!</b>\n\n"
                        "Теперь вы снова можете добавлять товары в боте.",
                        parse_mode="HTML"
                    )
                except:
                    pass
            else:
                await message.answer("❌ Произошла ошибка при разбане пользователя.")
        elif message.text.upper() == "НЕТ":
            await state.clear()
            await message.answer("❌ Разбан отменен.", reply_markup=get_admin_keyboard())
        else:
            await message.answer("Пожалуйста, введите 'ДА' для разбана или 'НЕТ' для отмены:")
    else:
        reason = message.text.strip()
        if len(reason) < 3:
            await message.answer("❌ Причина бана должна содержать не менее 3 символов. Введите причину:")
            return
        if ban_user_in_db(user_id, reason, admin_id):
            await state.clear()
            await message.answer(
                f"✅ <b>Пользователь {user_info} успешно забанен!</b>\n\n"
                f"📝 Причина: {reason}\n\n"
                f"Теперь он не сможет добавлять новые товары.",
                parse_mode="HTML",
                reply_markup=get_admin_keyboard()
            )
            try:
                await bot.send_message(
                    user_id,
                    f"⛔ <b>Вас заблокировали в боте!</b>\n\n"
                    f"📝 Причина: {reason}\n\n"
                    f"Вы больше не можете добавлять товары.\n"
                    f"Если вы считаете, что это ошибка, свяжитесь с администратором.",
                    parse_mode="HTML"
                )
            except:
                pass
        else:
            await message.answer("❌ Произошла ошибка при бане пользователя.")

@dp.message(F.text == "📊 Статистика")
async def admin_stats(message: types.Message, state: FSMContext):
    await state.clear()
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("⛔ У вас нет доступа.")
        return
    try:
        conn = sqlite3.connect('brainrot_shop.db')
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM products")
        total_products = c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM users")
        total_users = c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM users WHERE is_banned = 1")
        banned_users = c.fetchone()[0]
        c.execute("""
            SELECT DATE(created_at), COUNT(*) 
            FROM products 
            WHERE created_at >= date('now', '-7 days')
            GROUP BY DATE(created_at)
            ORDER BY DATE(created_at) DESC
        """)
        last_7_days = c.fetchall()
        conn.close()
        text = (
            "📊 <b>Статистика бота</b>\n\n"
            f"<b>👥 Пользователи:</b> {total_users}\n"
            f"<b>⛔ Забанено:</b> {banned_users}\n"
            f"<b>🛍️ Товаров всего:</b> {total_products}\n\n"
        )
        if last_7_days:
            text += "<b>📈 Активность за 7 дней:</b>\n"
            for day_data in last_7_days:
                day = datetime.strptime(day_data[0], "%Y-%m-%d").strftime("%d.%m")
                text += f"• {day}: {day_data[1]} товаров\n"
        await message.answer(text, parse_mode="HTML", reply_markup=get_admin_keyboard())
    except Exception as e:
        logger.error(f"❌ Ошибка в admin_stats: {e}")
        await message.answer("❌ Произошла ошибка при загрузке статистики.")

@dp.message(F.text == "🏠 Выход из админки")
async def admin_exit(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("Выход из панели администратора.", reply_markup=get_main_menu_keyboard())

# ================== БЕЛЫЙ СПИСОК ==================
@dp.message(F.text == "⚪ Управление белым списком")
async def admin_whitelist_menu(message: types.Message, state: FSMContext):
    await state.clear()
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("⛔ У вас нет доступа.")
        return
    await message.answer(
        "⚪ **Управление белого списка**\n\n"
        "Пользователи в белом списке:\n"
        "• Не имеют лимитов на добавление товаров\n"
        "• Могут добавлять неограниченное количество товаров\n\n"
        "Выберите действие:",
        parse_mode="Markdown",
        reply_markup=get_whitelist_keyboard()
    )

@dp.message(F.text == "➕ Добавить в белый список")
async def admin_add_to_whitelist_start(message: types.Message, state: FSMContext):
    await state.clear()
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
    if message.text == "❌ Отмена":
        await state.clear()
        await message.answer("❌ Добавление отменено.", reply_markup=get_whitelist_keyboard())
        return
    search_term = message.text.strip()
    admin_id = message.from_user.id
    try:
        conn = sqlite3.connect('brainrot_shop.db')
        c = conn.cursor()
        if search_term.isdigit():
            user_id = int(search_term)
            c.execute("SELECT username FROM users WHERE user_id = ?", (user_id,))
            user = c.fetchone()
            username = user[0] if user else None
        else:
            c.execute("SELECT user_id FROM users WHERE username = ?", (search_term,))
            user = c.fetchone()
            if user:
                user_id = user[0]
                username = search_term
            else:
                await message.answer("❌ Пользователь не найден в базе.")
                await state.clear()
                return
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
        if not username:
            c.execute("INSERT INTO users (user_id, is_whitelisted) VALUES (?, 1)", (user_id,))
        else:
            c.execute("UPDATE users SET is_whitelisted = 1 WHERE user_id = ?", (user_id,))
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
        try:
            await bot.send_message(
                user_id,
                "🎉 **Вас добавили в белый список!**\n\n"
                "Теперь вы можете добавлять неограниченное количество товаров "
                "без каких-либо лимитов.\n\n"
                "Спасибо за вашу активность! 🚀",
                parse_mode="Markdown"
            )
        except:
            pass
    except Exception as e:
        logger.error(f"❌ Ошибка в process_add_to_whitelist: {e}")
        await message.answer("❌ Произошла ошибка при добавлении в белый список.")
        await state.clear()

@dp.message(F.text == "➖ Удалить из белого списка")
async def admin_remove_from_whitelist_start(message: types.Message, state: FSMContext):
    await state.clear()
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
    if message.text == "❌ Отмена":
        await state.clear()
        await message.answer("❌ Удаление отменено.", reply_markup=get_whitelist_keyboard())
        return
    search_term = message.text.strip()
    admin_id = message.from_user.id
    try:
        conn = sqlite3.connect('brainrot_shop.db')
        c = conn.cursor()
        if search_term.isdigit():
            user_id = int(search_term)
            c.execute("SELECT username, is_whitelisted FROM users WHERE user_id = ?", (user_id,))
            result = c.fetchone()
            username = result[0] if result else None
        else:
            c.execute("SELECT user_id, username, is_whitelisted FROM users WHERE username = ?", (search_term,))
            result = c.fetchone()
        if not result:
            await message.answer("❌ Пользователь не найден в базе.")
            await state.clear()
            return
        if len(result) == 3:
            user_id, username, is_whitelisted = result
        else:
            user_id, is_whitelisted = result[0], result[1]
            username = search_term if not search_term.isdigit() else None
        if not is_whitelisted:
            user_info = f"@{username}" if username else f"ID: {user_id}"
            await message.answer(
                f"ℹ️ Пользователь {user_info} не в белом списке.",
                reply_markup=get_whitelist_keyboard()
            )
            await state.clear()
            return
        c.execute("UPDATE users SET is_whitelisted = 0 WHERE user_id = ?", (user_id,))
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
        try:
            await bot.send_message(
                user_id,
                f"⚠️ **Вас удалили из белого списка**\n\n"
                f"Теперь на вас распространяются обычные лимиты:\n"
                f"• {DAILY_LIMIT} товаров в сутки\n\n"
                f"Если вы считаете, что это ошибка, свяжитесь с администратором.",
                parse_mode="Markdown"
            )
        except:
            pass
    except Exception as e:
        logger.error(f"❌ Ошибка в process_remove_from_whitelist: {e}")
        await message.answer("❌ Произошла ошибка при удалении из белого списка.")
        await state.clear()

@dp.message(F.text == "👁 Показать белый список")
async def admin_show_whitelist(message: types.Message, state: FSMContext):
    await state.clear()
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("⛔ У вас нет доступа.")
        return
    users = get_whitelist()
    if not users:
        await message.answer("📭 Белый список пуст.", reply_markup=get_whitelist_keyboard())
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
async def admin_limits_stats(message: types.Message, state: FSMContext):
    await state.clear()
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("⛔ У вас нет доступа.")
        return
    try:
        conn = sqlite3.connect('brainrot_shop.db')
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM users")
        total_users = c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM users WHERE is_whitelisted = 1")
        whitelisted = c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM users WHERE is_banned = 1")
        banned = c.fetchone()[0]
        time_24h_ago = (datetime.now() - timedelta(hours=24)).strftime('%Y-%m-%d %H:%M:%S')
        c.execute("""
            SELECT u.user_id, u.username, COUNT(p.id) as product_count
            FROM users u
            LEFT JOIN products p ON u.user_id = p.seller_id AND p.created_at >= ?
            WHERE u.is_whitelisted = 0 AND u.is_banned = 0
            GROUP BY u.user_id
            HAVING product_count >= ?
            ORDER BY product_count DESC
        """, (time_24h_ago, DAILY_LIMIT))
        users_at_limit = c.fetchall()
        c.execute("""
            SELECT u.user_id, u.username, COUNT(p.id) as product_count
            FROM users u
            LEFT JOIN products p ON u.user_id = p.seller_id AND p.created_at >= ?
            WHERE u.is_banned = 0
            GROUP BY u.user_id
            ORDER BY product_count DESC
            LIMIT 10
        """, (time_24h_ago,))
        top_active = c.fetchall()
        conn.close()
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
            for user in users_at_limit[:5]:
                user_id, username, count = user
                user_ident = f"@{username}" if username else f"ID: {user_id}"
                text += f"• {user_ident}: {count} товаров\n"
            if len(users_at_limit) > 5:
                text += f"• ...и еще {len(users_at_limit)-5} пользователей\n"
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
async def back_to_admin(message: types.Message, state: FSMContext):
    await state.clear()
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("⛔ У вас нет доступа.")
        return
    await message.answer("Возврат в панель администратора.", reply_markup=get_admin_keyboard())

# ================== ПОКУПАТЕЛЬ ==================
@dp.message(F.text == "🛍️ Покупатель")
async def buyer_mode(message: types.Message, state: FSMContext):
    await state.clear()
    user_product_positions[message.from_user.id] = 0
    await message.answer("🛍️ Режим покупателя", reply_markup=get_buyer_keyboard())
    product = await get_first_product()
    if product:
        await show_product_with_review_button(message, product)
    else:
        await message.answer("😔 Товаров пока нет\n\nПопросите друзей добавить товары!", reply_markup=get_main_menu_keyboard())

@dp.message(F.text == "⏭️ Следующий товар")
async def next_product(message: types.Message, state: FSMContext):
    await state.clear()
    product = await get_next_product_for_user(message.from_user.id)
    if product:
        await show_product_with_review_button(message, product)
    else:
        await message.answer("😔 Товаров больше нет")

async def show_product_with_review_button(message: types.Message, product):
    # product = (id, seller_id, title, description, price, contact, created_at, expires_at, last_extended_at, last_checked_at)
    # индексы: 0:id, 1:seller_id, 2:title, 3:description, 4:price, 5:contact, 6:created_at, 7:expires_at, ...
    product_id = product[0]
    seller_id = product[1]
    title = product[2]
    description = product[3]
    price = product[4]
    contact = product[5]
    expires_at = product[7]
    expires_str = datetime.strptime(expires_at, '%Y-%m-%d %H:%M:%S').strftime('%d.%m.%Y %H:%M') if expires_at else 'не указано'
    text = (
        f"🛒 Товар #{product_id}\n\n"
        f"📌 Название: {title}\n"
        f"📝 Описание: {description}\n"
        f"💰 Цена: {price}\n"
        f"👤 Контакты: @{contact}\n"
        f"⏳ Истекает: {expires_str}"
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
async def back_to_main_callback(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.delete()
    await cmd_start(callback.message, state)

# ================== ОТЗЫВЫ ==================
@dp.callback_query(F.data.startswith("reviews:"))
async def show_seller_reviews(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
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
async def load_reviews_page(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
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
    await state.clear()
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
async def back_to_product(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    user_id = callback.from_user.id
    product = await get_next_product_for_user(user_id)
    if product:
        await show_product_with_review_button(callback.message, product)
    else:
        await callback.message.answer("😔 Товаров нет", reply_markup=get_main_menu_keyboard())
    await callback.answer()

# ================== МОДЕРАЦИЯ ОТЗЫВОВ (АДМИНКА) ==================
@dp.message(F.text == "📝 Модерация отзывов")
async def moderation_start(message: types.Message, state: FSMContext):
    await state.clear()
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

# ================== ПРОДАВЕЦ ==================
@dp.message(F.text == "💰 Продавец")
async def seller_mode(message: types.Message, state: FSMContext):
    await state.clear()
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

@dp.message(F.text == "➕ Добавить товар")
async def add_product_start(message: types.Message, state: FSMContext):
    await state.clear()
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

@dp.message(F.text == "❌ Отмена")
async def cancel_operation(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("❌ Операция отменена", reply_markup=get_seller_keyboard())

@dp.message(ProductForm.title)
async def process_title(message: types.Message, state: FSMContext):
    if len(message.text) > 100:
        await message.answer("❌ Слишком длинное название! Максимум 100 символов.")
        return
    await state.update_data(title=message.text)
    await state.set_state(ProductForm.description)
    await message.answer("📝 Введите описание товара:")

@dp.message(ProductForm.description)
async def process_description(message: types.Message, state: FSMContext):
    await state.update_data(description=message.text)
    await state.set_state(ProductForm.price)
    await message.answer("💰 Введите цену товара (например: 100 Robux):")

@dp.message(ProductForm.price)
async def process_price(message: types.Message, state: FSMContext):
    await state.update_data(price=message.text)
    await state.set_state(ProductForm.contact)
    await message.answer("👤 Введите ваш username для связи (без @):")

@dp.message(ProductForm.contact)
async def process_contact(message: types.Message, state: FSMContext):
    data = await state.get_data()
    try:
        conn = sqlite3.connect('brainrot_shop.db')
        c = conn.cursor()
        # Вычисляем дату истечения (3 дня от текущего момента)
        expires_at = datetime.now() + timedelta(days=3)
        c.execute(
            """INSERT INTO products 
               (seller_id, title, description, price, contact, created_at, expires_at, last_checked_at) 
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (message.from_user.id, data['title'], data['description'], 
             data['price'], message.text, datetime.now(), expires_at, datetime.now())
        )
        conn.commit()
        conn.close()

        can_add, limit_message = can_user_add_product(message.from_user.id)

        await message.answer(
            f"✅ Товар добавлен!\n\n"
            f"📌 Название: {data['title']}\n"
            f"📝 Описание: {data['description']}\n"
            f"💰 Цена: {data['price']}\n"
            f"👤 Контакты: @{message.text}\n"
            f"⏳ Истекает: {expires_at.strftime('%d.%m.%Y %H:%M')}\n\n"
            f"{limit_message}",
            reply_markup=get_seller_keyboard()
        )
    except Exception as e:
        await message.answer(f"❌ Ошибка при сохранении: {e}")
    finally:
        await state.clear()

@dp.message(F.text == "📋 Мои товары")
async def show_my_products(message: types.Message, state: FSMContext):
    await state.clear()
    conn = sqlite3.connect('brainrot_shop.db')
    c = conn.cursor()
    c.execute(
        """SELECT id, title, price, contact, expires_at FROM products WHERE seller_id = ? ORDER BY id DESC""",
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
    for product in products:
        pid, title, price, contact, expires_at = product
        expires_str = datetime.strptime(expires_at, '%Y-%m-%d %H:%M:%S').strftime('%d.%m.%Y %H:%M') if expires_at else 'не указано'
        text += f"#{pid} - {title}\n   💰 {price} | 👤 @{contact}\n   ⏳ Истекает: {expires_str}\n\n"
    await message.answer(text, reply_markup=get_seller_keyboard())

@dp.message(F.text == "✏️ Управление товарами")
async def manage_products(message: types.Message, state: FSMContext):
    await state.clear()
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

@dp.callback_query(F.data.startswith("delete_"))
async def delete_product_callback(callback: types.CallbackQuery):
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

@dp.callback_query(F.data.startswith("edit_"))
async def edit_product_callback(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
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

@dp.callback_query(F.data == "back_to_seller")
async def back_to_seller_callback(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.delete()
    await seller_mode(callback.message, state)

# ================== ФОНОВАЯ ЗАДАЧА: УВЕДОМЛЕНИЯ ОБ ИСТЕЧЕНИИ ==================
async def check_expiring_products():
    """Проверяет товары, которые истекают через 6 часов, и отправляет уведомления"""
    while True:
        try:
            conn = sqlite3.connect('brainrot_shop.db')
            c = conn.cursor()
            # Ищем товары, у которых expires_at между сейчас и через 6 часов
            time_6h_later = datetime.now() + timedelta(hours=6)
            c.execute("""
                SELECT id, seller_id, title, expires_at 
                FROM products 
                WHERE expires_at BETWEEN ? AND ?
            """, (datetime.now(), time_6h_later))
            expiring_products = c.fetchall()
            conn.close()
            for product in expiring_products:
                product_id, seller_id, title, expires_at = product
                # Отправляем уведомление продавцу
                try:
                    kb = InlineKeyboardBuilder()
                    kb.button(text="⏳ Продлить на 3 дня", callback_data=f"extend_{product_id}")
                    await bot.send_message(
                        seller_id,
                        f"⚠️ <b>Ваш товар скоро истечёт!</b>\n\n"
                        f"📌 Название: {title}\n"
                        f"⏳ Истекает: {datetime.strptime(expires_at, '%Y-%m-%d %H:%M:%S').strftime('%d.%m.%Y %H:%M')}\n\n"
                        f"Нажмите кнопку ниже, чтобы продлить товар ещё на 3 дня.",
                        parse_mode="HTML",
                        reply_markup=kb.as_markup()
                    )
                    logger.info(f"✅ Уведомление об истечении отправлено продавцу {seller_id} для товара {product_id}")
                except Exception as e:
                    logger.error(f"❌ Не удалось отправить уведомление продавцу {seller_id}: {e}")
            # Ждём 1 час до следующей проверки
            await asyncio.sleep(3600)
        except Exception as e:
            logger.error(f"❌ Ошибка в фоновой задаче проверки истечения: {e}")
            await asyncio.sleep(3600)

# ================== ФОНОВАЯ ЗАДАЧА: ПРОВЕРКА АКТУАЛЬНОСТИ ==================
async def check_product_relevance():
    """Раз в 3 дня спрашивает продавца, актуален ли товар"""
    while True:
        try:
            conn = sqlite3.connect('brainrot_shop.db')
            c = conn.cursor()
            # Находим товары, которые не проверялись больше 3 дней и ещё не истекли
            three_days_ago = datetime.now() - timedelta(days=3)
            c.execute("""
                SELECT id, seller_id, title 
                FROM products 
                WHERE last_checked_at < ? AND expires_at > ?
            """, (three_days_ago, datetime.now()))
            products_to_check = c.fetchall()
            conn.close()
            for product_id, seller_id, title in products_to_check:
                try:
                    kb = InlineKeyboardBuilder()
                    kb.button(text="✅ Продан, удалить", callback_data=f"sold_{product_id}")
                    kb.button(text="❌ Ещё продаётся", callback_data=f"still_selling_{product_id}")
                    kb.adjust(1)
                    await bot.send_message(
                        seller_id,
                        f"❓ <b>Проверка актуальности товара</b>\n\n"
                        f"📌 Название: {title}\n\n"
                        f"Товар всё ещё продаётся?",
                        parse_mode="HTML",
                        reply_markup=kb.as_markup()
                    )
                    logger.info(f"✅ Запрос актуальности отправлен продавцу {seller_id} для товара {product_id}")
                    # Обновляем last_checked_at сразу, чтобы не спамить
                    conn = sqlite3.connect('brainrot_shop.db')
                    c = conn.cursor()
                    c.execute("UPDATE products SET last_checked_at = ? WHERE id = ?", 
                             (datetime.now(), product_id))
                    conn.commit()
                    conn.close()
                except Exception as e:
                    logger.error(f"❌ Ошибка при отправке запроса актуальности для товара {product_id}: {e}")
            # Ждём 6 часов (можно изменить)
            await asyncio.sleep(6 * 3600)
        except Exception as e:
            logger.error(f"❌ Ошибка в фоновой задаче проверки актуальности: {e}")
            await asyncio.sleep(3600)

# ================== ОБРАБОТЧИКИ ПРОДЛЕНИЯ И ПРОВЕРКИ АКТУАЛЬНОСТИ ==================
@dp.callback_query(F.data.startswith("extend_"))
async def extend_product_callback(callback: types.CallbackQuery):
    """Продление товара на 3 дня"""
    product_id = int(callback.data.split("_")[1])
    try:
        conn = sqlite3.connect('brainrot_shop.db')
        c = conn.cursor()
        c.execute("SELECT seller_id, title, expires_at, last_extended_at FROM products WHERE id = ?", (product_id,))
        product = c.fetchone()
        if not product:
            await callback.answer("❌ Товар не найден!", show_alert=True)
            conn.close()
            return
        seller_id, title, expires_at, last_extended_at = product
        if callback.from_user.id != seller_id:
            await callback.answer("❌ Вы не можете продлить чужой товар!", show_alert=True)
            conn.close()
            return
        # Проверяем, можно ли продлить (не прошло ли 3 дня с последнего продления)
        if last_extended_at:
            last_extended = datetime.strptime(last_extended_at, '%Y-%m-%d %H:%M:%S')
            if datetime.now() - last_extended < timedelta(days=3):
                remaining = timedelta(days=3) - (datetime.now() - last_extended)
                hours = int(remaining.total_seconds() // 3600)
                await callback.answer(f"⏳ Продлить можно будет через {hours} часов", show_alert=True)
                conn.close()
                return
        new_expires_at = datetime.now() + timedelta(days=3)
        c.execute(
            "UPDATE products SET expires_at = ?, last_extended_at = ? WHERE id = ?",
            (new_expires_at, datetime.now(), product_id)
        )
        conn.commit()
        conn.close()
        await callback.message.edit_text(
            f"✅ <b>Товар успешно продлён!</b>\n\n"
            f"📌 Название: {title}\n"
            f"⏳ Новая дата истечения: {new_expires_at.strftime('%d.%m.%Y %H:%M')}\n\n"
            f"Следующее продление будет доступно через 3 дня.",
            parse_mode="HTML"
        )
        await callback.answer("✅ Товар продлён на 3 дня!", show_alert=False)
    except Exception as e:
        logger.error(f"❌ Ошибка при продлении товара {product_id}: {e}")
        await callback.answer("❌ Произошла ошибка при продлении", show_alert=True)

@dp.callback_query(F.data.startswith("sold_"))
async def mark_as_sold(callback: types.CallbackQuery):
    """Отметить товар как проданный и удалить"""
    product_id = int(callback.data.split("_")[1])
    try:
        conn = sqlite3.connect('brainrot_shop.db')
        c = conn.cursor()
        c.execute("SELECT seller_id, title FROM products WHERE id = ?", (product_id,))
        product = c.fetchone()
        if not product:
            await callback.answer("❌ Товар не найден!", show_alert=True)
            conn.close()
            return
        seller_id, title = product
        if callback.from_user.id != seller_id and callback.from_user.id not in ADMIN_IDS:
            await callback.answer("❌ Только продавец может отметить товар как проданный!", show_alert=True)
            conn.close()
            return
        c.execute("DELETE FROM products WHERE id = ?", (product_id,))
        conn.commit()
        conn.close()
        await callback.message.edit_text(
            f"✅ Товар <b>{title}</b> отмечен как проданный и удалён из ленты.",
            parse_mode="HTML"
        )
        await callback.answer("✅ Товар удалён", show_alert=False)
        # Уведомление администратора
        for admin_id in ADMIN_IDS:
            try:
                await bot.send_message(
                    admin_id,
                    f"📊 Продавец @{callback.from_user.username or callback.from_user.id} отметил товар как проданный:\n"
                    f"📌 {title}\n"
                    f"🆔 Товар #{product_id}"
                )
            except:
                pass
    except Exception as e:
        logger.error(f"❌ Ошибка при отметке товара {product_id} как проданного: {e}")
        await callback.answer("❌ Произошла ошибка", show_alert=True)

@dp.callback_query(F.data.startswith("still_selling_"))
async def mark_as_still_selling(callback: types.CallbackQuery):
    """Подтвердить, что товар ещё продаётся"""
    product_id = int(callback.data.split("_")[2])
    try:
        conn = sqlite3.connect('brainrot_shop.db')
        c = conn.cursor()
        c.execute("SELECT seller_id, title FROM products WHERE id = ?", (product_id,))
        product = c.fetchone()
        if not product:
            await callback.answer("❌ Товар не найден!", show_alert=True)
            conn.close()
            return
        seller_id, title = product
        if callback.from_user.id != seller_id and callback.from_user.id not in ADMIN_IDS:
            await callback.answer("❌ Только продавец может подтвердить актуальность!", show_alert=True)
            conn.close()
            return
        c.execute("UPDATE products SET last_checked_at = ? WHERE id = ?", (datetime.now(), product_id))
        conn.commit()
        conn.close()
        await callback.message.edit_text(
            f"✅ Спасибо! Товар <b>{title}</b> остаётся в ленте.\n"
            f"Следующая проверка через 3 дня.",
            parse_mode="HTML"
        )
        await callback.answer("✅ Актуальность подтверждена", show_alert=False)
    except Exception as e:
        logger.error(f"❌ Ошибка при подтверждении актуальности товара {product_id}: {e}")
        await callback.answer("❌ Произошла ошибка", show_alert=True)

# ================== ГЛАВНОЕ МЕНЮ ==================
@dp.message(F.text == "🏠 Главное меню")
async def main_menu(message: types.Message, state: FSMContext):
    await state.clear()
    user_product_positions[message.from_user.id] = 0
    await cmd_start(message, state)

# ================== О БОТЕ ==================
@dp.message(F.text == "ℹ️ О боте")
async def about_bot(message: types.Message, state: FSMContext):
    await state.clear()
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
        "• 📝 Отзывы и рейтинг продавцов\n"
        "• ⏳ Автоматическое истечение товаров через 3 дня\n"
        "• 🔄 Продление и проверка актуальности\n\n"
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
async def unknown_command(message: types.Message, state: FSMContext):
    current_state = await state.get_state()
    if current_state is None:
        await message.answer(
            "🤔 Я не понял вашу команду.\n\nИспользуйте кнопки меню или команду /start",
            reply_markup=get_main_menu_keyboard()
        )
    # Если состояние активно, игнорируем

# ================== ЗАПУСК БОТА ==================
async def main():
    try:
        logger.info("=" * 70)
        logger.info("🚀 Запуск Brainrot Shop Bot v4.0 (полностью рабочий с истечением и актуальностью)")
        logger.info("=" * 70)
        logger.info(f"📊 Настройки: Лимит {DAILY_LIMIT} товаров/сутки для обычных пользователей")

        # Создаём таблицы при запуске
        init_database()

        # Запускаем фоновые задачи
        asyncio.create_task(check_expiring_products())
        asyncio.create_task(check_product_relevance())

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

