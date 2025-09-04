import os
import json
import time
import asyncio
from functools import wraps
import re

from pyrogram import Client, filters
from pyrogram.enums import ChatType, ChatMemberStatus
from pyrogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    CallbackQuery,
)
from yt_dlp import YoutubeDL


# --- CONFIGURATION --- #
# اطلاعات ربات و ادمین
API_ID = 23043756
API_HASH = "ee37c0d54eb9b46167f3bde1d3b9e605"
BOT_TOKEN = "7119232868:AAHZsp2fBdeZ50sUHgHdETidP7BAgRxmo7c"
OWNER_ID = 5245202056

# --- Archive Channel and Database --- #
# IMPORTANT: Create a private channel, add the bot as an admin, and put its ID here.
# The ID should start with -100.
LOG_CHANNEL_ID = -1003039271282
LINK_DB_FILE = "link_database.json" # فایل پایگاه داده برای کش لینک‌ها

# مسیر فایل‌ها و پوشه‌ها
DOWNLOAD_DIR = "downloads/"
AUTH_FILE = "authorized_chats.json"
SETTINGS_FILE = "chat_settings.json"
# --- Authentication via Cookies ---
# پایدارترین روش: فقط از فایل کوکی استفاده کنید. راهنما در انتهای پاسخ آمده است.
COOKIES_FILE = "cookies.txt" 

# --- INITIALIZATION --- #
# ساخت پوشه دانلود در صورت عدم وجود
if not os.path.exists(DOWNLOAD_DIR):
    os.makedirs(DOWNLOAD_DIR)

# ساخت کلاینت پایروگرام
app = Client("youtube_downloader_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# متغیرهای گلوبال برای مدیریت وضعیت دانلود
active_downloads = {}
last_update_time = {}

# --- HELPER FUNCTIONS --- #

def load_json(file_path):
    """خواندن دیتا از یک فایل JSON."""
    if not os.path.exists(file_path):
        return {}
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, FileNotFoundError):
        return {}


def save_json(data, file_path):
    """ذخیره دیتا در یک فایل JSON."""
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)

def is_owner(user_id):
    """بررسی می‌کند که آیا کاربر ادمین ربات است."""
    return user_id == OWNER_ID

def get_authorized_chats():
    """لیست چت‌های مجاز را برمی‌گرداند."""
    data = load_json(AUTH_FILE)
    return [int(chat_id) for chat_id in data.get("authorized", [])]

def add_authorized_chat(chat_id):
    """یک چت را به لیست مجاز اضافه می‌کند."""
    data = load_json(AUTH_FILE)
    if "authorized" not in data:
        data["authorized"] = []
    if chat_id not in data["authorized"]:
        data["authorized"].append(chat_id)
        save_json(data, AUTH_FILE)
        return True
    return False

def remove_authorized_chat(chat_id):
    """یک چت را از لیست مجاز حذف می‌کند."""
    data = load_json(AUTH_FILE)
    if "authorized" in data and chat_id in data["authorized"]:
        data["authorized"].remove(chat_id)
        save_json(data, AUTH_FILE)
        return True
    return False

def get_chat_settings(chat_id):
    """تنظیمات کیفیت را برای یک چت خاص برمی‌گرداند."""
    settings = load_json(SETTINGS_FILE)
    return settings.get(str(chat_id), "720") # کیفیت پیش‌فرض 720p است

def set_chat_settings(chat_id, quality):
    """کیفیت را برای یک چت خاص تنظیم می‌کند."""
    settings = load_json(SETTINGS_FILE)
    settings[str(chat_id)] = quality
    save_json(settings, SETTINGS_FILE)

async def progress_hook(d, message: Message, chat_id: int):
    """هوک برای نمایش و به‌روزرسانی نوار پیشرفت دانلود."""
    if chat_id in active_downloads and active_downloads[chat_id]['cancelled']:
        raise Exception("Download cancelled.")

    if d['status'] == 'downloading':
        total_bytes = d.get('total_bytes') or d.get('total_bytes_estimate')
        if total_bytes:
            downloaded_bytes = d['downloaded_bytes']
            percentage = (downloaded_bytes / total_bytes) * 100
            
            current_time = time.time()
            if chat_id not in last_update_time or (current_time - last_update_time.get(chat_id, 0)) > 1.5:
                last_update_time[chat_id] = current_time
                
                progress_bar = "".join(["█" if i < percentage / 5 else "░" for i in range(20)])
                status_text = (
                    f"📥 **در حال دانلود...**\n"
                    f"`{progress_bar}`\n"
                    f"**پیشرفت:** {percentage:.1f}%\n"
                    f"**حجم کل:** {d.get('total_bytes_str', 'N/A')}"
                )
                try:
                    await message.edit_text(
                        status_text,
                        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("لغو ❌", f"cancel_{chat_id}")]])
                    )
                except Exception:
                    pass

