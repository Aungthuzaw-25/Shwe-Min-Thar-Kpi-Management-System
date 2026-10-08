# Email configuration

Application email is sent through the provider-neutral email service. In
**Super Admin → System Settings → Email Configuration**, add and configure
providers individually. The current registry offers Resend and SMTP; only one
configured provider can be active at a time.

New or edited configurations are saved disabled. Test the saved configuration,
then explicitly enable it to make it the active transactional email provider.
Enabling a different provider disables the previous one; disabling or removing
the active provider clears the active selection without selecting a fallback.
Disabled configurations can be re-enabled or removed and added again.
Pre-lifecycle single-provider settings are retained as disabled configurations
so they can be tested and deliberately activated.

## Resend

Set `RESEND_API_KEY` in the deployment environment, then add **Resend** and
configure the sender identity. Resend requires a verified sender domain, except
for its `onboarding@resend.dev` test sender, which is limited by Resend's test
mode rules. The API key is never stored in System Settings or returned to the
browser.

## SMTP

Add **SMTP** and configure the host, port, sender, and encryption mode
(`STARTTLS`, `SSL`, or `None`). If the server requires authentication, set
`SMTP_USERNAME` in System Settings and `SMTP_PASSWORD` in the deployment
environment. An unauthenticated SMTP server can be used without either value.
The password is not stored in System Settings or returned to the browser.

## Shared settings and diagnostics

From Email, From Name, and Support Email are shared sender/application settings.
Support Email is independent from From Email and is used for Reply-To and the
optional support footer.

**Test Connection** checks the saved provider configuration without sending an
email: Resend authentication and sender-domain status are checked through the
Resend API; SMTP connectivity and configured authentication are checked without
submitting a message. Tests do not enable providers. Missing sender, credential,
SMTP host, or connection errors are reported as sanitized diagnostics.

Provider metadata and configuration reuse the existing JSON `system_settings`
record. No provider secrets are stored there. Changes take effect on the next
email operation; updates to deployment environment secrets follow the hosting
platform's environment-update/restart behavior.

Railway, a VPS, Docker, and local development use the same code path. Provide
the relevant secrets in the runtime environment and select the provider in
System Settings; switching providers does not require business-logic changes.
