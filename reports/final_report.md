# Day 25 Reliability Report: Reliability Engineering for Production LLM Agents

## 1. Architecture Summary

Hệ thống **Reliability Gateway** hoạt động như một lớp cổng tiếp nhận và bảo vệ đa tầng đứng trước các nhà cung cấp LLM (Primary & Backup Providers). 

### Luồng định tuyến chi tiết:
1. **Lớp 1 - Semantic Cache Check:** 
   - Kiểm tra rào chắn bảo mật (Privacy Guardrails) đối với các từ khóa nhạy cảm (`password`, `credit card`, `balance`, `ssn`,...). Nếu chứa từ khóa nhạy cảm, bỏ qua cache.
   - Tính toán độ tương đồng Cosine dựa trên word tokens và character 3-grams giữa query mới và các query đã lưu.
   - Kiểm tra chống khớp sai (False-Hit Detection) đối với các số/năm 4 chữ số (ví dụ: query năm 2024 không được dùng cache của 2026).
   - Nếu hợp lệ và đạt ngưỡng tương đồng ($\ge 0.92$), trả về ngay kết quả từ Cache với độ trễ 0ms và chi phí 0$.
2. **Lớp 2 - Circuit Breaker & Provider Fallback Chain:**
   - Mỗi LLM Provider (Primary, Backup) được quản lý độc lập bởi một bộ ngắt mạch **Circuit Breaker** 3 trạng thái (`CLOSED`, `OPEN`, `HALF_OPEN`).
   - Yêu cầu được gửi qua Primary Provider trước. Nếu Primary Provider bị lỗi liên tiếp vượt ngưỡng `failure_threshold`, Circuit Breaker chuyển sang trạng thái `OPEN` để ngắt mạch (Fail-Fast), ngăn chặn bão lỗi (Retry Storm).
   - Khi Primary Provider bị lỗi hoặc mạch đang `OPEN`, Gateway tự động kích hoạt chuyển hướng dự phòng (Fallback) sang Backup Provider.
   - Khi hết thời gian `reset_timeout_seconds`, Circuit Breaker chuyển sang `HALF_OPEN` để gửi 1 request thăm dò (probe request). Nếu thành công, mạch đóng lại (`CLOSED`); nếu thất bại, mạch mở lại (`OPEN`).
3. **Lớp 3 - Static Degradation Fallback:**
   - Trong trường hợp toàn bộ các Provider đều lỗi hoặc đều ở trạng thái `OPEN`, Gateway trả về một phản hồi tĩnh an toàn (`static_fallback`) để thông báo hệ thống đang bảo trì, đảm bảo ứng dụng không bị crash.

```
User Request
    |
    v
[Gateway] ---> [Semantic Cache Check] ---> HIT? (Score >= 0.92 & Valid) ---> Return Cache (0ms, $0)
    |                                                |
    |                                                v MISS / Uncacheable
    v
[Circuit Breaker: Primary] ---------------> Primary Provider (GPT-4 / FakeLLM)
    |  (OPEN or Fail? Fallback)
    v
[Circuit Breaker: Backup] ----------------> Backup Provider (Claude / FakeLLM)
    |  (OPEN or Fail? Fallback)
    v
[Static Fallback Message] ("The service is temporarily degraded...")
```

---

## 2. Configuration

| Setting | Value | Reason & Justification |
|---|---:|---|
| `failure_threshold` | 3 | Cho phép chịu lỗi cục bộ nhỏ (1-2 request flaky) nhưng ngắt mạch ngay nếu có sự cố diện rộng liên tiếp 3 lần. |
| `reset_timeout_seconds` | 2.0s | Thời gian phạt hợp lý trong môi trường thử nghiệm để hệ thống sớm chuyển sang `HALF_OPEN` thăm dò phục hồi. |
| `success_threshold` | 1 | Chỉ cần 1 request thăm dò (probe) thành công ở trạng thái `HALF_OPEN` là đủ để xác nhận Provider đã sống lại. |
| `cache.ttl_seconds` | 300s (5 min) | Giữ câu trả lời trong 5 phút để tận dụng tính lặp lại của truy vấn ngắn hạn mà không bị cũ dữ liệu. |
| `cache.similarity_threshold` | 0.92 | Đã thử nghiệm ở 0.85 gây ra một số false-hit với các câu hỏi tương tự; 0.92 kết hợp n-gram cosine đảm bảo độ chính xác ngữ nghĩa cao. |
| `load_test.requests` | 100 | Mỗi kịch bản chaos chạy 100 request để đảm bảo kích hoạt đủ chu kỳ ngắt mạch, phục hồi và trích xuất số liệu thống kê. |

