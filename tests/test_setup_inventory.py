from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from setup_inventory import ExecutableInstance, duplicate_recommendation, infer_manager, render_instances  # noqa: E402


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


if __name__ == "__main__":
    unittest.main()
