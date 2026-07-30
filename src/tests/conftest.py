"""Shared fixtures for the Config Recorder override tests."""

import logging

import pytest

import ct_configrecorder_override as mod


@pytest.fixture(autouse=True)
def reset_identity_cache():
    """
    Clear the module-level caller identity cache around every test.

    The cache is deliberately global so it survives warm Lambda invocations,
    which means it would otherwise leak between tests.
    """
    mod._caller_identity = None
    yield
    mod._caller_identity = None


@pytest.fixture
def restore_log_levels():
    """Restore logger levels so logging tests cannot affect the others."""
    root = logging.getLogger()
    saved = {'root': root.level}
    for name in mod.SDK_LOGGERS:
        saved[name] = logging.getLogger(name).level

    yield

    root.setLevel(saved.pop('root'))
    for name, level in saved.items():
        logging.getLogger(name).setLevel(level)


@pytest.fixture
def recorder_env(monkeypatch):
    """
    Return a helper that sets the recorder environment variables.

    Defaults mirror a minimal EXCLUSION-strategy deployment; pass keyword
    overrides for the variables under test.
    """

    def _apply(**overrides):
        defaults = {
            'CONFIG_RECORDER_STRATEGY': 'EXCLUSION',
            'CONFIG_RECORDER_DEFAULT_RECORDING_FREQUENCY': 'CONTINUOUS',
            'CONFIG_RECORDER_OVERRIDE_DAILY_RESOURCE_LIST': '',
            'CONFIG_RECORDER_OVERRIDE_DAILY_GLOBAL_RESOURCE_LIST': '',
            'CONFIG_RECORDER_OVERRIDE_EXCLUDED_RESOURCE_LIST': '',
            'CONFIG_RECORDER_OVERRIDE_INCLUDED_RESOURCE_LIST': '',
            'CONTROL_TOWER_HOME_REGION': 'us-east-1',
        }
        defaults.update(overrides)
        for key, value in defaults.items():
            monkeypatch.setenv(key, value)

    return _apply


class FakeSts:
    """STS stub that counts calls, so tests can assert on API call volume."""

    def __init__(self):
        self.get_caller_identity_calls = 0
        self.assume_role_calls = 0

    def get_caller_identity(self):
        self.get_caller_identity_calls += 1
        return {
            'Account': '123456789012',
            'Arn': 'arn:aws:sts::123456789012:assumed-role/x/y',
        }

    def assume_role(self, RoleArn, RoleSessionName):
        self.assume_role_calls += 1
        return {
            'Credentials': {
                'AccessKeyId': 'AKIA',
                'SecretAccessKey': 'secret',
                'SessionToken': 'token',
            }
        }


@pytest.fixture
def fake_sts():
    return FakeSts()


@pytest.fixture(autouse=True)
def no_throttle_sleep(monkeypatch):
    """Skip the AssumeRole throttle delay so the suite runs fast."""
    monkeypatch.setattr(mod.time, 'sleep', lambda _: None)
