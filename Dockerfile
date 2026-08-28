# ============================================================
# Stage 1: Build Tailwind CSS (Optimized for Production Cache)
# ============================================================
FROM node:20-alpine AS tailwind-builder

WORKDIR /app

# ၁။ CSS နှင့် သက်ဆိုင်သော ဖိုင်များကိုသာ Copy ကူးပါ
COPY tailwind.config.js ./
COPY static/css/input.css ./static/css/
COPY templates/ ./templates/
COPY app/ ./app/ 

# ၂။ package.json မလိုဘဲ npx ဖြင့် တိုက်ရိုက် Download ဆွဲ၍ CSS Build ပါမည်
RUN npx -y tailwindcss@3 -i ./static/css/input.css -o ./static/css/output.css --minify

# ============================================================
# Stage 2: FastAPI Production Application
# ============================================================
FROM python:3.11-slim

# ၁။ Production Environment Variables သတ်မှတ်ခြင်း
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/code \
    PIP_NO_CACHE_DIR=1 \
    FASTAPI_ENV=production

WORKDIR /code

# ၂။ လိုအပ်သော System Packages များ သွင်းပြီး Cache ရှင်းလင်းခြင်း
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        build-essential \
        libpq-dev \
        libmagic1 \
        curl && \
    rm -rf /var/lib/apt/lists/*

# ၃။ လုံခြုံရေးအတွက် Non-root User တည်ဆောက်ခြင်း
RUN useradd --create-home --uid 1000 appuser

# ၄။ Python Dependencies များကို Cache Layer သုံး၍ သွင်းခြင်း
COPY requirements.txt .
RUN pip install --upgrade pip && \
    pip install -r requirements.txt

# ၅။ Application Source Code များကို ကူးယူခြင်း
COPY . .

# ၆။ Stage 1 မှ Minify လုပ်ထားသော Production CSS ကိုသာ ကူးယူခြင်း
COPY --from=tailwind-builder /app/static/css/output.css /code/static/css/output.css

# ၇။ 🌟 Root အနေဖြင့် Permission ပေးခြင်းနှင့် Folder များ ဖန်တီးခြင်း
RUN chmod +x /code/entrypoint.sh && \
    mkdir -p uploads logs static/css && \
    chown -R appuser:appuser /code

# ၈။ Non-root User သို့ ပြောင်းလဲခြင်း (Production Security)
USER appuser

EXPOSE 8000

# ၉။ Health Check (/health သို့ တိုက်ရိုက် စစ်ဆေးရန်)
HEALTHCHECK --interval=30s \
            --timeout=5s \
            --start-period=15s \
            --retries=3 \
    CMD curl -fsS http://localhost:8000/health || exit 1

# ၁၀။ 🌟 Entrypoint နှင့် Server Start Command
ENTRYPOINT ["/code/entrypoint.sh"]

CMD ["gunicorn", "app.main:app", "-w", "4", "-k", "uvicorn.workers.UvicornWorker", "-b", "0.0.0.0:8000", "--timeout", "120", "--keep-alive", "5", "--proxy-allow-from", "*"]