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
- An **EventBridge rule** that triggers the Lambda on the Control Tower lifecycle events that can redeploy the Config baseline: `CreateManagedAccount`, `UpdateManagedAccount`, `UpdateLandingZone`, `RegisterOrganizationalUnit`, `EnableBaseline`, `ResetEnabledBaseline` and `UpdateEnabledBaseline`
- An **EventBridge schedule** that periodically re-applies the settings, so a lifecycle event that never arrives cannot leave them reverted indefinitely
- A **CloudWatch alarm** on the Lambda `Errors` metric, which is the primary failure signal
- A **terraform_data resource** that invokes the Lambda on apply when the function code or configuration changes

By design the module uses only EventBridge and Lambda. There is no queue in the architecture.

### Region behaviour

Resources are created in whichever region your provider targets, following Terraform Registry convention — the module takes no region for resource placement. Apply it in your Control Tower home region.

`control_tower_home_region` is a separate concern: it tells the function where Control Tower lives, which by default is also the region that records global resource types. It defaults to the provider's region, which is correct for the normal case. Only set it explicitly if you are deliberately applying the module outside the Control Tower home region.

### Global resource types

The global IAM types — IAM users, groups, roles and customer managed policies — describe the same resources in every region. Recording them in more than one region duplicates the data and the cost, and IAM churns on every deploy, so the duplication is not cheap. [AWS recommends](https://docs.aws.amazon.com/config/latest/developerguide/select-resources.html) recording them once, in one supported region, to avoid duplicate configuration items and API throttling. Control Tower's own baseline records them in the home region only, and this module matches that.

Two AWS behaviours make that harder than it looks, and the module handles both:

- `includeGlobalResourceTypes` only works alongside `allSupported`. Under the `EXCLUSION_BY_RESOURCE_TYPES` strategy [AWS ignores the flag](https://docs.aws.amazon.com/config/latest/APIReference/API_RecordingGroup.html) and records the global IAM types anyway. The module therefore adds those four types to the exclusion list itself in every region except the home region. If your exclusion list is empty the module uses `allSupported` instead, where the flag does work.
- `AWS::RDS::GlobalCluster` is recorded in every region where the recorder is enabled regardless of `includeGlobalResourceTypes`, because the flag covers only the four IAM types. Add it to `config_recorder_excluded_resource_types` if you do not want it recorded more than once.

Because everything above hinges on one region name, two things guard it:

- The Lambda checks that the nominated region is one the StackSet actually reported. A typo, or a region Control Tower does not govern, otherwise matches nothing, so every region excludes the global IAM types and none records them — while each account updates successfully and the run reports success. That check now fails the run, which puts it on the `Errors` metric and the alarm. It only applies to full walks, since a single account need not have an instance in every governed region.
- Terraform rejects a nominated region where AWS cannot record global IAM types at all, at plan time.

#### When Control Tower is homed where global IAM types cannot be recorded

AWS can only record the global IAM types in regions where Config was available before February 2022. These ten came later, and Control Tower can be homed in some of them:

`ap-south-2` (Hyderabad), `ap-southeast-4` (Melbourne), `ap-southeast-5` (Malaysia), `ap-southeast-7` (Thailand), `ca-west-1` (Calgary), `eu-central-2` (Zurich), `eu-south-2` (Spain), `il-central-1` (Tel Aviv), `me-central-1` (UAE), `mx-central-1` (Mexico Central).

If your home region is one of these, the home region cannot be the one recording IAM. `global_iam_recording_region` nominates a different governed region instead, which is the only way to get any IAM coverage:

```hcl
control_tower_home_region   = "eu-central-2" # Zurich, cannot record global IAM types
global_iam_recording_region = "eu-west-1"    # so record them here instead
```

Set it to `""` to accept that no region records the global IAM types. That is a valid choice, but make it deliberately — conformance packs that evaluate IAM resources will have nothing to evaluate.

The variable defaults to `control_tower_home_region`, so leave it alone unless you are in this situation. Note that the `Delete` action still restores Control Tower's own defaults, which follow the home region rather than this setting.

### Recording frequency

`config_recorder_default_recording_frequency` sets the cadence for everything recorded. `config_recorder_override_recording_frequency` sets the cadence for the types named in `config_recorder_daily_resource_types` and `config_recorder_daily_global_resource_types`. AWS allows [exactly one override object](https://docs.aws.amazon.com/config/latest/APIReference/API_RecordingMode.html), so those two lists share a single frequency.

That is what makes the inverse arrangement possible: a `DAILY` default with `CONTINUOUS` for a few named types. AWS Firewall Manager depends on continuous recording for the types its policies cover, so if you use FMS and want daily recording for cost reasons, put those types in the override list and set the override frequency to `CONTINUOUS`.

```hcl
config_recorder_default_recording_frequency  = "DAILY"
config_recorder_override_recording_frequency = "CONTINUOUS"
# FMS DNS Firewall policies key on VPCs.
config_recorder_daily_resource_types         = "AWS::EC2::VPC"
config_recorder_daily_global_resource_types  = ""
```

Three resource types cannot be recorded daily and stay continuous whatever you configure:

- `AWS::Config::ResourceCompliance`
- `AWS::Config::ConformancePackCompliance`
- `AWS::Config::ConfigurationRecorder`

Under the `allSupported` strategy AWS sets them to continuous for you, so they are an unavoidable exception to any "everything daily" configuration.

### Scaling

The Lambda walks account-region pairs sequentially. Credentials are cached per account, so an account spanning three regions costs one `AssumeRole` call rather than three, and the one-second throttle delay is paid per account rather than per region.

Runtime is roughly `accounts × 1s` for the `AssumeRole` throttle delay, plus two Config API calls per account-region pair (one `DescribeConfigurationRecorders`, one `PutConfigurationRecorder`). An organization of 52 accounts across 4 regions is about 52 seconds of throttle delay plus 416 API calls, which sits comfortably inside the 15-minute timeout. The timeout is unlikely to be your first constraint.

`reserved_concurrent_executions` is 1, because overlapping runs would race each other writing the same recorders. That means Control Tower lifecycle events arriving together are processed one run at a time, so `eventbridge_maximum_event_age_in_seconds` matters more than the function timeout during a landing zone reset. If you outgrow the single sequential walk, the [`original-arch-no-copy-lambda`](https://github.com/fivexl/terraform-aws-control-tower-config-recorder/tree/original-arch-no-copy-lambda) branch uses a fan-out pattern with parallel invocations.

`lambda_memory_size` is not a function of organization size: the function holds one cached session per account and one settings dict per region, so memory stays roughly flat. Raise it to buy CPU, not headroom.

### Staying applied

Control Tower owns the `AWSControlTowerBP-BASELINE-CONFIG` StackSet. Anything that redeploys it resets the Config Recorder to the Control Tower default and discards this customization, so the module has to run again afterwards. The rule therefore subscribes to every [lifecycle event](https://docs.aws.amazon.com/controltower/latest/userguide/lifecycle-events.html) that can cause a redeploy, including the baseline events (`EnableBaseline`, `ResetEnabledBaseline`, `UpdateEnabledBaseline`) and OU registration, not just account creation.

Only successful lifecycle events are acted on. A failed Control Tower operation applied no baseline, so there is nothing to override, and assuming a role into a half-provisioned account would fail the run and trip the error alarm for no reason.

Two things make a missed event worse than it sounds, which is why the schedule exists:

- A missed event is silent. The function simply never runs, so nothing fails and the `Errors` alarm cannot tell you.
- An unchanged `terraform apply` will not fix it either. The apply-time invocation keys on the function's source code hash, so it only fires when the module itself changes.

`reconciliation_schedule_expression` (default `rate(12 hours)`) re-invokes the function on a timer, which bounds how long drift can last without needing to predict AWS's event list. That list has grown before — the baseline events are recent additions. Re-applying is idempotent, and at 52 accounts across 4 regions a run costs about 416 Config API calls. Set the variable to `null` to disable it and rely solely on lifecycle events.

Lifecycle events only reach EventBridge if you have an active CloudTrail trail with logging enabled. Control Tower creates an organization trail by default, but if CloudTrail was turned off in your landing zone settings, none of these events arrive and the schedule becomes the only trigger.

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
- An active **CloudTrail trail with logging enabled**, which is what delivers Control Tower lifecycle events to EventBridge. Control Tower creates an organization trail by default. Without it the module only runs on apply and on the reconciliation schedule.

The first `apply` in a fresh working directory can need to be run twice. `terraform-aws-modules/lambda/aws` computes the function's `source_code_hash` from the packaged archive, and on that first run the archive does not exist yet when the hash is computed, so plan and apply can disagree about it. This is upstream module behavior, not specific to this module.

`lambda_ignore_source_code_hash` (default `true`) suppresses that specific field so the mismatch cannot occur. This does not stop real code changes from deploying: the archive's filename is derived from the content of `src/` on every plan, and a changed filename is what the AWS provider actually redeploys on, regardless of this setting. There is no known downside to leaving it at its default in this module's configuration. Set it to `false` only to restore the exact upstream behavior, for example while diagnosing a suspected issue in the module itself.
- AWS CLI on the machine running Terraform, if `invoke_on_apply` is left enabled (the default). Set `invoke_on_apply = false` to remove that dependency and rely solely on Control Tower lifecycle events.

## Account targeting is checked at plan time

There is no safe default for `excluded_accounts`, because account IDs are specific to your organization. Rather than ship placeholder IDs that quietly match nothing, the module defaults to an empty list and refuses to plan in `EXCLUSION` mode until you fill it in:

```
excluded_accounts must not be empty in EXCLUSION mode. Every managed account
except the one this module runs in would have its Config Recorder rewritten,
including Log Archive and Audit, which should keep their Control Tower defaults.
```

List the accounts you want left alone — Log Archive and Audit at minimum — or use `INCLUSION` mode and name the accounts you want changed. `INCLUSION` mode applies the same reasoning in reverse and rejects an empty `included_accounts`, since the function would otherwise run and update nothing.

The account the module runs in is always skipped, so the management account is never rewritten regardless of these lists.

## Usage

### Using as a Module

```hcl
provider "aws" {
  # Resources are created here. Apply in the Control Tower home region.
  region = "us-east-1"
}

module "config_recorder_override" {
  source  = "fivexl/control-tower-config-recorder/aws"
  version = "~> 4.0"

  account_selection_mode = "EXCLUSION"

  # Required in EXCLUSION mode. Your real Log Archive and Audit account IDs,
  # plus anything else that should keep its Control Tower defaults.
  excluded_accounts = ["222222222222", "333333333333"]

  config_recorder_strategy                     = "EXCLUSION"
  config_recorder_excluded_resource_types      = "AWS::HealthLake::FHIRDatastore,AWS::Pinpoint::Segment,AWS::Pinpoint::ApplicationSettings"
  config_recorder_default_recording_frequency  = "CONTINUOUS"
  config_recorder_override_recording_frequency = "DAILY"
  config_recorder_daily_resource_types         = "AWS::AutoScaling::AutoScalingGroup,AWS::AutoScaling::LaunchConfiguration"
  config_recorder_daily_global_resource_types  = "AWS::IAM::Policy,AWS::IAM::User,AWS::IAM::Role,AWS::IAM::Group"

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

### Upgrading from 3.x

`excluded_accounts` no longer defaults to placeholder IDs, so `EXCLUSION` mode now fails at plan time until you list your real accounts. If you were relying on the placeholders, you were rewriting every managed account; set the variable explicitly.

Two behaviour changes apply themselves on the next run, with no configuration needed:

- Global IAM types are no longer recorded outside the Control Tower home region. If you were on the `EXCLUSION` strategy, this reduces what is recorded in your non-home governed regions and should reduce your Config bill. Add the four IAM types to `config_recorder_daily_global_resource_types` only if you want them recorded at the override cadence in the home region.
- `config_recorder_default_recording_frequency` is now honoured with empty override lists. If you set `DAILY` and kept a resource type in `config_recorder_daily_resource_types` purely to make the block appear, you can drop it.

```diff
 module "config_recorder_override" {
-  version = "~> 3.0"
+  version = "~> 4.0"

+  # Now required in EXCLUSION mode.
+  excluded_accounts = ["222222222222", "333333333333"]
 }
```

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
| [aws_cloudwatch_event_rule.reconciliation](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/cloudwatch_event_rule) | resource |
| [aws_cloudwatch_event_target.lambda](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/cloudwatch_event_target) | resource |
| [aws_cloudwatch_event_target.reconciliation](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/cloudwatch_event_target) | resource |
| [aws_cloudwatch_metric_alarm.lambda_errors](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/cloudwatch_metric_alarm) | resource |
| [terraform_data.invoke_lambda](https://registry.terraform.io/providers/hashicorp/terraform/latest/docs/resources/data) | resource |
| [terraform_data.validate_configuration](https://registry.terraform.io/providers/hashicorp/terraform/latest/docs/resources/data) | resource |
| [terraform_data.validate_global_iam_region](https://registry.terraform.io/providers/hashicorp/terraform/latest/docs/resources/data) | resource |
| [aws_iam_policy_document.lambda_policy](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/data-sources/iam_policy_document) | data source |
| [aws_partition.current](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/data-sources/partition) | data source |
| [aws_region.current](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/data-sources/region) | data source |

## Inputs

| Name | Description | Type | Default | Required |
| ---- | ----------- | ---- | ------- | :------: |
| <a name="input_account_selection_mode"></a> [account\_selection\_mode](#input\_account\_selection\_mode) | Account selection mode - EXCLUSION (processes all accounts except those in excluded\_accounts) or INCLUSION (processes only accounts in included\_accounts) | `string` | `"EXCLUSION"` | no |
| <a name="input_alarm_actions"></a> [alarm\_actions](#input\_alarm\_actions) | List of ARNs (for example an SNS topic) to notify when the error alarm fires. An alarm with no actions still records state but notifies nobody. | `list(string)` | `[]` | no |
| <a name="input_cloudwatch_logs_retention_in_days"></a> [cloudwatch\_logs\_retention\_in\_days](#input\_cloudwatch\_logs\_retention\_in\_days) | Number of days to retain Lambda CloudWatch log events | `number` | `14` | no |
| <a name="input_config_recorder_daily_global_resource_types"></a> [config\_recorder\_daily\_global\_resource\_types](#input\_config\_recorder\_daily\_global\_resource\_types) | Comma-separated list of global resource types the override recording frequency applies to. Only applied in the Control Tower home region, since that is the only region where global types are recorded. | `string` | `"AWS::IAM::Policy,AWS::IAM::User,AWS::IAM::Role,AWS::IAM::Group"` | no |
| <a name="input_config_recorder_daily_resource_types"></a> [config\_recorder\_daily\_resource\_types](#input\_config\_recorder\_daily\_resource\_types) | Comma-separated list of resource types the override recording frequency applies to. AWS allows a single override, so this list and config\_recorder\_daily\_global\_resource\_types share one frequency. | `string` | `"AWS::AutoScaling::AutoScalingGroup,AWS::AutoScaling::LaunchConfiguration"` | no |
| <a name="input_config_recorder_default_recording_frequency"></a> [config\_recorder\_default\_recording\_frequency](#input\_config\_recorder\_default\_recording\_frequency) | Default frequency of recording configuration changes. Applies to every recorded resource type except those listed in the two override lists. AWS::Config::ResourceCompliance, AWS::Config::ConformancePackCompliance and AWS::Config::ConfigurationRecorder cannot be recorded daily and stay continuous regardless. | `string` | `"CONTINUOUS"` | no |
| <a name="input_config_recorder_excluded_resource_types"></a> [config\_recorder\_excluded\_resource\_types](#input\_config\_recorder\_excluded\_resource\_types) | Comma-separated list of resource types to exclude from Config Recorder (used with EXCLUSION strategy) | `string` | `"AWS::HealthLake::FHIRDatastore,AWS::Pinpoint::Segment,AWS::Pinpoint::ApplicationSettings"` | no |
| <a name="input_config_recorder_included_resource_types"></a> [config\_recorder\_included\_resource\_types](#input\_config\_recorder\_included\_resource\_types) | Comma-separated list of resource types to include in Config Recorder (used with INCLUSION strategy) | `string` | `"AWS::S3::Bucket,AWS::CloudTrail::Trail"` | no |
| <a name="input_config_recorder_override_recording_frequency"></a> [config\_recorder\_override\_recording\_frequency](#input\_config\_recorder\_override\_recording\_frequency) | Recording frequency applied to the resource types in config\_recorder\_daily\_resource\_types and config\_recorder\_daily\_global\_resource\_types. Set this to CONTINUOUS with a DAILY default to keep specific types on continuous recording, which is what AWS Firewall Manager requires of the types its policies cover. | `string` | `"DAILY"` | no |
| <a name="input_config_recorder_strategy"></a> [config\_recorder\_strategy](#input\_config\_recorder\_strategy) | Config Recorder strategy - EXCLUSION or INCLUSION | `string` | `"EXCLUSION"` | no |
| <a name="input_control_tower_home_region"></a> [control\_tower\_home\_region](#input\_control\_tower\_home\_region) | Region where Control Tower is deployed. Global resource types are recorded only in this region unless global\_iam\_recording\_region overrides that. Defaults to the region of the calling provider, which is correct when the module is applied in the Control Tower home region. | `string` | `null` | no |
| <a name="input_create_error_alarm"></a> [create\_error\_alarm](#input\_create\_error\_alarm) | Create a CloudWatch alarm on the Lambda Errors metric. The function raises on any per-account failure, so this alarm fires when one or more accounts could not be updated. | `bool` | `true` | no |
| <a name="input_eventbridge_maximum_event_age_in_seconds"></a> [eventbridge\_maximum\_event\_age\_in\_seconds](#input\_eventbridge\_maximum\_event\_age\_in\_seconds) | Maximum age of a Control Tower event EventBridge will still attempt to deliver (60-86400). | `number` | `3600` | no |
| <a name="input_eventbridge_maximum_retry_attempts"></a> [eventbridge\_maximum\_retry\_attempts](#input\_eventbridge\_maximum\_retry\_attempts) | Number of times EventBridge retries delivering a Control Tower event to the Lambda before discarding it. | `number` | `10` | no |
| <a name="input_excluded_accounts"></a> [excluded\_accounts](#input\_excluded\_accounts) | List of AWS account IDs to exclude. Should contain Log Archive and Audit accounts at minimum. Only used when account\_selection\_mode is EXCLUSION, where an empty list is rejected at plan time. | `list(string)` | `[]` | no |
| <a name="input_global_iam_recording_region"></a> [global\_iam\_recording\_region](#input\_global\_iam\_recording\_region) | Region that records the global IAM resource types (IAM users, groups, roles, customer managed policies). Defaults to control\_tower\_home\_region, which is correct almost always. Set it to another governed region when Control Tower is homed in one of the regions where AWS cannot record global IAM types, or to an empty string to accept that no region records them. | `string` | `null` | no |
| <a name="input_included_accounts"></a> [included\_accounts](#input\_included\_accounts) | List of AWS account IDs to include. Only used when account\_selection\_mode is INCLUSION. | `list(string)` | `[]` | no |
| <a name="input_invoke_on_apply"></a> [invoke\_on\_apply](#input\_invoke\_on\_apply) | Invoke the Lambda on every terraform apply where the function code or configuration changed. Requires the AWS CLI on the machine running Terraform. Set to false to rely solely on Control Tower lifecycle events. | `bool` | `true` | no |
| <a name="input_lambda_ignore_source_code_hash"></a> [lambda\_ignore\_source\_code\_hash](#input\_lambda\_ignore\_source\_code\_hash) | Passed straight through to terraform-aws-modules/lambda/aws. Suppresses a spurious plan/apply mismatch on the first apply in a fresh working directory, where the deployment archive does not exist yet when the module computes source\_code\_hash. Real code changes are still deployed: this module builds the archive filename from the content of src/, and that filename changing is what the AWS provider actually keys a redeploy on. Defaults to true, since there is no known downside to that in this module's configuration. | `bool` | `true` | no |
| <a name="input_lambda_maximum_event_age_in_seconds"></a> [lambda\_maximum\_event\_age\_in\_seconds](#input\_lambda\_maximum\_event\_age\_in\_seconds) | Maximum age of an asynchronous invocation request Lambda will still process (60-21600). | `number` | `3600` | no |
| <a name="input_lambda_maximum_retry_attempts"></a> [lambda\_maximum\_retry\_attempts](#input\_lambda\_maximum\_retry\_attempts) | Number of times Lambda retries a failed asynchronous invocation (0-2). Updating the Config Recorder is idempotent, so retrying is safe. | `number` | `2` | no |
| <a name="input_lambda_memory_size"></a> [lambda\_memory\_size](#input\_lambda\_memory\_size) | Memory in MB allocated to the Lambda function. The function holds one cached session per account and one settings dict per region, so memory is roughly flat in the number of accounts; this mainly buys CPU. | `number` | `1024` | no |
| <a name="input_log_level"></a> [log\_level](#input\_log\_level) | Log level for the Lambda function. DEBUG is safe to enable: the AWS SDK loggers are pinned above DEBUG so temporary credentials are never written to CloudWatch Logs. | `string` | `"INFO"` | no |
| <a name="input_reconciliation_schedule_expression"></a> [reconciliation\_schedule\_expression](#input\_reconciliation\_schedule\_expression) | EventBridge schedule for periodically re-applying Config Recorder settings, for example rate(12 hours) or cron(0 3 * * ? *). Control Tower keeps adding lifecycle events, and a missed one leaves the recorder reverted with nothing on the Errors metric to show it, so this bounds how long that can last. Re-applying is idempotent. Set to null or an empty string to disable and rely solely on lifecycle events. | `string` | `"rate(12 hours)"` | no |
| <a name="input_tags"></a> [tags](#input\_tags) | A map of tags to add to all resources created by this module | `map(string)` | `{}` | no |

## Outputs

| Name | Description |
| ---- | ----------- |
| <a name="output_control_tower_home_region"></a> [control\_tower\_home\_region](#output\_control\_tower\_home\_region) | Region treated as the Control Tower home region |
| <a name="output_error_alarm_arn"></a> [error\_alarm\_arn](#output\_error\_alarm\_arn) | ARN of the CloudWatch alarm on the Lambda Errors metric, or null when create\_error\_alarm is false |
| <a name="output_eventbridge_rule_arn"></a> [eventbridge\_rule\_arn](#output\_eventbridge\_rule\_arn) | ARN of the EventBridge rule that triggers the Lambda on Control Tower lifecycle events |
| <a name="output_global_iam_recording_region"></a> [global\_iam\_recording\_region](#output\_global\_iam\_recording\_region) | Region that records the global IAM resource types, or an empty string when no region records them |
| <a name="output_lambda_function_arn"></a> [lambda\_function\_arn](#output\_lambda\_function\_arn) | ARN of the Config Recorder override Lambda function |
| <a name="output_lambda_function_name"></a> [lambda\_function\_name](#output\_lambda\_function\_name) | Name of the Config Recorder override Lambda function |
| <a name="output_lambda_role_arn"></a> [lambda\_role\_arn](#output\_lambda\_role\_arn) | ARN of the IAM role created for the Lambda function |
| <a name="output_reconciliation_rule_arn"></a> [reconciliation\_rule\_arn](#output\_reconciliation\_rule\_arn) | ARN of the EventBridge schedule that periodically re-applies Config Recorder settings, or null when reconciliation\_schedule\_expression is null |
<!-- END_TF_DOCS -->

## Account Selection Modes

### EXCLUSION Mode (Default)

Applies Config Recorder changes to all Control Tower managed accounts except those in `excluded_accounts`. The list must be non-empty, which is checked at plan time.

### INCLUSION Mode

Applies changes only to accounts listed in `included_accounts`. Useful for testing or targeting specific workload accounts. The list must be non-empty, which is checked at plan time.

### Important Warning

Regardless of mode, you should typically NOT customize the following accounts:
- **Log Archive Account** — centralized logging
- **Audit Account** — security audit

These have special roles in Control Tower governance and should maintain default Config Recorder settings. The **management account** needs no entry in either list: the function skips whichever account it runs in.

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

2. **On Control Tower events**: EventBridge triggers the Lambda when an account is created or updated, a baseline is enabled, reset or updated, an OU is registered, or the landing zone is updated. Events that name a single account are narrowed to it; OU and landing zone events walk the whole organization. Failed lifecycle events are ignored.

3. **On a schedule**: the reconciliation rule re-invokes the function on a timer so a missed event cannot leave the recorder reverted indefinitely. See [Staying applied](#staying-applied).

4. **Per-account processing**: For each account, the Lambda assumes the `AWSControlTowerExecution` role once, reads the existing Config Recorder in each region, and updates it according to the configured strategy and resource type lists.

5. **Reporting**: Per-account failures are collected rather than raised immediately, so one bad account does not stop the run. At the end, the function raises if anything failed, which surfaces on the Lambda `Errors` metric.

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
