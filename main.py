# main.py
import asyncio
import logging
from bot import main as bot_main
from server import start_server
from logging_config import setup_logging

# Configura logging
setup_logging()

async def run():
    """Executa o bot e o servidor"""
    # Inicia ambos em paralelo
    await asyncio.gather(
        bot_main(),
        start_server()
    )

if __name__ == "__main__":
    asyncio.run(run())