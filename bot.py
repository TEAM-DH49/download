import asyncio
import logging
import os
import io
import csv
import time
from dotenv import load_dotenv

load_dotenv()

from telegram import (
    Update,
    InlineQuery,
    InlineQueryResultVideo,
    InlineQueryResultPhoto,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    BotCommand,
    BotCommandScopeDefault,
    BotCommandScopeAllPrivateChats,
    BotCommandScopeAllChatAdministrators,
    MenuButtonCommands,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
    InlineQueryHandler,
    CallbackQueryHandler,
)
from insta_downloader import download_instagram
from database import (
    init_db,
    ensure_user,
    is_admin,
    set_admin,
    is_banned,
    ban_user,
    increment_download,
    add_download_record,
    add_to_queue,
    get_next_queue_item,
    mark_queue_item_processed,
    get_stats,
    get_all_users,
    get_all_users_with_details,
    get_banned_users,
    get_recent_downloads,
    clear_old_downloads,
    set_maintenance,
    is_maintenance,
    get_setting,
    set_setting,
    get_user_details,
    get_user_downloads,
    get_analytics,
    get_queue_status,
    clear_queue,
    add_required_channel,
    get_required_channels,
    remove_required_channel,
    set_premium,
    remove_premium,
    get_premium_users,
    is_premium,
    set_user_download_limit,
    get_user_download_limit,
    backup_database,
    get_platform_stats,
    get_top_users,
)

logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ["BOT_TOKEN"]
RAPIDAPI_KEY = os.environ["RAPIDAPI_KEY"]
ADMIN_USER_ID = int(os.environ.get("ADMIN_USER_ID", "6272469420"))

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

SPINNER_FRAMES = ["⏳", "⏳.", "⏳..", "⏳..."]
queue_processing = False


async def get_unjoined_channels(bot, user_id: int):
    """Return list of required-channel dicts the user has NOT joined yet."""
    channels = get_required_channels()
    if not channels:
        return []

    unjoined = []
    for ch in channels:
        username = ch["channel_username"].lstrip("@")
        try:
            member = await bot.get_chat_member(f"@{username}", user_id)
            if member.status in ("left", "kicked"):
                unjoined.append(ch)
        except TelegramError as e:
            logger.warning(f"Could not verify membership for @{username}: {e}")
            continue

    return unjoined


def build_join_keyboard(unjoined_channels):
    buttons = [
        [InlineKeyboardButton(f"➕ Join @{ch['channel_username'].lstrip('@')}", url=f"https://t.me/{ch['channel_username'].lstrip('@')}")]
        for ch in unjoined_channels
    ]
    buttons.append([InlineKeyboardButton("✅ I have joined", callback_data="check_join")])
    return InlineKeyboardMarkup(buttons)


