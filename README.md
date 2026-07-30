[![FivexL](https://releases.fivexl.io/like-this-repo-banner.png)](https://fivexl.io/#email-subscription)

### Want practical AWS infrastructure insights?

👉 [Subscribe to our newsletter](https://fivexl.io/#email-subscription) to get:

- Real stories from real AWS projects
- No-nonsense DevOps tactics
- Cost, security & compliance patterns that actually work
- Expert guidance from engineers in the field

=========================================================================

# Customize AWS Config Resource Tracking in AWS Control Tower

This solution customizes AWS Config Recorder settings across child accounts managed by AWS Control Tower. It overrides the default Config Recorder configuration to control which resource types are recorded, at what frequency, and in which accounts.

## Acknowledgments

This project is based on [aws-samples/aws-control-tower-config-customization](https://github.com/aws-samples/aws-control-tower-config-customization). We thank the original contributors for their work on the CloudFormation-based solution that inspired this Terraform module.

Originally based on the AWS blog post: https://aws.amazon.com/blogs/mt/customize-aws-config-resource-tracking-in-aws-control-tower-environment/

## Architecture

The solution deploys:
- A **Lambda function** (via [terraform-aws-modules/lambda/aws](https://registry.terraform.io/modules/terraform-aws-modules/lambda/aws)) that assumes the `AWSControlTowerExecution` role into each target account and updates the Config Recorder
- An **EventBridge rule** that triggers the Lambda on Control Tower lifecycle events (CreateManagedAccount, UpdateManagedAccount, UpdateLandingZone, ResetLandingZone)
- A **CloudWatch alarm** on the Lambda `Errors` metric, which is the primary failure signal
- A **terraform_data resource** that invokes the Lambda on apply when the function code or configuration changes

By design the module uses only EventBridge and Lambda. There is no queue in the architecture.

### Region behaviour

Resources are created in whichever region your provider targets, following Terraform Registry convention — the module takes no region for resource placement. Apply it in your Control Tower home region.

`control_tower_home_region` is a separate concern: it tells the function which region records global resource types (IAM and similar). It defaults to the provider's region, which is correct for the normal case. Only set it explicitly if you are deliberately applying the module outside the Control Tower home region.

### Scaling

The Lambda walks account-region pairs sequentially. Credentials are cached per account, so an account spanning three regions costs one `AssumeRole` call rather than three, and the one-second throttle delay is paid per account rather than per region.

For a rough sense of scale: each `AssumeRole` costs about a second, and each account-region pair costs one or two Config API calls on top. Within the 15-minute timeout that comfortably covers organizations in the low tens of accounts across a handful of regions. If you outgrow it, the [`original-arch-no-copy-lambda`](https://github.com/fivexl/terraform-aws-control-tower-config-recorder/tree/original-arch-no-copy-lambda) branch uses a fan-out pattern with parallel invocations.

### Failure handling

The apply-time invocation is asynchronous, so Terraform does not wait for the function and never sees its result. That means **a failed run will not fail your apply**. The signal is instead:

1. The function collects per-account failures, finishes the remaining accounts, then raises. One unreachable account does not stop the rest.
2. Raising increments the Lambda `Errors` metric.
3. The CloudWatch alarm on that metric fires. Wire `alarm_actions` to an SNS topic to be notified.

Set `create_error_alarm = false` if you monitor Lambda errors through some other mechanism. Note that with no alarm and no queue, a failure is only visible in the function logs.

## Prerequisites

- Terraform >= 1.5.0
- AWS provider >= 6.0
- Credentials for the Control Tower **management account**. The function calls `ListStackInstances` without `CallAs`, so running from a delegated administrator account is not supported.
- AWS CLI on the machine running Terraform, if `invoke_on_apply` is left enabled (the default). Set `invoke_on_apply = false` to remove that dependency and rely solely on Control Tower lifecycle events.

## Important: `excluded_accounts` must be overridden

The default value of `excluded_accounts` is a set of **placeholder** account IDs:

```hcl
default = ["111111111111", "222222222222", "333333333333"]
```

Those IDs match no real account. In `EXCLUSION` mode, leaving the default in place means the module rewrites the Config Recorder in **every** managed account, including Management, Log Archive, and Audit. Those three have special roles in Control Tower governance and should normally keep their default Config Recorder settings.

Always set this to your real account IDs. If you would rather start conservatively, use `INCLUSION` mode and name only the accounts you want to change.

## Usage

### Using as a Module

```hcl
provider "aws" {
  # Resources are created here. Apply in the Control Tower home region.
  region = "us-east-1"
}

module "config_recorder_override" {
  source  = "fivexl/control-tower-config-recorder/aws"
  version = "~> 3.0"

  account_selection_mode = "EXCLUSION"

  # Your real Management, Log Archive, and Audit account IDs.
  excluded_accounts = ["111111111111", "222222222222", "333333333333"]

  config_recorder_strategy                    = "EXCLUSION"
  config_recorder_excluded_resource_types     = "AWS::HealthLake::FHIRDatastore,AWS::Pinpoint::Segment,AWS::Pinpoint::ApplicationSettings"
  config_recorder_default_recording_frequency = "CONTINUOUS"
  config_recorder_daily_resource_types        = "AWS::AutoScaling::AutoScalingGroup,AWS::AutoScaling::LaunchConfiguration"
  config_recorder_daily_global_resource_types = "AWS::IAM::Policy,AWS::IAM::User,AWS::IAM::Role,AWS::IAM::Group"

  # Get told when an account fails to update.
  alarm_actions = [aws_sns_topic.alerts.arn]

  tags = {
    Terraform = "true"
  }
}

output "lambda_function_arn" {
  description = "ARN of the Config Recorder override Lambda function"
  value       = module.config_recorder_override.lambda_function_arn
}
```

See [`examples/basic`](examples/basic) and [`examples/inclusion-mode`](examples/inclusion-mode) for complete configurations.

### Upgrading from 2.x

`aws_region` was removed. It previously did two unrelated jobs: naming the region for resources, and naming the Control Tower home region. Since the module no longer declares a provider, resource placement comes from your provider configuration.

```diff
 module "config_recorder_override" {
-  aws_region = "us-east-1"
 }

+provider "aws" {
+  region = "us-east-1"
+}
```

If you apply the module somewhere other than the Control Tower home region, set `control_tower_home_region` explicitly. Otherwise nothing further is needed — it defaults to the provider's region.

Resource addresses also changed in 2.0.0 when the Lambda moved into `terraform-aws-modules/lambda/aws`. Upgrading from 1.x will replace the function unless you add `moved` blocks.

## Terraform specs

<!-- BEGIN_TF_DOCS -->
## Requirements

| Name | Version |
| ---- | ------- |
| <a name="requirement_terraform"></a> [terraform](#requirement\_terraform) | >= 1.5.0 |
| <a name="requirement_aws"></a> [aws](#requirement\_aws) | >= 6.0 |

## Providers

| Name | Version |
| ---- | ------- |
| <a name="provider_aws"></a> [aws](#provider\_aws) | 6.56.0 |
| <a name="provider_terraform"></a> [terraform](#provider\_terraform) | n/a |

## Modules

| Name | Source | Version |
| ---- | ------ | ------- |
| <a name="module_lambda"></a> [lambda](#module\_lambda) | terraform-aws-modules/lambda/aws | 8.2.1 |

## Resources

| Name | Type |
| ---- | ---- |
| [aws_cloudwatch_event_rule.control_tower](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/cloudwatch_event_rule) | resource |
| [aws_cloudwatch_event_target.lambda](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/cloudwatch_event_target) | resource |
| [aws_cloudwatch_metric_alarm.lambda_errors](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/cloudwatch_metric_alarm) | resource |
| [terraform_data.invoke_lambda](https://registry.terraform.io/providers/hashicorp/terraform/latest/docs/resources/data) | resource |
| [aws_iam_policy_document.lambda_policy](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/data-sources/iam_policy_document) | data source |
| [aws_partition.current](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/data-sources/partition) | data source |
| [aws_region.current](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/data-sources/region) | data source |

## Inputs

| Name | Description | Type | Default | Required |
| ---- | ----------- | ---- | ------- | :------: |
| <a name="input_account_selection_mode"></a> [account\_selection\_mode](#input\_account\_selection\_mode) | Account selection mode - EXCLUSION (processes all accounts except those in excluded\_accounts) or INCLUSION (processes only accounts in included\_accounts) | `string` | `"EXCLUSION"` | no |
| <a name="input_alarm_actions"></a> [alarm\_actions](#input\_alarm\_actions) | List of ARNs (for example an SNS topic) to notify when the error alarm fires. An alarm with no actions still records state but notifies nobody. | `list(string)` | `[]` | no |
| <a name="input_cloudwatch_logs_retention_in_days"></a> [cloudwatch\_logs\_retention\_in\_days](#input\_cloudwatch\_logs\_retention\_in\_days) | Number of days to retain Lambda CloudWatch log events | `number` | `14` | no |
| <a name="input_config_recorder_daily_global_resource_types"></a> [config\_recorder\_daily\_global\_resource\_types](#input\_config\_recorder\_daily\_global\_resource\_types) | Comma-separated list of global resource types to record daily in the Control Tower home region | `string` | `"AWS::IAM::Policy,AWS::IAM::User,AWS::IAM::Role,AWS::IAM::Group"` | no |
| <a name="input_config_recorder_daily_resource_types"></a> [config\_recorder\_daily\_resource\_types](#input\_config\_recorder\_daily\_resource\_types) | Comma-separated list of resource types to record at daily cadence | `string` | `"AWS::AutoScaling::AutoScalingGroup,AWS::AutoScaling::LaunchConfiguration"` | no |
| <a name="input_config_recorder_default_recording_frequency"></a> [config\_recorder\_default\_recording\_frequency](#input\_config\_recorder\_default\_recording\_frequency) | Default frequency of recording configuration changes | `string` | `"CONTINUOUS"` | no |
| <a name="input_config_recorder_excluded_resource_types"></a> [config\_recorder\_excluded\_resource\_types](#input\_config\_recorder\_excluded\_resource\_types) | Comma-separated list of resource types to exclude from Config Recorder (used with EXCLUSION strategy) | `string` | `"AWS::HealthLake::FHIRDatastore,AWS::Pinpoint::Segment,AWS::Pinpoint::ApplicationSettings"` | no |
| <a name="input_config_recorder_included_resource_types"></a> [config\_recorder\_included\_resource\_types](#input\_config\_recorder\_included\_resource\_types) | Comma-separated list of resource types to include in Config Recorder (used with INCLUSION strategy) | `string` | `"AWS::S3::Bucket,AWS::CloudTrail::Trail"` | no |
| <a name="input_config_recorder_strategy"></a> [config\_recorder\_strategy](#input\_config\_recorder\_strategy) | Config Recorder strategy - EXCLUSION or INCLUSION | `string` | `"EXCLUSION"` | no |
| <a name="input_control_tower_home_region"></a> [control\_tower\_home\_region](#input\_control\_tower\_home\_region) | Region where Control Tower is deployed. Global resource types are only recorded in this region. Defaults to the region of the calling provider, which is correct when the module is applied in the Control Tower home region. | `string` | `null` | no |
| <a name="input_create_error_alarm"></a> [create\_error\_alarm](#input\_create\_error\_alarm) | Create a CloudWatch alarm on the Lambda Errors metric. The function raises on any per-account failure, so this alarm fires when one or more accounts could not be updated. | `bool` | `true` | no |
| <a name="input_eventbridge_maximum_event_age_in_seconds"></a> [eventbridge\_maximum\_event\_age\_in\_seconds](#input\_eventbridge\_maximum\_event\_age\_in\_seconds) | Maximum age of a Control Tower event EventBridge will still attempt to deliver (60-86400). | `number` | `3600` | no |
| <a name="input_eventbridge_maximum_retry_attempts"></a> [eventbridge\_maximum\_retry\_attempts](#input\_eventbridge\_maximum\_retry\_attempts) | Number of times EventBridge retries delivering a Control Tower event to the Lambda before discarding it. | `number` | `10` | no |
| <a name="input_excluded_accounts"></a> [excluded\_accounts](#input\_excluded\_accounts) | List of AWS account IDs to exclude. Should contain Management, Log Archive, and Audit accounts at minimum. Only used when account\_selection\_mode is EXCLUSION. The default is placeholder IDs and must be overridden - see the warning in the README. | `list(string)` | <pre>[<br/>  "111111111111",<br/>  "222222222222",<br/>  "333333333333"<br/>]</pre> | no |
| <a name="input_included_accounts"></a> [included\_accounts](#input\_included\_accounts) | List of AWS account IDs to include. Only used when account\_selection\_mode is INCLUSION. | `list(string)` | `[]` | no |
| <a name="input_invoke_on_apply"></a> [invoke\_on\_apply](#input\_invoke\_on\_apply) | Invoke the Lambda on every terraform apply where the function code or configuration changed. Requires the AWS CLI on the machine running Terraform. Set to false to rely solely on Control Tower lifecycle events. | `bool` | `true` | no |
| <a name="input_lambda_maximum_event_age_in_seconds"></a> [lambda\_maximum\_event\_age\_in\_seconds](#input\_lambda\_maximum\_event\_age\_in\_seconds) | Maximum age of an asynchronous invocation request Lambda will still process (60-21600). | `number` | `3600` | no |
| <a name="input_lambda_maximum_retry_attempts"></a> [lambda\_maximum\_retry\_attempts](#input\_lambda\_maximum\_retry\_attempts) | Number of times Lambda retries a failed asynchronous invocation (0-2). Updating the Config Recorder is idempotent, so retrying is safe. | `number` | `2` | no |
| <a name="input_lambda_memory_size"></a> [lambda\_memory\_size](#input\_lambda\_memory\_size) | Memory in MB allocated to the Lambda function. Peak usage grows with the number of account-region pairs processed in one run. | `number` | `1024` | no |
| <a name="input_log_level"></a> [log\_level](#input\_log\_level) | Log level for the Lambda function. DEBUG is safe to enable: the AWS SDK loggers are pinned above DEBUG so temporary credentials are never written to CloudWatch Logs. | `string` | `"INFO"` | no |
| <a name="input_tags"></a> [tags](#input\_tags) | A map of tags to add to all resources created by this module | `map(string)` | `{}` | no |

## Outputs

| Name | Description |
| ---- | ----------- |
| <a name="output_control_tower_home_region"></a> [control\_tower\_home\_region](#output\_control\_tower\_home\_region) | Region treated as the Control Tower home region, where global resource types are recorded |
| <a name="output_error_alarm_arn"></a> [error\_alarm\_arn](#output\_error\_alarm\_arn) | ARN of the CloudWatch alarm on the Lambda Errors metric, or null when create\_error\_alarm is false |
| <a name="output_eventbridge_rule_arn"></a> [eventbridge\_rule\_arn](#output\_eventbridge\_rule\_arn) | ARN of the EventBridge rule that triggers the Lambda |
| <a name="output_lambda_function_arn"></a> [lambda\_function\_arn](#output\_lambda\_function\_arn) | ARN of the Config Recorder override Lambda function |
| <a name="output_lambda_function_name"></a> [lambda\_function\_name](#output\_lambda\_function\_name) | Name of the Config Recorder override Lambda function |
| <a name="output_lambda_role_arn"></a> [lambda\_role\_arn](#output\_lambda\_role\_arn) | ARN of the IAM role created for the Lambda function |
<!-- END_TF_DOCS -->

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
├── main.tf                            # Resources (Lambda module, EventBridge, invoke)
├── variables.tf                       # Input variables
├── outputs.tf                         # Outputs
├── versions.tf                        # Provider version constraints
├── .tflint.hcl                        # TFLint config, AWS ruleset enabled
├── .pre-commit-config.yaml            # fmt, docs, tflint, ruff, hygiene hooks
├── src/
│   ├── ct_configrecorder_override.py  # Lambda function source code
│   ├── requirements.txt               # CI and local testing only, not packaged
│   ├── pytest.ini                     # Test discovery and import path
│   └── tests/                         # Unit tests
├── examples/
│   ├── basic/                         # Basic exclusion mode example
│   └── inclusion-mode/                # Inclusion mode example
├── .gitignore                         # Terraform, Python, and OS exclusions
├── CHANGELOG.md                       # Version history
└── README.md                          # This file
```

## How It Works

1. **On `terraform apply`**: The Lambda is invoked asynchronously via `local-exec`, iterates all accounts from the `AWSControlTowerBP-BASELINE-CONFIG` StackSet, and applies the Config Recorder configuration. Because the invocation is asynchronous, Terraform does not wait for it — see [Failure handling](#failure-handling).

2. **On Control Tower events**: EventBridge triggers the Lambda when accounts are created/updated or the landing zone is updated/reset. The Lambda processes the affected account(s).

3. **Per-account processing**: For each account, the Lambda assumes the `AWSControlTowerExecution` role once, reads the existing Config Recorder in each region, and updates it according to the configured strategy and resource type lists.

4. **Reporting**: Per-account failures are collected rather than raised immediately, so one bad account does not stop the run. At the end, the function raises if anything failed, which surfaces on the Lambda `Errors` metric.

Note that the function skips the account it runs in, so the management account is never modified regardless of your `excluded_accounts` setting.

## Local development

```bash
# Run the test suite
cd src && python3 -m pytest

# Lint the Lambda source
ruff check src/

# Run every check the CI pipeline runs
pre-commit run --all-files
```

`pre-commit install` wires the hooks into your local commits. The `terraform_validate` hook is scoped to the module root, because the examples reference this module by its registry address and cannot resolve until the module is published.

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

Running `terraform destroy` removes the Lambda, IAM role, EventBridge rule, and alarm. It does **not** reset Config Recorders in child accounts to their defaults — those keep whatever this module last applied.

To reset them first, invoke the function with the `Delete` action before destroying. This restores `allSupported = true` in every targeted account:

```bash
aws lambda invoke \
  --function-name ct-config-recorder-override \
  --payload '{"action": "Delete"}' \
  --cli-binary-format raw-in-base64-out \
  response.json
```

The invocation is synchronous here, so it returns once every account has been reset. Check `response.json` and the function logs before destroying.
