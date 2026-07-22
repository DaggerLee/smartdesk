from config import HITL_WRITE_NOTE_DEFAULT, HITL_WRITE_NOTE_ENV_VAR


def test_hitl_write_note_phase_a_config_defaults_off():
    assert HITL_WRITE_NOTE_ENV_VAR == "SMARTDESK_HITL_WRITE_NOTE"
    assert HITL_WRITE_NOTE_DEFAULT is False
