# Security policy

Do not publish credentials, private memory data, user vaults, provider transcripts, logs, databases, or machine-specific configuration.

For private vulnerability reports, use the repository's GitHub private vulnerability reporting form. Do not post exploitable details in a public issue.

The memory service requires authentication for its HTTP MCP transport. Keep both HTTP and dashboard listeners on loopback unless a reviewed authenticated proxy is used. Dashboard access is not a substitute for service authentication.
