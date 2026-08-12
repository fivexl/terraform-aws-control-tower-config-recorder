"""
AWS Config Recorder Override for Control Tower environments.

This Lambda function customizes AWS Config Recorder settings across child accounts
managed by AWS Control Tower. It is triggered by:

1. EventBridge rules on Control Tower lifecycle events. Any operation that can
   redeploy AWSControlTowerBP-BASELINE-CONFIG has to be covered, because that
   StackSet is what overwrites this customization: the account events
   (CreateManagedAccount, UpdateManagedAccount), the landing zone events
   (UpdateLandingZone), OU registration (RegisterOrganizationalUnit) and the
   baseline events (EnableBaseline, ResetEnabledBaseline, UpdateEnabledBaseline).
2. A periodic reconciliation schedule, so that an event AWS adds in future, or one
   lost because CloudTrail logging was off, cannot leave the recorder reverted
   indefinitely. A missed event produces no error, so nothing else would notice.
3. Direct invocation from Terraform (via local-exec on apply)
4. Manual invocation for ad-hoc operations (e.g., resetting accounts with action=Delete)

The function iterates accounts from the AWSControlTowerBP-BASELINE-CONFIG StackSet,
assumes the AWSControlTowerExecution role in each target account, and updates the
Config Recorder according to the configured strategy and resource type lists.

API efficiency notes:
    - Caller identity (account ID + partition) is resolved once per container.
    - Assumed-role sessions are cached per account for the lifetime of one
      invocation, since STS credentials are region-agnostic. An account spanning
      N regions therefore costs one AssumeRole call, not N.
    - Environment configuration is parsed once per region, not per account.
    - The throttling delay is applied only after an actual AssumeRole call.

Failure handling:
    Per-account failures are collected rather than raised immediately, so one
    unreachable account does not stop the rest. Once the walk finishes, the
    function raises ConfigRecorderUpdateError if anything failed. That matters
    because the function is invoked asynchronously: nothing reads the return
    value, so raising is the only way a failure reaches the Lambda Errors metric
    and the CloudWatch alarm built on it.

Environment Variables:
    ACCOUNT_SELECTION_MODE: EXCLUSION or INCLUSION
    EXCLUDED_ACCOUNTS: JSON array of account IDs to skip (EXCLUSION mode)
    INCLUDED_ACCOUNTS: JSON array of account IDs to process (INCLUSION mode)
    CONFIG_RECORDER_STRATEGY: EXCLUSION or INCLUSION for resource types
    CONFIG_RECORDER_OVERRIDE_EXCLUDED_RESOURCE_LIST: Comma-separated resource types to exclude
    CONFIG_RECORDER_OVERRIDE_INCLUDED_RESOURCE_LIST: Comma-separated resource types to include
    CONFIG_RECORDER_OVERRIDE_DAILY_RESOURCE_LIST: Comma-separated resource types the
        override frequency applies to
    CONFIG_RECORDER_OVERRIDE_DAILY_GLOBAL_RESOURCE_LIST: Global resource types the override
        frequency applies to, in the home region only
    CONFIG_RECORDER_DEFAULT_RECORDING_FREQUENCY: CONTINUOUS or DAILY
    CONFIG_RECORDER_OVERRIDE_RECORDING_FREQUENCY: CONTINUOUS or DAILY, applied to the two
        override resource type lists above
    CONTROL_TOWER_HOME_REGION: AWS region where Control Tower is deployed
    LOG_LEVEL: Logging level (default: INFO)
"""

import json
import logging
import os
import time

import boto3
import botocore.exceptions


class ConfigRecorderUpdateError(Exception):
    """
    Raised when one or more accounts could not be updated.

    This exists so partial failures are visible. The function is invoked
    asynchronously, so nothing inspects its return value: raising is what
    increments the Lambda Errors metric, trips the CloudWatch alarm, and lets
    Lambda retry the run.
    """

DEFAULT_RECORDER_NAME = 'aws-controltower-BaselineConfigRecorder'
EXECUTION_ROLE_NAME = 'AWSControlTowerExecution'
STACK_SET_NAME = 'AWSControlTowerBP-BASELINE-CONFIG'

