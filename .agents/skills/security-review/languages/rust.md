# Rust Security Patterns

## Framework Detection

| Indicator | Framework |
|-----------|-----------|
| `actix_web`, `HttpServer`, `App::new` | Actix Web |
| `axum`, `Router`, `routing::get` | Axum |
| `rocket`, `#[launch]`, `#[get]` | Rocket |
| `warp`, `warp::filter`, `warp::path` | Warp |
| `sqlx`, `sqlx::query` | SQLx |
| `diesel`, `diesel::prelude` | Diesel |
| `serde`, `serde_json` | Serde (serialization) |

---

## Server-Controlled Values (NEVER Flag)

```rust
// SAFE: Server-controlled configuration
let api_url = std::env::var("API_URL").unwrap_or_default();
let resp = reqwest::get(&api_url).await; // NOT SSRF

// SAFE: Compile-time constants
const BASE_URL: &str = "https://api.internal";
let resp = reqwest::get(&format!("{}/health", BASE_URL)).await;
```

---

## SQL Injection

### SAFE: Parameterized Queries (SQLx)

```rust
// SAFE: SQLx parameterized query
let user = sqlx::query_as!(User, "SELECT * FROM users WHERE id = $1", id)
    .fetch_one(&pool)
    .await?;

// SAFE: SQLx with bind
let rows = sqlx::query("SELECT * FROM users WHERE name = ? AND active = ?")
    .bind(name)
    .bind(true)
    .fetch_all(&pool)
    .await?;
```

### SAFE: Diesel (Query Builder)

```rust
// SAFE: Diesel uses parameterized queries
let users = users::table
    .filter(users::name.eq(&name))
    .load::<User>(&conn)?;

// SAFE: Diesel schema queries are parameterized
users::table.find(id).first::<User>(&conn)?;
```

### VULNERABLE: String Interpolation

```rust
// VULNERABLE: SQL injection
let query = format!("SELECT * FROM users WHERE name = '{}'", name);
let rows = sqlx::query(&query).fetch_all(&pool).await?;

// VULNERABLE: String concatenation
let q = "SELECT * FROM users WHERE name = '".to_string() + &name + "'";
sqlx::query(&q).fetch_all(&pool).await?;
```

---

## XSS

### SAFE: Template Engines (Auto-escaped)

```rust
// SAFE: Askama templates auto-escape HTML
#[derive(Template)]
#[template(path = "user.html")]
struct UserTemplate { name: String }
// {{ name }} is auto-escaped

// SAFE: Tera auto-escapes by default
let mut ctx = tera::Context::new();
ctx.insert("name", &user_name);
tera.render("template.html", &ctx)?; // auto-escaped

// SAFE: maud macro escapes text
html! { div { (user_name) } } // auto-escaped
```

### VULNERABLE: Raw HTML Output

```rust
// VULNERABLE: Raw string to response
let name = params.get("name").unwrap_or("");
let body = format!("<div>{}</div>", name); // not escaped
HttpResponse::Ok().body(body)

// VULNERABLE: Tera with autoescape off
let mut tera = Tera::new("templates/**/*")?;
tera.autoescape_on(vec![]); // disable autoescape
// {{ user_input }} is NOT escaped
```

---

## Command Injection

### SAFE: Command::new with Args

```rust
// SAFE: Arguments as separate items (no shell)
let output = std::process::Command::new("git")
    .arg("commit")
    .arg("-m")
    .arg(&message)
    .output()?;
```

### VULNERABLE: Shell with User Input

```rust
// VULNERABLE: shell=true equivalent
let cmd = format!("echo {} > /tmp/file", input);
let output = std::process::Command::new("sh")
    .arg("-c")
    .arg(&cmd)
    .output()?;
```

---

## Unsafe Rust

### VULNERABLE: Unsafe Blocks with Untrusted Input

```rust
// VULNERABLE: Unsafe pointer arithmetic with user-controlled offset
unsafe {
    let ptr = base_ptr.offset(user_offset as isize);
    *ptr = value;
}

// VULNERABLE: Transmute with user-controlled type
unsafe {
    let value: u64 = std::mem::transmute(user_bytes);
}

// VULNERABLE: Unsafe FFI with untrusted input
unsafe {
    let c_str = std::ffi::CString::new(user_input).unwrap();
    external_c_function(c_str.as_ptr());
}
```

### SAFE: Checked Alternatives

```rust
// SAFE: Checked arithmetic
let result = base_ptr.checked_add(user_offset);

// SAFE: Safe FFI with validation
let c_str = std::ffi::CString::new(user_input)
    .map_err(|_| "invalid input")?; // checks for null bytes
unsafe { external_c_function(c_str.as_ptr()) }
```

---

## Deserialization

### SAFE: Serde JSON

