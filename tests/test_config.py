"""config 单元测试：默认值裁剪、落盘策略、数值校验、分组不变式。"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from src import config, profiles


class TestGroupInvariants(unittest.TestCase):
    def test_connection_group_not_rendered_on_main_window(self) -> None:
        """需求：连接设置不直接显示在主窗口。"""
        self.assertNotIn(config.GROUP_CONNECTION, config.GROUPS)

    def test_scheme_group_not_rendered_on_main_window(self) -> None:
        """需求：记住密码随测试连接一起进弹窗，因此主窗口不再渲染该分组。"""
        self.assertNotIn(config.GROUP_SCHEME, config.GROUPS)

    def test_main_window_groups_are_search_and_advanced(self) -> None:
        self.assertEqual(
            config.GROUPS, (config.GROUP_SEARCH, config.GROUP_ADVANCED)
        )

    def test_remember_password_moved_to_scheme_group(self) -> None:
        spec = config.FIELDS_BY_KEY["remember_password"]
        self.assertEqual(spec.group, config.GROUP_SCHEME)
        self.assertNotIn("remember_password", profiles.CONNECTION_KEYS)

    def test_remember_password_defaults_to_true(self) -> None:
        """需求：默认勾选记住密码。"""
        self.assertIs(config.FIELDS_BY_KEY["remember_password"].default, True)
        self.assertIs(config.defaults()["remember_password"], True)

    def test_connection_fields_are_dialog_only(self) -> None:
        for spec in config.CONNECTION_FIELDS:
            self.assertNotIn(spec.group, config.GROUPS)

    def test_every_field_belongs_to_a_known_group(self) -> None:
        known = {
            config.GROUP_SCHEME,
            config.GROUP_CONNECTION,
            config.GROUP_SEARCH,
            config.GROUP_ADVANCED,
        }
        for spec in config.FIELDS:
            self.assertIn(spec.group, known, spec.key)

    def test_no_duplicate_keys(self) -> None:
        keys = [f.key for f in config.FIELDS]
        self.assertEqual(len(keys), len(set(keys)))


class TestKeywordFields(unittest.TestCase):
    """需求：字段名关键词与内容关键词「默认为空」，且每次检索都会写入设置文件。

    因此这两个字段是「写入但不读回」——用户能在设置文件里看到搜过什么，
    而界面每次启动仍是空的。
    """

    def test_keyword_fields_default_to_empty(self) -> None:
        defaults = config.defaults()
        self.assertEqual(defaults["field_keyword"], "")
        self.assertEqual(defaults["content_keyword"], "")

    def test_keyword_fields_are_written(self) -> None:
        self.assertTrue(config.FIELDS_BY_KEY["field_keyword"].persist)
        self.assertTrue(config.FIELDS_BY_KEY["content_keyword"].persist)

    def test_keyword_fields_are_not_restored(self) -> None:
        self.assertFalse(config.FIELDS_BY_KEY["field_keyword"].restore)
        self.assertFalse(config.FIELDS_BY_KEY["content_keyword"].restore)

    def test_keywords_included_in_payload(self) -> None:
        values = config.defaults()
        values["field_keyword"] = "OrderID"
        values["content_keyword"] = "zhang"
        payload = config.build_payload(values, True)
        self.assertEqual(
            payload, {"field_keyword": "OrderID", "content_keyword": "zhang"}
        )

    def test_keywords_ignored_on_load(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cfg.json"
            path.write_text(
                '{"field_keyword": "OrderID", "content_keyword": "zhang"}',
                encoding="utf-8",
            )
            loaded = config.load(path)
            self.assertEqual(loaded["field_keyword"], "")
            self.assertEqual(loaded["content_keyword"], "")

    def test_other_search_fields_still_persist_and_restore(self) -> None:
        values = config.defaults()
        values["ignore_case"] = False
        values["field_mode"] = "精准"
        payload = config.build_payload(values, True)
        self.assertEqual(set(payload), {"ignore_case", "field_mode"})

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cfg.json"
            path.write_text(
                '{"ignore_case": false, "field_mode": "精准"}', encoding="utf-8"
            )
            loaded = config.load(path)
            self.assertFalse(loaded["ignore_case"])
            self.assertEqual(loaded["field_mode"], "精准")


class TestWorkerDefault(unittest.TestCase):
    """需求：并发线程数默认值按本机 CPU 核数给出一个适中的值。"""

    def test_scales_with_core_count(self) -> None:
        self.assertEqual(config.default_workers(2), config.MIN_WORKERS)
        self.assertEqual(config.default_workers(4), 4)
        self.assertEqual(config.default_workers(6), 6)
        self.assertEqual(config.default_workers(8), 8)

    def test_capped_for_busy_servers(self) -> None:
        self.assertEqual(config.default_workers(64), config.MAX_WORKERS_CAP)

    def test_has_lower_bound(self) -> None:
        self.assertEqual(config.default_workers(1), config.MIN_WORKERS)

    def test_uses_host_cpu_count_by_default(self) -> None:
        import os

        cores = os.cpu_count() or config.MIN_WORKERS
        self.assertEqual(
            config.default_workers(), max(config.MIN_WORKERS, min(cores, config.MAX_WORKERS_CAP))
        )

    def test_defaults_reflect_worker_default(self) -> None:
        self.assertEqual(config.defaults()["max_workers"], config.default_workers())

    def test_effective_default_matches(self) -> None:
        self.assertEqual(
            config.effective_default("max_workers"), config.default_workers()
        )

    def test_effective_default_for_plain_field(self) -> None:
        self.assertEqual(config.effective_default("field_mode"), config.DEFAULT_MODE_LABEL)

    def test_hint_mentions_cpu_cores(self) -> None:
        self.assertIn(str(config._CPU_CORES), config.FIELDS_BY_KEY["max_workers"].hint)


class TestBuildPayload(unittest.TestCase):
    def test_all_defaults_yield_empty_payload(self) -> None:
        self.assertEqual(config.build_payload(config.defaults(), True), {})

    def test_keeps_non_default_values(self) -> None:
        values = config.defaults()
        values["collation_base"] = "Chinese_PRC"
        values["max_targets"] = 9999
        payload = config.build_payload(values, True)
        self.assertEqual(payload, {"collation_base": "Chinese_PRC", "max_targets": 9999})

    def test_ignores_unknown_keys(self) -> None:
        payload = config.build_payload({"bogus": 1}, True)
        self.assertEqual(payload, {})

    def test_boolean_default_is_compared_by_value(self) -> None:
        values = config.defaults()
        values["with_nolock"] = False  # 默认 True
        self.assertEqual(config.build_payload(values, True), {"with_nolock": False})

    def test_password_stripped_when_not_remembered(self) -> None:
        values = config.defaults()
        values["password"] = "secret"
        self.assertNotIn("password", config.build_payload(values, False))

    def test_password_kept_when_remembered(self) -> None:
        values = config.defaults()
        values["password"] = "secret"
        self.assertEqual(
            config.build_payload(values, True), {"password": "secret"}
        )

    def test_blank_string_is_default(self) -> None:
        values = config.defaults()
        values["field_keyword"] = "   "
        self.assertEqual(config.build_payload(values, True), {})


class TestSavePolicy(unittest.TestCase):
    """需求：清除文件中的默认配置内容。"""

    def test_no_file_created_when_nothing_changed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "config.json"
            result = config.save(config.defaults(), False, path=target)
            self.assertIsNone(result)
            self.assertFalse(target.exists())

    def test_worker_default_is_treated_as_default(self) -> None:
        """按核数推导出的线程数不应被视为「用户改过」而写入文件。"""
        values = config.defaults()
        values["max_workers"] = config.default_workers()
        self.assertEqual(config.build_payload(values, True), {})

    def test_file_written_when_value_changed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "config.json"
            values = config.defaults()
            values["max_targets"] = 9999
            self.assertEqual(config.save(values, False, path=target), target)
            raw = json.loads(target.read_text(encoding="utf-8"))
            self.assertEqual(raw, {"max_targets": 9999})

    def test_existing_file_is_emptied_when_back_to_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "config.json"
            values = config.defaults()
            values["max_targets"] = 9999
            config.save(values, False, path=target)
            config.save(config.defaults(), False, path=target)
            self.assertEqual(json.loads(target.read_text(encoding="utf-8")), {})

    def test_saved_file_has_no_default_entries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "config.json"
            values = config.defaults()
            values["max_targets"] = 9999
            values["ignore_case"] = False
            config.save(values, False, path=target)
            raw = json.loads(target.read_text(encoding="utf-8"))
            self.assertEqual(set(raw), {"max_targets", "ignore_case"})

    def test_roundtrip_keeps_password_when_remembered(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cfg.json"
            values = config.defaults()
            values["server"] = "10.0.0.8"
            values["password"] = "secret"
            config.save(values, remember_password=True, path=path)
            loaded = config.load(path)
            self.assertEqual(loaded["server"], "10.0.0.8")
            self.assertEqual(loaded["password"], "secret")

    def test_password_dropped_when_not_remembered(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cfg.json"
            values = config.defaults()
            values["password"] = "secret"
            config.save(values, remember_password=False, path=path)
            self.assertEqual(config.load(path)["password"], "")

    def test_missing_file_returns_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(
                config.load(Path(tmp) / "none.json"), config.defaults()
            )

    def test_corrupt_file_returns_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cfg.json"
            path.write_text("{ not json", encoding="utf-8")
            self.assertEqual(config.load(path), config.defaults())

    def test_unknown_keys_ignored_on_load(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cfg.json"
            path.write_text('{"max_workers": 12, "bogus": 1}', encoding="utf-8")
            loaded = config.load(path)
            self.assertEqual(loaded["max_workers"], 12)
            self.assertNotIn("bogus", loaded)

    def test_config_path_is_in_project(self) -> None:
        self.assertTrue(str(config.config_path()).endswith("config.json"))
        self.assertTrue((config.config_path().parent / "main.py").exists())


class TestIntegerValidation(unittest.TestCase):
    def test_garbage_text_is_reported(self) -> None:
        values = {"max_workers": "八个"}
        self.assertTrue(
            any("并发线程数" in p for p in config.integer_problems(values))
        )

    def test_empty_port_allowed(self) -> None:
        self.assertEqual(config.integer_problems({"port": ""}), [])

    def test_empty_required_integer_reported(self) -> None:
        problems = config.integer_problems({"max_workers": ""})
        self.assertTrue(any("不能为空" in p for p in problems))

    def test_valid_integers_pass(self) -> None:
        values = {"port": 1433, "max_workers": 8, "sample_size": 3}
        self.assertEqual(config.integer_problems(values), [])

    def test_absent_keys_are_not_checked(self) -> None:
        """主窗口不再持有连接字段，缺失的整数项不应误报。"""
        self.assertEqual(config.integer_problems({}), [])

    def test_boolean_is_not_accepted_as_integer(self) -> None:
        problems = config.integer_problems({"max_workers": True})
        self.assertTrue(any("必须是整数" in p for p in problems))

    def test_check_integer_ignores_non_int_field(self) -> None:
        spec = config.FIELDS_BY_KEY["server"]
        self.assertEqual(config.check_integer(spec, "任意内容"), "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
