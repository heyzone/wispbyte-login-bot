import os
import sys
import asyncio
import aiohttp
from datetime import datetime
from playwright.async_api import async_playwright

# 配置常量
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
                if os.path.exists(photo_path):
                    os.remove(photo_path)
            except:
                pass

def build_report(results, start_time, end_time):
    online    = [r for r in results if r.get("server_status") == "already_online"]
    restarted = [r for r in results if r.get("server_status") == "restarted"]
    failed    = [r for r in results if not r["success"]]

    lines = ["🖥 <b>Wispbyte 状态报告</b>", f"时间: {start_time} → {end_time}", ""]
    if online:
        lines.append("✅ <b>在线中：</b>")
        lines.extend([f"• <code>{r['email']}</code>" for r in online])
        lines.append("")
    if restarted:
        lines.append("🔄 <b>已自动启动：</b>")
        lines.extend([f"• <code>{r['email']}</code>" for r in restarted])
        lines.append("")
    if failed:
        lines.append("❌ <b>操作失败：</b>")
        lines.extend([f"• <code>{r['email']}</code>: {r.get('reason','未知错误')}" for r in failed])
    return "\n".join(lines)

async def login_one(email: str, password: str):
    async with async_playwright() as p:
        # ── 启动配置：使用真实 Chrome 渠道并关闭无头模式 ──
        browser = await p.chromium.launch(
            headless=False, # 配合 xvfb-run 使用
            channel="chrome", 
            args=[
                "--no-sandbox",
                "--disable-blink-features=AutomationControlled", # 核心：隐藏自动化特征
                "--disable-infobars",
                "--window-size=1920,1080"
            ]
        )
        context = await browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        )
        
        # 进一步伪装 navigator.webdriver
        await context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        
        page = await context.new_page()
        page.set_default_timeout(60000)
        result = {"email": email, "success": False, "server_status": None, "reason": ""}

        try:
            print(f"[{email}] 正在访问登录页...")
            await page.goto(LOGIN_URL, wait_until="domcontentloaded")
            await asyncio.sleep(5)

            # 检查是否已在 Dashboard
            if "dashboard" in page.url:
                print(f"[{email}] 已处于登录状态")
            else:
                # ── 填写表单 ──
                await page.fill('input[type="email"], input[name="email"]', email)
                await page.fill('input[type="password"], input[name="password"]', password)
                print(f"[{email}] 已填写账号密码")

                # ── 处理 Cloudflare Turnstile ──
                try:
                    print(f"[{email}] 正在定位 Turnstile 验证框...")
                    cf_frame = page.locator('iframe[src*="challenges.cloudflare.com"]').first
                    await cf_frame.wait_for(state="visible", timeout=15000)
                    
                    # 模拟人类思维：停顿并点击验证框中心
                    await asyncio.sleep(3)
                    box = await cf_frame.bounding_box()
                    if box:
                        await page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
                        print(f"[{email}] 已模拟点击 Turnstile 中心点")
                    else:
                        await cf_frame.click()
                except Exception as cf_e:
                    print(f"[{email}] 未发现验证框或已自动通过: {cf_e}")

                # 等待 Token 生成
                for _ in range(20):
                    token = await page.evaluate('document.querySelector(\'[name="cf-turnstile-response"]\')?.value')
                    if token and len(token) > 10:
                        print(f"[{email}] ✅ Turnstile 验证通过")
                        break
                    await asyncio.sleep(1)

                # ── 点击登录 ──
                await page.click('button[type="submit"], button:has-text("Log In")')
                print(f"[{email}] 已提交登录，等待 Dashboard...")

            # ── 登录成功后的逻辑 ──
            await page.wait_for_url(DASHBOARD_URL_PATTERN, timeout=45000)
            await asyncio.sleep(3)
            
            # 在 Dashboard 查找管理按钮
            manage_btn = await page.wait_for_selector('text="MANAGE SERVER"', timeout=15000)
            await manage_btn.click()
            print(f"[{email}] 已进入服务器控制台")
            
            # ── 检查并操作服务器 ──
            await page.wait_for_load_state("networkidle")
            status_el = await page.wait_for_selector('#online-status-text', timeout=20000)
            status_text = (await status_el.inner_text()).strip().lower()
            
            if status_text == "online":
                print(f"[{email}] ✅ 服务器在线")
                result["server_status"] = "already_online"
            else:
                print(f"[{email}] 服务器离线，尝试启动...")
                start_btn = await page.wait_for_selector('#start-btn')
                await start_btn.click()
                # 等待状态变为 Online
                try:
                    await page.wait_for_function(
                        'document.getElementById("online-status-text")?.textContent?.trim().toLowerCase() === "online"',
                        timeout=60000
                    )
                    result["server_status"] = "restarted"
                except:
                    result["server_status"] = "restarted" # 即使超时也标记为已点启动
            
            result["success"] = True

        except Exception as e:
            print(f"[{email}] 发生错误: {e}")
            result["reason"] = str(e)[:100]
            # 失败截屏
            shot_path = f"error_{email.split('@')[0]}.png"
            await page.screenshot(path=shot_path)
            await tg_notify_photo(shot_path, f"❌ <b>{email}</b> 操作失败\n原因: {result['reason']}")

        await browser.close()
        return result

async def main():
    start_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    accounts_str = os.getenv("LOGIN_ACCOUNTS", "")
    if not accounts_str:
        return print("Error: LOGIN_ACCOUNTS 未设置")

    accounts = [a.strip() for a in accounts_str.split(",") if ":" in a]
    tasks = [login_one(acc.split(":")[0], acc.split(":")[1]) for acc in accounts]
    results = await asyncio.gather(*tasks)

    report = build_report(results, start_time, datetime.now().strftime("%H:%M:%S"))
    await tg_notify(report)
    print("任务结束。")

if __name__ == "__main__":
    asyncio.run(main())
