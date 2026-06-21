"""
feishu_bot.py - Hermes/OpenClaw 飞书网关 v0.2 (WebSocket 长连接模式)
=========================================================================

v0.2 新增:
- 消息持久化: ~/.feishu_bot/history/<chat_id>.jsonl (每条消息一行)
- 多 bot 路由: 每个 chat 记住默认 bot (hermes / openclaw / codex-pp)
- 命令: /history, /bot <name>, /reset

用法:
1. 飞书后台 → 事件订阅 → 选"长连接接收"
2. .env 填 FEISHU_APP_ID / FEISHU_APP_SECRET
3. 跑: python feishu_bot.py
4. 命令:
   - help / 帮助 - 列出命令
   - ping - 测试
   - /bot openclaw - 切到 openclaw (本 chat 持久化)
   - /bot - 查当前 bot
   - /history [N] - 看最近 N 条历史 (默认 10)
   - /reset - 清本 chat 历史
   - 任何问题 - 调当前 bot 回答

依赖: pip install lark-oapi
"""

import os
import sys
import json
import subprocess
import shlex
import logging
import tempfile
from datetime import datetime
from pathlib import Path

# fcntl 是 Unix only, Windows 用不到
try:
    import fcntl as _fcntl
    HAS_FCNTL = True
except ImportError:
    HAS_FCNTL = False

# ==== Windows GBK stdout 兜底 ====
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# ==== 自动加载 .env ====
_ENV_PATH = Path(__file__).resolve().parent / ".env"
if _ENV_PATH.exists():
    for _line in _ENV_PATH.read_text(encoding="utf-8", errors="ignore").splitlines():
        _line = _line.strip()
        if not _line or _line.startswith("#") or "=" not in _line:
            continue
        _k, _v = _line.split("=", 1)
        _k, _v = _k.strip(), _v.strip().strip('"').strip("'")
        if _k and _k not in os.environ:
            os.environ[_k] = _v

try:
    import lark_oapi as lark
    from lark_oapi.api.im.v1.model.p2_im_message_receive_v1 import (
        P2ImMessageReceiveV1,
        P2ImMessageReceiveV1Data,
    )
    HAS_LARK = True
except ImportError:
    HAS_LARK = False
    lark = None
    P2ImMessageReceiveV1 = None
    P2ImMessageReceiveV1Data = None

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("hermes-feishu")

APP_ID = os.getenv("FEISHU_APP_ID", "")
APP_SECRET = os.getenv("FEISHU_APP_SECRET", "")
DEFAULT_CLI = os.getenv("FEISHU_CLI", "hermes")
SUPPORTED_BOTS = ("hermes", "openclaw", "codex-pp")

# ==== 持久化路径 ====
HISTORY_DIR = Path.home() / ".feishu_bot" / "history"
ROUTING_FILE = Path.home() / ".feishu_bot" / "routing.json"
HISTORY_DIR.mkdir(parents=True, exist_ok=True)

# 文件锁 (Unix 优先, Windows 退化)
try:
    import fcntl as _fcntl
    HAS_FCNTL = True
except ImportError:
    HAS_FCNTL = False


def with_lock(fn):
    if not HAS_FCNTL:
        return fn()
    lock_path = HISTORY_DIR / ".lock"
    lock_fd = open(lock_path, "w")
    try:
        _fcntl.flock(lock_fd.fileno(), _fcntl.LOCK_EX)
        return fn()
    finally:
        try:
            _fcntl.flock(lock_fd.fileno(), _fcntl.LOCK_UN)
        except OSError:
            pass
        lock_fd.close()


# ==== 消息持久化 ====

def history_file(chat_id: str) -> Path:
    """每个 chat 一个 jsonl 文件"""
    safe = "".join(c if c.isalnum() else "_" for c in chat_id)[:64]
    return HISTORY_DIR / f"{safe}.jsonl"


def save_message(chat_id: str, record: dict) -> None:
    """追加一条消息到 history (原子写)"""
    f = history_file(chat_id)
    record.setdefault("ts", datetime.now().isoformat(timespec="seconds"))
    line = json.dumps(record, ensure_ascii=False) + "\n"
    # 简单文件锁 + 追加
    with_lock(lambda: None)
    fd, tmp = tempfile.mkstemp(dir=HISTORY_DIR, prefix=".msg_", suffix=".tmp")
    try:
        # 读旧 + 追加 + 写 tmp
        old = f.read_text(encoding="utf-8") if f.exists() else ""
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            out.write(old)
            out.write(line)
        os.replace(tmp, f)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def load_history(chat_id: str, limit: int = 10) -> list:
    """读最近 N 条 (尾部)"""
    f = history_file(chat_id)
    if not f.exists():
        return []
    lines = f.read_text(encoding="utf-8").splitlines()
    return [json.loads(l) for l in lines[-limit:] if l.strip()]


