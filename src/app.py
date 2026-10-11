"""主窗口：检索条件 + 配置方案 + 后台执行 + 结果与日志展示。

线程模型
--------
* 后台**单线程**负责调用 ``engine.run_scan``，所有工作线程由它派生；
* 后台线程只向 ``queue.Queue`` 投递事件，UI 通过 ``after(80ms)`` 轮询消费；
* Tk 控件只在主线程被访问；
* 窗口关闭时取消 ``after`` 定时器，避免 "invalid command name" 报错。

v1.4 变更：连接设置（含「记住密码」「测试连接」）整体移入弹窗；
双击结果单元格改为打开命中数据窗口；移除「保存设置」按钮，改为关闭时静默保存。
"""
from __future__ import annotations

import ctypes
import os
import queue
import sys
import threading
import time
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Any

from . import __version__, config, drivers, engine, paths, profiles
from .dialogs import ConnectionDialog, RowDetailDialog
from .models import ConnectionProfile, ScanStats, SearchRequest
from .widgets import (
    RESULT_HINT,
    COLOR_MUTED,
    LogPane,
    ResultTable,
    ScalarRow,
    ScrollFrame,
)

APP_TITLE = f"库表字段内容检索器 — SQL Server  v{__version__}"
POLL_INTERVAL_MS = 80
EVENTS_PER_TICK = 400
UI_ROW_LIMIT = 2000
HINT_RESET_MS = 4000


def enable_dpi_awareness() -> None:
    """声明 DPI 感知，避免高分屏下界面模糊。必须在创建窗口前调用。"""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:  # noqa: BLE001 - 非 Windows 或旧系统
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:  # noqa: BLE001
            pass


def open_path(path: Path | str) -> bool:
    """用系统默认程序打开文件或目录；失败返回 False。"""
    target = os.fspath(path)
    try:
        os.startfile(target)  # type: ignore[attr-defined]  # 仅 Windows
        return True
    except (AttributeError, OSError):
        pass
    try:
        return bool(webbrowser.open(Path(target).absolute().as_uri()))
    except Exception:  # noqa: BLE001
        return False


class FinderApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("1320x840")
        self.minsize(1100, 680)

        self._apply_scaling()

        self.rows: dict[str, ScalarRow] = {}
        self._events: "queue.Queue[object]" = queue.Queue()
        self._after_id: str | None = None
        self._hint_after_id: str | None = None
        self._cancel_event: threading.Event | None = None
        self._scan_thread: threading.Thread | None = None
        self._running = False
        self._active_jobs = 0
        self._stage_a_targets = []
        self._content_enabled = True
        self._ui_rows = 0
        self._store = profiles.ProfileStore()
        self._last_request: SearchRequest | None = None
        # 提示文案的换行宽度缓存：<Configure> 可能在构建过程中就触发，
        # 因此必须在建界面之前初始化。
        self._hint_wrap = 0
        self._sash_done = False

        self._build_toolbar()
        self._build_statusbar()
        self._build_body()

        self.status_var.set("就绪。请先在「配置方案」中新增并选择连接方案。")
        self.log.append("欢迎使用「库表字段内容检索器」。本工具仅执行只读查询。")
        self.log.append(
            f"版本 v{__version__}（{'打包运行' if getattr(sys, 'frozen', False) else '源码运行'}）"
            f"，配置目录：{paths.project_root()}"
        )
        self._log_environment()

        self._load_config()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.bind("<Destroy>", self._on_root_destroy)

    # ------------------------------------------------------------ 基础

    def _apply_scaling(self) -> None:
        try:
            scaling = self.winfo_fpixels("1i") / 72.0
        except tk.TclError:  # pragma: no cover
            return
        if 1.0 < scaling <= 2.0:
            self.tk.call("tk", "scaling", scaling)

    def _log_environment(self) -> None:
        available = drivers.list_available()
        if available:
            self.log.append(f"本机 ODBC 驱动：{'、'.join(available)}")
            return
        self.log.append(
            "未检测到任何 ODBC 驱动。请在「配置方案 → 新增 / 编辑」弹窗中"
            "点击下载链接安装 Microsoft ODBC Driver for SQL Server。",
            "error",
        )

    def _build_toolbar(self) -> None:
        """工具栏必须先 pack（非扩展区域），否则会被主体挤压。"""
        bar = ttk.Frame(self, padding=(10, 8))
        bar.pack(side="top", fill="x")

        self.btn_start = ttk.Button(bar, text="开始检索", command=self._on_start)
        self.btn_start.pack(side="left")
        self.btn_cancel = ttk.Button(
            bar, text="取消", command=self._on_cancel, state="disabled"
        )
        self.btn_cancel.pack(side="left", padx=(8, 0))

        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=12)

        ttk.Button(bar, text="清空结果", command=self._on_clear).pack(side="left")
        ttk.Button(bar, text="复制选中", command=self._on_copy).pack(
            side="left", padx=(8, 0)
        )

        ttk.Label(
            bar,
            text="只读检索 · 不修改任何数据",
            foreground=COLOR_MUTED,
        ).pack(side="right")

    def _build_statusbar(self) -> None:
        """状态栏同样属于非扩展区域，必须在主体之前 pack。"""
        bar = ttk.Frame(self, padding=(10, 6))
        bar.pack(side="bottom", fill="x")

        self.progress = ttk.Progressbar(bar, mode="determinate", length=240, maximum=100)
        self.progress.pack(side="left")

        self.status_var = tk.StringVar(value="")
        ttk.Label(bar, textvariable=self.status_var, anchor="w").pack(
            side="left", padx=(12, 0), fill="x", expand=True
        )

    def _build_body(self) -> None:
        self.paned = ttk.PanedWindow(self, orient="horizontal")
        self.paned.pack(side="top", fill="both", expand=True, padx=10, pady=(0, 8))

        self.scroll = ScrollFrame(self.paned)
        self._build_profile_box()
        for group in config.GROUPS:
            box = ttk.LabelFrame(self.scroll.body, text=group, padding=(10, 6))
            box.pack(fill="x", padx=6, pady=(8, 0))
            for spec in config.fields_of(group):
                row = ScalarRow(box, spec)
                row.pack(fill="x", pady=1)
                self.rows[spec.key] = row

        right = ttk.Frame(self.paned)
        notebook = ttk.Notebook(right)
        notebook.pack(fill="both", expand=True)
        self.notebook = notebook

        result_tab = ttk.Frame(notebook, padding=4)
        result_tab.rowconfigure(0, weight=1)
        result_tab.columnconfigure(0, weight=1)

        self.result_hint_var = tk.StringVar(value=RESULT_HINT)
        self.result_table = ResultTable(
            result_tab,
            on_status=self._set_result_hint,
            on_open_detail=self._on_open_row_detail,
        )
        self.result_table.grid(row=0, column=0, sticky="nsew")
        self.result_hint_label = ttk.Label(
            result_tab,
            textvariable=self.result_hint_var,
            foreground=COLOR_MUTED,
            anchor="w",
            justify="left",
        )
        self.result_hint_label.grid(row=1, column=0, sticky="ew", pady=(6, 0))
        result_tab.bind("<Configure>", self._on_result_tab_configure)
        notebook.add(result_tab, text="命中结果")

        log_tab = ttk.Frame(notebook, padding=4)
        self.log = LogPane(log_tab)
        self.log.pack(fill="both", expand=True)
        notebook.add(log_tab, text="运行日志")

        self.paned.add(self.scroll, weight=0)
        self.paned.add(right, weight=1)

        self._sash_done = False
        self.paned.bind("<Configure>", self._on_paned_configure)

    def _build_profile_box(self) -> None:
        """配置方案区：只负责「选哪套方案」，连接参数与测试都在弹窗内。"""
        box = ttk.LabelFrame(self.scroll.body, text="配置方案", padding=(10, 6))
        box.pack(fill="x", padx=6, pady=(8, 0))

        top = ttk.Frame(box)
        top.pack(fill="x")
        ttk.Label(top, text="当前方案", anchor="e", width=13).pack(
            side="left", padx=(0, 6)
        )
        self.profile_var = tk.StringVar(value="")
        self.profile_combo = ttk.Combobox(
            top, textvariable=self.profile_var, state="readonly", width=22
        )
        self.profile_combo.pack(side="left")
        self.profile_combo.bind("<<ComboboxSelected>>", self._on_profile_selected)

        actions = ttk.Frame(box)
        actions.pack(fill="x", pady=(6, 0))
        ttk.Label(actions, text="", width=13).pack(side="left", padx=(0, 6))
        ttk.Button(actions, text="新增", width=7, command=self._on_profile_new).pack(
            side="left"
        )
        ttk.Button(actions, text="编辑", width=7, command=self._on_profile_edit).pack(
            side="left", padx=(6, 0)
        )
        ttk.Button(actions, text="删除", width=7, command=self._on_profile_delete).pack(
            side="left", padx=(6, 0)
        )

        files = ttk.Frame(box)
        files.pack(fill="x", pady=(6, 0))
        ttk.Label(files, text="", width=13).pack(side="left", padx=(0, 6))
        self.btn_open_settings = ttk.Button(
            files, text="打开设置文件", width=12, command=self._on_open_settings_file
        )
        self.btn_open_settings.pack(side="left")
        self.btn_open_connections = ttk.Button(
            files,
            text="打开方案文件",
            width=12,
            command=self._on_open_connections_file,
        )
        self.btn_open_connections.pack(side="left", padx=(6, 0))

        ttk.Label(
            box,
            text=(
                "连接参数、记住密码与测试连接\n"
                "都在「新增 / 编辑」弹窗中完成。"
            ),
            foreground=COLOR_MUTED,
            wraplength=330,
            justify="left",
        ).pack(anchor="w", pady=(6, 0))

    def _on_paned_configure(self, event: tk.Event) -> None:
        """仅在窗口定型后设置一次分隔位置；用定时器会拿到尚未定型的尺寸。

        宽度取窗口的 38%：左侧表单是「标签 + 控件 + 提示」三列，
        太窄会把提示文字挤掉，太宽又会压缩结果表。
        """
        if self._sash_done or event.height < 200:
            return
        self._sash_done = True
        self.paned.sashpos(0, max(440, min(580, int(event.width * 0.38))))

    def _on_result_tab_configure(self, event: tk.Event) -> None:
        """让结果表下方的提示随可用宽度折行，而不是被截断。"""
        wrap = max(200, int(event.width) - 8)
        if wrap != self._hint_wrap:
            self._hint_wrap = wrap
            self.result_hint_label.configure(wraplength=wrap)

    # ------------------------------------------------------------ 结果提示

    def _set_result_hint(self, text: str) -> None:
        """在结果表下方给出一次性反馈，数秒后自动恢复操作提示。"""
        self.result_hint_var.set(text)
        if self._hint_after_id is not None:
            try:
                self.after_cancel(self._hint_after_id)
            except tk.TclError:  # pragma: no cover
                pass
        self._hint_after_id = self.after(HINT_RESET_MS, self._reset_result_hint)

    def _reset_result_hint(self) -> None:
        """恢复默认提示；若仍有未触发的复位定时器，一并取消。

        否则手动调用本方法后，那个仍在排队的定时器会在窗口销毁后触发，
        报出 ``invalid command name ... while executing ("after" script)``。
        """
        if self._hint_after_id is not None:
            try:
                self.after_cancel(self._hint_after_id)
            except tk.TclError:  # pragma: no cover
                pass
            self._hint_after_id = None
        self.result_hint_var.set(RESULT_HINT)

    def _cancel_timers(self) -> None:
        """取消全部挂起的 after 定时器。"""
        for attr in ("_after_id", "_hint_after_id"):
            handle = getattr(self, attr, None)
            if handle is None:
                continue
            try:
                self.after_cancel(handle)
            except tk.TclError:  # pragma: no cover
                pass
            setattr(self, attr, None)

    def _on_root_destroy(self, event: tk.Event) -> None:
        """兜底：无论窗口以何种方式销毁，都不留下悬空的定时器回调。"""
        if event.widget is self:
            self._cancel_timers()

    # ------------------------------------------------------------ 配置方案

    def _refresh_profile_combo(self, select: str = "") -> str:
        names = self._store.names()
        self.profile_combo.configure(values=names)
        target = select or self._store.pick_active()
        if target not in names:
            target = names[0] if names else ""
        self.profile_var.set(target)
        return target

    def _persist_profiles(self) -> bool:
        try:
            profiles.save(self._store)
        except OSError as exc:
            self.log.append(f"连接方案保存失败：{exc}", "error")
            return False
        return True

    def _has_profile(self) -> bool:
        return bool(self.profile_var.get().strip())

    def _current_profile_values(self) -> dict[str, Any]:
        name = self.profile_var.get().strip()
        return self._store.get(name) if name else {}

    def _on_profile_selected(self, _event: object = None) -> None:
        name = self.profile_var.get().strip()
        if not name:
            return
        self._store.active = name
        self._persist_profiles()
        self.status_var.set(f"已切换到连接方案「{name}」。")

    def _run_dialog(
        self, *, title: str, name: str, initial: dict[str, Any]
    ) -> dict | None:
        dialog = ConnectionDialog(
            self,
            title=title,
            name=name,
            initial=initial,
            remember_password=self._store.remember_for(name),
        )
        self.wait_window(dialog)
        return dialog.result

    def _on_profile_new(self) -> None:
        result = self._run_dialog(title="新增连接方案", name="", initial={})
        if not result:
            return
        name = result["name"]
        if self._store.has(name) and not messagebox.askyesno(
            "覆盖确认", f"方案「{name}」已存在，是否覆盖？", parent=self
        ):
            return
        self._store.upsert(
            name,
            result["values"],
            remember=bool(result.get("remember_password", True)),
        )
        self._refresh_profile_combo(name)
        if self._persist_profiles():
            self.log.append(f"已新增连接方案「{name}」。", "success")
            self.status_var.set(f"已新增连接方案「{name}」。")

    def _on_profile_edit(self) -> None:
        name = self.profile_var.get().strip()
        if not name:
            self._warn("请先选择一个方案。", "编辑失败")
            return
        result = self._run_dialog(
            title=f"编辑连接方案 — {name}",
            name=name,
            initial=self._store.get(name),
        )
        if not result:
            return
        new_name = result["name"]
        if new_name != name:
            if self._store.has(new_name) and not messagebox.askyesno(
                "覆盖确认", f"方案「{new_name}」已存在，是否覆盖？", parent=self
            ):
                return
            self._store.rename(name, new_name)
        self._store.upsert(
            new_name,
            result["values"],
            remember=bool(result.get("remember_password", True)),
        )
        self._refresh_profile_combo(new_name)
        if self._persist_profiles():
            self.log.append(f"连接方案「{new_name}」已更新。", "success")
            self.status_var.set(f"连接方案「{new_name}」已更新。")

    def _on_profile_delete(self) -> None:
        name = self.profile_var.get().strip()
        if not name:
            self._warn("请先选择一个方案。", "删除失败")
            return
        if not messagebox.askyesno(
            "删除方案", f"确定删除连接方案「{name}」？此操作不可撤销。", parent=self
        ):
            return
        self._store.remove(name)
        remaining = self._refresh_profile_combo()
        self._store.active = remaining
        if self._persist_profiles():
            self.log.append(f"连接方案「{name}」已删除。", "warn")
            self.status_var.set(
                f"连接方案「{name}」已删除"
                + (f"，已切换到「{remaining}」。" if remaining else "。")
            )

    # ------------------------------------------------------------ 配置文件

    def _open_file(self, path: Path, label: str) -> None:
        if not path.exists():
            self.log.append(
                f"{label}尚未生成：{path}（当前均为默认值时不会落盘）。", "warn"
            )
            self.status_var.set(f"{label}尚未生成，已打开所在目录。")
            open_path(path.parent)
            return
        if open_path(path):
            self.log.append(f"已打开{label}：{path}")
            self.status_var.set(f"已打开{label}。")
            return
        self._warn(f"无法打开{label}：{path}", "打开失败")

    def _on_open_settings_file(self) -> None:
        self._open_file(paths.config_path(), "设置文件")

    def _on_open_connections_file(self) -> None:
        self._open_file(paths.connections_path(), "方案文件")

    # ------------------------------------------------------------ 配置读写

    def _load_config(self) -> None:
        values = config.load()
        for key, value in values.items():
            row = self.rows.get(key)
            if row is not None:
                row.set(value)

        if self.rows["max_workers"].get() == config.default_workers():
            self.log.append(
                f"并发线程数按本机 {os.cpu_count()} 核默认取 "
                f"{config.default_workers()}（可在高级设置中调整）。"
            )

        self._store = profiles.load()
        seeded = profiles.seed_from_legacy_config(self._store)
        if seeded:
            self.log.append(f"已依据历史设置生成连接方案「{seeded}」。")
            self._persist_profiles()
        self._refresh_profile_combo()
        if not self._store.profiles:
            self.log.append(
                "尚未配置任何连接方案，请点击「配置方案 → 新增」填写连接信息。", "warn"
            )
        else:
            self.status_var.set(f"已载入连接方案「{self.profile_var.get()}」。")

    def _collect_values(self) -> dict[str, Any]:
        return {key: row.get() for key, row in self.rows.items()}

    def _collect_profile(self) -> ConnectionProfile:
        return config.to_connection_profile(self._current_profile_values())

    def _collect_request(self) -> SearchRequest:
        v = self._collect_values()
        return SearchRequest(
            field_keyword=str(v.get("field_keyword") or ""),
            field_mode=config.MODE_LABELS.get(
                str(v.get("field_mode")), config.MODE_LABELS[config.DEFAULT_MODE_LABEL]
            ),
            content_keyword=str(v.get("content_keyword") or ""),
            content_mode=config.MODE_LABELS.get(
                str(v.get("content_mode")), config.MODE_LABELS[config.DEFAULT_MODE_LABEL]
            ),
            ignore_case=bool(v.get("ignore_case")),
            collation_base=str(v.get("collation_base") or "Latin1_General"),
            max_workers=int(v.get("max_workers") or 8),
            max_targets=int(v.get("max_targets") or 5000),
            sample_size=int(v.get("sample_size") or 3),
            count_rows=bool(v.get("count_rows")),
            with_nolock=bool(v.get("with_nolock")),
            run_content_stage=bool(v.get("run_content")),
        )

    def _integer_problems(self) -> list[str]:
        return config.integer_problems(self._collect_values())

    def _save_settings_quietly(self) -> None:
        """静默落盘（启动检索时、以及关闭窗口时调用），失败不打扰用户。"""
        try:
            values = self._collect_values()
            config.save(
                values,
                remember_password=self._store.remember_for(
                    self.profile_var.get().strip()
                ),
            )
            profiles.save(self._store)
        except (OSError, ValueError):
            pass

    # ------------------------------------------------------------ 交互动作

    def _on_clear(self) -> None:
        self.result_table.clear()
        self._ui_rows = 0
        self.progress.configure(value=0)
        self.status_var.set("结果已清空。")

    def _on_copy(self) -> None:
        self.result_table.copy_current()

    def _on_open_row_detail(self, ref) -> None:
        """结果表双击回调：启用内容检索时打开命中数据窗口，否则不做任何动作。"""
        if not self._collect_request().run_content_stage:
            return
        if self._last_request is None:
            self._set_result_hint("请先执行一次检索，再双击查看命中数据。")
            return
        if not self._has_profile():
            self._set_result_hint("请先在「配置方案」中选择一个连接方案。")
            return
        profile = self._collect_profile()
        errors = _profile_errors(profile)
        if errors:
            self._set_result_hint(errors[0])
            return
        try:
            connect = engine.build_connection_factory(profile)
        except Exception as exc:  # noqa: BLE001
            self._set_result_hint(f"无法建立连接：{engine.describe_error(exc)}")
            return
        RowDetailDialog(self, ref=ref, request=self._last_request, connect=connect)

    def _require_profile(self) -> bool:
        if self._has_profile():
            return True
        self._warn(
            "尚未选择连接方案。请先在「配置方案」中点击「新增」填写连接信息。",
            "缺少连接方案",
        )
        return False

    def _on_start(self) -> None:
        if self._running:
            return
        if not self._require_profile():
            return
        profile = self._collect_profile()
        request = self._collect_request()

        problems = (
            _profile_errors(profile) + self._integer_problems() + request.validate()
        )
        if problems:
            self._warn("\n".join(problems), "参数校验未通过")
            return

        # 需求：每次开始检索都把检索条件写入设置文件，避免「改了却忘了保存」
        self._save_settings_quietly()

        self._content_enabled = request.run_content_stage
        self._last_request = request
        self._stage_a_targets = []
        self._ui_rows = 0
        self.result_table.clear()
        self.progress.configure(maximum=100, value=0)
        self._cancel_event = threading.Event()
        self._set_running(True)
        self.status_var.set("正在连接…")

        try:
            connect = engine.build_connection_factory(profile)
        except Exception as exc:  # noqa: BLE001
            self.log.append(f"初始化失败：{engine.describe_error(exc)}", "error")
            self._set_running(False)
            self.status_var.set("初始化失败。")
            return

        self._scan_thread = threading.Thread(
            target=self._scan_worker,
            args=(profile, request, connect, self._cancel_event),
            name="scan-runner",
            daemon=True,
        )
        self._begin_job()
        self._scan_thread.start()

    def _scan_worker(self, profile, request, connect, cancel_event) -> None:
        try:
            engine.run_scan(
                profile, request, connect, self._events.put, cancel_event=cancel_event
            )
        except Exception as exc:  # noqa: BLE001 - 兜底，保证 UI 一定能收尾
            self._events.put(
                engine.LogEvent(f"检索异常终止：{engine.describe_error(exc)}", "error")
            )
            self._events.put(engine.DoneEvent(ScanStats()))

    def _on_cancel(self) -> None:
        if self._cancel_event is not None:
            self._cancel_event.set()
            self.status_var.set("正在取消，等待进行中的列探测结束…")
            self.log.append("已请求取消。", "warn")
        self.btn_cancel.configure(state="disabled")

    def _on_close(self) -> None:
        if self._cancel_event is not None:
            self._cancel_event.set()
        self._cancel_timers()
        self._save_settings_quietly()
        self.destroy()

    # ------------------------------------------------------------ 事件循环

    def _begin_job(self) -> None:
        self._active_jobs += 1
        self._ensure_poll()

    def _end_job(self) -> None:
        self._active_jobs = max(0, self._active_jobs - 1)

    def _ensure_poll(self) -> None:
        if self._after_id is None and self._active_jobs > 0:
            self._after_id = self.after(POLL_INTERVAL_MS, self._poll)

    def _poll(self) -> None:
        # 正常由定时器触发（此时该 id 已失效，取消是幂等的）；若被直接调用，
        # 说明还有一个排队中的定时器，必须取消，否则它会变成悬空回调。
        if self._after_id is not None:
            try:
                self.after_cancel(self._after_id)
            except tk.TclError:  # pragma: no cover
                pass
        self._after_id = None
        for _ in range(EVENTS_PER_TICK):
            try:
                event = self._events.get_nowait()
            except queue.Empty:
                break
            self._handle_event(event)
        self._ensure_poll()

    def _handle_event(self, event: object) -> None:
        if isinstance(event, engine.LogEvent):
            self.log.append(_stamp(event.message), event.level)
        elif isinstance(event, engine.StageAEvent):
            self._stage_a_targets = event.targets
            self.log.append(
                _stamp(
                    f"阶段 A：元数据共 {event.total_columns} 列，字段名命中 "
                    f"{len(event.targets)} 列。"
                )
            )
            if not self._content_enabled:
                for ref in event.targets[:UI_ROW_LIMIT]:
                    self.result_table.add_ref(ref)
                self._ui_rows = min(len(event.targets), UI_ROW_LIMIT)
                if len(event.targets) > UI_ROW_LIMIT:
                    self.log.append(
                        f"命中列数超过界面上限 {UI_ROW_LIMIT} 条，仅展示前 "
                        f"{UI_ROW_LIMIT} 条。",
                        "warn",
                    )
            elif not event.targets:
                self.status_var.set("未找到匹配字段。")
        elif isinstance(event, engine.HitEvent):
            if self._ui_rows < UI_ROW_LIMIT:
                self.result_table.add_hit(event.hit)
                self._ui_rows += 1
            elif self._ui_rows == UI_ROW_LIMIT:
                self._ui_rows += 1
                self.log.append(
                    f"命中行数超过界面上限 {UI_ROW_LIMIT} 条，后续仅统计不再逐条展示。",
                    "warn",
                )
        elif isinstance(event, engine.ProgressEvent):
            self._apply_stats(event.stats)
        elif isinstance(event, engine.DoneEvent):
            self._apply_stats(event.stats)
            self._finish(event.stats)
            self._end_job()

    def _apply_stats(self, stats: ScanStats) -> None:
        if stats.total_targets > 0:
            self.progress.configure(maximum=stats.total_targets, value=stats.finished)
            self.status_var.set(stats.progress_text)
        else:
            self.progress.configure(maximum=100, value=0)

    def _finish(self, stats: ScanStats) -> None:
        self._set_running(False)
        if stats.cancelled:
            summary = (
                f"已取消：完成 {stats.finished}/{stats.total_targets} 列，"
                f"命中 {stats.matched} 列。"
            )
            level = "warn"
        elif stats.total_targets == 0 and stats.failed > 0:
            summary = "结束：检索过程中发生异常，详见运行日志。"
            level = "error"
        elif stats.total_targets == 0:
            summary = "结束：无待检索的列。"
            level = "info"
        else:
            summary = (
                f"完成：{stats.total_targets} 列已检索，命中 {stats.matched} 列，"
                f"异常 {stats.failed} 列，耗时 {stats.elapsed:.1f}s。"
            )
            level = "success" if stats.matched else "info"
        self.status_var.set(summary)
        self.log.append(_stamp(summary), level)

    def _set_running(self, running: bool) -> None:
        self._running = running
        self.btn_start.configure(state="disabled" if running else "normal")
        self.btn_cancel.configure(state="normal" if running else "disabled")

    # ------------------------------------------------------------ 辅助

    def _warn(self, message: str, title: str) -> None:
        self.log.append(_stamp(message.replace("\n", "；")), "error")
        self.status_var.set(title)
        messagebox.showwarning(title, message, parent=self)


def _stamp(message: str) -> str:
    return f"[{time.strftime('%H:%M:%S')}] {message}"


def _profile_errors(profile: ConnectionProfile) -> list[str]:
    """校验连接参数；驱动为空时自动优选本机驱动，一个都没有则给出可操作提示。"""
    from . import connstr

    if not profile.driver.strip():
        picked = drivers.detect_sql_server_driver(drivers.list_available())
        if not picked:
            return [
                "未检测到可用的 ODBC 驱动。请打开「配置方案 → 新增 / 编辑」，"
                "点击红色提示中的下载链接安装后重试。"
            ]
        profile.driver = picked
    return list(connstr.validate(profile))


def main() -> int:
    enable_dpi_awareness()
    app = FinderApp()
    app.mainloop()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
