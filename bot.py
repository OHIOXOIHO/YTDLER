import os
import json
import time
import asyncio
from functools import wraps
import re
import subprocess

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

def get_video_metadata(file_path):
    """
    Uses ffprobe to get accurate video metadata if yt-dlp fails to provide it.
    Requires ffmpeg to be installed on the system.
    """
    try:
        command = [
            "ffprobe",
            "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=width,height,duration",
            "-of", "csv=p=0:s=x"
        ]
        process = subprocess.run(command + [file_path], capture_output=True, text=True, check=True)
        # Expected output: "1280x720x15.345"
        output = process.stdout.strip().split('x')
        if len(output) == 3:
            width = int(output[0])
            height = int(output[1])
            duration = int(float(output[2]))
            return {"width": width, "height": height, "duration": duration}
    except (subprocess.CalledProcessError, FileNotFoundError, IndexError, ValueError) as e:
        print(f"Could not get metadata using ffprobe for {file_path}: {e}")
    return None

def generate_thumbnail(video_path):
    """
    Generates a thumbnail from the video file using ffmpeg.
    """
    thumbnail_path = f"{os.path.splitext(video_path)[0]}.jpg"
    try:
        command = [
            "ffmpeg",
            "-i", video_path,
            "-ss", "00:00:01.00", # Capture frame at 1 second
            "-vframes", "1",
            "-y", # Overwrite output file if it exists
            thumbnail_path
        ]
        subprocess.run(command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if os.path.exists(thumbnail_path):
            return thumbnail_path
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        print(f"Could not generate thumbnail for {video_path}: {e}")
    return None

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

async def progress_hook(d, message: Message, chat_id: int, playlist_progress=""):
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
                playlist_header = f"{playlist_progress}\n" if playlist_progress else ""
                status_text = (
                    f"{playlist_header}"
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

async def upload_progress(current, total, message: Message, chat_id: int, playlist_progress=""):
    """تابع نمایش نوار پیشرفت آپلود."""
    if chat_id in active_downloads and active_downloads[chat_id]['cancelled']:
        raise Exception("Upload cancelled.")
    
    current_time = time.time()
    if chat_id not in last_update_time or (current_time - last_update_time.get(chat_id, 0)) > 1.5:
        last_update_time[chat_id] = current_time
        
        percentage = (current / total) * 100
        progress_bar = "".join(["█" if i < percentage / 5 else "░" for i in range(20)])
        playlist_header = f"{playlist_progress}\n" if playlist_progress else ""
        status_text = (
            f"{playlist_header}"
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
        print(f"!!! [NON-FATAL] Could not send initial text log: {initial_log_e}")

    status_msg = await message.reply_text("🔎 در حال پردازش لینک، لطفا صبر کنید...")

    link_db = load_json(LINK_DB_FILE)
    if url in link_db:
        message_id_in_log_channel = link_db[url]
        try:
            await client.copy_message(
                chat_id=chat_id,
                from_chat_id=LOG_CHANNEL_ID,
                message_id=message_id_in_log_channel
            )
            await status_msg.delete()
            return # --- Mission Accomplished ---
        except Exception as e:
            if "empty message" in str(e).lower() or "message_id_invalid" in str(e).lower():
                print(f"Archived message for {url} deleted. Re-downloading.")
                del link_db[url]
                save_json(link_db, LINK_DB_FILE)
                # --- Let the code fall through to the download section ---
            else:
                await status_msg.edit_text(f"⚠️ ارسال از آرشیو ناموفق بود.\nخطا: {e}")
                return
    
    if chat_id in active_downloads and not active_downloads[chat_id].get('done', True):
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


async def download_and_upload_video(client, message, url, status_msg=None, playlist_progress=""):
    """تابع برای دانلود، آپلود، و ذخیره ویدیو در آرشیو."""
    chat_id = message.chat.id
    
    if not status_msg:
        status_msg = await message.reply_text("در حال آماده‌سازی برای دانلود...")
    else:
        await status_msg.edit_text(f"{playlist_progress}\nدر حال آماده‌سازی برای دانلود..." if playlist_progress else "در حال آماده‌سازی برای دانلود...")

    active_downloads[chat_id] = {'cancelled': False, 'done': False}
    file_path = None
    
    try:
        quality = get_chat_settings(chat_id)
        format_str = f"bestvideo[ext=mp4][height<={quality}]+bestaudio[ext=m4a]/best[ext=mp4][height<={quality}]/best"
        
        ydl_opts = {
            'format': format_str,
            'outtmpl': os.path.join(DOWNLOAD_DIR, '%(id)s.%(ext)s'),
            'progress_hooks': [lambda d: asyncio.ensure_future(progress_hook(d, status_msg, chat_id, playlist_progress=playlist_progress))],
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
        thumbnail_path = None

        if not duration or not width:
            print("Metadata from yt-dlp is incomplete. Falling back to ffprobe...")
            metadata = get_video_metadata(file_path)
            if metadata:
                print(f"ffprobe metadata found: {metadata}")
                duration = metadata['duration']
                width = metadata['width']
                height = metadata['height']

        base_filename = os.path.splitext(file_path)[0]
        for ext in ['webp', 'jpg', 'png', 'jpeg']:
             potential_thumb = f"{base_filename}.{ext}"
             if os.path.exists(potential_thumb):
                 thumbnail_path = potential_thumb
                 break
        
        if not thumbnail_path:
            print("No thumbnail found. Falling back to ffmpeg to generate one...")
            thumbnail_path = generate_thumbnail(file_path)
            if thumbnail_path:
                print(f"ffmpeg thumbnail generated at: {thumbnail_path}")

        upload_caption = f"{playlist_progress}\nفایل دانلود شد. در حال آپلود و آرشیو..." if playlist_progress else "فایل دانلود شد. در حال آپلود و آرشیو..."
        await status_msg.edit_text(upload_caption)
        
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
            progress_args=(status_msg, chat_id, playlist_progress)
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
        
        if not playlist_progress:
            await client.copy_message(
                chat_id=chat_id,
                from_chat_id=LOG_CHANNEL_ID,
                message_id=log_message.id
            )
            await status_msg.delete()
        else:
            await client.copy_message(
                chat_id=chat_id,
                from_chat_id=LOG_CHANNEL_ID,
                message_id=log_message.id
            )

    except Exception as e:
        error_message = str(e)
        final_error_message = f"عملیات ناموفق بود: {error_message}"
        if "Sign in to confirm" in error_message or "Private video" in error_message or "age-restricted" in error_message:
            final_error_message = (
                "**خطای احراز هویت!**\n\n"
                "این ویدیو نیازمند لاگین است.\n"
                "برای حل مشکل، لطفاً یک فایل `cookies.txt` جدید و معتبر ارائه دهید."
            )
        
        if not playlist_progress:
            try:
                await status_msg.edit_text(final_error_message)
            except Exception:
                pass
        
        raise e
        
    finally:
        active_downloads[chat_id]['done'] = True
        if not playlist_progress:
             if chat_id in last_update_time:
                del last_update_time[chat_id]
        if file_path:
            base_path = os.path.splitext(file_path)[0]
            for ext in ['.mp4', '.mkv', '.part', '.webp', '.jpg', '.png', '.jpeg']:
                file_to_remove = f"{base_path}{ext}"
                if os.path.exists(file_to_remove):
                    try:
                        os.remove(file_to_remove)
                    except OSError: pass


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
    chat_id = callback_query.message.chat.id
    
    if chat_id not in active_downloads or 'original_message' not in active_downloads[chat_id]:
         await callback_query.answer("اطلاعات اصلی درخواست یافت نشد. لطفا دوباره تلاش کنید.", show_alert=True)
         try:
            await callback_query.message.delete()
         except Exception: pass
         return
         
    original_user_message = active_downloads[chat_id]['original_message']
    
    await callback_query.message.delete()
    
    action = callback_query.data.split("_")[-1]

    if action == "no":
        if chat_id in active_downloads:
            del active_downloads[chat_id]
        return

    if action == "yes":
        playlist_info = active_downloads[chat_id]['playlist_info']
        playlist_count = len(playlist_info['entries'])
        status_msg = await original_user_message.reply_text(f"شروع پردازش {playlist_count} ویدیو...")
        
        link_db = load_json(LINK_DB_FILE)
        success_count = 0
        fail_count = 0
        for i, entry in enumerate(playlist_info['entries']):
            video_url = entry.get('url')
            if not video_url: 
                fail_count += 1
                continue

            if active_downloads.get(chat_id, {}).get('cancelled'):
                await status_msg.edit_text(f"عملیات پلی‌لیست توسط کاربر لغو شد.\n{success_count} ویدیو با موفقیت ارسال شد.")
                await asyncio.sleep(5)
                break
            
            progress_str = f"**پلی‌لیست: {i+1}/{playlist_count}**"
            
            cache_hit = False
            if video_url in link_db:
                try:
                    await status_msg.edit_text(f"{progress_str}\nدر حال ارسال ویدیو...")
                    await client.copy_message(chat_id, LOG_CHANNEL_ID, link_db[video_url])
                    success_count += 1
                    cache_hit = True
                except Exception as e:
                    if "empty message" in str(e).lower() or "message_id_invalid" in str(e).lower():
                        print(f"Archived playlist item for {video_url} was deleted. Re-downloading.")
                        del link_db[video_url]
                        save_json(link_db, LINK_DB_FILE)
                    else:
                        fail_count += 1
                        title = entry.get('title', 'N/A')
                        await status_msg.edit_text(f"{progress_str}\n❌ ارسال ویدیو `{title}` از آرشیو ناموفق بود.")
                        print(f"Failed to copy playlist item '{title}' from archive: {e}")
                        await asyncio.sleep(3)
                        continue
            
            if cache_hit:
                await asyncio.sleep(1)
                continue
            
            try:
                await download_and_upload_video(client, original_user_message, video_url, status_msg, playlist_progress=progress_str)
                success_count += 1
            except Exception as e:
                fail_count += 1
                title = entry.get('title', 'N/A')
                await status_msg.edit_text(f"{progress_str}\n❌ دانلود ویدیو `{title}` ناموفق بود.")
                print(f"Failed to process playlist item '{title}': {e}")
                await asyncio.sleep(3)
                continue

        await status_msg.edit_text(f"✅ پردازش پلی‌لیست تمام شد.\nموفق: {success_count} | ناموفق: {fail_count}")
        await asyncio.sleep(5)
        await status_msg.delete()
        
    if chat_id in active_downloads:
        del active_downloads[chat_id]

# --- MAIN EXECUTION --- #
if __name__ == "__main__":
    print("ربات در حال اجرا است...")
    add_authorized_chat(OWNER_ID)
    app.run()

