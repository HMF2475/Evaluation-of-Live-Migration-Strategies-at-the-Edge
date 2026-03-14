FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .

EXPOSE 8080

ENV SERVICE_NAME="edge-service"
ENV STATE_FILE="/app/state/service_state.json"

RUN mkdir -p /app/state

CMD ["python", "app.py"]