async def spin_status(status_msg, stop_event: asyncio.Event):
    i = 0
    while not stop_event.is_set():
        frame = SPINNER_FRAMES[i % len(SPINNER_FRAMES)]
        try:
            await status_msg.edit_text(f"{frame} Downloading...")
        except Exception:
            pass
        i += 1
        await asyncio.sleep(0.6)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ensure_user(
        user.id,
        username=user.username or "",
        first_name=user.first_name or "",
        last_name=user.last_name or "",
    )
    if user.id == ADMIN_USER_ID:
        set_admin(user.id, True)

    text = (
        "👋 Hello! Send me an Instagram Reel or Post link and I'll download it for you.\n\n"
        "Supported links:\n"
        "- https://www.instagram.com/reel/...\n"
        "- https://www.instagram.com/p/...\n"
        "- https://www.instagram.com/tv/...\n\n"
        "Inline mode: Use me in any chat by typing @botname <url>\n\n"
        "Type /commands to see all available commands"
    )

    await update.message.reply_text(text, disable_web_page_preview=True)


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ensure_user(user.id, username=user.username or "", first_name=user.first_name or "", last_name=user.last_name or "")

    text = (
        "📥 Just send any Instagram post/reel URL and I'll download it for you.\n\n"
        "Commands:\n"
        "/start - Start the bot\n"
        "/help - Show this message\n"
        "/commands - Show all available commands\n"
    )

    if is_admin(user.id):
        text += (
            "\n👑 Admin Commands:\n"
            "/stats - Bot statistics\n"
            "/users - List all users\n"
            "/banlist - View banned users\n"
            "/recentdownloads - Recent downloads\n"
            "/analytics - Bot analytics\n"
            "/userinfo <id> - User details\n"
            "/exportusers - Export users CSV\n"
            "/queuestatus - Queue status\n"
            "/clearqueue - Clear stuck queue\n"
            "/checkapi - Check API status\n"
            "/broadcast <msg> - Send message to all users\n"
            "/maintenance on/off - Toggle maintenance mode\n"
            "/cleardownloads - Delete downloads older than 30 days\n"
            "/channels - Manage required channels\n"
            "/channelsadd @username - Add required channel\n"
            "/channelsremove <id> - Remove required channel\n"
            "/premium <id> [days] - Give premium\n"
            "/removepremium <id> - Remove premium\n"
            "/premiumlist - List premium users\n"
            "/setlimit <id> <limit> - Set download limit\n"
            "/backup - Backup database\n"
            "/restart - Restart bot\n"
            "/logs - Recent logs\n"
            "/topusers - Top downloaders\n"
            "/ban <user_id> - Ban user\n"
            "/unban <user_id> - Unban user\n"
            "/makeadmin <user_id> - Make admin\n"
        )

    await update.message.reply_text(text)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    url = update.message.text.strip()

    ensure_user(
        user.id,
        username=user.username or "",
        first_name=user.first_name or "",
        last_name=user.last_name or "",
    )
    if user.id == ADMIN_USER_ID:
        set_admin(user.id, True)

    if is_banned(user.id):
        await update.message.reply_text("❌ You are banned from using this bot.")
        return

    if is_maintenance() and not is_admin(user.id):
        await update.message.reply_text("🔧 Bot is under maintenance. Please try again later.")
        return

    if not is_admin(user.id):
        unjoined = await get_unjoined_channels(context.bot, user.id)
        if unjoined:
            await update.message.reply_text(
                "🔒 Please join our channel to use this bot:",
                reply_markup=build_join_keyboard(unjoined),
            )
            return

    if not ("instagram.com" in url or "instagr.am" in url):
        await update.message.reply_text("❌ Please send a valid Instagram URL.")
        return

    status_msg = await update.message.reply_text("⏳ Downloading...")
    stop_event = asyncio.Event()
    spinner_task = asyncio.create_task(spin_status(status_msg, stop_event))

    try:
        media_path, caption, media_type, error = await download_instagram(url)

        stop_event.set()
        await spinner_task

        if error:
            await status_msg.edit_text(f"❌ {error}")
            add_download_record(user.id, url, "instagram", "failed")
            return

        add_download_record(user.id, url, "instagram", "completed")
        increment_download(user.id)

        await status_msg.edit_text("⏳ Uploading to Telegram...")

        with open(media_path, 'rb') as f:
            if "video" in media_type or media_path.endswith('.mp4'):
                for attempt in range(3):
                    try:
                        await update.message.reply_video(f, caption=caption[:1024])
                        break
                    except Exception as e:
                        if attempt < 2:
                            await asyncio.sleep(2 ** attempt)
                            f.seek(0)
                        else:
                            raise
            else:
                for attempt in range(3):
                    try:
                        await update.message.reply_photo(f, caption=caption[:1024])
                        break
                    except Exception as e:
                        if attempt < 2:
                            await asyncio.sleep(2 ** attempt)
                            f.seek(0)
                        else:
                            raise

        await status_msg.delete()

    except Exception as e:
        stop_event.set()
        try:
            await spinner_task
        except Exception:
            pass
        logger.error(f"Error for user {user.id}: {e}")
        await status_msg.edit_text(f"❌ Error: {str(e)[:200]}")


async def check_join_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = query.from_user

    unjoined = await get_unjoined_channels(context.bot, user.id)

    if unjoined:
        await query.answer("❌ You haven't joined all required channels yet.", show_alert=True)
        try:
            await query.edit_message_reply_markup(reply_markup=build_join_keyboard(unjoined))
        except Exception:
            pass
        return

    await query.answer("✅ Thanks for joining! You can now send links.", show_alert=True)
    try:
        await query.edit_message_text("✅ Verified! Send me an Instagram Reel or Post link and I'll download it for you.")
    except Exception:
        pass


