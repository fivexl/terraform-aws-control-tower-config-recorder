data "aws_partition" "current" {}

data "aws_region" "current" {}

locals {
  # Where Control Tower lives, which decides where global resource types are
  # recorded. Falls back to the region this module is being applied in.
  control_tower_home_region = coalesce(var.control_tower_home_region, data.aws_region.current.region)

  # The single region that records the global IAM types. Defaults to the home
  # region, matching the Control Tower baseline. An empty string is a deliberate
  # "nowhere", so this uses != null rather than coalesce.
  global_iam_recording_region = (
    var.global_iam_recording_region != null
    ? var.global_iam_recording_region
    : local.control_tower_home_region
  )

  # AWS can only record the global IAM resource types in regions where Config was
  # available before February 2022. These ten came later, and Control Tower can be
  # homed in some of them, so nominating one records nothing at all.
  # https://docs.aws.amazon.com/config/latest/developerguide/select-resources.html
  # Keep in step with that page as AWS adds regions.
  regions_without_global_iam_recording = [
    "ap-south-2",     # Asia Pacific (Hyderabad)
    "ap-southeast-4", # Asia Pacific (Melbourne)
    "ap-southeast-5", # Asia Pacific (Malaysia)
    "ap-southeast-7", # Asia Pacific (Thailand)
    "ca-west-1",      # Canada West (Calgary)
    "eu-central-2",   # Europe (Zurich)
    "eu-south-2",     # Europe (Spain)
    "il-central-1",   # Israel (Tel Aviv)
    "me-central-1",   # Middle East (UAE)
    "mx-central-1",   # Mexico (Central)
  ]

  # Account lists cross into the Lambda as JSON so the function can parse them
  # with json.loads rather than evaluating a Python literal.
  excluded_accounts_json = jsonencode(var.excluded_accounts)
  included_accounts_json = jsonencode(var.included_accounts)

  # Resource names are deliberately fixed rather than prefixed. Control Tower has
  # exactly one Config Recorder baseline per organization, so a second copy of
  # this module in the management account would fight the first one over the same
  # accounts. The hardcoded names make that collision fail loudly at apply time
  # instead of silently double-managing every account.
  function_name = "ct-config-recorder-override"
  rule_name     = "ct-config-recorder-override-trigger"

  # Both null and "" disable the schedule. See the variable for why.
  reconciliation_enabled = try(trimspace(var.reconciliation_schedule_expression), "") != ""
}

# -----------------------------------------------------------------------------
# Configuration guards
#
# These are preconditions rather than variable validation blocks because each one
# reads two variables at once, and cross-variable validation needs Terraform 1.9.
# This module supports 1.5, so the checks live on a resource instead.
#
# Both failures they catch are silent at runtime: one rewrites accounts nobody
# meant to touch, the other writes a recorder that records nothing. Plan time is
# the right place to find out.
# -----------------------------------------------------------------------------

resource "terraform_data" "validate_configuration" {
  # Re-planned whenever a guarded value changes, so the checks cannot be skipped
  # by an unrelated no-op plan.
  triggers_replace = [
    var.account_selection_mode,
    var.config_recorder_strategy,
    length(var.excluded_accounts),
    length(var.included_accounts),
    var.config_recorder_included_resource_types,
  ]

  lifecycle {
    precondition {
      condition     = var.account_selection_mode != "EXCLUSION" || length(var.excluded_accounts) > 0
      error_message = "excluded_accounts must not be empty in EXCLUSION mode. Every managed account except the one this module runs in would have its Config Recorder rewritten, including Log Archive and Audit, which should keep their Control Tower defaults. List the accounts to leave alone, or use account_selection_mode = \"INCLUSION\" to name the accounts to change instead."
    }

    precondition {
      condition     = var.account_selection_mode != "INCLUSION" || length(var.included_accounts) > 0
      error_message = "included_accounts must not be empty in INCLUSION mode, otherwise the function runs and updates nothing."
    }

    precondition {
      condition     = var.config_recorder_strategy != "INCLUSION" || trimspace(var.config_recorder_included_resource_types) != ""
      error_message = "config_recorder_included_resource_types must not be empty with config_recorder_strategy = \"INCLUSION\". That combination produces a Config Recorder that records nothing, disabling Config recording across every targeted account."
    }

  }
}