# The bundle of global resource types that includeGlobalResourceTypes covers.
# They describe the same global resources in every region, so recording them
# outside the Control Tower home region duplicates data at extra cost. Under the
# EXCLUSION_BY_RESOURCE_TYPES strategy the includeGlobalResourceTypes flag is
# ignored by AWS, so the only way to stop that duplication is to name these types
# as exclusions. See build_recorder_config.
GLOBAL_IAM_RESOURCE_TYPES = (
    'AWS::IAM::User',
    'AWS::IAM::Group',
    'AWS::IAM::Role',
    'AWS::IAM::Policy',
)

# Seconds to wait after an AssumeRole call to stay clear of STS request-rate limits.
ASSUME_ROLE_THROTTLE_DELAY = 1

# A lifecycle event marks the completion of a Control Tower operation and reports
# whether it succeeded or failed. Only a success means a baseline was applied and
# therefore needs overriding again.
LIFECYCLE_SUCCESS_STATE = 'SUCCEEDED'

# The AWS SDK logs raw HTTP request and response bodies at DEBUG level
# (botocore.parsers logs 'Response body'). The AssumeRole response body contains
# SecretAccessKey and SessionToken, so these loggers must never be allowed to
# reach DEBUG, regardless of the LOG_LEVEL requested for our own messages.
SDK_LOGGERS = ('boto3', 'botocore', 'urllib3', 's3transfer')
SDK_LOG_LEVEL = logging.WARNING

# This module logs through its own logger rather than the root logger, so its
# records are attributable and its level can be tuned independently of the SDK
# loggers. It has no explicit level, so it inherits whatever configure_logging
# applies to the root logger.
logger = logging.getLogger(__name__)

# Cached across warm invocations: the execution identity never changes for a
# given function, so this is safe to reuse. Assumed-role credentials are NOT
# cached here, since they expire and must not leak across invocations.
_caller_identity = None


def configure_logging():
    """
    Apply LOG_LEVEL to this function's own log output while keeping the AWS SDK
    loggers pinned above DEBUG.

    Setting the root logger to DEBUG would otherwise propagate to botocore, which
    logs raw HTTP response bodies. The AssumeRole response body carries the
    SecretAccessKey and SessionToken, so allowing that would write temporary
    credentials into CloudWatch Logs.
    """
    logging.getLogger().setLevel(os.getenv('LOG_LEVEL', 'INFO').upper())

    # An explicit level on these loggers takes precedence over the inherited
    # root level, so DEBUG records are never created. Propagation is left intact
    # so genuine SDK warnings and errors still reach CloudWatch.
    for name in SDK_LOGGERS:
        logging.getLogger(name).setLevel(SDK_LOG_LEVEL)


def _split_env_list(name):
    """Read a comma-separated environment variable into a list of strings."""
    raw = os.getenv(name, '')
    return raw.split(',') if raw else []


def get_caller_identity(sts_client):
    """
    Return the executing account ID and ARN partition, calling STS at most once
    per container.

    Args:
        sts_client: A boto3 STS client

    Returns:
        dict: {'account': str, 'partition': str}
    """
    global _caller_identity
    if _caller_identity is None:
        identity = sts_client.get_caller_identity()
        _caller_identity = {
            'account': identity['Account'],
            'partition': identity['Arn'].split(':')[1],
        }
        logger.info(f'Resolved caller identity: {_caller_identity}')
    return _caller_identity


