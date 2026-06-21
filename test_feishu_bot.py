"""test_feishu_bot.py — feishu_bot v0.2 单元测试
跑法: python -X utf8 -m unittest test_feishu_bot -v
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent))
import feishu_bot as fb  # noqa


class TestHistory(unittest.TestCase):
    """消息持久化"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="fbbot-test-"))
        self._p1 = patch.object(fb, "HISTORY_DIR", self.tmp)
        self._p2 = patch.object(fb, "ROUTING_FILE", self.tmp / "routing.json")
        self._p1.start(); self._p2.start()

    def tearDown(self):
        self._p1.stop(); self._p2.stop()
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_save_and_load(self):
        fb.save_message("chat_001", {"text": "hi", "response": "hello"})
        fb.save_message("chat_001", {"text": "bye", "response": "goodbye"})
        hist = fb.load_history("chat_001", limit=10)
        self.assertEqual(len(hist), 2)
        self.assertEqual(hist[0]["text"], "hi")
        self.assertEqual(hist[1]["text"], "bye")
        self.assertIn("ts", hist[0])

    def test_load_limit(self):
        for i in range(5):
            fb.save_message("chat", {"text": f"msg-{i}"})
        hist = fb.load_history("chat", limit=3)
        self.assertEqual(len(hist), 3)
        # 后 3 条
        self.assertEqual(hist[0]["text"], "msg-2")
        self.assertEqual(hist[2]["text"], "msg-4")

    def test_clear(self):
        for i in range(3):
            fb.save_message("chat", {"text": f"m{i}"})
        n = fb.clear_history("chat")
        self.assertEqual(n, 3)
        self.assertEqual(fb.load_history("chat"), [])


class TestRouting(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="fbbot-rt-"))
        self._p1 = patch.object(fb, "ROUTING_FILE", self.tmp / "routing.json")
        self._p1.start()

    def tearDown(self):
        self._p1.stop()
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_default_returns_env_default(self):
        # 没设过 → 返回 DEFAULT_CLI
        with patch.object(fb, "DEFAULT_CLI", "hermes"):
            self.assertEqual(fb.get_bot_for_chat("chat_x"), "hermes")

    def test_set_and_get(self):
        with patch.object(fb, "DEFAULT_CLI", "hermes"):
            fb.set_bot_for_chat("chat_001", "openclaw")
            self.assertEqual(fb.get_bot_for_chat("chat_001"), "openclaw")
            # 不影响其他 chat
            self.assertEqual(fb.get_bot_for_chat("chat_002"), "hermes")

    def test_invalid_bot_raises(self):
        with self.assertRaises(ValueError):
            fb.set_bot_for_chat("chat", "unknown_bot")


class TestHandleMessage(unittest.TestCase):
    """命令处理 (不调真实 CLI, mock call_cli)"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="fbbot-hm-"))
        self._p1 = patch.object(fb, "HISTORY_DIR", self.tmp)
        self._p2 = patch.object(fb, "ROUTING_FILE", self.tmp / "routing.json")
        self._p1.start(); self._p2.start()

    def tearDown(self):
        self._p1.stop(); self._p2.stop()
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_help(self):
        r = fb.handle_message("c", "s", "help")
        self.assertIn("v0.2", r)
        self.assertIn("/bot", r)

    def test_ping(self):
        r = fb.handle_message("c", "s", "ping")
        self.assertIn("pong", r)
        self.assertIn("bot=", r)

    def test_bot_query(self):
        r = fb.handle_message("c", "s", "/bot")
        self.assertIn("当前 bot", r)

    def test_bot_switch(self):
        r = fb.handle_message("c", "s", "/bot openclaw")
        self.assertIn("openclaw", r)
        # 持久化生效
        self.assertEqual(fb.get_bot_for_chat("c"), "openclaw")

    def test_bot_invalid(self):
        r = fb.handle_message("c", "s", "/bot xxx")
        self.assertIn("未知", r)

    def test_history_cmd(self):
        fb.save_message("c", {"text": "hello", "response": "hi"})
        r = fb.handle_message("c", "s", "/history")
        self.assertIn("最近", r)
        self.assertIn("hello", r)

    def test_reset(self):
        fb.save_message("c", {"text": "x"})
        r = fb.handle_message("c", "s", "/reset")
        self.assertIn("清掉", r)
        self.assertEqual(fb.load_history("c"), [])

    def test_normal_question_calls_bot(self):
        with patch("feishu_bot.call_cli", return_value="mocked answer") as mc:
            with patch.object(fb, "DEFAULT_CLI", "hermes"):
                r = fb.handle_message("c", "s", "什么是 API?")
                self.assertEqual(r, "mocked answer")
                self.assertEqual(mc.call_count, 1)
                # history 持久化
                hist = fb.load_history("c")
                self.assertEqual(hist[0]["text"], "什么是 API?")
                self.assertEqual(hist[0]["response"], "mocked answer")
                self.assertEqual(hist[0]["bot"], "hermes")

    def test_error_429_shows_token_plan_hint(self):
        """429 / Token Plan 错误必须显示真正原因, 不只是 exit code"""
        with patch.object(fb, "DEFAULT_CLI", "hermes"):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value.returncode = 1
                mock_run.return_value.stdout = "API call failed after 3 retries: HTTP 429: 已达到 Token Plan 用量上限 (2056)"
                mock_run.return_value.stderr = ""
                r = fb.handle_message("c", "s", "什么是 API?")  # 非 ping/help, 走 call_cli
                self.assertIn("Token Plan", r)
                self.assertIn("用量上限", r)
                self.assertIn("额度耗尽", r)  # 中文 hint
                # 不能只显示 exit code
                self.assertNotIn("[错误] CLI 1: ", r[:20])

    def test_error_401_shows_key_hint(self):
        """401 / 失效 key 必须显示具体原因"""
        with patch.object(fb, "DEFAULT_CLI", "hermes"):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value.returncode = 1
                mock_run.return_value.stdout = "AuthenticationError: 401 unauthorized"
                mock_run.return_value.stderr = ""
                r = fb.handle_message("c", "s", "测试中文")
                self.assertIn("API Key 失效", r)
                self.assertIn("401", r)

    def test_windows_error_code_translated(self):
        """Windows 错误码 (如 3221225786) 应翻译成人话"""
        with patch.object(fb, "DEFAULT_CLI", "hermes"):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value.returncode = 3221225786  # 0xC000013A
                mock_run.return_value.stdout = ""
                mock_run.return_value.stderr = ""
                r = fb.handle_message("c", "s", "测试问题")
                self.assertIn("3221225786", r)
                self.assertIn("Windows", r)


if __name__ == "__main__":
    unittest.main(verbosity=2)
