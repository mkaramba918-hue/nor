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
doc = client.open(SPREADSHEET_NAME)

sheet_norma = doc.worksheet("Норма")
sheet_ss = doc.worksheet("Таблица СС")
sheet_neaktiv = doc.worksheet("Неактивы")

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

# Соответствие слота сотрудника (1-7) строкам на разных листах:
# Слот 1-3: Положенцы [9] (строки Норма: 5-7, Таблица СС: 10-12, Неактивы: 14-16)
# Слот 4-7: Смотрящие [8] (строки Норма: 8-11, Таблица СС: 13-16, Неактивы: 17-20)
def map_slot_rows(slot: int):
    norma_r = 4 + slot
    ss_r = 9 + slot
    neaktiv_r = 13 + slot
    return norma_r, ss_r, neaktiv_r

STATUS_CONFIG = {
    "norma": {"title": "🟩 Норма (+5)", "points": 5, "is_numeric": True, "bg": {"red": 0.0, "green": 1.0, "blue": 0.0}, "fg": {"red": 0.0, "green": 0.0, "blue": 0.0}},
    "overnorma": {"title": "🟦 Перенорма (+8)", "points": 8, "is_numeric": True, "bg": {"red": 0.0, "green": 0.0, "blue": 1.0}, "fg": {"red": 1.0, "green": 1.0, "blue": 1.0}},
    "natyag": {"title": "🟪 Натяг (+1)", "points": 1, "is_numeric": True, "bg": {"red": 0.6, "green": 0.0, "blue": 0.9}, "fg": {"red": 1.0, "green": 1.0, "blue": 1.0}},
    "nonorma": {"title": "🟥 Нет нормы (-5)", "points": -5, "is_numeric": True, "bg": {"red": 1.0, "green": 0.0, "blue": 0.0}, "fg": {"red": 1.0, "green": 1.0, "blue": 1.0}},
    "neaktiv": {"title": "⬜ Неактив (-2)", "points": -2, "is_numeric": True, "bg": {"red": 0.6, "green": 0.6, "blue": 0.6}, "fg": {"red": 1.0, "green": 1.0, "blue": 1.0}},
    "new_ss": {"title": "🟨 Новый СС / Освоб.", "points": "Осв", "is_numeric": False, "bg": {"red": 1.0, "green": 1.0, "blue": 0.0}, "fg": {"red": 0.0, "green": 0.0, "blue": 0.0}},
    "dayoff": {"title": "🔷 Выходной / УВ (0)", "points": 0, "is_numeric": True, "bg": {"red": 0.0, "green": 0.9, "blue": 1.0}, "fg": {"red": 0.0, "green": 0.0, "blue": 0.0}},
    "question": {"title": "⬛ Под вопросом", "points": "?", "is_numeric": False, "bg": {"red": 0.1, "green": 0.1, "blue": 0.1}, "fg": {"red": 1.0, "green": 1.0, "blue": 1.0}}
}

def get_today_column():
    today_short = datetime.now().strftime("%d.%m")
    today_full = datetime.now().strftime("%d.%m.%y")
    header_row = sheet_norma.row_values(4)
    for idx in range(8, len(header_row)):
        val_str = str(header_row[idx]).strip()
        if today_short in val_str or today_full in val_str:
            return idx + 1
    return 9

def get_main_keyboard():
    rows = sheet_norma.get("G5:H11")
    buttons = []
    for idx, row in enumerate(rows, start=5):
        slot_num = idx - 4
        role = row[0].strip() if len(row) > 0 and row[0] else ("Положенец [9]" if slot_num <= 3 else "Смотрящий [8]")
        nick = row[1].strip() if len(row) > 1 and row[1] else ""
        if nick and nick.lower() not in ["none", "nick", "-", ""]:
            label = f"#{slot_num} {nick} [{role}]"
        else:
            label = f"#{slot_num} ▫️ {role} (Свободно)"
        buttons.append([InlineKeyboardButton(text=label, callback_data=f"sel_{idx}")])
        
    buttons.append([InlineKeyboardButton(text="⚡ Всем норму (+5)", callback_data="set_all_norma")])
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
    keyboard.append([InlineKeyboardButton(text="⬅️ Назад к списку", callback_data="back_to_menu")])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

# ================= КОМАНДА /HELP =================

