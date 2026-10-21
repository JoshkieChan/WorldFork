# Security

WorldFork is a local research application for one trusted operator. Keep it on loopback. It has no login, tenant isolation, TLS termination, or authorization model for a public deployment.

## Trust boundaries

The browser submits untrusted scenario data to the Python service. The service validates types, ranges, allowed fields, request origin, and the local request token before running a write operation. The request token limits cross-site writes; it does not authenticate local users. Other software running as the same operating-system user may access the service and local files.

The SQLite run store contains scenario names, inputs, and computed results. Treat those records and exports as local user data. They are not encrypted by the application. Use operating-system account protection and disk encryption where required.

The optional language interface sends a user-entered prompt to OpenAI over HTTPS. The provider credential stays in the server environment. Model output is untrusted data and is validated against the scenario schema. The model has no tools or executable output path.

The HTTP service checks the host and supplied origin, rejects cross-site requests, and requires a random process-local token for writes. Static assets use an allowlist and resolved-path containment. JSON parsing rejects duplicate keys and non-standard numeric constants; scenario validation rejects non-finite numbers. The response policy prevents framing and inline scripts. Provider calls use a fixed HTTPS endpoint with redirects and ambient proxies disabled. Compute, request, response, and history limits are listed in [architecture.md](docs/architecture.md).

## Operational guidance

- Keep `.env` files, API keys, and `.worldfork/` out of source control and shared archives.
- Do not expose the service through a public tunnel, reverse proxy, or a non-loopback bind address without designing authentication, authorization, TLS, deployment limits, and monitoring.
- Do not put private data into scenario names or prompts. Use the included synthetic dataset for experimentation.
- Stop the service before removing the local run store. Retain only exports that you need.
- Rotate a provider key at the provider if it is disclosed. Deleting it from a file does not revoke it.
- Pin and review dependency updates. Re-run the service and engine checks after changes.

## Reporting a problem

For a local copy, report issues privately to the project owner through a trusted channel. Include the affected version, reproduction steps, expected behavior, and a minimal example with no credentials or personal data. Do not publish working secrets in an issue or attach a private run database.

The security checks in this project cover selected application paths. They are not a penetration test, a third-party audit, or a guarantee that vulnerabilities are absent. The current verification scope is recorded in [docs/validation.md](docs/validation.md).
