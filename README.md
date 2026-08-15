# Kế Hoạch Thiết Kế Kỹ Thuật (v2)
### Đa nền tảng (Web App / Dashboard / Landing / E-commerce) + Hạ tầng Local AI (Qwen2.5-Coder-14B)

Vai trò: Technical Leader & Solution Architect
Phần cứng mục tiêu: RTX 3060 12GB + 64GB RAM

---

## 0. Nguyên tắc dẫn dắt

1. **Một bộ convention, nhiều stack.** HTML/CSS/JS thuần, Go+Templ, React/Next.js dùng chung design token, breakpoint, cách đặt tên — AI học một lần, áp dụng đúng cho mọi dự án.
2. **AI vào vai Reviewer trước, Coder sau.** Giai đoạn 1: chỉ đọc & báo lỗi. Giai đoạn 2 (sau khi đủ tin cậy): mới cho tự sửa file.
3. **AI là dev-tool, không phải production service.** llama.cpp/llama-swap chạy trên máy dev, không nằm trong runtime app khi lên production.

---

## 1. Roadmap theo giai đoạn

| Giai đoạn | Thời gian gợi ý | Vai trò AI | Mục tiêu |
|---|---|---|---|
| **0 — Nền tảng** | Tuần 1 | — | Cài hạ tầng AI + dựng monorepo, convention, design system |
| **1 — AI Tester/Reviewer** | Tuần 2-5 | Đọc code, tìm bug, chấm theo checklist | Xây lòng tin, thu thập pattern lỗi |
| **2 — AI Coding Assistant** | Sau khi GĐ1 ổn định | Tự viết/sửa file (agent mode) | Tăng tốc độ code thực tế |

---

## 2. Kiến trúc hệ thống tổng thể

### 2.1 Sơ đồ tổng quan

```
                        Client (Browser: Mobile 375px / Tablet 768px / PC 1280px+)
                                              │
        ┌─────────────────────────────────────┼──────────────────────────────────────┐
        │                                     │                                      │
 Static Site (HTML/CSS/JS thuần)        Next.js App (Dashboard/SaaS)          Go + Templ App (server-rendered + htmx)
        │                                     │                                      │
        └──────────────────────────── API / Service Layer ───────────────────────────┘
                                              │
                              ┌───────────────┴────────────────┐
                        PostgreSQL (dữ liệu chính)        Redis (cache/session/cart)

──────────────────────────── Ngoài luồng production ──────────────────────────── 
  AI Dev Tooling (chỉ chạy trên máy dev): llama.cpp + llama-swap → IDE agent → review/hỗ trợ code
```

### 2.2 Kiến trúc theo từng loại dự án

- **Landing/Marketing (HTML/CSS/JS thuần + Tailwind):** không backend/DB, build tĩnh, deploy CDN.
- **Dashboard/SaaS/E-commerce (Next.js + Tailwind):** App Router, Server Component mặc định, API routes/Prisma → PostgreSQL, Redis khi cần cache/session.
- **Go + Templ:** server-rendered, hạn chế JS tay bằng `htmx`; cấu trúc chuẩn `cmd/ internal/ pkg/`; DB qua `pgx`/`sqlc` tới cùng PostgreSQL.

### 2.3 Nguyên tắc chọn DB

PostgreSQL là DB chính cho mọi dự án có backend. Redis chỉ thêm khi thật sự cần cache/session/queue. Landing page tĩnh không cần DB.

---

## 3. Cấu trúc thư mục Monorepo & Coding Convention

```
my-workspace/
├── apps/
│   ├── site-static/        # HTML/CSS/JS thuần
│   ├── web-dashboard/      # Next.js
│   └── go-app/             # Go + Templ
├── packages/
│   ├── design-tokens/      # nguồn chân lý duy nhất: tokens.json
│   └── tailwind-preset/    # tailwind.config dùng chung
├── docs/
│   ├── ARCHITECTURE.md
│   ├── CONVENTIONS.md      # rule chính AI phải đọc
│   └── DESIGN-SYSTEM.md
└── ai/
    ├── llama-swap-config.yaml
    ├── review-checklist.md
    └── .clinerules
```

| Stack | Naming | Quy tắc quan trọng cho AI |
|---|---|---|
| HTML/CSS/JS thuần | kebab-case file | Không viết inline `<style>`/`<script>` dài, tách file riêng |
| Go + Templ | PascalCase component, snake_case file Go | `handler → service → repository`, không cho handler gọi thẳng DB; luôn `templ generate` sau khi sửa `.templ` |
| React/Next.js | PascalCase component, kebab-case route | Server Component mặc định, chỉ thêm `"use client"` khi cần state/event |
| Tất cả | — | Class Tailwind theo thứ tự: layout → spacing → typography → màu → hiệu ứng |

