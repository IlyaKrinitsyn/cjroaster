# Развёртывание cjroaster (Docker + OpenRouter)

Репозиторий: https://github.com/IlyaKrinitsyn/cjroaster (fork [com-agent](https://github.com/StasZaitsev93/com-agent))

## Как собирается UI на сервере

Отдельного фронтенд-билда **нет**. В Docker-образе один процесс:

```
uvicorn server:app  →  FastAPI
```

| Компонент | Где | Как попадает на сервер |
|-----------|-----|------------------------|
| **UI** | `index.html` | Копируется в образ (`COPY index.html`). Отдаётся как HTML на `GET /` |
| **Стили/JS** | CDN (Bootstrap 5) | Браузер клиента тянет с `cdn.jsdelivr.net` — нужен интернет у пользователя |
| **API** | `server.py` | Тот же origin: `fetch('/analyze')`, `fetch('/banks')` и т.д. |
| **БД** | SQLite `reports.db` | Том Docker: `/app/data/reports.db` |
| **Медиа/история** | `/app/data/references/` | Тот же том |

Схема запроса при «Прожарке»:

```
Браузер → GET /              → index.html
Браузер → POST /analyze      → LLM pipeline (OpenRouter)
Браузер → GET /history       → JSON из SQLite
```

**Streamlit (`app.py`)** в образ по умолчанию **не входит** — это альтернативный UI для локальной разработки. Продакшен = FastAPI + `index.html`.

## Быстрый старт

```bash
cd cjroaster   # https://github.com/IlyaKrinitsyn/cjroaster
cp .env.example .env
# Заполните LLM_API_KEY (OpenRouter)

docker compose build
docker compose up -d
```

Откройте: `http://<server>:8000/`

## Переменные окружения

См. `.env.example`. Обязательно:

- `LLM_API_KEY` — ключ OpenRouter (или другого OpenAI-compatible провайдера)
- `MODEL_NAME` — vision-модель, напр. `openai/gpt-4o`, `google/gemini-2.5-pro`

Опционально: `FIGMA_ACCESS_TOKEN`, `MOBBIN_API_KEY`.

## Сборка и push образа

```bash
docker build -t your-registry/com-agent:latest .
docker push your-registry/com-agent:latest
```

На сервере (через git):

```bash
git clone https://github.com/IlyaKrinitsyn/cjroaster.git
cd cjroaster
cp .env.example .env
# отредактируйте .env
docker compose up -d --build
```

Или через registry:

```bash
docker pull your-registry/com-agent:latest
docker run -d \
  --name com-agent \
  -p 8000:8000 \
  --env-file .env \
  -v com-agent-data:/app/data \
  your-registry/com-agent:latest
```

## API-ключи клиентов (внешний API)

```bash
docker exec -it com-agent python manage_keys.py add "Partner Name"
```

Вызов: `POST /api/v1/roast` с заголовком `Authorization: Bearer <key>`.

## LM Studio (без OpenRouter)

В `.env`:

```env
LLM_BASE_URL=http://host.docker.internal:1234/v1
LLM_API_KEY=lm-studio
MODEL_NAME=ваша-модель-в-lm-studio
EXTRA_BODY_JSON={"thinking":{"type":"disabled"}}
```

На Linux для доступа к LM Studio на хосте добавьте в `docker-compose.yml`:

```yaml
extra_hosts:
  - "host.docker.internal:host-gateway"
```

## Порт 8000 занят (CJXplorer на том же сервере)

Если `cjxplorer-core` уже слушает `0.0.0.0:8000`, **не останавливайте** его. В `.env` cjroaster:

```env
HOST_PORT=8001
PORT=8000
```

В `docker-compose.yml` маппинг должен быть **`8001:8000`** (хост:контейнер), а не `8001:8001`:

```yaml
ports:
  - "${HOST_PORT:-8001}:8000"
environment:
  PORT: "8000"
```

Ошибка: поменять `PORT=8001` внутри контейнера, но оставить проброс `8001:8000` — приложение не откроется.

Перезапуск:

```bash
docker compose down
docker compose up -d --build
curl http://127.0.0.1:8001/
```

UI: `http://<сервер>:8001/`

## Обратный прокси (nginx)

```nginx
location / {
    proxy_pass http://127.0.0.1:8001;  # HOST_PORT cjroaster
    client_max_body_size 50M;
    proxy_read_timeout 600s;
}
```

Увеличьте таймауты: один прогон CJ = несколько LLM-вызовов (до `API_TIMEOUT` на каждый).

## Чеклист после деплоя

1. `curl http://localhost:8001/` — HTML отвечает (или 8000, если HOST_PORT не меняли)
2. `curl http://localhost:8001/guides` — список гайдов
3. Прожарка 1 скриншота — нет ошибки `LLM_API_KEY`
4. Том `/app/data` — появился `reports.db`
