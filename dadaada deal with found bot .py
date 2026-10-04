# ============================================================
# dealfound_bot.py — v14 (tg://user first, openmessage fallback)
# ============================================================

import sys, os, re, io, asyncio, logging, sqlite3, html, traceback, collections, importlib.util
from datetime import datetime, timezone

from telegram import (Update, InlineKeyboardButton, InlineKeyboardMarkup)
from telegram.constants import ParseMode, ChatMemberStatus
from telegram.error import TelegramError
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, ConversationHandler,
    MessageHandler, ContextTypes, filters,
)

try:
    from PIL import Image, ImageDraw, ImageFont, ImageOps
    PILLOW_OK = True
except ImportError:
    PILLOW_OK = False
    Image = ImageDraw = ImageFont = ImageOps = None

try:
    from telethon import TelegramClient
    TELETHON_OK = True
except ImportError:
    TELETHON_OK = False
    TelegramClient = None

# ============================================================
# CONFIG
# ============================================================
BOT_TOKEN = "8625915540:AAHoLppO0t-1uEscO6LnRptrnKYq196fh_s"
ADMIN_IDS = {8641725650}
REPORT_CHANNEL_ID = -1003978167069
REQUIRED_CHANNEL = "@dealfound"
DEALFOUND_USERNAME = "@dealfound"
API_ID = 38879960
API_HASH = "a2d4c220faf65bf539e936c86435cc57"
SESSION_NAME = "dealfound_resolver"

_this_dir = os.path.dirname(os.path.abspath(__file__)) if "__file__" in globals() else os.getcwd()
WORK_DIR = _this_dir
SESSION_PATH = os.path.join(WORK_DIR, SESSION_NAME)
DB_PATH = os.path.join(WORK_DIR, "dealfound_reports.db")
CUSTOM_GFX_FILE = os.path.join(WORK_DIR, "custom_gfx.py")
CUSTOM_FIX_FILE = os.path.join(WORK_DIR, "custom_fix.py")

_BOT_REF = None

# ============================================================
# LOG BUFFER
# ============================================================
LOG_BUFFER = collections.deque(maxlen=500)
LIVE_LOG_USERS = set()
LIVE_LOG_ON = {"enabled": False}

async def _send_live_log(uid, msg, lvl):
    try:
        if _BOT_REF is None: return
        icon = "🟡" if lvl == "WARNING" else "🔴" if lvl == "ERROR" else "⚫"
        await _BOT_REF.send_message(chat_id=uid,
            text=f"{icon} <b>{lvl}</b>\n<pre>{html.escape(msg[:3000])}</pre>",
            parse_mode="HTML")
    except Exception: pass

class BufferHandler(logging.Handler):
    def emit(self, record):
        try:
            msg = self.format(record)
            LOG_BUFFER.append(msg)
            if LIVE_LOG_ON["enabled"] and _BOT_REF is not None:
                lvl = record.levelname
                if lvl in ("WARNING", "ERROR", "CRITICAL"):
                    for uid in list(LIVE_LOG_USERS):
                        try: asyncio.create_task(_send_live_log(uid, msg, lvl))
                        except Exception: pass
        except Exception: pass

logging.basicConfig(format="%(asctime)s | %(levelname)s | %(message)s", level=logging.INFO,
                    handlers=[logging.StreamHandler()])
log = logging.getLogger("DealFound")
log.setLevel(logging.DEBUG)
_bh = BufferHandler()
_bh.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s"))
log.addHandler(_bh)

# ============================================================
# EMOJI
# ============================================================
USE_CUSTOM_EMOJI = True
EMOJIS = {
    "check":"5274055917766202507","cross":"6105048883140760635",
    "warning":"6104936732954727732","fire":"6107130683853903534",
    "money":"6105174201696528302","rocket":"6105190140320159279",
    "user":"6107276622547653935","shield":"6104690120227559861",
    "star":"6107273096379504177","heart":"6104969984591532862",
    "time":"6105135907768113308","list":"6105072282122590595",
    "info":"6105179944067798549","pin":"6104654545013446747",
    "target":"6104984407091714072",
}
FALLBACK = {
    "check":"✅","cross":"❌","warning":"⚠️","fire":"🎯","money":"💰",
    "rocket":"🚀","user":"👤","shield":"🛡️","star":"⭐","heart":"❤️",
    "time":"⏳","list":"📋","info":"ℹ️","pin":"📌","target":"🎯",
}
_custom_emoji_ok = True

def ce(name, fb=None):
    f = fb or FALLBACK.get(name, "•")
    if not _custom_emoji_ok or not USE_CUSTOM_EMOJI: return f
    eid = EMOJIS.get(name)
    if not eid: return f
    return f'<tg-emoji emoji-id="{eid}">{f}</tg-emoji>'

def _disable_custom_emoji():
    global _custom_emoji_ok
    _custom_emoji_ok = False

# ============================================================
# DB
# ============================================================
def db(): return sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)

def init_db():
    con = db(); cur = con.cursor()
    cur.execute("""CREATE TABLE IF NOT EXISTS reports (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        reporter_id INTEGER NOT NULL, reporter_username TEXT,
        scammer_username TEXT NOT NULL, scammer_user_id INTEGER DEFAULT 0,
        amount TEXT DEFAULT '', description TEXT DEFAULT '',
        proof_link TEXT DEFAULT '', status TEXT NOT NULL DEFAULT 'pending',
        created_at TEXT NOT NULL, reviewed_at TEXT)""")
    cur.execute("""CREATE TABLE IF NOT EXISTS appeals (
        id INTEGER PRIMARY KEY AUTOINCREMENT, report_id INTEGER NOT NULL DEFAULT 0,
        post_link TEXT, appellant_id INTEGER NOT NULL, appellant_username TEXT,
        reason TEXT NOT NULL, proof_link TEXT, status TEXT NOT NULL DEFAULT 'pending',
        created_at TEXT NOT NULL, reviewed_at TEXT)""")
    cur.execute("""CREATE TABLE IF NOT EXISTS report_posts (
        report_id INTEGER PRIMARY KEY, channel_id INTEGER NOT NULL,
        message_id INTEGER NOT NULL, post_link TEXT)""")
    cur.execute("""CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY, value TEXT)""")
    cur.execute("""CREATE TABLE IF NOT EXISTS user_cache (
        username TEXT PRIMARY KEY, user_id INTEGER NOT NULL,
        resolved_at TEXT NOT NULL)""")
    cur.execute("""CREATE TABLE IF NOT EXISTS custom_buttons (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        label TEXT NOT NULL, url TEXT NOT NULL,
        location TEXT NOT NULL DEFAULT 'bot',
        color TEXT NOT NULL DEFAULT 'none',
        sort_order INTEGER DEFAULT 0, active INTEGER DEFAULT 1)""")
    cur.execute("""CREATE TABLE IF NOT EXISTS report_messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        report_id INTEGER NOT NULL, sender_id INTEGER NOT NULL,
        sender_role TEXT NOT NULL, text TEXT NOT NULL,
        created_at TEXT NOT NULL)""")
    cur.execute("""CREATE TABLE IF NOT EXISTS appeal_messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        appeal_id INTEGER NOT NULL, sender_id INTEGER NOT NULL,
        sender_role TEXT NOT NULL, text TEXT NOT NULL,
        created_at TEXT NOT NULL)""")
    cur.execute("""CREATE TABLE IF NOT EXISTS trusted_users (
        user_id INTEGER PRIMARY KEY, username TEXT,
        added_by INTEGER, note TEXT DEFAULT '',
        added_at TEXT NOT NULL)""")
    cur.execute("""CREATE TABLE IF NOT EXISTS user_reviews (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        target_id INTEGER NOT NULL, reviewer_id INTEGER NOT NULL,
        rating INTEGER NOT NULL, comment TEXT DEFAULT '',
        created_at TEXT NOT NULL)""")
    con.commit(); con.close()

def cache_get(u):
    u = (u or "").lstrip("@").strip().lower()
    if not u: return 0
    con = db(); cur = con.cursor()
    cur.execute("SELECT user_id FROM user_cache WHERE username=?", (u,))
    r = cur.fetchone(); con.close()
    return r[0] if r else 0

def cache_set(u, uid):
    u = (u or "").lstrip("@").strip().lower()
    if not u or not uid: return
    con = db(); cur = con.cursor()
    cur.execute("INSERT OR REPLACE INTO user_cache VALUES (?,?,?)",
                (u, int(uid), datetime.now(timezone.utc).isoformat()))
    con.commit(); con.close()

def save_setting(k, v):
    con = db(); cur = con.cursor()
    cur.execute("INSERT OR REPLACE INTO settings VALUES (?,?)", (k, v))
    con.commit(); con.close()

def get_setting(k):
    con = db(); cur = con.cursor()
    cur.execute("SELECT value FROM settings WHERE key=?", (k,))
    r = cur.fetchone(); con.close()
    return r[0] if r else None

def save_banner(f): save_setting("banner_file_id", f)
def get_banner(): return get_setting("banner_file_id")
def save_wlc(f): save_setting("wlc_banner_file_id", f)
def get_wlc(): return get_setting("wlc_banner_file_id")

def create_report(rid, run, su, sid, amt, desc, proof):
    con = db(); cur = con.cursor()
    cur.execute("""INSERT INTO reports (reporter_id, reporter_username, scammer_username,
        scammer_user_id, amount, description, proof_link, status, created_at)
        VALUES (?,?,?,?,?,?,?,'pending',?)""",
        (rid, run, su, sid, amt, desc, proof, datetime.now(timezone.utc).isoformat()))
    r = cur.lastrowid; con.commit(); con.close()
    return r

def get_report(rid):
    con = db(); cur = con.cursor()
    cur.execute("""SELECT id, reporter_id, reporter_username, scammer_username,
        scammer_user_id, amount, description, proof_link, status, created_at, reviewed_at
        FROM reports WHERE id=?""", (rid,))
    r = cur.fetchone(); con.close()
    return r

def set_report_status(rid, s):
    con = db(); cur = con.cursor()
    cur.execute("UPDATE reports SET status=?, reviewed_at=? WHERE id=?",
                (s, datetime.now(timezone.utc).isoformat(), rid))
    con.commit(); con.close()

def set_scammer_id(rid, uid):
    con = db(); cur = con.cursor()
    cur.execute("UPDATE reports SET scammer_user_id=? WHERE id=?", (uid, rid))
    con.commit(); con.close()

def get_scammer_stats(u):
    su = "@" + (u or "").lstrip("@").strip()
    if su == "@": return (0, 0, 0)
    con = db(); cur = con.cursor()
    cur.execute("""SELECT COUNT(*), COUNT(DISTINCT reporter_id) FROM reports
                   WHERE LOWER(scammer_username)=LOWER(?) AND status IN ('approved','appeal_accepted')""", (su,))
    r = cur.fetchone() or (0, 0)
    reports = r[0] or 0; victims = r[1] or 0
    cur.execute("""SELECT amount FROM reports WHERE LOWER(scammer_username)=LOWER(?)
                   AND status IN ('approved','appeal_accepted')""", (su,))
    rows = cur.fetchall(); con.close()
    total = 0.0
    for (a,) in rows:
        try:
            m = re.search(r"[\d.]+", str(a or ""))
            if m: total += float(m.group())
        except Exception: pass
    return (reports, victims, int(total))

def save_report_post(rid, cid, mid, link):
    con = db(); cur = con.cursor()
    cur.execute("INSERT OR REPLACE INTO report_posts VALUES (?,?,?,?)", (rid, cid, mid, link))
    con.commit(); con.close()

def get_report_post(rid):
    con = db(); cur = con.cursor()
    cur.execute("SELECT channel_id, message_id, post_link FROM report_posts WHERE report_id=?", (rid,))
    r = cur.fetchone(); con.close()
    return r

def build_post_link(mid):
    un = DEALFOUND_USERNAME.lstrip("@")
    return f"https://t.me/{un}/{mid}" if un else None

def extract_mid(link):
    if not link: return None
    link = link.strip().split("?")[0].rstrip("/")
    m = re.search(r"/(\d+)$", link)
    return int(m.group(1)) if m else None

def get_rid_by_mid(mid):
    con = db(); cur = con.cursor()
    cur.execute("SELECT report_id FROM report_posts WHERE message_id=?", (mid,))
    r = cur.fetchone(); con.close()
    return r[0] if r else None

def create_appeal(rid, link, aid, aun, reason, proof=None):
    con = db(); cur = con.cursor()
    cur.execute("""INSERT INTO appeals (report_id, post_link, appellant_id, appellant_username,
        reason, proof_link, status, created_at) VALUES (?,?,?,?,?,?,'pending',?)""",
        (rid, link, aid, aun, reason, proof, datetime.now(timezone.utc).isoformat()))
    a = cur.lastrowid; con.commit(); con.close()
    return a

def get_appeal(aid):
    con = db(); cur = con.cursor()
    cur.execute("""SELECT id, report_id, post_link, appellant_id, appellant_username,
        reason, proof_link, status, created_at, reviewed_at FROM appeals WHERE id=?""", (aid,))
    r = cur.fetchone(); con.close()
    return r

def set_appeal_status(aid, s):
    con = db(); cur = con.cursor()
    cur.execute("UPDATE appeals SET status=?, reviewed_at=? WHERE id=?",
                (s, datetime.now(timezone.utc).isoformat(), aid))
    con.commit(); con.close()

def add_report_message(rid, sid, role, text):
    con = db(); cur = con.cursor()
    cur.execute("""INSERT INTO report_messages (report_id, sender_id, sender_role, text, created_at)
                   VALUES (?,?,?,?,?)""",
                (rid, sid, role, text, datetime.now(timezone.utc).isoformat()))
    con.commit(); con.close()

def get_report_messages(rid, limit=30):
    con = db(); cur = con.cursor()
    cur.execute("""SELECT sender_role, text, created_at FROM report_messages
                   WHERE report_id=? ORDER BY id DESC LIMIT ?""", (rid, limit))
    r = cur.fetchall(); con.close()
    return list(reversed(r))

def add_appeal_message(aid, sid, role, text):
    con = db(); cur = con.cursor()
    cur.execute("""INSERT INTO appeal_messages (appeal_id, sender_id, sender_role, text, created_at)
                   VALUES (?,?,?,?,?)""",
                (aid, sid, role, text, datetime.now(timezone.utc).isoformat()))
    con.commit(); con.close()

def get_appeal_messages(aid, limit=30):
    con = db(); cur = con.cursor()
    cur.execute("""SELECT sender_role, text, created_at FROM appeal_messages
                   WHERE appeal_id=? ORDER BY id DESC LIMIT ?""", (aid, limit))
    r = cur.fetchall(); con.close()
    return list(reversed(r))

def mark_trusted(uid, uname, by, note=""):
    con = db(); cur = con.cursor()
    cur.execute("""INSERT OR REPLACE INTO trusted_users (user_id, username, added_by, note, added_at)
                   VALUES (?,?,?,?,?)""",
                (int(uid), (uname or "").lstrip("@"), int(by), note,
                 datetime.now(timezone.utc).isoformat()))
    con.commit(); con.close()

def unmark_trusted(uid):
    con = db(); cur = con.cursor()
    cur.execute("DELETE FROM trusted_users WHERE user_id=?", (int(uid),))
    con.commit(); con.close()

def is_trusted(uid):
    if not uid: return False
    con = db(); cur = con.cursor()
    cur.execute("SELECT 1 FROM trusted_users WHERE user_id=?", (int(uid),))
    r = cur.fetchone(); con.close()
    return bool(r)

def get_trusted_by_username(uname):
    uname = (uname or "").lstrip("@").strip().lower()
    if not uname: return None
    con = db(); cur = con.cursor()
    cur.execute("SELECT user_id FROM trusted_users WHERE LOWER(username)=?", (uname,))
    r = cur.fetchone(); con.close()
    return r[0] if r else None

def list_trusted(limit=50):
    con = db(); cur = con.cursor()
    cur.execute("SELECT user_id, username, note, added_at FROM trusted_users ORDER BY added_at DESC LIMIT ?", (limit,))
    r = cur.fetchall(); con.close()
    return r

def get_user_rating(uid):
    if not uid: return (0.0, 0)
    con = db(); cur = con.cursor()
    cur.execute("SELECT AVG(rating), COUNT(*) FROM user_reviews WHERE target_id=?", (int(uid),))
    r = cur.fetchone(); con.close()
    if not r or not r[0]: return (0.0, 0)
    return (round(float(r[0]), 1), int(r[1] or 0))

def add_custom_button(label, url, location, color="none", order=0):
    con = db(); cur = con.cursor()
    cur.execute("""INSERT INTO custom_buttons (label, url, location, color, sort_order)
                   VALUES (?,?,?,?,?)""", (label, url, location, color, order))
    bid = cur.lastrowid; con.commit(); con.close()
    return bid

