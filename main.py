import os
import json
import asyncio
import sqlite3
import logging
from datetime import datetime
import gspread
from google.oauth2.service_account import Credentials
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
from aiogram.filters import Command

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

BOT_TOKEN = os.getenv("BOT_TOKEN")
SPREADSHEET_NAME = os.getenv("SPREADSHEET_NAME", "| CHILLI | ...")
LOGS_SPREADSHEET_NAME = os.getenv("LOGS_SPREADSHEET_NAME", "Логи СС | А-ОПГ")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
GOOGLE_CREDS_JSON = os.getenv("GOOGLE_CREDS_JSON")

scopes = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]

creds_dict = json.loads(GOOGLE_CREDS_JSON)
credentials = Credentials.from_service_account_info(creds_dict, scopes=scopes)
client = gspread.authorize(credentials)

# 1. Основная фракционная таблица
main_doc = client.open(SPREADSHEET_NAME)
sheet_norma = main_doc.worksheet("Норма")
sheet_ss = main_doc.worksheet("Таблица СС")
sheet_neaktiv = main_doc.worksheet("Неактивы")

# 2. ОТДЕЛЬНАЯ таблица исключительно под логи
try:
    logs_doc = client.open(LOGS_SPREADSHEET_NAME)
except Exception:
    # Если передали ID таблицы вместо имени
    logs_doc = client.open_by_key(LOGS_SPREADSHEET_NAME)

sheet_logs = logs_doc.sheet1

# Проверяем и создаем шапку в отдельной таблице логов при первом запуске
if not sheet_logs.row_values(1):
    sheet_logs.append_row(["Дата и Время", "Должность / Роль", "Telegram ID", "Действие"])
    sheet_logs.format("A1:D1", {
        "backgroundColor": {"red": 0.15, "green": 0.2, "blue": 0.3},
        "horizontalAlignment": "CENTER",
        "textFormat": {"foregroundColor": {"red": 1.0, "green": 1.0, "blue": 1.0}, "bold": True}
    })

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# ================= БАЗА ДАННЫХ SQLITE =================
DB_PATH = "bot_database.db"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            login TEXT PRIMARY KEY,
            password TEXT NOT NULL,
            role TEXT NOT NULL
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            telegram_id INTEGER PRIMARY KEY,
            login TEXT NOT NULL,
            login_time TEXT NOT NULL,
            FOREIGN KEY (login) REFERENCES users(login) ON DELETE CASCADE
        )
    """)
    conn.commit()
    conn.close()

init_db()

def log_action(telegram_id: int, role: str, action: str):
    """Пишет лог строго в ОТДЕЛЬНУЮ Google Таблицу."""
    now_str = datetime.now().strftime("%d.%m.%Y %H:%M:%S")
    logging.info(f"[{role} | {telegram_id}] {action}")
    try:
        sheet_logs.append_row([now_str, role, str(telegram_id), action])
    except Exception as e:
        logging.error(f"Ошибка записи лога в отдельную таблицу: {e}")

def get_user_role(user_id: int):
    if ADMIN_ID != 0 and user_id == ADMIN_ID:
        return "Следящий (Владелец)"
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        SELECT u.role FROM sessions s
        JOIN users u ON s.login = u.login
        WHERE s.telegram_id = ?
    """, (user_id,))
    row = cur.fetchone()
    conn.close()
    return row[0] if row else None

def is_authorized(user_id: int) -> bool:
    return get_user_role(user_id) is not None

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
        
    # Кнопка открытия Mini App (работает отправка через sendData)
    buttons.append([
        InlineKeyboardButton(
            text="📱 Открыть Mini App панель",
            web_app=WebAppInfo(url="https://mkaramba918-hue.github.io/nor/webapp/")
        )
    ])
    
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

# ================= КОМАНДЫ =================

