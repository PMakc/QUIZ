FROM python:3.12-slim
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN mkdir -p /app/data
ENV PORT=8000
ENV DATA_DIR=/app/data
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT}"]
