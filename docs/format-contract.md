# Quy chuẩn chỉnh file SRT

Tài liệu này dùng để chuẩn hóa các file `.srt` trong thư mục `Srt` mỗi khi thêm file mới hoặc khi có yêu cầu chỉnh sửa subtitle.

## Định dạng bắt buộc

Mỗi block subtitle phải có đúng 4 dòng:

```text
790
00:39:49,160 --> 00:39:51,640
陈 路周，你 闯 祸 了。
Chén Lùzhōu，nǐ chuǎng huò le。
```

Thứ tự bắt buộc:

1. Số thứ tự subtitle
2. Mốc thời gian
3. Dòng Chinese
4. Dòng pinyin

Sau mỗi block phải có đúng 1 dòng trống.

## Quy tắc bắt buộc phải giữ đúng

### 1. Chinese phải đi đúng với pinyin

- Mỗi cụm Chinese phải khớp trực tiếp với cụm pinyin ở dòng dưới.
- Nếu pinyin tách thành 2 cụm thì Chinese cũng phải tách thành 2 cụm.
- Nếu pinyin giữ liền 1 cụm thì Chinese cũng phải giữ liền 1 cụm.
- Không được tự ý gộp Chinese nếu pinyin đang tách.
- Không được tự ý tách Chinese nhỏ hơn pinyin.
- Dấu câu không được tính là một cụm từ. Khi so khớp Chinese với pinyin, phải bỏ qua các token dấu câu.
- Dấu câu phải xuất hiện đúng vị trí tương ứng trên cả hai dòng.
- Số lượng cụm bằng nhau chỉ là điều kiện cần, chưa đủ để kết luận đã khớp.
- Sau khi bỏ dấu câu và marker người nói, phải ghép từng cụm Chinese với đúng cụm pinyin ở cùng vị trí và kiểm tra phát âm của chính cụm đó.
- Không được chuyển một phần pinyin của cụm trước sang cụm sau, hoặc gán lặp lại cùng một pinyin cho nhiều cụm Chinese.
- Không được giả định mỗi Hán tự luôn tương ứng với một âm tiết pinyin độc lập. Với **儿化音**, chữ `儿` là hậu tố của cụm đứng trước và phải nằm trong cùng một cụm Chinese.
- Các cụm như `哪儿`、`这儿`、`那儿`、`一会儿` phải lần lượt ghép với `nǎr`、`zhèr`、`nàr`、`yíhuìr`; không được tách `儿` thành một từ hoặc một segment riêng.

Ví dụ đúng:

```text
陈 路周
Chén Lùzhōu
```

```text
闯 祸
chuǎng huò
```

```text
女 朋友
nǚ péngyou
```

Ví dụ đúng vì từng cặp khớp theo vị trí:

```text
好 | 唱得 | 真好
Hǎo | chàngdé | zhēnhǎo
```

Ví dụ sai dù hai dòng đều có 3 cụm:

```text
好 | 唱得 | 真好
Hǎochàng | dé | zhēnhǎo
```

Trong ví dụ sai, `chàng` thuộc về `唱` nhưng bị ghép vào cụm pinyin của `好`. Không được chỉ kiểm tra số lượng cụm rồi coi là hợp lệ.

Ví dụ đúng với 儿化音:

```text
住 | 哪儿
zhù | nǎr
```

Ví dụ sai:

```text
住 | 哪 | 儿
zhù | nǎ | er
```

### 2. Không được có dấu cách ở đầu dòng

- Không được có khoảng trắng ở đầu dòng Chinese.
- Không được có khoảng trắng ở đầu dòng pinyin.
- Không được có khoảng trắng ở cuối dòng.

Ví dụ đúng:

```text
790
00:39:49,160 --> 00:39:51,640
陈 路周，你 闯 祸 了。
Chén Lùzhōu，nǐ chuǎng huò le。
```

Ví dụ sai:

