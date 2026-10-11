"""界面自检：不进入 mainloop，直接构造窗口并多轮刷新验证布局与行为。

运行：
    <venv-python> tests/smoke_test.py
无图形环境时整体跳过。

注意：所有用例都把配置落盘路径隔离到临时目录，绝不触碰项目根目录下
真实的 config.json / connections.json。判定通过与否的隐藏哨兵是
**stderr 中不得出现** ``invalid command name ... while executing``。
"""
from __future__ import annotations

import json
import sys
import tempfile
import time
import tkinter as tk
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import config, drivers, engine, profiles  # noqa: E402
from src.app import RESULT_HINT, UI_ROW_LIMIT, FinderApp  # noqa: E402
from src.dialogs import (  # noqa: E402
    LAMP_DOT_COLORS,
    ConnectionDialog,
    RowDetailDialog,
)
from src.models import ColumnRef, Hit, HitStatus, MatchMode, ScanStats  # noqa: E402
from src.widgets import (  # noqa: E402
    MENU_COPY_ALL,
    MENU_COPY_CELL,
    MENU_COPY_COLUMN,
    MENU_COPY_ROW,
    SEL_CELL,
    SEL_COL,
    SEL_NONE,
    SEL_ROW,
    ResultTable,
)

MAIN_WINDOW_GROUPS = (config.GROUP_SEARCH, config.GROUP_ADVANCED)


class _Evt:
    """极简鼠标事件替身，用于定位单元格。"""

    def __init__(self, x: int, y: int) -> None:
        self.x = x
        self.y = y
        self.x_root = x
        self.y_root = y


def _hit(column: str, table: str = "Users", sample: str = "", status=HitStatus.MATCH) -> Hit:
    ref = ColumnRef("dbo", table, column, "BASE TABLE", "nvarchar")
    return Hit(ref, status, rows=1, sample=sample)


class _FakeCursor:
    def __init__(self, conn: "_FakeConnection") -> None:
        self._conn = conn
        self.description = None
        self.closed = False

    def execute(self, sql: str, params: tuple = ()) -> None:
        self._conn.sql = sql
        self._conn.params = params
        columns, rows = self._conn.handler(sql, params)
        self.description = [(name,) for name in columns]
        self.rows = rows

    def fetchall(self):
        return list(self.rows)

    def close(self) -> None:
        self.closed = True


class _FakeConnection:
    """供 RowDetailDialog 使用的假连接：handler 返回 (列名, 行)。"""

    def __init__(self, handler) -> None:
        self.handler = handler
        self.sql = ""
        self.params: tuple = ()
        self.closed = False

    def cursor(self) -> _FakeCursor:
        return _FakeCursor(self)

    def close(self) -> None:
        self.closed = True


