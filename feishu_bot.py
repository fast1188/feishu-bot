"""
feishu_bot.py - 飞书机器人最小 demo
===================================

功能:
- 飞书机器人接收消息
- 调用 openclaw / hermes CLI
- 把结果发回飞书

用法:
1. 飞书开放平台: https://open.feishu.cn/app
2. 创建企业自建应用,记下 App ID 和 App Secret
3. 启用"机器人"能力
4. 添加"消息接收"webhook 或用 websocket
5. 填入 .env
6. 跑: py feishu_bot.py

需要的依赖:
- pip install lark-oapi websockets
"""

import os
import sys
import json
import subprocess
import shlex
import logging
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("feishu-bot")


# ============== 配置 ==============

# 飞书凭证(从飞书后台拿)
APP_ID = os.getenv("FEISHU_APP_ID", "cli_xxxxxxxxxxxx")
APP_SECRET = os.getenv("FEISHU_APP_SECRET", "your_app_secret_here")

# 验证 token(可选,验证 webhook 回调用)
VERIFICATION_TOKEN = os.getenv("FEISHU_VERIFICATION_TOKEN", "")
ENCRYPT_KEY = os.getenv("FEISHU_ENCRYPT_KEY", "")

# 监听端口(webhook 模式)
LISTEN_PORT = int(os.getenv("FEISHU_PORT", "9999"))

# 默认用哪个 CLI:openclaw / hermes / codex-pp
DEFAULT_CLI = os.getenv("FEISHU_CLI", "codex-pp")


# ============== CLI 调用 ==============

def call_cli(prompt: str, cli: str = None) -> str:
    """调用本地 CLI 工具"""
    cli = cli or DEFAULT_CLI
    try:
        if cli == "openclaw":
            cmd = ["openclaw", "ask", prompt]
        elif cli == "hermes":
            cmd = ["hermes", "chat", "--message", prompt]
        elif cli == "codex-pp":
            cmd = ["codex-pp", "ask", prompt]
        else:
            return f"[错误] 未知 CLI: {cli}"
        logger.info(f"执行命令: {' '.join(shlex.quote(c) for c in cmd)}")
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode != 0:
            err = (result.stderr or result.stdout or "").strip()
            logger.error(f"CLI 失败: {err}")
            return f"[错误] CLI 返回 {result.returncode}: {err[:200]}"
        out = (result.stdout or "").strip()
        return out if out else "[空响应]"
    except subprocess.TimeoutExpired:
        return "[超时] CLI 60 秒没返回"
    except FileNotFoundError:
        return f"[错误] 找不到 {cli} 命令(是否安装?PATH 里有吗?)"
    except Exception as e:
        logger.exception("CLI 调用异常")
        return f"[异常] {e}"


# ============== 飞书消息处理 ==============

def handle_message(sender: str, text: str) -> str:
    """处理一条飞书消息,返回要回复的文本"""
    text = text.strip()
    if not text:
        return ""

    # 内置命令
    if text in ("help", "帮助", "?"):
        return (
            "🤖 可用命令:\n"
            "  help - 显示本帮助\n"
            "  ping - 测试连通性\n"
            f"  cli <name> <问题> - 切换 CLI(openclaw/hermes/codex-pp)\n"
            f"  当前 CLI: {DEFAULT_CLI}"
        )
    if text in ("ping", "测试"):
        return "pong ✓"

    # 切换 CLI
    parts = text.split(maxsplit=1)
    if parts[0] == "cli" and len(parts) > 1:
        global DEFAULT_CLI
        sub = parts[1].split(maxsplit=1)
        new_cli, rest = sub[0], sub[1] if len(sub) > 1 else ""
        if new_cli in ("openclaw", "hermes", "codex-pp"):
            DEFAULT_CLI = new_cli
            return f"已切换到 {new_cli}"
        else:
            return f"未知 CLI: {new_cli}。可用: openclaw / hermes / codex-pp"

    # 默认:调 CLI 回答
    logger.info(f"用户 [{sender}] 问: {text[:80]}")
    response = call_cli(text)
    logger.info(f"CLI 回复长度: {len(response)}")
    return response


