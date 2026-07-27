FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir uv==0.8.0
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY . .
RUN uv sync --frozen --no-dev
ENV PATH="/app/.venv/bin:$PATH"
CMD ["uvicorn", "labpilot.main:app", "--host", "0.0.0.0", "--port", "8000"]