class _TkTestCase(unittest.TestCase):
    """公共装配：隔离配置路径 + 构造主窗口 + 刷新。"""

    app: FinderApp

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        base = Path(self._tmp.name)
        for target, filename in (
            ("src.paths.config_path", "config.json"),
            ("src.paths.connections_path", "connections.json"),
            ("src.paths.legacy_config_path", "legacy_config.json"),
            ("src.paths.legacy_connections_path", "legacy_connections.json"),
        ):
            patcher = mock.patch(target, lambda _b=base, _f=filename: _b / _f)
            patcher.start()
            self.addCleanup(patcher.stop)

        try:
            self.app = FinderApp()
        except tk.TclError as exc:  # pragma: no cover - 无显示环境
            self.skipTest(f"无可用图形环境：{exc}")
        self.app.geometry("1360x860+20+10")
        self._flush()

    def _flush(self, rounds: int = 3) -> None:
        for _ in range(rounds):
            self.app.update()
            self.app.update_idletasks()

    def _wait_for(self, event, timeout: float = 5.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if event.is_set():
                return True
            self.app.update()
            time.sleep(0.01)
        return event.is_set()

    def tearDown(self) -> None:
        app = getattr(self, "app", None)
        if app is not None:
            try:
                app.destroy()
            except tk.TclError:
                pass


class TestMainWindowLayout(_TkTestCase):
    # ---------------------------------------------------------- 构造

    def test_title(self) -> None:
        self.assertIn("SQL Server", self.app.title())

    def test_connection_fields_are_not_on_main_window(self) -> None:
        """需求：连接设置不直接显示在主窗口。"""
        expected = {f.key for g in MAIN_WINDOW_GROUPS for f in config.fields_of(g)}
        self.assertEqual(set(self.app.rows), expected)
        for spec in config.CONNECTION_FIELDS:
            self.assertNotIn(spec.key, self.app.rows, spec.key)

    def test_remember_password_not_on_main_window(self) -> None:
        """需求：记住密码随测试连接一起进弹窗。"""
        self.assertNotIn("remember_password", self.app.rows)

    def test_search_and_advanced_fields_present(self) -> None:
        for spec in config.fields_of(config.GROUP_SEARCH):
            self.assertIn(spec.key, self.app.rows)
        for spec in config.fields_of(config.GROUP_ADVANCED):
            self.assertIn(spec.key, self.app.rows)

    # ---------------------------------------------------------- 布局可见性

    def test_scroll_container_visible(self) -> None:
        self.assertEqual(self.app.scroll.winfo_ismapped(), 1)
        self.assertGreater(self.app.scroll.winfo_width(), 300)
        self.assertGreater(self.app.scroll.winfo_height(), 300)

    def test_toolbar_has_no_save_or_test_buttons(self) -> None:
        """需求：移除「保存设置」「测试连接」按钮。"""
        self.assertFalse(hasattr(self.app, "btn_save"))
        self.assertFalse(hasattr(self.app, "btn_test"))
        for button in (self.app.btn_start, self.app.btn_cancel):
            self.assertEqual(button.winfo_ismapped(), 1)
            self.assertGreater(button.winfo_height(), 10)

    def test_statusbar_visible(self) -> None:
        """底栏不可见是本项目最典型的 pack 顺序陷阱。"""
        self.assertEqual(self.app.progress.winfo_ismapped(), 1)
        self.assertGreater(self.app.progress.winfo_height(), 8)

    def test_paned_has_height_and_sash(self) -> None:
        self.assertGreater(self.app.paned.winfo_height(), 200)
        self.assertGreaterEqual(self.app.paned.sashpos(0), 380)

    def test_result_table_and_log_visible(self) -> None:
        self.assertEqual(self.app.result_table.winfo_ismapped(), 1)
        self.app.notebook.select(1)
        self._flush(3)
        self.assertEqual(self.app.log.winfo_ismapped(), 1)
        self.app.notebook.select(0)
        self._flush(3)
        self.assertEqual(self.app.result_table.winfo_ismapped(), 1)

    def test_profile_box_visible(self) -> None:
        self.assertEqual(self.app.profile_combo.winfo_ismapped(), 1)
        self.assertGreater(self.app.profile_combo.winfo_width(), 100)

    def test_open_file_buttons_visible(self) -> None:
        """需求：路径文字改为打开对应文件的按钮。"""
        for button in (self.app.btn_open_settings, self.app.btn_open_connections):
            self.assertEqual(button.winfo_ismapped(), 1)
            self.assertGreater(button.winfo_width(), 40)

    def test_result_hint_visible(self) -> None:
        self.assertEqual(self.app.result_hint_var.get(), RESULT_HINT)

    def test_scroll_areas_reachable(self) -> None:
        self.assertGreater(self.app.scroll.body.winfo_reqheight(), 0)
        self.assertEqual(self.app.scroll.vscroll.winfo_ismapped(), 1)

    # ---------------------------------------------------------- 事件处理

    def test_stage_a_event_populates_table_when_content_disabled(self) -> None:
        self.app.result_table.clear()
        self.app._content_enabled = False
        targets = [ColumnRef("dbo", "T", f"C{i}") for i in range(3)]
        self.app._handle_event(engine.StageAEvent(targets=targets, total_columns=10))
        self.assertEqual(self.app.result_table.count, 3)

    def test_hit_event_respects_ui_row_limit(self) -> None:
        self.app.result_table.clear()
        self.app._ui_rows = UI_ROW_LIMIT
        self.app._handle_event(engine.HitEvent(_hit("C")))
        self.assertEqual(self.app.result_table.count, 0)
        self.assertIn("上限", self.app.log.value())

    def test_done_event_updates_status(self) -> None:
        stats = ScanStats(total_targets=4, finished=4, matched=1, elapsed=1.23)
        self.app._handle_event(engine.DoneEvent(stats))
        text = self.app.status_var.get()
        self.assertIn("完成", text)
        self.assertIn("命中 1", text)
        self.assertEqual(str(self.app.btn_start.cget("state")), "normal")

    def test_cancelled_done_event(self) -> None:
        stats = ScanStats(total_targets=10, finished=3, matched=0, cancelled=True)
        self.app._handle_event(engine.DoneEvent(stats))
        self.assertIn("已取消", self.app.status_var.get())

    def test_progress_event_drives_bar(self) -> None:
        self.app._handle_event(engine.ProgressEvent(ScanStats(total_targets=10, finished=5)))
        self.assertEqual(int(self.app.progress.cget("maximum")), 10)
        self.assertEqual(int(self.app.progress.cget("value")), 5)

    def test_failed_scan_reports_error_summary(self) -> None:
        self.app._handle_event(engine.DoneEvent(ScanStats(total_targets=0, failed=1)))
        self.assertIn("异常", self.app.status_var.get())

    # ---------------------------------------------------------- 校验

    def test_start_without_profile_warns(self) -> None:
        self.app.rows["field_keyword"].set("UserID")
        with mock.patch("src.app.messagebox.showwarning") as warn:
            self.app._on_start()
        self.assertTrue(warn.called)
        self.assertFalse(self.app._running)

    def test_start_with_empty_keyword_warns(self) -> None:
        self.app._store.upsert("生产库", {"server": "10.0.0.8", "database": "d"})
        self.app._refresh_profile_combo("生产库")
        self.app.rows["field_keyword"].set("")
        with mock.patch("src.app.messagebox.showwarning") as warn:
            self.app._on_start()
        self.assertTrue(warn.called)
        self.assertIn("字段名关键词", warn.call_args[0][1])

    def test_integer_problems_detects_garbage(self) -> None:
        self.app.rows["max_workers"].set("八个")
        self.assertTrue(any("并发线程数" in p for p in self.app._integer_problems()))

    def test_collect_request_maps_modes(self) -> None:
        self.app.rows["field_mode"].set("精准")
        self.app.rows["content_mode"].set("模糊")
        self.app.rows["max_workers"].set("12")
        self.app.rows["ignore_case"].set(False)
        request = self.app._collect_request()
        self.assertIs(request.field_mode, MatchMode.EXACT)
        self.assertIs(request.content_mode, MatchMode.FUZZY)
        self.assertEqual(request.max_workers, 12)
        self.assertFalse(request.ignore_case)

    def test_collect_profile_reads_from_store(self) -> None:
        self.app._store.upsert(
            "生产库",
            {
                "server": "10.0.0.8",
                "database": "ERP",
                "auth": config.DEFAULT_AUTH_LABEL,
                "username": "sa",
                "password": "p",
                "port": 1433,
                "connect_timeout": 15,
                "query_timeout": 45,
            },
        )
        self.app._refresh_profile_combo("生产库")
        profile = self.app._collect_profile()
        self.assertEqual(profile.server, "10.0.0.8")
        self.assertEqual(profile.port, 1433)
        self.assertEqual(profile.connect_timeout, 15)
        self.assertEqual(profile.query_timeout, 45)

    def test_collect_profile_without_selection_is_blank(self) -> None:
        self.app._store = profiles.ProfileStore()
        self.app._refresh_profile_combo()
        profile = self.app._collect_profile()
        self.assertEqual(profile.server, "")
        self.assertEqual(profile.driver, "")

    def test_job_counter_never_goes_negative(self) -> None:
        self.app._end_job()
        self.app._end_job()
        self.assertEqual(self.app._active_jobs, 0)

    def test_close_cancels_pending_timer(self) -> None:
        self.app._after_id = None
        self.app._on_close()
        destroyed = False
        try:
            self.app.winfo_exists()
        except tk.TclError:
            destroyed = True  # 解释器已随窗口释放，正是期望结果
        self.assertTrue(destroyed, "窗口未被真正销毁")

    # ---------------------------------------------------------- 双击查看明细

    def _prepare_detail(self, content_enabled: bool = True) -> ColumnRef:
        ref = ColumnRef("dbo", "Users", "UserName", "BASE TABLE", "nvarchar")
        self.app.rows["run_content"].set(content_enabled)
        self.app.rows["content_keyword"].set("zhang")
        self.app._last_request = self.app._collect_request()
        self.app._store.upsert(
            "生产库",
            {"server": "h", "database": "d", "username": "sa", "password": "p"},
        )
        self.app._refresh_profile_combo("生产库")
        return ref

    def test_double_click_does_nothing_when_content_disabled(self) -> None:
        """需求：未启用内容检索时，双击不做任何动作。"""
        ref = self._prepare_detail(content_enabled=False)
        with mock.patch("src.app.RowDetailDialog") as dialog:
            self.app._on_open_row_detail(ref)
        self.assertFalse(dialog.called)
        self.assertEqual(self.app.result_hint_var.get(), RESULT_HINT)

    def test_double_click_opens_detail_when_enabled(self) -> None:
        if not drivers.detect_sql_server_driver(drivers.list_available()):
            self.skipTest("本机无可用 ODBC 驱动")
        ref = self._prepare_detail(content_enabled=True)
        with mock.patch("src.app.RowDetailDialog") as dialog:
            self.app._on_open_row_detail(ref)
        self.assertTrue(dialog.called)
        kwargs = dialog.call_args.kwargs
        self.assertEqual(kwargs["ref"], ref)
        self.assertEqual(kwargs["request"].content_keyword, "zhang")

    def test_double_click_without_scan_prompts(self) -> None:
        ref = self._prepare_detail(content_enabled=True)
        self.app._last_request = None
        with mock.patch("src.app.RowDetailDialog") as dialog:
            self.app._on_open_row_detail(ref)
        self.assertFalse(dialog.called)
        self.assertIn("请先执行一次检索", self.app.result_hint_var.get())

    def test_double_click_without_profile_prompts(self) -> None:
        ref = ColumnRef("dbo", "Users", "UserName")
        self.app.rows["run_content"].set(True)
        self.app._last_request = self.app._collect_request()
        self.app._store = profiles.ProfileStore()
        self.app._refresh_profile_combo()
        with mock.patch("src.app.RowDetailDialog") as dialog:
            self.app._on_open_row_detail(ref)
        self.assertFalse(dialog.called)
        self.assertIn("连接方案", self.app.result_hint_var.get())

    # ---------------------------------------------------------- 配置方案

    def test_profile_combo_starts_empty(self) -> None:
        self.assertEqual(self.app.profile_var.get(), "")
        self.assertEqual(list(self.app.profile_combo.cget("values")), [])

    def test_new_profile_via_dialog(self) -> None:
        result = {
            "name": "生产库",
            "values": {"server": "10.0.0.8", "database": "ERP"},
            "remember_password": True,
        }
        with mock.patch.object(self.app, "_run_dialog", return_value=result):
            self.app._on_profile_new()
        self.assertEqual(self.app.profile_var.get(), "生产库")
        self.assertIn("生产库", list(self.app.profile_combo.cget("values")))
        self.assertEqual(self.app._store.get("生产库")["server"], "10.0.0.8")

    def test_new_profile_carries_remember_password(self) -> None:
        result = {
            "name": "生产库",
            "values": {"server": "10.0.0.8", "database": "ERP"},
            "remember_password": False,
        }
        with mock.patch.object(self.app, "_run_dialog", return_value=result):
            self.app._on_profile_new()
        self.assertFalse(self.app._store.remember_for("生产库"))

    def test_new_profile_cancelled(self) -> None:
        with mock.patch.object(self.app, "_run_dialog", return_value=None):
            self.app._on_profile_new()
        self.assertEqual(self.app._store.names(), [])

    def test_new_profile_overwrite_cancelled(self) -> None:
        self.app._store.upsert("生产库", {"server": "1.1.1.1"})
        self.app._refresh_profile_combo("生产库")
        result = {
            "name": "生产库",
            "values": {"server": "9.9.9.9"},
            "remember_password": True,
        }
        with (
            mock.patch.object(self.app, "_run_dialog", return_value=result),
            mock.patch("src.app.messagebox.askyesno", return_value=False),
        ):
            self.app._on_profile_new()
        self.assertEqual(self.app._store.get("生产库")["server"], "1.1.1.1")

    def test_edit_profile_without_selection_warns(self) -> None:
        self.app.profile_var.set("")
        with mock.patch("src.app.messagebox.showwarning") as warn:
            self.app._on_profile_edit()
        self.assertTrue(warn.called)

    def test_edit_profile_updates_values(self) -> None:
        self.app._store.upsert("生产库", {"server": "1.1.1.1", "database": "A"})
        self.app._refresh_profile_combo("生产库")
        result = {
            "name": "生产库",
            "values": {"server": "2.2.2.2", "database": "B"},
            "remember_password": True,
        }
        with mock.patch.object(self.app, "_run_dialog", return_value=result):
            self.app._on_profile_edit()
        self.assertEqual(self.app._store.get("生产库")["server"], "2.2.2.2")
        self.assertEqual(self.app._store.get("生产库")["database"], "B")

    def test_edit_profile_renames(self) -> None:
        self.app._store.upsert("旧名", {"server": "1.1.1.1"})
        self.app._refresh_profile_combo("旧名")
        result = {
            "name": "新名",
            "values": {"server": "1.1.1.1"},
            "remember_password": True,
        }
        with mock.patch.object(self.app, "_run_dialog", return_value=result):
            self.app._on_profile_edit()
        self.assertFalse(self.app._store.has("旧名"))
        self.assertTrue(self.app._store.has("新名"))
        self.assertEqual(self.app.profile_var.get(), "新名")

    def test_edit_profile_cancelled_keeps_values(self) -> None:
        self.app._store.upsert("生产库", {"server": "1.1.1.1"})
        self.app._refresh_profile_combo("生产库")
        with mock.patch.object(self.app, "_run_dialog", return_value=None):
            self.app._on_profile_edit()
        self.assertEqual(self.app._store.get("生产库")["server"], "1.1.1.1")

    def test_switching_profile(self) -> None:
        self.app._store.upsert("A库", {"server": "1.1.1.1"})
        self.app._store.upsert("B库", {"server": "2.2.2.2"})
        self.app._refresh_profile_combo("A库")
        self.assertEqual(self.app._collect_profile().server, "1.1.1.1")
        self.app.profile_var.set("B库")
        self.app._on_profile_selected()
        self.assertEqual(self.app._collect_profile().server, "2.2.2.2")
        self.assertIn("已切换到连接方案", self.app.status_var.get())

    def test_delete_profile(self) -> None:
        self.app._store.upsert("临时库", {"server": "1.1.1.1"})
        self.app._refresh_profile_combo("临时库")
        with mock.patch("src.app.messagebox.askyesno", return_value=True):
            self.app._on_profile_delete()
        self.assertFalse(self.app._store.has("临时库"))
        self.assertEqual(self.app.profile_var.get(), "")

    def test_delete_profile_cancelled(self) -> None:
        self.app._store.upsert("保留库", {"server": "1.1.1.1"})
        self.app._refresh_profile_combo("保留库")
        with mock.patch("src.app.messagebox.askyesno", return_value=False):
            self.app._on_profile_delete()
        self.assertTrue(self.app._store.has("保留库"))

    def test_delete_switches_to_remaining_profile(self) -> None:
        self.app._store.upsert("甲", {"server": "1.1.1.1"})
        self.app._store.upsert("乙", {"server": "2.2.2.2"})
        self.app._refresh_profile_combo("乙")
        with mock.patch("src.app.messagebox.askyesno", return_value=True):
            self.app._on_profile_delete()
        self.assertEqual(self.app.profile_var.get(), "甲")
        self.assertEqual(self.app._collect_profile().server, "1.1.1.1")

    def test_profiles_persist_to_project_file(self) -> None:
        self.app._store.upsert("持久化库", {"server": "7.7.7.7", "database": "X"})
        self.app._refresh_profile_combo("持久化库")
        self.assertTrue(self.app._persist_profiles())
        on_disk = profiles.load()
        self.assertIn("持久化库", on_disk.names())
        self.assertEqual(on_disk.get("持久化库")["database"], "X")

    def test_password_not_persisted_when_not_remembered(self) -> None:
        self.app._store.upsert(
            "无密码库", {"server": "7.7.7.7", "password": "p"}, remember=False
        )
        self.app._refresh_profile_combo("无密码库")
        self.assertTrue(self.app._persist_profiles())
        self.assertEqual(profiles.load().get("无密码库")["password"], "")

    def test_password_persisted_when_remembered(self) -> None:
        self.app._store.upsert(
            "带密码库", {"server": "7.7.7.7", "password": "p"}, remember=True
        )
        self.app._refresh_profile_combo("带密码库")
        self.assertTrue(self.app._persist_profiles())
        self.assertEqual(profiles.load().get("带密码库")["password"], "p")

    def test_remember_is_per_profile(self) -> None:
        """需求：记住密码只影响当前方案。"""
        self.app._store.upsert(
            "生产库", {"server": "1.1.1.1", "password": "prod"}, remember=True
        )
        self.app._store.upsert(
            "测试库", {"server": "2.2.2.2", "password": "test"}, remember=False
        )
        self.assertTrue(self.app._persist_profiles())

        loaded = profiles.load()
        self.assertTrue(loaded.remember_for("生产库"))
        self.assertFalse(loaded.remember_for("测试库"))
        self.assertEqual(loaded.get("生产库")["password"], "prod")
        self.assertEqual(loaded.get("测试库")["password"], "")

    def test_toggling_remember_does_not_touch_other_profiles(self) -> None:
        self.app._store.upsert(
            "生产库", {"server": "1.1.1.1", "password": "prod"}, remember=True
        )
        self.app._store.upsert(
            "测试库", {"server": "2.2.2.2", "password": "test"}, remember=True
        )
        self.assertTrue(self.app._persist_profiles())

        # 通过编辑弹窗把「生产库」的记住密码关掉
        result = {
            "name": "生产库",
            "values": {"server": "1.1.1.1", "password": "prod"},
            "remember_password": False,
        }
        self.app._refresh_profile_combo("生产库")
        with mock.patch.object(self.app, "_run_dialog", return_value=result):
            self.app._on_profile_edit()
        self.assertTrue(self.app._persist_profiles())

        loaded = profiles.load()
        self.assertEqual(loaded.get("生产库")["password"], "")
        self.assertEqual(loaded.get("测试库")["password"], "test")

    def test_edit_profile_prefills_its_own_remember_flag(self) -> None:
        self.app._store.upsert("A库", {"server": "1.1.1.1"}, remember=False)
        self.app._store.upsert("B库", {"server": "2.2.2.2"}, remember=True)

        for name, expected in (("A库", False), ("B库", True)):
            with self.subTest(profile=name):
                self.app._refresh_profile_combo(name)
                with (
                    mock.patch("src.app.ConnectionDialog") as dialog_class,
                    mock.patch.object(self.app, "wait_window"),
                ):
                    dialog_class.return_value.result = None
                    self.app._on_profile_edit()
                self.assertIs(
                    dialog_class.call_args.kwargs["remember_password"], expected
                )

    # ---------------------------------------------------------- 配置文件

    def test_open_settings_file_when_missing_opens_folder(self) -> None:
        with mock.patch("src.app.open_path") as opener:
            self.app._on_open_settings_file()
        self.assertEqual(opener.call_args[0][0], Path(self._tmp.name))
        self.assertIn("尚未生成", self.app.status_var.get())

    def test_open_settings_file_when_present(self) -> None:
        target = Path(self._tmp.name) / "config.json"
        target.write_text("{}", encoding="utf-8")
        with mock.patch("src.app.open_path", return_value=True) as opener:
            self.app._on_open_settings_file()
        self.assertEqual(opener.call_args[0][0], target)
        self.assertIn("已打开设置文件", self.app.status_var.get())

    def test_open_connections_file_when_present(self) -> None:
        self.app._store.upsert("A", {"server": "h"})
        self.assertTrue(self.app._persist_profiles())
        with mock.patch("src.app.open_path", return_value=True) as opener:
            self.app._on_open_connections_file()
        self.assertEqual(opener.call_args[0][0], Path(self._tmp.name) / "connections.json")

    def test_open_file_reports_failure(self) -> None:
        Path(self._tmp.name, "config.json").write_text("{}", encoding="utf-8")
        with (
            mock.patch("src.app.open_path", return_value=False),
            mock.patch("src.app.messagebox.showwarning") as warn,
        ):
            self.app._on_open_settings_file()
        self.assertTrue(warn.called)

    # ---------------------------------------------------------- 设置保存

    def test_quiet_save_writes_non_defaults(self) -> None:
        self.app.rows["max_targets"].set("9999")
        self.app._save_settings_quietly()
        raw = json.loads(Path(self._tmp.name, "config.json").read_text(encoding="utf-8"))
        self.assertEqual(raw, {"max_targets": 9999})

    def test_quiet_save_creates_no_file_when_defaults(self) -> None:
        self.app._save_settings_quietly()
        self.assertFalse(Path(self._tmp.name, "config.json").exists())

    def test_worker_default_matches_cpu_count(self) -> None:
        """需求：并发线程数默认值按本机核数给出。"""
        self.assertEqual(
            config.default_workers(), int(self.app.rows["max_workers"].get())
        )

    def test_worker_default_logged(self) -> None:
        self.assertIn("并发线程数按本机", self.app.log.value())
        self.assertIn(f"{config.default_workers()}", self.app.log.value())

    # ---------------------------------------------------------- 检索时写入设置

    def _ready_profile(self) -> None:
        self.app._store.upsert(
            "生产库",
            {"server": "10.0.0.8", "database": "d", "username": "sa", "password": "p"},
        )
        self.app._refresh_profile_combo("生产库")

    def test_start_writes_search_conditions_to_settings(self) -> None:
        """需求：每次点击开始检索后，自动把检索条件写入设置文件。"""
        self._ready_profile()
        self.app.rows["field_keyword"].set("OrderID")
        self.app.rows["content_keyword"].set("zhang")
        self.app.rows["field_mode"].set("精准")
        self.app.rows["max_targets"].set("9999")

        # 让连接工厂失败以便在真正扫描前中断，从而只验证「已落盘」这一行为
        with mock.patch(
            "src.app.engine.build_connection_factory", side_effect=RuntimeError("stop")
        ):
            self.app._on_start()

        raw = json.loads(Path(self._tmp.name, "config.json").read_text(encoding="utf-8"))
        self.assertEqual(raw["field_keyword"], "OrderID")
        self.assertEqual(raw["content_keyword"], "zhang")
        self.assertEqual(raw["field_mode"], "精准")
        self.assertEqual(raw["max_targets"], 9999)

    def test_start_does_not_write_when_validation_fails(self) -> None:
        self._ready_profile()
        self.app.rows["field_keyword"].set("")
        with mock.patch("src.app.messagebox.showwarning"):
            self.app._on_start()
        self.assertFalse(Path(self._tmp.name, "config.json").exists())

    def test_keywords_always_start_empty(self) -> None:
        """需求：字段名关键词与内容关键词默认为空（不读回上次输入）。"""
        self.app.rows["field_keyword"].set("OrderID")
        self.app.rows["content_keyword"].set("zhang")
        self.app._save_settings_quietly()
        self.app.destroy()

        self.app = FinderApp()
        self.app.geometry("1360x860+20+10")
        self._flush()
        self.assertEqual(self.app.rows["field_keyword"].get(), "")
        self.assertEqual(self.app.rows["content_keyword"].get(), "")

    def test_available_driver_is_detected(self) -> None:
        available = drivers.list_available()
        if not available:
            self.skipTest("本机无 ODBC 驱动")
        self.assertTrue(drivers.detect_sql_server_driver(available))


class TestGridSelection(_TkTestCase):
    """单元格 / 整行 / 整列三种互斥选中态，以及由此派生的复制行为。"""

    def _seed(self, columns=("UserName", "OrderNo")) -> list[str]:
        table = self.app.result_table
        table.clear()
        for name in columns:
            table.add_hit(_hit(name, sample=f"{name}-示例"))
        self._flush(3)
        return list(table.tree.get_children())

    def _cell_event(self, iid: str, key: str) -> _Evt:
        box = self.app.result_table.tree.bbox(iid, key)
        self.assertIsNotNone(box, "单元格未渲染，无法定位")
        x, y, width, height = box  # type: ignore[misc]
        return _Evt(x + width // 2, y + height // 2)

    # ---- 列结构 ----
    def test_blank_control_column_is_first(self) -> None:
        self.assertEqual(ResultTable.COLUMN_KEYS[0], ResultTable.SEL_COLUMN)
        self.assertEqual(ResultTable.COLUMN_TITLES[0], "")
        self.assertEqual(ResultTable.COLUMN_KEYS[1], "idx")

    def test_control_column_excluded_from_exports(self) -> None:
        self.assertNotIn(ResultTable.SEL_COLUMN, ResultTable.EXPORT_KEYS)
        self.assertEqual(ResultTable.EXPORT_KEYS[0], "idx")

    def test_control_column_is_rendered(self) -> None:
        iids = self._seed()
        self.assertIsNotNone(
            self.app.result_table.tree.bbox(iids[0], ResultTable.SEL_COLUMN)
        )

    # ---- 单元格选中 ----
    def test_click_data_cell_selects_only_cell(self) -> None:
        iids = self._seed()
        table = self.app.result_table
        table._on_button1(self._cell_event(iids[0], "column"))
        sel = table.selection
        self.assertEqual(sel.kind, SEL_CELL)
        self.assertEqual(sel.row, iids[0])
        self.assertEqual(sel.col, "column")

    def test_cell_selection_clears_native_row_highlight(self) -> None:
        iids = self._seed()
        table = self.app.result_table
        table.select_row(iids[0])
        self.assertTrue(table.tree.selection())
        table.select_cell(iids[0], "column")
        self.assertEqual(list(table.tree.selection()), [])
        self._flush()
        self.assertTrue(table._overlay.visible, "单元格选中应有边框高亮")

    def test_cell_overlay_frames_are_placed(self) -> None:
        iids = self._seed()
        table = self.app.result_table
        table.select_cell(iids[0], "column")
        top = table._overlay._frames[0].place_info()
        self.assertTrue(top, "边框未放置")
        self.assertGreater(int(top["width"]), 0)
        self.assertGreater(int(top["height"]), 0)

    def test_click_empty_area_clears_selection(self) -> None:
        iids = self._seed()
        table = self.app.result_table
        table.select_cell(iids[0], "column")
        table._on_button1(_Evt(60, table.tree.winfo_height() - 2))
        self.assertEqual(table.selection.kind, SEL_NONE)
        self.assertEqual(list(table.tree.selection()), [])

    # ---- 行选中 ----
    def test_click_control_column_selects_whole_row(self) -> None:
        iids = self._seed()
        table = self.app.result_table
        table._on_button1(self._cell_event(iids[1], ResultTable.SEL_COLUMN))
        sel = table.selection
        self.assertEqual(sel.kind, SEL_ROW)
        self.assertEqual(sel.row, iids[1])
        self.assertEqual(sel.col, "")
        self.assertEqual(list(table.tree.selection()), [iids[1]])

    def test_row_selection_hides_overlay(self) -> None:
        iids = self._seed()
        table = self.app.result_table
        table.select_row(iids[0])
        self.assertFalse(table._overlay.visible)

    # ---- 列选中 ----
    def test_click_heading_selects_whole_column(self) -> None:
        self._seed()
        table = self.app.result_table
        table._on_heading("column")
        sel = table.selection
        self.assertEqual(sel.kind, SEL_COL)
        self.assertEqual(sel.col, "column")
        self.assertEqual(sel.row, "")

    def test_column_selection_clears_native_row_highlight(self) -> None:
        iids = self._seed()
        table = self.app.result_table
        table.select_row(iids[0])
        table._on_heading("column")
        self.assertEqual(list(table.tree.selection()), [])
        self._flush()
        self.assertTrue(table._overlay.visible, "整列选中应有边框高亮")

    def test_column_overlay_spans_visible_height(self) -> None:
        self._seed()
        table = self.app.result_table
        table._on_heading("column")
        frames = table._overlay._frames
        top = frames[0].place_info()    # 上边框：宽 = 列宽，高 = 线宽
        left = frames[2].place_info()   # 左边框：宽 = 线宽，高 = 整列可视高度
        self.assertGreater(int(top["width"]), 20)
        self.assertGreater(int(left["height"]), 20)
        self.assertGreater(int(top["width"]), int(left["width"]))

    def test_heading_of_control_column_is_ignored(self) -> None:
        self._seed()
        table = self.app.result_table
        table._on_heading(ResultTable.SEL_COLUMN)
        self.assertEqual(table.selection.kind, SEL_NONE)

    # ---- 互斥规则 ----
    def test_row_and_cell_are_mutually_exclusive(self) -> None:
        iids = self._seed()
        table = self.app.result_table
        table.select_row(iids[0])
        table.select_cell(iids[1], "column")
        self.assertEqual(table.selection.kind, SEL_CELL)
        self.assertEqual(list(table.tree.selection()), [])

    def test_column_and_row_are_mutually_exclusive(self) -> None:
        iids = self._seed()
        table = self.app.result_table
        table.select_column("column")
        table.select_row(iids[0])
        self.assertEqual(table.selection.kind, SEL_ROW)
        self.assertFalse(table._overlay.visible)

    def test_cell_and_column_are_mutually_exclusive(self) -> None:
        iids = self._seed()
        table = self.app.result_table
        table.select_cell(iids[0], "column")
        table.select_column("table")
        self.assertEqual(table.selection.kind, SEL_COL)
        self.assertEqual(table.selection.col, "table")
        self.assertEqual(table.selection.row, "")

    def test_only_one_selection_kind_at_a_time(self) -> None:
        iids = self._seed()
        table = self.app.result_table
        table.select_cell(iids[0], "column")
        table.select_row(iids[0])
        table.select_column("status")
        sel = table.selection
        self.assertEqual(sel.kind, SEL_COL)
        self.assertEqual((sel.row, sel.col), ("", "status"))

    # ---- 复制 ----
    def test_every_data_field_is_copyable(self) -> None:
        iids = self._seed()
        table = self.app.result_table
        for key in ResultTable.DATA_KEYS:
            with self.subTest(key=key):
                expected = table.cell_value(iids[0], key)
                self.assertEqual(table.copy_cell(iids[0], key), expected)
                self.assertEqual(self.app.clipboard_get(), expected)

    def test_copy_row_excludes_control_column(self) -> None:
        iids = self._seed()
        text = self.app.result_table.copy_row(iids[0])
        self.assertEqual(len(text.split("\t")), len(ResultTable.EXPORT_KEYS))
        self.assertTrue(text.startswith("1\t"))  # 序号在首位，空白列已被剔除

    def test_copy_column_includes_header(self) -> None:
        self._seed()
        text = self.app.result_table.copy_column("column")
        self.assertEqual(text.split("\n"), ["字段", "UserName", "OrderNo"])

    def test_copy_all_includes_header(self) -> None:
        self._seed()
        self.assertEqual(len(self.app.result_table.copy_all().split("\n")), 3)

    def test_copy_cell_on_control_column_is_refused(self) -> None:
        iids = self._seed()
        self.assertEqual(
            self.app.result_table.copy_cell(iids[0], ResultTable.SEL_COLUMN), ""
        )
        self.assertIn("数据单元格", self.app.result_hint_var.get())

    def test_copy_row_and_column_without_target_prompt(self) -> None:
        table = self.app.result_table
        table.clear()
        self.assertEqual(table.copy_row(), "")
        self.assertIn("选中一行", self.app.result_hint_var.get())
        self.assertEqual(table.copy_column(), "")
        self.assertIn("选中一列", self.app.result_hint_var.get())

    def test_copy_all_on_empty_table_prompts(self) -> None:
        self.app.result_table.clear()
        self.assertEqual(self.app.result_table.copy_all(), "")
        self.assertIn("结果为空", self.app.result_hint_var.get())

    # ---- Ctrl+C 分派 ----
    def test_ctrl_c_copies_selected_cell(self) -> None:
        iids = self._seed()
        table = self.app.result_table
        table.select_cell(iids[0], "table")
        table._on_ctrl_c(None)
        self.assertEqual(self.app.clipboard_get(), "Users")

    def test_ctrl_c_copies_selected_row(self) -> None:
        iids = self._seed()
        table = self.app.result_table
        table.select_row(iids[0])
        table._on_ctrl_c(None)
        self.assertEqual(
            len(self.app.clipboard_get().split("\t")), len(ResultTable.EXPORT_KEYS)
        )

    def test_ctrl_c_copies_selected_column(self) -> None:
        self._seed()
        table = self.app.result_table
        table.select_column("column")
        table._on_ctrl_c(None)
        self.assertEqual(
            self.app.clipboard_get().split("\n"), ["字段", "UserName", "OrderNo"]
        )

    def test_ctrl_c_without_selection_prompts(self) -> None:
        self._seed()
        table = self.app.result_table
        table.clear_selection()
        self.assertEqual(table.copy_current(), "")
        self.assertIn("没有选中对象", self.app.result_hint_var.get())

    def test_copy_current_kind_dispatch(self) -> None:
        iids = self._seed()
        table = self.app.result_table
        table.select_cell(iids[0], "column")
        self.assertEqual(table.copy_current(), "UserName")
        table.select_row(iids[1])
        self.assertTrue(table.copy_current().startswith("2\t"))
        table.select_column("status")
        self.assertEqual(table.copy_current().split("\n")[0], "状态")

    # ---- 双击：改为查看明细 ----
    def test_double_click_delegates_to_detail_opener(self) -> None:
        seen: list = []
        self.app.result_table._open_detail = seen.append
        iids = self._seed()
        self.app.result_table._on_double_click(self._cell_event(iids[0], "table"))
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0].table, "Users")
        self.assertEqual(seen[0].column, "UserName")

    def test_double_click_without_opener_does_nothing(self) -> None:
        self.app.result_table._open_detail = None
        iids = self._seed()
        result = self.app.result_table._on_double_click(self._cell_event(iids[0], "table"))
        self.assertEqual(result, "break")

    def test_double_click_on_control_column_does_not_open_detail(self) -> None:
        seen: list = []
        self.app.result_table._open_detail = seen.append
        iids = self._seed()
        self.app.result_table._on_double_click(
            self._cell_event(iids[0], ResultTable.SEL_COLUMN)
        )
        self.assertEqual(seen, [])

    def test_ref_for_returns_row_target(self) -> None:
        iids = self._seed()
        ref = self.app.result_table.ref_for(iids[0])
        self.assertIsNotNone(ref)
        self.assertEqual(ref.schema, "dbo")
        self.assertEqual(ref.column, "UserName")
        self.app.result_table.clear()
        self.assertIsNone(self.app.result_table.ref_for(iids[0]))

    # ---- 右键菜单 ----
    def test_menu_has_exactly_four_items(self) -> None:
        menu = self.app.result_table._menu
        labels = [menu.entrycget(i, "label") for i in range(menu.index("end") + 1)]
        self.assertEqual(
            labels, [MENU_COPY_CELL, MENU_COPY_ROW, MENU_COPY_COLUMN, MENU_COPY_ALL]
        )

    def test_menu_states_on_cell(self) -> None:
        iids = self._seed()
        table = self.app.result_table
        table._on_right_click(self._cell_event(iids[0], "column"))
        for label in (MENU_COPY_CELL, MENU_COPY_ROW, MENU_COPY_COLUMN, MENU_COPY_ALL):
            self.assertEqual(str(table._menu.entrycget(label, "state")), "normal", label)

    def test_menu_states_on_control_column(self) -> None:
        iids = self._seed()
        table = self.app.result_table
        table._on_right_click(self._cell_event(iids[0], ResultTable.SEL_COLUMN))
        self.assertEqual(str(table._menu.entrycget(MENU_COPY_CELL, "state")), "disabled")
        self.assertEqual(str(table._menu.entrycget(MENU_COPY_ROW, "state")), "normal")
        self.assertEqual(
            str(table._menu.entrycget(MENU_COPY_COLUMN, "state")), "disabled"
        )

    def test_menu_states_on_empty_table(self) -> None:
        table = self.app.result_table
        table.clear()
        table._on_right_click(_Evt(60, 400))
        self.assertEqual(str(table._menu.entrycget(MENU_COPY_ALL, "state")), "disabled")

    def test_right_click_selects_cell_under_cursor(self) -> None:
        iids = self._seed()
        table = self.app.result_table
        table._on_right_click(self._cell_event(iids[1], "sample"))
        self.assertEqual(table.selection.kind, SEL_CELL)
        self.assertEqual(table._ctx_row, iids[1])
        self.assertEqual(table._ctx_col, "sample")

    # ---- 清理与滚动 ----
    def test_clear_resets_selection(self) -> None:
        iids = self._seed()
        table = self.app.result_table
        table.select_cell(iids[0], "column")
        table.clear()
        self.assertEqual(table.selection.kind, SEL_NONE)
        self.assertEqual(list(table.tree.selection()), [])
        self.assertEqual(table.count, 0)

    def test_row_index_sequential_after_clear(self) -> None:
        table = self.app.result_table
        table.add_hit(_hit("A"))
        table.clear()
        table.add_hit(_hit("B"))
        iid = table.tree.get_children()[0]
        self.assertEqual(table.cell_value(iid, "idx"), "1")

    def test_hint_resets_after_timeout(self) -> None:
        iids = self._seed()
        self.app.result_table.copy_cell(iids[0], "column")
        self.assertNotEqual(self.app.result_hint_var.get(), RESULT_HINT)
        self.app._reset_result_hint()
        self.assertEqual(self.app.result_hint_var.get(), RESULT_HINT)

    def test_scroll_hides_stale_overlay(self) -> None:
        table = self.app.result_table
        table.clear()
        for index in range(60):
            table.add_hit(_hit(f"C{index}"))
        self._flush(3)
        table.select_cell(table.tree.get_children()[0], "column")
        table._on_yscroll("moveto", "1.0")
        self._flush(3)
        # 目标滚出可视区后应隐藏高亮，而不是画在错误位置
        self.assertFalse(table._overlay.visible)


