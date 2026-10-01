import asyncio
import os
import tempfile
import zipfile
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.types import Message, FSInputFile

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("Переменная окружения BOT_TOKEN не задана!")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# Путь к системным инклудам внутри контейнера (/app/include)
INCLUDE_DIR = os.path.abspath("include")


@dp.message(CommandStart())
async def start_handler(message: Message):
    await message.answer(
        "👋 Привет! Отправь мне `.pwn` файл или `.zip` архив с модом и инклудами, и я скомпилирую его в `.amx`."
    )


@dp.message(F.document)
async def handle_compilation(message: Message):
    doc = message.document
    file_name = doc.file_name.lower()

    if not (file_name.endswith(".pwn") or file_name.endswith(".zip")):
        await message.reply("⚠️ Пожалуйста, отправьте файл `.pwn` или архив `.zip`.")
        return

    status_msg = await message.reply("⏳ Файл получен. Идет подготовка и компиляция...")

    with tempfile.TemporaryDirectory() as tmpdir:
        download_path = os.path.join(tmpdir, doc.file_name)

        # 1. Скачиваем файл из Telegram
        try:
            tg_file = await bot.get_file(doc.file_id)
            await bot.download_file(tg_file.file_path, download_path)
        except Exception as e:
            await status_msg.edit_text(f"❌ Ошибка скачивания файла: {e}")
            return

        src_path = None
        custom_include_dir = None

        # 2. Обработка ZIP-архива
        if file_name.endswith(".zip"):
            extract_dir = os.path.join(tmpdir, "extracted")
            os.makedirs(extract_dir, exist_ok=True)

            try:
                with zipfile.ZipFile(download_path, "r") as zf:
                    zf.extractall(extract_dir)
            except Exception as e:
                await status_msg.edit_text(f"❌ Ошибка распаковки архива: {e}")
                return

            # Поиск .pwn файла и папки include внутри архива
            for root, dirs, files in os.walk(extract_dir):
                for d in dirs:
                    if d.lower() == "include":
                        custom_include_dir = os.path.join(root, d)
                for f in files:
                    if f.lower().endswith(".pwn") and not src_path:
                        src_path = os.path.join(root, f)

            if not src_path:
                await status_msg.edit_text("❌ В архиве не найден файл с расширением `.pwn`.")
                return

        # 3. Обработка одиночного .pwn
        else:
            src_path = download_path

        base_name = os.path.splitext(os.path.basename(src_path))[0]
        out_path = os.path.join(tmpdir, f"{base_name}.amx")
        pwn_dir = os.path.dirname(src_path)

        # 4. Формирование путей к инклудам (-i)
        include_args = [
            f"-i{pwn_dir}",
            f"-i{tmpdir}"
        ]
        if custom_include_dir and os.path.exists(custom_include_dir):
            include_args.append(f"-i{custom_include_dir}")
        if os.path.exists(INCLUDE_DIR):
            include_args.append(f"-i{INCLUDE_DIR}")

        # Команда вызова pawncc (установлен в /usr/local/bin)
        cmd = [
            "pawncc",
            src_path,
            f"-o{out_path}",
            *include_args,
            "-O1",
            "-d3",
            "-;+",
            "-(+"
        ]

        # 5. Запуск процесса компиляции
        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=90.0)
        except asyncio.TimeoutError:
            process.kill()
            await status_msg.edit_text("❌ Ошибка: Время ожидания компиляции превышено (90 сек).")
            return
        except Exception as e:
            await status_msg.edit_text(f"❌ Ошибка запуска компилятора: {e}")
            return

        # Декодирование вывода компилятора
        output_log = (
            stdout.decode("cp1251", errors="replace") + stderr.decode("cp1251", errors="replace")
        ).strip()

        # 6. Проверка результата: файл должен существовать, не быть нулевым (0 байт) и код возврата 0
        is_success = (
            os.path.exists(out_path)
            and os.path.getsize(out_path) > 0
            and process.returncode == 0
        )

        if is_success:
            await status_msg.delete()
            caption = "✅ Компиляция успешно завершена!"
            if "warning" in output_log.lower():
                caption += "\n\n⚠️ В коде присутствуют предупреждения компилятора."

            await message.reply_document(
                FSInputFile(out_path, filename=f"{base_name}.amx"),
                caption=caption
            )
        else:
            # Если pawncc упал с ошибкой и создал пустой файл 0 байт — удаляем его
            if os.path.exists(out_path):
                try:
                    os.remove(out_path)
                except OSError:
                    pass

            err_text = output_log[:3500] if output_log else "Неизвестная ошибка: файл .amx не был создан."
            await status_msg.edit_text(
                f"❌ **Ошибки компиляции:**\n```\n{err_text}\n```",
                parse_mode="Markdown"
            )


async def main():
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
    
