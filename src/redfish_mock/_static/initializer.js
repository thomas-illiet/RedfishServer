"use strict";

const profilePrefix = document.querySelector('meta[name="redfish-prefix"]').content;

window.ui = SwaggerUIBundle({
  url: profilePrefix + "/openapi.json",
  dom_id: "#swagger-ui",
  presets: [SwaggerUIBundle.presets.apis],
  layout: "BaseLayout",
  deepLinking: true,
  filter: true,
  docExpansion: "none",
  defaultModelsExpandDepth: -1,
  displayRequestDuration: true,
  showExtensions: true,
  persistAuthorization: false,
  validatorUrl: null,
  queryConfigEnabled: false,
  supportedSubmitMethods: ["get", "head", "post", "patch", "delete"],
  onComplete: function authorizeDevelopmentDefaults() {
    if (document.querySelector('meta[name="redfish-demo-auth"]').content === "true") {
      window.ui.preauthorizeBasic("basicAuth", "admin", "admin");
    }
  },
  requestInterceptor: function sameInstanceOnly(request) {
    const target = new URL(request.url, window.location.href);
    if (target.origin !== window.location.origin ||
        (profilePrefix && !target.pathname.startsWith(profilePrefix + "/"))) {
      throw new Error("Requests are restricted to this mock instance.");
    }
    // Use Swagger credentials, without the browser's HTTP-auth cache or prompts.
    request.credentials = "omit";
    return request;
  }
});
