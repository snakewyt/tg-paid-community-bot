# Telegram 付费社群机器人 — 配置教程

从零开始搭建你的 Telegram 付费社群机器人，包教会。教程假设你有一台 VPS（Ubuntu/Debian），已安装 Docker 和 Docker Compose。

---

## 目录

1. [准备工作](#1-准备工作)
2. [部署服务](#2-部署服务)
3. [最小配置跑通（Telegram Stars）](#3-最小配置跑通)
4. [创建你的第一个套餐](#4-创建你的第一个套餐)
5. [接入更多支付方式](#5-接入更多支付方式)
6. [管理员后台面板](#6-管理员后台面板)
7. [日常运营](#7-日常运营)
8. [安全加固（强烈建议）](#8-安全加固)
9. [常见问题](#9-常见问题)

---

## 1. 准备工作

你需要准备三样东西：

### 1.1 创建 Telegram 机器人

1. 打开 Telegram，搜索 [@BotFather](https://t.me/BotFather)
2. 发送 `/newbot`，按提示给机器人取名字（英文）
3. 拿到 token，类似：

```
1234567890:AAFabcdefGHIJKlmnOPQRSTuvwxyz
```

**记下来，别泄露。**

### 1.2 创建付费群/频道

在 Telegram 里创建你要收费的群或频道。把刚才创建的机器人拉进去，设为管理员，**至少要勾选这两项权限**：

- 邀请用户（Invite users via link）
- 禁言/移除成员（Ban users）

### 1.3 获取你自己的 Telegram User ID

你作为管理员，需要你的数字 ID。找到 [@userinfobot](https://t.me/userinfobot)，发 `/start`，它会回复：

```
你的 ID: 123456789
```

**把这个数字记下来。**

### 1.4 获取群/频道 chat_id

找到 [@getidsbot](https://t.me/getidsbot)，把机器人拉进你的付费群/频道，发送 `/id@getidsbot`。它会回复类似：

```
Chat ID: -1001234567890
```

负号开头的是超级群或频道。**记下来。**

### 1.5 准备域名（强烈推荐）

如果你要接外部支付（虎皮椒、易支付、Stripe、CryptoBot）或者用管理面板，**强烈建议配一个域名**。没有域名的话：

- 外部支付回调必须走你的 IP + 端口（如 `http://1.2.3.4:8000/webhook/hupijiao`），明文 HTTP
- 管理面板用户名和密码在明文中传输
- 没法用免费的 Let's Encrypt SSL 证书

**域名不需要备案**（国内备案只针对 80/443 端口的网站，我们的 8000 端口不受影响）。哪里买都行：

| 平台 | 普通 .com 年费 | 说明 |
|------|--------------|------|
| Cloudflare | ~$10/年 | **推荐**，送 DNS + CDN + DDoS 防护 |
| Namecheap | ~$10/年 | 老牌注册商 |
| 阿里云/腾讯云 | ~¥60/年 | 国内方便 |
| Namesilo | ~$8/年 | 便宜稳定 |

**买好域名后只需要做一件事：添加一条 A 记录指向你的 VPS IP。**

以 Cloudflare 为例（其他平台类似）：

1. 进入 DNS 设置
2. 添加记录：类型 `A`，名称 `@`（或 `pay`），内容填你的 VPS 公网 IP
3. TTL 设 Auto，保存

等几分钟生效。验证：

```bash
ping your-domain.com
# 应该返回你的 VPS IP
```

**本节总结——你现在应该有：**

| 编号 | 东西 | 示例 | 用途 |
|------|------|------|------|
| ① | Bot Token | `1234567890:AA...` | 机器人身份 |
| ② | Admin User ID | `123456789` | 管理员权限 |
| ③ | Chat ID | `-1001234567890` | 收费群 |
| ④ | 域名 | `pay.yoursite.com` | HTTPS + 回调 |

---

## 2. 部署服务

### 2.1 服务器要求

| 项目 | 最低 | 推荐 | 说明 |
|------|------|------|------|
| **CPU** | 1 核 | 2 核 | 并发不高时 1 核够用 |
| **内存** | 512 MB | 1 GB | Docker 本身吃约 200MB，Python 进程 ~150MB |
| **硬盘** | 10 GB | 20 GB | 代码几十 KB，SQLite 数据库撑死几 MB |
| **带宽** | 1 Mbps | 不限 | 只做 API 回调，流量极小 |
| **系统** | Ubuntu 20.04+ / Debian 11+ 64 位 | Ubuntu 22.04 LTS | 其他 Linux 也行，教程以 Ubuntu 为准 |
| **IP** | 公网 IPv4 | 公网 IPv4 | 外部支付回调需要公网可达 |
| **域名** | 可选 | 强烈推荐 | HTTP 下账号密码明文有风险，HTTPS 需要域名 |

**一句话：最便宜的云服务器就够。**

这是一个轻量级 Python 异步程序——单进程同时跑 Telegram 长轮询和支付回调 Web 服务，内存占用不到 300MB。SQLite 没有额外数据库进程开销。

### 2.2 推荐云厂商

| 厂商 | 最便宜方案 | 月费 | 适合 |
|------|-----------|------|------|
| 阿里云 ECS | 1 核 512MB | ~¥30 | 国内用户首选 |
| 腾讯云轻量 | 2 核 2GB | ~¥40 | 性价比高 |
| 雨云 / 狗云等 | 1 核 512MB | ~¥15-25 | 预算敏感 |
| Vultr / Hetzner | 1 核 1GB | $5-6 | 海外用户或 Paypal 收款 |
| Oracle 免费机 | 4 核 24GB | $0 | 永久免费 ARM 实例，但要抢 |

**最低预算方案：雨云 1 核 512M Ubuntu 22.04，月费不到 20 块。**

### 2.3 系统选择

教程以 **Ubuntu 22.04 LTS（64 位）** 为标准——这是目前最主流的服务器系统，软件源齐全，社区支持好。Debian 11/12 也完全兼容。

其他系统：

| 系统 | 可用？ | 备注 |
|------|--------|------|
| Ubuntu 20.04 / 22.04 / 24.04 | 完全可用 | 推荐 |
| Debian 11 / 12 | 完全可用 | 更轻量 |
| CentOS 7 / Rocky Linux | 可用 | 需 yum 换 apt 命令 |
| macOS | 可作为开发机 | 不推荐部署 |
| Windows | 不推荐 | Docker Desktop 开销大，不如 WSL |

### 2.4 一条命令装好 Docker

VPS 到手后，SSH 连上去，一行搞定：

```bash
curl -fsSL https://get.docker.com | bash
```

装完验证：

```bash
docker --version
# Docker version 26.x.x ...

docker compose version
# Docker Compose version v2.x.x ...
```

> 如果 `docker compose` 报错说找不到，说明 Docker 版本太老。用 `docker-compose`（带横杠）代替，或者升级 Docker。

### 2.5 部署

把项目传到 VPS 上（scp、git clone、或在服务器上直接创建都行）：

```bash
# 在 VPS 上：
mkdir -p ~/projects
cd ~/projects

# 方式 1：用 git（如果能访问 GitHub）
git clone https://github.com/your/repo.git tg-paid-community-bot
cd tg-paid-community-bot

# 方式 2：用 scp 从本地上传
# （在本机执行）scp -r tg-paid-community-bot user@你的VPS_IP:~/projects/
```

然后配置启动：

```bash
cp .env.example .env
nano .env
```

只填这两行就够：

```env
BOT_TOKEN=1234567890:AAFabcdefGHIJKlmnOPQRSTuvwxyz
ADMIN_IDS=123456789
```

启动：

```bash
docker compose up -d
```

第一次启动会下载 Python 3.12 镜像（约 150MB）并安装依赖，等待约 1 分钟。

### 2.6 验证运行状态

```bash
# 看是否在运行
docker compose ps

# 应该显示：
# NAME                       STATUS
# tg-paid-community-bot      Up

# 看日志
docker compose logs -f
```

看到类似输出即正常：

```
[INFO] app.main: Database migrations applied
[INFO] app.main: Starting bot...
```

按 `Ctrl+C` 退出日志。

### 2.7 确认 8000 端口可访问

**如果你用外部支付（虎皮椒/易支付/Stripe/CryptoBot），VPS 的 8000 端口必须公网可达。**

先确认机器人已开始监听：

```bash
curl http://127.0.0.1:8000/admin
# 会跳转到 /admin/login 就说明 web 服务和面板都正常
```

再从你本机浏览器测试：

```
http://你的VPS_IP:8000/admin
```

如果不能访问，大概率是**安全组 / 防火墙没开 8000 端口**：

```bash
# Ubuntu 防火墙
sudo ufw allow 8000/tcp
sudo ufw reload

# 如果用云服务商（阿里云/腾讯云等），还要在网页控制台的"安全组"里放行 TCP 8000
```

> 纯 Telegram Stars 收款不走公网回调，8000 端口可以不对外开放。

### 2.8 完整 .env 示例

这是所有配置项都填好的样子，实际使用时只填你需要的：

```env
# ---------- 机器人 ----------
BOT_TOKEN=1234567890:AAFabcdefGHIJKlmnOPQRSTuvwxyz
ADMIN_IDS=123456789,987654321

# ---------- Telegram Stars（零配置）----------
STARS_ENABLED=true

# ---------- CryptoBot ----------
CRYPTO_ENABLED=true
CRYPTO_API_TOKEN=abc123def456

# ---------- Stripe ----------
STRIPE_ENABLED=true
STRIPE_SECRET_KEY=sk_live_xxxxx
STRIPE_WEBHOOK_SECRET=whsec_xxxxx
STRIPE_SUCCESS_URL=https://t.me/your_bot
STRIPE_CANCEL_URL=https://t.me/your_bot

# ---------- 支付宝 → 虎皮椒 ----------
ALIPAY_BACKEND=hupijiao
HUPIJIAO_ENABLED=true
HUPIJIAO_API_URL=https://api.xunhupay.com
HUPIJIAO_APPID=hs_xxxxxxxx
HUPIJIAO_APPSECRET=xxxxxxxxxxxxx
HUPIJIAO_NOTIFY_URL=https://your-domain.com:8000/webhook/hupijiao

# ---------- 微信支付 → 易支付 ----------
WECHAT_BACKEND=epay
EPAY_ENABLED=true
EPAY_API_URL=https://pay.example.com/
EPAY_PID=10001
EPAY_KEY=xxxxxxxxxxx
EPAY_NOTIFY_URL=https://your-domain.com:8000/webhook/epay

# ---------- Webhook 服务 ----------
WEBHOOK_HOST=0.0.0.0
WEBHOOK_PORT=8000

# ---------- 管理面板 ----------
# 内置默认账号 admin / 123456，首次登录强制修改密码
# 如需自定义初始账号，取消下面两行的注释并修改：
#ADMIN_PANEL_USERNAME=admin
#ADMIN_PANEL_PASSWORD=你的自定义密码

# ---------- 机器人行为（可选，均可在管理面板里改）----------
# 允许使用机器人管理命令的 TG 用户名（不含 @，逗号分隔；与 ADMIN_IDS 二选一或同时填写）
#ADMIN_USERNAMES=alice,bob
# 用户发 /start 时的欢迎语
#WELCOME_MESSAGE=欢迎使用VIP会员购买机器人！\n\n请选择您需要的服务：
# 订单多久未支付自动过期（分钟）
#ORDER_TIMEOUT_MINUTES=30
# 会员到期前几天发提醒（0 = 禁用）
#EXPIRY_REMINDER_DAYS=3
# 到期提醒消息内容（{days} 替换为剩余天数，{expiry_date} 替换为到期日期）
#EXPIRY_REMINDER_MESSAGE=您的会员将在 {days} 天后到期，请及时续费！
```

---

## 3. 最小配置跑通

### 3.1 开启 Telegram Stars（零外部手续）

Telegram Stars 是 Telegram 自带的支付系统，无需任何商户资质。

在 `.env` 中加一行：

```env
STARS_ENABLED=true
```

重启：

```bash
docker compose restart
```

### 3.2 创建套餐

在 Telegram 里给机器人发命令：

```
/addplan 月度会员 30 -1001234567890 150
```

参数依次是：套餐名 天数 chat_id Stars价格。

如果一切正确，机器人回复：

```
Plan 月度会员 created (id=1).
```

> 系统 Python 版本不够别担心——Docker 容器内是 Python 3.12，不影响运行。

### 3.3 测试购买流程

用另一个 Telegram 账号给机器人发 `/start`，你会看到：

```
Choose a plan:
「月度会员 — 30d」
```

点击 → 看到价格 `Stars: 150 XTR` → 选择 Stars → 弹出 Telegram 原生付款界面 → 确认付款 → 机器人发入群链接 → 点击链接发出入群申请 → 瞬间批准入群。

**至此核心流程跑通。**

---

## 4. 创建你的第一个套餐

### 4.1 完整命令

```
/addplan 名称 天数 群ID Stars价 USDT价 美分价 支付宝价 微信价
```

**每个价格填 0 表示该支付方式不启用**（用户端不显示该渠道）。单位说明：

| 位置 | 含义 | 单位 | 示例 |
|------|------|------|------|
| 第 4 参数 Stars 价 | Telegram Stars | XTR | `150` |
| 第 5 参数 USDT 价 | CryptoBot | USDT | `5` |
| 第 6 参数 美分价 | Stripe 信用卡 | **美分**（$5 = 500） | `499` |
| 第 7 参数 支付宝价 | 支付宝 | 人民币元 | `29.90` |
| 第 8 参数 微信价 | 微信支付 | 人民币元 | `29.90` |

> **注意 Stripe 是美分不是美元**，填 `499` 表示 US$4.99，不是 $499。

例如：

```
/addplan VIP月卡 30 -1001234567890 150 5 499 29.90 29.90
/addplan VIP季卡 90 -1001234567890 400 12 1299 68.00 68.00
/addplan VIP年卡 365 -1001234567890 1200 30 3999 198.00 198.00
```

> **创建套餐前必须确保机器人是该群管理员**，否则创建会失败并提示"机器人未加入该群组"。

### 4.2 管理套餐

| 命令 | 用途 |
|------|------|
| `/plans` | 查看所有套餐 |
| `/editplan 1 days 60` | 把 1 号套餐改成 60 天 |
| `/editplan 1 name VIP-Pro` | 改名 |
| `/editplan 1 stars 200` | 改价格 |
| `/delplan 1` | 停用（已有会员不受影响） |

> **群组 chat_id 创建后不可修改**，因为已有订阅绑定到原始群组。如需更换群组请新建套餐。

### 4.3 Web 面板管理更直观

后台面板（见第 6 节）里每个套餐一行，名称/天数/五种价格直接在表格里修改，点"保存"即可。新建套餐时第二行清晰标注了每个输入框对应的支付渠道（Stars / USDT / Stripe美分 / 支付宝CNY / 微信CNY），鼠标悬停在价格框上还有 tooltip 说明单位和"0=不启用"。比命令更舒服。

---

## 5. 接入更多支付方式

五个支付渠道，逐项配置。不想用的留空即可，用户端自动不显示。

### 5.1 Telegram Stars ✅ 零配置

```env
STARS_ENABLED=true
```

### 5.2 CryptoBot（USDT / TON）

1. 打开 [@CryptoBot](https://t.me/CryptoBot)（测试用 [@CryptoTestnetBot](https://t.me/CryptoTestnetBot)）
2. 发送 `/start` → `Crypto Pay` → `Create App`
3. 拿到 API Token 后填：

```env
CRYPTO_ENABLED=true
CRYPTO_API_TOKEN=你的token
```

### 5.3 Stripe（信用卡）

1. 去 [dashboard.stripe.com](https://dashboard.stripe.com/apikeys) 获取密钥
2. 在 Stripe 后台配 Webhook：`https://你的域名:8000/webhook/stripe`，监听事件 `checkout.session.completed`

```env
STRIPE_ENABLED=true
STRIPE_SECRET_KEY=sk_live_xxxx
STRIPE_WEBHOOK_SECRET=whsec_xxxx
STRIPE_SUCCESS_URL=https://t.me/你的机器人用户名
STRIPE_CANCEL_URL=https://t.me/你的机器人用户名
```

### 5.4 支付宝 & 微信支付

这两个是用户看到的按钮，后台用易支付或虎皮椒处理。首先在 `.env` 里指定：

```env
# 支付宝用虎皮椒，微信支付用易支付
ALIPAY_BACKEND=hupijiao
WECHAT_BACKEND=epay
```

可选值：`hupijiao` | `epay` | 留空（不启用）。

#### 5.4.1 虎皮椒（xunhupay.com）

个人可注册，无需企业资质，支持支付宝和微信收款。

1. 访问 [xunhupay.com](https://www.xunhupay.com) 注册
2. 支付渠道管理 → 我的支付渠道 → 申请 → 拿到 APPID 和 APPSECRET
3. 在虎皮椒后台设置异步通知地址：`https://你的域名:8000/webhook/hupijiao`

```env
HUPIJIAO_ENABLED=true
HUPIJIAO_API_URL=https://api.xunhupay.com
HUPIJIAO_APPID=你的APPID
HUPIJIAO_APPSECRET=你的APPSECRET
HUPIJIAO_NOTIFY_URL=https://你的域名:8000/webhook/hupijiao
```

`HUPIJIAO_API_URL` 默认为 `https://api.xunhupay.com`，一般不用改。如果你用的是虎皮椒代理或自建网关，才需要修改这个地址。
```

#### 5.4.2 易支付

如果你有易支付商户号（Epay 协议）：

```env
EPAY_ENABLED=true
EPAY_API_URL=https://你的支付网关.com/
EPAY_PID=你的商户ID
EPAY_KEY=你的商户密钥
EPAY_NOTIFY_URL=https://你的域名:8000/webhook/epay
```

#### 举例：常见场景

**场景 1：只用虎皮椒收款**

```env
ALIPAY_BACKEND=hupijiao
WECHAT_BACKEND=hupijiao
HUPIJIAO_ENABLED=true
HUPIJIAO_API_URL=https://api.xunhupay.com
HUPIJIAO_APPID=...
HUPIJIAO_APPSECRET=...
HUPIJIAO_NOTIFY_URL=https://VPS的IP:8000/webhook/hupijiao
```

**场景 2：支付宝用虎皮椒，微信用易支付**

```env
ALIPAY_BACKEND=hupijiao
WECHAT_BACKEND=epay
HUPIJIAO_ENABLED=true
HUPIJIAO_API_URL=https://api.xunhupay.com
HUPIJIAO_APPID=...
HUPIJIAO_APPSECRET=...
HUPIJIAO_NOTIFY_URL=https://VPS的IP:8000/webhook/hupijiao
EPAY_ENABLED=true
EPAY_API_URL=...
EPAY_PID=...
EPAY_KEY=...
EPAY_NOTIFY_URL=https://VPS的IP:8000/webhook/epay
```

**场景 3：只要 Stars 和 Crypto，不要国内支付**

```env
ALIPAY_BACKEND=
WECHAT_BACKEND=
```

留空即不出现。

---

## 6. 管理员后台面板

### 6.1 开启面板

管理面板**默认已开启**，无需额外配置。初始账号：

```
用户名: admin
密码:   123456
```

启动后直接访问：

```
http://你的VPS_IP:8000/admin/login
```

**首次登录会强制跳转到修改密码页面**，必须设置新密码（至少 8 位）后才能进入管理后台。修改后的密码写入 `data/admin_creds.json` 持久保存，不会丢失。

如果想自定义初始账号，在 `.env` 中取消注释：

```env
ADMIN_PANEL_USERNAME=myadmin
ADMIN_PANEL_PASSWORD=mypassword
```

登录后会话保持 24 小时，左侧导航栏随时可跳转各功能页或退出。

### 6.2 面板布局

登录后进入侧边栏导航界面，包含以下页面：

| 导航项 | 路径 | 说明 |
|--------|------|------|
| 📊 首页 | `/admin` | 数据总览（用户、会员、收入、最近订单） |
| 📦 套餐管理 | `/admin/plans` | 机器人已加入群组/频道、套餐创建与定价 |
| 👥 会员管理 | `/admin/members` | 赠送、搜索、导出、优惠码 |
| ⚙️ 机器人配置 | `/admin/bot-config` | Bot Token、管理员用户名、欢迎语、超时、到期提醒等 |
| 💳 支付设置 | `/admin/settings` | 所有支付渠道配置，即时生效无需重启 |
| 🔒 修改密码 | `/admin/change-password` | 随时更新管理员密码 |

### 6.3 首页功能

- **顶部卡片**：总用户数、活跃会员、已完成订单数、近 7 天新订单
- **收入统计**：按支付渠道分组的累计收入
- **订单流水**：最近 20 笔，含支付渠道、金额、状态（超时关闭后又收到支付的订单显示「迟付复活」标签）

### 6.3.0 套餐管理页

路径：`/admin/plans`

- **机器人已加入的群组 / 频道**：自动列出机器人当前所在的群/频道（名称、类型、用户名、chat_id、是否为管理员）。把机器人加入群组并设为管理员后，这里会实时更新——不必再手动去查 chat_id
- **套餐管理**：
  - **新建套餐**：第一行填名称/天数/群ID，第二行填五种价格（每个价格前标注了渠道名，`(填0=不启用该渠道)` 提示在行首）
  - **chat_id 可选可填**：群/频道 chat_id 输入框是「下拉 + 手动输入」二合一——可从机器人已加入的群/频道里直接选，也可手动粘贴任意 chat_id
  - **编辑套餐**：表格内直接改，每种价格框悬停显示单位提示（如 `Stars·XTR | 0=不启用`、`Stripe·美分(999=US$9.99) | 0=不启用`）
  - **群组 ID 只读**：创建后不可修改，挂锁提示
  - 一键停售/恢复

### 6.3.1 会员管理页

路径：`/admin/members`

- **赠送会员**：填写用户 ID 或 @用户名、选择套餐、天数；履约后尝试私聊发送入群链接
- **活跃会员列表**：搜索、调整到期（`+7` / `-3` / `YYYY-MM-DD`）、移除并踢出
- **导出 CSV**：导出当前活跃会员（含用户名、套餐、群、到期、关联订单渠道/金额），UTF-8 BOM，可用 Excel 打开
- **免费体验 / 优惠码**：创建时**自动随机生成**优惠码；用户私聊机器人发送优惠码即可开通体验天数，并收到入群链接。同时仍会生成 Telegram 邀请链接（点链接申请入群也可开通）。后台可随时改天数（只影响之后新兑换/入群用户）。可设使用次数上限、链接过期时间，可撤销链接。创建时可选择适用对象：**全部用户 / 仅新用户 / 仅老用户**（新用户=该群从未有过会员记录，老用户=曾有过）
- **付费折扣 / 优惠码**：创建时**自动随机生成**优惠码，并同时得到深链 `https://t.me/Bot?start=promo_xxx`。用户可任选一种方式兑换：
  - **私聊机器人直接发送优惠码**（大小写不敏感）→ 机器人回复成功/失败文案，再点「🛒 购买/续费」下单
  - 打开深链 → `/start` 时自动绑定优惠
  购买对应套餐按折扣（百分比优先，或固定减免）计费。改折扣只影响之后新订单。同样可限制仅新用户或仅老用户
> Telegram 邀请链接的「过期时间」只控制链接何时失效，**不能**控制用户在群里待多久；在群时长一律由会员到期踢人负责。

### 6.4 机器人配置页

路径：`/admin/bot-config`，所有参数保存到 `data/bot_config.json`，覆盖 `.env` 中的对应值，**重启后仍生效**。

**基础配置：**

| 字段 | 说明 | 默认值 |
|------|------|--------|
| Bot Token | 机器人令牌（留空=不修改；修改后需**重启服务**） | — |
| 管理员 TG 用户名 | 逗号分隔，不含 `@`；与 `.env` 中 `ADMIN_IDS` 二选一或同时填写，**均可授予 Bot 管理员权限** | — |
| 机器人欢迎语 | 用户发 `/start` 时显示 | 内置文案 |
| 订单超时时间（分钟）| 未支付多久后自动过期 | `30` |

**到期提醒设置：**

| 字段 | 说明 | 默认值 |
|------|------|--------|
| 到期前几天提醒 | 提前几天发消息，`0` 禁用 | `3` |
| 到期提醒消息 | 支持 `{days}`（剩余天数）和 `{expiry_date}`（到期日期）变量 | 内置文案 |

### 6.5 支付设置页

路径：`/admin/settings`，所有支付渠道（Stars / Crypto / Stripe / 虎皮椒 / 易支付 / 路由）均可在前端直接配置，保存后**即时生效，无需重启**。配置持久化到 `data/payment_config.json`。

**页面结构：**

- **Telegram Stars**：启用开关、自定义 Token（留空则用 Bot Token）
- **CryptoBot (USDT)**：启用开关、API Token
- **Stripe (信用卡)**：启用开关、密钥、Webhook 密钥、成功/取消跳转地址
- **支付宝路由**：下拉选择（禁用 / 易支付 / 虎皮椒）+ 启用开关
- **微信支付路由**：下拉选择（禁用 / 易支付 / 虎皮椒）+ 启用开关
- **易支付 配置区**：API 地址、商户 ID、密钥、通知地址、**付款后跳转地址**
- **虎皮椒 配置区**：API 地址、APPID、APPSECRET、通知地址、**付款后跳转地址**

**动态显示逻辑：**

当你在"支付宝路由"或"微信支付路由"中选择「易支付」或「虎皮椒」后，页面**自动显示**对应的配置区域（ID 为 `backend-section-epay` / `backend-section-hupijiao`）。如果支付宝和微信都选了同一个后台，配置区域**只出现一次**，填一份即可——两个路由共用同一套后台参数。未选中的后台配置区域**自动隐藏**。

> 提示文字："全局共用：支付宝/微信路由指向此后台时共用以下配置，只需填写一次。"

**付款后跳转地址**（`return_url`）：用户支付完成后浏览器跳转的地址。虎皮椒建议填 `https://t.me/你的机器人用户名`，留空则回退为回调地址（不推荐）。

**开关与密钥：**
- 各渠道「启用」checkbox 取消勾选后保存即可关闭该渠道
- 密钥类字段留空表示不修改，页面不会回显已有密钥

### 6.6 安全提醒

- 登录频率限制（每 IP 每分钟最多 5 次）
- 密码 PBKDF2-HMAC-SHA256（10 万轮）哈希，`data/admin_creds.json` 权限 600
- 首次登录强制修改默认密码
- **所有 POST 表单**含 CSRF token（含修改密码页）
- **登出**会清除服务端 session，被盗 cookie 在登出后失效
- 配置文件 `data/payment_config.json`、`data/bot_config.json` 不含明文密钥回显
- HTTP 下密码明文传输——生产务必 Nginx + HTTPS（第 8 节）
- 可选：`.env` 设置 `ADMIN_PANEL_ALLOWED_IPS=IP1,IP2` 限制谁可访问 `/admin`

---

## 7. 日常运营

### 7.1 管理员命令一览

| 命令 | 用途 |
|------|------|
| `/admin` | 显示所有管理命令 |
| `/addplan 名称 天数 群ID 价格...` | 创建套餐（机器人需是群管理员） |
| `/editplan <id> <字段> <值>` | 修改套餐（字段：name/days/stars/crypto/stripe/cny/wechat；chat_id 不可改） |
| `/delplan <id>` | 停用套餐 |
| `/plans` | 查看所有套餐 |
| `/stats` | 收入统计 + 活跃会员数 |
| `/grant <用户ID或@用户名> <套餐ID> <天数>` | 赠送会员（与后台「赠送会员」相同） |
| `/setexpiry <用户ID或@用户名> <套餐ID> <+N/-N/日期>` | 调整到期（改到过去**立即踢人**） |
| `/active` | 活跃订阅列表（最多显示 100 条，含用户名） |
| `/broadcast <消息>` | 群发（限速 ~25 条/秒，单次最多 5000 人） |

> Bot 管理员：`.env` 中 `ADMIN_IDS` **或** 后台「管理员 TG 用户名」均可。会员相关操作（赠送、搜索、`/grant`、`/setexpiry`）均支持**数字 ID** 与 **@用户名**（用户名需对方曾与机器人对话，或可通过 Telegram API 解析）。

### 7.2 用户端命令

| 命令 | 用途 |
|------|------|
| `/start` | 选套餐付费 |
| `/my` | 查看订阅状态和到期时间 |

### 7.3 自动处理（无需干预）

- **每小时**检查到期会员 → 踢出群并解禁；踢人**失败则保持 active 下次重试**（不会误标 expired 导致漏踢）
- **每 6 小时**检查即将到期 → 提前 N 天提醒（`{days}`、`{expiry_date}` 变量）；每周期只提醒一次
- **每 15 分钟**清理超时未支付订单并通知用户
- **支付回调**：验签 → 校验金额/币种 → 幂等履约 → 仅首次履约发入群链接（网关重试不重复发）
- **迟付复活**：超时/取消后仍收到合法付款 → 自动补发会员，订单标「迟付复活」
- **Stripe**：失败事件验签后取消订单；`charge.refunded` 自动撤销会员并踢人
- **Stars**：付款前校验订单状态与金额；扣款后履约失败会提示用户联系管理员
- 重复下单：同套餐再次支付时自动取消旧 pending 订单
- 入群需申请，非会员自动拒绝；停售套餐不可购买
- **入群通知**：会员入群申请被自动批准后，Bot 向所有管理员（`ADMIN_IDS` + 已入库的管理员用户名）发送 Telegram 私聊通知（含用户、群组、套餐、到期时间）；管理员须曾与 Bot 对话

### 7.4 改支付方式

修改 `.env` 后重启容器即生效，不影响已有订阅。

---

## 8. 安全加固（配 Nginx + HTTPS）

通过 Nginx 反代 + Let's Encrypt 免费 SSL 证书，让所有支付回调和管理面板走 HTTPS。

### 8.1 装 Nginx

```bash
sudo apt update && sudo apt install nginx -y
```

### 8.2 使用项目自带 Nginx 配置

项目 `nginx/default.conf` 已经写好了反代规则。直接复制过去：

```bash
# 回到项目目录
cd ~/projects/tg-paid-community-bot

# 复制配置
sudo cp nginx/default.conf /etc/nginx/sites-available/bot

# 启用
sudo ln -s /etc/nginx/sites-available/bot /etc/nginx/sites-enabled/
```

### 8.3 改配置里的域名

```bash
sudo nano /etc/nginx/sites-available/bot
```

把第一行 `server_name your-domain.com` 里的 `your-domain.com` 改成你真实的域名。保存退出。

### 8.4 检查配置并启动

```bash
sudo nginx -t
# 应该输出：syntax is ok / test is successful

sudo systemctl reload nginx
```

### 8.5 申请免费 SSL 证书（Let's Encrypt）

```bash
# 装 certbot
sudo apt install certbot python3-certbot-nginx -y

# 一键申请（把 your-domain.com 换成真实域名）
sudo certbot --nginx -d your-domain.com
```

过程中：
- 输入邮箱
- 同意协议
- 是否接收营销邮件（选 N）

完成后 certbot 自动改写 Nginx 配置，加入 SSL 证书路径和 443 监听。

### 8.6 验证 HTTPS

浏览器访问 `https://your-domain.com/admin`，地址栏出现锁图标即成功。

### 8.7 更新 .env 中的回调地址

把所有支付方式的 `NOTIFY_URL` 从 `http://IP:8000` 改成 `https://your-domain.com`：

```env
# 虎皮椒
HUPIJIAO_NOTIFY_URL=https://your-domain.com/webhook/hupijiao

# 易支付
EPAY_NOTIFY_URL=https://your-domain.com/webhook/epay

# Stripe
# （在 Stripe Dashboard 里改 Webhook URL）

# CryptoBot
# （在 CryptoBot App 里改 callback URL）
```

然后去各自的支付平台后台，把通知地址也同步更新。**别忘了这一步，否则付款成功收不到回调。**

### 8.8 重启服务

```bash
docker compose restart
```

### 8.9 关闭 8000 端口（可选）

既然 HTTPS 通过 Nginx 走 443，8000 端口不需要对外了：

```bash
# 删除防火墙规则
sudo ufw delete allow 8000/tcp

# 云控制台安全组里也删掉 TCP 8000 入站规则
```

Docker 容器内 8000 端口仍然监听 `0.0.0.0`，但外部只能通过 Nginx 443 访问，更安全。

### 8.10 SSL 证书自动续期

Let's Encrypt 证书 90 天有效期，certbot 已自动配了续期定时器：

```bash
# 检查定时器
sudo systemctl status certbot.timer

# 手动测试续期（不会真续，只是模拟）
sudo certbot renew --dry-run
```

一切正常就完了——之后不用管，自动续。

### 8.11 Docker 自动重启

`docker-compose.yml` 里已有 `restart: unless-stopped`，容器意外退出或 VPS 重启后自动恢复。

### 8.12 限制管理后台访问 IP（可选）

在 `.env` 中设置：

```env
ADMIN_PANEL_ALLOWED_IPS=你的公网IP,办公室IP
```

留空则不限制。设置后只有列表中的 IP 可访问 `/admin` 路径（含登录页）。若走 Nginx 反代，需确保 `X-Forwarded-For` 传递正确，或直接把 Nginx 所在机器 IP 加入白名单。

---

## 9. 常见问题

### Q: 用户付款后没收到入群链接

1. 确认机器人在群里有"邀请用户"权限
2. 查看日志 `docker compose logs bot`，搜 "Failed to create invite link"
3. 如果是外部支付（Crypto/Stripe/易支付/虎皮椒），确认 webhook URL 在 VPS 公网可达
4. 管理后台订单列表可查看订单状态：超时关闭的订单显示"expired"，迟付款补发的显示"迟付复活"标签

### Q: 外部支付成功但没收到通知

在支付服务商后台（Stripe/CryptoBot/虎皮椒/易支付）查看回调日志。确保 `.env` 中的通知地址是 VPS 公网可访问的 `https://域名:8000/webhook/xxx`。

如果通知已发出但系统没处理，查看日志搜 `callback rejected`——通常是签名验证失败，检查密钥配置。
Stripe 支付失败或会话过期时，系统会自动收到事件并标记订单为已取消，同时通知用户。

### Q: 到期没自动踢人

1. 确认机器人在群里有"禁言/移除成员"权限
2. 日志搜 "Failed to kick"，通常就是权限不够

### Q: 想给某个人免费延期或赠送

- **延期**：后台会员管理填 `+7` / `-3`，或 `/setexpiry @用户名 套餐ID +7`
- **赠送**：后台「赠送会员」填 ID 或 `@用户名`，或 `/grant @用户名 套餐ID 天数`
- 对方**未与 Bot 对话**时无法私聊发链接，赠送成功后从后台提示中**复制链接**手动转发

### Q: 没有域名怎么测试支付回调

可用 **Cloudflare Quick Tunnel**（免费、临时）：

```bash
cloudflared tunnel --url http://127.0.0.1:8000
```

终端会输出 `https://xxxx.trycloudflare.com`，虎皮椒回调填 `https://xxxx.trycloudflare.com/webhook/hupijiao`。重启后地址会变，需同步更新后台与虎皮椒配置。正式运营建议购买域名并配 Nginx + HTTPS（第 8 节）。

### Q: 升级项目

```bash
git pull
docker compose down
docker compose up -d --build
```

启动时会自动执行 `alembic upgrade head`（含数据库索引迁移）。若需手动：

```bash
docker compose exec bot alembic upgrade head
```

升级前建议备份 `data/bot.db`。

### Q: 数据库能换 PostgreSQL 吗

可以。把 `.env` 里的 `DATABASE_URL` 换成 `postgresql+asyncpg://user:pass@host/dbname`，改 `requirements.txt` 加 `asyncpg`，重启即可。Alembic 会自动适配。

### Q: 怎么备份

```bash
cp data/bot.db data/bot.db.backup
```
