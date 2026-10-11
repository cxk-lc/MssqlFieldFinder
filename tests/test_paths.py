"""paths 单元测试：项目路径解析、环境变量覆盖、旧路径只读回退。"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src import paths


class TestProjectRoot(unittest.TestCase):
    def test_defaults_to_repo_root(self) -> None:
        root = paths.project_root()
        self.assertTrue((root / "main.py").exists())
        self.assertTrue((root / "src").is_dir())

    def test_env_override_wins(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {paths.ENV_HOME: tmp}):
                self.assertEqual(paths.project_root(), Path(tmp))

    def test_blank_env_is_ignored(self) -> None:
        with mock.patch.dict(os.environ, {paths.ENV_HOME: "   "}):
            self.assertTrue((paths.project_root() / "main.py").exists())

    def test_env_override_supports_user_prefix(self) -> None:
        with mock.patch.dict(os.environ, {paths.ENV_HOME: "~"}):
            self.assertEqual(paths.project_root(), Path.home())


class TestDataFilesLiveInProject(unittest.TestCase):
    """需求：配置文件改存项目路径下。"""

    def test_config_path_in_project_root(self) -> None:
        self.assertEqual(paths.config_path(), paths.project_root() / "config.json")

    def test_connections_path_in_project_root(self) -> None:
        self.assertEqual(
            paths.connections_path(), paths.project_root() / "connections.json"
        )

    def test_not_under_appdata(self) -> None:
        appdata = os.environ.get("APPDATA")
        if not appdata:
            self.skipTest("无 APPDATA 环境变量")
        self.assertNotIn(Path(appdata), paths.config_path().parents)

    def test_override_follows_env(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {paths.ENV_HOME: tmp}):
                self.assertEqual(paths.config_path(), Path(tmp) / "config.json")
                self.assertEqual(
                    paths.connections_path(), Path(tmp) / "connections.json"
                )


class TestLegacyPaths(unittest.TestCase):
    def test_legacy_paths_point_at_appdata(self) -> None:
        self.assertTrue(
            str(paths.legacy_config_path()).endswith("MssqlFieldFinder\\config.json")
            or str(paths.legacy_config_path()).endswith("MssqlFieldFinder/config.json")
        )
        self.assertTrue(
            str(paths.legacy_connections_path()).endswith("connections.json")
        )

    def test_legacy_dir_differs_from_project(self) -> None:
        self.assertNotEqual(
            paths.legacy_config_path().parent, paths.config_path().parent
        )

    def test_resolve_prefers_primary_when_present(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            primary = base / "new.json"
            legacy = base / "old.json"
            primary.write_text("{}", encoding="utf-8")
            legacy.write_text("{}", encoding="utf-8")
            self.assertEqual(paths.resolve_with_legacy(primary, legacy), primary)

    def test_resolve_falls_back_to_legacy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            primary = base / "new.json"
            legacy = base / "old.json"
            legacy.write_text("{}", encoding="utf-8")
            self.assertEqual(paths.resolve_with_legacy(primary, legacy), legacy)

    def test_resolve_returns_primary_when_neither_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            primary = base / "new.json"
            legacy = base / "old.json"
            self.assertEqual(paths.resolve_with_legacy(primary, legacy), primary)


if __name__ == "__main__":
    unittest.main(verbosity=2)
