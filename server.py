# server.py
from fastapi import FastAPI
import asyncio
import threading
from typing import Dict, Any

app = FastAPI(title="Telegram Bot Server")

@app.get("/")
def read_root():
    return {"status": "running", "message": "Userbot ativo no Hugging Face"}

@app.get("/health")
def health_check():
    return {"status": "healthy"}

async def start_server():
    """Inicia o servidor FastAPI"""
    import uvicorn
    from fastapi import FastAPI
    
    # Configuração do servidor
    config = uvicorn.Config(app, host="0.0.0.0", port=7860, log_level="info")
    server = uvicorn.Server(config)
    
    # Inicia o servidor
    await server.serve()