"""
Camstar 认证令牌生成
=====================
通过用户名和密码生成 Camstar 的 Bearer Auth Token。
原理：构造特定的凭证 JSON 然后进行 Base64 编码。
"""

import json
import base64


def generate_camstar_auth_token(username: str, password: str) -> str:
    """生成 Camstar 兼容的 Base64 认证令牌。"""
    credentials = {
        "username": username,
        "Password": {
            "value": password,
            "isEncrypted": False
        }
    }
    # separators=(',', ':') 确保和标准前端字符串化后不带多余空格
    json_creds = json.dumps(credentials, separators=(',', ':'))
    token_bytes = base64.b64encode(json_creds.encode('utf-8'))
    return token_bytes.decode('utf-8')


if __name__ == "__main__":
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="Generate Camstar Auth Token")
    parser.add_argument("--username", "-u", type=str, default="CamstarAdmin", help="The username for login")
    parser.add_argument("--password", "-p", type=str, required=True, help="The password for login")

    args = parser.parse_args()

    try:
        auth_token = generate_camstar_auth_token(args.username, args.password)
        print("====== 生成 CAMSTAR_AUTH_TOKEN ======")
        print(auth_token)
        print("=====================================")
    except Exception as e:
        print(f"Error generating token: {e}", file=sys.stderr)
