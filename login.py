import os
import sys
import asyncio
import aiohttp
from datetime import datetime
from playwright.async_api import async_playwright

LOGIN_URL = "https://wispbyte.com/client/servers"
DASHBOARD_URL_PATTERN = "**/client/dashboard**"

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
    online    = [r for r in results if r.get("server_status") == "already_online"]
    restarted = [r for r in results if r.get("server_status") == "restarted"]
    failed    = [r for r in results if not r["success"]]

    lines = [
        "🖥 Wispbyte 服务器状态报告",
        f"目标: <a href='https://wispbyte.com/client'>控制面板</a>",
        f"时间: {start_time} → {end_time}",
        ""
    ]
    if online:
        lines.append("✅ 服务器在线（无需操作）：")
        lines.extend([f"• <code>{r['email']}</code>" for r in online])
        lines.append("")
    if restarted:
        lines.append("🔄 已离线，已执行启动：")
        lines.extend([f"• <code>{r['email']}</code>" for r in restarted])
        lines.append("")
    if failed:
        lines.append("❌ 失败账号：")
        lines.extend([f"• <code>{r['email']}</code>  原因: {r.get('reason','未知')}" for r in failed])

    return "\n".join(lines)

async def login_one(email: str, password: str):
    async with async_playwright() as p:
        # 注意：如果 headless=True 依然被 CF 严格拦截，建议尝试在 Linux 服务器上使用 xvfb 配合 headless=False 运行
        browser = await p.chromium.launch(headless=True, args=[
            "--no-sandbox", "--disable-setuid-sandbox",
            "--disable-dev-shm-usage", "--disable-gpu",
            "--disable-extensions", "--window-size=1920,1080",
            "--disable-blink-features=AutomationControlled"
        ])
        context = await browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        
        # 注入防检测脚本
        await context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        
        page = await context.new_page()
        page.set_default_timeout(90000)

        result = {"email": email, "success": False, "server_status": None, "reason": ""}
        max_retries = 2

        for attempt in range(max_retries + 1):
            try:
                print(f"[{email}] 尝试 {attempt + 1}: 打开登录页...")
                await page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=90000)
                await asyncio.sleep(3)

                # 检查是否因为某种原因已经登录并在 dashboard
                if "client/dashboard" in page.url:
                    manage_el = await page.query_selector('text="MANAGE SERVER"')
                    if manage_el:
                        print(f"[{email}] 确认已在 Dashboard，无需重复登录")
                        result["success"] = True
                        break

                # ── 填写表单 ──
                print(f"[{email}] 开始填写账号密码...")
                email_input = await page.wait_for_selector('input[placeholder*="Email"], input[placeholder*="Username"], input[type="email"]', timeout=20000)
                await email_input.fill(email)
                
                pwd_input = await page.wait_for_selector('input[placeholder*="Password"], input[type="password"]', timeout=10000)
                await pwd_input.fill(password)

                # ── 处理 Cloudflare Turnstile ──
                print(f"[{email}] 等待 Cloudflare Turnstile 渲染...")
                try:
                    # 等待代表 CF iframe 的元素出现
                    cf_iframe_locator = page.locator('iframe[src*="challenges.cloudflare.com"]')
                    await cf_iframe_locator.first.wait_for(state="visible", timeout=15000)
                    
                    # 暂停几秒让 CF 完全加载完毕
                    await asyncio.sleep(4)
                    
                    # 关键点：不要进入 iframe 内部，直接点击 iframe 容器所在的位置
                    print(f"[{email}] 尝试点击 Turnstile 验证框...")
                    await cf_iframe_locator.first.click(position={"x": 30, "y": 30}) # 点击左上角偏移位置，模拟真实点击
                except Exception as e:
                    print(f"[{email}] Turnstile 验证框未找到或无法点击，可能已自动通过或页面结构不同: {str(e)[:100]}")

                # 轮询等待 CF 验证成功（标志是 cf-turnstile-response 的 value 有值）
                print(f"[{email}] 等待获取 Turnstile token...")
                turnstile_passed = False
                for _ in range(20): # 最多等 20 秒
                    token_val = await page.evaluate(
                        '''() => {
                            const t = document.querySelector('input[name="cf-turnstile-response"]');
                            return t ? t.value : "";
                        }'''
                    )
                    if token_val and len(token_val) > 10:
                        print(f"[{email}] ✅ Turnstile 验证成功 (Token 长度: {len(token_val)})")
                        turnstile_passed = True
                        break
                    await asyncio.sleep(1)

                if not turnstile_passed:
                    print(f"[{email}] ⚠️ 未检测到 Turnstile token，尝试强制点击登录按钮看是否能过...")

                # ── 点击登录 ──
                login_btn = await page.wait_for_selector('button:has-text("Log In")', timeout=10000)
                await login_btn.click()
                print(f"[{email}] 已点击登录，等待跳转至 Dashboard...")

                # ── 等待跳转到 Dashboard ──
                await page.wait_for_url(DASHBOARD_URL_PATTERN, timeout=40000)
                await page.wait_for_load_state("domcontentloaded", timeout=20000)
                await asyncio.sleep(3)

                # ── 寻找 MANAGE SERVER 按钮 ──
                manage_check = await page.wait_for_selector('text="MANAGE SERVER"', timeout=20000)
                if manage_check:
                    print(f"[{email}] ✅ 登录成功，到达 Dashboard 并在服务器列表找到 MANAGE SERVER！URL: {page.url}")
                    result["success"] = True
                    
                    # 点击 MANAGE SERVER 进入详情页
                    await manage_check.click()
                    await page.wait_for_load_state("domcontentloaded", timeout=30000)
                    await asyncio.sleep(3)
                    
                    # ── 读取服务器状态 ──
                    status_el = await page.wait_for_selector('#online-status-text', timeout=20000)
                    status_text = (await status_el.inner_text()).strip()
                    print(f"[{email}] 服务器状态: [{status_text}]")

                    if status_text.lower() == "online":
                        print(f"[{email}] ✅ 服务器在线，无需操作")
                        result["server_status"] = "already_online"
                        break
                    else:
                        # ── 离线则点击 Start ──
                        print(f"[{email}] 服务器离线，执行启动...")
                        start_btn = await page.wait_for_selector('#start-btn', timeout=10000)
                        await start_btn.click()
                        print(f"[{email}] 已点击 Start，等待启动（最多60秒）...")

                        try:
                            await page.wait_for_function(
                                'document.getElementById("online-status-text")?.textContent?.trim() === "Online"',
                                timeout=60000
                            )
                            print(f"[{email}] ✅ 服务器启动成功！")
                            result["server_status"] = "restarted"
                        except:
                            screenshot = f"warn_{email.replace('@','_')}_{int(datetime.now().timestamp())}.png"
                            await page.screenshot(path=screenshot, full_page=True)
                            await tg_notify_photo(
                                screenshot,
                                caption=f"⚠️ 启动超时\n账号: <code>{email}</code>\n已点击 Start 但60秒内未变为 Online"
                            )
                            result["server_status"] = "restarted"
                        break
                else:
                    raise Exception(f"跳转到 Dashboard 后未找到 MANAGE SERVER 按钮，URL: {page.url}")

            except Exception as e:
                print(f"[{email}] 第 {attempt + 1} 次失败: {e}")
                result["reason"] = str(e)[:200]
                if attempt < max_retries:
                    await context.close()
                    # 尝试更换 UA 避开检测
                    context = await browser.new_context(
                        viewport={"width": 1920, "height": 1080},
                        user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                    )
                    await context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
                    page = await context.new_page()
                    page.set_default_timeout(90000)
                    await asyncio.sleep(2)
                else:
                    screenshot = f"error_{email.replace('@','_')}_{int(datetime.now().timestamp())}.png"
                    try:
                        await page.screenshot(path=screenshot, full_page=True)
                        await tg_notify_photo(
                            screenshot,
                            caption=f"❌ 操作失败\n账号: <code>{email}</code>\n错误: <i>{str(e)[:200]}</i>\nURL: {page.url}"
                        )
                    except:
                        pass

        await context.close()
        await browser.close()
        return result

async def main():
    start_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    accounts_str = os.getenv("LOGIN_ACCOUNTS")
    if not accounts_str:
        await tg_notify("❌ Failed: 未配置任何账号")
        return

    accounts = [a.strip() for a in accounts_str.split(",") if ":" in a]
    if not accounts:
        await tg_notify("❌ Failed: LOGIN_ACCOUNTS 格式错误，应为 email:password")
        return

    tasks = [login_one(email, pwd) for email, pwd in (acc.split(":", 1) for acc in accounts)]
    results = await asyncio.gather(*tasks)

    end_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    final_msg = build_report(results, start_time, end_time)
    await tg_notify(final_msg)
    print(final_msg)

if __name__ == "__main__":
    accounts = os.getenv('LOGIN_ACCOUNTS', '').strip()
    count = len([a for a in accounts.split(',') if ':' in a]) if accounts else 0
    print(f"[{datetime.now()}] login.py 开始运行", file=sys.stderr)
    print(f"Python: {sys.version.split()[0]}, 有效账号数: {count}", file=sys.stderr)
    asyncio.run(main())
