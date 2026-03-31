import os
import asyncio
import aiohttp
import re
import json
from datetime import datetime
from base64 import b64encode
from nacl import encoding, public

# --- 配置区 ---
BASE_URL = "https://wispbyte.com"
# 从环境变量读取 Secret
ACCOUNTS_STR = os.getenv("LOGIN_ACCOUNTS", "")
GH_PAT = os.getenv("GH_PAT")
GH_REPO = os.getenv("GITHUB_REPOSITORY")

async def update_github_secret(new_value):
    """使用 PyNaCl 加密并回写 GitHub Secret"""
    if not GH_PAT or not GH_REPO:
        print("❌ 缺少 GH_PAT 或 GITHUB_REPOSITORY 环境变量，无法更新 Secret")
        return False

    headers = {
        "Authorization": f"token {GH_PAT}",
        "Accept": "application/vnd.github.v3+json"
    }
    
    try:
        async with aiohttp.ClientSession(headers=headers) as session:
            # 1. 获取公钥
            get_key_url = f"https://api.github.com/repos/{GH_REPO}/actions/secrets/public-key"
            async with session.get(get_key_url) as resp:
                if resp.status != 200:
                    print(f"❌ 获取公钥失败: {resp.status}")
                    return False
                key_data = await resp.json()
                public_key = key_data['key']
                key_id = key_data['key_id']

            # 2. 加密 Secret 值
            def encrypt(pk_str: str, val_str: str) -> str:
                pk = public.PublicKey(pk_str.encode("utf-8"), encoding.Base64Encoder)
                sealed_box = public.SealedBox(pk)
                encrypted = sealed_box.encrypt(val_str.encode("utf-8"))
                return b64encode(encrypted).decode("utf-8")

            encrypted_value = encrypt(public_key, new_value)

            # 3. 提交更新
            put_url = f"https://api.github.com/repos/{GH_REPO}/actions/secrets/LOGIN_ACCOUNTS"
            put_data = {"encrypted_value": encrypted_value, "key_id": key_id}
            async with session.put(put_url, json=put_data) as resp:
                if resp.status in [201, 204]:
                    print("✅ GitHub Secret: LOGIN_ACCOUNTS 更新成功")
                    return True
                else:
                    print(f"❌ Secret 更新失败，HTTP 状态码: {resp.status}")
                    return False
    except Exception as e:
        print(f"❌ 更新 Secret 时发生异常: {e}")
        return False

async def tg_notify(message: str):
    """发送 Telegram 通知"""
    token = os.getenv("TG_BOT_TOKEN")
    chat_id = os.getenv("TG_CHAT_ID")
    if token and chat_id:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        async with aiohttp.ClientSession() as session:
            await session.post(url, json={
                "chat_id": chat_id, 
                "text": message, 
                "parse_mode": "HTML",
                "disable_web_page_preview": True
            })

async def run_account(email, cookie_str):
    """单个账号的监控与重启逻辑"""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
        "Cookie": cookie_str,
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "X-Requested-With": "XMLHttpRequest"
    }
    
    new_cookie_found = None
    async with aiohttp.ClientSession(headers=headers) as session:
        # 1. 验证登录并提取新 Cookie
        try:
            async with session.get(f"{BASE_URL}/client/dashboard") as resp:
                # 检查响应头中的 Set-Cookie
                for c_name, c_obj in resp.cookies.items():
                    if c_name == "connect.sid":
                        potential_new = f"connect.sid={c_obj.value}"
                        if potential_new != cookie_str:
                            new_cookie_found = potential_new
                
                html = await resp.text()
                if "logout" not in html:
                    return {"email": email, "success": False, "reason": "Cookie 已失效", "new_cookie": None}
                
                # 提取所有服务器 ID
                server_ids = list(set(re.findall(r'/servers/([a-f0-9]{8})', html)))
        except Exception as e:
            return {"email": email, "success": False, "reason": f"访问异常: {str(e)}", "new_cookie": None}

        details = []
        for sid in server_ids:
            # 2. 获取服务器真实状态 (API 方式)
            is_online = True
            try:
                async with session.get(f"{BASE_URL}/client/servers/{sid}/status") as s_resp:
                    if s_resp.status == 200:
                        data = await s_resp.json()
                        status = str(data.get('status', data.get('state', ''))).lower()
                        # 只有包含 run/on/start 才认为在线
                        is_online = any(x in status for x in ['run', 'on', 'start'])
                    else:
                        # 备选方案：检查页面元素
                        async with session.get(f"{BASE_URL}/client/servers/{sid}/console") as c_resp:
                            c_html = await c_resp.text()
                            is_online = "text-success" in c_html or "online" in c_html.lower()
            except:
                is_online = True # 报错时默认跳过，防止误重启

            status_icon = "🟢 在线" if is_online else "🔴 离线"
            
            if not is_online:
                # 3. 执行重启
                async with session.get(f"{BASE_URL}/client/servers/{sid}/console") as c_resp:
                    c_html = await c_resp.text()
                    csrf = re.search(r'name="csrf-token"\s+content="([^"]+)"', c_html)
                    csrf_token = csrf.group(1) if csrf else ""
                
                post_headers = {**headers, "X-CSRF-TOKEN": csrf_token}
                async with session.post(f"{BASE_URL}/client/api/server/restart", 
                                        json={"serverId": sid}, headers=post_headers) as r_resp:
                    res_text = "🔄 已重启" if r_resp.status == 200 else "❌ 重启请求失败"
                    details.append(f"<code>{sid}</code>: {status_icon} -> {res_text}")
            else:
                details.append(f"<code>{sid}</code>: {status_icon} (跳过)")
            
            await asyncio.sleep(1) # 避免请求过快

        return {
            "email": email, 
            "success": True, 
            "details": "\n".join(details) if details else "未发现服务器", 
            "new_cookie": new_cookie_found
        }

async def main():
    if not ACCOUNTS_STR:
        print("❌ 未在 Secret 中发现 LOGIN_ACCOUNTS 配置")
        return

    # 解析多账号格式：email1----cookie1,email2----cookie2
    account_pairs = [a.split("----") for a in ACCOUNTS_STR.split(",") if "----" in a]
    
    tasks = [run_account(acc[0], acc[1]) for acc in account_pairs]
    results = await asyncio.gather(*tasks)
    
    report = [f"🖥 <b>Wispbyte 监控报告</b>\n{datetime.now().strftime('%m-%d %H:%M')}\n"]
    new_config_list = []
    any_updated = False

    for i, res in enumerate(results):
        icon = "✅" if res["success"] else "⚠️"
        report.append(f"{icon} <b>{res['email']}</b>\n{res.get('details', res.get('reason'))}")
        
        # 即使运行失败，也保留旧 Cookie；运行成功且有新 Cookie，则更新
        final_cookie = res["new_cookie"] if res["new_cookie"] else account_pairs[i][1]
        new_config_list.append(f"{res['email']}----{final_cookie}")
        
        if res["new_cookie"]:
            any_updated = True
            print(f"✨ 账号 {res['email']} 捕获到新 Cookie")

    # 自动回写 GitHub Secret
    if any_updated:
        new_secret_val = ",".join(new_config_list)
        success = await update_github_secret(new_secret_val)
        if success:
            report.append(f"\n🔄 <b>Cookie 已自动同步至 Secret</b>")
        else:
            report.append(f"\n⚠️ <b>Cookie 同步失败 (检查 GH_PAT 权限)</b>")

    await tg_notify("\n".join(report))

if __name__ == "__main__":
    asyncio.run(main())
