import asyncio
import os
import re
import shutil
import tempfile
import time
import uuid
import zipfile

from aiohttp import web
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    Message,
    FSInputFile,
    BufferedInputFile,
    ReplyKeyboardMarkup,
    KeyboardButton,
    WebAppInfo
)

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("Переменная BOT_TOKEN не установлена в настройках Railway!")

RAILWAY_STATIC_URL = os.getenv("RAILWAY_STATIC_URL")
if RAILWAY_STATIC_URL and not RAILWAY_STATIC_URL.startswith("http"):
    WEBAPP_URL = f"https://{RAILWAY_STATIC_URL}"
else:
    WEBAPP_URL = os.getenv("WEBAPP_URL", "https://your-domain.up.railway.app")

PORT = int(os.getenv("PORT", 8080))
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

STORAGE_DIR = "/tmp/amx_storage"
os.makedirs(STORAGE_DIR, exist_ok=True)

# Глобальная история всех компиляций (хранит последние 50 сборок Web App + Telegram)
BOT_START_TIME = time.time()
COMPILE_STATS = {"total": 0, "success": 0, "failed": 0}
compilation_history: list[dict] = []
user_logs: dict[int, dict] = {}


def get_pawncc_exe() -> str:
    """Поиск бинарника pawncc.exe внутри контейнера."""
    candidates = [
        "/app/compiler/bin/pawncc.exe",
        "/app/compiler/pawncc.exe"
    ]
    for c in candidates:
        if os.path.exists(c):
            return c

    if os.path.exists("/app/compiler"):
        for root, _, files in os.walk("/app/compiler"):
            if "pawncc.exe" in files:
                return os.path.join(root, "pawncc.exe")

    return "/app/compiler/bin/pawncc.exe"


def get_system_include() -> str:
    """Поиск папки базовых инклудов."""
    candidates = ["/app/compiler/include", "/app/include"]
    for c in candidates:
        if os.path.exists(c):
            return c
    return "/app/compiler/include"


def safe_decode(b: bytes) -> str:
    if not b:
        return ""
    for enc in ("utf-8", "cp1251", "cp866", "latin-1"):
        try:
            return b.decode(enc)
        except UnicodeDecodeError:
            continue
    return b.decode("utf-8", errors="replace")


def format_uptime(seconds: float) -> str:
    s = int(seconds)
    hours, remainder = divmod(s, 3600)
    minutes, sec = divmod(remainder, 60)
    days, hours = divmod(hours, 24)
    parts = []
    if days > 0:
        parts.append(f"{days} дн.")
    if hours > 0:
        parts.append(f"{hours} ч.")
    if minutes > 0:
        parts.append(f"{minutes} мин.")
    parts.append(f"{sec} сек.")
    return " ".join(parts)


def add_include_guards_to_files(root_dir: str, exclude_file: str):
    """Предотвращает бесконечное зацикливание при перекрестных вызовах #include."""
    for root, _, files in os.walk(root_dir):
        for f in files:
            f_l = f.lower()
            if (f_l.endswith(".inc") or f_l.endswith(".pwn")) and os.path.join(root, f) != exclude_file:
                fp = os.path.join(root, f)
                try:
                    with open(fp, "rb") as fl:
                        content = fl.read()
                    enc = "utf-8"
                    for t in ("utf-8", "cp1251", "latin-1"):
                        try:
                            text = content.decode(t)
                            enc = t
                            break
                        except UnicodeDecodeError:
                            continue
                    else:
                        text = content.decode("utf-8", errors="replace")

                    guard = f"_GUARD_{re.sub(r'[^A-Z0-9_]', '_', f.upper())}_"
                    if guard not in text:
                        guarded_text = f"#if defined {guard}\n    #endinput\n#endif\n#define {guard}\n\n" + text
                        with open(fp, "w", encoding=enc, errors="replace") as fl:
                            fl.write(guarded_text)
                except Exception:
                    pass


