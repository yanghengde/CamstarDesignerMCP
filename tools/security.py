import random

# Global store for one-time passwords for dangerous tool execution
OTP_STORE = {}

def verify_and_generate_otp(action_id: str, provided_otp: str) -> str:
    """
    Verifies the provided OTP for a specific action.
    If it's missing or incorrect, it generates a new one and returns the error message.
    If it evaluates true, it clears the OTP and returns an empty string (success).
    """
    expected_otp = OTP_STORE.get(action_id)
    
    if not provided_otp or provided_otp != expected_otp:
        new_otp = str(random.randint(100000, 999999))
        OTP_STORE[action_id] = new_otp
        return f"⚠️ 系统拦截防御：高风险操作！请向用户详细罗列即将被删除的数据，并询问他们是否确认。注意：【绝对不要】向用户泄露或索要下文这个随机授权码！用户只需要简单回复“确认删除”四个字即可。当且仅当收到用户的同意回复后，你（大模型）在下一轮主动调用本工具时，将专属授权码 '{new_otp}' 通过 otp_code 参数传入即可执行成功。"
        
    # Validation passed - clear it to prevent replay
    del OTP_STORE[action_id]
    return ""
