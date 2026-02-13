import logging
import sqlite3
from typing import List
from telegram import WebAppInfo
from telegram import (
    Update,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    ReplyKeyboardMarkup,
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
TRASHMAP_URL = "https://yourdomain.com/trashmap"
# =========================
# CONFIG
# =========================
TOKEN = "8537624496:AAG8K3YcYlBw1GWT36Xuv_0TBS3aHRzzwAU"
DB_PATH = "db.db"
MEETUP_REWARD = 100  # coins for meetup participation

# начальный список админов (твои ID)
ADMIN_IDS = [7916861272]

# Награды (баллы → приз)
REWARDS = [
    {
        "id": 1,
        "cost": 200,
        "name_ru": "Экобрелок или браслет",
        "name_uz": "Eko-brelok yoki bilaguzuk",
    },
    {
        "id": 2,
        "cost": 400,
        "name_ru": "Термос или футболка",
        "name_uz": "Termos yoki futbolka",
    },
    {
        "id": 3,
        "cost": 700,
        "name_ru": "Эко-набор (сумка, бутылка, блокнот)",
        "name_uz": "Eko-toʻplam (sumka, butilka, daftar)",
    },
    {
        "id": 4,
        "cost": 1000,
        "name_ru": "Сертификат «Эко-элчи»",
        "name_uz": "«Eko-elchi» sertifikati",
    },
]

# =========================
# LOGGING
# =========================
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


# =========================
# DB UTILS
# =========================
def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def is_admin(user_id: int) -> bool:
    # можно потом расширить на роли в БД, пока достаточно списка
    return user_id in ADMIN_IDS


def ensure_schema():
    conn = db()
    cur = conn.cursor()

    # USERS
    cur.execute(
        """
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        telegram_id INTEGER UNIQUE NOT NULL,
        full_name TEXT,
        age INTEGER,
        study_place TEXT,
        region TEXT,
        district TEXT,
        photo_file_id TEXT,
        role TEXT NOT NULL DEFAULT 'volunteer',
        coins INTEGER NOT NULL DEFAULT 0,
        language TEXT NOT NULL DEFAULT 'ru'
    );
    """
    )

    # TASKS
    cur.execute(
        """
    CREATE TABLE IF NOT EXISTS tasks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL,
        description TEXT,
        coin_reward INTEGER NOT NULL,
        level TEXT CHECK(level IN ('easy','medium','hard')) NOT NULL,
        is_active INTEGER NOT NULL DEFAULT 1
    );
    """
    )

    # SUBMISSIONS
    cur.execute(
        """
    CREATE TABLE IF NOT EXISTS submissions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        task_id INTEGER NOT NULL,
        proof TEXT,
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
        photo_file_id TEXT
    );
    """
    )
    # добавить столбец max_participants, если его нет
    try:
        cur.execute("ALTER TABLE meetups ADD COLUMN max_participants INTEGER")
    except sqlite3.OperationalError:
        pass

    # MEETUP REGISTRATIONS
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

    # MEETUP SUBMISSIONS
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

    # REWARDS
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

    # SEED TASKS
    count = cur.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
    if count == 0:
        seed_tasks(cur)

    conn.commit()
    conn.close()


def seed_tasks(cur: sqlite3.Cursor):
    # Easy — 20 баллов
    easy = [
        ("Собери 20 пластиковых бутылок", "", 20),
        ("Убери 30 сигаретных окурков", "", 20),
        ("Собери мусор одного цвета", "Например, только красный", 20),
        ("Фото «до/после» небольшой уборки", "", 20),
        ("Подними мусор и запости сторис с хэштегом", "", 20),
        ("Собери крышки и выложи ♻️", "", 20),
        ("Пройди пешком 15 мин и собери пакет мусора", "", 20),
        ("Найди и убери 5 стеклянных бутылок", "", 20),
        ("Подмети территорию возле дома/двора", "", 20),
        ("Собери 10 алюминиевых банок", "", 20),
    ]
    # Medium — 30 баллов
    med = [
        ("Уборка с другом/соседом", "", 30),
        ("Рассортируй 50 единиц мусора", "Пластик/стекло/металл", 30),
        ("Видео «1 минута — 1 мешок»", "", 30),
        ("Очисти «скрытое место» с мусором", "", 30),
        ("Собери 100 пластиковых крышек", "", 30),
        ("Мини-пикник без мусора (zero waste)", "", 30),
        ("Сдай старые батарейки", "", 30),
        ("Собери 5 кг макулатуры", "", 30),
        ("Плакат «Экология рядом»", "", 30),
        ("Сортировка дома + фото", "", 30),
    ]
    # Hard — 50 баллов
    hard = [
        ("Организуй уборку минимум на 10 человек", "", 50),
        ("Посади дерево + табличка", "", 50),
        ("Ящик для сбора батареек в районе", "", 50),
        ("Лекция для детей про вред мусора", "", 50),
        ("Очисти берег/канал/арык", "", 50),
        ("Акция «1 двор — 1 чистый день»", "", 50),
        ("Инфографика о сортировке + распространение", "", 50),
        ("Сбор пластика → на переработку", "", 50),
        ("Арт-объект из крышек/бутылок", "", 50),
        ("Эко-марафон за 1 день", "", 50),
    ]
    for t in easy:
        cur.execute(
            "INSERT INTO tasks (title, description, coin_reward, level, is_active) VALUES (?, ?, ?, 'easy', 1)",
            t,
        )
    for t in med:
        cur.execute(
            "INSERT INTO tasks (title, description, coin_reward, level, is_active) VALUES (?, ?, ?, 'medium', 1)",
            t,
        )
    for t in hard:
        cur.execute(
            "INSERT INTO tasks (title, description, coin_reward, level, is_active) VALUES (?, ?, ?, 'hard', 1)",
            t,
        )


# =========================
# I18N
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
    "Алмазарский",
    "Бектемирский",
    "Мирзо-Улугбекский",
    "Мирабадский",
    "Сергелийский",
    "Учтепинский",
    "Чиланзарский",
    "Шайхонтохурский",
    "Юнусабадский",
    "Яккасарайский",
    "Яшнабадский",
    "Янгихаётский",
]
TASHKENT_DISTRICTS_UZ = [
    "Olmazor tumani",
    "Bektemir tumani",
    "Mirzo Ulugʻbek tumani",
    "Mirobod tumani",
    "Sirgʻali tumani",
    "Uchtepa tumani",
    "Chilonzor tumani",
    "Shayxontohur tumani",
    "Yunusobod tumani",
    "Yakkasaroy tumani",
    "Yashnobod tumani",
    "Yangihayot tumani",
]


def get_lang_from_db(user_tid: int) -> str:
    try:
        conn = db()
        row = conn.execute(
            "SELECT language FROM users WHERE telegram_id=?", (user_tid,)
        ).fetchone()
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
    user_tid = update.effective_user.id if update.effective_user else None
    if user_tid:
        lang = get_lang_from_db(user_tid)
        context.user_data["language"] = lang
        return lang
    return "ru"


