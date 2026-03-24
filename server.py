# server.py
from fastapi import FastAPI
import threading
import uvicorn

app = FastAPI(title="Telegram Bot Server")

@app.get("/")
def read_root():
    return {"status": "running", "message": "Userbot ativo no Hugging Face"}

@app.head("/")
def read_root_head():
    # HEAD não deve retornar corpo, apenas status 200
    return {}

@app.get("/health")
def health_check():
    return {"status": "healthy"}

@app.head("/health")
def health_head():
    # HEAD retorna apenas cabeçalhos e status 200
    return {}

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