def auto_repair_source_code(file_path: str, db_host="127.0.0.1", db_user="user909028", db_name="user909028", db_pass="FpUjJoAu2gVD") -> list[str]:
    fixes = []
    try:
        with open(file_path, "rb") as f:
            raw = f.read()

        enc = "utf-8"
        for t_enc in ("utf-8", "cp1251", "latin-1"):
            try:
                code = raw.decode(t_enc)
                enc = t_enc
                break
            except UnicodeDecodeError:
                continue
        else:
            code = raw.decode("utf-8", errors="replace")

        # 1. Закрытие незакрытых кавычек
        def fix_quotes(m):
            l = m.group(0)
            if l.count('"') % 2 != 0:
                fixes.append(f"Закрыта кавычка: `{l.strip()[:35]}...`")
                return l + '"'
            return l

        code = re.sub(r'^#define\s+.*', fix_quotes, code, flags=re.MULTILINE)

        # 2. Настройки подключения HostGTA
        mysql_block = (
            f'#define MYSQL_HOST      "{db_host}"\n'
            f'#define MYSQL_USER      "{db_user}"\n'
            f'#define MYSQL_BASE      "{db_name}"\n'
            f'#define MYSQL_PASS      "{db_pass}"'
        )
        if re.search(r'#if\s+defined\s+LAN_MODE[\s\S]*?#endif', code):
            code = re.sub(r'#if\s+defined\s+LAN_MODE[\s\S]*?#endif', mysql_block, code)
            fixes.append("Обновлен блок MySQL под параметры HostGTA")
        elif re.search(r'#define\s+MYSQL_PASS', code):
            code = re.sub(r'#define\s+MYSQL_HOST\s+.*', f'#define MYSQL_HOST      "{db_host}"', code)
            code = re.sub(r'#define\s+MYSQL_USER\s+.*', f'#define MYSQL_USER      "{db_user}"', code)
            code = re.sub(r'#define\s+MYSQL_BASE\s+.*', f'#define MYSQL_BASE      "{db_name}"', code)
            code = re.sub(r'#define\s+MYSQL_PASS\s+.*', f'#define MYSQL_PASS      "{db_pass}"', code)
            fixes.append("Параметры MySQL приведены к заданным настройкам")

        # 3. Нормализация относительных путей
        before = code
        code = re.sub(r'#include\s+[<"](?:\.\.[/\\])*include[/\\](system[/\\][^>"]+)[>"]', r'#include <\1>', code)
        code = re.sub(r'#include\s+[<"](?:\.\.[/\\])*include[/\\]([^>"]+)[>"]', r'#include <\1>', code)
        code = re.sub(r'#include\s+[<"](?:\.\.[/\\])+([^>"]+)[>"]', r'#include <\1>', code)
        if code != before:
            fixes.append("Нормализованы пути `#include` (убраны `../`)")

        # 4. ЗАЩИТА ОТ КРАША 0000003A: гарантируем активный #pragma disablerecursion
        if re.search(r'//\s*#pragma\s+disablerecursion', code):
            code = re.sub(r'//\s*(#pragma\s+disablerecursion)[^\r\n]*', r'\1', code)
            fixes.append("Восстановлен `#pragma disablerecursion`")
        elif not re.search(r'#pragma\s+disablerecursion', code):
            code = "#pragma disablerecursion\n" + code
            fixes.append("Включен `#pragma disablerecursion` (защита от вылета памяти)")

        # 5. Точка входа main()
        if not re.search(r'\bmain\s*\(\s*\)', code):
            code += "\n\nmain() {}\n"
            fixes.append("Добавлена точка входа `main()`")

        with open(file_path, "w", encoding=enc, errors="replace") as f:
            f.write(code)

    except Exception as e:
        fixes.append(f"Предупреждение: {e}")

    return fixes


def save_compilation_record(source: str, input_file: str, target_file: str, status: str, returncode: int, elapsed: float, output: str, applied_fixes: list, user_id=None) -> dict:
    """Сохраняет отчет в историю компиляций бота."""
    global COMPILE_STATS
    rec_id = str(uuid.uuid4())[:6]
    rec = {
        "id": rec_id,
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "source": source,
        "input_file": input_file,
        "target_file": target_file,
        "status": status,
        "returncode": returncode,
        "elapsed": elapsed,
        "output": output,
        "applied_fixes": applied_fixes,
        "user_id": user_id
    }
    compilation_history.insert(0, rec)
    if len(compilation_history) > 50:
        compilation_history.pop()

    if user_id:
        user_logs[user_id] = rec

    COMPILE_STATS["total"] += 1
    if "Успешно" in status:
        COMPILE_STATS["success"] += 1
    else:
        COMPILE_STATS["failed"] += 1

    return rec


# ==============================================================================
#                  API ДЛЯ MINI APP (CORS + ЛИМИТ ДО 100 МБ)
# ==============================================================================

def cors_response(data, status=200):
    return web.json_response(
        data,
        status=status,
        headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
            "Access-Control-Allow-Headers": "*",
        }
    )

async def options_handler(request):
    return web.Response(
        status=200,
        headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
            "Access-Control-Allow-Headers": "*",
        }
    )

