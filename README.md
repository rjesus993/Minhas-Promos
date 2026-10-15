# Minhas Promos

Userbot do Telegram que monitora grupos e canais atrás de promoções e avisa
você (mensagem em "Mensagens salvas" + push) quando uma oferta casa com os
seus termos.

## Como funciona

1. Cada mensagem nova de grupo/canal é comparada com a sua lista de termos
   (`utils.contains_term`, depois `matcher.smart_match` com tolerância a
   plural, variações e erros de digitação em palavras longas).
2. Ofertas repetidas são descartadas por hash (janela de 30 min).
3. O alerta vai para "Mensagens salvas" e para o endpoint de push, com fila
   persistente em SQLite, retry com backoff e descarte de pushes velhos.
4. `main.py` supervisiona bot, servidor HTTP, fila de push e backups, com
   reinício automático e desligamento limpo (SIGINT/SIGTERM).

## Rodando

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
cp .env.example .env               # preencha API_ID, API_HASH e TELEGRAM_SESSION
python gerar_sessao.py             # uma vez, para gerar TELEGRAM_SESSION
python main.py
```

Docker: `docker build -t minhas-promos . && docker run --env-file .env -v $(pwd)/data:/app/data minhas-promos`

## Comandos (envie você mesmo, em qualquer chat)

Só mensagens **enviadas por você** são tratadas como comandos.

| Grupo | Comandos |
|---|---|
| Termos | `/termos` `/addtermo X` `/deltermo X` `/settermos a,b,c` `/delall` `/historico` |
| Grupos | `/listgroups` `/chats` `/allowchat ID` `/denychat ID` `/clearchats` |
| Sistema | `/status` `/ping` `/debug on\|off` `/chatid` `/dedup` |

## Endpoints HTTP

- `GET /` keepalive público.
- `GET /health` status resumido. Com `HEALTH_TOKEN`, devolve detalhes completos.
- `GET /health/{database,storage,system,external}` só com `HEALTH_TOKEN`
  (header `X-Health-Token` ou `?token=`).

## Variáveis de ambiente

Veja `.env.example`. Obrigatórias: `API_ID`, `API_HASH`, `TELEGRAM_SESSION`.

## Segurança

- Nunca commite `.env`, `*.session` ou `TELEGRAM_SESSION`. Se algum já foi
  para o histórico do git, revogue a sessão no Telegram (Configurações >
  Dispositivos) e gere outra.
- Defina `HEALTH_TOKEN` e `PUSH_TOKEN` em produção.

## Testes

```bash
pip install pytest rapidfuzz
pytest tests/
```
