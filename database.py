import os
import csv
import io
from datetime import datetime, timedelta
from typing import Optional, List, Dict

try:
    import psycopg2
    HAS_PSYCOPG2 = True
except ImportError:
    HAS_PSYCOPG2 = False

try:
    import sqlite3
    HAS_SQLITE = True
except ImportError:
    HAS_SQLITE = False

DATABASE_URL = os.environ.get("DATABASE_URL", "")


def get_conn():
    if DATABASE_URL and HAS_PSYCOPG2:
        conn = psycopg2.connect(DATABASE_URL, connect_timeout=10)
        conn.autocommit = False
        return conn
    elif HAS_SQLITE:
        DB_PATH = os.path.join(os.path.expanduser("~"), "Downloads", "pvt", "bot_data.db")
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn
    else:
        raise RuntimeError("No database driver available. Install psycopg2-binary or sqlite3.")


def init_db():
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id BIGINT PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            last_name TEXT,
            is_admin INTEGER DEFAULT 0,
            is_banned INTEGER DEFAULT 0,
            downloads_today INTEGER DEFAULT 0,
            last_download_date TEXT,
            premium_until TEXT,
            download_limit INTEGER DEFAULT 20,
            created_at TEXT
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS downloads (
            id SERIAL PRIMARY KEY,
            user_id BIGINT,
            url TEXT,
            platform TEXT,
            status TEXT,
            created_at TEXT,
            FOREIGN KEY(user_id) REFERENCES users(user_id)
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS queue (
            id SERIAL PRIMARY KEY,
            user_id BIGINT,
            chat_id BIGINT,
            url TEXT,
            status TEXT DEFAULT 'pending',
            created_at TEXT,
            processed_at TEXT
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS bot_settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS required_channels (
            id SERIAL PRIMARY KEY,
            channel_username TEXT,
            channel_id TEXT,
            is_required INTEGER DEFAULT 1,
            created_at TEXT
        )
    """)
    conn.commit()
    conn.close()


def ensure_user(user_id: int, username: str = "", first_name: str = "", last_name: str = ""):
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT user_id, is_admin FROM users WHERE user_id = %s", (user_id,))
    row = c.fetchone()
    if not row:
        c.execute(
            "INSERT INTO users (user_id, username, first_name, last_name, created_at) VALUES (%s, %s, %s, %s, %s)",
            (user_id, username, first_name, last_name, datetime.now().isoformat()),
        )
        conn.commit()
    conn.close()


def is_admin(user_id: int) -> bool:
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT is_admin FROM users WHERE user_id = %s", (user_id,))
    row = c.fetchone()
    conn.close()
    return bool(row and row[0])


def set_admin(user_id: int, admin: bool = True):
    conn = get_conn()
    c = conn.cursor()
    c.execute("UPDATE users SET is_admin = %s WHERE user_id = %s", (1 if admin else 0, user_id))
    conn.commit()
    conn.close()


def is_banned(user_id: int) -> bool:
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT is_banned FROM users WHERE user_id = %s", (user_id,))
    row = c.fetchone()
    conn.close()
    return bool(row and row[0])


def ban_user(user_id: int, banned: bool = True):
    conn = get_conn()
    c = conn.cursor()
    c.execute("UPDATE users SET is_banned = %s WHERE user_id = %s", (1 if banned else 0, user_id))
    conn.commit()
    conn.close()


def increment_download(user_id: int):
    today = datetime.now().strftime("%Y-%m-%d")
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        "UPDATE users SET downloads_today = downloads_today + 1, last_download_date = %s WHERE user_id = %s",
        (today, user_id),
    )
    conn.commit()
    conn.close()


def add_download_record(user_id: int, url: str, platform: str, status: str):
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        "INSERT INTO downloads (user_id, url, platform, status, created_at) VALUES (%s, %s, %s, %s, %s)",
        (user_id, url, platform, status, datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()


def add_to_queue(user_id: int, chat_id: int, url: str):
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        "INSERT INTO queue (user_id, chat_id, url, status, created_at) VALUES (%s, %s, %s, %s, %s)",
        (user_id, chat_id, url, "pending", datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()


def get_next_queue_item():
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT * FROM queue WHERE status = 'pending' ORDER BY id ASC LIMIT 1")
    row = c.fetchone()
    conn.close()
    if row:
        columns = [desc[0] for desc in c.description]
        return dict(zip(columns, row))
    return None


def mark_queue_item_processed(queue_id: int, status: str = "completed"):
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        "UPDATE queue SET status = %s, processed_at = %s WHERE id = %s",
        (status, datetime.now().isoformat(), queue_id),
    )
    conn.commit()
    conn.close()


def get_stats():
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM users")
    total_users = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM downloads")
    total_downloads = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM downloads WHERE date(created_at) = date('now')")
    today_downloads = c.fetchone()[0]
    conn.close()
    return {
        "total_users": total_users,
        "total_downloads": total_downloads,
        "today_downloads": today_downloads,
    }


def get_all_users():
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT user_id, username, first_name, is_admin, is_banned, downloads_today FROM users")
    rows = c.fetchall()
    conn.close()
    columns = [desc[0] for desc in c.description]
    return [dict(zip(columns, row)) for row in rows]


def get_all_users_with_details() -> List[Dict]:
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT * FROM users")
    rows = c.fetchall()
    conn.close()
    columns = [desc[0] for desc in c.description]
    return [dict(zip(columns, row)) for row in rows]


def get_banned_users():
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT user_id, username, first_name FROM users WHERE is_banned = 1")
    rows = c.fetchall()
    conn.close()
    columns = [desc[0] for desc in c.description]
    return [dict(zip(columns, row)) for row in rows]


def get_recent_downloads(limit: int = 20):
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        "SELECT * FROM downloads ORDER BY created_at DESC LIMIT %s",
        (limit,),
    )
    rows = c.fetchall()
    conn.close()
    columns = [desc[0] for desc in c.description]
    return [dict(zip(columns, row)) for row in rows]


def clear_old_downloads(days: int = 30):
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()
    conn = get_conn()
    c = conn.cursor()
    c.execute("DELETE FROM downloads WHERE created_at < %s", (cutoff,))
    deleted = c.rowcount
    conn.commit()
    conn.close()
    return deleted


def set_maintenance(enabled: bool):
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS bot_settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    c.execute(
        "INSERT INTO bot_settings (key, value) VALUES (%s, %s) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
        ("maintenance", "1" if enabled else "0"),
    )
    conn.commit()
    conn.close()


def is_maintenance():
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT value FROM bot_settings WHERE key = 'maintenance'")
    row = c.fetchone()
    conn.close()
    return bool(row and row[0] == "1")


def get_setting(key: str, default: str = "") -> str:
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT value FROM bot_settings WHERE key = %s", (key,))
    row = c.fetchone()
    conn.close()
    return row[0] if row else default


def set_setting(key: str, value: str):
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        "INSERT INTO bot_settings (key, value) VALUES (%s, %s) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
        (key, value),
    )
    conn.commit()
    conn.close()


def get_user_details(user_id: int) -> Optional[Dict]:
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT * FROM users WHERE user_id = %s", (user_id,))
    row = c.fetchone()
    conn.close()
    if row:
        columns = [desc[0] for desc in c.description]
        return dict(zip(columns, row))
    return None


def get_user_downloads(user_id: int, limit: int = 20) -> List[Dict]:
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        "SELECT * FROM downloads WHERE user_id = %s ORDER BY created_at DESC LIMIT %s",
        (user_id, limit),
    )
    rows = c.fetchall()
    conn.close()
    columns = [desc[0] for desc in c.description]
    return [dict(zip(columns, row)) for row in rows]


def get_analytics(days: int = 7) -> Dict:
    conn = get_conn()
    c = conn.cursor()
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()

    c.execute("SELECT COUNT(*) FROM users WHERE created_at >= %s", (cutoff,))
    new_users = c.fetchone()[0]

    c.execute("SELECT COUNT(*) FROM downloads WHERE created_at >= %s", (cutoff,))
    total_downloads = c.fetchone()[0]

    c.execute(
        "SELECT COUNT(*) FROM downloads WHERE created_at >= %s AND status = 'completed'",
        (cutoff,),
    )
    completed = c.fetchone()[0]

    c.execute(
        "SELECT COUNT(*) FROM downloads WHERE created_at >= %s AND status = 'failed'",
        (cutoff,),
    )
    failed = c.fetchone()[0]

    c.execute("SELECT platform, COUNT(*) as cnt FROM downloads WHERE created_at >= %s GROUP BY platform", (cutoff,))
    platforms = [dict(zip([desc[0] for desc in c.description], row)) for row in c.fetchall()]

    c.execute(
        """
        SELECT date(created_at) as day, COUNT(*) as cnt
        FROM downloads
        WHERE created_at >= %s
        GROUP BY date(created_at)
        ORDER BY day DESC
        LIMIT 7
        """,
        (cutoff,),
    )
    daily = [dict(zip([desc[0] for desc in c.description], row)) for row in c.fetchall()]

    conn.close()
    return {
        "days": days,
        "new_users": new_users,
        "total_downloads": total_downloads,
        "completed": completed,
        "failed": failed,
        "success_rate": round((completed / total_downloads * 100) if total_downloads else 0, 1),
        "platforms": platforms,
        "daily": daily,
    }


def get_queue_status() -> Dict:
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM queue WHERE status = 'pending'")
    pending = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM queue WHERE status = 'completed'")
    completed = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM queue WHERE status = 'failed'")
    failed = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM queue")
    total = c.fetchone()[0]
    conn.close()
    return {"pending": pending, "completed": completed, "failed": failed, "total": total}


def clear_queue():
    conn = get_conn()
    c = conn.cursor()
    c.execute("DELETE FROM queue WHERE status = 'pending'")
    deleted = c.rowcount
    conn.commit()
    conn.close()
    return deleted


def add_required_channel(channel_username: str, channel_id: str = ""):
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        "INSERT INTO required_channels (channel_username, channel_id, created_at) VALUES (%s, %s, %s)",
        (channel_username, channel_id, datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()


def get_required_channels() -> List[Dict]:
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT * FROM required_channels WHERE is_required = 1")
    rows = c.fetchall()
    conn.close()
    columns = [desc[0] for desc in c.description]
    return [dict(zip(columns, row)) for row in rows]


def remove_required_channel(channel_id: int):
    conn = get_conn()
    c = conn.cursor()
    c.execute("DELETE FROM required_channels WHERE id = %s", (channel_id,))
    conn.commit()
    conn.close()


def set_premium(user_id: int, days: int = 30):
    premium_until = (datetime.now() + timedelta(days=days)).isoformat()
    conn = get_conn()
    c = conn.cursor()
    c.execute("UPDATE users SET premium_until = %s WHERE user_id = %s", (premium_until, user_id))
    conn.commit()
    conn.close()


def remove_premium(user_id: int):
    conn = get_conn()
    c = conn.cursor()
    c.execute("UPDATE users SET premium_until = NULL WHERE user_id = %s", (user_id,))
    conn.commit()
    conn.close()


def get_premium_users() -> List[Dict]:
    conn = get_conn()
    c = conn.cursor()
    now = datetime.now().isoformat()
    c.execute(
        "SELECT user_id, username, first_name, premium_until FROM users WHERE premium_until IS NOT NULL AND premium_until > %s",
        (now,),
    )
    rows = c.fetchall()
    conn.close()
    columns = [desc[0] for desc in c.description]
    return [dict(zip(columns, row)) for row in rows]


def is_premium(user_id: int) -> bool:
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT premium_until FROM users WHERE user_id = %s", (user_id,))
    row = c.fetchone()
    conn.close()
    if not row or not row[0]:
        return False
    return datetime.fromisoformat(row[0]) > datetime.now()


def set_user_download_limit(user_id: int, limit: int):
    conn = get_conn()
    c = conn.cursor()
    c.execute("UPDATE users SET download_limit = %s WHERE user_id = %s", (limit, user_id))
    conn.commit()
    conn.close()


def get_user_download_limit(user_id: int) -> int:
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT download_limit FROM users WHERE user_id = %s", (user_id,))
    row = c.fetchone()
    conn.close()
    return row[0] if row else 20


def get_top_users(limit: int = 10) -> List[Dict]:
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        """
        SELECT user_id, username, first_name, downloads_today, COUNT(d.id) as total_downloads
        FROM users u
        LEFT JOIN downloads d ON u.user_id = d.user_id
        GROUP BY u.user_id
        ORDER BY total_downloads DESC
        LIMIT %s
        """,
        (limit,),
    )
    rows = c.fetchall()
    conn.close()
    columns = [desc[0] for desc in c.description]
    return [dict(zip(columns, row)) for row in rows]


def get_platform_stats() -> Dict:
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT platform, COUNT(*) as count, COUNT(DISTINCT user_id) as unique_users FROM downloads GROUP BY platform")
    rows = c.fetchall()
    conn.close()
    columns = [desc[0] for desc in c.description]
    return [dict(zip(columns, row)) for row in rows]


def backup_database() -> str:
    return "Supabase backup: Use Supabase dashboard to export your database."
