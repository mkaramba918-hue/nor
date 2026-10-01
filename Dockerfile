FROM python:3.11-slim

# Установка зависимостей сборки
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    cmake \
    git \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Скачивание и сборка компилятора pawncc
WORKDIR /tmp
RUN git clone --depth 1 https://github.com/openmultiplayer/compiler.git pawn-compiler && \
    cd pawn-compiler && \
    cmake -B build -DCMAKE_BUILD_TYPE=Release && \
    cmake --build build --target pawncc && \
    cp build/pawncc /usr/local/bin/ && \
    rm -rf /tmp/pawn-compiler

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копируем стандартные инклуды (если добавите их в репозиторий)
COPY include/ ./include/
COPY bot.py .

CMD ["python", "bot.py"]