async def api_compile_handler(request):
    post_data = await request.post()
    upload_field = post_data.get("file")
    if not upload_field:
        return cors_response({"success": False, "log": "Файл не передан!"}, status=400)

    db_host = post_data.get("db_host", "127.0.0.1")
    db_user = post_data.get("db_user", "user909028")
    db_name = post_data.get("db_name", "user909028")
    db_pass = post_data.get("db_pass", "FpUjJoAu2gVD")
    user_id_raw = post_data.get("user_id")
    user_id = int(user_id_raw) if user_id_raw and str(user_id_raw).isdigit() else None

    filename = upload_field.filename
    file_bytes = upload_field.file.read()
    start_time = time.time()

    with tempfile.TemporaryDirectory() as tmpdir:
        input_path = os.path.join(tmpdir, filename)
        with open(input_path, "wb") as f:
            f.write(file_bytes)

        extract_dir = os.path.join(tmpdir, "extracted")
        all_subdirs = set()
        src_path = None

        if filename.lower().endswith(".zip"):
            os.makedirs(extract_dir, exist_ok=True)
            with zipfile.ZipFile(input_path, "r") as zf:
                zf.extractall(extract_dir)

            pwn_candidates = []
            for root, dirs, files in os.walk(extract_dir):
                all_subdirs.add(root)
                for f in files:
                    if f.lower().endswith(".pwn"):
                        full_pwn = os.path.join(root, f)
                        score = os.path.getsize(full_pwn)
                        if "gamemode" in full_pwn.lower():
                            score += 100_000_000
                        pwn_candidates.append((full_pwn, score))

            if not pwn_candidates:
                save_compilation_record("Web App 🌐", filename, "не найден", "Ошибка ❌", 1, 0.0, "В архиве нет файла .pwn", [], user_id)
                return cors_response({"success": False, "log": "В архиве нет файла .pwn!"})
            pwn_candidates.sort(key=lambda x: x[1], reverse=True)
            src_path = pwn_candidates[0][0]
        else:
            src_path = input_path
            all_subdirs.add(tmpdir)

        pwn_dir = os.path.dirname(src_path)

        # Копируем библиотеки к исходнику
        if filename.lower().endswith(".zip"):
            for root, _, files in os.walk(extract_dir):
                for f in files:
                    f_l = f.lower()
                    if (f_l.endswith(".inc") or f_l.endswith(".pwn")) and os.path.join(root, f) != src_path:
                        src_f = os.path.join(root, f)
                        flat_d = os.path.join(pwn_dir, f)
                        if not os.path.exists(flat_d):
                            try:
                                shutil.copy2(src_f, flat_d)
                            except Exception:
                                pass
                        if "system" in root.lower():
                            sys_d = os.path.join(pwn_dir, "system")
                            os.makedirs(sys_d, exist_ok=True)
                            sys_dest = os.path.join(sys_d, f)
                            if not os.path.exists(sys_dest):
                                try:
                                    shutil.copy2(src_f, sys_dest)
                                except Exception:
                                    pass

        # Добавляем защиту от зацикливания инклудов
        add_include_guards_to_files(extract_dir if filename.lower().endswith(".zip") else tmpdir, src_path)

        # Копируем базовые библиотеки в include
        sys_include = get_system_include()
        if os.path.exists(sys_include):
            local_sys = os.path.join(pwn_dir, "include")
            os.makedirs(local_sys, exist_ok=True)
            for sf in os.listdir(sys_include):
                s_path = os.path.join(sys_include, sf)
                if os.path.isfile(s_path):
                    try:
                        shutil.copy2(s_path, os.path.join(local_sys, sf))
                        shutil.copy2(s_path, os.path.join(pwn_dir, sf))
                    except Exception:
                        pass

        fixes = auto_repair_source_code(src_path, db_host, db_user, db_name, db_pass)
        base_name = os.path.splitext(os.path.basename(src_path))[0]
        src_filename = os.path.basename(src_path)
        out_amx = os.path.join(pwn_dir, f"{base_name}.amx")

        pawncc_path = get_pawncc_exe()

        include_args = ["-i.", "-iinclude", "-isystem"]
        for d in all_subdirs:
            try:
                rel = os.path.relpath(d, pwn_dir)
                if rel != "." and f"-i{rel}" not in include_args:
                    include_args.append(f"-i{rel}")
            except ValueError:
                pass

        # taskset -c 0,1 ограничивает процесс 2 ядрами (устраняет сбой ntdll 48 cores)
        # -O0 -d0 устраняют переполнение внутренних таблиц оптимизатора
        cmd = [
            "taskset",
            "-c",
            "0,1",
            "wine",
            pawncc_path,
            src_filename,
            f"-o{base_name}.amx",
            *include_args,
            "-O0",
            "-d0",
            "-;+",
            "-(+"
        ]

        wine_env = os.environ.copy()
        wine_env["WINEDEBUG"] = "-all"
        wine_env["WINEARCH"] = "win32"
        wine_env["WINEPREFIX"] = "/tmp/wine"
        wine_env["WINEDLLOVERRIDES"] = "winedbg.exe=d"
        wine_env["DISPLAY"] = ""
        wine_env["XDG_RUNTIME_DIR"] = "/tmp"
        wine_env["PATH"] = f"{os.path.dirname(pawncc_path)}:{wine_env.get('PATH', '')}"

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=pwn_dir,
                env=wine_env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=180.0)
        except Exception as e:
            err_msg = f"Ошибка вызова компилятора: {e}"
            save_compilation_record("Web App 🌐", filename, src_filename, "Ошибка ❌", -1, 0.0, err_msg, fixes, user_id)
            return cors_response({"success": False, "log": err_msg})

        elapsed = round(time.time() - start_time, 2)
        out_log = (safe_decode(stdout) + "\n" + safe_decode(stderr)).strip()
        is_success = os.path.exists(out_amx) and os.path.getsize(out_amx) > 0 and proc.returncode == 0

        # Сохранение в общую историю бота
        rec = save_compilation_record(
            "Web App 🌐",
            filename,
            src_filename,
            "Успешно ✅" if is_success else "Ошибка ❌",
            proc.returncode,
            elapsed,
            out_log,
            fixes,
            user_id
        )

        if is_success:
            file_id = rec["id"]
            amx_mb = round(os.path.getsize(out_amx) / (1024 * 1024), 2)
            zip_target = os.path.join(STORAGE_DIR, f"{file_id}.zip")

            with zipfile.ZipFile(zip_target, "w", zipfile.ZIP_DEFLATED) as z:
                z.write(out_amx, arcname=f"{base_name}.amx")
                z.write(out_amx, arcname=f"gamemodes/{base_name}.amx")

            return cors_response({
                "success": True,
                "returncode": proc.returncode,
                "elapsed": elapsed,
                "amx_size": amx_mb,
                "log": out_log,
                "fixes": fixes,
                "download_url": f"/api/download/{file_id}"
            })
        else:
            return cors_response({
                "success": False,
                "returncode": proc.returncode,
                "elapsed": elapsed,
                "log": out_log if out_log else f"Процесс завершился с кодом {proc.returncode}",
                "fixes": fixes
            })


