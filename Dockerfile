FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Ensure the persistent data directory exists
RUN mkdir -p /app/data /app/workspaces

EXPOSE 8000

CMD ["python", "whatsapp_server.py"]