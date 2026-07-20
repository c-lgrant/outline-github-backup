FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install --no-cache-dir .
EXPOSE 8080
CMD ["uvicorn", "outline_backup.service.app:app", "--host", "0.0.0.0", "--port", "8080"]
