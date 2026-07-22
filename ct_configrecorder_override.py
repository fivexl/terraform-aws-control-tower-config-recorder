
"""
AWS Config Recorder Override for Control Tower environments.

This Lambda function customizes AWS Config Recorder settings across child accounts
managed by AWS Control Tower. It is triggered by:

1. EventBridge rules on Control Tower lifecycle events (CreateManagedAccount,
   UpdateManagedAccount, UpdateLandingZone, ResetLandingZone)
2. Direct invocation from Terraform (via local-exec on apply)
3. Manual invocation for ad-hoc operations (e.g., resetting accounts with action=Delete)

The function iterates accounts from the AWSControlTowerBP-BASELINE-CONFIG StackSet,
assumes the AWSControlTowerExecution role in each target account, and updates the
Config Recorder according to the configured strategy and resource type lists.

Environment Variables:
    ACCOUNT_SELECTION_MODE: EXCLUSION or INCLUSION
    EXCLUDED_ACCOUNTS: Python list string of account IDs to skip (EXCLUSION mode)
    INCLUDED_ACCOUNTS: Python list string of account IDs to process (INCLUSION mode)
    CONFIG_RECORDER_STRATEGY: EXCLUSION or INCLUSION for resource types
    CONFIG_RECORDER_OVERRIDE_EXCLUDED_RESOURCE_LIST: Comma-separated resource types to exclude
    CONFIG_RECORDER_OVERRIDE_INCLUDED_RESOURCE_LIST: Comma-separated resource types to include
    CONFIG_RECORDER_OVERRIDE_DAILY_RESOURCE_LIST: Comma-separated resource types for daily recording
    CONFIG_RECORDER_OVERRIDE_DAILY_GLOBAL_RESOURCE_LIST: Global resource types for daily recording
    CONFIG_RECORDER_DEFAULT_RECORDING_FREQUENCY: CONTINUOUS or DAILY
    CONTROL_TOWER_HOME_REGION: AWS region where Control Tower is deployed
    LOG_LEVEL: Logging level (default: INFO)
"""

import boto3
import botocore.exceptions
import json
import logging
import os
import ast
import time


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
        if should_process:
            logging.info(f'Account {account_id} included (in inclusion list)')
        else:
            logging.info(f'Account {account_id} excluded (not in inclusion list)')
        return should_process
    else:  # EXCLUSION mode (default)
        should_process = account_id not in excluded_accounts
        if should_process:
            logging.info(f'Account {account_id} included (not in exclusion list)')
        else:
            logging.info(f'Account {account_id} excluded (in exclusion list)')
        return should_process