@dp.message(Command("help"))
async def cmd_help(message: Message):
    if not is_authorized(message.from_user.id):
        await message.answer("🔒 Доступ ограничен.\nДля входа введите: `/login логин пароль`", parse_mode="Markdown")
        return
        
    role = get_user_role(message.from_user.id)
    help_text = (
        f"📖 **Руководство по командам бота**\n"
        f"Ваш статус: **{role}**\n\n"
        "🟢 **Управление нормой:**\n"
        "• `/start` или `/norma` — открыть меню со списком Старшего Состава.\n"
        "• Кнопка `⚡ Всем норму (+5)` — за секунду проставит норму всем 7 сотрудникам.\n"
        "• Кнопка `📊 Сводка нормы` — посмотреть все баллы за сегодня прямо в чате.\n\n"
        "👤 **Назначение сотрудника на пост:**\n"
        "• `/setnick [номер 1-7] [Ник]` — вписать нового сотрудника сразу во все 3 таблицы.\n"
        "  _Пример:_ `/setnick 1 Roma_Romanov`\n\n"
        "⚖️ **Выговоры и Предупреждения (Таблица СС):**\n"
        "• `/warn [номер 1-7]` — выдать выговор (+1).\n"
        "• `/unwarn [номер 1-7]` — снять выговор (-1).\n"
        "• `/pred [номер 1-7]` — выдать предупреждение (+1).\n"
        "• `/unpred [номер 1-7]` — снять предупреждение (-1).\n"
        "  _Пример:_ `/warn 2`\n\n"
        "🏖 **Неактивы:**\n"
        "• `/neaktiv [номер 1-7] [до какого числа]` — поставить неактив.\n"
        "  _Пример:_ `/neaktiv 1 18.09`\n\n"
        "🔐 **Доступ и управление (Следящий):**\n"
        "• `/adduser [логин] [пароль] [роль]` — выдать доступ Лидеру/ЗКО и тд.\n"
        "• `/deluser [логин]` — закрыть доступ и сбросить сессию.\n"
        "• `/users` — список всех активных логинов.\n"
        "• `/announce [текст]` — разослать важное сообщение всему руководству."
    )
    await message.answer(help_text, parse_mode="Markdown")

# ================= СМЕНА НИКА =================

@dp.message(Command("setnick"))
async def cmd_setnick(message: Message):
    if not is_authorized(message.from_user.id):
        return
    parts = message.text.split()
    if len(parts) != 3 or not parts[1].isdigit() or not (1 <= int(parts[1]) <= 7):
        await message.answer("⚠️ Формат: `/setnick [номер 1-7] [Nick_Name]`\n_Пример:_ `/setnick 1 Ivan_Ivanov`", parse_mode="Markdown")
        return
        
    slot = int(parts[1])
    nick = parts[2]
    norma_r, ss_r, neaktiv_r = map_slot_rows(slot)
    
    try:
        # Лист Норма: столбец H
        sheet_norma.update_cell(norma_r, 8, nick)
        # Лист Таблица СС: столбец A
        sheet_ss.update_cell(ss_r, 1, nick)
        # Лист Неактивы: столбец A
        sheet_neaktiv.update_cell(neaktiv_r, 1, nick)
        
        await message.answer(f"✅ Сотрудник #{slot} успешно обновлен на **{nick}** во всех таблицах!", parse_mode="Markdown")
    except Exception as e:
        await message.answer(f"Ошибка при смене ника: {e}")

# ================= НАКАЗАНИЯ (ВЫГОВОРЫ / ПРЕДЫ) =================

@dp.message(Command("warn", "unwarn", "pred", "unpred"))
async def cmd_punishments(message: Message):
    if not is_authorized(message.from_user.id):
        return
        
    parts = message.text.split()
    cmd = parts[0].replace("/", "").lower()
    
    if len(parts) != 2 or not parts[1].isdigit() or not (1 <= int(parts[1]) <= 7):
        await message.answer(f"⚠️ Формат: `/{cmd} [номер сотрудника 1-7]`\n_Пример:_ `/{cmd} 1`", parse_mode="Markdown")
        return
        
    slot = int(parts[1])
    _, ss_r, _ = map_slot_rows(slot)
    
    col_idx = 25 if "warn" in cmd else 26  # Столбец Y (25) = Выговоры, Z (26) = Преды
    max_val = 3 if "warn" in cmd else 2
    
    cur_val = sheet_ss.cell(ss_r, col_idx).value or f"0/{max_val}"
    try:
        cur_num = int(str(cur_val).split("/")[0])
    except Exception:
        cur_num = 0
        
    if "un" in cmd:
        new_num = max(0, cur_num - 1)
    else:
        new_num = min(max_val, cur_num + 1)
        
    new_str = f"{new_num}/{max_val}"
    sheet_ss.update_cell(ss_r, col_idx, new_str)
    
    p_type = "Выговор" if "warn" in cmd else "Предупреждение"
    alert = " 🚨 **ДОСТИГНУТ ЛИМИТ НАКАЗАНИЙ!**" if new_num >= max_val and "un" not in cmd else ""
    await message.answer(f"⚖️ Сотрудник #{slot}: {p_type} изменен: **{cur_val}** ➔ **{new_str}**{alert}", parse_mode="Markdown")

# ================= НЕАКТИВЫ =================

