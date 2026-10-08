# Email configuration

Application email is sent through the provider-neutral email service. Select an
implemented provider in **Super Admin → System Settings → Email Configuration**;
email features and templates do not depend on the selected provider.

## Resend

Set `RESEND_API_KEY` in the deployment environment, then select **Resend** and
configure the sender identity. Resend requires a verified sender domain, except
for its `onboarding@resend.dev` test sender, which is limited by Resend's test
mode rules. The API key is never stored in System Settings or returned to the
browser.

## SMTP

Select **SMTP** and configure the host, port, sender, and encryption mode
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
submitting a message. Save changes before running the test. Provider settings
take effect on the next email operation; changes to deployment environment
secrets follow the hosting platform's environment-update/restart behavior.

Railway, a VPS, Docker, and local development use the same code path. Provide
the relevant secrets in the runtime environment and select the provider in
System Settings; switching providers does not require business-logic changes.
