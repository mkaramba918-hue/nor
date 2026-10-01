import asyncio
import os
import tempfile
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, FSInputFile

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("Переменная окружения BOT_TOKEN не задана!")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

@dp.message(F.document)
async def handle_compilation(message: Message):
    doc = message.document
    
    # Проверка допустимых расширений
    if not doc.file_name.endswith((".cpp", ".c")):
        await message.reply("Отправьте исходный файл с расширением .cpp или .c")
        return

    status_msg = await message.reply("Файл получен. Выполняется компиляция...")

    with tempfile.TemporaryDirectory() as tmpdir:
        src_path = os.path.join(tmpdir, doc.file_name)
        out_path = os.path.join(tmpdir, "output.bin")

        # Скачивание файла от пользователя
        tg_file = await bot.get_file(doc.file_id)
        await bot.download_file(tg_file.file_path, src_path)

        compiler = "g++" if doc.file_name.endswith(".cpp") else "gcc"
        cmd = [compiler, src_path, "-O2", "-o", out_path]

        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )

        try:
            # Ограничение по времени выполнения — 15 секунд
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=15.0)
        except asyncio.TimeoutError:
            process.kill()
            await status_msg.edit_text("Ошибка: время ожидания компиляции превышено (15 сек).")
            return

        if process.returncode != 0:
            err_output = stderr.decode("utf-8", errors="replace")[:1000]
            await status_msg.edit_text(f"Ошибка компиляции:\n```\n{err_output}\n```", parse_mode="Markdown")
            return

        # Отправка скомпилированного бинарника
        await status_msg.delete()
        await message.reply_document(
            FSInputFile(out_path, filename="compiled_program"),
            caption="Компиляция успешно завершена."
        )

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