# Kept separate from the checks above because the region it validates can come from
# data.aws_region, and anything reading that resolves only once the provider is
# configured. The variable-only checks stay independent of it so they always fail at
# plan time.
resource "terraform_data" "validate_global_iam_region" {
  triggers_replace = [local.global_iam_recording_region]

  lifecycle {
    # Without this, a landing zone homed in one of those regions would exclude the
    # global IAM types in every other governed region while requesting them in the
    # one region AWS refuses to record them. Nothing would record them, and nothing
    # would say so.
    precondition {
      condition     = !contains(local.regions_without_global_iam_recording, local.global_iam_recording_region)
      error_message = "AWS cannot record the global IAM resource types in ${local.global_iam_recording_region}, because AWS Config was added there after February 2022. Set global_iam_recording_region to a governed region that supports them, or to \"\" to accept that no region records them."
    }
  }
}

# -----------------------------------------------------------------------------
# Lambda Function (using terraform-aws-modules/lambda/aws)
# -----------------------------------------------------------------------------

module "lambda" {
  source  = "terraform-aws-modules/lambda/aws"
  version = "8.2.1"

  function_name = local.function_name
  description   = "Override AWS Config Recorder settings in Control Tower managed accounts"
  handler       = "ct_configrecorder_override.lambda_handler"
  runtime       = "python3.14"
  architectures = ["x86_64"]
  memory_size   = var.lambda_memory_size
  timeout       = 900
  publish       = true

  source_path = [
    {
      path = "${path.module}/src"
      # Nothing here needs installing: the function imports only the standard
      # library plus boto3, which the runtime already provides. requirements.txt
      # exists for CI and local testing, so it is kept out of the package and
      # pip is explicitly disabled to stop a future change from bundling boto3.
      pip_requirements = false
      patterns = [
        "!tests/.*",
        "!requirements\\.txt",
        "!__pycache__/.*",
        # Dev tooling config, meaningful only to pytest/ruff on a workstation or in
        # CI. Neither is imported by the handler, so leaving them out shrinks the
        # deployed artifact without changing behavior.
        "!pytest\\.ini",
        "!ruff\\.toml",
        "!\\.pytest_cache/.*",
        "!\\.ruff_cache/.*",
      ]
    }
  ]

  # One concurrent execution: the function walks accounts sequentially and
  # overlapping runs would race each other writing the same Config Recorders.
  reserved_concurrent_executions = 1

  # Bound the asynchronous retry behaviour. put_configuration_recorder is
  # idempotent, so a retry re-applies the same settings harmlessly.
  create_async_event_config    = true
  maximum_retry_attempts       = var.lambda_maximum_retry_attempts
  maximum_event_age_in_seconds = var.lambda_maximum_event_age_in_seconds

  # terraform-aws-modules/lambda/aws computes source_code_hash from fileexists() on
  # the packaged archive. On the very first apply in a fresh working directory that
  # archive does not exist yet when the hash is computed, which can make plan and
  # apply disagree and require running apply twice. ignore_source_code_hash works
  # around it by skipping that hash entirely, which also means the function stops
  # redeploying automatically when only the source changes. Off by default for that
  # reason; see the README note on the first-apply case.
  ignore_source_code_hash = var.lambda_ignore_source_code_hash

  environment_variables = {
    ACCOUNT_SELECTION_MODE                              = var.account_selection_mode
    EXCLUDED_ACCOUNTS                                   = local.excluded_accounts_json
    INCLUDED_ACCOUNTS                                   = local.included_accounts_json
    LOG_LEVEL                                           = var.log_level
    CONFIG_RECORDER_STRATEGY                            = var.config_recorder_strategy
    CONFIG_RECORDER_OVERRIDE_DAILY_RESOURCE_LIST        = var.config_recorder_daily_resource_types
    CONFIG_RECORDER_OVERRIDE_DAILY_GLOBAL_RESOURCE_LIST = var.config_recorder_daily_global_resource_types
    CONFIG_RECORDER_OVERRIDE_EXCLUDED_RESOURCE_LIST     = var.config_recorder_excluded_resource_types
    CONFIG_RECORDER_OVERRIDE_INCLUDED_RESOURCE_LIST     = var.config_recorder_included_resource_types
    CONFIG_RECORDER_DEFAULT_RECORDING_FREQUENCY         = var.config_recorder_default_recording_frequency
    CONFIG_RECORDER_OVERRIDE_RECORDING_FREQUENCY        = var.config_recorder_override_recording_frequency
    GLOBAL_IAM_RECORDING_REGION                         = local.global_iam_recording_region
    CONTROL_TOWER_HOME_REGION                           = local.control_tower_home_region
  }

