"""引擎层：元数据发现 + 多线程内容检索。

设计要点
--------
* **连接工厂注入**：``run_scan`` 只接收一个 ``connect`` 可调用对象而非连接串，
  因此可用假连接完整测试并发、错误分类与取消逻辑，无需真实数据库。
* **线程本地连接**：pyodbc 的连接对象不是线程安全的，每个工作线程持有各自连接。
* **事件回调**：工作线程只调用 ``emit``（UI 侧实现为 ``queue.put``），
  绝不触碰 Tk 控件。``emit`` 必须线程安全。
* **取消语义**：``cancel_event`` 置位后，工作线程在**任务边界**退出；
  正在执行的语句由查询超时收敛。
"""
from __future__ import annotations

import queue
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from . import connstr, drivers, matcher, sqlbuild
from .models import (
    ColumnRef,
    ConnectionProfile,
    Hit,
    HitStatus,
    ScanStats,
    SearchRequest,
)

# ---------------------------------------------------------------- 事件


@dataclass
class LogEvent:
    """一条运行日志。level ∈ info / warn / error / success。"""

    message: str
    level: str = "info"


@dataclass
class StageAEvent:
    """阶段 A（字段定位）完成，给出将进入阶段 B 的目标列。"""

    targets: list[ColumnRef] = field(default_factory=list)
    total_columns: int = 0


@dataclass
class HitEvent:
    """单个目标列的检索结论。"""

    hit: Hit


@dataclass
class ProgressEvent:
    stats: ScanStats


@dataclass
class DoneEvent:
    stats: ScanStats
    results: list[Hit] = field(default_factory=list)


EmitFn = Callable[[object], None]


# ---------------------------------------------------------------- 连接


class ConnectionPool:
    """按线程分发连接：同一线程复用一条，不同线程互不共享。"""

    def __init__(self, factory: Callable[[], Any]) -> None:
        self._factory = factory
        self._local = threading.local()
        self._created: list[Any] = []
        self._lock = threading.Lock()

    def acquire(self) -> Any:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = self._factory()
            self._local.conn = conn
            with self._lock:
                self._created.append(conn)
        return conn

    def discard(self) -> None:
        """丢弃当前线程的连接（用于连接已损坏的场景，下次自动重连）。"""
        conn = getattr(self._local, "conn", None)
        self._local.conn = None
        if conn is not None:
            with self._lock:
                if conn in self._created:
                    self._created.remove(conn)
            _safe_close(conn)

    @property
    def created_count(self) -> int:
        with self._lock:
            return len(self._created)

    def close_all(self) -> None:
        with self._lock:
            conns, self._created = self._created, []
        for conn in conns:
            _safe_close(conn)


def _safe_close(conn: Any) -> None:
    try:
        conn.close()
    except Exception:  # noqa: BLE001 - 关闭失败不应影响主流程
        pass


def build_connection_factory(profile: ConnectionProfile) -> Callable[[], Any]:
    """构造 pyodbc 连接工厂（惰性导入 pyodbc，便于无驱动环境导入本模块）。"""
    import pyodbc  # noqa: PLC0415

    cs = connstr.build(profile)
    query_timeout = int(profile.query_timeout)
    login_timeout = int(profile.connect_timeout)

    def factory() -> Any:
        conn = pyodbc.connect(cs, autocommit=True, timeout=login_timeout)
        conn.timeout = query_timeout  # 语句级超时，防止慢表拖死工作线程
        return conn

    return factory


# ---------------------------------------------------------------- 错误分类


def classify_error(exc: BaseException) -> HitStatus:
    """把底层异常归到可读的终态。"""
    text = f"{type(exc).__name__}: {exc}".lower()
    if "permission" in text or "denied" in text or "229" in text:
        return HitStatus.DENIED
    if "timeout" in text or "timed out" in text or "hyt00" in text or "hyt01" in text:
        return HitStatus.TIMEOUT
    return HitStatus.ERROR