def build_recorder_config(aws_region):
    """
    Parse Config Recorder settings from the environment once per invocation.

    Resource-type lists are normalised here so that per-account processing does
    no further parsing. Two of the decisions depend on whether the target region
    is the Control Tower home region, so the result is resolved per region rather
    than once globally.

    Args:
        aws_region (str): The region the settings will be applied to

    Returns:
        dict: Normalised recorder configuration
    """
    strategy = os.getenv('CONFIG_RECORDER_STRATEGY', 'EXCLUSION')
    frequency = os.getenv('CONFIG_RECORDER_DEFAULT_RECORDING_FREQUENCY', 'CONTINUOUS')
    override_frequency = os.getenv('CONFIG_RECORDER_OVERRIDE_RECORDING_FREQUENCY', 'DAILY')

    # The two environment variables still carry DAILY in their names for
    # compatibility with earlier versions. They are simply the resource types
    # that override_frequency applies to, which is no longer always daily.
    override_types = _split_env_list('CONFIG_RECORDER_OVERRIDE_DAILY_RESOURCE_LIST')
    override_global = _split_env_list('CONFIG_RECORDER_OVERRIDE_DAILY_GLOBAL_RESOURCE_LIST')
    exclusion = _split_env_list('CONFIG_RECORDER_OVERRIDE_EXCLUDED_RESOURCE_LIST')
    inclusion = _split_env_list('CONFIG_RECORDER_OVERRIDE_INCLUDED_RESOURCE_LIST')

    is_home_region = os.getenv('CONTROL_TOWER_HOME_REGION') == aws_region

    if strategy == 'EXCLUSION' and exclusion and not is_home_region:
        # AWS ignores includeGlobalResourceTypes under EXCLUSION_BY_RESOURCE_TYPES
        # and records the global IAM types anyway, so sending the flag as False is
        # not enough to keep them out. Naming them as exclusions is, and outside
        # the home region they are duplicate recordings of the same global
        # resources. IAM churns on every deploy, so leaving them on multiplies
        # Config cost by the number of governed regions for no added coverage.
        exclusion = exclusion + [t for t in GLOBAL_IAM_RESOURCE_TYPES if t not in exclusion]

    if is_home_region:
        override_types = override_types + [
            t for t in override_global if t not in override_types]

    if strategy == 'EXCLUSION':
        # Overriding the cadence of a type that is not being recorded at all is
        # contradictory. Filtered after the global list is appended, so types
        # named in both the exclusion list and the global override list are
        # dropped rather than slipping past the filter.
        override_types = [x for x in override_types if x not in exclusion]
    else:
        # Anything given a cadence override must also be in the inclusion list,
        # otherwise the override refers to a type that is not being recorded.
        inclusion = inclusion + [x for x in override_types if x not in inclusion]

    return {
        'strategy': strategy,
        'frequency': frequency,
        'override_frequency': override_frequency,
        'override_types': override_types,
        'exclusion': exclusion,
        'inclusion': inclusion,
        'is_home_region': is_home_region,
    }


def build_recorder_payload(recorder_name, role_arn_config, config, event_type):
    """
    Build the ConfigurationRecorder payload for put_configuration_recorder.

    Args:
        recorder_name (str): Name of the Config Recorder to write
        role_arn_config (str): ARN of AWSServiceRoleForConfig in the target account
        config (dict): Output of build_recorder_config
        event_type (str): 'Delete' resets to Control Tower defaults

    Returns:
        dict: ConfigurationRecorder payload

    Raises:
        ValueError: If the INCLUSION strategy is configured with no resource types
    """
    if event_type == 'Delete':
        return {
            'name': recorder_name,
            'roleARN': role_arn_config,
            'recordingGroup': {
                'allSupported': True,
                'includeGlobalResourceTypes': config['is_home_region'],
            },
        }

    payload = {'name': recorder_name, 'roleARN': role_arn_config}

    if config['strategy'] == 'EXCLUSION':
        if config['exclusion']:
            payload['recordingGroup'] = {
                'allSupported': False,
                # Sent for completeness only. AWS ignores this field under
                # EXCLUSION_BY_RESOURCE_TYPES, which is why build_recorder_config
                # adds the global IAM types to the exclusion list itself outside
                # the home region.
                'includeGlobalResourceTypes': False,
                'exclusionByResourceTypes': {'resourceTypes': config['exclusion']},
                'recordingStrategy': {'useOnly': 'EXCLUSION_BY_RESOURCE_TYPES'},
            }
        else:
            # Nothing to exclude means record every supported type. The global IAM
            # types are recorded in the home region only, matching the Control
            # Tower baseline: they describe the same global resources in every
            # region, so recording them more than once adds cost without coverage.
            payload['recordingGroup'] = {
                'allSupported': True,
                'includeGlobalResourceTypes': config['is_home_region'],
            }
    elif config['inclusion']:
        payload['recordingGroup'] = {
            'allSupported': False,
            'includeGlobalResourceTypes': False,
            'resourceTypes': config['inclusion'],
            'recordingStrategy': {'useOnly': 'INCLUSION_BY_RESOURCE_TYPES'},
        }
    else:
        # allSupported False with no resourceTypes is a recorder that records
        # nothing. Silently switching Config off across an organization is worse
        # than failing, so refuse. Terraform also rejects this at plan time; this
        # guard covers a function invoked with hand-edited environment variables.
        raise ValueError(
            'INCLUSION strategy requires at least one resource type. Set '
            'config_recorder_included_resource_types, or switch to the EXCLUSION '
            'strategy. Refusing to write a Config Recorder that records nothing.')

    # Emitted whenever a cadence is configured, not only when there are override
    # types. A DAILY default with no overrides is the natural way to say "record
    # everything once every 24 hours", and omitting the block there left every
    # recorder on continuous recording with no error and no log line.
    if config['override_types'] or config['frequency'] != 'CONTINUOUS':
        payload['recordingMode'] = {'recordingFrequency': config['frequency']}

        if config['override_types']:
            override_frequency = config['override_frequency']
            # AWS caps recordingModeOverrides at a single object, so one override
            # frequency plus one resource type list is the whole of what the API
            # can express here.
            payload['recordingMode']['recordingModeOverrides'] = [
                {
                    'description': f'{override_frequency}_OVERRIDE',
                    'resourceTypes': config['override_types'],
                    'recordingFrequency': override_frequency,
                }
            ]

    return payload


