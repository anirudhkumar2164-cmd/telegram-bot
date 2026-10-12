import subprocess
import sys
import os

def auto_install(packages):
    for pkg in packages:
        mod_name = pkg.split("==")[0].split("[")[0]
        try:
            __import__(mod_name)
        except ImportError:
            print(f"📦 Installing {pkg}...")
            try:
                subprocess.check_call([sys.executable, "-m", "pip", "install", "--quiet", pkg])
            except subprocess.CalledProcessError:
                subprocess.check_call([sys.executable, "-m", "pip", "install", "--quiet", "--user", pkg])

print("🔧 Checking dependencies...")
auto_install(["telethon", "requests"])
print("✅ All dependencies OK\n")

import requests
import time
import sqlite3
import datetime
import re
import random
import string
import json
import asyncio
import threading
import queue
from urllib.parse import quote

from telethon import TelegramClient, functions, types
from telethon.sessions import StringSession
from telethon.errors import SessionPasswordNeededError

print("🔎 requests version:", requests.__version__)

_http = requests.Session()
_http.headers.update({"Connection": "keep-alive"})
adapter = requests.adapters.HTTPAdapter(pool_connections=20, pool_maxsize=50, max_retries=2)
_http.mount("https://", adapter)
_http.mount("http://", adapter)

# ============================================================
#  CONFIG
# ============================================================
BOT_TOKEN   = "8933238446:AAGpWpfgKoAJZsFUPKE30sJrgZ6oC5COUVA"
SUPER_ADMIN = 8352390128
ADMIN_IDS   = {8352390128}

API_ID   = 36024862
API_HASH = "d2045803d3373a87c265c1b6378ee346"

UPI_ID     = "b.dev.4@superyes"
QR_LINK    = "https://t.me/RinzoiiQr/5"
VOUCH_CHAT = ""

FEE_PERCENT = 5
MIN_FEE     = 10

BOT_USERNAME_TO_ADD = "@RinzoiMM_bot"
BOT_PUBLIC_USERNAME = "@RinzoiMMBot"
GC_COOLDOWN_SECONDS = 3600
PREFIXES = (".", "!", "/", ",", "?")

DB_FILE = "rinzoi_mm.db"
API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"

UB_LOOP  = None
UB_READY = threading.Event()
USERBOT  = None
LOGIN_TMP  = {}
OTP_BUFFER = {}
BOT_USERNAME = None

# ============================================================
#  DATABASE
# ============================================================
_tls = threading.local()

def _get_conn():
    c = getattr(_tls, "conn", None)
    if c is None:
        c = sqlite3.connect(DB_FILE, check_same_thread=False, timeout=15)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA synchronous=NORMAL")
        _tls.conn = c
    return c

def db_one(sql, params=()):
    return _get_conn().execute(sql, params).fetchone()

def db_all(sql, params=()):
    return _get_conn().execute(sql, params).fetchall()

def db_do(sql, params=()):
    c = _get_conn()
    cur = c.execute(sql, params)
    c.commit()
    return cur

_setup = sqlite3.connect(DB_FILE)
_setup.execute("PRAGMA journal_mode=WAL")
_setup.executescript("""
CREATE TABLE IF NOT EXISTS users(
    user_id INTEGER PRIMARY KEY, username TEXT,
    first_name TEXT, joined_at TEXT, last_gc_at TEXT);
CREATE TABLE IF NOT EXISTS admins(
    user_id INTEGER PRIMARY KEY, added_at TEXT);
CREATE TABLE IF NOT EXISTS config(
    key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS deal_groups(
    chat_id INTEGER PRIMARY KEY, deal_id TEXT UNIQUE,
    admin_id INTEGER, creator_id INTEGER,
    buyer_username TEXT, seller_username TEXT,
    deal_info TEXT, amount REAL DEFAULT 0, fee REAL DEFAULT 0,
    status TEXT DEFAULT 'pending',
    created_at TEXT, updated_at TEXT, paid_at TEXT,
    released_at TEXT, completed_at TEXT, cancelled_at TEXT);
CREATE TABLE IF NOT EXISTS login_state(
    uid INTEGER PRIMARY KEY, step TEXT, phone TEXT,
    phash TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS logs(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    admin_id INTEGER, action TEXT, details TEXT, timestamp TEXT);
""")
_setup.commit()

try:
    _setup.execute("ALTER TABLE users ADD COLUMN last_gc_at TEXT")
    _setup.commit()
except: pass
try:
    _setup.execute("ALTER TABLE deal_groups ADD COLUMN creator_id INTEGER")
    _setup.commit()
except: pass

if not _setup.execute("SELECT 1 FROM config WHERE key='mm_upi'").fetchone():
    _setup.execute("INSERT INTO config(key,value) VALUES(?,?)", ("mm_upi", UPI_ID))
if not _setup.execute("SELECT 1 FROM config WHERE key='fee_percent'").fetchone():
    _setup.execute("INSERT INTO config(key,value) VALUES(?,?)", ("fee_percent", str(FEE_PERCENT)))
if not _setup.execute("SELECT 1 FROM config WHERE key='min_fee'").fetchone():
    _setup.execute("INSERT INTO config(key,value) VALUES(?,?)", ("min_fee", str(MIN_FEE)))
_setup.commit()
_setup.close()

# ============================================================
#  HELPERS
# ============================================================
def now():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def money(a):
    try: return f"₹{float(a):,.2f}"
    except: return f"₹{a}"

def api(method, data=None, files=None):
    for attempt in range(2):
        try:
            r = _http.post(f"{API_URL}/{method}", data=data or {}, files=files, timeout=30)
            return r.json()
        except Exception as e:
            if attempt == 0:
                time.sleep(1); continue
            print("API err:", e)
            return {"ok": False}

def send(chat_id, text, kb=None, reply=None):
    d = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    if kb: d["reply_markup"] = json.dumps(kb)
    if reply: d["reply_to_message_id"] = reply
    return api("sendMessage", d)

