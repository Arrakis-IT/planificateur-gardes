# TODO

Items to resolve later. Move them to GitHub issues once the repository is published.

## Base image vulnerabilities without upstream fix (Trivy, 2026-10-12)

`trivy image` reports 49 HIGH (0 CRITICAL) findings in Debian 13 base packages, **none with a
fixed version available yet** (expat, util-linux, acl, ncurses, systemd libs, perl-base).
Python packages: none (pip removed from the runtime image).

Mitigation in place:
- The image runs `apt-get upgrade` at build time: rebuilding picks up Debian fixes as soon as
  they are published (Dependabot will bump the base image weekly once configured, phase 10).
- Most affected tools are unreachable at runtime: the container runs as a non-root user,
  read-only root filesystem, `cap_drop: ALL`, `no-new-privileges` (no mount / nsenter /
  login usage possible). The app parses no untrusted XML (expat is used by fontconfig on its
  own trusted configuration files).

Action: re-run `trivy image --severity HIGH,CRITICAL` before each release and rebuild when
fixes land.

## After the first deployment

- Run Mozilla HTTP Observatory against https://gardes.arrakis.lu (needs a public HTTPS host)
  and check the A+ estimate made offline in phase 9.
