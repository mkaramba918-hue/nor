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

# Путь к системным инклудам бота на сервере
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
        
        # Скачиваем файл из Telegram
        tg_file = await bot.get_file(doc.file_id)
        await bot.download_file(tg_file.file_path, download_path)

        src_path = None
        custom_include_dir = None

        # 1. Если прислан ZIP-архив
        if file_name.endswith(".zip"):
            extract_dir = os.path.join(tmpdir, "extracted")
            os.makedirs(extract_dir, exist_ok=True)
            
            try:
                with zipfile.ZipFile(download_path, 'r') as zf:
                    zf.extractall(extract_dir)
            except Exception as e:
                await status_msg.edit_text(f"❌ Ошибка повреждения архива: {e}")
                return

            # Ищем .pwn файл и папку include внутри архива
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

        # 2. Если прислан чистый .pwn
        else:
            src_path = download_path

        base_name = os.path.splitext(os.path.basename(src_path))[0]
        out_path = os.path.join(tmpdir, f"{base_name}.amx")
        pwn_dir = os.path.dirname(src_path)

        # Формируем флаги путей поиска инклудов (-i)
        include_args = [
            f"-i{pwn_dir}",
            f"-i{tmpdir}"
        ]
        if custom_include_dir and os.path.exists(custom_include_dir):
            include_args.append(f"-i{custom_include_dir}")
        if os.path.exists(INCLUDE_DIR):
            include_args.append(f"-i{INCLUDE_DIR}")

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

        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            # Для тяжелых модов даем до 90 секунд
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=90.0)
        except asyncio.TimeoutError:
            process.kill()
            await status_msg.edit_text("❌ Ошибка: Время ожидания компиляции превышено (90 сек).")
            return
        except Exception as e:
            await status_msg.edit_text(f"❌ Ошибка запуска компилятора: {e}")
            return

        output_log = (stdout.decode("cp1251", errors="replace") + stderr.decode("cp1251", errors="replace")).strip()

        # Проверяем результат
        if os.path.exists(out_path):
            await status_msg.delete()
            caption = "✅ Компиляция успешно завершена!"
            if "warning" in output_log.lower():
                caption += "\n\n⚠️ В коде присутствуют предупреждения компилятора."

            await message.reply_document(
                FSInputFile(out_path, filename=f"{base_name}.amx"),
                caption=caption
            )
        else:
            err_text = output_log[:3500] if output_log else "Неизвестная ошибка компилятора."
            await status_msg.edit_text(
                f"❌ **Ошибки компиляции:**\n```\n{err_text}\n```",
                parse_mode="Markdown"
            )

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
