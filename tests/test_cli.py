"""命令行入口与打包相关元数据的单元测试（不需要图形环境）。"""
from __future__ import annotations

import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import main as entry  # noqa: E402
from src import __app_name__, __version__  # noqa: E402


class TestVersionCommand(unittest.TestCase):
    def test_prints_version_and_exits_zero(self) -> None:
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = entry.main(["--version"])
        self.assertEqual(code, 0)
        self.assertEqual(buffer.getvalue().strip(), __version__)

    def test_version_format_is_semver(self) -> None:
        parts = __version__.split(".")
        self.assertEqual(len(parts), 3)
        self.assertTrue(all(p.isdigit() for p in parts), __version__)

    def test_title_embeds_version(self) -> None:
        """窗口标题带版本号，便于确认同事用的是哪一版。"""
        from src.app import APP_TITLE

        self.assertIn("SQL Server", APP_TITLE)
        self.assertIn(__version__, APP_TITLE)


class TestAppNameCommand(unittest.TestCase):
    """打包脚本据此生成产物名与产物目录。"""

    def test_prints_app_name_and_exits_zero(self) -> None:
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = entry.main(["--app-name"])
        self.assertEqual(code, 0)
        self.assertEqual(buffer.getvalue().strip(), __app_name__)

    def test_app_name_has_no_version_or_extension(self) -> None:
        self.assertNotIn(__version__, __app_name__)
        self.assertFalse(__app_name__.endswith(".exe"))
        self.assertNotIn(".", __app_name__)

    def test_app_name_is_filename_safe(self) -> None:
        for char in '<>:"/\\|?*':
            self.assertNotIn(char, __app_name__)


class TestAttachConsole(unittest.TestCase):
    def test_noop_when_not_frozen(self) -> None:
        """源码运行时已有标准输出，不需要附着控制台。"""
        self.assertFalse(getattr(sys, "frozen", False))
        self.assertFalse(entry.attach_console())


class TestSelfCheck(unittest.TestCase):
    def test_writes_log_and_exits_zero(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            buffer = io.StringIO()
            with (
                mock.patch("src.paths.project_root", lambda: base),
                contextlib.redirect_stdout(buffer),
            ):
                code = entry._self_check()

            self.assertEqual(code, 0)
            text = buffer.getvalue()
            self.assertIn(__version__, text)
            self.assertIn(str(base), text)

            log = base / "selfcheck.log"
            self.assertTrue(log.exists(), "应写出 selfcheck.log 供无控制台场景排查")
            self.assertIn(__version__, log.read_text(encoding="utf-8"))

    def test_reports_app_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            buffer = io.StringIO()
            with (
                mock.patch("src.paths.project_root", lambda: Path(tmp)),
                contextlib.redirect_stdout(buffer),
            ):
                entry._self_check()
            self.assertIn(__app_name__, buffer.getvalue())

    def test_reports_driver_and_profiles(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            buffer = io.StringIO()
            with (
                mock.patch("src.paths.project_root", lambda: base),
                mock.patch("src.drivers.list_available", return_value=["D1", "D2"]),
                contextlib.redirect_stdout(buffer),
            ):
                entry._self_check()
            text = buffer.getvalue()
            self.assertIn("D1、D2", text)
            self.assertIn("已存连接方案", text)

    def test_reports_failure_without_crashing(self) -> None:
        """环境探测失败时也要给出可读原因，而不是把自检本身搞崩。"""
        buffer = io.StringIO()
        with (
            mock.patch("src.paths.project_root", side_effect=OSError("拒绝访问")),
            contextlib.redirect_stdout(buffer),
        ):
            code = entry._self_check()
        self.assertEqual(code, 0)
        text = buffer.getvalue()
        self.assertIn(__version__, text)
        self.assertIn("环境探测失败", text)
        self.assertIn("拒绝访问", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
