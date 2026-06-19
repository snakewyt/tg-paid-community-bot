# Telegram 付费社群机器人

**Language / 语言:** [简体中文](README.md) · [English](README_en.md)

一套完整的 Telegram 付费社群管理系统。用户通过 Telegram 机器人自助选购套餐、完成支付，系统自动管理会员入群、到期踢出、续费提醒等全流程。

## 功能特性

- **套餐管理**：多种套餐、独立定价、随时上架/下架；群组 ID 创建后不可改
- **五种支付方式**：Telegram Stars、CryptoBot（USDT）、Stripe、支付宝、微信支付
- **支付后台路由**：支付宝/微信可分别选择易支付或虎皮椒，后台配置页动态显示
- **入群保护**：入群申请模式，仅活跃会员自动批准，链接外泄无效
- **支付安全**：回调验签、金额校验、幂等履约、并发防重复、网关重试不重复发链接
- **支付容错**：超时订单自动清理、迟到付款自动「复活」、Stripe 失败/退款自动处理
- **自动到期**：到期踢人（踢失败则重试，不误标过期）、续费提醒（支持 `{days}` / `{expiry_date}`）
- **管理后台**：仪表盘、套餐/会员/订单管理、**后台赠送会员**（支持 ID / @用户名）、调整到期日、支付与机器人配置
- **管理员命令**：Telegram 内 `/grant`、`/setexpiry`、`/broadcast` 等（`ADMIN_IDS` 或后台管理员用户名）
- **入群通知**：会员自动批准入群后，Bot 私信通知所有管理员
- **Docker 部署**：含 Nginx + HTTPS 教程，见 [docs/CONFIG_GUIDE.md](docs/CONFIG_GUIDE.md)

## 快速开始

```bash
git clone https://github.com/snakewyt/tg-paid-community-bot.git
cd tg-paid-community-bot
cp .env.example .env
# 编辑 .env：BOT_TOKEN、ADMIN_IDS
docker compose up -d
```

最小 `.env`：

```env
BOT_TOKEN=123456:ABC-DEF1234gh
ADMIN_IDS=123456789
```

创建第一个套餐（Telegram 对机器人发）：

```
/addplan 月度会员 30 -1001234567890 150
```

## 支付方式

| 支付方式 | 说明 |
|---------|------|
| Telegram Stars | 零配置，填 `STARS_ENABLED=true` |
| CryptoBot | USDT，需 API Token |
| Stripe | 信用卡（美分定价），支持退款 webhook |
| 支付宝 / 微信 | 路由到易支付或虎皮椒 |

套餐价格填 `0` = 该渠道不启用。Stripe 价格为**美分**（499 = $4.99）。

## 管理后台

访问 `http://服务器:8000/admin/login`

| 项目 | 说明 |
|------|------|
| 默认账号 | `admin` / `123456`（首次登录强制改密） |
| 首页 | 统计、套餐、**赠送会员**（ID / @用户名）、会员搜索/调整/移除、订单 |
| 机器人配置 | Token、管理员用户名、欢迎语、超时、到期提醒（Bot Token 留空=不修改） |
| 支付设置 | 全渠道开关与密钥（密钥留空=不修改） |

可选安全加固（`.env`）：

```env
# 仅允许这些 IP 访问 /admin（逗号分隔，留空=不限制）
ADMIN_PANEL_ALLOWED_IPS=你的公网IP
```

生产环境务必配置 Nginx + HTTPS，详见配置教程第 8 节。

## 管理员命令

| 命令 | 说明 |
|------|------|
| `/addplan 名称 天数 群ID [价格...]` | 创建套餐 |
| `/editplan <id> <字段> <值>` | 改套餐（**不可改 chat_id**） |
| `/grant <用户ID或@用户名> <套餐ID> <天数>` | 赠送会员（无法私聊时在后台/命令回复中显示链接） |
| `/setexpiry <用户ID或@用户名> <套餐ID> <+N/-N/日期>` | 调整到期（改到过去会立即踢人） |
| `/stats` / `/active` / `/broadcast` | 统计 / 活跃列表 / 群发（限速 ~25条/秒，上限 5000 人） |

> 管理员权限：`ADMIN_IDS`（`.env`）或后台「管理员 TG 用户名」均可。会员操作支持数字 ID 与 @用户名。

## 升级

```bash
git pull
docker compose down
docker compose up -d --build
# 容器内会自动 alembic upgrade head；也可手动：
docker compose exec bot alembic upgrade head
```

## 架构概览

```
用户 /start → 选套餐 → 选支付 → 回调验签+金额校验 → 幂等履约 → 发入群链接 → 自动批准入群 → 通知管理员
定时任务：每小时踢到期 / 每6h续费提醒 / 每15min清理超时订单
```

完整部署与域名、支付、安全说明 → [docs/CONFIG_GUIDE.md](docs/CONFIG_GUIDE.md)（中文）
