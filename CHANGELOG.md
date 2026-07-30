# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.0] - 2026-07-21

### Added
- Terraform module for overriding AWS Config Recorder settings in Control Tower managed accounts
- Producer Lambda function that discovers accounts via CloudFormation StackSet instances and fans out work via SQS
- Consumer Lambda function that assumes a role in each account and applies Config Recorder overrides
- SQS queue for Producer-to-Consumer communication with encryption at rest (alias/aws/sqs)
- EventBridge rule triggering on Control Tower lifecycle events (`UpdateLandingZone`, `CreateManagedAccount`, `UpdateManagedAccount`, `ResetLandingZone`)
- `terraform_data` resource with `local-exec` provisioner to invoke the Producer on every apply
- Support for EXCLUSION and INCLUSION account selection modes
- Configurable recording strategies: inclusion list, exclusion list, daily recording frequency
- Variables with validation rules for all inputs
- IAM roles with least-privilege permissions for both Lambda functions

### Architecture
- Producer/Consumer separation for parallel account processing via SQS fan-out
- Handles 100+ accounts efficiently with concurrent Consumer invocations
- Consumer concurrency set to 10 for controlled parallelism

### Configuration
- `account_selection_mode` — EXCLUSION (default) or INCLUSION
- `excluded_accounts` / `included_accounts` — account targeting lists
- `config_recorder_strategy` — recording strategy selection
- `config_recorder_daily_resource_types` — resources recorded on daily cadence
- `config_recorder_excluded_resource_types` / `config_recorder_included_resource_types` — resource type filtering
- `config_recorder_default_recording_frequency` — CONTINUOUS or DAILY
