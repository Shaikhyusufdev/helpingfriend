import os
import html
import threading
from urllib.parse import quote

import httpx
from flask import Flask
from telegram import Update, LinkPreviewOptions
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes

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
    "Prefer": "resolution=merge-duplicates",
}
NO_PREVIEW = LinkPreviewOptions(is_disabled=True)


# ---------- Database (Supabase) ----------
async def get_admin_ids() -> list[int]:
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get(f"{SB_URL}/rest/v1/admins?select=user_id", headers=HEADERS)
        r.raise_for_status()
        return [row["user_id"] for row in r.json()]


async def add_admin(uid: int, name: str = ""):
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.post(
            f"{SB_URL}/rest/v1/admins",
            headers=HEADERS,
            json={"user_id": uid, "name": name},
        )
        r.raise_for_status()


async def remove_admin(uid: int):
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.delete(f"{SB_URL}/rest/v1/admins?user_id=eq.{uid}", headers=HEADERS)
        r.raise_for_status()


async def is_staff(uid: int) -> bool:
    return uid == SUPER_ADMIN_ID or uid in await get_admin_ids()


def is_private(update: Update) -> bool:
    return update.effective_chat.type == "private"


# ---------- Commands ----------
async def find(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Aise use karo: /find movie ka naam\nExample: /find Pushpa")
        return
    query = " ".join(context.args)
    url = f"{SITE}?q={quote(query)}"
    text = f"🎬 <b>{html.escape(query)}</b>\n<a href=\"{url}\">▶️ WATCH</a>"
    await update.message.reply_text(
        text, parse_mode=ParseMode.HTML, link_preview_options=NO_PREVIEW
    )


async def request_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Aise use karo: /request movie ka naam\nExample: /request Pushpa 2")
        return

    user = update.effective_user
    query = " ".join(context.args)

    if user.username:
        who = f"@{html.escape(user.username)}"
    else:
        who = f'<a href="tg://user?id={user.id}">{html.escape(user.full_name)}</a>'

    msg = (
        "📩 <b>New Request</b>\n\n"
        f"🎬 <b>{html.escape(query)}</b>\n"
        f"👤 {who}\n"
        f"🆔 <code>{user.id}</code>"
    )

    recipients = {SUPER_ADMIN_ID, *await get_admin_ids()}
    for rid in recipients:
        try:
            await context.bot.send_message(rid, msg, parse_mode=ParseMode.HTML)
        except Exception:
            pass  # admin ne bot ko /start nahi kiya hoga

    await update.message.reply_text("✅ Aapki request admin tak pahunch gayi.")


async def board(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # sirf private chat me, taaki group me admin ka command na dikhe
    if not is_private(update) or not await is_staff(update.effective_user.id):
        return
    parts = update.message.text.split(maxsplit=1)
    if len(parts) < 2:
        await update.message.reply_text("Aise use karo: /board aapka message")
        return
    await context.bot.send_message(GROUP_ID, parts[1])
    await update.message.reply_text("✅ Group me bhej diya.")


async def addadmin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_private(update) or update.effective_user.id != SUPER_ADMIN_ID:
        return
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("Aise use karo: /addadmin USER_ID\n(ID @userinfobot se mil jayegi)")
        return
    uid = int(context.args[0])
    await add_admin(uid)
    await update.message.reply_text(
        f"✅ Admin add ho gaya: {uid}\nUsse bolo bot ko ek baar /start kare."
    )


async def removeadmin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_private(update) or update.effective_user.id != SUPER_ADMIN_ID:
        return
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("Aise use karo: /removeadmin USER_ID")
        return
    await remove_admin(int(context.args[0]))
    await update.message.reply_text("✅ Admin hata diya.")


async def admins(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_private(update) or update.effective_user.id != SUPER_ADMIN_ID:
        return
    ids = await get_admin_ids()
    text = "👥 Admins:\n" + ("\n".join(f"• {i}" for i in ids) if ids else "Koi admin nahi hai.")
    await update.message.reply_text(text)


async def chatid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"Chat ID: {update.effective_chat.id}")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Namaste! Group me /find ya /request use karo.")


# ---------- Keep-alive web server (Render + UptimeRobot) ----------
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
    app.add_handler(CommandHandler("board", board))
    app.add_handler(CommandHandler("addadmin", addadmin))
    app.add_handler(CommandHandler("removeadmin", removeadmin))
    app.add_handler(CommandHandler("admins", admins))
    app.add_handler(CommandHandler("chatid", chatid))
    app.run_polling()


if __name__ == "__main__":
    main()
