import os
import html
import threading
from urllib.parse import quote

import httpx
from flask import Flask
from telegram import (
    Update, LinkPreviewOptions, InlineKeyboardButton, InlineKeyboardMarkup,
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, MessageHandler,
    ContextTypes, filters,
)

BOT_TOKEN = os.environ["BOT_TOKEN"]
SUPER_ADMIN_ID = int(os.environ["SUPER_ADMIN_ID"])
GROUP_ID = int(os.environ["GROUP_ID"])
SB_URL = os.environ["SUPABASE_URL"].rstrip("/")
SB_KEY = os.environ["SUPABASE_KEY"]
SITE = os.environ.get("SITE_URL", "https://ofcmovies.com/search.php")

HEADERS = {
    "apikey": SB_KEY,
    "Authorization": f"Bearer {SB_KEY}",
    "Content-Type": "application/json",
    "Prefer": "resolution=merge-duplicates,return=representation",
}
NO_PREVIEW = LinkPreviewOptions(is_disabled=True)
HTML = ParseMode.HTML


# ---------- Database ----------
async def sb(method: str, path: str, **kw):
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.request(method, f"{SB_URL}/rest/v1/{path}", headers=HEADERS, **kw)
        r.raise_for_status()
        return r.json() if r.text else None


async def get_admins() -> list[dict]:
    return await sb("GET", "admins?select=user_id,name&order=user_id") or []


async def get_admin_ids() -> list[int]:
    return [a["user_id"] for a in await get_admins()]


async def add_admin(uid: int, name: str = ""):
    await sb("POST", "admins", json={"user_id": uid, "name": name})


async def remove_admin(uid: int):
    await sb("DELETE", f"admins?user_id=eq.{uid}")


async def is_staff(uid: int) -> bool:
    return uid == SUPER_ADMIN_ID or uid in await get_admin_ids()


async def save_request(user, query: str):
    await sb("POST", "requests", json={
        "user_id": user.id,
        "username": user.username or "",
        "full_name": user.full_name,
        "query": query,
    })


# --- NEW (A) ---
async def get_setting(key: str, default: str | None = None):
    rows = await sb("GET", f"settings?key=eq.{key}&select=value")
    return rows[0]["value"] if rows else default


async def set_setting(key: str, value: str):
    await sb("POST", "settings", json={"key": key, "value": value})
# --- /NEW (A) ---


async def pending_requests(limit: int = 10) -> list[dict]:
    return await sb("GET", f"requests?status=eq.pending&order=id.desc&limit={limit}") or []


async def pending_count() -> int:
    rows = await sb("GET", "requests?status=eq.pending&select=id") or []
    return len(rows)


async def mark_done(req_id: int):
    rows = await sb("PATCH", f"requests?id=eq.{req_id}", json={"status": "done"})
    return rows[0] if rows else None


def is_private(update: Update) -> bool:
    return update.effective_chat.type == "private"


def mention(username: str, uid: int, name: str) -> str:
    if username:
        return f"@{html.escape(username)}"
    return f'<a href="tg://user?id={uid}">{html.escape(name or "User")}</a>'


# ---------- User commands ----------
async def find(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Aise use karo: /find movie ka naam\nExample: /find Pushpa")
        return
    query = " ".join(context.args)
    url = f"{SITE}?q={quote(query)}"
    text = f"🎬 <b>{html.escape(query)}</b>\n<a href=\"{url}\">▶️ WATCH</a>"
    await update.message.reply_text(text, parse_mode=HTML, link_preview_options=NO_PREVIEW)


async def request_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Aise use karo: /request movie ka naam\nExample: /request Pushpa 2")
        return

    user = update.effective_user
    query = " ".join(context.args)
    await save_request(user, query)

    msg = (
        "📩 <b>New Request</b>\n\n"
        f"🎬 <b>{html.escape(query)}</b>\n"
        f"👤 {mention(user.username, user.id, user.full_name)}\n"
        f"🆔 <code>{user.id}</code>"
    )
    for rid in {SUPER_ADMIN_ID, *await get_admin_ids()}:
        try:
            await context.bot.send_message(rid, msg, parse_mode=HTML)
        except Exception:
            pass
    await update.message.reply_text("✅ Aapki request admin tak pahunch gayi.")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    # admin ka naam save kar lo (remove list me naam dikhane ke liye)
    if is_private(update) and uid != SUPER_ADMIN_ID and uid in await get_admin_ids():
        await add_admin(uid, update.effective_user.full_name)
    await update.message.reply_text("Namaste! Group me /find ya /request use karo.")


async def chatid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"Chat ID: {update.effective_chat.id}")


