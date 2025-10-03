from flask import Flask, render_template, request, session, send_from_directory
from flask_socketio import SocketIO, emit
from flask_cors import CORS
import sqlite3
import os
import html
import hashlib
import threading
import datetime

# For eventlet monkey patching (required for Flask-SocketIO with eventlet)
import eventlet
eventlet.monkey_patch()

app = Flask(__name__)
CORS(app)  # Allow CORS for all domains (optional, but often needed on Render)
app.secret_key = "your_secret_key"  # needed for session
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="eventlet")  # Use eventlet for production

DB_FILE = "messages.db"
MAX_MESSAGES = 100  # Limit messages sent to new users

# Initialize DB
if not os.path.exists(DB_FILE):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''CREATE TABLE messages
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  nickname TEXT,
                  decoration TEXT,
                  message TEXT,
                  timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)''')
    c.execute('''CREATE TABLE users
                 (nickname TEXT PRIMARY KEY,
                  key_hash TEXT,
                  decoration TEXT)''')
    conn.commit()
    conn.close()
else:
    # Add users table if missing (for upgrades)
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='users'")
    if not c.fetchone():
        c.execute('''CREATE TABLE users
                     (nickname TEXT PRIMARY KEY,
                      key_hash TEXT,
                      decoration TEXT)''')
        conn.commit()
    conn.close()

# Add avatar to users table if missing
def ensure_avatar_column():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("PRAGMA table_info(users)")
    columns = [row[1] for row in c.fetchall()]
    if 'avatar' not in columns:
        c.execute("ALTER TABLE users ADD COLUMN avatar TEXT")
        conn.commit()
    conn.close()
ensure_avatar_column()

# Add reactions table if missing
def ensure_reactions_table():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='reactions'")
    if not c.fetchone():
        c.execute('''CREATE TABLE reactions
                     (msg_id INTEGER, count INTEGER DEFAULT 0, PRIMARY KEY(msg_id))''')
        conn.commit()
    conn.close()
ensure_reactions_table()

# Track online users (in-memory, per process)
online_users = {}
online_lock = threading.Lock()

def sanitize_input(s, maxlen=64):
    if not isinstance(s, str):
        s = str(s)
    s = s.strip()
    s = s[:maxlen]
    return html.escape(s)

def hash_key(key):
    return hashlib.sha256(key.encode('utf-8')).hexdigest()

def get_db_conn():
    # Use check_same_thread=False for eventlet/threaded environments
    return sqlite3.connect(DB_FILE, check_same_thread=False)

def get_avatar(nickname, conn=None):
    # Accept optional connection for efficiency
    close_conn = False
    if conn is None:
        conn = get_db_conn()
        close_conn = True
    c = conn.cursor()
    c.execute("SELECT avatar FROM users WHERE nickname = ?", (nickname,))
    row = c.fetchone()
    if close_conn:
        conn.close()
    return row[0] if row and row[0] else ""

def save_message(nickname, decoration, message):
    try:
        conn = get_db_conn()
        c = conn.cursor()
        avatar = get_avatar(nickname, conn)
        c.execute("INSERT INTO messages (nickname, decoration, message) VALUES (?, ?, ?)",
                  (nickname, decoration, message))
        conn.commit()
        msg_id = c.lastrowid
        c.execute("SELECT timestamp FROM messages WHERE id = ?", (msg_id,))
        timestamp = c.fetchone()[0]
        if timestamp:
            dt = datetime.datetime.strptime(timestamp, "%Y-%m-%d %H:%M:%S")
            timestamp = dt.strftime("%Y-%m-%dT%H:%M:%S")
        conn.close()
        return timestamp, msg_id, avatar
    except Exception as e:
        print("DB error:", e)
        return None, None, None

def get_recent_messages(limit=MAX_MESSAGES):
    try:
        conn = get_db_conn()
        c = conn.cursor()
        c.execute("SELECT id, nickname, decoration, message, timestamp FROM messages ORDER BY id DESC LIMIT ?", (limit,))
        rows = c.fetchall()
        # Batch fetch avatars for all nicknames
        nicknames = list(set(row[1] for row in rows))
        if nicknames:
            qmarks = ",".join("?" for _ in nicknames)
            c.execute(f"SELECT nickname, avatar FROM users WHERE nickname IN ({qmarks})", nicknames)
            avatar_map = {row[0]: row[1] for row in c.fetchall()}
        else:
            avatar_map = {}
        result = []
        for row in rows[::-1]:
            avatar = avatar_map.get(row[1], "")
            timestamp = row[4]
            if timestamp:
                dt = datetime.datetime.strptime(timestamp, "%Y-%m-%d %H:%M:%S")
                timestamp = dt.strftime("%Y-%m-%dT%H:%M:%S")
            result.append([row[1], row[2], row[3], timestamp, row[0], avatar])
        conn.close()
        return result
    except Exception as e:
        print("DB error:", e)
        return []

def get_user(nickname):
    try:
        conn = get_db_conn()
        c = conn.cursor()
        c.execute("SELECT nickname, key_hash, decoration, avatar FROM users WHERE nickname = ?", (nickname,))
        row = c.fetchone()
        conn.close()
        return row
    except Exception as e:
        print("DB error:", e)
        return None

def create_user(nickname, key_hash, decoration, avatar):
    try:
        conn = get_db_conn()
        c = conn.cursor()
        c.execute("INSERT INTO users (nickname, key_hash, decoration, avatar) VALUES (?, ?, ?, ?)",
                  (nickname, key_hash, decoration, avatar))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print("DB error:", e)
        return False

def update_user_decoration_avatar(nickname, decoration, avatar):
    try:
        conn = get_db_conn()
        c = conn.cursor()
        c.execute("UPDATE users SET decoration = ?, avatar = ? WHERE nickname = ?", (decoration, avatar, nickname))
        conn.commit()
        conn.close()
    except Exception as e:
        print("DB error:", e)

def get_react_count(msg_id):
    try:
        conn = get_db_conn()
        c = conn.cursor()
        c.execute("SELECT count FROM reactions WHERE msg_id = ?", (msg_id,))
        row = c.fetchone()
        conn.close()
        return row[0] if row else 0
    except Exception:
        return 0

def increment_react(msg_id):
    try:
        conn = get_db_conn()
        c = conn.cursor()
        c.execute("INSERT INTO reactions (msg_id, count) VALUES (?, 1) ON CONFLICT(msg_id) DO UPDATE SET count = count + 1", (msg_id,))
        conn.commit()
        c.execute("SELECT count FROM reactions WHERE msg_id = ?", (msg_id,))
        count = c.fetchone()[0]
        conn.close()
        return count
    except Exception:
        return 0

def broadcast_online_users():
    with online_lock:
        users = [{'nickname': info['nickname'], 'avatar': info.get('avatar', '')} for info in online_users.values()]
    socketio.emit('online_users', users)

@app.route('/')
def index():
    try:
        # Serve index.html directly from root
        return send_from_directory('.', 'index.html')
    except Exception as e:
        print("Template rendering error:", e)
        return "Internal Server Error", 500

@app.route('/styles.css')
def styles():
    # Serve styles.css directly from root
    return send_from_directory('.', 'styles.css')

@socketio.on('join')
def handle_join(data):
    nickname = data.get('nickname', '')
    avatar = data.get('avatar', '')
    if nickname.startswith("Guest"):
        session['nickname'] = nickname
        session['decoration'] = ""
        session['avatar'] = avatar
        session['authenticated'] = True
        with online_lock:
            online_users[request.sid] = {'nickname': nickname, 'avatar': avatar}
        messages = get_recent_messages()
        emit('load_messages', messages)
        broadcast_online_users()
    else:
        emit('login_result', {'success': False, 'error': 'Please sign up or login.'})

@socketio.on('signup_or_login')
def handle_signup_or_login(data):
    nickname = sanitize_input(data.get('nickname', ''))
    key = data.get('key', '')
    decoration = sanitize_input(data.get('decoration', ''), maxlen=8)
    avatar = sanitize_input(data.get('avatar', ''), maxlen=128)
    if not nickname or not key:
        emit('login_result', {'success': False, 'error': 'Nickname and key required.'})
        return
    if nickname.startswith("Guest"):
        emit('login_result', {'success': False, 'error': 'Nickname cannot start with "Guest".'})
        return
    user = get_user(nickname)
    key_hash = hash_key(key)
    if user is None:
        if create_user(nickname, key_hash, decoration, avatar):
            session['nickname'] = nickname
            session['decoration'] = decoration
            session['avatar'] = avatar
            session['authenticated'] = True
            with online_lock:
                online_users[request.sid] = {'nickname': nickname, 'avatar': avatar}
            messages = get_recent_messages()
            emit('login_result', {'success': True})
            emit('load_messages', messages)
            broadcast_online_users()
        else:
            emit('login_result', {'success': False, 'error': 'Signup failed.'})
    else:
        user_avatar = user[3] if len(user) > 3 else ""
        if user[1] == key_hash:
            session['nickname'] = nickname
            session['decoration'] = decoration or user[2] or ""
            session['avatar'] = avatar or user_avatar or ""
            session['authenticated'] = True
            if decoration or avatar:
                update_user_decoration_avatar(nickname, decoration or user[2], avatar or user_avatar)
            with online_lock:
                online_users[request.sid] = {'nickname': nickname, 'avatar': session['avatar']}
            messages = get_recent_messages()
            emit('login_result', {'success': True})
            emit('load_messages', messages)
            broadcast_online_users()
        else:
            emit('login_result', {'success': False, 'error': 'Incorrect key.'})

@socketio.on('send_message')
def handle_message(data):
    if not session.get('authenticated'):
        return
    nickname = session.get('nickname', 'Anonymous')
    decoration = session.get('decoration', '')
    avatar = session.get('avatar', '')
    message = data.get('msg', '')
    # Only sanitize message (nickname/decoration/avatar are already sanitized on login)
    message = sanitize_input(message, maxlen=512)
    if not message:
        return
    timestamp, msg_id, avatar = save_message(nickname, decoration, message)
    if timestamp:
        emit('new_message', {'nickname': nickname, 'decoration': decoration, 'msg': message, 'timestamp': timestamp, 'id': msg_id, 'avatar': avatar}, broadcast=True)

@socketio.on('react')
def handle_react(data):
    msg_id = data.get('msg_id')
    if not msg_id:
        return
    count = increment_react(msg_id)
    emit('update_react', {'msg_id': msg_id, 'count': count}, broadcast=True)

@socketio.on('disconnect')
def handle_disconnect():
    with online_lock:
        if request.sid in online_users:
            del online_users[request.sid]
    broadcast_online_users()

@app.route('/clear', methods=['POST'])
def clear():
    clear_messages()
    return "Messages cleared", 200

def clear_messages():
    try:
        conn = get_db_conn()
        c = conn.cursor()
        c.execute("DELETE FROM messages")
        c.execute("DELETE FROM reactions")
        conn.commit()
        conn.close()
    except Exception as e:
        print("DB error (clear):", e)

if __name__ == "__main__":
    clear_messages()
    port = int(os.environ.get("PORT", 5000))
    socketio.run(app, host="0.0.0.0", port=port)
    # For Render.com: Gunicorn must be started with: gunicorn -k eventlet -w 1 server:app