async def api_download_handler(request):
    file_id = request.match_info.get("id")
    target_zip = os.path.join(STORAGE_DIR, f"{file_id}.zip")
    if os.path.exists(target_zip):
        return web.FileResponse(
            target_zip,
            headers={
                "Content-Disposition": 'attachment; filename="new_amx.zip"',
                "Access-Control-Allow-Origin": "*"
            }
        )
    return web.Response(text="Файл не найден", status=404)


async def start_web_server():
    app = web.Application(client_max_size=100 * 1024 * 1024)
    app.router.add_route("OPTIONS", "/{tail:.*}", options_handler)
    app.router.add_get("/", lambda r: web.HTTPFound("/webapp/index.html") if os.path.exists("webapp/index.html") else web.Response(text="Pawn Server Active", status=200))
    app.router.add_get("/health", lambda r: web.Response(text="OK", status=200))
    app.router.add_post("/api/compile", api_compile_handler)
    app.router.add_get("/api/download/{id}", api_download_handler)

    webapp_dir = os.path.abspath("webapp")
    if os.path.exists(webapp_dir):
        app.router.add_static("/webapp", path=webapp_dir, name="webapp")

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    print(f"✔ Web Server запущен на порту {PORT}")


# ==============================================================================
#                      КОМАНДЫ БОТА TELEGRAM
# ==============================================================================

@dp.message(CommandStart())
async def start_handler(message: Message):
    app_url = f"{WEBAPP_URL}/webapp/index.html"
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="⚡ Открыть Web-компилятор", web_app=WebAppInfo(url=app_url))]
        ],
        resize_keyboard=True
    )
    await message.answer(
        "👋 **Pawn Remote Compiler Bot**\n\n"
        "• Нажмите **«⚡ Открыть Web-компилятор»**, чтобы скомпилировать мод в Web App.\n"
        "• Либо отправьте архив `.zip` прямо сюда в чат.\n\n"
        "📖 Введите `/logs` — открыть всю историю компиляций (Web App + Telegram).",
        reply_markup=keyboard,
        parse_mode="Markdown"
    )


