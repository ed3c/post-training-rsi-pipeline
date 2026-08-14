from __future__ import annotations

import json

from post_training_rsi.__main__ import main


def test_cli_demo_emits_json(tmp_path, capsys) -> None:
    exit_code = main(["--workspace", str(tmp_path), "demo"])
    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["status"] == "completed"
    assert payload["peak_checkpoint_id"]
