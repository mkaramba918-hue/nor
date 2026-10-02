FROM python:3.10-slim

# Установка 32-битной архитектуры, Wine и утилит
RUN dpkg --add-architecture i386 && \
    apt-get update && \
    apt-get install -y --no-install-recommends \
        wine32 \
        wine \
        curl \
        unzip && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Скачивание и установка Windows-версии компилятора Pawn
RUN mkdir -p /app/compiler && \
    curl -fsSL -o /tmp/pawnc_win.zip https://github.com/pawn-lang/compiler/releases/download/v3.10.10/pawnc-3.10.10-windows.zip && \
    unzip -qo /tmp/pawnc_win.zip -d /app/compiler/ && \
    rm -rf /tmp/pawnc_win.zip

# Инициализация Wine без вывода лишних логов
ENV WINEDEBUG=-all
ENV WINEARCH=win32
RUN wineboot --init > /dev/null 2>&1 || true

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD sh -c "ulimit -s unlimited && python bot.py"