@dp.message(Command("help", "commands"))
async def help_handler(message: Message):
    help_text = (
        "📖 **Справка по командам компилятора:**\n\n"
        "• `/logs` — 📋 **Показать всю историю компиляций** (из Web App и Telegram)\n"
        "• `/log` — Открыть подробный лог последней сборки\n"
        "• `/log <номер>` — Открыть лог конкретной компиляции (например: `/log 1`)\n"
        "• `/status` — Состояние сервера, аптайм и число сборок\n"
        "• `/clean` — Очистить сохраненные архивы `.amx` на сервере\n"
        "• `/fixes` — Список автоматических исправлений кода мода"
    )
    await message.answer(help_text, parse_mode="Markdown")


@dp.message(Command("fixes"))
async def fixes_handler(message: Message):
    info = (
        "🛠 **Автоматические исправления Pawn:**\n\n"
        "1. Закрытие незакрытых кавычек в `#define`.\n"
        "2. Настройка базы HostGTA (`127.0.0.1`, `user909028`).\n"
        "3. Нормализация путей инклудов (устранение прыжков `../`).\n"
        "4. Включение `#pragma disablerecursion` (защита от вылета памяти).\n"
        "5. Защита от бесконечного зацикливания инклудов (`#endinput` guards).\n"
        "6. Добавление точки входа `main() {}` при её отсутствии."
    )
    await message.answer(info, parse_mode="Markdown")


@dp.message(Command("status"))
async def status_handler(message: Message):
    uptime_str = format_uptime(time.time() - BOT_START_TIME)
    pawncc_path = get_pawncc_exe()
    is_pawncc_ready = os.path.exists(pawncc_path)

    storage_size_bytes = 0
    storage_files_count = 0
    if os.path.exists(STORAGE_DIR):
        for f in os.listdir(STORAGE_DIR):
            fp = os.path.join(STORAGE_DIR, f)
            if os.path.isfile(fp):
                storage_size_bytes += os.path.getsize(fp)
                storage_files_count += 1
    storage_mb = round(storage_size_bytes / (1024 * 1024), 2)

    status_text = (
        "📊 **Системный статус компилятора:**\n\n"
        f"⏱ **Аптайм бота:** `{uptime_str}`\n"
        f"⚙️ **Windows Engine (Wine):** {'🟢 Готов к работе' if is_pawncc_ready else '🔴 Бинарник не найден'}\n"
        f"📍 **Путь к компилятору:** `{pawncc_path}`\n\n"
        f"📈 **Статистика компиляций:**\n"
        f"• Всего запросов: **{COMPILE_STATS['total']}**\n"
        f"• Успешно собрано: **{COMPILE_STATS['success']}** ✅\n"
        f"• Ошибок компиляции: **{COMPILE_STATS['failed']}** ❌\n\n"
        f"💽 **Временный кэш AMX:** {storage_files_count} файлов ({storage_mb} МБ)\n"
        f"🌐 **Web App URL:** `{WEBAPP_URL}`"
    )
    await message.answer(status_text, parse_mode="Markdown")


@dp.message(Command("clean"))
async def clean_handler(message: Message):
    deleted = 0
    freed_bytes = 0
    if os.path.exists(STORAGE_DIR):
        for f in os.listdir(STORAGE_DIR):
            fp = os.path.join(STORAGE_DIR, f)
            try:
                if os.path.isfile(fp):
                    freed_bytes += os.path.getsize(fp)
                    os.remove(fp)
                    deleted += 1
            except Exception:
                pass

    freed_mb = round(freed_bytes / (1024 * 1024), 2)
    await message.answer(
        f"🧹 **Очистка завершена!**\n"
        f"• Удалено временных файлов: **{deleted}**\n"
        f"• Освобождено памяти: **{freed_mb} МБ**",
        parse_mode="Markdown"
    )


