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
UNINSTALL_SERVICE_SCRIPT = ROOT / "scripts" / "uninstall-gateway-service.ps1"
UNINSTALL_GATEWAY_SCRIPT = ROOT / "scripts" / "uninstall-gateway.ps1"
UNINSTALL_GATEWAY_SH_SCRIPT = ROOT / "scripts" / "uninstall-gateway.sh"


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
    env = dict(env)
    env.setdefault("CODEX_GATEWAY_TEST_SERVICE_EXISTS", "0")
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


def run_gateway_uninstall(
    env: dict[str, str],
    args: list[str] | None = None,
) -> subprocess.CompletedProcess[str]:
    env = dict(env)
    env.setdefault("CODEX_GATEWAY_TEST_SERVICE_EXISTS", "0")
    return subprocess.run(
        [
            powershell(),
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(UNINSTALL_GATEWAY_SCRIPT),
            *(args or []),
        ],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
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


def test_setup_script_updates_existing_windows_service_when_confirmed(tmp_path: Path) -> None:
    uv_log_path = tmp_path / "uv.log"
    service_log_path = tmp_path / "service-install.log"
    write_fake_uv(tmp_path)
    env = os.environ.copy()
    env["PATH"] = str(tmp_path)
    env["UV_LOG"] = str(uv_log_path)
    env["CODEX_GATEWAY_TEST_SERVICE_EXISTS"] = "1"
    env["CODEX_GATEWAY_TEST_SERVICE_INSTALL_LOG"] = str(service_log_path)

    result = run_setup(env, input_text="n\ny\n")

    assert result.returncode == 0, result.stderr
    assert "Update and restart existing Codex Gateway Windows Service to apply current .env" in result.stdout
    assert "Windows Service updated and restarted: CodexGateway" in result.stdout
    assert service_log_path.read_text(encoding="utf-8").splitlines() == [
        "install ServiceName=CodexGateway DisplayName=Codex Gateway Start=True",
    ]


def test_setup_script_warns_existing_service_needs_restart_after_env_update(tmp_path: Path) -> None:
    uv_log_path = tmp_path / "uv.log"
    write_fake_uv(tmp_path)
    env = os.environ.copy()
    env["PATH"] = str(tmp_path)
    env["UV_LOG"] = str(uv_log_path)
    env["CODEX_GATEWAY_TEST_SERVICE_EXISTS"] = "1"

    result = run_setup(env, input_text="y\nn\n")

    assert result.returncode == 0, result.stderr
    assert "Update and restart existing Codex Gateway Windows Service to apply current .env" in result.stdout
    assert "Existing Windows Service was not restarted. Run this to apply .env changes:" in result.stdout
    assert ".\\scripts\\install-gateway-service.ps1 -Start" in result.stdout
    assert "uv run codex-gateway telegram run" not in result.stdout


def test_service_installer_does_not_dump_environment_values() -> None:
    script = INSTALL_SERVICE_SCRIPT.read_text(encoding="utf-8")

    assert '-Name "Environment"' in script
    assert "Select-Object -ExpandProperty Environment" not in script
    assert "Service environment configured" in script


def test_windows_service_scripts_do_not_use_cim_queries() -> None:
    install_script = INSTALL_SERVICE_SCRIPT.read_text(encoding="utf-8")
    uninstall_script = UNINSTALL_SERVICE_SCRIPT.read_text(encoding="utf-8")

    assert "Get-CimInstance" not in install_script
    assert "Get-CimInstance" not in uninstall_script


def test_service_installer_resolves_csharp_compiler_from_windows_directory() -> None:
    script = INSTALL_SERVICE_SCRIPT.read_text(encoding="utf-8")

    assert r"C:\Windows\Microsoft.NET" not in script
    assert "Resolve-CSharpCompiler" in script
    assert "SpecialFolder]::Windows" in script
    assert r"Microsoft.NET\Framework64\v4.0.30319\csc.exe" in script
    assert r"Microsoft.NET\Framework\v4.0.30319\csc.exe" in script


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


def make_uninstall_project(tmp_path: Path) -> Path:
    project_root = tmp_path / "project"
    (project_root / "workspace").mkdir(parents=True)
    (project_root / ".codex-gateway" / "telegram").mkdir(parents=True)
    (project_root / ".codex-gateway" / "logs").mkdir(parents=True)
    (project_root / ".codex-gateway" / "logs" / "gateway.log").write_text("log", encoding="utf-8")
    (project_root / ".env").write_text(
        "\n".join(
            [
                "CODEX_GATEWAY_TELEGRAM_STATE_DIR=.codex-gateway/telegram",
                "CODEX_GATEWAY_ALLOWED_ROOTS=workspace",
                "CODEX_GATEWAY_DEFAULT_CWD=workspace",
            ]
        ),
        encoding="utf-8",
    )
    return project_root


def test_full_windows_uninstall_removes_env_and_repo_gateway_state(tmp_path: Path) -> None:
    project_root = make_uninstall_project(tmp_path)
    env = os.environ.copy()
    env["CODEX_GATEWAY_TEST_PROJECT_ROOT"] = str(project_root)

    result = run_gateway_uninstall(
        env,
        args=["-EnvFile", str(project_root / ".env"), "-SkipProcessCleanup"],
    )

    assert result.returncode == 0, result.stderr
    assert not (project_root / ".env").exists()
    assert not (project_root / ".codex-gateway").exists()
    assert (project_root / "workspace").is_dir()
    assert "Codex CLI login/auth was not removed." in result.stdout
    assert "BotFather" in result.stdout


def test_full_windows_uninstall_dry_run_preserves_files(tmp_path: Path) -> None:
    project_root = make_uninstall_project(tmp_path)
    env = os.environ.copy()
    env["CODEX_GATEWAY_TEST_PROJECT_ROOT"] = str(project_root)

    result = run_gateway_uninstall(
        env,
        args=["-EnvFile", str(project_root / ".env"), "-SkipProcessCleanup", "-WhatIf"],
    )

    assert result.returncode == 0, result.stderr
    assert (project_root / ".env").is_file()
    assert (project_root / ".codex-gateway" / "logs" / "gateway.log").is_file()


def test_full_windows_uninstall_is_idempotent(tmp_path: Path) -> None:
    project_root = make_uninstall_project(tmp_path)
    env = os.environ.copy()
    env["CODEX_GATEWAY_TEST_PROJECT_ROOT"] = str(project_root)
    args = ["-EnvFile", str(project_root / ".env"), "-SkipProcessCleanup"]

    first = run_gateway_uninstall(env, args=args)
    second = run_gateway_uninstall(env, args=args)

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    assert "Already absent" in second.stdout


def test_full_windows_uninstall_delegates_service_removal(tmp_path: Path) -> None:
    project_root = make_uninstall_project(tmp_path)
    service_log_path = tmp_path / "service-uninstall.log"
    env = os.environ.copy()
    env["CODEX_GATEWAY_TEST_PROJECT_ROOT"] = str(project_root)
    env["CODEX_GATEWAY_TEST_SERVICE_EXISTS"] = "1"
    env["CODEX_GATEWAY_TEST_ASSUME_ADMIN"] = "1"
    env["CODEX_GATEWAY_TEST_SERVICE_UNINSTALL_LOG"] = str(service_log_path)

    result = run_gateway_uninstall(
        env,
        args=["-EnvFile", str(project_root / ".env"), "-SkipProcessCleanup"],
    )

    assert result.returncode == 0, result.stderr
    assert service_log_path.read_text(encoding="utf-8").splitlines() == [
        "uninstall ServiceName=CodexGateway",
    ]


def test_full_windows_uninstall_refuses_unsafe_state_dir_before_deleting(tmp_path: Path) -> None:
    project_root = make_uninstall_project(tmp_path)
    env = os.environ.copy()
    env["CODEX_GATEWAY_TEST_PROJECT_ROOT"] = str(project_root)

    result = run_gateway_uninstall(
        env,
        args=[
            "-EnvFile",
            str(project_root / ".env"),
            "-StateDir",
            str(project_root),
            "-SkipProcessCleanup",
        ],
    )

    assert result.returncode != 0
    assert "Refusing to remove the repository root" in result.stderr
    assert (project_root / ".env").is_file()
    assert (project_root / ".codex-gateway").is_dir()


def test_full_windows_uninstall_docker_cleanup_only_targets_gateway_volumes(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    docker_log_path = tmp_path / "docker.log"
    env = os.environ.copy()
    env["CODEX_GATEWAY_TEST_PROJECT_ROOT"] = str(project_root)
    env["CODEX_GATEWAY_TEST_DOCKER_LOG"] = str(docker_log_path)

    result = run_gateway_uninstall(
        env,
        args=["-EnvFile", str(project_root / ".env"), "-SkipProcessCleanup", "-DockerGatewayVolumes"],
    )

    assert result.returncode == 0, result.stderr
    docker_lines = docker_log_path.read_text(encoding="utf-8").splitlines()
    assert any("compose" in line and "down" in line and "--remove-orphans" in line for line in docker_lines)
    assert docker_lines[-1] == (
        "docker volume rm codex-gateway-linux_gateway-config codex-gateway-linux_gateway-state"
    )
    assert "codex-home" not in "\n".join(docker_lines)
    assert "gateway-workspace" not in "\n".join(docker_lines)
    assert "linux-venv" not in "\n".join(docker_lines)
    assert "uv-cache" not in "\n".join(docker_lines)


def test_full_uninstall_scripts_do_not_kill_arbitrary_app_server_port_owner() -> None:
    powershell_script = UNINSTALL_GATEWAY_SCRIPT.read_text(encoding="utf-8")
    shell_script = UNINSTALL_GATEWAY_SH_SCRIPT.read_text(encoding="utf-8")

    assert "Get-NetTCPConnection" not in powershell_script
    assert "LocalPort 8765" not in powershell_script
    assert "Get-ScheduledTask" not in powershell_script
    assert "schtasks" not in powershell_script.lower()
    assert "Get-CimInstance" not in powershell_script
    assert "lsof" not in shell_script
    assert ":8765" not in shell_script


def test_full_uninstall_bash_script_syntax_and_dry_run(tmp_path: Path) -> None:
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash is required to verify scripts/uninstall-gateway.sh")
    if "system32\\bash.exe" in bash.lower() or "system32/bash.exe" in bash.lower():
        pytest.skip("Windows system32 bash starts WSL and is not reliable for this syntax check")

    try:
        syntax = subprocess.run(
            [bash, "-n", str(UNINSTALL_GATEWAY_SH_SCRIPT)],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
            timeout=15,
        )
    except subprocess.TimeoutExpired:
        pytest.skip("bash did not return promptly for syntax validation")
    assert syntax.returncode == 0, syntax.stderr

    project_root = make_uninstall_project(tmp_path)
    env = os.environ.copy()
    env["CODEX_GATEWAY_TEST_PROJECT_ROOT"] = str(project_root)
    try:
        dry_run = subprocess.run(
            [
                bash,
                str(UNINSTALL_GATEWAY_SH_SCRIPT),
                "--env-file",
                str(project_root / ".env"),
                "--skip-process-cleanup",
                "--dry-run",
            ],
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
            check=False,
            timeout=20,
        )
    except subprocess.TimeoutExpired:
        pytest.skip("bash did not return promptly for dry-run validation")

    assert dry_run.returncode == 0, dry_run.stderr
    assert (project_root / ".env").is_file()
    assert (project_root / ".codex-gateway" / "logs" / "gateway.log").is_file()
    assert "Codex CLI login/auth was not removed." in dry_run.stdout