def describe_error(exc: BaseException, limit: int = 300) -> str:
    text = f"{type(exc).__name__}: {exc}".replace("\n", " ").strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


# ---------------------------------------------------------------- 元数据


def read_columns(conn: Any) -> list[ColumnRef]:
    """从一条连接读取全库列元数据（用户表 + 视图）。"""
    cur = conn.cursor()
    try:
        cur.execute(sqlbuild.METADATA_SQL)
        rows = cur.fetchall()
    finally:
        _safe_close_cursor(cur)

    refs: list[ColumnRef] = []
    for row in rows:
        refs.append(
            ColumnRef(
                schema=_text(row[0]),
                table=_text(row[1]),
                column=_text(row[3]),
                object_type=_text(row[2]) or "BASE TABLE",
                data_type=_text(row[4]),
            )
        )
    return refs


def _safe_close_cursor(cur: Any) -> None:
    try:
        cur.close()
    except Exception:  # noqa: BLE001
        pass


def _text(value: Any) -> str:
    return "" if value is None else str(value)


@dataclass(frozen=True)
class ServerInfo:
    """连接自检拿到的服务器信息。"""

    database: str = ""
    login: str = ""
    version: str = ""

    def summary(self) -> str:
        """界面展示用的精简文案：只保留结果、当前库与登录身份。"""
        if not self.database and not self.login:
            return "测试成功。"
        parts = []
        if self.database:
            parts.append(f"当前库：{self.database}")
        if self.login:
            parts.append(f"登录身份：{self.login}")
        return "测试成功。" + "；".join(parts)

    def full_text(self) -> str:
        """日志展示用的完整文案（含版本号）。"""
        text = self.summary()
        return f"{text}；{self.version}" if self.version else text


def probe_server_info(connect: Callable[[], Any]) -> ServerInfo:
    """连接自检：返回结构化的服务器信息。

    拆成结构化返回而非一句话，是为了让界面能按需要裁剪展示内容
    （例如弹窗只显示结果/当前库/登录身份，日志再带上版本号）。
    """
    conn = connect()
    try:
        cur = conn.cursor()
        try:
            cur.execute("SELECT DB_NAME(), SUSER_SNAME(), @@VERSION")
            row = cur.fetchone()
        finally:
            _safe_close_cursor(cur)
    finally:
        _safe_close(conn)
    if not row:
        return ServerInfo()
    version_raw = _text(row[2]) if len(row) > 2 else ""
    return ServerInfo(
        database=_text(row[0]),
        login=_text(row[1]),
        version=version_raw.splitlines()[0] if version_raw else "",
    )


def probe_server(connect: Callable[[], Any]) -> str:
    """连接自检：返回一段可展示的完整文本（含版本号，用于日志）。"""
    return probe_server_info(connect).full_text()


# ---------------------------------------------------------------- 检索任务


def probe_column(conn: Any, ref: ColumnRef, request: SearchRequest) -> Hit:
    """对单列执行一次内容探测，返回终态（不抛异常）。"""
    try:
        sql, params = sqlbuild.build_probe_sql(
            ref,
            request.content_keyword,
            request.content_mode,
            request.ignore_case,
            collation_base=request.collation_base,
            with_nolock=request.with_nolock,
            count_rows=request.count_rows,
            sample_size=request.sample_size,
        )
    except ValueError as exc:
        return Hit(ref, HitStatus.ERROR, detail=describe_error(exc))

    try:
        cur = conn.cursor()
        try:
            cur.execute(sql, params)
            rows = cur.fetchall()
        finally:
            _safe_close_cursor(cur)
    except Exception as exc:  # noqa: BLE001 - 单列失败必须降级为结果，不能中断整体
        return Hit(ref, classify_error(exc), detail=describe_error(exc))

    if request.count_rows:
        count = int(rows[0][0]) if rows and rows[0] and rows[0][0] is not None else 0
        status = HitStatus.MATCH if count > 0 else HitStatus.NO_MATCH
        return Hit(ref, status, rows=count)

    if not rows:
        return Hit(ref, HitStatus.NO_MATCH)

    samples = [_text(r[0]) if r else "" for r in rows]
    sample = " | ".join(s[:80] for s in samples)
    return Hit(ref, HitStatus.MATCH, rows=len(rows), sample=sample)


