import os
import sys
import asyncio
import aiohttp
from datetime import datetime
from playwright.async_api import async_playwright

BASE_URL = "https://wispbyte.com"
LOGIN_URL = f"{BASE_URL}/auth/login"

async def tg_notify(message: str):
    token = os.getenv("TG_BOT_TOKEN")
    chat_id = os.getenv("TG_CHAT_ID")
    if not token or not chat_id:
        print("Warning: 未设置 TG_BOT_TOKEN / TG_CHAT_ID，跳过通知")
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    async with aiohttp.ClientSession() as session:
        try:
            await session.post(url, data={
                "chat_id": chat_id,
                "text": message,
                "parse_mode": "HTML",
                "disable_web_page_preview": True
            })
        except Exception as e:
            print(f"Warning: Telegram 消息发送失败: {e}")

async def tg_notify_photo(photo_path: str, caption: str = ""):
    token = os.getenv("TG_BOT_TOKEN")
    chat_id = os.getenv("TG_CHAT_ID")
    if not token or not chat_id:
        return
    url = f"https://api.telegram.org/bot{token}/sendPhoto"
    async with aiohttp.ClientSession() as session:
        try:
            with open(photo_path, "rb") as f:
                data = aiohttp.FormData()
                data.add_field("chat_id", chat_id)
                data.add_field("photo", f, filename=os.path.basename(photo_path))
                if caption:
                    data.add_field("caption", caption)
                    data.add_field("parse_mode", "HTML")
                await session.post(url, data=data)
        except Exception as e:
            print(f"Warning: Telegram 图片发送失败: {e}")
        finally:
            try:
                os.remove(photo_path)
            except:
                pass

def build_report(results, start_time, end_time):
    online    = [r for r in results if r.get("status") == "already_online"]
    restarted = [r for r in results if r.get("status") == "restarted"]
    failed    = [r for r in results if r.get("status") == "failed"]

    lines = [
        "🖥 Wispbyte 服务器状态报告",
        f"时间: {start_time} → {end_time}",
        f"账号总数: {len(results)}",
        ""
    ]
    if online:
        lines.append("✅ 原本在线（无需操作）：")
        lines.extend([f"• <code>{r['email']}</code>  服务器 <code>{r['server_id']}</code>" for r in online])
        lines.append("")
    if restarted:
        lines.append("🔄 已离线，已执行启动：")
        lines.extend([f"• <code>{r['email']}</code>  服务器 <code>{r['server_id']}</code>" for r in restarted])
        lines.append("")
    if failed:
        lines.append("❌ 失败：")
        lines.extend([f"• <code>{r['email']}</code>  服务器 <code>{r['server_id']}</code>  原因: {r.get('reason', '未知')}" for r in failed])

    return "\n".join(lines)