@dp.message(Command("help"))
async def cmd_help(message: Message):
    if not is_authorized(message.from_user.id):
        await message.answer("🔒 Доступ ограничен.\nДля входа введите: `/login логин пароль`", parse_mode="Markdown")
        return
        
    role = get_user_role(message.from_user.id)
    help_text = (
        f"📖 **Руководство по командам**\n"
        f"Ваш статус: **{role}**\n\n"
        "🟢 **Норма:**\n"
        "• `/start` или `/norma` — список Старшего Состава.\n"
        "• Кнопка `⚡ Всем норму (+5)` — выставить норму всем 7 сотрудникам.\n"
        "• Кнопка `📊 Сводка нормы` — текущие баллы в чате.\n\n"
        "👤 **Назначение сотрудника:**\n"
        "• `/setnick [номер 1-7] [Ник]` — обновить ник во всех листах.\n\n"
        "⚖️ **Наказания (Таблица СС):**\n"
        "• `/warn [номер 1-7]` — выдать выговор (+1).\n"
        "• `/unwarn [номер 1-7]` — снять выговор (-1).\n"
        "• `/pred [номер 1-7]` — выдать предупреждение (+1).\n"
        "• `/unpred [номер 1-7]` — снять предупреждение (-1).\n\n"
        "🏖 **Неактивы:**\n"
        "• `/neaktiv [номер 1-7] [до какого числа]` — оформить неактив.\n\n"
        "📜 **Логи:**\n"
        "• Все действия пишутся в отдельную таблицу логов.\n"
        "• `/logs` — последние 10 записей в чат.\n\n"
        "🔐 **Следящий:**\n"
        "• `/adduser [логин] [пароль] [роль]` — создать доступ.\n"
        "• `/deluser [логин]` — удалить доступ.\n"
        "• `/users` — список логинов.\n"
        "• `/announce [текст]` — рассылка анонса."
    )
    await message.answer(help_text, parse_mode="Markdown")

@dp.message(Command("login"))
async def cmd_login(message: Message):
    parts = message.text.split()
    if len(parts) != 3:
        await message.answer("⚠️ Формат: `/login логин пароль`", parse_mode="Markdown")
        return
        
    login, password = parts[1], parts[2]
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT role FROM users WHERE login = ? AND password = ?", (login, password))
    user_row = cur.fetchone()
    
    if user_row:
        role = user_row[0]
        now_str = datetime.now().strftime("%d.%m.%Y %H:%M:%S")
        cur.execute("REPLACE INTO sessions (telegram_id, login, login_time) VALUES (?, ?, ?)", 
                    (message.from_user.id, login, now_str))
        conn.commit()
        conn.close()
        
        log_action(message.from_user.id, role, f"Вход под логином '{login}'")
        await message.answer(f"✅ Вход выполнен!\nДолжность: **{role}**.\nМеню: /start | Справка: /help", parse_mode="Markdown")
    else:
        conn.close()
        log_action(message.from_user.id, "Гость", f"Неудачная попытка входа с логином '{login}'")
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
    
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("REPLACE INTO users (login, password, role) VALUES (?, ?, ?)", (login, password, role))
    conn.commit()
    conn.close()
    
    log_action(message.from_user.id, "Следящий", f"Создан пользователь: {login} ({role})")
    await message.answer(f"✅ Доступ создан!\nЛогин: `{login}` | Пароль: `{password}`\nРоль: **{role}**", parse_mode="Markdown")