async def inline_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.inline_query.query.strip()
    user = update.effective_user

    ensure_user(user.id, username=user.username or "", first_name=user.first_name or "", last_name=user.last_name or "")
    if user.id == ADMIN_USER_ID:
        set_admin(user.id, True)

    if is_banned(user.id):
        return

    if is_maintenance() and not is_admin(user.id):
        return

    if not is_admin(user.id):
        unjoined = await get_unjoined_channels(context.bot, user.id)
        if unjoined:
            await update.inline_query.answer(
                [],
                switch_pm_text="🔒 Join our channel first, tap here",
                switch_pm_parameter="join",
                cache_time=0,
            )
            return

    if not query or not ("instagram.com" in query or "instagr.am" in query):
        await update.inline_query.answer([])
        return

    try:
        media_path, caption, media_type, error = await download_instagram(query)

        if error or not media_path:
            await update.inline_query.answer([])
            return

        results = []
        with open(media_path, 'rb') as f:
            if "video" in media_type or media_path.endswith('.mp4'):
                results.append(
                    InlineQueryResultVideo(
                        id="1",
                        video_url=media_path,
                        title="Instagram Reel/Video",
                        caption=caption[:1024] if caption else None,
                        thumb_url="https://upload.wikimedia.org/wikipedia/commons/thumb/a/a5/Instagram_icon.png/150px-Instagram_icon.png"
                    )
                )
            else:
                results.append(
                    InlineQueryResultPhoto(
                        id="1",
                        photo_url=media_path,
                        title="Instagram Photo",
                        caption=caption[:1024] if caption else None,
                        thumb_url="https://upload.wikimedia.org/wikipedia/commons/thumb/a/a5/Instagram_icon.png/150px-Instagram_icon.png"
                    )
                )

        await update.inline_query.answer(results, cache_time=300)

    except Exception as e:
        logger.error(f"Inline error for user {user.id}: {e}")
        await update.inline_query.answer([])


async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_admin(user.id):
        await update.message.reply_text("❌ Admin only command.")
        return

    stats = get_stats()
    await update.message.reply_text(
        f"📊 Bot Statistics:\n\n"
        f"Total users: {stats['total_users']}\n"
        f"Total downloads: {stats['total_downloads']}\n"
        f"Today's downloads: {stats['today_downloads']}"
    )


async def users_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_admin(user.id):
        await update.message.reply_text("❌ Admin only command.")
        return

    users = get_all_users()
    text = f"👥 Total Users: {len(users)}\n\n"
    for u in users[:20]:
        admin = "✅" if u['is_admin'] else "❌"
        banned = "🚫" if u['is_banned'] else ""
        text += f"{admin}{banned} {u['user_id']} - {u['username'] or u['first_name']} - Downloads: {u['downloads_today']}\n"

    if len(text) > 4000:
        text = text[:4000] + "\n... (showing first 20)"
    await update.message.reply_text(text)


async def ban_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_admin(user.id):
        await update.message.reply_text("❌ Admin only command.")
        return

    if not context.args:
        await update.message.reply_text("Usage: /ban <user_id>")
        return

    target_id = int(context.args[0])
    ban_user(target_id, True)
    await update.message.reply_text(f"✅ User {target_id} has been banned.")


async def unban_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_admin(user.id):
        await update.message.reply_text("❌ Admin only command.")
        return

    if not context.args:
        await update.message.reply_text("Usage: /unban <user_id>")
        return

    target_id = int(context.args[0])
    ban_user(target_id, False)
    await update.message.reply_text(f"✅ User {target_id} has been unbanned.")


async def makeadmin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_admin(user.id):
        await update.message.reply_text("❌ Admin only command.")
        return

    if not context.args:
        await update.message.reply_text("Usage: /makeadmin <user_id>")
        return

    target_id = int(context.args[0])
    set_admin(target_id, True)
    await update.message.reply_text(f"✅ User {target_id} is now an admin.")


async def broadcast_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_admin(user.id):
        await update.message.reply_text("❌ Admin only command.")
        return

    if not context.args:
        await update.message.reply_text("Usage: /broadcast <message>")
        return

    message = " ".join(context.args)
    users = get_all_users()
    success = 0
    fail = 0

    status_msg = await update.message.reply_text(f"📢 Broadcasting to {len(users)} users...")

    for u in users:
        try:
            await context.bot.send_message(chat_id=u["user_id"], text=message)
            success += 1
        except Exception:
            fail += 1

    await status_msg.edit_text(f"✅ Broadcast complete!\nSent: {success}\nFailed: {fail}")


