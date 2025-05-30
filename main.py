import telebot
import os
import logging
import time
import asyncio
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from threading import Thread

# Setup logging
logging.basicConfig(level=logging.INFO)

# Bot token from environment variable
BOT_TOKEN = os.getenv('BOT_TOKEN', 'YOUR_BOT_TOKEN')
bot = telebot.TeleBot(BOT_TOKEN)

# Secure root directory
ROOT_DIR = "/storage/emulated/0/DCIM"

# Supported media extensions
IMAGE_EXTENSIONS = ['jpg', 'jpeg', 'png', 'gif', 'bmp', 'webp']
VIDEO_EXTENSIONS = ['mp4', 'mov', 'mkv', 'avi', 'wmv', 'flv', 'webm']

# Helper: sanitize folder names
def safe_join(base, *paths):
    final_path = os.path.abspath(os.path.join(base, *paths))
    if not final_path.startswith(os.path.abspath(base)):
        raise ValueError("Invalid folder path.")
    return final_path

# Helper: check if file is media
def is_media_file(filename):
    ext = filename.lower().split('.')[-1]
    return ext in IMAGE_EXTENSIONS or ext in VIDEO_EXTENSIONS

# Helper: get file type
def get_file_type(filename):
    ext = filename.lower().split('.')[-1]
    if ext in IMAGE_EXTENSIONS:
        return 'image'
    elif ext in VIDEO_EXTENSIONS:
        return 'video'
    return 'other'

# Start or show folders
@bot.message_handler(commands=['start', 'folders'])
def send_folders(message):
    try:
        folders = [f for f in os.listdir(ROOT_DIR) if os.path.isdir(os.path.join(ROOT_DIR, f))]
        if not folders:
            bot.reply_to(message, "❌ No folders found.")
            return
        markup = InlineKeyboardMarkup()
        for folder in folders:
            markup.add(InlineKeyboardButton(f"📁 {folder}", callback_data=f"list::{folder}"))
        bot.send_message(message.chat.id, "📁 *Available folders:*", reply_markup=markup, parse_mode="Markdown")
    except Exception as e:
        bot.reply_to(message, f"Error: {e}")

# Callback to list files in folder with media preview options
@bot.callback_query_handler(func=lambda call: call.data.startswith("list::"))
def handle_list_callback(call):
    folder = call.data.split("::")[1]
    try:
        folder_path = safe_join(ROOT_DIR, folder)
        files = os.listdir(folder_path)
        if not files:
            bot.send_message(call.message.chat.id, "📂 No files in this folder.")
            return
        
        # Separate media and other files
        media_files = [f for f in files if is_media_file(f)]
        other_files = [f for f in files if not is_media_file(f)]
        
        reply = f"📷 *Files in {folder}:*\n\n"
        
        if media_files:
            reply += f"🎬 *Media files ({len(media_files)}):*\n"
            for f in media_files[:10]:  # Show first 10
                file_type = "📸" if get_file_type(f) == 'image' else "🎥"
                reply += f"{file_type} {f}\n"
            if len(media_files) > 10:
                reply += f"... and {len(media_files) - 10} more\n"
        
        if other_files:
            reply += f"\n📄 *Other files ({len(other_files)}):*\n"
            for f in other_files[:5]:  # Show first 5
                reply += f"📄 {f}\n"
            if len(other_files) > 5:
                reply += f"... and {len(other_files) - 5} more\n"
        
        # Add action buttons
        markup = InlineKeyboardMarkup()
        if media_files:
            markup.add(InlineKeyboardButton("🎬 Show All Media (Fast)", callback_data=f"showmedia::{folder}"))
            markup.add(InlineKeyboardButton("📸 Images Only", callback_data=f"images::{folder}"))
            markup.add(InlineKeyboardButton("🎥 Videos Only", callback_data=f"videos::{folder}"))
        markup.add(InlineKeyboardButton("📋 List All Files", callback_data=f"listall::{folder}"))
        markup.add(InlineKeyboardButton("🔙 Back to Folders", callback_data="back_to_folders"))
        
        bot.edit_message_text(reply, call.message.chat.id, call.message.message_id, 
                            reply_markup=markup, parse_mode="Markdown")
    except Exception as e:
        bot.send_message(call.message.chat.id, f"Error: {e}")
# Show all media files quickly
@bot.callback_query_handler(func=lambda call: call.data.startswith("showmedia::"))
def show_all_media(call):
    folder = call.data.split("::")[1]
    try:
        folder_path = safe_join(ROOT_DIR, folder)
        files = [f for f in os.listdir(folder_path) if is_media_file(f)]
        
        if not files:
            bot.answer_callback_query(call.id, "No media files found!")
            return
        
        bot.answer_callback_query(call.id, f"Sending {len(files)} media files...")
        bot.send_message(call.message.chat.id, f"🎬 *Sending {len(files)} media files from {folder}...*", parse_mode="Markdown")
        
        # Send files in a separate thread to avoid blocking
        thread = Thread(target=send_media_files, args=(call.message.chat.id, folder_path, files))
        thread.start()
        
    except Exception as e:
        bot.send_message(call.message.chat.id, f"Error: {e}")

