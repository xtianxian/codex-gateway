from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SETUP_SCRIPT = ROOT / "scripts" / "setup.ps1"
INSTALL_SERVICE_SCRIPT = ROOT / "scripts" / "install-gateway-service.ps1"


def powershell() -> str:
    executable = shutil.which("pwsh") or shutil.which("powershell")
    if executable is None:
        pytest.skip("PowerShell is required to verify scripts/setup.ps1")
    return executable


def run_setup(
    env: dict[str, str],
    args: list[str] | None = None,
    input_text: str = "n\nn\n",
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            powershell(),
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(SETUP_SCRIPT),
            *(args or []),
        ],
        cwd=ROOT,
        env=env,
        input=input_text,
        text=True,
        capture_output=True,
        check=False,
    )


def write_fake_uv(tmp_path: Path) -> Path:
    fake_uv = tmp_path / "uv.cmd"
    fake_uv.write_text(
        "@echo off\r\n"
        "echo %*>> \"%UV_LOG%\"\r\n"
        "exit /b 0\r\n",
        encoding="utf-8",
    )
    return fake_uv


def test_setup_script_reports_missing_uv_without_installing(tmp_path: Path) -> None:
    env = os.environ.copy()
    env["PATH"] = str(tmp_path)

    result = run_setup(env)

    assert result.returncode == 1
    assert "uv is missing" in result.stdout
    assert "powershell -ExecutionPolicy ByPass -c" in result.stdout
    assert ".\\scripts\\setup.ps1 -InstallUv" in result.stdout


def test_setup_script_runs_uv_sync_and_pytest(tmp_path: Path) -> None:
    log_path = tmp_path / "uv.log"
    write_fake_uv(tmp_path)
    env = os.environ.copy()
    env["PATH"] = str(tmp_path)
    env["UV_LOG"] = str(log_path)

    result = run_setup(env)

    assert result.returncode == 0, result.stderr
    assert log_path.read_text(encoding="utf-8").splitlines() == [
        "sync --extra dev",
        "run pytest",
    ]
    assert "Next steps:" in result.stdout
    assert "uv run codex-gateway telegram setup" in result.stdout
    assert "uv run codex-gateway telegram status" in result.stdout
    assert ".\\scripts\\install-gateway-service.ps1 -Start" in result.stdout
    assert "uv run codex-gateway telegram run" in result.stdout


def test_setup_script_runs_telegram_setup_when_confirmed(tmp_path: Path) -> None:
    log_path = tmp_path / "uv.log"
    write_fake_uv(tmp_path)
    env = os.environ.copy()
    env["PATH"] = str(tmp_path)
    env["UV_LOG"] = str(log_path)

    result = run_setup(env, input_text="y\nn\n")

    assert result.returncode == 0, result.stderr
    uv_lines = log_path.read_text(encoding="utf-8").splitlines()
    assert uv_lines[:2] == [
        "sync --extra dev",
        "run pytest",
    ]
    assert uv_lines[2] == "run codex-gateway telegram setup"
    assert "Start the gateway, then send /start from the configured Telegram user to get the pairing command:" in result.stdout
    assert re.search(r"/start [A-Z0-9]{4}-[A-Z0-9]{4}", result.stdout) is None


def test_setup_script_installs_windows_service_when_confirmed(tmp_path: Path) -> None:
    uv_log_path = tmp_path / "uv.log"
    service_log_path = tmp_path / "service-install.log"
    write_fake_uv(tmp_path)
    env = os.environ.copy()
    env["PATH"] = str(tmp_path)
    env["UV_LOG"] = str(uv_log_path)
    env["CODEX_GATEWAY_TEST_SERVICE_INSTALL_LOG"] = str(service_log_path)

    result = run_setup(env, input_text="n\ny\n")

    assert result.returncode == 0, result.stderr
    service_lines = service_log_path.read_text(encoding="utf-8").splitlines()
    assert service_lines == [
        "install ServiceName=CodexGateway DisplayName=Codex Gateway Start=True",
    ]
    assert "Windows Service installed and started: CodexGateway" in result.stdout
    assert ".codex-gateway\\logs\\service" in result.stdout


def test_service_installer_does_not_dump_environment_values() -> None:
    script = INSTALL_SERVICE_SCRIPT.read_text(encoding="utf-8")

    assert '-Name "Environment"' in script
    assert "Select-Object -ExpandProperty Environment" not in script
    assert "Service environment configured" in script


def test_setup_script_prints_start_instruction_after_starting_gateway(tmp_path: Path) -> None:
    uv_log_path = tmp_path / "uv.log"
    service_log_path = tmp_path / "service-install.log"
    write_fake_uv(tmp_path)
    env = os.environ.copy()
    env["PATH"] = str(tmp_path)
    env["UV_LOG"] = str(uv_log_path)
    env["CODEX_GATEWAY_TEST_SERVICE_INSTALL_LOG"] = str(service_log_path)

    result = run_setup(env, input_text="y\ny\n")

    assert result.returncode == 0, result.stderr
    assert "Codex Gateway can receive Telegram messages now." in result.stdout
    assert "Send /start from the configured Telegram user to get the pairing command." in result.stdout
    assert re.search(r"/start [A-Z0-9]{4}-[A-Z0-9]{4}", result.stdout) is None
    assert result.stdout.index("Windows Service installed and started: CodexGateway") < result.stdout.index("Send /start")


def test_setup_script_remove_startup_removes_windows_service(tmp_path: Path) -> None:
    service_log_path = tmp_path / "service-uninstall.log"
    env = os.environ.copy()
    env["PATH"] = str(tmp_path)
    env["CODEX_GATEWAY_TEST_SERVICE_UNINSTALL_LOG"] = str(service_log_path)

    result = run_setup(env, args=["-RemoveStartup"], input_text="")

    assert result.returncode == 0, result.stderr
    assert service_log_path.read_text(encoding="utf-8").splitlines() == [
        "uninstall ServiceName=CodexGateway",
    ]
    assert "Windows Service removal requested: CodexGateway" in result.stdout


def test_setup_script_skip_flags_do_not_prompt_or_register(tmp_path: Path) -> None:
    uv_log_path = tmp_path / "uv.log"
    service_log_path = tmp_path / "service-install.log"
    write_fake_uv(tmp_path)
    env = os.environ.copy()
    env["PATH"] = str(tmp_path)
    env["UV_LOG"] = str(uv_log_path)
    env["CODEX_GATEWAY_TEST_SERVICE_INSTALL_LOG"] = str(service_log_path)

    result = run_setup(env, args=["-SkipTelegramSetup", "-SkipStartup"], input_text="")

    assert result.returncode == 0, result.stderr
    assert uv_log_path.read_text(encoding="utf-8").splitlines() == [
        "sync --extra dev",
        "run pytest",
    ]
    assert "Run Telegram setup now?" not in result.stdout
    assert "Start Codex Gateway" not in result.stdout
    assert not service_log_path.exists()
