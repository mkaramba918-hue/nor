import os
import json
import asyncio
from datetime import datetime
import gspread
from google.oauth2.service_account import Credentials
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command

BOT_TOKEN = os.getenv("BOT_TOKEN")
SPREADSHEET_NAME = os.getenv("SPREADSHEET_NAME", "| CHILLI | ...")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
GOOGLE_CREDS_JSON = os.getenv("GOOGLE_CREDS_JSON")

scopes = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]

creds_dict = json.loads(GOOGLE_CREDS_JSON)
credentials = Credentials.from_service_account_info(creds_dict, scopes=scopes)
client = gspread.authorize(credentials)
sheet = client.open(SPREADSHEET_NAME).worksheet("Норма")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# Настройки отметок и цветов
STATUS_CONFIG = {
    "norma": {
        "title": "🟩 Норма (+5)",
        "val": "5",
        "bg": {"red": 0.0, "green": 1.0, "blue": 0.0},
        "fg": {"red": 0.0, "green": 0.0, "blue": 0.0}
    },
    "overnorma": {
        "title": "🟦 Перенорма (+8)",
        "val": "8",
        "bg": {"red": 0.0, "green": 0.0, "blue": 1.0},
        "fg": {"red": 1.0, "green": 1.0, "blue": 1.0}
    },
    "natyag": {
        "title": "🟪 Натяг (+1)",
        "val": "1",
        "bg": {"red": 0.6, "green": 0.0, "blue": 0.9},
        "fg": {"red": 1.0, "green": 1.0, "blue": 1.0}
    },
    "nonorma": {
        "title": "🟥 Нет нормы (-5)",
        "val": "-5",
        "bg": {"red": 1.0, "green": 0.0, "blue": 0.0},
        "fg": {"red": 1.0, "green": 1.0, "blue": 1.0}
    },
    "neaktiv": {
        "title": "⬜ Неактив (-2)",
        "val": "-2",
        "bg": {"red": 0.6, "green": 0.6, "blue": 0.6},
        "fg": {"red": 1.0, "green": 1.0, "blue": 1.0}
    },
    "new_ss": {
        "title": "🟨 Новый СС / Освоб.",
        "val": "Осв",
        "bg": {"red": 1.0, "green": 1.0, "blue": 0.0},
        "fg": {"red": 0.0, "green": 0.0, "blue": 0.0}
    },
    "dayoff": {
        "title": "🔷 Выходной / УВ (0)",
        "val": "0",
        "bg": {"red": 0.0, "green": 0.9, "blue": 1.0},
        "fg": {"red": 0.0, "green": 0.0, "blue": 0.0}
    },
    "question": {
        "title": "⬛ Под вопросом",
        "val": "?",
        "bg": {"red": 0.1, "green": 0.1, "blue": 0.1},
        "fg": {"red": 1.0, "green": 1.0, "blue": 1.0}
    }
}

def get_members_keyboard():
    """Считывает строго строки с 5 по 11 из колонок A (Должность) и B (Nick_Name)."""
    # Запрашиваем ровно строки СС: A5:B11
    rows = sheet.get("A5:B11")
    buttons = []
    
    for idx, row in enumerate(rows, start=5):
        role = row[0] if len(row) > 0 and row[0] else f"Строка {idx}"
        nick = row[1] if len(row) > 1 and row[1] else "Не указан"
        
        # Если ник реальный — пишем его, если "None" или пусто — пишем Должность
        if nick and nick.lower() not in ["none", "nick", ""]:
            label = f"{role}: {nick}"
        else:
            label = f"{role} (строка {idx})"
            
        buttons.append([InlineKeyboardButton(text=label, callback_data=f"sel_{idx}")])
        
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_status_keyboard(row_idx: int):
    keyboard = []
    row = []
    for key, cfg in STATUS_CONFIG.items():
        row.append(InlineKeyboardButton(text=cfg["title"], callback_data=f"set_{row_idx}_{key}"))
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
        
    keyboard.append([InlineKeyboardButton(text="⬅️ Назад к выбору", callback_data="back_to_menu")])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

@dp.message(Command("start"))
@dp.message(Command("norma"))
async def cmd_start(message: Message):
    if ADMIN_ID != 0 and message.from_user.id != ADMIN_ID:
        await message.answer("Доступ запрещен.")
        return
        
    await message.answer(
        "📋 **Выставление нормы Старшего Состава**\nВыберите сотрудника:",
        reply_markup=get_members_keyboard(),
        parse_mode="Markdown"
    )

@dp.callback_query(F.data == "back_to_menu")
async def back_to_menu(callback: CallbackQuery):
    await callback.message.edit_text(
        "📋 **Выставление нормы Старшего Состава**\nВыберите сотрудника:",
        reply_markup=get_members_keyboard(),
        parse_mode="Markdown"
    )
    await callback.answer()

@dp.callback_query(F.data.startswith("sel_"))
async def choose_member(callback: CallbackQuery):
    _, row_idx = callback.data.split("_")
    row_idx = int(row_idx)
    
    # Получаем должность и ник этой строки
    row_data = sheet.row_values(row_idx)
    role = row_data[0] if len(row_data) > 0 else f"Строка {row_idx}"
    nick = row_data[1] if len(row_data) > 1 else ""
    
    display_name = f"{role} ({nick})" if nick and nick.lower() != "none" else role
    
    await callback.message.edit_text(
        f"Выбран: **{display_name}**\nВыберите отметку за сегодня:",
        reply_markup=get_status_keyboard(row_idx),
        parse_mode="Markdown"
    )
    await callback.answer()

@dp.callback_query(F.data.startswith("set_"))
async def save_norma(callback: CallbackQuery):
    _, row_idx, status_key = callback.data.split("_")
    row_idx = int(row_idx)
    cfg = STATUS_CONFIG.get(status_key)

    if not cfg:
        await callback.answer("Ошибка статуса!", show_alert=True)
        return

    # Шапка находится на строке 4
    today_short = datetime.now().strftime("%d.%m")      # '14.09'
    today_full = datetime.now().strftime("%d.%m.%y")    # '14.09.26'

    try:
        # Строка 4 — это даты
        header_row = sheet.row_values(4)
        col_idx = None
        
        # Ищем колонку с сегодняшней датой
        for idx, col_val in enumerate(header_row):
            col_str = str(col_val).strip()
            if today_short in col_str or today_full in col_str:
                col_idx = idx + 1
                break

        if not col_idx:
            await callback.answer(f"Дата {today_short} не найдена в строке 4!", show_alert=True)
            return

        # 1. Записываем баллы/статус
        sheet.update_cell(row_idx, col_idx, cfg["val"])
        
        # 2. Красим ячейку
        cell_name = gspread.utils.rowcol_to_a1(row_idx, col_idx)
        sheet.format(cell_name, {
            "backgroundColor": cfg["bg"],
            "horizontalAlignment": "CENTER",
            "textFormat": {
                "foregroundColor": cfg["fg"],
                "bold": True
            }
        })

        await callback.message.edit_text(
            f"✅ Успешно!\nВыставлено: **{cfg['title']}** в ячейку `{cell_name}`.",
            reply_markup=get_members_keyboard(),
            parse_mode="Markdown"
        )
        await callback.answer("Сохранено в таблицу!")

    except Exception as e:
        await callback.answer(f"Ошибка: {e}", show_alert=True)

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
    
