variable "aws_region" {
  description = "AWS region to deploy resources in"
  type        = string
  default     = "us-east-1"
}

variable "account_selection_mode" {
  description = "Account selection mode - EXCLUSION (processes all accounts except those in excluded_accounts) or INCLUSION (processes only accounts in included_accounts)"
  type        = string
  default     = "EXCLUSION"

  validation {
    condition     = contains(["EXCLUSION", "INCLUSION"], var.account_selection_mode)
    error_message = "Must be EXCLUSION or INCLUSION."
  }
}

variable "excluded_accounts" {
  description = "List of AWS account IDs to exclude. Should contain Management, Log Archive, and Audit accounts at minimum. Only used when account_selection_mode is EXCLUSION."
  type        = list(string)
  default     = ["111111111111", "222222222222", "333333333333"]
}

variable "included_accounts" {
  description = "List of AWS account IDs to include. Only used when account_selection_mode is INCLUSION."
  type        = list(string)
  default     = []
}

variable "config_recorder_strategy" {
  description = "Config Recorder strategy - EXCLUSION or INCLUSION for resource types"
  type        = string
  default     = "EXCLUSION"

  validation {
    condition     = contains(["EXCLUSION", "INCLUSION"], var.config_recorder_strategy)
    error_message = "Must be EXCLUSION or INCLUSION."
  }
}

variable "config_recorder_excluded_resource_types" {
  description = "Comma-separated list of resource types to exclude from Config Recorder (used with EXCLUSION strategy)"
  type        = string
  default     = "AWS::HealthLake::FHIRDatastore,AWS::Pinpoint::Segment,AWS::Pinpoint::ApplicationSettings"
}

variable "config_recorder_included_resource_types" {
  description = "Comma-separated list of resource types to include in Config Recorder (used with INCLUSION strategy)"
  type        = string
  default     = "AWS::S3::Bucket,AWS::CloudTrail::Trail"
}

variable "config_recorder_daily_resource_types" {
  description = "Comma-separated list of resource types to record at daily cadence"
  type        = string
  default     = "AWS::AutoScaling::AutoScalingGroup,AWS::AutoScaling::LaunchConfiguration"
}

variable "config_recorder_daily_global_resource_types" {
  description = "Comma-separated list of global resource types to record daily in the Control Tower home region"
  type        = string
  default     = "AWS::IAM::Policy,AWS::IAM::User,AWS::IAM::Role,AWS::IAM::Group"
}

variable "config_recorder_default_recording_frequency" {
  description = "Default frequency of recording configuration changes"
  type        = string
  default     = "CONTINUOUS"

  validation {
    condition     = contains(["CONTINUOUS", "DAILY"], var.config_recorder_default_recording_frequency)
    error_message = "Must be CONTINUOUS or DAILY."
  }
}
