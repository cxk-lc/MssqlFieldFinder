"""弹窗集合：连接方案的新增 / 编辑，以及命中明细查看。

连接设置不出现在主窗口，全部在 :class:`ConnectionDialog` 中展示与编辑；
该弹窗还承担「记住密码」「测试连接（含结果指示灯）」与
「未检测到可用 ODBC 驱动」的红色提示 + 官方下载超链接。

:class:`RowDetailDialog` 用于在结果表上双击单元格时，展示该表实际命中的数据行。
"""
from __future__ import annotations

import threading
import tkinter as tk
import tkinter.font as tkfont
import webbrowser
from tkinter import messagebox, ttk
from typing import Any

from . import config, connstr, drivers, engine, sqlbuild
from .models import ColumnRef, ConnectionProfile, SearchRequest
from .widgets import COLOR_DANGER, COLOR_MUTED, ScalarRow, frame_background

DRIVER_WARNING_TEXT = "未检测到可用的 ODBC 驱动，点击此处前往官方下载页"

#: 指示灯状态 -> 圆点颜色 / 文字颜色
LAMP_DOT_COLORS = {
    "idle": "#9ca3af",
    "testing": "#f59e0b",
    "ok": "#16a34a",
    "fail": "#dc2626",
}
LAMP_TEXT_COLORS = {
    "idle": COLOR_MUTED,
    "testing": COLOR_MUTED,
    "ok": "#15803d",
    "fail": COLOR_DANGER,
}
POLL_MS = 80


def open_url(url: str) -> bool:
    """在系统默认浏览器中打开链接；失败不抛异常。"""
    try:
        return bool(webbrowser.open(url))
    except Exception:  # noqa: BLE001 - 无浏览器环境不应影响主流程
        return False


def make_download_link(parent: tk.Misc, url: str = drivers.ODBC_DOWNLOAD_URL) -> tk.Label:
    """构造红色带下划线的可点击超链接标签。"""
    label = tk.Label(
        parent,
        text=f"⚠ {DRIVER_WARNING_TEXT}",
        foreground=COLOR_DANGER,
        cursor="hand2",
        anchor="w",
        justify="left",
    )
    font = tkfont.nametofont("TkDefaultFont").copy()
    font.configure(underline=True)
    label.configure(font=font)
    label.bind("<Button-1>", lambda _event: open_url(url))
    return label


class _Lamp(tk.Frame):
    """指示灯：一个圆点 + 一行状态文字（超宽自动换行）。"""

    SIZE = 16

    def __init__(self, master: tk.Misc, text: str = "") -> None:
        super().__init__(master)
        self.canvas = tk.Canvas(
            self,
            width=self.SIZE,
            height=self.SIZE,
            highlightthickness=0,
            borderwidth=0,
            background=frame_background(self),
        )
        self.dot = self.canvas.create_oval(2, 2, self.SIZE - 2, self.SIZE - 2,
                                          fill=LAMP_DOT_COLORS["idle"], outline="")
        self.canvas.pack(side="left", anchor="n")
        self.text_var = tk.StringVar(value=text)
        self.label = ttk.Label(
            self, textvariable=self.text_var, anchor="w", justify="left"
        )
        self.label.pack(side="left", padx=(8, 0), fill="x", expand=True)
        self.state = "idle"

    def set_wrap(self, pixels: int) -> None:
        """设定文案的换行宽度，避免长文本把弹窗撑宽或被截断。"""
        self.label.configure(wraplength=max(160, int(pixels)))

    def set(self, state: str, message: str) -> None:
        self.state = state
        self.canvas.itemconfigure(self.dot, fill=LAMP_DOT_COLORS.get(state, "#9ca3af"))
        self.text_var.set(message)
        self.label.configure(foreground=LAMP_TEXT_COLORS.get(state, COLOR_MUTED))


