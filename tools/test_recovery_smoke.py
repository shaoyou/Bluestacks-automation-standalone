"""Smoke tests for screenshot retry + device recovery logic in adb_bot.py."""
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402

import adb_bot  # noqa: E402

REAL_RUN_CMD = adb_bot.run_cmd  # captured before any test monkeypatches it


def make_png_bytes(w=8, h=6):
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (200, 30, 30)).save(buf, format="PNG")
    return buf.getvalue()


def make_ctx():
    return adb_bot.RunContext(
        adb_path="adb", hdc_path="hdc", device_backend="adb", device="emulator-5564",
        user_id="default", source_id="", source_name="", source_file=None,
        dry_run=False, jitter=0, stop_at=None, src_screen_w=1080, src_screen_h=1920,
        dst_screen_w=1080, dst_screen_h=1920, trace_time_scale=1.0,
        plan_dir=None, image_match_debug=False,
    )


def fail(proc):
    return __import__("subprocess").CompletedProcess(args=[], returncode=1, stdout=b"", stderr=proc)


def test_screenshot_retry_recovers():
    ctx = make_ctx()
    png = make_png_bytes()
    calls = {"n": 0}
    connects = {"n": 0}

    def fake_exec_out(c, extra):
        calls["n"] += 1
        if calls["n"] <= 2:  # raw + png fail twice, then png succeeds
            return fail(b"")
        return __import__("subprocess").CompletedProcess(args=[], returncode=0, stdout=png, stderr=b"")

    def fake_run_cmd(cmd, check=True):
        if "connect" in cmd:
            connects["n"] += 1
        return __import__("subprocess").CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    adb_bot.adb_exec_out_result = fake_exec_out
    adb_bot.run_cmd = fake_run_cmd
    img = adb_bot.capture_device_screenshot(ctx)
    assert img.size == (8, 6), img.size
    assert calls["n"] == 4, calls  # 2 failing attempts (raw+png each) + 1 success png
    assert connects["n"] >= 1, "expected adb connect between attempts"
    print("test_screenshot_retry_recovers OK")


def test_screenshot_raises_deviceerror_after_retries():
    ctx = make_ctx()
    calls = {"n": 0}

    def fake_exec_out(c, extra):
        calls["n"] += 1
        return fail(b"")

    adb_bot.adb_exec_out_result = fake_exec_out
    adb_bot.run_cmd = lambda cmd, check=True: __import__("subprocess").CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
    adb_bot.recover_adb_server = lambda *a, **k: None
    try:
        adb_bot.capture_device_screenshot(ctx)
    except adb_bot.DeviceError as exc:
        assert "unknown error" in str(exc)
        assert calls["n"] == 6, calls  # 3 attempts x (raw+png)
        print("test_screenshot_raises_deviceerror_after_retries OK")
    else:
        raise AssertionError("expected DeviceError")


def test_recovery_resumes_plan():
    ctx = make_ctx()
    runs = {"n": 0}
    recoveries = {"n": 0}

    def fake_execute(c, actions):
        runs["n"] += 1
        if runs["n"] == 1:
            raise adb_bot.DeviceError("ADB screenshot failed:\nunknown error")

    def fake_recover(c, grace):
        recoveries = grace
        assert grace == 300.0, grace
        recoveries_record[0] = True
        return True

    recoveries_record = [False]
    adb_bot.execute_actions = fake_execute
    adb_bot.recover_device_connection = fake_recover
    adb_bot.run_actions_with_recovery(ctx, [{"type": "loop"}], 300.0)
    assert runs["n"] == 2, runs
    assert recoveries_record[0]
    print("test_recovery_resumes_plan OK")


def test_recovery_gives_up_when_grace_expires():
    ctx = make_ctx()
    adb_bot.execute_actions = lambda c, a: (_ for _ in ()).throw(adb_bot.DeviceError("down"))
    adb_bot.recover_device_connection = lambda c, g: False
    try:
        adb_bot.run_actions_with_recovery(ctx, [], 300.0)
    except adb_bot.DeviceError:
        print("test_recovery_gives_up_when_grace_expires OK")
    else:
        raise AssertionError("expected DeviceError to propagate")


