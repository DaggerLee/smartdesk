from config import HITL_WRITE_NOTE_DEFAULT, HITL_WRITE_NOTE_ENV_VAR


def test_hitl_write_note_cutover_config_defaults_on():
    assert HITL_WRITE_NOTE_ENV_VAR == "SMARTDESK_HITL_WRITE_NOTE"
    assert HITL_WRITE_NOTE_DEFAULT is True
