# This is a Python Telegram bot that downloads public Instagram Reels and sends them to the user.
# It is designed to be hosted on platforms like Render, which have ephemeral filesystems.

import os
import io
import threading
from flask import Flask
from dotenv import load_dotenv
from pyrogram import Client, filters
from yt_dlp import YoutubeDL
import asyncio
import requests

# Load environment variables from .env file
load_dotenv()

# --- Flask App for Uptime ---
# This part is to keep the bot alive on hosting platforms like Render.
app = Flask(__name__)

@app.route('/')
def home():
    """A simple route to check if the bot's web service is alive."""
    return "Bot is alive."

def run_flask():
    """Starts the Flask web server in a separate thread."""
    app.run(host="0.0.0.0", port=int(os.environ.get('PORT', 10000)))

# Run Flask in a separate thread to not block the bot
flask_thread = threading.Thread(target=run_flask)
flask_thread.start()

# --- Pyrogram Bot ---

# Get credentials from environment variables
API_ID = os.getenv('API_ID')
API_HASH = os.getenv('API_HASH')
BOT_TOKEN = os.getenv('BOT_TOKEN')

# Initialize the Pyrogram client
bot = Client(
    "reel_downloader_bot",
    api_id=int(API_ID),
    api_hash=API_HASH,
    bot_token=BOT_TOKEN
)

def get_cookie_file_path():
    """Dynamically determines the path to the cookies file."""
    # In a production environment like Render, you might need to use a persistent disk or a cloud storage service
    # for the cookies file. For this example, we assume the file is included in the deployment.
    return 'cookies.txt'

@bot.on_message(filters.text & (filters.private | filters.group) & filters.regex(r'instagram\.com/reels/.+'))
async def download_reel(client, message):
    """
    Handles incoming messages. If the message is a valid Instagram Reel URL,
    it downloads and uploads the video to Telegram without storing it on disk.
    """
    url = message.text.strip()

    status_message = await message.reply_text("⏳ Processing link...")

    # Define youtube-dl options for in-memory download
    ydl_opts = {
        'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
        'noplaylist': True,
        'cookiefile': get_cookie_file_path(), # Pass the cookies file to yt-dlp
    }

    try:
        await status_message.edit_text("📥 Downloading Reel...")

        # Run the yt-dlp download in a separate thread to not block the event loop
        loop = asyncio.get_event_loop()
        
        # The executor function to perform the blocking download operation
        def download_and_stream():
            with YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                
                # Get the direct download URL for the best mp4 format
                best_url = None
                # Prioritize a format that has a proper video stream.
                formats = info.get('formats', [])
                for f in formats:
                    if f.get('ext') == 'mp4' and f.get('url') and f.get('vcodec') != 'none':
                        best_url = f['url']
                        break
                
                if not best_url:
                    # Fallback to any mp4 if no video-specific format is found
                    for f in info.get('formats', []):
                        if f.get('ext') == 'mp4' and f.get('url'):
                            best_url = f['url']
                            break
                
                if not best_url:
                    raise Exception("No MP4 format found for the video.")

                # Stream the video data into the BytesIO object
                video_stream = io.BytesIO()
                response = requests.get(best_url, stream=True)
                for chunk in response.iter_content(chunk_size=1024):
                    if chunk:
                        video_stream.write(chunk)
                
                video_stream.seek(0)
                # Assign a name to the BytesIO object
                video_stream.name = "instagram_reel.mp4"

                # Return the video stream and caption
                caption = info.get('title', info.get('description', ''))
                return video_stream, caption

        # Await the execution of the blocking function in a thread pool
        video_stream, caption = await loop.run_in_executor(None, download_and_stream)
        
        await status_message.edit_text("⬆️ Uploading to Telegram...")
        
        await message.reply_video(
            video=video_stream,
            caption=caption
        )
        
        await status_message.delete()

    except Exception as e:
        error_text = f"❌ **Error:** Failed to download the reel.\n\n`{str(e)}`"
        await status_message.edit_text(error_text)
    
    finally:
        # Close the in-memory stream
        try:
            video_stream.close()
        except NameError:
            # Handle case where video_stream was not created
            pass

# Start the bot
print("Bot is starting...")
bot.run()
