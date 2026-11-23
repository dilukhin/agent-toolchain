from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from setup_inventory import ExecutableInstance, ExternalCliSpec, duplicate_recommendation, external_cli_inventory, infer_manager, render_instances  # noqa: E402


class WindowsNpmLayeredInventoryTests(unittest.TestCase):
    def test_node_installer_npm_is_identified_only_for_npm_command(self) -> None:
        path = Path("C:/Program Files/nodejs/npm.cmd")
        self.assertEqual(infer_manager(path, command="npm"), "node-bundled")
        self.assertEqual(infer_manager(path, command="node"), "unknown")

    def test_user_global_plus_node_bundled_npm_can_be_left_intentionally(self) -> None:
        items = [
            ExecutableInstance(
                Path("C:/Users/Dima/AppData/Roaming/npm/npm.cmd"),
                "11.12.1",
                "npm",
                True,
            ),
            ExecutableInstance(
                Path("C:/Program Files/nodejs/npm.cmd"),
                "11.12.1",
                "node-bundled",
                False,
            ),
        ]
        rendered = render_instances(items)
        recommendation = duplicate_recommendation("npm", items)

        self.assertIn("в составе Node.js", rendered)
        self.assertIn("layered-схема Windows", recommendation)
        self.assertIn("обе копии можно оставить", recommendation)
        self.assertIn("Не удаляйте npm.cmd из каталога Node.js вручную", recommendation)
        self.assertNotIn("остальные глобальные экземпляры удалить", recommendation)

    def test_layered_npm_version_divergence_is_reported_without_delete_advice(self) -> None:
        items = [
            ExecutableInstance(Path("C:/Users/Dima/AppData/Roaming/npm/npm.cmd"), "11.12.1", "npm", True),
            ExecutableInstance(Path("C:/Program Files/nodejs/npm.cmd"), "10.9.2", "node-bundled", False),
        ]
        recommendation = duplicate_recommendation("npm", items)
        self.assertIn("Версии копий различаются", recommendation)
        self.assertNotIn("остальные глобальные экземпляры удалить", recommendation)

    def test_generic_duplicate_policy_is_unchanged(self) -> None:
        items = [
            ExecutableInstance(Path("C:/Python314/python.exe"), "3.14.2", "unknown", True),
            ExecutableInstance(Path("C:/Python313/python.exe"), "3.13.7", "unknown", False),
        ]
        recommendation = duplicate_recommendation("Python", items)
        self.assertIn("остальные глобальные экземпляры удалить", recommendation)

    def test_opencode_install_script_path_is_classified_as_curl(self) -> None:
        path = Path("/home/test/.opencode/bin/opencode")
        self.assertEqual(infer_manager(path, command="opencode"), "curl")
        self.assertEqual(infer_manager(path, command="other"), "unknown")

    def test_local_bin_opencode_is_not_assumed_to_be_curl(self) -> None:
        path = Path("/home/test/.local/bin/opencode")
        self.assertEqual(infer_manager(path, command="opencode"), "local")

    def test_v2_npm_update_advisory_uses_v2_package(self) -> None:
        from unittest import mock
        for path in (
            Path("C:/Users/Dima/AppData/Roaming/npm/opencode.cmd"),
            Path("/home/dima/.local/lib/node_modules/@opencode/cli/bin/opencode"),
        ):
            with self.subTest(path=path), mock.patch("setup_inventory.executable_inventory", return_value=[
                ExecutableInstance(path, "opencode v2.3.2", "npm", True),
            ]):
                result = external_cli_inventory(ExternalCliSpec("opencode", "OpenCode"))
            self.assertEqual(result.active.package, "@opencode/cli")
            self.assertEqual(result.update_advice, "npm install -g @opencode/cli@latest")

    def test_unknown_npm_opencode_version_has_no_update_advice(self) -> None:
        from unittest import mock
        with mock.patch("setup_inventory.executable_inventory", return_value=[
            ExecutableInstance(Path("C:/Users/Dima/AppData/Roaming/npm/opencode.cmd"), None, "npm", True),
        ]):
            result = external_cli_inventory(ExternalCliSpec("opencode", "OpenCode"))
        self.assertIsNone(result.active.package)
        self.assertIsNone(result.update_advice)


if __name__ == "__main__":
    unittest.main()
