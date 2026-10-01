FROM python:3.11-slim

# Установка 32-битных библиотек (компилятор Pawn собран под i686) и утилит
RUN dpkg --add-architecture i386 && \
    apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    tar \
    unzip \
    libc6:i386 \
    libstdc++6:i386 \
    && rm -rf /var/lib/apt/lists/*

# Скачивание готового Pawn Compiler 3.10.10
RUN curl -fsSL -o /tmp/pawnc.tar.gz https://github.com/pawn-lang/compiler/releases/download/v3.10.10/pawnc-3.10.10-linux.tar.gz && \
    tar -xzf /tmp/pawnc.tar.gz -C /tmp/ && \
    cp /tmp/pawnc-3.10.10-linux/bin/pawncc /usr/local/bin/ && \
    cp /tmp/pawnc-3.10.10-linux/lib/* /usr/lib/i386-linux-gnu/ 2>/dev/null || cp /tmp/pawnc-3.10.10-linux/lib/* /usr/lib/ && \
    ldconfig && \
    chmod +x /usr/local/bin/pawncc && \
    rm -rf /tmp/pawnc*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Безопасное копирование архива с инклудами (сборка не упадет, даже если zip еще не загружен)
COPY include.zip* ./
RUN if [ -f include.zip ]; then \
        mkdir -p include && unzip -qo include.zip -d include/ && rm include.zip; \
    else \
        mkdir -p include; \
    fi

COPY bot.py .

CMD ["python", "bot.py"]