def fetch_rows(
    conn: Any,
    ref: ColumnRef,
    request: SearchRequest,
    limit: int = sqlbuild.DETAIL_ROW_LIMIT,
) -> tuple[list[str], list[tuple[str, ...]]]:
    """取回某表满足内容条件的前 N 行「全部列」，供明细窗口展示。

    与 :func:`probe_column` 共用同一套谓词与排序规则，因此明细窗口看到的行
    与结果表判定为「命中」的行完全一致。
    """
    sql, params = sqlbuild.build_detail_sql(
        ref,
        request.content_keyword,
        request.content_mode,
        request.ignore_case,
        collation_base=request.collation_base,
        with_nolock=request.with_nolock,
        limit=limit,
    )
    cur = conn.cursor()
    try:
        cur.execute(sql, params)
        rows = cur.fetchall()
        description = getattr(cur, "description", None) or []
        columns = [_text(col[0]) for col in description]
    finally:
        _safe_close_cursor(cur)

    return columns, [
        tuple(_text(value) for value in row) for row in rows
    ]


def fetch_rows_with(
    connect: Callable[[], Any],
    ref: ColumnRef,
    request: SearchRequest,
    limit: int = sqlbuild.DETAIL_ROW_LIMIT,
) -> tuple[list[str], list[tuple[str, ...]]]:
    """自建连接取回明细数据，用完即关（明细窗口是低频操作，不必复用连接池）。"""
    conn = connect()
    try:
        return fetch_rows(conn, ref, request, limit)
    finally:
        _safe_close(conn)


def _worker(
    task_queue: "queue.Queue[ColumnRef]",
    pool: ConnectionPool,
    request: SearchRequest,
    emit: EmitFn,
    cancel_event: threading.Event,
    stats: ScanStats,
    results: list[Hit],
    lock: threading.Lock,
    total: int,
) -> None:
    while not cancel_event.is_set():
        try:
            ref = task_queue.get_nowait()
        except queue.Empty:
            return

        try:
            conn = pool.acquire()
            hit = probe_column(conn, ref, request)
            if hit.status in (HitStatus.ERROR, HitStatus.TIMEOUT):
                # 连接可能已损坏，丢弃以便下次重建，避免后续列连续失败
                pool.discard()
        except Exception as exc:  # noqa: BLE001 - 建连失败
            hit = Hit(ref, classify_error(exc), detail=describe_error(exc))

        # 「未命中」占绝大多数，不回传以抑制 UI 噪音（数量由 ProgressEvent 覆盖）
        reportable = hit.status is not HitStatus.NO_MATCH
        if reportable:
            with lock:
                results.append(hit)

        with lock:
            stats.finished += 1
            if hit.status is HitStatus.MATCH:
                stats.matched += 1
            elif hit.status not in (HitStatus.NO_MATCH, HitStatus.CANCELLED):
                stats.failed += 1
            should_report = stats.finished % 10 == 0 or stats.finished >= total
            snapshot = ScanStats(**vars(stats)) if should_report else None

        if reportable:
            emit(HitEvent(hit))
        if snapshot is not None:
            emit(ProgressEvent(snapshot))


# ---------------------------------------------------------------- 主流程


