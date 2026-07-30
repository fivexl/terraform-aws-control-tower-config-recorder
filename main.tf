data "aws_region" "current" {}

data "aws_caller_identity" "current" {}

data "aws_partition" "current" {}

locals {
  # Format excluded/included accounts as Python list string for the Lambda env var
  excluded_accounts_str = "[${join(", ", [for a in var.excluded_accounts : "'${a}'"])}]"
  included_accounts_str = "[${join(", ", [for a in var.included_accounts : "'${a}'"])}]"
}

# -----------------------------------------------------------------------------
# Lambda deployment packages
# -----------------------------------------------------------------------------

data "archive_file" "producer_zip" {
  type        = "zip"
  source_file = "${path.module}/ct_configrecorder_override_producer.py"
  output_path = "${path.module}/ct_configrecorder_override_producer.zip"
}

data "archive_file" "consumer_zip" {
  type        = "zip"
  source_file = "${path.module}/ct_configrecorder_override_consumer.py"
  output_path = "${path.module}/ct_configrecorder_override_consumer.zip"
}

# -----------------------------------------------------------------------------
# SQS Queue for Producer -> Consumer communication
# -----------------------------------------------------------------------------

resource "aws_sqs_queue" "config_recorder" {
  name                       = "ct-config-recorder-override"
  visibility_timeout_seconds = 180
  delay_seconds              = 5
  kms_master_key_id          = "alias/aws/sqs"
}

# -----------------------------------------------------------------------------
# IAM Role for Producer Lambda
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

data "aws_iam_policy_document" "producer_policy" {
  statement {
    effect  = "Allow"
    actions = ["cloudformation:ListStackInstances"]
    resources = [
      "arn:${data.aws_partition.current.partition}:cloudformation:*:*:stackset/AWSControlTowerBP-BASELINE-CONFIG:*"
    ]
  }

  statement {
    effect = "Allow"
    actions = [
      "sqs:DeleteMessage",
      "sqs:ReceiveMessage",
      "sqs:SendMessage",
      "sqs:GetQueueAttributes",
    ]
    resources = [aws_sqs_queue.config_recorder.arn]
  }
}

resource "aws_iam_role" "producer_lambda" {
  name               = "ct-config-recorder-producer-lambda"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume_role.json
}

resource "aws_iam_role_policy" "producer_lambda" {
  name   = "ct-cro-producer"
  role   = aws_iam_role.producer_lambda.id
  policy = data.aws_iam_policy_document.producer_policy.json
}

