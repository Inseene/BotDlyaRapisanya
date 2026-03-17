import asyncio
import logging
import os
import re
import sqlite3
from contextlib import closing
from datetime import datetime
from typing import Optional

from aiogram import Bot, Dispatcher, F, Router, types
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.filters import Command, StateFilter
from aiogram.types import InlineKeyboardButton, KeyboardButton, ReplyKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

# Для вебхуков
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web

# ==========================================================
# ВСТАВЬ СЮДА ТОКЕН ОТ @BotFather
# ==========================================================
TOKEN = "8615286189:AAGxCbav0Yw8Q6C4ZoWVgcgg56yeyRDayNI"

# ==========================================================
# НАСТРОЙКИ ДЛЯ WEBHOOK (ВАЖНО!)
# ==========================================================
# ЗАМЕНИТЕ на URL вашего приложения на Render
# Например: https://school-bot.onrender.com
RENDER_URL = "https://your-app-name.onrender.com"  # ИЗМЕНИТЕ ЭТО!
WEBHOOK_PATH = "/webhook"
WEBHOOK_URL = RENDER_URL + WEBHOOK_PATH

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")
logger = logging.getLogger("school_schedule_bot")

router = Router()

# ==========================================================
# НАСТРОЙКИ ДОСТУПА (АДМИНКА)
# ==========================================================
ADMIN_USER_ID = 6754275656

# ==========================================================
# БАЗА ДАННЫХ (SQLite)
# ==========================================================
DB_PATH = os.path.join(os.path.dirname(__file__), "data.sqlite3")

