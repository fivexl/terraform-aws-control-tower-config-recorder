# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [3.0.0] - 2026-07-22

### Changed
- **BREAKING**: Replaced the `aws_region` variable with `control_tower_home_region`. The old variable conflated two unrelated things: where resources are created (now always the calling provider's region, per registry convention) and which region Control Tower calls home. `control_tower_home_region` defaults to the provider's region, so most callers can simply drop `aws_region`.
- **BREAKING**: Raised the minimum AWS provider to `>= 6.0`. The module reads `data.aws_region.current.region`; the older `name` and `id` attributes are deprecated in provider 6.x.
- **BREAKING**: Account lists now cross into the Lambda as JSON (`jsonencode`) instead of a hand-built Python list literal, and are parsed with `json.loads` instead of `ast.literal_eval`. Non-array values are rejected with a clear log message rather than failing later with a `TypeError`.
- The Lambda now raises `ConfigRecorderUpdateError` when any account fails, instead of logging the failure and returning `statusCode: 200`. Because the apply-time invocation is asynchronous, nothing read that return value, so failures were previously invisible. Raising surfaces them on the Lambda `Errors` metric.
- A failure that aborts the run before any account is processed (bad StackSet name, missing permissions) is now reported separately from per-account failures rather than being reported as a clean run.
- The apply-time invocation was reduced to a single portable `aws lambda invoke` call. The previous script used POSIX-only constructs (`[ ]`, `$((...))`, `grep`, single-quoted JSON) and a hardcoded `/tmp` path, so it could not run on Windows. The `TooManyRequestsException` retry loop it contained was unreachable: asynchronous invocations are queued and retried by Lambda rather than rejected synchronously.
- Lambda memory is now configurable via `lambda_memory_size` (default 1024 MB).
- `pip_requirements = false` is now set explicitly on `source_path` so a future change cannot silently bundle boto3 into the deployment package.

### Added
- `tags` variable, applied to the Lambda, IAM role, log group, EventBridge rule, and alarm
- `log_level` variable. DEBUG is safe to enable: the AWS SDK loggers are pinned above DEBUG so temporary credentials are never written to CloudWatch Logs
- `invoke_on_apply` variable to disable the apply-time invocation and rely solely on Control Tower lifecycle events
- `lambda_memory_size` variable
- CloudWatch alarm on the Lambda `Errors` metric, controlled by `create_error_alarm` and `alarm_actions`. This is the primary failure signal, since the apply-time invocation is asynchronous
- Retry bounds on asynchronous Lambda invocations (`lambda_maximum_retry_attempts`, `lambda_maximum_event_age_in_seconds`)
- Retry policy on the EventBridge target (`eventbridge_maximum_retry_attempts`, `eventbridge_maximum_event_age_in_seconds`). With no dead letter queue by design, these retries are what stand between a throttled delivery and a dropped Control Tower event
- `error_alarm_arn` and `control_tower_home_region` outputs
- `.tflint.hcl` enabling TFLint's bundled `terraform` ruleset, plus `terraform_tflint`, ruff, and hygiene hooks in pre-commit. The external AWS ruleset is deliberately not enabled: the shared CI workflow runs `tflint` with no `tflint --init` step, so requiring a downloadable plugin fails the build
- `src/ruff.toml` pinning the lint rule selection. CI installs the latest ruff, so relying on its defaults meant a new ruff release could fail the build on unchanged code
- `lifecycle_event_account` helper, removing the duplicated nested lookup between the Create and Update event branches
- `versions.tf` for each example so they are self-contained root modules
- `pytest.ini` and `conftest.py`, replacing the per-file `sys.path` manipulation

### Fixed
- `list_stack_instances` pagination now uses `page.get('Summaries', [])` instead of direct key access
- The Lambda now logs through a module logger rather than the root logger, so records are attributable and the level is tunable independently of the SDK loggers. `LOG_LEVEL` still controls it, which is covered by a test
- Handlers that catch an exception now use `logger.exception`, so failures carry a traceback. That matters more now that per-account failures are surfaced rather than swallowed
- Explanatory comments added for the placeholder `excluded_accounts` default and the deliberately fixed resource names

### Notes
- Resource names remain hardcoded (`ct-config-recorder-override`). Control Tower has one Config Recorder baseline per organization, so a second copy of this module in the management account would fight the first over the same accounts. Fixed names make that collision fail at apply time rather than silently double-managing accounts.
- No dead letter queue: this module intentionally uses only EventBridge and Lambda. The `Errors` metric alarm is the failure signal.

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
