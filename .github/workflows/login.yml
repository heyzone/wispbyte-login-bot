import os
import asyncio
import aiohttp
import re
from datetime import datetime
from base64 import b64encode
from nacl import encoding, public

# 配置
BASE_URL = "https://wispbyte.com"
ACCOUNTS_STR = os.getenv("LOGIN_ACCOUNTS", "")
GH_PAT = os.getenv("GH_PAT")
GH_REPO = os.getenv("GITHUB_REPOSITORY") # 格式: 用户名/仓库名

async def update_github_secret(new_value):
    """全自动回写 GitHub Secret"""
    if not GH_PAT or not GH_REPO:
        print("❌ 未检测到 GH_PAT 或 GH_REPO，无法自动更新")
        return False

    headers = {"Authorization": f"token {GH_PAT}", "Accept": "application/vnd.github.v3+json"}
    secret_name = "LOGIN_ACCOUNTS"
    
    async with aiohttp.ClientSession(headers=headers) as session:
        # 1. 获取公钥 (Public Key)
        get_key_url = f"https://api.github.com/repos/{GH_REPO}/actions/secrets/public-key"
        async with session.get(get_key_id_url := get_key_url) as resp:
            if resp.status != 200: return False
            key_data = await resp.json()
            public_key = key_data['key']
            key_id = key_data['key_id']

        # 2. 使用 PyNaCl 加密新 Secret
        def encrypt(public_key: str, secret_value: str) -> str:
            public_key = public.PublicKey(public_key.encode("utf-8"), encoding.Base64Encoder)
            sealed_box = public.SealedBox(public_key)
            encrypted = sealed_box.encrypt(secret_value.encode("utf-8"))
            return b64encode(encrypted).decode("utf-8")

        encrypted_value = encrypt(public_key, new_value)

        # 3. 提交更新
        put_url = f"https://api.github.com/repos/{GH_REPO}/actions/secrets/{secret_name}"
        put_data = {"encrypted_value": encrypted_value, "key_id": key_id}
        async with session.put(put_url, json=put_data) as resp:
            return resp.status in [201, 204]

async def tg_notify(message: str):
    token = os.getenv("TG_BOT_TOKEN")
    chat_id = os.getenv("TG_CHAT_ID")
    if token and chat_id:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        async with aiohttp.ClientSession() as session:
            await session.post(url, json={"chat_id": chat_id, "text": message, "parse_mode": "HTML"})

async def run_account(email, cookie_str):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Cookie": cookie_str
    }
    
    new_cookie_found = None
    async with aiohttp.ClientSession(headers=headers) as session:
        # 1. 访问 Dashboard 检查状态并尝试获取新 Cookie
        async with session.get(f"{BASE_URL}/client/dashboard") as resp:
            # 捕获 set-cookie
            raw_cookies = resp.headers.getall('Set-Cookie', [])
            for c in raw_cookies:
                if "connect.sid=" in c:
                    new_val = c.split(';')[0]
                    if new_val != cookie_str:
                        new_cookie_found = new_val

            html = await resp.text()
            if "logout" not in html:
                return {"email": email, "success": False, "reason": "Cookie 已失效", "new_cookie": None}
            
            server_ids = list(set(re.findall(r'/servers/([a-f0-9]{8})', html)))

        details = []
        for sid in server_ids:
            # 2. 状态监测
            async with session.get(f"{BASE_URL}/client/servers/{sid}/console") as c_resp:
                c_html = await c_resp.text()
                is_offline = "online" not in c_html.lower() or "offline" in c_html.lower()
                status_icon = "🔴 离线" if is_offline else "🟢 在线"
                
                if is_offline:
                    csrf = re.search(r'name="csrf-token"\s+content="([^"]+)"', c_html)
                    post_h = {"X-CSRF-TOKEN": csrf.group(1) if csrf else "", "X-Requested-With": "XMLHttpRequest"}
                    async with session.post(f"{BASE_URL}/client/api/server/restart", json={"serverId": sid}, headers=post_h) as r_resp:
                        res_text = "🔄 重启成功" if r_resp.status == 200 else "❌ 重启失败"
                        details.append(f"<code>{sid}</code>: {status_icon} -> {res_text}")
                else:
                    details.append(f"<code>{sid}</code>: {status_icon} (跳过)")

        return {"email": email, "success": True, "details": "\n".join(details), "new_cookie": new_cookie_found}

async def main():
    if not ACCOUNTS_STR: return
    account_pairs = [a.split("----") for a in ACCOUNTS_STR.split(",") if "----" in a]
    
    tasks = [run_account(acc[0], acc[1]) for acc in account_pairs]
    results = await asyncio.gather(*tasks)
    
    report = [f"🖥 <b>Wispbyte 智能监控报告</b>\n{datetime.now().strftime('%m-%d %H:%M')}\n"]
    new_config_list = []
    updated = False

    for i, res in enumerate(results):
        icon = "✅" if res["success"] else "⚠️"
        report.append(f"{icon} <b>{res['email']}</b>\n{res.get('details', res.get('reason'))}")
        
        # 构建新配置
        final_cookie = res["new_cookie"] if res["new_cookie"] else account_pairs[i][1]
        new_config_list.append(f"{res['email']}----{final_cookie}")
        if res["new_cookie"]: updated = True

    # 自动写回 GitHub
    if updated:
        new_str = ",".join(new_config_list)
        success = await update_github_secret(new_str)
        report.append(f"\n🔄 <b>Cookie 已更新并{'自动同步至 Secret' if success else '回写失败'}</b>")

    await tg_notify("\n".join(report))

if __name__ == "__main__":
    asyncio.run(main())
