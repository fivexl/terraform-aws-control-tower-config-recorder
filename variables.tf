# -----------------------------------------------------------------------------
# Region
# -----------------------------------------------------------------------------

# NOTE: This module deliberately takes no region for resource placement.
# Resources are created in whichever region the calling provider targets, per
# Terraform Registry convention. The variable below only declares which region
# Control Tower calls home, because that is what decides where global resource
# types (IAM and friends) are recorded.
variable "control_tower_home_region" {
  description = "Region where Control Tower is deployed. Global resource types are only recorded in this region. Defaults to the region of the calling provider, which is correct when the module is applied in the Control Tower home region."
  type        = string
  default     = null
}

# -----------------------------------------------------------------------------
# Account selection
# -----------------------------------------------------------------------------

variable "account_selection_mode" {
  description = "Account selection mode - EXCLUSION (processes all accounts except those in excluded_accounts) or INCLUSION (processes only accounts in included_accounts)"
  type        = string
  default     = "EXCLUSION"

  validation {
    condition     = contains(["EXCLUSION", "INCLUSION"], var.account_selection_mode)
    error_message = "Must be EXCLUSION or INCLUSION."
  }
}

# WARNING: The default below is a set of placeholder account IDs, not a safe
# default. Those IDs match no real account, so leaving this unset in EXCLUSION
# mode means the module rewrites the Config Recorder in EVERY managed account,
# including Management, Log Archive, and Audit. Those three should normally keep
# their Control Tower defaults. Always override this with your real account IDs.
variable "excluded_accounts" {
  description = "List of AWS account IDs to exclude. Should contain Management, Log Archive, and Audit accounts at minimum. Only used when account_selection_mode is EXCLUSION. The default is placeholder IDs and must be overridden - see the warning in the README."
  type        = list(string)
  default     = ["111111111111", "222222222222", "333333333333"]
}

variable "included_accounts" {
  description = "List of AWS account IDs to include. Only used when account_selection_mode is INCLUSION."
  type        = list(string)
  default     = []
}

# -----------------------------------------------------------------------------
# Recording strategy
# -----------------------------------------------------------------------------

variable "config_recorder_strategy" {
  description = "Config Recorder strategy - EXCLUSION or INCLUSION"
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

# -----------------------------------------------------------------------------
# Lambda
# -----------------------------------------------------------------------------

variable "lambda_memory_size" {
  description = "Memory in MB allocated to the Lambda function. Peak usage grows with the number of account-region pairs processed in one run."
  type        = number
  default     = 1024
}

variable "cloudwatch_logs_retention_in_days" {
  description = "Number of days to retain Lambda CloudWatch log events"
  type        = number
  default     = 14
}

variable "log_level" {
  description = "Log level for the Lambda function. DEBUG is safe to enable: the AWS SDK loggers are pinned above DEBUG so temporary credentials are never written to CloudWatch Logs."
  type        = string
  default     = "INFO"

  validation {
    condition     = contains(["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"], var.log_level)
    error_message = "Must be one of DEBUG, INFO, WARNING, ERROR, CRITICAL."
  }
}

variable "invoke_on_apply" {
  description = "Invoke the Lambda on every terraform apply where the function code or configuration changed. Requires the AWS CLI on the machine running Terraform. Set to false to rely solely on Control Tower lifecycle events."
  type        = bool
  default     = true
}

# -----------------------------------------------------------------------------
# Failure handling
#
# This module intentionally uses only EventBridge and Lambda, so there is no
# dead letter queue. The failure signal is the Lambda Errors metric: the function
# raises when any account fails, which surfaces the failure on that metric and
# triggers the alarm below.
# -----------------------------------------------------------------------------

variable "lambda_maximum_retry_attempts" {
  description = "Number of times Lambda retries a failed asynchronous invocation (0-2). Updating the Config Recorder is idempotent, so retrying is safe."
  type        = number
  default     = 2

  validation {
    condition     = var.lambda_maximum_retry_attempts >= 0 && var.lambda_maximum_retry_attempts <= 2
    error_message = "Must be between 0 and 2, as required by Lambda asynchronous invocation configuration."
  }
}

variable "lambda_maximum_event_age_in_seconds" {
  description = "Maximum age of an asynchronous invocation request Lambda will still process (60-21600)."
  type        = number
  default     = 3600

  validation {
    condition     = var.lambda_maximum_event_age_in_seconds >= 60 && var.lambda_maximum_event_age_in_seconds <= 21600
    error_message = "Must be between 60 and 21600 seconds."
  }
}

variable "eventbridge_maximum_retry_attempts" {
  description = "Number of times EventBridge retries delivering a Control Tower event to the Lambda before discarding it."
  type        = number
  default     = 10
}

variable "eventbridge_maximum_event_age_in_seconds" {
  description = "Maximum age of a Control Tower event EventBridge will still attempt to deliver (60-86400)."
  type        = number
  default     = 3600

  validation {
    condition     = var.eventbridge_maximum_event_age_in_seconds >= 60 && var.eventbridge_maximum_event_age_in_seconds <= 86400
    error_message = "Must be between 60 and 86400 seconds."
  }
}

variable "create_error_alarm" {
  description = "Create a CloudWatch alarm on the Lambda Errors metric. The function raises on any per-account failure, so this alarm fires when one or more accounts could not be updated."
  type        = bool
  default     = true
}

variable "alarm_actions" {
  description = "List of ARNs (for example an SNS topic) to notify when the error alarm fires. An alarm with no actions still records state but notifies nobody."
  type        = list(string)
  default     = []
}

# -----------------------------------------------------------------------------
# Tagging
# -----------------------------------------------------------------------------

variable "tags" {
  description = "A map of tags to add to all resources created by this module"
  type        = map(string)
  default     = {}
}
