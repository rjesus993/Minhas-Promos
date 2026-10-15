FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

# usuário não-root (uid 1000 é o esperado pelo Hugging Face Spaces)
RUN useradd -m -u 1000 app && mkdir -p /app/data && chown -R app:app /app
COPY --chown=app:app . .
USER app

EXPOSE 7860

# "/" é público e leve; /health detalhado exige HEALTH_TOKEN
HEALTHCHECK --interval=60s --timeout=5s --start-period=30s --retries=3 \
  CMD python -c "import urllib.request,os; urllib.request.urlopen('http://127.0.0.1:%s/' % os.getenv('SERVER_PORT','7860'), timeout=4)"

CMD ["python", "main.py"]
