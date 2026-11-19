import asyncio
import html
import logging
import os
import random
import re
import time

from dotenv import load_dotenv
from telegram import ForceReply, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatMemberStatus, ChatType, ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
from telethon import TelegramClient, errors
from telethon.sessions import StringSession
from telethon.tl.functions.channels import JoinChannelRequest
from telethon.tl.functions.messages import ImportChatInviteRequest

import db

load_dotenv()

BOT_TOKEN = os.environ["BOT_TOKEN"]
API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]
ADMIN_IDS = {int(x) for x in os.getenv("ADMIN_IDS", "").replace(" ", "").split(",") if x}
CONTROL_CHAT_ID = int(os.getenv("CONTROL_CHAT_ID", "-1004407796718"))
GAP_SECONDS = int(os.getenv("GAP_SECONDS", "60"))
MAX_FLOOD_WAIT = 300

logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("groupshelp")

clients: dict[int, TelegramClient] = {}
pending: dict[int, dict] = {}
round_lock = asyncio.Lock()

DEAD_SESSION_ERRORS = (
    errors.AuthKeyUnregisteredError,
    errors.SessionRevokedError,
    errors.SessionExpiredError,
    errors.UserDeactivatedError,
    errors.UserDeactivatedBanError,
)

HELP = (
    "<b>Commands</b>\n"
    "/addaccount – log in a userbot account\n"
    "/accounts – list accounts\n"
    "/delaccount &lt;id&gt; – log out and remove an account\n\n"
    "/addad – save the next text message as an ad\n"
    "/ads – list ads\n"
    "/delad &lt;id&gt; – delete an ad\n\n"
    "/addgroup &lt;chat id&gt; – add a group you own (bot must be admin there)\n"
    "/groups – list groups\n"
    "/delgroup &lt;id&gt; – remove a group\n\n"
    "/post – pick a group; every logged-in account sends one ad, {gap}s apart\n"
    "/cancel – cancel the current step"
)


def is_admin(update: Update) -> bool:
    return bool(update.effective_user) and update.effective_user.id in ADMIN_IDS


