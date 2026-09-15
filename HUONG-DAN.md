# SrtGen — Hướng dẫn sử dụng

SrtGen là công cụ làm phụ đề cho video tiếng Trung.

Bạn đưa cho nó một link YouTube hoặc một file video, nó trả lại **hai file phụ đề**:

| File | Nội dung |
| --- | --- |
| `<tên>.srt` | Tiếng Trung, mỗi câu có thêm một dòng pinyin bên dưới |
| `<tên>_vi.srt` | Tiếng Việt, số câu và thời gian khớp từng câu với file trên |

Máy tự làm hết. Bạn chỉ ngồi soát lại.

---

## Mục lục

1. [Cài lần đầu](#1-cài-lần-đầu)
2. [Máy không cho mở file](#2-máy-không-cho-mở-file)
3. [Mở app](#3-mở-app)
4. [Tạo phụ đề từ link YouTube](#4-tạo-phụ-đề-từ-link-youtube)
5. [Tạo phụ đề từ file có sẵn trong máy](#5-tạo-phụ-đề-từ-file-có-sẵn-trong-máy)
6. [Bạn nhận được những file gì](#6-bạn-nhận-được-những-file-gì)
7. [Tab Sửa phụ đề](#7-tab-sửa-phụ-đề)
8. [Tab Kiểm tra file](#8-tab-kiểm-tra-file)
9. [Tab Tên riêng](#9-tab-tên-riêng)
10. [Mở trong Aegisub](#10-mở-trong-aegisub)
11. [Chờ bao lâu](#11-chờ-bao-lâu)
12. [Tab Cài đặt](#12-tab-cài-đặt)
13. [Gặp lỗi này thì làm gì](#13-gặp-lỗi-này-thì-làm-gì)
14. [Gỡ khỏi máy](#14-gỡ-khỏi-máy)

---

## 1. Cài lần đầu

Bạn cần: một máy Mac, mạng Internet, ổ đĩa còn trống ít nhất **8GB**, và khoảng
30 phút. Không cần biết gì về máy tính.

**Bước 1.** Giải nén file SrtGen vừa tải về, rồi mở thư mục vừa giải nén.
(Tải từ GitHub thì thư mục có thể mang tên `support-gen-sub-main`, không sao.)

**Bước 2.** Bấm chuột phải vào file **`SrtGen.command`** nằm ngay ngoài cùng →
chọn **Open** (máy để tiếng Việt thì chữ này là **Mở**). Một hộp thoại hiện ra hỏi
bạn có chắc muốn mở không: bấm **Open** (**Mở**) thêm một lần nữa.

> `SrtGen.command` là file duy nhất bạn cần nhớ. Nó chép chương trình vào một chỗ
> cố định trong máy rồi mới cài, nên **cài xong thì thư mục tải về xoá lúc nào cũng
> được**. Có bản mới thì tải về và bấm lại đúng file này: nó tự cập nhật. App bị hỏng
> thì cũng bấm lại file này: nó tự kiểm tra và cài lại phần thiếu.

> Đừng bấm đúp ở lần đầu tiên: bấm đúp thì hộp thoại của máy không có nút Open.
> Bấm chuột phải rồi chọn Open thì hộp thoại có nút đó. Nếu vẫn không mở được,
> xem [mục 2](#2-máy-không-cho-mở-file).

**Bước 3.** Một cửa sổ chữ đen hiện ra. Nhấn Enter khi nó hỏi *"Bắt đầu?"*.

Đến giữa chừng nó hỏi chọn mô hình nghe. Cứ nhấn Enter để lấy lựa chọn 1
(cân bằng nhất, khuyên dùng).

Rồi ngồi chờ. Cửa sổ sẽ chạy 8 bước, mỗi bước có dấu ✓ khi xong.
Bạn có thể để đó đi làm việc khác, chỉ đừng đóng cửa sổ.

Xong sẽ thấy dòng chữ **CÀI ĐẶT XONG** và một biểu tượng **SrtGen** xuất hiện
trên màn hình nền. Ngay sau đó nó hỏi *"Mở SrtGen ngay bây giờ?"* — nhấn Enter
là app mở luôn.

Nếu thay vào đó thấy dòng **CÀI XONG NHƯNG CÒN MỤC CHƯA ĐẠT**: kéo lên xem bảng
kiểm tra ngay phía trên. Dòng nào có dấu ✗ thì ngay dưới nó có câu *"Cách xử lý"*.
Thường chỉ cần chạy lại `SrtGen.command` là đủ. Lúc này máy không hỏi mở app;
app vẫn mở được bằng biểu tượng **SrtGen**, chỉ có thể thiếu chức năng.

Nếu cửa sổ dừng ở dòng đỏ **KHÔNG CÀI TIẾP ĐƯỢC**: đọc câu ngay dưới dòng đó
(thường là mất mạng hoặc ổ đĩa thiếu chỗ trống), sửa xong rồi mở lại
`SrtGen.command`. Nó chạy tiếp từ đúng bước bị dừng.

### Máy có bị đụng gì không?

Không. Máy sẽ **không hỏi mật khẩu**. Mọi thứ được đặt vào đúng một thư mục riêng:

```
~/Library/Application Support/SrtGen
```

Ngoài thư mục đó chỉ có thêm biểu tượng **SrtGen** trên màn hình nền và một lối
tắt nhỏ trong `~/Library/Caches/SrtGen` trỏ về chỗ để mô hình nghe.
Phần mềm sẵn có của máy không bị sửa gì. Muốn gỡ thì dùng file có sẵn
`GoCaiDat.command` — nó dọn cả ba chỗ trên (xem [mục 14](#14-gỡ-khỏi-máy)).

### Cài dở bị đứt mạng thì sao?

Không sao. Bấm chuột phải vào `SrtGen.command` → **Open** lần nữa.
Nó tự nhận ra bước nào đã xong, bỏ qua, và **chạy tiếp từ đúng chỗ bị đứt**.
Phần đã tải về không phải tải lại.

---

## 2. Máy không cho mở file

Có hai chuyện khác nhau hay bị nhầm là một. Nhìn đúng câu máy báo rồi làm theo
đúng phần bên dưới.

### 2A. Máy báo *"không mở được vì không rõ nhà phát triển"*

Đây là chuyện bình thường của máy Mac, không phải máy hỏng, không phải virus.
macOS chặn mọi file tải từ Internet cho tới khi bạn nói "tôi cho phép".

**Cách 1 — bấm chuột phải (làm cách này trước)**

1. Bấm **chuột phải** vào file `SrtGen.command`.
2. Chọn **Open** (máy để tiếng Việt: **Mở**).
3. Hộp thoại hiện ra, bấm **Open** (**Mở**) một lần nữa.

Chỉ phải làm một lần cho mỗi file. Từ lần sau bấm đúp là chạy.

**Cách 2 — vào phần Cài đặt hệ thống**

Nếu bấm chuột phải mà không thấy chữ Open, hộp thoại chỉ có nút **Done** và
**Move to Trash**, hoặc máy vẫn không cho (máy chạy macOS 15 trở lên hay gặp
cảnh này, khi đó luôn phải làm cách 2):

1. Mở  **> System Settings** (máy cũ gọi là System Preferences; máy để tiếng
   Việt là **Cài đặt hệ thống**).
2. Chọn **Privacy & Security** (**Quyền riêng tư & Bảo mật**). Máy cũ thì chọn
   **Security & Privacy**, thẻ **General**.
3. Kéo xuống gần cuối. Sẽ thấy một dòng nói về `SrtGen.command`.
4. Bấm nút **Open Anyway** (**Vẫn mở**) bên cạnh dòng đó. Máy có thể hỏi mật
   khẩu đăng nhập máy: đó là mật khẩu của chính bạn, không phải của SrtGen.
5. Quay lại bấm đúp vào file. Hộp thoại hỏi lại thì bấm **Open**.

### 2B. Bấm đúp mà **không có gì xảy ra**, hoặc máy báo *"The file couldn't be opened"*

Đây là chuyện khác hẳn: file đã **mất quyền chạy** trên đường đi tới máy bạn —
gửi qua email, giải nén bằng phần mềm lạ, tải từ kho mã nguồn, hay chép qua ổ
USB định dạng Windows. Nội dung file vẫn nguyên vẹn, chỉ là máy không cho bấm đúp.
Máy cũng có thể báo *"could not be executed because you do not have appropriate
access privileges"* (không đủ quyền để chạy): đó cũng là chuyện này.

Trong thư mục `installer` có sẵn một file để chữa đúng chuyện này:
**`CAP-QUYEN.command`**. Nó gắn lại quyền chạy cho tất cả các file `.command`
cùng thư mục. Nó không cài gì, không xoá gì, không hỏi mật khẩu.

**Cách 1** — bấm chuột phải vào `CAP-QUYEN.command` → **Open** → **Open**.

**Cách 2 — luôn luôn được, kể cả khi chính file đó cũng mất quyền chạy:**

1. Mở ứng dụng **Terminal** (bấm ⌘ + phím cách, gõ `Terminal`, nhấn Enter).
2. Gõ đúng bốn chữ cái rồi **một dấu cách**, chưa nhấn Enter:

   ```
   bash 
   ```

3. **Kéo file `CAP-QUYEN.command` thả vào cửa sổ Terminal.** Đường dẫn tự hiện ra,
   thành một dòng trông như thế này:

   ```
   bash /Users/ban/Downloads/SrtGen/installer/CAP-QUYEN.command
   ```

4. Nhấn Enter.

Màn hình sẽ liệt kê từng file kèm dấu ✓. Xong rồi thì quay lại bấm đúp
`SrtGen.command`.
Chỉ phải làm một lần.

> Nếu vẫn còn file báo ✗: thư mục SrtGen đang nằm ở nơi máy không cho ghi
> (ổ USB Windows, ổ đĩa mạng, hoặc file `.dmg` chưa chép ra). Chép cả thư mục
> `SrtGen` vào **Documents** hoặc **Desktop** rồi làm lại.

### Lần đầu bấm biểu tượng SrtGen trên màn hình nền

Máy sẽ hỏi *"SrtGen muốn điều khiển Terminal"* (bản tiếng Anh ghi *"SrtGen"
wants access to control "Terminal"*). Bấm **OK** (có máy ghi **Allow**).
Không bấm OK thì biểu tượng không mở được app.

Lỡ bấm **Don't Allow**: cứ mở app bằng `KhoiDong.command` trong thư mục
`installer`, y hệt nhau. Muốn biểu tượng chạy lại thì vào **System Settings >
Privacy & Security > Automation**, tìm dòng **SrtGen** và bật công tắc **Terminal**.

---

## 3. Mở app

Bấm đúp vào biểu tượng **SrtGen** trên màn hình nền.
(Hoặc bấm đúp vào `KhoiDong.command` trong thư mục `installer` — y hệt nhau.)

Hai thứ sẽ hiện ra:

- Một **cửa sổ chữ đen**. Đó là động cơ của app. **Đừng đóng nó** trong lúc đang dùng.
- Một **trang web** trong trình duyệt. Đó là chỗ bạn làm việc.

Trang này chạy ngay trên máy bạn, không phải trên mạng. Tắt mạng vẫn dùng được
các phần không cần tải video.

**Tắt app:** đóng cửa sổ chữ đen, hoặc bấm Control + C trong cửa sổ đó.
Đóng cửa sổ thì máy hỏi bằng tiếng Anh *"Do you want to terminate running
processes in this window?"* — bấm **Terminate** (máy để tiếng Việt: **Chấm dứt**).
Đó là câu hỏi bình thường của
Terminal, không phải lỗi.

App có đúng **6 tab**, xếp ngang trên đầu trang:

| Tab | Dùng để |
| --- | --- |
| **Tạo phụ đề** | Dán link YouTube hoặc chọn file, bấm Bắt đầu, rồi chờ |
| **Sửa phụ đề** | Nghe lại từng câu và sửa tại chỗ |
| **Kiểm tra file** | Soi lỗi một file `.srt` có sẵn, sửa tự động và tải về |
| **Tên riêng** | Bảng tên nhân vật của từng phim |
| **Cài đặt** | Khoá API, mô hình nghe, nơi lưu file, kiểm tra máy |
| **Hướng dẫn** | Bản đọc nhanh của tài liệu này, ngay trong app |

Không có tab nào tên là "Đang chạy": lúc máy đang làm việc, màn hình theo dõi
tiến độ hiện ngay **trong chính tab Tạo phụ đề**.

---

## 4. Tạo phụ đề từ link YouTube

1. Vào tab **Tạo phụ đề**.
2. Dán link YouTube vào ô ở giữa trang (ô có dòng chữ *"Dán link YouTube, hoặc
   kéo thả file video / âm thanh vào đây"*).
3. Bấm **Bắt đầu**.

Nút **Bắt đầu** mờ đi cho tới khi bạn dán link hoặc chọn file — đó là bình thường.
Chọn nhầm thì bấm **Bỏ chọn** để làm lại.

### Muốn đổi cách chạy trước khi bấm Bắt đầu

Bấm mở phần **Cài đặt nâng cao** ngay dưới nút Bắt đầu. Trong đó có:

- **Chất lượng gỡ băng** — chọn mô hình nghe. Xem [mục 12](#12-tab-cài-đặt).
- **Kiểu nội dung** — **Phim / phim ngắn** hay **Tin tức / phóng sự**.
  Quyết định cách chia dòng phụ đề cho vừa nhịp nói.
- **Tuỳ chọn khác** — bốn ô tick:
  - *Nhờ AI soát tên riêng và chữ khó* — cần khoá API ở tab Cài đặt.
  - *Dịch thêm một file phụ đề tiếng Việt* — bật sẵn.
  - *Tách giọng khỏi nhạc nền* — làm chậm khoảng 2 lần.
  - *Xuất thêm bản .ass cho Aegisub*.

Để nguyên hết cũng chạy tốt.

### Lúc đang chạy

Trang chuyển sang màn hình theo dõi — **vẫn nằm trong tab Tạo phụ đề** — và báo
từng bước:

```
Bước  1/10  Tải video và tách âm thanh
Bước  2/10  Xử lý âm thanh
Bước  3/10  Nghe và gỡ băng            <- bước lâu nhất
Bước  4/10  Dọn kết quả gỡ băng
Bước  5/10  Chia câu theo nhịp thoại
Bước  6/10  Tách cụm và sinh pinyin
Bước  7/10  Chuẩn hoá định dạng
Bước  8/10  Nhờ AI soát tên riêng và chữ khó
Bước  9/10  Dịch sang tiếng Việt
Bước 10/10  Xuất file
```

Bước 3 chiếm gần hết thời gian.

- Muốn dừng giữa chừng: bấm nút **Dừng** màu đỏ ở góc phải.
- Muốn xem máy đang làm gì: mở phần **Nhật ký chi tiết** ở cuối màn hình.
  Trong đó có nút **Chép nhật ký** để gửi cho người hỗ trợ.

### Khi xong

App hiện màn hình kết quả với năm nút to:

- **Xem trước và sửa** — mở thẳng vào trình sửa (xem [mục 7](#7-tab-sửa-phụ-đề)).
- **Tải file .srt** — bản tiếng Trung kèm pinyin.
- **Tải file _vi.srt** — bản tiếng Việt, cùng mốc thời gian.
- **Mở thư mục** — mở Finder ngay tại chỗ chứa file.
- **Xem báo cáo** — thống kê và danh sách chỗ máy chưa chắc.

Muốn làm video kế tiếp: bấm **Làm video khác** ở cuối trang.

> **Lưu ý pháp lý:** chỉ tải video mà bạn có quyền dùng. Tải video của người
> khác về dùng lại có thể vi phạm điều khoản của YouTube và bản quyền.

### Máy đang chạy mà lỡ đóng cửa sổ trình duyệt?

Không mất gì. Việc chạy ở phía chương trình chứ không phải trong trình duyệt.
Mở lại trang SrtGen là app **tự tìm lại việc đang chạy dở** và hiện tiếp thanh
tiến trình ở đúng chỗ nó đang làm — bạn không phải bấm gì cả.

Mất mạng chốc lát cũng vậy: app hiện dòng *"Mất kết nối với chương trình"* rồi
tự nối lại.

> Nhưng **đừng tắt hẳn chương trình SrtGen** (đóng cửa sổ chữ đen / thoát app)
> khi đang chạy dở. Tắt chương trình là việc dừng luôn, và phải bấm **Bắt đầu**
> lại từ đầu.

---

## 5. Tạo phụ đề từ file có sẵn trong máy

Cách này không cần mạng (trừ khi bạn bật phần dịch bằng AI).

1. Vào tab **Tạo phụ đề**.
2. Bấm **Chọn file từ máy**, hoặc kéo thả file vào ô lớn giữa trang.
3. Bấm **Bắt đầu**.

Nút **Chọn file từ máy** và kéo thả nhận: `.mp4`, `.mkv`, `.mov`, `.avi`,
`.webm`, `.mp3`, `.wav`, `.m4a`, `.aac`, `.flac`, `.ogg`.

File `.m4v`, `.ts`, `.opus`, `.wma` thì nút chọn file không nhận (kéo thả thì app
báo *"Chưa nhận loại file này"*). Với những file này, **dán đường dẫn của file**
vào ô nhập — đi đường đó thì app đọc được. Cách lấy đường dẫn ở ngay dưới.

File rất nặng thì **dán thẳng đường dẫn của file** vào ô nhập sẽ nhanh hơn kéo thả.
Cách lấy đường dẫn: bấm chuột phải vào file trong Finder, giữ phím Option rồi
chọn *"Copy … as Pathname"*.

Các bước còn lại giống hệt mục 4, chỉ bỏ bước tải video nên nhanh hơn vài phút.

---

## 6. Bạn nhận được những file gì

Sau mỗi lần chạy, trong thư mục kết quả có:

| File | Là gì | Dùng khi nào |
| --- | --- | --- |
| `<tên>.srt` | Tiếng Trung + pinyin | **File giao đi** |
| `<tên>_vi.srt` | Tiếng Việt | **File giao đi** |
| `<tên>_song-ngu.ass` | Cả hai thứ tiếng chồng lên nhau | **Để soát trong Aegisub** |
| `<tên>.report.html` | Bản báo cáo, mở bằng trình duyệt | Xem chỗ nào máy chưa chắc |
| `<tên>.bundle.json` | Dữ liệu cho máy đọc | Không cần quan tâm |

Có thêm `<tên>.ass` nếu bạn bật *Xuất thêm bản .ass cho Aegisub*.

Thư mục kết quả nằm ở đâu thì xem dòng *"Kết quả đang được ghi vào: …"* ngay
dưới ô **Thư mục lưu kết quả** trong tab Cài đặt (ô để trống là app dùng thư mục
mặc định, đường dẫn thật vẫn hiện ở dòng đó), hoặc bấm nút **Mở thư mục** ở màn
hình kết quả.

### File có chữ `.truoc-` trong tên: bản cũ được cất, không phải rác

Có lúc app phải ghi lại một file kết quả đã có sẵn, ví dụ khi bạn chạy lại một
phim. Nếu file cũ **khác** bản mới, app **không ghi đè** lên nó. App đổi tên file
cũ trước, chèn thêm chữ `.truoc-` và giờ cất vào tên:

| File mới | Bản cũ được cất |
| --- | --- |
| `Phim.srt` | `Phim.truoc-20260910-183000.srt` |
| `Phim_vi.srt` | `Phim_vi.truoc-20260910-183000.srt` |

Dãy số là ngày giờ cất: `20260910-183000` là ngày 10/09/2026, lúc 18 giờ 30.
File `.ass` và file báo cáo cũng được cất theo cách này.

- Đây là **bản cũ của bạn**, có thể còn những câu bạn đã sửa tay. Không phải rác.
- App **không bao giờ tự xoá** những file này.
- Còn cần thì cứ giữ. Chỉ xoá khi bạn chắc chắn không cần nữa.
- Nội dung không đổi thì app không cất, nên thư mục không đầy lên vô ích.
- Muốn lấy lại một câu cũ: mở file `.truoc-` trong [tab Sửa phụ đề](#7-tab-sửa-phụ-đề)
  (dán đường dẫn của nó vào ô **Hoặc mở file có sẵn trên máy**), rồi chép câu đó
  sang bản mới.

Khi bạn bấm **Chạy lại với bảng tên mới**, bản bạn đã sửa tay cũng được cất theo
đúng cách đó: thành file `.truoc-` nằm cạnh file mới. App sẽ báo rõ tên file đã cất.

Một câu trong file `.srt` trông như sau:

```
12
00:01:22,100 --> 00:01:28,980
你 怎么 才 来？
Nǐ zěnme cái lái？
```

Câu tương ứng trong `_vi.srt`:

```
12
00:01:22,100 --> 00:01:28,980
Sao giờ này anh mới tới?
```

Số thứ tự và thời gian **giống hệt nhau** ở hai file. Đó là điều bắt buộc:
nhờ vậy ai mở file nào cũng thấy đúng câu đó.

---

## 7. Tab Sửa phụ đề

Đây là phần quan trọng nhất. Máy nghe rất khá nhưng không bao giờ đúng 100%.

### Các đường vào

- Vừa chạy xong một video: bấm **Xem trước và sửa** ở màn hình kết quả.
- Vào thẳng tab **Sửa phụ đề** rồi bấm **Mở lại kết quả gần nhất**
  (nút này chỉ hiện khi máy có sẵn một kết quả để mở).
- Hoặc, cũng trong tab đó, dán đường dẫn một file `.srt` trên máy vào ô
  **Hoặc mở file có sẵn trên máy** rồi bấm **Mở file này**.
  Có file `_vi.srt` nằm cùng thư mục thì app mở luôn cả hai.
  Mở kiểu này thì bấm **Lưu** là ghi thẳng vào đúng file đó.
- Hoặc bấm **Chọn file .srt…** rồi chọn file. Tiện hơn, nhưng đọc kỹ phần ngay dưới đây.

### Mở bằng nút Chọn file .srt… thì bấm Lưu và tải về

Trình duyệt không cho app biết file gốc của bạn nằm ở đâu. Vì vậy app chép nội
dung vào thư mục làm việc của nó và sửa trên **bản sao** đó.
**File gốc của bạn không đổi.**

Khi mở kiểu này, nút **Lưu** đổi tên thành **Lưu và tải về**. Bấm nút đó thì app:

1. Lưu bản sửa vào bản sao.
2. Tải ngay file `.srt` về máy bạn, và cả file `_vi.srt` nếu có. File tải về nằm
   ở thư mục **Tải về** (Downloads), giống mọi file tải từ trình duyệt.
3. Hiện dòng **Đã lưu vào: …** cho biết bản sao nằm ở đâu, kèm nút **Mở thư mục**.

Sau đó hãy dùng **file vừa tải về**. File gốc cũ vẫn là bản chưa sửa.

Thư mục làm việc bị xoá khi gỡ app, nên đừng để bản sửa duy nhất nằm ở đó.
Muốn sửa thẳng vào file của mình thì dùng cách **dán đường dẫn** ở trên.

### Bàn làm việc: vừa xem vừa sửa

Tab này xếp như phần mềm làm phụ đề chuyên nghiệp: **bên trái** là video, dạng sóng âm
và ô sửa dòng đang chọn; **bên phải** là danh sách toàn bộ dòng. Bạn nhìn hình, nghe
tiếng, sửa chữ và chỉnh mốc ở cùng một chỗ, không phải cuộn đi cuộn lại.

**Cách làm việc nhanh nhất** — đi từng dòng từ trên xuống:

1. Bấm một dòng bên phải. Video **tự nhảy tới và phát đúng câu đó** (tick *Tự phát khi
   chọn*). Cần nghe lại: `Ctrl + Space`. Cần nghe đi nghe lại: tick *Lặp dòng* (`Ctrl + L`).
2. Sửa ngay trong ba ô bên trái: **Tiếng Trung / Pinyin / Tiếng Việt**. Gõ tới đâu chữ
   trên hình đổi tới đó. Ô Pinyin tự báo đỏ nếu số cụm lệch với dòng Hán.
3. Xong thì bấm **✓ Xong, dòng sau** hoặc `Ctrl + Enter`: dòng được đánh dấu ✓, app tự
   sang dòng kế và phát. Thanh tiến độ *Đã soát x/y* cho biết còn bao nhiêu. Bộ lọc
   **Chưa soát** ở đầu bảng liệt kê những dòng còn lại. Dấu ✓ được lưu lại, tắt app mở
   lại vẫn còn.

**Chỉnh mốc thời gian** — khi chữ hiện sớm hay muộn:

- Tua video tới lúc câu bắt đầu, bấm **Bắt đầu = vị trí video** (`Ctrl + [`); tới lúc câu
  dứt, bấm **Kết thúc = vị trí video** (`Ctrl + ]`). Lệch chút xíu: **−0,1s / +0,1s**.
- Hoặc dùng **dạng sóng** (dải ngay dưới video): vùng xanh là dòng đang chọn, chỗ sóng
  nhô lên là lúc có tiếng nói. **Kéo mép trái/phải vùng xanh** là chỉnh mốc; bấm vào chỗ
  bất kỳ trên dải là tua tới đó. Vạch đỏ là vị trí đang phát.
- Ô sửa cũng hiện độ dài dòng và **tốc độ đọc** (chữ/giây): trên 9 là hơi nhanh, trên 12
  người xem khó đọc kịp — nên tách dòng hoặc kéo dài mốc.

**Tách, gộp, xoá dòng:**

- Máy gộp hai câu làm một? Đặt con trỏ trong ô Tiếng Trung tại chỗ muốn cắt, bấm
  **Tách tại con trỏ**. Pinyin tách theo, mốc thời gian chia theo tỉ lệ chữ. Dòng sau chưa
  có tiếng Việt — bấm **Dịch lại câu này**.
- Máy cắt một câu làm hai? Chọn dòng đầu, bấm **Gộp với dòng sau**.
- **Xoá dòng** bỏ hẳn dòng đó ở cả hai bản. Mọi thao tác này đều hoàn tác được (`Ctrl + Z`).

**Hai nút trợ giúp trong ô sửa:** **Sinh lại pinyin** (sau khi sửa chữ Hán) và **Dịch lại
câu này** (AI dịch lại đúng câu đó, có nhìn các câu quanh nó và bảng tên riêng — cần khoá
API để dịch tốt; không có khoá thì dùng bản miễn phí).

**Cột Tiếng Việt toàn chữ Hán?** Nghĩa là bước dịch chưa dịch được (thường do chưa có mã
API và máy chưa có phần dịch miễn phí). Trình sửa hiện băng vàng ngay trên bảng nói rõ bao
nhiêu dòng và vì sao; bấm **Dịch lại toàn bộ tiếng Việt** là app chỉ chạy lại bước dịch, không
gỡ băng lại. Bản miễn phí của Google dịch từng câu, **chậm** (khoảng 15–20 phút cho 300 câu) và
không hiểu ngữ cảnh; dán mã API Gemini vào Cài đặt rồi bấm nút đó thì vừa nhanh hơn vừa đúng
xưng hô, tên nhân vật.

**Tìm và thay thế** (`Ctrl + H`, nút kính lúp cạnh nút Lưu): một tên riêng bị viết lệch ở
nhiều dòng thì sửa một lần cho cả file, chọn được cột Hán / Pinyin / Việt.

Bấm **?** để xem bảng phím tắt đầy đủ. Các tuỳ chọn khác của khung video: **◀ Dòng trước /
Dòng sau ▶**, thanh trượt tua, tốc độ phát, **Hiện chữ** (tắt bớt Hán / Pinyin / Việt cho
thoáng), **Bảng chạy theo video**, **Thu nhỏ** khung hình để thấy nhiều dòng hơn.

Phim chạy từ **file video** trên máy hoặc từ **link YouTube** đều có hình sẵn. Phim chạy
từ các bản trước, hoặc từ file chỉ có tiếng, thì khung chỉ phát tiếng — với link YouTube,
bấm **Tải video để xem trước** là có hình, không phải chạy lại phim.

### Bảng sửa

Mỗi dòng của bảng là một câu phụ đề, có bảy cột:

| # | Thời gian | Nghe | Tiếng Trung | Pinyin | Tiếng Việt | Lỗi |
| --- | --- | --- | --- | --- | --- | --- |

**Cột Nghe** có một nút hình tam giác ▶. Bấm vào đó, app phát đúng đoạn âm thanh
của câu ấy rồi tự dừng. Đây là cách soát nhanh nhất: nghe một câu, liếc dòng chữ,
sai thì sửa ngay, đúng thì bấm mũi tên xuống sang câu sau.

**Cột Lỗi** hiện một con số nếu câu đó có lỗi. Bấm vào số để đọc chi tiết bằng
tiếng Việt.

### Sửa

Bấm vào ô cần sửa rồi gõ. Nhấn **Enter** để xác nhận, **Esc** để huỷ.

- **Sửa dòng tiếng Trung** → hiện nút nhỏ **Sinh lại pinyin cho câu này**.
  Bấm vào đó để máy viết lại dòng pinyin cho khớp.
  App cố ý không tự làm, để không xoá mất phần bạn vừa gõ tay.
- **Sửa dòng pinyin** → app đếm số cụm ngay. Lệch thì ô viền đỏ và báo
  *"Dòng Hán có 5 cụm, dòng pinyin có 4 cụm"*.
- **Sửa dòng tiếng Việt** → gõ thoải mái.
- **Sửa thời gian** → được, app báo ngay nếu hai câu đè lên nhau.

### Vạch màu ở đầu dòng nghĩa là gì

| Vạch | Nghĩa |
| --- | --- |
| Đỏ | Câu này có lỗi. Bấm vào số ở cột **Lỗi** để đọc lỗi bằng tiếng Việt. |
| Vàng | Cảnh báo nhẹ, hoặc AI đã sửa chỗ này và mời bạn nhìn qua. |
| Không màu | Máy không thấy vấn đề gì. |

### Tìm nhanh những dòng cần soát

Trên đầu bảng có bốn nút lọc: **Tất cả**, **Có lỗi**, **AI chưa duyệt**,
**Chưa có tiếng Việt**. Bên cạnh là ô **Tìm chữ trong phụ đề…** để tìm theo chữ.

### Phím tắt

| Phím | Làm gì |
| --- | --- |
| `Ctrl + Enter` | Đánh dấu ✓ đã soát, sang dòng sau (tự phát) |
| `Ctrl + ↑` / `Ctrl + ↓` | Dòng trước / dòng sau |
| `Ctrl + Space` | Nghe / xem đúng đoạn của dòng đang chọn |
| `Shift + Space` | Phát / dừng video liên tục |
| `←` / `→` | Tua lùi / tới 2 giây (khi không gõ) |
| `Ctrl + [` / `Ctrl + ]` | Mốc bắt đầu / kết thúc = vị trí video |
| `Ctrl + L` | Bật / tắt lặp dòng |
| `Ctrl + H` | Tìm và thay thế |
| `Ctrl + Z` / `Ctrl + Shift + Z` | Hoàn tác / làm lại |
| `Ctrl + S` (hoặc `⌘ + S`) | Lưu |
| `Tab` | Sang ô kế trong ô sửa |
| `↑` / `↓` (trong bảng) | Đổi dòng đang chọn |
| `?` | Bảng phím tắt |

Trên máy Mac, `Ctrl` ở trên đều dùng được bằng `⌘`.

### Lưu

Bấm nút **Lưu**. App ghi lại cả `.srt` lẫn `_vi.srt` và soát lỗi lại một lượt.
Nếu bạn mở phụ đề bằng nút **Chọn file .srt…**, nút này tên là **Lưu và tải về**
(xem đầu mục này).

> **Chỉ nút Lưu mới ghi vào file.** Đóng tab mà chưa bấm Lưu thì file trên đĩa
> vẫn là bản cũ.

### Tải file về máy — tuỳ chọn, làm sau cùng

Soát và sửa trên web xong xuôi rồi mới cần tải. Bấm **Xuất file…** cạnh nút Lưu:
app liệt kê file `.srt`, `_vi.srt`, bản song ngữ `_song-ngu.ass` cho Aegisub, báo cáo
và bundle — bấm cái nào cần. Nhớ bấm **Lưu** trước, không thì file tải về chưa có
phần vừa sửa. Không tải cũng không sao: mọi thứ đã nằm trong thư mục kết quả.

### Lỡ đóng tab lúc đang sửa dở

App tự ghi bản nháp trong lúc bạn gõ. Lần sau mở lại đúng phụ đề đó, một dải
màu vàng hiện lên đầu bảng: *"Bạn có bản sửa chưa lưu…"*, kèm hai nút:

- **Khôi phục bản sửa** — đổ phần gõ dở trở lại bảng. Lúc này file trên đĩa
  **vẫn chưa đổi**; nhìn thấy đúng rồi mới bấm **Lưu**.
- **Bỏ bản nháp** — xoá hẳn phần gõ dở. App hỏi lại một câu cho chắc.

Chưa bấm nút nào thì không có gì thay đổi cả.

Muốn quay về bản máy làm lúc đầu: bấm **Hoàn nguyên về bản máy tạo**.
App cũng hỏi lại một câu cho chắc. Nếu phim đã được chạy lại (ví dụ bằng nút
**Chạy lại với bảng tên mới**), thì “bản máy tạo” là bản của **lần chạy mới nhất**.

---

## 8. Tab Kiểm tra file

Dùng khi có người gửi cho bạn file `.srt` và bạn muốn biết nó có đúng quy cách không.
Không cần chạy gỡ băng nên chỉ mất vài giây.

1. Vào tab **Kiểm tra file**.
2. Kéo file `.srt` thả vào ô lớn giữa trang, hoặc bấm **Chọn file .srt**.
   Thả nhiều file cùng lúc cũng được.
3. Xem danh sách lỗi.

Mỗi lỗi ghi rõ **câu số mấy** (viết là *Cue* kèm số thứ tự) và **sai chỗ nào**,
bằng tiếng Việt. Ví dụ:
*Cue 268: dấu câu hai dòng không giống nhau. Dòng Hán dùng "。", dòng pinyin dùng "(không có)".*

Mỗi file trong danh sách có ba nút riêng:

- **Chỉ kiểm tra** — soi lại file, không đụng gì tới nó.
- **Sửa và tải về** — máy tự chữa những lỗi máy chữa được (khoảng trắng thừa,
  dấu câu sai loại, pinyin lệch cụm) rồi cho bạn tải file đã chữa về.
  File gốc của bạn không bị đụng tới.
  Nếu file có thêm dòng tiếng Việt, app **giữ dòng đó** và tách ra file riêng
  `_vi.srt`. Lúc đó app hiện hai nút tải: hãy tải **cả hai file** và để chung
  một thư mục.
- **Mở vào trình sửa** — đưa file này sang tab **Sửa phụ đề** để sửa tay.
  Lưu ý: đây là sửa trên **bản sao**. Sửa xong bấm **Lưu và tải về** để lấy file
  đã sửa về máy; file gốc của bạn không đổi (xem [mục 7](#7-tab-sửa-phụ-đề)).

Có nhiều file thì dùng hai nút chung ở đầu danh sách: **Kiểm tra tất cả** và
**Sửa tất cả và tải về**. Nút **Xoá danh sách** chỉ dọn danh sách trên màn hình,
không xoá file trên máy.

### Ô "Mở file có sẵn vào trình sửa" ở cuối trang

Trình sửa chỉ ghi thẳng vào file trên máy khi nó biết **đường dẫn** của file, mà
kéo thả hay bấm chọn file thì trình duyệt không cho biết đường dẫn. Vì vậy ở cuối
trang có ô riêng: dán đường dẫn file `.srt` vào rồi bấm **Mở theo đường dẫn**.

Nút **Chọn file .srt…** trong cùng ô đó cũng mở được file, nhưng là mở **bản sao**
như nút **Mở vào trình sửa** ở trên. Sửa xong thì bấm **Lưu và tải về**.

Cách lấy đường dẫn trên máy Mac: bấm chuột phải vào file trong Finder, giữ phím
Option rồi chọn *"Copy … as Pathname"*.

Nếu trong cùng thư mục có file `_vi.srt` đi kèm, app tự mở luôn cả hai và
soát thêm việc hai file có khớp nhau không.

---

## 9. Tab Tên riêng

Tên nhân vật, địa danh, tên tác phẩm của từng phim. Ghi ở đây một lần thì suốt
cả phim tên sẽ được **tách giống nhau** và **viết hoa giống nhau**.

1. Vào tab **Tên riêng**.
2. Chọn phim ở ô **Phim** trên cùng.
3. Sửa bảng, rồi bấm **Lưu bảng tên**.

Bảng tên riêng do AI tự lập trong lúc chạy, ở bước 8/10 — khi ô *Nhờ AI soát tên
riêng và chữ khó* được bật và đã có **Khoá API Gemini** trong tab Cài đặt. Chạy
với cài đặt mặc định (không bật AI) thì phim chưa có bảng, nhưng bạn tự lập được:

- Trong ô **Phim**, phim đã chạy mà chưa có bảng nằm ở nhóm *"Phim chưa có bảng
  tên"*, tên phim kèm chữ *"— chưa có bảng"*. Chọn phim đó, bấm **Thêm tên**, ghi
  tên, rồi bấm **Lưu bảng tên** — bảng được tạo đúng lúc bấm Lưu.
- Hoặc bấm **Lập bảng cho phim khác** để chọn phim từ danh sách.

Ô **Phim** chỉ ghi *"Chưa có phim nào"* khi máy chưa có phụ đề nào để lập bảng.

Bảng có ba cột:

| Cột | Nghĩa | Ví dụ |
| --- | --- | --- |
| **Chữ Hán** | Tên viết ra, đã ngăn cụm bằng dấu cách | `陈 路周` |
| **Pinyin** | Pinyin của tên, ngăn cụm y hệt cột bên trái | `Chén Lùzhōu` |
| **Tên tiếng Việt** | Tên bạn muốn thấy trong bản dịch `_vi.srt` | `Cường đầu trọc` |

**Luật quan trọng nhất:** cột **Chữ Hán** và cột **Pinyin** phải có **cùng số cụm**.
`陈 路周` đi với `Chén Lùzhōu` là 2 cụm ↔ 2 cụm. Lệch số cụm thì hàng đó bị đánh
dấu và app báo ngay dưới ô pinyin.

Cột **Tên tiếng Việt** để trống cũng được — máy sẽ tự phiên âm. Nhưng ghi vào thì
suốt cả phim tên đó được dịch đúng một kiểu, đây là chỗ đáng bỏ công nhất khi soát.

Hai nút nữa: **Thêm tên** chèn một hàng trống, **Nạp lại** lấy lại bản đang
nằm trên đĩa (bỏ mọi thứ bạn vừa gõ mà chưa bấm **Lưu bảng tên**).

### Sửa tên riêng rồi chạy lại

Lưu bảng tên xong, các file phụ đề đã làm **chưa tự đổi theo**. Muốn phụ đề
dùng bảng tên mới:

1. Vào tab **Tên riêng**, chọn đúng phim ở ô **Phim**.
2. Sửa tên, rồi bấm **Lưu bảng tên**.
3. Bấm **Chạy lại với bảng tên mới**.
4. Chờ app làm lại. App không nghe lại video, nên nhanh hơn lần đầu nhiều.

Bạn **không cần** file video gốc nữa. App dùng lại phần âm thanh và phần gỡ
băng còn lưu từ lần chạy trước. App làm lại phần tách cụm, pinyin và bản dịch,
rồi ghi lại các file phụ đề.

**Những câu bạn đã sửa tay không mất, nhưng cũng không tự chuyển sang bản mới.**
Trước khi ghi, app cất file cũ thành file có chữ `.truoc-` trong tên, ngay cạnh
file mới (xem [mục 6](#6-bạn-nhận-được-những-file-gì)). Mở file đó ra để chép lại
những câu bạn đã sửa.

**Mẹo:** soát và sửa **bảng tên riêng trước**, chạy lại, rồi mới soát từng câu.
Làm theo thứ tự này thì không phải chép lại câu nào.

Nếu app báo phim đó **đang chạy**, chờ nó chạy xong rồi bấm lại. Nếu app báo
**thiếu kết quả của lần chạy trước** (ví dụ thư mục làm việc đã bị dọn), thì phải
tạo phụ đề lại từ đầu với video gốc, như [mục 4](#4-tạo-phụ-đề-từ-link-youtube)
hoặc [mục 5](#5-tạo-phụ-đề-từ-file-có-sẵn-trong-máy).

### Bảng tên bị lỗi

Bảng tên có thể bị hỏng, thường do sửa tay bằng phần mềm khác và thiếu một dấu
phẩy. Khi đó app báo ngay trên tab **Tên riêng**. App **không xoá** file hỏng.
Khi bạn lưu bảng mới, hoặc khi AI ghi thêm tên, file hỏng được cất nguyên vẹn
thành `names.json.hong-<ngày giờ>`, nằm ngay cạnh bảng mới. Các tên cũ vẫn ở
trong file cất đó. Cần lấy lại thì gửi file đó cho người hỗ trợ.

---

## 10. Mở trong Aegisub

Aegisub là phần mềm chuyên để soát phụ đề. Nếu bạn quen dùng nó thì:

> **Mở file `<tên>_song-ngu.ass`.**

File này có cả tiếng Trung, pinyin và tiếng Việt hiện cùng lúc trên màn hình,
mỗi thứ một kiểu chữ một màu. Xem video và soát cả hai thứ tiếng trong một lượt.

Trong Aegisub: **File > Open Subtitles…** (phím tắt ⌘ + O). Có file video thì mở
thêm bằng **Video > Open Video…** để nghe và soát cho đúng câu.

Nếu chữ Hán hiện thành ô vuông: vào **Subtitle > Styles Manager** và đổi font sang
**PingFang SC** hoặc **Arial Unicode MS**.

**Đừng giao file `.ass` cho khách.** Nó chỉ để soát.
Hai file để giao đi là `<tên>.srt` và `<tên>_vi.srt`.

Thấy chỗ sai trong Aegisub thì đừng lưu đè lên `.ass`, cũng đừng xuất nó ra `.srt`
rồi mang đi dùng. Cách đúng là: ghi lại số câu cần sửa, rồi sửa đúng câu đó trong
[tab Sửa phụ đề](#7-tab-sửa-phụ-đề) — bấm **Lưu** là app ghi lại cả `.srt` lẫn
`_vi.srt` đúng quy cách.

Lỡ xuất `_song-ngu.ass` ra file `.srt` rồi thì vẫn cứu được. Đưa file đó vào
[tab Kiểm tra file](#8-tab-kiểm-tra-file) và bấm **Sửa và tải về**. File đó có ba
dòng chữ mỗi câu; app **giữ dòng tiếng Việt** và tách ra file riêng `_vi.srt`.
Nhớ tải về **cả hai file**. Dòng nào app không biết xếp vào đâu thì app liệt kê
riêng trong một khung vàng để bạn chép lại, chứ không lặng lẽ bỏ đi.

---

## 11. Chờ bao lâu

Số đo trên iMac 2017 (Core i7, 32GB RAM), dùng mô hình mặc định
**Cân bằng (khuyên dùng)**:

| Độ dài video | Riêng bước nghe | Tổng cộng |
| --- | --- | --- |
| 10 phút | khoảng 3 phút | 4-6 phút |
| 20 phút | khoảng 6 phút | 8-11 phút |
| **40 phút** | **khoảng 10-15 phút** | **15-20 phút** |
| 60 phút | khoảng 18 phút | 22-30 phút |

Cộng thêm nếu có:

- Tải video từ YouTube: thêm 1-3 phút.
- Dịch tiếng Việt: thêm 2-5 phút cho video 40 phút.
- Bật *Tách giọng khỏi nhạc nền*: **lâu gấp đôi**. Chỉ bật với phim nhiều nhạc nền.

Nếu bạn đổi sang mô hình **Chính xác nhất** thì riêng bước nghe mất 30-40 phút
cho video 40 phút, tức lâu gấp ba, mà kết quả chỉ nhỉnh hơn một chút.
Bình thường không cần.

Lần chạy đầu tiên sau khi cài có thể lâu hơn vài phút vì máy còn tải mô hình.
Chỉ tải một lần.

---

## 12. Tab Cài đặt

Đặt một lần rồi dùng mãi. Mọi thứ ở đây đều có thể để nguyên nếu bạn không chắc.

> **Sửa xong nhớ bấm nút Lưu cài đặt ở cuối trang.** Không bấm thì mọi thay đổi
> trong tab này mất khi tải lại trang. Riêng ô **Màu nền** đổi và được nhớ ngay,
> không cần bấm Lưu.

### Khoá API cho trợ lý AI và dịch

Dán khoá vào ô rồi bấm **Kiểm tra key**. Khoá dùng được thì app **lưu luôn**, không
cần bấm Lưu cài đặt nữa. Khoá sai thì app nói rõ là sai (thường do dán thiếu ký tự
hoặc dán cả dấu nháy) — dán lại cho đủ.

Ô **Khoá API Gemini** để trống thì app **vẫn chạy đủ**. Nhập vào thì được hai thứ tốt hơn:

- Máy nhờ AI soát lại tên riêng và những chữ đọc nhiều âm.
- **Bản dịch tiếng Việt hay hơn hẳn.** AI dịch theo mạch phim, nhớ tên nhân vật
  từ đầu đến cuối, xưng hô nhất quán.

Để trống thì app dịch bằng Google miễn phí: dịch từng câu rời rạc, không nhớ
ngữ cảnh, tên nhân vật mỗi chỗ một kiểu. Vẫn đọc hiểu được, nhưng phải sửa nhiều.

Nút hình con mắt bên cạnh ô để hiện/ẩn khoá.
Nhập xong bấm **Kiểm tra key**, app thử một câu và báo ✓ hoặc ✗.

Ngay dưới đó là ô **Dịch tiếng Việt bằng**, có bốn lựa chọn:

| Lựa chọn | Khi nào dùng |
| --- | --- |
| **Tự chọn** (mặc định) | Cứ để đây. Có khoá thì dùng Gemini, không có thì dùng bản miễn phí. |
| Gemini | Ép luôn dùng Gemini. Cần khoá API. |
| Google miễn phí | Không cần khoá, chất lượng thấp hơn rõ rệt. |
| Không dịch | Chỉ làm file tiếng Trung, không làm `_vi.srt`. |

### Chất lượng gỡ băng

Đây là thứ quyết định máy chạy nhanh hay chậm.

| Chọn cái này | Khi nào |
| --- | --- |
| **Cân bằng (khuyên dùng)** — mặc định | Gần như luôn luôn |
| Chính xác nhất | Thoại khó, nhiều tiếng ồn, và bạn không vội |
| Nhanh, độ chính xác vừa | Cần gấp |
| Thử nhanh | Chỉ để xem tool chạy thế nào |

Bên cạnh mỗi dòng có ghi thời gian ước tính cho video 40 phút và dung lượng phải
tải. Mô hình nào máy đã tải rồi thì chạy được ngay; chưa tải thì lần chạy đầu
tiên máy tự tải về, chỉ một lần. Muốn tải sẵn ngay thì bấm nút **Tải về** ở dòng
mô hình đó.

### Âm thanh và định dạng

- **Tách giọng khỏi nhạc nền (dành cho phim nhiều nhạc)** — mặc định tắt.
  Bật khi phim có nhạc nền to át tiếng thoại. Nhớ là **lâu gấp đôi**.
- **Xuất thêm bản .ass cho Aegisub** — cùng nội dung với `.srt`, đã đặt sẵn font.
- **Xuất bản song ngữ để soát (`_song-ngu.ass`)** — nên để bật. Đây là file bạn
  mở trong Aegisub khi soát lại.
- **Ghi file kèm dấu nhận dạng UTF-8 (BOM)** — giữ bật. Tắt đi thì một số phần
  mềm trên Windows sẽ hiện chữ Hán thành ký tự lạ.
- **Kiểu nội dung mặc định** — Phim / phim ngắn, hay Tin tức / phóng sự.
  Đây là giá trị dùng sẵn mỗi lần chạy; vẫn đổi được cho từng lần ở tab Tạo phụ đề.

### Nơi lưu file

Ô **Thư mục lưu kết quả**: để trống thì app dùng thư mục mặc định của nó.
Muốn để kết quả vào chỗ khác (ví dụ một thư mục trong Documents) thì dán đường
dẫn vào đây rồi bấm **Lưu cài đặt**. Nút **Mở** bên cạnh mở thư mục đó trong Finder.

Dưới đó là **Thư mục làm việc tạm** — chỉ để xem, không cần đụng tới.

### Giao diện

Ô **Màu nền**: Theo hệ thống / Luôn sáng / Luôn tối.

### Kiểm tra máy

Nút **Kiểm tra máy** chạy một lượt tự khám, liệt kê từng thứ máy cần
(uv, ffmpeg, yt-dlp, thư viện, mô hình, dung lượng đĩa) kèm cách xử lý nếu thiếu.

### Công cụ và trợ giúp

Ngay dưới là năm nút bảo trì:

- **Cài ffmpeg vào chương trình** — khi máy báo thiếu ffmpeg.
- **Cập nhật yt-dlp** — bấm khi tải YouTube báo lỗi. YouTube hay đổi cách hoạt
  động, bấm nút này là xong, mất khoảng 30 giây.
- **Tải model gỡ băng đang chọn** — tải sẵn mô hình nghe thay vì chờ lần chạy đầu.
- **Mở thư mục kết quả** — mở Finder tại chỗ chứa file phụ đề.
- **Mở hướng dẫn sử dụng** — mở chính trang hướng dẫn này trong một tab mới của
  trình duyệt.

---

## 13. Gặp lỗi này thì làm gì

Khi tạo phụ đề bị hỏng, tab **Tạo phụ đề** hiện màn hình *"Không chạy được"*.
Thường có sẵn khung **Cách xử lý** và một nút sửa nhanh (ví dụ **Cập nhật yt-dlp
giúp tôi**, **Cài ffmpeg giúp tôi**): làm theo khung đó trước, rồi bấm **Thử lại
từ đầu**. Phần **Chi tiết kỹ thuật** ở cuối là thứ gửi cho người hỗ trợ.

| Máy báo | Làm gì |
| --- | --- |
| *"không mở được vì không rõ nhà phát triển"* | Bấm chuột phải vào file → **Open** → **Open**. Xem [mục 2A](#2a-máy-báo-không-mở-được-vì-không-rõ-nhà-phát-triển). |
| Bấm đúp file `.command` mà không có gì xảy ra | File mất quyền chạy. Chạy `CAP-QUYEN.command`, xem [mục 2B](#2b-bấm-đúp-mà-không-có-gì-xảy-ra-hoặc-máy-báo-the-file-couldnt-be-opened). |
| *"The file couldn't be opened"* | Như trên. |
| *"Máy này chưa cài SrtGen"* | Bấm chuột phải `SrtGen.command` → **Open**, chờ cài xong. |
| Cửa sổ cài đặt dừng ở dòng đỏ *"KHÔNG CÀI TIẾP ĐƯỢC"* | Đọc câu ngay dưới dòng đó (thường là mất mạng hoặc ổ đĩa thiếu chỗ). Sửa xong mở lại `SrtGen.command`, nó chạy tiếp từ đúng bước bị dừng. |
| Cài xong nhưng hiện *"CÀI XONG NHƯNG CÒN MỤC CHƯA ĐẠT"* | Kéo lên xem bảng kiểm tra: dòng nào có dấu ✗ thì ngay dưới có câu *"Cách xử lý"*. Thường chỉ cần chạy lại `SrtGen.command`. Vẫn vậy thì gửi file nhật ký (xem cuối mục này). |
| *"Không tải được âm thanh từ link này"* / *"Unable to extract"* | Bấm **Cập nhật yt-dlp giúp tôi** nếu màn hình lỗi có nút đó; không thì vào tab **Cài đặt**, bấm **Cập nhật yt-dlp**. Rồi chạy lại. |
| *"YouTube yêu cầu đăng nhập mới cho tải video này"* (video giới hạn độ tuổi, dành cho hội viên) | Bấm **Cập nhật yt-dlp** ở tab **Cài đặt** rồi thử lại một lần. Vẫn không được thì tải video về máy bằng cách khác rồi dùng [mục 5](#5-tạo-phụ-đề-từ-file-có-sẵn-trong-máy). |
| *"Video này đang để chế độ riêng tư"* | Tải video về máy bằng cách khác rồi dùng [mục 5](#5-tạo-phụ-đề-từ-file-có-sẵn-trong-máy). |
| *"Máy chưa có ffmpeg"* | Bấm **Cài ffmpeg giúp tôi** ngay trên màn hình lỗi (hoặc **Cài ffmpeg vào chương trình** ở tab **Cài đặt**). Vẫn không được thì chạy lại `SrtGen.command`, nó tải bù đúng phần thiếu. |
| *"Tải mô hình … không xong sau … lần thử"* | Kiểm tra mạng rồi bấm **Bắt đầu** lại. Phần đã tải không mất, máy tải tiếp. |
| Cửa sổ đen hiện rồi tắt ngay | Chạy lại `SrtGen.command` để cài bù. Vẫn vậy thì gửi file nhật ký (xem cuối mục này). |
| Trình duyệt không tự mở | Nhìn cửa sổ đen, có một địa chỉ dạng `http://127.0.0.1:8xxx`. Gõ địa chỉ đó vào Safari. |
| Trang web trắng trơn | Bấm tải lại trang (⌘ + R). |
| Phụ đề nghe sai nhiều | Bật **Tách giọng khỏi nhạc nền** trong tab **Cài đặt** rồi chạy lại. Hoặc đổi **Chất lượng gỡ băng** sang **Chính xác nhất**. |
| Chữ Hán ra phồn thể | Đó là lỗi cũ đã sửa. Chạy lại; nếu vẫn còn, báo người hỗ trợ. |
| Bản dịch tiếng Việt kỳ cục | Nhập **Khoá API Gemini** trong tab **Cài đặt** rồi chạy lại. Chênh lệch rất rõ. |
| Tên nhân vật mỗi chỗ một kiểu | Như trên. Ngoài ra sửa bảng ở tab **Tên riêng**, bấm **Lưu bảng tên** rồi **Chạy lại với bảng tên mới** ([mục 9](#9-tab-tên-riêng)). |
| Đã sửa bảng tên mà phụ đề vẫn dùng tên cũ | Lưu bảng tên chưa đủ. Bấm thêm **Chạy lại với bảng tên mới**. Xem [mục 9](#9-tab-tên-riêng). |
| Thấy file lạ có chữ `.truoc-` trong tên | Đó là bản cũ app cất trước khi ghi file mới, không phải rác. Đừng xoá nếu còn cần. Xem [mục 6](#6-bạn-nhận-được-những-file-gì). |
| Thấy file `names.json.hong-…` | Bảng tên cũ bị lỗi, app đã cất nguyên vẹn. Đừng xoá. Xem [mục 9](#9-tab-tên-riêng). |
| Máy chạy mãi không xong | Nhìn thanh tiến trình ở tab **Tạo phụ đề**. Bước 3/10 vốn lâu, video 40 phút mất 10-15 phút là bình thường. |
| Đóng nhầm cửa sổ trình duyệt lúc đang chạy | Mở lại trang SrtGen. App tự tìm lại việc đang chạy, không phải bấm gì. |
| Sửa phụ đề xong đóng tab, mở lại thấy mất | Trình sửa chỉ ghi vào file khi bạn bấm **Lưu**. Mở lại phụ đề đó, nếu có dải vàng thì bấm **Khôi phục bản sửa** rồi bấm **Lưu**. Xem [mục 7](#7-tab-sửa-phụ-đề). |
| Mở file bằng nút **Chọn file .srt…**, sửa xong mà file gốc không đổi | Đúng vậy: app sửa trên bản sao. Bấm **Lưu và tải về**, rồi dùng file vừa tải về trong thư mục **Tải về**. Xem [mục 7](#7-tab-sửa-phụ-đề). |
| Máy hết chỗ trống | Xoá bớt file rồi chạy lại. Cần khoảng 8GB trống. |

### Gửi cho người hỗ trợ

Mọi lần chạy đều được ghi vào một file nhật ký. Lấy file mới nhất tại:

```
~/Library/Application Support/SrtGen/logs/
```

Mở Finder, nhấn ⌘ + ⇧ + G, dán đường dẫn trên vào, nhấn Enter.
Gửi file mới nhất trong đó — trong file có đủ thông tin để tìm ra nguyên nhân.

---

## 14. Gỡ khỏi máy

Bấm chuột phải vào `GoCaiDat.command` trong thư mục `installer` → **Open**.

Chương trình sẽ:

1. **Liệt kê từng thứ sắp bị xoá**, kèm dung lượng:
   - các thư viện, bản Python riêng, `uv` và `ffmpeg` của SrtGen, mô hình nghe,
     kho gói đã tải;
   - **dữ liệu làm việc của các phim**: âm thanh đã tách, kết quả trung gian của
     từng bước, **bản sửa dở chưa lưu** của tab Sửa phụ đề, và bản chép của file
     bạn kéo vào app (file gốc của bạn không bị đụng tới);
   - **lịch sử các lần chạy** (danh sách việc trong app);
   - câu trả lời AI và bản dịch đã nhớ sẵn (nằm trong `~/Library/Caches/SrtGen`);
   - nhật ký hoạt động và hồ sơ cài đặt;
   - **cài đặt của bạn, kể cả khoá API** — nhớ chép lại khoá nếu định cài lại;
   - biểu tượng **SrtGen** trên màn hình nền.

   Nếu còn **bản sửa dở chưa lưu**, chương trình báo có mấy bản. Những bản đó sẽ
   mất. Muốn giữ thì mở app, vào tab **Sửa phụ đề**, bấm **Lưu**, rồi mới gỡ.
2. Bắt bạn gõ tay chữ `XOA` rồi nhấn Enter. Gõ bất cứ thứ gì khác, hoặc chỉ
   nhấn Enter, thì không xoá gì cả.
3. **Giữ nguyên toàn bộ file `.srt` bạn đã tạo.** Muốn xoá cả những file đó
   thì phải trả lời thêm một câu hỏi nữa, bằng cách gõ `XOA-KET-QUA`.
4. **Cất bảng tên riêng trước khi xoá.** Bảng tên riêng ([mục 9](#9-tab-tên-riêng))
   nằm trong dữ liệu làm việc, nhưng **không bị mất theo**. Trước khi xoá bất cứ
   thứ gì, chương trình chép từng bảng sang thư mục `bang-ten-rieng` bên trong
   thư mục kết quả. Mỗi phim một file, đặt theo tên phim, ví dụ
   `Cô Gái Đến Từ Hôm Qua.names.json`. Nếu không đọc được tên phim (bộ thư viện
   của SrtGen đã hỏng, hoặc bạn chạy lại sau một lần gỡ dở) thì file mang mã
   phim, ví dụ `a1B2c3D4e5F.names.json`. Chép xong, chương trình so lại từng file với
   bản gốc. Nếu chép hỏng dù chỉ một bảng, dữ liệu làm việc được **giữ nguyên**
   và chương trình báo cho bạn.
   Nếu ở bước 3 bạn chọn xoá cả file `.srt`, bảng tên được chép ra màn hình nền,
   vào thư mục `SrtGen-bang-ten-rieng-<ngày giờ>`.

File bảng tên là file chữ. Mở bằng TextEdit để xem lại tên đã ghi, rồi gõ lại
những tên cần dùng vào tab **Tên riêng** sau khi cài lại.

Nếu bạn đã đổi **Thư mục lưu kết quả** trong tab Cài đặt, chương trình đọc đúng
chỗ mới đó chứ không đoán. Nếu chỗ đó nằm ngoài thư mục của SrtGen, ví dụ một
thư mục trong Documents, thì chương trình **không bao giờ xoá nó**, và không hỏi
câu `XOA-KET-QUA`. Muốn xoá các file `.srt` trong đó thì bạn tự xoá bằng Finder.
Nếu bạn đặt nó nằm lẫn bên trong thư mục làm việc của app, các file `.srt` cũng
vẫn được giữ nguyên.

Xoá xong, chương trình **kiểm lại**. Nếu còn sót thứ gì, chương trình liệt kê
từng thứ kèm cách xử lý, và dòng cuối ghi *"GỠ XONG NHƯNG CÒN SÓT MỘT SỐ THỨ"*
thay vì *"ĐÃ GỠ SRTGEN"*. Hai trường hợp hay gặp:

- **App đang chạy** nên không xoá được: đóng SrtGen rồi chạy lại `GoCaiDat.command`.
- **Bạn tự để file riêng** vào thư mục SrtGen: chương trình không đụng tới file đó.
  Tự xem và xoá nếu không cần.

Sau khi gỡ, máy chỉ còn những thứ được **giữ lại có chủ ý**:

- thư mục kết quả với các file `.srt`, nếu bạn giữ. Khi đó thư mục
  `~/Library/Application Support/SrtGen` vẫn còn, vì thư mục kết quả mặc định
  `output` nằm trong đó;
- thư mục `bang-ten-rieng` chứa các bảng tên riêng;
- nếu bạn đã đổi **Thư mục lưu kết quả**: thư mục kết quả mặc định cũ
  `~/Library/Application Support/SrtGen/output` cùng các file `.srt` cũ trong đó.
  Chương trình không xoá nó, kể cả khi bạn gõ `XOA-KET-QUA` — câu đó chỉ xoá thư
  mục kết quả đang dùng. Không cần nữa thì tự xoá bằng Finder;
- thư mục mã nguồn SrtGen, chỗ có file `GoCaiDat.command`. Không cần nữa thì kéo
  nó vào Thùng rác.

Bộ cài không thêm gì vào phần dùng chung của máy, nên không có lệnh dọn dẹp
nào khác phải chạy.

Muốn dùng lại: bấm chuột phải `SrtGen.command` → **Open**.
