"""Tests for ct_configrecorder_override Lambda function."""

import logging

import pytest
from conftest import HOME_REGION, OTHER_REGION

import ct_configrecorder_override as mod
from ct_configrecorder_override import (
    ConfigRecorderUpdateError,
    build_recorder_config,
    build_recorder_payload,
    get_account_session,
    get_caller_identity,
    parse_account_list,
    should_process_account,
    summarise_run,
)

# --- should_process_account ---------------------------------------------------

def test_exclusion_mode_excludes_listed_account():
    assert should_process_account('111111111111', 'EXCLUSION', ['111111111111'], []) is False


def test_exclusion_mode_includes_unlisted_account():
    assert should_process_account('999999999999', 'EXCLUSION', ['111111111111'], []) is True


def test_inclusion_mode_includes_listed_account():
    assert should_process_account('111111111111', 'INCLUSION', [], ['111111111111']) is True


def test_inclusion_mode_excludes_unlisted_account():
    assert should_process_account('999999999999', 'INCLUSION', [], ['111111111111']) is False


# --- parse_account_list ------------------------------------------------------

def test_parse_account_list_reads_json_array():
    assert parse_account_list('["111111111111", "222222222222"]', 'test') == [
        '111111111111', '222222222222']


def test_parse_account_list_handles_empty_array():
    assert parse_account_list('[]', 'test') == []


def test_parse_account_list_returns_empty_on_invalid_json():
    assert parse_account_list('not-json', 'test') == []


def test_parse_account_list_rejects_non_list_json():
    """A bare scalar would otherwise fail an `in` test far from the real cause."""
    assert parse_account_list('5', 'test') == []
    assert parse_account_list('{"a": 1}', 'test') == []


def test_parse_account_list_coerces_items_to_strings():
    """StackSet output is always strings, so comparisons must be string-to-string."""
    assert parse_account_list('[111111111111]', 'test') == ['111111111111']


# --- caller identity and session caching -------------------------------------

def test_caller_identity_is_resolved_once_per_container(fake_sts):
    first = get_caller_identity(fake_sts)
    second = get_caller_identity(fake_sts)

    assert first == {'account': '123456789012', 'partition': 'aws'}
    assert second == first
    assert fake_sts.get_caller_identity_calls == 1


def test_session_is_assumed_once_per_account(fake_sts):
    cache = {}

    # Same account across three regions must reuse one set of credentials.
    first = get_account_session(fake_sts, '999999999999', 'aws', cache)
    for _ in range(2):
        assert get_account_session(fake_sts, '999999999999', 'aws', cache) is first

    assert fake_sts.assume_role_calls == 1


def test_session_cache_separates_accounts(fake_sts):
    cache = {}

    a = get_account_session(fake_sts, '111111111111', 'aws', cache)
    b = get_account_session(fake_sts, '222222222222', 'aws', cache)

    assert a is not b
    assert fake_sts.assume_role_calls == 2


# --- credential safety in logs -----------------------------------------------

def test_sdk_loggers_never_reach_debug(monkeypatch, restore_log_levels):
    """
    botocore logs raw HTTP response bodies at DEBUG, and the AssumeRole response
    body contains SecretAccessKey and SessionToken. Even with LOG_LEVEL=DEBUG the
    SDK loggers must stay above DEBUG so credentials never reach CloudWatch.
    """
    monkeypatch.setenv('LOG_LEVEL', 'DEBUG')
    mod.configure_logging()

    assert logging.getLogger().isEnabledFor(logging.DEBUG) is True

    for name in ('botocore', 'botocore.parsers', 'boto3', 'urllib3'):
        assert logging.getLogger(name).isEnabledFor(logging.DEBUG) is False, (
            f'{name} would log credential material at DEBUG')


def test_module_logger_follows_log_level(monkeypatch, restore_log_levels):
    """
    The module logs through its own logger, which carries no explicit level and
    so must inherit whatever LOG_LEVEL sets on the root logger.
    """
    monkeypatch.setenv('LOG_LEVEL', 'DEBUG')
    mod.configure_logging()
    assert mod.logger.isEnabledFor(logging.DEBUG) is True

    monkeypatch.setenv('LOG_LEVEL', 'WARNING')
    mod.configure_logging()
    assert mod.logger.isEnabledFor(logging.DEBUG) is False
    assert mod.logger.isEnabledFor(logging.WARNING) is True


def test_sdk_loggers_still_report_warnings(monkeypatch, restore_log_levels):
    monkeypatch.setenv('LOG_LEVEL', 'DEBUG')
    mod.configure_logging()

    for name in mod.SDK_LOGGERS:
        logger = logging.getLogger(name)
        assert logger.isEnabledFor(logging.WARNING) is True
        # Propagation must stay on so warnings reach the Lambda root handler.
        assert logger.propagate is True


