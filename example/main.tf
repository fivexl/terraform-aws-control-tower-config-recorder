# ─────────────────────────────────────────────────────────────────────────────
# AWS Config Recorder Customization for Control Tower
# Uses the fivexl/terraform-aws-control-tower-config-recorder Terraform module
# to switch recording frequency to DAILY across all member accounts.
# Source:  "https://github.com/fivexl/terraform-aws-control-tower-config-recorder.git?ref=main"
# Blog:   https://aws.amazon.com/blogs/mt/customize-aws-config-resource-tracking-in-aws-control-tower-environment/
# IMPORTANT : IF THE ORGANISATION IS SMALL YOU CAN USE MAIN , IF IT IS EXPECTED TO GROW SWITCH TO original-arch-no-copy-lambda
# ─────────────────────────────────────────────────────────────────────────────



module "config_recorder_override" {
#  source = "git::https://github.com/fivexl/terraform-aws-control-tower-config-recorder.git?ref=main"
  source = "git::https://github.com/fivexl/terraform-aws-control-tower-config-recorder.git?ref=original-arch-no-copy-lambda"

  aws_region             = "us-east-1"
  account_selection_mode = "EXCLUSION"
  excluded_accounts      = ["111111111111", "222222222222", "333333333333"]

  # Recording strategy - record all resource types (exclusion with minimal exclusions)
  config_recorder_strategy                = "EXCLUSION"
  config_recorder_excluded_resource_types = "AWS::HealthLake::FHIRDatastore,AWS::Pinpoint::Segment,AWS::Pinpoint::ApplicationSettings"

  # Recording frequency - switch to DAILY for cost optimization
  config_recorder_default_recording_frequency = "DAILY"
  config_recorder_daily_resource_types        = "AWS::AutoScaling::AutoScalingGroup,AWS::AutoScaling::LaunchConfiguration"
  config_recorder_daily_global_resource_types = "AWS::IAM::Policy,AWS::IAM::User,AWS::IAM::Role,AWS::IAM::Group,AWS::Amplify::App,AWS::CleanRooms::PrivacyBudgetTemplate,AWS::CleanRoomsML::TrainingDataset,AWS::Cloud9::EnvironmentEC2"
}
