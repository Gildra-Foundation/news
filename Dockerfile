FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TZ=UTC

WORKDIR /app

# Шрифты для рендера карточек твитов/Reddit-постов (Pillow)
RUN apt-get update && apt-get install -y --no-install-recommends \
    fonts-dejavu-core \
    fonts-noto-color-emoji \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY config.py db.py ai.py digest.py emoji_store.py external_fetch.py render_post.py tg_reader.py tg_writer.py pipeline.py main.py init_session.py ./
COPY assets/ ./assets/

RUN useradd --create-home --uid 1000 app && \
    mkdir -p /app/data && \
    chown -R app:app /app
USER app

CMD ["python", "main.py"]
