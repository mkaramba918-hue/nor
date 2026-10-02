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

# Системная статистика и хранилище логов
BOT_START_TIME = time.time()
COMPILE_STATS = {"total": 0, "success": 0, "failed": 0}
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
    """Преобразование секунд в читаемый формат."""
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
    global COMPILE_STATS
    COMPILE_STATS["total"] += 1

    post_data = await request.post()
    upload_field = post_data.get("file")
    if not upload_field:
        COMPILE_STATS["failed"] += 1
        return cors_response({"success": False, "log": "Файл не передан!"}, status=400)

    db_host = post_data.get("db_host", "127.0.0.1")
    db_user = post_data.get("db_user", "user909028")
    db_name = post_data.get("db_name", "user909028")
    db_pass = post_data.get("db_pass", "FpUjJoAu2gVD")

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
                COMPILE_STATS["failed"] += 1
                return cors_response({"success": False, "log": "В архиве нет файла .pwn!"})
            pwn_candidates.sort(key=lambda x: x[1], reverse=True)
            src_path = pwn_candidates[0][0]
        else:
            src_path = input_path
            all_subdirs.add(tmpdir)

        pwn_dir = os.path.dirname(src_path)

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

        cmd = [
            "wine",
            pawncc_path,
            src_filename,
            f"-o{base_name}.amx",
            *include_args,
            "-O1",
            "-d2"
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
            COMPILE_STATS["failed"] += 1
            return cors_response({"success": False, "log": f"Ошибка вызова компилятора: {e}"})

        elapsed = round(time.time() - start_time, 2)
        out_log = (safe_decode(stdout) + "\n" + safe_decode(stderr)).strip()
        is_success = os.path.exists(out_amx) and os.path.getsize(out_amx) > 0 and proc.returncode == 0

        if is_success:
            COMPILE_STATS["success"] += 1
            file_id = str(uuid.uuid4())[:8]
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
            COMPILE_STATS["failed"] += 1
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
        "Сборка модов SA-MP / CRMP (включая крупные проекты на 60 000+ строк) на официальном Windows-компиляторе через среду Wine.\n\n"
        "🔹 **Как скомпилировать:**\n"
        "• Нажмите **«⚡ Открыть Web-компилятор»** ниже (удобный интерфейс с логами).\n"
        "• Либо отправьте `.zip` архив или файл `.pwn` прямо в этот чат.\n\n"
        "💡 Введите `/help`, чтобы просмотреть список всех команд и инструкции.",
        reply_markup=keyboard,
        parse_mode="Markdown"
    )


@dp.message(Command("help", "commands"))
async def help_handler(message: Message):
    help_text = (
        "📖 **Справка по командам бота:**\n\n"
        "• `/start` — Показать стартовое меню и кнопку Web App\n"
        "• `/help` — Эта подробная инструкция\n"
        "• `/status` — Состояние сервера, аптайм, память и статистика сборок\n"
        "• `/log` (или `/logs`) — Лог последней компиляции вашего мода\n"
        "• `/clean` — Очистить временные AMX архивы на сервере\n"
        "• `/fixes` — Список автоматических исправлений кода\n\n"
        "📦 **Требования к архиву `.zip`:**\n"
        "1. Архив должен содержать файл исходника `.pwn` (например, `gamemodes/new.pwn`).\n"
        "2. Все кастомные инклуды (папки `include/`, `pawno/`, `system/`) должны лежать внутри архива.\n"
        "3. Бот сам найдет зависимости и свяжет пути поиска."
    )
    await message.answer(help_text, parse_mode="Markdown")


@dp.message(Command("fixes", "autorepair"))
async def fixes_handler(message: Message):
    fixes_info = (
        "🛠 **Автоматические исправления мода:**\n\n"
        "Бот перед запуском компилятора сам проверяет и устраняет частые ошибки:\n\n"
        "1. **Кавычки:** закрывает незакрытые кавычки в `#define` (из-за которых летит синтаксис).\n"
        "2. **MySQL HostGTA:** настраивает подключение на `127.0.0.1`, базу и пользователя `user909028`.\n"
        "3. **Пути инклудов:** переводит вызовы `../include/file.pwn` в безопасный формат `<file.pwn>`.\n"
        "4. **Защита памяти:** включает `#pragma disablerecursion` (предотвращает падение 32-битного компилятора по ошибке `0000003A`).\n"
        "5. **Точка входа:** автоматически дописывает `main() {}`, если она отсутствует."
    )
    await message.answer(fixes_info, parse_mode="Markdown")


