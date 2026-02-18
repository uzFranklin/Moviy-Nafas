# moviy_nafas_bot.py
# Telegram bot for eco-volunteering project (Moviy Nafas)
# Python: 3.10+
# Library: python-telegram-bot v20+
#
# Install:
#   pip install python-telegram-bot==20.7
#
# Run:
#   set BOT_TOKEN=xxxxx   (Windows PowerShell: $env:BOT_TOKEN="xxxxx")
#   python moviy_nafas_bot.py

import logging
import os
import sqlite3
from typing import List, Optional

from telegram import (
    Update,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    ReplyKeyboardMarkup,
    KeyboardButton,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    ConversationHandler,
    filters,
)

# =========================
# CONFIG
# =========================
TOKEN = os.getenv("8537624496:AAG8K3YcYlBw1GWT36Xuv_0TBS3aHRzzwAU", "").strip()  # обязательно выстави переменную окружения BOT_TOKEN
DB_PATH = "db.db"

# Супер-админ (может сбросить БД и назначать региональных админов)
SUPERADMIN_IDS = [7916861272]  # <-- замени на свой ID(шники)

# Награды
MEETUP_REWARD = 100
TRASH_REWARD = 50  # монеты за одобренную заявку о мусоре (локация+фото+комментарий)

# =========================
# LOGGING
# =========================
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# =========================
# DB
# =========================
def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_schema():
    conn = db()
    cur = conn.cursor()

    # USERS (фото при регистрации УДАЛЕНО)
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER UNIQUE NOT NULL,
            full_name TEXT NOT NULL,
            age INTEGER,
            study_place TEXT,
            region TEXT NOT NULL,
            district TEXT,
            role TEXT NOT NULL DEFAULT 'volunteer', -- volunteer/admin/superadmin
            coins INTEGER NOT NULL DEFAULT 0,
            language TEXT NOT NULL DEFAULT 'ru',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        """
    )

    # региональные админы: admin_tid -> region
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS admin_regions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            admin_tid INTEGER NOT NULL,
            region TEXT NOT NULL,
            UNIQUE(admin_tid, region)
        );
        """
    )

    # TASKS: строго по региону, не пересекаются
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            region TEXT NOT NULL,
            created_by INTEGER,
            title TEXT NOT NULL,
            description TEXT,
            coin_reward INTEGER NOT NULL,
            level TEXT CHECK(level IN ('easy','medium','hard')) NOT NULL,
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        """
    )

    # SUBMISSIONS: статусы (pending/rejected/approved)
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS submissions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            task_id INTEGER NOT NULL,
            proof_file_id TEXT NOT NULL,
            comment TEXT,
            status TEXT CHECK(status IN ('pending','approved','rejected')) NOT NULL DEFAULT 'pending',
            coins_awarded INTEGER NOT NULL DEFAULT 0,
            submitted_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(id),
            FOREIGN KEY(task_id) REFERENCES tasks(id)
        );
        """
    )

    # MEETUPS
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS meetups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            description TEXT,
            location TEXT NOT NULL,
            start_at TEXT NOT NULL,
            photo_file_id TEXT,
            max_participants INTEGER
        );
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS meetup_registrations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            meetup_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            registered_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(meetup_id, user_id),
            FOREIGN KEY(meetup_id) REFERENCES meetups(id),
            FOREIGN KEY(user_id) REFERENCES users(id)
        );
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS meetup_submissions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            meetup_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            group_photo_file_id TEXT,
            selfie_file_id TEXT,
            status TEXT CHECK(status IN ('pending','approved','rejected')) NOT NULL DEFAULT 'pending',
            coins_awarded INTEGER NOT NULL DEFAULT 0,
            submitted_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(meetup_id) REFERENCES meetups(id),
            FOREIGN KEY(user_id) REFERENCES users(id)
        );
        """
    )

    # REWARDS exchange log
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS rewards (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            reward_name TEXT NOT NULL,
            coin_cost INTEGER NOT NULL,
            exchange_date TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(id)
        );
        """
    )

    # TRASH REPORTS (локация+фото+коммент) -> модерация админом области
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS trash_points (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            region TEXT NOT NULL,
            lat REAL NOT NULL,
            lon REAL NOT NULL,
            photo_file_id TEXT NOT NULL,
            note TEXT,
            status TEXT CHECK(status IN ('pending','approved','rejected')) NOT NULL DEFAULT 'pending',
            coins_awarded INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(id)
        );
        """
    )

    conn.commit()
    conn.close()


def reset_database_file():
    # Полный сброс (пользователи/задания/заявки) — чтобы все перерегистрировались
    try:
        if os.path.exists(DB_PATH):
            os.remove(DB_PATH)
    except Exception:
        # если файл занят — просто очистим таблицы
        conn = db()
        cur = conn.cursor()
        for t in ["submissions", "meetup_submissions", "meetup_registrations", "meetups", "rewards", "trash_points", "tasks", "admin_regions", "users"]:
            try:
                cur.execute(f"DELETE FROM {t};")
            except Exception:
                pass
        conn.commit()
        conn.close()

    ensure_schema()


def is_superadmin(user_id: int) -> bool:
    return user_id in SUPERADMIN_IDS


def get_user_row_by_tid(user_tid: int) -> Optional[sqlite3.Row]:
    conn = db()
    row = conn.execute("SELECT * FROM users WHERE telegram_id=?", (user_tid,)).fetchone()
    conn.close()
    return row


def get_lang_from_db(user_tid: int) -> str:
    try:
        conn = db()
        row = conn.execute("SELECT language FROM users WHERE telegram_id=?", (user_tid,)).fetchone()
        conn.close()
        if row and row["language"] in ("ru", "uz"):
            return row["language"]
    except Exception:
        pass
    return "ru"


def gl(update: Update, context: ContextTypes.DEFAULT_TYPE) -> str:
    lang = context.user_data.get("language")
    if lang in ("ru", "uz"):
        return lang
    if update.effective_user:
        lang = get_lang_from_db(update.effective_user.id)
        context.user_data["language"] = lang
        return lang
    return "ru"


def set_user_role(user_tid: int, role: str):
    conn = db()
    conn.execute("UPDATE users SET role=? WHERE telegram_id=?", (role, user_tid))
    conn.commit()
    conn.close()


def assign_admin_region(admin_tid: int, region: str):
    conn = db()
    conn.execute("INSERT OR IGNORE INTO admin_regions (admin_tid, region) VALUES (?, ?)", (admin_tid, region))
    conn.commit()
    conn.close()


def get_admin_regions(user_tid: int) -> List[str]:
    # супер-админ может модерировать всё
    if is_superadmin(user_tid):
        return REGIONS_RU[:]
    conn = db()
    rows = conn.execute("SELECT region FROM admin_regions WHERE admin_tid=? ORDER BY region", (user_tid,)).fetchall()
    conn.close()
    return [r["region"] for r in rows]


def is_region_admin(user_tid: int) -> bool:
    if is_superadmin(user_tid):
        return True
    u = get_user_row_by_tid(user_tid)
    if not u or u["role"] != "admin":
        return False
    conn = db()
    r = conn.execute("SELECT 1 FROM admin_regions WHERE admin_tid=? LIMIT 1", (user_tid,)).fetchone()
    conn.close()
    return bool(r)


# =========================
# DATA (Regions/Districts)
# =========================
REGIONS_RU = [
    "Республика Каракалпакстан",
    "Андижанская обл",
    "Бухарская обл",
    "Джизакская обл",
    "Кашкадарьинская обл",
    "Навоийская обл",
    "Наманганская обл",
    "Самаркандская обл",
    "Сурхандарьинская обл",
    "Сырдарьинская обл",
    "Ташкентская обл",
    "Ферганская обл",
    "Хорезмская обл",
    "г. Ташкент",
]
REGIONS_UZ = [
    "Qoraqalpogʻiston Respublikasi",
    "Andijon viloyati",
    "Buxoro viloyati",
    "Jizzax viloyati",
    "Qashqadaryo viloyati",
    "Navoiy viloyati",
    "Namangan viloyati",
    "Samarqand viloyati",
    "Surxondaryo viloyati",
    "Sirdaryo viloyati",
    "Toshkent viloyati",
    "Fargʻona viloyati",
    "Xorazm viloyati",
    "Toshkent shahri",
]
TASHKENT_DISTRICTS_RU = [
    "Алмазарский", "Бектемирский", "Мирзо-Улугбекский", "Мирабадский",
    "Сергелийский", "Учтепинский", "Чиланзарский", "Шайхонтохурский",
    "Юнусабадский", "Яккасарайский", "Яшнабадский", "Янгихаётский",
]
TASHKENT_DISTRICTS_UZ = [
    "Olmazor tumani", "Bektemir tumani", "Mirzo Ulugʻbek tumani", "Mirobod tumani",
    "Sirgʻali tumani", "Uchtepa tumani", "Chilonzor tumani", "Shayxontohur tumani",
    "Yunusobod tumani", "Yakkasaroy tumani", "Yashnobod tumani", "Yangihayot tumani",
]

# Rewards catalog (пример)
REWARDS = [
    {"id": 1, "cost": 200, "name_ru": "Экобрелок или браслет", "name_uz": "Eko-brelok yoki bilaguzuk"},
    {"id": 2, "cost": 400, "name_ru": "Термос или футболка", "name_uz": "Termos yoki futbolka"},
    {"id": 3, "cost": 700, "name_ru": "Эко-набор (сумка, бутылка, блокнот)", "name_uz": "Eko-toʻplam (sumka, butilka, daftar)"},
    {"id": 4, "cost": 1000, "name_ru": "Сертификат «Эко-элчи»", "name_uz": "«Eko-elchi» sertifikati"},
]

# =========================
# UI
# =========================
def main_kb(lang: str) -> ReplyKeyboardMarkup:
    if lang == "ru":
        rows = [
            ["👤 Мой профиль", "🌿 О проекте"],
            ["🎯 Задания", "📅 Офлайн встречи"],
            ["💰 Мой баланс", "🏆 Лидеры"],
            ["🗑 Сообщить о мусоре", "🎁 Обмен баллов"],
        ]
    else:
        rows = [
            ["👤 Profilim", "🌿 Loyiha haqida"],
            ["🎯 Topshiriqlar", "📅 Uchrashuvlar"],
            ["💰 Balans", "🏆 Reyting"],
            ["🗑 Axlat haqida xabar", "🎁 Sovg‘alar"],
        ]
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


def t(lang: str, ru: str, uz: str) -> str:
    return ru if lang == "ru" else uz


# =========================
# CANCEL
# =========================
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = gl(update, context)
    if update.message:
        await update.message.reply_text("❌ Отмена.", reply_markup=main_kb(lang))
    elif update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.message.reply_text("❌ Отмена.", reply_markup=main_kb(lang))
    return ConversationHandler.END


# =========================
# REGISTRATION (ФОТО УБРАНО)
# =========================
LANG_CHOICE, REG_NAME, REG_AGE, REG_STUDY, REG_REGION, REG_DISTRICT, REG_CONSENT = range(100, 107)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = get_user_row_by_tid(update.effective_user.id)
    if u:
        context.user_data["language"] = u["language"]
        await update.message.reply_text("✅ Вы уже зарегистрированы.", reply_markup=main_kb(gl(update, context)))
        return ConversationHandler.END

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🇷🇺 Русский", callback_data="lang_ru")],
        [InlineKeyboardButton("🇺🇿 O‘zbekcha", callback_data="lang_uz")],
    ])
    await update.message.reply_text("🌍 Выберите язык / Tilni tanlang:", reply_markup=kb)
    return LANG_CHOICE


async def set_language(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    lang = q.data.replace("lang_", "")
    context.user_data["language"] = lang
    await q.message.reply_text(t(lang, "Введите ФИО:", "F.I.Sh kiriting:"))
    return REG_NAME


async def reg_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["full_name"] = (update.message.text or "").strip()
    lang = gl(update, context)
    await update.message.reply_text(t(lang, "Введите возраст:", "Yoshingizni kiriting:"))
    return REG_AGE


async def reg_age(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # допустим любые цифры/текст, как раньше
    context.user_data["age"] = (update.message.text or "").strip()
    lang = gl(update, context)
    await update.message.reply_text(t(lang, "Введите место учёбы:", "O‘qish joyingizni kiriting:"))
    return REG_STUDY


async def reg_study(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["study_place"] = (update.message.text or "").strip()
    lang = gl(update, context)

    regions = REGIONS_RU if lang == "ru" else REGIONS_UZ
    buttons = [[InlineKeyboardButton(r, callback_data=f"reg_region|{r}")] for r in regions]
    await update.message.reply_text(t(lang, "Выберите область:", "Viloyatni tanlang:"), reply_markup=InlineKeyboardMarkup(buttons))
    return REG_REGION


async def reg_region(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    region = q.data.split("|", 1)[1]
    context.user_data["region"] = region
    lang = gl(update, context)

    if region in ("г. Ташкент", "Toshkent shahri"):
        districts = TASHKENT_DISTRICTS_RU if lang == "ru" else TASHKENT_DISTRICTS_UZ
        buttons = [[InlineKeyboardButton(d, callback_data=f"reg_dist|{d}")] for d in districts]
        await q.message.reply_text(t(lang, "Выберите район г. Ташкент:", "Toshkent tumani:"), reply_markup=InlineKeyboardMarkup(buttons))
        return REG_DISTRICT

    await q.message.reply_text(t(lang, "Введите ваш район:", "Tumaningizni kiriting:"))
    return REG_DISTRICT


async def reg_district(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = gl(update, context)
    if update.callback_query:
        q = update.callback_query
        await q.answer()
        district = q.data.split("|", 1)[1]
        context.user_data["district"] = district
        target = q.message
    else:
        context.user_data["district"] = (update.message.text or "").strip()
        target = update.message

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(t(lang, "✅ Согласен", "✅ Roziman"), callback_data="consent_yes")],
        [InlineKeyboardButton(t(lang, "❌ Не согласен", "❌ Rozi emasman"), callback_data="consent_no")],
    ])
    await target.reply_text(
        t(
            lang,
            "📌 Мы собираем ваши данные только для целей проекта.\nОни не передаются третьим лицам.\n\nВы согласны?",
            "📌 Maʼlumotlaringiz faqat loyiha uchun.\nUchinchi shaxslarga berilmaydi.\n\nRozimisiz?",
        ),
        reply_markup=kb,
    )
    return REG_CONSENT


async def reg_consent(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    lang = gl(update, context)
    if q.data == "consent_no":
        await q.message.reply_text(t(lang, "❌ Регистрация отменена.", "❌ Ro‘yxatdan o‘tish bekor qilindi."))
        return ConversationHandler.END

    user_tid = q.from_user.id

    conn = db()
    conn.execute(
        """
        INSERT INTO users (telegram_id, full_name, age, study_place, region, district, role, coins, language)
        VALUES (?, ?, ?, ?, ?, ?, 'volunteer', 0, ?)
        """,
        (
            user_tid,
            context.user_data["full_name"],
            context.user_data["age"],
            context.user_data["study_place"],
            context.user_data["region"],
            context.user_data["district"],
            lang,
        ),
    )
    conn.commit()
    conn.close()

    if is_superadmin(user_tid):
        set_user_role(user_tid, "superadmin")

    await q.message.reply_text(
        t(lang, "🎉 Регистрация завершена!", "🎉 Ro‘yxatdan o‘tish yakunlandi!"),
        reply_markup=main_kb(lang),
    )
    return ConversationHandler.END


# =========================
# PROFILE / BALANCE / ABOUT
# =========================
async def profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = gl(update, context)
    u = get_user_row_by_tid(update.effective_user.id)
    if not u:
        await update.message.reply_text(t(lang, "❌ Сначала /start", "❌ Avval /start"), reply_markup=main_kb(lang))
        return

    text = (
        f"👤 {u['full_name']}\n"
        f"{t(lang,'Возраст','Yosh')}: {u['age']}\n"
        f"{t(lang,'Учёба','O‘qish')}: {u['study_place']}\n"
        f"{t(lang,'Регион','Viloyat')}: {u['region']}\n"
        f"{t(lang,'Район','Tuman')}: {u['district']}\n"
        f"💰 {t(lang,'Баланс','Balans')}: {u['coins']}⚡\n"
        f"🛡 {t(lang,'Роль','Rol')}: {u['role']}"
    )
    await update.message.reply_text(text, reply_markup=main_kb(lang))


async def balance_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = gl(update, context)
    u = get_user_row_by_tid(update.effective_user.id)
    if not u:
        await update.message.reply_text(t(lang, "❌ Сначала /start", "❌ Avval /start"), reply_markup=main_kb(lang))
        return
    await update.message.reply_text(f"💰 {t(lang,'Баланс','Balans')}: {u['coins']}⚡", reply_markup=main_kb(lang))


async def about_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = gl(update, context)
    await update.message.reply_text(
        t(
            lang,
            "🌿 Проект: волонтёры очищают город, выполняют задания и получают монеты за вклад.",
            "🌿 Loyiha: volontyorlar shaharni tozalaydi, topshiriqlar bajaradi va ball oladi.",
        ),
        reply_markup=main_kb(lang),
    )


# =========================
# TASKS (по области + статус)
# =========================
TASK_LEVEL, TASK_LIST, TASK_SUBMIT = range(200, 203)


def status_label(lang: str, status: Optional[str]) -> str:
    if status == "pending":
        return t(lang, " ⏳(на проверке)", " ⏳(tekshiruvda)")
    if status == "approved":
        return t(lang, " ✅(принято)", " ✅(tasdiq)")
    if status == "rejected":
        return t(lang, " ❌(отклонено)", " ❌(rad)")
    return ""


async def tasks_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = gl(update, context)
    u = get_user_row_by_tid(update.effective_user.id)
    if not u:
        await update.message.reply_text(t(lang, "❌ Сначала /start", "❌ Avval /start"), reply_markup=main_kb(lang))
        return ConversationHandler.END

    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("🟢 Easy", callback_data="level_easy"),
        InlineKeyboardButton("🟡 Medium", callback_data="level_medium"),
        InlineKeyboardButton("🔴 Hard", callback_data="level_hard"),
    ]])
    await update.message.reply_text(t(lang, "Выберите уровень:", "Darajani tanlang:"), reply_markup=kb)
    return TASK_LEVEL


async def tasks_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    lang = gl(update, context)
    level = q.data.split("_", 1)[1]

    conn = db()
    u = conn.execute("SELECT id, region FROM users WHERE telegram_id=?", (q.from_user.id,)).fetchone()
    if not u:
        conn.close()
        await q.message.reply_text(t(lang, "❌ Сначала /start", "❌ Avval /start"))
        return ConversationHandler.END

    rows = conn.execute(
        """
        SELECT 
            t.id, t.title, t.coin_reward, t.is_active,
            (
                SELECT s.status
                FROM submissions s
                WHERE s.user_id=? AND s.task_id=t.id
                ORDER BY s.submitted_at DESC, s.id DESC
                LIMIT 1
            ) AS my_status
        FROM tasks t
        WHERE t.level=? AND t.region=? AND t.is_active=1
        ORDER BY t.id
        """,
        (u["id"], level, u["region"]),
    ).fetchall()
    conn.close()

    if not rows:
        await q.message.reply_text(t(lang, "📭 Нет заданий в вашей области.", "📭 Viloyatingizda topshiriq yo‘q."))
        return ConversationHandler.END

    buttons = []
    for r in rows:
        label = f"{r['title']} (+{r['coin_reward']}⚡){status_label(lang, r['my_status'])}"
        buttons.append([InlineKeyboardButton(label, callback_data=f"task_submit|{r['id']}")])

    await q.message.reply_text(t(lang, "Выберите задание:", "Topshiriqni tanlang:"), reply_markup=InlineKeyboardMarkup(buttons))
    return TASK_LIST


async def tasks_choose(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    lang = gl(update, context)
    task_id = int(q.data.split("|", 1)[1])
    context.user_data["submit_task_id"] = task_id
    await q.message.reply_text(t(lang, "📸 Отправьте фото выполнения:", "📸 Bajarilganini rasm bilan yuboring:"))
    return TASK_SUBMIT


async def tasks_submit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = gl(update, context)
    if not update.message.photo:
        await update.message.reply_text(t(lang, "Нужно фото.", "Rasm kerak."))
        return TASK_SUBMIT

    photo_id = update.message.photo[-1].file_id
    task_id = context.user_data.get("submit_task_id")
    if not task_id:
        await update.message.reply_text("❌ Ошибка. Откройте /tasks заново.")
        return ConversationHandler.END

    conn = db()
    u = conn.execute("SELECT id, region FROM users WHERE telegram_id=?", (update.effective_user.id,)).fetchone()
    if not u:
        conn.close()
        await update.message.reply_text(t(lang, "❌ Сначала /start", "❌ Avval /start"))
        return ConversationHandler.END

    # защита: нельзя отправить задание другой области
    trow = conn.execute("SELECT id FROM tasks WHERE id=? AND region=?", (task_id, u["region"])).fetchone()
    if not trow:
        conn.close()
        await update.message.reply_text(t(lang, "❌ Это задание недоступно.", "❌ Bu topshiriq mavjud emas."))
        return ConversationHandler.END

    existing = conn.execute(
        """
        SELECT id FROM submissions
        WHERE user_id=? AND task_id=?
        ORDER BY submitted_at DESC, id DESC
        LIMIT 1
        """,
        (u["id"], task_id),
    ).fetchone()

    if existing:
        conn.execute(
            """
            UPDATE submissions
            SET proof_file_id=?, status='pending', coins_awarded=0, submitted_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (photo_id, existing["id"]),
        )
    else:
        conn.execute(
            "INSERT INTO submissions (user_id, task_id, proof_file_id, status) VALUES (?, ?, ?, 'pending')",
            (u["id"], task_id, photo_id),
        )

    conn.commit()
    conn.close()

    await update.message.reply_text(t(lang, "✅ Отправлено на проверку.", "✅ Tekshiruvga yuborildi."), reply_markup=main_kb(lang))
    return ConversationHandler.END


