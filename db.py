import os
import sqlite3
import time

DB_PATH = os.getenv("DB_PATH", os.path.join(os.path.dirname(os.path.abspath(__file__)), "groupshelp.db"))

_conn = sqlite3.connect(DB_PATH, check_same_thread=False)
_conn.row_factory = sqlite3.Row
_conn.executescript(
    """
    CREATE TABLE IF NOT EXISTS accounts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        phone TEXT UNIQUE NOT NULL,
        user_id INTEGER,
        name TEXT,
        session TEXT NOT NULL,
        active INTEGER NOT NULL DEFAULT 1,
        added_at INTEGER NOT NULL
    );
    CREATE TABLE IF NOT EXISTS ads (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        text TEXT NOT NULL,
        added_at INTEGER NOT NULL
    );
    CREATE TABLE IF NOT EXISTS groups (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        chat_id INTEGER UNIQUE NOT NULL,
        title TEXT,
        link TEXT,
        added_at INTEGER NOT NULL
    );
    """
)
_conn.commit()
try:
    os.chmod(DB_PATH, 0o600)
except OSError:
    pass


def _all(q, args=()):
    return [dict(r) for r in _conn.execute(q, args).fetchall()]


def _one(q, args=()):
    r = _conn.execute(q, args).fetchone()
    return dict(r) if r else None


def _run(q, args=()):
    cur = _conn.execute(q, args)
    _conn.commit()
    return cur


# accounts
def save_account(phone, user_id, name, session):
    _run(
        "INSERT INTO accounts (phone, user_id, name, session, active, added_at) VALUES (?, ?, ?, ?, 1, ?) "
        "ON CONFLICT(phone) DO UPDATE SET user_id=excluded.user_id, name=excluded.name, "
        "session=excluded.session, active=1",
        (phone, user_id, name, session, int(time.time())),
    )
    return _one("SELECT * FROM accounts WHERE phone=?", (phone,))


def list_accounts(active_only=False):
    q = "SELECT * FROM accounts" + (" WHERE active=1" if active_only else "") + " ORDER BY id"
    return _all(q)


def get_account(acc_id):
    return _one("SELECT * FROM accounts WHERE id=?", (acc_id,))


def set_account_active(acc_id, active):
    _run("UPDATE accounts SET active=? WHERE id=?", (1 if active else 0, acc_id))


def delete_account(acc_id):
    _run("DELETE FROM accounts WHERE id=?", (acc_id,))


# ads
def add_ad(text):
    return _run("INSERT INTO ads (text, added_at) VALUES (?, ?)", (text, int(time.time()))).lastrowid


def list_ads():
    return _all("SELECT * FROM ads ORDER BY id")


def delete_ad(ad_id):
    return _run("DELETE FROM ads WHERE id=?", (ad_id,)).rowcount


# groups
def save_group(chat_id, title, link):
    _run(
        "INSERT INTO groups (chat_id, title, link, added_at) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(chat_id) DO UPDATE SET title=excluded.title, link=COALESCE(excluded.link, groups.link)",
        (chat_id, title, link, int(time.time())),
    )
    return _one("SELECT * FROM groups WHERE chat_id=?", (chat_id,))


def list_groups():
    return _all("SELECT * FROM groups ORDER BY id")


def get_group(group_id):
    return _one("SELECT * FROM groups WHERE id=?", (group_id,))


def delete_group(group_id):
    return _run("DELETE FROM groups WHERE id=?", (group_id,)).rowcount
