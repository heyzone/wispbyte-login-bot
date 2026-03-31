# Wispbyte 自动登录机器人

每 **3 天**自动登录 wispbyte 服务器，防止掉线。

LOGIN_ACCOUNTS的格式为：邮箱----connect id=....(cookie)

增加git token，自动回传cookie，改为用cookie登录更加稳健！

## 配置步骤

1. **创建 Telegram Bot**
   - 找 [@BotFather](https://t.me/BotFather) 创建 Bot
   - 获取 `TG_BOT_TOKEN`
   - 发消息给 Bot，获取 `TG_CHAT_ID`（可用 [@userinfobot](https://t.me/userinfobot)）

2. **GitHub Secrets 设置**
   - 进入仓库 → Settings → Secrets and variables → Actions
   - 添加以下 Secrets：

   | Name | Value |
   |------|-------|
   | `TG_BOT_TOKEN` | 你的 Bot Token |
   | `TG_CHAT_ID` | 你的 Chat ID |
   | `LOGIN_ACCOUNTS` | `email1:pass1,email2:pass2` |

3. **启用 Actions**
   - 第一次运行会自动安装 Chromium 并执行

## 定时规则

```yaml
cron: '0 20 */2 * *'  # 每两天 UTC 20:00（北京 4:00）
