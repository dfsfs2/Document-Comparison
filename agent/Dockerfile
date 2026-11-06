FROM python:3.13-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY advisor.py format_report.py formatter.py knowledge_import.py knowledge.json server.py ./
USER 10001
EXPOSE 8000
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000", "--limit-concurrency", "8"]