def list_custom_buttons(location=None, active_only=True):
    con = db(); cur = con.cursor()
    q = "SELECT id, label, url, location, color, sort_order, active FROM custom_buttons WHERE 1=1"
    params = []
    if location: q += " AND location=?"; params.append(location)
    if active_only: q += " AND active=1"
    q += " ORDER BY sort_order, id"
    cur.execute(q, params)
    r = cur.fetchall(); con.close()
    return r

def delete_custom_button(bid):
    con = db(); cur = con.cursor()
    cur.execute("DELETE FROM custom_buttons WHERE id=?", (bid,))
    con.commit(); con.close()

def toggle_custom_button(bid):
    con = db(); cur = con.cursor()
    cur.execute("UPDATE custom_buttons SET active = 1 - active WHERE id=?", (bid,))
    con.commit(); con.close()

# ============================================================
# RESOLVER
# ============================================================
_resolver_client = None

def _session_exists():
    for p in [SESSION_PATH + ".session",
              os.path.join(os.getcwd(), SESSION_NAME + ".session"),
              os.path.join(WORK_DIR, SESSION_NAME + ".session"),
              "/app/" + SESSION_NAME + ".session",
              os.path.join(os.path.expanduser("~"), SESSION_NAME + ".session")]:
        if os.path.exists(p): return p
    return None

async def get_resolver():
    global _resolver_client
    if not TELETHON_OK: return None
    if _resolver_client is not None: return _resolver_client
    sess = _session_exists()
    if not sess:
        log.warning("[Resolver] No session file found!")
        return None
    base = sess[:-8] if sess.endswith(".session") else sess
    try:
        _resolver_client = TelegramClient(base, API_ID, API_HASH)
        await _resolver_client.connect()
        if not await _resolver_client.is_user_authorized():
            log.warning("[Resolver] Not authorized!")
            _resolver_client = None; return None
        me = await _resolver_client.get_me()
        log.info(f"[Resolver] OK @{getattr(me,'username','?')} (id={me.id})")
        return _resolver_client
    except Exception as e:
        log.error(f"[Resolver] {e}")
        _resolver_client = None; return None

async def resolve_username(username, max_retries=5):
    clean = (username or "").lstrip("@").strip()
    if not clean: return 0
    cached = cache_get(clean)
    if cached:
        log.info(f"[Resolver] CACHE @{clean} -> {cached}")
        return cached
    global _resolver_client
    for attempt in range(1, max_retries + 1):
        try:
            client = await get_resolver()
            if client is not None:
                entity = await client.get_entity(f"@{clean}")
                uid = int(entity.id)
                if uid > 0:
                    log.info(f"[Resolver] OK @{clean} -> {uid} (try {attempt})")
                    cache_set(clean, uid)
                    return uid
        except Exception as e:
            err = str(e)
            log.warning(f"[Resolver] try {attempt}: {err[:120]}")
            if "flood" in err.lower() or "wait" in err.lower():
                m = re.search(r"(\d+)", err)
                if m: await asyncio.sleep(min(int(m.group(1)) + 1, 15)); continue
        await asyncio.sleep(0.3)
    log.error(f"[Resolver] FAIL @{clean}")
    return 0

async def get_display_name(username):
    clean = (username or "").lstrip("@").strip()
    for _ in range(3):
        client = await get_resolver()
        if client is None:
            await asyncio.sleep(0.4); continue
        try:
            e = await client.get_entity(f"@{clean}")
            fn = getattr(e, "first_name", "") or ""
            ln = getattr(e, "last_name", "") or ""
            return (f"{fn} {ln}".strip()) or clean
        except Exception: await asyncio.sleep(0.4)
    return clean

async def get_profile_photo(username):
    clean = (username or "").lstrip("@").strip()
    if not clean: return None
    for _ in range(3):
        client = await get_resolver()
        if client is None:
            await asyncio.sleep(0.4); continue
        try:
            e = await client.get_entity(f"@{clean}")
            return await client.download_profile_photo(e, file=bytes)
        except Exception: await asyncio.sleep(0.4)
    return None

async def shutdown_resolver():
    global _resolver_client
    if _resolver_client is not None:
        try: await _resolver_client.disconnect()
        except Exception: pass
        _resolver_client = None

# ============================================================
# SAFE SEND
# ============================================================
async def safe_send(bot, cid, text, **kw):
    try: return await bot.send_message(chat_id=cid, text=text, **kw)
    except TelegramError as e:
        if "entity" in str(e).lower() or "parse" in str(e).lower():
            _disable_custom_emoji()
            clean = re.sub(r'<tg-emoji[^>]*>(.*?)</tg-emoji>', r'\1', text)
            kw["parse_mode"] = ParseMode.HTML
            return await bot.send_message(chat_id=cid, text=clean, **kw)
        raise

async def safe_reply(message, text, **kw):
    try: return await message.reply_text(text, **kw)
    except TelegramError as e:
        if "entity" in str(e).lower() or "parse" in str(e).lower():
            _disable_custom_emoji()
            clean = re.sub(r'<tg-emoji[^>]*>(.*?)</tg-emoji>', r'\1', text)
            return await message.reply_text(clean, **kw)
        raise

async def safe_answer(q, text=None, alert=False):
    try: await q.answer(text, show_alert=alert)
    except Exception: pass

async def safe_edit(q, text, **kw):
    try:
        if q.message.photo: await q.edit_message_caption(caption=text, **kw)
        else: await q.edit_message_text(text=text, **kw)
    except Exception:
        try:
            clean = re.sub(r'<tg-emoji[^>]*>(.*?)</tg-emoji>', r'\1', text)
            if q.message.photo: await q.edit_message_caption(caption=clean, **kw)
            else: await q.edit_message_text(text=clean, **kw)
        except Exception: pass

# ============================================================
# HELPERS
# ============================================================
USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{5,32}$")

def _uid(obj):
    if obj is None: return 0
    u = getattr(obj, "effective_user", None) or getattr(obj, "from_user", None)
    return getattr(u, "id", 0) or 0 if u else 0

def normalize_username(raw):
    if not raw: return None
    s = raw.strip().replace("https://t.me/", "").replace("http://t.me/", "")
    s = s.lstrip("@").split("/")[0].split("?")[0]
    return s if USERNAME_RE.fullmatch(s) else None

def try_extract_id(msg):
    if not msg: return None, None
    r = getattr(msg, "reply_to_message", None)
    if r:
        rf = getattr(r, "from_user", None)
        if rf and getattr(rf, "id", None): return int(rf.id), "Reply"
    o = getattr(msg, "forward_origin", None)
    if o is not None:
        su = getattr(o, "sender_user", None)
        if su and getattr(su, "id", None): return int(su.id), "Forward"
    f = getattr(msg, "forward_from", None)
    if f and getattr(f, "id", None): return int(f.id), "Forward"
    txt = (msg.text or "").strip()
    m = re.search(r"tg://user\?id=(\d+)", txt)
    if m: return int(m.group(1)), "Link"
    if txt.isdigit() and int(txt) > 0: return int(txt), "Manual"
    return None, None

async def check_member(bot, uid):
    try:
        m = await bot.get_chat_member(REQUIRED_CHANNEL, uid)
        return m.status in (ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER)
    except Exception: return False

# ============================================================
# BANNER HELPERS
# ============================================================
FONT_B = ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
          "/system/fonts/Roboto-Bold.ttf"]
FONT_R = ["/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
          "/system/fonts/Roboto-Regular.ttf"]

def _font(size, bold=True):
    for p in (FONT_B if bold else FONT_R):
        try:
            if os.path.exists(p): return ImageFont.truetype(p, size)
        except Exception: pass
    try: return ImageFont.load_default()
    except Exception: return None

def _tb(d, t, f):
    try:
        b = d.textbbox((0,0), t, font=f); return b[2]-b[0], b[3]-b[1]
    except Exception: return 0, 0

def _cx(d, t, f, cx, y, fill):
    if not f: return
    try:
        w, _ = _tb(d, t, f)
        d.text((cx - w/2, y), t, font=f, fill=fill)
    except Exception: pass

def _pill(d, box, r, fill=None, outline=None, w=0, width=0):
    try: d.rounded_rectangle(box, radius=r, fill=fill, outline=outline, width=width or w)
    except Exception: pass

# ============================================================
# DEFAULT BANNER
# ============================================================
def make_banner(username, display_name, reports, victims, total,
                profile_bytes=None, status="SCAMMER",
                rating=0.0, rating_count=0, trusted=False):
    if not PILLOW_OK: return None
    try:
        W, H = 1100, 560
        BG = (10, 10, 12, 255); CARD = (20, 20, 24, 255); BORDER = (40, 40, 46, 255)
        WHITE = (240, 240, 245, 255); GREY = (130, 130, 140, 255); DIVIDER = (35, 35, 42, 255)
        if trusted:
            ACCENT = (52, 211, 118); BADGE_BG = (18, 55, 34); BADGE_TXT = (130, 240, 170)
        elif status == "SCAMMER":
            ACCENT = (235, 60, 70); BADGE_BG = (65, 18, 24); BADGE_TXT = (250, 130, 140)
        else:
            ACCENT = (80, 130, 230); BADGE_BG = (22, 38, 70); BADGE_TXT = (150, 180, 245)

        img = Image.new("RGBA", (W, H), BG)
        d = ImageDraw.Draw(img, "RGBA")
        glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        gd = ImageDraw.Draw(glow, "RGBA")
        for r in range(600, 0, -8):
            a = int(20 * (1 - r / 600) ** 2)
            gd.ellipse([W // 2 - r, -r * 2, W // 2 + r, r // 3], fill=(*ACCENT, a))
        img = Image.alpha_composite(img, glow)
        d = ImageDraw.Draw(img, "RGBA")
        _pill(d, [15, 15, W - 15, H - 15], 22, fill=None, outline=BORDER, width=2)

        pcx, pcy, pr = 110, 145, 62
        d.ellipse([pcx - pr - 5, pcy - pr - 5, pcx + pr + 5, pcy + pr + 5], outline=ACCENT, width=3)
        av = pr * 2
        avi = Image.new("RGBA", (av, av), (0, 0, 0, 0))
        pasted = False
        if profile_bytes:
            try:
                p = Image.open(io.BytesIO(profile_bytes)).convert("RGBA")
                p = ImageOps.fit(p, (av, av), Image.LANCZOS)
                mask = Image.new("L", (av, av), 0)
                ImageDraw.Draw(mask).ellipse([0, 0, av - 1, av - 1], fill=255)
                avi.paste(p, (0, 0), mask); pasted = True
            except Exception: pasted = False
        if not pasted:
            ad = ImageDraw.Draw(avi, "RGBA")
            ad.ellipse([0, 0, av, av], fill=(35, 35, 40, 255))
            ad.ellipse([pr - 24, pr - 40, pr + 24, pr + 8], fill=(70, 70, 78, 255))
            ad.ellipse([pr - 45, pr + 15, pr + 45, pr + 90], fill=(70, 70, 78, 255))
        img.alpha_composite(avi, (pcx - pr, pcy - pr))
        d = ImageDraw.Draw(img, "RGBA")

        ux = 210
        d.text((ux, 85), "USERNAME", font=_font(12, True), fill=GREY)
        cun = str(username or "").lstrip("@")[:20]
        d.text((ux, 102), "@" + cun, font=_font(28, True), fill=WHITE)
        d.text((ux, 148), "DISPLAY NAME", font=_font(12, True), fill=GREY)
        dn = str(display_name or username or "?")[:26]
        d.text((ux, 165), dn, font=_font(20, True), fill=WHITE)

        label = ("TRUSTED" if trusted else "SCAMMER" if status == "SCAMMER" else "NOT FLAGGED")
        bf = _font(14, True)
        tw, th = _tb(d, label, bf)
        bx2 = W - 40; bx1 = bx2 - tw - 40; by1 = 75; by2 = 108
        _pill(d, [bx1, by1, bx2, by2], 16, fill=BADGE_BG, outline=ACCENT, width=2)
        d.text((bx1 + 20, by1 + 8), label, font=bf, fill=BADGE_TXT)

        if rating_count > 0:
            rt = f"★ {rating:.1f}/5   {rating_count} reviews"
            rw, _ = _tb(d, rt, _font(13, False))
            d.text((bx2 - rw, 122), rt, font=_font(13, False), fill=(245, 200, 80, 255))

        d.line([(60, 235), (W - 60, 235)], fill=DIVIDER, width=1)

        cy1, cy2 = 275, 455
        card_w = 300; gap = 25
        sx0 = (W - (card_w * 3 + gap * 2)) // 2
        stats = [("Reports", str(reports)), ("Victims", str(victims)), ("Total Scammed", f"${int(total)}")]
        label_font = _font(13, True); value_font = _font(42, True)
        for i, (lb, val) in enumerate(stats):
            x1 = sx0 + i * (card_w + gap); x2 = x1 + card_w
            cx = (x1 + x2) // 2
            _pill(d, [x1, cy1, x2, cy2], 14, fill=CARD, outline=BORDER, width=2)
            d.line([(x1 + 30, cy1 + 2), (x2 - 30, cy1 + 2)], fill=ACCENT, width=3)
            _cx(d, lb, label_font, cx, cy1 + 25, GREY)
            vx1 = x1 + 30; vx2 = x2 - 30; vy1 = cy2 - 85; vy2 = cy2 - 25
            _pill(d, [vx1, vy1, vx2, vy2], 10, fill=(10, 10, 12, 255), outline=(*ACCENT, 90), width=1)
            _cx(d, val, value_font, cx, vy1 + 10, WHITE)

        ft = "Powered by DealFound"
        ff = _font(13, True)
        tw, _ = _tb(d, ft, ff)
        shield_cx = W // 2 - tw // 2 - 16; shield_cy = H - 45
        d.ellipse([shield_cx - 9, shield_cy - 9, shield_cx + 9, shield_cy + 9], outline=ACCENT, width=1)
        d.text((shield_cx - 4, shield_cy - 7), "D", font=_font(11, True), fill=ACCENT)
        d.text((W // 2 - tw // 2 + 6, H - 53), ft, font=ff, fill=GREY)

        out = io.BytesIO()
        img.convert("RGB").save(out, "PNG", optimize=True)
        out.seek(0); return out
    except Exception as e:
        log.exception(f"[Banner] {e}")
        return None

# ============================================================
# CUSTOM GFX LOADER
# ============================================================
_custom_gfx_func = None
_custom_gfx_mtime = 0

def load_custom_gfx():
    global _custom_gfx_func, _custom_gfx_mtime
    if not os.path.exists(CUSTOM_GFX_FILE):
        _custom_gfx_func = None
        return None
    try:
        mtime = os.path.getmtime(CUSTOM_GFX_FILE)
        if _custom_gfx_func is not None and mtime == _custom_gfx_mtime:
            return _custom_gfx_func
        spec = importlib.util.spec_from_file_location("custom_gfx", CUSTOM_GFX_FILE)
        mod = importlib.util.module_from_spec(spec)
        mod.__dict__.update({"log": log, "io": io, "os": os, "re": re,
            "PILLOW_OK": PILLOW_OK, "_font": _font, "_tb": _tb, "_cx": _cx, "_pill": _pill, "html": html})
        if PILLOW_OK:
            mod.__dict__.update({"Image": Image, "ImageDraw": ImageDraw,
                "ImageFont": ImageFont, "ImageOps": ImageOps})
        spec.loader.exec_module(mod)
        if hasattr(mod, "make_banner"):
            _custom_gfx_func = mod.make_banner
            _custom_gfx_mtime = mtime
            log.info(f"[GFX] Custom loaded")
            return _custom_gfx_func
        _custom_gfx_func = None
        return None
    except Exception as e:
        log.exception(f"[GFX] Load failed: {e}")
        return None

def get_banner_func():
    f = load_custom_gfx()
    return f if f else make_banner

def reload_gfx():
    global _custom_gfx_func, _custom_gfx_mtime
    _custom_gfx_func = None; _custom_gfx_mtime = 0
    return load_custom_gfx()

# ============================================================
# CUSTOM FIX LOADER
# ============================================================
_custom_fix_loaded = False
_custom_fix_mtime = 0

def load_custom_fix():
    global _custom_fix_loaded, _custom_fix_mtime
    if not os.path.exists(CUSTOM_FIX_FILE):
        return False
    try:
        mtime = os.path.getmtime(CUSTOM_FIX_FILE)
        if _custom_fix_loaded and mtime == _custom_fix_mtime:
            return True
        with open(CUSTOM_FIX_FILE, "r", encoding="utf-8") as f:
            code = f.read()
        exec(compile(code, str(CUSTOM_FIX_FILE), "exec"), globals())
        _custom_fix_loaded = True; _custom_fix_mtime = mtime
        log.info(f"[Fix] Custom fix loaded")
        return True
    except Exception as e:
        log.exception(f"[Fix] Load failed: {e}")
        return False

def reload_fix():
    global _custom_fix_loaded, _custom_fix_mtime
    _custom_fix_loaded = False; _custom_fix_mtime = 0
    return load_custom_fix()

# ============================================================
# KEYBOARDS
# ============================================================
def _build_button_rows(location):
    cb = list_custom_buttons(location=location)
    rows = []; cur_row = []
    for bid, label, url, loc, color, order, active in cb:
        cur_row.append(InlineKeyboardButton(label, url=url))
        if len(cur_row) == 2:
            rows.append(cur_row); cur_row = []
    if cur_row: rows.append(cur_row)
    return rows

def main_kb():
    rows = [
        [InlineKeyboardButton("Report User", callback_data="df_report"),
         InlineKeyboardButton("Appeal", callback_data="df_appeal")],
    ]
    rows.extend(_build_button_rows("bot"))
    rows.append([InlineKeyboardButton("DealFound", url="https://t.me/dealfound")])
    return InlineKeyboardMarkup(rows)

def back_kb():
    return InlineKeyboardMarkup([[InlineKeyboardButton("Back", callback_data="df_home")]])

def admin_panel_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Pending", callback_data="ad_pending"),
         InlineKeyboardButton("Stats", callback_data="ad_stats")],
        [InlineKeyboardButton("Appeals", callback_data="ad_appeals")],
        [InlineKeyboardButton("Report Banner", callback_data="ad_setban"),
         InlineKeyboardButton("Welcome Banner", callback_data="ad_setwlc")],
        [InlineKeyboardButton("Fix Old Report ID", callback_data="ad_fixid"),
         InlineKeyboardButton("Republish All", callback_data="ad_repuball")],
        [InlineKeyboardButton("Add Custom Button", callback_data="ad_addbtn"),
         InlineKeyboardButton("List Buttons", callback_data="ad_listbtn")],
        [InlineKeyboardButton("Trusted Users", callback_data="ad_trusted"),
         InlineKeyboardButton("Mark Trusted", callback_data="ad_marktrust")],
        [InlineKeyboardButton("Broadcast", callback_data="ad_broadcast"),
         InlineKeyboardButton("Users", callback_data="ad_users")],
        [InlineKeyboardButton("Backup DB", callback_data="ad_backup"),
         InlineKeyboardButton("Clear ID Cache", callback_data="ad_clearcache")],
        [InlineKeyboardButton("Terminal Logs", callback_data="ad_term"),
         InlineKeyboardButton("GFX Info", callback_data="ad_gfx")],
        [InlineKeyboardButton("Fix Info", callback_data="ad_fix"),
         InlineKeyboardButton("Restart Bot", callback_data="ad_restart")],
        [InlineKeyboardButton("Delete Post", callback_data="ad_delpost")],
    ])

def set_id_kb(rid):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Forward Scammer Msg", callback_data=f"df_fwd:{rid}")],
        [InlineKeyboardButton("Enter ID Manually", callback_data=f"df_man:{rid}")],
        [InlineKeyboardButton("Cancel", callback_data=f"df_cancel_id:{rid}")],
    ])