# КОМАНДА /LOGS — ВЫВОДИТ ВСЮ ИСТОРИЮ ВСЕХ СБОРОК
@dp.message(Command("logs"))
async def logs_all_handler(message: Message):
    if not compilation_history:
        await message.reply(
            "ℹ️ История компиляций пока пуста.\n"
            "Запустите сборку в Web App или отправьте `.zip` архив в чат.",
            parse_mode="Markdown"
        )
        return

    lines = [f"📋 **История всех компиляций (всего: {len(compilation_history)}):**\n"]
    for i, item in enumerate(compilation_history[:15], 1):
        lines.append(
            f"**{i}.** `{item['time']}` | {item['source']}\n"
            f"• **Файл:** `{item['input_file']}` (`{item['target_file']}`)\n"
            f"• **Результат:** {item['status']} (Код: `{item['returncode']}`, `{item['elapsed']}s`)\n"
            f"• Посмотреть лог: `/log_{item['id']}` или `/log {i}`\n"
        )

    summary_text = "\n".join(lines)

    # Формируем общий текстовый файл со всеми логами всех сборок
    full_dump = []
    for i, item in enumerate(compilation_history, 1):
        full_dump.append(
            f"==================== СБОРКА #{i} [{item['id']}] ====================\n"
            f"Время: {item['time']} | Источник: {item['source']}\n"
            f"Входной файл: {item['input_file']} | Исходник: {item['target_file']}\n"
            f"Статус: {item['status']} | Код возврата: {item['returncode']} | Время: {item['elapsed']}s\n"
            f"Автоисправления: {', '.join(item.get('applied_fixes', []))}\n\n"
            f"--- ВЫВОД ПАВН-КОМПИЛЯТОРА ---\n"
            f"{item['output']}\n\n"
        )
    dump_bytes = "\n".join(full_dump).encode("utf-8")
    doc_file = BufferedInputFile(dump_bytes, filename="all_compilations_history.txt")

    if len(summary_text) <= 3500:
        await message.reply(summary_text, parse_mode="Markdown")
        await message.reply_document(doc_file, caption="📄 Полный лог всех сборок в одном файле")
    else:
        await message.reply_document(doc_file, caption=summary_text[:1000] + "\n\n...полный список в файле выше.")


# КОМАНДА /LOG — ВЫВОДИТ ПОСЛЕДНИЙ ЛОГ ИЛИ ЛОГ ПО НОМЕРУ / ID
@dp.message(Command("log"))
async def log_single_handler(message: Message):
    args = message.text.split()
    target_rec = None

    if len(args) > 1:
        param = args[1].replace("#", "").strip()
        if param.isdigit():
            idx = int(param) - 1
            if 0 <= idx < len(compilation_history):
                target_rec = compilation_history[idx]
        else:
            for item in compilation_history:
                if item["id"].lower() == param.lower():
                    target_rec = item
                    break

    # Если аргументов нет — берем последнюю сборку пользователя или самую последнюю в системе
    if not target_rec:
        user_id = message.from_user.id
        if user_id in user_logs:
            target_rec = user_logs[user_id]
        elif compilation_history:
            target_rec = compilation_history[0]

    if not target_rec:
        await message.reply(
            "ℹ️ В системе пока нет сохранённых логов компиляции.\n"
            "Запустите сборку в Web App или отправьте архив в чат.",
            parse_mode="Markdown"
        )
        return

    report_header = (
        f"📋 **Лог сборки [{target_rec['id']}]**\n"
        f"• **Источник:** {target_rec['source']}\n"
        f"• **Архив:** `{target_rec['input_file']}`\n"
        f"• **Файл:** `{target_rec['target_file']}`\n"
        f"• **Статус:** {target_rec['status']}\n"
        f"• **Код возврата:** `{target_rec['returncode']}`\n"
        f"• **Время выполнения:** `{target_rec['elapsed']} сек`\n"
    )

    fixes_str = "\n".join([f"• {x}" for x in target_rec.get("applied_fixes", [])])
    if not fixes_str:
        fixes_str = "Автоисправления не потребовались"

    full_log_text = (
        f"=== ДЕТАЛИ КОМПИЛЯЦИИ ===\n"
        f"ID: {target_rec['id']} | Время: {target_rec['time']}\n"
        f"Входной файл: {target_rec['input_file']}\n"
        f"Целевой исходник: {target_rec['target_file']}\n"
        f"Статус: {target_rec['status']}\n"
        f"Код завершения: {target_rec['returncode']}\n"
        f"Время выполнения: {target_rec['elapsed']}s\n\n"
        f"=== ПРИМЕНЕННЫЕ АВТОИСПРАВЛЕНИЯ ===\n"
        f"{fixes_str}\n\n"
        f"=== ВЫВОД ПАВН-КОМПИЛЯТОРА ===\n"
        f"{target_rec['output'] if target_rec['output'] else '(Вывод компилятора пуст)'}\n"
    )

    if len(full_log_text) <= 3000:
        await message.reply(f"{report_header}\n```\n{full_log_text}\n```", parse_mode="Markdown")
    else:
        file_data = full_log_text.encode("utf-8")
        doc_file = BufferedInputFile(file_data, filename=f"compile_log_{target_rec['target_file']}.txt")
        await message.reply_document(document=doc_file, caption=report_header, parse_mode="Markdown")