class ConnectionDialog(tk.Toplevel):
    """新增 / 编辑连接方案。

    确定后 ``result`` 为 ``{"name": str, "values": dict, "remember_password": bool}``。
    """

    def __init__(
        self,
        master: tk.Misc,
        *,
        title: str,
        name: str = "",
        initial: dict[str, Any] | None = None,
        remember_password: bool = True,
    ) -> None:
        super().__init__(master)
        self.title(title)
        self.transient(master)
        self.resizable(False, False)

        self.result: dict[str, Any] | None = None
        self.rows: dict[str, ScalarRow] = {}
        self.driver_warning: tk.Label | None = None

        self._testing = False
        self._test_done = threading.Event()
        self._test_outcome: tuple[str, str] = ("idle", "")
        self._after_id: str | None = None

        self._build(master)
        self._apply_initial(initial or {})
        self._remember_row.set(bool(remember_password))
        self._ensure_driver_choices()
        self._warn_if_no_driver()
        self.name_var.set(name)

        self._center_on(master)
        try:
            self.grab_set()
        except tk.TclError:  # pragma: no cover - 窗口尚不可见时忽略
            pass
        self.name_entry.focus_set()
        self.bind("<Return>", lambda _event: self._on_ok())
        self.bind("<Escape>", lambda _event: self._on_cancel())
        self.protocol("WM_DELETE_WINDOW", self._on_cancel)

    # ------------------------------------------------------------ 构建

    def _build(self, master: tk.Misc) -> None:
        header = ttk.Frame(self, padding=(12, 10, 12, 0))
        header.pack(fill="x")
        ttk.Label(header, text="方案名称", width=13, anchor="e").pack(
            side="left", padx=(0, 6)
        )
        self.name_var = tk.StringVar(value="")
        self.name_entry = ttk.Entry(header, textvariable=self.name_var, width=26)
        self.name_entry.pack(side="left")

        options = ttk.Frame(self, padding=(12, 6, 12, 0))
        options.pack(fill="x")
        self._remember_row = ScalarRow(
            options, config.FIELDS_BY_KEY["remember_password"]
        )
        self._remember_row.pack(fill="x")

        self.form = ttk.LabelFrame(self, text="连接设置", padding=(10, 6))
        self.form.pack(fill="x", padx=12, pady=(6, 0))
        for spec in config.CONNECTION_FIELDS:
            row = ScalarRow(self.form, spec)
            row.pack(fill="x", pady=1)
            self.rows[spec.key] = row

        ttk.Separator(self, orient="horizontal").pack(fill="x", padx=12, pady=(10, 0))
        status = ttk.Frame(self, padding=(12, 8, 12, 0))
        status.pack(fill="x")
        self.lamp = _Lamp(status, "尚未测试连接。")
        self.lamp.pack(fill="x")

        ttk.Separator(self, orient="horizontal").pack(fill="x", padx=12, pady=(10, 0))
        footer = ttk.Frame(self, padding=(12, 8, 12, 10))
        footer.pack(fill="x")
        ttk.Button(footer, text="取消", command=self._on_cancel).pack(side="right")
        ttk.Button(footer, text="确定", command=self._on_ok).pack(
            side="right", padx=(0, 8)
        )
        self.btn_test = ttk.Button(footer, text="测试连接", command=self._on_test)
        self.btn_test.pack(side="left")

    def _apply_initial(self, initial: dict[str, Any]) -> None:
        for key, row in self.rows.items():
            if key in initial:
                row.set(initial[key])

    def _ensure_driver_choices(self) -> list[str]:
        available = drivers.list_available()
        row = self.rows.get("driver")
        if row is None:
            return available
        row.widget.configure(values=available)  # type: ignore[attr-defined]
        current = str(row.get()).strip()
        if current and current in available:
            return available
        picked = drivers.detect_sql_server_driver(available)
        if picked:
            row.set(picked)
        return available

    def _warn_if_no_driver(self) -> None:
        """无可用驱动时，在 ODBC 配置项紧下方给出红色下载链接。"""
        if drivers.has_usable_driver():
            return
        self.driver_warning = make_download_link(self.form)
        self.driver_warning.pack(
            anchor="w", padx=(105, 0), pady=(0, 4), after=self.rows["driver"]
        )

    def _center_on(self, master: tk.Misc) -> None:
        # 先让表单完成布局，再据此确定指示灯文案的换行宽度——
        # 这样长文案会在窗口内自动折行，而不是把弹窗撑宽或被截断。
        # 用 reqwidth 而非 winfo_width：窗口尚未映射时后者会返回 1。
        self.update_idletasks()
        self.lamp.set_wrap(self.form.winfo_reqwidth() - 40)
        self.update_idletasks()

        width, height = self.winfo_width(), self.winfo_height()
        px, py = master.winfo_rootx(), master.winfo_rooty()
        pw, ph = master.winfo_width(), master.winfo_height()
        self.geometry(
            f"+{px + max(0, (pw - width) // 2)}+{py + max(0, (ph - height) // 3)}"
        )

    # ------------------------------------------------------------ 读写

    def values(self) -> dict[str, Any]:
        return {key: row.get() for key, row in self.rows.items()}

    def remember_password(self) -> bool:
        return bool(self._remember_row.get())

    def _collect_hard_errors(self, name: str, values: dict[str, Any]) -> list[str]:
        errors: list[str] = []
        if not name:
            errors.append("「方案名称」不能为空。")
        if not str(values.get("server") or "").strip():
            errors.append("「服务器/实例」不能为空。")
        if not str(values.get("database") or "").strip():
            errors.append("「数据库」不能为空。")
        errors.extend(config.integer_problems(values))
        return errors

    def _collect_soft_errors(self, values: dict[str, Any]) -> list[str]:
        """驱动缺失不阻断保存（用户可能想先存参数、后装驱动）。"""
        profile: ConnectionProfile = config.to_connection_profile(values)
        return [e for e in connstr.validate(profile) if "驱动" not in e]

    # ------------------------------------------------------------ 测试连接

    def _on_test(self) -> None:
        if self._testing:
            return
        values = self.values()
        profile = config.to_connection_profile(values)
        problems = config.integer_problems(values) + list(connstr.validate(profile))
        if problems:
            self.lamp.set("fail", "连接失败：" + problems[0])
            return

        self._testing = True
        self._test_done.clear()
        self._test_outcome = ("testing", "")
        self.btn_test.configure(state="disabled")
        self.lamp.set("testing", "正在测试连接…")
        threading.Thread(target=self._test_worker, args=(profile,), daemon=True).start()
        self._schedule_poll()

    def _test_worker(self, profile: ConnectionProfile) -> None:
        try:
            connect = engine.build_connection_factory(profile)
            # 只取「结果 + 当前库 + 登录身份」；版本号等长内容留给日志
            self._test_outcome = ("ok", engine.probe_server_info(connect).summary())
        except Exception as exc:  # noqa: BLE001 - 任何失败都要落到指示灯上
            self._test_outcome = ("fail", f"连接失败：{engine.describe_error(exc)}")
        finally:
            self._test_done.set()

    def _schedule_poll(self) -> None:
        self._after_id = self.after(POLL_MS, self._poll)

    def _poll(self) -> None:
        # 先取消可能仍在排队的定时器：正常由定时器触发时该 id 已失效，
        # 取消是幂等的；若被直接调用，则避免留下孤儿定时器。
        self._cancel_timer()
        if not self._test_done.is_set():
            self._schedule_poll()
            return
        state, message = self._test_outcome
        self._testing = False
        self.btn_test.configure(state="normal")
        self.lamp.set(state, message)

    def _cancel_timer(self) -> None:
        if self._after_id is not None:
            try:
                self.after_cancel(self._after_id)
            except tk.TclError:  # pragma: no cover
                pass
            self._after_id = None

    def destroy(self) -> None:
        self._cancel_timer()
        super().destroy()

    # ------------------------------------------------------------ 事件

    def _on_ok(self) -> None:
        name = self.name_var.get().strip()
        values = self.values()

        hard = self._collect_hard_errors(name, values)
        if hard:
            messagebox.showwarning("请检查填写内容", "\n".join(hard), parent=self)
            return

        soft = self._collect_soft_errors(values)
        if soft and not messagebox.askyesno(
            "连接参数不完整",
            "\n".join(soft) + "\n\n仍要保存该方案吗？",
            parent=self,
        ):
            return

        self.result = {
            "name": name,
            "values": values,
            "remember_password": self.remember_password(),
        }
        self.destroy()

    def _on_cancel(self) -> None:
        self.result = None
        self.destroy()


