"""engine 单元测试：用假连接覆盖元数据解析、并发、错误分类、取消与规模控制。

不依赖任何真实数据库。
"""
from __future__ import annotations

import threading
import time
import unittest

from src import engine, sqlbuild
from src.models import (
    ColumnRef,
    ConnectionProfile,
    HitStatus,
    MatchMode,
    SearchRequest,
)

# ---------------------------------------------------------------- 假连接


class FakeCursor:
    def __init__(self, conn: "FakeConnection") -> None:
        self._conn = conn
        self.closed = False
        self.sql = ""
        self.params: tuple = ()
        self.description = None

    def execute(self, sql: str, params: tuple = ()) -> None:
        self.sql = sql
        self.params = params
        result = self._conn.handler(sql, params)
        if isinstance(result, Exception):
            raise result
        # handler 返回 (列名列表, 行列表) 时视为明细查询，需要填充 description
        if (
            isinstance(result, tuple)
            and len(result) == 2
            and isinstance(result[0], list)
            and isinstance(result[1], list)
        ):
            columns, rows = result
            self.description = [(name,) for name in columns]
            self.rows = rows
        else:
            self.description = None
            self.rows = result
        self._conn.executed.append((sql, params))

    def fetchall(self) -> list:
        return list(getattr(self, "rows", []))

    def fetchone(self):
        rows = getattr(self, "rows", [])
        return rows[0] if rows else None

    def close(self) -> None:
        self.closed = True


class FakeConnection:
    """极简 DB-API 连接替身。handler(sql, params) 返回行列表或异常。"""

    def __init__(self, handler, label: str = "conn") -> None:
        self.handler = handler
        self.label = label
        self.closed = False
        self.timeout = None
        self.executed: list[tuple[str, tuple]] = []

    def cursor(self) -> FakeCursor:
        return FakeCursor(self)

    def close(self) -> None:
        self.closed = True


METADATA_ROWS = [
    ("dbo", "Users", "BASE TABLE", "UserID", "int"),
    ("dbo", "Users", "BASE TABLE", "UserName", "nvarchar"),
    ("dbo", "Orders", "BASE TABLE", "OrderNo", "varchar"),
    ("sales", "vOrders", "VIEW", "CustomerName", "nvarchar"),
]


def make_handler(
    metadata: list | None = None,
    content: dict[str, list] | None = None,
    content_error: dict[str, Exception] | None = None,
    delay: float = 0.0,
):
    metadata = METADATA_ROWS if metadata is None else metadata
    content = content or {}
    content_error = content_error or {}

    def handler(sql: str, params: tuple):
        if "INFORMATION_SCHEMA" in sql:
            return metadata
        if "DB_NAME()" in sql:
            return [("CoolBuddy", "sa", "Microsoft SQL Server 2019 (RTM)\r\nmore")]
        if delay:
            time.sleep(delay)
        key = next(
            (
                k
                for k in list(content) + list(content_error)
                if sqlbuild.quote_qualified(*k.split(".")[:2]) in sql
                and sqlbuild.quote_ident(k.split(".")[2]) in sql
            ),
            None,
        )
        if key is not None:
            if key in content_error:
                return content_error[key]
            return content[key]
        return []

    return handler


def make_connect(handler, created: list | None = None):
    created = created if created is not None else []

    def connect() -> FakeConnection:
        conn = FakeConnection(handler, label=f"conn-{len(created)}")
        created.append(conn)
        return conn

    return connect, created


def collect_events():
    events: list = []

    def emit(evt) -> None:
        events.append(evt)

    return events, emit


# ---------------------------------------------------------------- 测试


