# InsightSQL AI — one image for both services (the command picks backend or frontend).
# Multi-stage: dependencies are installed into a virtualenv in the build stage and only
# that virtualenv plus the application code reach the final, non-root image.

FROM python:3.13-slim AS build
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
COPY requirements.txt /tmp/requirements.txt
RUN pip install --requirement /tmp/requirements.txt

FROM python:3.13-slim AS runtime
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DATA_DIR=/app/data
RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --home-dir /app --no-create-home app
WORKDIR /app
COPY --from=build /opt/venv /opt/venv
COPY backend/ backend/
COPY frontend/ frontend/
COPY config/ config/
COPY sample_data/ sample_data/
COPY .streamlit/ .streamlit/
COPY pyproject.toml .env.example ./
# The data folder belongs to the app user; a new named volume inherits this ownership.
RUN mkdir -p /app/data && chown app:app /app/data
USER app
EXPOSE 8000 8501
# Default: the API. docker-compose.yml runs the frontend from the same image.
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