async def maintenance_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_admin(user.id):
        await update.message.reply_text("❌ Admin only command.")
        return

    if not context.args:
        current = is_maintenance()
        await update.message.reply_text(f"Maintenance mode is currently: {'ON' if current else 'OFF'}")
        return

    mode = context.args[0].lower()
    if mode == "on":
        set_maintenance(True)
        await update.message.reply_text("🔧 Maintenance mode is now ON. Normal users cannot use the bot.")
    elif mode == "off":
        set_maintenance(False)
        await update.message.reply_text("✅ Maintenance mode is now OFF. Bot is back online.")
    else:
        await update.message.reply_text("Usage: /maintenance on or /maintenance off")


async def banlist_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_admin(user.id):
        await update.message.reply_text("❌ Admin only command.")
        return

    banned = get_banned_users()
    if not banned:
        await update.message.reply_text("No banned users.")
        return

    text = f"🚫 Banned Users ({len(banned)}):\n\n"
    for u in banned[:20]:
        text += f"{u['user_id']} - {u['username'] or u['first_name']}\n"

    if len(text) > 4000:
        text = text[:4000] + "\n... (showing first 20)"
    await update.message.reply_text(text)


async def recentdownloads_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_admin(user.id):
        await update.message.reply_text("❌ Admin only command.")
        return

    downloads = get_recent_downloads(limit=20)
    if not downloads:
        await update.message.reply_text("No downloads yet.")
        return

    text = f"📥 Recent Downloads (last {len(downloads)}):\n\n"
    for d in downloads:
        text += f"User {d['user_id']} - {d['platform']} - {d['status']}\nURL: {d['url'][:50]}\n\n"

    if len(text) > 4000:
        text = text[:4000] + "\n... (showing first 20)"
    await update.message.reply_text(text)


async def cleardownloads_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_admin(user.id):
        await update.message.reply_text("❌ Admin only command.")
        return

    deleted = clear_old_downloads(days=30)
    await update.message.reply_text(f"🗑 Deleted {deleted} downloads older than 30 days.")


async def analytics_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_admin(user.id):
        await update.message.reply_text("❌ Admin only command.")
        return

    days = 7
    if context.args:
        try:
            days = int(context.args[0])
        except ValueError:
            pass

    data = get_analytics(days=days)
    text = (
        f"📊 Analytics (last {days} days):\n\n"
        f"👥 New users: {data['new_users']}\n"
        f"📥 Total downloads: {data['total_downloads']}\n"
        f"✅ Completed: {data['completed']}\n"
        f"❌ Failed: {data['failed']}\n"
        f"📈 Success rate: {data['success_rate']}%\n\n"
        "Platforms:\n"
    )
    for p in data["platforms"]:
        text += f"- {p['platform']}: {p['cnt']}\n"

    text += "\nDaily breakdown:\n"
    for d in data["daily"]:
        text += f"- {d['day']}: {d['cnt']}\n"

    await update.message.reply_text(text)


async def userinfo_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_admin(user.id):
        await update.message.reply_text("❌ Admin only command.")
        return

    if not context.args:
        await update.message.reply_text("Usage: /userinfo <user_id>")
        return

    try:
        target_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Invalid user ID.")
        return

    details = get_user_details(target_id)
    if not details:
        await update.message.reply_text("User not found.")
        return

    downloads = get_user_downloads(target_id, limit=5)
    text = (
        f"👤 User Info:\n\n"
        f"ID: {details['user_id']}\n"
        f"Name: {details['first_name']} {details['last_name'] or ''}\n"
        f"Username: @{details['username'] or 'N/A'}\n"
        f"Admin: {'Yes' if details['is_admin'] else 'No'}\n"
        f"Banned: {'Yes' if details['is_banned'] else 'No'}\n"
        f"Premium until: {details['premium_until'] or 'None'}\n"
        f"Downloads today: {details['downloads_today']}\n"
        f"Daily limit: {details['download_limit']}\n"
        f"Joined: {details['created_at']}\n\n"
        "Recent downloads:\n"
    )
    for d in downloads:
        text += f"- {d['platform']} | {d['status']} | {d['url'][:40]}\n"

    await update.message.reply_text(text)


