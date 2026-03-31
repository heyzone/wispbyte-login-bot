import os
import asyncio
import aiohttp
import re
from datetime import datetime

# 配置
BASE_URL = "https://wispbyte.com"
# 格式: email----cookie
# 示例: lanxintech@outlook.com----connect.sid=s%3Axxxxxx
ACCOUNTS_STR = os.getenv("LOGIN_ACCOUNTS", "")

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
        "Cookie": cookie_str,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp, culinary/*;q=0.8"
    }

    async with aiohttp.ClientSession(headers=headers) as session:
        # 1. 访问 Dashboard 验证并获取服务器 ID
        async with session.get(f"{BASE_URL}/client/dashboard") as resp:
            html = await resp.text()
            if "login" in resp.url.path or "logout" not in html:
                return {"email": email, "success": False, "reason": "Cookie 已失效，请手动更新"}
            
            # 提取服务器 ID (参考 worker.js 的正则)
            server_ids = list(set(re.findall(r'/servers/([a-f0-9]{8})', html)))
            if not server_ids:
                return {"email": email, "success": False, "reason": "未找到服务器 ID"}

        # 2. 遍历服务器执行重启
        results = []
        for sid in server_ids:
            # 获取 CSRF Token
            async with session.get(f"{BASE_URL}/client/servers/{sid}/console") as c_resp:
                c_html = await c_resp.text()
                csrf_match = re.search(r'name="csrf-token"\s+content="([^"]+)"', c_html)
                csrf = csrf_match.group(1) if csrf_match else ""

            # 发送重启 POST
            post_headers = {"X-CSRF-TOKEN": csrf, "X-Requested-With": "XMLHttpRequest"}
            async with session.post(f"{BASE_URL}/client/api/server/restart", 
                                    json={"serverId": sid}, 
                                    headers=post_headers) as r_resp:
                if r_resp.status == 200:
                    results.append(f"{sid}: OK")
                else:
                    results.append(f"{sid}: Fail({r_resp.status})")
        
        return {"email": email, "success": True, "details": ", ".join(results)}

async def main():
    if not ACCOUNTS_STR:
        print("无账号配置")
        return

    # 解析账号: 邮箱----Cookie
    account_list = [a.split("----") for a in ACCOUNTS_STR.split(",") if "----" in a]
    
    tasks = [run_account(acc[0], acc[1]) for acc in account_list]
    final_results = await asyncio.gather(*tasks)
    
    # 构建通知
    report = [f"🖥 <b>Wispbyte 自动重启报告</b>\n时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"]
    for res in final_results:
        status = "✅" if res["success"] else "❌"
        msg = res["details"] if res["success"] else res["reason"]
        report.append(f"{status} <code>{res['email']}</code>: {msg}")
    
    await tg_notify("\n".join(report))

if __name__ == "__main__":
    asyncio.run(main())