async def upload_progress(current, total, message: Message, chat_id: int):
    """تابع نمایش نوار پیشرفت آپلود."""
    if chat_id in active_downloads and active_downloads[chat_id]['cancelled']:
        raise Exception("Upload cancelled.")
    
    current_time = time.time()
    if chat_id not in last_update_time or (current_time - last_update_time.get(chat_id, 0)) > 1.5:
        last_update_time[chat_id] = current_time
        
        percentage = (current / total) * 100
        progress_bar = "".join(["█" if i < percentage / 5 else "░" for i in range(20)])
        status_text = (
            f"📤 **در حال آپلود...**\n"
            f"`{progress_bar}`\n"
            f"**پیشرفت:** {percentage:.1f}%\n"
        )
        try:
            await message.edit_text(
                status_text,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("لغو ❌", f"cancel_{chat_id}")]])
            )
        except Exception:
            pass
    
# --- AUTHORIZATION DECORATOR --- #

def authorized_only(func):
    """دکوریتور برای محدود کردن دسترسی به کاربران و گروه‌های مجاز."""
    @wraps(func)
    async def wrapped(client, message, *args, **kwargs):
        chat_id = message.chat.id
        authorized_chats = get_authorized_chats()
        
        if chat_id in authorized_chats or is_owner(message.from_user.id):
            return await func(client, message, *args, **kwargs)
        else:
            if message.chat.type in [ChatType.GROUP, ChatType.SUPERGROUP]:
                return
            await message.reply_text("شما مجاز به استفاده از این ربات نیستید.")
    return wrapped

# --- COMMAND HANDLERS --- #

@app.on_message(filters.command("start") & filters.private)
async def start_command(client, message: Message):
    await message.reply_text(
        "سلام! من یک ربات دانلودر یوتیوب هستم.\n"
        "فقط کافیه لینک ویدیو، پلی‌لیست یا کانال رو برام بفرستی.\n"
        "از دستور /setting برای تنظیم کیفیت پیش‌فرض استفاده کن."
    )

@app.on_message(filters.command("authorize"))
async def authorize_command(client, message: Message):
    if not is_owner(message.from_user.id):
        return await message.reply_text("شما ادمین ربات نیستی!")

    parts = message.text.split()
    chat_to_authorize = 0

    if len(parts) > 1 and parts[1].lstrip('-').isdigit():
        chat_to_authorize = int(parts[1])
    elif message.chat.type in [ChatType.GROUP, ChatType.SUPERGROUP]:
        chat_to_authorize = message.chat.id
    else:
        return await message.reply_text("استفاده صحیح: `/authorize <chat_id>` یا از این دستور در گروه مورد نظر استفاده کن.")

    if add_authorized_chat(chat_to_authorize):
        await message.reply_text(f"✅ چت با شناسه `{chat_to_authorize}` مجاز شد.")
    else:
        await message.reply_text(f"⚠️ چت با شناسه `{chat_to_authorize}` از قبل مجاز بود.")


@app.on_message(filters.command("unauthorize"))
async def unauthorize_command(client, message: Message):
    if not is_owner(message.from_user.id):
        return await message.reply_text("شما ادمین ربات نیستی!")

    parts = message.text.split()
    if len(parts) < 2 or not parts[1].lstrip('-').isdigit():
        return await message.reply_text("استفاده صحیح: `/unauthorize <chat_id>`")

    chat_to_unauthorize = int(parts[1])
    if remove_authorized_chat(chat_to_unauthorize):
        await message.reply_text(f"❌ چت با شناسه `{chat_to_unauthorize}` غیرمجاز شد.")
    else:
        await message.reply_text(f"⚠️ چت با شناسه `{chat_to_unauthorize}` در لیست مجاز یافت نشد.")

