FROM python:3.12-slim

# ffprobe 用於偵測內嵌繁中字幕（processor 在缺席時會優雅降級）
RUN DEBIAN_FRONTEND=noninteractive apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:/root/.cargo/bin:${PATH}"

RUN uv venv /app/.venv

WORKDIR /app

# 先複製依賴定義，利用 Docker layer cache
COPY pyproject.toml uv.lock ./
RUN UV_COMPILE_BYTECODE=1 uv sync --frozen --no-dev

COPY core/ ./core/
COPY api.py main.py scan.py ./

# 支援 build-time 指定 UID/GID，對應主機媒體目錄的擁有者
ARG UID=1026
ARG GID=100
# 若目標 GID/UID 已存在，改用重用策略避免建置中斷
RUN if getent group "${GID}" >/dev/null; then \
        :; \
    elif getent group appgroup >/dev/null; then \
        groupmod --gid "${GID}" appgroup; \
    else \
        groupadd --gid "${GID}" appgroup; \
    fi && \
    if id -u appuser >/dev/null 2>&1; then \
        usermod --non-unique --uid "${UID}" --gid "${GID}" appuser; \
    else \
        useradd --non-unique --uid "${UID}" --gid "${GID}" --create-home appuser; \
    fi
USER appuser

# 路徑重映射：Bazarr 容器路徑前綴 → 本容器掛載路徑前綴
# 留空表示不做重映射（兩個容器掛載路徑相同時）
ENV REMAP_ROOT_FROM="" \
    REMAP_ROOT_TO=""

EXPOSE 6768

ENTRYPOINT ["uv", "run", "uvicorn", "api:app", \
            "--host", "0.0.0.0", \
            "--port", "6768", \
            "--workers", "1"]