---

## 3. SLO Definitions

| SLI (Service Level Indicator) | SLO Target | Actual Value | Met? |
|---|---|---:|---|
| **Availability (Độ sẵn sàng)** | $\ge 99\%$ | **99.67%** | **YES** |
| **Latency P95** | $< 2500\text{ ms}$ | **312.02 ms** | **YES** |
| **Fallback Success Rate** | $\ge 95\%$ | **98.55%** | **YES** |
| **Cache Hit Rate** | $\ge 10\%$ | **63.33%** | **YES** |
| **Recovery Time (MTTR)** | $< 5000\text{ ms}$ | **2221.93 ms** | **YES** |

---

## 4. Metrics Summary (Từ `reports/metrics.json`)

| Metric | Value | Ghi chú kỹ thuật |
|---|---:|---|
| `total_requests` | 300 | 100 request x 3 kịch bản chaos |
| `availability` | **0.9967** (99.67%) | Chỉ có 0.33% request chạm đến static fallback |
| `error_rate` | **0.0033** (0.33%) | Tỉ lệ lỗi tổng thể |
| `latency_p50_ms` | **268.41 ms** | Độ trễ trung vị |
| `latency_p95_ms` | **312.02 ms** | Độ trễ phân vị 95% |
| `latency_p99_ms` | **319.25 ms** | Độ trễ phân vị 99% |
| `fallback_success_rate` | **0.9855** (98.55%) | Chuyển hướng sang Backup Provider thành công xuất sắc |
| `cache_hit_rate` | **0.6333** (63.33%) | Tận dụng tốt truy vấn lặp lại trong tập mẫu |
| `circuit_open_count` | **8** | Số lần ngắt mạch của Circuit Breakers |
| `recovery_time_ms` | **2221.93 ms** | Thời gian trung bình từ khi OPEN đến khi phục hồi CLOSED (~2.2s) |
| `estimated_cost` | **$0.047186** | Tổng chi phí thực tế tiêu tốn |
| `estimated_cost_saved` | **$0.190000** | Chi phí tiết kiệm được nhờ Cache |

---

## 5. Cache Comparison: Có Cache vs Không Có Cache

So sánh kết quả chạy thực nghiệm giữa 2 chế độ `cache.enabled: false` và `cache.enabled: true`:

| Metric | Without Cache | With Cache | Delta / Lợi ích |
|---|---:|---:|---|
| `latency_p50_ms` | 269.31 ms | 268.41 ms | Giảm ~0.9 ms |
| `latency_p95_ms` | 316.37 ms | 312.02 ms | Giảm ~4.35 ms |
| `estimated_cost` | $0.126682 | $0.047186 | **Tiết kiệm 62.7% chi phí!** |
| `cache_hit_rate` | 0.0% | 63.33% | +63.33% |
| `circuit_open_count` | 20 | 8 | **Giảm 60% áp lực quá tải lên Provider!** |
| `availability` | 94.67% | 99.67% | **Tăng 5.0% độ sẵn sàng** |

> **Nhận xét:** Cache không chỉ giúp tiết kiệm chi phí mà còn đóng vai trò là "tấm khiên" hấp thụ tới 63% lượng truy vấn, giúp giảm đáng kể số lần Provider bị quá tải và ngắt mạch (từ 20 lần xuống 8 lần), từ đó kéo độ sẵn sàng tổng thể từ 94.67% lên 99.67%.

---

## 6. Redis Shared Cache

### Tại sao cần Shared Cache trong Production?
- **Hạn chế của In-memory Cache:** Trong mô hình microservices hoặc Kubernetes với nhiều pod/instance chạy song song, In-memory Cache bị phân mảnh cục bộ trong RAM của từng instance. Instance A đã cache câu trả lời nhưng Instance B không hề biết, dẫn đến gọi trùng lặp lên LLM và lãng phí chi phí.
- **Giải pháp `SharedRedisCache`:** Sử dụng cụm Redis tập trung làm backend lưu trữ:
  - Chia sẻ tức thì kết quả cache giữa mọi instance.
  - Tự động quản lý vòng đời dữ liệu thông qua Redis `EXPIRE` (TTL tự động, không tốn tài nguyên dọn rác thủ công).
  - Tích hợp hàm băm truy vấn xác định `_query_hash()` và tìm kiếm tương đồng ngữ nghĩa qua `scan_iter`.