# =========================
# ADMIN: submissions review (по своей области)
# =========================
async def submissions_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = gl(update, context)
    if not is_region_admin(update.effective_user.id):
        await update.message.reply_text(t(lang, "❌ Нет прав админа.", "❌ Admin huquqi yo‘q."))
        return

    regions = get_admin_regions(update.effective_user.id)
    if not regions:
        await update.message.reply_text("❌ Вам не назначена область.")
        return

    placeholders = ",".join(["?"] * len(regions))
    conn = db()
    rows = conn.execute(
        f"""
        SELECT s.id, s.proof_file_id, u.full_name, u.telegram_id, t.title, t.coin_reward, t.region
        FROM submissions s
        JOIN users u ON u.id = s.user_id
        JOIN tasks t ON t.id = s.task_id
        WHERE s.status='pending' AND t.region IN ({placeholders})
        ORDER BY s.submitted_at ASC, s.id ASC
        """,
        tuple(regions),
    ).fetchall()
    conn.close()

    if not rows:
        await update.message.reply_text(t(lang, "📭 Нет заданий на проверке.", "📭 Tekshiruvda topshiriq yo‘q."))
        return

    for r in rows:
        caption = f"🏷 {r['region']}\n👤 {r['full_name']}\n📌 {r['title']}\n💰 +{r['coin_reward']}⚡"
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Принять", callback_data=f"sub_ok|{r['id']}"),
            InlineKeyboardButton("❌ Отклонить", callback_data=f"sub_no|{r['id']}"),
        ]])
        try:
            await update.message.reply_photo(photo=r["proof_file_id"], caption=caption, reply_markup=kb)
        except Exception:
            await update.message.reply_text(caption, reply_markup=kb)


