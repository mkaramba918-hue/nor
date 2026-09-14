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

USERS_FILE = "authorized_users.json"

def load_auth_data():
    if os.path.exists(USERS_FILE):
        try:
            with open(USERS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"users": {}, "active_sessions": {}}

def save_auth_data(data):
    with open(USERS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def get_user_role(user_id: int):
    if ADMIN_ID != 0 and user_id == ADMIN_ID:
        return "Следящий (Владелец)"
    data = load_auth_data()
    login = data.get("active_sessions", {}).get(str(user_id))
    if login and login in data.get("users", {}):
        return data["users"][login].get("role", "Лидер")
    return None

def is_authorized(user_id: int) -> bool:
    return get_user_role(user_id) is not None

STATUS_CONFIG = {
    "norma": {
        "title": "🟩 Норма (+5)",
        "points": 5,
        "is_numeric": True,
        "bg": {"red": 0.0, "green": 1.0, "blue": 0.0},
        "fg": {"red": 0.0, "green": 0.0, "blue": 0.0}
    },
    "overnorma": {
        "title": "🟦 Перенорма (+8)",
        "points": 8,
        "is_numeric": True,
        "bg": {"red": 0.0, "green": 0.0, "blue": 1.0},
        "fg": {"red": 1.0, "green": 1.0, "blue": 1.0}
    },
    "natyag": {
        "title": "🟪 Натяг (+1)",
        "points": 1,
        "is_numeric": True,
        "bg": {"red": 0.6, "green": 0.0, "blue": 0.9},
        "fg": {"red": 1.0, "green": 1.0, "blue": 1.0}
    },
    "nonorma": {
        "title": "🟥 Нет нормы (-5)",
        "points": -5,
        "is_numeric": True,
        "bg": {"red": 1.0, "green": 0.0, "blue": 0.0},
        "fg": {"red": 1.0, "green": 1.0, "blue": 1.0}
    },
    "neaktiv": {
        "title": "⬜ Неактив (-2)",
        "points": -2,
        "is_numeric": True,
        "bg": {"red": 0.6, "green": 0.6, "blue": 0.6},
        "fg": {"red": 1.0, "green": 1.0, "blue": 1.0}
    },
    "new_ss": {
        "title": "🟨 Новый СС / Освоб.",
        "points": "Осв",
        "is_numeric": False,
        "bg": {"red": 1.0, "green": 1.0, "blue": 0.0},
        "fg": {"red": 0.0, "green": 0.0, "blue": 0.0}
    },
    "dayoff": {
        "title": "🔷 Выходной / УВ (0)",
        "points": 0,
        "is_numeric": True,
        "bg": {"red": 0.0, "green": 0.9, "blue": 1.0},
        "fg": {"red": 0.0, "green": 0.0, "blue": 0.0}
    },
    "question": {
        "title": "⬛ Под вопросом",
        "points": "?",
        "is_numeric": False,
        "bg": {"red": 0.1, "green": 0.1, "blue": 0.1},
        "fg": {"red": 1.0, "green": 1.0, "blue": 1.0}
    }
}

def get_members_keyboard():
    rows = sheet.get("G5:H11")
    buttons = []
    
    for idx, row in enumerate(rows, start=5):
        role = row[0].strip() if len(row) > 0 and row[0] else ("Положенец [9]" if idx <= 7 else "Смотрящий [8]")
        nick = row[1].strip() if len(row) > 1 and row[1] else ""
        
        if nick and nick.lower() not in ["none", "nick", "-", ""]:
            label = f"👤 {nick} | {role}"
        else:
            label = f"▫️ {role} (Свободно)"
            
        buttons.append([InlineKeyboardButton(text=label, callback_data=f"sel_{idx}")])
        
    buttons.append([InlineKeyboardButton(text="📊 Посмотреть сводку нормы", callback_data="show_summary")])
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
        
    keyboard.append([InlineKeyboardButton(text="⬅️ Назад к списку СС", callback_data="back_to_menu")])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

# ================= АВТОРИЗАЦИЯ И УПРАВЛЕНИЕ =================

@dp.message(Command("login"))
async def cmd_login(message: Message):
    parts = message.text.split()
    if len(parts) != 3:
        await message.answer("⚠️ Формат: `/login логин пароль`", parse_mode="Markdown")
        return
        
    login, password = parts[1], parts[2]
    data = load_auth_data()
    
    user_info = data.get("users", {}).get(login)
    if user_info and user_info.get("password") == password:
        # Привязываем Telegram ID к этому логину
        if "active_sessions" not in data:
            data["active_sessions"] = {}
        data["active_sessions"][str(message.from_user.id)] = login
        save_auth_data(data)
        
        role = user_info.get("role", "Лидер")
        await message.answer(f"✅ Вход выполнен!\nВаша должность: **{role}**.\nМеню управления: /start", parse_mode="Markdown")
    else:
        await message.answer("❌ Неверный логин или пароль!")

@dp.message(Command("logout"))
async def cmd_logout(message: Message):
    data = load_auth_data()
    uid = str(message.from_user.id)
    if uid in data.get("active_sessions", {}):
        del data["active_sessions"][uid]
        save_auth_data(data)
        await message.answer("Вы вышли из учетной записи.")
    else:
        await message.answer("Вы не были авторизованы.")

@dp.message(Command("adduser"))
async def cmd_adduser(message: Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("❌ Только Следящий (владелец бота) может создавать пользователей.")
        return
        
    parts = message.text.split(maxsplit=3)
    if len(parts) < 3:
        await message.answer("⚠️ Формат: `/adduser логин пароль роль`\nПример: `/adduser leader 12345 Лидер ОПГ`", parse_mode="Markdown")
        return
        
    login = parts[1]
    password = parts[2]
    role = parts[3] if len(parts) > 3 else "Лидер"
    
    data = load_auth_data()
    if "users" not in data:
        data["users"] = {}
    data["users"][login] = {"password": password, "role": role}
    save_auth_data(data)
    
    await message.answer(
        f"✅ Доступ создан!\nЛогин: `{login}`\nПароль: `{password}`\nРоль: **{role}**\nПередайте эти данные для входа через `/login`.",
        parse_mode="Markdown"
    )

@dp.message(Command("users"))
async def cmd_users(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    data = load_auth_data()
    users = data.get("users", {})
    if not users:
        await message.answer("Пока нет созданных пользователей.")
        return
    
    active_sessions = data.get("active_sessions", {})
    lines = ["👥 **Список доступов:**\n"]
    for login, info in users.items():
        # Проверяем, зашел ли кто-то под этим логином
        active_uids = [uid for uid, l in active_sessions.items() if l == login]
        status_str = f"🟢 В сети (ID: {', '.join(active_uids)})" if active_uids else "⚪ Не в сети"
        lines.append(f"• Логин: `{login}` | Пароль: `{info.get('password')}`\n  Роль: **{info.get('role')}** | {status_str}")
    
    await message.answer("\n".join(lines), parse_mode="Markdown")

@dp.message(Command("deluser"))
async def cmd_deluser(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    parts = message.text.split()
    if len(parts) != 2:
        await message.answer("⚠️ Формат: `/deluser логин`", parse_mode="Markdown")
        return
        
    login = parts[1]
    data = load_auth_data()
    
    if login in data.get("users", {}):
        # 1. Удаляем пользователя
        del data["users"][login]
        
        # 2. МГНОВЕННО выбрасываем всех, кто зашел под этим логином!
        active_sessions = data.get("active_sessions", {})
        to_delete = [uid for uid, l in active_sessions.items() if l == login]
        for uid in to_delete:
            del active_sessions[uid]
            
        save_auth_data(data)
        await message.answer(f"✅ Доступ для `{login}` полностью отозван и сессия завершена!", parse_mode="Markdown")
    else:
        await message.answer("Пользователь с таким логином не найден.")

# ================= АНОНСЫ =================

@dp.message(Command("announce"))
async def cmd_announce(message: Message):
    role = get_user_role(message.from_user.id)
    if not role:
        await message.answer("У вас нет доступа к рассылке.")
        return

    text = message.text.replace("/announce", "").strip()
    if not text:
        await message.answer("⚠️ Формат: `/announce Текст вашего объявления`", parse_mode="Markdown")
        return

    data = load_auth_data()
    recipient_ids = list(data.get("active_sessions", {}).keys())
    if ADMIN_ID and str(ADMIN_ID) not in recipient_ids:
        recipient_ids.append(str(ADMIN_ID))

    sent_count = 0
    announce_msg = f"📢 **ВАЖНОЕ ОПОВЕЩЕНИЕ**\nОт: **{role}**\n\n{text}"

    for uid in recipient_ids:
        try:
            await bot.send_message(chat_id=int(uid), text=announce_msg, parse_mode="Markdown")
            sent_count += 1
        except Exception:
            pass

    await message.answer(f"✅ Оповещение отправлено {sent_count} участникам системы!")

# ================= МЕНЮ И ВЫСТАВЛЕНИЕ НОРМЫ =================

@dp.message(Command("start"))
@dp.message(Command("norma"))
async def cmd_start(message: Message):
    if not is_authorized(message.from_user.id):
        await message.answer("🔒 Доступ ограничен.\nДля входа введите: `/login логин пароль`", parse_mode="Markdown")
        return
        
    user_role = get_user_role(message.from_user.id)
    await message.answer(
        f"⚡ **Панель СС [A-ОПГ]** | Вы: *{user_role}*\nВыберите сотрудника для выставления нормы:",
        reply_markup=get_members_keyboard(),
        parse_mode="Markdown"
    )

@dp.message(Command("status"))
@dp.callback_query(F.data == "show_summary")
async def show_summary(event: Message | CallbackQuery):
    user_id = event.from_user.id
    if not is_authorized(user_id):
        if isinstance(event, CallbackQuery):
            await event.answer("Нет доступа!", show_alert=True)
        return

    today_short = datetime.now().strftime("%d.%m")
    today_full = datetime.now().strftime("%d.%m.%y")

    header = sheet.row_values(4)
    rows_data = sheet.get("G5:P11")
    
    col_idx = None
    for idx in range(8, len(header)):
        val_str = str(header[idx]).strip()
        if today_short in val_str or today_full in val_str:
            col_idx = idx + 1
            break

    msg_lines = [f"📊 **Сводка нормы за сегодня ({today_short}):**\n"]
    
    for row in rows_data:
        role = row[0] if len(row) > 0 else "СС"
        nick = row[1] if len(row) > 1 and row[1].lower() != "none" else "Не назначен"
        
        today_val = "—"
        if col_idx is not None:
            slice_col = col_idx - 7
            if 0 <= slice_col < len(row) and row[slice_col]:
                today_val = row[slice_col]

        total_points = row[-1] if len(row) >= 10 else "0"
        msg_lines.append(f"• **{nick}** ({role}): отметка `{today_val}` | Итог: `{total_points}` б.")

    summary_text = "\n".join(msg_lines)
    summary_kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ К выставлению нормы", callback_data="back_to_menu")]])

    if isinstance(event, CallbackQuery):
        await event.message.edit_text(summary_text, reply_markup=summary_kb, parse_mode="Markdown")
        await event.answer()
    else:
        await event.answer(summary_text, reply_markup=summary_kb, parse_mode="Markdown")

@dp.callback_query(F.data == "back_to_menu")
async def back_to_menu(callback: CallbackQuery):
    if not is_authorized(callback.from_user.id):
        await callback.answer("🔒 Доступ отозван!", show_alert=True)
        return
    await callback.message.edit_text(
        "⚡ **Панель СС [A-ОПГ]**\nВыберите сотрудника для выставления нормы:",
        reply_markup=get_members_keyboard(),
        parse_mode="Markdown"
    )
    await callback.answer()

@dp.callback_query(F.data.startswith("sel_"))
async def choose_member(callback: CallbackQuery):
    if not is_authorized(callback.from_user.id):
        await callback.answer("🔒 Доступ отозван!", show_alert=True)
        return
        
    _, row_idx = callback.data.split("_")
    row_idx = int(row_idx)
    
    role = sheet.cell(row_idx, 7).value or "Сотрудник"
    nick = sheet.cell(row_idx, 8).value or ""
    
    title = f"{nick} [{role}]" if nick and nick.lower() not in ["none", "nick", "-"] else f"{role} (строка {row_idx})"
    
    await callback.message.edit_text(
        f"Выбран: **{title}**\nКакую отметку выставить?",
        reply_markup=get_status_keyboard(row_idx),
        parse_mode="Markdown"
    )
    await callback.answer()

@dp.callback_query(F.data.startswith("set_"))
async def save_norma(callback: CallbackQuery):
    if not is_authorized(callback.from_user.id):
        await callback.answer("🔒 Доступ отозван!", show_alert=True)
        return
        
    _, row_idx, status_key = callback.data.split("_")
    row_idx = int(row_idx)
    cfg = STATUS_CONFIG.get(status_key)

    if not cfg:
        await callback.answer("Ошибка статуса!", show_alert=True)
        return

    today_short = datetime.now().strftime("%d.%m")
    today_full = datetime.now().strftime("%d.%m.%y")

    try:
        header_row = sheet.row_values(4)
        col_idx = None
        
        for idx in range(8, len(header_row)):
            val_str = str(header_row[idx]).strip()
            if today_short in val_str or today_full in val_str:
                col_idx = idx + 1
                break

        if not col_idx:
            row_vals = sheet.row_values(row_idx)
            for check_col in range(9, 16):
                if check_col > len(row_vals) or not row_vals[check_col - 1] or str(row_vals[check_col - 1]).strip() in ["-", ""]:
                    col_idx = check_col
                    break

        if not col_idx:
            col_idx = 9

        current_val = sheet.cell(row_idx, col_idx).value
        final_val = cfg["points"]

        if cfg["is_numeric"]:
            try:
                if current_val and str(current_val).strip() not in ["-", "", "None"]:
                    existing_points = int(str(current_val).strip())
                    final_val = existing_points + int(cfg["points"])
            except ValueError:
                final_val = cfg["points"]

        sheet.update_cell(row_idx, col_idx, str(final_val))
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
            f"✅ Выставлено: **{cfg['title']}**\nВ ячейку `{cell_name}` записано: **`{final_val}`**.",
            reply_markup=get_members_keyboard(),
            parse_mode="Markdown"
        )
        await callback.answer("Сохранено и подсчитано!")

    except Exception as e:
        await callback.answer(f"Ошибка: {e}", show_alert=True)

async def main():
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
    
