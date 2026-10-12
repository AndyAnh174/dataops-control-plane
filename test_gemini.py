import time
from dataops_control_plane.config import Settings
from dataops_control_plane.services.provider_factory import create_embedding_provider, create_rca_client

print("=" * 60)
print("🚀 ĐANG KHỞI ĐỘNG KIỂM TRA GOOGLE GEMINI THẬT...")
print("=" * 60)

settings = Settings()
rca_client = create_rca_client(settings)
embedding_provider = create_embedding_provider(settings)

print(f"👉 Model RCA LLM        : {rca_client.model_name}")
print(f"👉 Model Embedding      : {embedding_provider.model_name}")
print(f"👉 Embedding Dimensions : {embedding_provider.dimensions}")

# 1. Test Embedding
print("\n[1/2] Đang thử nghiệm tạo Vector Embedding với Gemini...")
t0 = time.time()
vector = embedding_provider.embed(["Pipeline failed due to data quality contract violation"])
emb_time = round(time.time() - t0, 2)
print(f"✅ Tạo vector thành công trong {emb_time}s! Độ dài vector: {len(vector[0])} chiều.")

# 2. Test LLM RCA
print("\n[2/2] Đang gửi sự cố dữ liệu mẫu lên Gemini để phân tích...")
t0 = time.time()
result = rca_client.generate(
    system_prompt="You are an expert DataOps Incident Analysis Agent.",
    user_prompt=(
        "Pipeline run #42 failed at stage 'data-quality'. "
        "Error log shows: Great Expectations validation failed because column 'amount' was renamed to 'total_amount'. "
        "Please provide root cause analysis and recommended recovery action."
    ),
    schema={
        "type": "object",
        "properties": {
            "root_cause": {"type": "string"},
            "recommended_action": {"type": "string"},
            "confidence": {"type": "number"},
        },
        "required": ["root_cause", "recommended_action", "confidence"],
    },
)
llm_time = round(time.time() - t0, 2)

print(f"✅ Gemini đã phân tích xong trong {llm_time}s!")
print("\n" + "=" * 60)
print("🎯 KẾT QUẢ PHÂN TÍCH TỪ GEMINI TRẢ VỀ:")
print("=" * 60)
print(f"📌 Nguyên nhân gốc (Root Cause):")
print(f"   {result.payload.get('root_cause')}\n")
print(f"📌 Đề xuất xử lý (Recommended Action):")
print(f"   {result.payload.get('recommended_action')}\n")
print(f"📌 Độ tin cậy (Confidence): {result.payload.get('confidence') * 100}%")
print(f"📌 Số token sử dụng      : {result.prompt_tokens + result.completion_tokens} tokens")
print("=" * 60)
print("🎉 CHÚC MỪNG! TÍNH NĂNG GEMINI CLOUD ĐÃ HOẠT ĐỘNG HOÀN HẢO 100%!")
print("=" * 60)