# Show only images
@bot.callback_query_handler(func=lambda call: call.data.startswith("images::"))
def show_images(call):
    folder = call.data.split("::")[1]
    try:
        folder_path = safe_join(ROOT_DIR, folder)
        files = [f for f in os.listdir(folder_path) if get_file_type(f) == 'image']
        
        if not files:
            bot.answer_callback_query(call.id, "No images found!")
            return
        
        bot.answer_callback_query(call.id, f"Sending {len(files)} images...")
        bot.send_message(call.message.chat.id, f"📸 *Sending {len(files)} images from {folder}...*", parse_mode="Markdown")
        
        thread = Thread(target=send_media_files, args=(call.message.chat.id, folder_path, files))
        thread.start()
        
    except Exception as e:
        bot.send_message(call.message.chat.id, f"Error: {e}")

# Show only videos
@bot.callback_query_handler(func=lambda call: call.data.startswith("videos::"))
def show_videos(call):
    folder = call.data.split("::")[1]
    try:
        folder_path = safe_join(ROOT_DIR, folder)
        files = [f for f in os.listdir(folder_path) if get_file_type(f) == 'video']
        
        if not files:
            bot.answer_callback_query(call.id, "No videos found!")
            return
        
        bot.answer_callback_query(call.id, f"Sending {len(files)} videos...")
        bot.send_message(call.message.chat.id, f"🎥 *Sending {len(files)} videos from {folder}...*", parse_mode="Markdown")
        
        thread = Thread(target=send_media_files, args=(call.message.chat.id, folder_path, files))
        thread.start()
        
    except Exception as e:
        bot.send_message(call.message.chat.id, f"Error: {e}")

# List all files callback
@bot.callback_query_handler(func=lambda call: call.data.startswith("listall::"))
def list_all_files(call):
    folder = call.data.split("::")[1]
    try:
        folder_path = safe_join(ROOT_DIR, folder)
        files = os.listdir(folder_path)
        if not files:
            bot.send_message(call.message.chat.id, "📂 No files in this folder.")
            return
        
        reply = f"📋 *All files in {folder}:*\n\n"
        for i, f in enumerate(files, 1):
            file_type = get_file_type(f)
            if file_type == 'image':
                icon = "📸"
            elif file_type == 'video':
                icon = "🎥"
            else:
                icon = "📄"
            reply += f"{i}. {icon} {f}\n"
            
            # Split long messages
            if len(reply) > 3500:
                bot.send_message(call.message.chat.id, reply, parse_mode="Markdown")
                reply = ""
        
        if reply:
            bot.send_message(call.message.chat.id, reply, parse_mode="Markdown")
            
    except Exception as e:
        bot.send_message(call.message.chat.id, f"Error: {e}")
# Back to folders callback
@bot.callback_query_handler(func=lambda call: call.data == "back_to_folders")
def back_to_folders(call):
    try:
        folders = [f for f in os.listdir(ROOT_DIR) if os.path.isdir(os.path.join(ROOT_DIR, f))]
        if not folders:
            bot.edit_message_text("❌ No folders found.", call.message.chat.id, call.message.message_id)
            return
        markup = InlineKeyboardMarkup()
        for folder in folders:
            markup.add(InlineKeyboardButton(f"📁 {folder}", callback_data=f"list::{folder}"))
        bot.edit_message_text("📁 *Available folders:*", call.message.chat.id, call.message.message_id,
                            reply_markup=markup, parse_mode="Markdown")
    except Exception as e:
        bot.send_message(call.message.chat.id, f"Error: {e}")

# Function to send media files quickly
def send_media_files(chat_id, folder_path, files):
    try:
        sent_count = 0
        error_count = 0
        
        for filename in files:
            try:
                file_path = os.path.join(folder_path, filename)
                if not os.path.isfile(file_path):
                    continue
                
                file_type = get_file_type(filename)
                
                with open(file_path, 'rb') as f:
                    if file_type == 'image':
                        bot.send_photo(chat_id, f, caption=f"📸 {filename}")
                    elif file_type == 'video':
                        bot.send_video(chat_id, f, caption=f"🎥 {filename}")
                
                sent_count += 1
                
                # Small delay to avoid hitting rate limits (adjust as needed)
                time.sleep(0.5)  # 0.5 seconds between files for "fast" sending
                
            except Exception as e:
                error_count += 1
                logging.error(f"Error sending {filename}: {e}")
                continue
        
        # Send completion message
        completion_msg = f"✅ *Completed!*\n📤 Sent: {sent_count} files"
        if error_count > 0:
            completion_msg += f"\n❌ Errors: {error_count} files"
        
        bot.send_message(chat_id, completion_msg, parse_mode="Markdown")
        
    except Exception as e:
        bot.send_message(chat_id, f"❌ Error during bulk send: {e}")