def send_photo(chat_id, photo, caption="", kb=None):
    d = {"chat_id": chat_id, "photo": photo, "caption": caption, "parse_mode": "HTML"}
    if kb: d["reply_markup"] = json.dumps(kb)
    return api("sendPhoto", d)

def edit(chat_id, mid, text, kb=None):
    d = {"chat_id": chat_id, "message_id": mid, "text": text, "parse_mode": "HTML"}
    if kb is not None: d["reply_markup"] = json.dumps(kb)
    return api("editMessageText", d)

def del_msg(chat_id, mid):
    return api("deleteMessage", {"chat_id": chat_id, "message_id": mid})

def cb_answer(cid, text="", alert=False):
    return api("answerCallbackQuery", {"callback_query_id": cid, "text": text[:200], "show_alert": alert})

def send_force_reply(chat_id, prompt, placeholder="Type here..."):
    d = {"chat_id": chat_id, "text": prompt, "parse_mode": "HTML",
         "reply_markup": json.dumps({"force_reply": True, "selective": True, "input_field_placeholder": placeholder})}
    return api("sendMessage", d)

def is_admin(uid):
    if uid in ADMIN_IDS: return True
    return bool(db_one("SELECT 1 FROM admins WHERE user_id=?", (uid,)))

def get_all_admins():
    ids = set(ADMIN_IDS)
    for r in db_all("SELECT user_id FROM admins"):
        ids.add(r["user_id"])
    return ids

def log(uid, action, details=""):
    try:
        db_do("INSERT INTO logs(admin_id,action,details,timestamp) VALUES(?,?,?,?)",
              (uid, action, details[:1000], now()))
    except Exception as e: print("log err:", e)

def save_user(u):
    try:
        db_do("""INSERT INTO users(user_id,username,first_name,joined_at)
            VALUES(?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET
            username=excluded.username, first_name=excluded.first_name""",
            (u.get("id"), u.get("username"), u.get("first_name", ""), now()))
    except Exception as e: print("user err:", e)

def user_can_make_gc(uid):
    r = db_one("SELECT last_gc_at FROM users WHERE user_id=?", (uid,))
    if not r or not r["last_gc_at"]:
        return True, 0
    try:
        last = datetime.datetime.strptime(r["last_gc_at"], "%Y-%m-%d %H:%M:%S")
        diff = (datetime.datetime.now() - last).total_seconds()
        if diff >= GC_COOLDOWN_SECONDS:
            return True, 0
        return False, int(GC_COOLDOWN_SECONDS - diff)
    except:
        return True, 0

def mark_gc_made(uid):
    try:
        db_do("UPDATE users SET last_gc_at=? WHERE user_id=?", (now(), uid))
    except Exception as e:
        print("mark_gc_made err:", e)

def valid_username(u):
    return bool(re.fullmatch(r"@[A-Za-z0-9_]{5,32}", u or ""))

def parse_amount(v):
    try:
        a = float(str(v).replace(",", ""))
        return round(a, 2) if 0 < a <= 1e8 else None
    except: return None

def cfg_get(k, d=None):
    r = db_one("SELECT value FROM config WHERE key=?", (k,))
    return r["value"] if r else d

def cfg_set(k, v):
    db_do("""INSERT INTO config(key,value) VALUES(?,?)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value""", (k, v))

def fee_for(a):
    pct = float(cfg_get("fee_percent", str(FEE_PERCENT)))
    mn  = float(cfg_get("min_fee", str(MIN_FEE)))
    return max(round(a * pct / 100, 2), mn)

# ============================================================
#  QR (with AUTO AMOUNT)
# ============================================================
def qr_url(upi, amount=None, name="Rinzoi MM"):
    if amount:
        data = f"upi://pay?pa={upi}&pn={quote(name)}&am={amount}&cu=INR"
    else:
        data = f"upi://pay?pa={upi}"
    return f"https://api.qrserver.com/v1/create-qr-code/?size=500x500&data={quote(data)}"

def login_state_get(uid):
    return db_one("SELECT * FROM login_state WHERE uid=?", (uid,))

def login_state_set(uid, step, phone=None, phash=None):
    db_do("""INSERT INTO login_state(uid,step,phone,phash,created_at)
        VALUES(?,?,?,?,?) ON CONFLICT(uid) DO UPDATE SET
        step=excluded.step, phone=excluded.phone, phash=excluded.phash,
        created_at=excluded.created_at""", (uid, step, phone, phash, now()))

def login_state_clear(uid):
    db_do("DELETE FROM login_state WHERE uid=?", (uid,))

def gen_id():
    while True:
        i = "".join(random.choices(string.ascii_uppercase + string.digits, k=6))
        if not db_one("SELECT 1 FROM deal_groups WHERE deal_id=?", (i,)):
            return i

def deal_of(chat_id):
    return db_one("SELECT * FROM deal_groups WHERE chat_id=?", (chat_id,))

def update_deal(chat_id, **kw):
    kw["updated_at"] = now()
    cols = ", ".join(f"{k}=?" for k in kw)
    db_do(f"UPDATE deal_groups SET {cols} WHERE chat_id=?", (*kw.values(), chat_id))

# ============================================================
#  ADMIN NOTIFICATION with DELETE BUTTON
# ============================================================
def notify_admins_new_gc(creator_id, creator_name, creator_username,
                         gc_id, gc_link, deal_id):
    user_line = f'<a href="tg://user?id={creator_id}">{creator_name or "User"}</a>'
    un_line = f"@{creator_username}" if creator_username else "no username"

    text = (
        "<b>New Deal GC Created</b>\n\n"
        f"<b>Created by:</b> {user_line}\n"
        f"<b>User ID:</b> <code>{creator_id}</code>\n"
        f"<b>Username:</b> {un_line}\n\n"
        f"<b>Deal ID:</b> <code>{deal_id}</code>\n"
        f"<b>GC ID:</b> <code>{gc_id}</code>\n"
        f"<b>GC Link:</b> {gc_link}\n\n"
        f"<b>Time:</b> {now()}"
    )

    kb = {"inline_keyboard": [[
        {"text":"Delete GC", "callback_data": f"delgc:{gc_id}"}
    ]]}

    for admin_id in get_all_admins():
        try:
            send(admin_id, text, kb)
        except Exception as e:
            print(f"notify admin {admin_id} err:", repr(e))

