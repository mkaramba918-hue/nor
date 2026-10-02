FROM python:3.10-slim

# 1. Установка архитектуры i386, Wine и системных утилит
RUN dpkg --add-architecture i386 && \
    apt-get update && \
    apt-get install -y --no-install-recommends \
        wine32 \
        wine \
        curl \
        unzip && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 2. Настройка окружения для Wine без предупреждений
ENV WINEDEBUG=-all
ENV WINEARCH=win32
ENV WINEPREFIX=/root/.wine
ENV XDG_RUNTIME_DIR=/tmp

# 3. Скачивание Windows-компилятора Pawn v3.10.10 с GitHub
RUN mkdir -p /tmp/pawnc_dl /app/compiler/bin /app/compiler/include && \
    curl -fsSL -o /tmp/pawnc_win.zip https://github.com/pawn-lang/compiler/releases/download/v3.10.10/pawnc-3.10.10-windows.zip && \
    unzip -qo /tmp/pawnc_win.zip -d /tmp/pawnc_dl/ && \
    find /tmp/pawnc_dl -name "pawncc.exe" -exec cp {} /app/compiler/bin/ \; && \
    find /tmp/pawnc_dl -name "*.dll" -exec cp {} /app/compiler/bin/ \; && \
    cp /app/compiler/bin/* /app/compiler/ 2>/dev/null || true && \
    find /tmp/pawnc_dl -type d -name "include" -exec cp -r {}/. /app/compiler/include/ \; && \
    rm -rf /tmp/pawnc_dl /tmp/pawnc_win.zip

# 4. Скачивание базовых SA-MP инклудов напрямую с GitHub (вместо упавшего sa-mp.mp)
RUN curl -fsSL -o /tmp/samp-stdlib.zip https://github.com/pawn-lang/samp-stdlib/archive/refs/heads/master.zip && \
    unzip -qo /tmp/samp-stdlib.zip -d /tmp/samp-stdlib && \
    cp /tmp/samp-stdlib/*/*.inc /app/compiler/include/ 2>/dev/null || true && \
    rm -rf /tmp/samp-stdlib /tmp/samp-stdlib.zip

# 5. Инициализация Wine при сборке
RUN wineboot --init > /dev/null 2>&1 || true

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD sh -c "ulimit -s unlimited && python bot.py"
