# server.py
from fastapi import FastAPI
import threading
import uvicorn

app = FastAPI(title="Telegram Bot Server")

@app.get("/")
def read_root():
    return {"status": "running", "message": "Userbot ativo no Hugging Face"}

@app.get("/health")
def health_check():
    return {"status": "healthy"}

def run_server():
    """Executa o servidor FastAPI em uma thread separada"""
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=7860,
        log_level="info"
    )

def start_server():
    """Inicia o servidor em background sem bloquear o bot"""
    thread = threading.Thread(target=run_server, daemon=True)
    thread.start()