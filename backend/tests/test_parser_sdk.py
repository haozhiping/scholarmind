from common.config import settings


def test_mineru_kie_config_present():
    assert settings.MINERU_KIE_BASE_URL == "https://mineru.net/api/kie"
    assert hasattr(settings, "MINERU_PIPELINE_ID")
    assert hasattr(settings, "MINERU_API_KEY")
    assert settings.MINERU_POLL_INTERVAL == 5
    assert settings.MINERU_TIMEOUT == 300