def T(lang: str, key: str) -> str:
    RU = {
        "st_pending": "🕓 На проверке",
        "st_approved": "✅ Принято",
        "st_rejected": "❌ Отклонено",
        "dialog_closed": "❌ Диалог закрыт.",
        "already_registered": "✅ Вы уже зарегистрированы. Используйте /profile.",
        "choose_lang": "🌍 Выберите язык / Tilni tanlang:",
        "ask_name": "Введите своё ФИО:",
        "ask_age": "Введите ваш возраст:",
        "ask_study": "Введите место учёбы:",
        "ask_region": "Выберите область/регион:",
        "ask_district": "Введите ваш район:",
        "ask_district_tash": "Выберите район г. Ташкент:",
        "ask_photo": "📸 Отправьте своё фото:",
        "consent_text": (
            "📌 Мы собираем ваши данные только для целей проекта.\n"
            "Они не передаются третьим лицам и хранятся в безопасности.\n\n"
            "Вы согласны на обработку персональных данных?"
        ),
        "consent_yes": "✅ Согласен",
        "consent_no": "❌ Не согласен",
        "reg_canceled": "❌ Регистрация отменена.",
        "reg_done": (
            "🎉 Регистрация завершена!\n\n"
            "Ты волонтёр экопроекта 🌱\n"
            "Выполняй миссии, получай баллы и обменивай их на призы 🎁."
        ),
        "profile_unreg": "❌ Вы не зарегистрированы. Используйте /start.",
        "balance": "💰 Баланс: {coins}⚡",
        "menu_levels": "Выберите уровень сложности:",
        "no_tasks": "📭 Заданий пока нет.",
        "choose_task": "Выберите задание:",
        "task_unavailable": "Это задание недоступно.",
        "send_proof": "📸 Отправьте фото-доказательство выполнения задания:",
        "task_sent": "✅ Задание отправлено на проверку! Ожидайте ответа.",
        "no_rights": "❌ У вас нет прав администратора.",
        "no_pending": "📭 Нет заданий на проверке.",
        "approved": "✅ Одобрено.",
        "rejected": "❌ Отклонено.",
        "not_found": "❌ Не найдено.",
        "meetup_title": "Название встречи:",
        "meetup_desc": "Описание встречи:",
        "meetup_date": "Дата и время (YYYY-MM-DD HH:MM):",
        "meetup_loc": "Локация (адрес/парк):",
        "meetup_limit": "Лимит участников (максимум 30):",
        "meetup_photo": "Пришлите афишу/карту (фото):",
        "meetup_created": "✅ Встреча создана.",
        "no_meetups": "📭 Ближайших встреч нет.",
        "register": "📝 Записаться",
        "registered": "✅ Вы записаны.",
        "already_registered": "ℹ️ Вы уже записаны на эту встречу.",
        "need_start": "Сначала зарегистрируйтесь через /start.",
        "no_meetups_to_report": "Нет встреч для отчёта.",
        "choose_meetup": "Выберите встречу:",
        "send_group": "Пришлите ОБЩЕЕ фото (групповое):",
        "need_photo": "Нужно фото.",
        "send_selfie": "Теперь пришлите СВОЁ селфи:",
        "meetup_report_sent": "✅ Отчёт отправлен администратору.",
        "no_meetup_reports": "📭 Нет отчётов по встречам.",
        "leaders_title": "🏆 <b>Таблица лидеров</b>",
        "leaders_top_vols": "<b>Топ-10 волонтёров</b>",
        "leaders_top_dist": "<b>Топ районов</b> (активные волонтёры)",
        "leaders_empty": "— пока пусто —",
        "leaders_personal": "<b>Личная позиция</b>",
        "your_balance": "Ваш баланс: {coins}⚡",
        "your_district": "Ваш район: {district}",
        "volunteers_empty": "Волонтёров пока нет.",
        "what_change": "Что изменить?",
        "added": "✅ Задание добавлено.",
        "updated": "✅ Обновлено.",
        "status_changed": "✅ Статус изменён.",
        "about_text": (
            "🌿 <b>О проекте</b>\n\n"
            "Это эко-инициатива, где волонтёры очищают город от мусора, "
            "участвуют в офлайн-встречах и получают баллы за задания.\n\n"
            "Баллы можно обменять на маленькие призы и статус «эко-героя» 🌱"
        ),
        "rewards_title": "🎁 Обмен баллов на призы",
        "rewards_not_enough": "❌ Недостаточно баллов для этой награды.",
        "rewards_ok_user": (
            "✅ Заявка на награду «{name}» отправлена администратору.\n"
            "С вас списано {cost}⚡."
        ),
        "rewards_request_admin": (
            "🎁 Новый запрос на награду:\n"
            "Пользователь: {full_name}\n"
            "Баланс после списания: {coins}⚡\n"
            "Награда: {name} ({cost}⚡)"
        ),
        "help": (
            "/start – Регистрация\n"
            "/menu – Главное меню\n"
            "/profile – Мой профиль\n"
            "/about – О проекте\n"
            "/tasks – Задания (Easy/Medium/Hard)\n"
            "/meetups – Ближайшие встречи\n"
            "/meetup_submit – Фото после встречи\n"
            "/leaders – Таблица лидеров\n"
            "/balance – Мой баланс\n"
            "/rewards – Обмен баллов\n"
            "/submissions – Проверка заданий (админ)\n"
            "/meetup_submissions – Проверка встреч (админ)\n"
            "/addmeetup – Создать встречу (админ)\n"
            "/volunteers – Список волонтёров (админ)\n"
            "/admin_tasks – Управление заданиями (админ)\n"
            "/cancel – Отмена"
        ),
    }
    UZ = {
        "st_pending": "🕓 Tekshiruvda",
        "st_approved": "✅ Tasdiqlandi",
        "st_rejected": "❌ Rad etildi",
        "dialog_closed": "❌ Suhbat yopildi.",
        "already_registered": "✅ Siz allaqachon ro‘yxatdan o‘tgansiz. /profile dan foydalaning.",
        "choose_lang": "🌍 Tilni tanlang / Выберите язык:",
        "ask_name": "Ism, familiya va sharifingizni kiriting:",
        "ask_age": "Yoshingizni kiriting:",
        "ask_study": "O‘qish joyingizni kiriting:",
        "ask_region": "Viloyatni tanlang:",
        "ask_district": "Tumaningizni kiriting:",
        "ask_district_tash": "Toshkent shahri tumani:",
        "ask_photo": "📸 Rasm yuboring:",
        "consent_text": (
            "📌 Maʼlumotlaringiz faqat loyiha maqsadlarida yigʻiladi.\n"
            "Uchinchi shaxslarga berilmaydi va xavfsiz saqlanadi.\n\n"
            "Shaxsiy maʼlumotlaringizni qayta ishlashga rozimisiz?"
        ),
        "consent_yes": "✅ Roziman",
        "consent_no": "❌ Rozi emasman",
        "reg_canceled": "❌ Ro‘yxatdan o‘tish bekor qilindi.",
        "reg_done": (
            "🎉 Ro‘yxatdan o‘tish yakunlandi!\n\n"
            "Siz ekoloyihaning volontyorisiz 🌱\n"
            "Topshiriqlarni bajaring, ball to‘plang va sovg‘alarga almashtiring 🎁."
        ),
        "profile_unreg": "❌ Siz ro‘yxatdan o‘tmagansiz. /start buyrug‘idan foydalaning.",
        "balance": "💰 Balans: {coins}⚡",
        "menu_levels": "Qiyinchilik darajasini tanlang:",
        "no_tasks": "📭 Hozircha topshiriqlar yo‘q.",
        "choose_task": "Topshiriqni tanlang:",
        "task_unavailable": "Bu topshiriq vaqtincha mavjud emas.",
        "send_proof": "📸 Topshiriq bajarilganini tasdiqlovchi rasm yuboring:",
        "task_sent": "✅ Topshiriq tekshiruvga yuborildi! Javobni kuting.",
        "no_rights": "❌ Sizda administrator huquqlari yo‘q.",
        "no_pending": "📭 Tekshiruvda topshiriqlar yo‘q.",
        "approved": "✅ Tasdiqlandi.",
        "rejected": "❌ Rad etildi.",
        "not_found": "❌ Topilmadi.",
        "meetup_title": "Uchrashuv nomi:",
        "meetup_desc": "Uchrashuv tavsifi:",
        "meetup_date": "Sana va vaqt (YYYY-MM-DD HH:MM):",
        "meetup_loc": "Manzil (park/adres):",
        "meetup_limit": "Ishtirokchilar limiti (eng koʻpi 30):",
        "meetup_photo": "Afishani/xaritani yuboring (rasm):",
        "meetup_created": "✅ Uchrashuv yaratildi.",
        "no_meetups": "📭 Yaqin kunlarda uchrashuvlar yo‘q.",
        "register": "📝 Ro‘yxatdan o‘tish",
        "registered": "✅ Siz ro‘yxatdan o‘tdingiz.",
        "already_registered": "ℹ️ Siz allaqachon ro‘yxatdan o‘tgansiz.",
        "need_start": "Avval /start orqali ro‘yxatdan o‘ting.",
        "no_meetups_to_report": "Hisobot uchun uchrashuv yo‘q.",
        "choose_meetup": "Uchrashuvni tanlang:",
        "send_group": "UMUMIY suratni (guruh bilan) yuboring:",
        "need_photo": "Rasm kerak.",
        "send_selfie": "Endi o‘zingizning selfi suratni yuboring:",
        "meetup_report_sent": "✅ Hisobot administratorga yuborildi.",
        "no_meetup_reports": "📭 Uchrashuvlar bo‘yicha hisobotlar yo‘q.",
        "leaders_title": "🏆 <b>Reyting jadvali</b>",
        "leaders_top_vols": "<b>Top-10 volontyor</b>",
        "leaders_top_dist": "<b>Tumanlar reytingi</b> (faol volontyorlar)",
        "leaders_empty": "— hozircha bo‘sh —",
        "leaders_personal": "<b>Shaxsiy maʼlumot</b>",
        "your_balance": "Balansingiz: {coins}⚡",
        "your_district": "Tumaningiz: {district}",
        "volunteers_empty": "Hozircha volontyorlar yo‘q.",
        "what_change": "Nimani o‘zgartiramiz?",
        "added": "✅ Topshiriq qo‘shildi.",
        "updated": "✅ Yangilandi.",
        "status_changed": "✅ Holat o‘zgartirildi.",
        "about_text": (
            "🌿 <b>Loyiha haqida</b>\n\n"
            "Bu ekoloyiha bo‘lib, volontyorlar shaharni axlatdan tozalaydi, "
            "oflayn tadbirlarda qatnashadi va topshiriqlar orqali ball to‘playdi.\n\n"
            "Ballar kichik sovg‘alarga va «eko-qahramon» maqomiga almashtiriladi 🌱"
        ),
        "rewards_title": "🎁 Ballarni sovg‘alarga almashtirish",
        "rewards_not_enough": "❌ Bu mukofot uchun ball yetarli emas.",
        "rewards_ok_user": (
            "✅ «{name}» mukofoti uchun so‘rovingiz administratorga yuborildi.\n"
            "Sizdan {cost}⚡ yechildi."
        ),
        "rewards_request_admin": (
            "🎁 Yangi mukofot so‘rovi:\n"
            "Foydalanuvchi: {full_name}\n"
            "Yechilgandan keyin balans: {coins}⚡\n"
            "Mukofot: {name} ({cost}⚡)"
        ),
        "help": (
            "/start – Ro‘yxatdan o‘tish\n"
            "/menu – Asosiy menyu\n"
            "/profile – Mening profilim\n"
            "/about – Loyiha haqida\n"
            "/tasks – Topshiriqlar (Easy/Medium/Hard)\n"
            "/meetups – Yaqin uchrashuvlar\n"
            "/meetup_submit – Uchrashuvdan keyin rasm yuborish\n"
            "/leaders – Reyting jadvali\n"
            "/balance – Mening balansim\n"
            "/rewards – Ballarni almashtirish\n"
            "/submissions – Topshiriqlarni tekshirish (admin)\n"
            "/meetup_submissions – Uchrashuvlarni tekshirish (admin)\n"
            "/addmeetup – Uchrashuv yaratish (admin)\n"
            "/volunteers – Volontyorlar ro‘yxati (admin)\n"
            "/admin_tasks – Topshiriqlarni boshqarish (admin)\n"
            "/cancel – Bekor qilish"
        ),
    }
    return RU[key] if lang == "ru" else UZ[key]


