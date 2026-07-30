terraform {
  required_version = ">= 1.5.0"

  required_providers {
    aws = {
      source = "hashicorp/aws"
      # 6.0 is the floor because this module reads
      # data.aws_region.current.region. The older `name` and `id` attributes on
      # that data source are deprecated in provider 6.x.
      version = ">= 6.0"
    }
  }
}
