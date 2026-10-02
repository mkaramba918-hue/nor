import asyncio
import os
import re
import resource
import shutil
import tempfile
import time
import zipfile
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, FSInputFile, BufferedInputFile

# 1. Снятие системного лимита стека операционной системы (защита от SIGSEGV -11)
def set_unlimited_stack():
    try:
        resource.setrlimit(resource.RLIMIT_STACK, (resource.RLIM_INFINITY, resource.RLIM_INFINITY))
    except Exception:
        try:
            _, hard = resource.getrlimit(resource.RLIMIT_STACK)
            resource.setrlimit(resource.RLIMIT_STACK, (hard, hard))
        except Exception:
            pass

set_unlimited_stack()

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("Переменная окружения BOT_TOKEN не задана!")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

INCLUDE_DIR = os.path.abspath("include")
user_logs: dict[int, dict] = {}


def safe_decode(b: bytes) -> str:
    """Декодирование вывода компилятора с автоопределением кодировки."""
    if not b:
        return ""
    for enc in ("utf-8", "cp1251", "cp866", "latin-1"):
        try:
            return b.decode(enc)
        except UnicodeDecodeError:
            continue
    return b.decode("utf-8", errors="replace")


def auto_repair_source_code(file_path: str) -> list[str]:
    """Автоматическое обнаружение и исправление синтаксических ошибок в .pwn."""
    fixes = []
    try:
        with open(file_path, "rb") as f:
            raw_bytes = f.read()

        enc = "utf-8"
        for test_enc in ("utf-8", "cp1251", "latin-1"):
            try:
                code = raw_bytes.decode(test_enc)
                enc = test_enc
                break
            except UnicodeDecodeError:
                continue
        else:
            code = raw_bytes.decode("utf-8", errors="replace")

        # 1. Автозакрытие незакрытых кавычек в строках #define
        def fix_quotes(match):
            line = match.group(0)
            if line.count('"') % 2 != 0:
                fixes.append(f"Закрыта незакрытая кавычка в строке настроек: `{line.strip()[:35]}...`")
                return line + '"'
            return line

        code = re.sub(r'^#define\s+.*', fix_quotes, code, flags=re.MULTILINE)

        # 2. Исправление настроек MySQL для HostGTA
        mysql_block = (
            '#define MYSQL_HOST      "127.0.0.1"\n'
            '#define MYSQL_USER      "user909028"\n'
            '#define MYSQL_BASE      "user909028"\n'
            '#define MYSQL_PASS      "FpUjJoAu2gVD"'
        )
        if re.search(r'#if\s+defined\s+LAN_MODE[\s\S]*?#endif', code):
            code = re.sub(r'#if\s+defined\s+LAN_MODE[\s\S]*?#endif', mysql_block, code)
            fixes.append("Исправлен блок MySQL (заданы реквизиты HostGTA и убран поврежденный LAN_MODE)")
        elif re.search(r'#define\s+MYSQL_PASS', code):
            code = re.sub(r'#define\s+MYSQL_HOST\s+.*', '#define MYSQL_HOST      "127.0.0.1"', code)
            code = re.sub(r'#define\s+MYSQL_USER\s+.*', '#define MYSQL_USER      "user909028"', code)
            code = re.sub(r'#define\s+MYSQL_BASE\s+.*', '#define MYSQL_BASE      "user909028"', code)
            code = re.sub(r'#define\s+MYSQL_PASS\s+.*', '#define MYSQL_PASS      "FpUjJoAu2gVD"', code)
            fixes.append("Обновлены параметры подключения к базе данных MySQL")

        # 3. Нормализация относительных путей инклудов (устранение прыжков ../)
        before_inc = code
        code = re.sub(r'#include\s+[<"]\.\.[/\\]include[/\\]system[/\\]([^>"]+)[>"]', r'#include <system/\1>', code)
        code = re.sub(r'#include\s+[<"]\.\.[/\\]include[/\\]([^>"]+)[>"]', r'#include <\1>', code)
        code = re.sub(r'#include\s+[<"]\.\.[/\\]([^>"]+)[>"]', r'#include <\1>', code)
        code = re.sub(r'#include\s+<system/([^>"]+)>', r'#include <\1>', code)  # дублирование
        if code != before_inc:
            fixes.append("Нормализованы пути инклудов: вызовы `../include/` переведены в `<...>`")

        # 4. Отключение директив, вызывающих Segfault -11 на Pawncc 3.10
        if re.search(r'#pragma\s+disablerecursion', code):
            code = re.sub(r'(#pragma\s+disablerecursion)', r'// \1 /* Отключено ботом: вызывает Segfault -11 */', code)
            fixes.append("Закомментирован `#pragma disablerecursion` (предотвращен сбой памяти компилятора)")

        # 5. Проверка точки входа
        if not re.search(r'\bmain\s*\(\s*\)', code):
            code += "\n\nmain() {}\n"
            fixes.append("Добавлена обязательная точка входа `main() {}`")

        # 6. Проверка баланса многострочных комментариев
        if code.count("/*") > code.count("*/"):
            code += "\n*/\n" * (code.count("/*") - code.count("*/"))
            fixes.append("Закрыт оборванный многострочный комментарий `/* ... */`")

        with open(file_path, "w", encoding=enc, errors="replace") as f:
            f.write(code)

    except Exception as e:
        fixes.append(f"Предупреждение анализатора: {e}")

    return fixes