async def submission_review(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    lang = gl(update, context)

    if not is_region_admin(q.from_user.id):
        await q.message.reply_text(t(lang, "❌ Нет прав админа.", "❌ Admin huquqi yo‘q."))
        return

    action, sid_str = q.data.split("|", 1)
    sid = int(sid_str)
    ok = action == "sub_ok"

    regions = get_admin_regions(q.from_user.id)
    placeholders = ",".join(["?"] * len(regions))

    conn = db()
    # проверяем, что заявка из области админа
    check = conn.execute(
        f"""
        SELECT s.id, s.user_id, u.telegram_id, t.title, t.coin_reward
        FROM submissions s
        JOIN tasks t ON t.id = s.task_id
        JOIN users u ON u.id = s.user_id
        WHERE s.id=? AND t.region IN ({placeholders})
        """,
        (sid, *regions),
    ).fetchone()

    if not check:
        conn.close()
        await q.message.reply_text(t(lang, "❌ Не ваша область.", "❌ Sizning viloyatingiz emas."))
        return

    if ok:
        conn.execute("UPDATE users SET coins=coins+? WHERE id=?", (check["coin_reward"], check["user_id"]))
        conn.execute("UPDATE submissions SET status='approved', coins_awarded=? WHERE id=?", (check["coin_reward"], sid))
        conn.commit()
        conn.close()
        try:
            await context.bot.send_message(check["telegram_id"], f"✅ «{check['title']}» — +{check['coin_reward']}⚡")
        except Exception:
            pass
        await q.message.reply_text("✅ Принято.")
    else:
        conn.execute("UPDATE submissions SET status='rejected' WHERE id=?", (sid,))
        conn.commit()
        conn.close()
        try:
            await context.bot.send_message(check["telegram_id"], f"❌ «{check['title']}» — отклонено")
        except Exception:
            pass
        await q.message.reply_text("❌ Отклонено.")


# =========================
# TRASH REPORT (location + photo + comment) + coins after approval
# =========================
TR_LOC, TR_PHOTO, TR_NOTE = range(700, 703)


async def trash_report_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = gl(update, context)
    u = get_user_row_by_tid(update.effective_user.id)
    if not u:
        await update.message.reply_text(t(lang, "❌ Сначала /start", "❌ Avval /start"))
        return ConversationHandler.END

    kb = ReplyKeyboardMarkup([[KeyboardButton(t(lang, "📍 Отправить локацию", "📍 Lokatsiya yuborish"), request_location=True)]],
                            resize_keyboard=True)
    await update.message.reply_text(t(lang, "1) Отправьте локацию:", "1) Lokatsiyani yuboring:"), reply_markup=kb)
    return TR_LOC


async def trash_report_loc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = gl(update, context)
    if not update.message.location:
        await update.message.reply_text(t(lang, "Нужно отправить локацию кнопкой.", "Lokatsiyani tugma orqali yuboring."))
        return TR_LOC

    context.user_data["tr_lat"] = float(update.message.location.latitude)
    context.user_data["tr_lon"] = float(update.message.location.longitude)
    await update.message.reply_text(t(lang, "2) Теперь отправьте фото этого места.", "2) Endi shu joyning rasmini yuboring."),
    return TR_PHOTO


async def trash_report_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = gl(update, context)
    if not update.message.photo:
        await update.message.reply_text(t(lang, "Нужно фото.", "Rasm kerak."))
        return TR_PHOTO
    context.user_data["tr_photo"] = update.message.photo[-1].file_id
    await update.message.reply_text(t(lang, "3) Напишите комментарий.", "3) Izoh yozing."))
    return TR_NOTE


async def trash_report_note(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = gl(update, context)
    note = (update.message.text or "").strip()

    u = get_user_row_by_tid(update.effective_user.id)
    if not u:
        await update.message.reply_text(t(lang, "❌ Сначала /start", "❌ Avval /start"))
        return ConversationHandler.END

    lat = context.user_data.get("tr_lat")
    lon = context.user_data.get("tr_lon")
    photo = context.user_data.get("tr_photo")
    if lat is None or lon is None or not photo:
        await update.message.reply_text("❌ Ошибка. Попробуйте снова /trash_report")
        return ConversationHandler.END

    conn = db()
    conn.execute(
        """
        INSERT INTO trash_points (user_id, region, lat, lon, photo_file_id, note, status, coins_awarded)
        VALUES (?, ?, ?, ?, ?, ?, 'pending', 0)
        """,
        (u["id"], u["region"], lat, lon, photo, note),
    )
    conn.commit()
    conn.close()

    context.user_data.pop("tr_lat", None)
    context.user_data.pop("tr_lon", None)
    context.user_data.pop("tr_photo", None)

    await update.message.reply_text(t(lang, "✅ Отправлено на проверку администратору.", "✅ Admin tekshiruviga yuborildi."),
                                    reply_markup=main_kb(lang))
    return ConversationHandler.END


async def trash_points_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = gl(update, context)
    if not is_region_admin(update.effective_user.id):
        await update.message.reply_text(t(lang, "❌ Нет прав админа.", "❌ Admin huquqi yo‘q."))
        return

    regions = get_admin_regions(update.effective_user.id)
    placeholders = ",".join(["?"] * len(regions))

    conn = db()
    rows = conn.execute(
        f"""
        SELECT tp.id, tp.lat, tp.lon, tp.photo_file_id, tp.note, tp.region, u.telegram_id, u.full_name
        FROM trash_points tp
        JOIN users u ON u.id = tp.user_id
        WHERE tp.status='pending' AND tp.region IN ({placeholders})
        ORDER BY tp.created_at ASC, tp.id ASC
        """,
        tuple(regions),
    ).fetchall()
    conn.close()

    if not rows:
        await update.message.reply_text(t(lang, "📭 Нет заявок о мусоре.", "📭 Axlat so‘rovlari yo‘q."))
        return

    for r in rows:
        caption = (
            f"🏷 {r['region']}\n"
            f"🗑 Заявка #{r['id']}\n"
            f"👤 {r['full_name']}\n"
            f"📍 {r['lat']}, {r['lon']}\n"
            f"📝 {r['note'] or ''}\n"
            f"💰 Награда при одобрении: +{TRASH_REWARD}⚡"
        )
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Принять", callback_data=f"tp_ok|{r['id']}"),
            InlineKeyboardButton("❌ Отклонить", callback_data=f"tp_no|{r['id']}"),
            InlineKeyboardButton("📍 Maps", url=f"https://maps.google.com/?q={r['lat']},{r['lon']}"),
        ]])
        try:
            await update.message.reply_photo(photo=r["photo_file_id"], caption=caption, reply_markup=kb)
        except Exception:
            await update.message.reply_text(caption, reply_markup=kb)