async def exportusers_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_admin(user.id):
        await update.message.reply_text("❌ Admin only command.")
        return

    users = get_all_users_with_details()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["user_id", "username", "first_name", "is_admin", "is_banned", "premium_until", "downloads_today", "created_at"])
    for u in users:
        writer.writerow([
            u["user_id"],
            u.get("username", ""),
            u.get("first_name", ""),
            u["is_admin"],
            u["is_banned"],
            u.get("premium_until", ""),
            u["downloads_today"],
            u["created_at"],
        ])

    output.seek(0)
    await update.message.reply_document(
        document=output.getvalue().encode("utf-8"),
        filename="users_export.csv",
        caption=f"📊 Exported {len(users)} users",
    )


async def queuestatus_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_admin(user.id):
        await update.message.reply_text("❌ Admin only command.")
        return

    status = get_queue_status()
    await update.message.reply_text(
        f"📦 Queue Status:\n\n"
        f"Pending: {status['pending']}\n"
        f"Completed: {status['completed']}\n"
        f"Failed: {status['failed']}\n"
        f"Total: {status['total']}"
    )


async def clearqueue_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_admin(user.id):
        await update.message.reply_text("❌ Admin only command.")
        return

    deleted = clear_queue()
    await update.message.reply_text(f"🗑 Cleared {deleted} stuck queue items.")


async def checkapi_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_admin(user.id):
        await update.message.reply_text("❌ Admin only command.")
        return

    await update.message.reply_text(
        "🔑 RapidAPI Status:\n\n"
        f"Key: {RAPIDAPI_KEY[:8]}...\n"
        "Note: Full quota/balance check requires provider-specific endpoint."
    )


async def channels_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_admin(user.id):
        await update.message.reply_text("❌ Admin only command.")
        return

    channels = get_required_channels()
    if not channels:
        await update.message.reply_text("No required channels set.\nUsage: /channels add @username\n/channels remove <id>\n/channels list")
        return

    text = "📢 Required Channels:\n\n"
    for ch in channels:
        text += f"ID: {ch['id']} | @{ch['channel_username']}\n"

    await update.message.reply_text(text)


async def channels_add_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_admin(user.id):
        await update.message.reply_text("❌ Admin only command.")
        return

    if not context.args:
        await update.message.reply_text("Usage: /channelsadd @channelusername")
        return

    username = context.args[0].replace("@", "")
    add_required_channel(username)
    await update.message.reply_text(f"✅ Added @{username} to required channels.")


async def channels_remove_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_admin(user.id):
        await update.message.reply_text("❌ Admin only command.")
        return

    if not context.args:
        await update.message.reply_text("Usage: /channelsremove <id>")
        return

    try:
        channel_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Invalid ID.")
        return

    remove_required_channel(channel_id)
    await update.message.reply_text(f"✅ Removed channel {channel_id}.")


async def premium_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_admin(user.id):
        await update.message.reply_text("❌ Admin only command.")
        return

    if len(context.args) < 1:
        await update.message.reply_text("Usage: /premium <user_id> [days]")
        return

    try:
        target_id = int(context.args[0])
        days = int(context.args[1]) if len(context.args) > 1 else 30
    except ValueError:
        await update.message.reply_text("Invalid arguments.")
        return

    set_premium(target_id, days)
    await update.message.reply_text(f"✅ User {target_id} is now premium for {days} days.")


async def removepremium_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_admin(user.id):
        await update.message.reply_text("❌ Admin only command.")
        return

    if not context.args:
        await update.message.reply_text("Usage: /removepremium <user_id>")
        return

    target_id = int(context.args[0])
    remove_premium(target_id)
    await update.message.reply_text(f"✅ Removed premium from user {target_id}.")


async def premiumlist_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_admin(user.id):
        await update.message.reply_text("❌ Admin only command.")
        return

    premium_users = get_premium_users()
    if not premium_users:
        await update.message.reply_text("No premium users.")
        return

    text = f"💎 Premium Users ({len(premium_users)}):\n\n"
    for u in premium_users[:20]:
        text += f"{u['user_id']} - {u['username'] or u['first_name']} - until {u['premium_until']}\n"

    if len(text) > 4000:
        text = text[:4000] + "\n... (showing first 20)"
    await update.message.reply_text(text)


