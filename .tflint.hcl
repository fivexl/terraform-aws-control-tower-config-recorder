# Only the bundled "terraform" ruleset is enabled here, deliberately.
#
# The shared CI workflow runs `tflint -f compact` with no `tflint --init` step.
# External rulesets such as tflint-ruleset-aws have to be downloaded by `--init`
# first, so declaring one makes CI fail with:
#
#   Failed to initialize plugins; Plugin `aws` not found. Did you run `tflint --init`?
#
# The terraform ruleset ships inside tflint itself and needs no install, so this
# config works both in CI and locally with no extra setup. To enable the AWS
# ruleset later, add a `tflint --init` step to
# fivexl/github-reusable-workflows/.github/workflows/terraform-job.yml first.
plugin "terraform" {
  enabled = true
  preset  = "recommended"
}

rule "terraform_required_providers" {
  enabled = true
}

rule "terraform_required_version" {
  enabled = true
}

rule "terraform_unused_declarations" {
  enabled = true
}

rule "terraform_documented_variables" {
  enabled = true
}

rule "terraform_documented_outputs" {
  enabled = true
}

rule "terraform_naming_convention" {
  enabled = true
}
