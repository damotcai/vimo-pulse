# VĨ MÔ PULSE — Kiến trúc Ra Quyết định Đầu tư (VN-Index & Vĩ mô Toàn cầu)

Website tổng hợp & tự động cập nhật dữ liệu **Kinh tế Vĩ mô Mỹ/Việt Nam + Chứng khoán Việt Nam (VN-Index, VN30, HNX, UPCoM & 8 chỉ số ngành HOSE)** theo kiến trúc **Decision-First CIO Workspace**, giúp nhà đầu tư mở lên mỗi sáng là có ngay **điểm rủi ro vĩ mô, phương án phân bổ tài sản, bản đồ luân chuyển nhóm ngành và kịch bản hành động “Nếu… Thì…”**.

## Khởi chạy nhanh mỗi sáng

1. Nhấp đúp vào file **`start_vimo.bat`** — tự khởi chạy máy chủ nội bộ và mở trình duyệt tại **`http://127.0.0.1:8765`**.
2. Hoặc chạy từ PowerShell:
   ```powershell
   cd c:\Users\admin\Desktop\vimo
   python server.py
   ```

## Kiến trúc 5 Không gian làm việc (Phím tắt `1`–`5` · `0` Toàn cảnh)

1. **`1 · Chiến lược & VN-Index` (Mặc định mỗi sáng)**:
   - **La bàn Vĩ mô – Thị trường (0–100 điểm)**: Chấm điểm minh bạch trên 5 trụ cột thực tế (*Tăng trưởng & PMI*, *Kiểm soát Lạm phát*, *Fed & Lợi suất*, *Tỷ giá & Khối ngoại*, *Xu hướng VN-Index*).
   - **Phương án Phân bổ Tài sản Đề xuất**: Tỷ trọng động cho *Cổ phiếu Việt Nam · Tiền mặt/Trái phiếu · Vàng XAU · Tài sản USD* kèm định hướng hành động hôm nay.
   - **Kịch bản “Nếu… Thì…”**: Các ngưỡng kỹ thuật (MA50, MA200, VIX) và vĩ mô (trần SBV, DXY, FedWatch) để chủ động tăng/giảm tỷ trọng.
   - **Trung tâm Chỉ số VN (`VN-Index`, `VN30`, `HNX`, `UPCoM`)**: Biểu đồ Nến Nhật + `MA20 / MA50 / MA200` + Khối lượng, **Đối chiếu trực tiếp VN-Index vs `USD/VND` / `DXY` / `US 10Y` / `S&P 500`**, **So sánh % YTD các năm (2022–2026)**, **Dòng tiền Khối ngoại 20 phiên (CafeF)** và bảng mốc kỹ thuật (RSI-14, Đỉnh/Đáy 52 tuần).
   - **Bản đồ Luân chuyển 8 Nhóm ngành HOSE (`VNFIN`, `VNREAL`, `VNIT`, `VNIND`, `VNMAT`, `VNENE`, `VNCONS`, `VNHEAL`)**: Xếp hạng *Ưu tiên / Theo dõi / Thận trọng* theo cả Sức mạnh giá (RS vs VN-Index) lẫn bối cảnh vĩ mô.
2. **`2 · Lạm phát · PMI · Lao động`**: CPI, Core CPI, PCE, Core PCE, PPI (U.S. BLS & BEA), CPI Việt Nam (GSO), PMI Mỹ vs Việt Nam, Thất nghiệp & NFP — đầy đủ chế độ *Chuỗi thời gian / So sánh các năm / MoM*.
3. **`3 · FedWatch & Lợi suất`**: Xác suất Nâng/Giữ/Hạ lãi suất Fed live từ CBOT `ZQ` Futures, lịch sử nâng/hạ lãi suất NY Fed, Đường cong lợi suất Trái phiếu Mỹ (Fed H.15) & Spread 10Y–2Y.
4. **`4 · Tỷ giá · SBV · VN–Mỹ`**: Biểu đồ 2 trục USD/VND vs DXY, so sánh YTD qua các năm, bảng Vietcombank XML, khối **SBV tự động bỏ qua nếu cổng không có dữ liệu**, và đối chiếu đa năm Việt Nam vs Hoa Kỳ (World Bank).
5. **`5 · Lịch & Tin Chính sách VN`**: Lịch kinh tế ForexFactory quy đổi giờ Việt Nam (GMT+7) & Tin tức SBV / Chính sách VN từ CafeF, VnEconomy, VnExpress & Google News.
