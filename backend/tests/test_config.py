import config


def test_retrieval_constants_are_positive():
    assert config.TOP_K > 0
    assert config.CHUNK_SIZE > 0
    assert config.CHUNK_OVERLAP >= 0
    assert config.CHUNK_OVERLAP < config.CHUNK_SIZE


def test_relevance_threshold_in_range():
    # Cosine distance is in [0, 2]; threshold must sit in a sensible subset
    assert 0 < config.RELEVANCE_THRESHOLD < 2.0


def test_max_agent_turns_positive():
    assert config.MAX_AGENT_TURNS > 0


def test_gemini_base_url_https():
    assert config.GEMINI_BASE_URL.startswith("https://")
