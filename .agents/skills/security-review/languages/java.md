# Java Security Patterns

## Framework Detection

| Indicator | Framework |
|-----------|-----------|
| `org.springframework`, `@Controller`, `@RestController`, `@RequestMapping` | Spring |
| `jakarta.servlet`, `HttpServlet`, `doGet`, `doPost` | Servlet/JSP |
| `javax.persistence`, `@Entity`, `EntityManager` | JPA/Hibernate |
| `org.hibernate`, `SessionFactory`, `Criteria` | Hibernate |
| `import org.mybatis`, `@Mapper` | MyBatis |
| `import org.apache.struts` | Struts |
| `javax.xml.bind`, `@XmlRootElement` | JAXB |

---

## Server-Controlled Values (NEVER Flag)

```java
// SAFE: Server-controlled configuration
@Value("${api.url}")
private String apiUrl;

restTemplate.getForObject(apiUrl, String.class); // NOT SSRF - from application.properties/yml

// SAFE: Environment variables
String dbUrl = System.getenv("DATABASE_URL");
dataSource.setUrl(dbUrl); // server operator controls this

// SAFE: Constants
private static final String BASE_URL = "https://api.internal";
restTemplate.getForObject(BASE_URL + "/users", String.class);
```

---

## SQL Injection

### SAFE: JPA/Hibernate (Parameterized)

```java
// SAFE: JPQL with named parameters
@Query("SELECT u FROM User u WHERE u.name = :name")
List<User> findByName(@Param("name") String name);

// SAFE: Criteria API
CriteriaBuilder cb = em.getCriteriaBuilder();
CriteriaQuery<User> cq = cb.createQuery(User.class);
Root<User> root = cq.from(User.class);
cq.where(cb.equal(root.get("name"), name));

// SAFE: Native query with parameters
@Query(value = "SELECT * FROM users WHERE name = :name", nativeQuery = true)
List<User> findByNameNative(@Param("name") String name);
```

### SAFE: JDBC PreparedStatement

```java
// SAFE: PreparedStatement with parameters
PreparedStatement ps = conn.prepareStatement("SELECT * FROM users WHERE id = ?");
ps.setLong(1, userId);
ResultSet rs = ps.executeQuery();
```

### VULNERABLE: String Interpolation

```java
// VULNERABLE: SQL injection
String query = "SELECT * FROM users WHERE name = '" + name + "'";
Statement stmt = conn.createStatement();
ResultSet rs = stmt.executeQuery(query);

// VULNERABLE: JPQL with string concatenation
@Query("SELECT u FROM User u WHERE u.name = '" + name + "'")
// This is NOT parameterized

// VULNERABLE: Hibernate HQL injection
String hql = "FROM User WHERE name = '" + userInput + "'";
Query query = session.createQuery(hql);

// VULNERABLE: MyBatis with ${} (string substitution)
@Select("SELECT * FROM users WHERE name = '${name}'")
// Use #{} instead: "SELECT * FROM users WHERE name = #{name}"
```

### VULNERABLE: Dynamic Table/Column

```java
// VULNERABLE: Table name from user input
String table = request.getParameter("table");
String query = "SELECT * FROM " + table + " WHERE id = ?";
// Fix: use allowlist
```

---

## XSS

### SAFE: Thymeleaf (Auto-escaped)

```html
<!-- SAFE: Thymeleaf auto-escapes -->
<p th:text="${user.name}">Name</p>
<!-- th:utext does NOT escape - only for trusted content -->
```

### SAFE: JSP EL (Auto-escaped if configured)

```jsp
<!-- SAFE: JSTL c:out escapes by default -->
<c:out value="${user.name}"/>

<!-- SAFE: JSP EL is escaped if response encoding is set properly -->
<%@ page contentType="text/html; charset=UTF-8" %>
${user.name}
```

### VULNERABLE: Raw Output

```java
// VULNERABLE: Writing user input to response
response.getWriter().write("<div>" + userInput + "</div>");

// VULNERABLE: Thymeleaf th:utext with user input
// <p th:utext="${userInput}">...</p> -- NOT escaped

// VULNERABLE: JSP without escaping
<%= request.getParameter("name") %>

// VULNERABLE: Spring model attribute without escaping
model.addAttribute("comment", userInput);
// If rendered with th:utext or in a script context
```