def fixid_kb(rid):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Forward Scammer Msg", callback_data=f"fx_fwd:{rid}")],
        [InlineKeyboardButton("Enter ID", callback_data=f"fx_man:{rid}")],
        [InlineKeyboardButton("Republish Post", callback_data=f"fx_repub:{rid}")],
        [InlineKeyboardButton("Cancel", callback_data="ad_home")],
    ])

def repuball_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Yes, Republish All", callback_data="ad_repuball_yes"),
         InlineKeyboardButton("Cancel", callback_data="ad_home")],
    ])

def btn_loc_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Bot Menu Only", callback_data="btn_loc:bot"),
         InlineKeyboardButton("Post Only", callback_data="btn_loc:post")],
        [InlineKeyboardButton("Both", callback_data="btn_loc:both")],
        [InlineKeyboardButton("Cancel", callback_data="ad_home")],
    ])

# ============================================================
# WELCOME
# ============================================================
WELCOME = (
    f"{ce('shield')} <b>Welcome to DealFound</b>\n\n"
    f"<b>Report suspicious accounts and review scam reports.</b>\n\n"
    f"{ce('list')} <b>Report User — submit a scam report</b>\n"
    f"{ce('list')} <b>Appeal — challenge a published report</b>\n"
    f"{ce('list')} <b>Check User — /check @username</b>\n\n"
    f"{ce('info')} <b>All reports are reviewed by the DF Team.</b>"
)

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if _uid(update) == 0: return
    try: context.user_data.clear()
    except Exception: pass
    banner = get_wlc()
    if banner:
        try:
            await update.message.reply_photo(photo=banner, caption=WELCOME,
                                             parse_mode="HTML", reply_markup=main_kb())
            return
        except Exception: pass
    await safe_reply(update.message, WELCOME, parse_mode="HTML", reply_markup=main_kb())

async def cb_home(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q: return
    await safe_answer(q)
    try: context.user_data.clear()
    except Exception: pass
    await safe_edit(q, WELCOME, parse_mode="HTML", reply_markup=main_kb())

# ============================================================
# REPORT FLOW
# ============================================================
DF_T, DF_A, DF_D, DF_P = range(1, 5)

async def report_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q: return ConversationHandler.END
    await safe_answer(q)
    if not await check_member(context.bot, _uid(q)):
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("Join @DealFound", url="https://t.me/dealfound")],
            [InlineKeyboardButton("Check Again", callback_data="df_report"),
             InlineKeyboardButton("Back", callback_data="df_home")],
        ])
        await safe_edit(q, f"{ce('warning')} <b>Join @dealfound to continue.</b>",
                        parse_mode="HTML", reply_markup=kb)
        return ConversationHandler.END
    try: context.user_data.clear()
    except Exception: pass
    await safe_edit(q, f"{ce('user')} <b>Step 1/4 — Enter target username or user ID:</b>",
                    parse_mode="HTML")
    return DF_T

async def r_target(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or not msg.text:
        await safe_reply(msg, f"{ce('cross')} <b>Send username or ID.</b>", parse_mode="HTML")
        return DF_T
    raw = msg.text.strip()
    if raw.isdigit() and int(raw) > 0:
        context.user_data["s_un"] = f"id{raw}"; context.user_data["s_id"] = int(raw)
        await safe_reply(msg, f"{ce('check')} <b>Target:</b> <code>{raw}</code>\n\n"
                              f"{ce('money')} <b>Step 2/4 — Amount:</b>", parse_mode="HTML")
        return DF_A
    clean = normalize_username(raw)
    if not clean:
        await safe_reply(msg, f"{ce('cross')} <b>Invalid.</b>", parse_mode="HTML")
        return DF_T
    context.user_data["s_un"] = "@" + clean
    detected_id = await resolve_username(clean)
    context.user_data["s_id"] = detected_id or 0
    if detected_id:
        await safe_reply(msg, f"{ce('check')} <b>@{html.escape(clean)}</b>\n"
                              f"🆔 <code>{detected_id}</code>\n\n"
                              f"{ce('money')} <b>Step 2/4 — Amount:</b>", parse_mode="HTML")
    else:
        await safe_reply(msg, f"{ce('warning')} <b>@{html.escape(clean)}</b>\n"
                              f"🆔 <i>Could not resolve</i>\n\n"
                              f"{ce('money')} <b>Step 2/4 — Amount:</b>", parse_mode="HTML")
    return DF_A

async def r_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or not msg.text:
        await safe_reply(msg, f"{ce('cross')} <b>Invalid amount.</b>", parse_mode="HTML")
        return DF_A
    amt = msg.text.strip()
    if not amt or len(amt) > 100:
        await safe_reply(msg, f"{ce('cross')} <b>Invalid amount.</b>", parse_mode="HTML")
        return DF_A
    context.user_data["amt"] = amt
    await safe_reply(msg, f"{ce('list')} <b>Step 3/4 — Explain the scam:</b>", parse_mode="HTML")
    return DF_D

async def r_desc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or not msg.text:
        await safe_reply(msg, f"{ce('cross')} <b>Send explanation.</b>", parse_mode="HTML")
        return DF_D
    d = msg.text.strip()
    if len(d) < 3 or len(d) > 3000:
        await safe_reply(msg, f"{ce('cross')} <b>3-3000 chars.</b>", parse_mode="HTML")
        return DF_D
    context.user_data["desc"] = d
    await safe_reply(msg, f"{ce('pin')} <b>Step 4/4 — Proof link:</b>", parse_mode="HTML")
    return DF_P

async def r_proof(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or not msg.text:
        await safe_reply(msg, f"{ce('cross')} <b>Send link.</b>", parse_mode="HTML")
        return DF_P
    proof = msg.text.strip()
    if not (proof.startswith("https://t.me/") or proof.startswith("http://t.me/")):
        await safe_reply(msg, f"{ce('cross')} <b>Invalid link.</b>", parse_mode="HTML")
        return DF_P
    u = update.effective_user
    su = context.user_data["s_un"]; sid = context.user_data.get("s_id", 0)
    amt = context.user_data.get("amt", ""); d = context.user_data.get("desc", "")
    rid = create_report(u.id if u else 0, u.username if u else None, su, sid, amt, d, proof)
    rname = f"@{u.username}" if u and u.username else "No username"
    admin_text = (f"{ce('warning')} <b>NEW REPORT #{rid}</b>\n\n"
                  f"{ce('user')} <b>Reporter:</b> <b>{html.escape(rname)}</b> (<code>{u.id if u else 0}</code>)\n\n"
                  f"{ce('fire')} <b>Target:</b> <b>{html.escape(su)}</b>\n"
                  f"🆔 <code>{sid or 'NOT SET'}</code>\n\n"
                  f"{ce('money')} <b>Amount:</b> <b>{html.escape(amt)}</b>\n\n"
                  f"📝 <b>{html.escape(d)}</b>\n\n"
                  f"🔗 {html.escape(proof)}")
    rows = []
    if not sid: rows.append([InlineKeyboardButton("Set User ID", callback_data=f"df_setid:{rid}")])
    rows.append([InlineKeyboardButton("Approve", callback_data=f"df_approve:{rid}"),
                 InlineKeyboardButton("Reject", callback_data=f"df_reject:{rid}")])
    rows.append([InlineKeyboardButton("Message Reporter", callback_data=f"rc_chat:{rid}")])
    for aid in ADMIN_IDS:
        try: await safe_send(context.bot, aid, admin_text, parse_mode="HTML",
                             reply_markup=InlineKeyboardMarkup(rows), disable_web_page_preview=True)
        except Exception: pass
    await safe_reply(msg, f"{ce('check')} <b>Report submitted.</b>",
                     parse_mode="HTML", reply_markup=main_kb())
    try: context.user_data.clear()
    except Exception: pass
    return ConversationHandler.END

async def report_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try: context.user_data.clear()
    except Exception: pass
    try:
        await update.message.reply_text(f"{ce('cross')} <b>Cancelled.</b>",
                                        parse_mode="HTML", reply_markup=main_kb())
    except Exception: pass
    return ConversationHandler.END

# ============================================================
# APPEAL FLOW
# ============================================================
DF_AR, DF_ARR, DF_ARP = 10, 11, 12

async def appeal_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q: return ConversationHandler.END
    await safe_answer(q)
    try: context.user_data.clear()
    except Exception: pass
    await safe_edit(q, f"{ce('info')} <b>Appeal — Post Link:</b>", parse_mode="HTML", reply_markup=back_kb())
    return DF_AR

async def ap_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text: return DF_AR
    link = update.message.text.strip()
    if not (link.startswith("https://t.me/") or link.startswith("http://t.me/") or link.startswith("t.me/")):
        await safe_reply(update.message, f"{ce('cross')} <b>Invalid.</b>", parse_mode="HTML")
        return DF_AR
    mid = extract_mid(link); rid = 0
    if mid:
        found = get_rid_by_mid(mid)
        if found: rid = found
    context.user_data["ap_link"] = link; context.user_data["ap_rid"] = rid
    await safe_reply(update.message, f"{ce('check')} <b>Reason:</b>", parse_mode="HTML")
    return DF_ARR

async def ap_reason(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text: return DF_ARR
    r = update.message.text.strip()
    if len(r) < 10 or len(r) > 3000:
        await safe_reply(update.message, f"{ce('cross')} <b>10-3000 chars.</b>", parse_mode="HTML")
        return DF_ARR
    context.user_data["ap_reason"] = r
    await safe_reply(update.message, f"{ce('list')} <b>Proof link or type</b> <code>none</code>:",
                     parse_mode="HTML")
    return DF_ARP

async def ap_proof(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text: return DF_ARP
    p = update.message.text.strip()
    pl = None if p.lower() == "none" else p
    if pl and not (pl.startswith("https://t.me/") or pl.startswith("http://t.me/")):
        await safe_reply(update.message, f"{ce('cross')} <b>Invalid.</b>", parse_mode="HTML")
        return DF_ARP
    u = update.effective_user
    rid = context.user_data.get("ap_rid", 0); link = context.user_data.get("ap_link", "")
    r = context.user_data["ap_reason"]
    uname = u.username if u else None
    aid = create_appeal(rid, link, u.id if u else 0, uname, r, pl)
    admin_text = (f"{ce('warning')} <b>NEW APPEAL #{aid}</b>\n\n"
                  f"Post: {html.escape(link)}\n"
                  f"User: @{html.escape(uname or 'N/A')} (<code>{u.id if u else 0}</code>)\n\n"
                  f"Reason: <b>{html.escape(r)}</b>\n\n"
                  f"Proof: {html.escape(pl or 'None')}")
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("Accept", callback_data=f"df_ap_ok:{aid}"),
         InlineKeyboardButton("Reject", callback_data=f"df_ap_no:{aid}")],
        [InlineKeyboardButton("Message Appellant", callback_data=f"ac_chat:{aid}")],
    ])
    for a in ADMIN_IDS:
        try: await safe_send(context.bot, a, admin_text, parse_mode="HTML", reply_markup=kb)
        except Exception: pass
    await safe_reply(update.message, f"{ce('check')} <b>Appeal #{aid} submitted.</b>",
                     parse_mode="HTML", reply_markup=main_kb())
    try: context.user_data.clear()
    except Exception: pass
    return ConversationHandler.END

async def appeal_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try: context.user_data.clear()
    except Exception: pass
    try:
        await update.message.reply_text(f"{ce('cross')} <b>Cancelled.</b>",
                                        parse_mode="HTML", reply_markup=main_kb())
    except Exception: pass
    return ConversationHandler.END

async def cb_ap_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q or _uid(q) not in ADMIN_IDS:
        await safe_answer(q, "No.", alert=True); return
    await safe_answer(q)
    try:
        action, aid_s = q.data.split(":", 1); aid = int(aid_s)
    except Exception: return
    ap = get_appeal(aid)
    if not ap: return
    (aid, rid, link, apid, aun, reason, proof, status, cat, rat) = ap
    if status != "pending":
        await safe_answer(q, f"Already {status}.", alert=True); return
    if action == "df_ap_no":
        set_appeal_status(aid, "rejected")
        try: await safe_send(context.bot, apid, f"{ce('cross')} <b>Appeal #{aid} Rejected</b>", parse_mode="HTML")
        except Exception: pass
        await safe_edit(q, f"{ce('cross')} <b>Appeal #{aid} rejected.</b>", parse_mode="HTML")
    else:
        set_appeal_status(aid, "accepted")
        if rid: set_report_status(rid, "appeal_accepted")
        try: await safe_send(context.bot, apid, f"{ce('check')} <b>Appeal #{aid} Accepted</b>", parse_mode="HTML")
        except Exception: pass
        await safe_edit(q, f"{ce('check')} <b>Appeal #{aid} accepted.</b>", parse_mode="HTML")

# ============================================================
# CHAT — REPORT
# ============================================================
async def cb_rc_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q or _uid(q) not in ADMIN_IDS:
        await safe_answer(q, "No.", alert=True); return
    await safe_answer(q)
    try: rid = int(q.data.split(":", 1)[1])
    except Exception: return
    rep = get_report(rid)
    if not rep: return
    context.user_data["chat_rid"] = rid; context.user_data["chat_role"] = "admin"
    await q.message.reply_text(
        f"{ce('info')} <b>Message Reporter — Report #{rid}</b>\n\n"
        f"Reporter: <code>{rep[1]}</code>\n\n<b>Type your message.</b>\n\n/cancel to abort.",
        parse_mode="HTML")

async def cb_rc_hist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q or _uid(q) not in ADMIN_IDS:
        await safe_answer(q, "No.", alert=True); return
    await safe_answer(q)
    try: rid = int(q.data.split(":", 1)[1])
    except Exception: return
    msgs = get_report_messages(rid)
    if not msgs:
        await q.message.reply_text(f"{ce('info')} <b>No messages yet.</b>", parse_mode="HTML")
        return
    lines = [f"{ce('list')} <b>Chat — Report #{rid}</b>\n"]
    for role, text, ts in msgs:
        who = "DF Team" if role == "admin" else "Reporter"
        lines.append(f"<b>{who}:</b> {html.escape(text)}")
    txt = "\n\n".join(lines)
    if len(txt) > 4000: txt = txt[-4000:]
    await q.message.reply_text(txt, parse_mode="HTML")

async def recv_admin_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if _uid(update) not in ADMIN_IDS: return
    if context.user_data.get("chat_role") != "admin": return
    rid = context.user_data.get("chat_rid")
    if not rid: return
    if not update.message or not update.message.text: return
    text = update.message.text.strip()
    if len(text) < 1 or len(text) > 2000:
        await safe_reply(update.message, f"{ce('cross')} <b>1-2000 chars.</b>", parse_mode="HTML")
        return
    rep = get_report(rid)
    if not rep:
        context.user_data.pop("chat_rid", None); context.user_data.pop("chat_role", None)
        return
    add_report_message(rid, _uid(update), "admin", text)
    try:
        await safe_send(context.bot, rep[1],
            f"{ce('info')} <b>DF Team — About your Report #{rid}</b>\n\n"
            f"{html.escape(text)}\n\n<i>Reply to respond.</i>", parse_mode="HTML")
        await safe_reply(update.message, f"{ce('check')} <b>Sent to reporter.</b>", parse_mode="HTML")
    except Exception as e:
        await safe_reply(update.message, f"{ce('cross')} <b>{html.escape(str(e))}</b>", parse_mode="HTML")

async def recv_reporter_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = _uid(update)
    if uid == 0 or uid in ADMIN_IDS: return
    if not update.message: return
    reply_to = getattr(update.message, "reply_to_message", None)
    if not reply_to: return
    bot_id = context.bot.id if hasattr(context.bot, "id") else 0
    sender = getattr(reply_to, "from_user", None)
    if not sender or sender.id != bot_id: return
    reply_text = reply_to.text or reply_to.caption or ""
    m = re.search(r"About your Report #(\d+)", reply_text)
    if not m: return
    rid = int(m.group(1))
    rep = get_report(rid)
    if not rep: return
    text = update.message.text or update.message.caption or "[media]"
    if len(text) > 2000: text = text[:2000] + "..."
    add_report_message(rid, uid, "reporter", text)
    uname = update.effective_user.username or f"id{uid}"
    for aid in ADMIN_IDS:
        try:
            await safe_send(context.bot, aid,
                f"{ce('info')} <b>Reporter Reply — Report #{rid}</b>\n\n"
                f"From: @{html.escape(uname)}\n\n{html.escape(text)}",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("Reply", callback_data=f"rc_chat:{rid}")
                ]]))
        except Exception: pass
    await safe_reply(update.message, f"{ce('check')} <b>Sent to DF Team.</b>", parse_mode="HTML")

