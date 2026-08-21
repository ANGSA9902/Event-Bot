FROM mcr.microsoft.com/playwright:python:v1.48.0

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

RUN playwright install chromium

COPY . .

CMD ["python", "bot.py"]