def run_scan(
    profile: ConnectionProfile,
    request: SearchRequest,
    connect: Callable[[], Any],
    emit: EmitFn,
    cancel_event: threading.Event | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> ScanStats:
    """执行完整检索流程，通过 ``emit`` 投递事件，返回最终统计。

    流程：连接自检 → 阶段 A（拉元数据 + 客户端字段名过滤）→
    阶段 B（线程池逐列内容探测）→ 汇总。
    """
    started = clock()
    cancel_event = cancel_event or threading.Event()
    stats = ScanStats()
    results: list[Hit] = []

    def finish(cancelled: bool = False) -> ScanStats:
        stats.elapsed = clock() - started
        stats.cancelled = cancelled
        emit(DoneEvent(stats, sorted(results, key=_result_sort_key)))
        return stats

    # ---- 阶段 A：字段定位 ----
    emit(LogEvent(f"正在读取 {profile.server}/{profile.database} 的列元数据…"))
    try:
        conn = connect()
    except Exception as exc:  # noqa: BLE001
        message = describe_error(exc)
        emit(LogEvent(f"连接失败：{message}", "error"))
        stats.failed = 1
        return finish()

    try:
        refs = read_columns(conn)
    except Exception as exc:  # noqa: BLE001
        emit(LogEvent(f"读取元数据失败：{describe_error(exc)}", "error"))
        stats.failed = 1
        return finish()
    finally:
        _safe_close(conn)

    emit(LogEvent(f"元数据读取完成：共 {len(refs)} 个列（含用户表与视图）。"))

    targets = matcher.filter_columns(
        refs, request.field_keyword, request.field_mode, request.ignore_case
    )
    emit(
        LogEvent(
            f"字段名「{request.field_keyword}」（{request.field_mode.label}、"
            f"{'忽略' if request.ignore_case else '区分'}大小写）命中 "
            f"{len(targets)} 个列。",
            "success" if targets else "warn",
        )
    )
    emit(StageAEvent(targets=targets, total_columns=len(refs)))

    if not targets:
        emit(LogEvent("未找到匹配字段，流程结束。", "warn"))
        return finish()

    if not request.run_content_stage:
        emit(LogEvent("已关闭内容检索，仅输出字段定位结果。"))
        stats.total_targets = 0
        return finish()

    if len(targets) > request.max_targets:
        emit(
            LogEvent(
                f"命中列数 {len(targets)} 超过上限 {request.max_targets}，"
                f"仅检索前 {request.max_targets} 个。",
                "warn",
            )
        )
        targets = targets[: request.max_targets]

    # ---- 阶段 B：内容定位（多线程） ----
    total = len(targets)
    stats.total_targets = total
    workers = max(1, min(int(request.max_workers), total))
    emit(
        LogEvent(
            f"开始内容检索：{total} 个列 / {workers} 个线程，"
            f"内容「{request.content_keyword}」"
            f"（{request.content_mode.label}、"
            f"{'忽略' if request.ignore_case else '区分'}大小写）"
            f"{'、计数模式' if request.count_rows else '、存在性模式'}。"
        )
    )

    task_queue: "queue.Queue[ColumnRef]" = queue.Queue()
    for ref in targets:
        task_queue.put(ref)

    pool = ConnectionPool(connect)
    lock = threading.Lock()
    threads = [
        threading.Thread(
            target=_worker,
            args=(
                task_queue,
                pool,
                request,
                emit,
                cancel_event,
                stats,
                results,
                lock,
                total,
            ),
            name=f"scanner-{i}",
            daemon=True,
        )
        for i in range(workers)
    ]
    try:
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    except KeyboardInterrupt:  # pragma: no cover - 交互式中断
        cancel_event.set()
    finally:
        pool.close_all()

    cancelled = cancel_event.is_set()
    if cancelled:
        emit(LogEvent("已取消：未派发的列已跳过。", "warn"))
    emit(
        LogEvent(
            f"检索完成：命中 {stats.matched} 个列，"
            f"异常 {stats.failed} 个，耗时 {clock() - started:.1f}s。",
            "success" if stats.matched else "info",
        )
    )
    return finish(cancelled=cancelled)


def _result_sort_key(hit: Hit) -> tuple:
    """命中优先，其次按 库表列 字典序，便于人工阅读。"""
    return (
        0 if hit.status is HitStatus.MATCH else 1,
        hit.ref.schema,
        hit.ref.table,
        hit.ref.column,
    )
