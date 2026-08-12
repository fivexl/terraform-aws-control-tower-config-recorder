provider "aws" {
  # Apply this in the Control Tower home region. The module creates its resources
  # in whichever region this provider targets.
  region = "us-east-1"
}

module "config_recorder_override" {
  source  = "fivexl/control-tower-config-recorder/aws"
  version = "~> 4.0"

  # Inclusion mode only touches the accounts listed below, which makes it the
  # safer choice for a first rollout or for testing against a single account.
  account_selection_mode = "INCLUSION"
  included_accounts      = ["123456789012", "234567890123"]

  config_recorder_strategy                = "INCLUSION"
  config_recorder_included_resource_types = "AWS::IAM::Role,AWS::IAM::Policy,AWS::S3::Bucket,AWS::KMS::Key"

  # Record everything on the list above once every 24 hours. The override lists are
  # empty because there is nothing here that needs a different cadence.
  config_recorder_default_recording_frequency  = "DAILY"
  config_recorder_override_recording_frequency = "DAILY"
  config_recorder_daily_resource_types         = ""
  config_recorder_daily_global_resource_types  = ""

  cloudwatch_logs_retention_in_days = 30
  log_level                         = "DEBUG"

  tags = {
    Terraform = "true"
    Module    = "control-tower-config-recorder"
  }
}

output "lambda_function_arn" {
  description = "ARN of the Config Recorder override Lambda function"
  value       = module.config_recorder_override.lambda_function_arn
}

output "control_tower_home_region" {
  description = "Region treated as the Control Tower home region"
  value       = module.config_recorder_override.control_tower_home_region
}