```text
790
00:39:49,160 --> 00:39:51,640
陈 路周，你 闯 祸 了。
 Chén Lùzhōu，nǐ chuǎng huò le。
```

### 3. Phải sử dụng dấu câu tiếng Trung

Dòng Chinese và dòng pinyin đều phải sử dụng cùng một bộ dấu câu tiếng Trung:

| Không dùng | Phải dùng | Tên dấu |
| --- | --- | --- |
| `,` | `，` | Dấu phẩy |
| `.` | `。` | Dấu chấm |
| `?` | `？` | Dấu hỏi |
| `!` | `！` | Dấu chấm than |
| `:` | `：` | Dấu hai chấm |
| `;` | `；` | Dấu chấm phẩy |
| `...` | `……` | Dấu lửng |
| `--` hoặc `-` dùng để ngắt câu | `——` | Dấu gạch ngang/ngắt lời |
| `()` | `（）` | Dấu ngoặc đơn |
| `""` | `“”` | Dấu ngoặc kép |
| `''` | `‘’` | Dấu ngoặc đơn trích dẫn |
| `<>` | `《》` | Dấu tên sách, phim hoặc tác phẩm |

Quy tắc vị trí:

- Không được có dấu cách trước dấu câu.
- Không được có dấu cách sau dấu `，` `。` `？` `！` `：` `；` hoặc `……`.
- Dấu câu phải dính tự nhiên vào nội dung, giống cách viết tiếng Trung thông thường.
- Cách dùng và vị trí dấu câu phải đồng bộ giữa dòng Chinese và dòng pinyin.
- Không được trộn dấu câu ASCII và dấu câu tiếng Trung trong cùng một file.

Ví dụ đúng:

```text
好，唱得 真好
Hǎo，chàngdé zhēnhǎo
```

```text
短剧：《不愧 是 顶级 女 保镖》
Duǎnjù：《Bùkuì shì dǐngjí nǚ bǎobiāo》
```

```text
陈 路周，你 闯 祸 了。
Chén Lùzhōu，nǐ chuǎng huò le。
```

Ví dụ sai vì sử dụng dấu câu ASCII:

```text
好, 唱得 真好
Hǎo, chàngdé zhēnhǎo
```

Ví dụ sai vì có khoảng trắng quanh dấu câu:

```text
好 ， 唱得 真好
Hǎo ， chàngdé zhēnhǎo
```

Ví dụ sai vì dấu câu giữa hai dòng không đồng bộ:

```text
好，唱得 真好
Hǎo, chàngdé zhēnhǎo
```

#### Marker đổi người nói trong cùng một cue

- Dấu `-` dùng để đánh dấu hoặc phân tách hai người nói là marker cấu trúc, không phải dấu câu trong lời thoại.
- Marker này được phép giữ dạng ASCII `-` để tương thích SRT.
- Dấu `-` ở cuối câu hoặc dùng để thể hiện lời nói bị ngắt không phải marker; trường hợp đó bắt buộc đổi thành `——` và viết dính vào nội dung đứng trước.
- Nếu `-` đứng đầu phần lời thoại, phải có đúng 1 dấu cách sau nó.
- Nếu `-` phân tách hai phần lời thoại, phải có đúng 1 dấu cách ở cả hai bên.
- Nếu ngay trước marker `-` là dấu câu như `……` hoặc `！`, vẫn phải giữ đúng 1 dấu cách giữa dấu câu và marker. Đây là ngoại lệ cấu trúc đối với quy tắc không có khoảng trắng sau dấu câu.
- Vị trí marker phải giống nhau giữa dòng Chinese và dòng pinyin.
- Không tính `-` là một cụm Chinese hoặc một cụm pinyin.

Ví dụ đúng:

```text
- 我 没有 - 你 说 这……
- wǒ méiyǒu - nǐ shuō zhè……
```

### 4. Dấu `……` và các dấu khác phải đồng bộ