# --- NEW (B) ---
DEFAULT_WELCOME = (
    "👋 Welcome {name}!\n\n"
    "🎬 Movie dhundhne ke liye: /find movie ka naam\n"
    "📩 Request karne ke liye: /request movie ka naam"
)


async def welcome(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != GROUP_ID:
        return
    if await get_setting("welcome_on", "1") != "1":
        return
    tmpl = await get_setting("welcome_text", DEFAULT_WELCOME)
    for m in update.message.new_chat_members:
        if m.is_bot:
            continue
        text = (
            html.escape(tmpl)
            .replace("{name}", mention(m.username, m.id, m.full_name))
            .replace("{group}", html.escape(update.effective_chat.title or "group"))
        )
        await update.message.reply_text(text, parse_mode=HTML, link_preview_options=NO_PREVIEW)
# --- /NEW (B) ---


# ---------- Admin panel ----------
async def panel_title(uid: int) -> str:
    return "👑 <b>Super Admin Panel</b>" if uid == SUPER_ADMIN_ID else "🛠 <b>Admin Panel</b>"


async def panel_markup(uid: int) -> InlineKeyboardMarkup:
    n = await pending_count()
    rows = [
        [InlineKeyboardButton(f"📩 Requests ({n})", callback_data="p:req")],
        [InlineKeyboardButton("📢 Board", callback_data="p:board")],
        [InlineKeyboardButton("👋 Welcome", callback_data="p:wel")],   # NEW (C)
    ]
    if uid == SUPER_ADMIN_ID:
        rows.append([
            InlineKeyboardButton("➕ Add Admin", callback_data="p:add"),
            InlineKeyboardButton("➖ Remove Admin", callback_data="p:rem"),
        ])
        rows.append([InlineKeyboardButton("👥 Admin List", callback_data="p:list")])
    return InlineKeyboardMarkup(rows)


async def admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_private(update) or not await is_staff(uid):
        return  # normal user ko kuch reply nahi
    context.user_data.pop("state", None)
    await update.message.reply_text(
        await panel_title(uid), parse_mode=HTML, reply_markup=await panel_markup(uid)
    )


async def safe_edit(q, text: str, markup=None):
    try:
        await q.edit_message_text(text, parse_mode=HTML, reply_markup=markup,
                                  link_preview_options=NO_PREVIEW)
    except Exception:
        pass  # "message is not modified" jaisa error ignore


BACK = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="p:back")]])


async def show_requests(q):
    rows = await pending_requests()
    if not rows:
        await safe_edit(q, "📩 <b>Requests</b>\n\nKoi pending request nahi hai. 🎉", BACK)
        return
    text = "📩 <b>Pending Requests</b> (latest 10)\n\n"
    buttons = []
    for r in rows:
        text += (
            f"<b>#{r['id']}</b> 🎬 {html.escape(r['query'])}\n"
            f"👤 {mention(r['username'], r['user_id'], r['full_name'])} "
            f"(<code>{r['user_id']}</code>)\n\n"
        )
        buttons.append(InlineKeyboardButton(f"✅ #{r['id']} Done", callback_data=f"p:done:{r['id']}"))
    kb = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]
    kb.append([InlineKeyboardButton("🔙 Back", callback_data="p:back")])
    await safe_edit(q, text, InlineKeyboardMarkup(kb))


# --- NEW (D) ---
async def show_welcome(q):
    on = await get_setting("welcome_on", "1") == "1"
    text = await get_setting("welcome_text", DEFAULT_WELCOME)
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✏️ Set Message", callback_data="p:wset")],
        [InlineKeyboardButton("🔕 Turn OFF" if on else "🔔 Turn ON", callback_data="p:wtog"),
         InlineKeyboardButton("🔄 Reset", callback_data="p:wreset")],
        [InlineKeyboardButton("🔙 Back", callback_data="p:back")],
    ])
    await safe_edit(
        q,
        f"👋 <b>Welcome Message</b>\nStatus: {'ON ✅' if on else 'OFF ❌'}\n\n"
        f"<i>Current:</i>\n{html.escape(text)}",
        kb,
    )
# --- /NEW (D) ---


