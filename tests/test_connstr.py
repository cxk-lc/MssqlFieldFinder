"""drivers 与 connstr 单元测试：驱动优选、连接串构造、参数校验。"""
from __future__ import annotations

import unittest

from src import connstr, drivers
from src.models import AuthMode, ConnectionProfile


class TestPickDriver(unittest.TestCase):
    def test_prefers_18(self) -> None:
        avail = ["SQL Server", "ODBC Driver 17 for SQL Server", "ODBC Driver 18 for SQL Server"]
        self.assertEqual(drivers.pick_driver(avail), "ODBC Driver 18 for SQL Server")

    def test_prefers_17_when_18_absent(self) -> None:
        avail = ["SQL Server", "ODBC Driver 11 for SQL Server", "ODBC Driver 17 for SQL Server"]
        self.assertEqual(drivers.pick_driver(avail), "ODBC Driver 17 for SQL Server")

    def test_native_client_over_generic(self) -> None:
        avail = ["SQL Server", "SQL Server Native Client 11.0"]
        self.assertEqual(drivers.pick_driver(avail), "SQL Server Native Client 11.0")

    def test_odbc_line_beats_native_client_same_version(self) -> None:
        """档位优先于版本号：ODBC Driver 11 是 Native Client 11.0 的替代品。"""
        avail = ["SQL Server Native Client 11.0", "ODBC Driver 11 for SQL Server"]
        self.assertEqual(drivers.pick_driver(avail), "ODBC Driver 11 for SQL Server")

    def test_generic_last_resort(self) -> None:
        self.assertEqual(drivers.pick_driver(["SQL Server"]), "SQL Server")

    def test_unknown_versioned_driver_picked(self) -> None:
        avail = ["ODBC Driver 15 for SQL Server", "SQL Server"]
        self.assertEqual(drivers.pick_driver(avail), "ODBC Driver 15 for SQL Server")

    def test_highest_unknown_version_wins(self) -> None:
        avail = ["ODBC Driver 15 for SQL Server", "ODBC Driver 19 for SQL Server"]
        self.assertEqual(drivers.pick_driver(avail), "ODBC Driver 19 for SQL Server")

    def test_whitespace_tolerated(self) -> None:
        avail = ["  ODBC Driver 17 for SQL Server  "]
        self.assertEqual(drivers.pick_driver(avail), "ODBC Driver 17 for SQL Server")

    def test_empty_raises(self) -> None:
        with self.assertRaises(drivers.DriverNotFound):
            drivers.pick_driver([])

    def test_no_sql_server_driver_raises(self) -> None:
        with self.assertRaises(drivers.DriverNotFound):
            drivers.pick_driver(["Microsoft Access Driver (*.mdb, *.accdb)"])

    def test_local_machine_has_a_driver(self) -> None:
        """本机应至少能选出一个 SQL Server 驱动（探测 pyodbc 实际列表）。"""
        available = drivers.list_available()
        if not available:
            self.skipTest("本机无 pyodbc 或未安装 ODBC 驱动")
        picked = drivers.pick_driver(available)
        self.assertTrue(picked)


