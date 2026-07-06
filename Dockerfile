FROM python:3.12-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 FIE_DATA_DIR=/app/data

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000
# Populate the store on boot, then serve the control room.
CMD ["sh", "-c", "python -m fie.cli demo && python -m fie.cli serve --host 0.0.0.0 --port 8000"]
