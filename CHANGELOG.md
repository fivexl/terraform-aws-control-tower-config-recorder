# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [2.0.0] - 2026-07-22

### Changed
- **BREAKING**: Refactored Lambda deployment to use [terraform-aws-modules/lambda/aws](https://registry.terraform.io/modules/terraform-aws-modules/lambda/aws) v8.2.1 for consistency with other FivexL modules
- **BREAKING**: Moved Lambda source code from `ct_configrecorder_override.py` to `src/ct_configrecorder_override.py`
- **BREAKING**: Removed `archive` provider requirement (handled internally by the lambda module)
- IAM role and policies are now managed by the lambda module instead of standalone resources

### Added
- `cloudwatch_logs_retention_in_days` variable (default: 14 days)
- `lambda_role_arn` output
- Lambda function versioning (`publish = true`)

### Removed
- Direct `aws_iam_role`, `aws_iam_role_policy`, `aws_iam_role_policy_attachment` resources (replaced by lambda module)
- Direct `aws_lambda_function` resource (replaced by lambda module)
- Direct `aws_lambda_permission` resource (replaced by `allowed_triggers` in lambda module)
- `archive` provider dependency

## [1.0.0] - 2026-07-21

### Added
- Terraform module for overriding AWS Config Recorder settings in Control Tower managed accounts
- Single Lambda function (`ct_configrecorder_override.py`) that processes all accounts sequentially
- EventBridge rule triggering on Control Tower lifecycle events (`UpdateLandingZone`, `CreateManagedAccount`, `UpdateManagedAccount`, `ResetLandingZone`)
- `terraform_data` resource with `local-exec` provisioner to invoke Lambda on every apply
- Retry logic with backoff in local-exec to handle concurrent execution limits
- Async invocation (`--invocation-type Event`) to avoid blocking Terraform on Lambda execution
- Support for EXCLUSION and INCLUSION account selection modes
- Configurable recording strategies: inclusion list, exclusion list, daily recording frequency
- Variables with validation rules for all inputs
- IAM role with least-privilege permissions for Lambda execution

### Configuration
- `account_selection_mode` — EXCLUSION (default) or INCLUSION
- `excluded_accounts` / `included_accounts` — account targeting lists
- `config_recorder_strategy` — recording strategy selection
- `config_recorder_daily_resource_types` — resources recorded on daily cadence
- `config_recorder_excluded_resource_types` / `config_recorder_included_resource_types` — resource type filtering
- `config_recorder_default_recording_frequency` — CONTINUOUS or DAILY
