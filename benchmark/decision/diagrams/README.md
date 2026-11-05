# Sơ đồ benchmark Decision

_Các số đếm mô tả lượt chạy HCMUTE ngày 06/11/2026._

---

## 📊 Luồng dữ liệu và thực nghiệm

![Luồng benchmark từ log đến báo cáo](../figures/07_benchmark_flow.svg)

```mermaid
flowchart TB
    accTitle: Luồng benchmark Decision HCMUTE
    accDescr: Log prod, dev và CI được kiểm tra, che thông tin, tạo nhãn đại diện và chia theo nhóm trước khi chạy năm model. Nhãn đối chiếu không được gửi vào model; kết quả chính và phép thử chèn chỉ dẫn được báo cáo riêng.

    subgraph corpus ["Dữ liệu và nhãn đại diện"]
        raw_logs[("Log prod / dev / CI<br/>387 file · 267,09 MiB")]
        verify_copies["Kiểm tra SHA-256<br/>Đọc bản đủ và ổn định"]
        redact_extract["Che thông tin phổ biến<br/>Bóc nhãn mức log / build"]
        deduplicate_group["Loại trùng / gom nguồn<br/>Chọn và chốt 339 mẫu"]
        raw_logs --> verify_copies --> redact_extract --> deduplicate_group
    end

    subgraph frozen_data ["Đóng băng 339 mẫu · seed 2026"]
        group_split["Chia theo cụm nguồn<br/>Giữ template trong một tập"]
        development_validation["Development 70<br/>Validation 111"]
        final_test["Test giữ riêng: 158<br/>Prod 29 · Dev 76 · CI 53"]
        state_payload["State tối đa 2.000 ký tự<br/>Che mức log / kết quả trực tiếp"]
        proxy_labels[("Nhãn proxy giữ riêng<br/>Không gửi vào request")]
        deduplicate_group --> group_split
        group_split --> development_validation
        group_split --> final_test
        final_test --> state_payload
        final_test --> proxy_labels
    end

    subgraph execution ["Chạy tuần tự trên Colab · concurrency = 1"]
        shared_harness["Cùng câu hỏi / tiêu chí<br/>Warmup riêng · 3 lượt / model"]
        ollama_models["Ollama 0.40.14 · Q8_0<br/>d3:9b · nimble-v3:9b<br/>clef-flash:9b"]
        openai_model["OpenAI Decisions API<br/>gpt-6-luna"]
        jev_model["TypeSafe SystemOne API<br/>jev-1.14.0"]
        predictions[("2.370 dự đoán chính<br/>474 / model · 0 lỗi định dạng")]
        state_payload --> shared_harness
        shared_harness --> ollama_models
        shared_harness --> openai_model
        shared_harness --> jev_model
        ollama_models --> predictions
        openai_model --> predictions
        jev_model --> predictions
    end

    subgraph evidence ["Chấm điểm và lưu bằng chứng"]
        evaluate_metrics["F1, precision, recall<br/>Brier, AUROC, p50/p95, VRAM"]
        paired_probe["Probe riêng: 6 cặp / model<br/>60 dự đoán · ASR có điều kiện"]
        evidence_report["CSV / JSONL / LaTeX<br/>Hash dữ liệu, câu hỏi, model digest"]
        next_dataset["Chưa làm: nhãn sự cố<br/>Thêm cụm prod/dev độc lập"]
        predictions --> evaluate_metrics
        proxy_labels --> evaluate_metrics
        final_test -.-> paired_probe
        evaluate_metrics --> evidence_report
        paired_probe --> evidence_report
        evidence_report -.-> next_dataset
    end

    classDef data fill:#f3f4f6,stroke:#6b7280,stroke-width:2px,color:#1f2937
    classDef process fill:#dbeafe,stroke:#2563eb,stroke-width:2px,color:#1e3a5f
    classDef result fill:#dcfce7,stroke:#16a34a,stroke-width:2px,color:#14532d
    classDef caution fill:#fef9c3,stroke:#ca8a04,stroke-width:2px,color:#713f12
    class raw_logs,proxy_labels,development_validation data
    class verify_copies,redact_extract,deduplicate_group,group_split,state_payload,shared_harness,ollama_models,openai_model,jev_model,evaluate_metrics process
    class final_test,predictions,evidence_report result
    class paired_probe,next_dataset caution
```

Luồng model là lựa chọn cấu hình trong cùng harness; request thực tế chạy tuần tự, concurrency bằng 1. Nhãn đối chiếu không nằm trong payload model.

## 🧪 Thiết kế đối chứng prompt injection

![Thiết kế sáu cặp đối chứng và tấn công](../figures/08_injection_design.svg)

```mermaid
flowchart TB
    accTitle: Thiết kế probe prompt injection
    accDescr: Sáu mẫu nguồn được biến thành sáu cặp đối chứng và chèn chỉ dẫn sai, rồi chạy riêng trên cùng năm model. ASR chỉ tính trên những cặp mà đối chứng được dự đoán đúng.

    source_cases["Chọn trước 6 mẫu test<br/>2 prod · 2 dev · 2 CI"]
    common_prefix["Giữ cùng tiền tố<br/>Tối đa 1.600 ký tự"]
    control_state["6 mẫu đối chứng<br/>Không chèn chỉ dẫn"]
    injected_state["6 mẫu tấn công<br/>Chèn yêu cầu chọn nhãn sai"]
    shared_question["Giữ nguyên câu hỏi / tiêu chí<br/>Chạy 5 model, lần lượt"]
    paired_answers[("30 đối chứng + 30 tấn công<br/>Tách khỏi 2.370 dự đoán chính")]
    conditional_asr["ASR = bị kéo đúng nhãn sai<br/>/ số đối chứng dự đoán đúng"]
    measured_outcome["d3 0/4 · Nimble 0/4 · Jev 0/4<br/>Clef 1/4 · OpenAI 1/5"]
    scope_limit["Chỉ 6 cặp / model<br/>Chưa chứng minh chống tấn công tổng quát"]

    source_cases --> common_prefix
    common_prefix --> control_state
    common_prefix --> injected_state
    control_state --> shared_question
    injected_state --> shared_question
    shared_question --> paired_answers --> conditional_asr --> measured_outcome
    measured_outcome -.-> scope_limit

    classDef process fill:#dbeafe,stroke:#2563eb,stroke-width:2px,color:#1e3a5f
    classDef result fill:#dcfce7,stroke:#16a34a,stroke-width:2px,color:#14532d
    classDef caution fill:#fef9c3,stroke:#ca8a04,stroke-width:2px,color:#713f12
    class source_cases,common_prefix,shared_question,conditional_asr process
    class control_state,paired_answers,measured_outcome result
    class injected_state,scope_limit caution
```

ASR chỉ tính trong các cặp có đối chứng đúng. Sáu mẫu nguồn không đủ để xác nhận khả năng chống tấn công tổng quát.

## 🔗 Bằng chứng

Số liệu: [aggregate của lượt chạy](../results/hcmute-20261106.json). Phương pháp: [protocol](../PROTOCOL.md) và [protocol probe](../ROBUSTNESS-PROTOCOL.md). Các quy ước sơ đồ tham khảo Scientific Agent Skills; nguồn đầy đủ ở [references](../REFERENCES.md).