def update_config_recorder(account_id, aws_region, event_type):
    """
    Assume role into the target account and update its Config Recorder settings.

    Uses the AWSControlTowerExecution role to access child accounts. On 'Delete'
    events, resets the Config Recorder to Control Tower defaults (allSupported=True).
    Otherwise, applies the configured recording strategy (EXCLUSION or INCLUSION)
    with optional daily recording frequency overrides.

    Args:
        account_id (str): Target AWS account ID
        aws_region (str): Target AWS region
        event_type (str): Event type — 'Delete' resets to defaults, anything else applies config
    """
    try:
        STS = boto3.client('sts', region_name=aws_region)
        curr_account = STS.get_caller_identity()['Account']

        if curr_account == account_id:
            logging.info(f'Skipping current account {account_id}')
            return

        part = STS.get_caller_identity()['Arn'].split(':')[1]
        role_arn = f'arn:{part}:iam::{account_id}:role/AWSControlTowerExecution'
        ses_name = f'{account_id}-AWSControlTowerExecution'

        response = STS.assume_role(RoleArn=role_arn, RoleSessionName=ses_name)
        sts_session = boto3.Session(
            aws_access_key_id=response['Credentials']['AccessKeyId'],
            aws_secret_access_key=response['Credentials']['SecretAccessKey'],
            aws_session_token=response['Credentials']['SessionToken'])

        logging.info(f'Assumed role in account {account_id}, region {aws_region}')

        configservice = sts_session.client('config', region_name=aws_region)

        # Describe existing configuration recorder
        configrecorder = configservice.describe_configuration_recorders()
        logging.info(f'Existing Configuration Recorder: {configrecorder}')

        recorder_name = 'aws-controltower-BaselineConfigRecorder'
        if (configrecorder and 'ConfigurationRecorders' in configrecorder
                and len(configrecorder['ConfigurationRecorders']) > 0):
            recorder_name = configrecorder['ConfigurationRecorders'][0]['name']
            logging.info(f'Using existing recorder name: {recorder_name}')

        role_arn_config = f'arn:aws:iam::{account_id}:role/aws-service-role/config.amazonaws.com/AWSServiceRoleForConfig'

        # Read strategy and resource lists from environment
        CONFIG_RECORDER_STRATEGY = os.getenv('CONFIG_RECORDER_STRATEGY', 'EXCLUSION')

        CONFIG_RECORDER_DAILY_RESOURCE_STRING = os.getenv('CONFIG_RECORDER_OVERRIDE_DAILY_RESOURCE_LIST', '')
        CONFIG_RECORDER_OVERRIDE_DAILY_RESOURCE_LIST = (
            CONFIG_RECORDER_DAILY_RESOURCE_STRING.split(',')
            if CONFIG_RECORDER_DAILY_RESOURCE_STRING != '' else [])

        CONFIG_RECORDER_DAILY_GLOBAL_RESOURCE_STRING = os.getenv('CONFIG_RECORDER_OVERRIDE_DAILY_GLOBAL_RESOURCE_LIST', '')
        CONFIG_RECORDER_DAILY_GLOBAL_RESOURCE_LIST = (
            CONFIG_RECORDER_DAILY_GLOBAL_RESOURCE_STRING.split(',')
            if CONFIG_RECORDER_DAILY_GLOBAL_RESOURCE_STRING != '' else [])

        CONFIG_RECORDER_EXCLUSION_RESOURCE_STRING = os.getenv('CONFIG_RECORDER_OVERRIDE_EXCLUDED_RESOURCE_LIST', '')
        CONFIG_RECORDER_EXCLUSION_RESOURCE_LIST = (
            CONFIG_RECORDER_EXCLUSION_RESOURCE_STRING.split(',')
            if CONFIG_RECORDER_EXCLUSION_RESOURCE_STRING != '' else [])

        CONFIG_RECORDER_INCLUSION_RESOURCE_STRING = os.getenv('CONFIG_RECORDER_OVERRIDE_INCLUDED_RESOURCE_LIST', '')
        CONFIG_RECORDER_INCLUSION_RESOURCE_LIST = (
            CONFIG_RECORDER_INCLUSION_RESOURCE_STRING.split(',')
            if CONFIG_RECORDER_INCLUSION_RESOURCE_STRING != '' else [])

        CONFIG_RECORDER_DEFAULT_RECORDING_FREQUENCY = os.getenv('CONFIG_RECORDER_DEFAULT_RECORDING_FREQUENCY', 'CONTINUOUS')

        # For exclusion strategy, remove daily resources that are in exclusion list
        if CONFIG_RECORDER_STRATEGY == 'EXCLUSION':
            CONFIG_RECORDER_OVERRIDE_DAILY_RESOURCE_LIST = [
                x for x in CONFIG_RECORDER_OVERRIDE_DAILY_RESOURCE_LIST
                if x not in CONFIG_RECORDER_EXCLUSION_RESOURCE_LIST]
        else:
            # For inclusion strategy, ensure daily resources are in the inclusion list
            for resource_type in CONFIG_RECORDER_OVERRIDE_DAILY_RESOURCE_LIST:
                if resource_type not in CONFIG_RECORDER_INCLUSION_RESOURCE_LIST:
                    CONFIG_RECORDER_INCLUSION_RESOURCE_LIST.append(resource_type)

        # Add global daily resources if this is the home region
        home_region = os.getenv('CONTROL_TOWER_HOME_REGION') == aws_region
        if home_region:
            CONFIG_RECORDER_OVERRIDE_DAILY_RESOURCE_LIST += CONFIG_RECORDER_DAILY_GLOBAL_RESOURCE_LIST

        if event_type == 'Delete':
            response = configservice.put_configuration_recorder(
                ConfigurationRecorder={
                    'name': recorder_name,
                    'roleARN': role_arn_config,
                    'recordingGroup': {
                        'allSupported': True,
                        'includeGlobalResourceTypes': home_region
                    }
                })
            logging.warning(
                f'Configuration Recorder reset to default. Response: {json.dumps(response, default=str)}')
        else:
            if CONFIG_RECORDER_STRATEGY == 'EXCLUSION':
                logging.info(f'Using EXCLUSION strategy')
                logging.info(f'Exclusion resource list: {CONFIG_RECORDER_EXCLUSION_RESOURCE_LIST}')
                logging.info(f'Daily override resource list: {CONFIG_RECORDER_OVERRIDE_DAILY_RESOURCE_LIST}')

                config_recorder = {
                    'name': recorder_name,
                    'roleARN': role_arn_config,
                    'recordingGroup': {
                        'allSupported': False,
                        'includeGlobalResourceTypes': False,
                        'exclusionByResourceTypes': {
                            'resourceTypes': CONFIG_RECORDER_EXCLUSION_RESOURCE_LIST
                        },
                        'recordingStrategy': {
                            'useOnly': 'EXCLUSION_BY_RESOURCE_TYPES'
                        }
                    },
                    'recordingMode': {
                        'recordingFrequency': CONFIG_RECORDER_DEFAULT_RECORDING_FREQUENCY,
                        'recordingModeOverrides': [
                            {
                                'description': 'DAILY_OVERRIDE',
                                'resourceTypes': CONFIG_RECORDER_OVERRIDE_DAILY_RESOURCE_LIST,
                                'recordingFrequency': 'DAILY'
                            }
                        ] if CONFIG_RECORDER_OVERRIDE_DAILY_RESOURCE_LIST else []
                    }
                }

                if not CONFIG_RECORDER_EXCLUSION_RESOURCE_LIST:
                    config_recorder['recordingGroup'].pop('exclusionByResourceTypes')
                    config_recorder['recordingGroup'].pop('recordingStrategy')
                    config_recorder['recordingGroup']['allSupported'] = True
                    config_recorder['recordingGroup']['includeGlobalResourceTypes'] = True
            else:
                logging.info(f'Using INCLUSION strategy')
                for resource_type in CONFIG_RECORDER_OVERRIDE_DAILY_RESOURCE_LIST:
                    if resource_type not in CONFIG_RECORDER_INCLUSION_RESOURCE_LIST:
                        CONFIG_RECORDER_INCLUSION_RESOURCE_LIST.append(resource_type)

                logging.info(f'Inclusion resource list: {CONFIG_RECORDER_INCLUSION_RESOURCE_LIST}')
                logging.info(f'Daily override resource list: {CONFIG_RECORDER_OVERRIDE_DAILY_RESOURCE_LIST}')

                config_recorder = {
                    'name': recorder_name,
                    'roleARN': role_arn_config
                }

                if not CONFIG_RECORDER_INCLUSION_RESOURCE_LIST:
                    config_recorder['recordingGroup'] = {
                        'allSupported': False,
                        'includeGlobalResourceTypes': False
                    }
                else:
                    config_recorder['recordingGroup'] = {
                        'allSupported': False,
                        'includeGlobalResourceTypes': False,
                        'resourceTypes': CONFIG_RECORDER_INCLUSION_RESOURCE_LIST,
                        'recordingStrategy': {
                            'useOnly': 'INCLUSION_BY_RESOURCE_TYPES'
                        }
                    }

                if CONFIG_RECORDER_OVERRIDE_DAILY_RESOURCE_LIST:
                    config_recorder['recordingMode'] = {
                        'recordingFrequency': CONFIG_RECORDER_DEFAULT_RECORDING_FREQUENCY,
                        'recordingModeOverrides': [
                            {
                                'description': 'DAILY_OVERRIDE',
                                'resourceTypes': CONFIG_RECORDER_OVERRIDE_DAILY_RESOURCE_LIST,
                                'recordingFrequency': 'DAILY'
                            }
                        ]
                    }

            response = configservice.put_configuration_recorder(
                ConfigurationRecorder=config_recorder)
            logging.info(f'Response for put_configuration_recorder: {response}')

        # Verify the update
        configrecorder = configservice.describe_configuration_recorders()
        logging.info(f'Post Change Configuration recorder: {configrecorder}')

    except botocore.exceptions.ClientError as exe:
        logging.error(f'Unable to update Config Recorder for account {account_id} in region {aws_region}: {exe}')
        raise exe