@dp.message(Command("neaktiv"))
async def cmd_neaktiv(message: Message):
    if not is_authorized(message.from_user.id):
        return
    parts = message.text.split()
    if len(parts) != 3 or not parts[1].isdigit() or not (1 <= int(parts[1]) <= 7):
        await message.answer("⚠️ Формат: `/neaktiv [номер 1-7] [по какое число]`\n_Пример:_ `/neaktiv 1 18.09`", parse_mode="Markdown")
        return
        
    slot = int(parts[1])
    until_date = parts[2]
    today_str = datetime.now().strftime("%d.%m.%y")
    norma_r, _, neaktiv_r = map_slot_rows(slot)
    
    # 1. Запись на лист «Неактивы» в колонку C (AG)
    val_text = f"От {today_str} | До {until_date}"
    sheet_neaktiv.update_cell(neaktiv_r, 3, val_text)
    
    # 2. Покраска сегодняшнего дня в листе «Норма» статусом Неактив (-2)
    col_today = get_today_column()
    cfg = STATUS_CONFIG["neaktiv"]
    sheet_norma.update_cell(norma_r, col_today, "-2")
    cell_name = gspread.utils.rowcol_to_a1(norma_r, col_today)
    sheet_norma.format(cell_name, {
        "backgroundColor": cfg["bg"],
        "horizontalAlignment": "CENTER",
        "textFormat": {"foregroundColor": cfg["fg"], "bold": True}
    })
    
    await message.answer(f"🏖 Неактив для сотрудника #{slot} оформлен!\nЗапись: `{val_text}`\nВ норме за сегодня выставлено: `-2` (серый цвет).", parse_mode="Markdown")

# ================= СИСТЕМА ДОСТУПА =================

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
        if "active_sessions" not in data:
            data["active_sessions"] = {}
        data["active_sessions"][str(message.from_user.id)] = login
        save_auth_data(data)
        role = user_info.get("role", "Лидер")
        await message.answer(f"✅ Вход выполнен!\nДолжность: **{role}**.\nМеню: /start | Справка: /help", parse_mode="Markdown")
    else:
        await message.answer("❌ Неверный логин или пароль!")