class TestReadColumns(unittest.TestCase):
    def test_parses_rows(self) -> None:
        conn = FakeConnection(make_handler())
        refs = engine.read_columns(conn)
        self.assertEqual(len(refs), 4)
        first = refs[0]
        self.assertEqual(
            (first.schema, first.table, first.column, first.object_type, first.data_type),
            ("dbo", "Users", "UserID", "BASE TABLE", "int"),
        )

    def test_view_flagged(self) -> None:
        refs = engine.read_columns(FakeConnection(make_handler()))
        self.assertTrue(refs[3].is_view)
        self.assertEqual(refs[3].object_label, "视图")
        self.assertFalse(refs[0].is_view)

    def test_none_values_become_empty(self) -> None:
        rows = [(None, "T", None, "C", None)]
        refs = engine.read_columns(FakeConnection(make_handler(metadata=rows)))
        self.assertEqual(refs[0].schema, "")
        self.assertEqual(refs[0].object_type, "BASE TABLE")

    def test_metadata_sql_targets_information_schema(self) -> None:
        conn = FakeConnection(make_handler())
        engine.read_columns(conn)
        self.assertIn("INFORMATION_SCHEMA", conn.executed[0][0])

    def test_readonly(self) -> None:
        conn = FakeConnection(make_handler())
        engine.read_columns(conn)
        upper = conn.executed[0][0].upper()
        for kw in ("INSERT", "UPDATE ", "DELETE", "DROP", "ALTER", "TRUNCATE"):
            self.assertNotIn(kw, upper)


class TestErrorClassification(unittest.TestCase):
    def test_permission(self) -> None:
        exc = Exception("The SELECT permission was denied on the object 'T'")
        self.assertIs(engine.classify_error(exc), HitStatus.DENIED)

    def test_permission_sqlstate_229(self) -> None:
        self.assertIs(engine.classify_error(Exception("(229) ...")), HitStatus.DENIED)

    def test_timeout_text(self) -> None:
        self.assertIs(engine.classify_error(Exception("Query timeout expired")), HitStatus.TIMEOUT)

    def test_timeout_sqlstate(self) -> None:
        self.assertIs(engine.classify_error(Exception("HYT00 something")), HitStatus.TIMEOUT)

    def test_generic_error(self) -> None:
        self.assertIs(engine.classify_error(Exception("Invalid column name")), HitStatus.ERROR)

    def test_describe_truncates(self) -> None:
        text = engine.describe_error(Exception("x" * 1000), limit=50)
        self.assertEqual(len(text), 50)
        self.assertTrue(text.endswith("…"))

    def test_describe_flattens_newlines(self) -> None:
        self.assertNotIn("\n", engine.describe_error(Exception("a\nb")))


class TestConnectionPool(unittest.TestCase):
    def test_same_thread_reuses_connection(self) -> None:
        pool = engine.ConnectionPool(lambda: FakeConnection(make_handler()))
        a = pool.acquire()
        b = pool.acquire()
        self.assertIs(a, b)
        self.assertEqual(pool.created_count, 1)

    def test_one_connection_per_thread(self) -> None:
        pool = engine.ConnectionPool(lambda: FakeConnection(make_handler()))
        seen: list[int] = []
        lock = threading.Lock()

        def work() -> None:
            conn = pool.acquire()
            with lock:
                seen.append(id(conn))

        threads = [threading.Thread(target=work) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(set(seen)), 5)
        self.assertEqual(pool.created_count, 5)

    def test_discard_forces_reconnect(self) -> None:
        created: list = []
        pool = engine.ConnectionPool(lambda: created.append(1) or FakeConnection(make_handler()))
        first = pool.acquire()
        pool.discard()
        second = pool.acquire()
        self.assertIsNot(first, second)
        self.assertTrue(first.closed)
        self.assertEqual(pool.created_count, 1)

    def test_close_all_closes_everything(self) -> None:
        conns: list[FakeConnection] = []

        def factory() -> FakeConnection:
            c = FakeConnection(make_handler())
            conns.append(c)
            return c

        pool = engine.ConnectionPool(factory)
        pool.acquire()
        pool.close_all()
        self.assertTrue(all(c.closed for c in conns))
        self.assertEqual(pool.created_count, 0)

    def test_close_all_is_idempotent(self) -> None:
        pool = engine.ConnectionPool(lambda: FakeConnection(make_handler()))
        pool.acquire()
        pool.close_all()
        pool.close_all()


