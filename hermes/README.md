# Hermes

Stack Docker Compose cho MiniHermes Telegram bot và Web UI chat.

## Files

- `Dockerfile`: build image Python và cài MiniHermes từ source trong thư mục này.
- `docker-compose.yml`: compose file dùng trong Dokploy.
- `.env.example`: biến môi trường cần cấu hình, không chứa secret thật.
- `config.sample.json`: cấu hình mẫu. Container sẽ copy thành `/data/config.json` nếu chưa có.
- `data/`: dữ liệu bền vững, gồm config thật, memory, credentials, sessions và logs.
- `workspace/`: thư mục làm việc mặc định cho file tools và lệnh `/codex`.

## Web UI

Mặc định container chạy Web UI ở port `8080`.

```text
http://localhost:8080
```

Trong Dokploy, map domain hoặc port vào service `hermes` port `8080`. Nếu muốn đổi port ngoài host, sửa:

```bash
MINIHERMES_WEB_PORT=8080
```

## Deploy

1. Copy `.env.example` thành `.env` trong Dokploy hoặc cấu hình trực tiếp trong phần environment.
2. Điền `MINIHERMES_API_KEY`.
3. Nếu dùng Telegram, điền `TELEGRAM_BOT_TOKEN`.
4. Nếu muốn giới hạn người dùng Telegram, điền `TELEGRAM_ALLOWED_CHAT_IDS`, ví dụ `123456789,987654321`.
5. Trỏ Dokploy vào thư mục `hermes` và dùng `docker-compose.yml`.

## Config

Lần chạy đầu tiên sẽ tạo `data/config.json` từ `config.sample.json`. Có thể sửa `data/config.json` sau lần chạy đầu, hoặc dùng biến môi trường trong `.env` để override các giá trị chính.

Mặc định service chạy Web UI và tự bật Telegram nếu có `TELEGRAM_BOT_TOKEN`:

```bash
minihermes serve
```

Nếu cần chạy lệnh khác, sửa `command` trong `docker-compose.yml`, ví dụ:

```yaml
command: ["tools"]
```