File `docs/CONVENTIONS.md` là rule nạp vào **mọi** request của AI — tối thiểu gồm: quy tắc chung, quy tắc responsive (mục 4), quy tắc riêng từng stack, và quy tắc riêng khi AI đóng vai Reviewer (mục 6).

---

## 4. Design System Responsive (dùng chung mọi stack)

**Token dùng chung** (`packages/design-tokens/tokens.json`): breakpoints `640/768/1024/1280`, spacing scale theo đơn vị 4px, bảng màu, font scale. `packages/tailwind-preset` import token này, dùng cho cả `site-static`, `web-dashboard` (Next.js build tự động) và `go-app` (build CSS bằng Tailwind CLI standalone quét `views/**/*.templ`).

| Breakpoint | Thiết bị | Quy tắc |
|---|---|---|
| < 640px | Điện thoại | 1 cột, nav hamburger/bottom-tab, chữ ≥16px |
| 640–1023px | Tablet | 2 cột nếu hợp lý |
| ≥ 1024px | Laptop/PC | Layout đầy đủ, sidebar + grid nhiều cột |

Checklist responsive QA (người và AI đều dùng): test ở 375/768/1024/1440px, không scroll ngang, ảnh `max-width:100%`, vùng chạm ≥44x44px trên mobile.

---

## 5. Hạ tầng AI — Cài đặt llama.cpp + Qwen2.5-Coder-14B

Tối ưu cho **RTX 3060 12GB + 64GB RAM DDR3**. Nguyên tắc: nhồi tối đa layer vào GPU, không offload model chính sang RAM DDR3 (băng thông thấp → chậm rõ rệt nếu offload).

### 5.1 Chọn bản quant GGUF

| Quant | VRAM ước tính | Khuyến nghị |
|---|---|---|
| Q4_K_S | ~8.5GB | Dùng nếu muốn context dài hơn |
| **Q4_K_M** | **~9-10GB** | **Mặc định — vừa khít 12GB VRAM** |
| Q5_K_M | ~10.5-11GB | Chỉ dùng nếu tắt bớt app khác, context ngắn |
| Q6_K/Q8_0 | 13GB+ | Không vừa 12GB, sẽ phải offload → chậm |

### 5.2 Cài đặt trên Windows

**Option A — Dùng bản build sẵn (khuyến nghị, nhanh nhất, không cần Visual Studio):**

1. Cài **NVIDIA driver** mới nhất + **CUDA Toolkit** (khớp phiên bản, ví dụ 12.x) từ trang NVIDIA.
2. Vào `https://github.com/ggml-org/llama.cpp/releases`, tải bản zip Windows có chữ **cuda** phù hợp phiên bản CUDA vừa cài (ví dụ `llama-<version>-bin-win-cuda-cu12.x-x64.zip`), giải nén vào ví dụ `C:\llama.cpp`.
3. Nếu release có kèm gói `cudart` runtime riêng, giải nén luôn vào cùng thư mục (chứa các DLL CUDA cần thiết để chạy).
4. Mở PowerShell, `cd C:\llama.cpp`, chạy thử `.\llama-server.exe --version` để xác nhận chạy được.

**Option B — Build từ source (khi cần tùy chỉnh flag build):**

```powershell
# Cài trước: Visual Studio 2022 (workload "Desktop development with C++"),
# CMake, Git, CUDA Toolkit

git clone https://github.com/ggml-org/llama.cpp
cd llama.cpp
cmake -B build -DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES=86
cmake --build build --config Release -j
```

Binary sau khi build nằm ở `build\bin\Release\llama-server.exe`.

**⚠️ Mẹo tối ưu quan trọng riêng cho Windows (RTX 30 series):**
Driver NVIDIA trên Windows có tính năng "CUDA Sysmem Fallback" — khi VRAM đầy, thay vì báo lỗi OOM, driver **âm thầm** đẩy dữ liệu sang RAM hệ thống qua PCIe, khiến tốc độ giảm rất mạnh mà không có cảnh báo rõ ràng. Nên vào **NVIDIA Control Panel → Manage 3D Settings → Program Settings**, chọn `llama-server.exe`, đặt **"CUDA - Sysmem Fallback Policy"** thành **"Prefer No Sysmem Fallback"**. Việc này giúp bạn nhận lỗi OOM rõ ràng để chỉnh `--n-gpu-layers`/`--ctx-size` thay vì chạy âm thầm rất chậm mà không hiểu vì sao.

### 5.3 Cài đặt trên Linux Ubuntu

```bash
# Cài CUDA Toolkit trước (khớp driver) từ trang NVIDIA nếu chưa có

git clone https://github.com/ggml-org/llama.cpp
cd llama.cpp

# RTX 3060 = Ampere, compute capability 8.6
cmake -B build -DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES=86
cmake --build build --config Release -j$(nproc)
```