class RowDetailDialog(tk.Toplevel):
    """展示某表满足内容条件的命中数据。

    查询在后台线程执行，界面经 ``after`` 轮询取结果，避免登录/慢查询把窗口冻住。
    """

    def __init__(
        self,
        master: tk.Misc,
        *,
        ref: ColumnRef,
        request: SearchRequest,
        connect,
        limit: int = sqlbuild.DETAIL_ROW_LIMIT,
    ) -> None:
        super().__init__(master)
        self.title(f"命中数据 — {ref.schema}.{ref.table}.{ref.column}")
        self.transient(master)
        self.geometry("1000x580")
        self.minsize(640, 360)

        self._ref = ref
        self._request = request
        self._connect = connect
        self._limit = limit
        self._columns: list[str] = []
        self._rows: list[tuple[str, ...]] = []
        self._error = ""
        self._done = threading.Event()
        self._after_id: str | None = None

        self._build()
        self._center_on(master)
        self.bind("<Escape>", lambda _event: self.destroy())
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self._start()

    # ------------------------------------------------------------ 构建

    def _build(self) -> None:
        head = ttk.Frame(self, padding=(12, 10, 12, 0))
        head.pack(fill="x")
        ttk.Label(head, text="数据表", foreground=COLOR_MUTED).grid(
            row=0, column=0, sticky="e", padx=(0, 6)
        )
        ttk.Label(head, text=f"{self._ref.schema}.{self._ref.table}").grid(
            row=0, column=1, sticky="w"
        )
        ttk.Label(head, text="命中字段", foreground=COLOR_MUTED).grid(
            row=1, column=0, sticky="e", padx=(0, 6)
        )
        ttk.Label(head, text=self._ref.column).grid(row=1, column=1, sticky="w")
        ttk.Label(head, text="检索条件", foreground=COLOR_MUTED).grid(
            row=2, column=0, sticky="e", padx=(0, 6)
        )
        ttk.Label(head, text=self._condition_text()).grid(row=2, column=1, sticky="w")

        body = ttk.Frame(self, padding=(12, 10, 12, 0))
        body.pack(fill="both", expand=True)
        body.rowconfigure(0, weight=1)
        body.columnconfigure(0, weight=1)

        self.tree = ttk.Treeview(body, show="headings", selectmode="extended")
        yscroll = ttk.Scrollbar(body, orient="vertical", command=self.tree.yview)
        xscroll = ttk.Scrollbar(body, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")
        xscroll.grid(row=1, column=0, sticky="ew")

        footer = ttk.Frame(self, padding=(12, 8, 12, 10))
        footer.pack(fill="x")
        self.status_var = tk.StringVar(value="正在查询…")
        ttk.Label(footer, textvariable=self.status_var, anchor="w").pack(
            side="left", fill="x", expand=True
        )
        ttk.Button(footer, text="关闭", command=self.destroy).pack(side="right")
        self.btn_copy = ttk.Button(
            footer, text="复制全部数据", command=self.copy_all, state="disabled"
        )
        self.btn_copy.pack(side="right", padx=(0, 8))

    def _condition_text(self) -> str:
        mode = self._request.content_mode.label
        case = "忽略大小写" if self._request.ignore_case else "区分大小写"
        return (
            f"{mode}匹配「{self._request.content_keyword}」，{case}，"
            f"最多显示 {self._limit} 行"
        )

    def _center_on(self, master: tk.Misc) -> None:
        self.update_idletasks()
        width, height = self.winfo_width(), self.winfo_height()
        px, py = master.winfo_rootx(), master.winfo_rooty()
        pw, ph = master.winfo_width(), master.winfo_height()
        self.geometry(
            f"+{px + max(0, (pw - width) // 2)}+{py + max(0, (ph - height) // 3)}"
        )

    # ------------------------------------------------------------ 取数

    def _start(self) -> None:
        threading.Thread(target=self._worker, daemon=True).start()
        self._schedule_poll()

    def _worker(self) -> None:
        try:
            self._columns, self._rows = engine.fetch_rows_with(
                self._connect, self._ref, self._request, self._limit
            )
        except Exception as exc:  # noqa: BLE001 - 失败也要给出可读原因
            self._error = f"查询失败：{engine.describe_error(exc)}"
        finally:
            self._done.set()

    def _schedule_poll(self) -> None:
        self._after_id = self.after(POLL_MS, self._poll)

    def _poll(self) -> None:
        self._cancel_timer()
        if not self._done.is_set():
            self._schedule_poll()
            return
        self._render()

    def _cancel_timer(self) -> None:
        if self._after_id is not None:
            try:
                self.after_cancel(self._after_id)
            except tk.TclError:  # pragma: no cover
                pass
            self._after_id = None

    def destroy(self) -> None:
        self._cancel_timer()
        super().destroy()

    # ------------------------------------------------------------ 渲染

    def _render(self) -> None:
        if self._error:
            self.status_var.set(self._error)
            return

        keys = [f"c{index}" for index in range(len(self._columns))]
        self.tree.configure(columns=keys or ["c0"], show="headings")
        for key, name in zip(keys, self._columns):
            self.tree.heading(key, text=name)
            self.tree.column(key, width=150, anchor="w", stretch=False)
        if not self._columns:
            self.tree.heading("c0", text="(无列)")

        for row in self._rows:
            self.tree.insert("", "end", values=row)

        if not self._rows:
            self.status_var.set("未查到数据行（可能已被并发修改，或当前账号无权读取）。")
            return
        self.btn_copy.configure(state="normal")
        suffix = "（已达上限，可能还有更多）" if len(self._rows) >= self._limit else ""
        self.status_var.set(f"共 {len(self._rows)} 行{suffix}")

    def copy_all(self) -> str:
        if not self._columns:
            return ""
        lines = ["\t".join(self._columns)]
        lines.extend("\t".join(row) for row in self._rows)
        text = "\n".join(lines)
        self.clipboard_clear()
        self.clipboard_append(text)
        self.status_var.set(f"已复制 {len(self._rows)} 行到剪贴板。")
        return text
