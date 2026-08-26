from dataops_control_plane.main import create_app


def test_database_url_can_be_configured_from_environment(monkeypatch) -> None:
    """Catches deployments silently connecting to the local development database."""
    monkeypatch.setenv("DATAOPS_DATABASE_URL", "sqlite:///./configured-for-test.db")

    application = create_app()

    assert str(application.state.engine.url) == "sqlite:///./configured-for-test.db"