# ОБРАБОТКА КОМАНД ВИДА /log_a1b2c3
@dp.message(F.text.regexp(r"^/log_([a-zA-Z0-9]+)"))
async def log_by_hash_handler(message: Message):
    match = re.match(r"^/log_([a-zA-Z0-9]+)", message.text)
    if not match:
        return
    log_id = match.group(1).lower()
    target_rec = None
    for item in compilation_history:
        if item["id"].lower() == log_id:
            target_rec = item
            break

    if not target_rec:
        await message.reply("❌ Лог с таким идентификатором не найден.")
        return

    full_log_text = (
        f"=== ДЕТАЛИ КОМПИЛЯЦИИ [{target_rec['id']}] ===\n"
        f"Время: {target_rec['time']} | Источник: {target_rec['source']}\n"
        f"Входной файл: {target_rec['input_file']}\n"
        f"Целевой исходник: {target_rec['target_file']}\n"
        f"Статус: {target_rec['status']} (Код: {target_rec['returncode']})\n"
        f"Время выполнения: {target_rec['elapsed']}s\n\n"
        f"=== ВЫВОД ПАВН-КОМПИЛЯТОРА ===\n"
        f"{target_rec['output'] if target_rec['output'] else '(Вывод пуст)'}\n"
    )
    if len(full_log_text) <= 3000:
        await message.reply(f"```\n{full_log_text}\n```", parse_mode="Markdown")
    else:
        file_data = full_log_text.encode("utf-8")
        doc_file = BufferedInputFile(file_data, filename=f"compile_log_{target_rec['id']}.txt")
        await message.reply_document(document=doc_file, parse_mode="Markdown")