def test_assume_role_response_is_not_logged(fake_sts, caplog):
    """The credentials returned by AssumeRole must not appear in any log record."""
    with caplog.at_level(logging.DEBUG):
        get_account_session(fake_sts, '999999999999', 'aws', {})

    emitted = ' '.join(record.getMessage() for record in caplog.records)
    assert 'secret' not in emitted
    assert 'token' not in emitted
    assert 'AKIA' not in emitted
    # The account ID is still expected, so the log remains useful.
    assert '999999999999' in emitted


# --- summarise_run -----------------------------------------------------------

def test_summarise_run_returns_summary_on_full_success():
    result = summarise_run({'updated': 5, 'failed': 0, 'failures': [], 'fatal': None})
    assert result == {'statusCode': 200, 'updated': 5, 'failed': 0}


def test_summarise_run_raises_on_partial_failure():
    """
    Partial failures must raise. The invocation is asynchronous, so returning a
    500 body would be discarded and the run would look successful.
    """
    with pytest.raises(ConfigRecorderUpdateError) as exc:
        summarise_run({
            'updated': 3,
            'failed': 1,
            'failures': ['111111111111/eu-west-1: boom'],
            'fatal': None,
        })

    assert '1 of 4' in str(exc.value)
    assert '111111111111/eu-west-1' in str(exc.value)


def test_summarise_run_raises_on_fatal_error():
    with pytest.raises(ConfigRecorderUpdateError, match='StackSet missing'):
        summarise_run({
            'updated': 0,
            'failed': 0,
            'failures': [],
            'fatal': 'ValidationError: StackSet missing',
        })


def test_summarise_run_succeeds_when_nothing_matched():
    """Zero targeted accounts is a valid outcome, not a failure."""
    assert summarise_run(
        {'updated': 0, 'failed': 0, 'failures': [], 'fatal': None})['statusCode'] == 200


# --- build_recorder_config ---------------------------------------------------

def test_exclusion_strategy_drops_override_types_that_are_excluded(recorder_env):
    recorder_env(
        CONFIG_RECORDER_OVERRIDE_DAILY_RESOURCE_LIST='AWS::EC2::Volume,AWS::S3::Bucket',
        CONFIG_RECORDER_OVERRIDE_EXCLUDED_RESOURCE_LIST='AWS::EC2::Volume',
    )
    assert build_recorder_config(OTHER_REGION)['override_types'] == ['AWS::S3::Bucket']


def test_global_override_types_added_only_in_home_region(recorder_env):
    recorder_env(CONFIG_RECORDER_OVERRIDE_DAILY_GLOBAL_RESOURCE_LIST='AWS::IAM::Role')

    assert build_recorder_config(HOME_REGION)['override_types'] == ['AWS::IAM::Role']
    assert build_recorder_config(OTHER_REGION)['override_types'] == []


def test_global_override_types_are_filtered_by_the_exclusion_list(recorder_env):
    """
    A type named in both lists was previously appended after the exclusion filter
    ran, producing a cadence override for a type that is not recorded at all.
    """
    recorder_env(
        CONFIG_RECORDER_OVERRIDE_EXCLUDED_RESOURCE_LIST='AWS::IAM::Role',
        CONFIG_RECORDER_OVERRIDE_DAILY_GLOBAL_RESOURCE_LIST='AWS::IAM::Role',
    )
    assert build_recorder_config(HOME_REGION)['override_types'] == []


def test_override_types_are_not_duplicated_across_the_two_lists(recorder_env):
    recorder_env(
        CONFIG_RECORDER_OVERRIDE_DAILY_RESOURCE_LIST='AWS::IAM::Role',
        CONFIG_RECORDER_OVERRIDE_DAILY_GLOBAL_RESOURCE_LIST='AWS::IAM::Role',
    )
    assert build_recorder_config(HOME_REGION)['override_types'] == ['AWS::IAM::Role']


def test_override_frequency_is_read_from_the_environment(recorder_env):
    recorder_env(CONFIG_RECORDER_OVERRIDE_RECORDING_FREQUENCY='CONTINUOUS')
    assert build_recorder_config(HOME_REGION)['override_frequency'] == 'CONTINUOUS'


def test_inclusion_strategy_adds_override_types_to_inclusion_list(recorder_env):
    recorder_env(
        CONFIG_RECORDER_STRATEGY='INCLUSION',
        CONFIG_RECORDER_OVERRIDE_INCLUDED_RESOURCE_LIST='AWS::S3::Bucket',
        CONFIG_RECORDER_OVERRIDE_DAILY_RESOURCE_LIST='AWS::EC2::Volume',
    )
    assert build_recorder_config(OTHER_REGION)['inclusion'] == [
        'AWS::S3::Bucket', 'AWS::EC2::Volume']