class TestProbeColumn(unittest.TestCase):
    req = SearchRequest(
        field_keyword="UserName",
        content_keyword="zhang",
        content_mode=MatchMode.FUZZY,
        ignore_case=False,
    )
    from src.models import ColumnRef as _CR  # noqa: PLC0414

    ref = _CR("dbo", "Users", "UserName", "BASE TABLE", "nvarchar")

    def test_match_returns_sample(self) -> None:
        conn = FakeConnection(make_handler(content={"dbo.Users.UserName": [("张三",), ("zhang",)]}))
        hit = engine.probe_column(conn, self.ref, self.req)
        self.assertIs(hit.status, HitStatus.MATCH)
        self.assertEqual(hit.rows, 2)
        self.assertIn("张三", hit.sample)

    def test_no_match(self) -> None:
        conn = FakeConnection(make_handler(content={}))
        hit = engine.probe_column(conn, self.ref, self.req)
        self.assertIs(hit.status, HitStatus.NO_MATCH)

    def test_count_mode(self) -> None:
        req = SearchRequest(
            field_keyword="UserName", content_keyword="x", count_rows=True
        )
        conn = FakeConnection(make_handler(content={"dbo.Users.UserName": [(7,)]}))
        hit = engine.probe_column(conn, self.ref, req)
        self.assertIs(hit.status, HitStatus.MATCH)
        self.assertEqual(hit.rows, 7)

    def test_count_mode_zero(self) -> None:
        req = SearchRequest(field_keyword="UserName", content_keyword="x", count_rows=True)
        conn = FakeConnection(make_handler(content={"dbo.Users.UserName": [(0,)]}))
        hit = engine.probe_column(conn, self.ref, req)
        self.assertIs(hit.status, HitStatus.NO_MATCH)
        self.assertEqual(hit.rows, 0)

    def test_permission_error_is_captured_not_raised(self) -> None:
        conn = FakeConnection(
            make_handler(content_error={"dbo.Users.UserName": Exception("permission denied")})
        )
        hit = engine.probe_column(conn, self.ref, self.req)
        self.assertIs(hit.status, HitStatus.DENIED)
        self.assertTrue(hit.detail)

    def test_case_sensitive_flag_reaches_sql(self) -> None:
        conn = FakeConnection(make_handler(content={}))
        engine.probe_column(conn, self.ref, self.req)
        self.assertIn("Latin1_General_CS_AS", conn.executed[0][0])

    def test_nolock_can_be_disabled(self) -> None:
        req = SearchRequest(field_keyword="UserName", content_keyword="x", with_nolock=False)
        conn = FakeConnection(make_handler(content={}))
        engine.probe_column(conn, self.ref, req)
        self.assertNotIn("NOLOCK", conn.executed[0][0])

    def test_params_are_bound_not_inlined(self) -> None:
        req = SearchRequest(field_keyword="UserName", content_keyword="' OR 1=1--")
        conn = FakeConnection(make_handler(content={}))
        engine.probe_column(conn, self.ref, req)
        sql, params = conn.executed[0]
        self.assertNotIn("1=1", sql)
        self.assertEqual(params, ("%' OR 1=1--%",))