def process_accounts(selection_mode, excluded_accounts, included_accounts, account, event_type):
    """
    Retrieve Control Tower managed accounts and update Config Recorder in each.

    Lists accounts from the AWSControlTowerBP-BASELINE-CONFIG StackSet, filters
    them based on selection mode, and calls update_config_recorder for each.
    A 0.5s delay between accounts avoids STS AssumeRole throttling.

    Failures on individual accounts are logged but do not stop processing of
    remaining accounts.

    Args:
        selection_mode (str): 'EXCLUSION' or 'INCLUSION'
        excluded_accounts (list): Account IDs to skip (EXCLUSION mode)
        included_accounts (list): Account IDs to process (INCLUSION mode)
        account (str): Specific account ID, or empty string for all accounts
        event_type (str): Passed through to update_config_recorder
    """
    try:
        client = boto3.client('cloudformation')
        paginator = client.get_paginator('list_stack_instances')

        if account == '':
            page_iterator = paginator.paginate(StackSetName='AWSControlTowerBP-BASELINE-CONFIG')
        else:
            page_iterator = paginator.paginate(
                StackSetName='AWSControlTowerBP-BASELINE-CONFIG',
                StackInstanceAccount=account)

        for page in page_iterator:
            logging.info(page)

            for item in page['Summaries']:
                account_id = item['Account']
                region = item['Region']

                if should_process_account(account_id, selection_mode, excluded_accounts, included_accounts):
                    try:
                        update_config_recorder(account_id, region, event_type)
                        # Delay between accounts to avoid STS throttling
                        time.sleep(1)
                    except Exception as e:
                        logging.error(f'Failed to update account {account_id} in {region}: {e}')
                        # Continue processing other accounts

    except Exception as e:
        exception_type = e.__class__.__name__
        exception_message = str(e)
        logging.exception(f'{exception_type}: {exception_message}')


