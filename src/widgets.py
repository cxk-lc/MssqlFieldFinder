"""可复用界面控件：滚动容器、表单行、结果表、日志面板。

所有控件均为普通 ttk 组件，不进入事件循环也可实例化，便于自检脚本验证。
"""
from __future__ import annotations

import tkinter as tk
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from tkinter import ttk

from .config import Field
from .models import Hit, HitStatus

# 浅色主题下的取色（避免依赖具体 ttk 主题的默认值）
COLOR_TEXT = "#1f2937"
COLOR_MUTED = "#6b7280"
COLOR_BORDER = "#d1d5db"
COLOR_PANEL = "#ffffff"
COLOR_TOOLBAR = "#f3f4f6"
COLOR_SELECT = "#2563eb"
COLOR_DANGER = "#b91c1c"

LEVEL_COLORS = {
    "info": COLOR_TEXT,
    "debug": COLOR_MUTED,
    "warn": "#b45309",
    "error": COLOR_DANGER,
    "success": "#15803d",
}

ERROR_STATUSES = {
    HitStatus.TIMEOUT,
    HitStatus.DENIED,
    HitStatus.ERROR,
    HitStatus.CANCELLED,
}

#: 结果表下方的操作提示（复制反馈会临时替换它）
RESULT_HINT = (
    "单击选中单元格 · 单击序号左侧空白格选中整行 · 单击列名选中整列 · "
    "双击查看该表的命中数据 · 右键四项复制 · Ctrl+C 复制当前选中对象"
)

#: 右键菜单项（固定四项）
MENU_COPY_CELL = "复制单元格"
MENU_COPY_ROW = "复制所在行"
MENU_COPY_COLUMN = "复制所在列"
MENU_COPY_ALL = "复制全部结果"

#: 选中态
SEL_NONE = "none"
SEL_CELL = "cell"
SEL_ROW = "row"
SEL_COL = "col"


def frame_background(widget: tk.Misc) -> str:
    """取当前 ttk 主题的框架底色，用于让 Canvas 与周围面板融为一体。"""
    try:
        color = ttk.Style(widget).lookup("TFrame", "background")
    except tk.TclError:  # pragma: no cover - 主题缺失
        color = ""
    return color or "#f0f0f0"


class ScrollFrame(ttk.Frame):
    """Canvas + 内嵌 Frame 的可滚动容器。

    关键点（否则出现「滚不动」或「内容被压扁」）：

    * ``<Configure>`` 时同步内嵌窗口宽度，避免横向压缩；
    * 滚轮仅在指针进入区域时用 ``bind_all`` 绑定，离开即解绑，
      以免多个滚动区域互相抢占滚轮事件。
    """

    def __init__(self, master: tk.Misc, **kwargs) -> None:
        super().__init__(master, **kwargs)
        self.canvas = tk.Canvas(
            self,
            highlightthickness=0,
            borderwidth=0,
            background=frame_background(self),
        )
        self.vscroll = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.vscroll.set)
        self.vscroll.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)

        self.body = ttk.Frame(self.canvas)
        self._window = self.canvas.create_window((0, 0), window=self.body, anchor="nw")

        self.body.bind("<Configure>", self._on_body_configure)
        self.canvas.bind("<Configure>", self._on_canvas_configure)
        self.canvas.bind("<Enter>", self._bind_wheel)
        self.canvas.bind("<Leave>", self._unbind_wheel)

    def _on_body_configure(self, _event: tk.Event | None = None) -> None:
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas_configure(self, event: tk.Event) -> None:
        self.canvas.itemconfigure(self._window, width=event.width)

    def _bind_wheel(self, _event: tk.Event | None = None) -> None:
        self.canvas.bind_all("<MouseWheel>", self._on_wheel)

    def _unbind_wheel(self, _event: tk.Event | None = None) -> None:
        self.canvas.unbind_all("<MouseWheel>")

    def _on_wheel(self, event: tk.Event) -> None:
        if self.body.winfo_reqheight() <= self.canvas.winfo_height():
            return
        delta = -1 if event.delta > 0 else 1
        self.canvas.yview_scroll(delta, "units")