---

## Command Injection

### SAFE: ProcessBuilder with Args

```java
// SAFE: ProcessBuilder with separate arguments
ProcessBuilder pb = new ProcessBuilder("git", "commit", "-m", message);
Process p = pb.start();

// SAFE: No shell involved
Process p = Runtime.getRuntime().exec(new String[]{"ls", "-la", dir});
```

### VULNERABLE: Shell with User Input

```java
// VULNERABLE: Shell invocation
String cmd = "git commit -m '" + message + "'";
Runtime.getRuntime().exec(new String[]{"sh", "-c", cmd});

// VULNERABLE: String concatenation in exec
String cmd = "curl " + userUrl;
Runtime.getRuntime().exec(cmd);
```

---

## Deserialization

### VULNERABLE: ObjectInputStream (Critical)

```java
// VULNERABLE: Java deserialization RCE
ObjectInputStream ois = new ObjectInputStream(request.getInputStream());
Object obj = ois.readObject(); // arbitrary class instantiation

// VULNERABLE: XMLDecoder
XMLDecoder decoder = new XMLDecoder(new ByteArrayInputStream(userInput.getBytes()));
Object obj = decoder.readObject(); // can call arbitrary methods

// VULNERABLE: XStream without security framework
XStream xstream = new XStream();
xstream.fromXML(userInput); // RCE if no allowlist
```

### SAFE: Safe Alternatives

```java
// SAFE: Jackson with type validation
ObjectMapper mapper = new ObjectMapper();
mapper.enable(DeserializationFeature.FAIL_ON_UNKNOWN_PROPERTIES);
MyClass obj = mapper.readValue(json, MyClass.class); // specific class, not Object

// SAFE: ObjectInputFilter (Java 9+)
ObjectInputStream ois = new ObjectInputStream(in);
ois.setObjectInputFilter(info -> {
    if (info.serialClass() != ExpectedClass.class) return REJECTED;
    return ALLOWED;
});
```

---

## Path Traversal

### SAFE: Path Validation

```java
// SAFE: Normalize and check prefix
Path requested = Paths.get(userInput).normalize();
if (!requested.startsWith(baseDir)) {
    throw new SecurityException("path traversal");
}
Path resolved = baseDir.resolve(requested).normalize();

// SAFE: Allowlist
Set<String> allowed = Set.of("report.pdf", "logo.png");
if (!allowed.contains(userInput)) {
    throw new SecurityException("file not allowed");
}
```

### VULNERABLE: Direct User Path

```java
// VULNERABLE: Path traversal
String file = request.getParameter("file");
File f = new File("/data/" + file); // ../../etc/passwd
new FileInputStream(f);

// VULNERABLE: Spring resource resolution
@GetMapping("/download/{file}")
void download(@PathVariable String file) {
    Path path = Paths.get("/data/" + file); // no validation
}
```

---

## SSRF

### VULNERABLE: User-Controlled URL

```java
// VULNERABLE: SSRF
String url = request.getParameter("url");
restTemplate.getForObject(url, String.class);

// VULNERABLE: Webhook URL from user
String webhookUrl = request.getParameter("webhook");
HttpClient client = HttpClient.newHttpClient();
HttpRequest req = HttpRequest.newBuilder()
    .uri(URI.create(webhookUrl))
    .POST(HttpRequest.BodyPublishers.ofString(body))
    .build();
```

### SAFE: Server-Controlled

```java
// SAFE: URL from config
@Value("${external.api.url}")
private String apiUrl;
restTemplate.getForObject(apiUrl + "/endpoint", String.class);
```

---

## Cryptography

### SAFE: Proper Crypto

