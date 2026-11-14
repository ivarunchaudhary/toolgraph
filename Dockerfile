# ToolGraph app image. HydraDB runs as its own service (published image, unmodified).
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 TIKTOKEN_CACHE_DIR=/app/.cache/tiktoken
WORKDIR /app

COPY requirements-app.txt .
RUN pip install -r requirements-app.txt

COPY backend backend
COPY data data
COPY frontend frontend
COPY results results

# bake the embedding model + tokenizer + tool/capability vectors into the image (no downloads at boot)
RUN python -c "import tiktoken; tiktoken.get_encoding('o200k_base')" \
 && python -c "from backend.app.retrieve import vector, graph; vector.tool_index(); graph.cap_index()"

RUN useradd -u 10001 -m app && chown -R app /app
USER app
EXPOSE 8000
CMD ["sh", "-c", "uvicorn backend.app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
