data "aws_partition" "current" {}

data "aws_region" "current" {}

locals {
  # Where Control Tower lives, which decides where global resource types are
  # recorded. Falls back to the region this module is being applied in.
  control_tower_home_region = coalesce(var.control_tower_home_region, data.aws_region.current.region)

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
    CONTROL_TOWER_HOME_REGION                           = local.control_tower_home_region
  }

  attach_policy_json = true
  policy_json        = data.aws_iam_policy_document.lambda_policy.json

  allowed_triggers = {
    ControlTowerEvents = {
      principal  = "events.amazonaws.com"
      source_arn = aws_cloudwatch_event_rule.control_tower.arn
    }
  }

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

  event_pattern = jsonencode({
    source      = ["aws.controltower"]
    detail-type = ["AWS Service Event via CloudTrail"]
    detail = {
      eventName = ["UpdateLandingZone", "CreateManagedAccount", "UpdateManagedAccount", "ResetLandingZone"]
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
