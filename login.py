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
ACCOUNTS_STR = os.getenv("LOGIN_ACCOUNTS", "")
GH_PAT = os.getenv("GH_PAT")
GH_REPO = os.getenv("GITHUB_REPOSITORY")

async def update_github_secret(new_value):
    """使用 PyNaCl 加密并回写 GitHub Secret"""
    if not GH_PAT or not GH_REPO:
        print("❌ 缺少 GH_PAT 或 GITHUB_REPOSITORY 环境变量")
        return False

    headers = {
        "Authorization": f"token {GH_PAT}",
        "Accept": "application/vnd.github.v3+json"
    }
    
    try:
        async with aiohttp.ClientSession(headers=headers) as session:
            # 1. 获取公钥
            async with session.get(f"https://api.github.com/repos/{GH_REPO}/actions/secrets/public-key") as resp:
                if resp.status != 200: return False
                key_data = await resp.json()
                public_key, key_id = key_data['key'], key_data['key_id']

            # 2. 加密逻辑
            pk = public.PublicKey(public_key.encode("utf-8"), encoding.Base64Encoder)
            sealed_box = public.SealedBox(pk)
            encrypted_value = b64encode(sealed_box.encrypt(new_value.encode("utf-8"))).decode("utf-8")

            # 3. 提交更新
            put_url = f"https://api.github.com/repos/{GH_REPO}/actions/secrets/LOGIN_ACCOUNTS"
            async with session.put(put_url, json={"encrypted_value": encrypted_value, "key_id": key_id}) as resp:
                return resp.status in [201, 204]
    except Exception as e:
        print(f"❌ Secret 更新异常: {e}")
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
    """核心逻辑：状态侦测（保守派）+ 自动救活"""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
        "Cookie": cookie_str,
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "X-Requested-With": "XMLHttpRequest"
    }
    
    new_cookie_val = None
    async with aiohttp.ClientSession(headers=headers) as session:
        try:
            # 1. 登录校验 & Cookie 提取
            async with session.get(f"{BASE_URL}/client/dashboard") as resp:
                for c_name, c_obj in resp.cookies.items():
                    if c_name == "connect.sid":
                        captured = f"connect.sid={c_obj.value}"
                        if captured != cookie_str and "s%3A" in captured:
                            new_cookie_val = captured
                
                html = await resp.text()
                if "logout" not in html:
                    return {"email": email, "success": False, "reason": "Cookie 已失效", "new_cookie": None}
                
                server_ids = list(set(re.findall(r'/servers/([a-f0-9]{8})', html)))

            details = []
            for sid in server_ids:
                # 2. 状态检测：增加白名单关键词，防止误判
                is_online = True # 默认在线（安全策略）
                
                try:
                    async with session.get(f"{BASE_URL}/client/servers/{sid}/status", timeout=10) as s_resp:
                        if s_resp.status == 200:
                            s_raw = str(await s_resp.text()).lower()
                            print(f"DEBUG [{sid}] API返回: {s_raw}")
                            # 只要包含以下任意词汇，即判定为在线
                            is_online = any(x in s_raw for x in ['run', 'on', 'start', 'process', 'active', '1', 'true'])
                        else:
                            # API不通时，尝试HTML特征搜索
                            async with session.get(f"{BASE_URL}/client/servers/{sid}/console") as c_resp:
                                c_html = (await c_resp.text()).lower()
                                is_online = any(x in c_html for x in ['text-success', 'bg-success', 'online', '运行中'])
                except Exception as e:
                    print(f"⚠️ [{sid}] 状态获取异常: {e}")
                    is_online = True 

                status_icon = "🟢 在线" if is_online else "🔴 离线"
                
                if not is_online:
                    # 3. 重启流程
                    async with session.get(f"{BASE_URL}/client/servers/{sid}/console") as c_resp:
                        csrf = re.search(r'name="csrf-token"\s+content="([^"]+)"', await c_resp.text())
                        csrf_token = csrf.group(1) if csrf else ""
                    
                    post_h = {**headers, "X-CSRF-TOKEN": csrf_token}
                    async with session.post(f"{BASE_URL}/client/api/server/restart", 
                                            json={"serverId": sid}, headers=post_h) as r_resp:
                        res_text = "🔄 已执行重启" if r_resp.status == 200 else "❌ 重启失败"
                        details.append(f"<code>{sid}</code>: {status_icon} -> {res_text}")
                else:
                    details.append(f"<code>{sid}</code>: {status_icon} (跳过重启)")
                
                await asyncio.sleep(1.5)

            return {"email": email, "success": True, "details": "\n".join(details), "new_cookie": new_cookie_val}
        except Exception as e:
            return {"email": email, "success": False, "reason": f"运行出错: {str(e)}", "new_cookie": None}

async def main():
    if not ACCOUNTS_STR:
        print("❌ 未发现 LOGIN_ACCOUNTS 配置")
        return

    # 解析账号，支持逗号分隔
    account_pairs = []
    for entry in ACCOUNTS_STR.split(","):
        if "----" in entry:
            parts = entry.split("----")
            account_pairs.append({"email": parts[0].strip(), "cookie": parts[1].strip()})
    
    results = await asyncio.gather(*[run_account(acc["email"], acc["cookie"]) for acc in account_pairs])
    
    report = [f"🖥 <b>Wispbyte 监控报告</b>\n{datetime.now().strftime('%m-%d %H:%M')}\n"]
    new_config_entries = []
    any_updated = False

    for i, res in enumerate(results):
        icon = "✅" if res["success"] else "⚠️"
        report.append(f"{icon} <b>{res['email']}</b>\n{res.get('details', res.get('reason'))}")
        
        # 严格格式拼接，防止 connect.sid= 重复
        old_cookie = account_pairs[i]["cookie"]
        new_cookie = res["new_cookie"]
        
        if res["success"] and new_cookie:
            final_cookie = new_cookie if "connect.sid=" in new_cookie else f"connect.sid={new_cookie}"
            any_updated = True
        else:
            final_cookie = old_cookie
            
        new_config_entries.append(f"{res['email']}----{final_cookie}")

    # 自动同步 Secret
    if any_updated:
        new_secret_content = ",".join(new_config_entries)
        success = await update_github_secret(new_secret_content)
        if success:
            report.append(f"\n🔄 <b>Cookie 已自动同步至 Secret</b>")
            print("✅ GitHub Secret 自动同步完成")
        else:
            report.append(f"\n⚠️ <b>Cookie 同步失败 (检查 PAT 权限)</b>")

    await tg_notify("\n".join(report))

if __name__ == "__main__":
    asyncio.run(main())