@app.on_message(filters.command("setting"))
@authorized_only
async def setting_command(client, message: Message):
    chat_id = message.chat.id
    
    if message.chat.type in [ChatType.GROUP, ChatType.SUPERGROUP]:
        member = await client.get_chat_member(chat_id, message.from_user.id)
        if member.status not in [ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER]:
            return await message.reply_text("فقط ادمین‌های گروه می‌توانند تنظیمات را تغییر دهند.")
            
    current_quality = get_chat_settings(chat_id)
    
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(f"{'✅' if current_quality == '2160' else ''} 4K (2160p)", "set_quality_2160")],
        [InlineKeyboardButton(f"{'✅' if current_quality == '1080' else ''} 1080p", "set_quality_1080")],
        [InlineKeyboardButton(f"{'✅' if current_quality == '720' else ''} 720p", "set_quality_720")],
        [InlineKeyboardButton(f"{'✅' if current_quality == '480' else ''} 480p", "set_quality_480")],
        [InlineKeyboardButton(f"{'✅' if current_quality == '360' else ''} 360p", "set_quality_360")],
    ])
    await message.reply_text("لطفا کیفیت دانلود پیش‌فرض را انتخاب کنید:", reply_markup=keyboard)


# --- MESSAGE HANDLER FOR LINKS --- #

@app.on_message(filters.text & ~filters.regex(r"^/") & (filters.private | filters.group))
@authorized_only
async def handle_link(client, message: Message):
    if not message.text: 
        return

    url_match = re.search(r"http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\\(\\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+", message.text)
    if not url_match:
        return

    url = url_match.group(0)
    chat_id = message.chat.id

    # --- Immediate and Silent Logging of the request ---
    try:
        user = message.from_user
        log_text = (
            f"**درخواست لینک جدید از طرف کاربر**\n\n"
            f"🔗 **لینک:** `{url}`\n"
            f"👤 **نام:** {user.first_name} {user.last_name or ''}\n"
            f"📧 **یوزرنیم:** @{user.username if user.username else 'N/A'}\n"
            f"🆔 **آیدی:** `{user.id}`\n"
            f"💬 **چت:** `{chat_id}`"
        )
        await client.send_message(LOG_CHANNEL_ID, log_text, disable_web_page_preview=True)
    except Exception as initial_log_e:
        print(f"!!! [NON-FATAL] Could not send initial text log to archive channel: {initial_log_e}")

    status_msg = await message.reply_text("🔎 در حال پردازش لینک، لطفا صبر کنید...")

    # --- Caching Logic ---
    link_db = load_json(LINK_DB_FILE)
    if url in link_db:
        message_id_in_log_channel = link_db[url]
        try:
            await status_msg.edit_text("✅ لینک در آرشیو یافت شد. در حال ارسال...")
            await client.copy_message(
                chat_id=chat_id,
                from_chat_id=LOG_CHANNEL_ID,
                message_id=message_id_in_log_channel
            )
            await status_msg.delete()
            return
        except Exception as e:
            await status_msg.edit_text(f"⚠️ ارسال از آرشیو ناموفق بود. دانلود مجدد... \nخطا: {e}")
    
    if chat_id in active_downloads and not active_downloads[chat_id]['done']:
        return await status_msg.edit_text("یک دانلود دیگر در این چت در حال انجام است. لطفا صبر کنید.")

    try:
        ydl_opts_info = {
            'quiet': True,
            'extract_flat': True,
            'geo_bypass': True,
            'age_limit': 99,
        }
        if os.path.exists(COOKIES_FILE):
            ydl_opts_info['cookiefile'] = COOKIES_FILE

        with YoutubeDL(ydl_opts_info) as ydl:
            info = ydl.extract_info(url, download=False)

        if 'entries' in info:
            playlist_count = len(info['entries'])
            keyboard = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("✅ بله", f"download_playlist_yes"),
                    InlineKeyboardButton("نه ❌", "download_playlist_no")
                ]
            ])
            await status_msg.edit_text(
                f"این لینک شامل {playlist_count} ویدیو است. آیا می‌خواهید همه را دانلود کنید؟",
                reply_markup=keyboard
            )
            active_downloads[chat_id] = {'playlist_info': info, 'original_message': message, 'done': False}
        else:
            await download_and_upload_video(client, message, url, status_msg)

    except Exception as e:
        error_message = str(e)
        if "Sign in to confirm" in error_message or "Private video" in error_message or "age-restricted" in error_message:
            await status_msg.edit_text(
                "**خطای احراز هویت!**\n\n"
                "این ویدیو نیازمند لاگین است (خصوصی، محدودیت سنی و ...).\n"
                "برای حل مشکل، لطفاً یک فایل `cookies.txt` جدید و معتبر از مرورگر خود استخراج کرده و در کنار ربات قرار دهید."
            )
        else:
            await status_msg.edit_text(f"خطایی رخ داد: {error_message}")


