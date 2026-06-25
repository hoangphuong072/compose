# Container Name

Mô tả ngắn container hoặc stack này dùng để làm gì.

## Files

- `Dockerfile`: image build definition.
- `docker-compose.yml`: compose file dùng trong Dokploy.
- `.env.example`: danh sách biến môi trường cần cấu hình.
- `.dockerignore`: loại bỏ file không cần thiết khi build image.

## Environment

Copy `.env.example` thành `.env` trong Dokploy hoặc cấu hình trực tiếp trong phần environment của Dokploy.

## Deploy Notes

- Kiểm tra port expose trong `docker-compose.yml`.
- Kiểm tra volume nếu service cần lưu dữ liệu lâu dài.
- Không commit secret thật vào repo.