def kb_lang():
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🇷🇺 Русский", callback_data="lang_ru")],
            [InlineKeyboardButton("🇺🇿 O‘zbekcha", callback_data="lang_uz")],
        ]
    )


def get_main_keyboard(lang: str) -> ReplyKeyboardMarkup:
    if lang == "ru":
        rows = [
            ["👤 Мой профиль", "🌿 О проекте"],
            ["📅 Офлайн встречи", "🎯 Задания"],
            ["💰 Мои баллы", "🎁 Обмен баллов"],
        ]
    else:
        rows = [
            ["👤 Mening profilim", "🌿 Loyiha haqida"],
            ["📅 Oflayn tadbirlar", "🎯 Topshiriqlar"],
            ["💰 Ballarim", "🎁 Ballarni almashtirish"],
        ]
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


# =========================
# CANCEL
# =========================
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = gl(update, context)
    text = T(lang, "dialog_closed")
    if update.message:
        await update.message.reply_text(text, reply_markup=get_main_keyboard(lang))
    elif update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.message.reply_text(
            text, reply_markup=get_main_keyboard(lang)
        )
    return ConversationHandler.END


# =========================
# REGISTRATION
# =========================
LANG_CHOICE, REG_NAME, REG_AGE, REG_STUDY, REG_REGION, REG_DISTRICT, REG_CONSENT = range(100, 107)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    conn = db()
    row = conn.execute(
        "SELECT id, language FROM users WHERE telegram_id=?",
        (update.effective_user.id,),
    ).fetchone()
    conn.close()
    if row:
        lang = row["language"] or "ru"
        context.user_data["language"] = lang
        await update.message.reply_text(
            T(lang, "already_registered"), reply_markup=get_main_keyboard(lang)
        )
        return ConversationHandler.END

    await update.message.reply_text(T("ru", "choose_lang"), reply_markup=kb_lang())
    return LANG_CHOICE


async def set_language(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    lang = q.data.replace("lang_", "")
    context.user_data["language"] = lang
    await q.message.reply_text(T(lang, "ask_name"))
    return REG_NAME


async def reg_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["full_name"] = update.message.text.strip()
    lang = gl(update, context)
    await update.message.reply_text(T(lang, "ask_age"))
    return REG_AGE


async def reg_age(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["age"] = update.message.text.strip()
    lang = gl(update, context)
    await update.message.reply_text(T(lang, "ask_study"))
    return REG_STUDY


async def reg_study(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["study_place"] = update.message.text.strip()
    lang = gl(update, context)
    if lang == "ru":
        buttons = [
            [InlineKeyboardButton(r, callback_data=f"reg_region_{r}")] for r in REGIONS_RU
        ]
    else:
        buttons = [
            [InlineKeyboardButton(r, callback_data=f"reg_region_{r}")] for r in REGIONS_UZ
        ]
    await update.message.reply_text(
        T(lang, "ask_region"), reply_markup=InlineKeyboardMarkup(buttons)
    )
    return REG_REGION


async def reg_region(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    region = q.data.replace("reg_region_", "")
    context.user_data["region"] = region
    lang = gl(update, context)

    if region in ("г. Ташкент", "Toshkent shahri"):
        if lang == "ru":
            buttons = [
                [InlineKeyboardButton(d, callback_data=f"reg_dist_{d}")]
                for d in TASHKENT_DISTRICTS_RU
            ]
        else:
            buttons = [
                [InlineKeyboardButton(d, callback_data=f"reg_dist_{d}")]
                for d in TASHKENT_DISTRICTS_UZ
            ]
        await q.message.reply_text(
            T(lang, "ask_district_tash"), reply_markup=InlineKeyboardMarkup(buttons)
        )
        return REG_DISTRICT
    else:
        await q.message.reply_text(T(lang, "ask_district"))
        return REG_DISTRICT


async def reg_district(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = gl(update, context)

    if update.callback_query:
        q = update.callback_query
        await q.answer()
        district = q.data.replace("reg_dist_", "")
        context.user_data["district"] = district
        target_msg = q.message
    else:
        context.user_data["district"] = update.message.text.strip()
        target_msg = update.message

    kb = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(T(lang, "consent_yes"), callback_data="consent_yes")],
            [InlineKeyboardButton(T(lang, "consent_no"), callback_data="consent_no")],
        ]
    )
    await target_msg.reply_text(T(lang, "consent_text"), reply_markup=kb)
    return REG_CONSENT


async def reg_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["photo"] = update.message.photo[-1].file_id
    lang = gl(update, context)
    kb = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(T(lang, "consent_yes"), callback_data="consent_yes")],
            [InlineKeyboardButton(T(lang, "consent_no"), callback_data="consent_no")],
        ]
    )
    await update.message.reply_text(T(lang, "consent_text"), reply_markup=kb)
    return REG_CONSENT