async def download_and_upload_video(client, message, url, status_msg=None):
    """تابع برای دانلود، آپلود، و ذخیره ویدیو در آرشیو."""
    chat_id = message.chat.id
    
    if not status_msg:
        status_msg = await message.reply_text("در حال آماده‌سازی برای دانلود...")
    else:
        await status_msg.edit_text("در حال آماده‌سازی برای دانلود...")

    active_downloads[chat_id] = {'cancelled': False, 'done': False}
    file_path, thumbnail_path = None, None
    
    try:
        quality = get_chat_settings(chat_id)
        format_str = f"bestvideo[ext=mp4][height<={quality}]+bestaudio[ext=m4a]/best[ext=mp4][height<={quality}]/best"
        
        ydl_opts = {
            'format': format_str,
            'outtmpl': os.path.join(DOWNLOAD_DIR, '%(id)s.%(ext)s'),
            'progress_hooks': [lambda d: asyncio.ensure_future(progress_hook(d, status_msg, chat_id))],
            'noplaylist': True,
            'merge_output_format': 'mp4',
            'writethumbnail': True,
            'geo_bypass': True,
            'age_limit': 99,
        }
        if os.path.exists(COOKIES_FILE):
            ydl_opts['cookiefile'] = COOKIES_FILE

        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            file_path = ydl.prepare_filename(info)
        
        if active_downloads.get(chat_id, {}).get('cancelled'):
            raise Exception("دانلود توسط کاربر لغو شد.")

        caption = info.get('title', 'Video')
        duration = int(info.get('duration') or 0)
        width = int(info.get('width') or 0)
        height = int(info.get('height') or 0)
        
        base_filename = os.path.splitext(file_path)[0]
        for ext in ['webp', 'jpg', 'png', 'jpeg']:
             potential_thumb = f"{base_filename}.{ext}"
             if os.path.exists(potential_thumb):
                 thumbnail_path = potential_thumb
                 break

        await status_msg.edit_text("فایل دانلود شد. در حال آپلود و آرشیو...")
        
        log_message = await client.send_video(
            chat_id=LOG_CHANNEL_ID,
            video=file_path,
            caption=caption,
            supports_streaming=True,
            duration=duration,
            thumb=thumbnail_path,
            width=width,
            height=height,
            progress=upload_progress,
            progress_args=(status_msg, chat_id)
        )

        link_db = load_json(LINK_DB_FILE)
        link_db[url] = log_message.id
        save_json(link_db, LINK_DB_FILE)
        
        user = message.from_user
        log_text = (
            f"✅ **ویدیو با موفقیت دانلود و آرشیو شد برای کاربر:**\n"
            f"👤 **نام:** {user.first_name} {user.last_name or ''}\n"
            f"🆔 **آیدی:** `{user.id}`"
        )
        await client.send_message(LOG_CHANNEL_ID, log_text, reply_to_message_id=log_message.id)

        await status_msg.edit_text("✅ آرشیو شد. در حال ارسال...")

        await client.copy_message(
            chat_id=chat_id,
            from_chat_id=LOG_CHANNEL_ID,
            message_id=log_message.id
        )
        
        await status_msg.delete()

    except Exception as e:
        error_message = str(e)
        if "Sign in to confirm" in error_message or "Private video" in error_message or "age-restricted" in error_message:
            await status_msg.edit_text(
                "**خطای احراز هویت!**\n\n"
                "این ویدیو نیازمند لاگین است (خصوصی، محدودیت سنی و ...).\n"
                "برای حل مشکل، لطفاً یک فایل `cookies.txt` جدید و معتبر از مرورگر خود استخراج کرده و در کنار ربات قرار دهید."
            )
        else:
            try:
                await status_msg.edit_text(f"عملیات ناموفق بود: {error_message}")
            except Exception:
                pass
        print(f"!!! [FATAL ERROR] Operation failed: {e}")
        
    finally:
        active_downloads[chat_id]['done'] = True
        if chat_id in last_update_time:
            del last_update_time[chat_id]
        if file_path and os.path.exists(file_path):
            os.remove(file_path)
        if thumbnail_path and os.path.exists(thumbnail_path):
            os.remove(thumbnail_path)


