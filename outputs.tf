output "lambda_function_name" {
  description = "Name of the Config Recorder override Lambda function"
  value       = module.lambda.lambda_function_name
}

output "lambda_function_arn" {
  description = "ARN of the Config Recorder override Lambda function"
  value       = module.lambda.lambda_function_arn
}

output "lambda_role_arn" {
  description = "ARN of the IAM role created for the Lambda function"
  value       = module.lambda.lambda_role_arn
}

output "eventbridge_rule_arn" {
  description = "ARN of the EventBridge rule that triggers the Lambda on Control Tower lifecycle events"
  value       = aws_cloudwatch_event_rule.control_tower.arn
}

output "reconciliation_rule_arn" {
  description = "ARN of the EventBridge schedule that periodically re-applies Config Recorder settings, or null when reconciliation_schedule_expression is null"
  value       = one(aws_cloudwatch_event_rule.reconciliation[*].arn)
}

output "error_alarm_arn" {
  description = "ARN of the CloudWatch alarm on the Lambda Errors metric, or null when create_error_alarm is false"
  value       = try(aws_cloudwatch_metric_alarm.lambda_errors[0].arn, null)
}

output "control_tower_home_region" {
  description = "Region treated as the Control Tower home region"
  value       = local.control_tower_home_region
}

output "global_iam_recording_region" {
  description = "Region that records the global IAM resource types, or an empty string when no region records them"
  value       = local.global_iam_recording_region
}
