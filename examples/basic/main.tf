provider "aws" {
  # Apply this in the Control Tower home region. The module creates its resources
  # in whichever region this provider targets.
  region = "us-east-1"
}

module "config_recorder_override" {
  source  = "fivexl/control-tower-config-recorder/aws"
  version = "~> 4.0"

  account_selection_mode = "EXCLUSION"

  # Replace these with your real Log Archive and Audit account IDs. Anything not
  # listed here has its Config Recorder rewritten, so an empty list is rejected at
  # plan time. The management account is skipped automatically.
  excluded_accounts = ["222222222222", "333333333333"]

  config_recorder_strategy                     = "EXCLUSION"
  config_recorder_excluded_resource_types      = "AWS::HealthLake::FHIRDatastore,AWS::Pinpoint::Segment,AWS::Pinpoint::ApplicationSettings"
  config_recorder_default_recording_frequency  = "CONTINUOUS"
  config_recorder_override_recording_frequency = "DAILY"
  config_recorder_daily_resource_types         = "AWS::AutoScaling::AutoScalingGroup,AWS::AutoScaling::LaunchConfiguration"

  # Global IAM types are recorded in the Control Tower home region only, so this
  # list is applied there and ignored in the other governed regions.
  config_recorder_daily_global_resource_types = "AWS::IAM::Policy,AWS::IAM::User,AWS::IAM::Role,AWS::IAM::Group"

  tags = {
    Terraform = "true"
    Module    = "control-tower-config-recorder"
  }
}

output "lambda_function_arn" {
  description = "ARN of the Config Recorder override Lambda function"
  value       = module.config_recorder_override.lambda_function_arn
}

output "lambda_role_arn" {
  description = "ARN of the IAM role created for the Lambda function"
  value       = module.config_recorder_override.lambda_role_arn
}

output "eventbridge_rule_arn" {
  description = "ARN of the EventBridge rule that triggers the Lambda"
  value       = module.config_recorder_override.eventbridge_rule_arn
}

output "error_alarm_arn" {
  description = "ARN of the CloudWatch alarm on the Lambda Errors metric"
  value       = module.config_recorder_override.error_alarm_arn
}
