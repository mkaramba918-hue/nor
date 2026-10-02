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

# Снятие системного лимита стека операционной системы (защита от SIGSEGV / кода -11)
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

# Путь к системным инклудам внутри контейнера (/app/include)
INCLUDE_DIR = os.path.abspath("include")

# Словарь для хранения последних логов пользователей: user_id -> dict
user_logs: dict[int, dict] = {}


def safe_decode(b: bytes) -> str:
    """Безопасное декодирование вывода компилятора."""
    if not b:
        return ""
    for enc in ("utf-8", "cp1251", "cp866", "latin-1"):
        try:
            return b.decode(enc)
        except UnicodeDecodeError:
            continue
    return b.decode("utf-8", errors="replace")


@dp.message(CommandStart())
async def start_handler(message: Message):
    await message.answer(
        "👋 **Pawn Compiler Bot**\n\n"
        "Отправьте мне файл `.pwn` или архив `.zip` с модом и инклудами для компиляции.\n\n"
        "Доступные команды:\n"
        "• `/log` — получить подробный лог последней компиляции.",
        parse_mode="Markdown"
    )


@dp.message(Command("log", "logs"))
async def log_handler(message: Message):
    user_id = message.from_user.id
    data = user_logs.get(user_id)

    if not data:
        await message.reply(
            "ℹ️ У вас пока нет сохранённых логов компиляции.\n"
            "Отправьте файл `.pwn` или архив `.zip`, а затем вызовите `/log`.",
            parse_mode="Markdown"
        )
        return

    report_header = (
        f"📋 **Лог компиляции**\n"
        f"• **Архив/Файл:** `{data['input_file']}`\n"
        f"• **Скомпилирован:** `{data['target_file']}`\n"
        f"• **Статус:** {data['status']}\n"
        f"• **Код возврата:** `{data['returncode']}`\n"
        f"• **Время:** `{data['elapsed']} сек`\n"
    )

    full_log_text = (
        f"=== ДЕТАЛИ КОМПИЛЯЦИИ ===\n"
        f"Входной файл: {data['input_file']}\n"
        f"Целевой исходник: {data['target_file']}\n"
        f"Команда: {data['command']}\n"
        f"Код завершения: {data['returncode']}\n"
        f"Время выполнения: {data['elapsed']}s\n\n"
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
async def handle_compilation(message: Message):
    doc = message.document
    raw_file_name = doc.file_name
    file_name = raw_file_name.lower()

    if not (file_name.endswith(".pwn") or file_name.endswith(".zip")):
        await message.reply("⚠️ Пожалуйста, отправьте файл `.pwn` или архив `.zip`.")
        return

    status_msg = await message.reply("⏳ Файл получен. Идет распаковка и компиляция...")
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

        # 1. Распаковка архива
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
                        if "include" in pwn_lower or "map" in pwn_lower or "filterscripts" in pwn_lower:
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

        # 2. Автоматическое исправление синтаксиса и путей инклудов в исходнике
        try:
            with open(src_path, "rb") as f:
                raw_bytes = f.read()

            enc = "utf-8"
            for test_enc in ("utf-8", "cp1251", "latin-1"):
                try:
                    code_str = raw_bytes.decode(test_enc)
                    enc = test_enc
                    break
                except UnicodeDecodeError:
                    continue
            else:
                code_str = raw_bytes.decode("utf-8", errors="replace")

            # Закрываем незакрытые кавычки в настройках MySQL
            code_str = re.sub(r'(#define\s+MYSQL_PASS\s+"[^"\r\n]+)(\r?\n)', r'\1"\2', code_str)

            # Нормализуем относительные пути ../include/... -> <...>
            code_str = re.sub(r'#include\s+[<"]\.\./include/system/([^>"]+)[>"]', r'#include <system/\1>', code_str)
            code_str = re.sub(r'#include\s+[<"]\.\./include/([^>"]+)[>"]', r'#include <\1>', code_str)
            code_str = re.sub(r'#include\s+[<"]\.\.\\include\\([^>"]+)[>"]', r'#include <\1>', code_str)

            # Добавляем main() если отсутствует
            if not re.search(r'\bmain\s*\(\s*\)', code_str):
                code_str += "\n\nmain() {}\n"

            with open(src_path, "w", encoding=enc, errors="replace") as f:
                f.write(code_str)
        except Exception:
            pass

        # 3. Создание символических ссылок для разрешения ../include
        parent_dir = os.path.dirname(pwn_dir)
        for inc_d in list(found_include_dirs):
            try:
                os.symlink(inc_d, os.path.join(parent_dir, "include"))
            except OSError:
                pass
            try:
                os.symlink(inc_d, os.path.join(tmpdir, "include"))
            except OSError:
                pass

        base_name = os.path.splitext(os.path.basename(src_path))[0]
        out_path = os.path.join(tmpdir, f"{base_name}.amx")

        # 4. Сбор аргументов инклудов
        include_args = [
            f"-i{pwn_dir}",
            f"-i{extract_dir}" if file_name.endswith(".zip") else f"-i{tmpdir}"
        ]
        for inc_dir in sorted(found_include_dirs):
            include_args.append(f"-i{inc_dir}")
        if os.path.exists(INCLUDE_DIR):
            include_args.append(f"-i{INCLUDE_DIR}")

        # 5. Безопасные флаги: -O0 (без вылета оптимизатора), -d0 (без переполнения стека), -v2 (подробный лог)
        cmd = [
            "pawncc",
            src_path,
            f"-o{out_path}",
            *include_args,
            "-O0",
            "-d0",
            "-;+",
            "-(+",
            "-v2"
        ]

        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=pwn_dir,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                preexec_fn=set_unlimited_stack
            )
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=180.0)
        except asyncio.TimeoutError:
            process.kill()
            await status_msg.edit_text("❌ Ошибка: Время ожидания компиляции превышено (180 сек).")
            return
        except Exception as e:
            await status_msg.edit_text(f"❌ Ошибка вызова компилятора: {e}")
            return

        elapsed = round(time.time() - start_time, 2)
        out_text = safe_decode(stdout).strip()
        err_text = safe_decode(stderr).strip()
        output_log = (out_text + "\n" + err_text).strip()

        is_success = (
            os.path.exists(out_path)
            and os.path.getsize(out_path) > 0
            and process.returncode == 0
        )

        user_logs[message.from_user.id] = {
            "input_file": raw_file_name,
            "target_file": os.path.basename(src_path),
            "command": " ".join(cmd),
            "returncode": process.returncode,
            "output": output_log,
            "elapsed": elapsed,
            "status": "Успешно ✅" if is_success else "Ошибка ❌",
        }

        if is_success:
            await status_msg.delete()
            caption = f"✅ Компиляция успешно завершена ({elapsed} сек)!\nИсходник: `{os.path.basename(src_path)}`"
            if "warning" in output_log.lower():
                caption += "\n\n⚠️ Присутствуют предупреждения компилятора (введите `/log` для просмотра)."

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

            detail = output_log if output_log else f"Процесс завершился с кодом {process.returncode} (вывод пуст)."
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
    