# --- global IAM resource types ------------------------------------------------
#
# includeGlobalResourceTypes is ignored under EXCLUSION_BY_RESOURCE_TYPES, so the
# only way to stop the global IAM types being recorded in every governed region is
# to name them as exclusions outside the home region.

def test_global_iam_types_are_excluded_outside_the_home_region(recorder_env):
    recorder_env(CONFIG_RECORDER_OVERRIDE_EXCLUDED_RESOURCE_LIST='AWS::EC2::Volume')

    exclusion = build_recorder_config(OTHER_REGION)['exclusion']

    assert exclusion[0] == 'AWS::EC2::Volume'
    assert set(mod.GLOBAL_IAM_RESOURCE_TYPES).issubset(exclusion)


def test_global_iam_types_are_left_recorded_in_the_home_region(recorder_env):
    recorder_env(CONFIG_RECORDER_OVERRIDE_EXCLUDED_RESOURCE_LIST='AWS::EC2::Volume')

    assert build_recorder_config(HOME_REGION)['exclusion'] == ['AWS::EC2::Volume']


def test_global_iam_exclusions_are_not_duplicated(recorder_env):
    """An operator who already excluded a global type must not get it twice."""
    recorder_env(CONFIG_RECORDER_OVERRIDE_EXCLUDED_RESOURCE_LIST='AWS::IAM::Role')

    exclusion = build_recorder_config(OTHER_REGION)['exclusion']

    assert exclusion.count('AWS::IAM::Role') == 1
    assert len(exclusion) == len(mod.GLOBAL_IAM_RESOURCE_TYPES)


def test_global_iam_types_are_not_excluded_when_nothing_else_is(recorder_env):
    """
    With an empty exclusion list the payload uses allSupported instead, where
    includeGlobalResourceTypes does work. Injecting exclusions here would switch
    strategies behind the operator's back.
    """
    recorder_env()

    assert build_recorder_config(OTHER_REGION)['exclusion'] == []


def test_global_iam_types_are_not_excluded_under_inclusion_strategy(recorder_env):
    recorder_env(
        CONFIG_RECORDER_STRATEGY='INCLUSION',
        CONFIG_RECORDER_OVERRIDE_INCLUDED_RESOURCE_LIST='AWS::S3::Bucket',
        CONFIG_RECORDER_OVERRIDE_EXCLUDED_RESOURCE_LIST='AWS::EC2::Volume',
    )

    assert build_recorder_config(OTHER_REGION)['exclusion'] == ['AWS::EC2::Volume']


# --- build_recorder_payload --------------------------------------------------

@pytest.mark.parametrize(
    ('region', 'expect_global'),
    [(HOME_REGION, True), (OTHER_REGION, False)],
)
def test_delete_event_resets_to_control_tower_defaults(recorder_env, region, expect_global):
    recorder_env()
    payload = build_recorder_payload(
        'rec', 'role-arn', build_recorder_config(region), 'Delete')

    assert payload['recordingGroup']['allSupported'] is True
    assert payload['recordingGroup']['includeGlobalResourceTypes'] is expect_global
    assert 'recordingMode' not in payload


@pytest.mark.parametrize(
    ('region', 'expect_global'),
    [(HOME_REGION, True), (OTHER_REGION, False)],
)
def test_empty_exclusion_list_records_everything(recorder_env, region, expect_global):
    """
    An empty exclusion list must not switch global IAM recording on outside the
    home region. Doing so silently multiplies Config cost by the number of
    governed regions while adding no coverage, because IAM is global.
    """
    recorder_env()
    payload = build_recorder_payload(
        'rec', 'role-arn', build_recorder_config(region), 'apply')

    group = payload['recordingGroup']
    assert group['allSupported'] is True
    assert group['includeGlobalResourceTypes'] is expect_global
    assert 'exclusionByResourceTypes' not in group


def test_exclusion_payload_uses_exclusion_strategy(recorder_env):
    recorder_env(CONFIG_RECORDER_OVERRIDE_EXCLUDED_RESOURCE_LIST='AWS::EC2::Volume')
    payload = build_recorder_payload(
        'rec', 'role-arn', build_recorder_config(HOME_REGION), 'apply')

    group = payload['recordingGroup']
    assert group['allSupported'] is False
    assert group['recordingStrategy']['useOnly'] == 'EXCLUSION_BY_RESOURCE_TYPES'
    assert group['exclusionByResourceTypes']['resourceTypes'] == ['AWS::EC2::Volume']


