# Cloud Security Reference

## Overview

Cloud security covers IAM, storage, compute, networking, and managed services across AWS, GCP, and Azure. Focus on exploitable misconfigurations that allow unauthorized access, data exposure, or privilege escalation.

---

## AWS

### IAM

```hcl
# VULNERABLE: Overly permissive policy
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Action": "*",
    "Resource": "*"
  }]
}

# VULNERABLE: Inline policy with wildcard
resource "aws_iam_user_policy" "user" {
  policy = jsonencode({
    Statement = [{
      Effect   = "Allow"
      Action   = "s3:*"
      Resource = "*"
    }]
  })
}
```

```hcl
# SAFE: Least privilege
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Action": ["s3:GetObject"],
    "Resource": "arn:aws:s3:::app-data/*"
  }]
}
```

### S3

```hcl
# VULNERABLE: Public bucket with write
resource "aws_s3_bucket" "uploads" {
  acl = "public-read-write"
}

# VULNERABLE: No encryption
resource "aws_s3_bucket" "data" {
  # no server_side_encryption_configuration
}

# VULNERABLE: Versioning disabled (no ransomware recovery)
resource "aws_s3_bucket" "data" {
  versioning { enabled = false }
}
```

### RDS

```hcl
# VULNERABLE: Publicly accessible RDS
resource "aws_db_instance" "main" {
  publicly_accessible = true
  storage_encrypted   = false
}

# VULNERABLE: Hardcoded password
resource "aws_db_instance" "main" {
  password = "admin123"
}
```

### Lambda

```hcl
# VULNERABLE: Lambda with overly broad execution role
resource "aws_iam_role" "lambda" {
  assume_role_policy = data.aws_iam_policy_document.assume.json
}
resource "aws_iam_role_policy_attachment" "lambda" {
  role       = aws_iam_role.lambda.name
  policy_arn = "arn:aws:iam::aws:policy/AdministratorAccess"  # full admin
}

# VULNERABLE: Lambda environment with plaintext secret
resource "aws_lambda_function" "api" {
  environment {
    variables = {
      DB_PASSWORD = "secret123"  # plaintext
    }
  }
  # no kms_key_arn set
}
```

### CloudFront

```hcl
# VULNERABLE: Origin without HTTPS
resource "aws_cloudfront_distribution" "cdn" {
  origin {
    domain_name = "origin.example.com"
    custom_origin_config {
      origin_protocol_policy = "HTTP_only"  # unencrypted
    }
  }
}

# VULNERABLE: No WAF attached
resource "aws_cloudfront_distribution" "cdn" {
  # no web_acl_id
}
```

---

## GCP

### IAM

```hcl
# VULNERABLE: Broad role assignment
resource "google_project_iam_binding" "binding" {
  role = "roles/owner"
  members = [
    "user:developer@example.com",  # owner to all project resources
  ]
}

# VULNERABLE: allUsers member
resource "google_storage_bucket_iam_binding" "binding" {
  members = [
    "allUsers",  # public access
  ]
  role = "roles/storage.objectViewer"
}
```

```hcl
# SAFE: Specific role
resource "google_project_iam_binding" "binding" {
  role = "roles/storage.objectViewer"
  members = [
    "serviceAccount:app@project.iam.gserviceaccount.com",
  ]
}
```

### Storage (GCS)

```hcl
# VULNERABLE: Public bucket
resource "google_storage_bucket" "data" {
  uniform_bucket_level_access = false
}
resource "google_storage_bucket_acl" "data" {
  role_entity = ["READER-allUsers"]
}

# VULNERABLE: No encryption
resource "google_storage_bucket" "data" {
  # no encryption block
}
```

```hcl
# SAFE: Private with encryption
resource "google_storage_bucket" "data" {
  uniform_bucket_level_access = true
  encryption {
    default_kms_key_name = google_kms_crypto_key.bucket.id
  }
}
```

### Compute

```hcl
# VULNERABLE: Serial console enabled
resource "google_compute_instance" "vm" {
  metadata = {
    serial-port-enable = "true"  # exposes boot logs with secrets
  }
}

# VULNERABLE: External IP on all instances
resource "google_compute_instance" "vm" {
  network_interface {
    access_config {}  # external IP
  }
}
```

### Cloud SQL

```hcl
# VULNERABLE: Public SQL instance
resource "google_sql_database_instance" "main" {
  settings {
    ip_configuration {
      ipv4_enabled = true  # public IP
      # no authorized_networks
    }
  }
}

# VULNERABLE: No SSL
resource "google_sql_database_instance" "main" {
  settings {
    ip_configuration {
      require_ssl = false
    }
  }
}
```

---

## Azure

### IAM

```hcl
# VULNERABLE: Owner role at subscription level
resource "azurerm_role_assignment" "ra" {
  role_definition_name = "Owner"
  scope                = "/subscriptions/12345"  # entire subscription
  principal_id         = data.azurerm_user_assigned_identity.app.principal_id
}

# VULNERABLE: Contributor with data access
resource "azurerm_role_assignment" "ra" {
  role_definition_name = "Contributor"
  scope                = azurerm_storage_account.data.id
}
```

```hcl
# SAFE: Specific role at resource level
resource "azurerm_role_assignment" "ra" {
  role_definition_name = "Storage Blob Data Reader"
  scope                = azurerm_storage_container.data.resource_manager_id
  principal_id         = data.azurerm_user_assigned_identity.app.principal_id
}
```

### Storage

```hcl
# VULNERABLE: Public blob container
resource "azurerm_storage_container" "data" {
  container_access_type = "blob"  # public read
}

# VULNERABLE: No encryption (custom key)
resource "azurerm_storage_account" "data" {
  # no customer_managed_key
  enable_https_traffic_only = false  # HTTP allowed
}
```

```hcl
# SAFE: Private with encryption
resource "azurerm_storage_container" "data" {
  container_access_type = "private"
}

resource "azurerm_storage_account" "data" {
  enable_https_traffic_only = true
  min_tls_version          = "TLS1_2"
}
```

### Key Vault

```hcl
# VULNERABLE: Key Vault with public access
resource "azurerm_key_vault" "vault" {
  network_acls {
    default_action = "Allow"  # public access
  }
  # no purge_protection_enabled
}

# VULNERABLE: Secret with long expiry
resource "azurerm_key_vault_secret" "db" {
  expiration_date = timeadd(timestamp(), "87600h")  # 10 years
}
```

```hcl
# SAFE: Restricted access + soft delete
resource "azurerm_key_vault" "vault" {
  network_acls {
    default_action = "Deny"
    virtual_network_subnet_ids = [azurerm_subnet.vault.id]
  }
  soft_delete_enabled        = true
  purge_protection_enabled   = true
}
```

### Azure SQL

```hcl
# VULNERABLE: Public SQL server
resource "azurerm_mssql_server" "main" {
  public_network_access_enabled = true
  # no firewall rules restricting access
}

# VULNERABLE: No threat detection
resource "azurerm_mssql_server" "main" {
  # no threat_detection_policy
}
```

```hcl
# SAFE: Restricted access
resource "azurerm_mssql_server" "main" {
  public_network_access_enabled = true
}

resource "azurerm_mssql_firewall_rule" "app" {
  start_ip_address = "10.0.0.1"
  end_ip_address   = "10.0.0.255"
}

resource "azurerm_mssql_server_security_alert_policy" "main" {
  state = "Enabled"
}
```