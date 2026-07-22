output "lambda_function_name" {
  description = "Name of the Config Recorder override Lambda function"
  value       = aws_lambda_function.config_recorder_override.function_name
}

output "lambda_function_arn" {
  description = "ARN of the Config Recorder override Lambda function"
  value       = aws_lambda_function.config_recorder_override.arn
}

output "eventbridge_rule_arn" {
  description = "ARN of the EventBridge rule that triggers the Lambda"
  value       = aws_cloudwatch_event_rule.control_tower.arn
}