def clear_history(chat_id: str) -> int:
    """清本 chat 历史, 返回删了几条"""
    f = history_file(chat_id)
    if not f.exists():
        return 0
    n = sum(1 for _ in f.read_text(encoding="utf-8").splitlines() if _.strip())
    f.unlink()
    return n


# ==== 多 bot 路由 ====

def load_routing() -> dict:
    """读 chat_id → bot 映射"""
    if not ROUTING_FILE.exists():
        return {}
    try:
        return json.loads(ROUTING_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_routing(routing: dict) -> None:
    """原子写"""
    ROUTING_FILE.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=ROUTING_FILE.parent, prefix=".route_", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(routing, f, ensure_ascii=False, indent=2)
        os.replace(tmp, ROUTING_FILE)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def get_bot_for_chat(chat_id: str) -> str:
    """查 chat 的 bot, 没设过返回 DEFAULT_CLI"""
    routing = load_routing()
    return routing.get(chat_id, DEFAULT_CLI)


def set_bot_for_chat(chat_id: str, bot: str) -> None:
    """设 chat 的 bot"""
    if bot not in SUPPORTED_BOTS:
        raise ValueError(f"未知 bot: {bot} (支持: {', '.join(SUPPORTED_BOTS)})")
    routing = load_routing()
    routing[chat_id] = bot
    save_routing(routing)


# ==== CLI 调用 ====

def call_cli(prompt: str, cli: str) -> str:
    if cli == "openclaw":
        cmd = ["openclaw", "ask", prompt]
    elif cli == "hermes":
        cmd = ["hermes", "chat", "-q", prompt, "-Q"]
    elif cli == "codex-pp":
        cmd = ["codex-pp", "ask", prompt]
    else:
        return f"[错误] 未知 CLI: {cli}"
    logger.info(f"执行 ({cli}): {' '.join(shlex.quote(c) for c in cmd)}")
    try:
        result = subprocess.run(
            cmd,
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=180,
        )
        if result.returncode == 0:
            out = (result.stdout or "").strip()
            return out if out else "[空响应]"
        # 错误: 优先显示 stdout (hermes 把 API 错误打到 stdout), 再 stderr, 最后 exit code
        out = (result.stdout or "").strip()
        err = (result.stderr or "").strip()
        msg = out or err or f"(no output, exit={result.returncode})"

        # 翻译常见 Windows 错误码
        rc = result.returncode
        win_hints = {
            3221225786: "Windows: 进程被强制终止 (可能是 DLL 初始化失败 / 内存访问违例)",
            3221225477: "Windows: ACCESS_VIOLATION (内存访问违例)",
            3221225501: "Windows: 栈溢出",
            3221225595: "Windows: 堆损坏",
            1: "通用错误 (hermes 通常把真原因打到 stdout)",
        }
        hint = win_hints.get(rc, "")

        # 检测 API 错误类型 (token 耗尽 / 401 / 超时)
        api_hint = ""
        lo = msg.lower()
        if "429" in msg or "token plan" in lo or "用量上限" in msg:
            api_hint = " → 【MiniMax Token Plan 额度耗尽】等下月重置, 或换账号, 或换 deepseek(已 dead)"
        elif "401" in msg or "unauthorized" in lo or "authorized_error" in lo:
            api_hint = " → 【API Key 失效】去 minimax 后台重置"
        elif "timeout" in lo:
            api_hint = " → 【超时】网络问题或 provider 慢"

        return f"[错误] CLI 退出码 {rc}{(' · ' + hint) if hint else ''}: {msg[:300]}{api_hint}"
    except subprocess.TimeoutExpired:
        return "[超时] CLI 180 秒没回"
    except FileNotFoundError:
        return f"[错误] 找不到 {cli} 命令 (PATH 没装?)"
    except Exception as e:
        logger.exception("CLI 异常")
        return f"[异常] {e}"


# ==== 命令处理 ====

def cmd_help() -> str:
    return (
        "🤖 飞书网关 v0.2 (Hermes/OpenClaw)\n"
        "命令:\n"
        "  help / 帮助 - 列命令\n"
        "  ping - 测试\n"
        "  /bot <name> - 切 bot (hermes / openclaw / codex-pp)\n"
        "  /bot - 查当前 bot\n"
        "  /history [N] - 看最近 N 条历史 (默认 10)\n"
        "  /reset - 清本 chat 历史\n"
        "  任何问题 - 调当前 bot 回答"
    )


def cmd_history(chat_id: str, n: int = 10) -> str:
    hist = load_history(chat_id, limit=n)
    if not hist:
        return "(本 chat 无历史)"
    lines = [f"📜 最近 {len(hist)} 条 (chat {chat_id[:12]}...):"]
    for h in hist:
        ts = h.get("ts", "?")[:19]
        sender = h.get("sender", "?")[:8]
        text = (h.get("text") or h.get("response") or "")[:60]
        bot = h.get("bot", "?")
        lines.append(f"  [{ts}] {sender} via {bot}: {text}")
    return "\n".join(lines)


def handle_message(chat_id: str, sender_open_id: str, text: str) -> str:
    text = text.strip()
    if not text:
        return ""

    # 内置命令
    if text in ("help", "帮助", "?"):
        return cmd_help()
    if text in ("ping", "测试"):
        bot = get_bot_for_chat(chat_id)
        return f"pong ✓ (bot={bot}, Hermes 网关 v0.2 ready)"

    if text == "/bot":
        return f"当前 bot: `{get_bot_for_chat(chat_id)}`"

    if text.startswith("/bot "):
        new_bot = text[5:].strip()
        if new_bot not in SUPPORTED_BOTS:
            return f"未知 bot: {new_bot}\n支持: {', '.join(SUPPORTED_BOTS)}"
        set_bot_for_chat(chat_id, new_bot)
        return f"✓ 本 chat 切到 `{new_bot}` (持久化)"

    if text == "/reset":
        n = clear_history(chat_id)
        return f"✓ 清掉 {n} 条历史"

    if text.startswith("/history"):
        parts = text.split()
        n = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 10
        return cmd_history(chat_id, n)

    # 调 bot
    bot = get_bot_for_chat(chat_id)
    logger.info(f"[{chat_id[:12]}/{sender_open_id[:8]}] ({bot}) 问: {text[:80]}")
    response = call_cli(text, bot)
    logger.info(f"[{chat_id[:12]}] 回: {response[:80]}")

    # 持久化
    save_message(chat_id, {
        "chat_id": chat_id,
        "sender": sender_open_id,
        "text": text,
        "bot": bot,
        "response": response[:500],  # 截断避免巨大
    })

    return response


# ==== 飞书发消息 ====

_http_client = None


def reply_text(message, text: str):
    """发文本消息回飞书"""
    global _http_client
    if _http_client is None:
        return
    if not HAS_LARK:
        return
    try:
        req = (
            lark.im.v1.CreateMessageRequest.builder()
            .receive_id_type("open_id")
            .request_body(
                lark.im.v1.CreateMessageRequestBody.builder()
                .receive_id(message.sender.sender_id.open_id)
                .msg_type("text")
                .content(json.dumps({"text": text[:3000]}))
                .build()
            )
            .build()
        )
        resp = _http_client.im.v1.message.create(req)
        if not resp.success():
            logger.error(f"发消息失败: {resp.code} {resp.msg}")
    except Exception as e:
        logger.exception(f"回消息异常: {e}")


def get_chat_id(message) -> str:
    """取 chat_id (优先 chat_id, fallback open_id)"""
    try:
        return message.message.chat_id
    except AttributeError:
        try:
            return message.sender.sender_id.open_id
        except AttributeError:
            return "unknown"


# ==== WebSocket 事件回调 ====

def on_message_receive(event):
    if not HAS_LARK:
        return
    try:
        msg = event.event
        if msg.message.message_type != "text":
            return
        try:
            content = json.loads(msg.message.content)
            text = content.get("text", "").strip()
        except Exception:
            text = msg.message.content.strip()
        # 去 @ bot
        if text.startswith("@"):
            parts = text.split(maxsplit=1)
            text = parts[1] if len(parts) > 1 else ""
        chat_id = get_chat_id(msg)
        sender = msg.sender.sender_id.open_id if hasattr(msg.sender, 'sender_id') else "?"
        reply = handle_message(chat_id, sender, text)
        if reply:
            reply_text(msg, reply)
    except Exception as e:
        logger.exception("处理消息失败")


# ==== 启动 ====

def main():
    global _http_client
    if not APP_ID or not APP_SECRET:
        logger.error("FEISHU_APP_ID / FEISHU_APP_SECRET 未设置,看 .env")
        sys.exit(1)
    if not HAS_LARK:
        logger.error("缺 lark_oapi, 跑: pip install lark-oapi")
        sys.exit(1)

    _http_client = lark.Client.builder().app_id(APP_ID).app_secret(APP_SECRET).build()

    handler = (
        lark.EventDispatcherHandler.builder("", "")
        .register_p2_im_message_receive_v1(on_message_receive)
        .build()
    )

    ws_client = lark.ws.Client(
        APP_ID, APP_SECRET,
        event_handler=handler,
        log_level=lark.LogLevel.INFO,
    )

    logger.info("=" * 50)
    logger.info("飞书网关 v0.2 (WebSocket 模式)")
    logger.info(f"App ID: {APP_ID}")
    logger.info(f"默认 CLI: {DEFAULT_CLI}")
    logger.info(f"history: {HISTORY_DIR}")
    logger.info(f"routing: {ROUTING_FILE}")
    logger.info("=" * 50)
    logger.info("启动长连接,等飞书推事件...")
    ws_client.start()


if __name__ == "__main__":
    main()