class TestConnectionDialog(_TkTestCase):
    """连接设置弹窗：字段承载、记住密码、测试连接指示灯、驱动检测提示、校验。"""

    def _open(self, **kwargs) -> ConnectionDialog:
        dialog = ConnectionDialog(self.app, **kwargs)
        self.addCleanup(self._safe_destroy, dialog)
        self._flush(3)
        return dialog

    def _safe_destroy(self, dialog: ConnectionDialog) -> None:
        try:
            dialog.destroy()
        except tk.TclError:
            pass

    def _fill(self, dialog: ConnectionDialog) -> None:
        dialog.name_var.set("生产库")
        dialog.rows["server"].set("10.0.0.8")
        dialog.rows["database"].set("ERP")
        dialog.rows["username"].set("sa")
        dialog.rows["password"].set("p")

    # ---- 字段承载 ----
    def test_holds_all_connection_fields(self) -> None:
        dialog = self._open(title="新增连接方案")
        self.assertEqual(set(dialog.rows), {f.key for f in config.CONNECTION_FIELDS})

    def test_does_not_hold_search_fields(self) -> None:
        dialog = self._open(title="新增连接方案")
        self.assertNotIn("field_keyword", dialog.rows)
        self.assertNotIn("max_workers", dialog.rows)

    def test_initial_values_loaded(self) -> None:
        dialog = self._open(
            title="编辑连接方案",
            name="生产库",
            initial={"server": "10.0.0.8", "database": "ERP", "port": 1433},
        )
        self.assertEqual(dialog.name_var.get(), "生产库")
        values = dialog.values()
        self.assertEqual(values["server"], "10.0.0.8")
        self.assertEqual(values["database"], "ERP")
        self.assertEqual(values["port"], 1433)

    # ---- 记住密码 ----
    def test_has_remember_password_row(self) -> None:
        dialog = self._open(title="新增连接方案")
        self.assertTrue(dialog._remember_row.winfo_ismapped())

    def test_remember_password_defaults_to_checked(self) -> None:
        dialog = self._open(title="新增连接方案")
        self.assertTrue(dialog.remember_password())

    def test_remember_password_respects_initial_flag(self) -> None:
        dialog = self._open(title="新增连接方案", remember_password=False)
        self.assertFalse(dialog.remember_password())

    def test_ok_returns_remember_password(self) -> None:
        dialog = self._open(title="新增连接方案", remember_password=False)
        self._fill(dialog)
        dialog._on_ok()
        self.assertIsNotNone(dialog.result)
        self.assertFalse(dialog.result["remember_password"])

    # ---- 确定 / 取消 ----
    def test_cancel_leaves_no_result(self) -> None:
        dialog = self._open(title="新增连接方案", name="X")
        dialog._on_cancel()
        self.assertIsNone(dialog.result)

    def test_ok_returns_name_and_values(self) -> None:
        dialog = self._open(title="新增连接方案")
        self._fill(dialog)
        dialog._on_ok()
        self.assertIsNotNone(dialog.result)
        self.assertEqual(dialog.result["name"], "生产库")
        self.assertEqual(dialog.result["values"]["server"], "10.0.0.8")

    def test_ok_blocks_empty_name(self) -> None:
        dialog = self._open(title="新增连接方案")
        dialog.rows["server"].set("10.0.0.8")
        dialog.rows["database"].set("ERP")
        with mock.patch("src.dialogs.messagebox.showwarning") as warn:
            dialog._on_ok()
        self.assertTrue(warn.called)
        self.assertIn("方案名称", warn.call_args[0][1])
        self.assertIsNone(dialog.result)

    def test_ok_blocks_empty_server_and_database(self) -> None:
        dialog = self._open(title="新增连接方案")
        dialog.name_var.set("生产库")
        with mock.patch("src.dialogs.messagebox.showwarning") as warn:
            dialog._on_ok()
        message = warn.call_args[0][1]
        self.assertIn("服务器", message)
        self.assertIn("数据库", message)

    def test_ok_blocks_bad_integer(self) -> None:
        dialog = self._open(title="新增连接方案")
        self._fill(dialog)
        dialog.rows["query_timeout"].set("三十")
        with mock.patch("src.dialogs.messagebox.showwarning") as warn:
            dialog._on_ok()
        self.assertIn("查询超时", warn.call_args[0][1])

    def test_incomplete_credentials_ask_for_confirmation(self) -> None:
        dialog = self._open(title="新增连接方案")
        dialog.name_var.set("生产库")
        dialog.rows["server"].set("10.0.0.8")
        dialog.rows["database"].set("ERP")
        with mock.patch("src.dialogs.messagebox.askyesno", return_value=False) as ask:
            dialog._on_ok()
        self.assertTrue(ask.called)
        self.assertIsNone(dialog.result)

    # ---- 测试连接指示灯 ----
    def test_test_connection_button_visible(self) -> None:
        dialog = self._open(title="新增连接方案")
        self.assertEqual(dialog.btn_test.winfo_ismapped(), 1)

    def test_lamp_starts_idle(self) -> None:
        dialog = self._open(title="新增连接方案")
        self.assertEqual(dialog.lamp.state, "idle")

    def test_test_connection_fails_on_missing_params(self) -> None:
        dialog = self._open(title="新增连接方案")
        dialog._on_test()
        self.assertEqual(dialog.lamp.state, "fail")
        self.assertIn("连接失败", dialog.lamp.text_var.get())

    def test_test_connection_fails_without_driver(self) -> None:
        with mock.patch("src.drivers.list_available", return_value=[]):
            dialog = self._open(title="新增连接方案")
        self._fill(dialog)
        dialog._on_test()
        self.assertEqual(dialog.lamp.state, "fail")
        self.assertIn("驱动", dialog.lamp.text_var.get())

    def test_lamp_text_wraps_inside_window(self) -> None:
        """需求：文案超出窗口宽度时自动换行。"""
        dialog = self._open(title="新增连接方案")
        wrap = int(dialog.lamp.label.cget("wraplength"))
        self.assertGreater(wrap, 160)
        self.assertLessEqual(wrap, dialog.winfo_width())

    def test_long_lamp_text_does_not_widen_dialog(self) -> None:
        dialog = self._open(title="新增连接方案")
        before = dialog.winfo_width()
        dialog.lamp.set(
            "ok",
            "测试成功。当前库：" + "A" * 80 + "；登录身份：" + "B" * 80,
        )
        self._flush(3)
        self.assertLessEqual(dialog.winfo_width(), before + 8)

    def test_test_connection_success_shows_green_lamp(self) -> None:
        dialog = self._open(title="新增连接方案")
        self._fill(dialog)
        with (
            mock.patch("src.dialogs.engine.build_connection_factory", return_value=lambda: None),
            mock.patch(
                "src.dialogs.engine.probe_server_info",
                return_value=engine.ServerInfo(
                    database="ERP", login="sa", version="Microsoft SQL Server 2019 (RTM)"
                ),
            ),
        ):
            dialog._on_test()
            self.assertEqual(dialog.lamp.state, "testing")
            self.assertTrue(self._wait_for(dialog._test_done))
            dialog._poll()
        self.assertEqual(dialog.lamp.state, "ok")
        self.assertEqual(dialog.lamp.text_var.get(), "测试成功。当前库：ERP；登录身份：sa")
        dot = dialog.lamp.canvas.itemcget(dialog.lamp.dot, "fill")
        self.assertEqual(dot.lower(), LAMP_DOT_COLORS["ok"].lower())
        self.assertEqual(str(dialog.btn_test.cget("state")), "normal")

    def test_success_message_omits_version(self) -> None:
        """需求：成功文案只需要显示结果、当前库、登录身份。"""
        dialog = self._open(title="新增连接方案")
        self._fill(dialog)
        with (
            mock.patch("src.dialogs.engine.build_connection_factory", return_value=lambda: None),
            mock.patch(
                "src.dialogs.engine.probe_server_info",
                return_value=engine.ServerInfo(
                    database="ERP", login="sa", version="Microsoft SQL Server 2019 (RTM)"
                ),
            ),
        ):
            dialog._on_test()
            self.assertTrue(self._wait_for(dialog._test_done))
            dialog._poll()
        text = dialog.lamp.text_var.get()
        self.assertNotIn("Microsoft", text)
        self.assertIn("当前库", text)
        self.assertIn("登录身份", text)

    def test_test_connection_failure_shows_red_lamp(self) -> None:
        dialog = self._open(title="新增连接方案")
        self._fill(dialog)
        with mock.patch(
            "src.dialogs.engine.build_connection_factory",
            side_effect=RuntimeError("Login failed for user 'sa'."),
        ):
            dialog._on_test()
            self.assertTrue(self._wait_for(dialog._test_done))
            dialog._poll()
        self.assertEqual(dialog.lamp.state, "fail")
        self.assertIn("连接失败", dialog.lamp.text_var.get())
        self.assertIn("Login failed", dialog.lamp.text_var.get())
        dot = dialog.lamp.canvas.itemcget(dialog.lamp.dot, "fill")
        self.assertEqual(dot.lower(), LAMP_DOT_COLORS["fail"].lower())

    def test_test_connection_disables_button_while_running(self) -> None:
        dialog = self._open(title="新增连接方案")
        self._fill(dialog)
        with (
            mock.patch("src.dialogs.engine.build_connection_factory", return_value=lambda: None),
            mock.patch("src.dialogs.engine.probe_server_info", return_value=engine.ServerInfo()),
        ):
            dialog._on_test()
            self.assertEqual(str(dialog.btn_test.cget("state")), "disabled")
            self.assertTrue(self._wait_for(dialog._test_done))

    def test_repeated_test_is_ignored_while_running(self) -> None:
        dialog = self._open(title="新增连接方案")
        self._fill(dialog)
        with (
            mock.patch("src.dialogs.engine.build_connection_factory", return_value=lambda: None),
            mock.patch("src.dialogs.engine.probe_server_info", return_value=engine.ServerInfo()),
        ):
            dialog._on_test()
            dialog._on_test()  # 第二次应被忽略，不应抛错
            self.assertTrue(self._wait_for(dialog._test_done))

    # ---- 驱动检测 ----
    def test_driver_combobox_populated_when_available(self) -> None:
        available = drivers.list_available()
        if not available:
            self.skipTest("本机无 ODBC 驱动")
        dialog = self._open(title="新增连接方案")
        self.assertEqual(list(dialog.rows["driver"].widget.cget("values")), available)
        self.assertIn(dialog.values()["driver"], available)
        self.assertIsNone(dialog.driver_warning)

    def test_driver_warning_shown_when_no_driver(self) -> None:
        with mock.patch("src.drivers.list_available", return_value=[]):
            dialog = self._open(title="新增连接方案")
        self.assertIsNotNone(dialog.driver_warning, "缺少驱动时应有红色提示")
        self.assertEqual(dialog.values()["driver"], "")

    def test_driver_warning_is_red_and_clickable(self) -> None:
        with mock.patch("src.drivers.list_available", return_value=[]):
            dialog = self._open(title="新增连接方案")
        label = dialog.driver_warning
        assert label is not None
        self.assertEqual(str(label.cget("foreground")).lower(), "#b91c1c")
        self.assertEqual(str(label.cget("cursor")), "hand2")
        self.assertIn("未检测到", str(label.cget("text")))

    def test_driver_warning_click_opens_download_page(self) -> None:
        with mock.patch("src.drivers.list_available", return_value=[]), mock.patch(
            "src.dialogs.webbrowser.open"
        ) as opener:
            dialog = self._open(title="新增连接方案")
            assert dialog.driver_warning is not None
            dialog.driver_warning.event_generate("<Button-1>")
            self.app.update()
            self.assertTrue(opener.called)
            self.assertEqual(opener.call_args[0][0], drivers.ODBC_DOWNLOAD_URL)

    def test_open_url_swallows_failures(self) -> None:
        from src import dialogs

        with mock.patch(
            "src.dialogs.webbrowser.open", side_effect=RuntimeError("boom")
        ):
            self.assertFalse(dialogs.open_url("https://example.invalid"))