resource "aws_iam_role_policy_attachment" "producer_basic_execution" {
  role       = aws_iam_role.producer_lambda.name
  policy_arn = "arn:${data.aws_partition.current.partition}:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

# -----------------------------------------------------------------------------
# IAM Role for Consumer Lambda
# -----------------------------------------------------------------------------

data "aws_iam_policy_document" "consumer_policy" {
  statement {
    effect    = "Allow"
    actions   = ["sts:AssumeRole"]
    resources = ["*"]
  }

  statement {
    effect = "Allow"
    actions = [
      "sqs:DeleteMessage",
      "sqs:ReceiveMessage",
      "sqs:SendMessage",
      "sqs:GetQueueAttributes",
    ]
    resources = [aws_sqs_queue.config_recorder.arn]
  }
}

resource "aws_iam_role" "consumer_lambda" {
  name               = "ct-config-recorder-consumer-lambda"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume_role.json
}

resource "aws_iam_role_policy" "consumer_lambda" {
  name   = "ct-cro-consumer"
  role   = aws_iam_role.consumer_lambda.id
  policy = data.aws_iam_policy_document.consumer_policy.json
}

resource "aws_iam_role_policy_attachment" "consumer_basic_execution" {
  role       = aws_iam_role.consumer_lambda.name
  policy_arn = "arn:${data.aws_partition.current.partition}:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

# -----------------------------------------------------------------------------
# Producer Lambda Function
# -----------------------------------------------------------------------------

resource "aws_lambda_function" "producer" {
  function_name    = "ct-config-recorder-override-producer"
  filename         = data.archive_file.producer_zip.output_path
  source_code_hash = data.archive_file.producer_zip.output_base64sha256
  handler          = "ct_configrecorder_override_producer.lambda_handler"
  role             = aws_iam_role.producer_lambda.arn
  runtime          = "python3.12"
  memory_size      = 128
  timeout          = 300
  architectures    = ["x86_64"]

  reserved_concurrent_executions = 1

  environment {
    variables = {
      ACCOUNT_SELECTION_MODE = var.account_selection_mode
      EXCLUDED_ACCOUNTS      = local.excluded_accounts_str
      INCLUDED_ACCOUNTS      = local.included_accounts_str
      LOG_LEVEL              = "INFO"
      SQS_URL                = aws_sqs_queue.config_recorder.url
    }
  }
}

# -----------------------------------------------------------------------------
# Consumer Lambda Function
# -----------------------------------------------------------------------------

resource "aws_lambda_function" "consumer" {
  function_name    = "ct-config-recorder-override-consumer"
  filename         = data.archive_file.consumer_zip.output_path
  source_code_hash = data.archive_file.consumer_zip.output_base64sha256
  handler          = "ct_configrecorder_override_consumer.lambda_handler"
  role             = aws_iam_role.consumer_lambda.arn
  runtime          = "python3.12"
  memory_size      = 128
  timeout          = 180
  architectures    = ["x86_64"]

  reserved_concurrent_executions = 10

  environment {
    variables = {
      LOG_LEVEL                                    = "INFO"
      CONFIG_RECORDER_STRATEGY                     = var.config_recorder_strategy
      CONFIG_RECORDER_OVERRIDE_DAILY_RESOURCE_LIST = var.config_recorder_daily_resource_types
      CONFIG_RECORDER_OVERRIDE_DAILY_GLOBAL_RESOURCE_LIST = var.config_recorder_daily_global_resource_types
      CONFIG_RECORDER_OVERRIDE_EXCLUDED_RESOURCE_LIST     = var.config_recorder_excluded_resource_types
      CONFIG_RECORDER_OVERRIDE_INCLUDED_RESOURCE_LIST     = var.config_recorder_included_resource_types
      CONFIG_RECORDER_DEFAULT_RECORDING_FREQUENCY         = var.config_recorder_default_recording_frequency
      CONTROL_TOWER_HOME_REGION                           = var.aws_region
    }
  }
}

# -----------------------------------------------------------------------------
# SQS -> Consumer Lambda event source mapping
# -----------------------------------------------------------------------------

resource "aws_lambda_event_source_mapping" "consumer_sqs" {
  event_source_arn = aws_sqs_queue.config_recorder.arn
  function_name    = aws_lambda_function.consumer.arn
  batch_size       = 1
  enabled          = true
}

# -----------------------------------------------------------------------------
# EventBridge Rule — triggers Producer on Control Tower lifecycle events
# -----------------------------------------------------------------------------

resource "aws_cloudwatch_event_rule" "control_tower" {
  name        = "ct-config-recorder-override-trigger"
  description = "Rule to trigger config recorder override producer lambda"

  event_pattern = jsonencode({
    source      = ["aws.controltower"]
    detail-type = ["AWS Service Event via CloudTrail"]
    detail = {
      eventName = ["UpdateLandingZone", "CreateManagedAccount", "UpdateManagedAccount", "ResetLandingZone"]
    }
  })
}

resource "aws_cloudwatch_event_target" "producer_lambda" {
  rule = aws_cloudwatch_event_rule.control_tower.name
  arn  = aws_lambda_function.producer.arn
}

resource "aws_lambda_permission" "eventbridge" {
  statement_id  = "AllowEventBridgeInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.producer.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.control_tower.arn
}

# -----------------------------------------------------------------------------
# Invoke the Producer Lambda on every apply to process all accounts
# This replaces the CloudFormation Custom Resource trigger
# -----------------------------------------------------------------------------

resource "terraform_data" "invoke_producer" {
  # Re-trigger whenever Producer code or its environment changes
  triggers_replace = [
    aws_lambda_function.producer.source_code_hash,
    aws_lambda_function.producer.environment,
    aws_lambda_function.consumer.source_code_hash,
    aws_lambda_function.consumer.environment,
  ]

  provisioner "local-exec" {
    command = <<-EOT
      aws lambda invoke \
        --function-name ${aws_lambda_function.producer.function_name} \
        --region ${var.aws_region} \
        --payload '${jsonencode({ action = "apply" })}' \
        --cli-binary-format raw-in-base64-out \
        /tmp/lambda_producer_response.json && cat /tmp/lambda_producer_response.json
    EOT
  }
}
