# Customize AWS Config Resource Tracking in AWS Control Tower

This solution customizes AWS Config Recorder settings across child accounts managed by AWS Control Tower. It overrides the default Config Recorder configuration to control which resource types are recorded, at what frequency, and in which accounts.

## Acknowledgments

This project is based on [aws-samples/aws-control-tower-config-customization](https://github.com/aws-samples/aws-control-tower-config-customization). We thank the original contributors for their work on the CloudFormation-based solution that inspired this Terraform module.

Originally based on the AWS blog post: https://aws.amazon.com/blogs/mt/customize-aws-config-resource-tracking-in-aws-control-tower-environment/

## Architecture

The solution uses a fan-out pattern for scalable processing across many accounts:

- **Producer Lambda** — Triggered by EventBridge on Control Tower lifecycle events. Lists accounts from the `AWSControlTowerBP-BASELINE-CONFIG` StackSet, filters them based on account selection mode, and sends an SQS message per account/region pair.
- **SQS Queue** — Buffers messages between Producer and Consumer with a 5s delay and 180s visibility timeout. Encrypted with AWS-managed KMS key.
- **Consumer Lambda** — Triggered by SQS event source mapping (batch size 1, concurrency 10). Assumes the `AWSControlTowerExecution` role into each target account and updates the Config Recorder.
- **EventBridge Rule** — Triggers the Producer on Control Tower events: CreateManagedAccount, UpdateManagedAccount, UpdateLandingZone, ResetLandingZone.
- **terraform_data resource** — Invokes the Producer on every `terraform apply` when code or configuration changes.

This fan-out design handles 100+ accounts well because Consumer invocations run in parallel (up to 10 concurrent), with SQS handling retries automatically.

## Prerequisites

- Terraform >= 1.5.0
- AWS CLI configured with credentials for the Control Tower management account

## Usage

```bash
terraform init
terraform plan
terraform apply
```

### Using as a Module

To use this as a Terraform module from another repository:

```hcl
module "config_recorder_override" {
  source = "git::https://github.com/fivexl/terraform-aws-control-tower-config-recorder.git?ref=original-arch-no-copy-lambda"

  aws_region             = "us-east-1"
  account_selection_mode = "EXCLUSION"
  excluded_accounts      = ["111111111111", "222222222222", "333333333333"]

  config_recorder_strategy                     = "EXCLUSION"
  config_recorder_excluded_resource_types      = "AWS::HealthLake::FHIRDatastore,AWS::Pinpoint::Segment,AWS::Pinpoint::ApplicationSettings"
  config_recorder_default_recording_frequency  = "CONTINUOUS"
  config_recorder_daily_resource_types         = "AWS::AutoScaling::AutoScalingGroup,AWS::AutoScaling::LaunchConfiguration"
  config_recorder_daily_global_resource_types  = "AWS::IAM::Policy,AWS::IAM::User,AWS::IAM::Role,AWS::IAM::Group"
}

output "producer_lambda_arn" {
  value = module.config_recorder_override.producer_lambda_function_arn
}

output "consumer_lambda_arn" {
  value = module.config_recorder_override.consumer_lambda_function_arn
}
```

### Example terraform.tfvars

```hcl
aws_region = "us-east-1"

account_selection_mode = "EXCLUSION"
excluded_accounts      = ["111111111111", "222222222222", "333333333333"]

config_recorder_strategy                     = "EXCLUSION"
config_recorder_excluded_resource_types      = "AWS::HealthLake::FHIRDatastore,AWS::Pinpoint::Segment,AWS::Pinpoint::ApplicationSettings"
config_recorder_default_recording_frequency  = "CONTINUOUS"
config_recorder_daily_resource_types         = "AWS::AutoScaling::AutoScalingGroup,AWS::AutoScaling::LaunchConfiguration"
config_recorder_daily_global_resource_types  = "AWS::IAM::Policy,AWS::IAM::User,AWS::IAM::Role,AWS::IAM::Group"
```

## Variables

### Account Selection

| Variable | Description | Default |
|----------|-------------|---------|
| `aws_region` | AWS region to deploy in | `us-east-1` |
| `account_selection_mode` | `EXCLUSION` or `INCLUSION` | `EXCLUSION` |
| `excluded_accounts` | List of account IDs to skip (EXCLUSION mode) | `["111111111111", "222222222222", "333333333333"]` |
| `included_accounts` | List of account IDs to process (INCLUSION mode) | `[]` |

### Recording Strategy

| Variable | Description | Default |
|----------|-------------|---------|
| `config_recorder_strategy` | `EXCLUSION` or `INCLUSION` for resource types | `EXCLUSION` |
| `config_recorder_excluded_resource_types` | Comma-separated resource types to exclude | `AWS::HealthLake::FHIRDatastore,...` |
| `config_recorder_included_resource_types` | Comma-separated resource types to include | `AWS::S3::Bucket,AWS::CloudTrail::Trail` |

### Recording Frequency

| Variable | Description | Default |
|----------|-------------|---------|
| `config_recorder_default_recording_frequency` | `CONTINUOUS` or `DAILY` | `CONTINUOUS` |
| `config_recorder_daily_resource_types` | Resource types recorded daily | `AWS::AutoScaling::AutoScalingGroup,...` |
| `config_recorder_daily_global_resource_types` | Global resource types recorded daily in home region | `AWS::IAM::Policy,AWS::IAM::User,...` |

## Account Selection Modes

### EXCLUSION Mode (Default)

Applies Config Recorder changes to all Control Tower managed accounts except those in `excluded_accounts`. Include your Management, Log Archive, and Audit accounts at minimum.

### INCLUSION Mode

Applies changes only to accounts listed in `included_accounts`. Useful for testing or targeting specific workload accounts.

### Important Warning

Regardless of mode, you should typically NOT customize the following accounts:
- **Management Account** — Control Tower management account
- **Log Archive Account** — centralized logging
- **Audit Account** — security audit

These have special roles in Control Tower governance and should maintain default Config Recorder settings.

## File Structure

```
.
├── main.tf                                    # Resources (SQS, IAM, Lambda, EventBridge)
├── variables.tf                               # Input variables
├── outputs.tf                                 # Outputs
├── versions.tf                                # Provider version constraints
├── ct_configrecorder_override_producer.py     # Producer Lambda source code
├── ct_configrecorder_override_consumer.py     # Consumer Lambda source code
├── .gitignore                                 # Terraform and OS exclusions
├── CHANGELOG.md                               # Version history
└── README.md                                  # This file
```

## How It Works

1. **On `terraform apply`**: The Producer Lambda is invoked via `local-exec`. It lists all accounts from the Control Tower StackSet, filters them based on the selection mode, and sends an SQS message for each account/region pair.

2. **On Control Tower events**: EventBridge triggers the Producer Lambda when accounts are created/updated or the landing zone is updated/reset.

3. **SQS fan-out**: Each SQS message triggers a Consumer Lambda invocation (up to 10 in parallel). The Consumer assumes the `AWSControlTowerExecution` role in the target account and updates the Config Recorder.

4. **Retry handling**: If a Consumer invocation fails, SQS automatically retries the message after the visibility timeout expires.

## Examples

### Exclude high-volume resource types

```hcl
config_recorder_strategy                = "EXCLUSION"
config_recorder_excluded_resource_types = "AWS::EC2::NetworkInterface,AWS::EC2::Volume,AWS::Lambda::Function"
```

### Only track security-critical resources

```hcl
config_recorder_strategy                = "INCLUSION"
config_recorder_included_resource_types = "AWS::IAM::Role,AWS::IAM::Policy,AWS::S3::Bucket,AWS::KMS::Key"
config_recorder_default_recording_frequency = "DAILY"
```

### Target specific workload accounts

```hcl
account_selection_mode = "INCLUSION"
included_accounts      = ["123456789012", "234567890123", "345678901234"]
```

## Destroying

Running `terraform destroy` will remove all resources including the Lambda functions, SQS queue, and EventBridge rule. It does NOT reset Config Recorders in child accounts to their defaults. If you need to reset them, invoke the Producer Lambda manually with a `"Delete"` action before destroying:

```bash
aws lambda invoke \
  --function-name ct-config-recorder-override-producer \
  --payload '{"action": "Delete"}' \
  --cli-binary-format raw-in-base64-out \
  /tmp/response.json
```
