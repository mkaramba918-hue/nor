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
user_logs: dict[int, dict] = {}


def get_pawncc_exe() -> str:
    """Гарантированный поиск бинарника pawncc.exe внутри контейнера."""
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
    """Гарантированный поиск папки системных инклудов."""
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
                fixes.append(f"Закрыта незакрытая кавычка: `{l.strip()[:35]}...`")
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
            fixes.append("Нормализованы пути `#include`: устранены прыжки `../`")

        # 4. Отключение падающей прагмы
        if re.search(r'#pragma\s+disablerecursion', code):
            code = re.sub(r'(#pragma\s+disablerecursion)', r'// \1 /* Отключено для защиты от сбоя */', code)
            fixes.append("Отключен `#pragma disablerecursion`")

        # 5. Точка входа main()
        if not re.search(r'\bmain\s*\(\s*\)', code):
            code += "\n\nmain() {}\n"
            fixes.append("Добавлена точка входа `main()`")

        if code.count("/*") > code.count("*/"):
            code += "\n*/\n" * (code.count("/*") - code.count("*/"))
            fixes.append("Закрыт незакрытый комментарий /* */")

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
    post_data = await request.post()
    upload_field = post_data.get("file")
    if not upload_field:
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
                return cors_response({"success": False, "log": "В архиве нет файла .pwn!"})
            pwn_candidates.sort(key=lambda x: x[1], reverse=True)
            src_path = pwn_candidates[0][0]
        else:
            src_path = input_path
            all_subdirs.add(tmpdir)

        pwn_dir = os.path.dirname(src_path)

        # Подтягиваем все .inc и вспомогательные .pwn к исходнику
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

        # Копируем системные инклуды в pwn_dir/include
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

        # Формируем относительные пути инклудов без слэшей в начале
        include_args = ["-i.", "-iinclude", "-isystem"]
        for d in all_subdirs:
            try:
                rel = os.path.relpath(d, pwn_dir)
                if rel != "." and f"-i{rel}" not in include_args:
                    include_args.append(f"-i{rel}")
            except ValueError:
                pass

        # Очищенные флаги компилятора (без -Z+ и без абсолютных путей)
        cmd = [
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
        wine_env["WINEPREFIX"] = "/root/.wine"
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
            return cors_response({"success": False, "log": f"Ошибка вызова компилятора: {e}"})

        elapsed = round(time.time() - start_time, 2)
        out_log = (safe_decode(stdout) + "\n" + safe_decode(stderr)).strip()
        is_success = os.path.exists(out_amx) and os.path.getsize(out_amx) > 0 and proc.returncode == 0

        if is_success:
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
#                      БОТ TELEGRAM
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
        "👋 **Pawn Compiler Bot (Windows Wine Engine)**\n\n"
        "• Нажмите **«⚡ Открыть Web-компилятор»**, чтобы запустить компиляцию в Web App.\n"
        "• Либо отправьте архив `.zip` прямо в этот чат.",
        reply_markup=keyboard,
        parse_mode="Markdown"
    )


@dp.message(F.document)
async def handle_document(message: Message):
    doc = message.document
    raw_name = doc.file_name
    if not (raw_name.lower().endswith(".zip") or raw_name.lower().endswith(".pwn")):
        await message.reply("⚠️ Отправьте архив `.zip` или файл `.pwn`.")
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
                await status_msg.edit_text("❌ В архиве нет файла `.pwn`.")
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
            "-O0",
            "-d0",
            "-;+",
            "-(+"
        ]

        wine_env = os.environ.copy()
        wine_env["WINEDEBUG"] = "-all"
        wine_env["WINEARCH"] = "win32"
        wine_env["WINEPREFIX"] = "/root/.wine"
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
            await status_msg.edit_text(f"❌ Ошибка вызова компилятора: {e}")
            return

        elapsed = round(time.time() - start_time, 2)
        out_log = (safe_decode(stdout) + "\n" + safe_decode(stderr)).strip()
        is_success = os.path.exists(out_amx) and os.path.getsize(out_amx) > 0 and proc.returncode == 0

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
                f"📁 **Размер:** {amx_mb} МБ\n"
                f"📦 **Внутри:** `{base_name}.amx` и `gamemodes/{base_name}.amx`\n\n"
                f"🛠 **Авто-исправления:**\n{fixes_info}"
            )
            await message.reply_document(FSInputFile(zip_out, filename=f"{base_name}_amx.zip"), caption=caption, parse_mode="Markdown")
        else:
            await status_msg.edit_text(f"❌ **Ошибка сборки (код: {proc.returncode}):**\n```\n{out_log[:3200]}\n```", parse_mode="Markdown")


async def main():
    await start_web_server()
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