def should_process_account(account_id, selection_mode, excluded_accounts, included_accounts):
    """
    Determine if an account should be processed based on selection mode.

    Args:
        account_id (str): AWS account ID to check
        selection_mode (str): Either 'EXCLUSION' or 'INCLUSION'
        excluded_accounts (list): List of account ID strings to exclude (used in EXCLUSION mode)
        included_accounts (list): List of account ID strings to include (used in INCLUSION mode)

    Returns:
        bool: True if account should be processed, False otherwise
    """
    if selection_mode == 'INCLUSION':
        should_process = account_id in included_accounts
        reason = 'in inclusion list' if should_process else 'not in inclusion list'
    else:  # EXCLUSION mode (default)
        should_process = account_id not in excluded_accounts
        reason = 'not in exclusion list' if should_process else 'in exclusion list'

    verdict = 'included' if should_process else 'excluded'
    logger.info(f'Account {account_id} {verdict} ({reason})')
    return should_process


def get_account_session(sts_client, account_id, partition, session_cache):
    """
    Return a boto3 Session for the target account, assuming the role only once.

    STS credentials are not region-scoped, so a single session is reused for
    every region of a given account. The cache is invocation-scoped so that
    credentials never outlive the request that created them.

    Args:
        sts_client: A boto3 STS client in the executing account
        account_id (str): Target AWS account ID
        partition (str): ARN partition (aws, aws-us-gov, aws-cn)
        session_cache (dict): Invocation-scoped cache keyed by account ID

    Returns:
        boto3.Session: Session with credentials for the target account
    """
    if account_id in session_cache:
        return session_cache[account_id]

    role_arn = f'arn:{partition}:iam::{account_id}:role/{EXECUTION_ROLE_NAME}'
    credentials = sts_client.assume_role(
        RoleArn=role_arn,
        RoleSessionName=f'{account_id}-{EXECUTION_ROLE_NAME}',
    )['Credentials']

    session = boto3.Session(
        aws_access_key_id=credentials['AccessKeyId'],
        aws_secret_access_key=credentials['SecretAccessKey'],
        aws_session_token=credentials['SessionToken'],
    )
    session_cache[account_id] = session
    logger.info(f'Assumed {EXECUTION_ROLE_NAME} in account {account_id}')

    # Only throttle when an STS call was actually made.
    time.sleep(ASSUME_ROLE_THROTTLE_DELAY)
    return session


