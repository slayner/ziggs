# Terraform Security Reference

## Overview

Terraform misconfigurations can lead to exposed secrets, overly permissive IAM, insecure infrastructure, and supply chain risks. Review `.tf` files, module sources, state management, and variable definitions.

---

## Secrets in Code

### Plaintext Secrets

```hcl
# VULNERABLE: Secret hardcoded in HCL
resource "aws_db_instance" "main" {
  password = "supersecret123"
}

# VULNERABLE: Secret in variable default
variable "db_password" {
  default = "password123"
}

# VULNERABLE: API key hardcoded
provider "aws" {
  access_key = "AKIA..."
  secret_key = "abc123..."
}
```

```hcl
# SAFE: Secret from secret store
resource "aws_db_instance" "main" {
  password = data.aws_secretsmanager_secret_version.db.password_secret_string
}

# SAFE: From environment/TF_VAR
variable "db_password" {
  type      = string
  sensitive = true
  # no default
}

# SAFE: From SSM Parameter Store
data "aws_ssm_parameter" "db_password" {
  name            = "/db/password"
  with_decryption = true
}
```

### Sensitive Values Not Marked

```hcl
# VULNERABLE: Sensitive value not marked
output "db_endpoint" {
  value = aws_db_instance.main.endpoint
}
output "db_password" {
  value = aws_db_instance.main.password  # will show in plan output
}

# SAFE: Marked as sensitive
output "db_endpoint" {
  value     = aws_db_instance.main.endpoint
  sensitive = true
}
output "db_password" {
  value     = aws_db_instance.main.password
  sensitive = true
}
```

---

## IAM Permissions

### Overly Permissive Policies

```hcl
# VULNERABLE: Wildcard permissions
resource "aws_iam_policy" "app" {
  policy = jsonencode({
    Statement = [{
      Effect   = "Allow"
      Action   = "*"              # all actions
      Resource = "*"              # all resources
    }]
  })
}

# VULNERABLE: s3:* on all buckets
resource "aws_iam_policy" "app" {
  policy = jsonencode({
    Statement = [{
      Effect   = "Allow"
      Action   = "s3:*"
      Resource = "arn:aws:s3:::*"
    }]
  })
}
```

```hcl
# SAFE: Least privilege
resource "aws_iam_policy" "app" {
  policy = jsonencode({
    Statement = [{
      Effect   = "Allow"
      Action   = ["s3:GetObject", "s3:PutObject"]
      Resource = "arn:aws:s3:::app-uploads/*"
    }]
  })
}
```

---

## Storage Security

### Public S3 Buckets

```hcl
# VULNERABLE: Public read/write
resource "aws_s3_bucket" "data" {
  bucket = "data-bucket"
  acl    = "public-read-write"
}

# VULNERABLE: Block public access disabled
resource "aws_s3_bucket_public_access_block" "data" {
  bucket                  = aws_s3_bucket.data.id
  block_public_acls       = false
  block_public_policy     = false
  ignore_public_acls      = false
  restrict_public_buckets = false
}
```

```hcl
# SAFE: Private bucket with public access blocked
resource "aws_s3_bucket" "data" {
  bucket = "data-bucket"
}

resource "aws_s3_bucket_public_access_block" "data" {
  bucket                  = aws_s3_bucket.data.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# SAFE: Encryption at rest
resource "aws_s3_bucket_server_side_encryption_configuration" "data" {
  bucket = aws_s3_bucket.data.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}
```

---

## Network Security

### Open Security Groups

```hcl
# VULNERABLE: Open to world
resource "aws_security_group" "web" {
  ingress {
    from_port   = 0
    to_port     = 65535
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]  # all ports open to world
  }
}

# VULNERABLE: SSH open to world
resource "aws_security_group" "ssh" {
  ingress {
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

# VULNERABLE: Database open to world
resource "aws_security_group" "db" {
  ingress {
    from_port   = 5432
    to_port     = 5432
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }
}
```

```hcl
# SAFE: Restricted ingress
resource "aws_security_group" "web" {
  ingress {
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]  # HTTPS only
  }
}

resource "aws_security_group" "db" {
  ingress {
    from_port   = 5432
    to_port     = 5432
    protocol    = "tcp"
    security_groups = [aws_security_group.web.id]  # only web SG
  }
}
```

---

## State Management

### State File with Secrets

```hcl
# VULNERABLE: State stored locally (contains plaintext secrets)
# Default behavior - terraform.tfstate has all secrets in plaintext

# VULNERABLE: State in public S3
terraform {
  backend "s3" {
    bucket = "public-terraform-state"
    key    = "prod/terraform.tfstate"
    # no encryption, no locking
  }
}
```

```hcl
# SAFE: Encrypted remote state with locking
terraform {
  backend "s3" {
    bucket         = "private-terraform-state"
    key            = "prod/terraform.tfstate"
    encrypt        = true
    dynamodb_table = "terraform-locks"
    kms_key_id     = "arn:aws:kms:us-east-1:123:key/abc"
  }
}
```

---

## Module Sources

### Untrusted Modules

```hcl
# VULNERABLE: Module from untrusted source
module "vpc" {
  source = "github.com/randomuser/terraform-vpc"
}

# VULNERABLE: Module from unversioned source
module "vpc" {
  source = "github.com/myorg/terraform-vpc"
  # no version pin
}
```

```hcl
# SAFE: Pinned version from trusted registry
module "vpc" {
  source  = "terraform-aws-modules/vpc/aws"
  version = "5.0.0"  # pinned
}

# SAFE: Pinned git commit
module "vpc" {
  source = "github.com/myorg/terraform-vpc?ref=abc123def"
}
```

---

## Encryption

### Unencrypted Resources

```hcl
# VULNERABLE: Unencrypted EBS volumes
resource "aws_ebs_volume" "data" {
  encrypted = false
}

# VULNERABLE: Unencrypted RDS
resource "aws_db_instance" "main" {
  storage_encrypted = false
}
```

```hcl
# SAFE: Encryption enabled
resource "aws_ebs_volume" "data" {
  encrypted = true
  kms_key_id = aws_kms_key.ebs.arn
}

resource "aws_db_instance" "main" {
  storage_encrypted = true
  kms_key_id        = aws_kms_key.rds.arn
}
```