def admin_only(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not is_admin(update):
            return
        return await func(update, context)

    return wrapper


def control_only(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not update.effective_chat or update.effective_chat.id != CONTROL_CHAT_ID:
            return
        return await func(update, context)

    return wrapper


def new_client(session: str = "") -> TelegramClient:
    return TelegramClient(StringSession(session), API_ID, API_HASH)


async def ask(update: Update, text: str):
    await update.effective_chat.send_message(
        text, parse_mode=ParseMode.HTML, reply_markup=ForceReply(selective=False)
    )


async def drop_pending(user_id: int):
    state = pending.pop(user_id, None)
    if state and state.get("client"):
        try:
            await state["client"].disconnect()
        except Exception:
            pass


# ---------- start / help ----------

@admin_only
@control_only
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(HELP.format(gap=GAP_SECONDS), parse_mode=ParseMode.HTML)


@admin_only
@control_only
async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    had = update.effective_user.id in pending
    await drop_pending(update.effective_user.id)
    await update.message.reply_text("Cancelled." if had else "Nothing to cancel.")


# ---------- accounts ----------

@admin_only
@control_only
async def cmd_addaccount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await drop_pending(update.effective_user.id)
    pending[update.effective_user.id] = {"step": "phone"}
    await ask(update, "Reply with the phone number with country code, e.g. +919876543210\n/cancel to stop.")


async def login_phone(update: Update, state: dict, text: str):
    phone = "+" + re.sub(r"\D", "", text)
    if len(phone) < 8:
        await update.message.reply_text("That doesn't look like a phone number. Try again or /cancel.")
        return
    client = new_client()
    await client.connect()
    try:
        sent = await client.send_code_request(phone)
    except errors.PhoneNumberInvalidError:
        await client.disconnect()
        await update.message.reply_text("Invalid phone number. Try again or /cancel.")
        return
    except errors.FloodWaitError as e:
        await client.disconnect()
        pending.pop(update.effective_user.id, None)
        await update.message.reply_text(f"Telegram says wait {e.seconds}s before requesting another code.")
        return
    state.update(step="code", client=client, phone=phone, hash=sent.phone_code_hash)
    await ask(
        update,
        "Code sent to that account's Telegram app.\n"
        "Reply with it <b>with spaces</b>, e.g. <code>1 2 3 4 5</code> "
        "(Telegram cancels codes that are forwarded as-is). I'll delete your message.",
    )


async def login_code(update: Update, state: dict, text: str):
    code = re.sub(r"\D", "", text)
    try:
        await update.message.delete()
    except Exception:
        pass
    client: TelegramClient = state["client"]
    try:
        await client.sign_in(phone=state["phone"], code=code, phone_code_hash=state["hash"])
    except errors.SessionPasswordNeededError:
        state["step"] = "password"
        await ask(update, "This account has 2FA. Reply with its password (I'll delete your message).")
        return
    except errors.PhoneCodeInvalidError:
        await ask(update, "Wrong code. Reply with it again or /cancel.")
        return
    except errors.PhoneCodeExpiredError:
        await drop_pending(update.effective_user.id)
        await update.effective_chat.send_message("Code expired. Run /addaccount again.")
        return
    await finish_login(update, state)


async def login_password(update: Update, state: dict, text: str):
    try:
        await update.message.delete()
    except Exception:
        pass
    client: TelegramClient = state["client"]
    try:
        await client.sign_in(password=text)
    except errors.PasswordHashInvalidError:
        await ask(update, "Wrong 2FA password. Reply with it again or /cancel.")
        return
    await finish_login(update, state)


async def finish_login(update: Update, state: dict):
    client: TelegramClient = state["client"]
    me = await client.get_me()
    name = " ".join(x for x in (me.first_name, me.last_name) if x) or str(me.id)
    acc = db.save_account(state["phone"], me.id, name, client.session.save())
    old = clients.pop(acc["id"], None)
    if old and old is not client:
        await old.disconnect()
    clients[acc["id"]] = client
    pending.pop(update.effective_user.id, None)
    await update.effective_chat.send_message(
        f"Logged in: <b>{html.escape(name)}</b> ({state['phone']}) as account #{acc['id']}.",
        parse_mode=ParseMode.HTML,
    )


@admin_only
@control_only
async def cmd_accounts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    accs = db.list_accounts()
    if not accs:
        await update.message.reply_text("No accounts. Use /addaccount.")
        return
    lines = []
    for a in accs:
        status = "online" if a["id"] in clients else ("offline" if a["active"] else "logged out")
        lines.append(f"#{a['id']} {html.escape(a['name'] or '')} – {a['phone']} – {status}")
    await update.message.reply_text("<b>Accounts</b>\n" + "\n".join(lines), parse_mode=ParseMode.HTML)


@admin_only
@control_only
async def cmd_delaccount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args or not context.args[0].lstrip("#").isdigit():
        await update.message.reply_text("Usage: /delaccount <id> (see /accounts)")
        return
    acc_id = int(context.args[0].lstrip("#"))
    acc = db.get_account(acc_id)
    if not acc:
        await update.message.reply_text("No such account.")
        return
    client = clients.pop(acc_id, None)
    if client:
        try:
            await client.log_out()
        except Exception:
            await client.disconnect()
    db.delete_account(acc_id)
    await update.message.reply_text(f"Account #{acc_id} logged out and removed.")


# ---------- ads ----------

@admin_only
@control_only
async def cmd_addad(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await drop_pending(update.effective_user.id)
    pending[update.effective_user.id] = {"step": "ad"}
    await ask(update, "Reply with the ad text (formatting is kept). /cancel to stop.")


@admin_only
@control_only
async def cmd_ads(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ads = db.list_ads()
    if not ads:
        await update.message.reply_text("No ads. Use /addad.")
        return
    await update.message.reply_text(f"{len(ads)} ad(s):")
    for ad in ads:
        await update.effective_chat.send_message(
            f"<b>#{ad['id']}</b>\n{ad['text']}", parse_mode=ParseMode.HTML, disable_web_page_preview=True
        )


@admin_only
@control_only
async def cmd_delad(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args or not context.args[0].lstrip("#").isdigit():
        await update.message.reply_text("Usage: /delad <id> (see /ads)")
        return
    ok = db.delete_ad(int(context.args[0].lstrip("#")))
    await update.message.reply_text("Ad deleted." if ok else "No such ad.")


# ---------- groups ----------

def parse_group_ref(ref: str):
    ref = ref.strip()
    if re.fullmatch(r"-?\d+", ref):
        return int(ref)
    m = re.fullmatch(r"(?:https?://)?(?:t\.me|telegram\.me)/([A-Za-z0-9_]{4,})/?", ref)
    if m:
        return "@" + m.group(1)
    if re.fullmatch(r"@?[A-Za-z0-9_]{4,}", ref):
        return "@" + ref.lstrip("@")
    return None


@admin_only
@control_only
async def cmd_addgroup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    bot = context.bot
    chat_ref = parse_group_ref(context.args[0]) if context.args else None
    if chat_ref is None:
        await update.message.reply_text(
            "Usage: /addgroup <chat id>, e.g. /addgroup -1001234567890\n"
            "Add the bot to that group as admin first."
        )
        return
    if chat_ref == CONTROL_CHAT_ID:
        await update.message.reply_text("That's this control chat.")
        return
    try:
        chat = await bot.get_chat(chat_ref)
        member = await bot.get_chat_member(chat.id, update.effective_user.id)
    except Exception as e:
        await update.effective_message.reply_text(f"Can't read that group (is the bot in it?): {e}")
        return
    if chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        await update.effective_message.reply_text("That's not a group.")
        return
    if member.status != ChatMemberStatus.OWNER:
        await update.effective_message.reply_text("Only groups you own can be added.")
        return
    link = f"https://t.me/{chat.username}" if chat.username else chat.invite_link
    if not link:
        try:
            link = await bot.export_chat_invite_link(chat.id)
        except Exception:
            link = None
    g = db.save_group(chat.id, chat.title, link)
    note = "" if link else "\nNo invite link (make the bot admin with invite rights) – accounts must already be members."
    await update.effective_message.reply_text(
        f"Group #{g['id']} saved: {chat.title}{note}"
    )


@admin_only
@control_only
async def cmd_groups(update: Update, context: ContextTypes.DEFAULT_TYPE):
    groups = db.list_groups()
    if not groups:
        await update.message.reply_text("No groups. Use /addgroup <chat id>.")
        return
    lines = [f"#{g['id']} {html.escape(g['title'] or '')} (<code>{g['chat_id']}</code>)" for g in groups]
    await update.message.reply_text("<b>Groups</b>\n" + "\n".join(lines), parse_mode=ParseMode.HTML)


@admin_only
@control_only
async def cmd_delgroup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args or not context.args[0].lstrip("#").isdigit():
        await update.message.reply_text("Usage: /delgroup <id> (see /groups)")
        return
    ok = db.delete_group(int(context.args[0].lstrip("#")))
    await update.message.reply_text("Group removed." if ok else "No such group.")


# ---------- posting ----------

async def resolve_group(client: TelegramClient, group: dict):
    try:
        return await client.get_input_entity(group["chat_id"])
    except (ValueError, TypeError):
        pass
    await client.get_dialogs(limit=300)
    try:
        return await client.get_input_entity(group["chat_id"])
    except (ValueError, TypeError):
        pass
    link = group.get("link")
    if not link:
        raise RuntimeError("not a member of the group")
    m = re.search(r"t\.me/(?:\+|joinchat/)([A-Za-z0-9_-]+)", link)
    try:
        if m:
            await client(ImportChatInviteRequest(m.group(1)))
        else:
            await client(JoinChannelRequest(link.rstrip("/").rsplit("/", 1)[-1]))
    except errors.UserAlreadyParticipantError:
        pass
    await client.get_dialogs(limit=50)
    return await client.get_input_entity(group["chat_id"])


async def send_ad(client: TelegramClient, peer, text: str):
    try:
        await client.send_message(peer, text, parse_mode="html")
    except (errors.FloodWaitError, errors.SlowModeWaitError) as e:
        if e.seconds > MAX_FLOOD_WAIT:
            raise
        await asyncio.sleep(e.seconds + 1)
        await client.send_message(peer, text, parse_mode="html")


async def run_round(app: Application, chat_id: int, group: dict):
    accounts = [a for a in db.list_accounts(active_only=True) if a["id"] in clients]
    ads = db.list_ads()
    if not accounts or not ads:
        await app.bot.send_message(chat_id, "Need at least one online account (/accounts) and one ad (/ads).")
        return

    pool: list[dict] = []
    lines = [f"Posting to <b>{html.escape(group['title'] or str(group['chat_id']))}</b> – "
             f"{len(accounts)} account(s), {GAP_SECONDS}s gap"]
    status = await app.bot.send_message(chat_id, "\n".join(lines), parse_mode=ParseMode.HTML)

    async def update_status():
        try:
            await status.edit_text("\n".join(lines), parse_mode=ParseMode.HTML)
        except Exception:
            pass

    sent = 0
    last_sent = 0.0
    for acc in accounts:
        client = clients.get(acc["id"])
        label = f"#{acc['id']} {html.escape(acc['name'] or acc['phone'])}"
        if not client:
            lines.append(f"{label}: skipped (offline)")
            continue
        if not pool:
            pool = random.sample(ads, len(ads))
        ad = pool.pop()
        try:
            peer = await resolve_group(client, group)
            wait = last_sent + GAP_SECONDS - time.monotonic()
            if last_sent and wait > 0:
                lines.append(f"{label}: waiting {int(wait)}s…")
                await update_status()
                await asyncio.sleep(wait)
                lines.pop()
            await send_ad(client, peer, ad["text"])
            last_sent = time.monotonic()
            sent += 1
            lines.append(f"{label}: sent ad #{ad['id']}")
        except DEAD_SESSION_ERRORS as e:
            db.set_account_active(acc["id"], False)
            clients.pop(acc["id"], None)
            lines.append(f"{label}: session dead ({type(e).__name__}), removed from rotation")
        except errors.FloodWaitError as e:
            lines.append(f"{label}: skipped, Telegram flood wait {e.seconds}s")
        except errors.PeerFloodError:
            lines.append(f"{label}: skipped, account is limited by Telegram (PeerFlood) – rest it")
        except (errors.ChatWriteForbiddenError, errors.UserBannedInChannelError, errors.ChannelPrivateError) as e:
            lines.append(f"{label}: can't write in group ({type(e).__name__})")
        except Exception as e:
            log.exception("send failed for account %s", acc["id"])
            lines.append(f"{label}: failed ({html.escape(str(e))[:120]})")
        await update_status()

    lines.append(f"\nDone: {sent}/{len(accounts)} sent.")
    await update_status()


async def start_round(update: Update, context: ContextTypes.DEFAULT_TYPE, group: dict):
    if round_lock.locked():
        await update.effective_chat.send_message("A round is already running. Wait for it to finish.")
        return

    async def job():
        async with round_lock:
            try:
                await run_round(context.application, update.effective_chat.id, group)
            except Exception:
                log.exception("round failed")
                await context.bot.send_message(update.effective_chat.id, "Round crashed, check logs.")

    context.application.create_task(job())


@admin_only
@control_only
async def cmd_post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    groups = db.list_groups()
    if not groups:
        await update.message.reply_text("No groups. Use /addgroup <chat id>.")
        return
    kb = [[InlineKeyboardButton(g["title"] or str(g["chat_id"]), callback_data=f"post:{g['id']}")] for g in groups]
    await update.message.reply_text("Post to which group?", reply_markup=InlineKeyboardMarkup(kb))


async def cb_post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not is_admin(update) or update.effective_chat.id != CONTROL_CHAT_ID:
        await q.answer()
        return
    group = db.get_group(int(q.data.split(":", 1)[1]))
    await q.answer()
    if not group:
        await q.edit_message_text("Group no longer exists.")
        return
    await q.edit_message_text(f"Starting round for {group['title']}…")
    await start_round(update, context, group)


# ---------- text input router ----------

@admin_only
@control_only
async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    reply_to = update.message.reply_to_message
    if not reply_to or not reply_to.from_user or reply_to.from_user.id != context.bot.id:
        return
    state = pending.get(update.effective_user.id)
    if not state:
        return
    text = update.message.text
    step = state["step"]
    try:
        if step == "phone":
            await login_phone(update, state, text)
        elif step == "code":
            await login_code(update, state, text)
        elif step == "password":
            await login_password(update, state, text)
        elif step == "ad":
            ad_id = db.add_ad(update.message.text_html)
            pending.pop(update.effective_user.id, None)
            await update.message.reply_text(f"Ad #{ad_id} saved.")
    except Exception as e:
        log.exception("step %s failed", step)
        await drop_pending(update.effective_user.id)
        await update.effective_chat.send_message(f"Failed: {e}\nStart again.")


# ---------- lifecycle ----------

async def post_init(app: Application):
    for acc in db.list_accounts(active_only=True):
        client = new_client(acc["session"])
        try:
            await client.connect()
            if await client.is_user_authorized():
                clients[acc["id"]] = client
                log.info("account #%s %s online", acc["id"], acc["phone"])
                continue
            log.warning("account #%s %s is logged out", acc["id"], acc["phone"])
            db.set_account_active(acc["id"], False)
        except Exception:
            log.exception("account #%s failed to connect", acc["id"])
        await client.disconnect()
    log.info("control_chat=%s admins=%s gap=%ss accounts_online=%s", CONTROL_CHAT_ID, sorted(ADMIN_IDS), GAP_SECONDS, len(clients))


async def post_shutdown(app: Application):
    for client in list(clients.values()):
        try:
            await client.disconnect()
        except Exception:
            pass


def main():
    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .concurrent_updates(True)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )
    app.add_handler(CommandHandler(["start", "help"], cmd_start))
    app.add_handler(CommandHandler("cancel", cmd_cancel))
    app.add_handler(CommandHandler("addaccount", cmd_addaccount))
    app.add_handler(CommandHandler("accounts", cmd_accounts))
    app.add_handler(CommandHandler("delaccount", cmd_delaccount))
    app.add_handler(CommandHandler("addad", cmd_addad))
    app.add_handler(CommandHandler("ads", cmd_ads))
    app.add_handler(CommandHandler("delad", cmd_delad))
    app.add_handler(CommandHandler("addgroup", cmd_addgroup))
    app.add_handler(CommandHandler("groups", cmd_groups))
    app.add_handler(CommandHandler("delgroup", cmd_delgroup))
    app.add_handler(CommandHandler("post", cmd_post))
    app.add_handler(CallbackQueryHandler(cb_post, pattern=r"^post:\d+$"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
