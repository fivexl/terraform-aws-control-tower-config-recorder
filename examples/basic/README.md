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

No requirements.

## Providers

No providers.

## Modules

| Name | Source | Version |
| ---- | ------ | ------- |
| <a name="module_config_recorder_override"></a> [config\_recorder\_override](#module\_config\_recorder\_override) | fivexl/control-tower-config-recorder/aws | ~> 2.0 |

## Resources

No resources.

## Inputs

No inputs.

## Outputs

| Name | Description |
| ---- | ----------- |
| <a name="output_eventbridge_rule_arn"></a> [eventbridge\_rule\_arn](#output\_eventbridge\_rule\_arn) | n/a |
| <a name="output_lambda_arn"></a> [lambda\_arn](#output\_lambda\_arn) | n/a |
| <a name="output_lambda_role_arn"></a> [lambda\_role\_arn](#output\_lambda\_role\_arn) | n/a |
<!-- END_TF_DOCS -->