"""v1.89.4: one shared SecretStr unwrap helper (audit_checks and docs_service use it)."""
import inspect

from pydantic import SecretStr

from src.services import audit_checks, docs_service, secret_values
from src.services.secret_values import secret_present, secret_value


def test_secret_value_cases():
    assert secret_value(None) == ""
    assert secret_value(SecretStr("abc")) == "abc"
    assert secret_value("plain") == "plain"
    assert secret_value(SecretStr("")) == ""


def test_secret_present_cases():
    assert secret_present(None) is False
    assert secret_present(SecretStr("")) is False
    assert secret_present("   ") is False
    assert secret_present(SecretStr("x")) is True
    assert secret_present("x") is True


def test_modules_use_the_shared_helper_and_drop_private_copies():
    for mod in (audit_checks, docs_service):
        assert not hasattr(mod, "_present") and not hasattr(mod, "_secret")
        assert mod.secret_present is secret_values.secret_present
        assert mod.secret_value is secret_values.secret_value
        src = inspect.getsource(mod)
        assert "def _present" not in src and "def _secret" not in src
