# Security Policy

## Supported status

The package declares Python `>=3.11`; CI currently tests Python 3.11. There is
no published release/support matrix, long-term support policy, or guaranteed
security backport policy. The current `main` branch is the only documented
development target. Do not infer that old commits or deployments receive
security fixes.

## Reporting a vulnerability

Please do not disclose a suspected vulnerability in a public issue, pull
request, discussion, or chat.

If GitHub private vulnerability reporting is enabled for this repository, use
the repository's **Security** tab and **Report a vulnerability** action to
start a private GitHub Security Advisory.

This repository does not publish a dedicated security email address or other
verified private fallback contact. If private vulnerability reporting is not
available, do not post exploit details publicly. You may contact the owner
through a private contact method that is publicly listed on the
[owner's GitHub profile](https://github.com/ShreyaJ-27), if one is available,
and ask for a private reporting route without including sensitive details.

Include, when safe:

- Affected commit, version, route, or configuration.
- The impact and conditions required to reproduce it.
- Minimal reproduction steps or a proof of concept that does not access real
  data, persist changes, or affect other users.
- Relevant logs with secrets, personal information, and civic evidence
  removed.

Give maintainers a reasonable opportunity to investigate and prepare a fix
before any public disclosure. No response or remediation timeline is
committed. Avoid testing against systems or datasets you do not own or have
permission to use.

## Security-relevant limitations

- The optional FastAPI service is unauthenticated and intended for localhost
  and demo use, not public deployment. It has upload limits and path checks,
  but lacks deployment controls such as authentication/authorization,
  TLS/proxy configuration, rate and concurrency controls, operational request
  deadlines, and deployment monitoring. Keep it bound to `127.0.0.1`.
- `/triage` accepts a small GeoJSON upload, not media uploads. The service
  executes the canonical pipeline in a request-scoped temporary media root;
  referenced local media is not made available through the upload.
- The CLI and pipeline read local media paths. They constrain paths to the
  configured media root, but users must still choose trusted input and media
  roots and protect the local files and generated reports.
- Live model requests send selected images and prompts/facts to the configured
  local or hosted OpenAI-compatible provider. Provider logging, retention, and
  access are outside this repository's control.
- Real civic complaint text, precise locations, photographs, and metadata
  may contain sensitive personal information. The checked-in benchmark is
  synthetic; it does not make real-data processing risk-free.
- `.env` is ignored by Git, but users are responsible for keeping credentials
  out of commits, logs, reports, and support requests.
- The project has not claimed an independent security audit, compliance
  certification, or production-security review.