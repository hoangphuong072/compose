# Dokploy Compose Collection

Repo này lưu các `Dockerfile` và `docker-compose.yml` dùng để deploy qua Dokploy.

Mỗi loại container hoặc stack nằm trong một thư mục riêng. Thư mục nên tự chứa toàn bộ file cần thiết để build và deploy service đó.

## Cấu trúc đề xuất

```text
.
├── README.md
├── _template/
│   ├── README.md
│   ├── Dockerfile
│   ├── docker-compose.yml
│   ├── .dockerignore
│   └── .env.example
└── <container-name>/
    ├── README.md
    ├── Dockerfile
    ├── docker-compose.yml
    ├── .dockerignore
    └── .env.example
```

## Quy ước thêm container mới

1. Copy thư mục `_template` thành tên container hoặc stack mới.
2. Sửa `README.md` trong thư mục đó để ghi mục đích, biến môi trường và cách deploy.
3. Sửa `Dockerfile` và `docker-compose.yml` theo nhu cầu của container.
4. Không commit file `.env` thật. Chỉ commit `.env.example`.

## Quy ước đặt tên

- Dùng chữ thường và dấu gạch ngang: `n8n`, `postgres-backup`, `cloudflared`.
- Tên thư mục nên trùng với tên service chính trong `docker-compose.yml`.
- Nếu một stack có nhiều service phụ, vẫn giữ trong cùng một thư mục nếu chúng deploy chung.

## Stacks

- `hermes`: MiniHermes Telegram bot và Web UI chat, dùng `config.sample.json` làm cấu hình mẫu và lưu dữ liệu thật trong volume `data`.
- `hermes-camoufox`: MiniHermes kèm Camoufox Playwright websocket server nội bộ, dùng khi cần browser Camoufox cài sẵn trong cùng stack.

## Dokploy

Khi tạo compose app trong Dokploy, trỏ project vào đúng thư mục của container cần deploy và dùng file `docker-compose.yml` trong thư mục đó.