async def panel_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    uid = q.from_user.id
    await q.answer()
    if not await is_staff(uid):
        return
    sup = uid == SUPER_ADMIN_ID
    data = q.data

    if data == "p:back":
        context.user_data.pop("state", None)
        await safe_edit(q, await panel_title(uid), await panel_markup(uid))

    elif data == "p:req":
        await show_requests(q)

    elif data.startswith("p:done:"):
        req = await mark_done(int(data.split(":")[2]))
        if req:
            # group me bina admin ka naam bataye notify
            link = f"{SITE}?q={quote(req['query'])}"
            who = mention(req["username"], req["user_id"], req["full_name"])
            try:
                await context.bot.send_message(
                    GROUP_ID,
                    f"✅ {who}, aapki request <b>{html.escape(req['query'])}</b> add ho gayi!\n"
                    f"<a href=\"{link}\">▶️ WATCH</a>",
                    parse_mode=HTML, link_preview_options=NO_PREVIEW,
                )
            except Exception:
                pass
        await show_requests(q)

    elif data == "p:board":
        context.user_data["state"] = "board"
        await safe_edit(q, "📢 <b>Board</b>\n\nAb wo message bhejo jo group me jana hai.\nCancel: /cancel", BACK)

    # --- NEW (D) branches ---
    elif data == "p:wel":
        await show_welcome(q)

    elif data == "p:wset":
        context.user_data["state"] = "welcome"
        await safe_edit(
            q,
            "✏️ <b>Naya welcome message bhejo</b>\n\n"
            "<code>{name}</code> likhoge to naye member ka naam aayega\n"
            "<code>{group}</code> likhoge to group ka naam aayega\n\n"
            "Cancel: /cancel",
            BACK,
        )

    elif data == "p:wtog":
        on = await get_setting("welcome_on", "1") == "1"
        await set_setting("welcome_on", "0" if on else "1")
        await show_welcome(q)

    elif data == "p:wreset":
        await set_setting("welcome_text", DEFAULT_WELCOME)
        await show_welcome(q)
    # --- /NEW (D) branches ---

    elif data == "p:add" and sup:
        context.user_data["state"] = "addadmin"
        await safe_edit(q, "➕ <b>Add Admin</b>\n\nUs user ki numeric ID bhejo (@userinfobot se milti hai).\nCancel: /cancel", BACK)

    elif data == "p:rem" and sup:
        admins = await get_admins()
        if not admins:
            await safe_edit(q, "Koi admin nahi hai.", BACK)
            return
        kb = [[InlineKeyboardButton(f"❌ {a.get('name') or a['user_id']}", callback_data=f"p:rm:{a['user_id']}")]
              for a in admins]
        kb.append([InlineKeyboardButton("🔙 Back", callback_data="p:back")])
        await safe_edit(q, "➖ <b>Kisko hatana hai?</b>", InlineKeyboardMarkup(kb))

    elif data.startswith("p:rm:") and sup:
        await remove_admin(int(data.split(":")[2]))
        await safe_edit(q, "✅ Admin hata diya.", BACK)

    elif data == "p:list" and sup:
        admins = await get_admins()
        text = "👥 <b>Admins</b>\n\n" + (
            "\n".join(f"• {html.escape(a.get('name') or '-')} (<code>{a['user_id']}</code>)" for a in admins)
            if admins else "Koi admin nahi hai."
        )
        await safe_edit(q, text, BACK)


async def text_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = context.user_data.get("state")
    uid = update.effective_user.id
    if not state or not await is_staff(uid):
        return

    if state == "board":
        await context.bot.send_message(GROUP_ID, update.message.text)
        await update.message.reply_text("✅ Group me bhej diya.", reply_markup=await panel_markup(uid))

    # --- NEW (D) ---
    elif state == "welcome":
        await set_setting("welcome_text", update.message.text)
        await update.message.reply_text(
            "✅ Welcome message save ho gaya.", reply_markup=await panel_markup(uid)
        )
    # --- /NEW (D) ---

    elif state == "addadmin" and uid == SUPER_ADMIN_ID:
        txt = update.message.text.strip()
        if not txt.isdigit():
            await update.message.reply_text("Sirf numeric ID bhejo ya /cancel karo.")
            return
        await add_admin(int(txt))
        await update.message.reply_text(
            f"✅ Admin add ho gaya: {txt}\nUsse bolo bot ko ek baar /start kare.",
            reply_markup=await panel_markup(uid),
        )
    context.user_data.pop("state", None)


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("state", None)
    await update.message.reply_text("Cancel ho gaya. /admin se panel kholo.")


# ---------- Keep-alive web server ----------
web = Flask(__name__)


@web.route("/")
def home():
    return "Bot is alive"


def run_web():
    web.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))


def main():
    threading.Thread(target=run_web, daemon=True).start()

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("find", find))
    app.add_handler(CommandHandler("request", request_cmd))
    app.add_handler(CommandHandler("admin", admin_cmd))
    app.add_handler(CommandHandler("cancel", cancel))
    app.add_handler(CommandHandler("chatid", chatid))
    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, welcome))  # NEW (E)
    app.add_handler(CallbackQueryHandler(panel_cb, pattern=r"^p:"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE, text_input))
    app.run_polling()


if __name__ == "__main__":
    main()
