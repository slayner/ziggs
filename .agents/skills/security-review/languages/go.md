# Go Security Patterns

## Framework Detection

| Indicator | Framework |
|-----------|-----------|
| `net/http`, `http.HandleFunc`, `http.Handler` | net/http |
| `gin-gonic/gin`, `gin.Engine` | Gin |
| `echo`, `e.GET`, `e.POST` | Echo |
| `fiber`, `fiber.New()` | Fiber |
| `gorm.io/gorm`, `gorm.Open` | GORM |
| `database/sql`, `sql.DB` | database/sql |

---

## Server-Controlled Values (NEVER Flag)

Go configuration via env vars, flags, or config files is server-controlled:

```go
// SAFE: Server-controlled configuration
url := os.Getenv("API_URL")
resp, _ := http.Get(url) // NOT SSRF - operator sets API_URL

// SAFE: Constants
const BaseURL = "https://api.internal"
resp, _ := http.Get(BaseURL + "/health")

// SAFE: Config struct loaded at startup
cfg := loadConfig() // from file/env at init
db, _ := sql.Open("postgres", cfg.DBUrl)
```

**Only flag if:**
- The value comes from `r.URL.Query()`, `r.FormValue()`, `r.Body`, or `r.Header`
- The value is read from user-controlled database content

---

## SQL Injection

### SAFE: Parameterized Queries (database/sql)

```go
// SAFE: Parameterized query
row := db.QueryRow("SELECT * FROM users WHERE id = $1", id)
rows, err := db.Query("SELECT * FROM users WHERE name = ? AND active = ?", name, true)

// SAFE: Prepared statement
stmt, err := db.Prepare("SELECT * FROM users WHERE id = $1")
defer stmt.Close()
row := stmt.QueryRow(id)
```

### SAFE: GORM (Auto-parameterized)

```go
// SAFE: GORM parameterizes automatically
db.Where("name = ?", name).First(&user)
db.Where("id IN ?", ids).Find(&users)
db.Raw("SELECT * FROM users WHERE id = ?", id).Scan(&user)

// SAFE: GORM struct-based queries
db.First(&user, name) // uses parameterized WHERE
```

### VULNERABLE: String Interpolation

```go
// VULNERABLE: SQL injection
query := fmt.Sprintf("SELECT * FROM users WHERE name = '%s'", r.URL.Query().Get("name"))
rows, _ := db.Query(query)

// VULNERABLE: SQL injection via string concatenation
name := r.FormValue("name")
query := "SELECT * FROM users WHERE name = '" + name + "'"
rows, _ := db.Query(query)

// VULNERABLE: GORM raw with string interpolation
db.Raw(fmt.Sprintf("SELECT * FROM users WHERE name = '%s'", name)).Scan(&user)
```

### VULNERABLE: Dynamic Table/Column Names

```go
// VULNERABLE: Table name from user input
table := r.URL.Query().Get("table")
query := fmt.Sprintf("SELECT * FROM %s WHERE id = ?", table)
// Fix: use allowlist
allowedTables := map[string]bool{"users": true, "orders": true}
if !allowedTables[table] { return error }
```

---

## XSS

### SAFE: html/template (Auto-escaped)

```go
// SAFE: html/template auto-escapes
tmpl, _ := template.New("page").Parse(`<div>{{.Name}}</div>`)
tmpl.Execute(w, data) // Name is HTML-escaped

// SAFE: template.HTML only for trusted content
const navBar = template.HTML("<nav>...</nav>") // constant, not user input
```

### VULNERABLE: text/template for HTML

```go
// VULNERABLE: text/template does NOT escape HTML
tmpl, _ := texttemplate.New("page").Parse(`<div>{{.Name}}</div>`)
tmpl.Execute(w, data) // Name is NOT escaped

// VULNERABLE: Writing user input directly to ResponseWriter
fmt.Fprintf(w, "<div>%s</div>", r.URL.Query().Get("name"))

// VULNERABLE: template.HTML with user input
userInput := r.FormValue("comment")
tmpl.Execute(w, map[string]interface{}{"comment": template.HTML(userInput)})
```

---

## Command Injection

### SAFE: exec.Command with Args

```go
// SAFE: Arguments passed as separate args (no shell interpretation)
cmd := exec.Command("git", "commit", "-m", message)
output, err := cmd.CombinedOutput()

// SAFE: No shell involved
cmd := exec.Command("ls", "-la", dir)
```

### VULNERABLE: Shell with User Input

```go
// VULNERABLE: Shell invocation with user input
cmd := exec.Command("sh", "-c", "git commit -m '"+message+"'")
cmd := exec.Command("bash", "-c", fmt.Sprintf("echo %s > /tmp/file", userInput))

// VULNERABLE: os/exec with shell
cmd := exec.Command("sh", "-c", "curl " + url)
```

---

## Path Traversal

### SAFE: filepath.Clean with Validation

```go
// SAFE: Clean and validate path
requestedFile := r.URL.Query().Get("file")
cleanPath := filepath.Clean(requestedFile)
if strings.HasPrefix(cleanPath, "..") || filepath.IsAbs(cleanPath) {
    return error
}
fullPath := filepath.Join(baseDir, cleanPath)

// SAFE: Allowlist approach
allowedFiles := map[string]string{
    "report": "/data/reports/report.pdf",
    "logo":   "/data/assets/logo.png",
}
if path, ok := allowedFiles[requestedFile]; ok {
    serveFile(path)
}
```