async def setlimit_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_admin(user.id):
        await update.message.reply_text("❌ Admin only command.")
        return

    if len(context.args) < 2:
        await update.message.reply_text("Usage: /setlimit <user_id> <limit>")
        return

    try:
        target_id = int(context.args[0])
        limit = int(context.args[1])
    except ValueError:
        await update.message.reply_text("Invalid arguments.")
        return

    set_user_download_limit(target_id, limit)
    await update.message.reply_text(f"✅ Set download limit for {target_id} to {limit}/day.")


async def backup_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_admin(user.id):
        await update.message.reply_text("❌ Admin only command.")
        return

    path = backup_database()
    await update.message.reply_text(f"✅ Backup created: {path}")


async def restart_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_admin(user.id):
        await update.message.reply_text("❌ Admin only command.")
        return

    await update.message.reply_text("🔄 Restarting bot...")
    import os
    import sys
    os.execv(sys.executable, [sys.executable] + sys.argv)


async def logs_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_admin(user.id):
        await update.message.reply_text("❌ Admin only command.")
        return

    log_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bot.log")
    if not os.path.exists(log_path):
        await update.message.reply_text("No log file found.")
        return

    with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
        lines = f.readlines()[-50:]

    text = "📜 Recent Logs:\n\n" + "".join(lines[-50:])
    if len(text) > 4000:
        text = text[-4000:]
    await update.message.reply_text(text)


async def topusers_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_admin(user.id):
        await update.message.reply_text("❌ Admin only command.")
        return

    top = get_top_users(limit=10)
    text = "🏆 Top Users:\n\n"
    for i, u in enumerate(top, 1):
        text += f"{i}. {u['first_name']} (@{u['username'] or 'N/A'}) - {u['total_downloads']} downloads\n"

    await update.message.reply_text(text)


async def commands_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ensure_user(user.id, username=user.username or "", first_name=user.first_name or "", last_name=user.last_name or "")

    text = (
        "📋 Available Commands:\n\n"
        "👤 User Commands:\n"
        "/start - Start the bot\n"
        "/help - Help information\n"
        "/commands - Show this commands list\n\n"
        "💡 Tip: Just send any Instagram post/reel URL to download it.\n"
        "   Or use inline mode: @botname <url>"
    )

    if is_admin(user.id):
        text += (
            "\n\n👑 Admin Commands:\n"
            "/stats - Bot statistics\n"
            "/users - List all users\n"
            "/banlist - View banned users\n"
            "/recentdownloads - Recent downloads\n"
            "/analytics - Bot analytics\n"
            "/userinfo <id> - User details\n"
            "/exportusers - Export users CSV\n"
            "/queuestatus - Queue status\n"
            "/clearqueue - Clear stuck queue\n"
            "/checkapi - Check API status\n"
            "/broadcast <msg> - Send message to all users\n"
            "/maintenance on/off - Toggle maintenance mode\n"
            "/cleardownloads - Delete downloads older than 30 days\n"
            "/channels - Manage required channels\n"
            "/channelsadd @username - Add required channel\n"
            "/channelsremove <id> - Remove required channel\n"
            "/premium <id> [days] - Give premium\n"
            "/removepremium <id> - Remove premium\n"
            "/premiumlist - List premium users\n"
            "/setlimit <id> <limit> - Set download limit\n"
            "/backup - Backup database\n"
            "/restart - Restart bot\n"
            "/logs - Recent logs\n"
            "/topusers - Top downloaders\n"
            "/ban <user_id> - Ban user\n"
            "/unban <user_id> - Unban user\n"
            "/makeadmin <user_id> - Make admin\n"
        )

    await update.message.reply_text(text)


