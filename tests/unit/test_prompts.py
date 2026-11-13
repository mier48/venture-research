from bore.agents.prompt_registry import PROMPT_FILES, render_prompt

FORBIDDEN = ["7.0", "min_final", "requested_ideas", "umbral", "25%", "20%", "15%", "peso"]


def test_prompts_blind_to_threshold_weights_and_count():
    for role in PROMPT_FILES:
        text = render_prompt(role)
        for token in FORBIDDEN:
            assert token not in text, f"{role} contiene {token!r}"
        assert "# prompt_version:" in text