@dp.message(Command("users"))
async def cmd_users(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
        
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        SELECT u.login, u.password, u.role, s.telegram_id 
        FROM users u
        LEFT JOIN sessions s ON u.login = s.login
    """)
    rows = cur.fetchall()
    conn.close()
    
    if not rows:
        await message.answer("Пока нет зарегистрированных пользователей.")
        return
        
    lines = ["👥 **Список доступов:**\n"]
    for login, pwd, role, tg_id in rows:
        status_str = f"🟢 В сети (ID: `{tg_id}`)" if tg_id else "⚪ Не в сети"
        lines.append(f"• Логин: `{login}` | Пароль: `{pwd}`\n  Роль: **{role}** | {status_str}")
        
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
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT login FROM users WHERE login = ?", (login,))
    if cur.fetchone():
        cur.execute("DELETE FROM sessions WHERE login = ?", (login,))
        cur.execute("DELETE FROM users WHERE login = ?", (login,))
        conn.commit()
        conn.close()
        
        log_action(message.from_user.id, "Следящий", f"Удален доступ пользователя '{login}'")
        await message.answer(f"✅ Доступ для `{login}` закрыт и сессия завершена.", parse_mode="Markdown")
    else:
        conn.close()
        await message.answer("Пользователь не найден.")

@dp.message(Command("logs"))
async def cmd_logs(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
        
    all_logs = sheet_logs.get_all_values()
    if len(all_logs) <= 1:
        await message.answer("Отдельная таблица логов пока пуста.")
        return
        
    recent = all_logs[-10:]
    lines = ["📜 **Последние записи из отдельной таблицы логов:**\n"]
    for row in recent:
        ts = row[0] if len(row) > 0 else ""
        role = row[1] if len(row) > 1 else ""
        act = row[3] if len(row) > 3 else ""
        lines.append(f"`[{ts}]` **{role}**: {act}")
        
    await message.answer("\n".join(lines), parse_mode="Markdown")

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
        sheet_norma.update_cell(norma_r, 8, nick)
        sheet_ss.update_cell(ss_r, 1, nick)
        sheet_neaktiv.update_cell(neaktiv_r, 1, nick)
        
        role = get_user_role(message.from_user.id)
        log_action(message.from_user.id, role, f"Изменил ник слота #{slot} на '{nick}'")
        await message.answer(f"✅ Сотрудник #{slot} обновлен на **{nick}** во всех таблицах!", parse_mode="Markdown")
    except Exception as e:
        await message.answer(f"Ошибка при смене ника: {e}")

@dp.message(Command("warn", "unwarn", "pred", "unpred"))
async def cmd_punishments(message: Message):
    if not is_authorized(message.from_user.id):
        return
        
    parts = message.text.split()
    cmd = parts[0].replace("/", "").lower()
    
    if len(parts) != 2 or not parts[1].isdigit() or not (1 <= int(parts[1]) <= 7):
        await message.answer(f"⚠️ Формат: `/{cmd} [номер сотрудника 1-7]`", parse_mode="Markdown")
        return
        
    slot = int(parts[1])
    _, ss_r, _ = map_slot_rows(slot)
    
    col_idx = 25 if "warn" in cmd else 26
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
    alert = " 🚨 **ЛИМИТ НАКАЗАНИЙ!**" if new_num >= max_val and "un" not in cmd else ""
    
    role = get_user_role(message.from_user.id)
    log_action(message.from_user.id, role, f"{p_type} для слота #{slot}: {cur_val} -> {new_str}")
    await message.answer(f"⚖️ Сотрудник #{slot}: {p_type} изменен: **{cur_val}** ➔ **{new_str}**{alert}", parse_mode="Markdown")

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
    
    val_text = f"От {today_str} | До {until_date}"
    sheet_neaktiv.update_cell(neaktiv_r, 3, val_text)
    
    col_today = get_today_column()
    cfg = STATUS_CONFIG["neaktiv"]
    sheet_norma.update_cell(norma_r, col_today, "-2")
    cell_name = gspread.utils.rowcol_to_a1(norma_r, col_today)
    sheet_norma.format(cell_name, {
        "backgroundColor": cfg["bg"],
        "horizontalAlignment": "CENTER",
        "textFormat": {"foregroundColor": cfg["fg"], "bold": True}
    })
    
    role = get_user_role(message.from_user.id)
    log_action(message.from_user.id, role, f"Неактив для слота #{slot} ({val_text})")
    await message.answer(f"🏖 Неактив для слота #{slot} оформлен!\nЗапись: `{val_text}`\nВ норме выставлено: `-2`.", parse_mode="Markdown")

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
        
    role = get_user_role(callback.from_user.id)
    log_action(callback.from_user.id, role, "Выставил норму (+5) всем сотрудникам СС")
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

    role = get_user_role(callback.from_user.id)
    slot_num = row_idx - 4
    log_action(callback.from_user.id, role, f"Выставил '{cfg['title']}' сотруднику #{slot_num} (ячейка {cell_name} = {final_val})")

    await callback.message.edit_text(
        f"✅ Выставлено: **{cfg['title']}**\nВ ячейку `{cell_name}` записано: **`{final_val}`**.",
        reply_markup=get_main_keyboard(),
        parse_mode="Markdown"
    )
    await callback.answer("Сохранено и покрашено!")

# ================= ОБРАБОТЧИК ДАННЫХ ИЗ MINI APP =================
@dp.message(F.web_app_data)
async def handle_webapp_data(message: Message):
    try:
        data = json.loads(message.web_app_data.data)
        action = data.get("action")

        # 1. Логин
        if action == "login":
            login = data.get("login")
            password = data.get("password")
            conn = sqlite3.connect(DB_PATH)
            cur = conn.cursor()
            cur.execute("SELECT role FROM users WHERE login = ? AND password = ?", (login, password))
            user_row = cur.fetchone()

            if user_row:
                role = user_row[0]
                now_str = datetime.now().strftime("%d.%m.%Y %H:%M:%S")
                cur.execute("REPLACE INTO sessions (telegram_id, login, login_time) VALUES (?, ?, ?)", 
                            (message.from_user.id, login, now_str))
                conn.commit()
                conn.close()
                log_action(message.from_user.id, role, f"[Mini App] Вход под логином '{login}'")
                await message.answer(f"✅ Вход через Mini App выполнен!\nДолжность: **{role}**.", parse_mode="Markdown")
            else:
                conn.close()
                await message.answer("❌ Ошибка входа через Mini App: неверный логин или пароль!")
            return

        # Проверка авторизации для остальных действий
        if not is_authorized(message.from_user.id):
            await message.answer("🔒 Сначала авторизуйтесь через Mini App или команду `/login`!", parse_mode="Markdown")
            return

        role = get_user_role(message.from_user.id)

        # 2. Норма одному сотруднику
        if action == "norma":
            slot = int(data.get("slot"))
            status_key = data.get("status")
            cfg = STATUS_CONFIG.get(status_key)
            norma_r, _, _ = map_slot_rows(slot)
            col_idx = get_today_column()

            current_val = sheet_norma.cell(norma_r, col_idx).value
            final_val = cfg["points"]
            if cfg["is_numeric"]:
                try:
                    if current_val and str(current_val).strip() not in ["-", "", "None"]:
                        final_val = int(str(current_val).strip()) + int(cfg["points"])
                except ValueError:
                    final_val = cfg["points"]

            sheet_norma.update_cell(norma_r, col_idx, str(final_val))
            cell_name = gspread.utils.rowcol_to_a1(norma_r, col_idx)
            sheet_norma.format(cell_name, {
                "backgroundColor": cfg["bg"],
                "horizontalAlignment": "CENTER",
                "textFormat": {"foregroundColor": cfg["fg"], "bold": True}
            })
            log_action(message.from_user.id, role, f"[Mini App] Выставил '{cfg['title']}' слоту #{slot} ({cell_name} = {final_val})")
            await message.answer(f"✅ [Mini App] Сотруднику #{slot} выставлено: **{cfg['title']}** (итог ячейки: `{final_val}`)", parse_mode="Markdown")

        # 3. Всем норму (+5)
        elif action == "all_norma":
            col_idx = get_today_column()
            cfg = STATUS_CONFIG["norma"]
            for r in range(5, 12):
                sheet_norma.update_cell(r, col_idx, "5")
                cell_name = gspread.utils.rowcol_to_a1(r, col_idx)
                sheet_norma.format(cell_name, {
                    "backgroundColor": cfg["bg"],
                    "horizontalAlignment": "CENTER",
                    "textFormat": {"foregroundColor": cfg["fg"], "bold": True}
                })
            log_action(message.from_user.id, role, "[Mini App] Выставил норму (+5) всем СС")
            await message.answer("✅ [Mini App] Всем сотрудникам СС выставлена норма (+5)!", parse_mode="Markdown")

        # 4. Запрос сводки
        elif action == "summary":
            today_short = datetime.now().strftime("%d.%m")
            col_idx = get_today_column()
            rows_data = sheet_norma.get("G5:P11")
            msg_lines = [f"📊 **Сводка нормы ({today_short}):**\n"]
            for row in rows_data:
                r_role = row[0] if len(row) > 0 else "СС"
                nick = row[1] if len(row) > 1 and row[1].lower() != "none" else "Не назначен"
                slice_col = col_idx - 7
                today_val = row[slice_col] if 0 <= slice_col < len(row) and row[slice_col] else "—"
                total = row[-1] if len(row) >= 10 else "0"
                msg_lines.append(f"• **{nick}** ({r_role}): `{today_val}` | Итог: `{total}` б.")
            await message.answer("\n".join(msg_lines), parse_mode="Markdown")

        # 5. Выговоры и преды
        elif action == "punish":
            p_type = data.get("type")
            slot = int(data.get("slot"))
            _, ss_r, _ = map_slot_rows(slot)
            col_idx = 25 if "warn" in p_type else 26
            max_val = 3 if "warn" in p_type else 2

            try:
                cur_val = sheet_ss.cell(ss_r, col_idx).value or f"0/{max_val}"
            except Exception:
                cur_val = f"0/{max_val}"

            if not cur_val or "/" not in str(cur_val):
                cur_num = 0
            else:
                try:
                    cur_num = int(str(cur_val).split("/")[0])
                except Exception:
                    cur_num = 0

            new_num = max(0, cur_num - 1) if "un" in p_type else min(max_val, cur_num + 1)
            new_str = f"{new_num}/{max_val}"
            sheet_ss.update_cell(ss_r, col_idx, new_str)

            title = "Выговор" if "warn" in p_type else "Предупреждение"
            log_action(message.from_user.id, role, f"[Mini App] {title} для #{slot}: {cur_val} -> {new_str}")
            await message.answer(f"⚖️ [Mini App] #{slot}: {title} изменен: **{cur_val}** ➔ **{new_str}**", parse_mode="Markdown")

        # 6. Смена ника
        elif action == "setnick":
            slot = int(data.get("slot"))
            nick = data.get("nick")
            norma_r, ss_r, neaktiv_r = map_slot_rows(slot)
            sheet_norma.update_cell(norma_r, 8, nick)
            sheet_ss.update_cell(ss_r, 1, nick)
            sheet_neaktiv.update_cell(neaktiv_r, 1, nick)
            log_action(message.from_user.id, role, f"[Mini App] Ник слота #{slot} изменен на '{nick}'")
            await message.answer(f"✅ [Mini App] Сотрудник #{slot} обновлен на **{nick}** во всех листах!", parse_mode="Markdown")

        # 7. Неактив
        elif action == "neaktiv":
            slot = int(data.get("slot"))
            until = data.get("until")
            today_str = datetime.now().strftime("%d.%m.%y")
            norma_r, _, neaktiv_r = map_slot_rows(slot)
            val_text = f"От {today_str} | До {until}"
            sheet_neaktiv.update_cell(neaktiv_r, 3, val_text)

            col_today = get_today_column()
            cfg = STATUS_CONFIG["neaktiv"]
            sheet_norma.update_cell(norma_r, col_today, "-2")
            cell_name = gspread.utils.rowcol_to_a1(norma_r, col_today)
            sheet_norma.format(cell_name, {
                "backgroundColor": cfg["bg"],
                "horizontalAlignment": "CENTER",
                "textFormat": {"foregroundColor": cfg["fg"], "bold": True}
            })
            log_action(message.from_user.id, role, f"[Mini App] Неактив слоту #{slot} ({val_text})")
            await message.answer(f"🏖 [Mini App] Неактив #{slot} оформлен: `{val_text}` (отметка -2)", parse_mode="Markdown")

        # 8. Анонс
        elif action == "announce":
            text = data.get("text")
            conn = sqlite3.connect(DB_PATH)
            cur = conn.cursor()
            cur.execute("SELECT telegram_id FROM sessions")
            rows = cur.fetchall()
            conn.close()
            recipient_ids = [r[0] for r in rows]
            if ADMIN_ID and ADMIN_ID not in recipient_ids:
                recipient_ids.append(ADMIN_ID)
            sent = 0
            for uid in recipient_ids:
                try:
                    await bot.send_message(chat_id=int(uid), text=f"📢 **ВАЖНЫЙ АНОНС**\nОт: **{role}**\n\n{text}", parse_mode="Markdown")
                    sent += 1
                except Exception:
                    pass
            log_action(message.from_user.id, role, f"[Mini App] Анонс ({sent} чел.): '{text[:30]}...'")
            await message.answer(f"✅ [Mini App] Анонс отправлен {sent} участникам!", parse_mode="Markdown")

    except Exception as e:
        await message.answer(f"Ошибка Mini App: {e}")

# ================= КОМАНДА АНОНСА ИЗ ЧАТА =================
@dp.message(Command("announce"))
async def cmd_announce(message: Message):
    role = get_user_role(message.from_user.id)
    if not role:
        return
    text = message.text.replace("/announce", "").strip()
    if not text:
        await message.answer("⚠️ Формат: `/announce Текст объявления`", parse_mode="Markdown")
        return
    
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT telegram_id FROM sessions")
    rows = cur.fetchall()
    conn.close()
    
    recipient_ids = [r[0] for r in rows]
    if ADMIN_ID and ADMIN_ID not in recipient_ids:
        recipient_ids.append(ADMIN_ID)
        
    sent = 0
    for uid in recipient_ids:
        try:
            await bot.send_message(chat_id=int(uid), text=f"📢 **ВАЖНЫЙ АНОНС**\nОт: **{role}**\n\n{text}", parse_mode="Markdown")
            sent += 1
        except Exception:
            pass
            
    log_action(message.from_user.id, role, f"Отправил анонс ({sent} получателям): '{text[:30]}...'")
    await message.answer(f"✅ Анонс отправлен {sent} участникам!")

async def main():
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
    