# ============================================================
# CHAT — APPEAL
# ============================================================
async def cb_ac_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q or _uid(q) not in ADMIN_IDS:
        await safe_answer(q, "No.", alert=True); return
    await safe_answer(q)
    try: aid = int(q.data.split(":", 1)[1])
    except Exception: return
    ap = get_appeal(aid)
    if not ap: return
    context.user_data["chat_aid"] = aid; context.user_data["chat_role"] = "admin_appeal"
    await q.message.reply_text(
        f"{ce('info')} <b>Message Appellant — Appeal #{aid}</b>\n\n"
        f"Appellant: <code>{ap[3]}</code>\n\n<b>Type your message.</b>\n\n/cancel to abort.",
        parse_mode="HTML")

async def cb_ac_hist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q or _uid(q) not in ADMIN_IDS:
        await safe_answer(q, "No.", alert=True); return
    await safe_answer(q)
    try: aid = int(q.data.split(":", 1)[1])
    except Exception: return
    msgs = get_appeal_messages(aid)
    if not msgs:
        await q.message.reply_text(f"{ce('info')} <b>No messages yet.</b>", parse_mode="HTML")
        return
    lines = [f"{ce('list')} <b>Chat — Appeal #{aid}</b>\n"]
    for role, text, ts in msgs:
        who = "DF Team" if role == "admin" else "Appellant"
        lines.append(f"<b>{who}:</b> {html.escape(text)}")
    txt = "\n\n".join(lines)
    if len(txt) > 4000: txt = txt[-4000:]
    await q.message.reply_text(txt, parse_mode="HTML")

async def recv_admin_appeal_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if _uid(update) not in ADMIN_IDS: return
    if context.user_data.get("chat_role") != "admin_appeal": return
    aid = context.user_data.get("chat_aid")
    if not aid: return
    if not update.message or not update.message.text: return
    text = update.message.text.strip()
    if len(text) < 1 or len(text) > 2000:
        await safe_reply(update.message, f"{ce('cross')} <b>1-2000 chars.</b>", parse_mode="HTML")
        return
    ap = get_appeal(aid)
    if not ap:
        context.user_data.pop("chat_aid", None); context.user_data.pop("chat_role", None)
        return
    add_appeal_message(aid, _uid(update), "admin", text)
    try:
        await safe_send(context.bot, ap[3],
            f"{ce('info')} <b>DF Team — About your Appeal #{aid}</b>\n\n"
            f"{html.escape(text)}\n\n<i>Reply to respond.</i>", parse_mode="HTML")
        await safe_reply(update.message, f"{ce('check')} <b>Sent to appellant.</b>", parse_mode="HTML")
    except Exception as e:
        await safe_reply(update.message, f"{ce('cross')} <b>{html.escape(str(e))}</b>", parse_mode="HTML")

async def recv_appellant_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = _uid(update)
    if uid == 0 or uid in ADMIN_IDS: return
    if not update.message: return
    reply_to = getattr(update.message, "reply_to_message", None)
    if not reply_to: return
    bot_id = context.bot.id if hasattr(context.bot, "id") else 0
    sender = getattr(reply_to, "from_user", None)
    if not sender or sender.id != bot_id: return
    reply_text = reply_to.text or reply_to.caption or ""
    m = re.search(r"About your Appeal #(\d+)", reply_text)
    if not m: return
    aid = int(m.group(1))
    ap = get_appeal(aid)
    if not ap: return
    text = update.message.text or update.message.caption or "[media]"
    if len(text) > 2000: text = text[:2000] + "..."
    add_appeal_message(aid, uid, "appellant", text)
    uname = update.effective_user.username or f"id{uid}"
    for admin_id in ADMIN_IDS:
        try:
            await safe_send(context.bot, admin_id,
                f"{ce('info')} <b>Appellant Reply — Appeal #{aid}</b>\n\n"
                f"From: @{html.escape(uname)}\n\n{html.escape(text)}",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("Reply", callback_data=f"ac_chat:{aid}")
                ]]))
        except Exception: pass
    await safe_reply(update.message, f"{ce('check')} <b>Sent to DF Team.</b>", parse_mode="HTML")

# ============================================================
# /check
# ============================================================
async def cmd_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if _uid(update) == 0: return
    args = context.args or []
    if not args:
        await safe_reply(update.message, f"{ce('info')} <code>/check @username</code>", parse_mode="HTML")
        return
    clean = normalize_username(args[0])
    if not clean:
        await safe_reply(update.message, f"{ce('cross')} <b>Invalid.</b>", parse_mode="HTML")
        return
    thinking = None
    try: thinking = await update.message.reply_text(f"{ce('time')} <b>Checking…</b>", parse_mode="HTML")
    except Exception: pass
    reports, victims, total = get_scammer_stats(clean)
    dn = clean; pb = None
    try: dn = await get_display_name(clean)
    except Exception: pass
    try: pb = await get_profile_photo(clean)
    except Exception: pass
    uid = await resolve_username(clean)
    trusted = False
    if uid: trusted = is_trusted(uid)
    if not trusted:
        t_id = get_trusted_by_username(clean)
        if t_id:
            trusted = True
            if not uid: uid = t_id
    rating, rating_count = (0.0, 0)
    if uid: rating, rating_count = get_user_rating(uid)
    if trusted: status = "TRUSTED"
    elif reports > 0: status = "SCAMMER"
    else: status = "UNVERIFIED"
    bo = None
    if PILLOW_OK:
        func = get_banner_func()
        try:
            bo = func(clean, dn, reports, victims, total, pb, status,
                     rating=rating, rating_count=rating_count, trusted=trusted)
        except Exception as e:
            log.error(f"[GFX] Custom failed, fallback: {e}")
            bo = make_banner(clean, dn, reports, victims, total, pb, status,
                            rating=rating, rating_count=rating_count, trusted=trusted)
    if thinking:
        try: await thinking.delete()
        except Exception: pass
    badge = ""
    if trusted: badge = f"\n{ce('check')} <b>TRUSTED USER</b>"
    elif reports > 0: badge = f"\n{ce('warning')} <b>SCAMMER</b>"
    cap = (f"{ce('shield')} <b>DealFound Scam Check</b>\n\n"
           f"{ce('user')} <b>@{html.escape(clean)}</b>{badge}\n"
           f"{ce('list')} <b>Reports:</b> <b>{reports}</b>\n"
           f"🧑 <b>Victims:</b> <b>{victims}</b>\n"
           f"{ce('money')} <b>Total Scammed:</b> <b>${total}</b>")
    if rating_count > 0:
        cap += f"\n⭐ <b>Rating:</b> <b>{rating:.1f}/5</b> ({rating_count} reviews)"
    if bo:
        try:
            await update.message.reply_photo(photo=bo, caption=cap, parse_mode="HTML")
            return
        except Exception: pass
    await safe_reply(update.message, cap, parse_mode="HTML")

# ============================================================
# CUSTOM GFX COMMANDS
# ============================================================
async def cmd_addgfxcode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if _uid(update) not in ADMIN_IDS: return
    context.user_data["await_gfx_code"] = True
    await update.message.reply_text(
        f"{ce('info')} <b>Add Custom GFX Code</b>\n\n"
        f"Send Python code with <code>def make_banner(...)</code>\n\n/cancel to abort.",
        parse_mode="HTML")

async def recv_gfx_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("await_gfx_code"): return
    if _uid(update) not in ADMIN_IDS: return
    msg = update.message
    if not msg: return
    code = msg.text or msg.caption or ""
    if not code: return
    code = re.sub(r"^```(?:python)?\s*\n?", "", code)
    code = re.sub(r"\n?```\s*$", "", code)
    if "def make_banner" not in code:
        await msg.reply_text(f"{ce('cross')} <b>Code must have def make_banner.</b>", parse_mode="HTML")
        return
    try: compile(code, "<custom_gfx>", "exec")
    except SyntaxError as e:
        await msg.reply_text(f"{ce('cross')} <b>Syntax error:</b>\n<code>{html.escape(str(e))}</code>",
                             parse_mode="HTML")
        return
    try:
        with open(CUSTOM_GFX_FILE, "w", encoding="utf-8") as f: f.write(code)
    except Exception as e:
        await msg.reply_text(f"{ce('cross')} <b>Save failed:</b> <code>{html.escape(str(e))}</code>",
                             parse_mode="HTML")
        return
    func = reload_gfx()
    context.user_data.pop("await_gfx_code", None)
    if func is None:
        await msg.reply_text(f"{ce('warning')} <b>Saved but failed to load.</b>", parse_mode="HTML")
        return
    await msg.reply_text(
        f"{ce('check')} <b>Custom GFX saved!</b>\n\nTest: <code>/testgfx @username</code>",
        parse_mode="HTML")

async def cmd_removegfxcode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if _uid(update) not in ADMIN_IDS: return
    try:
        if os.path.exists(CUSTOM_GFX_FILE): os.remove(CUSTOM_GFX_FILE)
        reload_gfx()
        await update.message.reply_text(f"{ce('check')} <b>Custom GFX removed.</b>", parse_mode="HTML")
    except Exception as e:
        await update.message.reply_text(f"{ce('cross')} <b>{html.escape(str(e))}</b>", parse_mode="HTML")

async def cmd_showgfxcode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if _uid(update) not in ADMIN_IDS: return
    if not os.path.exists(CUSTOM_GFX_FILE):
        await update.message.reply_text(f"{ce('info')} <b>No custom GFX.</b>", parse_mode="HTML")
        return
    try:
        with open(CUSTOM_GFX_FILE, "r", encoding="utf-8") as f: code = f.read()
        bio = io.BytesIO(code.encode("utf-8")); bio.name = "custom_gfx.py"
        await update.message.reply_document(document=bio, filename="custom_gfx.py")
    except Exception as e:
        await update.message.reply_text(f"{ce('cross')} <b>{html.escape(str(e))}</b>", parse_mode="HTML")