# ============================================================
#  BOT-API GC CONTROL
# ============================================================
def bot_set_gc_name(gc_id, name):
    try:
        r = api("setChatTitle", {"chat_id": gc_id, "title": name})
        if r.get("ok"):
            print(f"✅ Bot set name: {name}")
            return True
        print("❌ setChatTitle:", r)
        return False
    except Exception as e:
        print("setChatTitle err:", repr(e))
        return False

def bot_set_gc_photo(gc_id, photo_path):
    try:
        if not photo_path or not os.path.exists(photo_path):
            return False
        for attempt in range(3):
            try:
                with open(photo_path, "rb") as f:
                    r = _http.post(f"{API_URL}/setChatPhoto",
                                   data={"chat_id": gc_id},
                                   files={"photo": f}, timeout=45).json()
                if r.get("ok"):
                    print("✅ Bot set photo")
                    return True
            except Exception as e:
                print(f"setChatPhoto try {attempt+1} err:", repr(e))
            time.sleep(2)
        return False
    except Exception as e:
        print("setChatPhoto err:", repr(e))
        return False

def bot_send_msg(gc_id, text):
    try:
        r = send(gc_id, text)
        if r.get("ok"):
            print(f"✅ Bot sent msg")
            return r["result"]["message_id"]
        return None
    except Exception as e:
        print("bot_send_msg err:", repr(e))
        return None

def bot_pin_msg(gc_id, mid):
    try:
        r = api("pinChatMessage", {
            "chat_id": gc_id, "message_id": mid,
            "disable_notification": True
        })
        if r.get("ok"):
            print(f"✅ Bot pinned msg")
            return True
        return False
    except Exception as e:
        print("bot_pin_msg err:", repr(e))
        return False

# ============================================================
#  USERBOT ASYNC
# ============================================================
async def ub_start_async(phone):
    client = TelegramClient(StringSession(), API_ID, API_HASH)
    await client.connect()
    if not client.is_connected():
        raise Exception("Telegram se connect nahi ho paya")
    r = await client.send_code_request(phone)
    return client, r.phone_code_hash

async def ub_verify_async(client, phone, code, phash):
    try:
        await client.sign_in(phone=phone, code=code, phone_code_hash=phash)
        return "ok"
    except SessionPasswordNeededError:
        return "2fa"

async def ub_verify_2fa_async(client, password):
    await client.sign_in(password=password)
    return "ok"

async def ub_boot_async(session_str):
    global USERBOT
    try:
        USERBOT = TelegramClient(StringSession(session_str), API_ID, API_HASH)
        await USERBOT.connect()
        if not await USERBOT.is_user_authorized():
            USERBOT = None
            return False
        return True
    except Exception as e:
        print("ub_boot err:", e)
        USERBOT = None
        return False

async def ub_create_gc_async(title, admin_id):
    if not USERBOT:
        return None

    admin = None
    try:
        admin = await USERBOT.get_entity(admin_id)
    except Exception as e:
        print("admin entity err:", repr(e))
        try:
            admin = await USERBOT.get_entity(int(admin_id))
        except: pass

    try:
        res = await USERBOT(functions.channels.CreateChannelRequest(
            title="Rinzoi MM", about="Rinzoi MM Deal", megagroup=True))
    except Exception as e:
        print("create gc err:", repr(e))
        return None

    chat = res.chats[0]
    chat_id = int(f"-100{chat.id}")
    print(f"✅ GC created: {chat_id}")

    if admin:
        try:
            await USERBOT(functions.channels.EditAdminRequest(
                channel=chat, user_id=admin,
                admin_rights=types.ChatAdminRights(
                    change_info=True, post_messages=True, edit_messages=True,
                    delete_messages=True, ban_users=True, invite_users=True,
                    pin_messages=True, add_admins=False, manage_call=True,
                    other=True), rank="MM Admin"))
        except Exception as e:
            print("admin rights err:", repr(e))

    try:
        bot_entity = await USERBOT.get_entity(BOT_USERNAME_TO_ADD)
        try:
            await USERBOT(functions.channels.InviteToChannelRequest(
                channel=chat, users=[bot_entity]))
            print(f"✅ Bot invited")
        except Exception as e:
            print("bot invite err:", repr(e))

        await USERBOT(functions.channels.EditAdminRequest(
            channel=chat, user_id=bot_entity,
            admin_rights=types.ChatAdminRights(
                change_info=True, delete_messages=True, ban_users=True,
                invite_users=True, pin_messages=True, add_admins=False,
                manage_call=True, other=True,
                post_messages=True, edit_messages=True),
            rank="MM Assistant"))
        print("✅ Bot is admin")
    except Exception as e:
        print("bot add err:", repr(e))

    try:
        await USERBOT(functions.channels.TogglePreHistoryHiddenRequest(
            channel=chat, enabled=False))
    except: pass

    invite_link = ""
    await asyncio.sleep(0.8)
    from telethon.tl.functions.messages import ExportChatInviteRequest

    try:
        r1 = await USERBOT(ExportChatInviteRequest(peer=chat))
        invite_link = r1.link
        print(f"✅ LINK: {invite_link}")
    except Exception as e1:
        print("err peer:", repr(e1))
        try:
            r2 = await USERBOT(ExportChatInviteRequest(channel=chat))
            invite_link = r2.link
        except Exception as e2:
            print("err channel:", repr(e2))

    return chat_id, invite_link

