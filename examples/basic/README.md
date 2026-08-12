# Basic Example

This example deploys the Config Recorder override module with the default exclusion strategy.

## Usage

```bash
terraform init
terraform plan
terraform apply
```

## Prerequisites

- AWS CLI configured with credentials for the Control Tower management account
- Terraform >= 1.5.0

<!-- BEGIN_TF_DOCS -->
## Requirements

| Name | Version |
| ---- | ------- |
| <a name="requirement_terraform"></a> [terraform](#requirement\_terraform) | >= 1.5.0 |
| <a name="requirement_aws"></a> [aws](#requirement\_aws) | >= 6.0 |

## Providers

No providers.

## Modules

| Name | Source | Version |
| ---- | ------ | ------- |
| <a name="module_config_recorder_override"></a> [config\_recorder\_override](#module\_config\_recorder\_override) | fivexl/control-tower-config-recorder/aws | ~> 4.0 |

## Resources

No resources.

## Inputs

No inputs.

## Outputs

| Name | Description |
| ---- | ----------- |
| <a name="output_error_alarm_arn"></a> [error\_alarm\_arn](#output\_error\_alarm\_arn) | ARN of the CloudWatch alarm on the Lambda Errors metric |
| <a name="output_eventbridge_rule_arn"></a> [eventbridge\_rule\_arn](#output\_eventbridge\_rule\_arn) | ARN of the EventBridge rule that triggers the Lambda |
| <a name="output_lambda_function_arn"></a> [lambda\_function\_arn](#output\_lambda\_function\_arn) | ARN of the Config Recorder override Lambda function |
| <a name="output_lambda_role_arn"></a> [lambda\_role\_arn](#output\_lambda\_role\_arn) | ARN of the IAM role created for the Lambda function |
<!-- END_TF_DOCS -->