class TestRunScan(unittest.TestCase):
    def _req(self, **kw) -> SearchRequest:
        base = dict(
            field_keyword="User",
            field_mode=MatchMode.FUZZY,
            content_keyword="zhang",
            content_mode=MatchMode.FUZZY,
            ignore_case=True,
            max_workers=4,
        )
        base.update(kw)
        return SearchRequest(**base)

    def test_end_to_end(self) -> None:
        handler = make_handler(
            content={"dbo.Users.UserName": [("zhangsan",)], "dbo.Users.UserID": [(1,)]}
        )
        connect, _ = make_connect(handler)
        events, emit = collect_events()
        stats = engine.run_scan(
            ConnectionProfile(server="h", database="d"), self._req(), connect, emit
        )

        stage_a = [e for e in events if isinstance(e, engine.StageAEvent)]
        self.assertEqual(len(stage_a), 1)
        self.assertEqual(
            [r.column for r in stage_a[0].targets], ["UserID", "UserName"]
        )

        done = [e for e in events if isinstance(e, engine.DoneEvent)]
        self.assertEqual(len(done), 1)
        self.assertEqual(stats.matched, 2)
        self.assertEqual(stats.total_targets, 2)
        self.assertEqual(stats.finished, 2)
        self.assertFalse(stats.cancelled)
        self.assertEqual(len(done[0].results), 2)
        # 命中项排在前面
        self.assertTrue(all(h.status is HitStatus.MATCH for h in done[0].results))

    def test_no_field_match_skips_content_stage(self) -> None:
        connect, created = make_connect(make_handler())
        events, emit = collect_events()
        stats = engine.run_scan(
            ConnectionProfile(server="h", database="d"),
            self._req(field_keyword="不存在的字段"),
            connect,
            emit,
        )
        self.assertEqual(stats.total_targets, 0)
        self.assertEqual(stats.matched, 0)
        self.assertEqual(len(created), 1)  # 只建了元数据那一条连接
        self.assertTrue(any("未找到匹配字段" in e.message for e in events if hasattr(e, "message")))

    def test_content_stage_can_be_disabled(self) -> None:
        connect, _ = make_connect(make_handler())
        events, emit = collect_events()
        stats = engine.run_scan(
            ConnectionProfile(server="h", database="d"),
            self._req(run_content_stage=False),
            connect,
            emit,
        )
        self.assertEqual(stats.total_targets, 0)
        self.assertTrue(any("已关闭内容检索" in e.message for e in events if hasattr(e, "message")))

    def test_connection_failure_is_reported(self) -> None:
        def connect():
            raise RuntimeError("Login failed for user 'sa'.")

        events, emit = collect_events()
        stats = engine.run_scan(
            ConnectionProfile(server="h", database="d"), self._req(), connect, emit
        )
        errors = [e for e in events if isinstance(e, engine.LogEvent) and e.level == "error"]
        self.assertEqual(len(errors), 1)
        self.assertIn("Login failed", errors[0].message)
        self.assertEqual(stats.failed, 1)
        self.assertTrue(any(isinstance(e, engine.DoneEvent) for e in events))

    def test_metadata_failure_is_reported(self) -> None:
        def handler(sql, params):
            if "INFORMATION_SCHEMA" in sql:
                return RuntimeError("Invalid object name")
            return []

        connect, _ = make_connect(handler)
        events, emit = collect_events()
        stats = engine.run_scan(
            ConnectionProfile(server="h", database="d"), self._req(), connect, emit
        )
        self.assertEqual(stats.failed, 1)
        self.assertTrue(any(isinstance(e, engine.DoneEvent) for e in events))

    def test_max_targets_clamped(self) -> None:
        connect, _ = make_connect(make_handler(content={}))
        events, emit = collect_events()
        stats = engine.run_scan(
            ConnectionProfile(server="h", database="d"),
            self._req(max_targets=1),
            connect,
            emit,
        )
        self.assertEqual(stats.total_targets, 1)
        self.assertTrue(
            any("超过上限" in e.message for e in events if isinstance(e, engine.LogEvent))
        )

    def test_cancel_before_start_finishes_immediately(self) -> None:
        connect, _ = make_connect(make_handler(content={}))
        events, emit = collect_events()
        cancel = threading.Event()
        cancel.set()
        stats = engine.run_scan(
            ConnectionProfile(server="h", database="d"),
            self._req(),
            connect,
            emit,
            cancel_event=cancel,
        )
        self.assertTrue(stats.cancelled)
        self.assertEqual(stats.finished, 0)

    def test_workers_do_not_share_connections(self) -> None:
        """核心线程安全断言：每个工作线程各持一条连接，且连接不被跨线程复用。"""
        pairs: list[tuple[str, int]] = []
        created: list[FakeConnection] = []
        lock = threading.Lock()

        def connect() -> FakeConnection:
            holder: dict[str, FakeConnection] = {}

            def handler(sql: str, params: tuple):
                if "INFORMATION_SCHEMA" in sql:
                    return METADATA_ROWS
                with lock:
                    pairs.append((threading.current_thread().name, id(holder["conn"])))
                return []

            conn = FakeConnection(handler)
            holder["conn"] = conn
            created.append(conn)
            return conn

        _events, emit = collect_events()
        engine.run_scan(
            ConnectionProfile(server="h", database="d"),
            self._req(max_workers=4),
            connect,
            emit,
        )

        self.assertTrue(pairs, "内容检索阶段未产生任何探测")
        per_thread: dict[str, set[int]] = {}
        for thread_name, conn_id in pairs:
            per_thread.setdefault(thread_name, set()).add(conn_id)

        for thread_name, conn_ids in per_thread.items():
            self.assertEqual(len(conn_ids), 1, f"{thread_name} 复用了多条连接")
        used = [next(iter(ids)) for ids in per_thread.values()]
        self.assertEqual(len(used), len(set(used)), "同一连接被多个线程共享")

        # 连接总数 = 1 条元数据连接 + 实际投入工作的线程数（不超线程上限）
        self.assertEqual(len(created), 1 + len(per_thread))
        self.assertLessEqual(len(per_thread), 4)

    def test_progress_and_hits_streamed(self) -> None:
        content = {
            "dbo.Users.UserID": [(1,)],
            "dbo.Users.UserName": [(2,)],
        }
        connect, _ = make_connect(make_handler(content=content))
        events, emit = collect_events()
        engine.run_scan(
            ConnectionProfile(server="h", database="d"), self._req(), connect, emit
        )
        hits = [e for e in events if isinstance(e, engine.HitEvent)]
        self.assertEqual(len(hits), 2)
        progress = [e for e in events if isinstance(e, engine.ProgressEvent)]
        self.assertTrue(progress)
        self.assertEqual(progress[-1].stats.finished, 2)

    def test_non_matches_are_not_streamed_as_hits(self) -> None:
        connect, _ = make_connect(make_handler(content={}))
        events, emit = collect_events()
        stats = engine.run_scan(
            ConnectionProfile(server="h", database="d"), self._req(), connect, emit
        )
        hits = [e for e in events if isinstance(e, engine.HitEvent)]
        self.assertEqual(hits, [])
        self.assertEqual(stats.finished, 2)