async def ub_delete_gc_async(chat_id):
    """GC delete karo (userbot se, kyunki woh creator hai)."""
    if not USERBOT: return False
    try:
        ch = await USERBOT.get_entity(chat_id)
        from telethon.tl.functions.channels import DeleteChannelRequest
        await USERBOT(DeleteChannelRequest(channel=ch))
        print(f"✅ GC deleted: {chat_id}")
        return True
    except Exception as e:
        print("delete gc err:", repr(e))
        # Fallback: leave
        try:
            ch = await USERBOT.get_entity(chat_id)
            await USERBOT.delete_dialog(ch)
            print(f"✅ Left GC: {chat_id}")
            return True
        except Exception as e2:
            print("leave err:", repr(e2))
            return False

async def ub_lock_async(chat_id, lock=True):
    if not USERBOT: return
    try:
        ch = await USERBOT.get_entity(chat_id)
        perms = types.ChatBannedRights(
            until_date=None, send_messages=lock, send_media=lock,
            send_stickers=lock, send_gifs=lock, send_games=lock,
            send_inline=lock, embed_links=lock)
        await USERBOT(functions.channels.EditBannedRequest(
            channel=ch, participant=ch, banned_rights=perms))
    except Exception as e:
        print("lock err:", e)

# ============================================================
#  USERBOT THREAD
# ============================================================
def userbot_thread_runner():
    global UB_LOOP
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    UB_LOOP = loop
    UB_READY.set()
    print("✅ Userbot loop ready")

    sess = cfg_get("userbot_session")
    if sess:
        try:
            ok = loop.run_until_complete(ub_boot_async(sess))
            print("✅ Userbot booted" if ok else "⚠️ Session invalid")
        except Exception as e:
            print("Boot err:", e)
    else:
        print("ℹ️ No session. DM me `.login` karo.")
    loop.run_forever()

def run_ub(coro, timeout=180):
    if not UB_LOOP: return None
    try:
        fut = asyncio.run_coroutine_threadsafe(coro, UB_LOOP)
        return fut.result(timeout=timeout)
    except Exception as e:
        import traceback
        print("run_ub EXCEPTION:", repr(e))
        traceback.print_exc()
        return None