def update_config_recorder(session, account_id, aws_region, partition, config, event_type):
    """
    Update the Config Recorder in the target account and region.

    On 'Delete' events, resets the Config Recorder to Control Tower defaults
    (allSupported=True). Otherwise applies the configured recording strategy
    with optional per-resource-type recording frequency overrides.

    Args:
        session (boto3.Session): Session with credentials for the target account
        account_id (str): Target AWS account ID
        aws_region (str): Target AWS region
        partition (str): ARN partition (aws, aws-us-gov, aws-cn)
        config (dict): Region-resolved settings from build_recorder_config
        event_type (str): 'Delete' resets to defaults, anything else applies config
    """
    try:
        configservice = session.client('config', region_name=aws_region)

        existing = configservice.describe_configuration_recorders()
        recorders = existing.get('ConfigurationRecorders') or []
        recorder_name = recorders[0]['name'] if recorders else DEFAULT_RECORDER_NAME
        logger.info(f'Using recorder name {recorder_name} in {account_id}/{aws_region}')

        role_arn_config = (
            f'arn:{partition}:iam::{account_id}:role/aws-service-role/'
            'config.amazonaws.com/AWSServiceRoleForConfig'
        )

        payload = build_recorder_payload(recorder_name, role_arn_config, config, event_type)

        logger.info(f'Applying Config Recorder payload: {json.dumps(payload, default=str)}')
        configservice.put_configuration_recorder(ConfigurationRecorder=payload)

        if event_type == 'Delete':
            logger.warning(f'Config Recorder reset to defaults in {account_id}/{aws_region}')
        else:
            logger.info(f'Config Recorder updated in {account_id}/{aws_region}')

        # Read back only when debugging; this costs an API call per region.
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(
                f'Post-change recorder: {configservice.describe_configuration_recorders()}')

    except botocore.exceptions.ClientError:
        logger.exception(
            f'Unable to update Config Recorder for account {account_id} in region {aws_region}')
        # Re-raise unchanged so process_accounts can record which pair failed.
        raise


def process_accounts(selection_mode, excluded_accounts, included_accounts, account, event_type):
    """
    Retrieve Control Tower managed accounts and update Config Recorder in each.

    Lists stack instances from the AWSControlTowerBP-BASELINE-CONFIG StackSet,
    filters them based on selection mode, and updates each account-region pair.

    The executing account is skipped, and assumed-role sessions are cached per
    account so that multi-region accounts cost a single AssumeRole call.

    Failures on individual accounts are logged and counted but do not stop
    processing of the remaining accounts. The counts are returned so the caller
    can decide whether the run as a whole failed.

    Args:
        selection_mode (str): 'EXCLUSION' or 'INCLUSION'
        excluded_accounts (list): Account IDs to skip (EXCLUSION mode)
        included_accounts (list): Account IDs to process (INCLUSION mode)
        account (str): Specific account ID, or empty string for all accounts
        event_type (str): Passed through to update_config_recorder

    Returns:
        dict: {'updated': int, 'failed': int, 'failures': list, 'fatal': str|None}
    """
    result = {'updated': 0, 'failed': 0, 'failures': [], 'fatal': None}

    try:
        sts_client = boto3.client('sts')
        identity = get_caller_identity(sts_client)
        current_account = identity['account']
        partition = identity['partition']

        # Invocation-scoped so assumed credentials never outlive this request.
        session_cache = {}
        # Accounts already rejected by selection rules or a failed AssumeRole.
        skipped_accounts = set()
        # Settings depend only on the region, so resolve each region once.
        config_cache = {}

        cloudformation = boto3.client('cloudformation')
        paginator = cloudformation.get_paginator('list_stack_instances')

        paginate_args = {'StackSetName': STACK_SET_NAME}
        if account:
            paginate_args['StackInstanceAccount'] = account

        for page in paginator.paginate(**paginate_args):
            for item in page.get('Summaries', []):
                account_id = item['Account']
                region = item['Region']

                if account_id in skipped_accounts:
                    continue

                if account_id == current_account:
                    logger.info(f'Skipping current account {account_id}')
                    skipped_accounts.add(account_id)
                    continue

                if not should_process_account(
                        account_id, selection_mode, excluded_accounts, included_accounts):
                    # Selection is per account, so record it and skip its other regions.
                    skipped_accounts.add(account_id)
                    continue

                try:
                    session = get_account_session(
                        sts_client, account_id, partition, session_cache)
                except botocore.exceptions.ClientError:
                    # Without credentials no region of this account is reachable.
                    logger.exception(f'Failed to assume role in account {account_id}')
                    skipped_accounts.add(account_id)
                    result['failed'] += 1
                    result['failures'].append(f'{account_id}: AssumeRole failed')
                    continue

                if region not in config_cache:
                    config_cache[region] = build_recorder_config(region)

                try:
                    update_config_recorder(
                        session, account_id, region, partition, config_cache[region], event_type)
                    result['updated'] += 1
                # Deliberately broad: one bad account-region pair must not stop
                # the walk. The failure is recorded and reported at the end.
                except Exception as e:  # noqa: BLE001
                    logger.exception(f'Failed to update account {account_id} in {region}')
                    result['failed'] += 1
                    result['failures'].append(f'{account_id}/{region}: {e}')
                    # Continue processing other accounts and regions.

    except Exception as e:
        # A failure out here (bad StackSet name, missing permissions) means no
        # account was processed at all, so record it separately from per-account
        # failures rather than reporting a clean run.
        exception_type = e.__class__.__name__
        exception_message = str(e)
        logger.exception(f'{exception_type}: {exception_message}')
        result['fatal'] = f'{exception_type}: {exception_message}'

    return result


