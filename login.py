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
    if not GH_PAT or not GH_REPO:
        print("❌ 缺少 GH_PAT 或 GITHUB_REPOSITORY 环境变量")
        return False
    headers = {
        "Authorization": f"token {GH_PAT}",
        "Accept": "application/vnd.github.v3+json"
    }
    try:
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.get(f"https://api.github.com/repos/{GH_REPO}/actions/secrets/public-key") as resp:
                if resp.status != 200:
                    print(f"❌ 获取公钥失败: {resp.status}")
                    return False
                key_data = await resp.json()
                public_key, key_id = key_data['key'], key_data['key_id']

            pk = public.PublicKey(public_key.encode("utf-8"), encoding.Base64Encoder)
            sealed_box = public.SealedBox(pk)
            encrypted_value = b64encode(sealed_box.encrypt(new_value.encode("utf-8"))).decode("utf-8")

            put_url = f"https://api.github.com/repos/{GH_REPO}/actions/secrets/LOGIN_ACCOUNTS"
            async with session.put(put_url, json={"encrypted_value": encrypted_value, "key_id": key_id}) as resp:
                success = resp.status in [201, 204]
                print(f"{'✅' if success else '❌'} GitHub Secret 更新: HTTP {resp.status}")
                return success
    except Exception as e:
        print(f"❌ Secret 更新异常: {e}")
        return False

async def tg_notify(message: str):
    token = os.getenv("TG_BOT_TOKEN")
    chat_id = os.getenv("TG_CHAT_ID")
    if not token or not chat_id:
        print("⚠️ TG_BOT_TOKEN 或 TG_CHAT_ID 未设置，跳过通知")
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        async with aiohttp.ClientSession() as session:
            resp = await session.post(url, json={
                "chat_id": chat_id,
                "text": message,
                "parse_mode": "HTML",
                "disable_web_page_preview": True
            })
            result = await resp.json()
            if result.get("ok"):
                print("✅ TG 通知发送成功")
            else:
                print(f"❌ TG 通知发送失败: {result}")
    except Exception as e:
        print(f"❌ TG 通知异常: {e}")

async def run_account(email, cookie_str):
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
            print(f"[{email}] 检查登录状态...")
            async with session.get(f"{BASE_URL}/client/dashboard") as resp:
                print(f"[{email}] dashboard HTTP: {resp.status}, URL: {resp.url}")
                for c_name, c_obj in resp.cookies.items():
                    if c_name == "connect.sid":
                        captured = f"connect.sid={c_obj.value}"
                        if captured != cookie_str and "s%3A" in captured:
                            new_cookie_val = captured
                            print(f"[{email}] 检测到新 Cookie")

                html = await resp.text()
                print(f"[{email}] 页面含 'logout': {'logout' in html}, 页面长度: {len(html)}")

                if "logout" not in html:
                    print(f"[{email}] Cookie 已失效")
                    return {"email": email, "success": False, "reason": "Cookie 已失效，请重新获取", "new_cookie": None}

                server_ids = list(set(re.findall(r'/servers/([a-f0-9]{8})', html)))
                print(f"[{email}] 找到服务器: {server_ids}")

                if not server_ids:
                    return {"email": email, "success": False, "reason": "未找到任何服务器ID", "new_cookie": None}

            details = []
            for sid in server_ids:
                print(f"[{email}] 检查服务器 {sid} 状态...")
                is_online = True

                try:
                    async with session.get(f"{BASE_URL}/client/servers/{sid}/status", timeout=aiohttp.ClientTimeout(total=10)) as s_resp:
                        print(f"[{email}] [{sid}] status HTTP: {s_resp.status}")
                        if s_resp.status == 200:
                            s_raw = str(await s_resp.text()).lower()
                            print(f"[{email}] [{sid}] API返回: {s_raw[:200]}")
                            is_online = any(x in s_raw for x in ['run', 'on', 'start', 'process', 'active', '1', 'true'])
                        else:
                            async with session.get(f"{BASE_URL}/client/servers/{sid}/console") as c_resp:
                                c_html = (await c_resp.text()).lower()
                                is_online = any(x in c_html for x in ['text-success', 'bg-success', 'online', '运行中'])
                                print(f"[{email}] [{sid}] console 页面判断在线: {is_online}")
                except Exception as e:
                    print(f"[{email}] [{sid}] 状态获取异常: {e}")
                    is_online = True

                status_icon = "🟢 在线" if is_online else "🔴 离线"
                print(f"[{email}] [{sid}] 最终状态: {status_icon}")

                if not is_online:
                    print(f"[{email}] [{sid}] 执行重启...")
                    async with session.get(f"{BASE_URL}/client/servers/{sid}/console") as c_resp:
                        csrf = re.search(r'name="csrf-token"\s+content="([^"]+)"', await c_resp.text())
                        csrf_token = csrf.group(1) if csrf else ""
                        print(f"[{email}] [{sid}] CSRF token: {'已获取' if csrf_token else '未获取'}")

                    post_h = {**headers, "X-CSRF-TOKEN": csrf_token}
                    async with session.post(
                        f"{BASE_URL}/client/api/server/restart",
                        json={"serverId": sid},
                        headers=post_h
                    ) as r_resp:
                        print(f"[{email}] [{sid}] 重启 HTTP: {r_resp.status}")
                        res_text = "🔄 已执行重启" if r_resp.status == 200 else f"❌ 重启失败(HTTP {r_resp.status})"
                        details.append(f"<code>{sid}</code>: {status_icon} → {res_text}")
                else:
                    details.append(f"<code>{sid}</code>: {status_icon}")

                await asyncio.sleep(1.5)

            return {"email": email, "success": True, "details": "\n".join(details), "new_cookie": new_cookie_val}

        except Exception as e:
            print(f"[{email}] 运行异常: {e}")
            return {"email": email, "success": False, "reason": f"运行出错: {str(e)}", "new_cookie": None}

