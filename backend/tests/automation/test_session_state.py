import json

from dealercenter_agent.automation.session_state import load_saved_state, save_state


def test_load_saved_state_returns_none_when_file_missing(tmp_path):
    assert load_saved_state(tmp_path / "missing.json") is None


def test_load_saved_state_returns_parsed_json_when_present(tmp_path):
    path = tmp_path / "state.json"
    path.write_text(json.dumps({"cookies": [], "origins": []}))
    assert load_saved_state(path) == {"cookies": [], "origins": []}


def test_save_state_creates_file_when_absent(tmp_path, capsys):
    path = tmp_path / "state.json"
    assert not path.exists()

    save_state(path, {"cookies": [1]})

    assert path.exists()
    assert json.loads(path.read_text()) == {"cookies": [1]}
    assert "Created new" in capsys.readouterr().out


def test_save_state_overwrites_file_when_present(tmp_path, capsys):
    path = tmp_path / "state.json"
    path.write_text(json.dumps({"cookies": ["stale"]}))

    save_state(path, {"cookies": ["fresh"]})

    assert json.loads(path.read_text()) == {"cookies": ["fresh"]}
    assert "Overwrote existing" in capsys.readouterr().out