class TestRowDetailDialog(_TkTestCase):
    """双击后展示的「命中数据」窗口。"""

    ref = ColumnRef("dbo", "Users", "UserName", "BASE TABLE", "nvarchar")

    def _request(self) -> object:
        self.app.rows["content_keyword"].set("zhang")
        self.app.rows["content_mode"].set("模糊")
        self.app.rows["ignore_case"].set(True)
        return self.app._collect_request()

    def _open(self, handler, **kwargs) -> RowDetailDialog:
        conn = _FakeConnection(handler)
        dialog = RowDetailDialog(
            self.app,
            ref=self.ref,
            request=kwargs.pop("request", self._request()),
            connect=kwargs.pop("connect", lambda: conn),
            **kwargs,
        )
        self.addCleanup(self._safe_destroy, dialog)
        self.conn = conn
        return dialog

    def _safe_destroy(self, dialog: RowDetailDialog) -> None:
        try:
            dialog.destroy()
        except tk.TclError:
            pass

    def test_window_title_contains_target(self) -> None:
        dialog = self._open(lambda _s, _p: (["ID"], [(1,)]))
        self.assertIn("dbo.Users.UserName", dialog.title())

    def test_condition_text_reflects_request(self) -> None:
        dialog = self._open(lambda _s, _p: (["ID"], [(1,)]))
        text = dialog._condition_text()
        self.assertIn("zhang", text)
        self.assertIn("模糊", text)
        self.assertIn("忽略大小写", text)

    def test_query_uses_same_predicate_as_scan(self) -> None:
        dialog = self._open(lambda _s, _p: (["ID"], [(1,)]))
        self.assertTrue(self._wait_for(dialog._done))
        dialog._poll()
        self.assertIn("SELECT TOP (200) *", self.conn.sql)
        self.assertIn("[dbo].[Users]", self.conn.sql)
        self.assertIn("LIKE", self.conn.sql)
        self.assertEqual(self.conn.params, ("%zhang%",))

    def test_rows_are_rendered_with_dynamic_columns(self) -> None:
        dialog = self._open(
            lambda _s, _p: (["UserID", "UserName"], [(1, "zhangsan"), (2, "zhangli")])
        )
        self.assertTrue(self._wait_for(dialog._done))
        dialog._poll()
        self.assertEqual(list(dialog.tree["columns"]), ["c0", "c1"])
        self.assertEqual(dialog.tree.heading("c0", "text"), "UserID")
        self.assertEqual(dialog.tree.heading("c1", "text"), "UserName")
        self.assertEqual(len(dialog.tree.get_children()), 2)
        self.assertIn("共 2 行", dialog.status_var.get())
        self.assertEqual(str(dialog.btn_copy.cget("state")), "normal")

    def test_empty_result_is_reported(self) -> None:
        dialog = self._open(lambda _s, _p: (["ID"], []))
        self.assertTrue(self._wait_for(dialog._done))
        dialog._poll()
        self.assertIn("未查到数据行", dialog.status_var.get())
        self.assertEqual(str(dialog.btn_copy.cget("state")), "disabled")

    def test_limit_reached_is_flagged(self) -> None:
        dialog = self._open(
            lambda _s, _p: (["ID"], [(i,) for i in range(3)]), limit=3
        )
        self.assertTrue(self._wait_for(dialog._done))
        dialog._poll()
        self.assertIn("已达上限", dialog.status_var.get())

    def test_query_error_is_shown_on_status(self) -> None:
        def handler(_sql, _params):
            raise RuntimeError("Invalid object name 'Users'")

        dialog = self._open(handler)
        self.assertTrue(self._wait_for(dialog._done))
        dialog._poll()
        self.assertIn("查询失败", dialog.status_var.get())
        self.assertIn("Invalid object name", dialog.status_var.get())

    def test_copy_all_produces_tsv(self) -> None:
        dialog = self._open(
            lambda _s, _p: (["UserID", "UserName"], [(1, "zhangsan"), (2, "zhangli")])
        )
        self.assertTrue(self._wait_for(dialog._done))
        dialog._poll()
        text = dialog.copy_all()
        self.assertEqual(
            text.split("\n"), ["UserID\tUserName", "1\tzhangsan", "2\tzhangli"]
        )
        self.assertEqual(self.app.clipboard_get(), text)
        self.assertIn("已复制 2 行", dialog.status_var.get())

    def test_copy_all_on_empty_result_returns_empty(self) -> None:
        dialog = self._open(lambda _s, _p: ([], []))
        self.assertTrue(self._wait_for(dialog._done))
        dialog._poll()
        self.assertEqual(dialog.copy_all(), "")


