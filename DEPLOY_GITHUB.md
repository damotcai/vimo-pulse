# Hướng dẫn đưa VĨ MÔ PULSE lên GitHub & chạy online miễn phí 24/7

Vì website dùng **Python backend** (`server.py`) để kéo dữ liệu trực tuyến từ BLS, BEA, NY Fed, VNDirect, CafeF, SJC, Vietcombank, SBV... mà không bị chặn CORS:
- **GitHub Pages** chỉ chứa file tĩnh (HTML/JS), không chạy được Python trực tiếp.
- **Cách chuẩn và miễn phí 100%**: Lưu code lên **GitHub**, rồi kết nối **Render.com** (đăng nhập bằng chính tài khoản GitHub, không cần thẻ tín dụng) → tự động tạo website thật dạng **`https://vimo-pulse.onrender.com`** cho bất kỳ ai truy cập.

---

## Bước 1: Đưa thư mục code lên GitHub (Không cần cài Git)

1. Đăng nhập **https://github.com** → bấm dấu **`+`** góc trên bên phải → chọn **New repository**.
2. Đặt tên repo (ví dụ: `vimo-pulse`), chọn **Public**, bấm **Create repository**.
3. Ở trang repo vừa tạo, bấm dòng chữ **`uploading an existing file`**.
4. Mở thư mục `c:\Users\admin\Desktop\vimo`, kéo thả vào GitHub toàn bộ các file/thư mục sau *(bỏ qua thư mục `__pycache__`)*:
   - `server.py`
   - `macro_fetcher.py`
   - `requirements.txt`
   - `render.yaml`
   - `.gitignore`
   - `README.md`
   - Thư mục `static/` (gồm `index.html`, `styles.css`, `app.js`)
   - Thư mục `data/` (chỉ cần file `pmi_vn_baseline.json`)
5. Kéo xuống dưới bấm **Commit changes**.

---

## Bước 2: Phát hành thành website thật trên Render.com (Miễn phí)

1. Vào **https://render.com** → bấm **Get Started** → chọn **Sign in with GitHub** (đăng nhập thẳng bằng tài khoản GitHub của bạn).
2. Chọn **New +** → chọn **Blueprint** (hoặc **Web Service**):
   - Nếu chọn **Blueprint**: chọn repo `vimo-pulse` → Render tự đọc file `render.yaml` có sẵn (tự cài `requests`, chạy `python server.py`, đặt máy chủ Singapore gần Việt Nam) → bấm **Apply**.
   - Nếu chọn **Web Service**: chọn repo `vimo-pulse` → điền:
     - **Runtime**: `Python 3`
     - **Region**: `Singapore`
     - **Build Command**: `pip install -r requirements.txt`
     - **Start Command**: `python server.py`
     - **Instance Type**: `Free`
     → bấm **Create Web Service**.
3. Chờ khoảng 1–2 phút để Render build xong. Khi hiện trạng thái **`Live`**, bấm vào link dạng **`https://vimo-pulse.onrender.com`** — đó chính là website thật của bạn, gửi cho ai mở bằng điện thoại hay máy tính đều xem được!

---

## Lưu ý nhỏ khi dùng gói miễn phí (Free Tier)
- Gói Free của Render sẽ tự "ngủ đông" sau 15 phút không có người truy cập; lần mở đầu tiên sau khi ngủ sẽ chờ khoảng 20–30 giây để máy chủ thức dậy, sau đó chạy rất nhanh.
- Nếu muốn web **không bao giờ ngủ đông** (luôn mở tức thì), bạn có thể tạo 1 monitor miễn phí trên **https://uptimerobot.com** ping vào link `https://<ten-web-cua-ban>.onrender.com/api/health` mỗi 10 phút.
- Mỗi lần bạn sửa code và cập nhật lên GitHub, Render sẽ **tự động cập nhật website mới** sau ~1 phút.