Binary nằm ở `build/bin/llama-server`.

### 5.4 Tải model GGUF (chung cho cả 2 OS)

```bash
pip install -U "huggingface_hub[cli]"

huggingface-cli download bartowski/Qwen2.5-Coder-14B-Instruct-GGUF \
  Qwen2.5-Coder-14B-Instruct-Q4_K_M.gguf \
  --local-dir ./models/qwen2.5-coder-14b
```

(Trên Windows chạy y hệt trong PowerShell, chỉ cần đã cài Python + pip.)

### 5.5 Chạy llama-server (tối ưu cho RTX 3060 12GB)

**Linux:**
```bash
./build/bin/llama-server \
  --model ./models/qwen2.5-coder-14b/Qwen2.5-Coder-14B-Instruct-Q4_K_M.gguf \
  --host 0.0.0.0 --port 8080 \
  --n-gpu-layers 999 \
  --ctx-size 16384 \
  --flash-attn on \
  --cache-type-k q8_0 --cache-type-v q8_0 \
  --threads 8 \
  --jinja
```

**Windows (PowerShell), cùng flag, khác đường dẫn:**
```powershell
.\llama-server.exe `
  --model .\models\qwen2.5-coder-14b\Qwen2.5-Coder-14B-Instruct-Q4_K_M.gguf `
  --host 0.0.0.0 --port 8080 `
  --n-gpu-layers 999 `
  --ctx-size 16384 `
  --flash-attn on `
  --cache-type-k q8_0 --cache-type-v q8_0 `
  --threads 8 `
  --jinja
```

Giải thích flag:
- `--n-gpu-layers 999`: cố nhồi toàn bộ layer lên GPU — nếu OOM, giảm dần (ví dụ 40 → 35...).
- `--cache-type-k/v q8_0`: nén KV cache, tiết kiệm VRAM đáng kể so với fp16 mặc định.
- `--flash-attn on`: giảm VRAM, tăng tốc trên Ampere trở lên — luôn bật.
- `--threads`: đặt bằng số **nhân vật lý** CPU (không tính hyperthread) — quá cao không giúp nhanh hơn, thậm chí chậm hơn do tranh chấp luồng.
- `--jinja`: bắt buộc để dùng đúng chat template của Qwen2.5-Coder-Instruct.

Theo dõi VRAM: Linux dùng `nvidia-smi -l 1`, Windows dùng `nvidia-smi -l 1` trong PowerShell hoặc Task Manager tab Performance → GPU → Dedicated GPU memory.

### 5.6 Kết hợp nhiều model bằng llama-swap (tránh quá tải GPU)

Tải `llama-swap` từ `https://github.com/mostlygeek/llama-swap` (có binary cho cả Windows và Linux, hoặc Docker image CUDA cho Linux).

```yaml
# llama-swap-config.yaml
healthCheckTimeout: 180
globalTTL: 900   # tự unload model sau 15 phút không dùng

models:
  qwen-coder-review:      # review sâu / code chính
    cmd: |
      llama-server --port ${PORT}
      --model ./models/qwen2.5-coder-14b/Qwen2.5-Coder-14B-Instruct-Q4_K_M.gguf
      --n-gpu-layers 999 --ctx-size 16384 --flash-attn on
      --cache-type-k q8_0 --cache-type-v q8_0 --jinja

  qwen-coder-lint-fast:   # check nhanh / autocomplete
    cmd: |
      llama-server --port ${PORT}
      --model ./models/qwen2.5-coder-7b/Qwen2.5-Coder-7B-Instruct-Q4_K_M.gguf
      --n-gpu-layers 999 --ctx-size 8192 --flash-attn on --jinja

  embed-model:             # RAG index codebase, chạy CPU để rảnh GPU
    cmd: |
      llama-server --port ${PORT}
      --model ./models/nomic-embed-text-v1.5.Q8_0.gguf
      --embedding --n-gpu-layers 0
```

Chạy: `llama-swap --config llama-swap-config.yaml` (cùng lệnh trên cả 2 OS). Trỏ IDE agent (Continue/Cline) vào `http://localhost:8080/v1` (cổng của llama-swap, không phải cổng llama-server riêng lẻ) — llama-swap tự load/unload model theo request, đảm bảo chỉ một model lớn chiếm VRAM tại một thời điểm.

### 5.7 Kiểm tra nhanh

```bash
curl http://localhost:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"qwen-coder-review","messages":[{"role":"user","content":"Viết hàm fibonacci bằng Python"}]}'
```

(Windows PowerShell: dùng `curl.exe` — PowerShell có alias `curl` trỏ tới `Invoke-WebRequest` gây lỗi cú pháp, nên gọi rõ `curl.exe ...` hoặc dùng Git Bash.)

### 5.8 Xử lý sự cố

