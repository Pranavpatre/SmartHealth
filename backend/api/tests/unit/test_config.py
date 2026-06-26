"""Config tests — ensure Settings loads correctly from env."""
import os


def test_default_environment_is_development():
    os.environ["ENVIRONMENT"] = "development"
    # Re-import to get fresh settings
    import importlib
    import config as cfg_module
    importlib.reload(cfg_module)
    settings = cfg_module.Settings()
    assert settings.environment == "development"


def test_allowed_origins_comma_string_parsed():
    os.environ["ALLOWED_ORIGINS"] = "http://a.com,http://b.com"
    import importlib
    import config as cfg_module
    importlib.reload(cfg_module)
    settings = cfg_module.Settings()
    assert len(settings.cors_origins) == 2
    assert "http://a.com" in settings.cors_origins


def test_celery_broker_defaults_to_redis_url():
    settings_obj = __import__("config").Settings()
    assert settings_obj.celery_broker == settings_obj.redis_url