@dp.message(F.document)
async def handle_document(message: Message):
    doc = message.document
    raw_name = doc.file_name
    if not (raw_name.lower().endswith(".zip") or raw_name.lower().endswith(".pwn")):
        save_compilation_record("Telegram 💬", raw_name, "отклонен", "Ошибка ❌", -1, 0.0, "Неподдерживаемый формат файла", [], message.from_user.id)
        await message.reply("⚠ Пожалуйста, отправьте архив `.zip` или файл `.pwn`.")
        return

    status_msg = await message.reply("⏳ Загрузка и компиляция через Wine Windows Engine...")
    start_time = time.time()

    with tempfile.TemporaryDirectory() as tmpdir:
        clean_name = raw_name.replace(" ", "_")
        download_path = os.path.join(tmpdir, clean_name)
        tg_file = await bot.get_file(doc.file_id)
        await bot.download_file(tg_file.file_path, download_path)

        extract_dir = os.path.join(tmpdir, "extracted")
        all_subdirs = set()
        src_path = None

        if raw_name.lower().endswith(".zip"):
            os.makedirs(extract_dir, exist_ok=True)
            with zipfile.ZipFile(download_path, "r") as zf:
                zf.extractall(extract_dir)

            pwn_candidates = []
            for root, dirs, files in os.walk(extract_dir):
                all_subdirs.add(root)
                for f in files:
                    if f.lower().endswith(".pwn"):
                        full_pwn = os.path.join(root, f)
                        score = os.path.getsize(full_pwn)
                        if "gamemode" in full_pwn.lower():
                            score += 100_000_000
                        pwn_candidates.append((full_pwn, score))

            if not pwn_candidates:
                save_compilation_record("Telegram 💬", raw_name, "не найден", "Ошибка ❌", 1, 0.0, "В архиве нет файла .pwn", [], message.from_user.id)
                await status_msg.edit_text("❌ В архиве нет файла исходного кода `.pwn`.")
                return

            pwn_candidates.sort(key=lambda x: x[1], reverse=True)
            src_path = pwn_candidates[0][0]
        else:
            src_path = download_path
            all_subdirs.add(tmpdir)

        pwn_dir = os.path.dirname(src_path)

        if raw_name.lower().endswith(".zip"):
            for root, _, files in os.walk(extract_dir):
                for f in files:
                    f_l = f.lower()
                    if (f_l.endswith(".inc") or f_l.endswith(".pwn")) and os.path.join(root, f) != src_path:
                        src_f = os.path.join(root, f)
                        dest = os.path.join(pwn_dir, f)
                        if not os.path.exists(dest):
                            try:
                                shutil.copy2(src_f, dest)
                            except Exception:
                                pass
                        if "system" in root.lower():
                            sys_d = os.path.join(pwn_dir, "system")
                            os.makedirs(sys_d, exist_ok=True)
                            sys_dest = os.path.join(sys_d, f)
                            if not os.path.exists(sys_dest):
                                try:
                                    shutil.copy2(src_f, sys_dest)
                                except Exception:
                                    pass

        add_include_guards_to_files(extract_dir if raw_name.lower().endswith(".zip") else tmpdir, src_path)

        sys_include = get_system_include()
        if os.path.exists(sys_include):
            local_sys = os.path.join(pwn_dir, "include")
            os.makedirs(local_sys, exist_ok=True)
            for sf in os.listdir(sys_include):
                s_path = os.path.join(sys_include, sf)
                if os.path.isfile(s_path):
                    try:
                        shutil.copy2(s_path, os.path.join(local_sys, sf))
                        shutil.copy2(s_path, os.path.join(pwn_dir, sf))
                    except Exception:
                        pass

        fixes = auto_repair_source_code(src_path)
        base_name = os.path.splitext(os.path.basename(src_path))[0]
        src_filename = os.path.basename(src_path)
        out_amx = os.path.join(pwn_dir, f"{base_name}.amx")

        pawncc_path = get_pawncc_exe()

        include_args = ["-i.", "-iinclude", "-isystem"]
        for d in all_subdirs:
            try:
                rel = os.path.relpath(d, pwn_dir)
                if rel != "." and f"-i{rel}" not in include_args:
                    include_args.append(f"-i{rel}")
            except ValueError:
                pass

        cmd = [
            "taskset",
            "-c",
            "0,1",
            "wine",
            pawncc_path,
            src_filename,
            f"-o{base_name}.amx",
            *include_args,
            "-O0",
            "-d0",
            "-;+",
            "-(+"
        ]

        wine_env = os.environ.copy()
        wine_env["WINEDEBUG"] = "-all"
        wine_env["WINEARCH"] = "win32"
        wine_env["WINEPREFIX"] = "/tmp/wine"
        wine_env["WINEDLLOVERRIDES"] = "winedbg.exe=d"
        wine_env["DISPLAY"] = ""
        wine_env["XDG_RUNTIME_DIR"] = "/tmp"
        wine_env["PATH"] = f"{os.path.dirname(pawncc_path)}:{wine_env.get('PATH', '')}"

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=pwn_dir,
                env=wine_env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=180.0)
        except Exception as e:
            err_msg = f"Ошибка вызова компилятора: {e}"
            save_compilation_record("Telegram 💬", raw_name, src_filename, "Ошибка ❌", -1, 0.0, err_msg, fixes, message.from_user.id)
            await status_msg.edit_text(f"❌ {err_msg}")
            return

        elapsed = round(time.time() - start_time, 2)
        out_log = (safe_decode(stdout) + "\n" + safe_decode(stderr)).strip()
        is_success = os.path.exists(out_amx) and os.path.getsize(out_amx) > 0 and proc.returncode == 0

        save_compilation_record(
            "Telegram 💬",
            raw_name,
            src_filename,
            "Успешно ✅" if is_success else "Ошибка ❌",
            proc.returncode,
            elapsed,
            out_log,
            fixes,
            message.from_user.id
        )

        if is_success:
            await status_msg.delete()
            amx_mb = round(os.path.getsize(out_amx) / (1024 * 1024), 2)
            zip_out = os.path.join(tmpdir, f"{base_name}_amx.zip")
            with zipfile.ZipFile(zip_out, "w", zipfile.ZIP_DEFLATED) as z:
                z.write(out_amx, arcname=f"{base_name}.amx")
                z.write(out_amx, arcname=f"gamemodes/{base_name}.amx")

            fixes_info = "\n".join([f"• {x}" for x in fixes]) if fixes else "Без правок."
            caption = (
                f"✅ **Собрано через Windows Engine!** ({elapsed} сек)\n\n"
                f"📁 **Размер AMX:** {amx_mb} МБ\n"
                f"📦 **Внутри архива:** `{base_name}.amx` и `gamemodes/{base_name}.amx`\n\n"
                f"🛠 **Авто-исправления:**\n{fixes_info}\n\n"
                f"ℹ️ Для просмотра лога введите `/log` или `/logs`."
            )
            await message.reply_document(
                FSInputFile(zip_out, filename=f"{base_name}_amx.zip"),
                caption=caption,
                parse_mode="Markdown"
            )
        else:
            COMPILE_STATS["failed"] += 1
            err_box = out_log[:3200] if out_log else f"Процесс завершился с кодом {proc.returncode}"
            await status_msg.edit_text(
                f"❌ **Ошибка сборки (`{os.path.basename(src_path)}`):**\n"
                f"```\n{err_box}\n```\n\n"
                f"ℹ️ Для детального отчёта введите `/log`.",
                parse_mode="Markdown"
            ) 


# ==============================================================================
#                      ТОЧКА ВХОДА (WEB SERVER + BOT)
# ==============================================================================

async def main():
    await start_web_server()
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
            