@dp.message(Command("status", "info"))
async def status_handler(message: Message):
    uptime_str = format_uptime(time.time() - BOT_START_TIME)
    pawncc_path = get_pawncc_exe()
    is_pawncc_ready = os.path.exists(pawncc_path)

    # Подсчет размера временного хранилища
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


@dp.message(Command("clean", "clear"))
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
        f"• Удалено временных архивов: **{deleted}**\n"
        f"• Освобождено памяти: **{freed_mb} МБ**",
        parse_mode="Markdown"
    )


@dp.message(Command("log", "logs"))
async def log_handler(message: Message):
    user_id = message.from_user.id
    data = user_logs.get(user_id)

    if not data:
        await message.reply(
            "ℹ️ У вас пока нет сохранённых логов компиляции в текущей сессии.\n"
            "Отправьте архив мода или запустите сборку в Web App.",
            parse_mode="Markdown"
        )
        return

    report_header = (
        f"📋 **Лог последней сборки**\n"
        f"• **Архив:** `{data['input_file']}`\n"
        f"• **Файл:** `{data['target_file']}`\n"
        f"• **Статус:** {data['status']}\n"
        f"• **Код возврата:** `{data['returncode']}`\n"
        f"• **Время выполнения:** `{data['elapsed']} сек`\n"
    )

    fixes_str = "\n".join([f"• {x}" for x in data.get("applied_fixes", [])])
    if not fixes_str:
        fixes_str = "Автоисправления не потребовались"

    full_log_text = (
        f"=== ДЕТАЛИ КОМПИЛЯЦИИ ===\n"
        f"Входной файл: {data['input_file']}\n"
        f"Целевой исходник: {data['target_file']}\n"
        f"Команда: {data['command']}\n"
        f"Код завершения: {data['returncode']}\n"
        f"Время выполнения: {data['elapsed']}s\n\n"
        f"=== ПРИМЕНЕННЫЕ АВТОИСПРАВЛЕНИЯ ===\n"
        f"{fixes_str}\n\n"
        f"=== ВЫВОД ПАВН-КОМПИЛЯТОРА ===\n"
        f"{data['output'] if data['output'] else '(Вывод компилятора пуст)'}\n"
    )

    if len(full_log_text) <= 3000:
        await message.reply(
            f"{report_header}\n```\n{full_log_text}\n```",
            parse_mode="Markdown"
        )
    else:
        file_data = full_log_text.encode("utf-8")
        doc_file = BufferedInputFile(file_data, filename=f"compile_log_{data['target_file']}.txt")
        await message.reply_document(
            document=doc_file,
            caption=report_header,
            parse_mode="Markdown"
        )


@dp.message(F.document)
async def handle_document(message: Message):
    global COMPILE_STATS
    COMPILE_STATS["total"] += 1

    doc = message.document
    raw_name = doc.file_name
    if not (raw_name.lower().endswith(".zip") or raw_name.lower().endswith(".pwn")):
        COMPILE_STATS["failed"] += 1
        await message.reply("⚠️️ Пожалуйста, отправьте архив `.zip` или файл `.pwn`.")
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
                COMPILE_STATS["failed"] += 1
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
            "wine",
            pawncc_path,
            src_filename,
            f"-o{base_name}.amx",
            *include_args,
            "-O1",
            "-d2"
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
            COMPILE_STATS["failed"] += 1
            await status_msg.edit_text(f"❌ Ошибка вызова компилятора: {e}")
            return

        elapsed = round(time.time() - start_time, 2)
        out_log = (safe_decode(stdout) + "\n" + safe_decode(stderr)).strip()
        is_success = os.path.exists(out_amx) and os.path.getsize(out_amx) > 0 and proc.returncode == 0

        # Сохранение истории для команды /log
        user_logs[message.from_user.id] = {
            "input_file": raw_name,
            "target_file": os.path.basename(src_path),
            "command": " ".join(cmd),
            "returncode": proc.returncode,
            "output": out_log,
            "elapsed": elapsed,
            "status": "Успешно ✅" if is_success else "Ошибка ❌",
            "applied_fixes": fixes
        }

        if is_success:
            COMPILE_STATS["success"] += 1
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
                f"ℹ️ Для просмотра лога введите `/log`."
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
