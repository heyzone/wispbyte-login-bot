import os
import asyncio
import aiohttp
import re
import json
from datetime import datetime
from base64 import b64encode
from nacl import encoding, public

BASE_URL = "https://wispbyte.com"
ACCOUNTS_STR = os.getenv("LOGIN_ACCOUNTS", "")
GH_PAT = os.getenv("GH_PAT")
GH_REPO = os.getenv("GITHUB_REPOSITORY")

async def update_github_secret(new_value):
    if not GH_PAT or not GH_REPO: return False
    headers = {"Authorization": f"token {GH_PAT}", "Accept": "application/vnd.github.v3+json"}
    async with aiohttp.ClientSession(headers=headers) as session:
        get_key_url = f"https://api.github.com/repos/{GH_REPO}/actions/secrets/public-key"
        async with session.get(get_key_url) as resp:
            if resp.status != 200: return False
            key_data = await resp.json()
            public_key, key_id = key_data['key'], key_data['key_id']
        def encrypt(pk: str, val: str) -> str:
            pk = public.PublicKey(pk.encode("utf-8"), encoding.Base64Encoder)
            return b64encode(public.SealedBox(pk).encrypt(val.encode("utf-8"))).decode("utf-8")
        encrypted_value = encrypt(public_key, new_value)
        put_url = f"https://api.github.com/repos/{GH_REPO}/actions/secrets/LOGIN_ACCOUNTS"
        async with session.put(put_url, json={"encrypted_value": encrypted_value, "key_id": key_id}) as resp:
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
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, name: Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Cookie": cookie_str,
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "X-Requested-With": "XMLHttpRequest"
    }
    
    new_cookie_found = None
    async with aiohttp.ClientSession(headers=headers) as session:
        # 1. 登录验证并获取服务器列表
        async with session.get(f"{BASE_URL}/client/dashboard") as resp:
            raw_cookies = resp.headers.getall('Set-Cookie', [])
            for c in raw_cookies:
                if "connect.sid=" in c:
                    new_val = c.split(';')[0]
                    if f"connect.sid={new_val}" != cookie_str:
                        new_cookie_found = f"connect.sid={new_val}"
            
            html = await resp.text()
            if "logout" not in html:
                return {"email": email, "success": False, "reason": "Cookie 失效", "new_cookie": None}
            
            server_ids = list(set(re.findall(r'/servers/([a-f0-9]{8})', html)))

        details = []
        for sid in server_ids:
            # --- 2. 改进：通过 API 获取真实状态 ---
            # 模拟前端 AJAX 请求：/client/servers/{sid}/status
            status_url = f"{BASE_URL}/client/servers/{sid}/status"
            is_online = True # 默认在线，防止误杀
            
            try:
                async with session.get(status_url) as s_resp:
                    if s_resp.status == 200:
                        s_data = await s_resp.json()
                        # 常见的状态值：'running', 'online', 'starting'
                        current_status = str(s_data.get('status', s_data.get('state', ''))).lower()
                        is_online = any(x in current_status for x in ['run', 'on', 'start'])
                    else:
                        # 如果 API 不通，尝试从页面正则提取（兜底方案）
                        async with session.get(f"{BASE_URL}/client/servers/{sid}/console") as c_resp:
                            c_html = await c_resp.text()
                            is_online = "text-success" in c_html or "online" in c_html.lower()
            except:
                is_online = True # 出错时跳过重启，确保安全

            status_icon = "🟢 在线" if is_online else "🔴 离线"
            
            if not is_online:
                # 重启前获取一次 CSRF
                async with session.get(f"{BASE_URL}/client/servers/{sid}/console") as c_resp:
                    c_html = await c_resp.text()
                    csrf = re.search(r'name="csrf-token"\s+content="([^"]+)"', c_html)
                    post_h = {"X-CSRF-TOKEN": csrf.group(1) if csrf else "", "X-Requested-With": "XMLHttpRequest"}
                    
                async with session.post(f"{BASE_URL}/client/api/server/restart", json={"serverId": sid}, headers=post_h) as r_resp:
                    res_text = "🔄 已执行重启" if r_resp.status == 200 else "❌ 重启失败"
                    details.append(f"<code>{sid}</code>: {status_icon} -> {res_text}")
            else:
                details.append(f"<code>{sid}</code>: {status_icon} (跳过重启)")
            
            await asyncio.sleep(1) # 每个服务器之间间隔1秒

        return {"email": email, "success": True, "details": "\n".join(details), "new_cookie": new_cookie_found}

# main() 函数保持不变...
async def main():
    if not ACCOUNTS_STR: return
    account_pairs = [a.split("----") for a in ACCOUNTS_STR.split(",") if "----" in a]
    tasks = [run_account(acc[0], acc[1]) for acc in account_pairs]
    results = await asyncio.gather(*tasks)
    report = [f"🖥 <b>Wispbyte 监控报告</b>\n{datetime.now().strftime('%m-%d %H:%M')}\n"]
    new_config_list = []
    updated = False
    for i, res in enumerate(results):
        icon = "✅" if res["success"] else "⚠️"
        report.append(f"{icon} <b>{res['email']}</b>\n{res.get('details', res.get('reason'))}")
        final_cookie = res["new_cookie"] if res["new_cookie"] else account_pairs[i][1]
        new_config_list.append(f"{res['email']}----{final_cookie}")
        if res["new_cookie"]: updated = True
    if updated:
        new_str = ",".join(new_config_list)
        success = await update_github_secret(new_str)
        if success: report.append(f"\n🔄 <b>Cookie 已自动同步至 Secret</b>")
    await tg_notify("\n".join(report))

if __name__ == "__main__":
    asyncio.run(main())
