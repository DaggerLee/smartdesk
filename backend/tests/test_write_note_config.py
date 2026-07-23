from pathlib import Path

import config


def test_write_note_root_defaults_to_the_persistent_data_directory() -> None:
    assert config.WRITE_NOTE_ROOT_ENV_VAR == "SMARTDESK_DATA_DIR"
    assert config.WRITE_NOTE_ROOT_DEFAULT == Path("data")
    assert config.WRITE_NOTE_ROOT == Path("data")


def test_docker_volume_covers_the_write_note_root() -> None:
    compose = (Path(__file__).parents[2] / "docker-compose.yml").read_text()
    assert "smartdesk_data:/app/data" in compose
    assert config.WRITE_NOTE_ROOT_DEFAULT == Path("data")