class ScalarRow(ttk.Frame):
    """由 Field 声明生成的一行表单：标签 + 控件 + 提示。"""

    def __init__(self, master: tk.Misc, spec: Field) -> None:
        super().__init__(master)
        self.spec = spec
        self.columnconfigure(1, weight=1)

        label = ttk.Label(self, text=spec.label, anchor="e", width=13)
        label.grid(row=0, column=0, sticky="e", padx=(0, 6), pady=2)

        self.var: tk.Variable
        if spec.kind == "bool":
            self.var = tk.BooleanVar(value=bool(spec.default))
            widget: tk.Widget = ttk.Checkbutton(self, variable=self.var)
        elif spec.kind == "choice":
            self.var = tk.StringVar(value=str(spec.default))
            widget = ttk.Combobox(
                self,
                textvariable=self.var,
                values=list(spec.choices),
                state="readonly",
                width=spec.width,
            )
        elif spec.kind == "password":
            self.var = tk.StringVar(value=str(spec.default))
            widget = ttk.Entry(self, textvariable=self.var, show="*", width=spec.width)
        else:
            self.var = tk.StringVar(value="" if spec.default == "" else str(spec.default))
            widget = ttk.Entry(self, textvariable=self.var, width=spec.width)

        widget.grid(row=0, column=1, sticky="w", pady=2)

        if spec.hint:
            ttk.Label(
                self,
                text=spec.hint,
                foreground=COLOR_MUTED,
                wraplength=150,
                justify="left",
            ).grid(row=0, column=2, sticky="nw", padx=(8, 0), pady=3)

        self.widget = widget

    # ---- 读写 ----
    def get(self) -> object:
        if self.spec.kind == "bool":
            return bool(self.var.get())
        if self.spec.kind == "int":
            text = str(self.var.get()).strip()
            if text == "":
                return None
            try:
                return int(text)
            except ValueError:
                return text  # 交给校验层报错，避免在控件层吞掉用户输入
        return str(self.var.get())

    def set(self, value: object) -> None:
        if self.spec.kind == "bool":
            self.var.set(bool(value))
        elif value is None:
            self.var.set("")
        else:
            self.var.set(str(value))


class _BorderOverlay:
    """用四条细边框勾勒矩形，从而高亮单元格/整列而不遮挡文字。

    Tk 的 Treeview 只支持整行选中，无法高亮单个单元格；若用带底色的控件覆盖，
    又会盖住文字。因此这里只用四条 2px 的边框围出轮廓。
    """

    THICKNESS = 2

    def __init__(self, parent: tk.Misc, color: str = COLOR_SELECT) -> None:
        self._frames = [
            tk.Frame(parent, background=color, borderwidth=0, highlightthickness=0)
            for _ in range(4)
        ]
        self.visible = False

    def show(self, x: int, y: int, width: int, height: int) -> None:
        if width <= self.THICKNESS or height <= self.THICKNESS:
            self.hide()
            return
        t = self.THICKNESS
        for frame, geometry in zip(
            self._frames,
            (
                (x, y, width, t),               # 上
                (x, y + height - t, width, t),  # 下
                (x, y, t, height),              # 左
                (x + width - t, y, t, height),  # 右
            ),
        ):
            frame.place(x=geometry[0], y=geometry[1], width=geometry[2], height=geometry[3])
            frame.lift()
        self.visible = True

    def hide(self) -> None:
        for frame in self._frames:
            frame.place_forget()
        self.visible = False


@dataclass
class GridSelection:
    """结果表的选中态。

    三种选中态**互斥**：``kind`` 只能取其中一个值，任一 setter 都会重置其余两者。
    """

    kind: str = SEL_NONE
    row: str = ""
    col: str = ""

    @property
    def is_empty(self) -> bool:
        return self.kind == SEL_NONE

    @property
    def label(self) -> str:
        if self.kind == SEL_CELL:
            return "单元格"
        if self.kind == SEL_ROW:
            return "整行"
        if self.kind == SEL_COL:
            return "整列"
        return "无"