### Bằng chứng chia sẻ trạng thái (Shared State):
Cài đặt `SharedRedisCache` đã vượt qua toàn bộ các bài kiểm tra tích hợp:
- `test_set_and_exact_get`: Lưu bằng hash key và truy xuất chính xác 1.0.
- `test_shared_state_across_instances`: Instance `c1` ghi dữ liệu, Instance `c2` đọc được ngay lập tức dữ liệu đó.
- `test_privacy_query_not_cached` & `test_false_hit_different_years`: Các rào chắn bảo mật và chống lệch năm hoạt động đồng nhất trên Redis.

---

## 7. Chaos Scenarios & Đánh Giá

| Scenario | Expected Behavior | Observed Behavior | Status |
|---|---|---|:---:|
| `primary_timeout_100` | Primary Provider chết 100% $\rightarrow$ Circuit Breaker Primary ngắt mạch (`OPEN`), toàn bộ traffic chuyển sang Backup Provider. | Circuit Breaker Primary mở mạch sau 3 lỗi liên tiếp; 100% request còn lại định tuyến mượt mà sang Backup. | **PASS** |
| `primary_flaky_50` | Primary Provider lỗi 50% $\rightarrow$ Circuit Breaker dao động giữa các trạng thái `CLOSED` $\leftrightarrow$ `OPEN` $\leftrightarrow$ `HALF_OPEN`, lưu lượng phân bổ xen kẽ Primary và Backup. | Circuit Breaker chuyển trạng thái linh hoạt, tự động thăm dò và phục hồi khi Primary ổn định trở lại. | **PASS** |
| `all_healthy` | Cả hai Provider đều khỏe mạnh $\rightarrow$ 100% request được xử lý qua Primary Provider hoặc Cache, không có Circuit Breaker nào bị ngắt. | Tất cả request thành công qua route `primary` hoặc `cache_hit`, `circuit_open_count = 0`. | **PASS** |

---

## 8. Failure Analysis & Điểm Yếu Cần Khắc Phục

### 1. Điểm yếu còn tồn tại:
- **Trạng thái Circuit Breaker chưa được chia sẻ (Local Breaker State):** Hiện tại Circuit Breaker mới lưu trạng thái trong RAM của từng tiến trình. Trong mô hình nhiều instance, nếu Primary Provider chết, mỗi instance đều phải chịu 3 lần lỗi cục bộ trước khi ngắt mạch.
- **Chi phí quét tương đồng ngữ nghĩa trên Redis:** Khi lượng key trên Redis lớn (hàng triệu keys), việc duyệt qua `scan_iter` và tính cosine similarity trên Python sẽ gây nghẽn CPU và tăng độ trễ.

### 2. Giải pháp cải tiến trước khi lên Production:
- **Distributed Circuit Breaker:** Lưu counter lỗi và trạng thái mạch lên Redis bằng `INCR` và `EXPIRE` dạng atomic keys.
- **Vector Database / Redis RediSearch:** Sử dụng module Vector Search của Redis hoặc chuyển sang Vector DB chuyên dụng (Qdrant, Pinecone) để tìm kiếm Semantic Cache theo độ phức tạp $O(\log N)$ thay vì quét tuần tự $O(N)$.
- **Cost Budget Cap:** Cài đặt ngưỡng chi phí tối đa theo ngày/tháng, tự động chuyển sang mô hình LLM giá rẻ khi ngân sách vượt quá 80%.

---

## 9. Kế Hoạch Cải Tiến Tiếp Theo (Next Steps)

1. **Redis Vector Search:** Nâng cấp `SharedRedisCache` sử dụng Text Embeddings (ví dụ: `text-embedding-3-small`) kết hợp Redis Vector Indexing để tăng tốc độ truy vấn cache dưới 2ms cho hàng triệu bản ghi.
2. **Concurrency & Thread Pooling:** Bổ sung `ThreadPoolExecutor` vào Gateway để xử lý song song các tác vụ phân tích PII và định tuyến Provider.
3. **Dynamic Timeout & Adaptive Circuit Breaker:** Tự động điều chỉnh `reset_timeout_seconds` tăng dần (Exponential Backoff) nếu probe request trong trạng thái `HALF_OPEN` tiếp tục thất bại nhiều lần.
