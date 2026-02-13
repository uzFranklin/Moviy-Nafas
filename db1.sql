-- Таблица пользователей
CREATE TABLE users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_id INTEGER UNIQUE NOT NULL,
    name TEXT NOT NULL,
    role TEXT CHECK(role IN ('volunteer', 'admin')) NOT NULL,
    coins INTEGER DEFAULT 0,
    full_name TEXT,
    age INTEGER,
    study_place TEXT,
    city TEXT
);

-- Таблица заданий
CREATE TABLE tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    description TEXT,
    coin_reward INTEGER NOT NULL,
    is_active BOOLEAN DEFAULT 1
);

-- Таблица выполненных заданий
CREATE TABLE submissions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    task_id INTEGER NOT NULL,
    photo_path TEXT,
    status TEXT CHECK(status IN ('pending', 'approved', 'rejected')) DEFAULT 'pending',
    submission_date TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(user_id) REFERENCES users(id),
    FOREIGN KEY(task_id) REFERENCES tasks(id)
);

-- Таблица наград и обменов
CREATE TABLE rewards (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    reward_name TEXT NOT NULL,
    coin_cost INTEGER NOT NULL,
    exchange_date TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(user_id) REFERENCES users(id)
);
