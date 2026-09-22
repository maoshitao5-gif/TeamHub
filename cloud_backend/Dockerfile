# ── 构建镜像：TeamHub 云服务 ──
# 基础镜像使用 Debian slim，避免 Alpine 的 musl 与 psycopg2 兼容性问题
FROM python:3.11-slim

WORKDIR /app

# 安装系统依赖（libpq-dev 给 psycopg2，gcc 编译 bcrypt）
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev gcc && \
    rm -rf /var/lib/apt/lists/*

# 先复制依赖文件，利用 Docker 缓存层
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制应用代码
COPY . .

# 创建非 root 用户运行服务
RUN useradd -m appuser && chown -R appuser:appuser /app
USER appuser

# 暴露端口（与 ENV PORT 一致）
EXPOSE 9000

# 生产启动命令：gunicorn + uvicorn workers
# --workers 根据 CPU 核数调整（推荐 2*CPU+1）
CMD ["gunicorn", "main:app", \
     "--worker-class", "uvicorn.workers.UvicornWorker", \
     "--workers", "2", \
     "--bind", "0.0.0.0:9000", \
     "--timeout", "120", \
     "--access-logfile", "-", \
     "--error-logfile", "-"]
