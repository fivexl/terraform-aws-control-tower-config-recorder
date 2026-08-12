# Inclusion Mode Example

This example uses inclusion mode to target specific accounts and only record security-critical resource types.

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
| <a name="output_control_tower_home_region"></a> [control\_tower\_home\_region](#output\_control\_tower\_home\_region) | Region treated as the Control Tower home region |
| <a name="output_lambda_function_arn"></a> [lambda\_function\_arn](#output\_lambda\_function\_arn) | ARN of the Config Recorder override Lambda function |
<!-- END_TF_DOCS -->