class ResultTable(ttk.Frame):
    """命中结果表：单元格 / 整行 / 整列三种互斥选中态 + 逐字段复制。

    选中规则
    --------
    ============  ====================================  ==========================
    操作          结果                                  视觉
    ============  ====================================  ==========================
    单击数据单元格 选中该单元格                           该格蓝色边框
    单击空白控制列 选中该行                               整行高亮
    单击列名       选中该整列                             整列蓝色边框
    单击空白区域   清空选中                               无
    双击单元格     查看该表满足条件的命中数据（需启用内容检索） —
    Ctrl+C        复制当前选中对象（格 / 行 / 列）          —
    ============  ====================================  ==========================
    """

    #: 首列是空白控制列：点击它选中整行
    SEL_COLUMN = "sel"

    COLUMNS = (
        (SEL_COLUMN, "", 34, "center"),
        ("idx", "序号", 48, "center"),
        ("status", "状态", 58, "center"),
        ("schema", "架构", 76, "w"),
        ("table", "表 / 视图名", 168, "w"),
        ("objtype", "类型", 52, "center"),
        ("column", "字段", 130, "w"),
        ("dtype", "字段类型", 96, "w"),
        ("rows", "命中行数", 78, "center"),
        ("sample", "示例值", 250, "w"),
    )

    COLUMN_KEYS = tuple(c[0] for c in COLUMNS)
    COLUMN_TITLES = tuple(c[1] for c in COLUMNS)
    #: 可作为单元格/列被选中的列。空白控制列恒为第 0 列，故直接切片。
    #: 注意：类体内的生成器表达式看不到同类属性（作用域隔离），不能用
    #: ``tuple(k for k in COLUMN_KEYS if k != SEL_COLUMN)`` 这种写法。
    DATA_KEYS = COLUMN_KEYS[1:]
    #: 导出 TSV 时跳过的列（控制列不应出现在粘贴结果里）
    EXPORT_KEYS = DATA_KEYS
    EXPORT_TITLES = COLUMN_TITLES[1:]

    def __init__(
        self,
        master: tk.Misc,
        on_status: Callable[[str], None] | None = None,
        on_open_detail: Callable[[object], None] | None = None,
    ) -> None:
        super().__init__(master)
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)

        self._on_status = on_status or (lambda _text: None)
        self._open_detail = on_open_detail
        self._ctx_row = ""
        self._ctx_col = ""
        self._count = 0
        self._refs: dict[str, object] = {}
        self._selection = GridSelection()
        self._sync_id: str | None = None

        self.tree = ttk.Treeview(
            self,
            columns=list(self.COLUMN_KEYS),
            show="headings",
            selectmode="extended",
        )
        for key, title, width, anchor in self.COLUMNS:
            self.tree.heading(key, text=title, command=lambda k=key: self._on_heading(k))
            self.tree.column(
                key,
                width=width,
                anchor=anchor,
                stretch=(key == "sample"),
                minwidth=20 if key == self.SEL_COLUMN else 40,
            )

        self.tree.tag_configure("match", foreground="#15803d")
        self.tree.tag_configure("problem", foreground=COLOR_DANGER)

        yscroll = ttk.Scrollbar(self, orient="vertical", command=self._on_yscroll)
        xscroll = ttk.Scrollbar(self, orient="horizontal", command=self._on_xscroll)
        self.tree.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)

        self.tree.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")
        xscroll.grid(row=1, column=0, sticky="ew")

        self._overlay = _BorderOverlay(self)
        self._build_menu()

        self.tree.bind("<Button-1>", self._on_button1)
        self.tree.bind("<Button-3>", self._on_right_click)
        self.tree.bind("<Double-1>", self._on_double_click)
        self.tree.bind("<Control-c>", self._on_ctrl_c)
        self.tree.bind("<Control-C>", self._on_ctrl_c)
        self.tree.bind("<Configure>", lambda _e: self._schedule_sync())
        self.tree.bind("<MouseWheel>", lambda _e: self._schedule_sync(), add="+")
        self.bind("<Destroy>", self._on_destroy)

    # ---------------------------------------------------------- 行操作

    @property
    def count(self) -> int:
        return self._count

    @property
    def selection(self) -> GridSelection:
        return self._selection

    def clear(self) -> None:
        self.tree.delete(*self.tree.get_children())
        self._count = 0
        self._refs.clear()
        self._selection = GridSelection()
        self._ctx_row = self._ctx_col = ""
        self._overlay.hide()

    def _append(self, rest: tuple, tag: str, ref: object = None) -> None:
        """`rest` 为「序号」之后的全部字段值，序号由本方法统一编号。"""
        self._count += 1
        iid = self.tree.insert("", "end", values=("", self._count, *rest), tags=(tag,))
        if ref is not None:
            self._refs[iid] = ref

    def ref_for(self, iid: str) -> object | None:
        """取某一行对应的 ColumnRef（双击查看明细时需要）。"""
        return self._refs.get(iid)

    def add_ref(
        self, ref, status: str = "已定位", rows: str = "", sample: str = ""
    ) -> None:
        """仅字段定位（未做内容检索）时的结果行。"""
        self._append(
            (
                status,
                ref.schema,
                ref.table,
                ref.object_label,
                ref.column,
                ref.data_type,
                rows,
                sample,
            ),
            "match",
            ref,
        )

    def add_hit(self, hit: Hit) -> None:
        rows = "" if hit.rows is None else str(hit.rows)
        self._append(
            (
                hit.status.value,
                hit.ref.schema,
                hit.ref.table,
                hit.ref.object_label,
                hit.ref.column,
                hit.ref.data_type,
                rows,
                hit.sample or hit.detail,
            ),
            "problem" if hit.status in ERROR_STATUSES else "match",
            hit.ref,
        )

    # ---------------------------------------------------------- 选中模型

    def _clear_native(self) -> None:
        current = self.tree.selection()
        if current:
            self.tree.selection_remove(*current)

    def select_cell(self, iid: str, key: str) -> None:
        """选中单个单元格：清空行选中与列选中。"""
        if key not in self.DATA_KEYS or not iid:
            return
        self._clear_native()
        self._selection = GridSelection(SEL_CELL, iid, key)
        self._sync_overlay()

    def select_row(self, iid: str) -> None:
        """选中整行：清空单元格与列选中。"""
        if not iid:
            return
        self._selection = GridSelection(SEL_ROW, iid, "")
        self.tree.selection_set(iid)
        self.tree.focus(iid)
        self._sync_overlay()

    def select_column(self, key: str) -> None:
        """选中整列：清空单元格与行选中。"""
        if key not in self.DATA_KEYS:
            return
        self._clear_native()
        self._selection = GridSelection(SEL_COL, "", key)
        self._sync_overlay()

    def clear_selection(self) -> None:
        self._selection = GridSelection()
        self._clear_native()
        self._sync_overlay()

    # ---------------------------------------------------------- 定位与高亮

    def column_at(self, x: int) -> str:
        column_id = self.tree.identify_column(x)
        if not column_id:
            return ""
        try:
            index = int(str(column_id).lstrip("#")) - 1
        except ValueError:
            return ""
        if not 0 <= index < len(self.COLUMN_KEYS):
            return ""
        return self.COLUMN_KEYS[index]

    def cell_at(self, event: tk.Event) -> tuple[str, str]:
        """由鼠标事件定位 (行 iid, 列键)；落在表头或空白区域时返回两个空串。"""
        if self.tree.identify_region(event.x, event.y) not in ("cell", "tree"):
            return "", ""
        row = self.tree.identify_row(event.y)
        if not row:
            return "", ""
        return row, self.column_at(event.x)

    @property
    def _ox(self) -> int:
        return self.tree.winfo_x()

    @property
    def _oy(self) -> int:
        return self.tree.winfo_y()

    def _schedule_sync(self) -> None:
        if self._sync_id is None:
            self._sync_id = self.after_idle(self._do_sync)

    def _do_sync(self) -> None:
        self._sync_id = None
        self._sync_overlay()

    def _sync_overlay(self) -> None:
        sel = self._selection
        if sel.kind == SEL_CELL:
            box = self.tree.bbox(sel.row, sel.col)
            if not box:  # 目标被滚动出可视区
                self._overlay.hide()
                return
            x, y, width, height = box
            self._overlay.show(self._ox + x, self._oy + y, width, height)
        elif sel.kind == SEL_COL:
            rect = self._column_rect(sel.col)
            if rect is None:
                self._overlay.hide()
            else:
                self._overlay.show(*rect)
        else:
            self._overlay.hide()

    def _column_rect(self, key: str) -> tuple[int, int, int, int] | None:
        """整列高亮的矩形：横向取列宽，纵向从可视内容顶部到底部。"""
        children = self.tree.get_children()
        if not children:
            return None
        start = int(self.tree.yview()[0] * len(children))
        box = None
        for iid in children[max(0, start - 1) : start + 4]:
            box = self.tree.bbox(iid, key)
            if box:
                break
        if not box:
            for iid in children:
                box = self.tree.bbox(iid, key)
                if box:
                    break
        if not box:
            return None
        x, top, width, _height = box
        return (self._ox + x, self._oy + top, width, max(0, self.tree.winfo_height() - top))

    # ---------------------------------------------------------- 交互

    def _on_heading(self, key: str) -> None:
        """点击列名 -> 选中整列（空白控制列不参与）。"""
        if key != self.SEL_COLUMN:
            self.select_column(key)

    def _on_button1(self, event: tk.Event) -> str | None:
        self.tree.focus_set()
        region = self.tree.identify_region(event.x, event.y)
        if region in ("separator", "heading"):
            # 分隔线交给原生逻辑以支持拖拽调列宽；
            # 列名点击由 heading 的 command 回调负责（见 _on_heading），此处不重复处理。
            return None
        if region in ("cell", "tree"):
            row = self.tree.identify_row(event.y)
            key = self.column_at(event.x)
            if not row:
                self.clear_selection()
            elif key == self.SEL_COLUMN:
                self.select_row(row)
            elif key:
                self.select_cell(row, key)
            else:
                self.clear_selection()
            return "break"
        self.clear_selection()
        return "break"

    def _on_double_click(self, event: tk.Event) -> str:
        """双击单元格：交由外部决定是否打开「命中数据」窗口。

        未启用内容检索时外部回调会直接返回，界面不做任何动作——
        因此这里不再承担「复制单元格」的职责（复制已由右键菜单与 Ctrl+C 覆盖）。
        """
        row, key = self.cell_at(event)
        if not row or not key or key == self.SEL_COLUMN or self._open_detail is None:
            return "break"
        ref = self.ref_for(row)
        if ref is not None:
            self._open_detail(ref)
        return "break"

    def _on_ctrl_c(self, _event: tk.Event) -> str:
        self.copy_current()
        return "break"

    def _on_destroy(self, event: tk.Event) -> None:
        if event.widget is not self:
            return
        if self._sync_id is not None:
            try:
                self.after_cancel(self._sync_id)
            except tk.TclError:  # pragma: no cover
                pass
            self._sync_id = None

    def _on_yscroll(self, *args: object) -> None:
        self.tree.yview(*args)
        self._schedule_sync()

    def _on_xscroll(self, *args: object) -> None:
        self.tree.xview(*args)
        self._schedule_sync()

    # ---------------------------------------------------------- 右键菜单

    def _build_menu(self) -> None:
        self._menu = tk.Menu(self, tearoff=0)
        self._menu.add_command(label=MENU_COPY_CELL, command=self.copy_cell)
        self._menu.add_command(label=MENU_COPY_ROW, command=self.copy_row)
        self._menu.add_command(label=MENU_COPY_COLUMN, command=self.copy_column)
        self._menu.add_command(label=MENU_COPY_ALL, command=self.copy_all)

    def _on_right_click(self, event: tk.Event) -> str:
        region = self.tree.identify_region(event.x, event.y)
        key = self.column_at(event.x)
        if region == "heading":
            self._ctx_row, self._ctx_col = "", key if key != self.SEL_COLUMN else ""
            if key and key != self.SEL_COLUMN:
                self.select_column(key)
        else:
            row = self.tree.identify_row(event.y)
            if row and key == self.SEL_COLUMN:
                self.select_row(row)
                self._ctx_row, self._ctx_col = row, ""
            elif row and key:
                self.select_cell(row, key)
                self._ctx_row, self._ctx_col = row, key
            else:
                self._ctx_row, self._ctx_col = "", ""

        self._update_menu_state()
        try:
            self._menu.tk_popup(event.x_root, event.y_root)
        finally:
            self._menu.grab_release()
        return "break"

    def _update_menu_state(self) -> None:
        sel = self._selection
        has_rows = bool(self.tree.get_children())
        self._menu.entryconfigure(
            MENU_COPY_CELL,
            state="normal" if self._ctx_row and self._ctx_col else "disabled",
        )
        self._menu.entryconfigure(
            MENU_COPY_ROW,
            state="normal" if (self._ctx_row or sel.kind in (SEL_ROW, SEL_CELL)) else "disabled",
        )
        self._menu.entryconfigure(
            MENU_COPY_COLUMN,
            state="normal" if (self._ctx_col or sel.kind == SEL_COL) else "disabled",
        )
        self._menu.entryconfigure(
            MENU_COPY_ALL, state="normal" if has_rows else "disabled"
        )

    # ---------------------------------------------------------- 复制

    def cell_value(self, iid: str, key: str) -> str:
        if key not in self.COLUMN_KEYS:
            return ""
        values = self.tree.item(iid, "values")
        index = self.COLUMN_KEYS.index(key)
        return str(values[index]) if index < len(values) else ""

    def _to_clipboard(self, text: str, message: str) -> str:
        self.clipboard_clear()
        self.clipboard_append(text)
        self._on_status(message)
        return text

    def _title_of(self, key: str) -> str:
        try:
            return self.COLUMN_TITLES[self.COLUMN_KEYS.index(key)]
        except ValueError:
            return key

    def _resolve_row(self, row: str | None) -> str:
        if row:
            return row
        if self._ctx_row:
            return self._ctx_row
        if self._selection.kind in (SEL_CELL, SEL_ROW):
            return self._selection.row
        return ""

    def _resolve_col(self, key: str | None) -> str:
        if key:
            return key
        if self._ctx_col:
            return self._ctx_col
        if self._selection.kind == SEL_CELL:
            return self._selection.col
        if self._selection.kind == SEL_COL:
            return self._selection.col
        return ""

    def copy_cell(self, row: str | None = None, col: str | None = None) -> str:
        """复制单个字段值。"""
        iid = self._resolve_row(row)
        key = col or self._ctx_col or (
            self._selection.col if self._selection.kind == SEL_CELL else ""
        )
        if not iid or not key or key == self.SEL_COLUMN:
            self._on_status("请先点选一个数据单元格（或用右键菜单指定目标）。")
            return ""
        value = self.cell_value(iid, key)
        title = self._title_of(key)
        preview = value if len(value) <= 40 else value[:39] + "…"
        message = (
            f"已复制「{title}」：{preview}" if preview else f"已复制「{title}」（空值）。"
        )
        return self._to_clipboard(value, message)

    def copy_row(self, row: str | None = None) -> str:
        """复制整行（不含空白控制列，可直接粘贴进 Excel）。"""
        iid = self._resolve_row(row)
        if not iid:
            self._on_status("请先选中一行（点击序号左侧的空白格）。")
            return ""
        values = [
            self.cell_value(iid, key) for key in self.EXPORT_KEYS
        ]
        return self._to_clipboard(
            "\t".join(values), f"已复制第 {values[0]} 行的全部字段。"
        )

    def copy_column(self, col: str | None = None) -> str:
        """复制整列（含表头）。"""
        key = self._resolve_col(col)
        if not key or key == self.SEL_COLUMN:
            self._on_status("请先选中一列（点击列名）。")
            return ""
        items = self.tree.get_children()
        if not items:
            self._on_status("结果为空，无可复制内容。")
            return ""
        title = self._title_of(key)
        lines = [title]
        lines.extend(self.cell_value(iid, key) for iid in items)
        return self._to_clipboard(
            "\n".join(lines), f"已复制「{title}」列的 {len(items)} 个值。"
        )

    def copy_all(self) -> str:
        items = self.tree.get_children()
        if not items:
            self._on_status("结果为空，无可复制内容。")
            return ""
        return self._to_clipboard(
            self.tsv_for(items), f"已复制全部 {len(items)} 行。"
        )

    def copy_current(self) -> str:
        """Ctrl+C 入口：按当前选中对象（单元格 / 行 / 列）分派。"""
        sel = self._selection
        if sel.kind == SEL_CELL:
            return self.copy_cell(sel.row, sel.col)
        if sel.kind == SEL_ROW:
            return self.copy_row(sel.row)
        if sel.kind == SEL_COL:
            return self.copy_column(sel.col)
        self._on_status("当前没有选中对象：点单元格、点空白列或点列名后再复制。")
        return ""

    # ---------------------------------------------------------- 导出文本

    def tsv_for(self, items: Iterable[str], include_header: bool = True) -> str:
        lines: list[str] = []
        if include_header:
            lines.append("\t".join(self.EXPORT_TITLES))
        for iid in items:
            lines.append("\t".join(self.cell_value(iid, key) for key in self.EXPORT_KEYS))
        return "\n".join(lines)

    def tsv(self, only_selected: bool = True) -> str:
        """兼容入口：导出选中行（无选中时导出全部）。"""
        items: Sequence[str] = (
            self.tree.selection() if only_selected else self.tree.get_children()
        )
        if only_selected and not items:
            items = self.tree.get_children()
        return self.tsv_for(items)


