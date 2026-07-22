# Customize AWS Config Resource Tracking in AWS Control Tower

This solution customizes AWS Config Recorder settings across child accounts managed by AWS Control Tower. It overrides the default Config Recorder configuration to control which resource types are recorded, at what frequency, and in which accounts.

## Acknowledgments

This project is based on [aws-samples/aws-control-tower-config-customization](https://github.com/aws-samples/aws-control-tower-config-customization). We thank the original contributors for their work on the CloudFormation-based solution that inspired this Terraform module.

Originally based on the AWS blog post: https://aws.amazon.com/blogs/mt/customize-aws-config-resource-tracking-in-aws-control-tower-environment/

## Architecture

The solution deploys:
- A single **Lambda function** that assumes the `AWSControlTowerExecution` role into each target account and updates the Config Recorder
- An **EventBridge rule** that triggers the Lambda on Control Tower lifecycle events (CreateManagedAccount, UpdateManagedAccount, UpdateLandingZone, ResetLandingZone)
- A **terraform_data resource** that invokes the Lambda on every `terraform apply` when code or configuration changes

The Lambda processes accounts sequentially with a small delay between each to avoid STS throttling. For organizations with fewer than ~50 accounts this works well. For larger organizations, consider re-introducing a fan-out pattern (e.g., SQS or Step Functions).

### Scaling Limits

Due to the sequential processing and 15-minute Lambda timeout, this module supports approximately **30 accounts with 2+ regions** (or ~200 account-region pairs total). Each iteration takes ~3-4 seconds (1s throttle delay + API call time). If your organization exceeds this limit, use the `original-arch-no-copy-lambda` [https://github.com/fivexl/terraform-aws-control-tower-config-recorder/tree/original-arch-no-copy-lambda] branch which uses an SQS fan-out pattern with parallel Consumer invocations.

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

To use this as a Terraform module from the registry:

```hcl
module "config_recorder_override" {
  source  = "fivexl/control-tower-config-recorder/aws"
  version = "~> 1.0"

  aws_region             = "us-east-1"
  account_selection_mode = "EXCLUSION"
  excluded_accounts      = ["111111111111", "222222222222", "333333333333"]

  config_recorder_strategy                     = "EXCLUSION"
  config_recorder_excluded_resource_types      = "AWS::HealthLake::FHIRDatastore,AWS::Pinpoint::Segment,AWS::Pinpoint::ApplicationSettings"
  config_recorder_default_recording_frequency  = "CONTINUOUS"
  config_recorder_daily_resource_types         = "AWS::AutoScaling::AutoScalingGroup,AWS::AutoScaling::LaunchConfiguration"
  config_recorder_daily_global_resource_types  = "AWS::IAM::Policy,AWS::IAM::User,AWS::IAM::Role,AWS::IAM::Group"
}

output "lambda_arn" {
  value = module.config_recorder_override.lambda_function_arn
}

output "eventbridge_rule_arn" {
  value = module.config_recorder_override.eventbridge_rule_arn
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
├── main.tf                            # Resources (Lambda, IAM, EventBridge)
├── variables.tf                       # Input variables
├── outputs.tf                         # Outputs
├── versions.tf                        # Provider version constraints
├── ct_configrecorder_override.py      # Lambda function source code
├── examples/
│   ├── basic/                         # Basic exclusion mode example
│   └── inclusion-mode/                # Inclusion mode example
├── .gitignore                         # Terraform and OS exclusions
├── CHANGELOG.md                       # Version history
└── README.md                          # This file
```

## How It Works

1. **On `terraform apply`**: The Lambda is invoked via `local-exec`, iterates all accounts from the `AWSControlTowerBP-BASELINE-CONFIG` StackSet, and applies the Config Recorder configuration.

2. **On Control Tower events**: EventBridge triggers the Lambda when accounts are created/updated or the landing zone is updated/reset. The Lambda processes the affected account(s).

3. **Per-account processing**: For each account, the Lambda assumes the `AWSControlTowerExecution` role, reads the existing Config Recorder, and updates it according to the configured strategy and resource type lists.

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

Running `terraform destroy` will remove the Lambda, IAM role, and EventBridge rule. It does NOT reset Config Recorders in child accounts to their defaults. If you need to reset them, invoke the Lambda manually with a `"Delete"` action before destroying:

```bash
aws lambda invoke \
  --function-name ct-config-recorder-override \
  --payload '{"action": "Delete"}' \
  --cli-binary-format raw-in-base64-out \
  /tmp/response.json
```