class TestFetchRows(unittest.TestCase):
    """双击查看命中明细所用的取数函数。"""

    ref = ColumnRef("dbo", "Users", "UserName", "BASE TABLE", "nvarchar")
    request = SearchRequest(
        field_keyword="UserName",
        content_keyword="zhang",
        content_mode=MatchMode.FUZZY,
        ignore_case=False,
    )

    def _conn(self, rows, columns=("UserID", "UserName")) -> FakeConnection:
        def handler(_sql, _params):
            return (list(columns), list(rows))

        return FakeConnection(handler)

    def test_returns_columns_and_stringified_rows(self) -> None:
        conn = self._conn([(1, "zhangsan"), (2, "zhangli")])
        columns, rows = engine.fetch_rows(conn, self.ref, self.request)
        self.assertEqual(columns, ["UserID", "UserName"])
        self.assertEqual(rows, [("1", "zhangsan"), ("2", "zhangli")])

    def test_none_becomes_empty_string(self) -> None:
        columns, rows = engine.fetch_rows(self._conn([(None, None)]), self.ref, self.request)
        self.assertEqual(columns, ["UserID", "UserName"])
        self.assertEqual(rows, [("", "")])

    def test_empty_result(self) -> None:
        columns, rows = engine.fetch_rows(self._conn([]), self.ref, self.request)
        self.assertEqual(columns, ["UserID", "UserName"])
        self.assertEqual(rows, [])

    def test_sql_selects_all_columns_with_predicate(self) -> None:
        conn = self._conn([])
        engine.fetch_rows(conn, self.ref, self.request)
        sql, params = conn.executed[0]
        self.assertIn("SELECT TOP (200) *", sql)
        self.assertIn("[dbo].[Users]", sql)
        self.assertIn("LIKE", sql)
        self.assertIn("Latin1_General_CS_AS", sql)
        self.assertEqual(params, ("%zhang%",))

    def test_limit_is_forwarded(self) -> None:
        conn = self._conn([])
        engine.fetch_rows(conn, self.ref, self.request, limit=7)
        self.assertIn("TOP (7)", conn.executed[0][0])

    def test_cursor_is_closed(self) -> None:
        conn = self._conn([])
        captured = {}
        original = conn.cursor

        def spy():
            cur = original()
            captured["cursor"] = cur
            return cur

        conn.cursor = spy  # type: ignore[method-assign]
        engine.fetch_rows(conn, self.ref, self.request)
        self.assertTrue(captured["cursor"].closed)

    def test_errors_propagate_to_caller(self) -> None:
        def handler(_sql, _params):
            return RuntimeError("Invalid object name 'Users'")

        with self.assertRaises(RuntimeError):
            engine.fetch_rows(FakeConnection(handler), self.ref, self.request)

    def test_fetch_rows_with_opens_and_closes_connection(self) -> None:
        created: list[FakeConnection] = []

        def connect() -> FakeConnection:
            conn = self._conn([(1, "zhangsan")])
            created.append(conn)
            return conn

        columns, rows = engine.fetch_rows_with(connect, self.ref, self.request)
        self.assertEqual(columns, ["UserID", "UserName"])
        self.assertEqual(rows, [("1", "zhangsan")])
        self.assertEqual(len(created), 1)
        self.assertTrue(created[0].closed)

    def test_fetch_rows_with_closes_on_error(self) -> None:
        created: list[FakeConnection] = []

        def connect() -> FakeConnection:
            conn = FakeConnection(lambda _s, _p: RuntimeError("boom"))
            created.append(conn)
            return conn

        with self.assertRaises(RuntimeError):
            engine.fetch_rows_with(connect, self.ref, self.request)
        self.assertTrue(created[0].closed)


