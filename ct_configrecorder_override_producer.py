
#
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
#
# Permission is hereby granted, free of charge, to any person obtaining
# a copy of this software and associated documentation files (the "Software"),
# to deal in the Software without restriction, including without limitation
# the rights to use, copy, modify,merge, publish, distribute, sublicense,
# and/or sell copies of the Software, and to permit persons to whom the
# Software is furnished to do so.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS
# OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS
# IN THE SOFTWARE.
#

import boto3
import os
import logging
import ast

def should_process_account(account_id, selection_mode, excluded_accounts, included_accounts):
    """
    Determine if an account should be processed based on selection mode.
    
    Args:
        account_id (str): AWS account ID to check (e.g., '123456789012')
        selection_mode (str): Either 'EXCLUSION' or 'INCLUSION'
        excluded_accounts (list): List of account ID strings to exclude (used in EXCLUSION mode)
        included_accounts (list): List of account ID strings to include (used in INCLUSION mode)
    
    Returns:
        bool: True if account should be processed, False otherwise
    
    Example:
        should_process_account('123456789012', 'INCLUSION', [], ['123456789012', '234567890123'])
        # Returns True because account is in the inclusion list
    """
    if selection_mode == 'INCLUSION':
        # In inclusion mode, only process accounts in the included list
        should_process = account_id in included_accounts
        if should_process:
            logging.info(f'Account {account_id} included (in inclusion list)')
        else:
            logging.info(f'Account {account_id} excluded (not in inclusion list)')
        return should_process
    else:  # EXCLUSION mode (default)
        # In exclusion mode, process all accounts except those in excluded list
        should_process = account_id not in excluded_accounts
        if should_process:
            logging.info(f'Account {account_id} included (not in exclusion list)')
        else:
            logging.info(f'Account {account_id} excluded (in exclusion list)')
        return should_process

def lambda_handler(event, context):
    
    LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO')
    logging.getLogger().setLevel(LOG_LEVEL)

    try:
        logging.info('Event Data: ')
        logging.info(event)
        sqs_url = os.getenv('SQS_URL')
        
        # Read environment variables
        selection_mode = os.getenv('ACCOUNT_SELECTION_MODE', 'EXCLUSION')
        excluded_accounts_str = os.getenv('EXCLUDED_ACCOUNTS', '[]')
        included_accounts_str = os.getenv('INCLUDED_ACCOUNTS', '[]')
        
        logging.info(f'Account Selection Mode: {selection_mode}')
        logging.info(f'Excluded Accounts: {excluded_accounts_str}')
        logging.info(f'Included Accounts: {included_accounts_str}')
        
        # Parse account lists from string format to Python lists
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
        
        sqs_client = boto3.client('sqs')
        
        # Check if the lambda was triggered from EventBridge
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
            override_config_recorder(selection_mode, excluded_accounts, included_accounts, sqs_url, account, 'controltower')
        elif event_source == 'aws.controltower' and event_name == 'CreateManagedAccount':  
            account = event['detail']['serviceEventDetails']['createManagedAccountStatus']['account']['accountId']
            logging.info(f'Overriding config recorder for SINGLE account: {account}')
            override_config_recorder(selection_mode, excluded_accounts, included_accounts, sqs_url, account, 'controltower')
        elif event_source == 'aws.controltower' and event_name in ('UpdateLandingZone', 'ResetLandingZone'):
            logging.info(f'Overriding config recorder for ALL accounts due to {event_name} event')
            override_config_recorder(selection_mode, excluded_accounts, included_accounts, sqs_url, '', 'controltower')
        else:
            # Direct invocation (e.g., from Terraform local-exec or manual trigger)
            action = event.get('action', 'apply')
            logging.info(f'Direct invocation with action: {action}')
            override_config_recorder(selection_mode, excluded_accounts, included_accounts, sqs_url, '', action)

        logging.info('Execution Successful')
        
        return {
            'statusCode': 200
        }

    except Exception as e:
        exception_type = e.__class__.__name__
        exception_message = str(e)
        logging.exception(f'{exception_type}: {exception_message}')
        return {'statusCode': 500, 'error': exception_message}


def override_config_recorder(selection_mode, excluded_accounts, included_accounts, sqs_url, account, event):
    """
    Retrieve Control Tower managed accounts and send SQS messages for processing.
    
    Args:
        selection_mode (str): 'EXCLUSION' or 'INCLUSION'
        excluded_accounts (list): Parsed list of excluded account IDs
        included_accounts (list): Parsed list of included account IDs
        sqs_url (str): SQS queue URL
        account (str): Specific account ID to process, or empty string for all accounts
        event (str): Event type (e.g., 'Create', 'Update', 'Delete', 'controltower')
    """
    try:
        client = boto3.client('cloudformation')
        # Create a reusable Paginator
        paginator = client.get_paginator('list_stack_instances')
        
        # Create a PageIterator from the Paginator
        if account == '':
            page_iterator = paginator.paginate(StackSetName='AWSControlTowerBP-BASELINE-CONFIG')
        else:
            page_iterator = paginator.paginate(StackSetName='AWSControlTowerBP-BASELINE-CONFIG', StackInstanceAccount=account)
            
        sqs_client = boto3.client('sqs')
        
        # Process each account returned from Control Tower
        for page in page_iterator:
            logging.info(page)
            
            for item in page['Summaries']:
                account_id = item['Account']
                region = item['Region']
                # Delegate filtering decision to send_message_to_sqs
                send_message_to_sqs(
                    event, account_id, region, selection_mode, 
                    excluded_accounts, included_accounts, 
                    sqs_client, sqs_url)
                    
    except Exception as e:
        exception_type = e.__class__.__name__
        exception_message = str(e)
        logging.exception(f'{exception_type}: {exception_message}')

def send_message_to_sqs(event, account, region, selection_mode, excluded_accounts, included_accounts, sqs_client, sqs_url):
    
    try:
        # Use the new filtering function to determine if account should be processed
        if should_process_account(account, selection_mode, excluded_accounts, included_accounts):
            # Construct and send SQS message
            sqs_msg = f'{{"Account": "{account}", "Region": "{region}", "Event": "{event}"}}'
            response = sqs_client.send_message(
                QueueUrl=sqs_url,
                MessageBody=sqs_msg)
            logging.info(f'Message sent to SQS: {sqs_msg}')
        # Logging for inclusion/exclusion decisions is handled within should_process_account
                
    except Exception as e:
        exception_type = e.__class__.__name__
        exception_message = str(e)
        logging.exception(f'{exception_type}: {exception_message}') 
