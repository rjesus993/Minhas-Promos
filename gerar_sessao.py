# gerar_sessao.py
from telethon.sync import TelegramClient
from telethon.sessions import StringSession
from config import API_ID, API_HASH

with TelegramClient(StringSession(), API_ID, API_HASH) as client:
    print("\n" + "=" * 60)
    print("SUA STRING SESSION:")
    print("=" * 60)
    print(client.session.save())
    print("=" * 60)