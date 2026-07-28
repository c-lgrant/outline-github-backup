FROM python:3.12-slim@sha256:57cd7c3a7a273101a6485ba99423ee568157882804b1124b4dd04266317710de
WORKDIR /app
COPY requirements.lock pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install --no-cache-dir --require-hashes -r requirements.lock \
    && pip install --no-cache-dir --no-deps .
EXPOSE 8080
CMD ["uvicorn", "outline_backup.service.app:app", "--host", "0.0.0.0", "--port", "8080"]