async def trash_point_review(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    lang = gl(update, context)

    if not is_region_admin(q.from_user.id):
        await q.message.reply_text(t(lang, "❌ Нет прав админа.", "❌ Admin huquqi yo‘q."))
        return

    action, pid_str = q.data.split("|", 1)
    pid = int(pid_str)
    ok = action == "tp_ok"

    regions = get_admin_regions(q.from_user.id)
    placeholders = ",".join(["?"] * len(regions))

    conn = db()
    row = conn.execute(
        f"""
        SELECT tp.id, tp.user_id, tp.region, u.telegram_id
        FROM trash_points tp
        JOIN users u ON u.id = tp.user_id
        WHERE tp.id=? AND tp.region IN ({placeholders})
        """,
        (pid, *regions),
    ).fetchone()

    if not row:
        conn.close()
        await q.message.reply_text(t(lang, "❌ Не ваша область.", "❌ Sizning viloyatingiz emas."))
        return

    if ok:
        conn.execute("UPDATE users SET coins=coins+? WHERE id=?", (TRASH_REWARD, row["user_id"]))
        conn.execute("UPDATE trash_points SET status='approved', coins_awarded=? WHERE id=?", (TRASH_REWARD, pid))
        conn.commit()
        conn.close()
        try:
            await context.bot.send_message(row["telegram_id"], f"✅ Ваша заявка о мусоре принята — +{TRASH_REWARD}⚡")
        except Exception:
            pass
        await q.message.reply_text("✅ Принято.")
    else:
        conn.execute("UPDATE trash_points SET status='rejected' WHERE id=?", (pid,))
        conn.commit()
        conn.close()
        try:
            await context.bot.send_message(row["telegram_id"], "❌ Ваша заявка о мусоре отклонена.")
        except Exception:
            pass
        await q.message.reply_text("❌ Отклонено.")


# =========================
# ADMIN TASKS (создание задач по области)
# =========================
ADM_PICK_REGION, ADM_LEVEL, ADM_LIST, ADM_NEW_TITLE, ADM_NEW_DESC, ADM_NEW_REWARD, ADM_EDIT_FIELD, ADM_EDIT_VALUE = range(500, 508)


async def admin_tasks_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = gl(update, context)
    if not is_region_admin(update.effective_user.id):
        await update.message.reply_text(t(lang, "❌ Нет прав админа.", "❌ Admin huquqi yo‘q."))
        return ConversationHandler.END

    regions = get_admin_regions(update.effective_user.id)
    if len(regions) == 1:
        context.user_data["adm_region"] = regions[0]
        return await admin_pick_level_prompt(update, context)

    kb = InlineKeyboardMarkup([[InlineKeyboardButton(r, callback_data=f"adm_reg|{r}")] for r in regions])
    await update.message.reply_text("Выберите область:", reply_markup=kb)
    return ADM_PICK_REGION


async def admin_pick_region(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    context.user_data["adm_region"] = q.data.split("|", 1)[1]
    return await admin_pick_level_prompt(update, context)


async def admin_pick_level_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = gl(update, context)
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("🟢 Easy", callback_data="adm_level_easy"),
        InlineKeyboardButton("🟡 Medium", callback_data="adm_level_medium"),
        InlineKeyboardButton("🔴 Hard", callback_data="adm_level_hard"),
    ]])
    msg = update.callback_query.message if update.callback_query else update.message
    await msg.reply_text(t(lang, "Выберите уровень:", "Darajani tanlang:"), reply_markup=kb)
    return ADM_LEVEL