async def cmd_testgfx(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if _uid(update) not in ADMIN_IDS: return
    args = context.args or []
    if not args:
        await update.message.reply_text(f"{ce('info')} <b>Usage:</b> <code>/testgfx @username</code>",
                                        parse_mode="HTML")
        return
    clean = normalize_username(args[0])
    if not clean:
        await update.message.reply_text(f"{ce('cross')} <b>Invalid.</b>", parse_mode="HTML")
        return
    thinking = await update.message.reply_text(f"{ce('time')} <b>Generating…</b>", parse_mode="HTML")
    try:
        dn = clean; pb = None
        try: dn = await get_display_name(clean)
        except Exception: pass
        try: pb = await get_profile_photo(clean)
        except Exception: pass
        reports, victims, total = get_scammer_stats(clean)
        uid = await resolve_username(clean)
        trusted = False
        if uid: trusted = is_trusted(uid)
        rating, rating_count = (0.0, 0)
        if uid: rating, rating_count = get_user_rating(uid)
        if trusted: status = "TRUSTED"
        elif reports > 0: status = "SCAMMER"
        else: status = "UNVERIFIED"
        func = get_banner_func()
        label = "CUSTOM" if func is not make_banner else "DEFAULT"
        try:
            bio = func(clean, dn, reports, victims, total, pb, status,
                       rating=rating, rating_count=rating_count, trusted=trusted)
        except Exception as e:
            log.exception("testgfx custom")
            await thinking.edit_text(f"{ce('cross')} <b>Custom failed:</b>\n<code>{html.escape(str(e)[:300])}</code>",
                                     parse_mode="HTML")
            return
        if bio is None:
            await thinking.edit_text(f"{ce('cross')} <b>Returned None.</b>", parse_mode="HTML")
            return
        await thinking.delete()
        await update.message.reply_photo(photo=bio,
            caption=f"{ce('check')} <b>GFX [{label}]</b> for @{clean}\nStatus: <b>{status}</b>")
    except Exception as e:
        log.exception("testgfx")
        try:
            await thinking.edit_text(f"{ce('cross')} <b>Error:</b> <code>{html.escape(str(e)[:300])}</code>",
                                     parse_mode="HTML")
        except Exception: pass

# ============================================================
# CUSTOM FIX COMMANDS
# ============================================================
async def cmd_addfixcode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if _uid(update) not in ADMIN_IDS: return
    context.user_data["await_fix_code"] = True
    await update.message.reply_text(
        f"{ce('info')} <b>Add Custom Fix Code</b>\n\n"
        f"Send Python code. Exec in globals — overrides functions.\n\n/cancel to abort.",
        parse_mode="HTML")

async def recv_fix_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("await_fix_code"): return
    if _uid(update) not in ADMIN_IDS: return
    msg = update.message
    if not msg: return
    code = msg.text or msg.caption or ""
    if not code: return
    code = re.sub(r"^```(?:python)?\s*\n?", "", code)
    code = re.sub(r"\n?```\s*$", "", code)
    try: compile(code, "<custom_fix>", "exec")
    except SyntaxError as e:
        await msg.reply_text(f"{ce('cross')} <b>Syntax error:</b>\n<code>{html.escape(str(e))}</code>",
                             parse_mode="HTML")
        return
    try:
        with open(CUSTOM_FIX_FILE, "w", encoding="utf-8") as f: f.write(code)
    except Exception as e:
        await msg.reply_text(f"{ce('cross')} <b>Save failed:</b> <code>{html.escape(str(e))}</code>",
                             parse_mode="HTML")
        return
    ok = reload_fix()
    context.user_data.pop("await_fix_code", None)
    if not ok:
        await msg.reply_text(f"{ce('warning')} <b>Saved but failed to load.</b>", parse_mode="HTML")
        return
    await msg.reply_text(f"{ce('check')} <b>Custom fix saved!</b>", parse_mode="HTML")

async def cmd_removefixcode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if _uid(update) not in ADMIN_IDS: return
    try:
        if os.path.exists(CUSTOM_FIX_FILE): os.remove(CUSTOM_FIX_FILE)
        await update.message.reply_text(
            f"{ce('warning')} <b>Fix removed.</b>\n\nBot restart karo.", parse_mode="HTML")
    except Exception as e:
        await update.message.reply_text(f"{ce('cross')} <b>{html.escape(str(e))}</b>", parse_mode="HTML")

async def cmd_showfixcode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if _uid(update) not in ADMIN_IDS: return
    if not os.path.exists(CUSTOM_FIX_FILE):
        await update.message.reply_text(f"{ce('info')} <b>No custom fix.</b>", parse_mode="HTML")
        return
    try:
        with open(CUSTOM_FIX_FILE, "r", encoding="utf-8") as f: code = f.read()
        bio = io.BytesIO(code.encode("utf-8")); bio.name = "custom_fix.py"
        await update.message.reply_document(document=bio, filename="custom_fix.py")
    except Exception as e:
        await update.message.reply_text(f"{ce('cross')} <b>{html.escape(str(e))}</b>", parse_mode="HTML")

# ============================================================
# NEW ADMIN COMMANDS
# ============================================================
async def cmd_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if _uid(update) not in ADMIN_IDS: return
    args = context.args or []
    if not args:
        context.user_data["await_broadcast"] = True
        await update.message.reply_text(f"{ce('info')} <b>Send broadcast message.</b>\n\n/cancel to abort.",
                                        parse_mode="HTML")
        return
    await _do_broadcast(update, context, " ".join(args))

async def recv_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("await_broadcast"): return
    if _uid(update) not in ADMIN_IDS: return
    if not update.message or not update.message.text: return
    text = update.message.text.strip()
    context.user_data.pop("await_broadcast", None)
    await _do_broadcast(update, context, text)

async def _do_broadcast(update, context, text):
    con = db(); cur = con.cursor()
    cur.execute("SELECT DISTINCT reporter_id FROM reports WHERE reporter_id > 0")
    users = [r[0] for r in cur.fetchall()]
    con.close()
    if not users:
        await update.message.reply_text(f"{ce('info')} <b>No users.</b>", parse_mode="HTML")
        return
    status = await update.message.reply_text(f"{ce('time')} <b>Broadcasting to {len(users)}…</b>",
                                             parse_mode="HTML")
    ok = 0; fail = 0
    msg = f"{ce('info')} <b>DealFound Update</b>\n\n{html.escape(text)}"
    for uid in users:
        if uid in ADMIN_IDS: continue
        try:
            await context.bot.send_message(uid, msg, parse_mode="HTML", disable_web_page_preview=True)
            ok += 1
            await asyncio.sleep(0.05)
        except Exception: fail += 1
    try:
        await status.edit_text(f"{ce('check')} <b>Done</b>\n\nSent: <b>{ok}</b>\nFailed: <b>{fail}</b>",
                               parse_mode="HTML")
    except Exception: pass

async def cmd_restartbot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if _uid(update) not in ADMIN_IDS: return
    await update.message.reply_text(f"{ce('warning')} <b>Restarting bot…</b>", parse_mode="HTML")
    await asyncio.sleep(1)
    try: await shutdown_resolver()
    except Exception: pass
    python = sys.executable
    os.execv(python, [python] + sys.argv)

async def cmd_backupdb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if _uid(update) not in ADMIN_IDS: return
    try:
        if not os.path.exists(DB_PATH):
            await update.message.reply_text(f"{ce('cross')} <b>DB not found.</b>", parse_mode="HTML")
            return
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        fname = f"dealfound_backup_{ts}.db"
        with open(DB_PATH, "rb") as f: bio = io.BytesIO(f.read())
        bio.name = fname
        await update.message.reply_document(document=bio, filename=fname,
            caption=f"{ce('check')} <b>Backup</b> <code>{ts}</code>")
    except Exception as e:
        await update.message.reply_text(f"{ce('cross')} <b>{html.escape(str(e))}</b>", parse_mode="HTML")

async def cmd_clearidcache(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if _uid(update) not in ADMIN_IDS: return
    try:
        con = db(); cur = con.cursor()
        cur.execute("SELECT COUNT(*) FROM user_cache")
        before = cur.fetchone()[0] or 0
        cur.execute("DELETE FROM user_cache")
        con.commit(); con.close()
        await update.message.reply_text(f"{ce('check')} <b>Cache cleared ({before})</b>", parse_mode="HTML")
    except Exception as e:
        await update.message.reply_text(f"{ce('cross')} <b>{html.escape(str(e))}</b>", parse_mode="HTML")

async def cmd_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if _uid(update) not in ADMIN_IDS: return
    con = db(); cur = con.cursor()
    cur.execute("SELECT COUNT(DISTINCT reporter_id) FROM reports")
    tu = cur.fetchone()[0] or 0
    cur.execute("SELECT COUNT(*) FROM reports")
    tr = cur.fetchone()[0] or 0
    cur.execute("SELECT COUNT(*) FROM user_cache")
    cn = cur.fetchone()[0] or 0
    cur.execute("SELECT COUNT(*) FROM trusted_users")
    tn = cur.fetchone()[0] or 0
    con.close()
    await update.message.reply_text(
        f"{ce('info')} <b>Bot Users</b>\n\n"
        f"Unique reporters: <b>{tu}</b>\nTotal reports: <b>{tr}</b>\n"
        f"Cached IDs: <b>{cn}</b>\nTrusted users: <b>{tn}</b>",
        parse_mode="HTML")

async def cb_ad_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q or _uid(q) not in ADMIN_IDS: return
    await safe_answer(q)
    context.user_data["await_broadcast"] = True
    await q.message.reply_text(f"{ce('info')} <b>Send broadcast message.</b>\n\n/cancel to abort.",
                               parse_mode="HTML")

async def cb_ad_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q or _uid(q) not in ADMIN_IDS: return
    await safe_answer(q)
    con = db(); cur = con.cursor()
    cur.execute("SELECT COUNT(DISTINCT reporter_id) FROM reports")
    tu = cur.fetchone()[0] or 0
    cur.execute("SELECT COUNT(*) FROM reports")
    tr = cur.fetchone()[0] or 0
    cur.execute("SELECT COUNT(*) FROM user_cache")
    cn = cur.fetchone()[0] or 0
    cur.execute("SELECT COUNT(*) FROM trusted_users")
    tn = cur.fetchone()[0] or 0
    con.close()
    await safe_edit(q,
        f"{ce('info')} <b>Bot Users</b>\n\nUnique: <b>{tu}</b>\nReports: <b>{tr}</b>\n"
        f"Cache: <b>{cn}</b>\nTrusted: <b>{tn}</b>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Back", callback_data="ad_home")]]))

async def cb_ad_backup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q or _uid(q) not in ADMIN_IDS: return
    await safe_answer(q)
    if not os.path.exists(DB_PATH):
        await q.message.reply_text(f"{ce('cross')} <b>DB not found.</b>", parse_mode="HTML")
        return
    try:
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        fname = f"dealfound_backup_{ts}.db"
        with open(DB_PATH, "rb") as f: bio = io.BytesIO(f.read())
        bio.name = fname
        await q.message.reply_document(document=bio, filename=fname,
            caption=f"{ce('check')} <b>DB Backup</b>")
    except Exception as e:
        await q.message.reply_text(f"{ce('cross')} <b>{html.escape(str(e))}</b>", parse_mode="HTML")

async def cb_ad_clearcache(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q or _uid(q) not in ADMIN_IDS: return
    await safe_answer(q, "Clearing…")
    try:
        con = db(); cur = con.cursor()
        cur.execute("SELECT COUNT(*) FROM user_cache")
        before = cur.fetchone()[0] or 0
        cur.execute("DELETE FROM user_cache")
        con.commit(); con.close()
        await q.message.reply_text(f"{ce('check')} <b>Cache cleared ({before})</b>", parse_mode="HTML")
    except Exception as e:
        await q.message.reply_text(f"{ce('cross')} <b>{html.escape(str(e))}</b>", parse_mode="HTML")

async def cb_ad_restart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q or _uid(q) not in ADMIN_IDS: return
    await safe_answer(q, "Restarting…")
    await q.message.reply_text(f"{ce('warning')} <b>Restarting bot…</b>", parse_mode="HTML")
    await asyncio.sleep(1)
    try: await shutdown_resolver()
    except Exception: pass
    python = sys.executable
    os.execv(python, [python] + sys.argv)

# ============================================================
# TERMINAL
# ============================================================
async def cmd_terminal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if _uid(update) not in ADMIN_IDS: return
    if not LOG_BUFFER:
        await update.message.reply_text(f"{ce('info')} <b>Log empty.</b>", parse_mode="HTML")
        return
    lines = list(LOG_BUFFER)[-50:]
    text = "\n".join(lines)
    if len(text) > 3500: text = text[-3500:]
    await update.message.reply_text(f"{ce('list')} <b>Last {len(lines)} lines:</b>\n\n<pre>{html.escape(text)}</pre>",
                                    parse_mode="HTML")

async def cmd_terminalstart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if _uid(update) not in ADMIN_IDS: return
    LIVE_LOG_ON["enabled"] = True
    LIVE_LOG_USERS.add(_uid(update))
    await update.message.reply_text(f"{ce('check')} <b>Live ON</b>\n\nOff: /terminalstop", parse_mode="HTML")

async def cmd_terminalstop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if _uid(update) not in ADMIN_IDS: return
    LIVE_LOG_ON["enabled"] = False
    LIVE_LOG_USERS.discard(_uid(update))
    await update.message.reply_text(f"{ce('cross')} <b>Live OFF</b>", parse_mode="HTML")

async def cmd_terminalclear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if _uid(update) not in ADMIN_IDS: return
    LOG_BUFFER.clear()
    await update.message.reply_text(f"{ce('check')} <b>Cleared</b>", parse_mode="HTML")

async def cmd_session_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if _uid(update) not in ADMIN_IDS: return
    sess = _session_exists()
    r = "NOT FOUND"
    if sess:
        try:
            client = await get_resolver()
            if client:
                me = await client.get_me()
                r = f"OK @{getattr(me,'username','?')} (id={me.id})"
            else: r = "File exists but not authorized"
        except Exception as e: r = f"Error: {e}"
    await update.message.reply_text(f"{ce('info')} <b>Session:</b> {html.escape(r)}", parse_mode="HTML")

async def cb_ad_term(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q or _uid(q) not in ADMIN_IDS: return
    await safe_answer(q)
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("Start Live", callback_data="ad_termstart"),
         InlineKeyboardButton("Stop Live", callback_data="ad_termstop")],
        [InlineKeyboardButton("Show Last 50", callback_data="ad_termshow"),
         InlineKeyboardButton("Clear", callback_data="ad_termclr")],
        [InlineKeyboardButton("Session Info", callback_data="ad_sessinfo")],
        [InlineKeyboardButton("Back", callback_data="ad_home")],
    ])
    await safe_edit(q, f"{ce('info')} <b>Terminal</b>\n\nLive: <b>{'ON' if LIVE_LOG_ON['enabled'] else 'OFF'}</b>\nBuffer: <b>{len(LOG_BUFFER)}</b>",
                    parse_mode="HTML", reply_markup=kb)

async def cb_ad_termstart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q or _uid(q) not in ADMIN_IDS: return
    await safe_answer(q, "Live ON")
    LIVE_LOG_ON["enabled"] = True
    LIVE_LOG_USERS.add(_uid(q))
    await cb_ad_term(update, context)

async def cb_ad_termstop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q or _uid(q) not in ADMIN_IDS: return
    await safe_answer(q, "Live OFF")
    LIVE_LOG_ON["enabled"] = False
    LIVE_LOG_USERS.discard(_uid(q))
    await cb_ad_term(update, context)

async def cb_ad_termshow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q or _uid(q) not in ADMIN_IDS: return
    await safe_answer(q)
    if not LOG_BUFFER:
        await q.message.reply_text(f"{ce('info')} <b>Log empty.</b>", parse_mode="HTML")
        return
    lines = list(LOG_BUFFER)[-50:]
    text = "\n".join(lines)
    if len(text) > 3500: text = text[-3500:]
    await q.message.reply_text(f"{ce('list')} <b>Last {len(lines)}:</b>\n\n<pre>{html.escape(text)}</pre>",
                               parse_mode="HTML")

