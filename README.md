# 飞书机器人 Demo(本地 CLI 网关)

> 不回飞书?这可能是你缺的 demo

## 这是什么?

最小可用的飞书机器人(飞书 → 本地 CLI 网关)。

**架构:**

```
飞书用户发消息
  ↓
飞书服务器
  ↓ webhook
你的服务(本 demo)
  ↓ subprocess
openclaw / hermes / codex-pp
  ↓
回复
  ↓
飞书服务器
  ↓
飞书用户收到
```

## 5 分钟跑起来

### 1. 创建飞书应用

1. 打开 https://open.feishu.cn/app
2. 点"创建企业自建应用"
3. 填名字 + 描述
4. **启用"机器人"能力**(应用能力 → 机器人)
5. **添加权限**: im:message, im:message:receive_v1
6. 拿到 **App ID** 和 **App Secret**

### 2. 配 webhook 接收

- 应用 → 事件订阅
- 订阅方式:**Webhook**
- 请求 URL: `https://你的域名/webhook`(本 demo 监听 9999)
- 没公网 IP?用 ngrok:
 ```
 ngrok http 9999
 # 把 https://xxxx.ngrok.io 作为 webhook URL
 ```
- 验证 token: 随便填(本 demo 不强制验证)

### 3. 配 .env

```bash
cp .env.example .env
# 改里面的:
# FEISHU_APP_ID=cli_xxx
# FEISHU_APP_SECRET=xxx
# FEISHU_PORT=9999
# FEISHU_CLI=codex-pp
```

### 4. 安装 + 跑

```bash
py -m pip install -r requirements.txt
py feishu_bot.py
```

**带 debug 模式(不启动服务,直接测):**

```bash
py feishu_bot.py --debug
# 飞书消息> 你好
#   回复: 你好,我是 codex-pp...
```

**带 CLI 检查模式:**

```bash
py feishu_bot.py --check
# 检查 CLI 可用性...
#   ✓ openclaw: ...
#   ✗ hermes: 找不到(没安装?PATH 没?)
```

## 自带命令(在飞书发这些)

```
help    # 显示帮助
ping    # 测试连通
cli openclaw 你的问题  # 用 openclaw 回
cli hermes 你的问题    # 用 hermes 回
cli codex-pp 你的问题  # 用 codex-pp 回(默认)
```

## 不回飞书?对照排查

| 现象 | 大概率原因 | 怎么查 |
|------|------------|--------|
| 服务没启动 | 网关挂了 | `py feishu_bot.py --check` 看 CLI 装没 |
| 服务启动但收不到消息 | Webhook URL 不通 | 飞书后台 → 事件订阅 → 点"测试" |
| 收到消息但 CLI 报错 | CLI 没装 / PATH 没 | `--check` 看 |
| CLI 跑得动但回 401 | App 凭证错 | 重置 App Secret,改 .env |
| 时不时失效 | access_token 过期 | 本 demo 简化版没用 token,长期用要加 |

## 给 openclaw / hermes 用户的特殊说明

你装 openclaw / hermes 后:

1. 确认 CLI 在 PATH:`where openclaw` / `where hermes`
2. 跑 `--check` 看能不能被网关调起
3. 默认 CLI 改 `FEISHU_CLI=openclaw`(或 hermes)
4. 重启网关服务

## 相关项目

- [codex-pp](https://github.com/fast118/codex-pp) - 国产化 AI CLI
- [cc-switch](https://github.com/fast118/cc-switch) - 桌面 GUI
- [ai-agent-skills](https://github.com/fast118/ai-agent-skills) - 11 个 skills
- 飞书开放平台: https://open.feishu.cn/app

## 许可证

MIT


## 💬 联系

扫码加微信群（AI 工具使用 / 提 issue / 需求讨论）：

![微信群](assets/wechat-qr.png)

或提 [GitHub Issue](https://github.com/fast118/feishu-bot/issues)