def test_recovery_gives_up_on_immediate_refailures():
    ctx = make_ctx()
    runs = {"n": 0}
    recoveries = {"n": 0}

    def fake_execute(c, actions):
        runs["n"] += 1
        raise adb_bot.DeviceError("still down")

    def fake_recover(c, grace):
        recoveries["n"] += 1
        return True

    adb_bot.execute_actions = fake_execute
    adb_bot.recover_device_connection = fake_recover
    # Bypass the no-progress window timing by faking monotonic jumps.
    clock = {"t": 1000.0}
    real_monotonic = adb_bot.time.monotonic
    adb_bot.time.monotonic = lambda: clock["t"]

    def fake_sleep(sec):
        clock["t"] += 30  # each sleep in the wrapper's callers jumps past the 20s window

    adb_bot.time.sleep = fake_sleep
    try:
        adb_bot.run_actions_with_recovery(ctx, [], 300.0)
    except adb_bot.DeviceError:
        assert runs["n"] == 4, runs  # initial + 3 no-progress resumes
        assert recoveries["n"] == 3, recoveries
        print("test_recovery_gives_up_on_immediate_refailures OK")
    else:
        raise AssertionError("expected DeviceError to propagate")
    finally:
        adb_bot.time.monotonic = real_monotonic


def test_zero_grace_keeps_old_behavior():
    ctx = make_ctx()

    def fake_execute(c, actions):
        raise adb_bot.DeviceError("down")

    called = {"recover": 0}
    adb_bot.execute_actions = fake_execute
    adb_bot.recover_device_connection = lambda c, g: called.__setitem__("recover", called["recover"] + 1) or True
    try:
        adb_bot.run_actions_with_recovery(ctx, [], 0.0)
    except adb_bot.DeviceError:
        assert called["recover"] == 0
        print("test_zero_grace_keeps_old_behavior OK")
    else:
        raise AssertionError("expected DeviceError to propagate")


def test_spawn_lock_retried_in_run_cmd():
    import subprocess as sp

    attempts = {"n": 0}

    def fake_run(cmd, **kwargs):
        attempts["n"] += 1
        if attempts["n"] <= 2:
            raise PermissionError(32, "另一个程序正在使用此文件，进程无法访问。")
        return sp.CompletedProcess(args=cmd, returncode=0, stdout="ok", stderr="")

    original_run = adb_bot.subprocess.run
    adb_bot.subprocess.run = fake_run
    try:
        result = REAL_RUN_CMD(["adb", "devices"])
        assert result.returncode == 0
        assert attempts["n"] == 3, attempts
    finally:
        adb_bot.subprocess.run = original_run
    print("test_spawn_lock_retried_in_run_cmd OK")


def test_spawn_lock_escalates_to_device_error():
    import subprocess as sp

    def fake_run(cmd, **kwargs):
        raise PermissionError(32, "另一个程序正在使用此文件，进程无法访问。")

    original_run = adb_bot.subprocess.run
    adb_bot.subprocess.run = fake_run
    try:
        REAL_RUN_CMD(["adb", "devices"])
    except PermissionError:
        print("test_spawn_lock_escalates_to_device_error OK")
    else:
        raise AssertionError("expected PermissionError to propagate")
    finally:
        adb_bot.subprocess.run = original_run


def test_recovery_handles_oserror():
    ctx = make_ctx()
    runs = {"n": 0}
    recovered = {"recovered": 0}

    def fake_execute(c, actions):
        runs["n"] += 1
        if runs["n"] == 1:
            raise PermissionError(32, "另一个程序正在使用此文件，进程无法访问。")

    adb_bot.execute_actions = fake_execute
    adb_bot.recover_device_connection = lambda c, g: recovered.__setitem__("recovered", recovered["recovered"] + 1) or True
    adb_bot.run_actions_with_recovery(ctx, [{"type": "loop"}], 300.0)
    assert runs["n"] == 2 and recovered["recovered"] == 1, (runs, recovered)
    print("test_recovery_handles_oserror OK")


if __name__ == "__main__":
    test_screenshot_retry_recovers()
    test_screenshot_raises_deviceerror_after_retries()
    test_recovery_resumes_plan()
    test_recovery_gives_up_when_grace_expires()
    test_recovery_gives_up_on_immediate_refailures()
    test_zero_grace_keeps_old_behavior()
    test_spawn_lock_retried_in_run_cmd()
    test_spawn_lock_escalates_to_device_error()
    test_recovery_handles_oserror()
    print("ALL SMOKE TESTS PASSED")