  attach_policy_json = true
  policy_json        = data.aws_iam_policy_document.lambda_policy.json

  allowed_triggers = merge(
    {
      ControlTowerEvents = {
        principal  = "events.amazonaws.com"
        source_arn = aws_cloudwatch_event_rule.control_tower.arn
      }
    },
    local.reconciliation_enabled ? {
      ReconciliationSchedule = {
        principal = "events.amazonaws.com"
        # one() rather than [0] so this is safe to evaluate when the rule count
        # is zero.
        source_arn = one(aws_cloudwatch_event_rule.reconciliation[*].arn)
      }
    } : {},
  )

  cloudwatch_logs_retention_in_days = var.cloudwatch_logs_retention_in_days

  tags = var.tags
}

data "aws_iam_policy_document" "lambda_policy" {
  statement {
    effect  = "Allow"
    actions = ["cloudformation:ListStackInstances"]
    resources = [
      "arn:${data.aws_partition.current.partition}:cloudformation:*:*:stackset/AWSControlTowerBP-BASELINE-CONFIG:*"
    ]
  }

  # The account is a wildcard because target accounts are discovered at runtime
  # from the StackSet, but the role name is fixed: Control Tower creates
  # AWSControlTowerExecution in every managed account.
  statement {
    effect    = "Allow"
    actions   = ["sts:AssumeRole"]
    resources = ["arn:${data.aws_partition.current.partition}:iam::*:role/AWSControlTowerExecution"]
  }
}

# -----------------------------------------------------------------------------
# EventBridge Rule — triggers on Control Tower lifecycle events
# -----------------------------------------------------------------------------

resource "aws_cloudwatch_event_rule" "control_tower" {
  name        = local.rule_name
  description = "Rule to trigger config recorder override lambda"

  # Every operation that can redeploy AWSControlTowerBP-BASELINE-CONFIG belongs
  # here, because that StackSet is what overwrites this customization. A missing
  # event is invisible: the function never runs, nothing fails, and the Errors
  # alarm stays quiet while the recorder sits reverted.
  #
  # detail-type matters. Lifecycle events are non-API service events, published as
  # "AWS Service Event via CloudTrail". Control Tower API calls are published as
  # "AWS API Call via CloudTrail" and would not match this rule.
  event_pattern = jsonencode({
    source      = ["aws.controltower"]
    detail-type = ["AWS Service Event via CloudTrail"]
    detail = {
      eventName = [
        # Account factory: a new or re-enrolled account gets the baseline.
        "CreateManagedAccount",
        "UpdateManagedAccount",
        # Landing zone update re-applies baselines across the organization.
        "UpdateLandingZone",
        # Not in the documented lifecycle event list, and ResetLandingZone is an
        # API operation, which CloudTrail publishes under the other detail-type.
        # Retained anyway: an unmatched name costs nothing, and removing it would
        # be a bet against undocumented behaviour. A reset should reach us through
        # the landing zone, OU and baseline events it triggers.
        "ResetLandingZone",
        # Extending governance to an OU enrolls its accounts and deploys the
        # Config baseline to each one.
        "RegisterOrganizationalUnit",
        # Baseline operations redeploy the Config baseline directly. These were
        # the gap: they are newer than the events above and can revert every
        # targeted account.
        "EnableBaseline",
        "ResetEnabledBaseline",
        "UpdateEnabledBaseline",
      ]
    }
  })

  tags = var.tags
}