class LogPane(ttk.Frame):
    """带级别着色的运行日志。"""

    MAX_LINES = 5000

    def __init__(self, master: tk.Misc) -> None:
        super().__init__(master)
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)

        self.text = tk.Text(
            self,
            wrap="word",
            height=8,
            state="disabled",
            background=COLOR_PANEL,
            foreground=COLOR_TEXT,
            relief="flat",
            borderwidth=0,
            padx=8,
            pady=6,
        )
        self.text.tag_configure("info", foreground=LEVEL_COLORS["info"])
        self.text.tag_configure("debug", foreground=LEVEL_COLORS["debug"])
        self.text.tag_configure("warn", foreground=LEVEL_COLORS["warn"])
        self.text.tag_configure("error", foreground=LEVEL_COLORS["error"])
        self.text.tag_configure("success", foreground=LEVEL_COLORS["success"])

        yscroll = ttk.Scrollbar(self, orient="vertical", command=self.text.yview)
        self.text.configure(yscrollcommand=yscroll.set)
        self.text.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")

    def append(self, message: str, level: str = "info") -> None:
        tag = level if level in LEVEL_COLORS else "info"
        self.text.configure(state="normal")
        self.text.insert("end", message + "\n", tag)
        if int(self.text.index("end-1c").split(".")[0]) > self.MAX_LINES:
            self.text.delete("1.0", "200.0")
        self.text.configure(state="disabled")
        self.text.see("end")

    def clear(self) -> None:
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.configure(state="disabled")

    def value(self) -> str:
        return self.text.get("1.0", "end-1c")
