# 🎥 Instagram Reel Downloader Bot

[![Python Version](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

An asynchronous Telegram bot built with **Pyrogram** and **yt-dlp** that downloads Instagram Reels and sends them as video files. This bot is specifically designed for ephemeral environments like **Render** by processing media entirely in-memory.

---

## 🚀 Features

*   **⚡ In-Memory Streaming:** Downloads and uploads videos using `io.BytesIO`, ensuring no files are stored on the host's disk.
*   **📡 Uptime-Ready:** Integrated **Flask** web server to maintain activity on hosting platforms (prevents idling).
*   **🍪 Login Bypass:** Full support for `cookies.txt` to navigate Instagram's rate limits and authentication walls.
*   **🛠️ Robust Downloader:** Powered by `yt-dlp` for high-quality video extraction and metadata handling.
*   **💬 Group & Private Support:** Optimized for use in direct messages or shared groups.

---

## ⚙️ Configuration

### Environment Variables
Create a `.env` file in the root directory or set these in your cloud provider's dashboard:

| Variable | Description |
| :--- | :--- |
| **API_ID** | Your Telegram API ID from [my.telegram.org](https://my.telegram.org) |
| **API_HASH** | Your Telegram API Hash from [my.telegram.org](https://my.telegram.org) |
| **BOT_TOKEN** | Your Bot Token from [@BotFather](https://t.me/botfather) |
| **PORT** | The port for the Flask uptime server (Default: 10000) |

---

## 📦 Installation

1.  **Clone the Repository**
    ```bash
    git clone [https://github.com/nikraj1/firsttgbot.git](https://github.com/nikraj1/firsttgbot.git)
    cd firsttgbot
    ```

2.  **Install Dependencies**
    ```bash
    pip install -r requirements.txt
    ```

3.  **Prepare Cookies**
    *   Export your Instagram cookies using a browser extension (Netscape format).
    *   Save as `cookies.txt` in the root folder.
    *   **Security Note:** Never commit `cookies.txt` to GitHub. It is already added to `.gitignore`.

4.  **Run the Bot**
    ```bash
    python your_script_name.py
    ```

---

## 🛠️ Tech Stack

*   **Framework:** [Pyrogram](https://docs.pyrogram.org/)
*   **Engine:** [yt-dlp](https://github.com/yt-dlp/yt-dlp)
*   **Web Server:** [Flask](https://flask.palletsprojects.com/)
*   **HTTP Client:** [Requests](https://requests.readthedocs.io/)

---

## 🚀 Deployment on Render

1.  Create a new **Web Service** on Render.
2.  Connect your GitHub repository.
3.  Set **Build Command:** `pip install -r requirements.txt`
4.  Set **Start Command:** `python your_script_name.py`
5.  Add your environment variables in the **Environment** tab.
6.  *Optional:* Use a service like **UptimeRobot** to ping your Render URL every 10 minutes to keep the bot active.

---

## 🤝 Contributing

Contributions make the open-source community an amazing place to learn, inspire, and create.

1. Fork the Project
2. Create your Feature Branch (`git checkout -b feature/NewFeature`)
3. Commit your Changes (`git commit -m 'Add NewFeature'`)
4. Push to the Branch (`git push origin feature/NewFeature`)
5. Open a Pull Request

---

## 📝 License

Distributed under the MIT License. See `LICENSE` for more information.