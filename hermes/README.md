# Hermes

Stack Docker Compose cho MiniHermes Telegram bot.

## Files

- `Dockerfile`: build image Python và cài MiniHermes từ source trong thư mục này.
- `docker-compose.yml`: compose file dùng trong Dokploy.
- `.env.example`: biến môi trường cần cấu hình, không chứa secret thật.
- `config.sample.json`: cấu hình mẫu. Container sẽ copy thành `/data/config.json` nếu chưa có.
- `data/`: dữ liệu bền vững, gồm config thật, memory, credentials, sessions và logs.
- `workspace/`: thư mục làm việc mặc định cho file tools và lệnh `/codex`.

## Deploy

1. Copy `.env.example` thành `.env` trong Dokploy hoặc cấu hình trực tiếp trong phần environment.
2. Điền `MINIHERMES_API_KEY` và `TELEGRAM_BOT_TOKEN`.
3. Nếu muốn giới hạn người dùng Telegram, điền `TELEGRAM_ALLOWED_CHAT_IDS`, ví dụ `123456789,987654321`.
4. Trỏ Dokploy vào thư mục `hermes` và dùng `docker-compose.yml`.

## Config

Lần chạy đầu tiên sẽ tạo `data/config.json` từ `config.sample.json`. Có thể sửa `data/config.json` sau lần chạy đầu, hoặc dùng biến môi trường trong `.env` để override các giá trị chính.

Mặc định bot chạy:

```bash
minihermes telegram
```

Nếu cần chạy lệnh khác, sửa `command` trong `docker-compose.yml`, ví dụ:

```yaml
command: ["tools"]
```
