import asyncio
import sys

sys.stdout.reconfigure(encoding='utf-8')

# Ensure config is loaded
import config  # noqa: F401

from tools.mfgorders import create_mfgorder

async def main():
    order_name = "MO-TEST-20260624-01"
    product_name = "海信家用空调-HS-AC01"
    qty = 100.0
    
    print(f"尝试创建工单: Name={order_name}, Product={product_name} (Rev: 1), Qty={qty}...")
    try:
        res = await create_mfgorder(
            name=order_name,
            product_name=product_name,
            product_revision="1",
            qty=qty,
            description="测试创建海信家用空调工单",
        )
        print("接口返回结果:")
        print(res)
    except Exception as e:
        print(f"执行异常: {e}")

if __name__ == "__main__":
    asyncio.run(main())
