(() => {
  const root = document.getElementById("accountProfilePage");
  if (!root) return;

  const api = "/api/v1/account/me";
  const profileForm = root.querySelector("[data-account-profile-form]");
  const passwordForm = root.querySelector("[data-account-password-form]");
  const nameInput = root.querySelector("#acct-display-name");
  const phoneInput = root.querySelector("#acct-phone");
  const emailInput = root.querySelector("#acct-email");
  const isSuperAdmin = root.dataset.superAdmin === "true";
  const saveButton = root.querySelector("[data-profile-save]");
  const initialProfile = {
    display_name: nameInput.value,
    phone: phoneInput.value,
    email: emailInput.value,
  };
  let currentAvatarUrl = root.querySelector("[data-avatar-preview] .kpi-avatar__image")?.src || "";
  let initialAvatarUrl = currentAvatarUrl;
  let pendingAvatarFile = null;
  let avatarRemovalPending = false;
  let previewObjectUrl = "";

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
    const profileChanged = nameInput.value !== initialProfile.display_name
      || phoneInput.value !== initialProfile.phone
      || (isSuperAdmin && emailInput.value !== initialProfile.email);
    saveButton.disabled = !profileChanged && !pendingAvatarFile && !avatarRemovalPending;
  }
  nameInput.addEventListener("input", updateSaveState);
  phoneInput.addEventListener("input", updateSaveState);
  if (isSuperAdmin) {
    emailInput.addEventListener("input", () => {
      root.querySelector('[data-field-error="email"]').textContent = "";
      updateSaveState();
    });
  }

  profileForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    clearErrors(profileForm);
    banner("profile", "");
    if (isSuperAdmin && !emailInput.checkValidity()) {
      root.querySelector('[data-field-error="email"]').textContent = "Enter a valid email address.";
      emailInput.focus();
      return;
    }
    saveButton.disabled = true;
    saveButton.innerHTML = '<span class="acct-spinner" aria-hidden="true"></span> Saving…';
    try {
      const profileChanged = nameInput.value !== initialProfile.display_name
        || phoneInput.value !== initialProfile.phone
        || (isSuperAdmin && emailInput.value !== initialProfile.email);
      if (profileChanged) {
        const payload = { display_name: nameInput.value, phone: phoneInput.value };
        if (isSuperAdmin && emailInput.value !== initialProfile.email) payload.email = emailInput.value;
        const result = await request(api, {
          method: "PATCH",
          headers: { "content-type": "application/json" },
          body: JSON.stringify(payload),
        });
        initialProfile.display_name = nameInput.value;
        initialProfile.phone = phoneInput.value;
        initialProfile.email = result.email || emailInput.value;
        emailInput.value = initialProfile.email;
        root.querySelector("[data-profile-summary-name]").textContent = result.display_name || nameInput.value;
        root.querySelector("[data-profile-summary-email]").textContent = initialProfile.email;
      }

      if (pendingAvatarFile) {
        const form = new FormData();
        form.append("file", pendingAvatarFile);
        const result = await request(`${api}/avatar`, { method: "POST", body: form });
        currentAvatarUrl = result.avatar_url;
        initialAvatarUrl = currentAvatarUrl;
        pendingAvatarFile = null;
        avatarInput.value = "";
        if (previewObjectUrl) URL.revokeObjectURL(previewObjectUrl);
        previewObjectUrl = "";
        renderAvatar(currentAvatarUrl);
        removeAvatar.hidden = false;
        avatarProgress.textContent = "";
      } else if (avatarRemovalPending) {
        await request(`${api}/avatar`, { method: "DELETE" });
        currentAvatarUrl = "";
        initialAvatarUrl = "";
        avatarRemovalPending = false;
        renderAvatar("");
        removeAvatar.hidden = true;
        avatarProgress.textContent = "";
      }
      banner("profile", "Your profile was updated.", "success");
    } catch (error) {
      const message = showErrors(profileForm, error.result, error.message);
      banner("profile", message || "Unable to save profile changes.", "error");
    } finally {
      saveButton.textContent = "Save changes";
      updateSaveState();
    }
  });

  root.querySelector("[data-profile-cancel]").addEventListener("click", () => {
    nameInput.value = initialProfile.display_name;
    phoneInput.value = initialProfile.phone;
    emailInput.value = initialProfile.email;
    pendingAvatarFile = null;
    avatarRemovalPending = false;
    avatarInput.value = "";
    if (previewObjectUrl) URL.revokeObjectURL(previewObjectUrl);
    previewObjectUrl = "";
    currentAvatarUrl = initialAvatarUrl;
    renderAvatar(initialAvatarUrl);
    removeAvatar.hidden = !initialAvatarUrl;
    avatarProgress.textContent = "";
    clearErrors(profileForm);
    banner("profile", "");
    updateSaveState();
  });

  window.addEventListener("beforeunload", (event) => {
    if (!saveButton.disabled) {
      event.preventDefault();
    }
  });

  const avatarInput = root.querySelector("#acct-avatar-file");
  const avatarProgress = root.querySelector("[data-avatar-progress]");
  const removeAvatar = root.querySelector("[data-avatar-remove]");
  const preview = root.querySelector("[data-avatar-preview]");
  avatarInput.addEventListener("change", () => {
    const file = avatarInput.files?.[0];
    if (!file) return;
    if (!["image/jpeg", "image/png", "image/webp"].includes(file.type) || file.size > 2 * 1024 * 1024) {
      banner("profile", "Choose a JPG, PNG or WebP image no larger than 2 MB.", "error");
      avatarInput.value = "";
      return;
    }
    pendingAvatarFile = file;
    avatarRemovalPending = false;
    if (previewObjectUrl) URL.revokeObjectURL(previewObjectUrl);
    previewObjectUrl = URL.createObjectURL(file);
    renderAvatar(previewObjectUrl);
    removeAvatar.hidden = false;
    avatarProgress.textContent = "Photo ready to upload when you save changes.";
    banner("profile", "");
    updateSaveState();
  });
  // Avatar drop zone: dropping a file hands it to the existing hidden
  // input, so the change handler above (validation + preview + the
  // "ready to upload" progress line) runs unchanged.
  const avatarDropzone = root.querySelector("#acct-avatar-dropzone");
  if (avatarDropzone) {
    avatarDropzone.addEventListener("dragover", (event) => { event.preventDefault(); avatarDropzone.classList.add("is-dragover"); });
    avatarDropzone.addEventListener("dragleave", () => avatarDropzone.classList.remove("is-dragover"));
    avatarDropzone.addEventListener("drop", (event) => {
      event.preventDefault();
      avatarDropzone.classList.remove("is-dragover");
      if (!event.dataTransfer.files.length) return;
      avatarInput.files = event.dataTransfer.files;
      avatarInput.dispatchEvent(new Event("change", { bubbles: true }));
    });
  }

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

  function renderAvatar(url) {
    root.querySelectorAll(".kpi-avatar").forEach((avatar) => {
      if (url) {
        avatar.classList.add("kpi-avatar--photo");
        avatar.classList.remove("kpi-avatar--image-failed");
        ensureAvatarImage(avatar).src = url;
      } else {
        avatar.querySelector(".kpi-avatar__image")?.remove();
        avatar.classList.remove("kpi-avatar--photo", "kpi-avatar--image-failed");
        avatar.querySelector(".kpi-avatar__initials")?.classList.remove("kpi-avatar__fallback");
      }
    });
  }

  removeAvatar.addEventListener("click", () => {
    pendingAvatarFile = null;
    avatarInput.value = "";
    avatarRemovalPending = Boolean(currentAvatarUrl);
    if (previewObjectUrl) URL.revokeObjectURL(previewObjectUrl);
    previewObjectUrl = "";
    renderAvatar(avatarRemovalPending ? "" : currentAvatarUrl);
    removeAvatar.hidden = true;
    avatarProgress.textContent = avatarRemovalPending
      ? "Photo will be removed when you save changes."
      : "";
    updateSaveState();
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