def parse_account_list(raw_value, label):
    """
    Parse a JSON array of account IDs from an environment variable.

    Terraform passes these with jsonencode, so the value is standard JSON such as
    '["111111111111"]'. Anything that is not a JSON array is rejected rather than
    returned, because a bare scalar would later fail an `in` test with TypeError
    far from the real cause.

    Args:
        raw_value (str): JSON array, for example '["111111111111"]'
        label (str): Name used in error logging

    Returns:
        list: Account IDs as strings, or an empty list if the value is unusable
    """
    try:
        parsed = json.loads(raw_value)
    except (TypeError, ValueError):
        logger.exception(f'Failed to parse {label} as JSON')
        return []

    if not isinstance(parsed, list):
        logger.error(
            f'Expected {label} to be a JSON array, got {type(parsed).__name__}. Ignoring it.')
        return []

    # Account IDs are compared against StackSet output, which is always strings.
    return [str(item) for item in parsed]


def _lifecycle_status_payloads(event):
    """
    Yield the status objects nested under serviceEventDetails.

    Every Control Tower lifecycle event carries exactly one such object, named
    after the originating operation (createManagedAccountStatus,
    enableBaselineStatus, and so on).
    """
    details = event.get('detail', {}).get('serviceEventDetails') or {}
    if not isinstance(details, dict):
        return

    for payload in details.values():
        if isinstance(payload, dict):
            yield payload


def lifecycle_event_state(event):
    """
    Return the completion state of a Control Tower lifecycle event.

    The field differs by event family: the account, OU and landing zone events
    carry 'state', while the baseline events nest it at
    enabledBaselineDetails.statusSummary.status.

    Returns None when no state can be read, which the caller treats as "process
    it anyway". Failing open is deliberate. Dropping an event whose shape we did
    not recognise would leave the Config Recorder reverted with nothing on the
    Errors metric to show it, which is the failure mode this whole function
    exists to prevent.

    Args:
        event (dict): The EventBridge event

    Returns:
        str|None: 'SUCCEEDED', 'FAILED', or None if not present
    """
    for payload in _lifecycle_status_payloads(event):
        if 'state' in payload:
            return payload['state']

        summary = (payload.get('enabledBaselineDetails') or {}).get('statusSummary') or {}
        if 'status' in summary:
            return summary['status']

    return None


def _account_from_target_identifier(target_identifier):
    """
    Pull the account ID out of a baseline event targetIdentifier ARN.

    The target is either an account, for example
    arn:aws:organizations::111122223333:account/o-abc123/444455556666, or an OU.
    An OU target means the baseline was applied at OU level, so there is no single
    account to narrow to and the caller walks the whole organization instead.

    Args:
        target_identifier (str): Organizations ARN of the baseline target

    Returns:
        str: Account ID, or '' when the target is not a single account
    """
    resource = target_identifier.rsplit(':', 1)[-1]
    kind, _, path = resource.partition('/')

    if kind == 'account' and path:
        return path.rsplit('/', 1)[-1]

    return ''


def lifecycle_event_account(event):
    """
    Return the account a lifecycle event targets, or '' when it is organization-wide.

    Three shapes are in play:

        CreateManagedAccount, UpdateManagedAccount
            account.accountId
        EnableBaseline, ResetEnabledBaseline, UpdateEnabledBaseline
            enabledBaselineDetails.targetIdentifier, an Organizations ARN
        RegisterOrganizationalUnit, UpdateLandingZone, SetupLandingZone
            no single account: an organizationalUnit, or an 'accounts' list

    This reads whichever shape is present rather than switching on the event
    name, so an event type AWS adds later still resolves without a code change.
    That matters because the list has grown before: the baseline events are recent
    additions, and missing them is what let a baseline redeploy silently revert
    this customization.

    '' is passed straight through to process_accounts, which then walks every
    account in the StackSet. That is the right fallback for OU registration and
    landing zone updates, which affect many accounts at once.

    Args:
        event (dict): The EventBridge event

    Returns:
        str: The affected account ID, or '' for organization-wide events
    """
    for payload in _lifecycle_status_payloads(event):
        # 'account' is a single object. Note that the landing zone events instead
        # carry 'accounts', a list, which correctly does not match here.
        account = (payload.get('account') or {}).get('accountId')
        if account:
            return str(account)

        target = (payload.get('enabledBaselineDetails') or {}).get('targetIdentifier')
        if target:
            return _account_from_target_identifier(target)

    return ''


