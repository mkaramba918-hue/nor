FROM python:3.10-slim

# Установка 32-битных библиотек и утилит
RUN dpkg --add-architecture i386 && \
    apt-get update && \
    apt-get install -y --no-install-recommends \
        libc6:i386 \
        libstdc++6:i386 \
        curl \
        unzip && \
    rm -rf /var/lib/apt/lists/*

# Установка Pawn Compiler v3.10.10 и базовых инклудов
RUN curl -fsSL -o /tmp/pawnc.tar.gz https://github.com/pawn-lang/compiler/releases/download/v3.10.10/pawnc-3.10.10-linux.tar.gz && \
    tar -xzf /tmp/pawnc.tar.gz -C /tmp/ && \
    cp /tmp/pawnc-3.10.10-linux/bin/pawncc /usr/local/bin/pawncc && \
    cp /tmp/pawnc-3.10.10-linux/lib/* /usr/lib/i386-linux-gnu/ 2>/dev/null || true && \
    mkdir -p /app/include && \
    cp -r /tmp/pawnc-3.10.10-linux/include/* /app/include/ 2>/dev/null || true && \
    rm -rf /tmp/pawnc* && \
    ldconfig && \
    chmod +x /usr/local/bin/pawncc

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Запуск с неограниченным размером системного стека
CMD sh -c "ulimit -s unlimited && python bot.py"
