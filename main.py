import os
import json
import asyncio
import logging
from datetime import datetime
import gspread
from google.oauth2.service_account import Credentials
from aiohttp import web
from aiogram import Bot, Dispatcher
from aiogram.types import Message
from aiogram.filters import Command

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

BOT_TOKEN = os.getenv("BOT_TOKEN")
LOGS_SPREADSHEET_NAME = os.getenv("LOGS_SPREADSHEET_NAME", "Логи Форума | А-ОПГ")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
LOGS_CHAT_ID = int(os.getenv("LOGS_CHAT_ID", str(ADMIN_ID)))
SECRET_API_KEY = os.getenv("SECRET_API_KEY", "forum_super_secret_key")
PORT = int(os.getenv("PORT", 8080))
GOOGLE_CREDS_JSON = os.getenv("GOOGLE_CREDS_JSON")

# Подключение Google Sheets
scopes = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]

creds_dict = json.loads(GOOGLE_CREDS_JSON)
credentials = Credentials.from_service_account_info(creds_dict, scopes=scopes)
client = gspread.authorize(credentials)

try:
    logs_doc = client.open(LOGS_SPREADSHEET_NAME)
except Exception:
    logs_doc = client.open_by_key(LOGS_SPREADSHEET_NAME)

sheet_logs = logs_doc.sheet1

# Проверка и форматирование шапки
if not sheet_logs.row_values(1):
    sheet_logs.append_row(["Дата и Время", "Администратор / СС", "Действие / Вердикт", "Игрок / Ник", "Причина", "Ссылка на тему"])
    sheet_logs.format("A1:F1", {
        "backgroundColor": {"red": 0.15, "green": 0.2, "blue": 0.3},
        "horizontalAlignment": "CENTER",
        "textFormat": {"foregroundColor": {"red": 1.0, "green": 1.0, "blue": 1.0}, "bold": True}
    })

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# ================= КОМАНДЫ БОТА В TELEGRAM =================

@dp.message(Command("start"))
async def cmd_start(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    await message.answer(
        "🛡 **Сервер логирования скрипта форума активен**\n\n"
        "• Данные принимаются по API со скрипта Tampermonkey.\n"
        "• Все вердикты записываются в Google Таблицу и транслируются сюда.\n\n"
        "Команды:\n"
        "/last — показать 5 последних действий с форума\n"
        "/status — статус соединения с таблицей",
        parse_mode="Markdown"
    )

@dp.message(Command("last"))
async def cmd_last(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    rows = sheet_logs.get_all_values()
    if len(rows) <= 1:
        await message.answer("В таблице логов пока нет записей.")
        return
    recent = rows[-5:]
    msg = ["📜 **Последние действия на форуме:**\n"]
    for r in recent:
        ts = r[0] if len(r) > 0 else ""
        adm = r[1] if len(r) > 1 else ""
        act = r[2] if len(r) > 2 else ""
        target = r[3] if len(r) > 3 else ""
        link = r[5] if len(r) > 5 else ""
        msg.append(f"⏱ `{ts}`\n👤 **{adm}** ➔ {act} ({target})\n🔗 [Открыть тему]({link})\n")
    await message.answer("\n".join(msg), parse_mode="Markdown", disable_web_page_preview=True)

# ================= HTTP ЭНДПОИНТ ДЛЯ СКРИПТА ФОРУМА =================

async def handle_forum_log(request):
    try:
        data = await request.json()
        
        # Защита эндпоинта секретным ключом
        if data.get("secret_key") != SECRET_API_KEY:
            return web.json_response({"error": "Unauthorized"}, status=403)
            
        now_str = datetime.now().strftime("%d.%m.%Y %H:%M:%S")
        admin_nick = data.get("admin_nick", "Неизвестно")
        action = data.get("action", "Действие")
        target_player = data.get("target_player", "—")
        reason = data.get("reason", "—")
        thread_url = data.get("thread_url", "—")
        
        # 1. Запись в Google Таблицу
        sheet_logs.append_row([now_str, admin_nick, action, target_player, reason, thread_url])
        
        # 2. Отправка уведомления в Telegram
        tg_text = (
            f"📋 **Новое действие на форуме!**\n\n"
            f"👤 **Кто:** `{admin_nick}`\n"
            f"⚡ **Вердикт:** {action}\n"
            f"🎯 **Нарушитель/Игрок:** `{target_player}`\n"
            f"📝 **Причина/Наказание:** `{reason}`\n"
            f"🔗 **Тема:** [Ссылка на форум]({thread_url})"
        )
        if LOGS_CHAT_ID:
            await bot.send_message(
                chat_id=LOGS_CHAT_ID, 
                text=tg_text, 
                parse_mode="Markdown",
                disable_web_page_preview=True
            )
            
        return web.json_response({"status": "ok"})
    except Exception as e:
        logging.error(f"Ошибка при обработке лога с форума: {e}")
        return web.json_response({"error": str(e)}, status=500)

async def start_server():
    app = web.Application()
    app.router.add_post("/api/forum-log", handle_forum_log)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    logging.info(f"Веб-сервер логирования запущен на порту {PORT}")

async def main():
    await start_server()
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
    
