# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [4.0.0] - 2026-08-12

### Fixed
- **Global IAM resource types are no longer recorded outside the Control Tower home region.** Two separate paths got this wrong. With an empty `config_recorder_excluded_resource_types`, `includeGlobalResourceTypes` was hardcoded to `true`, switching global IAM recording on in every governed region; it now follows the home region like the `Delete` branch always did. Under the `EXCLUSION_BY_RESOURCE_TYPES` strategy the flag is [ignored by AWS](https://docs.aws.amazon.com/config/latest/APIReference/API_RecordingGroup.html) altogether, so sending it as `false` did nothing and the default configuration was recording IAM users, groups, roles and customer managed policies in every governed region. The four types are now added to the exclusion list itself outside the home region. IAM is global and churns on every deploy, so for an organization with N accounts across R regions this was (R-1)×N accounts recording duplicate data at full cost.
- **`config_recorder_default_recording_frequency` is now honoured when the override lists are empty.** `recordingMode` was only emitted when there was at least one override type, so `DAILY` with empty lists — the natural way to say "record everything once every 24 hours" — sent nothing and left every recorder on continuous recording, with no error and no log line. The block is now emitted whenever a non-default frequency is configured, and `recordingModeOverrides` only when there are types to override.
- **`config_recorder_daily_global_resource_types` no longer bypasses the exclusion filter.** The global list was appended after the filter ran, so a type named in both it and `config_recorder_excluded_resource_types` produced a cadence override for a type that was not being recorded at all. Duplicate entries across the two override lists are also collapsed now.
- **The EventBridge rule now covers the Control Tower operations that actually redeploy the Config baseline.** It subscribed to `ResetLandingZone`, which is not a [documented lifecycle event](https://docs.aws.amazon.com/controltower/latest/userguide/lifecycle-events.html) but an API operation, and CloudTrail publishes API calls under a different `detail-type` than the one this rule matches. Meanwhile `RegisterOrganizationalUnit`, `EnableBaseline`, `ResetEnabledBaseline` and `UpdateEnabledBaseline` were missing, and every one of them can redeploy `AWSControlTowerBP-BASELINE-CONFIG` and discard this customization. A missed event is silent: the function never runs, so nothing fails, the `Errors` alarm stays quiet, and an unchanged `terraform apply` does not re-invoke either because the apply-time trigger keys on the source code hash. The recorder stayed reverted until someone changed the module or invoked the function by hand.
- **A wrong `control_tower_home_region` no longer stops global IAM recording everywhere in silence.** The region was an unchecked string comparison, so a typo or a region Control Tower does not govern matched nothing, and every region then excluded the global IAM types while each account updated successfully and the run reported success. The consequence got sharper with the exclusion-list fix above: before it, a wrong region left IAM recorded everywhere; after it, IAM was recorded nowhere. The Lambda now fails the run when the nominated region was not among those the StackSet reported, which surfaces it on the `Errors` metric. The check applies to full walks only, since a single account need not have an instance in every governed region. Both region variables also get a syntax validation, which catches `us-east-11` but deliberately is not relied on for `us-east-2` when `us-east-1` was meant.
- **Failed lifecycle events are now ignored.** A failed Control Tower operation applied no baseline, so there was nothing to override, but the function still ran and assumed a role into an account that may not have been provisioned. That failed the run and tripped the error alarm for no reason.

### Added
- `config_recorder_override_recording_frequency` variable (default `DAILY`), the recording frequency applied to the two override resource type lists. This makes the inverse arrangement expressible for the first time: a `DAILY` default with `CONTINUOUS` for a few named types. AWS Firewall Manager [depends on continuous recording](https://docs.aws.amazon.com/config/latest/APIReference/API_RecordingMode.html) for the types its policies cover, so an FMS user who wants daily recording for cost reasons previously had no way to exempt them. AWS caps `recordingModeOverrides` at one object, so a single frequency plus one type list is the whole of what the API can express.
- `global_iam_recording_region` variable, separating "where Control Tower lives" from "which single region records the global IAM resource types". AWS cannot record those types in the ten regions where Config arrived after February 2022, and Control Tower can be homed in some of them, including Europe (Zurich). Previously such a landing zone would exclude the IAM types in every other governed region while requesting them in the one region AWS refuses to record them, so nothing recorded them. The variable defaults to `control_tower_home_region`, accepts another governed region, or `""` to accept that no region records them. A plan-time precondition rejects a nominated region from that list and names the variable in the error. Also adds a `global_iam_recording_region` output.
- `reconciliation_schedule_expression` variable (default `rate(12 hours)`) and the EventBridge schedule behind it, which re-invokes the function on a timer. Subscribing to more lifecycle events is necessary but not sufficient: the list has grown before and this module had already fallen behind it, so the schedule bounds how long drift can last without needing to predict what AWS adds next. Re-applying is idempotent. Set to `null` or `""` to disable. Also adds a `reconciliation_rule_arn` output.
- Lifecycle event parsing now reads whichever payload shape is present rather than switching on the event name, so an event type AWS adds later resolves without a code change. The three shapes differ in both fields that matter: the account events carry `account.accountId` and `state`, the baseline events carry an Organizations ARN at `enabledBaselineDetails.targetIdentifier` with the status nested at `statusSummary.status`, and the OU and landing zone events name no single account at all. An unreadable state is processed rather than dropped, because silently discarding an unrecognised event would reproduce the exact failure this function exists to prevent.
- Plan-time preconditions for three configurations that previously failed silently at runtime: an empty `excluded_accounts` in `EXCLUSION` mode, an empty `included_accounts` in `INCLUSION` mode, and an empty `config_recorder_included_resource_types` with the `INCLUSION` strategy. These are preconditions on a `terraform_data` resource rather than variable `validation` blocks because each reads two variables at once, and cross-variable validation needs Terraform 1.9 while this module supports 1.5.
- The function now raises rather than writing a Config Recorder with `allSupported` false and no resource types, which records nothing. Terraform catches this at plan time; the runtime guard covers a function invoked with hand-edited environment variables.
- Tests for every branch above, and the payload tests are now parametrised over home and non-home region. Previously every payload test ran in the home region, which is what let the global resource type defect stay invisible.
- README sections on global resource types (including `AWS::RDS::GlobalCluster`, which is recorded in every enabled region regardless of `includeGlobalResourceTypes`), recording frequency, the three types AWS will not record daily, a concrete scaling formula, and how the customization stays applied.
- README notes that lifecycle events only reach EventBridge when an active CloudTrail trail has logging enabled. Control Tower creates one by default, but without it the module only runs on apply and on the schedule.

### Changed
- **BREAKING**: `excluded_accounts` now defaults to `[]` instead of `["111111111111", "222222222222", "333333333333"]`, and `EXCLUSION` mode rejects an empty list at plan time. The placeholder IDs matched no real account, so an unconfigured module rewrote the Config Recorder in every managed account. A dangerous default plus a prose warning in the README was weaker than a default that cannot misfire.
- `config_recorder_daily_resource_types` and `config_recorder_daily_global_resource_types` keep their names for compatibility but are now documented as the resource types the override frequency applies to, which is daily only by default.
- `lambda_memory_size` description no longer claims peak memory grows with the number of account-region pairs. The function holds one cached session per account and one settings dict per region, so memory is roughly flat; the setting mainly buys CPU.
- The `Delete` action follows `control_tower_home_region` rather than `global_iam_recording_region`, since it restores Control Tower's own defaults and Control Tower records global types where it lives.
- `pytest.ini` puts `tests` on `pythonpath` so shared test constants can be imported from `conftest`.

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
