"""Send one model decision through the native v17 command path."""
import argparse
import base64
import ctypes
from ctypes import wintypes
import json
import os
import shlex
import subprocess
import sys
import time

import matches
from match_windows import Windows


KEYMAP = "toprow_commands_v17"
COMMANDS = {
    "2": ("mine_lock", "mining_locked", "Assign the available workers to home mineral mining."),
    "-": ("restore_auto", "auto_restored", "Leave worker and army decisions to the baseline."),
}
# Run this client where the configured endpoint and credentials are accessible.
HTTP_CLIENT = '''
import json, os, sys, urllib.request
from pathlib import Path
endpoint, key_file = sys.argv[1:]
key = Path(key_file).read_text().strip() if key_file else os.getenv("STAGE1_API_KEY")
headers = {"Content-Type": "application/json"}
if key:
    headers["Authorization"] = "Bearer " + key
request = urllib.request.Request(endpoint, data=sys.stdin.buffer.read(), headers=headers)
with urllib.request.urlopen(request, timeout=18) as response:
    print(json.dumps(json.load(response)))
'''


def decision_messages(state, context=None):
    handoff = (" Follow the active policy using the current observation and consider the previous review."
               if context is not None else "")
    return [
        {"role": "system", "content": (
            "You control Protoss in an early Brood War match. Choose the better action "
            "for the observed state. The baseline normally handles mining and production. "
            "Only override miners if the state justifies it." + handoff + " Return exactly one key character. "
            + " ".join(f"{key}: {description}" for key, (_, _, description) in COMMANDS.items())
        )},
        {"role": "user", "content": json.dumps(state if context is None else {"observation": state, **context})},
    ]


def model_call(args, messages, max_tokens, archive, *, json_object=False):
    body = {
        "model": args.model,
        "temperature": 0,
        "max_tokens": max_tokens,
        "messages": messages,
    }
    if args.no_thinking:
        body["chat_template_kwargs"] = {"enable_thinking": False}
    if json_object:
        body["response_format"] = {"type": "json_object"}
    matches.store(archive, {"request": body})
    client_args = [args.endpoint.rstrip("/") + "/chat/completions", args.key_file or ""]
    if args.ssh:
        payload = base64.b64encode(HTTP_CLIENT.encode()).decode("ascii")
        code = f'import base64;exec(base64.b64decode("{payload}"))'
        command = ["ssh", "-T", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
                   "-o", "ConnectTimeout=10", args.ssh,
                   shlex.join(["python3", "-c", code, *client_args])]
    else:
        command = [sys.executable, "-c", HTTP_CLIENT, *client_args]
    started = time.monotonic()
    result = subprocess.run(command, input=json.dumps(body), encoding="utf-8",
                              stdout=subprocess.PIPE, check=True, timeout=28)
    response = json.loads(result.stdout)
    matches.store(archive, {"request": body, "response": response,
                            "seconds": round(time.monotonic() - started, 3)})
    return response


def next_row(reader, predicate, seconds):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        offset = reader.tell()
        line = reader.readline()
        if line.endswith(b"\n"):
            row = json.loads(line)
            if predicate(row):
                return row
        else:
            reader.seek(offset)
            time.sleep(.05)
    raise RuntimeError("等待 v17 原生日志超时。")


def game_window(output):
    api = Windows()
    pid, _ = api.owner(int((output / "game-window.txt").read_text()))
    return next(w["hwnd"] for w in api.windows({pid: "self"}) if w["window_class"] == "SWarClass")


def post_key(hwnd, key):
    post = ctypes.WinDLL("user32", use_last_error=True).PostMessageW
    post.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    vk = 0xBD if key == "-" else ord(key)
    for message, flags in ((0x100, 1), (0x101, 1 | (1 << 30) | (1 << 31))):
        if not post(hwnd, message, vk, flags):
            raise ctypes.WinError(ctypes.get_last_error())


def decide_in_match(run, args, report, context):
    output = run / "bwapi-data/write"
    hwnd = game_window(output)
    with (output / "commander-layer.jsonl").open("rb") as reader:
        start = next_row(reader, lambda row: row["event"] == "start", 3)
        if start.get("keymap") != KEYMAP or not start["human_layer"]:
            raise RuntimeError("本局未启用 v17 原生指挥层。")
        state = next_row(reader, lambda row: row["event"] == "state" and row["frame"] >= 120, 15)
        evidence = {"native_start": start, "observation": state, "requested_model": args.model}
        matches.store(report, evidence)
        response = model_call(args, decision_messages(state, context), 32, run / "decision-call.json")
        choice = response["choices"][0]
        raw = choice["message"]["content"] or ""
        key = raw.strip()
        evidence.update(model=response["model"], response_id=response["id"], raw_decision=raw,
                        finish_reason=choice["finish_reason"], key=key)
        matches.store(report, evidence)
        if key not in COMMANDS:
            raise RuntimeError(f"模型未返回受支持的单键，详见 {report}")
        reader.seek(0, os.SEEK_END)
        post_key(hwnd, key)
        receipt = next_row(reader, lambda row: row["event"] == "command", 5)
        evidence["receipt"] = receipt
        matches.store(report, evidence)
        action, status, _ = COMMANDS[key]
        if (receipt["keymap"], receipt["key"], receipt["modifier"], receipt["action"], receipt["status"]) != (
                KEYMAP, key, "none", action, status):
            raise RuntimeError(f"原生回执与模型决策不符，详见 {report}")


def model_arguments(parser):
    parser.add_argument("--model", required=True)
    parser.add_argument("--endpoint", required=True, help="OpenAI-compatible API base ending in /v1")
    parser.add_argument("--ssh", help="Run the API request on this configured SSH host")
    parser.add_argument("--key-file", help="API key file on the host making the request; defaults to STAGE1_API_KEY")
    parser.add_argument("--no-thinking", action="store_true", help="Use the Qwen vLLM non-thinking template")


def run_once(run, args, *, report_name="stage1.json", context=None, speed=20,
             smoke_frames=1800, match_timeout=None):
    dispatched = False
    report = run / report_name

    def update(_):
        nonlocal dispatched
        if not dispatched and matches.read_json(run / "match.json")["state"] == "playing":
            dispatched = True
            decide_in_match(run, args, report, context)

    print(run, flush=True)
    result = matches.playing(run, "Destination", "PylonPuller", speed=speed, fullscreen=False,
                             update=update, smoke_frames=smoke_frames, render_profile="standard",
                             data_only=True, keep_results=False, match_timeout=match_timeout)
    rows = (run / "bwapi-data/write/commander-layer.jsonl").read_text(encoding="utf-8").splitlines()
    commands = [row for row in map(json.loads, rows) if row["event"] == "command"]
    evidence = matches.read_json(report)
    if result["state"] != "finished" or commands != [evidence["receipt"]]:
        raise RuntimeError("需对局正常结束并且恰有一条对应的原生指挥回执。")
    evidence.update(match=result, native_command_count=len(commands))
    matches.store(report, evidence)
    print(report)
    return evidence


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    model_arguments(parser)
    run_once(matches.new_run(), parser.parse_args())


if __name__ == "__main__":
    main()