def lambda_handler(event, context):

    LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO')
    logging.getLogger().setLevel(LOG_LEVEL)

    try:
        logging.info('Event Data: ')
        logging.info(event)

        # Read environment variables
        selection_mode = os.getenv('ACCOUNT_SELECTION_MODE', 'EXCLUSION')
        excluded_accounts_str = os.getenv('EXCLUDED_ACCOUNTS', '[]')
        included_accounts_str = os.getenv('INCLUDED_ACCOUNTS', '[]')

        logging.info(f'Account Selection Mode: {selection_mode}')
        logging.info(f'Excluded Accounts: {excluded_accounts_str}')
        logging.info(f'Included Accounts: {included_accounts_str}')

        # Parse account lists
        try:
            excluded_accounts = ast.literal_eval(excluded_accounts_str)
        except (ValueError, SyntaxError) as e:
            logging.error(f'Failed to parse excluded accounts: {e}')
            excluded_accounts = []

        try:
            included_accounts = ast.literal_eval(included_accounts_str)
        except (ValueError, SyntaxError) as e:
            logging.error(f'Failed to parse included accounts: {e}')
            included_accounts = []

        # Determine the trigger source
        is_eb_triggered = 'source' in event
        logging.info(f'Is EventBridge Triggered: {str(is_eb_triggered)}')
        event_source = ''
        event_name = ''

        if is_eb_triggered:
            event_source = event['source']
            logging.info(f'Control Tower Event Source: {event_source}')
            event_name = event['detail']['eventName']
            logging.info(f'Control Tower Event Name: {event_name}')

        if event_source == 'aws.controltower' and event_name == 'UpdateManagedAccount':
            account = event['detail']['serviceEventDetails']['updateManagedAccountStatus']['account']['accountId']
            logging.info(f'Overriding config recorder for SINGLE account: {account}')
            process_accounts(selection_mode, excluded_accounts, included_accounts, account, 'controltower')

        elif event_source == 'aws.controltower' and event_name == 'CreateManagedAccount':
            account = event['detail']['serviceEventDetails']['createManagedAccountStatus']['account']['accountId']
            logging.info(f'Overriding config recorder for SINGLE account: {account}')
            process_accounts(selection_mode, excluded_accounts, included_accounts, account, 'controltower')

        elif event_source == 'aws.controltower' and event_name in ('UpdateLandingZone', 'ResetLandingZone'):
            logging.info(f'Overriding config recorder for ALL accounts due to {event_name} event')
            process_accounts(selection_mode, excluded_accounts, included_accounts, '', 'controltower')

        else:
            # Direct invocation (e.g., from Terraform local-exec or manual trigger)
            action = event.get('action', 'apply')
            logging.info(f'Direct invocation with action: {action}')
            process_accounts(selection_mode, excluded_accounts, included_accounts, '', action)

        logging.info('Execution Successful')
        return {'statusCode': 200}

    except Exception as e:
        exception_type = e.__class__.__name__
        exception_message = str(e)
        logging.exception(f'{exception_type}: {exception_message}')
        return {'statusCode': 500, 'error': exception_message}
