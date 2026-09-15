"""The config file, whose most important case is the one where there is no config file."""

from __future__ import annotations

from pathlib import Path

import pytest

from tmux_tools.config import Config, config_path, load_config


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "config.toml"
    path.write_text(text)
    return path


def test_no_config_file_means_scan_everything(tmp_path: Path):
    assert load_config(tmp_path / "nothing-here.toml") == Config()


def test_an_empty_file_means_scan_everything(tmp_path: Path):
    assert load_config(write(tmp_path, "")) == Config()


def test_a_file_without_a_devices_table_means_scan_everything(tmp_path: Path):
    assert load_config(write(tmp_path, "[something-else]\nkey = 1\n")) == Config()


def test_both_keys_are_read(tmp_path: Path):
    config = load_config(write(tmp_path, '[devices]\nonly = ["laptop"]\nskip = ["cache", "runner"]\n'))
    assert config == Config(only=("laptop",), skip=("cache", "runner"))


def test_an_empty_list_is_not_a_selection(tmp_path: Path):
    assert load_config(write(tmp_path, "[devices]\nonly = []\n")) == Config()


def test_a_malformed_file_stops_the_command(tmp_path: Path):
    with pytest.raises(SystemExit):
        load_config(write(tmp_path, "[devices\nonly = \n"))


def test_a_string_where_a_list_belongs_stops_the_command(tmp_path: Path):
    with pytest.raises(SystemExit):
        load_config(write(tmp_path, '[devices]\nonly = "laptop"\n'))


def test_a_list_of_something_other_than_names_stops_the_command(tmp_path: Path):
    with pytest.raises(SystemExit):
        load_config(write(tmp_path, "[devices]\nskip = [1, 2]\n"))


def test_a_devices_key_that_is_not_a_table_stops_the_command(tmp_path: Path):
    with pytest.raises(SystemExit):
        load_config(write(tmp_path, 'devices = "laptop"\n'))


def test_the_config_lives_under_xdg_config_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert config_path() == tmp_path / "fprochazka-tmux-tools" / "config.toml"


def test_without_xdg_config_home_the_config_lives_under_dot_config(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    assert config_path() == Path.home() / ".config" / "fprochazka-tmux-tools" / "config.toml"