# Manual command to list
@bot.message_handler(commands=['list'])
def list_files(message):
    try:
        folder = message.text.split(maxsplit=1)[1]
        folder_path = safe_join(ROOT_DIR, folder)
        files = os.listdir(folder_path)
        if not files:
            bot.reply_to(message, "📂 No files in this folder.")
            return
        
        # Separate media and other files
        media_files = [f for f in files if is_media_file(f)]
        other_files = [f for f in files if not is_media_file(f)]
        
        reply = f"📷 *Files in {folder}:*\n\n"
        
        if media_files:
            reply += f"🎬 *Media files ({len(media_files)}):*\n"
            for f in media_files:
                file_type = "📸" if get_file_type(f) == 'image' else "🎥"
                reply += f"{file_type} {f}\n"
        
        if other_files:
            reply += f"\n📄 *Other files ({len(other_files)}):*\n"
            for f in other_files:
                reply += f"📄 {f}\n"
        
        bot.reply_to(message, reply, parse_mode="Markdown")
    except IndexError:
        bot.reply_to(message, "Usage: /list FOLDER_NAME")
    except Exception as e:
        bot.reply_to(message, f"Error: {e}")

# Show all media in folder command
@bot.message_handler(commands=['showmedia'])
def show_media_command(message):
    try:
        folder = message.text.split(maxsplit=1)[1]
        folder_path = safe_join(ROOT_DIR, folder)
        files = [f for f in os.listdir(folder_path) if is_media_file(f)]
if not files:
            bot.reply_to(message, "📂 No media files in this folder.")
            return
        
        bot.reply_to(message, f"🎬 *Sending {len(files)} media files from {folder}...*", parse_mode="Markdown")
        
        # Send files in a separate thread
        thread = Thread(target=send_media_files, args=(message.chat.id, folder_path, files))
        thread.start()
        
    except IndexError:
        bot.reply_to(message, "Usage: /showmedia FOLDER_NAME")
    except Exception as e:
        bot.reply_to(message, f"Error: {e}")

# Get specific file
@bot.message_handler(commands=['get'])
def get_file(message):
    try:
        _, folder, filename = message.text.split(maxsplit=2)
        file_path = safe_join(ROOT_DIR, folder, filename)
        if not os.path.isfile(file_path):
            bot.reply_to(message, "❌ File not found.")
            return
        
        file_type = get_file_type(filename)
        with open(file_path, 'rb') as f:
            if file_type == 'image':
                bot.send_photo(message.chat.id, f, caption=f"📸 {filename}")
            elif file_type == 'video':
                bot.send_video(message.chat.id, f, caption=f"🎥 {filename}")
            else:
                bot.send_document(message.chat.id, f, caption=f"📄 {filename}")
    except Exception as e:
        bot.reply_to(message, f"Error: {e}")

# Delete file
@bot.message_handler(commands=['delete'])
def delete_file(message):
    try:
        _, folder, filename = message.text.split(maxsplit=2)
        file_path = safe_join(ROOT_DIR, folder, filename)
        if os.path.isfile(file_path):
            os.remove(file_path)
            bot.reply_to(message, f"🗑️ Deleted {filename}", parse_mode="Markdown")
        else:
            bot.reply_to(message, "❌ File not found.")
    except Exception as e:
        bot.reply_to(message, f"Error: {e}")

# Help command
@bot.message_handler(commands=['help'])
def help_message(message):
    help_text = (
        "📌 *Bot Commands:*\n\n"
        "🗂️ *Navigation:*\n"
        "/folders - Show all folders\n"
        "/list FOLDER - List files in folder\n\n"
        "🎬 *Media Commands:*\n"
        "/showmedia FOLDER - Send all media files fast\n"
        "/get FOLDER FILE - Send specific file\n\n"
        "🗑️ *Management:*\n"
        "/delete FOLDER FILE - Delete file\n\n"
        "💡 *Tips:*\n"
        "• Use folder buttons for easy navigation\n"
        "• Media files are sent with 0.5s delay\n"
        "• Supports images: jpg, png, gif, etc.\n"
        "• Supports videos: mp4, mov, mkv, etc."
    )
    bot.reply_to(message, help_text, parse_mode="Markdown")

# Start polling
logging.info("Enhanced media bot running...")
bot.infinity_polling()