async def check_and_restart(email: str, password: str, server_id: str):
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=[
            "--no-sandbox", "--disable-setuid-sandbox",
            "--disable-dev-shm-usage", "--disable-gpu",
            "--disable-extensions", "--window-size=1920,1080",
            "--disable-blink-features=AutomationControlled"
        ])
        context = await browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
        )
        page = await context.new_page()
        page.set_default_timeout(90000)

        result = {"email": email, "server_id": server_id, "status": "failed", "reason": ""}
        max_retries = 2

        for attempt in range(max_retries + 1):
            try:
                # ── 1. 打开登录页 ──
                print(f"[{email}] 尝试 {attempt + 1}: 打开登录页...")
                await page.goto(LOGIN_URL, wait_until="load", timeout=90000)
                await page.wait_for_load_state("domcontentloaded", timeout=30000)
                await asyncio.sleep(3)

                # ── 2. 确认登录表单存在 ──
                email_input = await page.query_selector(
                    'input[placeholder*="Email"], input[placeholder*="Username"], input[type="email"]'
                )
                if not email_input:
                    raise Exception("找不到登录表单，页面可能未正常加载")

                await email_input.fill(email)
                await page.fill('input[placeholder*="Password"], input[type="password"]', password)
                print(f"[{email}] 已填写账号密码，等待 Turnstile 验证...")

                # ── 3. 等待 Cloudflare Turnstile 自动完成 ──
                try:
                    await page.wait_for_function(
                        '''() => {
                            const token = document.querySelector('input[name="cf-turnstile-response"]');
                            return token && token.value && token.value.length > 0;
                        }''',
                        timeout=30000
                    )
                    print(f"[{email}] Turnstile 验证通过")
                except:
                    print(f"[{email}] Turnstile 等待超时，尝试直接点击登录...")

                # ── 4. 点击登录按钮 ──
                await page.click('button:has-text("Log In")')
                print(f"[{email}] 已点击登录，等待跳转...")

                # ── 5. 等待跳转到 client 页面 ──
                await page.wait_for_url("**/client**", timeout=30000)
                await page.wait_for_load_state("domcontentloaded", timeout=20000)
                await asyncio.sleep(3)
                print(f"[{email}] 登录成功！当前页面: {page.url}")

                # ── 6. 等待服务器列表加载，点击 MANAGE SERVER ──
                print(f"[{email}] 等待服务器列表加载...")
                await page.wait_for_load_state("networkidle", timeout=20000)
                await asyncio.sleep(2)

                # 优先：找含 server_id 标签附近的 MANAGE SERVER 按钮（多服务器安全）
                manage_btn = None
                try:
                    # 找显示 #server_id 的元素，向上找祖先容器，再找按钮
                    manage_btn = await page.query_selector(
                        f'text=#{server_id}'
                    )
                    if manage_btn:
                        # 在同一个卡片容器内找 MANAGE SERVER 按钮
                        card = await manage_btn.evaluate_handle(
                            'el => el.closest("div[class*=\'server\'], div[class*=\'card\'], section, article, li") || el.parentElement.parentElement.parentElement'
                        )
                        manage_btn = await card.query_selector('text=MANAGE SERVER')
                except:
                    manage_btn = None

                # 降级：直接找页面上的 MANAGE SERVER（单服务器账号足够）
                if not manage_btn:
                    manage_btn = await page.query_selector('text=MANAGE SERVER')

                if not manage_btn:
                    raise Exception("找不到 MANAGE SERVER 按钮，服务器列表可能未加载")

                await manage_btn.click()
                await page.wait_for_load_state("domcontentloaded", timeout=30000)
                await asyncio.sleep(3)
                print(f"[{email}] 已进入 Console 页，当前: {page.url}")

                # ── 7. 读取服务器状态 ──
                print(f"[{email}] 等待状态元素加载...")
                status_el = await page.wait_for_selector('#online-status-text', timeout=20000)
                status_text = (await status_el.inner_text()).strip()
                print(f"[{email}] 服务器 {server_id} 当前状态: {status_text}")

                if status_text.lower() == "online":
                    print(f"[{email}] 服务器在线，无需操作")
                    result["status"] = "already_online"
                    break

                # ── 8. 服务器离线，点击 Start ──
                print(f"[{email}] 服务器状态为 [{status_text}]，执行启动...")
                start_btn = await page.wait_for_selector('#start-btn', timeout=10000)
                await start_btn.click()
                print(f"[{email}] 已点击 Start，等待服务器启动（最多60秒）...")

                # ── 9. 等待状态变为 Online ──
                try:
                    await page.wait_for_function(
                        'document.getElementById("online-status-text")?.textContent?.trim() === "Online"',
                        timeout=60000
                    )
                    print(f"[{email}] 服务器已成功启动！")
                    result["status"] = "restarted"
                except:
                    screenshot = f"warn_{email.replace('@', '_')}_{int(datetime.now().timestamp())}.png"
                    await page.screenshot(path=screenshot, full_page=True)
                    await tg_notify_photo(
                        screenshot,
                        caption=f"⚠️ Wispbyte 启动超时\n账号: <code>{email}</code>\n服务器: <code>{server_id}</code>\n已点击 Start 但60秒内未变为 Online"
                    )
                    result["status"] = "restarted"
                    result["reason"] = "启动超时，已点击 Start，结果待观察"
                break

            except Exception as e:
                print(f"[{email}] 第 {attempt + 1} 次失败: {e}")
                result["reason"] = str(e)[:200]
                if attempt < max_retries:
                    await context.close()
                    context = await browser.new_context(
                        viewport={"width": 1920, "height": 1080},
                        user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
                    )
                    page = await context.new_page()
                    page.set_default_timeout(90000)
                    await asyncio.sleep(3)
                else:
                    screenshot = f"error_{email.replace('@', '_')}_{int(datetime.now().timestamp())}.png"
                    await page.screenshot(path=screenshot, full_page=True)
                    await tg_notify_photo(
                        screenshot,
                        caption=f"❌ Wispbyte 操作失败\n账号: <code>{email}</code>\n服务器: <code>{server_id}</code>\n错误: <i>{str(e)[:200]}</i>"
                    )

        await context.close()
        await browser.close()
        return result

async def main():
    start_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    accounts_str = os.getenv("LOGIN_ACCOUNTS")
    if not accounts_str:
        await tg_notify("❌ Failed: 未配置任何账号")
        return

    # 格式：email:password:server_id,email2:password2:server_id2
    accounts = []
    for a in accounts_str.split(","):
        a = a.strip()
        parts = a.split(":", 2)
        if len(parts) == 3:
            accounts.append(tuple(parts))
        else:
            print(f"Warning: 跳过格式错误配置: {a}（应为 email:password:server_id）")

    if not accounts:
        await tg_notify("❌ Failed: LOGIN_ACCOUNTS 格式错误，应为 email:password:server_id")
        return

    tasks = [check_and_restart(email, pwd, sid) for email, pwd, sid in accounts]
    results = await asyncio.gather(*tasks)

    end_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    final_msg = build_report(list(results), start_time, end_time)
    await tg_notify(final_msg)
    print(final_msg)

if __name__ == "__main__":
    accounts = os.getenv('LOGIN_ACCOUNTS', '').strip()
    count = len([a for a in accounts.split(',') if a.count(':') >= 2]) if accounts else 0
    print(f"[{datetime.now()}] wispbyte.py 开始运行", file=sys.stderr)
    print(f"Python: {sys.version.split()[0]}, 有效账号数: {count}", file=sys.stderr)
    asyncio.run(main())
