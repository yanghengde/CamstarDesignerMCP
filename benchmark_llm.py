import asyncio
import time
from openai import AsyncOpenAI
import os
from dotenv import load_dotenv

load_dotenv()

LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.deepseek.com/v1")

oai_client = AsyncOpenAI(api_key=LLM_API_KEY, base_url=LLM_BASE_URL)

async def test_model(model_name, prompt):
    print(f"Testing model: {model_name}...")
    start_time = time.time()
    try:
        response_stream = await oai_client.chat.completions.create(
            model=model_name,
            messages=[{"role": "user", "content": prompt}],
            stream=True,
            max_tokens=800
        )
        
        first_chunk_time = None
        full_content = ""
        
        async for chunk in response_stream:
            if len(chunk.choices) == 0:
                continue
            delta = chunk.choices[0].delta
            
            if delta.content:
                if first_chunk_time is None:
                    first_chunk_time = time.time()
                full_content += delta.content
                
        end_time = time.time()
        
        ttft = (first_chunk_time - start_time) if first_chunk_time else 0
        total_time = end_time - start_time
        print(f"[{model_name}] TTFT (Time to First Token): {ttft:.4f}s")
        print(f"[{model_name}] Total Time: {total_time:.4f}s")
        print(f"[{model_name}] Length of response: {len(full_content)} characters")
        print(f"[{model_name}] Speed (approx): {len(full_content) / total_time:.2f} char/s")
        print("-" * 50)
    except Exception as e:
        print(f"Error testing {model_name}: {e}\n")

async def main():
    prompt = "请详细介绍一下工厂制造执行系统 (MES) 的核心模块和功能，要求逻辑清晰，字数在500字左右。"
    print(f"API Base URL: {LLM_BASE_URL}")
    print("=" * 50)
    # Warm up call (optional, but good for fair comparison)
    print("Warming up...")
    try:
        await oai_client.chat.completions.create(
            model="deepseek-chat",
            messages=[{"role": "user", "content": "hello"}],
            max_tokens=10
        )
    except Exception as e:
        pass
    print("=" * 50)
    
    await test_model("deepseek-chat", prompt)
    await test_model("deepseek-v4-flash", prompt)

if __name__ == "__main__":
    asyncio.run(main())
