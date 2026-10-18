FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN mkdir -p data/sessions data/logos data/generated_reports
CMD ["python", "-m", "app.main"]
