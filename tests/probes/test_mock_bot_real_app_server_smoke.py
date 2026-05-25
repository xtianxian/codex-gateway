from __future__ import annotations

import pytest

from testing.probes import mock_bot_real_app_server_smoke as smoke


def test_default_hybrid_skips_rollout_dependent_archive_commands() -> None:
    assert smoke.DEFAULT_SKIPPED_COMMAND_NAMES >= {"archive", "unarchive"}
    assert "persisted rollout" in smoke.DEFAULT_COMMAND_SKIP_REASONS["archive"]
    assert "persisted rollout" in smoke.DEFAULT_COMMAND_SKIP_REASONS["unarchive"]


def test_apps_forbidden_response_is_classified_as_feature_gated_skip() -> None:
    result = smoke._classify_command_result(
        "/apps",
        "App-server command failed: failed to list apps: Request failed with status 403 Forbidden",
    )

    assert result.status == "skip"
    assert "feature-gated" in result.detail


def test_audit_affected_rollback_failure_is_classified_as_state_skip() -> None:
    default_result = smoke._classify_command_result(
        "/rollback",
        "App-server command failed: no completed turns to roll back",
    )
    audit_result = smoke._classify_command_result(
        "/rollback",
        "App-server command failed: no completed turns to roll back",
        audit_affected=True,
    )

    assert default_result is not None
    assert default_result.status == "fail"
    assert audit_result is not None
    assert audit_result.status == "skip"
    assert "completed turn history" in audit_result.detail


@pytest.mark.asyncio
async def test_callback_keyboard_clear_requirement_detects_missing_markup() -> None:
    bot = smoke.MockBot()
    results: list[smoke.SmokeResult] = []

    class Bridge:
        async def handle_update(self, _update: dict[str, object]) -> None:
            await bot.edit_message_text(smoke.CHAT_ID, 1001, "done")

    await smoke._choose(
        Bridge(),
        bot,
        results,
        "/model callback GPT",
        "select:token",
        1001,
        require_keyboard_clear=True,
    )

    assert results[-1].status == "fail"
    assert "clear the inline keyboard" in results[-1].detail


@pytest.mark.asyncio
async def test_callback_keyboard_clear_requirement_accepts_empty_keyboard_markup() -> None:
    bot = smoke.MockBot()
    results: list[smoke.SmokeResult] = []

    class Bridge:
        async def handle_update(self, _update: dict[str, object]) -> None:
            await bot.edit_message_text(
                smoke.CHAT_ID,
                1001,
                "done",
                reply_markup=smoke.CALLBACK_CLEAR_REPLY_MARKUP,
            )

    await smoke._choose(
        Bridge(),
        bot,
        results,
        "/model callback GPT",
        "select:token",
        1001,
        require_keyboard_clear=True,
    )

    assert results[-1].status == "ok"
    assert results[-1].detail == "done"


def test_cli_fixture_covers_registry_and_parser_commands() -> None:
    results: list[smoke.SmokeResult] = []

    smoke._assert_fixture_completeness(results)

    assert results == []
    assert {"model", "permissions", "plugins", "debug-config", "stop"} <= set(smoke.CLI_SLASH_COMMAND_FIXTURE)


def test_command_coverage_fails_when_fixture_command_is_missing() -> None:
    results = [smoke.SmokeResult("/start", "ok", "ok")]

    smoke._assert_command_coverage(results, skipped_command_names=set())

    failures = [result for result in results if result.status == "fail"]
    assert failures
    assert "missing coverage" in failures[-1].detail
    assert "model" in failures[-1].detail


def test_command_coverage_accepts_explicit_skip_classification() -> None:
    names = smoke.parity_command_names()
    results = [smoke.SmokeResult(f"/{name}", "ok", "ok") for name in names - {"apps"}]
    results.append(smoke.SmokeResult("/apps", "skip", "feature-gated"))

    smoke._assert_command_coverage(results, skipped_command_names={"apps"})

    assert [result for result in results if result.status == "fail"] == []
