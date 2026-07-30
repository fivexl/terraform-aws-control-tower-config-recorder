output "producer_lambda_function_name" {
  description = "Name of the Producer Lambda function"
  value       = aws_lambda_function.producer.function_name
}

output "producer_lambda_function_arn" {
  description = "ARN of the Producer Lambda function"
  value       = aws_lambda_function.producer.arn
}

output "consumer_lambda_function_name" {
  description = "Name of the Consumer Lambda function"
  value       = aws_lambda_function.consumer.function_name
}

output "consumer_lambda_function_arn" {
  description = "ARN of the Consumer Lambda function"
  value       = aws_lambda_function.consumer.arn
}

output "sqs_queue_url" {
  description = "URL of the SQS queue used for Producer -> Consumer communication"
  value       = aws_sqs_queue.config_recorder.url
}

output "eventbridge_rule_arn" {
  description = "ARN of the EventBridge rule that triggers the Producer Lambda"
  value       = aws_cloudwatch_event_rule.control_tower.arn
}