class TestProfileSeeding(unittest.TestCase):
    """历史 %APPDATA% 下的连接值应自动生成首个方案，避免升级后丢配置。"""

    def test_seed_from_legacy_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "legacy_config.json").write_text(
                '{"server": "10.0.0.8", "database": "ERP"}', encoding="utf-8"
            )
            with (
                mock.patch("src.paths.config_path", lambda: base / "config.json"),
                mock.patch(
                    "src.paths.connections_path", lambda: base / "connections.json"
                ),
                mock.patch(
                    "src.paths.legacy_config_path",
                    lambda: base / "legacy_config.json",
                ),
                mock.patch(
                    "src.paths.legacy_connections_path",
                    lambda: base / "legacy_connections.json",
                ),
            ):
                try:
                    app = FinderApp()
                except tk.TclError as exc:  # pragma: no cover
                    self.skipTest(f"无可用图形环境：{exc}")
                try:
                    app.update()
                    self.assertEqual(
                        app.profile_var.get(), profiles.DEFAULT_PROFILE_NAME
                    )
                    self.assertEqual(app._collect_profile().server, "10.0.0.8")
                finally:
                    try:
                        app.destroy()
                    except tk.TclError:
                        pass


if __name__ == "__main__":
    unittest.main(verbosity=2)