def test_exclusion_payload_excludes_global_types_outside_home_region(recorder_env):
    recorder_env(CONFIG_RECORDER_OVERRIDE_EXCLUDED_RESOURCE_LIST='AWS::EC2::Volume')
    payload = build_recorder_payload(
        'rec', 'role-arn', build_recorder_config(OTHER_REGION), 'apply')

    excluded = payload['recordingGroup']['exclusionByResourceTypes']['resourceTypes']
    assert set(mod.GLOBAL_IAM_RESOURCE_TYPES).issubset(excluded)


def test_inclusion_payload_uses_inclusion_strategy(recorder_env):
    recorder_env(
        CONFIG_RECORDER_STRATEGY='INCLUSION',
        CONFIG_RECORDER_OVERRIDE_INCLUDED_RESOURCE_LIST='AWS::S3::Bucket',
    )
    payload = build_recorder_payload(
        'rec', 'role-arn', build_recorder_config(HOME_REGION), 'apply')

    group = payload['recordingGroup']
    assert group['resourceTypes'] == ['AWS::S3::Bucket']
    assert group['recordingStrategy']['useOnly'] == 'INCLUSION_BY_RESOURCE_TYPES'


def test_inclusion_strategy_with_no_resource_types_raises(recorder_env):
    """
    The alternative is a recorder with allSupported False and no resourceTypes,
    which records nothing at all. Failing is better than silently switching Config
    off across an organization.
    """
    recorder_env(CONFIG_RECORDER_STRATEGY='INCLUSION')

    with pytest.raises(ValueError, match='records nothing'):
        build_recorder_payload(
            'rec', 'role-arn', build_recorder_config(HOME_REGION), 'apply')


# --- recording mode ----------------------------------------------------------

def test_override_is_emitted_when_override_types_present(recorder_env):
    recorder_env(
        CONFIG_RECORDER_OVERRIDE_EXCLUDED_RESOURCE_LIST='AWS::EC2::Volume',
        CONFIG_RECORDER_OVERRIDE_DAILY_RESOURCE_LIST='AWS::S3::Bucket',
    )
    payload = build_recorder_payload(
        'rec', 'role-arn', build_recorder_config(HOME_REGION), 'apply')

    assert payload['recordingMode']['recordingFrequency'] == 'CONTINUOUS'
    override = payload['recordingMode']['recordingModeOverrides'][0]
    assert override['recordingFrequency'] == 'DAILY'
    assert override['resourceTypes'] == ['AWS::S3::Bucket']


def test_default_frequency_is_applied_without_any_override_types(recorder_env):
    """
    DAILY with both override lists empty is the natural way to say "record
    everything once every 24 hours". Emitting no recordingMode there left every
    recorder on continuous recording with no error and no log line.
    """
    recorder_env(CONFIG_RECORDER_DEFAULT_RECORDING_FREQUENCY='DAILY')
    payload = build_recorder_payload(
        'rec', 'role-arn', build_recorder_config(HOME_REGION), 'apply')

    assert payload['recordingMode']['recordingFrequency'] == 'DAILY'
    assert 'recordingModeOverrides' not in payload['recordingMode']


def test_override_frequency_can_be_continuous_against_a_daily_default(recorder_env):
    """
    Firewall Manager needs continuous recording for the types its policies cover,
    which requires exempting specific types from an otherwise daily default.
    """
    recorder_env(
        CONFIG_RECORDER_DEFAULT_RECORDING_FREQUENCY='DAILY',
        CONFIG_RECORDER_OVERRIDE_RECORDING_FREQUENCY='CONTINUOUS',
        CONFIG_RECORDER_OVERRIDE_DAILY_RESOURCE_LIST='AWS::EC2::VPC',
    )
    payload = build_recorder_payload(
        'rec', 'role-arn', build_recorder_config(HOME_REGION), 'apply')

    assert payload['recordingMode']['recordingFrequency'] == 'DAILY'
    override = payload['recordingMode']['recordingModeOverrides'][0]
    assert override['recordingFrequency'] == 'CONTINUOUS'
    assert override['resourceTypes'] == ['AWS::EC2::VPC']
    assert override['description'] == 'CONTINUOUS_OVERRIDE'


def test_no_recording_mode_when_nothing_is_configured(recorder_env):
    """A continuous default with no overrides is the AWS default, so send nothing."""
    recorder_env(CONFIG_RECORDER_OVERRIDE_EXCLUDED_RESOURCE_LIST='AWS::EC2::Volume')
    payload = build_recorder_payload(
        'rec', 'role-arn', build_recorder_config(HOME_REGION), 'apply')

    assert 'recordingMode' not in payload