| Vấn đề | Nguyên nhân | Cách sửa |
|---|---|---|
| CUDA out of memory khi load | ngl/ctx-size quá cao so với VRAM trống | Giảm `--n-gpu-layers`, `--ctx-size`, hoặc đổi Q4_K_S |
| Chạy chậm bất thường trên Windows dù không báo lỗi | Sysmem Fallback đang âm thầm dùng RAM | Đặt lại "Prefer No Sysmem Fallback" trong NVIDIA Control Panel (mục 5.2) |
| Chạy chậm trên Linux dù VRAM còn trống | Có phần layer bị offload CPU (RAM DDR3 chậm) | Kiểm tra log lúc load xem bao nhiêu layer trên GPU; giảm ctx-size để nhồi hết layer lên GPU |
| Model trả lời sai định dạng chat | Thiếu `--jinja` | Luôn bật `--jinja` |
| `curl` lỗi cú pháp trên PowerShell | PowerShell alias `curl` → `Invoke-WebRequest` | Gọi `curl.exe` thay vì `curl`, hoặc dùng Git Bash |

---

## 6. Quy trình làm việc với Local LLM theo giai đoạn

### 6.1 Giai đoạn 1 — AI làm Tester/Reviewer

- Cấu hình agent (Cline/Continue) chế độ **chỉ đọc**, không cho auto-edit file, trỏ vào endpoint llama-swap.
- Checklist review chuẩn — lưu ở `ai/review-checklist.md`: lỗi cú pháp/logic, vấn đề bảo mật cơ bản (input không validate, SQL nối chuỗi, secret hardcode), vi phạm convention, vi phạm quy tắc responsive, accessibility cơ bản, performance rõ ràng (N+1 query, re-render thừa). Output bắt buộc theo format: `[Mức độ] [File:dòng] [Mô tả] → [Đề xuất sửa]`.
- Tích hợp git hook `pre-commit` gọi model `qwen-coder-lint-fast` để review diff, ở **chế độ cảnh báo, không chặn commit** (`exit 0`) trong vài tuần đầu. Trên Windows, git hook chạy qua `sh.exe` của Git for Windows nên script bash vẫn hoạt động — chỉ cần cài thêm `jq` (`winget install jqlang.jq`) vì Windows không có sẵn.
- Trước khi mở PR/push, chạy review sâu thủ công bằng model `qwen-coder-review` (14B).
- Đầu ra: log các lỗi AI phát hiện đúng/sai — dùng để tinh chỉnh checklist và quyết định thời điểm chuyển Giai đoạn 2.

### 6.2 Giai đoạn 2 (tương lai) — Coding Assistant

Điều kiện chuyển: tỷ lệ cảnh báo mức "Cao" chính xác ổn định (ví dụ ≥80% trong 2-3 tuần liên tục). Khi đó: bật agent mode tự sửa file, bật RAG toàn bộ codebase (Continue.dev codebase indexing), quy trình AI đề xuất → người review diff → merge. Vẫn giữ git hook review làm lớp kiểm tra độc lập thứ hai.

---

## 7. Hành động cụ thể — 4 tuần đầu

| Tuần | Việc cần làm |
|---|---|
| 1 | Cài llama.cpp theo mục 5.2/5.3 (chọn Windows hoặc Ubuntu), tải model Q4_K_M, chạy thử llama-server; song song dựng monorepo + `packages/design-tokens` + `docs/CONVENTIONS.md` |
| 2 | Cài llama-swap với 3 model (review 14B, lint 7B, embed); trỏ IDE agent vào endpoint llama-swap |
| 3 | Viết `ai/review-checklist.md`, cấu hình agent chế độ chỉ-đọc, gắn git hook cảnh báo |
| 4 | Chạy thử trên 1 dự án nhỏ thật (landing page), thu thập log lỗi AI phát hiện đúng/sai để tinh chỉnh checklist |

---

## 8. Rủi ro & lưu ý

| Rủi ro | Ảnh hưởng | Cách giảm thiểu |
|---|---|---|
| Model 14B Q4 review kém chính xác hơn model cloud lớn | Bỏ sót bug hoặc báo sai | Giữ vai trò cảnh báo tham khảo, không thay review của người, ít nhất ở Giai đoạn 1 |
| RAM DDR3 băng thông thấp | Nếu offload sang CPU sẽ rất chậm | Luôn nhồi tối đa layer lên GPU, theo dõi VRAM trước khi chạy |
| Windows: Sysmem Fallback âm thầm dùng RAM khi đầy VRAM | Chậm bất thường, khó chẩn đoán | Đặt "Prefer No Sysmem Fallback" trong NVIDIA Control Panel (mục 5.2) |
| Convention không nhất quán giữa 3 stack | AI học lệch, áp dụng sai giữa các app | Bắt buộc mọi token/breakpoint/naming lấy từ `packages/design-tokens` |
