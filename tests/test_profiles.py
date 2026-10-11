"""profiles 单元测试：多方案存取、密码策略、损坏容错、与 config 分组的一致性守卫。"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src import config, profiles


class TestExtractConnectionValues(unittest.TestCase):
    def test_only_connection_keys_kept(self) -> None:
        values = {
            "server": "h",
            "password": "p",
            "field_keyword": "Order",
            "max_workers": 8,
            "with_nolock": True,
        }
        got = profiles.extract_connection_values(values)
        self.assertEqual(set(got), {"server", "password"})

    def test_missing_keys_are_skipped(self) -> None:
        self.assertEqual(profiles.extract_connection_values({}), {})

    def test_connection_keys_match_config_group(self) -> None:
        """守卫用例：CONNECTION_KEYS 必须与 config 中「连接设置」分组逐项一致。"""
        declared = {f.key for f in config.CONNECTION_FIELDS}
        self.assertEqual(set(profiles.CONNECTION_KEYS), declared)


class TestProfileStore(unittest.TestCase):
    def setUp(self) -> None:
        self.store = profiles.ProfileStore()

    def test_upsert_and_get(self) -> None:
        self.store.upsert("生产库", {"server": "10.0.0.8", "database": "ERP"})
        self.assertEqual(self.store.get("生产库")["server"], "10.0.0.8")
        self.assertTrue(self.store.has("生产库"))

    def test_get_returns_copy(self) -> None:
        self.store.upsert("A", {"server": "h"})
        got = self.store.get("A")
        got["server"] = "mutated"
        self.assertEqual(self.store.get("A")["server"], "h")

    def test_get_missing_is_empty(self) -> None:
        self.assertEqual(self.store.get("不存在"), {})

    def test_blank_name_raises(self) -> None:
        with self.assertRaises(ValueError):
            self.store.upsert("   ", {"server": "h"})

    def test_name_is_trimmed(self) -> None:
        self.store.upsert("  测试库  ", {"server": "h"})
        self.assertTrue(self.store.has("测试库"))

    def test_upsert_sets_active(self) -> None:
        self.store.upsert("A", {"server": "h"})
        self.assertEqual(self.store.active, "A")

    def test_names_sorted(self) -> None:
        for name in ("丙", "甲", "乙"):
            self.store.upsert(name, {"server": "h"})
        self.assertEqual(self.store.names(), sorted(["丙", "甲", "乙"]))

    def test_remove_clears_active(self) -> None:
        self.store.upsert("A", {"server": "h"})
        self.assertTrue(self.store.remove("A"))
        self.assertEqual(self.store.active, "")
        self.assertFalse(self.store.has("A"))

    def test_remove_missing_returns_false(self) -> None:
        self.assertFalse(self.store.remove("不存在"))

    def test_pick_active_prefers_active(self) -> None:
        for name in ("A", "B"):
            self.store.upsert(name, {"server": "h"})
        self.store.active = "B"
        self.assertEqual(self.store.pick_active(), "B")

    def test_pick_active_falls_back_to_first(self) -> None:
        self.store.upsert("B", {"server": "h"})
        self.store.upsert("A", {"server": "h"})
        self.store.active = "已删除的方案"
        self.assertEqual(self.store.pick_active(), "A")

    def test_pick_active_on_empty_store(self) -> None:
        self.assertEqual(self.store.pick_active(), "")

    def test_rename(self) -> None:
        self.store.upsert("旧名", {"server": "h"})
        self.store.rename("旧名", "新名")
        self.assertTrue(self.store.has("新名"))
        self.assertFalse(self.store.has("旧名"))
        self.assertEqual(self.store.active, "新名")

    def test_rename_missing_is_noop(self) -> None:
        self.store.upsert("A", {"server": "h"})
        self.store.rename("不存在", "B")
        self.assertEqual(self.store.names(), ["A"])

    def test_rename_blank_raises(self) -> None:
        self.store.upsert("A", {"server": "h"})
        with self.assertRaises(ValueError):
            self.store.rename("A", "  ")

    def test_rename_to_same_name_is_noop(self) -> None:
        self.store.upsert("A", {"server": "h"})
        self.store.rename("A", "A")
        self.assertEqual(self.store.names(), ["A"])

    def test_upsert_strips_non_connection_keys(self) -> None:
        self.store.upsert("A", {"server": "h", "field_keyword": "x"})
        self.assertNotIn("field_keyword", self.store.get("A"))


class TestPersistence(unittest.TestCase):
    def _path(self, tmp: str) -> Path:
        return Path(tmp) / "connections.json"

    def test_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = self._path(tmp)
            store = profiles.ProfileStore()
            store.upsert("生产库", {"server": "10.0.0.8", "database": "ERP", "port": "1433"})
            store.upsert("测试库", {"server": "10.0.0.9", "database": "ERP_TEST"})
            store.active = "测试库"
            profiles.save(store, path)

            loaded = profiles.load(path)
            self.assertEqual(loaded.names(), sorted(["生产库", "测试库"]))
            self.assertEqual(loaded.active, "测试库")
            self.assertEqual(loaded.get("生产库")["database"], "ERP")
            self.assertEqual(loaded.get("生产库")["port"], "1433")

    def test_password_kept_when_remembered(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = self._path(tmp)
            store = profiles.ProfileStore()
            store.upsert("A", {"server": "h", "password": "secret"}, remember=True)
            profiles.save(store, path)
            self.assertEqual(profiles.load(path).get("A")["password"], "secret")

    def test_password_stripped_when_not_remembered(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = self._path(tmp)
            store = profiles.ProfileStore()
            store.upsert("A", {"server": "h", "password": "secret"}, remember=False)
            profiles.save(store, path)
            self.assertEqual(profiles.load(path).get("A")["password"], "")

    def test_remember_flag_roundtrips(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = self._path(tmp)
            store = profiles.ProfileStore()
            store.upsert("A", {"server": "h"}, remember=False)
            profiles.save(store, path)
            self.assertFalse(profiles.load(path).remember_for("A"))

    def test_save_does_not_mutate_in_memory_store(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = profiles.ProfileStore()
            store.upsert("A", {"server": "h", "password": "secret"}, remember=False)
            profiles.save(store, self._path(tmp))
            self.assertEqual(store.get("A")["password"], "secret")

    def test_missing_file_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            loaded = profiles.load(Path(tmp) / "none.json")
            self.assertEqual(loaded.names(), [])
            self.assertEqual(loaded.active, "")

    def test_corrupt_file_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = self._path(tmp)
            path.write_text("{ not json", encoding="utf-8")
            self.assertEqual(profiles.load(path).names(), [])

    def test_non_dict_root_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = self._path(tmp)
            path.write_text("[1, 2, 3]", encoding="utf-8")
            self.assertEqual(profiles.load(path).names(), [])

    def test_profiles_not_dict_is_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = self._path(tmp)
            path.write_text('{"profiles": "oops"}', encoding="utf-8")
            self.assertEqual(profiles.load(path).names(), [])

    def test_non_dict_profile_body_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = self._path(tmp)
            path.write_text(
                '{"profiles": {"A": {"server": "h"}, "B": 123}}', encoding="utf-8"
            )
            self.assertEqual(profiles.load(path).names(), ["A"])

    def test_unknown_keys_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = self._path(tmp)
            path.write_text(
                '{"profiles": {"A": {"server": "h", "bogus": 1}}}', encoding="utf-8"
            )
            self.assertEqual(set(profiles.load(path).get("A")), {"server"})

    def test_save_leaves_no_temp_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = profiles.ProfileStore()
            store.upsert("A", {"server": "h"})
            profiles.save(store, self._path(tmp))
            leftovers = [p.name for p in Path(tmp).iterdir() if p.suffix == ".tmp"]
            self.assertEqual(leftovers, [])

    def test_save_is_valid_json_with_version(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = self._path(tmp)
            store = profiles.ProfileStore()
            store.upsert("A", {"server": "h"})
            profiles.save(store, path)
            raw = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(raw["version"], profiles.SCHEMA_VERSION)

    def test_default_store_path_in_project(self) -> None:
        path = profiles.store_path()
        self.assertTrue(str(path).endswith("connections.json"))
        self.assertTrue((path.parent / "main.py").exists())

    def test_empty_store_writes_no_file(self) -> None:
        """需求：清除文件中的默认配置内容——空方案集合不落盘。"""
        with tempfile.TemporaryDirectory() as tmp:
            target = self._path(tmp)
            self.assertIsNone(profiles.save(profiles.ProfileStore(), target))
            self.assertFalse(target.exists())

    def test_saved_payload_omits_empty_sections(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = self._path(tmp)
            store = profiles.ProfileStore()
            store.upsert("A", {"server": "h"})
            profiles.save(store, path)
            raw = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(set(raw), {"version", "active", "profiles"})

    def test_legacy_fallback_when_project_file_absent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            legacy = base / "legacy.json"
            store = profiles.ProfileStore()
            store.upsert("老方案", {"server": "10.0.0.8"})
            profiles.save(store, legacy)

            with mock.patch(
                "src.profiles.store_path", lambda: base / "connections.json"
            ), mock.patch(
                "src.paths.legacy_connections_path", lambda: legacy
            ):
                loaded = profiles.load()
            self.assertEqual(loaded.names(), ["老方案"])

    def test_legacy_is_not_used_when_project_file_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            legacy = base / "legacy.json"
            primary = base / "connections.json"
            old = profiles.ProfileStore()
            old.upsert("老方案", {"server": "1.1.1.1"})
            profiles.save(old, legacy)
            new = profiles.ProfileStore()
            new.upsert("新方案", {"server": "2.2.2.2"})
            profiles.save(new, primary)

            with mock.patch(
                "src.profiles.store_path", lambda: primary
            ), mock.patch(
                "src.paths.legacy_connections_path", lambda: legacy
            ):
                loaded = profiles.load()
            self.assertEqual(loaded.names(), ["新方案"])

    def test_legacy_files_are_never_modified(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            legacy = base / "legacy.json"
            store = profiles.ProfileStore()
            store.upsert("老方案", {"server": "h"})
            profiles.save(store, legacy)
            before = legacy.read_bytes()

            with mock.patch(
                "src.profiles.store_path", lambda: base / "connections.json"
            ), mock.patch(
                "src.paths.legacy_connections_path", lambda: legacy
            ):
                profiles.load()
            self.assertEqual(legacy.read_bytes(), before)
            self.assertFalse((base / "connections.json").exists())


class TestSeed(unittest.TestCase):
    def test_seeds_from_legacy_config_values(self) -> None:
        store = profiles.ProfileStore()
        name = profiles.ensure_seed(store, {"server": "10.0.0.8", "database": "ERP"})
        self.assertEqual(name, profiles.DEFAULT_PROFILE_NAME)
        self.assertEqual(store.get(name)["server"], "10.0.0.8")

    def test_no_seed_when_store_has_profiles(self) -> None:
        store = profiles.ProfileStore()
        store.upsert("已有", {"server": "h"})
        self.assertEqual(profiles.ensure_seed(store, {"server": "10.0.0.8"}), "")
        self.assertEqual(store.names(), ["已有"])

    def test_no_seed_without_server(self) -> None:
        store = profiles.ProfileStore()
        self.assertEqual(profiles.ensure_seed(store, {"server": "   "}), "")
        self.assertEqual(store.names(), [])

    def test_seed_carries_remember_flag(self) -> None:
        store = profiles.ProfileStore()
        profiles.ensure_seed(store, {"server": "h", "remember_password": False})
        self.assertFalse(store.remember_for(profiles.DEFAULT_PROFILE_NAME))

    def test_seed_from_legacy_config_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            legacy = Path(tmp) / "config.json"
            legacy.write_text(
                '{"server": "10.0.0.8", "database": "ERP"}', encoding="utf-8"
            )
            store = profiles.ProfileStore()
            name = profiles.seed_from_legacy_config(store, legacy)
            self.assertEqual(name, profiles.DEFAULT_PROFILE_NAME)
            self.assertEqual(store.get(name)["database"], "ERP")

    def test_seed_from_legacy_skipped_when_store_has_profiles(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            legacy = Path(tmp) / "config.json"
            legacy.write_text('{"server": "10.0.0.8"}', encoding="utf-8")
            store = profiles.ProfileStore()
            store.upsert("已有", {"server": "h"})
            self.assertEqual(profiles.seed_from_legacy_config(store, legacy), "")
            self.assertEqual(store.names(), ["已有"])

    def test_seed_from_legacy_missing_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = profiles.ProfileStore()
            self.assertEqual(
                profiles.seed_from_legacy_config(store, Path(tmp) / "none.json"), ""
            )

    def test_seed_from_legacy_corrupt_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            legacy = Path(tmp) / "config.json"
            legacy.write_text("{ broken", encoding="utf-8")
            store = profiles.ProfileStore()
            self.assertEqual(profiles.seed_from_legacy_config(store, legacy), "")


class TestPerProfileRemember(unittest.TestCase):
    """需求：记住密码只影响当前方案，各方案之间互不干扰。"""

    def test_default_is_remember(self) -> None:
        store = profiles.ProfileStore()
        store.upsert("A", {"server": "h"})
        self.assertTrue(store.remember_for("A"))
        self.assertTrue(store.remember_for("未定义"))

    def test_flags_are_independent(self) -> None:
        store = profiles.ProfileStore()
        store.upsert("A", {"server": "h", "password": "p1"}, remember=True)
        store.upsert("B", {"server": "h", "password": "p2"}, remember=False)
        self.assertTrue(store.remember_for("A"))
        self.assertFalse(store.remember_for("B"))

    def test_blank_passwords_only_affects_disabled_profile(self) -> None:
        store = profiles.ProfileStore()
        store.upsert("A", {"server": "h", "password": "p1"}, remember=True)
        store.upsert("B", {"server": "h", "password": "p2"}, remember=False)
        profiles.blank_passwords(store)
        self.assertEqual(store.get("A")["password"], "p1")
        self.assertEqual(store.get("B")["password"], "")

    def test_set_remember_with_blank_name_is_ignored(self) -> None:
        store = profiles.ProfileStore()
        store.set_remember("   ", False)
        self.assertEqual(store.remember, {})

    def test_remove_drops_flag(self) -> None:
        store = profiles.ProfileStore()
        store.upsert("A", {"server": "h"}, remember=False)
        store.remove("A")
        self.assertNotIn("A", store.remember)

    def test_rename_moves_flag(self) -> None:
        store = profiles.ProfileStore()
        store.upsert("旧", {"server": "h"}, remember=False)
        store.rename("旧", "新")
        self.assertNotIn("旧", store.remember)
        self.assertFalse(store.remember_for("新"))

    def test_upsert_without_remember_keeps_existing_flag(self) -> None:
        store = profiles.ProfileStore()
        store.upsert("A", {"server": "h"}, remember=False)
        store.upsert("A", {"server": "h2"})
        self.assertFalse(store.remember_for("A"))

    def test_save_omits_default_true_flags(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "connections.json"
            store = profiles.ProfileStore()
            store.upsert("A", {"server": "h"}, remember=True)
            profiles.save(store, path)
            raw = json.loads(path.read_text(encoding="utf-8"))
            self.assertNotIn("remember", raw)

    def test_save_writes_only_disabled_flags(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "connections.json"
            store = profiles.ProfileStore()
            store.upsert("A", {"server": "h"}, remember=True)
            store.upsert("B", {"server": "h"}, remember=False)
            profiles.save(store, path)
            raw = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(raw["remember"], {"B": False})

    def test_independent_flags_survive_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "connections.json"
            store = profiles.ProfileStore()
            store.upsert("A", {"server": "h", "password": "p1"}, remember=True)
            store.upsert("B", {"server": "h", "password": "p2"}, remember=False)
            profiles.save(store, path)

            loaded = profiles.load(path)
            self.assertTrue(loaded.remember_for("A"))
            self.assertFalse(loaded.remember_for("B"))
            self.assertEqual(loaded.get("A")["password"], "p1")
            self.assertEqual(loaded.get("B")["password"], "")

    def test_legacy_global_flag_migrates_to_all_profiles(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "connections.json"
            path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "active": "A",
                        "remember_password": False,
                        "profiles": {"A": {"server": "h"}, "B": {"server": "h"}},
                    }
                ),
                encoding="utf-8",
            )
            loaded = profiles.load(path)
            self.assertFalse(loaded.remember_for("A"))
            self.assertFalse(loaded.remember_for("B"))

    def test_legacy_true_flag_keeps_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "connections.json"
            path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "remember_password": True,
                        "profiles": {"A": {"server": "h"}},
                    }
                ),
                encoding="utf-8",
            )
            self.assertTrue(profiles.load(path).remember_for("A"))


class TestBlankPasswords(unittest.TestCase):
    def test_blanks_profiles_with_flag_off(self) -> None:
        store = profiles.ProfileStore()
        store.upsert("A", {"server": "h", "password": "p1"}, remember=True)
        store.upsert("B", {"server": "h", "password": "p2"}, remember=False)
        profiles.blank_passwords(store)
        self.assertEqual(store.get("A")["password"], "p1")
        self.assertEqual(store.get("B")["password"], "")

    def test_leaves_profiles_without_password_alone(self) -> None:
        store = profiles.ProfileStore()
        store.upsert("A", {"server": "h"}, remember=False)
        profiles.blank_passwords(store)
        self.assertEqual(store.get("A"), {"server": "h"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
