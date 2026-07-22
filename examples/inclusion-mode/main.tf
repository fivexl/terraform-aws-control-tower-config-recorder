provider "aws" {
  region = "us-east-1"
}

module "config_recorder_override" {
  source = "fivexl/control-tower-config-recorder/aws"

  aws_region             = "us-east-1"
  account_selection_mode = "INCLUSION"
  included_accounts      = ["123456789012", "234567890123"]

  config_recorder_strategy                    = "INCLUSION"
  config_recorder_included_resource_types     = "AWS::IAM::Role,AWS::IAM::Policy,AWS::S3::Bucket,AWS::KMS::Key"
  config_recorder_default_recording_frequency = "DAILY"
  config_recorder_daily_resource_types        = "AWS::IAM::Role,AWS::IAM::Policy"
  config_recorder_daily_global_resource_types = "AWS::IAM::Policy,AWS::IAM::User,AWS::IAM::Role,AWS::IAM::Group"
}

output "lambda_arn" {
  value = module.config_recorder_override.lambda_function_arn
}