@dp.message(CommandStart())
async def start_handler(message: Message):
    await message.answer(
        "👋 **Pawn Compiler Bot с Авто-Исправлением**\n\n"
        "Отправьте мне архив `.zip` с модом или файл `.pwn`.\n"
        "Бот самостоятельно исправит ошибки синтаксиса, пропишет базу данных и скомпилирует `.amx`.\n\n"
        "• `/log` — посмотреть подробный лог сборки.",
        parse_mode="Markdown"
    )


@dp.message(Command("log", "logs"))
async def log_handler(message: Message):
    user_id = message.from_user.id
    data = user_logs.get(user_id)

    if not data:
        await message.reply("ℹ️ У вас пока нет сохранённых логов компиляции.")
        return

    report_header = (
        f"📋 **Лог компиляции**\n"
        f"• **Файл:** `{data['input_file']}`\n"
        f"• **Исходник:** `{data['target_file']}`\n"
        f"• **Статус:** {data['status']}\n"
        f"• **Код:** `{data['returncode']}`\n"
        f"• **Время:** `{data['elapsed']} сек`\n"
    )

    full_log_text = (
        f"=== ДЕТАЛИ КОМПИЛЯЦИИ ===\n"
        f"Входной файл: {data['input_file']}\n"
        f"Целевой исходник: {data['target_file']}\n"
        f"Команда: {data['command']}\n"
        f"Код завершения: {data['returncode']}\n"
        f"Время выполнения: {data['elapsed']}s\n\n"
        f"=== ПРИМЕНЕННЫЕ АВТОИСПРАВЛЕНИЯ ===\n"
        f"{chr(10).join(data.get('applied_fixes', [])) if data.get('applied_fixes') else 'Ошибок не обнаружено'}\n\n"
        f"=== ВЫВОД ПАВН-КОМПИЛЯТОРА ===\n"
        f"{data['output'] if data['output'] else '(Вывод компилятора пуст)'}\n"
    )

    if len(full_log_text) <= 3000:
        await message.reply(f"{report_header}\n```\n{full_log_text}\n```", parse_mode="Markdown")
    else:
        file_data = full_log_text.encode("utf-8")
        doc_file = BufferedInputFile(file_data, filename=f"compile_log_{data['target_file']}.txt")
        await message.reply_document(document=doc_file, caption=report_header, parse_mode="Markdown")