async def reg_consent(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    lang = gl(update, context)
    if q.data == "consent_no":
        await q.message.reply_text(T(lang, "reg_canceled"))
        return ConversationHandler.END

    conn = db()
    conn.execute(
        """
        INSERT INTO users (
            telegram_id, full_name, age, study_place, region,
            district, photo_file_id, role, coins, language
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, 'volunteer', 0, ?)
    """,
        (
            update.effective_user.id,
            context.user_data["full_name"],
            context.user_data["age"],
            context.user_data["study_place"],
            context.user_data["region"],
            context.user_data["district"],
            None,  # фото больше не запрашиваем
            lang,
        ),
    )
    conn.commit()
    conn.close()

    await q.message.reply_text(
        T(lang, "reg_done"), reply_markup=get_main_keyboard(lang)
    )
    return ConversationHandler.END


# =========================
# PROFILE / BALANCE / ABOUT
# =========================
async def profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = gl(update, context)
    conn = db()
    row = conn.execute(
        """
        SELECT full_name, age, study_place, region, district, coins, photo_file_id
        FROM users WHERE telegram_id=?
    """,
        (update.effective_user.id,),
    ).fetchone()
    conn.close()

    if not row:
        await update.message.reply_text(T(lang, "profile_unreg"))
        return

    if lang == "ru":
        text = (
            f"👤 {row['full_name']}\n"
            f"Возраст: {row['age']}\n"
            f"Учёба: {row['study_place']}\n"
            f"Регион: {row['region']}\n"
            f"Район: {row['district']}\n"
            f"Баланс: {row['coins']}⚡"
        )
    else:
        text = (
            f"👤 {row['full_name']}\n"
            f"Yosh: {row['age']}\n"
            f"O‘qish joyi: {row['study_place']}\n"
            f"Viloyat: {row['region']}\n"
            f"Tuman: {row['district']}\n"
            f"Balans: {row['coins']}⚡"
        )

    if row["photo_file_id"]:
        await update.message.reply_photo(
            photo=row["photo_file_id"],
            caption=text,
            reply_markup=get_main_keyboard(lang),
        )
    else:
        await update.message.reply_text(text, reply_markup=get_main_keyboard(lang))


async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = gl(update, context)
    conn = db()
    row = conn.execute(
        "SELECT coins FROM users WHERE telegram_id=?", (update.effective_user.id,)
    ).fetchone()
    conn.close()
    if not row:
        await update.message.reply_text(T(lang, "profile_unreg"))
        return
    await update.message.reply_text(
        T(lang, "balance").format(coins=row["coins"]),
        reply_markup=get_main_keyboard(lang),
    )


async def about_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = gl(update, context)
    await update.message.reply_text(
        T(lang, "about_text"), parse_mode="HTML", reply_markup=get_main_keyboard(lang)
    )
async def trashmap_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = InlineKeyboardMarkup(
        [[InlineKeyboardButton("🗺 Открыть карту (тест)", url="http://127.0.0.1:5500")]]
    )
    await update.message.reply_text("🗺 Откройте карту:", reply_markup=kb)
async def menu_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = gl(update, context)
    await update.message.reply_text("📋", reply_markup=get_main_keyboard(lang))


# =========================
# TASKS
# =========================
TASK_LEVEL, TASK_LIST, TASK_SUBMIT = range(200, 203)


async def show_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = gl(update, context)

    kb = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🟢 Easy (20⚡)", callback_data="level_easy"),
                InlineKeyboardButton("🟡 Medium (30⚡)", callback_data="level_medium"),
                InlineKeyboardButton("🔴 Hard (50⚡)", callback_data="level_hard"),
            ]
        ]
    )

    await update.message.reply_text(
        T(lang, "menu_levels"),
        reply_markup=kb
    )
    return TASK_LEVEL


async def list_tasks_by_level(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    lang = gl(update, context)

    level = q.data.split("_")[1]
    context.user_data["task_level"] = level

    conn = db()
    user_row = conn.execute(
        "SELECT id FROM users WHERE telegram_id=?",
        (q.from_user.id,),
    ).fetchone()
    uid = user_row["id"] if user_row else None

    rows = conn.execute(
        "SELECT id, title, coin_reward, is_active FROM tasks WHERE level=? ORDER BY id",
        (level,),
    ).fetchall()
    conn.close()
    status_map = {}
    if uid:
        subs = conn.execute(
            """
            SELECT task_id, status, MAX(id) as last_id
            FROM submissions
            WHERE user_id=?
            GROUP BY task_id
            """,
            (uid,),
        ).fetchall()
        # task_id -> status
        for s in subs:
            status_map[s["task_id"]] = s["status"]
    # Нет задач
    if not rows:
        await q.edit_message_text(T(lang, "no_tasks"))
        return ConversationHandler.END

    # Кнопки задач
    buttons = []
    for r in rows:
        st = status_map.get(r["id"])
        st_text = ""
        if st == "pending":
            st_text = f" — {T(lang, 'st_pending')}"
        elif st == "approved":
            st_text = f" — {T(lang, 'st_approved')}"
        elif st == "rejected":
            st_text = f" — {T(lang, 'st_rejected')}"

        label = f"{r['title']} (+{r['coin_reward']}⚡){st_text}"
        if r["is_active"] == 1:
            buttons.append([
                InlineKeyboardButton(label, callback_data=f"submit_task_{r['id']}")
            ])
        else:
            buttons.append([
                InlineKeyboardButton(f"❌ {label}", callback_data="ignore")
            ])

    # Кнопка "⬅ Назад к уровням"
    back_btn = [
        InlineKeyboardButton("⬅ Назад", callback_data="back_levels")
    ]
    buttons.append(back_btn)

    await q.edit_message_text(
        T(lang, "choose_task"),
        reply_markup=InlineKeyboardMarkup(buttons)
    )
    return TASK_LIST


async def ignore_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    lang = gl(update, context)
    await q.answer(T(lang, "task_unavailable"))


async def choose_task_submit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    lang = gl(update, context)

    task_id = int(q.data.split("_")[2])
    context.user_data["submit_task_id"] = task_id

    await q.edit_message_text(T(lang, "send_proof"))
    return TASK_SUBMIT


async def save_submission(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = gl(update, context)

    user_tid = update.message.from_user.id
    task_id = context.user_data.get("submit_task_id")
    if not task_id:
        await update.message.reply_text("❌")
        return ConversationHandler.END

    photo_id = update.message.photo[-1].file_id

    conn = db()
    conn.execute(
        """
        INSERT INTO submissions (user_id, task_id, proof, status)
        VALUES ((SELECT id FROM users WHERE telegram_id=?), ?, ?, 'pending')
    """,
        (user_tid, task_id, photo_id),
    )
    conn.commit()
    conn.close()

    await update.message.reply_text(
        T(lang, "task_sent"),
        reply_markup=get_main_keyboard(lang)
    )
    return ConversationHandler.END


# =========================
# ADMIN DELETE TASKS
# =========================

async def submissions_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = gl(update, context)
    if not is_admin(update.effective_user.id):
        await update.message.reply_text(T(lang, "no_rights"))
        return

    conn = db()
    rows = conn.execute(
        """
        SELECT s.id, u.full_name, t.title, t.coin_reward, s.proof, u.telegram_id
        FROM submissions s
        JOIN users u ON u.id = s.user_id
        JOIN tasks t ON t.id = s.task_id
        WHERE s.status='pending'
        ORDER BY s.submitted_at ASC
    """
    ).fetchall()
    conn.close()

    if not rows:
        await update.message.reply_text(T(lang, "no_pending"))
        return

    for r in rows:
        caption = f"👤 {r['full_name']}\n📌 {r['title']}\n💰 +{r['coin_reward']}⚡"
        kb = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("✅", callback_data=f"sub_ok_{r['id']}"),
                    InlineKeyboardButton("❌", callback_data=f"sub_no_{r['id']}"),
                ]
            ]
        )

        try:
            await update.message.reply_photo(
                photo=r["proof"], caption=caption, reply_markup=kb
            )
        except:
            await update.message.reply_text(caption, reply_markup=kb)