# ============================================================
#  OTP KEYPAD
# ============================================================
def render_otp_keypad(uid, valid_seconds=300):
    buf = OTP_BUFFER.get(uid, "")
    display = (buf + "_" * (5 - len(buf))) if len(buf) < 5 else buf
    mins = max(1, valid_seconds // 60)
    text = (f"<b>Enter the OTP using the keypad below</b>\n"
            f"Current: <code>{display}</code>\n"
            f"Format: 12345\nValid for: <b>{mins} minutes</b>")
    kb = {"inline_keyboard": [
        [{"text":"1","callback_data":"otp:d:1"},{"text":"2","callback_data":"otp:d:2"},{"text":"3","callback_data":"otp:d:3"}],
        [{"text":"4","callback_data":"otp:d:4"},{"text":"5","callback_data":"otp:d:5"},{"text":"6","callback_data":"otp:d:6"}],
        [{"text":"7","callback_data":"otp:d:7"},{"text":"8","callback_data":"otp:d:8"},{"text":"9","callback_data":"otp:d:9"}],
        [{"text":"Back","callback_data":"otp:back"},{"text":"0","callback_data":"otp:d:0"},{"text":"Cancel","callback_data":"otp:cancel"}],
        [{"text":"Show Code","callback_data":"otp:show"}]]}
    return text, kb

def help_text():
    return """<b>RINZOI MM BOT</b>

Prefixes: <code>. ! / , ?</code>

<b>LOGIN (Admin only)</b>
<code>.login</code>  <code>.logout</code>  <code>.me</code>

<b>SETUP (Admin only)</b>
<code>.addupi yourupi@bank</code>
<code>.settos text</code>
<code>.setfee 5</code>  <code>.setminfee 10</code>
<code>.setGcname Name</code>
<code>.setDp</code> (reply to photo)
<code>.setvouch @Channel</code>

<b>DEAL GC</b>
<code>.mm</code> -> auto GC
<code>.pay 100</code>  <code>.rvc</code>  <code>.rec</code>  <code>.voc</code>  <code>.com</code>

<b>SUPER</b>
<code>.addadmin 12345</code>  <code>.broadcast text</code>  <code>.stats</code>"""

# ============================================================
#  BACKGROUND WORKERS
# ============================================================
def mm_worker(uid, chat):
    try:
        urow = db_one("SELECT username, first_name FROM users WHERE user_id=?", (uid,))
        creator_name = (urow["first_name"] if urow and urow["first_name"] else "User")
        creator_username = (urow["username"] if urow and urow["username"] else "")

        did = gen_id()
        tpl = cfg_get("gc_name_template", "Rinzoi MM | @Rinzoi")
        try: gname = tpl.format(deal_id=did)
        except: gname = tpl

        result = run_ub(ub_create_gc_async(gname, uid), timeout=180)
        if not result:
            send(chat, "<b>GC create fail. Please try again later.</b>")
            return

        gc_id, link = result
        mark_gc_made(uid)

        t = now()
        db_do("""INSERT INTO deal_groups(chat_id,deal_id,admin_id,creator_id,
            buyer_username,seller_username,deal_info,created_at,updated_at)
            VALUES(?,?,?,?,?,?,?,?,?)""",
            (gc_id, did, uid, uid, "", "", "", t, t))

        # Admin notification with delete button
        try:
            notify_admins_new_gc(
                creator_id=uid,
                creator_name=creator_name,
                creator_username=creator_username,
                gc_id=gc_id,
                gc_link=link,
                deal_id=did
            )
            print(f"✅ Admins notified about GC {did}")
        except Exception as e:
            print("notify_admins err:", repr(e))

        bot_set_gc_name(gc_id, gname)

        dp = cfg_get("deal_dp_path")
        if dp and os.path.exists(dp):
            bot_set_gc_photo(gc_id, dp)

        welcome = (
            "<b>Share the link below with anyone involved in this deal</b>\n\n"
            f"{link}\n\n"
            "<b>Only 2 people can join using this link</b>"
        )
        bot_send_msg(gc_id, welcome)

        gc_intro = (
            "<b>Hey. Please state the terms of the deal.</b>\n\n"
            "<b>- What is the deal?</b>\n"
            "<b>- Who is the buyer/seller?</b>\n"
            "<b>- What is the agreed price and which crypto?</b>\n"
            "<b>- Include any other relevant information.</b>\n\n"
            "<b>• I am not dealing below 50rs ~ @Rinzoi</b>"
        )
        mid = bot_send_msg(gc_id, gc_intro)
        if mid:
            bot_pin_msg(gc_id, mid)

        text2 = (
            "<b>Your Deal GC has been created successfully!</b>\n\n"
            f"<b>GC Link:</b> {link}\n\n"
            "<b>Please join the GC and wait for further instructions.</b>"
        )
        send(chat, text2)
        log(uid, "CREATE_GC", f"{did} chat={gc_id} link={link} by={uid}")
    except Exception as e:
        print("mm_worker err:", repr(e))
        send(chat, f"<b>Error:</b> <code>{e}</code>")


def login_phone_worker(uid, chat, rt_mid, phone):
    try:
        result = run_ub(ub_start_async(phone), timeout=120)
        if not result:
            send(chat, "<b>OTP fail. Check internet/VPN.</b>")
            login_state_clear(uid); return
        client, phash = result
        LOGIN_TMP[uid] = {"client": client, "phone": phone, "hash": phash}
        login_state_set(uid, "otp", phone, phash)
        OTP_BUFFER[uid] = ""
        txt, kb = render_otp_keypad(uid)
        send(chat, txt, kb)
    except Exception as e:
        print("login_phone_worker err:", repr(e))
        send(chat, f"<b>Error:</b> <code>{e}</code>")
        login_state_clear(uid)


def otp_verify_worker(uid, chat, mid, phone, buf, phash):
    try:
        r = run_ub(ub_verify_async(LOGIN_TMP[uid]["client"], phone, buf, phash), timeout=120)
        if r == "ok":
            sess = LOGIN_TMP[uid]["client"].session.save()
            cfg_set("userbot_session", sess)
            cfg_set("userbot_phone", phone)
            LOGIN_TMP.pop(uid, None); OTP_BUFFER.pop(uid, None); login_state_clear(uid)
            run_ub(ub_boot_async(sess), timeout=60)
            edit(chat, mid, "<b>Login successful!</b>\n\nNow use <code>.mm</code>")
        elif r == "2fa":
            OTP_BUFFER.pop(uid, None)
            login_state_set(uid, "pass", phone, phash)
            edit(chat, mid, "<b>2FA password required</b> (reply to this):")
            send_force_reply(chat, "<b>2FA password:</b>", "password")
        else:
            OTP_BUFFER[uid] = ""
            txt, kb = render_otp_keypad(uid)
            edit(chat, mid, "<b>Wrong OTP.</b>\n\n" + txt, kb)
    except Exception as e:
        print("otp_verify_worker err:", repr(e))
        edit(chat, mid, f"<b>Error:</b> <code>{e}</code>")

# ============================================================
#  COMMAND HANDLER
# ============================================================
def handle_command(msg):
    text  = msg.get("text", "")
    user  = msg.get("from", {})
    uid   = user.get("id")
    chat  = msg["chat"]["id"]
    ctype = msg["chat"]["type"]
    mid   = msg["message_id"]

    rt = msg.get("reply_to_message") or {}
    if rt and (rt.get("from") or {}).get("is_bot") and uid:
        st = login_state_get(uid)
        if st and ctype == "private":
            del_msg(chat, mid)
            if st["step"] == "phone":
                phone = text.strip()
                if not re.fullmatch(r"\+?\d{8,15}", phone):
                    del_msg(chat, rt["message_id"])
                    send_force_reply(chat, "<b>Invalid phone.</b>\n<b>Send phone:</b>", "+919999999999")
                    return
                del_msg(chat, rt["message_id"])
                send(chat, "<b>Sending OTP...</b>")
                threading.Thread(target=login_phone_worker,
                                 args=(uid, chat, rt["message_id"], phone),
                                 daemon=True).start()
                return
            if st["step"] == "pass":
                pwd = text.strip()
                del_msg(chat, rt["message_id"])
                if uid not in LOGIN_TMP:
                    send(chat, "<b>Session expired. Use .login again.</b>")
                    login_state_clear(uid); return
                try:
                    r = run_ub(ub_verify_2fa_async(LOGIN_TMP[uid]["client"], pwd), timeout=90)
                    if not r:
                        send(chat, "<b>2FA failed.</b>"); return
                    sess = LOGIN_TMP[uid]["client"].session.save()
                    cfg_set("userbot_session", sess)
                    cfg_set("userbot_phone", st["phone"])
                    LOGIN_TMP.pop(uid, None); login_state_clear(uid)
                    run_ub(ub_boot_async(sess), timeout=60)
                    send(chat, "<b>2FA verified!</b>")
                except Exception as e:
                    send(chat, f"<b>{e}</b>")
                return

    # /start MENU
    if text.strip() in ("/start", ".start", "!start"):
        kb = {"inline_keyboard": [[
            {"text":"Create Deal GC", "callback_data":"newgc"}
        ]]}
        menu_text = (
            "<b>Rinzoi MM Bot</b>\n\n"
            "<b>Create a Deal GC for your Buyer & Seller in just one click!</b>\n\n"
            "<b>No hassle, just a simple way to manage your deals.</b>\n\n"
            f"<b>Start Now:</b> {BOT_PUBLIC_USERNAME}\n\n"
            "<b>Always verify your MM before starting a deal.</b>"
        )
        send(chat, menu_text, kb)
        return

    stripped = text.strip()
    if not stripped or stripped[0] not in PREFIXES:
        return

    if ctype in ("group", "supergroup"):
        del_msg(chat, mid)

    normalized = "." + stripped[1:]
    parts = normalized.strip().split(maxsplit=3)
    cmd = parts[0].lower()

    admin_cmds = (".login", ".logout", ".me", ".setgcname", ".setdp",
                  ".addupi", ".settos", ".setfee", ".setminfee", ".setvouch",
                  ".addadmin", ".stats", ".broadcast", ".help", ".logs", ".find",
                  ".deladmin", ".admins")
    deal_cmds_set = (".pay", ".rvc", ".rec", ".voc", ".com", ".cancel",
                     ".lock", ".unlock", ".time", ".ban", ".unban", ".unmute")
    public_cmds = (".mm", ".start")

    if cmd not in admin_cmds and cmd not in deal_cmds_set and cmd not in public_cmds:
        if is_admin(uid):
            send(chat, "<b>Unknown command.</b> <code>.help</code>")
        return

    if cmd not in public_cmds and cmd not in deal_cmds_set:
        if not is_admin(uid): return
    if cmd in deal_cmds_set:
        if not is_admin(uid): return

    if cmd == ".help":
        send(chat, help_text()); return

    if cmd == ".login":
        if ctype != "private": send(chat, "<b>Send this in DM.</b>"); return
        sess = cfg_get("userbot_session")
        if sess:
            send(chat, f"<b>Already logged in:</b> <code>{cfg_get('userbot_phone','?')}</code>"); return
        kb = {"inline_keyboard": [[
            {"text":"Login","callback_data":"login_start"},
            {"text":"Cancel","callback_data":"login_cancel"}]]}
        send(chat, "<b>LOGIN</b>\n\n<b>Click the button below</b>", kb); return

    if cmd == ".logout":
        cfg_set("userbot_session", ""); cfg_set("userbot_phone", "")
        try:
            if USERBOT: run_ub(USERBOT.log_out(), timeout=30)
        except: pass
        login_state_clear(uid); LOGIN_TMP.pop(uid, None); OTP_BUFFER.pop(uid, None)
        send(chat, "<b>Logged out.</b>"); return

    if cmd == ".me":
        ph = cfg_get("userbot_phone"); sess = cfg_get("userbot_session")
        if ph and sess: send(chat, f"<b>Logged in:</b> <code>{ph}</code>")
        else: send(chat, "<b>Not logged in.</b>")
        return

    if cmd == ".setgcname":
        if len(parts) < 2: send(chat, "<b>Usage:</b> <code>.setGcname Name</code>"); return
        cfg_set("gc_name_template", normalized.split(maxsplit=1)[1])
        send(chat, "<b>GC name template set.</b>"); return

    if cmd == ".setdp":
        rt = msg.get("reply_to_message") or {}
        if not rt.get("photo"): send(chat, "<b>Reply to a photo.</b>"); return
        fid = rt["photo"][-1]["file_id"]
        f = api("getFile", {"file_id": fid})
        if not f.get("ok"): send(chat, "<b>Failed.</b>"); return
        url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{f['result']['file_path']}"
        img = _http.get(url).content
        os.makedirs("assets", exist_ok=True)
        with open("assets/deal_dp.jpg", "wb") as fh: fh.write(img)
        cfg_set("deal_dp_path", "assets/deal_dp.jpg")
        send(chat, "<b>Deal DP saved.</b>"); return

    if cmd == ".addupi":
        if len(parts) < 2: send(chat, "<b>Usage:</b> <code>.addupi yourupi@bank</code>"); return
        cfg_set("mm_upi", parts[1].strip())
        send(chat, f"<b>UPI set:</b> <code>{parts[1]}</code>"); return

    if cmd == ".settos":
        if len(parts) < 2: send(chat, "<b>Usage:</b> <code>.settos text</code>"); return
        cfg_set("tos_text", normalized.split(maxsplit=1)[1])
        send(chat, "<b>TOS set.</b>"); return

    if cmd == ".setfee":
        if len(parts) < 2: send(chat, "<b>Usage:</b> <code>.setfee 5</code>"); return
        cfg_set("fee_percent", parts[1]); send(chat, f"<b>Fee = {parts[1]}%</b>"); return

    if cmd == ".setminfee":
        if len(parts) < 2: send(chat, "<b>Usage:</b> <code>.setminfee 10</code>"); return
        cfg_set("min_fee", parts[1]); send(chat, f"<b>Min fee = Rs {parts[1]}</b>"); return

    if cmd == ".setvouch":
        if len(parts) < 2: send(chat, "<b>Usage:</b> <code>.setvouch @Channel</code>"); return
        cfg_set("vouch_channel", parts[1].strip()); send(chat, "<b>Vouch channel set.</b>"); return

    if cmd == ".mm":
        sess = cfg_get("userbot_session")
        if not sess: send(chat, "<b>First use</b> <code>.login</code>"); return
        if not USERBOT: send(chat, "<b>Userbot inactive.</b>"); return

        allowed, wait = user_can_make_gc(uid)
        if not allowed and not is_admin(uid):
            mins = wait // 60; secs = wait % 60
            send(chat, f"<b>You can create 1 GC per hour.</b>\n<b>Wait:</b> {mins}m {secs}s")
            return

        send(chat, "<b>Your deal GC is being created. Please wait.</b>")
        threading.Thread(target=mm_worker, args=(uid, chat), daemon=True).start()
        return

    d = deal_of(chat)
    if d is None and cmd in deal_cmds_set:
        send(chat, "<b>Use this in a deal GC.</b>"); return

    if cmd == ".pay":
        if len(parts) < 2: send(chat, "<b>Usage:</b> <code>.pay 100</code>"); return
        a = parse_amount(parts[1])
        if a is None: send(chat, "<b>Invalid amount.</b>"); return
        f = fee_for(a)
        update_deal(chat, amount=a, fee=f)
        upi = cfg_get("mm_upi", UPI_ID)
        d = deal_of(chat)
        total = d['amount'] + d['fee']
        caption = (
            f"<b>Deal Amount:</b> {money(d['amount'])}\n"
            f"<b>MM Fee:</b> {money(d['fee'])}\n"
            f"<b>Total:</b> {money(total)}\n\n"
            f"<b>UPI:</b> <code>{upi}</code>\n\n"
            "<b>Scan the QR or copy the UPI ID above</b>"
        )
        kb = {"inline_keyboard": [
            [{"text":"TOS","callback_data":f"tos:{d['deal_id']}"},
             {"text":"Copy UPI","callback_data":f"upi:{d['deal_id']}"}],
            [{"text":"I Paid","callback_data":f"rvc:{d['deal_id']}"}]]}
        send_photo(chat, qr_url(upi, total), caption, kb)
        return

    if cmd == ".rvc":
        update_deal(chat, status="paid", paid_at=now())
        send(chat, f"<b>Payment Received</b>\n<b>Amount:</b> {money(d['amount'])}")
        return

    if cmd == ".rec":
        update_deal(chat, status="released", released_at=now())
        upi = cfg_get("mm_upi", UPI_ID)
        send(chat,
             f"<b>Payment Released</b>\n"
             f"<b>Amount:</b> {money(d['amount'])}\n"
             f"<b>UPI:</b> <code>{upi}</code>")
        return

    if cmd == ".com":
        update_deal(chat, status="completed", completed_at=now())
        threading.Thread(target=lambda: run_ub(ub_lock_async(chat, True), timeout=30), daemon=True).start()
        send(chat, "<b>Deal Completed</b>\n<b>GC Locked</b>")
        return

    if cmd == ".lock":
        threading.Thread(target=lambda: run_ub(ub_lock_async(chat, True), timeout=30), daemon=True).start()
        send(chat, "<b>Locked</b>"); return
    if cmd == ".unlock":
        threading.Thread(target=lambda: run_ub(ub_lock_async(chat, False), timeout=30), daemon=True).start()
        send(chat, "<b>Unlocked</b>"); return
    if cmd == ".cancel":
        update_deal(chat, status="cancelled", cancelled_at=now())
        threading.Thread(target=lambda: run_ub(ub_lock_async(chat, True), timeout=30), daemon=True).start()
        send(chat, f"<b>Cancelled</b>\n<code>{d['deal_id']}</code>"); return

    if cmd == ".voc":
        send(chat, "<b>Vouch @Rinzoi MM'd</b>"); return

    if cmd == ".addadmin":
        if uid != SUPER_ADMIN: send(chat, "<b>Super admin only.</b>"); return
        if len(parts) < 2 or not parts[1].isdigit(): send(chat, "<b>Invalid ID.</b>"); return
        db_do("INSERT OR IGNORE INTO admins(user_id,added_at) VALUES(?,?)", (int(parts[1]), now()))
        send(chat, f"<b>Admin added:</b> <code>{parts[1]}</code>"); return

    if cmd == ".deladmin":
        if uid != SUPER_ADMIN: send(chat, "<b>Super admin only.</b>"); return
        if len(parts) < 2 or not parts[1].isdigit(): send(chat, "<b>Invalid ID.</b>"); return
        db_do("DELETE FROM admins WHERE user_id=?", (int(parts[1]),))
        send(chat, f"<b>Admin removed:</b> <code>{parts[1]}</code>"); return

    if cmd == ".admins":
        rows = db_all("SELECT user_id FROM admins")
        ids = sorted(ADMIN_IDS) + [r["user_id"] for r in rows if r["user_id"] not in ADMIN_IDS]
        send(chat, "<b>Admins</b>\n" + "\n".join(f"<code>{i}</code>" for i in ids)); return

    if cmd == ".stats":
        t = db_one("SELECT COUNT(*) FROM deal_groups")[0]
        c = db_one("SELECT COUNT(*) FROM deal_groups WHERE status='completed'")[0]
        send(chat, f"<b>Total:</b> {t}\n<b>Completed:</b> {c}"); return

    if cmd == ".logs":
        rows = db_all("SELECT * FROM logs ORDER BY id DESC LIMIT 15")
        out = "<b>Latest Logs</b>\n\n"
        for r in rows: out += f"#{r['id']} {r['action']} - {r['details']}\n"
        send(chat, out[:4000]); return

    if cmd == ".find":
        if len(parts) < 2: send(chat, "<b>Usage:</b> <code>.find DEALID</code>"); return
        r = db_one("SELECT * FROM deal_groups WHERE deal_id=?", (parts[1].upper(),))
        if not r: send(chat, "<b>Not found.</b>"); return
        send(chat, f"<b>Deal ID:</b> <code>{r['deal_id']}</code>\n<b>Status:</b> {r['status']}\n<b>Amount:</b> {money(r['amount'])}")
        return

# ============================================================
#  CALLBACK
# ============================================================
def handle_callback(cq):
    data = cq.get("data", "")
    uid  = cq["from"]["id"]
    chat = cq["message"]["chat"]["id"]
    mid  = cq["message"]["message_id"]

    # ---- DELETE GC (admin only) ----
    if data.startswith("delgc:"):
        if not is_admin(uid):
            return cb_answer(cq["id"], "Admins only", True)
        try:
            gc_id = int(data.split(":", 1)[1])
        except:
            return cb_answer(cq["id"], "Invalid GC", True)

        cb_answer(cq["id"], "Deleting GC...")
        ok = run_ub(ub_delete_gc_async(gc_id), timeout=60)
        if ok:
            try:
                db_do("UPDATE deal_groups SET status='deleted', updated_at=? WHERE chat_id=?",
                      (now(), gc_id))
            except: pass
            edit(chat, mid, "<b>GC Deleted</b>")
            log(uid, "DELETE_GC", f"chat={gc_id}")
        else:
            edit(chat, mid, "<b>Failed to delete GC.</b>\n<b>Check userbot.</b>")
        return

    if data == "newgc":
        sess = cfg_get("userbot_session")
        if not sess:
            return cb_answer(cq["id"], "Bot not logged in. Contact admin.", True)
        if not USERBOT:
            return cb_answer(cq["id"], "Userbot inactive. Contact admin.", True)

        allowed, wait = user_can_make_gc(uid)
        if not allowed and not is_admin(uid):
            mins = wait // 60; secs = wait % 60
            return cb_answer(cq["id"], f"1 GC per hour. Wait: {mins}m {secs}s", True)

        cb_answer(cq["id"], "Creating GC...")
        send(chat, "<b>Your deal GC is being created. Please wait.</b>")
        threading.Thread(target=mm_worker, args=(uid, chat), daemon=True).start()
        return

    if data == "login_start":
        if chat != uid: return cb_answer(cq["id"], "DM only.", True)
        LOGIN_TMP.pop(uid, None); OTP_BUFFER.pop(uid, None); login_state_clear(uid)
        login_state_set(uid, "phone")
        del_msg(chat, mid)
        send_force_reply(chat, "<b>Send phone</b> (with country code):", "+91...")
        return cb_answer(cq["id"], "Reply below")

    if data == "login_cancel":
        LOGIN_TMP.pop(uid, None); OTP_BUFFER.pop(uid, None); login_state_clear(uid)
        del_msg(chat, mid); send(chat, "<b>Cancelled.</b>")
        return cb_answer(cq["id"], "X")

    if data.startswith("otp:"):
        action = data.split(":", 1)[1]
        st = login_state_get(uid)
        if not st or st["step"] != "otp":
            return cb_answer(cq["id"], "Session expired.", True)
        if uid not in LOGIN_TMP:
            return cb_answer(cq["id"], "Gone. Use .login again.", True)
        if action == "cancel":
            OTP_BUFFER.pop(uid, None); LOGIN_TMP.pop(uid, None); login_state_clear(uid)
            del_msg(chat, mid); send(chat, "<b>Cancelled.</b>")
            return cb_answer(cq["id"], "X")
        if action == "show":
            return cb_answer(cq["id"], f"{OTP_BUFFER.get(uid,'_') or '_'}", True)
        if action == "back":
            OTP_BUFFER[uid] = OTP_BUFFER.get(uid, "")[:-1]
        elif action.startswith("d:"):
            buf = OTP_BUFFER.get(uid, "")
            if len(buf) < 5: OTP_BUFFER[uid] = buf + action[2:]
        buf = OTP_BUFFER.get(uid, "")
        if len(buf) == 5:
            edit(chat, mid, "<b>Verifying...</b>")
            cb_answer(cq["id"], "Verifying")
            threading.Thread(target=otp_verify_worker,
                             args=(uid, chat, mid, st["phone"], buf, st["phash"]),
                             daemon=True).start()
            return
        txt, kb = render_otp_keypad(uid)
        edit(chat, mid, txt, kb)
        return cb_answer(cq["id"])

    if ":" not in data: return cb_answer(cq["id"])
    act, did = data.split(":", 1)
    d = db_one("SELECT * FROM deal_groups WHERE deal_id=?", (did,))
    if not d: return cb_answer(cq["id"], "Not found", True)
    if act == "tos": return cb_answer(cq["id"], cfg_get("tos_text", "No TOS."), True)
    if act == "upi": return cb_answer(cq["id"], cfg_get("mm_upi", UPI_ID), True)
    if act == "rvc":
        if not is_admin(uid): return cb_answer(cq["id"], "Admins only", True)
        update_deal(d["chat_id"], status="paid", paid_at=now())
        return cb_answer(cq["id"], "Marked received")
    return cb_answer(cq["id"])

# ============================================================
#  ROUTER
# ============================================================
UPDATE_QUEUE = queue.Queue(maxsize=500)

def update_worker():
    while True:
        try:
            u = UPDATE_QUEUE.get()
            if u is None: break
            handle(u)
        except Exception as e:
            print("worker err:", repr(e))
        finally:
            try: UPDATE_QUEUE.task_done()
            except: pass

def handle(update):
    try:
        if "message" in update:
            m = update["message"]
            if m.get("from"): save_user(m["from"])
            handle_command(m)
        elif "callback_query" in update:
            handle_callback(update["callback_query"])
    except Exception as e:
        print("handle err:", repr(e))

def bot_poll():
    print("🤖 Polling started...")
    offset = 0
    while True:
        try:
            r = api("getUpdates", {"offset": offset, "timeout": 25,
                                   "allowed_updates": '["message","callback_query"]'})
            if not r.get("ok"):
                time.sleep(1); continue
            for u in r.get("result", []):
                offset = u["update_id"] + 1
                try: UPDATE_QUEUE.put_nowait(u)
                except: handle(u)
        except Exception as e:
            print("poll err:", repr(e))
            time.sleep(1)

# ============================================================
#  MAIN
# ============================================================
if __name__ == "__main__":
    print("=" * 50)
    print("  RINZOI MM BOT — WITH DELETE GC")
    print("=" * 50)

    me = api("getMe")
    if me.get("ok"):
        BOT_USERNAME = me["result"]["username"]
        print(f"✅ Bot: @{BOT_USERNAME}")

    print("🚀 Userbot starting...")
    threading.Thread(target=userbot_thread_runner, daemon=True).start()
    UB_READY.wait(timeout=10)
    print("✅ Ready\n")

    for i in range(4):
        threading.Thread(target=update_worker, daemon=True).start()
    print("✅ 4 update workers started")

    threading.Thread(target=bot_poll, daemon=True).start()
    print("Bot ready! DM me `.login`")

    try:
        while True: time.sleep(60)
    except KeyboardInterrupt:
        print("\n👋")