# ============== 飞书 webhook 处理 ==============

def process_event(event_data: dict) -> dict:
    """处理一条飞书事件,返回响应"""
    try:
        header = event_data.get("header", {})
        event_type = header.get("event_type", "")

        if event_type != "im.message.receive_v1":
            logger.debug(f"忽略事件类型: {event_type}")
            return {"code": 0}

        event = event_data.get("event", {})
        sender = event.get("sender", {}).get("sender_id", {}).get("open_id", "unknown")
        message = event.get("message", {})
        msg_type = message.get("message_type", "text")
        text = message.get("content", "{}")

        # 只处理 text 类型
        if msg_type != "text":
            reply = f"[暂不支持 {msg_type} 类型消息]"
        else:
            try:
                text_data = json.loads(text)
                text = text_data.get("text", "").strip()
            except Exception:
                text = text.strip()

        # 去掉 @机器人 前缀
        if text.startswith("@"):
            parts = text.split(maxsplit=1)
            text = parts[1] if len(parts) > 1 else ""

        reply = handle_message(sender, text)
        logger.info(f"回复 [{sender}]: {reply[:80] if reply else '(空)'}")

        return {
            "code": 0,
            "msg": "ok",
            "data": {"reply": reply},
        }
    except Exception as e:
        logger.exception("处理事件失败")
        return {"code": 1, "msg": str(e)}


# ============== HTTP 服务(webhook 模式) ==============

def run_webhook_server():
    """用内置 http.server 跑 webhook"""
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            try:
                event = json.loads(body.decode("utf-8"))
                # 验证 token(可选)
                token = event.get("token", "")
                if VERIFICATION_TOKEN and token != VERIFICATION_TOKEN:
                    self.send_response(401)
                    self.end_headers()
                    return
                # URL 验证挑战
                if event.get("type") == "url_verification":
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"challenge": event.get("challenge", "")}).encode())
                    return
                # 处理消息
                response = process_event(event)
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(response).encode())
            except Exception as e:
                logger.exception("Webhook 处理失败")
                self.send_response(500)
                self.end_headers()

        def log_message(self, format, *args):
            pass  # 禁用默认日志

    server = HTTPServer(("0.0.0.0", LISTEN_PORT), Handler)
    logger.info(f"飞书 webhook 服务启动: 0.0.0.0:{LISTEN_PORT}")
    logger.info(f"App ID: {APP_ID}")
    logger.info(f"默认 CLI: {DEFAULT_CLI}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("服务停止")


# ============== CLI 调试模式 ==============

def cli_debug_mode():
    """直接测试 CLI 调用(不启动服务)"""
    print("飞书机器人 CLI 调试模式")
    print(f"默认 CLI: {DEFAULT_CLI}")
    print("输入 'help' 看命令, 'quit' 退出")
    print()
    while True:
        try:
            user_input = input("飞书消息> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not user_input:
            continue
        if user_input.lower() in ("quit", "exit", "q"):
            break
        reply = handle_message("debug-user", user_input)
        print(f"  回复: {reply}\n")


# ============== 入口 ==============

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="飞书机器人(本地 CLI 网关)")
    parser.add_argument("--debug", action="store_true", help="调试模式(不启动服务)")
    parser.add_argument("--check", action="store_true", help="检查 CLI 是否可用")
    args = parser.parse_args()

    if args.check:
        print("检查 CLI 可用性...")
        for cli in ["openclaw", "hermes", "codex-pp"]:
            try:
                result = subprocess.run(
                    [cli, "--version"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                if result.returncode == 0:
                    ver = (result.stdout or result.stderr).strip().split("\n")[0]
                    print(f"  ✓ {cli}: {ver}")
                else:
                    print(f"  ✗ {cli}: 返回 {result.returncode}")
            except FileNotFoundError:
                print(f"  ✗ {cli}: 找不到(没安装?PATH 没?)")
            except Exception as e:
                print(f"  ✗ {cli}: {e}")
        sys.exit(0)

    if args.debug:
        cli_debug_mode()
    else:
        run_webhook_server()