### VULNERABLE: Direct User Path

```go
// VULNERABLE: Path traversal
file := r.URL.Query().Get("file")
data, _ := os.ReadFile("/var/data/" + file) // ../etc/passwd

// VULNERABLE: filepath.Join doesn't prevent traversal
file := r.URL.Query().Get("file")
fullPath := filepath.Join(baseDir, file) // if file = "../../etc/passwd"
data, _ := os.ReadFile(fullPath)
```

---

## SSRF

### VULNERABLE: User-Controlled URL

```go
// VULNERABLE: SSRF
targetURL := r.URL.Query().Get("url")
resp, _ := http.Get(targetURL)

// VULNERABLE: Webhook URL from user
webhookURL := r.FormValue("webhook_url")
_, _ = http.Post(webhookURL, "application/json", body)
```

### SAFE: Server-Controlled URL

```go
// SAFE: URL from config
resp, _ := http.Get(cfg.ExternalAPIURL + "/endpoint")

// SAFE: Validated URL
parsedURL, _ := url.Parse(targetURL)
if !isAllowedHost(parsedURL.Hostname()) {
    return error
}
resp, _ := http.Get(parsedURL.String())
```

---

## Deserialization

### SAFE: JSON (encoding/json)

```go
// SAFE: JSON unmarshal is not dangerous
var data struct{ Name string `json:"name"` }
json.NewDecoder(r.Body).Decode(&data)
```

### VULNERABLE: Gob with Untrusted Data

```go
// VULNERABLE: Gob can instantiate arbitrary types
var data interface{}
gob.NewDecoder(r.Body).Decode(&data) // can instantiate arbitrary types

// VULNERABLE: Unsafe deserialization
// gob decoding into interface{} with untrusted input
```

---

## Cryptography

### SAFE: Proper Crypto

```go
// SAFE: bcrypt for passwords
hashed, _ := bcrypt.GenerateFromPassword([]byte(password), bcrypt.DefaultCost)

// SAFE: crypto/rand for tokens
b := make([]byte, 32)
rand.Read(b)
token := hex.EncodeToString(b)

// SAFE: crypto/sha256 for hashing
h := sha256.Sum256([]byte(data))
```

### VULNERABLE: Weak Crypto

```go
// VULNERABLE: MD5 for passwords
h := md5.Sum([]byte(password))

// VULNERABLE: math/rand for security tokens
token := strconv.Itoa(rand.Intn(999999))

// VULNERABLE: Custom crypto
encrypted := xorCipher(data, key) // roll-your-own crypto
```

---

## Authentication & Sessions

### SAFE: Secure Session

```go
// SAFE: Signed session cookie
store := sessions.NewCookieStore([]byte(secretKey))
store.Options = &sessions.Options{
    HttpOnly: true,
    Secure:   true,
    SameSite: http.SameSiteStrictMode,
    MaxAge:   3600,
}
```

### VULNERABLE: Insecure Session

```go
// VULNERABLE: Unsigned cookie
http.SetCookie(w, &http.Cookie{
    Name:  "user_id",
    Value: strconv.Itoa(userID), // unsigned, forgeable
})

// VULNERABLE: Missing HttpOnly/Secure
http.SetCookie(w, &http.Cookie{
    Name:     "session",
    Value:    token,
    HttpOnly: false, // accessible via JS
    Secure:   false, // sent over HTTP
})
```

---

## CSRF

### SAFE: CSRF Token

```go
// SAFE: gorilla/csrf middleware
csrf.Protect([]byte(secretKey))(handler)

// SAFE: Double-submit cookie
if r.FormValue("csrf_token") != getCSRFCookie(r) {
    return error
}
```

### VULNERABLE: No CSRF Protection

```go
// VULNERABLE: State-changing GET
http.HandleFunc("/delete-account", func(w http.ResponseWriter, r *http.Request) {
    if r.Method == "GET" { // GET for state change
        deleteUser(r.FormValue("user_id"))
    }
})
```

---

## File Upload

### VULNERABLE: Unrestricted Upload

```go
// VULNERABLE: No validation
r.ParseMultipartForm(10 << 20)
file, header, _ := r.FormFile("upload")
dst, _ := os.Create("/uploads/" + header.Filename) // user-controlled name
io.Copy(dst, file)
```

### SAFE: Validated Upload

```go
// SAFE: Validate filename and content type
allowedTypes := map[string]bool{"image/png": true, "image/jpeg": true}
contentType := header.Header.Get("Content-Type")
if !allowedTypes[contentType] {
    return error
}
safeName := uuid.New().String() + filepath.Ext(header.Filename)
dst, _ := os.Create(filepath.Join(uploadDir, safeName))
```

---

## Context Timeout

### SAFE: Context with Timeout

```go
// SAFE: Context prevents resource exhaustion
ctx, cancel := context.WithTimeout(r.Context(), 10*time.Second)
defer cancel()
result := db.QueryRowContext(ctx, query, id)
```

### VULNERABLE: No Timeout

```go
// VULNERABLE: No timeout allows DoS
resp, _ := http.Get(userProvidedURL) // no timeout
body, _ := io.ReadAll(resp.Body)     // unbounded read
```