```rust
// SAFE: serde_json::from_str is memory-safe
let data: MyStruct = serde_json::from_str(&body)?;

// SAFE: serde with strict types
#[derive(Deserialize)]
struct Request {
    name: String,
    count: u32,
}
let req: Request = serde_json::from_str(&body)?;
```

### VULNERABLE: Deserialization Gadget Chains

```rust
// VULNERABLE: serde_json with arbitrary types (if using untagged enums)
// Can allow type confusion if not careful
#[derive(Deserialize)]
#[serde(untagged)]
enum AnyType {
    String(String),
    Number(i64),
    Map(serde_json::Map<String, serde_json::Value>),
}
// Not inherently dangerous but can lead to logic bugs
```

---

## Path Traversal

### SAFE: Canonicalize + Check Prefix

```rust
// SAFE: Canonicalize and check prefix
let req_path = std::path::Path::new(&user_path);
let full = base_dir.join(req_path);
let canonical = full.canonicalize()?;
if !canonical.starts_with(&base_dir) {
    return Err("path traversal");
}

// SAFE: Normalize path components
let clean: PathBuf = user_path
    .split('/')
    .filter(|c| *c != ".." && !c.is_empty())
    .collect();
```

### VULNERABLE: Unvalidated Path

```rust
// VULNERABLE: Path traversal
let file = query.get("file").unwrap_or("");
let path = format!("/data/{}", file); // ../../../etc/passwd
let content = std::fs::read(path)?;
```

---

## Cryptography

### SAFE: Proper Crypto

```rust
// SAFE: argon2 for passwords
let hash = argon2::hash_password(password, &argon2_params)?;
argon2::verify_password(password, &hash)?;

// SAFE: ring for crypto operations
use ring::rand::SystemRandom;
let rng = SystemRandom::new();
let random_bytes = ring::rand::generate::<[u8; 32]>(&rng)?;

// SAFE: sha2 crate
use sha2::{Sha256, Digest};
let hash = Sha256::digest(data);
```

### VULNERABLE: Weak Crypto

```rust
// VULNERABLE: MD5 for passwords
use md5;
let hash = md5::compute(password.as_bytes());

// VULNERABLE: rand::thread_rng for security tokens
use rand::Rng;
let token: u32 = rand::thread_rng().gen();
```

---

## SSRF

### VULNERABLE: User-Controlled URL

```rust
// VULNERABLE: SSRF
let url = params.get("url").unwrap_or("");
let resp = reqwest::get(url).await?;
```

### SAFE: Server-Controlled or Validated

```rust
// SAFE: URL from config
let url = format!("{}/api/endpoint", config.base_url);
let resp = reqwest::get(&url).await?;

// SAFE: Validated URL
let parsed = url::Url::parse(user_url)?;
if !is_allowed_host(parsed.host_str()) {
    return Err("host not allowed");
}
let resp = reqwest::get(parsed.as_str()).await?;
```

---

## Memory Safety

### SAFE: Bounds-checked Access

```rust
// SAFE: Rust arrays/slices are bounds-checked
let val = vec.get(index)?; // returns None if out of bounds
let val = vec[index]; // panics if out of bounds (safe, no UB)
```

### VULNERABLE: Unsafe Memory Operations

```rust
// VULNERABLE: get_unchecked bypasses bounds checking
unsafe {
    let val = vec.get_unchecked(index); // can read out of bounds
}

// VULNERABLE: set_unchecked
unsafe {
    vec.set_unchecked(index, value); // can write out of bounds
}
```

---

## File Upload

### SAFE: Validated Upload

```rust
// SAFE: Validate content type and generate safe name
let content_type = file.content_type().unwrap_or("");
if !["image/png", "image/jpeg"].contains(&content_type) {
    return Err("invalid file type");
}
let safe_name = format!("{}.{}", uuid::Uuid::new_v4(), extension);
let path = upload_dir.join(&safe_name);
tokio::fs::write(path, file.bytes().await?).await?;
```

### VULNERABLE: Unrestricted Upload

```rust
// VULNERABLE: User-controlled filename
let filename = file.name().unwrap_or("unknown");
let path = format!("uploads/{}", filename); // path traversal via filename
tokio::fs::write(path, data).await?;
```

---

## Authentication & Sessions

### SAFE: Signed Session

```rust
// SAFE: Signed/encrypted session cookie
let session_key = std::env::var("SESSION_KEY")?;
let cookie = Cookie::build("session", signed_value)
    .http_only(true)
    .secure(true)
    .same_site(SameSite::Strict)
    .max_age(time::Duration::hours(1))
    .finish();
```

### VULNERABLE: Insecure Session

```rust
// VULNERABLE: Unsigned user ID in cookie
let cookie = Cookie::build("user_id", user_id.to_string())
    .finish(); // unsigned, forgeable
```