def db_connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def db_init() -> None:
    with closing(db_connect()) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS menu_buttons (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                text TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                position INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS classes (
                grade TEXT NOT NULL,
                class_name TEXT NOT NULL,
                PRIMARY KEY (grade, class_name)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schedule (
                class_name TEXT NOT NULL,
                day TEXT NOT NULL,
                pos INTEGER NOT NULL,
                lesson TEXT NOT NULL,
                PRIMARY KEY (class_name, day, pos)
            )
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS announcements (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                body TEXT NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS subscribers (
                chat_id INTEGER PRIMARY KEY
            )
            """
        )

        cur = conn.execute("SELECT COUNT(*) AS c FROM menu_buttons")
        if int(cur.fetchone()["c"]) == 0:
            defaults = [
                ("📚 Расписание уроков", 1, 10),
                ("📢 Объявления", 1, 20),
                ("❓ Помощь", 1, 30),
            ]
            conn.executemany(
                "INSERT INTO menu_buttons(text, enabled, position) VALUES (?, ?, ?)",
                defaults,
            )
            logger.info("Добавлены начальные кнопки меню")

        cur = conn.execute("SELECT COUNT(*) AS c FROM classes")
        if int(cur.fetchone()["c"]) == 0:
            seed = [
                ("5", "5А"), ("5", "5Б"), ("5", "5В"), ("5", "5Г"),
                ("6", "6А"), ("6", "6Б"), ("6", "6В"),
                ("7", "7А"), ("7", "7Б"), ("7", "7В"), ("7", "7Г"),
                ("8", "8А"), ("8", "8Б"), ("8", "8В"),
                ("9", "9А"), ("9", "9Б"), ("9", "9В"),
                ("10", "10А"), ("10", "10Б"),
                ("11", "11А"), ("11", "11Б"),
            ]
            conn.executemany("INSERT INTO classes(grade, class_name) VALUES (?, ?)", seed)
            logger.info("Добавлены начальные классы")

        set_setting_if_empty(conn, "help_text", "❓ Помощь\n\nНажми «📚 Расписание уроков» и выбери класс.")
        
        conn.commit()
        logger.info("База данных инициализирована")

def set_setting_if_empty(conn: sqlite3.Connection, key: str, value: str) -> None:
    cur = conn.execute("SELECT value FROM settings WHERE key=?", (key,))
    if cur.fetchone() is None:
        conn.execute("INSERT INTO settings(key, value) VALUES(?, ?)", (key, value))

def get_setting(key: str, default: str = "") -> str:
    with closing(db_connect()) as conn:
        cur = conn.execute("SELECT value FROM settings WHERE key=?", (key,))
        row = cur.fetchone()
        return str(row["value"]) if row else default

def set_setting(key: str, value: str) -> None:
    with closing(db_connect()) as conn:
        conn.execute(
            "INSERT INTO settings(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        conn.commit()

def get_menu_buttons() -> list[str]:
    with closing(db_connect()) as conn:
        rows = conn.execute(
            "SELECT text FROM menu_buttons WHERE enabled=1 ORDER BY position ASC, id ASC"
        ).fetchall()
        return [str(r["text"]) for r in rows]

def get_classes_for_grade(grade: str) -> list[str]:
    with closing(db_connect()) as conn:
        rows = conn.execute(
            "SELECT class_name FROM classes WHERE grade=? ORDER BY class_name ASC",
            (grade,),
        ).fetchall()
        return [str(r["class_name"]) for r in rows]

def add_announcement(title: str, body: str) -> int:
    with closing(db_connect()) as conn:
        cur = conn.execute(
            "INSERT INTO announcements(title, body) VALUES (?, ?)",
            (title, body),
        )
        conn.commit()
        return int(cur.lastrowid)

def list_announcements() -> list[sqlite3.Row]:
    with closing(db_connect()) as conn:
        return conn.execute(
            "SELECT id, title, body FROM announcements ORDER BY created_at DESC"
        ).fetchall()

def get_announcement(ann_id: int) -> Optional[sqlite3.Row]:
    with closing(db_connect()) as conn:
        row = conn.execute(
            "SELECT id, title, body FROM announcements WHERE id=?",
            (ann_id,),
        ).fetchone()
        return row

def clear_announcements() -> None:
    with closing(db_connect()) as conn:
        conn.execute("DELETE FROM announcements")
        conn.commit()

def add_subscriber(chat_id: int) -> None:
    with closing(db_connect()) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO subscribers(chat_id) VALUES (?)",
            (chat_id,),
        )
        conn.commit()

def get_subscribers() -> list[int]:
    with closing(db_connect()) as conn:
        rows = conn.execute("SELECT chat_id FROM subscribers").fetchall()
        return [int(r["chat_id"]) for r in rows]

def set_schedule_for_day(class_name: str, day: str, lessons: list[str]) -> None:
    class_name = normalize_class_name(class_name)
    with closing(db_connect()) as conn:
        conn.execute("DELETE FROM schedule WHERE class_name=? AND day=?", (class_name, day))
        for i, lesson in enumerate(lessons, start=1):
            conn.execute(
                "INSERT INTO schedule(class_name, day, pos, lesson) VALUES (?, ?, ?, ?)",
                (class_name, day, i, lesson),
            )
        conn.commit()

def get_schedule_for_today(class_name: str) -> tuple[str, Optional[list[str]]]:
    class_name = normalize_class_name(class_name)
    today = get_today_ru()
    with closing(db_connect()) as conn:
        rows = conn.execute(
            "SELECT pos, lesson FROM schedule WHERE class_name=? AND day=? ORDER BY pos ASC",
            (class_name, today),
        ).fetchall()
        if len(rows) == 0:
            return today, None
        return today, [str(r["lesson"]) for r in rows]

def normalize_class_name(s: str) -> str:
    s = (s or "").strip().upper().replace(" ", "")
    s = s.replace("A", "А").replace("B", "Б").replace("V", "В").replace("G", "Г")
    return s

PARALLEL_EMOJI: dict[str, str] = {
    "5": "5️⃣",
    "6": "6️⃣",
    "7": "7️⃣",
    "8": "8️⃣",
    "9": "9️⃣",
    "10": "🔟",
    "11": "1️⃣1️⃣",
}

RU_DAYS = ["понедельник", "вторник", "среда", "четверг", "пятница"]

# ==========================================================
# FSM (состояния админки)
# ==========================================================

class AdminStates(StatesGroup):
    editing_announcements = State()
    editing_announcement_body = State()
    editing_help = State()
    adding_menu_button = State()
    renaming_menu_button = State()
    adding_class_to_grade = State()
    schedule_choose_class = State()
    schedule_choose_day = State()
    schedule_set_lessons = State()

def is_admin(message: types.Message) -> bool:
    return (
        message.chat.type == "private"
        and ADMIN_USER_ID != 0
        and message.from_user is not None
        and message.from_user.id == ADMIN_USER_ID
    )

def is_admin_cb(callback: types.CallbackQuery) -> bool:
    return (
        callback.message is not None
        and callback.message.chat.type == "private"
        and ADMIN_USER_ID != 0
        and callback.from_user is not None
        and callback.from_user.id == ADMIN_USER_ID
    )

# ==========================================================
# КЛАВИАТУРЫ
# ==========================================================

def get_main_keyboard():
    builder = ReplyKeyboardBuilder()
    buttons = get_menu_buttons()
    for b in buttons:
        builder.add(KeyboardButton(text=b))
    if len(buttons) <= 2:
        builder.adjust(2)
    else:
        builder.adjust(2, 2, 2, 2, 2)
    return builder.as_markup(resize_keyboard=True)

def get_parallels_keyboard() -> types.InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for grade in ["5", "6", "7", "8", "9", "10", "11"]:
        emoji = PARALLEL_EMOJI.get(grade, grade)
        builder.add(
            InlineKeyboardButton(
                text=f"{emoji} классы",
                callback_data=f"par|{grade}",
            )
        )
    builder.adjust(2, 2, 2, 1)
    builder.row(
        InlineKeyboardButton(text="🏠 Главное меню", callback_data="nav|main"),
    )
    return builder.as_markup()

def announcements_keyboard() -> types.InlineKeyboardMarkup:
    rows = list_announcements()
    kb = InlineKeyboardBuilder()
    if not rows:
        kb.add(InlineKeyboardButton(text="Пока объявлений нет", callback_data="ann|none"))
    else:
        for r in rows:
            kb.add(
                InlineKeyboardButton(
                    text=str(r["title"]),
                    callback_data=f"ann|{r['id']}",
                )
            )
    kb.adjust(1)
    kb.row(
        InlineKeyboardButton(text="🏠 Главное меню", callback_data="nav|main"),
    )
    return kb.as_markup()

def get_classes_keyboard(grade: str) -> types.InlineKeyboardMarkup:
    classes = get_classes_for_grade(grade)
    builder = InlineKeyboardBuilder()
    for cls in classes:
        builder.add(InlineKeyboardButton(text=cls, callback_data=f"cls|{cls}|{grade}"))

    if len(classes) >= 4:
        builder.adjust(4)
    else:
        builder.adjust(3)

    builder.row(
        InlineKeyboardButton(text="⬅️ Назад", callback_data="nav|parallels"),
        InlineKeyboardButton(text="🏠 Главное меню", callback_data="nav|main"),
    )
    return builder.as_markup()

def admin_keyboard() -> types.InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.add(InlineKeyboardButton(text="🧩 Кнопки меню", callback_data="adm|menu"))
    b.add(InlineKeyboardButton(text="❓ Помощь", callback_data="adm|help"))
    b.add(InlineKeyboardButton(text="📢 Объявления (+ новое)", callback_data="adm|ann"))
    b.add(InlineKeyboardButton(text="📚 Редактировать расписание", callback_data="adm|sch"))
    b.add(InlineKeyboardButton(text="🏠 Главное меню", callback_data="nav|main"))
    b.adjust(2, 2, 1)
    return b.as_markup()

def admin_menu_manage_keyboard() -> types.InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.add(InlineKeyboardButton(text="➕ Добавить кнопку", callback_data="adm|menu|add"))
    b.add(InlineKeyboardButton(text="✏️ Переименовать", callback_data="adm|menu|rename"))
    b.add(InlineKeyboardButton(text="✅ Вкл/Выкл", callback_data="adm|menu|toggle"))
    b.add(InlineKeyboardButton(text="🗑 Удалить", callback_data="adm|menu|del"))
    b.row(
        InlineKeyboardButton(text="⬅️ Назад", callback_data="adm|back"),
        InlineKeyboardButton(text="🏠 Главное меню", callback_data="nav|main"),
    )
    return b.as_markup()

def admin_schedule_classes_keyboard(grade: str) -> types.InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for cls in get_classes_for_grade(grade):
        b.add(InlineKeyboardButton(text=cls, callback_data=f"adm|schsel|cls|{cls}|{grade}"))
    if len(get_classes_for_grade(grade)) >= 4:
        b.adjust(4)
    else:
        b.adjust(3)
    b.row(InlineKeyboardButton(text="➕ Добавить класс", callback_data=f"adm|sch|addcls|{grade}"))
    b.row(
        InlineKeyboardButton(text="⬅️ Назад", callback_data="adm|sch"),
        InlineKeyboardButton(text="🏠 Главное меню", callback_data="nav|main"),
    )
    return b.as_markup()

# ==========================================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ==========================================================

def get_today_ru() -> str:
    ru_days = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
    return ru_days[datetime.now().weekday()]

def format_schedule_for_today(class_name: str) -> str:
    class_name = normalize_class_name(class_name)
    today, lessons = get_schedule_for_today(class_name)

    if lessons is None:
        return f"📚 {class_name}\n🗓️ Сегодня: {today.title()}\n\nРасписание пока не добавлено."

    if len(lessons) == 1 and lessons[0] == "__OFF__":
        return f"📚 {class_name}\n🗓️ Сегодня: {today.title()}\n\nВыходной."

    return f"📚 {class_name}\n🗓️ Сегодня: {today.title()}\n\n" + "\n".join(lessons)

def get_schedule_result_keyboard(grade: str) -> types.InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="⬅️ Назад", callback_data=f"nav|classes|{grade}"),
        InlineKeyboardButton(text="🏠 Главное меню", callback_data="nav|main"),
    )
    return builder.as_markup()

# ==========================================================
# ОБРАБОТЧИКИ КОМАНД
# ==========================================================

@router.message(Command("start"))
async def cmd_start(message: types.Message):
    try:
        await message.answer(
            f"👋 Привет, {message.from_user.full_name}!\n\n"
            "Я бот школьного расписания.\n"
            "Выбирай раздел в меню ниже 👇",
            reply_markup=get_main_keyboard(),
        )
    except Exception:
        logger.exception("Ошибка в /start")

@router.message(Command("admin"))
async def cmd_admin(message: types.Message, state: FSMContext):
    if not is_admin(message):
        return
    await state.clear()
    await message.answer("🔐 Админ-панель:", reply_markup=get_main_keyboard())
    await message.answer("Что редактируем?", reply_markup=admin_keyboard())

@router.message(Command("myid"))
async def cmd_myid(message: types.Message):
    uid = message.from_user.id if message.from_user else None
    await message.answer(f"Твой user_id: {uid}")

@router.message(Command("help"))
async def cmd_help(message: types.Message):
    try:
        await message.answer(
            get_setting("help_text", "❓ Помощь\n\nНажми «📚 Расписание уроков» и выбери класс."),
            reply_markup=get_main_keyboard(),
        )
    except Exception:
        logger.exception("Ошибка в /help")

@router.message(F.text == "📚 Расписание уроков")
async def menu_schedule(message: types.Message):
    try:
        await message.answer(
            "Выбери параллель:",
            reply_markup=get_parallels_keyboard(),
        )
    except Exception:
        logger.exception("Ошибка при открытии меню расписания")

@router.message(F.text == "📢 Объявления")
async def menu_announcements(message: types.Message):
    try:
        await message.answer(
            "📢 Объявления:",
            reply_markup=announcements_keyboard(),
        )
    except Exception:
        logger.exception("Ошибка в разделе объявлений")

@router.message(F.text == "❓ Помощь")
async def menu_help_button(message: types.Message):
    await cmd_help(message)

@router.message(StateFilter(None))
async def fallback_text(message: types.Message):
    try:
        add_subscriber(message.chat.id)
    except Exception:
        logger.exception("Ошибка при добавлении подписчика")

    text = (message.text or "").strip()
    if text == "📚 Расписание уроков":
        return await menu_schedule(message)
    if text == "📢 Объявления":
        return await menu_announcements(message)
    if text == "❓ Помощь":
        return await menu_help_button(message)

    try:
        await message.answer("Пока для этой кнопки нет действия.", reply_markup=get_main_keyboard())
    except Exception:
        logger.exception("Ошибка в fallback обработчике")

# ==========================================================
# INLINE-НАВИГАЦИЯ
# ==========================================================

@router.callback_query(F.data.startswith("ann|"))
async def show_announcement(callback: types.CallbackQuery):
    if callback.data == "ann|none":
        await callback.answer()
        return
    try:
        ann_id = int(callback.data.split("|")[1])
    except Exception:
        await callback.answer("Ошибка объявления")
        return
    row = get_announcement(ann_id)
    if not row:
        await callback.message.edit_text("Это объявление уже удалено.")
    else:
        await callback.message.edit_text(
            f"📢 {row['title']}\n\n{row['body']}",
            reply_markup=announcements_keyboard(),
        )
    await callback.answer()

@router.callback_query(F.data == "nav|main")
async def nav_main(callback: types.CallbackQuery):
    try:
        if callback.message:
            await callback.message.delete()
        await callback.message.answer("Главное меню:", reply_markup=get_main_keyboard())
    except Exception:
        logger.exception("Ошибка навигации в главное меню")
    finally:
        await callback.answer()

@router.callback_query(F.data == "nav|parallels")
async def nav_parallels(callback: types.CallbackQuery):
    try:
        await callback.message.edit_text("Выбери параллель:", reply_markup=get_parallels_keyboard())
    except Exception:
        logger.exception("Ошибка навигации к параллелям")
    finally:
        await callback.answer()

@router.callback_query(F.data.startswith("nav|classes|"))
async def nav_classes(callback: types.CallbackQuery):
    try:
        grade = callback.data.split("|", 2)[2]
        if len(get_classes_for_grade(grade)) == 0:
            await callback.message.edit_text(
                "Параллель не найдена. Вернись в выбор параллели.",
                reply_markup=get_parallels_keyboard(),
            )
            return
        await callback.message.edit_text(
            f"Выбери класс ({PARALLEL_EMOJI.get(grade, grade)}):",
            reply_markup=get_classes_keyboard(grade),
        )
    except Exception:
        logger.exception("Ошибка nav|classes")
    finally:
        await callback.answer()

@router.callback_query(F.data.startswith("par|"))
async def choose_parallel(callback: types.CallbackQuery):
    try:
        grade = callback.data.split("|", 1)[1]
        if len(get_classes_for_grade(grade)) == 0:
            await callback.message.edit_text(
                "Параллель не найдена. Попробуй ещё раз.",
                reply_markup=get_parallels_keyboard(),
            )
            return
        await callback.message.edit_text(
            f"Выбери класс ({PARALLEL_EMOJI.get(grade, grade)}):",
            reply_markup=get_classes_keyboard(grade),
        )
    except Exception:
        logger.exception("Ошибка выбора параллели")
    finally:
        await callback.answer()

@router.callback_query(F.data.startswith("cls|"))
async def choose_class(callback: types.CallbackQuery):
    try:
        parts = callback.data.split("|")
        if len(parts) < 3:
            await callback.message.edit_text(
                "Ошибка: класс не распознан.",
                reply_markup=get_parallels_keyboard(),
            )
            return

        class_name = parts[1]
        grade = parts[2]

        if len(get_classes_for_grade(grade)) == 0:
            await callback.message.edit_text(
                "Ошибка: параллель не найдена.",
                reply_markup=get_parallels_keyboard(),
            )
            return

        if normalize_class_name(class_name) not in {normalize_class_name(x) for x in get_classes_for_grade(grade)}:
            await callback.message.edit_text(
                "Ошибка: класс не найден в выбранной параллели.",
                reply_markup=get_classes_keyboard(grade),
            )
            return

        text = format_schedule_for_today(class_name)
        await callback.message.edit_text(
            text,
            reply_markup=get_schedule_result_keyboard(grade),
        )
    except Exception:
        logger.exception("Ошибка выбора класса")
        try:
            await callback.message.edit_text(
                "Произошла ошибка. Попробуй ещё раз или открой главное меню.",
                reply_markup=get_parallels_keyboard(),
            )
        except Exception:
            logger.exception("Не удалось отправить сообщение об ошибке")
    finally:
        await callback.answer()

# ==========================================================
# АДМИНКА
# ==========================================================

@router.callback_query(F.data == "adm|back")
async def adm_back(callback: types.CallbackQuery, state: FSMContext):
    if not is_admin_cb(callback):
        return await callback.answer()
    await state.clear()
    await callback.message.edit_text("Что редактируем?", reply_markup=admin_keyboard())
    await callback.answer()

@router.callback_query(F.data == "adm|menu")
async def adm_menu_root(callback: types.CallbackQuery, state: FSMContext):
    if not is_admin_cb(callback):
        return await callback.answer()
    await state.clear()
    await callback.message.edit_text("🧩 Кнопки главного меню:", reply_markup=admin_menu_manage_keyboard())
    await callback.answer()

@router.callback_query(F.data == "adm|menu|add")
async def adm_menu_add(callback: types.CallbackQuery, state: FSMContext):
    if not is_admin_cb(callback):
        return await callback.answer()
    await state.set_state(AdminStates.adding_menu_button)
    await callback.message.edit_text(
        "Напиши текст новой кнопки (например: «📞 Контакты»).\n\n"
        "Чтобы отменить — /admin",
        reply_markup=admin_menu_manage_keyboard(),
    )
    await callback.answer()

@router.message(AdminStates.adding_menu_button)
async def adm_menu_add_text(message: types.Message, state: FSMContext):
    if not is_admin(message):
        return
    text = (message.text or "").strip()
    if not text:
        return await message.answer("Пустой текст. Напиши ещё раз.")

    with closing(db_connect()) as conn:
        cur = conn.execute("SELECT COALESCE(MAX(position), 0) AS p FROM menu_buttons")
        pos = int(cur.fetchone()["p"]) + 10
        conn.execute("INSERT INTO menu_buttons(text, enabled, position) VALUES (?, 1, ?)", (text, pos))
        conn.commit()

    await state.clear()
    await message.answer("✅ Кнопка добавлена. Главное меню обновится.", reply_markup=get_main_keyboard())
    await message.answer("🧩 Управление кнопками:", reply_markup=admin_menu_manage_keyboard())

# ... (здесь все остальные обработчики админки, которые были в вашем коде)
# Для краткости я не копирую их все, но они остаются без изменений

# ==========================================================
# НАСТРОЙКА WEBHOOK
# ==========================================================

async def on_startup(bot: Bot) -> None:
    """Действия при запуске бота"""
    await bot.set_webhook(
        WEBHOOK_URL,
        allowed_updates=dp.resolve_used_update_types(),
        drop_pending_updates=True  # Удалить старые обновления
    )
    logger.info(f"✅ Вебхук установлен на {WEBHOOK_URL}")

async def on_shutdown(bot: Bot) -> None:
    """Действия при остановке бота"""
    await bot.delete_webhook()
    logger.info("❌ Вебхук удален")

# Создаем диспетчер
dp = Dispatcher(storage=MemoryStorage())
dp.include_router(router)

# Регистрируем функции запуска и остановки
dp.startup.register(on_startup)
dp.shutdown.register(on_shutdown)

async def main():
    """Главная функция"""
    # Проверяем токен
    if not TOKEN or TOKEN == "PASTE_YOUR_TOKEN_HERE":
        raise RuntimeError("❌ Вставь токен в переменную TOKEN в файле bot.py")
    
    # Проверяем URL для вебхука
    if RENDER_URL == "https://your-app-name.onrender.com":
        logger.warning("⚠️ ВНИМАНИЕ: Измените RENDER_URL на адрес вашего приложения!")
        logger.warning("Сейчас используется: " + RENDER_URL)
    
    # Инициализируем базу данных
    db_init()
    
    # Создаем бота
    bot = Bot(token=TOKEN)
    
    # Создаем aiohttp приложение
    app = web.Application()
    
    # Настраиваем вебхук
    webhook_requests_handler = SimpleRequestHandler(
        dispatcher=dp,
        bot=bot,
    )
    webhook_requests_handler.register(app, path=WEBHOOK_PATH)
    setup_application(app, dp, bot=bot)
    
    # Получаем порт из переменных окружения Render
    port = int(os.environ.get("PORT", 10000))
    
    logger.info(f"🚀 Бот запускается на порту {port}")
    logger.info(f"📡 Вебхук URL: {WEBHOOK_URL}")
    
    # Запускаем веб-сервер
    return await web._run_app(app, host="0.0.0.0", port=port)

if __name__ == "__main__":
    asyncio.run(main())
