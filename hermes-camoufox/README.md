# Hermes + Camoufox

Stack Docker Compose cài sẵn MiniHermes và Camoufox, dùng cho Dokploy.

Stack này không viết lại Hermes hoặc Camoufox. Docker image cài MiniHermes từ source `hermes/` trong repo và cài package Camoufox chính thức từ PyPI. Playwright được pin ở `1.47.0` vì Camoufox `0.4.11` dùng layout internal browser server của Playwright version này. Compose chạy:

- `hermes`: MiniHermes Web UI/Telegram ở port container `8080`, publish ra host bằng `MINIHERMES_WEB_HOST_PORT`.
- `camoufox`: Camoufox Playwright websocket server trong network nội bộ ở `ws://camoufox:9222/camoufox`.

## Files

- `Dockerfile`: build image có sẵn MiniHermes, Camoufox và browser binary.
- `docker-compose.yml`: compose file dùng trong Dokploy.
- `.env.example`: biến môi trường cần cấu hình, không chứa secret thật.
- `data/`: dữ liệu bền vững của Hermes.
- `camoufox-data/`: dữ liệu runtime riêng cho service Camoufox.
- `workspace/`: thư mục làm việc mặc định của Hermes.

## Kết nối Hermes với Camoufox

Compose truyền sẵn hai biến vào `hermes`:

```bash
CAMOUFOX_WS_ENDPOINT=ws://camoufox:9222/camoufox
PLAYWRIGHT_FIREFOX_WS_ENDPOINT=ws://camoufox:9222/camoufox
```

Camoufox chạy bằng remote server chính thức:

```bash
python -m camoufox server
```

Trong stack này lệnh chạy tương đương `launch_server(headless=True, host="0.0.0.0", port=9222, ws_path="camoufox")`.

## Deploy

1. Copy `.env.example` thành `.env` trong Dokploy hoặc cấu hình trực tiếp trong phần environment.
2. Điền `MINIHERMES_API_KEY`.
3. Nếu dùng Telegram, điền `TELEGRAM_BOT_TOKEN`.
4. Trỏ Dokploy vào thư mục `hermes-camoufox` và dùng `docker-compose.yml`.

Web UI mặc định:

```text
http://localhost:8080
```

Nếu cần đổi cổng public trên host hoặc Dokploy, đổi `MINIHERMES_WEB_HOST_PORT`; giữ `MINIHERMES_WEB_CONTAINER_PORT=8080` trừ khi bạn muốn đổi cổng lắng nghe bên trong container.

Camoufox chỉ `expose` port trong Docker network, không publish ra host mặc định. Hermes truy cập qua Docker network nội bộ bằng `CAMOUFOX_WS_ENDPOINT`.
