# CI/CD Security Reference

## Overview

CI/CD pipelines are high-value targets: they often have access to production credentials, source code, and deployment keys. Compromise of a pipeline can lead to supply chain attacks, secret theft, and unauthorized production access.

---

## GitHub Actions

### Pull Request Target Injection

```yaml
# VULNERABLE: Code injection via pull_request_target
on:
  pull_request_target:
    types: [opened]

jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          ref: ${{ github.event.pull_request.head.sha }}  # checks out PR code
      - run: |
          npm install   # runs PR-controlled package.json with repo secrets
        env:
          NPM_TOKEN: ${{ secrets.NPM_TOKEN }}  # secret exposed to untrusted code
```

```yaml
# SAFE: Use pull_request for untrusted code, or isolate secrets
on:
  pull_request:
    types: [opened]

jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: npm install  # no secrets available to PR code
```

### Expression Injection

```yaml
# VULNERABLE: Script injection via issue/PR title or body
on:
  issues:
    types: [opened]

jobs:
  process:
    runs-on: ubuntu-latest
    steps:
      - run: |
          echo "${{ github.event.issue.title }}"  # injection point
          echo "${{ github.event.pull_request.body }}"  # injection point
```

```yaml
# SAFE: Use environment variables
steps:
  - env:
      ISSUE_TITLE: ${{ github.event.issue.title }}
    run: |
      echo "$ISSUE_TITLE"  # shell-escaped via env var
```

### Secret Exposure

```yaml
# VULNERABLE: Secret printed in logs
steps:
  - run: |
      echo "Token: $TOKEN"
    env:
      TOKEN: ${{ secrets.API_TOKEN }}

# VULNERABLE: Secret in build artifact
steps:
  - run: |
      echo "password=$SECRET" >> .env
    env:
      SECRET: ${{ secrets.DB_PASSWORD }}
  - uses: actions/upload-artifact@v3
    with:
      name: config
      path: .env  # secret in downloadable artifact
```

```yaml
# SAFE: Masked automatically
steps:
  - run: |
      curl -H "Authorization: Bearer $TOKEN" https://api.example.com
    env:
      TOKEN: ${{ secrets.API_TOKEN }}  # automatically masked in logs
```

### Runner Security

```yaml
# VULNERABLE: Self-hosted runner with persistent state
jobs:
  build:
    runs-on: self-hosted  # shared runner, secrets may persist

# VULNERABLE: Workflow runs on tag/branch without review
on:
  push:
    branches: [main]
# No approval required, any push triggers deployment with secrets
```

```yaml
# SAFE: Ephemeral runners + environment protection
jobs:
  deploy:
    runs-on: ubuntu-latest
    environment:
      name: production
      url: https://app.example.com
    # requires manual approval if environment protection rules set
```

---

## GitLab CI

### Secret Leakage

```yaml
# VULNERABLE: Secret in variables printed
build:
  script:
    - echo "Using $DEPLOY_TOKEN"  # visible in job logs

# VULNERABLE: Secret passed to untrusted script
build:
  script:
    - ./user-provided-script.sh  # script from repo, has access to CI vars
  variables:
    DEPLOY_TOKEN: $CI_DEPLOY_TOKEN
```

```yaml
# SAFE: Masked variables
build:
  script:
    - curl -H "Authorization: Bearer $DEPLOY_TOKEN" https://api.example.com
  variables:
    DEPLOY_TOKEN: $CI_DEPLOY_TOKEN  # GitLab masks this if configured
```

### Untrusted Pipeline Execution

```yaml
# VULNERABLE: Running untrusted CI on protected branch
build:
  rules:
    - if: $CI_PIPELINE_SOURCE == "merge_request_event"
  script:
    - make deploy
  # Merge request from fork can trigger with secrets
```

```yaml
# SAFE: Separate jobs for MR and protected branches
test:
  rules:
    - if: $CI_PIPELINE_SOURCE == "merge_request_event"
  script:
    - make test  # no secrets

deploy:
  rules:
    - if: $CI_COMMIT_BRANCH == "main"
  script:
    - make deploy
  variables:
    DEPLOY_TOKEN: $CI_DEPLOY_TOKEN
```

---

## Dependency Security

### Unpinned Dependencies

```yaml
# VULNERABLE: Unpinned action versions
steps:
  - uses: actions/checkout@main  # mutable
  - uses: actions/setup-node@v4  # tag, not digest

# VULNERABLE: Third-party action without review
steps:
  - uses: randomuser/setup-tool@v1  # can be updated maliciously
```

```yaml
# SAFE: Pinned to commit SHA
steps:
  - uses: actions/checkout@8e5e7e5cb8c3156cbe153f401922f1f5f9e4e5ad  # v4.1.0
  - uses: actions/setup-node@1e60f620b952fc0b6206119e0df9dd6c1f6f6c5e  # v4.0.0
```

---

## Artifact Poisoning

### Cache/Artifact Tampering

```yaml
# VULNERABLE: Cache writable by any workflow
jobs:
  build:
    steps:
      - uses: actions/cache@v3
        with:
          path: ~/.npm
          key: npm-${{ hashFiles('package-lock.json') }}
      # If PR can modify cache, it can poison it for all builds

# VULNERABLE: Build output used without verification
jobs:
  build:
    steps:
      - run: npm run build
      - uses: actions/upload-artifact@v3
        with:
          name: dist
          path: dist/

  deploy:
    needs: build
    steps:
      - uses: actions/download-artifact@v3
        with:
          name: dist
      - run: cp -r dist/* /var/www/  # deploys untrusted build
```

```yaml
# SAFE: Verify provenance and scope cache
jobs:
  build:
    steps:
      - uses: actions/cache@v3
        with:
          path: ~/.npm
          key: npm-${{ runner.os }}-${{ hashFiles('package-lock.json') }}
          # cache scoped to OS
      - run: npm ci && npm run build
      - name: Verify build
        run: |
          test -f dist/index.html
          npm audit --audit-level=high
```

---

## Deployment Security

### Missing Approval Gates

```yaml
# VULNERABLE: Auto-deploy to production without approval
jobs:
  deploy-production:
    if: github.ref == 'refs/heads/main'
    steps:
      - run: deploy.sh production
    # anyone can push to main and trigger deploy
```

```yaml
# SAFE: Environment protection + required reviewers
jobs:
  deploy-production:
    environment:
      name: production
      # GitHub requires manual approval if configured
    if: github.ref == 'refs/heads/main'
    steps:
      - run: deploy.sh production
```

### OIDC Token Leakage

```yaml
# VULNERABLE: OIDC token with broad audience
permissions:
  id-token: write  # broad write
  contents: read

# VULNERABLE: OIDC token passed to untrusted script
steps:
  - run: ./deploy.sh
    env:
      AWS_WEB_IDENTITY_TOKEN: ${{ env.AWS_WEB_IDENTITY_TOKEN }}
```

```yaml
# SAFE: Minimal permissions + restricted audience
permissions:
  id-token: write
  contents: read
steps:
  - uses: aws-actions/configure-aws-credentials@v4
    with:
      role-to-assume: arn:aws:iam::123:role/github-actions
      aws-region: us-east-1
      # OIDC handled by action, not exposed to scripts
```