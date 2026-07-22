data "aws_region" "current" {}

data "aws_caller_identity" "current" {}

data "aws_partition" "current" {}

locals {
  # Format excluded/included accounts as Python list string for the Lambda env var
  excluded_accounts_str = "[${join(", ", [for a in var.excluded_accounts : "'${a}'"])}]"
  included_accounts_str = "[${join(", ", [for a in var.included_accounts : "'${a}'"])}]"
}

# -----------------------------------------------------------------------------
# Lambda deployment package
# -----------------------------------------------------------------------------

data "archive_file" "lambda_zip" {
  type        = "zip"
  source_file = "${path.module}/ct_configrecorder_override.py"
  output_path = "${path.module}/ct_configrecorder_override.zip"
}

# -----------------------------------------------------------------------------
# IAM Role for the Lambda function
# -----------------------------------------------------------------------------

data "aws_iam_policy_document" "lambda_assume_role" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

data "aws_iam_policy_document" "lambda_policy" {
  statement {
    effect  = "Allow"
    actions = ["cloudformation:ListStackInstances"]
    resources = [
      "arn:${data.aws_partition.current.partition}:cloudformation:*:*:stackset/AWSControlTowerBP-BASELINE-CONFIG:*"
    ]
  }

  statement {
    effect    = "Allow"
    actions   = ["sts:AssumeRole"]
    resources = ["*"]
  }
}

resource "aws_iam_role" "lambda" {
  name               = "ct-config-recorder-override-lambda"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume_role.json
}

resource "aws_iam_role_policy" "lambda" {
  name   = "ct-config-recorder-override"
  role   = aws_iam_role.lambda.id
  policy = data.aws_iam_policy_document.lambda_policy.json
}

resource "aws_iam_role_policy_attachment" "lambda_basic_execution" {
  role       = aws_iam_role.lambda.name
  policy_arn = "arn:${data.aws_partition.current.partition}:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

# -----------------------------------------------------------------------------
# Lambda Function
# -----------------------------------------------------------------------------

resource "aws_lambda_function" "config_recorder_override" {
  function_name    = "ct-config-recorder-override"
  filename         = data.archive_file.lambda_zip.output_path
  source_code_hash = data.archive_file.lambda_zip.output_base64sha256
  handler          = "ct_configrecorder_override.lambda_handler"
  role             = aws_iam_role.lambda.arn
  runtime          = "python3.12"
  memory_size      = 256
  timeout          = 900
  architectures    = ["x86_64"]

  reserved_concurrent_executions = 1

  environment {
    variables = {
      ACCOUNT_SELECTION_MODE                              = var.account_selection_mode
      EXCLUDED_ACCOUNTS                                   = local.excluded_accounts_str
      INCLUDED_ACCOUNTS                                   = local.included_accounts_str
      LOG_LEVEL                                           = "INFO"
      CONFIG_RECORDER_STRATEGY                            = var.config_recorder_strategy
      CONFIG_RECORDER_OVERRIDE_DAILY_RESOURCE_LIST        = var.config_recorder_daily_resource_types
      CONFIG_RECORDER_OVERRIDE_DAILY_GLOBAL_RESOURCE_LIST = var.config_recorder_daily_global_resource_types
      CONFIG_RECORDER_OVERRIDE_EXCLUDED_RESOURCE_LIST     = var.config_recorder_excluded_resource_types
      CONFIG_RECORDER_OVERRIDE_INCLUDED_RESOURCE_LIST     = var.config_recorder_included_resource_types
      CONFIG_RECORDER_DEFAULT_RECORDING_FREQUENCY         = var.config_recorder_default_recording_frequency
      CONTROL_TOWER_HOME_REGION                           = var.aws_region
    }
  }
}

# -----------------------------------------------------------------------------
# EventBridge Rule — triggers on Control Tower lifecycle events
# -----------------------------------------------------------------------------

resource "aws_cloudwatch_event_rule" "control_tower" {
  name        = "ct-config-recorder-override-trigger"
  description = "Rule to trigger config recorder override lambda"

  event_pattern = jsonencode({
    source      = ["aws.controltower"]
    detail-type = ["AWS Service Event via CloudTrail"]
    detail = {
      eventName = ["UpdateLandingZone", "CreateManagedAccount", "UpdateManagedAccount", "ResetLandingZone"]
    }
  })
}

resource "aws_cloudwatch_event_target" "lambda" {
  rule = aws_cloudwatch_event_rule.control_tower.name
  arn  = aws_lambda_function.config_recorder_override.arn
}

resource "aws_lambda_permission" "eventbridge" {
  statement_id  = "AllowEventBridgeInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.config_recorder_override.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.control_tower.arn
}

# -----------------------------------------------------------------------------
# Invoke the Lambda on every apply to process all accounts
# This replaces the CloudFormation Custom Resource trigger
# -----------------------------------------------------------------------------

resource "terraform_data" "invoke_lambda" {
  # Re-trigger whenever the Lambda code or environment changes
  triggers_replace = [
    aws_lambda_function.config_recorder_override.source_code_hash,
    aws_lambda_function.config_recorder_override.environment,
  ]

  provisioner "local-exec" {
    command = <<-EOT
      echo "Waiting for any in-flight Lambda execution to complete..."
      MAX_ATTEMPTS=6
      ATTEMPT=0
      while [ $ATTEMPT -lt $MAX_ATTEMPTS ]; do
        RESULT=$(aws lambda invoke \
          --function-name ${aws_lambda_function.config_recorder_override.function_name} \
          --region ${var.aws_region} \
          --invocation-type Event \
          --payload '${jsonencode({ action = "apply" })}' \
          --cli-binary-format raw-in-base64-out \
          /tmp/lambda_response.json 2>&1) && break
        if echo "$RESULT" | grep -q "TooManyRequestsException"; then
          ATTEMPT=$((ATTEMPT + 1))
          echo "Lambda is still running (attempt $ATTEMPT/$MAX_ATTEMPTS). Waiting 150s..."
          sleep 150
        else
          echo "$RESULT" >&2
          exit 1
        fi
      done
      if [ $ATTEMPT -ge $MAX_ATTEMPTS ]; then
        echo "ERROR: Lambda did not become available after $MAX_ATTEMPTS attempts" >&2
        exit 1
      fi
      cat /tmp/lambda_response.json
    EOT
  }
}