@dp.message(Command("adduser"))
async def cmd_adduser(message: Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("❌ Только Следящий может создавать доступы.")
        return
    parts = message.text.split(maxsplit=3)
    if len(parts) < 3:
        await message.answer("⚠️ Формат: `/adduser логин пароль роль`\n_Пример:_ `/adduser leader 12345 Лидер ОПГ`", parse_mode="Markdown")
        return
    login = parts[1]
    password = parts[2]
    role = parts[3] if len(parts) > 3 else "Лидер"
    data = load_auth_data()
    if "users" not in data:
        data["users"] = {}
    data["users"][login] = {"password": password, "role": role}
    save_auth_data(data)
    await message.answer(f"✅ Доступ создан!\nЛогин: `{login}` | Пароль: `{password}`\nРоль: **{role}**", parse_mode="Markdown")

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
    lines = ["👥 **Список учетных записей:**\n"]
    for login, info in users.items():
        active_uids = [uid for uid, l in active_sessions.items() if l == login]
        status_str = "🟢 В сети" if active_uids else "⚪ Не в сети"
        lines.append(f"• Логин: `{login}` | Пароль: `{info.get('password')}` | **{info.get('role')}** ({status_str})")
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
        del data["users"][login]
        active_sessions = data.get("active_sessions", {})
        to_delete = [uid for uid, l in active_sessions.items() if l == login]
        for uid in to_delete:
            del active_sessions[uid]
        save_auth_data(data)
        await message.answer(f"✅ Доступ для `{login}` закрыт и сессия завершена.", parse_mode="Markdown")
    else:
        await message.answer("Пользователь не найден.")

@dp.message(Command("announce"))
async def cmd_announce(message: Message):
    role = get_user_role(message.from_user.id)
    if not role:
        return
    text = message.text.replace("/announce", "").strip()
    if not text:
        await message.answer("⚠️ Формат: `/announce Текст объявления`", parse_mode="Markdown")
        return
    data = load_auth_data()
    recipient_ids = list(data.get("active_sessions", {}).keys())
    if ADMIN_ID and str(ADMIN_ID) not in recipient_ids:
        recipient_ids.append(str(ADMIN_ID))
    sent = 0
    for uid in recipient_ids:
        try:
            await bot.send_message(chat_id=int(uid), text=f"📢 **ВАЖНЫЙ АНОНС**\nОт: **{role}**\n\n{text}", parse_mode="Markdown")
            sent += 1
        except Exception:
            pass
    await message.answer(f"✅ Анонс отправлен {sent} участникам!")

# ================= МЕНЮ ВЫСТАВЛЕНИЯ НОРМЫ =================

@dp.message(Command("start"))
@dp.message(Command("norma"))
async def cmd_start(message: Message):
    if not is_authorized(message.from_user.id):
        await message.answer("🔒 Доступ ограничен.\nДля входа введите: `/login логин пароль`", parse_mode="Markdown")
        return
    role = get_user_role(message.from_user.id)
    await message.answer(
        f"⚡ **Панель СС [A-ОПГ]** | Вы: *{role}*\nВыберите сотрудника или действие (справка: /help):",
        reply_markup=get_main_keyboard(),
        parse_mode="Markdown"
    )

@dp.callback_query(F.data == "back_to_menu")
async def back_to_menu(callback: CallbackQuery):
    if not is_authorized(callback.from_user.id):
        await callback.answer("🔒 Доступ отозван!", show_alert=True)
        return
    await callback.message.edit_text(
        "⚡ **Панель СС [A-ОПГ]**\nВыберите сотрудника или действие (справка: /help):",
        reply_markup=get_main_keyboard(),
        parse_mode="Markdown"
    )
    await callback.answer()

@dp.callback_query(F.data == "set_all_norma")
async def set_all_norma(callback: CallbackQuery):
    if not is_authorized(callback.from_user.id):
        return
    col_idx = get_today_column()
    cfg = STATUS_CONFIG["norma"]
    await callback.answer("⏳ Выставляем норму всем...")
    for r in range(5, 12):
        sheet_norma.update_cell(r, col_idx, "5")
        cell_name = gspread.utils.rowcol_to_a1(r, col_idx)
        sheet_norma.format(cell_name, {
            "backgroundColor": cfg["bg"],
            "horizontalAlignment": "CENTER",
            "textFormat": {"foregroundColor": cfg["fg"], "bold": True}
        })
    await callback.message.edit_text(
        "✅ **Всем сотрудникам СС выставлена норма (+5)!**\nЯчейки закрашены зеленым.",
        reply_markup=get_main_keyboard(),
        parse_mode="Markdown"
    )

@dp.callback_query(F.data == "show_summary")
async def show_summary(callback: CallbackQuery):
    if not is_authorized(callback.from_user.id):
        return
    today_short = datetime.now().strftime("%d.%m")
    col_idx = get_today_column()
    rows_data = sheet_norma.get("G5:P11")
    msg_lines = [f"📊 **Сводка нормы за сегодня ({today_short}):**\n"]
    for row in rows_data:
        role = row[0] if len(row) > 0 else "СС"
        nick = row[1] if len(row) > 1 and row[1].lower() != "none" else "Не назначен"
        slice_col = col_idx - 7
        today_val = row[slice_col] if 0 <= slice_col < len(row) and row[slice_col] else "—"
        total = row[-1] if len(row) >= 10 else "0"
        msg_lines.append(f"• **{nick}** ({role}): отметка `{today_val}` | Итог: `{total}` б.")
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_menu")]])
    await callback.message.edit_text("\n".join(msg_lines), reply_markup=kb, parse_mode="Markdown")
    await callback.answer()

@dp.callback_query(F.data.startswith("sel_"))
async def choose_member(callback: CallbackQuery):
    if not is_authorized(callback.from_user.id):
        return
    _, row_idx = callback.data.split("_")
    row_idx = int(row_idx)
    role = sheet_norma.cell(row_idx, 7).value or "Сотрудник"
    nick = sheet_norma.cell(row_idx, 8).value or ""
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
        return
    _, row_idx, status_key = callback.data.split("_")
    row_idx = int(row_idx)
    cfg = STATUS_CONFIG.get(status_key)
    if not cfg:
        await callback.answer("Ошибка статуса!", show_alert=True)
        return

    col_idx = get_today_column()
    current_val = sheet_norma.cell(row_idx, col_idx).value
    final_val = cfg["points"]

    if cfg["is_numeric"]:
        try:
            if current_val and str(current_val).strip() not in ["-", "", "None"]:
                existing_points = int(str(current_val).strip())
                final_val = existing_points + int(cfg["points"])
        except ValueError:
            final_val = cfg["points"]

    sheet_norma.update_cell(row_idx, col_idx, str(final_val))
    cell_name = gspread.utils.rowcol_to_a1(row_idx, col_idx)
    sheet_norma.format(cell_name, {
        "backgroundColor": cfg["bg"],
        "horizontalAlignment": "CENTER",
        "textFormat": {"foregroundColor": cfg["fg"], "bold": True}
    })

    await callback.message.edit_text(
        f"✅ Выставлено: **{cfg['title']}**\nВ ячейку `{cell_name}` записано: **`{final_val}`**.",
        reply_markup=get_main_keyboard(),
        parse_mode="Markdown"
    )
    await callback.answer("Сохранено и покрашено!")

async def main():
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
    
