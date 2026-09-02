# Kubernetes Security Reference

## Overview

Kubernetes misconfigurations can lead to container escapes, privilege escalation, unauthorized access to secrets, and cluster takeover. Review manifests, Helm charts, RBAC policies, and network policies.

---

## Pod Security

### Running as Root

```yaml
# VULNERABLE: Running as root without restrictions
apiVersion: v1
kind: Pod
spec:
  containers:
  - name: app
    image: app:latest
    # no securityContext
```

```yaml
# SAFE: Non-root with security context
apiVersion: v1
kind: Pod
spec:
  securityContext:
    runAsNonRoot: true
    runAsUser: 1000
    fsGroup: 1000
  containers:
  - name: app
    image: app:latest
    securityContext:
      runAsNonRoot: true
      runAsUser: 1000
      allowPrivilegeEscalation: false
      readOnlyRootFilesystem: true
      capabilities:
        drop:
        - ALL
```

### Privileged Containers

```yaml
# VULNERABLE: Privileged container (host access)
spec:
  containers:
  - name: app
    image: app:latest
    securityContext:
      privileged: true  # equivalent to root on host
```

### Dangerous Capabilities

```yaml
# VULNERABLE: Dangerous capabilities mounted
spec:
  containers:
  - name: app
    securityContext:
      capabilities:
        add:
        - SYS_ADMIN      # mount, namespace ops
        - NET_ADMIN       # network configuration
        - SYS_PTRACE      # process inspection
        - DAC_READ_SEARCH # bypass file permissions
```

### Host Path Mounts

```yaml
# VULNERABLE: Mounting host filesystem
spec:
  containers:
  - name: app
    volumeMounts:
    - mountPath: /host
      name: host
  volumes:
  - name: host
    hostPath:
      path: /            # entire host filesystem accessible

# VULNERABLE: Docker socket mounted
  - name: docker-sock
    hostPath:
      path: /var/run/docker.sock  # can create privileged containers
```

---

## RBAC

### Overly Permissive Roles

```yaml
# VULNERABLE: Cluster-admin granted broadly
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
subjects:
- kind: Group
  name: developers       # all developers get cluster-admin
roleRef:
  kind: ClusterRole
  name: cluster-admin    # full cluster access
```

```yaml
# VULNERABLE: Wildcard permissions
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
rules:
- apiGroups: ["*"]
  resources: ["*"]
  verbs: ["*"]            # full access to everything
```

```yaml
# SAFE: Least privilege
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
rules:
- apiGroups: [""]
  resources: ["pods", "pods/log"]
  verbs: ["get", "list", "watch"]
```

### ServiceAccount Token

```yaml
# VULNERABLE: ServiceAccount token mounted to every pod (pre-1.24 default)
spec:
  automountServiceAccountToken: true  # if not needed, this is a risk

# SAFE: Disable token mounting when not needed
spec:
  automountServiceAccountToken: false
  containers:
  - name: app
```

---

## Secrets

### Plaintext Secrets in Manifests

```yaml
# VULNERABLE: Secret in plaintext
apiVersion: v1
kind: Secret
data:
  password: cGFzc3dvcmQxMjM=  # base64, not encrypted

# VULNERABLE: Secret in env var directly
spec:
  containers:
  - name: app
    env:
    - name: DB_PASSWORD
      value: "supersecret123"  # plaintext in manifest
```

```yaml
# SAFE: Secret from external secret store
spec:
  containers:
  - name: app
    env:
    - name: DB_PASSWORD
      valueFrom:
        secretKeyRef:
          name: db-secret
          key: password
---
# Even better: use ExternalSecrets (Vault, AWS Secrets Manager)
apiVersion: external-secrets.io/v1
kind: ExternalSecret
spec:
  secretStoreRef:
    name: vault-backend
  target:
    name: db-secret
  data:
  - secretKey: password
    remoteRef:
      key: database/password
```

---

## Network Policies

### No Network Isolation

```yaml
# VULNERABLE: No network policy = all pods can talk to each other
# Missing NetworkPolicy means full mesh connectivity
```

```yaml
# SAFE: Default deny + explicit allow
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
spec:
  podSelector: {}
  policyTypes:
  - Ingress
  - Egress
  ingress: []   # deny all ingress
  egress: []    # deny all egress by default
---
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
spec:
  podSelector:
    matchLabels:
      app: frontend
  policyTypes:
  - Egress
  egress:
  - to:
    - podSelector:
        matchLabels:
          app: backend
    ports:
    - port: 8080
```

---

## Resource Limits

### No Limits (DoS Risk)

```yaml
# VULNERABLE: No resource limits
spec:
  containers:
  - name: app
    image: app:latest
    # no resources = can consume entire node
```

```yaml
# SAFE: Resource requests and limits
spec:
  containers:
  - name: app
    image: app:latest
    resources:
      requests:
        cpu: 100m
        memory: 128Mi
      limits:
        cpu: 500m
        memory: 512Mi
```

---

## Admission Controllers

### Pod Security Standards

```yaml
# SAFE: Enforce restricted Pod Security Standard
apiVersion: v1
kind: Namespace
metadata:
  name: production
  labels:
    pod-security.kubernetes.io/enforce: restricted
    pod-security.kubernetes.io/audit: restricted
    pod-security.kubernetes.io/warn: restricted
```

---

## Image Security

### Dangerous Image Patterns

```yaml
# VULNERABLE: latest tag (unpredictable)
image: app:latest

# VULNERABLE: No image pull policy
imagePullPolicy: Always  # or missing

# SAFE: Pin image digest
image: app@sha256:abc123def456...
imagePullPolicy: IfNotPresent
```

---

## API Server Exposure

### VULNERABLE: Anonymous Access

```yaml
# VULNERABLE: Anonymous auth enabled
apiServer:
  anonymous-auth: true

# VULNERABLE: Insecure port
apiServer:
  insecure-bind-address: 0.0.0.0
  insecure-port: 8080
```

### SAFE: Authenticated Access Only

```yaml
# SAFE: Anonymous auth disabled
apiServer:
  anonymous-auth: false
  authorization-mode: Node,RBAC
  secure-port: 6443
  bind-address: 0.0.0.0
```