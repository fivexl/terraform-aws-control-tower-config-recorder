provider "aws" {
  region = "us-east-1"
}

module "config_recorder_override" {
  source  = "fivexl/control-tower-config-recorder/aws"
  version = "~> 2.0"

  aws_region             = "us-east-1"
  account_selection_mode = "EXCLUSION"
  excluded_accounts      = ["111111111111", "222222222222", "333333333333"]

  config_recorder_strategy                    = "EXCLUSION"
  config_recorder_excluded_resource_types     = "AWS::HealthLake::FHIRDatastore,AWS::Pinpoint::Segment,AWS::Pinpoint::ApplicationSettings"
  config_recorder_default_recording_frequency = "CONTINUOUS"
  config_recorder_daily_resource_types        = "AWS::AutoScaling::AutoScalingGroup,AWS::AutoScaling::LaunchConfiguration"
  config_recorder_daily_global_resource_types = "AWS::IAM::Policy,AWS::IAM::User,AWS::IAM::Role,AWS::IAM::Group"
}

output "lambda_arn" {
  value = module.config_recorder_override.lambda_function_arn
}

output "lambda_role_arn" {
  value = module.config_recorder_override.lambda_role_arn
}

output "eventbridge_rule_arn" {
  value = module.config_recorder_override.eventbridge_rule_arn
}