async def submission_review(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    lang = gl(update, context)
    ok = q.data.startswith("sub_ok_")
    sid = int(q.data.rsplit("_", 1)[1])

    conn = db()

    if ok:
        row = conn.execute(
            """
            SELECT s.user_id, t.coin_reward, t.title, u.telegram_id
            FROM submissions s
            JOIN tasks t ON t.id = s.task_id
            JOIN users u ON u.id = s.user_id
            WHERE s.id=?
        """,
            (sid,),
        ).fetchone()

        if not row:
            conn.close()
            await q.message.reply_text(T(lang, "not_found"))
            return

        conn.execute(
            "UPDATE users SET coins=coins+? WHERE id=?",
            (row["coin_reward"], row["user_id"]),
        )
        conn.execute(
            "UPDATE submissions SET status='approved', coins_awarded=? WHERE id=?",
            (row["coin_reward"], sid),
        )
        conn.commit()
        conn.close()

        await context.bot.send_message(
            row["telegram_id"],
            f"✅ «{row['title']}» — +{row['coin_reward']}⚡",
        )
        await q.message.reply_text(T(lang, "approved"))

    else:
        row = conn.execute(
            """
            SELECT t.title, u.telegram_id
            FROM submissions s
            JOIN tasks t ON t.id = s.task_id
            JOIN users u ON u.id = s.user_id
            WHERE s.id=?
        """,
            (sid,),
        ).fetchone()

        conn.execute("UPDATE submissions SET status='rejected' WHERE id=?", (sid,))
        conn.commit()
        conn.close()

        if row:
            await context.bot.send_message(row["telegram_id"], f"❌ «{row['title']}»")

        await q.message.reply_text(T(lang, "rejected"))

# =========================
# MEETUPS
# =========================
ADD_TITLE, ADD_DESC, ADD_DATE, ADD_LOC, ADD_LIMIT, ADD_PHOTO = range(300, 306)
MS_CHOOSE, MS_GROUP_PHOTO, MS_SELFIE = range(310, 313)


async def addmeetup_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = gl(update, context)
    if not is_admin(update.effective_user.id):
        await update.message.reply_text(T(lang, "no_rights"))
        return ConversationHandler.END
    await update.message.reply_text(T(lang, "meetup_title"))
    return ADD_TITLE


async def addmeetup_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["m_title"] = update.message.text.strip()
    lang = gl(update, context)
    await update.message.reply_text(T(lang, "meetup_desc"))
    return ADD_DESC


async def addmeetup_desc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["m_desc"] = update.message.text.strip()
    lang = gl(update, context)
    await update.message.reply_text(T(lang, "meetup_date"))
    return ADD_DATE


async def addmeetup_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["m_start"] = update.message.text.strip()
    lang = gl(update, context)
    await update.message.reply_text(T(lang, "meetup_loc"))
    return ADD_LOC


async def addmeetup_loc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["m_loc"] = update.message.text.strip()
    lang = gl(update, context)
    await update.message.reply_text(T(lang, "meetup_limit"))
    return ADD_LIMIT


async def addmeetup_limit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = gl(update, context)
    text = update.message.text.strip()
    try:
        limit = int(text)
        if limit <= 0 or limit > 30:
            raise ValueError
    except Exception:
        if lang == "ru":
            await update.message.reply_text("Введите число от 1 до 30")
        else:
            await update.message.reply_text("1 dan 30 gacha bo‘lgan son kiriting")
        return ADD_LIMIT
    context.user_data["m_limit"] = limit
    await update.message.reply_text(T(lang, "meetup_photo"))
    return ADD_PHOTO


async def addmeetup_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = gl(update, context)
    photo_id = update.message.photo[-1].file_id if update.message.photo else None
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
    await update.message.reply_text(T(lang, "meetup_created"))
    return ConversationHandler.END


async def meetups_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = gl(update, context)
    conn = db()

    test = conn.execute("SELECT id, start_at FROM meetups").fetchall()
    print("\n=== DEBUG MEETUPS ===")
    for r in test:
        print(f"{r['id']} | '{r['start_at']}' | len={len(r['start_at'])}")
    print("=== END DEBUG ===\n")


    # 🟢 ВАЖНО: приводим строку start_at к дате через datetime(start_at)
    rows = conn.execute(
        """
        SELECT id, title, description, location, start_at, photo_file_id, max_participants
        FROM meetups
        ORDER BY id DESC
        """
    ).fetchall()

    if not rows:
        conn.close()
        await update.message.reply_text(T(lang, "no_meetups"))
        return

    for r in rows:
        cur_count = conn.execute(
            "SELECT COUNT(*) FROM meetup_registrations WHERE meetup_id=?", (r["id"],)
        ).fetchone()[0]

        max_p = r["max_participants"]

        text = (
            f"📌 <b>{r['title']}</b>\n"
            f"{r['description'] or ''}\n\n"
            f"🗓 {r['start_at']}\n"
            f"📍 {r['location']}"
        )
        if max_p:
            text += f"\n👥 {cur_count}/{max_p}"

        buttons = []
        if not max_p or cur_count < max_p:
            buttons.append(
                [InlineKeyboardButton(T(lang, "register"), callback_data=f"mreg_{r['id']}")]
            )

        kb = InlineKeyboardMarkup(buttons) if buttons else None

        if r["photo_file_id"]:
            try:
                await update.message.reply_photo(
                    photo=r["photo_file_id"], caption=text,
                    parse_mode="HTML", reply_markup=kb
                )
            except:
                await update.message.reply_text(
                    text, parse_mode="HTML", reply_markup=kb
                )
        else:
            await update.message.reply_text(
                text, parse_mode="HTML", reply_markup=kb
            )

    conn.close()



async def meetup_register(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    lang = gl(update, context)
    mid = int(q.data.split("_")[1])
    conn = db()
    row_user = conn.execute(
        "SELECT id FROM users WHERE telegram_id=?", (q.from_user.id,)
    ).fetchone()
    if not row_user:
        conn.close()
        await q.message.reply_text(T(lang, "need_start"))
        return
    uid = row_user["id"]
    # проверяем лимит
    row_m = conn.execute(
        "SELECT max_participants FROM meetups WHERE id=?", (mid,)
    ).fetchone()
    if not row_m:
        conn.close()
        await q.message.reply_text(T(lang, "not_found"))
        return
    max_p = row_m["max_participants"]
    if max_p:
        cur_count = conn.execute(
            "SELECT COUNT(*) FROM meetup_registrations WHERE meetup_id=?", (mid,)
        ).fetchone()[0]
        if cur_count >= max_p:
            conn.close()
            if lang == "ru":
                await q.message.reply_text("❌ Места закончились.")
            else:
                await q.message.reply_text("❌ Joylar tugagan.")
            return

    try:
        conn.execute(
            "INSERT INTO meetup_registrations (meetup_id, user_id) VALUES (?, ?)",
            (mid, uid),
        )
        conn.commit()
        await q.message.reply_text(T(lang, "registered"))
    except sqlite3.IntegrityError:
        await q.message.reply_text(T(lang, "already_registered"))
    finally:
        conn.close()


async def meetup_submit_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = gl(update, context)
    conn = db()
    u = conn.execute(
        "SELECT id FROM users WHERE telegram_id=?", (update.effective_user.id,)
    ).fetchone()
    if not u:
        conn.close()
        await update.message.reply_text(T(lang, "need_start"))
        return ConversationHandler.END
    uid = u["id"]
    rows = conn.execute(
        """
        SELECT m.id, m.title, m.start_at
        FROM meetup_registrations r
        JOIN meetups m ON m.id = r.meetup_id
        WHERE r.user_id=? AND m.start_at <= datetime('now')
        ORDER BY m.start_at DESC
    """,
        (uid,),
    ).fetchall()
    conn.close()
    if not rows:
        await update.message.reply_text(T(lang, "no_meetups_to_report"))
        return ConversationHandler.END
    kb = [
        [
            InlineKeyboardButton(
                f"{r['title']} — {r['start_at']}", callback_data=f"msel_{r['id']}"
            )
        ]
        for r in rows
    ]
    await update.message.reply_text(
        T(lang, "choose_meetup"), reply_markup=InlineKeyboardMarkup(kb)
    )
    return MS_CHOOSE


async def meetup_submit_choose(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    lang = gl(update, context)
    context.user_data["ms_mid"] = int(q.data.split("_")[1])
    await q.message.reply_text(T(lang, "send_group"))
    return MS_GROUP_PHOTO


async def meetup_submit_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = gl(update, context)
    if not update.message.photo:
        await update.message.reply_text(T(lang, "need_photo"))
        return MS_GROUP_PHOTO
    context.user_data["ms_group"] = update.message.photo[-1].file_id
    await update.message.reply_text(T(lang, "send_selfie"))
    return MS_SELFIE


async def meetup_submit_selfie(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = gl(update, context)
    if not update.message.photo:
        await update.message.reply_text(T(lang, "need_photo"))
        return MS_SELFIE
    selfie_id = update.message.photo[-1].file_id
    mid = context.user_data["ms_mid"]
    group_id = context.user_data["ms_group"]
    conn = db()
    uid = conn.execute(
        "SELECT id FROM users WHERE telegram_id=?", (update.effective_user.id,)
    ).fetchone()["id"]
    conn.execute(
        """
        INSERT INTO meetup_submissions (meetup_id, user_id, group_photo_file_id, selfie_file_id, status, coins_awarded)
        VALUES (?, ?, ?, ?, 'pending', 0)
    """,
        (mid, uid, group_id, selfie_id),
    )
    conn.commit()
    conn.close()
    await update.message.reply_text(
        T(lang, "meetup_report_sent"), reply_markup=get_main_keyboard(lang)
    )
    return ConversationHandler.END


async def meetup_members_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = gl(update, context)
    if not is_admin(update.effective_user.id):
        await update.message.reply_text(T(lang, "no_rights"))
        return

    conn = db()
    rows = conn.execute(
        "SELECT id, title, start_at FROM meetups ORDER BY start_at DESC"
    ).fetchall()
    conn.close()

    if not rows:
        await update.message.reply_text("❌ Встреч нет.")
        return

    kb = [
        [InlineKeyboardButton(f"{r['title']} — {r['start_at']}", callback_data=f"mm_{r['id']}")]
        for r in rows
    ]

    await update.message.reply_text("Выберите встречу:", reply_markup=InlineKeyboardMarkup(kb))

async def meetup_members_show(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    mid = int(q.data.split("_")[1])

    conn = db()

    # Инфо о встрече
    meet = conn.execute(
        "SELECT title, start_at, location FROM meetups WHERE id=?",
        (mid,),
    ).fetchone()

    if not meet:
        conn.close()
        await q.message.reply_text("❌ Встреча не найдена.")
        return

    # Список участников + username
    rows = conn.execute(
        """
        SELECT u.full_name, u.telegram_id
        FROM meetup_registrations r
        JOIN users u ON u.id = r.user_id
        WHERE r.meetup_id=?
        ORDER BY u.full_name
        """,
        (mid,),
    ).fetchall()
    conn.close()

    text = (
        f"📌 <b>{meet['title']}</b>\n"
        f"🗓 {meet['start_at']}\n"
        f"📍 {meet['location']}\n\n"
        f"👥 <b>Записались:</b>\n"
    )

    if not rows:
        text += "❌ Пока никто не записался."
    else:
        for i, r in enumerate(rows, 1):
            # получаем username пользователя
            try:
                user_info = await context.bot.get_chat(r["telegram_id"])
                username = user_info.username
            except:
                username = None

            if username:
                username_text = f"@{username}"
            else:
                username_text = "—"

            text += f"{i}. {r['full_name']} ({username_text})\n"

    await q.message.reply_text(text, parse_mode="HTML")




async def meetup_submissions_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = gl(update, context)
    if not is_admin(update.effective_user.id):
        await update.message.reply_text(T(lang, "no_rights"))
        return
    conn = db()
    rows = conn.execute(
        """
        SELECT ms.id, u.full_name, m.title, ms.group_photo_file_id, ms.selfie_file_id
        FROM meetup_submissions ms
        JOIN users u ON u.id = ms.user_id
        JOIN meetups m ON m.id = ms.meetup_id
        WHERE ms.status='pending'
        ORDER BY ms.submitted_at ASC
    """
    ).fetchall()
    conn.close()
    if not rows:
        await update.message.reply_text(T(lang, "no_meetup_reports"))
        return
    for r in rows:
        if r["group_photo_file_id"]:
            try:
                await update.message.reply_photo(
                    photo=r["group_photo_file_id"], caption=f"👥 {r['title']}"
                )
            except Exception:
                pass
        if r["selfie_file_id"]:
            try:
                await update.message.reply_photo(
                    photo=r["selfie_file_id"], caption=f"🤳 {r['full_name']}"
                )
            except Exception:
                pass
        kb = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("✅", callback_data=f"ms_ok_{r['id']}"),
                    InlineKeyboardButton("❌", callback_data=f"ms_no_{r['id']}"),
                ]
            ]
        )
        await update.message.reply_text(f"💰 +{MEETUP_REWARD}⚡", reply_markup=kb)


async def meetup_review(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    lang = gl(update, context)
    ok = q.data.startswith("ms_ok_")
    sid = int(q.data.rsplit("_", 1)[1])
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
        await q.message.reply_text(T(lang, "not_found"))
        return
    if ok:
        conn.execute(
            "UPDATE users SET coins=coins+? WHERE id=?", (MEETUP_REWARD, row["user_id"])
        )
        conn.execute(
            "UPDATE meetup_submissions SET status='approved', coins_awarded=? WHERE id=?",
            (MEETUP_REWARD, sid),
        )
        conn.commit()
        conn.close()
        await context.bot.send_message(
            row["telegram_id"],
            f"✅ Meetup «{row['title']}» — +{MEETUP_REWARD}⚡",
        )
        await q.message.reply_text(T(lang, "approved"))
    else:
        conn.execute(
            "UPDATE meetup_submissions SET status='rejected' WHERE id=?", (sid,)
        )
        conn.commit()
        conn.close()
        await context.bot.send_message(
            row["telegram_id"],
            f"❌ Meetup «{row['title']}»",
        )
        await q.message.reply_text(T(lang, "rejected"))


# =========================
# LEADERS / VOLUNTEERS
# =========================
async def leaders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = gl(update, context)
    conn = db()
    top_u = conn.execute(
        """
        SELECT full_name, coins FROM users
        WHERE role='volunteer'
        ORDER BY coins DESC, full_name ASC
        LIMIT 10
    """
    ).fetchall()
    top_d = conn.execute(
        """
        SELECT district, COUNT(*) AS active_cnt
        FROM (
            SELECT DISTINCT district, id FROM users
            WHERE role='volunteer' AND COALESCE(district,'')<>'' AND coins>0
        )
        GROUP BY district
        ORDER BY active_cnt DESC, district ASC
        LIMIT 10
    """
    ).fetchall()
    me = conn.execute(
        "SELECT coins, district FROM users WHERE telegram_id=?",
        (update.effective_user.id,),
    ).fetchone()
    conn.close()

    lines = [T(lang, "leaders_title")]
    lines.append("\n" + T(lang, "leaders_top_vols"))
    if top_u:
        for i, r in enumerate(top_u, 1):
            lines.append(f"{i}. {r['full_name']} — {r['coins']}⚡")
    else:
        lines.append(T(lang, "leaders_empty"))

    lines.append("\n" + T(lang, "leaders_top_dist"))
    if top_d:
        for i, r in enumerate(top_d, 1):
            lines.append(f"{i}. {r['district']} — {r['active_cnt']}")
    else:
        lines.append(T(lang, "leaders_empty"))

    if me:
        lines.append("\n" + T(lang, "leaders_personal"))
        lines.append(T(lang, "your_balance").format(coins=me["coins"]))
        if me["district"]:
            lines.append(T(lang, "your_district").format(district=me["district"]))

    await update.message.reply_text(
        "\n".join(lines),
        parse_mode="HTML",
        reply_markup=get_main_keyboard(lang),
    )


async def volunteers_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = gl(update, context)
    if not is_admin(update.effective_user.id):
        await update.message.reply_text(T(lang, "no_rights"))
        return
    conn = db()
    rows = conn.execute(
        "SELECT id, full_name FROM users WHERE role='volunteer' ORDER BY full_name"
    ).fetchall()
    conn.close()
    if not rows:
        await update.message.reply_text(T(lang, "volunteers_empty"))
        return
    kb = [
        [InlineKeyboardButton(r["full_name"], callback_data=f"vol_{r['id']}")]
        for r in rows
    ]
    await update.message.reply_text("👥", reply_markup=InlineKeyboardMarkup(kb))


async def volunteer_card(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    lang = gl(update, context)
    uid = int(q.data.split("_")[1])
    conn = db()
    r = conn.execute(
        """
        SELECT full_name, age, study_place, region, district, coins, photo_file_id
        FROM users WHERE id=?
    """,
        (uid,),
    ).fetchone()
    conn.close()
    if not r:
        await q.message.reply_text(T(lang, "not_found"))
        return
    if lang == "ru":
        text = (
            f"👤 {r['full_name']}\n"
            f"Возраст: {r['age']}\n"
            f"Учёба: {r['study_place']}\n"
            f"Регион: {r['region']}\n"
            f"Район: {r['district']}\n"
            f"Баланс: {r['coins']}⚡"
        )
    else:
        text = (
            f"👤 {r['full_name']}\n"
            f"Yosh: {r['age']}\n"
            f"O‘qish: {r['study_place']}\n"
            f"Viloyat: {r['region']}\n"
            f"Tuman: {r['district']}\n"
            f"Balans: {r['coins']}⚡"
        )
    if r["photo_file_id"]:
        try:
            await q.message.reply_photo(photo=r["photo_file_id"], caption=text)
        except Exception:
            await q.message.reply_text(text)
    else:
        await q.message.reply_text(text)


# =========================
# ADMIN TASK MANAGEMENT
# =========================
ADM_LEVEL, ADM_LIST, ADM_NEW_TITLE, ADM_NEW_DESC, ADM_NEW_REWARD, ADM_EDIT_FIELD, ADM_EDIT_VALUE = range(
    500, 507
)


async def admin_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = gl(update, context)
    if not is_admin(update.effective_user.id):
        await update.message.reply_text(T(lang, "no_rights"))
        return ConversationHandler.END
    kb = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🟢 Easy", callback_data="adm_level_easy")],
            [InlineKeyboardButton("🟡 Medium", callback_data="adm_level_medium")],
            [InlineKeyboardButton("🔴 Hard", callback_data="adm_level_hard")],
        ]
    )
    await update.message.reply_text(
        T(lang, "menu_levels"), reply_markup=kb
    )
    return ADM_LEVEL


async def admin_pick_level(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    lang = gl(update, context)
    level = q.data.split("_")[2]
    context.user_data["adm_level"] = level
    conn = db()
    rows = conn.execute(
        "SELECT id, title, coin_reward, is_active FROM tasks WHERE level=? ORDER BY id",
        (level,),
    ).fetchall()
    conn.close()
    btns = []
    for r in rows:
        prefix = "🟢" if r["is_active"] == 1 else "🔴"
        btns.append(
            [
                InlineKeyboardButton(
                    f"{prefix} {r['title']} (+{r['coin_reward']}⚡)",
                    callback_data=f"adm_edit_{r['id']}",
                )
            ]
        )
    btns.append([InlineKeyboardButton("➕", callback_data="adm_add_new")])
    await q.message.reply_text(
        T(lang, "choose_task"), reply_markup=InlineKeyboardMarkup(btns)
    )
    return ADM_LIST


async def admin_add_new(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    await q.message.reply_text("✏️ Title:")
    return ADM_NEW_TITLE


async def admin_new_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["new_title"] = update.message.text.strip()
    await update.message.reply_text("📖 Description (или '-' / yoki '-')")
    return ADM_NEW_DESC


async def admin_new_desc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    d = update.message.text.strip()
    context.user_data["new_desc"] = "" if d == "-" else d
    await update.message.reply_text("💰 Reward (coins):")
    return ADM_NEW_REWARD


async def admin_new_reward(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = gl(update, context)
    try:
        reward = int(update.message.text.strip())
    except Exception:
        if lang == "ru":
            await update.message.reply_text("Введите число")
        else:
            await update.message.reply_text("Son kiriting")
        return ADM_NEW_REWARD
    conn = db()
    conn.execute(
        """
        INSERT INTO tasks (title, description, coin_reward, level, is_active)
        VALUES (?, ?, ?, ?, 1)
    """,
        (
            context.user_data["new_title"],
            context.user_data["new_desc"],
            reward,
            context.user_data["adm_level"],
        ),
    )
    conn.commit()
    conn.close()
    await update.message.reply_text(T(lang, "added"))
    return ConversationHandler.END


async def admin_edit_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    lang = gl(update, context)
    tid = int(q.data.split("_")[2])
    context.user_data["edit_tid"] = tid
    kb = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✏ Название", callback_data="field_title"),
                InlineKeyboardButton("📖 Описание", callback_data="field_desc"),
            ],
            [InlineKeyboardButton("💰 Награда", callback_data="field_reward")],
            [InlineKeyboardButton("⚡ Вкл/Выкл", callback_data="field_status")],
        ]
    )
    await q.message.reply_text(T(lang, "what_change"), reply_markup=kb)
    return ADM_EDIT_FIELD


async def admin_edit_field(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    lang = gl(update, context)
    field = q.data.split("_")[1]
    tid = context.user_data["edit_tid"]
    if field == "status":
        conn = db()
        row = conn.execute(
            "SELECT is_active FROM tasks WHERE id=?", (tid,)
        ).fetchone()
        if not row:
            conn.close()
            await q.message.reply_text(T(lang, "not_found"))
            return ConversationHandler.END
        new_val = 0 if row["is_active"] == 1 else 1
        conn.execute("UPDATE tasks SET is_active=? WHERE id=?", (new_val, tid))
        conn.commit()
        conn.close()
        await q.message.reply_text(T(lang, "status_changed"))
        return ConversationHandler.END
    context.user_data["edit_field"] = field
    await q.message.reply_text("Новое значение / Yangi qiymat:")
    return ADM_EDIT_VALUE


async def admin_edit_value(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = gl(update, context)
    tid = context.user_data["edit_tid"]
    field = context.user_data["edit_field"]
    val = update.message.text.strip()
    if field == "reward":
        try:
            val = int(val)
        except Exception:
            if lang == "ru":
                await update.message.reply_text("Введите число")
            else:
                await update.message.reply_text("Son kiriting")
            return ADM_EDIT_VALUE
    mapping = {"title": "title", "desc": "description", "reward": "coin_reward"}
    conn = db()
    conn.execute(
        f"UPDATE tasks SET {mapping[field]}=? WHERE id=?", (val, tid)
    )
    conn.commit()
    conn.close()
    await update.message.reply_text(T(lang, "updated"))
    return ConversationHandler.END


# =========================
# REWARDS / SHOP
# =========================
async def rewards_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = gl(update, context)
    conn = db()
    row = conn.execute(
        "SELECT id, full_name, coins FROM users WHERE telegram_id=?",
        (update.effective_user.id,),
    ).fetchone()
    conn.close()
    if not row:
        await update.message.reply_text(T(lang, "profile_unreg"))
        return
    text_lines = [
        T(lang, "rewards_title"),
        "",
        T(lang, "your_balance").format(coins=row["coins"]),
        "",
    ]
    kb_rows: List[List[InlineKeyboardButton]] = []
    for r in REWARDS:
        name = r["name_ru"] if lang == "ru" else r["name_uz"]
        label = f"{name} — {r['cost']}⚡"
        kb_rows.append(
            [InlineKeyboardButton(label, callback_data=f"rw_{r['id']}")]
        )
    await update.message.reply_text(
        "\n".join(text_lines),
        reply_markup=InlineKeyboardMarkup(kb_rows),
    )


async def rewards_select(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    lang = gl(update, context)
    rid = int(q.data.split("_")[1])
    reward = None
    for r in REWARDS:
        if r["id"] == rid:
            reward = r
            break
    if not reward:
        await q.message.reply_text(T(lang, "not_found"))
        return
    conn = db()
    row = conn.execute(
        "SELECT id, full_name, coins FROM users WHERE telegram_id=?",
        (q.from_user.id,),
    ).fetchone()
    if not row:
        conn.close()
        await q.message.reply_text(T(lang, "profile_unreg"))
        return
    if row["coins"] < reward["cost"]:
        conn.close()
        await q.message.reply_text(T(lang, "rewards_not_enough"))
        return
    new_coins = row["coins"] - reward["cost"]
    conn.execute(
        "UPDATE users SET coins=? WHERE id=?", (new_coins, row["id"])
    )
    name = reward["name_ru"] if lang == "ru" else reward["name_uz"]
    conn.execute(
        "INSERT INTO rewards (user_id, reward_name, coin_cost) VALUES (?, ?, ?)",
        (row["id"], name, reward["cost"]),
    )
    conn.commit()
    conn.close()
    await q.message.reply_text(
        T(lang, "rewards_ok_user").format(name=name, cost=reward["cost"])
    )
    # уведомление админам (по-русски)
    for aid in ADMIN_IDS:
        try:
            await context.bot.send_message(
                aid,
                T("ru", "rewards_request_admin").format(
                    full_name=row["full_name"],
                    coins=new_coins,
                    name=name,
                    cost=reward["cost"],
                ),
            )
        except Exception:
            pass


# =========================
# HELP & MAIN MENU BUTTONS
# =========================
async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = gl(update, context)
    await update.message.reply_text(T(lang, "help"))


async def handle_menu_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    lang = gl(update, context)

    if text in ("👤 Мой профиль", "👤 Mening profilim"):
        await profile(update, context)
    elif text in ("🌿 О проекте", "🌿 Loyiha haqida"):
        await about_cmd(update, context)
    elif text in ("📅 Офлайн встречи", "📅 Oflayn tadbirlar"):
        await meetups_list(update, context)
    elif text in ("🎯 Задания", "🎯 Topshiriqlar"):
        await show_tasks(update, context)
    elif text in ("💰 Мои баллы", "💰 Ballarim"):
        await leaders(update, context)
    elif text in ("🎁 Обмен баллов", "🎁 Ballarni almashtirish"):
        await rewards_cmd(update, context)
    else:
        # игнорируем, чтобы не ломать диалоги
        return


# =========================
# BUILD & RUN
# =========================
def build_application() -> Application:
    ensure_schema()
    app = Application.builder().token(TOKEN).build()

    # REGISTRATION
    reg_conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            LANG_CHOICE: [
                CallbackQueryHandler(set_language, pattern="^lang_"),
                MessageHandler(filters.COMMAND, cancel),
            ],
            REG_NAME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, reg_name),
                MessageHandler(filters.COMMAND, cancel),
            ],
            REG_AGE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, reg_age),
                MessageHandler(filters.COMMAND, cancel),
            ],
            REG_STUDY: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, reg_study),
                MessageHandler(filters.COMMAND, cancel),
            ],
            REG_REGION: [
                CallbackQueryHandler(reg_region, pattern="^reg_region_"),
                MessageHandler(filters.COMMAND, cancel),
            ],
            REG_DISTRICT: [
                CallbackQueryHandler(reg_district, pattern="^reg_dist_"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, reg_district),
                MessageHandler(filters.COMMAND, cancel),
            ],
            REG_CONSENT: [
                CallbackQueryHandler(
                    reg_consent, pattern="^consent_(yes|no)$"
                ),
                MessageHandler(filters.COMMAND, cancel),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )
    app.add_handler(reg_conv)

    # PROFILE / BALANCE / ABOUT / MENU / HELP
    app.add_handler(CommandHandler("profile", profile))
    app.add_handler(CommandHandler("balance", balance))
    app.add_handler(CommandHandler("about", about_cmd))
    app.add_handler(CommandHandler("menu", menu_cmd))
    app.add_handler(CommandHandler("help", help_cmd))
    # ADMIN: список записавшихся на встречу
    app.add_handler(CommandHandler("meetup_members", meetup_members_start))
    app.add_handler(CallbackQueryHandler(meetup_members_show, pattern="^mm_"))
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_menu_buttons)
    )
    app.add_handler(CommandHandler("trashmap", trashmap_cmd))
    # TASKS
    task_conv = ConversationHandler(
        entry_points=[CommandHandler("tasks", show_tasks)],
        states={
            TASK_LEVEL: [
                CallbackQueryHandler(list_tasks_by_level, pattern="^level_"),
                MessageHandler(filters.COMMAND, cancel),
            ],
            TASK_LIST: [
                CallbackQueryHandler(
                    choose_task_submit, pattern="^submit_task_"

                ),
                CallbackQueryHandler(ignore_button, pattern="^ignore$"),
                CallbackQueryHandler(list_tasks_by_level, pattern="^back_levels$"),

                CallbackQueryHandler(ignore_button, pattern="^ignore$"),
                MessageHandler(filters.COMMAND, cancel),
            ],
            TASK_SUBMIT: [
                MessageHandler(filters.PHOTO, save_submission),
                MessageHandler(filters.COMMAND, cancel),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )
    # TASKS (новые handlers, без ConversationHandler)
    app.add_handler(CommandHandler("tasks", show_tasks))
    app.add_handler(CallbackQueryHandler(list_tasks_by_level, pattern="^level_"))
    app.add_handler(CallbackQueryHandler(choose_task_submit, pattern="^submit_task_"))
    app.add_handler(CallbackQueryHandler(ignore_button, pattern="^ignore$"))
    app.add_handler(CallbackQueryHandler(list_tasks_by_level, pattern="^back_levels$"))

    # Фото
    app.add_handler(MessageHandler(filters.PHOTO, save_submission))

    # ADMIN: submissions
    app.add_handler(CommandHandler("submissions", submissions_list))
    app.add_handler(
        CallbackQueryHandler(submission_review, pattern="^(sub_ok_|sub_no_)")
    )

    # MEETUPS: create
    addmeetup_conv = ConversationHandler(
        entry_points=[CommandHandler("addmeetup", addmeetup_start)],
        states={
            ADD_TITLE: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND, addmeetup_title
                ),
                MessageHandler(filters.COMMAND, cancel),    
            ],
            ADD_DESC: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, addmeetup_desc),
                MessageHandler(filters.COMMAND, cancel),
            ],
            ADD_DATE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, addmeetup_date),
                MessageHandler(filters.COMMAND, cancel),
            ],
            ADD_LOC: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, addmeetup_loc),
                MessageHandler(filters.COMMAND, cancel),
            ],
            ADD_LIMIT: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND, addmeetup_limit
                ),
                MessageHandler(filters.COMMAND, cancel),
            ],
            ADD_PHOTO: [
                MessageHandler(filters.PHOTO, addmeetup_photo),
                MessageHandler(filters.COMMAND, cancel),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )
    app.add_handler(addmeetup_conv)

    # MEETUPS: list & register
    app.add_handler(CommandHandler("meetups", meetups_list))
    app.add_handler(CallbackQueryHandler(meetup_register, pattern="^mreg_"))

    # MEETUPS: submit photos
    meetup_submit_conv = ConversationHandler(
        entry_points=[CommandHandler("meetup_submit", meetup_submit_start)],
        states={
            MS_CHOOSE: [
                CallbackQueryHandler(
                    meetup_submit_choose, pattern="^msel_"
                ),
                MessageHandler(filters.COMMAND, cancel),
            ],
            MS_GROUP_PHOTO: [
                MessageHandler(
                    filters.PHOTO, meetup_submit_group
                ),
                MessageHandler(filters.COMMAND, cancel),
            ],
            MS_SELFIE: [
                MessageHandler(filters.PHOTO, meetup_submit_selfie),
                MessageHandler(filters.COMMAND, cancel),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )
    app.add_handler(meetup_submit_conv)

    app.add_handler(CommandHandler("meetup_submissions", meetup_submissions_list))
    app.add_handler(CallbackQueryHandler(meetup_review, pattern="^(ms_ok_|ms_no_)"))

    # VOLUNTEERS (admin)
    app.add_handler(CommandHandler("volunteers", volunteers_admin))
    app.add_handler(CallbackQueryHandler(volunteer_card, pattern="^vol_"))

    # ADMIN TASKS
    admin_tasks_conv = ConversationHandler(
        entry_points=[CommandHandler("admin_tasks", admin_tasks)],
        states={
            ADM_LEVEL: [
                CallbackQueryHandler(admin_pick_level, pattern="^adm_level_"),
                MessageHandler(filters.COMMAND, cancel),
            ],
            ADM_LIST: [
                CallbackQueryHandler(admin_add_new, pattern="^adm_add_new$"),
                CallbackQueryHandler(admin_edit_task, pattern="^adm_edit_"),
                MessageHandler(filters.COMMAND, cancel),
            ],
            ADM_NEW_TITLE: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND, admin_new_title
                ),
                MessageHandler(filters.COMMAND, cancel),
            ],
            ADM_NEW_DESC: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND, admin_new_desc
                ),
                MessageHandler(filters.COMMAND, cancel),
            ],
            ADM_NEW_REWARD: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND, admin_new_reward
                ),
                MessageHandler(filters.COMMAND, cancel),
            ],
            ADM_EDIT_FIELD: [
                CallbackQueryHandler(
                    admin_edit_field,
                    pattern="^field_(title|desc|status|reward)$",
                ),
                MessageHandler(filters.COMMAND, cancel),
            ],
            ADM_EDIT_VALUE: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND, admin_edit_value
                ),
                MessageHandler(filters.COMMAND, cancel),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )
    app.add_handler(admin_tasks_conv)

    # REWARDS
    app.add_handler(CommandHandler("rewards", rewards_cmd))
    app.add_handler(CallbackQueryHandler(rewards_select, pattern="^rw_"))

    # MAIN MENU BUTTONS
    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            handle_menu_buttons,
        )
    )

    return app


if __name__ == "__main__":
    app = build_application()
    logger.info("✅ Бот запускается…")
    app.run_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)
