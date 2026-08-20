import pytest
from pydantic import ValidationError

from app.game.models import (
    BaseMessage,
    JoinGamePayload,
    MessageType,
    ScorePayload,
    SubmitAnswersPayload,
    UpdateSettingsPayload,
)


def test_player_names_are_trimmed_and_bounded():
    assert JoinGamePayload(player_name="  Alex  ").player_name == "Alex"

    with pytest.raises(ValidationError):
        JoinGamePayload(player_name="   ")
    with pytest.raises(ValidationError):
        JoinGamePayload(player_name="x" * 25)


def test_answers_and_scores_reject_out_of_range_values():
    with pytest.raises(ValidationError):
        SubmitAnswersPayload(answers={"Animal": "x" * 65})
    with pytest.raises(ValidationError):
        ScorePayload(scores={"Animal": {"player": 3}})


def test_settings_have_safe_bounds():
    assert UpdateSettingsPayload(scoring_timeout_seconds=0).scoring_timeout_seconds == 0

    with pytest.raises(ValidationError):
        UpdateSettingsPayload(round_duration_seconds=10)
    with pytest.raises(ValidationError):
        UpdateSettingsPayload(rush_seconds=2)


def test_message_envelope_rejects_unknown_types_and_fields():
    assert BaseMessage(type=MessageType.GET_GAMES).payload == {}

    with pytest.raises(ValidationError):
        BaseMessage.model_validate({"type": "UNKNOWN", "payload": {}})
    with pytest.raises(ValidationError):
        BaseMessage.model_validate(
            {
                "type": "GET_GAMES",
                "payload": {},
                "unexpected": True,
            }
        )
