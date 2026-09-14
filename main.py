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
    """Считывает ники из столбца B со строки 6."""
    nicknames = sheet.col_values(2)
    buttons = []
    
    # Считываем список со строки 6
    rows_data = nicknames[5:] if len(nicknames) >= 6 else []
    
    for idx, nick in enumerate(rows_data, start=6):
        nick = str(nick).strip()
        # Если ник уже указан в таблице
        if nick and nick.lower() != "nick":
            buttons.append([InlineKeyboardButton(text=nick, callback_data=f"sel_{idx}")])
        else:
            buttons.append([InlineKeyboardButton(text=f"Сотрудник (строка {idx})", callback_data=f"sel_{idx}")])
            
    # Если столбец совсем пустой, генерируем базовые строки 6-12
    if not buttons:
        for r in range(6, 12):
            buttons.append([InlineKeyboardButton(text=f"Сотрудник (строка {r})", callback_data=f"sel_{r}")])
            
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
        
    keyboard.append([InlineKeyboardButton(text="⬅️ Назад к списку", callback_data="back_to_menu")])
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
    
    # Получаем ник из таблицы или пишем номер строки
    nick = sheet.cell(row_idx, 2).value or f"Строка {row_idx}"
    
    await callback.message.edit_text(
        f"Сотрудник: **{nick}** (строка {row_idx})\nВыберите статус нормы:",
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
        await callback.answer("Неверный статус!", show_alert=True)
        return

    today_full = datetime.now().strftime("%d.%m.%y")
    today_short = datetime.now().strftime("%d.%m")

    try:
        header_row = sheet.row_values(5)
        col_idx = None
        
        for idx, col_val in enumerate(header_row):
            col_str = str(col_val).strip()
            if today_full in col_str or today_short in col_str:
                col_idx = idx + 1
                break

        if not col_idx:
            await callback.answer(f"Дата {today_short} не найдена в строке 5 таблицы!", show_alert=True)
            return

        # Записываем значение и красим ячейку
        sheet.update_cell(row_idx, col_idx, cfg["val"])
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
            f"✅ Выставлено: **{cfg['title']}**\nЯчейка `{cell_name}` успешно закрашена!",
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
    