def summarise_run(result):
    """
    Turn per-account results into a return value, raising if anything failed.

    Args:
        result (dict): Output of process_accounts

    Returns:
        dict: Summary payload when every targeted account was updated

    Raises:
        ConfigRecorderUpdateError: If the run aborted, or any account failed
    """
    updated = result['updated']
    failed = result['failed']

    if result['fatal']:
        logger.error(f'Run aborted before completion: {result["fatal"]}')
        raise ConfigRecorderUpdateError(result['fatal'])

    if failed:
        detail = '; '.join(result['failures'])
        logger.error(f'Updated {updated} account-region pairs, {failed} failed: {detail}')
        raise ConfigRecorderUpdateError(
            f'{failed} of {updated + failed} account-region pairs failed: {detail}')

    logger.info(f'Execution successful. Updated {updated} account-region pairs.')
    return {'statusCode': 200, 'updated': updated, 'failed': 0}


def lambda_handler(event, context):

    configure_logging()

    try:
        logger.info(f'Event Data: {event}')

        selection_mode = os.getenv('ACCOUNT_SELECTION_MODE', 'EXCLUSION')
        excluded_accounts = parse_account_list(
            os.getenv('EXCLUDED_ACCOUNTS', '[]'), 'excluded accounts')
        included_accounts = parse_account_list(
            os.getenv('INCLUDED_ACCOUNTS', '[]'), 'included accounts')

        logger.info(f'Account Selection Mode: {selection_mode}')
        logger.info(f'Excluded Accounts: {excluded_accounts}')
        logger.info(f'Included Accounts: {included_accounts}')

        event_source = event.get('source', '')
        event_name = event.get('detail', {}).get('eventName', '')
        if event_source:
            logger.info(f'Control Tower event {event_source}/{event_name}')

        if event_source == 'aws.controltower':
            state = lifecycle_event_state(event)

            if state is not None and state.upper() != LIFECYCLE_SUCCESS_STATE:
                # A lifecycle event records the completion of an operation, so a
                # failed one means the baseline was not applied and there is
                # nothing to override. Acting on it would assume a role in an
                # account that may not be provisioned, which would fail the run
                # and trip the error alarm for no reason.
                logger.warning(
                    f'Ignoring {event_name} lifecycle event in state {state}')
                return {'statusCode': 200, 'updated': 0, 'failed': 0, 'skipped': event_name}

            if state is None:
                # Processed rather than dropped: see lifecycle_event_state.
                logger.warning(f'No lifecycle state found on {event_name}, processing anyway')

            account = lifecycle_event_account(event)
            scope = f'SINGLE account {account}' if account else 'ALL accounts'
            logger.info(f'Overriding config recorder for {scope} due to {event_name}')
            result = process_accounts(
                selection_mode, excluded_accounts, included_accounts, account, 'controltower')

        else:
            # Direct invocation: Terraform local-exec, the reconciliation
            # schedule, or a manual trigger such as {"action": "Delete"}.
            action = event.get('action', 'apply')
            logger.info(f'Direct invocation with action: {action}')
            result = process_accounts(
                selection_mode, excluded_accounts, included_accounts, '', action)

        return summarise_run(result)

    except ConfigRecorderUpdateError:
        # Already logged in summarise_run. Re-raised so the Errors metric fires.
        raise

    except Exception as e:
        exception_type = e.__class__.__name__
        exception_message = str(e)
        logger.exception(f'{exception_type}: {exception_message}')
        # Raise rather than returning a 500 body. Nothing reads the return value
        # of an asynchronous invocation, so returning would report success.
        raise ConfigRecorderUpdateError(f'{exception_type}: {exception_message}') from e
