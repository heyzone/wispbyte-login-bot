import os
import sys
import asyncio
import aiohttp
from datetime import datetime
from playwright.async_api import async_playwright

LOGIN_URL = "https://wispbyte.com/client/servers"

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
    success = [r for r in results if r["success"]]
    failed  = [r for r in results if not r["success"]]

    lines = [
        "Wispbyte 自动登录报告",
        f"目标: <a href='https://wispbyte.com/client'>控制面板</a>",
        f"时间: {start_time} → {end_time}",
        f"结果: <b>{len(success)} 成功</b> | <b>{len(failed)} 失败</b>",
        ""
    ]

    if success:
        lines.append("✅ 成功账号：")
        lines.extend([f"• <code>{r['email']}</code>" for r in success])
        lines.append("")

    if failed:
        lines.append("❌ 失败账号：")
        lines.extend([f"• <code>{r['email']}</code>" for r in failed])

    return "\n".join(lines)

async def login_one(email: str, password: str):
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=[
            "--no-sandbox", "--disable-setuid-sandbox",
            "--disable-dev-shm-usage", "--disable-gpu",
            "--disable-extensions", "--window-size=1920,1080",
            "--disable-blink-features=AutomationControlled"
        ])
        context = await browser.new_context(viewport={"width": 1920, "height": 1080},
                                            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36")
        page = await context.new_page()
        page.set_default_timeout(90000)

        result = {"email": email, "success": False}
        max_retries = 2

        for attempt in range(max_retries + 1):
            try:
                print(f"[{email}] 尝试 {attempt + 1}: 打开登录页...")
                await page.goto(LOGIN_URL, wait_until="load", timeout=90000)
                await page.wait_for_load_state("domcontentloaded", timeout=30000)
                await asyncio.sleep(5)

                if "client" in page.url and "login" not in page.url.lower():
                    print(f"[{email}] 已登录！")
                    result["success"] = True
                    break

                await page.wait_for_selector(
                    'input[placeholder*="Email"], input[placeholder*="Username"], input[type="email"], input[type="text"]',
                    timeout=20000
                )
                await page.fill('input[placeholder*="Email"], input[placeholder*="Username"], input[type="email"], input[type="text"]', email)
                await page.fill('input[placeholder*="Password"], input[type="password"]', password)

                try:
                    await page.wait_for_selector('text=确认您是真人, input[type="checkbox"]', timeout=10000)
                    await page.click('text=确认您是真人')
                    await asyncio.sleep(3)
                except:
                    pass

                await page.click('button:has-text("Log In")')
                await page.wait_for_url("**/client**", timeout=30000)
                result["success"] = True
                break

            except Exception as e:
                if attempt < max_retries:
                    await context.close()
                    context = await browser.new_context(viewport={"width": 1920, "height": 1080},
                                                        user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36")
                    page = await context.new_page()
                    await asyncio.sleep(2)
                else:
                    screenshot = f"error_{email.replace('@', '_')}_{int(datetime.now().timestamp())}.png"
                    await page.screenshot(path=screenshot, full_page=True)
                    await tg_notify_photo(
                        screenshot,
                        caption=f"Wispbyte 登录失败\n账号: <code>{email}</code>\n错误: <i>{str(e)[:200]}</i>\nURL: {page.url}"
                    )
        await context.close()
        await browser.close()
        return result

async def main():
    start_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    accounts_str = os.getenv("LOGIN_ACCOUNTS")
    if not accounts_str:
        await tg_notify("Failed: 未配置任何账号")
        return

    accounts = [a.strip() for a in accounts_str.split(",") if ":" in a]
    if not accounts:
        await tg_notify("Failed: LOGIN_ACCOUNTS 格式错误，应为 email:password")
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
