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
  description = "ARN of the EventBridge rule that triggers the Lambda"
  value       = aws_cloudwatch_event_rule.control_tower.arn
}