async def cb_ad_termclr(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q or _uid(q) not in ADMIN_IDS: return
    await safe_answer(q, "Cleared")
    LOG_BUFFER.clear()
    await cb_ad_term(update, context)

async def cb_ad_sessinfo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q or _uid(q) not in ADMIN_IDS: return
    await safe_answer(q)
    sess = _session_exists()
    r = "NOT FOUND"
    if sess:
        try:
            client = await get_resolver()
            if client:
                me = await client.get_me()
                r = f"OK @{getattr(me,'username','?')} (id={me.id})"
            else: r = "File exists but not authorized"
        except Exception as e: r = f"Error: {e}"
    await q.message.reply_text(f"{ce('info')} <b>Session:</b> {html.escape(r)}", parse_mode="HTML")

# ============================================================
# SET ID
# ============================================================
async def cb_setid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q or _uid(q) not in ADMIN_IDS:
        await safe_answer(q, "No.", alert=True); return
    await safe_answer(q)
    try: rid = int(q.data.split(":", 1)[1])
    except Exception: return
    context.user_data["df_wait_rid"] = rid
    try:
        await q.message.reply_text(
            f"{ce('info')} <b>Set User ID — Report #{rid}</b>\n\n"
            f"<i>Forward scammer's message (best) or enter ID manually.</i>",
            parse_mode="HTML", reply_markup=set_id_kb(rid))
    except Exception: pass

async def cb_fwd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q or _uid(q) not in ADMIN_IDS: return
    await safe_answer(q)
    try: rid = int(q.data.split(":", 1)[1])
    except Exception: return
    context.user_data["df_wait_rid"] = rid; context.user_data["df_mode"] = "forward"
    await q.message.reply_text(f"<b>Forward scammer's message now.</b>\n\n/cancel to abort.", parse_mode="HTML")

async def cb_man(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q or _uid(q) not in ADMIN_IDS: return
    await safe_answer(q)
    try: rid = int(q.data.split(":", 1)[1])
    except Exception: return
    context.user_data["df_wait_rid"] = rid; context.user_data["df_mode"] = "manual"
    await q.message.reply_text(f"<b>Send numeric User ID.</b>\n\n/cancel to abort.", parse_mode="HTML")

async def cb_cancel_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q or _uid(q) not in ADMIN_IDS: return
    await safe_answer(q)
    context.user_data.pop("df_wait_rid", None); context.user_data.pop("df_mode", None)
    await q.message.reply_text(f"{ce('cross')} <b>Cancelled.</b>", parse_mode="HTML")

async def recv_forward(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if _uid(update) not in ADMIN_IDS: return
    if context.user_data.get("df_mode") != "forward": return
    rid = context.user_data.get("df_wait_rid")
    if not rid: return
    uid, src = try_extract_id(update.message)
    context.user_data.pop("df_mode", None); context.user_data.pop("df_wait_rid", None)
    if uid:
        set_scammer_id(rid, uid)
        await update.message.reply_text(f"{ce('check')} <b>ID saved: {uid}</b>", parse_mode="HTML")
    else:
        await update.message.reply_text(f"{ce('warning')} <b>Could not extract.</b>", parse_mode="HTML")

async def recv_manual(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if _uid(update) not in ADMIN_IDS: return
    if context.user_data.get("df_mode") != "manual": return
    rid = context.user_data.get("df_wait_rid")
    if not rid: return
    if not update.message or not update.message.text: return
    v = update.message.text.strip()
    if not v.isdigit():
        await safe_reply(update.message, f"{ce('cross')} <b>Numeric only.</b>", parse_mode="HTML")
        return
    set_scammer_id(rid, int(v))
    context.user_data.pop("df_mode", None); context.user_data.pop("df_wait_rid", None)
    await safe_reply(update.message, f"{ce('check')} <b>ID saved: {v}</b>", parse_mode="HTML")

# ============================================================
# PUBLISH — tg://user → tg://openmessage → username
# ============================================================
async def _publish_report(bot, deal):
    (rid, r_id, r_un, s_un, s_id, amt, desc, proof, status, cat, rat) = deal
    clean = str(s_un or "").strip().lstrip("@").strip()
    try: stored_id = int(s_id) if s_id else 0
    except Exception: stored_id = 0

    if not clean and not stored_id:
        return None, "no username or ID"

    resolved_id = 0
    if clean and not clean.startswith("id"):
        try: resolved_id = await resolve_username(clean, max_retries=5)
        except Exception: resolved_id = 0
    final_id = resolved_id or stored_id

    # Caption with tg://user link (best for caption)
    if clean and not clean.startswith("id"):
        if final_id and final_id > 0:
            name_disp = f'<a href="tg://user?id={final_id}">@{html.escape(clean)}</a>'
        else:
            name_disp = f"@{html.escape(clean)}"
    elif clean.startswith("id"):
        name_disp = f"ID <code>{html.escape(clean[2:])}</code>"
    else:
        name_disp = "Unknown"

    caption = (f"{ce('cross')} <b>{name_disp}</b>\n\n"
               f"<b>has been reported as a scammer following DF Team review.</b>")
    if final_id and final_id > 0:
        caption += f"\n🆔 <b>User ID:</b> <code>{final_id}</code>"

    # ============ BUTTON ATTEMPTS — tg://user FIRST ============
    proof_btn = None
    if proof and proof.startswith("http"):
        proof_btn = InlineKeyboardButton("View Proof", url=proof)

    attempts = []

    # 1. tg://user?id=ID
    if final_id and final_id > 0:
        row = [InlineKeyboardButton("View Profile", url=f"tg://user?id={final_id}")]
        if proof_btn: row.append(proof_btn)
        attempts.append([row])

    # 2. tg://openmessage?user_id=ID
    if final_id and final_id > 0:
        row = [InlineKeyboardButton("View Profile", url=f"tg://openmessage?user_id={final_id}")]
        if proof_btn: row.append(proof_btn)
        attempts.append([row])

    # 3. https://t.me/username (GUARANTEED FALLBACK)
    if clean and not clean.startswith("id"):
        row = [InlineKeyboardButton("View Profile", url=f"https://t.me/{clean}")]
        if proof_btn: row.append(proof_btn)
        attempts.append([row])
    elif clean.startswith("id") and len(clean) > 2:
        row = [InlineKeyboardButton("View Profile", url=f"https://t.me/{clean[2:]}")]
        if proof_btn: row.append(proof_btn)
        attempts.append([row])

    # 4. Proof only
    if proof_btn:
        attempts.append([[proof_btn]])

    # 5. No buttons
    attempts.append(None)

    all_rows = attempts[0] if attempts else []

    # Banner
    saved = get_banner()
    gfx = None
    if PILLOW_OK and not saved:
        reports, victims, total = get_scammer_stats(clean)
        reports = max(reports, 1); victims = max(victims, 1)
        dn = clean if clean else f"User {final_id}"
        pb = None
        if clean and resolved_id:
            try: dn = await get_display_name(clean)
            except Exception: pass
            try: pb = await get_profile_photo(clean)
            except Exception: pass
        func = get_banner_func()
        try:
            gfx = func(clean if not clean.startswith("id") else f"id{final_id}",
                       dn, reports, victims, total, pb, "SCAMMER")
        except Exception as e:
            log.error(f"[GFX] Custom failed: {e}")
            gfx = make_banner(clean if not clean.startswith("id") else f"id{final_id}",
                              dn, reports, victims, total, pb, "SCAMMER")

    # Send with fallback chain
    labels = ["tg_user", "openmessage", "username", "proof_only", "no_buttons"]
    sent = None; last_err = None; used = None
    for idx, rows in enumerate(attempts):
        lbl = labels[idx] if idx < len(labels) else f"try{idx}"
        kb = InlineKeyboardMarkup(rows) if rows else None

        if saved:
            try:
                sent = await bot.send_photo(chat_id=REPORT_CHANNEL_ID, photo=saved,
                                            caption=caption, parse_mode="HTML", reply_markup=kb)
                used = f"saved+{lbl}"; break
            except Exception as e:
                last_err = str(e)
                el = last_err.lower()
                if "button" not in el and "user" not in el:
                    log.warning(f"[Publish] {lbl} saved: {last_err[:80]}")

        if gfx is not None:
            try:
                gfx.seek(0)
                sent = await bot.send_photo(chat_id=REPORT_CHANNEL_ID, photo=gfx,
                                            caption=caption, parse_mode="HTML", reply_markup=kb)
                used = f"gfx+{lbl}"; break
            except Exception as e:
                last_err = str(e)
                el = last_err.lower()
                if "button" not in el and "user" not in el:
                    log.warning(f"[Publish] {lbl} gfx: {last_err[:80]}")

        try:
            sent = await bot.send_message(chat_id=REPORT_CHANNEL_ID, text=caption,
                                          parse_mode="HTML", reply_markup=kb,
                                          disable_web_page_preview=True)
            used = f"text+{lbl}"; break
        except Exception as e:
            last_err = str(e)
            el = last_err.lower()
            if "button" not in el and "user" not in el:
                log.warning(f"[Publish] {lbl} text: {last_err[:80]}")

    if sent is None:
        return None, last_err or "unknown"

    log.info(f"[Publish] OK — {used}")
    link = build_post_link(sent.message_id)
    save_report_post(rid, REPORT_CHANNEL_ID, sent.message_id, link)
    return sent, used

async def cb_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q or _uid(q) not in ADMIN_IDS:
        await safe_answer(q, "No.", alert=True); return
    await safe_answer(q)
    try:
        action, rid_s = q.data.split(":", 1); rid = int(rid_s)
    except Exception: return
    rep = get_report(rid)
    if not rep:
        await safe_edit(q, f"{ce('cross')} <b>Not found.</b>", parse_mode="HTML"); return
    status = rep[8]
    if status != "pending":
        await safe_answer(q, f"Already {status}.", alert=True); return
    if action == "df_reject":
        set_report_status(rid, "rejected")
        try: await safe_send(context.bot, rep[1], f"{ce('cross')} <b>Report #{rid} Rejected</b>", parse_mode="HTML")
        except Exception: pass
        await safe_edit(q, f"{ce('cross')} <b>Report #{rid} rejected.</b>", parse_mode="HTML")
        return
    sent, mode = await _publish_report(context.bot, rep)
    if sent is None:
        try:
            await q.message.reply_text(f"{ce('cross')} <b>Publish failed:</b> <code>{html.escape(str(mode))}</code>",
                                       parse_mode="HTML")
        except Exception: pass
        return
    set_report_status(rid, "approved")
    try:
        await safe_send(context.bot, rep[1], f"{ce('check')} <b>Report #{rid} Approved</b>", parse_mode="HTML")
    except Exception: pass
    await safe_edit(q, f"{ce('check')} <b>Report #{rid} approved.</b>\nMode: <b>{html.escape(str(mode))}</b>",
                    parse_mode="HTML")

# ============================================================
# BULK REPUBLISH
# ============================================================
async def _bulk_republish(bot, chat_id):
    con = db(); cur = con.cursor()
    cur.execute("SELECT id FROM reports WHERE status='approved' ORDER BY id ASC")
    rows = cur.fetchall(); con.close()
    if not rows: return 0, 0, []
    success = 0; failed = 0; errors = []
    for (rid,) in rows:
        try:
            rep = get_report(rid)
            if not rep: failed += 1; continue
            old = get_report_post(rid)
            if old:
                try: await bot.delete_message(chat_id=old[0], message_id=old[1])
                except Exception as e: log.warning(f"[BulkRepub] del #{rid}: {e}")
            sent, mode = await _publish_report(bot, rep)
            if sent is None:
                failed += 1; errors.append(f"#{rid}: {mode}"); continue
            success += 1
            await asyncio.sleep(1.5)
        except Exception as e:
            failed += 1; errors.append(f"#{rid}: {str(e)[:50]}")
    return success, failed, errors

async def cb_ad_repuball(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q or _uid(q) not in ADMIN_IDS: return
    await safe_answer(q)
    con = db(); cur = con.cursor()
    cur.execute("SELECT COUNT(*) FROM reports WHERE status='approved'")
    count = cur.fetchone()[0] or 0
    con.close()
    if count == 0:
        await safe_edit(q, f"{ce('info')} <b>No approved reports.</b>", parse_mode="HTML",
                        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Back", callback_data="ad_home")]]))
        return
    await safe_edit(q, f"{ce('warning')} <b>Republish All</b>\n\nTotal: <b>{count}</b>\n~{count*2} sec\n\nSure?",
                    parse_mode="HTML", reply_markup=repuball_kb())

async def cb_ad_repuball_yes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q or _uid(q) not in ADMIN_IDS: return
    await safe_answer(q)
    msg = await q.message.reply_text(f"{ce('time')} <b>Republishing…</b>", parse_mode="HTML")
    success, failed, errors = await _bulk_republish(context.bot, REPORT_CHANNEL_ID)
    txt = f"{ce('check')} <b>Done!</b>\n\nSuccess: <b>{success}</b>\nFailed: <b>{failed}</b>"
    if errors: txt += f"\n\n<b>Errors:</b>\n<code>{html.escape(chr(10).join(errors[:5]))}</code>"
    try: await msg.edit_text(txt, parse_mode="HTML")
    except Exception: await msg.reply_text(txt, parse_mode="HTML")

# ============================================================
# ADMIN PANEL
# ============================================================
async def cmd_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if _uid(update) not in ADMIN_IDS: return
    try: context.user_data.clear()
    except Exception: pass
    await update.message.reply_text(f"{ce('shield')} <b>Admin Panel</b>",
                                    parse_mode="HTML", reply_markup=admin_panel_kb())

async def cb_ad_home(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q or _uid(q) not in ADMIN_IDS: return
    await safe_answer(q)
    try: context.user_data.clear()
    except Exception: pass
    await safe_edit(q, f"{ce('shield')} <b>Admin Panel</b>", parse_mode="HTML", reply_markup=admin_panel_kb())

async def cb_ad_pending(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q or _uid(q) not in ADMIN_IDS: return
    await safe_answer(q)
    con = db(); cur = con.cursor()
    cur.execute("SELECT id, reporter_username, scammer_username, amount, scammer_user_id FROM reports WHERE status='pending' ORDER BY id DESC LIMIT 20")
    rows = cur.fetchall(); con.close()
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("Back", callback_data="ad_home")]])
    if not rows:
        await safe_edit(q, f"{ce('check')} <b>No pending.</b>", parse_mode="HTML", reply_markup=kb); return
    lines = [f"{ce('list')} <b>Pending ({len(rows)})</b>\n"]
    for rid, run, sun, amt, sid in rows:
        id_mark = "[ID]" if sid else "[?]"
        lines.append(f"<b>#{rid}</b> {id_mark} {html.escape(str(run or '?'))} -> {html.escape(str(sun or '?'))}")
    await safe_edit(q, "\n".join(lines), parse_mode="HTML", reply_markup=kb)

async def cb_ad_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q or _uid(q) not in ADMIN_IDS: return
    await safe_answer(q)
    con = db(); cur = con.cursor()
    cur.execute("SELECT status, COUNT(*) FROM reports GROUP BY status")
    rr = dict(cur.fetchall())
    cur.execute("SELECT status, COUNT(*) FROM appeals GROUP BY status")
    ar = dict(cur.fetchall())
    cur.execute("SELECT COUNT(*) FROM custom_buttons")
    btns = cur.fetchone()[0] or 0
    cur.execute("SELECT COUNT(*) FROM reports WHERE scammer_user_id=0 AND status='approved'")
    noid = cur.fetchone()[0] or 0
    cur.execute("SELECT COUNT(*) FROM trusted_users")
    trs = cur.fetchone()[0] or 0
    con.close()
    sess = _session_exists()
    gfx_s = "CUSTOM" if os.path.exists(CUSTOM_GFX_FILE) else "DEFAULT"
    fix_s = "LOADED" if os.path.exists(CUSTOM_FIX_FILE) else "NONE"
    text = (f"{ce('star')} <b>Stats</b>\n\n"
            f"Reports: P{rr.get('pending',0)} A{rr.get('approved',0)} R{rr.get('rejected',0)}\n"
            f"Appeals: P{ar.get('pending',0)} A{ar.get('accepted',0)} R{ar.get('rejected',0)}\n"
            f"Buttons: {btns} Trusted: {trs}\n"
            f"No ID: {noid}\nGFX: {gfx_s}\nFix: {fix_s}\n"
            f"Session: {'OK' if sess else 'MISSING'}\n"
            f"Telethon: {'ON' if TELETHON_OK else 'OFF'}\n"
            f"Pillow: {'ON' if PILLOW_OK else 'OFF'}")
    await safe_edit(q, text, parse_mode="HTML",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Back", callback_data="ad_home")]]))

async def cb_ad_appeals(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q or _uid(q) not in ADMIN_IDS: return
    await safe_answer(q)
    con = db(); cur = con.cursor()
    cur.execute("SELECT id, appellant_id, post_link FROM appeals WHERE status='pending' ORDER BY id DESC LIMIT 20")
    rows = cur.fetchall(); con.close()
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("Back", callback_data="ad_home")]])
    if not rows:
        await safe_edit(q, f"{ce('check')} <b>No pending appeals.</b>", parse_mode="HTML", reply_markup=kb); return
    lines = [f"<b>Appeals ({len(rows)})</b>\n"]
    for aid, apid, link in rows:
        lines.append(f"<b>#{aid}</b> {apid} {html.escape((link or '')[:40])}")
    await safe_edit(q, "\n".join(lines), parse_mode="HTML", reply_markup=kb)

async def cb_ad_setban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q or _uid(q) not in ADMIN_IDS: return
    await safe_answer(q)
    context.user_data["wait_banner"] = True; context.user_data["wait_wlc"] = False
    await q.message.reply_text(f"{ce('info')} <b>Send report banner as photo.</b>", parse_mode="HTML")

async def cb_ad_setwlc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q or _uid(q) not in ADMIN_IDS: return
    await safe_answer(q)
    context.user_data["wait_wlc"] = True; context.user_data["wait_banner"] = False
    await q.message.reply_text(f"{ce('info')} <b>Send welcome banner as photo.</b>", parse_mode="HTML")

async def cb_ad_delpost(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q or _uid(q) not in ADMIN_IDS: return
    await safe_answer(q)
    context.user_data["ad_mode"] = "delpost"
    await safe_edit(q, f"{ce('info')} <b>Send Report ID to delete:</b>", parse_mode="HTML",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Back", callback_data="ad_home")]]))

# ============================================================
# TRUSTED
# ============================================================
async def cb_ad_trusted(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q or _uid(q) not in ADMIN_IDS: return
    await safe_answer(q)
    rows = list_trusted()
    kb_rows = []
    lines = [f"{ce('list')} <b>Trusted ({len(rows)})</b>\n"]
    for uid, uname, note, added in rows:
        lines.append(f"<b>@{html.escape(uname or 'N/A')}</b> (<code>{uid}</code>)")
        kb_rows.append([InlineKeyboardButton(f"Unmark @{uname or uid}", callback_data=f"tr_del:{uid}")])
    kb_rows.append([InlineKeyboardButton("Back", callback_data="ad_home")])
    if not rows: lines.append("<i>None.</i>")
    await safe_edit(q, "\n".join(lines), parse_mode="HTML", reply_markup=InlineKeyboardMarkup(kb_rows))

async def cb_ad_marktrust(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q or _uid(q) not in ADMIN_IDS: return
    await safe_answer(q)
    context.user_data["ad_mode"] = "marktrust"
    await safe_edit(q, f"{ce('info')} <b>Mark Trusted</b>\n\nSend <b>@username</b>:",
                    parse_mode="HTML",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Cancel", callback_data="ad_home")]]))

async def cb_tr_del(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q or _uid(q) not in ADMIN_IDS: return
    await safe_answer(q)
    try: uid = int(q.data.split(":", 1)[1])
    except Exception: return
    unmark_trusted(uid)
    await cb_ad_trusted(update, context)

# ============================================================
# FIX OLD ID
# ============================================================
async def cb_ad_fixid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q or _uid(q) not in ADMIN_IDS: return
    await safe_answer(q)
    con = db(); cur = con.cursor()
    cur.execute("""SELECT id, scammer_username FROM reports
                   WHERE status='approved' AND (scammer_user_id=0 OR scammer_user_id IS NULL)
                   ORDER BY id DESC LIMIT 15""")
    rows = cur.fetchall(); con.close()
    if not rows:
        await safe_edit(q, f"{ce('check')} <b>No reports without ID.</b>", parse_mode="HTML",
                        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Back", callback_data="ad_home")]]))
        return
    lines = [f"{ce('warning')} <b>Missing User ID:</b>\n"]
    kb_rows = []
    for rid, sun in rows:
        lines.append(f"<b>#{rid}</b> {html.escape(str(sun))}")
        kb_rows.append([InlineKeyboardButton(f"Fix #{rid}", callback_data=f"fx_open:{rid}")])
    kb_rows.append([InlineKeyboardButton("Back", callback_data="ad_home")])
    await safe_edit(q, "\n".join(lines), parse_mode="HTML", reply_markup=InlineKeyboardMarkup(kb_rows))

async def cb_fx_open(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q or _uid(q) not in ADMIN_IDS: return
    await safe_answer(q)
    try: rid = int(q.data.split(":", 1)[1])
    except Exception: return
    rep = get_report(rid)
    if not rep: return
    await safe_edit(q,
        f"{ce('info')} <b>Fix Report #{rid}</b>\n\nTarget: <b>{html.escape(str(rep[3]))}</b>\nCurrent ID: <code>{rep[4] or 'none'}</code>",
        parse_mode="HTML", reply_markup=fixid_kb(rid))

async def cb_fx_fwd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q or _uid(q) not in ADMIN_IDS: return
    await safe_answer(q)
    try: rid = int(q.data.split(":", 1)[1])
    except Exception: return
    context.user_data["fx_rid"] = rid; context.user_data["fx_mode"] = "forward"
    await q.message.reply_text(f"<b>Forward scammer's message now.</b>\n\n/cancel to abort.", parse_mode="HTML")

async def cb_fx_man(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q or _uid(q) not in ADMIN_IDS: return
    await safe_answer(q)
    try: rid = int(q.data.split(":", 1)[1])
    except Exception: return
    context.user_data["fx_rid"] = rid; context.user_data["fx_mode"] = "manual"
    await q.message.reply_text(f"<b>Send numeric User ID for #{rid}.</b>\n\n/cancel to abort.", parse_mode="HTML")

async def cb_fx_repub(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q or _uid(q) not in ADMIN_IDS: return
    await safe_answer(q)
    try: rid = int(q.data.split(":", 1)[1])
    except Exception: return
    rep = get_report(rid)
    if not rep: return
    if not rep[4]:
        await q.message.reply_text(f"{ce('cross')} <b>No ID set.</b>", parse_mode="HTML")
        return
    old = get_report_post(rid)
    if old:
        try: await context.bot.delete_message(chat_id=old[0], message_id=old[1])
        except Exception as e: log.warning(f"del: {e}")
    sent, mode = await _publish_report(context.bot, rep)
    if sent is None:
        await q.message.reply_text(f"{ce('cross')} <b>Failed:</b> <code>{html.escape(str(mode))}</code>",
                                   parse_mode="HTML")
        return
    await q.message.reply_text(f"{ce('check')} <b>Republished #{rid}</b>\nMode: {html.escape(str(mode))}",
                               parse_mode="HTML")

async def recv_fx_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if _uid(update) not in ADMIN_IDS: return
    mode = context.user_data.get("fx_mode")
    if not mode: return
    rid = context.user_data.get("fx_rid")
    if not rid: return
    if mode == "forward":
        uid, src = try_extract_id(update.message)
        context.user_data.pop("fx_mode", None); context.user_data.pop("fx_rid", None)
        if uid:
            set_scammer_id(rid, uid)
            await update.message.reply_text(
                f"{ce('check')} <b>ID saved:</b> <code>{uid}</code>\n\nTap <b>Republish</b>.",
                parse_mode="HTML", reply_markup=fixid_kb(rid))
        else:
            await update.message.reply_text(f"{ce('warning')} <b>Could not extract.</b>", parse_mode="HTML")
    elif mode == "manual":
        if not update.message or not update.message.text: return
        v = update.message.text.strip()
        if not v.isdigit():
            await safe_reply(update.message, f"{ce('cross')} <b>Numeric only.</b>", parse_mode="HTML")
            return
        set_scammer_id(rid, int(v))
        context.user_data.pop("fx_mode", None); context.user_data.pop("fx_rid", None)
        await update.message.reply_text(
            f"{ce('check')} <b>ID saved:</b> <code>{v}</code>\n\nTap <b>Republish</b>.",
            parse_mode="HTML", reply_markup=fixid_kb(rid))

# ============================================================
# CUSTOM BUTTONS
# ============================================================
(BTN_LABEL, BTN_URL, BTN_LOC) = range(80, 83)

async def cb_ad_addbtn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q or _uid(q) not in ADMIN_IDS: return
    await safe_answer(q)
    try: context.user_data.clear()
    except Exception: pass
    await safe_edit(q, f"{ce('info')} <b>Add Custom Button — Step 1/3</b>\n\n<b>Enter label:</b>",
                    parse_mode="HTML",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Cancel", callback_data="ad_home")]]))
    return BTN_LABEL

async def btn_label(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text: return BTN_LABEL
    label = update.message.text.strip()
    if len(label) < 1 or len(label) > 50:
        await safe_reply(update.message, f"{ce('cross')} <b>1-50 chars.</b>", parse_mode="HTML")
        return BTN_LABEL
    context.user_data["btn_label"] = label
    await safe_reply(update.message, f"{ce('info')} <b>Step 2/3 — URL:</b>", parse_mode="HTML")
    return BTN_URL

async def btn_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text: return BTN_URL
    url = update.message.text.strip()
    if not (url.startswith("https://") or url.startswith("http://") or url.startswith("tg://")):
        await safe_reply(update.message, f"{ce('cross')} <b>Must start with http/https/tg</b>", parse_mode="HTML")
        return BTN_URL
    context.user_data["btn_url"] = url
    await safe_reply(update.message, f"{ce('info')} <b>Step 3/3 — Location:</b>",
                     parse_mode="HTML", reply_markup=btn_loc_kb())
    return BTN_LOC

async def btn_loc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q: return ConversationHandler.END
    await safe_answer(q)
    loc = q.data.split(":", 1)[1]
    label = context.user_data.get("btn_label")
    url = context.user_data.get("btn_url")
    if not (label and url):
        await safe_edit(q, f"{ce('cross')} <b>Session expired.</b>", parse_mode="HTML", reply_markup=admin_panel_kb())
        return ConversationHandler.END
    add_custom_button(label, url, loc)
    try: context.user_data.clear()
    except Exception: pass
    await safe_edit(q, f"{ce('check')} <b>Button Added!</b>\n\n<b>{html.escape(label)}</b>\nLocation: {loc}",
                    parse_mode="HTML", reply_markup=admin_panel_kb())
    return ConversationHandler.END

async def btn_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try: context.user_data.clear()
    except Exception: pass
    try:
        await update.message.reply_text(f"{ce('cross')} <b>Cancelled.</b>", parse_mode="HTML", reply_markup=admin_panel_kb())
    except Exception: pass
    return ConversationHandler.END

async def cb_ad_listbtn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q or _uid(q) not in ADMIN_IDS: return
    await safe_answer(q)
    rows = list_custom_buttons(location=None, active_only=False)
    if not rows:
        await safe_edit(q, f"{ce('info')} <b>No custom buttons.</b>", parse_mode="HTML",
                        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Back", callback_data="ad_home")]]))
        return
    kb_rows = []
    lines = [f"{ce('list')} <b>Buttons ({len(rows)})</b>\n"]
    for bid, label, url, loc, color, order, active in rows:
        status = "ON " if active else "OFF"
        lines.append(f"[{status}] <b>#{bid}</b> {html.escape(label)} -> {loc}")
        kb_rows.append([
            InlineKeyboardButton(f"{'Disable' if active else 'Enable'} #{bid}", callback_data=f"btn_tog:{bid}"),
            InlineKeyboardButton(f"Del #{bid}", callback_data=f"btn_del:{bid}"),
        ])
    kb_rows.append([InlineKeyboardButton("Back", callback_data="ad_home")])
    await safe_edit(q, "\n".join(lines), parse_mode="HTML", reply_markup=InlineKeyboardMarkup(kb_rows))

async def cb_btn_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q or _uid(q) not in ADMIN_IDS: return
    await safe_answer(q)
    try: bid = int(q.data.split(":", 1)[1])
    except Exception: return
    toggle_custom_button(bid)
    await cb_ad_listbtn(update, context)

async def cb_btn_del(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q or _uid(q) not in ADMIN_IDS: return
    await safe_answer(q)
    try: bid = int(q.data.split(":", 1)[1])
    except Exception: return
    delete_custom_button(bid)
    await cb_ad_listbtn(update, context)

async def cb_ad_gfx(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q or _uid(q) not in ADMIN_IDS: return
    await safe_answer(q)
    has = os.path.exists(CUSTOM_GFX_FILE)
    size = 0
    if has:
        try: size = os.path.getsize(CUSTOM_GFX_FILE)
        except Exception: pass
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("Add/Replace", callback_data="gfx_add"),
         InlineKeyboardButton("View", callback_data="gfx_view")],
        [InlineKeyboardButton("Remove", callback_data="gfx_rm")],
        [InlineKeyboardButton("Back", callback_data="ad_home")],
    ])
    await safe_edit(q, f"{ce('info')} <b>Custom GFX</b>\n\n{'CUSTOM' if has else 'DEFAULT'}\nSize: {size}",
                    parse_mode="HTML", reply_markup=kb)

async def cb_gfx_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q or _uid(q) not in ADMIN_IDS: return
    await safe_answer(q)
    context.user_data["await_gfx_code"] = True
    await q.message.reply_text(f"{ce('info')} <b>Send Python code with make_banner.</b>", parse_mode="HTML")

async def cb_gfx_view(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q or _uid(q) not in ADMIN_IDS: return
    await safe_answer(q)
    if not os.path.exists(CUSTOM_GFX_FILE):
        await q.message.reply_text(f"{ce('info')} <b>No custom GFX.</b>", parse_mode="HTML")
        return
    try:
        with open(CUSTOM_GFX_FILE, "r", encoding="utf-8") as f: code = f.read()
        bio = io.BytesIO(code.encode("utf-8")); bio.name = "custom_gfx.py"
        await q.message.reply_document(document=bio, filename="custom_gfx.py")
    except Exception as e:
        await q.message.reply_text(f"{ce('cross')} <b>{html.escape(str(e))}</b>", parse_mode="HTML")

async def cb_gfx_rm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q or _uid(q) not in ADMIN_IDS: return
    await safe_answer(q)
    try:
        if os.path.exists(CUSTOM_GFX_FILE): os.remove(CUSTOM_GFX_FILE)
        reload_gfx()
        await q.message.reply_text(f"{ce('check')} <b>Custom GFX removed.</b>", parse_mode="HTML")
    except Exception as e:
        await q.message.reply_text(f"{ce('cross')} <b>{html.escape(str(e))}</b>", parse_mode="HTML")

async def cb_ad_fix(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q or _uid(q) not in ADMIN_IDS: return
    await safe_answer(q)
    has = os.path.exists(CUSTOM_FIX_FILE)
    size = 0
    if has:
        try: size = os.path.getsize(CUSTOM_FIX_FILE)
        except Exception: pass
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("Add Fix", callback_data="fix_add"),
         InlineKeyboardButton("View", callback_data="fix_view")],
        [InlineKeyboardButton("Remove", callback_data="fix_rm")],
        [InlineKeyboardButton("Back", callback_data="ad_home")],
    ])
    await safe_edit(q, f"{ce('info')} <b>Custom Fix</b>\n\n{'LOADED' if has else 'NONE'}\nSize: {size}",
                    parse_mode="HTML", reply_markup=kb)

async def cb_fix_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q or _uid(q) not in ADMIN_IDS: return
    await safe_answer(q)
    context.user_data["await_fix_code"] = True
    await q.message.reply_text(f"{ce('info')} <b>Send Python code now.</b>", parse_mode="HTML")

async def cb_fix_view(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q or _uid(q) not in ADMIN_IDS: return
    await safe_answer(q)
    if not os.path.exists(CUSTOM_FIX_FILE):
        await q.message.reply_text(f"{ce('info')} <b>No custom fix.</b>", parse_mode="HTML")
        return
    try:
        with open(CUSTOM_FIX_FILE, "r", encoding="utf-8") as f: code = f.read()
        bio = io.BytesIO(code.encode("utf-8")); bio.name = "custom_fix.py"
        await q.message.reply_document(document=bio, filename="custom_fix.py")
    except Exception as e:
        await q.message.reply_text(f"{ce('cross')} <b>{html.escape(str(e))}</b>", parse_mode="HTML")

async def cb_fix_rm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q or _uid(q) not in ADMIN_IDS: return
    await safe_answer(q)
    try:
        if os.path.exists(CUSTOM_FIX_FILE): os.remove(CUSTOM_FIX_FILE)
        await q.message.reply_text(f"{ce('warning')} <b>Fix removed.</b>\nBot restart.", parse_mode="HTML")
    except Exception as e:
        await q.message.reply_text(f"{ce('cross')} <b>{html.escape(str(e))}</b>", parse_mode="HTML")

# ============================================================
# MISC INPUT
# ============================================================
async def recv_admin_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if _uid(update) not in ADMIN_IDS: return
    mode = context.user_data.get("ad_mode")
    if not mode: return
    if not update.message or not update.message.text: return
    txt = update.message.text.strip()
    if mode == "delpost":
        context.user_data.pop("ad_mode", None)
        if not txt.isdigit(): return
        rid = int(txt)
        old = get_report_post(rid)
        if not old:
            await safe_reply(update.message, f"{ce('cross')} <b>No post.</b>", parse_mode="HTML")
            return
        try:
            await context.bot.delete_message(chat_id=old[0], message_id=old[1])
            set_report_status(rid, "rejected")
            await safe_reply(update.message, f"{ce('check')} <b>Post #{rid} deleted.</b>",
                             parse_mode="HTML", reply_markup=admin_panel_kb())
        except Exception as e:
            await safe_reply(update.message, f"{ce('cross')} <b>{html.escape(str(e))}</b>", parse_mode="HTML")
    elif mode == "marktrust":
        context.user_data.pop("ad_mode", None)
        clean = normalize_username(txt)
        if not clean:
            await safe_reply(update.message, f"{ce('cross')} <b>Invalid username.</b>", parse_mode="HTML")
            return
        uid = await resolve_username(clean)
        if not uid:
            await safe_reply(update.message, f"{ce('cross')} <b>Could not resolve ID.</b>", parse_mode="HTML")
            return
        mark_trusted(uid, clean, _uid(update))
        await safe_reply(update.message, f"{ce('check')} <b>Marked:</b> @{html.escape(clean)}\nID: <code>{uid}</code>",
                         parse_mode="HTML", reply_markup=admin_panel_kb())

async def recv_banner(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if _uid(update) not in ADMIN_IDS: return
    if not update.message or not update.message.photo: return
    try:
        if context.user_data.get("wait_wlc"):
            save_wlc(update.message.photo[-1].file_id)
            context.user_data["wait_wlc"] = False
            await update.message.reply_text(f"{ce('check')} <b>Welcome banner updated.</b>", parse_mode="HTML")
        elif context.user_data.get("wait_banner"):
            save_banner(update.message.photo[-1].file_id)
            context.user_data["wait_banner"] = False
            await update.message.reply_text(f"{ce('check')} <b>Report banner updated.</b>", parse_mode="HTML")
    except Exception: pass

async def cmd_cancel_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get("login_in_progress"):
        return await login_cancel(update, context)
    for k in ["df_wait_rid", "df_mode", "ad_mode", "fx_mode", "fx_rid",
              "chat_rid", "chat_role", "chat_aid",
              "await_gfx_code", "await_fix_code", "await_broadcast"]:
        context.user_data.pop(k, None)
    try:
        await update.message.reply_text(f"{ce('cross')} <b>Cancelled.</b>", parse_mode="HTML", reply_markup=main_kb())
    except Exception: pass

# ============================================================
# LOGIN
# ============================================================
LOGIN_PHONE, LOGIN_OTP, LOGIN_PASSWORD = range(50, 53)
_login_clients = {}

async def cmd_login(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if _uid(update) not in ADMIN_IDS: return
    if not TELETHON_OK:
        await safe_reply(update.message, f"{ce('cross')} <b>Telethon not installed.</b>", parse_mode="HTML")
        return
    sess = _session_exists()
    if sess:
        try:
            client = await get_resolver()
            if client is not None:
                me = await client.get_me()
                await safe_reply(update.message,
                    f"{ce('check')} <b>Already logged in</b>\n\nAccount: @{getattr(me, 'username', '?')}\nID: <code>{me.id}</code>",
                    parse_mode="HTML")
                return
        except Exception: pass
    try: context.user_data.clear()
    except Exception: pass
    context.user_data["login_in_progress"] = True
    await safe_reply(update.message,
        f"{ce('info')} <b>Telethon Login</b>\n\n<b>Step 1/3 — Send phone.</b>\n\nExample: <code>+919999999999</code>\n\n/cancel to abort.",
        parse_mode="HTML")
    return LOGIN_PHONE

async def login_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if _uid(update) not in ADMIN_IDS: return LOGIN_PHONE
    msg = update.message
    if not msg or not msg.text: return LOGIN_PHONE
    phone = msg.text.strip().replace(" ", "").replace("-", "")
    if not phone.startswith("+") or not phone[1:].isdigit():
        await safe_reply(msg, f"{ce('cross')} <b>Invalid phone.</b>", parse_mode="HTML")
        return LOGIN_PHONE
    await safe_reply(msg, f"{ce('time')} <b>Sending OTP to</b> <code>{html.escape(phone)}</code>…", parse_mode="HTML")
    try:
        client = TelegramClient(SESSION_PATH, API_ID, API_HASH)
        await client.connect()
        sent = await client.send_code_request(phone)
        _login_clients[_uid(update)] = {"client": client, "phone": phone, "phone_code_hash": sent.phone_code_hash}
        await safe_reply(msg, f"{ce('check')} <b>OTP sent!</b>\n\n<b>Step 2/3 — Send OTP.</b>", parse_mode="HTML")
        return LOGIN_OTP
    except Exception as e:
        log.error(f"[Login] send_code: {e}")
        await safe_reply(msg, f"{ce('cross')} <b>Failed.</b>\n\n<code>{html.escape(str(e)[:300])}</code>", parse_mode="HTML")
        return ConversationHandler.END

async def login_otp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if _uid(update) not in ADMIN_IDS: return LOGIN_OTP
    msg = update.message
    if not msg or not msg.text: return LOGIN_OTP
    otp = msg.text.strip().replace(" ", "")
    data = _login_clients.get(_uid(update))
    if not data:
        await safe_reply(msg, f"{ce('cross')} <b>Session expired.</b>", parse_mode="HTML")
        return ConversationHandler.END
    client = data["client"]; phone = data["phone"]; pch = data["phone_code_hash"]
    try:
        from telethon.errors import SessionPasswordNeededError
        try:
            await client.sign_in(phone=phone, code=otp, phone_code_hash=pch)
        except SessionPasswordNeededError:
            await safe_reply(msg, f"{ce('warning')} <b>2FA required.</b>\n\n<b>Step 3/3 — Send 2FA.</b>", parse_mode="HTML")
            return LOGIN_PASSWORD
        me = await client.get_me()
        await client.disconnect()
        _login_clients.pop(_uid(update), None)
        context.user_data.pop("login_in_progress", None)
        await safe_reply(msg, f"{ce('check')} <b>Login OK!</b>\n\n@{getattr(me,'username','?')}", parse_mode="HTML")
        return ConversationHandler.END
    except Exception as e:
        log.error(f"[Login] sign_in: {e}")
        await safe_reply(msg, f"{ce('cross')} <b>OTP failed.</b>\n\n<code>{html.escape(str(e)[:300])}</code>", parse_mode="HTML")
        try: await client.disconnect()
        except Exception: pass
        _login_clients.pop(_uid(update), None)
        context.user_data.pop("login_in_progress", None)
        return ConversationHandler.END

async def login_password(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if _uid(update) not in ADMIN_IDS: return LOGIN_PASSWORD
    msg = update.message
    if not msg or not msg.text: return LOGIN_PASSWORD
    pwd = msg.text.strip()
    data = _login_clients.get(_uid(update))
    if not data:
        await safe_reply(msg, f"{ce('cross')} <b>Session expired.</b>", parse_mode="HTML")
        return ConversationHandler.END
    client = data["client"]
    try:
        await client.sign_in(password=pwd)
        me = await client.get_me()
        await client.disconnect()
        _login_clients.pop(_uid(update), None)
        context.user_data.pop("login_in_progress", None)
        await safe_reply(msg, f"{ce('check')} <b>Login OK!</b>\n\n@{getattr(me,'username','?')}", parse_mode="HTML")
        return ConversationHandler.END
    except Exception as e:
        log.error(f"[Login] password: {e}")
        await safe_reply(msg, f"{ce('cross')} <b>Password failed.</b>\n\n<code>{html.escape(str(e)[:300])}</code>", parse_mode="HTML")
        try: await client.disconnect()
        except Exception: pass
        _login_clients.pop(_uid(update), None)
        context.user_data.pop("login_in_progress", None)
        return ConversationHandler.END

async def login_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = _uid(update)
    data = _login_clients.pop(uid, None)
    if data:
        try: await data["client"].disconnect()
        except Exception: pass
    try: context.user_data.clear()
    except Exception: pass
    try:
        await update.message.reply_text(f"{ce('cross')} <b>Login cancelled.</b>", parse_mode="HTML", reply_markup=main_kb())
    except Exception: pass
    return ConversationHandler.END

# ============================================================
# ERROR
# ============================================================
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    err = context.error
    log.exception(f"Error | {type(err).__name__}: {err}")
    if "can't parse entities" in str(err).lower(): _disable_custom_emoji()
    ignore = ["TimedOut","timeout","ConnectError","NetworkError","Message is not modified",
              "Query is too old","Forbidden","chat not found","Button_user_invalid",
              "BUTTON_USER_INVALID","message to edit not found","NoneType","Conflict"]
    if any(p in str(err) for p in ignore): return

# ============================================================
# MAIN
# ============================================================
async def post_init(app: Application):
    global _BOT_REF
    _BOT_REF = app.bot
    log.info(f"[Startup] DB: {DB_PATH}")
    if os.path.exists(CUSTOM_GFX_FILE): load_custom_gfx()
    if os.path.exists(CUSTOM_FIX_FILE): load_custom_fix()
    sess = _session_exists()
    if sess: log.info(f"[Startup] Session: {sess}")
    else: log.warning(f"[Startup] Session NOT found! /login")
    await get_resolver()

async def post_shutdown(app: Application):
    await shutdown_resolver()

def main():
    init_db()
    app = (Application.builder()
           .token(BOT_TOKEN)
           .post_init(post_init)
           .post_shutdown(post_shutdown)
           .connect_timeout(30.0).read_timeout(30.0).write_timeout(30.0)
           .pool_timeout(30.0).build())

    login_conv = ConversationHandler(
        entry_points=[CommandHandler("login", cmd_login)],
        states={
            LOGIN_PHONE:    [MessageHandler(filters.TEXT & ~filters.COMMAND, login_phone)],
            LOGIN_OTP:      [MessageHandler(filters.TEXT & ~filters.COMMAND, login_otp)],
            LOGIN_PASSWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, login_password)],
        },
        fallbacks=[CommandHandler("cancel", login_cancel)],
        allow_reentry=True)

    report_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(report_start, pattern=r"^df_report$")],
        states={
            DF_T: [MessageHandler(filters.TEXT & ~filters.COMMAND, r_target)],
            DF_A: [MessageHandler(filters.TEXT & ~filters.COMMAND, r_amount)],
            DF_D: [MessageHandler(filters.TEXT & ~filters.COMMAND, r_desc)],
            DF_P: [MessageHandler(filters.TEXT & ~filters.COMMAND, r_proof)],
        },
        fallbacks=[CommandHandler("cancel", report_cancel)],
        allow_reentry=True)

    appeal_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(appeal_start, pattern=r"^df_appeal$")],
        states={
            DF_AR:  [MessageHandler(filters.TEXT & ~filters.COMMAND, ap_link)],
            DF_ARR: [MessageHandler(filters.TEXT & ~filters.COMMAND, ap_reason)],
            DF_ARP: [MessageHandler(filters.TEXT & ~filters.COMMAND, ap_proof)],
        },
        fallbacks=[CommandHandler("cancel", appeal_cancel)],
        allow_reentry=True)

    btn_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(cb_ad_addbtn, pattern=r"^ad_addbtn$")],
        states={
            BTN_LABEL: [MessageHandler(filters.TEXT & ~filters.COMMAND, btn_label)],
            BTN_URL:   [MessageHandler(filters.TEXT & ~filters.COMMAND, btn_url)],
            BTN_LOC:   [CallbackQueryHandler(btn_loc, pattern=r"^btn_loc:")],
        },
        fallbacks=[CommandHandler("cancel", btn_cancel)],
        allow_reentry=True)

    app.add_handler(login_conv)
    app.add_handler(report_conv)
    app.add_handler(appeal_conv)
    app.add_handler(btn_conv)

    # Input handlers HIGH priority
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & filters.User(ADMIN_IDS), recv_broadcast), group=-12)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & filters.User(ADMIN_IDS), recv_fix_code), group=-11)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & filters.User(ADMIN_IDS), recv_gfx_code), group=-10)

    # Commands
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("check", cmd_check))
    app.add_handler(CommandHandler("admin", cmd_admin))
    app.add_handler(CommandHandler("cancel", cmd_cancel_all))
    app.add_handler(CommandHandler("terminal", cmd_terminal))
    app.add_handler(CommandHandler("terminalstart", cmd_terminalstart))
    app.add_handler(CommandHandler("terminalstop", cmd_terminalstop))
    app.add_handler(CommandHandler("terminalclear", cmd_terminalclear))
    app.add_handler(CommandHandler("sessioninfo", cmd_session_info))
    app.add_handler(CommandHandler("addgfxcode", cmd_addgfxcode))
    app.add_handler(CommandHandler("removegfxcode", cmd_removegfxcode))
    app.add_handler(CommandHandler("showgfxcode", cmd_showgfxcode))
    app.add_handler(CommandHandler("testgfx", cmd_testgfx))
    app.add_handler(CommandHandler("addfixcode", cmd_addfixcode))
    app.add_handler(CommandHandler("removefixcode", cmd_removefixcode))
    app.add_handler(CommandHandler("showfixcode", cmd_showfixcode))
    app.add_handler(CommandHandler("broadcast", cmd_broadcast))
    app.add_handler(CommandHandler("restartbot", cmd_restartbot))
    app.add_handler(CommandHandler("backupdb", cmd_backupdb))
    app.add_handler(CommandHandler("clearidcache", cmd_clearidcache))
    app.add_handler(CommandHandler("users", cmd_users))

    # Callbacks
    app.add_handler(CallbackQueryHandler(cb_home, pattern=r"^df_home$"))
    app.add_handler(CallbackQueryHandler(cb_setid, pattern=r"^df_setid:\d+$"))
    app.add_handler(CallbackQueryHandler(cb_fwd, pattern=r"^df_fwd:\d+$"))
    app.add_handler(CallbackQueryHandler(cb_man, pattern=r"^df_man:\d+$"))
    app.add_handler(CallbackQueryHandler(cb_cancel_id, pattern=r"^df_cancel_id:\d+$"))
    app.add_handler(CallbackQueryHandler(cb_admin, pattern=r"^(df_approve|df_reject):\d+$"))
    app.add_handler(CallbackQueryHandler(cb_ap_admin, pattern=r"^(df_ap_ok|df_ap_no):\d+$"))
    app.add_handler(CallbackQueryHandler(cb_ad_home, pattern=r"^ad_home$"))
    app.add_handler(CallbackQueryHandler(cb_ad_pending, pattern=r"^ad_pending$"))
    app.add_handler(CallbackQueryHandler(cb_ad_appeals, pattern=r"^ad_appeals$"))
    app.add_handler(CallbackQueryHandler(cb_ad_stats, pattern=r"^ad_stats$"))
    app.add_handler(CallbackQueryHandler(cb_ad_setban, pattern=r"^ad_setban$"))
    app.add_handler(CallbackQueryHandler(cb_ad_setwlc, pattern=r"^ad_setwlc$"))
    app.add_handler(CallbackQueryHandler(cb_ad_delpost, pattern=r"^ad_delpost$"))
    app.add_handler(CallbackQueryHandler(cb_ad_fixid, pattern=r"^ad_fixid$"))
    app.add_handler(CallbackQueryHandler(cb_ad_repuball, pattern=r"^ad_repuball$"))
    app.add_handler(CallbackQueryHandler(cb_ad_repuball_yes, pattern=r"^ad_repuball_yes$"))
    app.add_handler(CallbackQueryHandler(cb_fx_open, pattern=r"^fx_open:\d+$"))
    app.add_handler(CallbackQueryHandler(cb_fx_fwd, pattern=r"^fx_fwd:\d+$"))
    app.add_handler(CallbackQueryHandler(cb_fx_man, pattern=r"^fx_man:\d+$"))
    app.add_handler(CallbackQueryHandler(cb_fx_repub, pattern=r"^fx_repub:\d+$"))
    app.add_handler(CallbackQueryHandler(cb_ad_listbtn, pattern=r"^ad_listbtn$"))
    app.add_handler(CallbackQueryHandler(cb_btn_toggle, pattern=r"^btn_tog:\d+$"))
    app.add_handler(CallbackQueryHandler(cb_btn_del, pattern=r"^btn_del:\d+$"))
    app.add_handler(CallbackQueryHandler(cb_ad_term, pattern=r"^ad_term$"))
    app.add_handler(CallbackQueryHandler(cb_ad_termstart, pattern=r"^ad_termstart$"))
    app.add_handler(CallbackQueryHandler(cb_ad_termstop, pattern=r"^ad_termstop$"))
    app.add_handler(CallbackQueryHandler(cb_ad_termshow, pattern=r"^ad_termshow$"))
    app.add_handler(CallbackQueryHandler(cb_ad_termclr, pattern=r"^ad_termclr$"))
    app.add_handler(CallbackQueryHandler(cb_ad_sessinfo, pattern=r"^ad_sessinfo$"))
    app.add_handler(CallbackQueryHandler(cb_ad_gfx, pattern=r"^ad_gfx$"))
    app.add_handler(CallbackQueryHandler(cb_gfx_add, pattern=r"^gfx_add$"))
    app.add_handler(CallbackQueryHandler(cb_gfx_view, pattern=r"^gfx_view$"))
    app.add_handler(CallbackQueryHandler(cb_gfx_rm, pattern=r"^gfx_rm$"))
    app.add_handler(CallbackQueryHandler(cb_ad_fix, pattern=r"^ad_fix$"))
    app.add_handler(CallbackQueryHandler(cb_fix_add, pattern=r"^fix_add$"))
    app.add_handler(CallbackQueryHandler(cb_fix_view, pattern=r"^fix_view$"))
    app.add_handler(CallbackQueryHandler(cb_fix_rm, pattern=r"^fix_rm$"))
    app.add_handler(CallbackQueryHandler(cb_ad_broadcast, pattern=r"^ad_broadcast$"))
    app.add_handler(CallbackQueryHandler(cb_ad_users, pattern=r"^ad_users$"))
    app.add_handler(CallbackQueryHandler(cb_ad_backup, pattern=r"^ad_backup$"))
    app.add_handler(CallbackQueryHandler(cb_ad_clearcache, pattern=r"^ad_clearcache$"))
    app.add_handler(CallbackQueryHandler(cb_ad_restart, pattern=r"^ad_restart$"))
    app.add_handler(CallbackQueryHandler(cb_rc_chat, pattern=r"^rc_chat:\d+$"))
    app.add_handler(CallbackQueryHandler(cb_rc_hist, pattern=r"^rc_hist:\d+$"))
    app.add_handler(CallbackQueryHandler(cb_ac_chat, pattern=r"^ac_chat:\d+$"))
    app.add_handler(CallbackQueryHandler(cb_ac_hist, pattern=r"^ac_hist:\d+$"))
    app.add_handler(CallbackQueryHandler(cb_ad_trusted, pattern=r"^ad_trusted$"))
    app.add_handler(CallbackQueryHandler(cb_ad_marktrust, pattern=r"^ad_marktrust$"))
    app.add_handler(CallbackQueryHandler(cb_tr_del, pattern=r"^tr_del:\d+$"))

    # Messages
    app.add_handler(MessageHandler(filters.FORWARDED, recv_fx_input), group=0)
    app.add_handler(MessageHandler(filters.FORWARDED, recv_forward), group=1)
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND & ~filters.FORWARDED & filters.REPLY & ~filters.User(ADMIN_IDS),
        recv_appellant_chat), group=-3)
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND & ~filters.FORWARDED & filters.REPLY & ~filters.User(ADMIN_IDS),
        recv_reporter_chat), group=-2)
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND & ~filters.FORWARDED & filters.User(ADMIN_IDS),
        recv_admin_appeal_chat), group=-1)
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND & ~filters.FORWARDED & filters.User(ADMIN_IDS),
        recv_admin_chat), group=0)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.FORWARDED, recv_fx_input), group=2)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.FORWARDED, recv_manual), group=3)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.FORWARDED, recv_admin_input), group=4)
    app.add_handler(MessageHandler(filters.PHOTO, recv_banner))

    app.add_error_handler(error_handler)

    print("=" * 60)
    print("DealFound Bot v14 — tg://user FIRST")
    print(f"DB: {DB_PATH}")
    print(f"Session: {SESSION_PATH}.session")
    print(f"Telethon: {'ON' if TELETHON_OK else 'OFF'}")
    print(f"Pillow: {'ON' if PILLOW_OK else 'OFF'}")
    print("=" * 60)
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()