```java
// SAFE: BCrypt for passwords
String hash = BCrypt.hashpw(password, BCrypt.gensalt());

// SAFE: SecureRandom for tokens
SecureRandom sr = new SecureRandom();
byte[] token = new byte[32];
sr.nextBytes(token);

// SAFE: PBKDF2
PBEKeySpec spec = new PBEKeySpec(password.toCharArray(), salt, 10000, 256);
SecretKeyFactory skf = SecretKeyFactory.getInstance("PBKDF2WithHmacSHA256");
byte[] hash = skf.generateSecret(spec).getEncoded();
```

### VULNERABLE: Weak Crypto

```java
// VULNERABLE: MD5 for passwords
MessageDigest md = MessageDigest.getInstance("MD5");
byte[] hash = md.digest(password.getBytes());

// VULNERABLE: java.util.Random for security tokens
Random rand = new Random();
int token = rand.nextInt(999999);

// VULNERABLE: ECB mode
Cipher cipher = Cipher.getInstance("AES/ECB/PKCS5Padding");
```

---

## SSRF via Spring

### VULNERABLE: Spring Actuator Without Auth

```yaml
# VULNERABLE: Actuator exposed without auth
management:
  endpoints:
    web:
      exposure:
        include: "*"  # all endpoints public
```

### SAFE: Restricted Actuator

```yaml
# SAFE: Auth required + limited endpoints
management:
  endpoints:
    web:
      exposure:
        include: health,info
  endpoint:
    health:
      show-details: when-authorized
```

---

## Authentication & Sessions

### SAFE: Spring Security

```java
// SAFE: Spring Security with proper session config
@Override
protected void configure(HttpSecurity http) throws Exception {
    http.sessionManagement()
        .sessionCreationPolicy(SessionCreationPolicy.IF_REQUIRED)
        .invalidSessionUrl("/login")
        .sessionFixation().migrateSession()
        .and()
        .csrf().csrfTokenRepository(CookieCsrfTokenRepository.withHttpOnlyFalse());
}
```

### VULNERABLE: Insecure Session

```java
// VULNERABLE: Unsigned cookie
Cookie cookie = new Cookie("user_id", String.valueOf(userId));
response.addCookie(cookie); // unsigned, forgeable, no HttpOnly/Secure

// VULNERABLE: CSRF disabled globally
@Override
protected void configure(HttpSecurity http) throws Exception {
    http.csrf().disable(); // blanket CSRF disable
}
```

---

## Expression Language Injection

### VULNERABLE: EL Injection

```java
// VULNERABLE: OGNL expression injection (Struts)
String expr = request.getParameter("expression");
Object result = Ognl.getValue(expr, context);

// VULNERABLE: SpEL injection
String expression = request.getParameter("expr");
ExpressionParser parser = new SpelExpressionParser();
Expression exp = parser.parseExpression(expression);
Object result = exp.getValue();

// VULNERABLE: MVEL injection
String script = request.getParameter("script");
Object result = MVEL.eval(script);
```

### SAFE: No Dynamic Expression Evaluation

```java
// SAFE: No expression evaluation on user input
// Use predefined expressions only
Expression exp = parser.parseExpression("user.name");
```

---

## XXE

### VULNERABLE: XML External Entity

```java
// VULNERABLE: XXE via DocumentBuilderFactory
DocumentBuilderFactory dbf = DocumentBuilderFactory.newInstance();
DocumentBuilder db = dbf.newDocumentBuilder();
Document doc = db.parse(userInput); // XXE enabled by default

// VULNERABLE: SAXParserFactory without disabling entities
SAXParserFactory spf = SAXParserFactory.newInstance();
SAXParser parser = spf.newSAXParser();
parser.parse(userInput, handler);
```

### SAFE: Disable External Entities

```java
// SAFE: DocumentBuilderFactory with XXE disabled
DocumentBuilderFactory dbf = DocumentBuilderFactory.newInstance();
dbf.setFeature("http://apache.org/xml/features/disallow-doctype-decl", true);
dbf.setFeature("http://xml.org/sax/features/external-general-entities", false);
dbf.setFeature("http://xml.org/sax/features/external-parameter-entities", false);
dbf.setXIncludeAware(false);
dbf.setExpandEntityReferences(false);
DocumentBuilder db = dbf.newDocumentBuilder();
```