async def admin_pick_level(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    lang = gl(update, context)
    level = q.data.split("_", 2)[2]
    context.user_data["adm_level"] = level
    region = context.user_data.get("adm_region")

    conn = db()
    rows = conn.execute(
        "SELECT id, title, coin_reward, is_active FROM tasks WHERE level=? AND region=? ORDER BY id",
        (level, region),
    ).fetchall()
    conn.close()

    btns = []
    for r in rows:
        prefix = "🟢" if r["is_active"] == 1 else "🔴"
        btns.append([InlineKeyboardButton(f"{prefix} {r['title']} (+{r['coin_reward']}⚡)", callback_data=f"adm_edit|{r['id']}")])
    btns.append([InlineKeyboardButton("➕ Добавить", callback_data="adm_add_new")])

    await q.message.reply_text(f"🏷 {region}\n{t(lang,'Список задач:','Topshiriqlar:')}", reply_markup=InlineKeyboardMarkup(btns))
    return ADM_LIST


async def admin_add_new(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    await q.message.reply_text("✏️ Название задачи:")
    return ADM_NEW_TITLE


async def admin_new_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["new_title"] = (update.message.text or "").strip()
    await update.message.reply_text("📖 Описание (или '-' если нет):")
    return ADM_NEW_DESC


async def admin_new_desc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    d = (update.message.text or "").strip()
    context.user_data["new_desc"] = "" if d == "-" else d
    await update.message.reply_text("💰 Награда (coins):")
    return ADM_NEW_REWARD


async def admin_new_reward(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = gl(update, context)
    try:
        reward = int((update.message.text or "").strip())
        if reward < 0:
            raise ValueError
    except Exception:
        await update.message.reply_text(t(lang, "Введите число.", "Son kiriting."))
        return ADM_NEW_REWARD

    region = context.user_data.get("adm_region")
    level = context.user_data.get("adm_level")

    conn = db()
    conn.execute(
        """
        INSERT INTO tasks (region, created_by, title, description, coin_reward, level, is_active)
        VALUES (?, ?, ?, ?, ?, ?, 1)
        """,
        (region, update.effective_user.id, context.user_data["new_title"], context.user_data["new_desc"], reward, level),
    )
    conn.commit()
    conn.close()

    await update.message.reply_text(t(lang, "✅ Задание добавлено.", "✅ Topshiriq qo‘shildi."))
    return ConversationHandler.END


async def admin_edit_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    context.user_data["edit_task_id"] = int(q.data.split("|", 1)[1])
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✏ Название", callback_data="field_title"), InlineKeyboardButton("📖 Описание", callback_data="field_desc")],
        [InlineKeyboardButton("💰 Награда", callback_data="field_reward")],
        [InlineKeyboardButton("⚡ Вкл/Выкл", callback_data="field_status")],
    ])
    await q.message.reply_text("Что изменить?", reply_markup=kb)
    return ADM_EDIT_FIELD


async def admin_edit_field(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    lang = gl(update, context)
    field = q.data.split("_", 1)[1]
    tid = context.user_data["edit_task_id"]
    region = context.user_data.get("adm_region")

    conn = db()
    ok_row = conn.execute("SELECT 1 FROM tasks WHERE id=? AND region=?", (tid, region)).fetchone()
    if not ok_row:
        conn.close()
        await q.message.reply_text("❌ Это задание не из вашей области.")
        return ConversationHandler.END

    if field == "status":
        row = conn.execute("SELECT is_active FROM tasks WHERE id=?", (tid,)).fetchone()
        new_val = 0 if row["is_active"] == 1 else 1
        conn.execute("UPDATE tasks SET is_active=? WHERE id=?", (new_val, tid))
        conn.commit()
        conn.close()
        await q.message.reply_text(t(lang, "✅ Статус изменён.", "✅ Holat o‘zgardi."))
        return ConversationHandler.END

    conn.close()
    context.user_data["edit_field"] = field
    await q.message.reply_text("Новое значение:")
    return ADM_EDIT_VALUE


async def admin_edit_value(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = gl(update, context)
    tid = context.user_data["edit_task_id"]
    field = context.user_data["edit_field"]
    val = (update.message.text or "").strip()
    region = context.user_data.get("adm_region")

    mapping = {"title": "title", "desc": "description", "reward": "coin_reward"}
    if field == "reward":
        try:
            val = int(val)
            if val < 0:
                raise ValueError
        except Exception:
            await update.message.reply_text(t(lang, "Введите число.", "Son kiriting."))
            return ADM_EDIT_VALUE

    conn = db()
    ok_row = conn.execute("SELECT 1 FROM tasks WHERE id=? AND region=?", (tid, region)).fetchone()
    if not ok_row:
        conn.close()
        await update.message.reply_text("❌ Это задание не из вашей области.")
        return ConversationHandler.END

    conn.execute(f"UPDATE tasks SET {mapping[field]}=? WHERE id=?", (val, tid))
    conn.commit()
    conn.close()

    await update.message.reply_text(t(lang, "✅ Обновлено.", "✅ Yangilandi."))
    return ConversationHandler.END


# =========================
# MEETUPS (сохранены функции)
# =========================
ADD_TITLE, ADD_DESC, ADD_DATE, ADD_LOC, ADD_LIMIT, ADD_PHOTO = range(300, 306)
MS_CHOOSE, MS_GROUP_PHOTO, MS_SELFIE = range(310, 313)


async def addmeetup_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = gl(update, context)
    if not is_region_admin(update.effective_user.id):
        await update.message.reply_text(t(lang, "❌ Нет прав админа.", "❌ Admin huquqi yo‘q."))
        return ConversationHandler.END
    await update.message.reply_text("Название встречи:")
    return ADD_TITLE


async def addmeetup_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["m_title"] = (update.message.text or "").strip()
    await update.message.reply_text("Описание встречи:")
    return ADD_DESC


async def addmeetup_desc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["m_desc"] = (update.message.text or "").strip()
    await update.message.reply_text("Дата и время (YYYY-MM-DD HH:MM):")
    return ADD_DATE


async def addmeetup_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["m_start"] = (update.message.text or "").strip()
    await update.message.reply_text("Локация (адрес/парк):")
    return ADD_LOC


async def addmeetup_loc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["m_loc"] = (update.message.text or "").strip()
    await update.message.reply_text("Лимит участников (1-30):")
    return ADD_LIMIT


async def addmeetup_limit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = gl(update, context)
    text = (update.message.text or "").strip()
    try:
        limit = int(text)
        if limit <= 0 or limit > 30:
            raise ValueError
    except Exception:
        await update.message.reply_text(t(lang, "Введите число 1-30.", "1-30 son kiriting."))
        return ADD_LIMIT

    context.user_data["m_limit"] = limit
    await update.message.reply_text("Пришлите афишу/карту (фото):")
    return ADD_PHOTO


async def addmeetup_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = gl(update, context)
    if not update.message.photo:
        await update.message.reply_text(t(lang, "Нужно фото.", "Rasm kerak."))
        return ADD_PHOTO
    photo_id = update.message.photo[-1].file_id

    conn = db()
    conn.execute(
        """
        INSERT INTO meetups (title, description, location, start_at, photo_file_id, max_participants)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            context.user_data["m_title"],
            context.user_data["m_desc"],
            context.user_data["m_loc"],
            context.user_data["m_start"],
            photo_id,
            context.user_data["m_limit"],
        ),
    )
    conn.commit()
    conn.close()
    await update.message.reply_text(t(lang, "✅ Встреча создана.", "✅ Uchrashuv yaratildi."))
    return ConversationHandler.END


async def meetups_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = gl(update, context)
    conn = db()
    rows = conn.execute(
        "SELECT id, title, description, location, start_at, photo_file_id, max_participants FROM meetups ORDER BY id DESC"
    ).fetchall()
    if not rows:
        conn.close()
        await update.message.reply_text(t(lang, "📭 Встреч нет.", "📭 Uchrashuv yo‘q."))
        return

    for r in rows:
        cur_count = conn.execute("SELECT COUNT(*) FROM meetup_registrations WHERE meetup_id=?", (r["id"],)).fetchone()[0]
        text = (
            f"📌 <b>{r['title']}</b>\n"
            f"{r['description'] or ''}\n\n"
            f"🗓 {r['start_at']}\n"
            f"📍 {r['location']}\n"
            f"👥 {cur_count}/{r['max_participants'] or ''}"
        )
        kb = InlineKeyboardMarkup([[InlineKeyboardButton(t(lang, "📝 Записаться", "📝 Ro‘yxatdan o‘tish"), callback_data=f"mreg|{r['id']}")]])
        try:
            await update.message.reply_photo(photo=r["photo_file_id"], caption=text, parse_mode=ParseMode.HTML, reply_markup=kb)
        except Exception:
            await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)

    conn.close()


async def meetup_register(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    lang = gl(update, context)
    mid = int(q.data.split("|", 1)[1])

    conn = db()
    u = conn.execute("SELECT id FROM users WHERE telegram_id=?", (q.from_user.id,)).fetchone()
    if not u:
        conn.close()
        await q.message.reply_text(t(lang, "❌ Сначала /start", "❌ Avval /start"))
        return

    try:
        conn.execute("INSERT INTO meetup_registrations (meetup_id, user_id) VALUES (?, ?)", (mid, u["id"]))
        conn.commit()
        await q.message.reply_text(t(lang, "✅ Вы записаны.", "✅ Ro‘yxatdan o‘tdingiz."))
    except sqlite3.IntegrityError:
        await q.message.reply_text(t(lang, "ℹ️ Уже записаны.", "ℹ️ Allaqachon ro‘yxatdan o‘tgansiz."))
    finally:
        conn.close()


async def meetup_submit_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = gl(update, context)
    conn = db()
    u = conn.execute("SELECT id FROM users WHERE telegram_id=?", (update.effective_user.id,)).fetchone()
    if not u:
        conn.close()
        await update.message.reply_text(t(lang, "❌ Сначала /start", "❌ Avval /start"))
        return ConversationHandler.END

    rows = conn.execute(
        """
        SELECT m.id, m.title, m.start_at
        FROM meetup_registrations r
        JOIN meetups m ON m.id = r.meetup_id
        WHERE r.user_id=?
        ORDER BY m.start_at DESC
        """,
        (u["id"],),
    ).fetchall()
    conn.close()

    if not rows:
        await update.message.reply_text(t(lang, "Нет встреч для отчёта.", "Hisobot uchun uchrashuv yo‘q."))
        return ConversationHandler.END

    kb = [[InlineKeyboardButton(f"{r['title']} — {r['start_at']}", callback_data=f"msel|{r['id']}")] for r in rows]
    await update.message.reply_text(t(lang, "Выберите встречу:", "Uchrashuvni tanlang:"), reply_markup=InlineKeyboardMarkup(kb))
    return MS_CHOOSE


async def meetup_submit_choose(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    lang = gl(update, context)
    context.user_data["ms_mid"] = int(q.data.split("|", 1)[1])
    await q.message.reply_text(t(lang, "Пришлите ОБЩЕЕ фото (групповое):", "UMUMIY rasm yuboring:"))
    return MS_GROUP_PHOTO


async def meetup_submit_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = gl(update, context)
    if not update.message.photo:
        await update.message.reply_text(t(lang, "Нужно фото.", "Rasm kerak."))
        return MS_GROUP_PHOTO
    context.user_data["ms_group"] = update.message.photo[-1].file_id
    await update.message.reply_text(t(lang, "Теперь пришлите СВОЁ селфи:", "Endi o‘zingizning selfi yuboring:"))
    return MS_SELFIE


async def meetup_submit_selfie(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = gl(update, context)
    if not update.message.photo:
        await update.message.reply_text(t(lang, "Нужно фото.", "Rasm kerak."))
        return MS_SELFIE

    selfie_id = update.message.photo[-1].file_id
    mid = context.user_data["ms_mid"]
    group_id = context.user_data["ms_group"]

    conn = db()
    uid = conn.execute("SELECT id FROM users WHERE telegram_id=?", (update.effective_user.id,)).fetchone()["id"]
    conn.execute(
        """
        INSERT INTO meetup_submissions (meetup_id, user_id, group_photo_file_id, selfie_file_id, status, coins_awarded)
        VALUES (?, ?, ?, ?, 'pending', 0)
        """,
        (mid, uid, group_id, selfie_id),
    )
    conn.commit()
    conn.close()

    await update.message.reply_text(t(lang, "✅ Отчёт отправлен.", "✅ Hisobot yuborildi."), reply_markup=main_kb(lang))
    return ConversationHandler.END


async def meetup_submissions_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = gl(update, context)
    if not is_region_admin(update.effective_user.id):
        await update.message.reply_text(t(lang, "❌ Нет прав админа.", "❌ Admin huquqi yo‘q."))
        return

    conn = db()
    rows = conn.execute(
        """
        SELECT ms.id, u.full_name, u.telegram_id, m.title, ms.group_photo_file_id, ms.selfie_file_id
        FROM meetup_submissions ms
        JOIN users u ON u.id = ms.user_id
        JOIN meetups m ON m.id = ms.meetup_id
        WHERE ms.status='pending'
        ORDER BY ms.submitted_at ASC
        """
    ).fetchall()
    conn.close()

    if not rows:
        await update.message.reply_text(t(lang, "📭 Нет отчётов.", "📭 Hisobotlar yo‘q."))
        return

    for r in rows:
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Принять", callback_data=f"ms_ok|{r['id']}"),
            InlineKeyboardButton("❌ Отклонить", callback_data=f"ms_no|{r['id']}"),
        ]])
        await update.message.reply_text(f"👤 {r['full_name']}\nMeetup: {r['title']}\n💰 +{MEETUP_REWARD}⚡", reply_markup=kb)
        try:
            await update.message.reply_photo(photo=r["group_photo_file_id"], caption="Групповое фото")
            await update.message.reply_photo(photo=r["selfie_file_id"], caption="Селфи")
        except Exception:
            pass


async def meetup_review(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    action, sid_str = q.data.split("|", 1)
    sid = int(sid_str)
    ok = action == "ms_ok"

    conn = db()
    row = conn.execute(
        """
        SELECT ms.user_id, u.telegram_id, m.title
        FROM meetup_submissions ms
        JOIN users u ON u.id = ms.user_id
        JOIN meetups m ON m.id = ms.meetup_id
        WHERE ms.id=?
        """,
        (sid,),
    ).fetchone()

    if not row:
        conn.close()
        await q.message.reply_text("❌ Не найдено.")
        return

    if ok:
        conn.execute("UPDATE users SET coins=coins+? WHERE id=?", (MEETUP_REWARD, row["user_id"]))
        conn.execute("UPDATE meetup_submissions SET status='approved', coins_awarded=? WHERE id=?", (MEETUP_REWARD, sid))
        conn.commit()
        conn.close()
        try:
            await context.bot.send_message(row["telegram_id"], f"✅ Meetup «{row['title']}» — +{MEETUP_REWARD}⚡")
        except Exception:
            pass
        await q.message.reply_text("✅ Принято.")
    else:
        conn.execute("UPDATE meetup_submissions SET status='rejected' WHERE id=?", (sid,))
        conn.commit()
        conn.close()
        try:
            await context.bot.send_message(row["telegram_id"], f"❌ Meetup «{row['title']}» — отклонено")
        except Exception:
            pass
        await q.message.reply_text("❌ Отклонено.")


# =========================
# LEADERS (по coins)
# =========================
async def leaders_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = gl(update, context)
    conn = db()
    top_u = conn.execute(
        "SELECT full_name, coins FROM users WHERE role='volunteer' ORDER BY coins DESC, full_name ASC LIMIT 10"
    ).fetchall()
    conn.close()

    if not top_u:
        await update.message.reply_text(t(lang, "— пока пусто —", "— hozircha bo‘sh —"), reply_markup=main_kb(lang))
        return

    lines = [t(lang, "🏆 Топ волонтёров:", "🏆 Top volontyorlar:")]
    for i, r in enumerate(top_u, 1):
        lines.append(f"{i}. {r['full_name']} — {r['coins']}⚡")
    await update.message.reply_text("\n".join(lines), reply_markup=main_kb(lang))


# =========================
# REWARDS
# =========================
async def rewards_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = gl(update, context)
    u = get_user_row_by_tid(update.effective_user.id)
    if not u:
        await update.message.reply_text(t(lang, "❌ Сначала /start", "❌ Avval /start"))
        return

    text_lines = [
        t(lang, "🎁 Обмен баллов на призы", "🎁 Sovg‘alar"),
        "",
        f"{t(lang,'Ваш баланс','Balans')}: {u['coins']}⚡",
    ]
    kb_rows = []
    for r in REWARDS:
        name = r["name_ru"] if lang == "ru" else r["name_uz"]
        kb_rows.append([InlineKeyboardButton(f"{name} — {r['cost']}⚡", callback_data=f"rw|{r['id']}")])

    await update.message.reply_text("\n".join(text_lines), reply_markup=InlineKeyboardMarkup(kb_rows))


async def rewards_select(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    lang = gl(update, context)

    rid = int(q.data.split("|", 1)[1])
    reward = next((r for r in REWARDS if r["id"] == rid), None)
    if not reward:
        await q.message.reply_text("❌ Не найдено.")
        return

    conn = db()
    row = conn.execute("SELECT id, full_name, coins FROM users WHERE telegram_id=?", (q.from_user.id,)).fetchone()
    if not row:
        conn.close()
        await q.message.reply_text(t(lang, "❌ Сначала /start", "❌ Avval /start"))
        return

    if row["coins"] < reward["cost"]:
        conn.close()
        await q.message.reply_text(t(lang, "❌ Недостаточно баллов.", "❌ Ball yetarli emas."))
        return

    new_coins = row["coins"] - reward["cost"]
    conn.execute("UPDATE users SET coins=? WHERE id=?", (new_coins, row["id"]))
    name = reward["name_ru"] if lang == "ru" else reward["name_uz"]
    conn.execute("INSERT INTO rewards (user_id, reward_name, coin_cost) VALUES (?, ?, ?)", (row["id"], name, reward["cost"]))
    conn.commit()
    conn.close()

    await q.message.reply_text(t(lang, f"✅ Заявка отправлена: {name}", f"✅ So‘rov yuborildi: {name}"))


# =========================
# SUPERADMIN: make_admin + reset_all
# =========================
async def make_admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_superadmin(update.effective_user.id):
        await update.message.reply_text("❌ Нет прав.")
        return

    args = context.args
    if len(args) < 2:
        await update.message.reply_text("Использование:\n/make_admin <tg_id> <region_ru>\nПример:\n/make_admin 123456789 Самаркандская обл")
        return

    try:
        admin_tid = int(args[0])
    except Exception:
        await update.message.reply_text("tg_id должен быть числом.")
        return

    region = " ".join(args[1:]).strip()
    if region not in REGIONS_RU:
        await update.message.reply_text("Регион должен быть из списка RU регионов (как в регистрации).")
        return

    u = get_user_row_by_tid(admin_tid)
    if not u:
        await update.message.reply_text("Этот пользователь ещё не зарегистрирован. Пусть сначала нажмёт /start.")
        return

    set_user_role(admin_tid, "admin")
    assign_admin_region(admin_tid, region)
    await update.message.reply_text(f"✅ Админ назначен: {admin_tid} -> {region}")


async def reset_all_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_superadmin(update.effective_user.id):
        await update.message.reply_text("❌ Нет прав.")
        return
    reset_database_file()
    await update.message.reply_text("✅ База сброшена. Все пользователи должны зарегистрироваться заново через /start.")


# =========================
# HELP + MENU BUTTONS
# =========================
async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # списки команд отдельно
    txt = (
        "👤 Волонтёр:\n"
        "/start – регистрация\n"
        "/profile – профиль\n"
        "/balance – баланс\n"
        "/tasks – задания (по области) + статус\n"
        "/trash_report – сообщить о мусоре (локация+фото+коммент)\n"
        "/meetups – встречи\n"
        "/meetup_submit – отчёт по встрече\n"
        "/leaders – лидеры\n"
        "/rewards – обмен баллов\n"
        "/about – о проекте\n"
        "/help – помощь\n\n"
        "🛡 Админ области:\n"
        "/submissions – проверка заданий\n"
        "/admin_tasks – добавить/редактировать задания своей области\n"
        "/trash_points – проверка заявок о мусоре\n"
        "/addmeetup – создать встречу\n"
        "/meetup_submissions – проверка отчётов по встречам\n\n"
        "⭐ Супер-админ:\n"
        "/make_admin <tg_id> <region_ru>\n"
        "/reset_all\n"
        "/cancel – отмена диалога"
    )
    await update.message.reply_text(txt)


async def menu_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = gl(update, context)
    txt = (update.message.text or "").strip()

    if txt in ("👤 Мой профиль", "👤 Profilim"):
        return await profile(update, context)
    if txt in ("🌿 О проекте", "🌿 Loyiha haqida"):
        return await about_cmd(update, context)
    if txt in ("🎯 Задания", "🎯 Topshiriqlar"):
        await update.message.reply_text(t(lang, "Нажмите /tasks", "/tasks buyrug‘ini bosing"))
        return
    if txt in ("📅 Офлайн встречи", "📅 Uchrashuvlar"):
        return await meetups_list(update, context)
    if txt in ("💰 Мой баланс", "💰 Balans"):
        return await balance_cmd(update, context)
    if txt in ("🏆 Лидеры", "🏆 Reyting"):
        return await leaders_cmd(update, context)
    if txt in ("🗑 Сообщить о мусоре", "🗑 Axlat haqida xabar"):
        # запускаем диалог как команду
        return await trash_report_start(update, context)
    if txt in ("🎁 Обмен баллов", "🎁 Sovg‘alar"):
        return await rewards_cmd(update, context)
    return


# =========================
# BUILD
# =========================
def build_app() -> Application:
    ensure_schema()
    app = Application.builder().token(TOKEN).build()

    # registration conv
    reg_conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            LANG_CHOICE: [CallbackQueryHandler(set_language, pattern="^lang_")],
            REG_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_name)],
            REG_AGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_age)],
            REG_STUDY: [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_study)],
            REG_REGION: [CallbackQueryHandler(reg_region, pattern="^reg_region\\|")],
            REG_DISTRICT: [
                CallbackQueryHandler(reg_district, pattern="^reg_dist\\|"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, reg_district),
            ],
            REG_CONSENT: [CallbackQueryHandler(reg_consent, pattern="^consent_(yes|no)$")],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )
    app.add_handler(reg_conv)

    # trash report conv
    trash_conv = ConversationHandler(
        entry_points=[CommandHandler("trash_report", trash_report_start)],
        states={
            TR_LOC: [MessageHandler(filters.LOCATION, trash_report_loc)],
            TR_PHOTO: [MessageHandler(filters.PHOTO, trash_report_photo)],
            TR_NOTE: [MessageHandler(filters.TEXT & ~filters.COMMAND, trash_report_note)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )
    app.add_handler(trash_conv)

    # tasks conv
    task_conv = ConversationHandler(
        entry_points=[CommandHandler("tasks", tasks_start)],
        states={
            TASK_LEVEL: [CallbackQueryHandler(tasks_list, pattern="^level_")],
            TASK_LIST: [CallbackQueryHandler(tasks_choose, pattern="^task_submit\\|")],
            TASK_SUBMIT: [MessageHandler(filters.PHOTO, tasks_submit)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )
    app.add_handler(task_conv)

    # admin tasks conv
    admin_tasks_conv = ConversationHandler(
        entry_points=[CommandHandler("admin_tasks", admin_tasks_start)],
        states={
            ADM_PICK_REGION: [CallbackQueryHandler(admin_pick_region, pattern="^adm_reg\\|")],
            ADM_LEVEL: [CallbackQueryHandler(admin_pick_level, pattern="^adm_level_")],
            ADM_LIST: [
                CallbackQueryHandler(admin_add_new, pattern="^adm_add_new$"),
                CallbackQueryHandler(admin_edit_task, pattern="^adm_edit\\|"),
            ],
            ADM_NEW_TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_new_title)],
            ADM_NEW_DESC: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_new_desc)],
            ADM_NEW_REWARD: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_new_reward)],
            ADM_EDIT_FIELD: [CallbackQueryHandler(admin_edit_field, pattern="^field_(title|desc|status|reward)$")],
            ADM_EDIT_VALUE: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_edit_value)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )
    app.add_handler(admin_tasks_conv)

    # meetups convs
    addmeetup_conv = ConversationHandler(
        entry_points=[CommandHandler("addmeetup", addmeetup_start)],
        states={
            ADD_TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, addmeetup_title)],
            ADD_DESC: [MessageHandler(filters.TEXT & ~filters.COMMAND, addmeetup_desc)],
            ADD_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, addmeetup_date)],
            ADD_LOC: [MessageHandler(filters.TEXT & ~filters.COMMAND, addmeetup_loc)],
            ADD_LIMIT: [MessageHandler(filters.TEXT & ~filters.COMMAND, addmeetup_limit)],
            ADD_PHOTO: [MessageHandler(filters.PHOTO, addmeetup_photo)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )
    app.add_handler(addmeetup_conv)

    meetup_submit_conv = ConversationHandler(
        entry_points=[CommandHandler("meetup_submit", meetup_submit_start)],
        states={
            MS_CHOOSE: [CallbackQueryHandler(meetup_submit_choose, pattern="^msel\\|")],
            MS_GROUP_PHOTO: [MessageHandler(filters.PHOTO, meetup_submit_group)],
            MS_SELFIE: [MessageHandler(filters.PHOTO, meetup_submit_selfie)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )
    app.add_handler(meetup_submit_conv)

    # simple commands
    app.add_handler(CommandHandler("profile", profile))
    app.add_handler(CommandHandler("balance", balance_cmd))
    app.add_handler(CommandHandler("about", about_cmd))
    app.add_handler(CommandHandler("leaders", leaders_cmd))
    app.add_handler(CommandHandler("meetups", meetups_list))
    app.add_handler(CommandHandler("rewards", rewards_cmd))
    app.add_handler(CommandHandler("help", help_cmd))

    # callbacks
    app.add_handler(CallbackQueryHandler(submission_review, pattern="^(sub_ok\\||sub_no\\|)"))
    app.add_handler(CallbackQueryHandler(trash_point_review, pattern="^(tp_ok\\||tp_no\\|)"))
    app.add_handler(CallbackQueryHandler(meetup_register, pattern="^mreg\\|"))
    app.add_handler(CallbackQueryHandler(meetup_review, pattern="^(ms_ok\\||ms_no\\|)"))
    app.add_handler(CallbackQueryHandler(rewards_select, pattern="^rw\\|"))

    # admin commands
    app.add_handler(CommandHandler("submissions", submissions_cmd))
    app.add_handler(CommandHandler("trash_points", trash_points_cmd))
    app.add_handler(CommandHandler("meetup_submissions", meetup_submissions_cmd))

    # superadmin commands
    app.add_handler(CommandHandler("make_admin", make_admin_cmd))
    app.add_handler(CommandHandler("reset_all", reset_all_cmd))

    # menu buttons
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, menu_buttons))

    return app


if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("❌ Не найден BOT_TOKEN. Задай переменную окружения BOT_TOKEN.")
    ensure_schema()
    application = build_app()
    logger.info("✅ Bot is starting…")
    application.run_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)