# --- CALLBACK QUERY HANDLERS --- #

@app.on_callback_query(filters.regex(r"^set_quality_"))
async def set_quality_callback(client, callback_query: CallbackQuery):
    chat_id = callback_query.message.chat.id
    user_id = callback_query.from_user.id
    
    if callback_query.message.chat.type in [ChatType.GROUP, ChatType.SUPERGROUP]:
        member = await client.get_chat_member(chat_id, user_id)
        if member.status not in [ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER]:
            return await callback_query.answer("فقط ادمین‌ها می‌توانند این تنظیمات را تغییر دهند.", show_alert=True)
            
    quality = callback_query.data.split("_")[-1]
    set_chat_settings(chat_id, quality)
    await callback_query.answer(f"کیفیت پیش‌فرض روی {quality}p تنظیم شد.", show_alert=True)
    await callback_query.message.delete()


@app.on_callback_query(filters.regex(r"^cancel_"))
async def cancel_download_callback(client, callback_query: CallbackQuery):
    chat_id = int(callback_query.data.split("_")[-1])
    if chat_id in active_downloads:
        active_downloads[chat_id]['cancelled'] = True
        await callback_query.answer("درخواست لغو ارسال شد...", show_alert=True)
    else:
        await callback_query.answer("هیچ دانلود فعالی برای لغو وجود ندارد.", show_alert=True)


@app.on_callback_query(filters.regex(r"^download_playlist_"))
async def playlist_callback(client, callback_query: CallbackQuery):
    action = callback_query.data.split("_")[-1]
    chat_id = callback_query.message.chat.id
    
    await callback_query.message.delete()
    
    if action == "no":
        if chat_id in active_downloads:
            del active_downloads[chat_id]
        return

    if action == "yes":
        if chat_id not in active_downloads or 'playlist_info' not in active_downloads[chat_id]:
            return await client.send_message(chat_id, "اطلاعات پلی‌لیست یافت نشد. لطفا دوباره تلاش کنید.")

        playlist_info = active_downloads[chat_id]['playlist_info']
        original_user_message = active_downloads[chat_id]['original_message']

        await original_user_message.reply_text(f"شروع پردازش {len(playlist_info['entries'])} ویدیو...")
        
        link_db = load_json(LINK_DB_FILE)
        for i, entry in enumerate(playlist_info['entries']):
            video_url = entry['url']
            if active_downloads.get(chat_id, {}).get('cancelled'):
                await original_user_message.reply_text("عملیات پلی‌لیست توسط کاربر لغو شد.")
                break
            
            if video_url in link_db:
                status_msg = await original_user_message.reply_text(f"✅ ویدیو {i+1} در آرشیو یافت شد...")
                await client.copy_message(chat_id, LOG_CHANNEL_ID, link_db[video_url])
                await status_msg.delete()
                await asyncio.sleep(1) # to avoid flood waits
                continue

            status_message_playlist = await original_user_message.reply_text(f"⏳ دانلود ویدیو {i+1}: `{entry.get('title', 'N/A')}`")
            try:
                await download_and_upload_video(client, original_user_message, video_url, status_message_playlist)
            except Exception as e:
                await original_user_message.reply_text(f"❌ دانلود ویدیو `{entry.get('title', 'N/A')}` ناموفق بود: {e}")
                continue

        await original_user_message.reply_text("✅ تمام ویدیوهای پلی‌لیست پردازش شدند.")
        
    if chat_id in active_downloads:
        del active_downloads[chat_id]

# --- MAIN EXECUTION --- #
if __name__ == "__main__":
    print("ربات در حال اجرا است...")
    add_authorized_chat(OWNER_ID)
    app.run()