async def main():
    print(f"[{datetime.now()}] wispbyte_cookie.py 开始运行")

    if not ACCOUNTS_STR:
        msg = "❌ Wispbyte: 未发现 LOGIN_ACCOUNTS 配置"
        print(msg)
        await tg_notify(msg)
        return

    # 解析账号
    account_pairs = []
    for entry in ACCOUNTS_STR.split(","):
        entry = entry.strip()
        if "----" in entry:
            parts = entry.split("----", 1)
            account_pairs.append({"email": parts[0].strip(), "cookie": parts[1].strip()})

    print(f"解析到账号数: {len(account_pairs)}")

    if not account_pairs:
        msg = f"❌ Wispbyte: LOGIN_ACCOUNTS 格式错误，未解析到账号\n格式应为: email----connect.sid=xxx"
        print(msg)
        await tg_notify(msg)
        return

    results = await asyncio.gather(*[run_account(acc["email"], acc["cookie"]) for acc in account_pairs])

    report = [f"🖥 <b>Wispbyte 监控报告</b>\n{datetime.now().strftime('%m-%d %H:%M')}\n"]
    new_config_entries = []
    any_updated = False

    for i, res in enumerate(results):
        icon = "✅" if res["success"] else "⚠️"
        report.append(f"{icon} <b>{res['email']}</b>\n{res.get('details', res.get('reason', '无详情'))}")

        old_cookie = account_pairs[i]["cookie"]
        new_cookie = res.get("new_cookie")

        if res["success"] and new_cookie:
            final_cookie = new_cookie if "connect.sid=" in new_cookie else f"connect.sid={new_cookie}"
            any_updated = True
            print(f"[{res['email']}] Cookie 有更新")
        else:
            final_cookie = old_cookie

        new_config_entries.append(f"{res['email']}----{final_cookie}")

    if any_updated:
        new_secret_content = ",".join(new_config_entries)
        success = await update_github_secret(new_secret_content)
        if success:
            report.append("\n🔄 <b>Cookie 已自动同步至 Secret</b>")
        else:
            report.append("\n⚠️ <b>Cookie 同步失败（检查 PAT 权限）</b>")

    final_msg = "\n".join(report)
    print(f"最终报告:\n{final_msg}")
    await tg_notify(final_msg)

if __name__ == "__main__":
    asyncio.run(main())