- Dấu lửng tiếng Trung phải viết đúng là `……`.
- Không được viết thành `...`, `. . .`, `.. .`, `……` kèm khoảng trắng hoặc số lượng dấu tùy ý.
- Không được có dấu cách trước hoặc sau `……`, ngoại trừ đúng 1 dấu cách trước marker đổi người nói `-`.
- Nếu trước đó là một từ hoặc cụm thì `……` phải dính vào cụm đó.
- Nếu sau `……` vẫn còn nội dung thì nội dung tiếp theo cũng phải viết liền, không thêm khoảng trắng vì dấu câu.
- Cách dùng dấu lửng phải đồng bộ giữa dòng Chinese và dòng pinyin.

Ví dụ đúng:

```text
您 好，您 拨打……
Nín hǎo，nín bōdǎ……
```

```text
我……
Wǒ……
```

Ví dụ sai:

```text
您 好, 您 拨打 ...
Nín hǎo, nín bōdǎ . . .
```

### 5. Không được thò thụt dấu cách trong câu

- Chỉ dùng 1 dấu cách giữa 2 cụm từ.
- Không được dùng khoảng trắng để ngăn cách dấu câu.
- Không được có 2 dấu cách liên tiếp.
- Không được có khoảng trắng thừa đầu câu hoặc cuối câu.

Ví dụ đúng:

```text
谈 胥，明天 见 一 面 好好 聊 聊。
Tán Xū，míngtiān jiàn yí miàn hǎohǎo liáo liáo。
```

Ví dụ sai:

```text
谈  胥 ， 明天 见 一 面 好好 聊 聊 。
Tán  Xū ， míngtiān jiàn yí miàn hǎohǎo liáo liáo 。
```

### 6. Tên riêng phải nhất quán

- Tên riêng phải tách đúng theo nhóm pinyin.
- Không được cùng một tên mà lúc gộp, lúc tách khác nhau trong cùng file.
- Khi một tên đã chốt cách tách, phải giữ nguyên trong toàn bộ file.

Ví dụ đúng:

```text
徐 栀
Xú Zhī
```

```text
谈 胥
Tán Xū
```

```text
陈 路周
Chén Lùzhōu
```

### 7. Câu nói thông thường phải tách theo nhịp pinyin

- Ưu tiên canh đều giữa Chinese và pinyin.
- Chỉ ghép nếu thật sự là một cụm đi với nhau trong pinyin.
- Nếu pinyin đang tách nhỏ thì Chinese cũng phải tách nhỏ tương ứng.
- Dấu câu phải được bỏ qua khi đếm và so khớp số cụm từ.

Ví dụ đúng:

```text
您 好，您 拨打……
Nín hǎo，nín bōdǎ……
```

```text
我 都 说 过 了，她 不 是 我 女 朋友。
Wǒ dōu shuō guò le，tā bú shì wǒ nǚ péngyou。
```

## Yêu cầu đối với backend tạo subtitle bundle

- Backend phải nhận diện cả dấu câu tiếng Trung và dấu câu ASCII cũ để tương thích với dữ liệu lịch sử.
- Backend phải tách dấu câu thành segment `punctuation` độc lập trước khi ghép Chinese với pinyin.
- Backend phải nhận diện `——` là dấu câu ngắt lời; chỉ dấu `-` đứng đầu hoặc nằm giữa hai phần thoại mới được xem là marker đổi người nói.
- Segment dấu câu phải có `pinyin` rỗng hoặc `null`.
- Marker đổi người nói `-` phải được nhận diện là segment cấu trúc hoặc punctuation có `pinyin` rỗng, không được coi là từ vựng.
- Backend chỉ được ghép các segment từ vựng sau khi đã loại dấu câu ở cả hai dòng.
- Không được chỉ dùng `split(" ")` để ghép Chinese với pinyin.
- Backend phải coi khoảng trắng trong file SRT là ranh giới cụm từ đã được biên tập; không được tách tiếp Chinese theo từng Hán tự hoặc theo số lượng âm tiết pinyin.
- Với 儿化音, backend phải giữ toàn bộ cụm như `哪儿` trong một segment `word` và gán pinyin rút gọn tương ứng như `nǎr`; không được tạo segment riêng cho `儿`.
- Nếu số lượng cụm Chinese và pinyin không bằng nhau sau khi loại dấu câu, phải báo lỗi để kiểm tra thủ công.
- Sau khi số lượng cụm bằng nhau, backend vẫn phải kiểm tra từng cặp Chinese–pinyin theo vị trí; không được chỉ kiểm tra tổng số cụm.
- Tuyệt đối không được gán lại pinyin của cụm trước cho cụm sau khi ghép thất bại.
- Khi xuất giao diện, backend phải giữ nguyên dấu câu tiếng Trung từ dòng Chinese.

