import pytest

from mealplanner.config import ConfigError, Settings, load_settings


def test_defaults_with_empty_environment() -> None:
    s = load_settings({})
    assert isinstance(s, Settings)
    assert s.host == "127.0.0.1"
    assert s.port == 8000
    assert str(s.data_dir) == "data"
    assert s.model
    assert s.allowed_hosts == ()
    assert s.owner_login is None
    assert s.dev_login is None


def test_logins_trimmed_and_lowercased() -> None:
    s = load_settings(
        {"MEALPLANNER_OWNER_LOGIN": " Owner@Example.COM ", "MEALPLANNER_DEV_LOGIN": " Dev@X.io "}
    )
    assert s.owner_login == "owner@example.com"
    assert s.dev_login == "dev@x.io"


def test_blank_logins_are_none() -> None:
    s = load_settings({"MEALPLANNER_OWNER_LOGIN": "  ", "MEALPLANNER_DEV_LOGIN": ""})
    assert s.owner_login is None
    assert s.dev_login is None


def test_each_variable_overrides_default() -> None:
    s = load_settings(
        {
            "MEALPLANNER_HOST": "::1",
            "MEALPLANNER_PORT": "9001",
            "MEALPLANNER_DATA_DIR": "/tmp/mp",
            "MEALPLANNER_MODEL": "some-model",
            "MEALPLANNER_ALLOWED_HOSTS": "a.ts.net",
        }
    )
    assert s.host == "::1"
    assert s.port == 9001
    assert str(s.data_dir) == "/tmp/mp"
    assert s.model == "some-model"
    assert s.allowed_hosts == ("a.ts.net",)


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.5", "100.101.102.103", "::", "example.com"])
def test_non_loopback_host_rejected(host: str) -> None:
    with pytest.raises(ConfigError):
        load_settings({"MEALPLANNER_HOST": host})


@pytest.mark.parametrize("host", ["127.0.0.1", "::1", "localhost"])
def test_loopback_host_accepted(host: str) -> None:
    assert load_settings({"MEALPLANNER_HOST": host}).host == host


@pytest.mark.parametrize("port", ["abc", "0", "70000", "-1", ""])
def test_bad_port_rejected(port: str) -> None:
    with pytest.raises(ConfigError):
        load_settings({"MEALPLANNER_PORT": port})


def test_allowed_hosts_parsing() -> None:
    s = load_settings({"MEALPLANNER_ALLOWED_HOSTS": " a.ts.net , ,b.ts.net,, "})
    assert s.allowed_hosts == ("a.ts.net", "b.ts.net")


def test_load_settings_reads_os_environ_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MEALPLANNER_PORT", "8123")
    assert load_settings().port == 8123


def test_bridge_defaults_and_not_required() -> None:
    s = load_settings({})
    assert s.bridge_host == "127.0.0.1"
    assert s.bridge_port is None
    assert s.bridge_token is None


def test_bridge_settings_read() -> None:
    s = load_settings(
        {
            "MEALPLANNER_BRIDGE_HOST": "::1",
            "MEALPLANNER_BRIDGE_PORT": "9001",
            "MEALPLANNER_BRIDGE_TOKEN": " tok ",
        }
    )
    assert (s.bridge_host, s.bridge_port, s.bridge_token) == ("::1", 9001, "tok")


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.5", "example.com", ""])
def test_non_loopback_bridge_host_rejected(host: str) -> None:
    with pytest.raises(ConfigError, match="MEALPLANNER_BRIDGE_HOST"):
        load_settings({"MEALPLANNER_BRIDGE_HOST": host})


@pytest.mark.parametrize("port", ["abc", "0", "65536", "-1"])
def test_bad_bridge_port_rejected(port: str) -> None:
    with pytest.raises(ConfigError, match="MEALPLANNER_BRIDGE_PORT"):
        load_settings({"MEALPLANNER_BRIDGE_PORT": port})


def test_blank_bridge_port_and_token_are_unset() -> None:
    s = load_settings({"MEALPLANNER_BRIDGE_PORT": " ", "MEALPLANNER_BRIDGE_TOKEN": " "})
    assert s.bridge_port is None
    assert s.bridge_token is None


def test_bridge_token_hidden_and_not_compared() -> None:
    s = load_settings({"MEALPLANNER_BRIDGE_TOKEN": "SECRET-TOKEN"})
    assert "SECRET-TOKEN" not in repr(s)
    assert s == load_settings({"MEALPLANNER_BRIDGE_TOKEN": "other"})
