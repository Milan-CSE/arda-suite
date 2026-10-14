"""Safety contract for the Raspberry Pi installer."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import unittest


SERVICE_ROOT = Path(__file__).parent
TOKEN_SENTINEL = "test-only-" + "value-" * 8


class PiInstallerSafetyTests(unittest.TestCase):
    def test_fresh_install_is_loopback_only_and_does_not_print_token(self):
        with tempfile.TemporaryDirectory(prefix="memory-install-test-") as tmp:
            root = Path(tmp)
            service = root / "service"
            shutil.copytree(
                SERVICE_ROOT,
                service,
                ignore=shutil.ignore_patterns(".venv", "__pycache__", ".pytest_cache"),
            )
            home = root / "user-home"
            home.mkdir()
            fake_bin = root / "bin"
            fake_bin.mkdir()
            systemd_dir = root / "systemd"
            systemd_dir.mkdir()
            self._make_fake_commands(fake_bin)
            venv_bin = service / ".venv" / "bin"
            venv_bin.mkdir(parents=True)
            for name in ("pip", "python"):
                path = venv_bin / name
                path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
                path.chmod(0o755)

            env = os.environ.copy()
            env.update({
                "PATH": f"{fake_bin}:{env['PATH']}",
                "HOME": str(home),
                "USER": "testuser",
                "SUDO_USER": "testuser",
                "MEM0_INSTALL_HOME": str(home),
                "MEM0_SYSTEMD_DIR": str(systemd_dir),
                "DOCKER_ARGS_FILE": str(root / "docker-args.txt"),
            })
            result = subprocess.run(
                ["bash", str(service / "scripts" / "install-pi.sh")],
                cwd=service,
                env=env,
                capture_output=True,
                text=True,
                timeout=30,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn(TOKEN_SENTINEL, result.stdout)
            self.assertNotIn(TOKEN_SENTINEL, result.stderr)

            config = (home / ".mem0" / "config.env").read_text(encoding="utf-8")
            self.assertIn("MEM0_MCP_HOST=127.0.0.1", config)
            self.assertIn("MEM0_DASHBOARD_HOST=127.0.0.1", config)
            self.assertNotIn("MEM0_MCP_ALLOW_NONLOCAL=1", config)

            mcp_unit = (systemd_dir / "mem0-mcp.service").read_text(encoding="utf-8")
            dashboard_unit = (systemd_dir / "mem0-dashboard.service").read_text(encoding="utf-8")
            self.assertIn(f"WorkingDirectory={service}", mcp_unit)
            self.assertIn(f"ExecStart={service}/.venv/bin/python", mcp_unit)
            self.assertIn("MEM0_MCP_HOST=127.0.0.1", mcp_unit)
            self.assertNotIn("0.0.0.0", mcp_unit + dashboard_unit)
            self.assertNotIn("/mem0-local", mcp_unit + dashboard_unit)
            self.assertIn("MEM0_DASHBOARD_HOST=127.0.0.1", dashboard_unit)
            docker_args = (root / "docker-args.txt").read_text(encoding="utf-8")
            self.assertIn("127.0.0.1:6333:6333", docker_args)

            token_file = home / ".mem0" / "mcp-token"
            self.assertEqual(token_file.read_text(encoding="utf-8").strip(), TOKEN_SENTINEL)
            self.assertEqual(stat.S_IMODE(token_file.stat().st_mode), 0o600)
            config_file = home / ".mem0" / "config.env"
            self.assertEqual(stat.S_IMODE(config_file.stat().st_mode), 0o600)

            env["DOCKER_EXISTING_UNSAFE"] = "1"
            unsafe = subprocess.run(
                ["bash", str(service / "scripts" / "install-pi.sh")],
                cwd=service,
                env=env,
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertNotEqual(unsafe.returncode, 0)
            self.assertIn("not confirmed loopback-only", unsafe.stderr)

    @staticmethod
    def _make_fake_commands(fake_bin: Path) -> None:
        commands = {
            "sudo": "#!/bin/sh\n# Execute only harmless file capture/copy commands in the temp test environment.\ncase \"$1\" in tee) shift; exec tee \"$@\";; cp) shift; exec cp \"$@\";; docker) shift; exec docker \"$@\";; esac\ncat >/dev/null 2>/dev/null || true\nexit 0\n",
            "docker": "#!/bin/sh\ncase \"$1\" in\n  ps) [ \"${DOCKER_EXISTING_UNSAFE:-}\" = 1 ] && echo mem0-qdrant;;\n  port) echo 0.0.0.0:6333;;\n  run) printf '%s\\n' \"$@\" > \"$DOCKER_ARGS_FILE\";;\nesac\nexit 0\n",
            "ollama": "#!/bin/sh\nexit 0\n",
            "systemctl": "#!/bin/sh\nexit 0\n",
            "chown": "#!/bin/sh\nexit 0\n",
            "openssl": f"#!/bin/sh\nprintf '%s\\n' '{TOKEN_SENTINEL}'\n",
        }
        for name, body in commands.items():
            path = fake_bin / name
            path.write_text(body, encoding="utf-8")
            path.chmod(0o755)


if __name__ == "__main__":
    unittest.main()