async def post_init(application):
    bot = application.bot

    user_commands = [
        BotCommand("start", "Start the bot"),
        BotCommand("help", "View help information"),
        BotCommand("commands", "Show available commands"),
    ]

    admin_commands = user_commands + [
        BotCommand("stats", "View bot statistics"),
        BotCommand("users", "List all users"),
        BotCommand("banlist", "View banned users"),
        BotCommand("recentdownloads", "Recent downloads"),
        BotCommand("analytics", "Bot analytics"),
        BotCommand("userinfo", "User details"),
        BotCommand("exportusers", "Export users CSV"),
        BotCommand("queuestatus", "Queue status"),
        BotCommand("clearqueue", "Clear stuck queue"),
        BotCommand("checkapi", "Check API status"),
        BotCommand("broadcast", "Send broadcast to all users"),
        BotCommand("maintenance", "Toggle maintenance mode"),
        BotCommand("cleardownloads", "Delete old downloads"),
        BotCommand("channels", "Manage channels"),
        BotCommand("channelsadd", "Add required channel"),
        BotCommand("channelsremove", "Remove required channel"),
        BotCommand("premium", "Give premium"),
        BotCommand("removepremium", "Remove premium"),
        BotCommand("premiumlist", "List premium users"),
        BotCommand("setlimit", "Set download limit"),
        BotCommand("backup", "Backup database"),
        BotCommand("restart", "Restart bot"),
        BotCommand("logs", "Recent logs"),
        BotCommand("topusers", "Top downloaders"),
        BotCommand("ban", "Ban a user"),
        BotCommand("unban", "Unban a user"),
        BotCommand("makeadmin", "Make a user admin"),
    ]

    await bot.set_my_commands(
        user_commands,
        scope=BotCommandScopeDefault(),
    )

    await bot.set_my_commands(
        user_commands,
        scope=BotCommandScopeAllPrivateChats(),
    )

    await bot.set_my_commands(
        admin_commands,
        scope=BotCommandScopeAllChatAdministrators(),
    )

    await bot.set_chat_menu_button(
        menu_button=MenuButtonCommands()
    )


async def queue_worker():
    global queue_processing
    while True:
        queue_processing = True
        item = get_next_queue_item()
        if item:
            try:
                media_path, caption, media_type, error = await download_instagram(item["url"])
                mark_queue_item_processed(item["id"], "completed" if media_path else "failed")
            except Exception as e:
                logger.error(f"Queue worker error: {e}")
                mark_queue_item_processed(item["id"], "failed")
        else:
            queue_processing = False
            await asyncio.sleep(5)


def create_bot_app() -> ApplicationBuilder:
    init_db()
    set_admin(ADMIN_USER_ID, True)

    app = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .read_timeout(60)
        .connect_timeout(60)
        .write_timeout(60)
        .media_write_timeout(120)
        .post_init(post_init)
        .build()
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("commands", commands_cmd))
    app.add_handler(CommandHandler("stats", stats_cmd))
    app.add_handler(CommandHandler("users", users_cmd))
    app.add_handler(CommandHandler("banlist", banlist_cmd))
    app.add_handler(CommandHandler("recentdownloads", recentdownloads_cmd))
    app.add_handler(CommandHandler("analytics", analytics_cmd))
    app.add_handler(CommandHandler("userinfo", userinfo_cmd))
    app.add_handler(CommandHandler("exportusers", exportusers_cmd))
    app.add_handler(CommandHandler("queuestatus", queuestatus_cmd))
    app.add_handler(CommandHandler("clearqueue", clearqueue_cmd))
    app.add_handler(CommandHandler("checkapi", checkapi_cmd))
    app.add_handler(CommandHandler("broadcast", broadcast_cmd))
    app.add_handler(CommandHandler("maintenance", maintenance_cmd))
    app.add_handler(CommandHandler("cleardownloads", cleardownloads_cmd))
    app.add_handler(CommandHandler("channels", channels_cmd))
    app.add_handler(CommandHandler("channelsadd", channels_add_cmd))
    app.add_handler(CommandHandler("channelsremove", channels_remove_cmd))
    app.add_handler(CommandHandler("premium", premium_cmd))
    app.add_handler(CommandHandler("removepremium", removepremium_cmd))
    app.add_handler(CommandHandler("premiumlist", premiumlist_cmd))
    app.add_handler(CommandHandler("setlimit", setlimit_cmd))
    app.add_handler(CommandHandler("backup", backup_cmd))
    app.add_handler(CommandHandler("restart", restart_cmd))
    app.add_handler(CommandHandler("logs", logs_cmd))
    app.add_handler(CommandHandler("topusers", topusers_cmd))
    app.add_handler(CommandHandler("ban", ban_cmd))
    app.add_handler(CommandHandler("unban", unban_cmd))
    app.add_handler(CommandHandler("makeadmin", makeadmin_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(InlineQueryHandler(inline_query))
    app.add_handler(CallbackQueryHandler(check_join_callback, pattern="^check_join$"))

    return app


def main():
    app = create_bot_app()
    logger.info("Bot started successfully")
    print("Bot is running...")
    print(f"Admin user ID: {ADMIN_USER_ID}")
    app.run_polling()


if __name__ == "__main__":
    main()