@dp.message(F.document)
async def handle_compilation(message: Message):
    doc = message.document
    raw_file_name = doc.file_name
    file_name = raw_file_name.lower()

    if not (file_name.endswith(".pwn") or file_name.endswith(".zip")):
        await message.reply("⚠️ Пожалуйста, отправьте файл `.pwn` или архив `.zip`.")
        return

    status_msg = await message.reply("⏳ Загрузка файла и первичный анализ...")
    start_time = time.time()

    with tempfile.TemporaryDirectory() as tmpdir:
        clean_file_name = raw_file_name.replace(" ", "_")
        download_path = os.path.join(tmpdir, clean_file_name)

        try:
            tg_file = await bot.get_file(doc.file_id)
            await bot.download_file(tg_file.file_path, download_path)
        except Exception as e:
            await status_msg.edit_text(f"❌ Ошибка скачивания файла: {e}")
            return

        src_path = None
        found_include_dirs = set()
        extract_dir = os.path.join(tmpdir, "extracted")

        if file_name.endswith(".zip"):
            os.makedirs(extract_dir, exist_ok=True)
            try:
                with zipfile.ZipFile(download_path, "r") as zf:
                    zf.extractall(extract_dir)
            except Exception as e:
                await status_msg.edit_text(f"❌ Ошибка распаковки архива: {e}")
                return

            pwn_candidates = []
            for root, dirs, files in os.walk(extract_dir):
                r_lower = root.lower()
                for d in dirs:
                    d_lower = d.lower()
                    if d_lower in ("include", "pawno", "map", "filterscripts"):
                        found_include_dirs.add(os.path.join(root, d))
                    if d_lower == "include" and "pawno" in r_lower:
                        found_include_dirs.add(os.path.join(root, d))

                for f in files:
                    if f.lower().endswith(".pwn"):
                        full_pwn_path = os.path.join(root, f)
                        try:
                            f_size = os.path.getsize(full_pwn_path)
                        except OSError:
                            f_size = 0

                        score = f_size
                        pwn_lower = full_pwn_path.lower()
                        if "gamemode" in pwn_lower:
                            score += 100_000_000
                        if f.lower() in ("new.pwn", "main.pwn", "mode.pwn"):
                            score += 50_000_000
                        if "include" in pwn_lower or "map" in pwn_lower:
                            score -= 10_000_000

                        pwn_candidates.append((full_pwn_path, score))

            if not pwn_candidates:
                await status_msg.edit_text("❌ В архиве не найден файл исходного кода `.pwn`.")
                return

            pwn_candidates.sort(key=lambda x: x[1], reverse=True)
            src_path = pwn_candidates[0][0]
        else:
            src_path = download_path

        pwn_dir = os.path.dirname(src_path)

        # 1. Первичный авторемонт кода
        await status_msg.edit_text("🛠 Поиск и автоматическое исправление синтаксических ошибок...")
        applied_fixes = auto_repair_source_code(src_path)

        # 2. Символические ссылки для разрешения нестандартных путей
        parent_dir = os.path.dirname(pwn_dir)
        for inc_d in list(found_include_dirs):
            for target_link in (os.path.join(parent_dir, "include"), os.path.join(tmpdir, "include"), "/tmp/include"):
                try:
                    os.symlink(inc_d, target_link)
                except OSError:
                    pass

        base_name = os.path.splitext(os.path.basename(src_path))[0]
        out_path = os.path.join(tmpdir, f"{base_name}.amx")

        include_args = [
            f"-i{pwn_dir}",
            f"-i{extract_dir}" if file_name.endswith(".zip") else f"-i{tmpdir}"
        ]
        for inc_dir in sorted(found_include_dirs):
            include_args.append(f"-i{inc_dir}")
        if os.path.exists(INCLUDE_DIR):
            include_args.append(f"-i{INCLUDE_DIR}")

        # Функция запуска компилятора
        async def execute_pawn(flags: list[str]):
            cmd = ["pawncc", src_path, f"-o{out_path}", *include_args, *flags]
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=pwn_dir,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                preexec_fn=set_unlimited_stack
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=180.0)
            return proc.returncode, safe_decode(stdout).strip() + "\n" + safe_decode(stderr).strip(), cmd

        # --- ПОПЫТКА 1: Стандартная сборка ---
        await status_msg.edit_text("⚙️ Запуск компиляции...")
        flags = ["-O0", "-d0", "-;+", "-(+"]
        returncode, output_log, last_cmd = await execute_pawn(flags)

        # --- ПОПЫТКА 2 (Если произошла ошибка): Вторичный интеллектуальный ремонт ---
        if returncode != 0 or not os.path.exists(out_path) or os.path.getsize(out_path) == 0:
            output_log_clean = output_log.strip()

            # Реакция на "cannot read from file: X" -> ищем файл и копируем в инклуды
            missing_files = re.findall(r'cannot read from file:\s*"([^"]+)"', output_log_clean)
            for mf in missing_files:
                mf_name = os.path.basename(mf)
                for r, _, fs in os.walk(extract_dir):
                    if mf_name in fs:
                        src_find = os.path.join(r, mf_name)
                        shutil.copy2(src_find, os.path.join(pwn_dir, mf_name))
                        applied_fixes.append(f"Найден и подключен отсутствующий файл: `{mf_name}`")

            # Повторный запуск с флагом увеличенного лимита строк
            await status_msg.edit_text("🔄 Ошибка устранена, выполняется повторная компиляция...")
            flags = ["-O0", "-d0", "-Z+", "-;+"]
            returncode, output_log, last_cmd = await execute_pawn(flags)

        elapsed = round(time.time() - start_time, 2)
        is_success = (
            os.path.exists(out_path)
            and os.path.getsize(out_path) > 0
            and returncode == 0
        )

        user_logs[message.from_user.id] = {
            "input_file": raw_file_name,
            "target_file": os.path.basename(src_path),
            "command": " ".join(last_cmd),
            "returncode": returncode,
            "output": output_log.strip(),
            "elapsed": elapsed,
            "status": "Успешно ✅" if is_success else "Ошибка ❌",
            "applied_fixes": applied_fixes
        }

        if is_success:
            await status_msg.delete()
            amx_size = round(os.path.getsize(out_path) / (1024 * 1024), 2)
            fixes_text = "\n".join([f"• {f}" for f in applied_fixes]) if applied_fixes else "Файл не потребовал исправлений."

            caption = (
                f"✅ **Мод успешно скомпилирован!** ({elapsed} сек)\n\n"
                f"📁 **Размер AMX:** {amx_size} МБ\n"
                f"🛠 **Автоматические исправления:**\n{fixes_text}"
            )

            await message.reply_document(
                FSInputFile(out_path, filename=f"{base_name}.amx"),
                caption=caption,
                parse_mode="Markdown"
            )
        else:
            if os.path.exists(out_path):
                try:
                    os.remove(out_path)
                except OSError:
                    pass

            detail = output_log.strip() if output_log.strip() else f"Процесс завершился с кодом {returncode} (вывод пуст)."
            err_box = detail[:3200]
            await status_msg.edit_text(
                f"❌ **Ошибки компиляции (`{os.path.basename(src_path)}`):**\n"
                f"```\n{err_box}\n```\n"
                f"ℹ️ Для полного отчёта отправьте команду `/log`.",
                parse_mode="Markdown"
            )


async def main():
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
    