resource "aws_cloudwatch_event_target" "lambda" {
  rule = aws_cloudwatch_event_rule.control_tower.name
  arn  = module.lambda.lambda_function_arn

  # Without a dead letter queue these retries are the only thing standing between
  # a throttled delivery and a silently dropped Control Tower event. Reserved
  # concurrency is 1, so bursts do get deferred and need the retry window.
  retry_policy {
    maximum_retry_attempts       = var.eventbridge_maximum_retry_attempts
    maximum_event_age_in_seconds = var.eventbridge_maximum_event_age_in_seconds
  }
}

# -----------------------------------------------------------------------------
# Reconciliation schedule
#
# The event list above is a moving target: Control Tower has added lifecycle
# events over time, and the baseline events were missing here until 4.0.0. A
# missed event is silent, because the function simply never runs. Nothing fails,
# so the Errors alarm cannot help, and an unchanged `terraform apply` will not
# re-invoke either, since the apply-time trigger keys on the source code hash.
#
# This schedule bounds that exposure without needing to predict the event list.
# put_configuration_recorder is idempotent, so re-applying the same settings is a
# no-op beyond the API calls.
# -----------------------------------------------------------------------------

resource "aws_cloudwatch_event_rule" "reconciliation" {
  count = local.reconciliation_enabled ? 1 : 0

  name                = "${local.function_name}-reconciliation"
  description         = "Periodically re-apply Config Recorder settings so a missed Control Tower event cannot leave them reverted indefinitely"
  schedule_expression = var.reconciliation_schedule_expression

  tags = var.tags
}

resource "aws_cloudwatch_event_target" "reconciliation" {
  count = local.reconciliation_enabled ? 1 : 0

  rule = aws_cloudwatch_event_rule.reconciliation[0].name
  arn  = module.lambda.lambda_function_arn

  # Same event the apply-time invocation sends, which the function reads as its
  # default action.
  input = jsonencode({ action = "apply" })
}

# -----------------------------------------------------------------------------
# Error alarm
#
# The function raises when any account fails to update, which increments the
# Lambda Errors metric. That makes this alarm the primary failure signal, since
# the apply-time invocation is asynchronous and Terraform never sees the result.
# -----------------------------------------------------------------------------

resource "aws_cloudwatch_metric_alarm" "lambda_errors" {
  count = var.create_error_alarm ? 1 : 0

  alarm_name        = "${local.function_name}-errors"
  alarm_description = "Config Recorder override Lambda reported an error. One or more accounts may not have been updated - check the function logs for the failing account IDs."

  namespace   = "AWS/Lambda"
  metric_name = "Errors"
  statistic   = "Sum"

  comparison_operator = "GreaterThanOrEqualToThreshold"
  threshold           = 1
  period              = 300
  evaluation_periods  = 1

  # A period with no invocations reports no data rather than zero, which should
  # not read as a problem.
  treat_missing_data = "notBreaching"

  dimensions = {
    FunctionName = module.lambda.lambda_function_name
  }

  alarm_actions = var.alarm_actions
  ok_actions    = var.alarm_actions

  tags = var.tags
}

# -----------------------------------------------------------------------------
# Invoke the Lambda on apply so existing accounts pick up configuration changes
# without waiting for the next Control Tower lifecycle event.
#
# The command is kept to a single portable invocation with no shell builtins,
# no quoting and no temporary file path, so it runs under both POSIX shells and
# cmd.exe. The payload is omitted deliberately: an empty event makes the function
# fall through to its default 'apply' action.
# -----------------------------------------------------------------------------

resource "terraform_data" "invoke_lambda" {
  count = var.invoke_on_apply ? 1 : 0

  # Re-trigger whenever the Lambda code or environment changes
  triggers_replace = [
    module.lambda.lambda_function_source_code_hash,
    module.lambda.lambda_function_version,
  ]

  provisioner "local-exec" {
    command = join(" ", [
      "aws lambda invoke",
      "--function-name ${module.lambda.lambda_function_name}",
      "--region ${data.aws_region.current.region}",
      "--invocation-type Event",
      "${path.module}/.lambda-invoke-response.json",
    ])
  }
}
