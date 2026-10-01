import asyncio
import os
import tempfile
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.types import Message, FSInputFile

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("Переменная окружения BOT_TOKEN не задана!")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# Путь к системным инклудам внутри контейнера
INCLUDE_DIR = os.path.abspath("include")

@dp.message(CommandStart())
async def start_handler(message: Message):
    await message.answer(
        "👋 Привет! Отправь мне мод или скрипт в формате `.pwn`, и я скомпилирую его в `.amx`."
    )

@dp.message(F.document)
async def handle_compilation(message: Message):
    doc = message.document
    
    if not doc.file_name.endswith(".pwn"):
        await message.reply("⚠️ Пожалуйста, отправьте файл исходного кода с расширением `.pwn`")
        return

    status_msg = await message.reply("⏳ Файл получен. Идет компиляция мода...")

    with tempfile.TemporaryDirectory() as tmpdir:
        src_path = os.path.join(tmpdir, doc.file_name)
        base_name = os.path.splitext(doc.file_name)[0]
        out_path = os.path.join(tmpdir, f"{base_name}.amx")

        # Скачиваем файл из Telegram
        tg_file = await bot.get_file(doc.file_id)
        await bot.download_file(tg_file.file_path, src_path)

        # Команда запуска pawncc
        # -i задает путь к инклудам, -O1 базовую оптимизацию, -d3 отладочную инфу
        cmd = [
            "pawncc",
            src_path,
            f"-o{out_path}",
            f"-i{INCLUDE_DIR}",
            f"-i{tmpdir}",
            "-O1",
            "-d3",
            "-;+",
            "-(+"
        ]

        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )

        try:
            # Для тяжелых модов даем до 40 секунд на сборку
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=40.0)
        except asyncio.TimeoutError:
            process.kill()
            await status_msg.edit_text("❌ Ошибка: Время ожидания компиляции превышено (40 сек).")
            return

        output_log = (stdout.decode("cp1251", errors="replace") + stderr.decode("cp1251", errors="replace")).strip()

        # Проверяем, создался ли .amx файл
        if os.path.exists(out_path):
            await status_msg.delete()
            caption = "✅ Компиляция успешно завершена!"
            if "warning" in output_log.lower():
                caption += "\n\n⚠️ Предупреждения компилятора присутствуют в коде."

            await message.reply_document(
                FSInputFile(out_path, filename=f"{base_name}.amx"),
                caption=caption
            )
        else:
            # Обрезаем вывод логов, если он слишком большой для Telegram
            err_text = output_log[:3500] if output_log else "Неизвестная ошибка компилятора."
            await status_msg.edit_text(
                f"❌ **Ошибки компиляции:**\n```\n{err_text}\n```",
                parse_mode="Markdown"
            )

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
    