Ví dụ dữ liệu:

```text
好，唱得 真好
Hǎo，chàngdé zhēnhǎo
```

Phải được tách thành:

```text
Chinese: 好 | ， | 唱得 | 真好
Pinyin:  Hǎo | ， | chàngdé | zhēnhǎo
```

Và tạo bundle tương đương:

```json
[
  { "kind": "word", "text": "好", "pinyin": "Hǎo" },
  { "kind": "punctuation", "text": "，", "pinyin": null },
  { "kind": "word", "text": "唱得", "pinyin": "chàngdé" },
  { "kind": "word", "text": "真好", "pinyin": "zhēnhǎo" }
]
```

## Checklist trước khi kết thúc

Trước khi coi là đã xử lý xong 1 file, phải kiểm tra:

1. Mỗi block có đúng 4 dòng.
2. Chinese và pinyin có cùng số cụm sau khi bỏ qua dấu câu và marker người nói.
3. Từng cặp Chinese–pinyin ở cùng vị trí có phát âm khớp chính xác; không chỉ kiểm tra số lượng cụm.
4. Không có khoảng trắng ở đầu dòng.
5. Không có khoảng trắng ở cuối dòng.
6. Không còn dấu câu ASCII như `,` `.` `?` `!` `:` `;` trong nội dung subtitle.
7. Không có khoảng trắng trước hoặc sau dấu câu tiếng Trung.
8. Dấu `……` được dùng đồng bộ.
9. Vị trí và loại dấu câu đồng bộ giữa Chinese và pinyin.
10. Marker đổi người nói `-` có vị trí và khoảng trắng đồng bộ giữa hai dòng.
11. Không có 2 dấu cách liên tiếp, ngoại trừ khoảng trắng bắt buộc quanh marker `-` vẫn chỉ được đúng 1 dấu.
12. Tên riêng được tách nhất quán trong toàn bộ file.

Lưu ý: dấu phẩy trong timestamp SRT, ví dụ `00:01:48,380`, vẫn phải giữ nguyên theo định dạng SRT và không được đổi thành `，`.

## Cách làm việc cho các file mới

Khi thêm file mới hoặc có yêu cầu chỉnh subtitle, làm theo thứ tự:

1. Đọc một đoạn đầu file để hiểu style pinyin hiện có.
2. Lấy pinyin làm chuẩn để chia cụm từ, nhưng bỏ qua dấu câu và marker người nói khi đếm cụm.
3. Chia lại dòng Chinese sao cho khớp 1-1 theo nhóm pinyin, sau đó kiểm tra phát âm của từng cặp ở cùng vị trí.
4. Chuẩn hóa dấu câu nội dung sang `，` `。` `？` `！` `：` `；` `……` và các cặp dấu tiếng Trung tương ứng.
5. Đảm bảo vị trí dấu câu đồng bộ giữa dòng Chinese và dòng pinyin.
6. Kiểm tra lại các tên riêng lặp lại nhiều lần trong file.
7. Kiểm tra toàn bộ khoảng trắng đầu dòng, cuối dòng, khoảng trắng kép và khoảng trắng quanh dấu câu.

Nếu có yêu cầu chỉnh subtitle sau này, mặc định phải theo đúng README này.
