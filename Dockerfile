# Gunakan image Playwright resmi tanpa ARG
FROM mcr.microsoft.com/playwright:python-v1.48.0

WORKDIR /app

# Copy dan install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install browser Playwright
RUN playwright install chromium

# Copy semua kode
COPY . .

# Jalankan bot
CMD ["python", "bot.py"]