class TestBuildConnectionString(unittest.TestCase):
    def _sql_profile(self, **kw) -> ConnectionProfile:
        base = dict(
            server="10.0.0.8",
            database="CoolBuddy",
            auth_mode=AuthMode.SQL,
            username="sa",
            password="P@ss;word",
            driver="ODBC Driver 17 for SQL Server",
        )
        base.update(kw)
        return ConnectionProfile(**base)  # type: ignore[arg-type]

    def test_contains_core_keys(self) -> None:
        cs = connstr.build(self._sql_profile())
        self.assertIn("DRIVER={ODBC Driver 17 for SQL Server}", cs)
        self.assertIn("SERVER={10.0.0.8}", cs)
        self.assertIn("DATABASE={CoolBuddy}", cs)

    def test_sql_auth_keys(self) -> None:
        cs = connstr.build(self._sql_profile())
        self.assertIn("UID={sa}", cs)
        self.assertIn("PWD={P@ss;word}", cs)
        self.assertNotIn("Trusted_Connection", cs)

    def test_semicolon_in_password_is_braced(self) -> None:
        cs = connstr.build(self._sql_profile(password="a;b"))
        self.assertIn("PWD={a;b}", cs)

    def test_closing_brace_is_doubled(self) -> None:
        cs = connstr.build(self._sql_profile(password="a}b"))
        self.assertIn("PWD={a}}b}", cs)

    def test_windows_auth_excludes_credentials(self) -> None:
        cs = connstr.build(
            self._sql_profile(auth_mode=AuthMode.WINDOWS, username="ignored", password="x")
        )
        self.assertIn("Trusted_Connection=yes", cs)
        self.assertNotIn("PWD=", cs)
        self.assertNotIn("UID=", cs)

    def test_port_appended(self) -> None:
        cs = connstr.build(self._sql_profile(port=1433))
        self.assertIn("SERVER={10.0.0.8,1433}", cs)

    def test_no_port_keeps_bare_server(self) -> None:
        cs = connstr.build(self._sql_profile(port=None))
        self.assertIn("SERVER={10.0.0.8}", cs)
        self.assertNotIn(",", cs.split("SERVER=")[1].split(";")[0])

    def test_named_instance_server(self) -> None:
        cs = connstr.build(self._sql_profile(server=r"HOST\SQLEXPRESS"))
        self.assertIn(r"SERVER={HOST\SQLEXPRESS}", cs)

    def test_encrypt_disabled_by_default(self) -> None:
        cs = connstr.build(self._sql_profile())
        self.assertIn("Encrypt=no", cs)
        self.assertNotIn("TrustServerCertificate", cs)

    def test_encrypt_enabled_with_trust(self) -> None:
        cs = connstr.build(self._sql_profile(encrypt=True, trust_server_certificate=True))
        self.assertIn("Encrypt=yes", cs)
        self.assertIn("TrustServerCertificate=yes", cs)

    def test_encrypt_enabled_without_trust(self) -> None:
        cs = connstr.build(self._sql_profile(encrypt=True, trust_server_certificate=False))
        self.assertIn("TrustServerCertificate=no", cs)

    def test_ends_with_separator(self) -> None:
        self.assertTrue(connstr.build(self._sql_profile()).endswith(";"))

    def test_missing_driver_raises(self) -> None:
        with self.assertRaises(connstr.ProfileError):
            connstr.build(self._sql_profile(driver=""))


class TestValidate(unittest.TestCase):
    def _ok(self) -> ConnectionProfile:
        return ConnectionProfile(
            server="h", database="d", username="u", password="p", driver="SQL Server"
        )

    def test_ok_profile_has_no_errors(self) -> None:
        self.assertEqual(connstr.validate(self._ok()), [])

    def test_missing_server(self) -> None:
        p = self._ok()
        p.server = "  "
        self.assertTrue(any("服务器" in e for e in connstr.validate(p)))

    def test_missing_database(self) -> None:
        p = self._ok()
        p.database = ""
        self.assertTrue(any("数据库" in e for e in connstr.validate(p)))

    def test_sql_auth_requires_username(self) -> None:
        p = self._ok()
        p.username = ""
        self.assertTrue(any("用户名" in e for e in connstr.validate(p)))

    def test_sql_auth_requires_password(self) -> None:
        p = self._ok()
        p.password = ""
        self.assertTrue(any("密码" in e for e in connstr.validate(p)))

    def test_windows_auth_needs_no_credentials(self) -> None:
        p = self._ok()
        p.auth_mode = AuthMode.WINDOWS
        p.username = p.password = ""
        self.assertEqual(connstr.validate(p), [])

    def test_bad_port(self) -> None:
        p = self._ok()
        p.port = 0
        self.assertTrue(any("端口" in e for e in connstr.validate(p)))
        p.port = 70000
        self.assertTrue(any("端口" in e for e in connstr.validate(p)))

    def test_bad_timeouts(self) -> None:
        p = self._ok()
        p.connect_timeout = 0
        p.query_timeout = 0
        errors = connstr.validate(p)
        self.assertTrue(any("连接超时" in e for e in errors))
        self.assertTrue(any("查询超时" in e for e in errors))


if __name__ == "__main__":
    unittest.main(verbosity=2)
