# Hướng dẫn cài đặt và sử dụng

Tài liệu này viết bằng tiếng Việt, tập trung vào tình huống thực tế nhất: **bạn đã có
sẵn một repo landing page (hoặc bất kỳ project frontend nào) và muốn giao cho agent
tự kiểm tra / sửa lỗi qua đêm.**

---

## 1. Cài đặt

### 1.1. Yêu cầu

- Python 3.10 trở lên
- Node.js + npm (nếu project bạn check là project Node/React/Vue/...)
- Git
- [Ollama](https://ollama.com) đã cài và đang chạy
- Card đồ họa: hướng dẫn này giả định bạn có RTX 3060 12GB (như cấu hình mặc định
  trong `config.yaml`) — nếu bạn có GPU khác, xem mục 1.3.

### 1.2. Cài Python package + Playwright

```bash
cd autonomous-coding-agent
pip install -r requirements.txt --break-system-packages
playwright install chromium
```

Nếu muốn dùng lệnh `ai-agent` ngắn gọn thay vì `python -m cli.main`, cài thêm:

```bash
pip install -e . --break-system-packages
```

### 1.3. Cài model cho Ollama

```bash
ollama pull qwen2.5-coder:7b      # model chính, dùng để đọc/sửa code
ollama pull qwen2.5-vl:7b         # model vision, dùng để "nhìn" screenshot (tùy chọn)
ollama serve                       # nếu Ollama chưa chạy sẵn dạng service
```

Nếu GPU của bạn ít hơn 12GB VRAM, dùng bản nhỏ hơn (`qwen2.5-coder:3b` hoặc tương tự) —
model càng lớn thì chất lượng sửa code càng tốt nhưng càng chậm và dễ tràn sang chạy
trên CPU (chậm hơn rất nhiều).

Nếu bạn **không** cần bước kiểm tra hình ảnh (visual review), có thể bỏ trống
`vision_model` trong config — bước đó sẽ tự động được bỏ qua, không báo lỗi.

### 1.4. Kiểm tra môi trường trước khi dùng

**Luôn chạy lệnh này trước khi giao task lần đầu, hoặc sau khi đổi máy:**

```bash
python -m cli.main doctor
```

Lệnh này kiểm tra: Ollama có đang chạy không, model đã pull chưa, Node/npm/Git/Python
có sẵn không, Playwright Chromium chạy được không, và thư mục workspace có ghi được
không. Nếu có dòng nào `[FAIL]`, sửa xong mới chạy task thật.

Ví dụ kết quả khi mọi thứ ổn:

```
Environment check:

  [OK  ] ollama: Ollama OK — coding_model=qwen2.5-coder:7b, vision_model=qwen2.5-vl:7b
  [OK  ] node: /usr/bin/node (v22.22.2)
  [OK  ] npm: /usr/bin/npm (10.9.7)
  [OK  ] git: /usr/bin/git (git version 2.43.0)
  [OK  ] python3: /usr/bin/python3 (Python 3.12.3)
  [OK  ] playwright-chromium: chromium launches successfully
  [OK  ] workspace: /path/to/workspace exists and is writable

All checks passed.
```

---

## 2. Cách agent "nhìn" vào một project

Agent chỉ được phép đọc/ghi/chạy lệnh bên trong **một thư mục duy nhất**, gọi là
`workspace.root`, được khai báo trong `config.yaml`:

```yaml
workspace:
  root: ./workspace
```

Đây là **con đường duy nhất** để chỉ định "project nào agent sẽ làm việc" — không có
tham số dòng lệnh nào khác để trỏ vào project. Điều này quan trọng vì nó cũng chính
là ranh giới an toàn: agent tuyệt đối không đọc/ghi được gì bên ngoài thư mục này.

---

## 3. Cách cho agent check một repo landing page có sẵn

Đây là câu trả lời trực tiếp cho câu hỏi của bạn. Có **hai cách**, tùy bạn muốn agent
làm việc trực tiếp trên repo gốc hay trên một bản copy.

### Cách A — Trỏ thẳng vào repo có sẵn (khuyến nghị)

Đây là cách dùng bình thường nhất: sửa `workspace.root` trỏ thẳng vào đường dẫn repo
landing page của bạn.

```yaml
# config.yaml (hoặc một file config riêng, xem mục 3.3)
workspace:
  root: /duong/dan/toi/landing-page-cua-ban
```

Sau đó chạy:

```bash
python -m cli.main run tasks/kiem-tra-landing-page.md
```

**Agent sẽ tự động:**

1. Kiểm tra thư mục đó đã là git repo chưa.
   - **Nếu chưa có `.git`** (ví dụ bạn mới tải/giải nén landing page về, chưa init
     git) — agent **tự động chạy `git init` + commit lần đầu** để có một điểm mốc
     sạch trước khi bắt đầu sửa. Đây là tính năng đã được thêm vào để xử lý đúng
     tình huống "repo landing page có sẵn nhưng chưa phải git repo".
   - **Nếu đã là git repo nhưng đang có thay đổi chưa commit** (working tree dirty)
     — agent **sẽ dừng lại ngay, báo `BLOCKED`**, không tự ý commit hộ bạn. Lý do:
     nếu agent tự commit luôn cả những thay đổi dở dang của bạn, nhánh `git diff`
     cuối cùng sẽ lẫn lộn giữa code của bạn và code agent sửa, rất khó review.
     → Bạn cần `git commit` hoặc `git stash` các thay đổi đang dang dở trước, rồi
     chạy lại.
   - **Nếu đã là git repo và sạch (clean)** — agent tạo một nhánh mới
     `ai/<ten-task>-<thoi-gian>` và làm việc hoàn toàn trên nhánh đó, không đụng vào
     `main`/`master`.

2. Đọc `package.json`, `README`, cấu trúc thư mục để hiểu project đang dùng
   framework/stack gì (React, Vue, HTML thuần, Tailwind, ...) — **không tự ý đổi
   framework hay kiến trúc có sẵn**.

3. Bắt đầu vòng lặp: lập kế hoạch → sửa code → chạy lint/build → khởi động dev
   server → mở bằng trình duyệt thật (Playwright) ở nhiều kích thước màn hình →
   chụp ảnh màn hình → tự đánh giá → sửa tiếp nếu còn lỗi → lặp lại.

### Cách B — Làm việc trên một bản copy (an toàn hơn nếu bạn chưa tin tưởng agent)

Nếu bạn muốn giữ nguyên repo gốc và chỉ để agent thử nghiệm trên bản sao:

```bash
cp -r /duong/dan/toi/landing-page-cua-ban ./workspace
```

rồi dùng `workspace.root: ./workspace` (giá trị mặc định, không cần sửa config).
Khi agent hoàn tất, bạn tự so sánh/merge ngược lại repo gốc nếu ưng ý.

### 3.1. Nếu project không có sẵn các lệnh `npm run lint` / `npm run build`

Agent (vai trò Tester) tự động đọc `scripts` trong `package.json` và chỉ chạy những
lệnh nào **thực sự tồn tại**:

- Có `"build": "..."` → chạy `npm run build`, kết quả PASS/FAIL theo exit code thật.
- Không có `"lint"` → bước lint được báo là `SKIPPED` (bỏ qua), **không bao giờ báo
  nhầm là PASS** khi thực ra chưa chạy gì cả. Đây là quy tắc cứng trong code, không
  phải chỉ dặn LLM.

→ Nếu landing page của bạn là HTML/CSS/JS thuần, không có bước build, bạn vẫn dùng
được — chỉ là các bước build/lint sẽ hiện `SKIPPED` trong báo cáo, còn bước quan
trọng nhất là **browser test + responsive test** vẫn chạy đầy đủ vì nó không phụ
thuộc vào `package.json`.

### 3.2. Nếu landing page không có "dev server" (chỉ là file HTML tĩnh)

Agent khởi động app bằng cách chạy `npm run dev` (hoặc `pnpm dev`/`yarn dev` tùy agent
phát hiện bạn dùng package manager nào), sau đó mở trình duyệt tới một URL cố định để
kiểm tra. Nếu project của bạn **chỉ là các file HTML tĩnh, không có script `dev`**,
thêm đoạn sau vào `package.json` của project (tạo file mới nếu project chưa có):

```json
{
  "scripts": {
    "dev": "python3 -m http.server 3000"
  }
}
```

**Về URL agent sẽ mở để kiểm tra:** mặc định là `http://localhost:3000`. Có hai cách
đổi, ưu tiên theo thứ tự sau (cách đặt trong task luôn thắng cách đặt trong config):

1. **Đổi trong file task** — thêm một section `## Dev Server` với URL ở dòng đầu tiên:

   ```markdown
   ## Dev Server

   http://localhost:8080
   ```

   Cách này phù hợp khi mỗi task/project dùng một cổng khác nhau.

2. **Đổi trong `config.yaml`** — sửa giá trị mặc định dùng chung cho mọi task:

   ```yaml
   agent:
     dev_server_url: http://localhost:8080
   ```

Không có tham số dòng lệnh (`--port` hay tương tự) để đổi URL này — phải khai báo
qua task hoặc config như trên.

### 3.3. Viết task cho việc "check landing page"

Tạo file trong `tasks/`, ví dụ `tasks/kiem-tra-landing-page.md`:

```markdown
# Task

Kiểm tra và sửa các lỗi hiển thị, lỗi console, và lỗi responsive trên landing page
hiện có trong repo. Không thêm tính năng mới, không đổi nội dung — chỉ sửa lỗi kỹ
thuật và lỗi hiển thị.

## Requirements

- Giữ nguyên kiến trúc, framework, và nội dung hiện có của project.
- Không thêm dependency mới trừ khi thực sự cần thiết để sửa lỗi.
- Không sửa các trang/section đang hoạt động tốt.

## Viewports

- 375x812
- 768x1024
- 1440x900

## Acceptance Criteria

- Build passes (nếu project có bước build).
- Không có lỗi console.
- Không bị tràn ngang (horizontal overflow) ở bất kỳ kích thước màn hình nào.
- Layout mobile hiển thị đúng.
- Layout tablet hiển thị đúng.
- Layout desktop hiển thị đúng.
- Các trang khác trong site vẫn hoạt động bình thường.
```

Rồi chạy:

```bash
python -m cli.main run tasks/kiem-tra-landing-page.md --config config.yaml
```

(Nếu bạn có nhiều project khác nhau, nên tạo **một file config riêng cho mỗi
project** — ví dụ `config.landing-page-a.yaml`, `config.landing-page-b.yaml` — vì
`workspace.root` chỉ trỏ được vào một thư mục. Dùng `--config` để chọn file tương
ứng khi chạy.)

---

## 4. Theo dõi khi agent đang chạy

```bash
# xem trạng thái tất cả task đã/đang chạy
python -m cli.main status

# xem log dạng người đọc được, theo thời gian thực
tail -f logs/<task-id>/agent.log

# xem log dạng JSON có cấu trúc (dùng để phân tích tự động)
tail -f logs/<task-id>/agent.jsonl
```

Mỗi vòng lặp (iteration) sẽ ghi vào `logs/<task-id>/iteration-NNN/`:

| File | Nội dung |
|---|---|
| `tool_calls.json` | tất cả lệnh/tool agent đã gọi trong vòng này |
| `test-results.json` | kết quả lint/build/test |
| `review.json` | đánh giá cuối cùng của Reviewer |
| `screenshots/*.png` | ảnh chụp màn hình từng kích thước viewport |
| `*.stdout.log`, `*.stderr.log` | log của dev server và các lệnh shell |

---

## 5. Sáng hôm sau — quy trình review

```bash
python -m cli.main report <task-id>       # đọc báo cáo tổng kết
cd /duong/dan/repo && git log --oneline    # xem các commit agent đã tạo (nếu auto_commit bật)
git diff main...ai/<task-id>-<timestamp>   # xem toàn bộ thay đổi trước khi merge
```

Báo cáo (`reports/<task-id>.md`) luôn kết thúc bằng một trong hai trạng thái:

- **`READY_FOR_HUMAN_REVIEW`** — agent cho rằng đã đạt các tiêu chí, nhưng **bạn vẫn
  phải tự kiểm tra UI và đọc diff trước khi merge**. Agent không bao giờ tự nhận là
  "hoàn hảo" hay "sẵn sàng production" — chỉ nói là sẵn sàng để bạn review.
- **`BLOCKED`** — agent không tự sửa được sau nhiều lần thử, hoặc gặp lỗi kỹ thuật.
  Báo cáo sẽ ghi rõ lý do bị chặn, lỗi cuối cùng, và đề xuất bạn nên kiểm tra thủ
  công chỗ nào.

Agent **không bao giờ tự `git push`** — không có lệnh nào trong code làm việc này,
kể cả khi bạn bật `auto_push: true` trong config. Đây là giới hạn cố ý, không phải
thiếu sót.

---

## 6. Các câu hỏi thường gặp khi check landing page có sẵn

**Hỏi: Landing page của tôi dùng nhiều trang (không chỉ 1 trang chủ), agent có tự
kiểm tra hết các trang không?**
Trả lời: Phiên bản hiện tại, bước browser test chỉ kiểm tra **một URL** mỗi lần chạy
(mặc định `http://localhost:3000`, đổi được qua `## Dev Server` trong task hoặc
`agent.dev_server_url` trong config — xem mục 3.2). Nếu landing page có nhiều trang
cần kiểm tra riêng, hãy liệt kê rõ các đường dẫn trong phần `## Requirements` của
task để Planner/Coder biết mà kiểm tra thêm qua công cụ `browser_check` — đây không
phải bước tự động lặp qua toàn site.

**Hỏi: Nếu tôi chạy lại agent nhiều lần cho cùng một repo, nhánh git có bị trùng
không?**
Trả lời: Không — tên nhánh luôn có timestamp (`ai/<task>-<YYYYMMDD-HHMMSS>`), mỗi lần
chạy là một nhánh mới, nhánh cũ không bị xóa hay ghi đè.

**Hỏi: Repo landing page của tôi không có `.git`, tôi có cần tự `git init` trước
không?**
Trả lời: Không cần — agent tự làm việc này (xem mục 3, Cách A, bước 1). Bạn chỉ cần
tự `git init` trước nếu muốn kiểm soát commit đầu tiên theo ý mình.

**Hỏi: Tôi có project Python/Django, không phải Node — dùng được không?**
Trả lời: Bước lint/build/test hiện tại chỉ tự động phát hiện qua `package.json`
(dự án Node). Với project không phải Node, các bước đó sẽ hiện `SKIPPED` (không
chạy được gì) thay vì báo sai là PASS — đây là giới hạn đã ghi trong README, phần
"Limitations". Bước kiểm tra trình duyệt/responsive vẫn hoạt động bình thường vì nó
không phụ thuộc vào `package.json`.

---

## 7. Tóm tắt lệnh CLI

```bash
python -m cli.main run <duong-dan-task.md>   # chạy một task
python -m cli.main status                     # xem trạng thái tất cả task
python -m cli.main logs <task-id>             # xem log của một task
python -m cli.main report <task-id>           # xem báo cáo cuối
python -m cli.main stop <task-id>             # dừng task đang chạy
python -m cli.main resume <task-id>           # xem trạng thái lần chạy gần nhất
                                                # (chưa hỗ trợ resume giữa chừng thật sự)
python -m cli.main doctor                     # kiểm tra môi trường
python -m cli.main --config <file> <lệnh>     # dùng file config khác (nhiều project)
```
