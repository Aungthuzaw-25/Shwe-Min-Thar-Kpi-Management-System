(() => {
  const root = document.getElementById("accountProfilePage");
  if (!root) return;

  const api = "/api/v1/account/me";
  const profileForm = root.querySelector("[data-account-profile-form]");
  const passwordForm = root.querySelector("[data-account-password-form]");
  const nameInput = root.querySelector("#acct-display-name");
  const phoneInput = root.querySelector("#acct-phone");
  const saveButton = root.querySelector("[data-profile-save]");
  const initialProfile = { display_name: nameInput.value, phone: phoneInput.value };
  let currentAvatarUrl = root.querySelector("[data-avatar-preview] .kpi-avatar__image")?.src || "";

  function banner(kind, message, state = "error") {
    const element = root.querySelector(`[data-acct-banner="${kind}"]`);
    if (!element) return;
    element.textContent = message;
    element.className = `acct-banner acct-banner--${state}`;
    element.hidden = !message;
  }

  async function request(path, options = {}) {
    const response = await fetch(path, { credentials: "same-origin", ...options });
    const contentType = response.headers.get("content-type") || "";
    const result = contentType.includes("application/json") ? await response.json() : null;
    if (!response.ok) throw Object.assign(new Error(result?.detail || "The request could not be completed."), { response, result });
    return result;
  }

  function clearErrors(form) {
    form.querySelectorAll("[data-field-error]").forEach((item) => { item.textContent = ""; });
  }

  function showErrors(form, result, fallbackMessage) {
    const fields = result?.errors || result?.field_errors || {};
    Object.entries(fields).forEach(([field, message]) => {
      const target = form.querySelector(`[data-field-error="${CSS.escape(field)}"]`);
      if (target) target.textContent = Array.isArray(message) ? message.join(" ") : String(message);
    });
    const detail = result?.detail;
    const message = typeof detail === "string" ? detail : fallbackMessage;
    if (message && !Object.keys(fields).length) return message;
    return "";
  }

  function updateSaveState() {
    saveButton.disabled = nameInput.value === initialProfile.display_name
      && phoneInput.value === initialProfile.phone;
  }
  nameInput.addEventListener("input", updateSaveState);
  phoneInput.addEventListener("input", updateSaveState);

  profileForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    clearErrors(profileForm);
    banner("profile", "");
    saveButton.disabled = true;
    saveButton.innerHTML = '<span class="acct-spinner" aria-hidden="true"></span> Saving…';
    try {
      const result = await request(api, {
        method: "PATCH",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ display_name: nameInput.value, phone: phoneInput.value }),
      });
      initialProfile.display_name = nameInput.value;
      initialProfile.phone = phoneInput.value;
      root.querySelector("[data-profile-summary-name]").textContent = result.display_name || nameInput.value;
      banner("profile", "Your profile was updated.", "success");
    } catch (error) {
      const message = showErrors(profileForm, error.result, error.message);
      banner("profile", message || "Please correct the highlighted fields.", "error");
    } finally {
      saveButton.textContent = "Save changes";
      updateSaveState();
    }
  });

  root.querySelector("[data-profile-cancel]").addEventListener("click", () => {
    nameInput.value = initialProfile.display_name;
    phoneInput.value = initialProfile.phone;
    clearErrors(profileForm);
    banner("profile", "");
    updateSaveState();
  });

  window.addEventListener("beforeunload", (event) => {
    if (nameInput.value !== initialProfile.display_name || phoneInput.value !== initialProfile.phone) {
      event.preventDefault();
    }
  });

  const avatarInput = root.querySelector("#acct-avatar-file");
  const avatarProgress = root.querySelector("[data-avatar-progress]");
  const removeAvatar = root.querySelector("[data-avatar-remove]");
  const preview = root.querySelector("[data-avatar-preview]");
  avatarInput.addEventListener("change", async () => {
    const file = avatarInput.files?.[0];
    if (!file) return;
    if (!["image/jpeg", "image/png", "image/webp"].includes(file.type) || file.size > 2 * 1024 * 1024) {
      banner("profile", "Choose a JPG, PNG or WebP image no larger than 2 MB.", "error");
      avatarInput.value = "";
      return;
    }
    const previous = currentAvatarUrl;
    const localUrl = URL.createObjectURL(file);
    const image = ensureAvatarImage(preview.querySelector(".kpi-avatar"));
    image.src = localUrl;
    avatarProgress.textContent = "Uploading photo…";
    banner("profile", "");
    const form = new FormData();
    form.append("file", file);
    try {
      const result = await request(`${api}/avatar`, { method: "POST", body: form });
      currentAvatarUrl = result.avatar_url;
      root.querySelectorAll(".kpi-avatar").forEach((avatar) => {
        avatar.classList.add("kpi-avatar--photo");
        avatar.classList.remove("kpi-avatar--image-failed");
        ensureAvatarImage(avatar).src = result.avatar_url;
      });
      removeAvatar.hidden = false;
      avatarProgress.textContent = "";
      banner("profile", "Profile photo updated.", "success");
    } catch (error) {
      image.src = previous;
      avatarProgress.textContent = "";
      banner("profile", error.message, "error");
    } finally {
      URL.revokeObjectURL(localUrl);
      avatarInput.value = "";
    }
  });

  function ensureAvatarImage(avatar) {
    let image = avatar.querySelector(".kpi-avatar__image");
    if (!image) {
      image = document.createElement("img");
      image.className = "kpi-avatar__image";
      image.alt = avatar.getAttribute("aria-label") || "Profile photo";
      const sizeClass = [...avatar.classList].find((className) => /^kpi-avatar--\d+$/.test(className));
      image.width = Number(sizeClass?.replace("kpi-avatar--", "")) || 96;
      image.height = image.width;
      image.loading = "lazy";
      image.dataset.kpiAvatarImage = "";
      avatar.prepend(image);
      avatar.querySelector(".kpi-avatar__initials")?.classList.add("kpi-avatar__fallback");
    }
    return image;
  }

  removeAvatar.addEventListener("click", async () => {
    removeAvatar.disabled = true;
    try {
      await request(`${api}/avatar`, { method: "DELETE" });
      window.location.assign("/account/profile?avatar_removed=1");
    } catch (error) {
      banner("profile", error.message, "error");
    } finally {
      removeAvatar.disabled = false;
    }
  });

  root.querySelectorAll("[data-password-toggle]").forEach((button) => {
    button.addEventListener("click", () => {
      const input = root.querySelector(`#${CSS.escape(button.dataset.passwordToggle)}`);
      const show = input.type === "password";
      input.type = show ? "text" : "password";
      button.textContent = show ? "Hide" : "Show";
      button.setAttribute("aria-label", `${show ? "Hide" : "Show"} ${input.labels[0]?.textContent || "password"}`);
      button.setAttribute("aria-pressed", String(show));
    });
  });

  const newPassword = root.querySelector("#acct-new-password");
  const meter = root.querySelector("[data-password-meter]");
  const strengthLabel = root.querySelector("[data-password-strength]");
  const checklist = root.querySelector("[data-password-checklist]");
  function updatePasswordRules() {
    const value = newPassword.value;
    const current = root.querySelector("#acct-current-password")?.value || "";
    const name = nameInput.value.toLocaleLowerCase();
    const email = root.querySelector("#acct-email").value.split("@")[0].toLocaleLowerCase();
    const checks = {
      length: value.length >= 10 && value.length <= 128,
      common: value.length > 0 && !new Set(["password", "password1", "password123", "1234567890", "qwerty123", "letmein", "welcome123", "admin123"]).has(value.toLocaleLowerCase()),
      different: value.length > 0 && value !== current,
      identity: value.length > 0 && ![name, email].filter(Boolean).some((part) => value.toLocaleLowerCase().includes(part)),
    };
    Object.entries(checks).forEach(([key, met]) => checklist.querySelector(`[data-password-rule="${key}"]`).dataset.met = String(met));
    const strength = Object.values(checks).filter(Boolean).length;
    meter.dataset.strength = String(strength);
    strengthLabel.textContent = ["Too weak", "Weak", "Good", "Strong", "Strong"][strength];
  }
  newPassword.addEventListener("input", updatePasswordRules);
  root.querySelector("#acct-current-password")?.addEventListener("input", updatePasswordRules);
  nameInput.addEventListener("input", updatePasswordRules);

  passwordForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    clearErrors(passwordForm);
    banner("password", "");
    const submit = root.querySelector("[data-password-submit]");
    submit.disabled = true;
    submit.innerHTML = '<span class="acct-spinner" aria-hidden="true"></span> Updating…';
    try {
      await request(`${api}/password`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          current_password: root.querySelector("#acct-current-password")?.value || null,
          new_password: newPassword.value,
          confirm_password: root.querySelector("#acct-confirm-password").value,
        }),
      });
      passwordForm.reset();
      updatePasswordRules();
      banner("password", "Password changed. Other devices were signed out.", "success");
    } catch (error) {
      const message = showErrors(passwordForm, error.result, error.message);
      banner("password", message || "Please correct the highlighted fields.", "error");
    } finally {
      submit.disabled = false;
      submit.textContent = root.dataset.localPasswordEnabled === "true" ? "Change password" : "Set password";
    }
  });

  async function reauthenticate() {
    if (root.dataset.localPasswordEnabled !== "true") return true;
    const currentPassword = root.querySelector("#acct-current-password")?.value;
    if (!currentPassword) {
      banner("connected", "Enter your current password in the Security section before managing connected accounts.", "error");
      return false;
    }
    await request(`${api}/reauth`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ current_password: currentPassword }),
    });
    return true;
  }

  root.querySelectorAll("[data-provider-link]").forEach((button) => {
    button.addEventListener("click", async () => {
      button.disabled = true;
      try {
        if (await reauthenticate()) window.location.assign(`/api/v1/auth/${button.dataset.providerLink}/link/start`);
      } catch (error) {
        banner("connected", error.message, "error");
      } finally {
        button.disabled = false;
      }
    });
  });

  root.querySelectorAll("[data-provider-unlink]").forEach((button) => {
    button.addEventListener("click", async () => {
      if (!(await window.appConfirm("Disconnect this sign-in method?", { title: "Disconnect account", confirmText: "Disconnect", cancelText: "Cancel" }))) return;
      button.disabled = true;
      try {
        if (!(await reauthenticate())) return;
        const provider = button.dataset.providerUnlink;
        await request(`/api/v1/auth/${provider}/link`, { method: "DELETE" });
        window.location.reload();
      } catch (error) {
        banner("connected", error.message, "error");
      } finally {
        button.disabled = false;
      }
    });
  });

  const params = new URLSearchParams(window.location.search);
  if (params.has("oauth_linked")) banner("connected", "Account connected successfully.", "success");
  else if (params.has("oauth_error")) banner("connected", params.get("oauth_error"), "error");
  if (params.has("reauthenticated")) banner("password", "Identity verified. You can now set your password.", "success");
  if (params.has("avatar_removed")) banner("profile", "Profile photo removed.", "success");
})();