class TestProbeServer(unittest.TestCase):
    def test_returns_summary(self) -> None:
        connect, _ = make_connect(make_handler())
        text = engine.probe_server(connect)
        self.assertIn("测试成功", text)
        self.assertIn("CoolBuddy", text)
        self.assertIn("sa", text)

    def test_closes_connection(self) -> None:
        connect, created = make_connect(make_handler())
        engine.probe_server(connect)
        self.assertTrue(all(c.closed for c in created))

    def test_info_is_structured(self) -> None:
        connect, _ = make_connect(make_handler())
        info = engine.probe_server_info(connect)
        self.assertEqual(info.database, "CoolBuddy")
        self.assertEqual(info.login, "sa")
        self.assertIn("Microsoft SQL Server 2019", info.version)

    def test_summary_has_only_result_database_and_login(self) -> None:
        """需求：弹窗只需显示结果、当前库、登录身份。"""
        connect, _ = make_connect(make_handler())
        summary = engine.probe_server_info(connect).summary()
        self.assertEqual(summary, "测试成功。当前库：CoolBuddy；登录身份：sa")
        self.assertNotIn("Microsoft", summary)

    def test_full_text_adds_version(self) -> None:
        connect, _ = make_connect(make_handler())
        full = engine.probe_server_info(connect).full_text()
        self.assertIn("Microsoft SQL Server 2019", full)

    def test_missing_row_yields_short_summary(self) -> None:
        info = engine.ServerInfo()
        self.assertEqual(info.summary(), "测试成功。")
        self.assertEqual(info.full_text(), "测试成功。")

    def test_summary_with_partial_info(self) -> None:
        self.assertEqual(
            engine.ServerInfo(database="ERP").summary(), "测试成功。当前库：ERP"
        )
        self.assertEqual(
            engine.ServerInfo(login="sa").summary(), "测试成功。登录身份：sa"
        )

    def test_long_summary_stays_single_line(self) -> None:
        """文案由界面负责换行，不应自带换行符。"""
        info = engine.ServerInfo(database="A" * 120, login="B" * 120)
        self.assertNotIn("\n", info.summary())


if __name__ == "__main__":
    unittest.main(verbosity=2)
